"""Transaction-owned writer for the reviewed physical audit.audit_events table.

This does not deliver WORM objects or enable a business command. The owning
service supplies its already-authorized facts and resolved retention deadline.
Legacy audit chains are never rehashed or silently adopted.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from shared.audit.integrity import (
    CHAIN_GENESIS_HASH,
    AuditChainVerification,
    AuditImmutabilityError,
    AuditIntegrityError,
    AuditIntegrityIssue,
    canonical_json,
    sha256_hex,
)
from shared.infrastructure.persistence.postgresql import PostgresEngine

IMMUTABLE_COLUMNS = (
    "audit_event_id",
    "tenant_id",
    "sequence_no",
    "event_type",
    "actor_subject_id",
    "service_principal",
    "actor_role",
    "action",
    "resource_type",
    "resource_id",
    "decision_id",
    "source_snapshot_id",
    "parser_run_id",
    "before_value",
    "after_value",
    "reason",
    "result",
    "reason_code",
    "correlation_id",
    "causation_id",
    "previous_event_sha256",
    "occurred_at",
    "retained_until",
    "legal_hold",
)
UUID_COLUMNS = {
    "audit_event_id",
    "tenant_id",
    "actor_subject_id",
    "resource_id",
    "decision_id",
    "source_snapshot_id",
    "parser_run_id",
    "correlation_id",
    "causation_id",
}
_READ_ONE = "SELECT audit_event_id, tenant_id, sequence_no, event_type, actor_subject_id, service_principal, actor_role, action, resource_type, resource_id, decision_id, source_snapshot_id, parser_run_id, before_value::text AS before_value, after_value::text AS after_value, reason, result, reason_code, correlation_id, causation_id, previous_event_sha256, occurred_at, retained_until, legal_hold, event_sha256, worm_object_uri FROM audit.audit_events WHERE tenant_id = ? AND audit_event_id = ?"

_READ_TAIL = "SELECT audit_event_id, tenant_id, sequence_no, event_type, actor_subject_id, service_principal, actor_role, action, resource_type, resource_id, decision_id, source_snapshot_id, parser_run_id, before_value::text AS before_value, after_value::text AS after_value, reason, result, reason_code, correlation_id, causation_id, previous_event_sha256, occurred_at, retained_until, legal_hold, event_sha256, worm_object_uri FROM audit.audit_events WHERE tenant_id = ? ORDER BY sequence_no DESC LIMIT 1"

_READ_ALL = "SELECT audit_event_id, tenant_id, sequence_no, event_type, actor_subject_id, service_principal, actor_role, action, resource_type, resource_id, decision_id, source_snapshot_id, parser_run_id, before_value::text AS before_value, after_value::text AS after_value, reason, result, reason_code, correlation_id, causation_id, previous_event_sha256, occurred_at, retained_until, legal_hold, event_sha256, worm_object_uri FROM audit.audit_events WHERE tenant_id = ? ORDER BY sequence_no"

_INSERT_EVENT = "INSERT INTO audit.audit_events (audit_event_id,tenant_id,sequence_no,event_type,actor_subject_id,service_principal,actor_role,action,resource_type,resource_id,decision_id,source_snapshot_id,parser_run_id,before_value,after_value,reason,result,reason_code,correlation_id,causation_id,previous_event_sha256,occurred_at,retained_until,legal_hold,event_sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?::jsonb,?::jsonb,?,?,?,?,?,?,?,?,?,?)"


def _decode(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is not None:
        for name in ("before_value", "after_value"):
            if row[name] is not None:
                row[name] = json.loads(row[name])
    return row


def _uuid(value: Any) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Canonical audit identity must be a canonical UUID string")
    return value


def _time(value: Any) -> str:
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Canonical audit timestamps require an explicit timezone")
    return value.astimezone(UTC).isoformat()


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = {name: row.get(name) for name in IMMUTABLE_COLUMNS}
    for name in UUID_COLUMNS:
        if payload[name] is not None:
            payload[name] = str(payload[name])
    for name in ("occurred_at", "retained_until"):
        payload[name] = _time(payload[name])
    return payload


@dataclass(frozen=True)
class CanonicalAuditFact:
    audit_event_id: str
    tenant_id: str
    event_type: str
    action: str
    resource_type: str
    resource_id: str
    result: str
    correlation_id: str
    occurred_at: datetime
    retained_until: datetime
    actor_subject_id: str | None = None
    service_principal: str | None = None
    actor_role: str | None = None
    decision_id: str | None = None
    source_snapshot_id: str | None = None
    parser_run_id: str | None = None
    before_value: Any = None
    after_value: Any = None
    reason: str | None = None
    reason_code: str | None = None
    causation_id: str | None = None
    legal_hold: bool = False

    def values(self) -> dict[str, Any]:
        row = dict(vars(self))
        for name in ("audit_event_id", "tenant_id", "resource_id", "correlation_id"):
            _uuid(row[name])
        for name in UUID_COLUMNS:
            if row[name] is not None:
                _uuid(row[name])
        if not self.actor_subject_id and not self.service_principal:
            raise ValueError("An actual human or service audit principal is required")
        if self.result not in {"ALLOWED", "DENIED", "SUCCEEDED", "FAILED", "MASKED"}:
            raise ValueError("Unknown canonical audit result")
        if any(
            not isinstance(row[k], str) or not row[k].strip()
            for k in ("event_type", "action", "resource_type")
        ):
            raise ValueError("Canonical audit event/action/resource type is required")
        if self.service_principal is not None and (
            not isinstance(self.service_principal, str) or not self.service_principal.strip()
        ):
            raise ValueError("Service principal cannot be blank")
        if type(self.legal_hold) is not bool:
            raise ValueError("Legal hold fact must be a boolean")
        if self.result in {"DENIED", "MASKED"} and (
            self.before_value is not None or self.after_value is not None
        ):
            raise ValueError("Denial/mask audit facts must not contain sensitive values")
        row["occurred_at"], row["retained_until"] = (
            _time(self.occurred_at),
            _time(self.retained_until),
        )
        if datetime.fromisoformat(row["retained_until"]) <= datetime.fromisoformat(
            row["occurred_at"]
        ):
            raise ValueError("Resolved retention deadline must follow the event")
        # Copy and reject non-JSON/NaN values rather than hash unstable reprs.
        for name in ("before_value", "after_value"):
            row[name] = json.loads(json.dumps(row[name], allow_nan=False))
        return row


class CanonicalPostgresAuditLog:
    def __init__(self, engine: PostgresEngine) -> None:
        if engine.dialect != "postgresql":
            raise ValueError("Canonical audit writer requires PostgreSQL")
        self.engine = engine

    @contextmanager
    def _tenant(self, tenant_id: str):
        tenant_id = _uuid(tenant_id)
        with self.engine.transaction():
            bound = self.engine.query_one("SELECT current_setting('app.tenant_id', true) AS tenant")
            if bound and bound["tenant"] and bound["tenant"] != tenant_id:
                raise PermissionError("Cannot change a transaction's verified tenant")
            self.engine.query_one("SELECT set_config('app.tenant_id', ?, true)", (tenant_id,))
            yield

    def append(self, fact: CanonicalAuditFact) -> dict[str, Any]:
        values = fact.values()
        with self._tenant(fact.tenant_id):
            self.engine.query_one(
                "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                (f"canonical-audit:{fact.tenant_id}",),
            )
            prior = _decode(
                self.engine.query_one(
                    _READ_ONE,
                    (fact.tenant_id, fact.audit_event_id),
                )
            )
            if prior:
                if any(_payload(prior)[key] != value for key, value in values.items()):
                    raise AuditImmutabilityError(
                        "Existing audit identity has different immutable facts"
                    )
                if sha256_hex(_payload(prior)) != prior["event_sha256"]:
                    raise AuditIntegrityError(
                        "Existing audit identity fails integrity verification"
                    )
                return prior
            tail = _decode(
                self.engine.query_one(
                    _READ_TAIL,
                    (fact.tenant_id,),
                )
            )
            if tail and sha256_hex(_payload(tail)) != tail["event_sha256"]:
                raise AuditIntegrityError(
                    "Audit tail is corrupt or requires approved legacy reconciliation"
                )
            if tail:
                if type(tail["sequence_no"]) is not int or tail["sequence_no"] < 1:
                    raise AuditIntegrityError("Audit tail sequence is invalid")
                predecessor = (
                    self.engine.query_one(
                        "SELECT event_sha256 FROM audit.audit_events WHERE tenant_id = ? AND sequence_no = ?",
                        (fact.tenant_id, tail["sequence_no"] - 1),
                    )
                    if tail["sequence_no"] > 1
                    else {"event_sha256": CHAIN_GENESIS_HASH}
                )
                if not predecessor or tail["previous_event_sha256"] != predecessor["event_sha256"]:
                    raise AuditIntegrityError("Audit tail is not linked to its predecessor")
            values["sequence_no"] = int(tail["sequence_no"]) + 1 if tail else 1
            values["previous_event_sha256"] = tail["event_sha256"] if tail else CHAIN_GENESIS_HASH
            values["event_sha256"] = sha256_hex(_payload(values))
            columns = (*IMMUTABLE_COLUMNS, "event_sha256")
            parameters = tuple(
                canonical_json(values[name])
                if name in {"before_value", "after_value"}
                else values[name]
                for name in columns
            )
            self.engine.execute(_INSERT_EVENT, parameters)
            # Delivery is separate; this writer cannot invent a WORM receipt.
            persisted = _decode(
                self.engine.query_one(
                    _READ_ONE,
                    (fact.tenant_id, fact.audit_event_id),
                )
            )
            if persisted is None or sha256_hex(_payload(persisted)) != persisted["event_sha256"]:
                raise AuditIntegrityError("Persisted audit facts failed verification")
            return persisted

    def list_events(self, tenant_id: str) -> list[dict[str, Any]]:
        with self._tenant(tenant_id):
            return [
                _decode(row)
                for row in self.engine.query(
                    _READ_ALL,
                    (tenant_id,),
                )
            ]

    def verify_chain(self, tenant_id: str) -> AuditChainVerification:
        rows = self.list_events(tenant_id)
        previous = CHAIN_GENESIS_HASH
        issues = []
        for index, row in enumerate(rows):
            if (
                row["sequence_no"] != index + 1
                or row["previous_event_sha256"] != previous
                or sha256_hex(_payload(row)) != row["event_sha256"]
            ):
                issues.append(
                    AuditIntegrityIssue(
                        index, str(row["audit_event_id"]), "Canonical audit order/hash differs"
                    )
                )
            previous = row["event_sha256"]
        return AuditChainVerification(ok=not issues, issues=tuple(issues))
