"""Durable, restart-survivable audit log (ODP-PV-009).

Drop-in replacement for :class:`shared.audit.events.InMemoryAuditLog`. Audit
records are stored columnar (not pickled) so ``correlation_id`` stays a real,
indexed query column — the product needs to resolve a request's audit trail by
correlation id after a process restart (acceptance: "core decision entities
persist audit/correlation metadata").
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from shared.audit.events import AuditEvent
from shared.audit.integrity import (
    CHAIN_GENESIS_HASH,
    AuditChainVerification,
    AuditImmutabilityError,
    AuditIntegrityError,
    attach_audit_event_integrity,
    verify_audit_chain,
    verify_audit_event_integrity,
)
from shared.audit.restore import committed_envelope_event, reject_unproven_ledger_replay
from shared.audit.worm import AuditWormSink, committed_audit_checksum
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.observability.correlation import validate_audit_correlation

_INTEGRITY_COLUMNS: dict[str, str] = {
    "sequence": "INTEGER",
    "previous_hash": "TEXT",
    "event_hash": "TEXT",
    "signature_key_id": "TEXT",
    "signature_version": "TEXT",
    "signature_alg": "TEXT",
    "worm_sink_id": "TEXT",
}


class DurableAuditLog:
    """``record`` / ``list_events`` over the ``durable_audit_events`` table."""

    def __init__(
        self, engine: SqliteEngine, *, worm_sink: AuditWormSink | None = None
    ) -> None:
        self._engine = engine
        self._worm_sink = worm_sink
        self._ensure_integrity_columns()
        if worm_sink is not None:
            if not callable(getattr(worm_sink, "write_committed_audit_event", None)):
                raise ValueError("AUDIT_COMMITTED_WORM_SINK_REQUIRED")
            try:
                self._engine.query("SELECT event_id FROM durable_audit_worm_outbox LIMIT 0")
            except Exception:
                raise RuntimeError("AUDIT_WORM_OUTBOX_SCHEMA_REQUIRED") from None

    def record(self, event: AuditEvent) -> AuditEvent:
        validate_audit_correlation(event.event_type, event.correlation_id)
        with self._engine.lock, self._engine.transaction():
            existing = self._engine.query_one(
                "SELECT * FROM durable_audit_events WHERE event_id = ?",
                (event.event_id,),
            )
            if existing is not None:
                return self._row_to_event(existing)

            last = self._engine.query_one(
                "SELECT sequence, event_hash FROM durable_audit_events "
                "WHERE sequence IS NOT NULL ORDER BY sequence DESC LIMIT 1"
            )
            sequence = int(last["sequence"]) + 1 if last is not None else 1
            previous_hash = (
                str(last["event_hash"]) if last is not None else CHAIN_GENESIS_HASH
            )
            sink_id = _event_sink_id(event, self._worm_sink)
            attach_audit_event_integrity(
                event,
                sequence=sequence,
                previous_hash=previous_hash,
                sink_id=sink_id,
            )
            self._engine.execute(
                "INSERT INTO durable_audit_events("
                "  event_id, event_type, actor, action, resource, outcome, "
                "  correlation_id, job_id, metadata_json, occurred_at, "
                "  sequence, previous_hash, event_hash, signature_key_id, "
                "  signature_version, signature_alg, worm_sink_id"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    event.sequence,
                    event.previous_hash,
                    event.event_hash,
                    event.signature_key_id,
                    event.signature_version,
                    event.signature_alg,
                    event.worm_sink_id,
                ),
            )
            if self._worm_sink is not None:
                self._engine.execute(
                    "INSERT INTO durable_audit_worm_outbox(event_id, sink_id, event_hash, state) VALUES (?, ?, ?, 'pending')",
                    (event.event_id, self._worm_sink.sink_id, event.event_hash),
                )
                self._engine.after_commit(self.flush_worm)
            return event

    def flush_worm(self, *, limit: int = 16) -> int:
        """Bounded committed publication. Never called inside a business txn.

        Persist intent BEFORE I/O. Lost reply/ack/claim receipt is UNKNOWN and
        NEVER automatically retried, including after restart or another pool.
        SQL commit success is not a WORM delivery receipt. No daemon/CLI/authority.
        """
        if self._engine.in_transaction or type(limit) is not int or not 1 <= limit <= 64:
            raise RuntimeError("AUDIT_WORM_PUBLICATION_REFUSED")
        if self._worm_sink is None:
            return 0
        delivered = 0
        for _ in range(limit):
            with self._engine.lock, self._engine.transaction():
                row = self._engine.query_one(
                    "SELECT o.event_id, o.event_hash FROM durable_audit_worm_outbox o "
                    "JOIN durable_audit_events e ON e.event_id = o.event_id "
                    "WHERE o.sink_id = ? AND o.state = 'pending' ORDER BY e.sequence LIMIT 1",
                    (self._worm_sink.sink_id,),
                )
                if row is None:
                    break
                claimed = self._engine.execute(
                    "UPDATE durable_audit_worm_outbox SET state = 'attempted' WHERE event_id = ? AND state = 'pending'",
                    (row["event_id"],),
                )
                if claimed.rowcount != 1:
                    continue
                stored = self._engine.query_one("SELECT * FROM durable_audit_events WHERE event_id = ?", (row["event_id"],))
            # Only a committed claim/row reaches external transport. Any claim
            # commit failure escapes without sending; never infer rollback.
            try:
                event = self._row_to_event(stored)
                if (event.event_hash != row["event_hash"] or event.worm_sink_id != self._worm_sink.sink_id
                        or not verify_audit_event_integrity(event)):
                    raise ValueError
                receipt = self._worm_sink.write_committed_audit_event(event)
                if (receipt.sink_id != self._worm_sink.sink_id or receipt.record_id != event.event_id
                        or receipt.record_type != "audit-events"
                        or receipt.checksum != committed_audit_checksum(event.to_dict())):
                    raise ValueError
                self._engine.execute("UPDATE durable_audit_worm_outbox SET state = 'delivered' WHERE event_id = ? AND state = 'attempted'",
                                     (event.event_id,))
                delivered += 1
            except Exception:
                self._engine.execute("UPDATE durable_audit_worm_outbox SET state = 'unknown' WHERE event_id = ? AND state = 'attempted'",
                                     (row["event_id"],))
        return delivered

    def list_events(
        self,
        *,
        correlation_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[AuditEvent]:
        if correlation_id is None and tenant_id is None:
            events = self._read_events()
            verify_audit_chain(events).raise_for_tamper()
            return events

        # Operational lookups use the correlation index and validate each
        # returned record; verify_chain() remains the full-chain evidence check.
        clauses: list[str] = []
        params: list[str] = []
        if correlation_id is not None:
            clauses.append("correlation_id = ?")
            params.append(correlation_id)
        if tenant_id is not None:
            if str(getattr(self._engine, "dialect", "")).lower() == "postgresql":
                clauses.append("metadata_json ->> 'tenant_id' = ?")
            else:
                clauses.append("json_extract(metadata_json, '$.tenant_id') = ?")
            params.append(tenant_id)
        # Clause names are fixed above; caller values remain bound parameters.
        rows = self._engine.query(
            "SELECT * FROM durable_audit_events WHERE "  # nosec B608
            + " AND ".join(clauses)
            + " ORDER BY seq",
            tuple(params),
        )
        events = [self._row_to_event(row) for row in rows]
        if any(not verify_audit_event_integrity(event) for event in events):
            raise AuditIntegrityError(
                "audit event integrity verification failed for correlation query"
            )
        return events

    def verify_chain(self) -> AuditChainVerification:
        return verify_audit_chain(self._read_events())

    def replay(self, events: Iterable[AuditEvent]) -> list[AuditEvent]:
        """Append restored events while preserving the original chain order."""

        values = reject_unproven_ledger_replay(events)
        replayed: list[AuditEvent] = []
        for event in sorted(values, key=_audit_event_replay_key):
            replayed.append(self.record(event))
        return replayed

    def replay_committed_envelopes(self, envelopes: Iterable[dict[str, Any]]) -> list[AuditEvent]:
        # Validate the ENTIRE supplied batch before any insert/publication. Caller
        # must authenticate immutable-sink transport; checksum is not authority.
        events = [committed_envelope_event(envelope) for envelope in envelopes]
        verify_audit_chain(sorted(events, key=_audit_event_replay_key)).raise_for_tamper()
        with self._engine.transaction():
            return [self.record(event) for event in sorted(events, key=_audit_event_replay_key)]

    def delete_event(self, event_id: str) -> None:
        raise AuditImmutabilityError(
            f"audit sink is append-only; delete denied for {event_id}"
        )

    def update_event_metadata(self, event_id: str, metadata: dict[str, Any]) -> None:
        raise AuditImmutabilityError(
            f"audit sink is append-only; update denied for {event_id}"
        )

    def _ensure_integrity_columns(self) -> None:
        existing = {
            str(row["name"])
            for row in self._engine.query("PRAGMA table_info(durable_audit_events)")
        }
        for column, column_type in _INTEGRITY_COLUMNS.items():
            if column not in existing:
                self._engine.execute(
                    f"ALTER TABLE durable_audit_events ADD COLUMN {column} {column_type}"
                )

    def _read_events(self) -> list[AuditEvent]:
        rows = self._engine.query("SELECT * FROM durable_audit_events ORDER BY seq")
        return [self._row_to_event(row) for row in rows]

    @staticmethod
    def _row_to_event(row) -> AuditEvent:
        values = _row_values(row)
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
            sequence=values.get("sequence"),
            previous_hash=values.get("previous_hash"),
            event_hash=values.get("event_hash"),
            signature_key_id=values.get("signature_key_id")
            or AuditEvent.__dataclass_fields__["signature_key_id"].default,
            signature_version=values.get("signature_version")
            or AuditEvent.__dataclass_fields__["signature_version"].default,
            signature_alg=values.get("signature_alg")
            or AuditEvent.__dataclass_fields__["signature_alg"].default,
            worm_sink_id=values.get("worm_sink_id")
            or AuditEvent.__dataclass_fields__["worm_sink_id"].default,
        )


def _row_values(row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


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


__all__ = ["DurableAuditLog"]
