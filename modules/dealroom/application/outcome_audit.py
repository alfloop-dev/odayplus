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
        raw_values_for_redaction: tuple[float | str, ...] = (),
    ) -> ConfidentialAccessDecision:
        attempt = ConfidentialAccessAttempt(
            actor_id=actor_id,
            role=role,
            resource=resource,
            action=action,
        )
        decision, reason, receipt = ConfidentialAccessAuditor.evaluate_access(
            attempt, confidentiality
        )

        for val in raw_values_for_redaction:
            if val is not None:
                self.forbidden_values_seen.append(str(val))

        self.audit_events.append(receipt)
        return decision

    def build_audit_receipt(self) -> dict[str, Any]:
        """Build redacted audit receipt and verify zero confidential value leaks."""
        summary = {
            "kind": "avm-confidential-access-audit-receipt",
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


def generate_dealroom_outcome_audit_receipt(
    attempts: list[tuple[str, Role | str, str, Action | str]],
    *,
    forbidden_raw_prices: tuple[float | str, ...] = (),
) -> dict[str, Any]:
    pack = AVMOutcomeAccessAuditPack()
    for actor_id, role, resource, action in attempts:
        pack.record_access_attempt(
            actor_id=actor_id,
            role=role,
            resource=resource,
            action=action,
            raw_values_for_redaction=forbidden_raw_prices,
        )
    return pack.build_audit_receipt()
