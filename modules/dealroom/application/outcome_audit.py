"""Audit log recorder and redacted receipt generator for DealRoom AVM outcomes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from modules.dealroom.domain.confidential_access import (
    ConfidentialAccessAttempt,
    ConfidentialAccessAuditor,
    ConfidentialAccessDecision,
    ConfidentialLevel,
    assert_no_confidential_leak,
)
from shared.auth.rbac import Action, Role


@dataclass
class AVMOutcomeAccessAuditPack:
    audited_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    audit_events: list[dict[str, Any]] = field(default_factory=list)
    forbidden_values_seen: list[str] = field(default_factory=list)

    def record_access_attempt(
        self,
        actor_id: str,
        role: Role | str,
        resource: str,
        action: Action | str,
        confidentiality: ConfidentialLevel = ConfidentialLevel.HIGH,
        *,
        authority_key: str | None = None,
        context: dict[str, Any] | None = None,
        raw_values_for_redaction: tuple[float | str, ...] = (),
    ) -> ConfidentialAccessDecision:
        attempt = ConfidentialAccessAttempt(
            actor_id=actor_id,
            role=role,
            resource=resource,
            action=action,
            context=context or {},
        )
        decision, reason, receipt = ConfidentialAccessAuditor.evaluate_access(
            attempt, confidentiality, authority_key=authority_key
        )

        for val in raw_values_for_redaction:
            if val is not None:
                self.forbidden_values_seen.append(str(val))

        self.audit_events.append(receipt)
        return decision

    def build_audit_receipt(
        self,
        *,
        dataset_snapshot_hash: str = "",
        model_version: str = "dealroom-avm-baseline-v1",
        task_id: str = "ODP-PLAN-AVM-OUTCOME-001",
    ) -> dict[str, Any]:
        """Build redacted audit receipt and verify zero confidential value leaks."""
        summary = {
            "kind": "avm-confidential-access-audit-receipt",
            "task_id": task_id,
            "model_version": model_version,
            "dataset_snapshot_hash": dataset_snapshot_hash,
            "audited_at": self.audited_at.isoformat(),
            "total_access_attempts": len(self.audit_events),
            "permitted_count": sum(
                1 for e in self.audit_events if e.get("decision") == "PERMIT"
            ),
            "denied_count": sum(
                1 for e in self.audit_events if e.get("decision") == "DENY"
            ),
            "audit_events": self.audit_events,
            "confidentiality_enforcement": {
                "confidential_raw_values_masked": True,
                "zero_leak_verified": True,
            },
        }

        # Calculate sha256 digest of receipt body
        body_json = json.dumps(summary, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(body_json.encode("utf-8")).hexdigest()
        summary["sha256"] = digest

        # Fail-closed assertion: ensure zero confidential values leaked into receipt
        assert_no_confidential_leak(summary, forbidden_raw_values=tuple(self.forbidden_values_seen))
        return summary


def verify_audit_receipt(
    audit_receipt: dict[str, Any] | None,
    *,
    expected_snapshot_hash: str = "",
    authority_key: str | None = None,
) -> bool:
    """Recompute body integrity, check count reconciliation, and verify zero leak & lineage binding."""
    if not isinstance(audit_receipt, dict):
        return False

    from modules.avm.domain.outcome import get_production_authority_verifier_key
    from modules.dealroom.domain.confidential_access import create_identity_proof

    key = authority_key or get_production_authority_verifier_key()
    if not key:
        return False

    # M3 Fix: Enforce structural confidential leak validation before accepting audit receipt body
    try:
        assert_no_confidential_leak(audit_receipt)
    except Exception:
        return False

    sha256_digest = audit_receipt.get("sha256", "")
    if not isinstance(sha256_digest, str) or not len(sha256_digest) == 64:
        return False

    # Enforce lowercase hex SHA256 format
    import re
    if not re.match(r"^[0-9a-f]{64}$", sha256_digest):
        return False

    # Recompute body integrity digest
    body = {k: v for k, v in audit_receipt.items() if k != "sha256"}
    body_json = json.dumps(body, sort_keys=True, separators=(",", ":"))
    expected_digest = hashlib.sha256(body_json.encode("utf-8")).hexdigest()
    if sha256_digest != expected_digest:
        return False

    total_attempts = audit_receipt.get("total_access_attempts", -1)
    permitted_count = audit_receipt.get("permitted_count", -1)
    denied_count = audit_receipt.get("denied_count", -1)
    events = audit_receipt.get("audit_events", [])

    if not isinstance(events, list) or len(events) != total_attempts or total_attempts <= 0:
        return False
    if permitted_count < 0 or denied_count < 0 or (permitted_count + denied_count != total_attempts):
        return False

    valid_permitted = 0
    valid_denied = 0

    for e in events:
        if not isinstance(e, dict):
            return False
        dec = e.get("decision")
        if dec == "PERMIT":
            actor_id = str(e.get("actor_id", ""))
            role_str = str(e.get("role", ""))
            tenant_id = str(e.get("tenant_id", "tenant-avm-001"))
            proof = str(e.get("identity_proof_sha256", ""))
            if not actor_id or not role_str or not proof:
                return False
            if role_str not in ("FINANCE_LEGAL", "PLATFORM_ADMIN", "finance_legal", "platform_admin"):
                return False
            try:
                expected_proof = create_identity_proof(
                    actor_id, role_str, tenant_id, authority_key=key, event_id=e.get("event_id")
                )
            except Exception:
                return False
            if proof != expected_proof:
                return False
            valid_permitted += 1
        elif dec == "DENY":
            valid_denied += 1
        else:
            return False

    if permitted_count != valid_permitted or denied_count != valid_denied:
        return False

    # B24: Require authorized access evidence (must have >= 1 permitted access by an authorized role)
    if permitted_count < 1:
        return False

    zero_leak = audit_receipt.get("confidentiality_enforcement", {}).get("zero_leak_verified", False)
    if zero_leak is not True:
        return False

    if expected_snapshot_hash:
        rcpt_snapshot = audit_receipt.get("dataset_snapshot_hash", "")
        if rcpt_snapshot != expected_snapshot_hash:
            return False

    return True


def generate_dealroom_outcome_audit_receipt(
    attempts: list[tuple[Any, ...]],
    *,
    authority_key: str | None = None,
    forbidden_raw_prices: tuple[float | str, ...] = (),
    dataset_snapshot_hash: str = "",
    model_version: str = "dealroom-avm-baseline-v1",
    task_id: str = "ODP-PLAN-AVM-OUTCOME-001",
) -> dict[str, Any]:
    pack = AVMOutcomeAccessAuditPack()
    for item in attempts:
        actor_id, role, resource, action = item[0], item[1], item[2], item[3]
        ctx = item[4] if len(item) > 4 and isinstance(item[4], dict) else {}
        pack.record_access_attempt(
            actor_id=actor_id,
            role=role,
            resource=resource,
            action=action,
            authority_key=authority_key,
            context=ctx,
            raw_values_for_redaction=forbidden_raw_prices,
        )
    return pack.build_audit_receipt(
        dataset_snapshot_hash=dataset_snapshot_hash,
        model_version=model_version,
        task_id=task_id,
    )
