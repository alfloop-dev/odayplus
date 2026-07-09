"""Durable, restart-survivable audit log (ODP-PV-009).

Drop-in replacement for :class:`shared.audit.events.InMemoryAuditLog`. Audit
records are stored columnar (not pickled) so ``correlation_id`` stays a real,
indexed query column — the product needs to resolve a request's audit trail by
correlation id after a process restart (acceptance: "core decision entities
persist audit/correlation metadata").
"""

from __future__ import annotations

import json
from datetime import datetime

from shared.audit.events import AuditEvent
from shared.infrastructure.persistence.engine import SqliteEngine


class DurableAuditLog:
    """``record`` / ``list_events`` over the ``durable_audit_events`` table."""

    def __init__(self, engine: SqliteEngine) -> None:
        self._engine = engine

    def record(self, event: AuditEvent) -> AuditEvent:
        self._engine.execute(
            "INSERT INTO durable_audit_events("
            "  event_id, event_type, actor, action, resource, outcome, "
            "  correlation_id, job_id, metadata_json, occurred_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(event_id) DO NOTHING",
            (
                event.event_id,
                event.event_type,
                event.actor,
                event.action,
                event.resource,
                event.outcome,
                event.correlation_id,
                event.job_id,
                json.dumps(event.metadata),
                event.occurred_at.isoformat(),
            ),
        )
        return event

    def list_events(self, *, correlation_id: str | None = None) -> list[AuditEvent]:
        if correlation_id is None:
            rows = self._engine.query(
                "SELECT * FROM durable_audit_events ORDER BY seq"
            )
        else:
            rows = self._engine.query(
                "SELECT * FROM durable_audit_events WHERE correlation_id = ? ORDER BY seq",
                (correlation_id,),
            )
        return [self._row_to_event(row) for row in rows]

    @staticmethod
    def _row_to_event(row) -> AuditEvent:
        return AuditEvent(
            event_type=row["event_type"],
            actor=row["actor"],
            action=row["action"],
            resource=row["resource"],
            outcome=row["outcome"],
            correlation_id=row["correlation_id"],
            metadata=json.loads(row["metadata_json"]),
            job_id=row["job_id"],
            event_id=row["event_id"],
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
        )


__all__ = ["DurableAuditLog"]
