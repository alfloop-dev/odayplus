from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from shared.audit.integrity import (
    CHAIN_GENESIS_HASH,
    DEFAULT_AUDIT_INTEGRITY_KEY_ID,
    DEFAULT_AUDIT_SIGNATURE_ALG,
    DEFAULT_AUDIT_SIGNATURE_VERSION,
    DEFAULT_AUDIT_WORM_SINK_ID,
    AuditChainVerification,
    AuditImmutabilityError,
    attach_audit_event_integrity,
    verify_audit_chain,
)
from shared.audit.worm import AuditWormSink


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
    sequence: int | None = None
    previous_hash: str | None = None
    event_hash: str | None = None
    signature_key_id: str = DEFAULT_AUDIT_INTEGRITY_KEY_ID
    signature_version: str = DEFAULT_AUDIT_SIGNATURE_VERSION
    signature_alg: str = DEFAULT_AUDIT_SIGNATURE_ALG
    worm_sink_id: str = DEFAULT_AUDIT_WORM_SINK_ID

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "actor": self.actor,
            "action": self.action,
            "resource": self.resource,
            "result": self.outcome,
            "outcome": self.outcome,
            "correlation_id": self.correlation_id,
            "job_id": self.job_id,
            "metadata": self.metadata,
            "occurred_at": self.occurred_at.isoformat(),
        }
        if self.event_hash is not None or self.sequence is not None:
            payload["integrity"] = {
                "sequence": self.sequence,
                "previous_hash": self.previous_hash,
                "event_hash": self.event_hash,
                "signature_key_id": self.signature_key_id,
                "signature_version": self.signature_version,
                "signature_alg": self.signature_alg,
                "worm_sink_id": self.worm_sink_id,
            }
        return payload


class InMemoryAuditLog:
    def __init__(self, *, worm_sink: AuditWormSink | None = None) -> None:
        self._events: list[AuditEvent] = []
        self._worm_sink = worm_sink

    def record(self, event: AuditEvent) -> AuditEvent:
        previous_hash = (
            self._events[-1].event_hash if self._events else CHAIN_GENESIS_HASH
        )
        sink_id = _event_sink_id(event, self._worm_sink)
        attach_audit_event_integrity(
            event,
            sequence=len(self._events) + 1,
            previous_hash=previous_hash or CHAIN_GENESIS_HASH,
            sink_id=sink_id,
        )
        if self._worm_sink is not None:
            self._worm_sink.write_audit_event(event)
        self._events.append(event)
        return event

    def list_events(
        self,
        *,
        correlation_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[AuditEvent]:
        events = list(self._events)
        if tenant_id is not None:
            events = [
                event
                for event in events
                if str(event.metadata.get("tenant_id") or "") == tenant_id
            ]
        if correlation_id is not None:
            events = [
                event for event in events if event.correlation_id == correlation_id
            ]
        return events

    def verify_chain(self) -> AuditChainVerification:
        return verify_audit_chain(self._events)

    def replay(self, events: Iterable[AuditEvent]) -> list[AuditEvent]:
        """Append restored events in their original chain order.

        Replaying into an empty sink preserves the original sequence numbers,
        hashes, actor, correlation id, and WORM metadata because the integrity
        payload is deterministic for a given ordered event stream.
        """

        replayed: list[AuditEvent] = []
        for event in sorted(events, key=_audit_event_replay_key):
            replayed.append(self.record(event))
        return replayed

    def delete_event(self, event_id: str) -> None:
        raise AuditImmutabilityError(
            f"audit sink is append-only; delete denied for {event_id}"
        )

    def update_event_metadata(self, event_id: str, metadata: dict[str, Any]) -> None:
        raise AuditImmutabilityError(
            f"audit sink is append-only; update denied for {event_id}"
        )


def _audit_event_replay_key(event: AuditEvent) -> tuple[int, str, str]:
    return (
        event.sequence if event.sequence is not None else 2**63 - 1,
        event.occurred_at.isoformat(),
        event.event_id,
    )


def _event_sink_id(event: AuditEvent, worm_sink: AuditWormSink | None) -> str:
    if event.event_hash is not None:
        return event.worm_sink_id
    if worm_sink is not None:
        return worm_sink.sink_id
    return event.worm_sink_id
