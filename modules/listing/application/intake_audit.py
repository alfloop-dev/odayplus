"""Bind Intake command audit facts to the physical writer and server policy."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from modules.listing.application.intake_registry_calendar import RegistryIntakeWorkflowPolicy
from shared.audit.canonical_postgresql import CanonicalAuditFact, CanonicalPostgresAuditLog
from shared.audit.errors import CanonicalAuditUnavailable
from shared.audit.integrity import sha256_hex
from shared.audit.persistence import RETENTION_PURGE_ROLES

AUDIT_RETENTION_KIND = "intake_audit_retention"
POLICY_EVIDENCE_KEY = "audit_retention_policy"


@dataclass(frozen=True)
class ResolvedIntakeAuditRetention:
    tenant_id: str
    policy_version_id: str
    policy_sha256: str
    approved_by: str
    approved_at: str
    retained_until: datetime


class RegistryIntakeAuditRetention:
    """Read-only effective policy and current approval account/role/scope."""

    def __init__(self, policies: Any, identities: Any) -> None:
        self._policies = policies
        self._authority = RegistryIntakeWorkflowPolicy(policies, identities)

    def resolve(
        self, scope: Mapping[str, Any], occurred_at: datetime
    ) -> ResolvedIntakeAuditRetention:
        try:
            row = self._authority._check_policy(
                self._policies.find_effective(
                    policy_kind=AUDIT_RETENTION_KIND, tenant_id=scope["tenant_id"], at=occurred_at
                ),
                kind=AUDIT_RETENTION_KIND,
                scope=scope,
                at=occurred_at,
            )
            if row.owner_role not in RETENTION_PURGE_ROLES:
                raise ValueError("Audit retention requires a records/retention/compliance owner")
            p = row.parameters
            if (
                type(p.get("schema_version")) is not int
                or p["schema_version"] != 1
                or p.get("duration_basis") != "UTC_ELAPSED"
                or type(p.get("retention_days")) is not int
                or p["retention_days"] <= 0
                or not all(row.reads(name) for name in ("scope", "occurred_at"))
            ):
                raise ValueError("Invalid or undeclared audit retention inputs")
            deadline = occurred_at.astimezone(UTC) + timedelta(days=p["retention_days"])
            payload = {**row.to_dict(), "approved_at": row.approved_at.isoformat()}
            return ResolvedIntakeAuditRetention(
                row.tenant_id,
                row.policy_version_id,
                sha256_hex(payload),
                row.approved_by,
                row.approved_at.isoformat(),
                deadline,
            )
        except Exception as exc:
            raise CanonicalAuditUnavailable(
                "Approved Intake audit retention is unavailable"
            ) from exc


class CanonicalIntakeAuditSink:
    def __init__(self, writer: CanonicalPostgresAuditLog, retention: Any, delivery: Any = None) -> None:
        self.writer = writer
        self.retention = retention
        self.delivery = delivery

    def set_retention_provider(self, retention: Any) -> None:
        if retention is None or not callable(getattr(retention, "resolve", None)):
            raise ValueError("A server audit retention resolver is required")
        self.retention = retention

    def is_committed(self, audit: Mapping[str, Any]) -> bool:
        try:
            row = self.writer.get_event(audit["tenant_id"], audit["audit_event_id"])
            return bool(
                row
                and row["event_sha256"] == audit.get("canonical_event_sha256")
                and row["resource_type"] == audit["resource_type"]
                and row["resource_id"] == audit["resource_id"]
                and row["result"] == audit["result"]
                and row["before_value"] == audit.get("before_value")
                and row["after_value"] == audit.get("after_value")
            )
        except Exception:
            return False

    def persist(self, audit: Mapping[str, Any], scope: Mapping[str, Any]) -> dict[str, Any]:
        """The caller owns the same SQL transaction as its business writes."""
        result = deepcopy(dict(audit))
        authorized_scope = deepcopy(dict(scope))
        try:
            if self.delivery is None:
                raise ValueError("Canonical Intake audit delivery is unavailable")
            self.delivery.require_business_transaction()
            if result["tenant_id"] != authorized_scope["tenant_id"]:
                raise ValueError("Audit tenant differs from its authoritative parent")
            at = datetime.fromisoformat(result["occurred_at"].replace("Z", "+00:00"))
            if at.utcoffset() is None:
                raise ValueError("Audit timestamp has no timezone")
            before_scope = deepcopy(authorized_scope)
            policy = self.retention.resolve(authorized_scope, at)
            if (
                not isinstance(policy, ResolvedIntakeAuditRetention)
                or authorized_scope != before_scope
                or policy.tenant_id != result["tenant_id"]
                or not policy.policy_version_id
                or len(policy.policy_sha256) != 64
            ):
                raise ValueError("Audit policy does not bind to the authorized scope")
            after = deepcopy(result["after_value"])
            if not isinstance(after, dict) or POLICY_EVIDENCE_KEY in after:
                raise ValueError("Audit policy evidence namespace is unavailable")
            # This augments the audited snapshot, never the business resource.
            after[POLICY_EVIDENCE_KEY] = {
                "policy_version_id": policy.policy_version_id,
                "policy_sha256": policy.policy_sha256,
                "approved_by": policy.approved_by,
                "approved_at": policy.approved_at,
                "retained_until": policy.retained_until.astimezone(UTC).isoformat(),
            }
            fact = CanonicalAuditFact(
                audit_event_id=result["audit_event_id"],
                tenant_id=result["tenant_id"],
                event_type=result["action"],
                action=result["action"],
                resource_type=result["resource_type"],
                resource_id=result["resource_id"],
                actor_subject_id=result.get("actor_subject_id"),
                service_principal=result.get("service_principal"),
                actor_role=result.get("actor_role"),
                result=result["result"],
                correlation_id=result["correlation_id"],
                occurred_at=at,
                retained_until=policy.retained_until,
                before_value=result.get("before_value"),
                after_value=after,
                reason=result.get("reason"),
                reason_code=result.get("reason_code"),
            )
            persisted = self.writer.append(fact)
            self.delivery.enqueue(persisted)
            result["after_value"] = after
            result["canonical_event_sha256"] = persisted["event_sha256"]
            result["canonical_sequence_no"] = persisted["sequence_no"]
            result["retained_until"] = persisted["retained_until"]
            return result
        except CanonicalAuditUnavailable:
            raise
        except Exception as exc:
            raise CanonicalAuditUnavailable(
                "Canonical Intake audit could not be committed"
            ) from exc
