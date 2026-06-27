from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class AuditEvent:
    event_type: str
    actor: str
    action: str
    resource: str
    outcome: str
    correlation_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
    job_id: str | None = None
    event_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "actor": self.actor,
            "action": self.action,
            "resource": self.resource,
            "outcome": self.outcome,
            "correlation_id": self.correlation_id,
            "job_id": self.job_id,
            "metadata": self.metadata,
            "occurred_at": self.occurred_at.isoformat(),
        }


class InMemoryAuditLog:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> AuditEvent:
        self._events.append(event)
        return event

    def list_events(self, *, correlation_id: str | None = None) -> list[AuditEvent]:
        if correlation_id is None:
            return list(self._events)
        return [event for event in self._events if event.correlation_id == correlation_id]
