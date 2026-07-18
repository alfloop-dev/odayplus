from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from shared.domain.events import DomainEvent, validate_event
from shared.infrastructure.persistence.engine import SqliteEngine


class OutboxError(Exception):
    pass

class InMemoryOutboxRepository:
    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}

    def save(self, event: DomainEvent) -> None:
        errors = validate_event(event)
        if errors:
            raise ValueError(f"Event validation failed: {errors}")

        event_id = event.event_id
        
        # Check unique constraint: UNIQUE (tenant_id, aggregate_type, aggregate_id, aggregate_version, event_type)
        for existing_entry in self.events.values():
            e = existing_entry["event"]
            if (e.tenant_id == event.tenant_id and
                e.aggregate_type == event.aggregate_type and
                e.aggregate_id == event.aggregate_id and
                e.aggregate_version == event.aggregate_version and
                e.event_type == event.event_type):
                raise ValueError("Duplicate event: unique constraint violation")

        self.events[event_id] = {
            "event": event,
            "published_at": None,
            "publish_attempts": 0,
            "last_error": None,
            "retention_until": event.occurred_at + timedelta(days=30),
            "published_message_id": None,
            "available_at": event.occurred_at,
            "locked_by": None,
            "lock_expires_at": None,
        }

    def get_unpublished_events(self) -> list[DomainEvent]:
        now = datetime.now(UTC)
        unpublished = []
        for item in self.events.values():
            if item["published_at"] is None:
                if item["available_at"] <= now:
                    if item["locked_by"] is None or (item["lock_expires_at"] and item["lock_expires_at"] <= now):
                        unpublished.append(item["event"])
        unpublished.sort(key=lambda e: e.occurred_at)
        return unpublished

    def claim_batch(self, locked_by: str, lease_seconds: int = 60, batch_size: int = 200) -> list[DomainEvent]:
        now = datetime.now(UTC)
        candidates = self.get_unpublished_events()[:batch_size]
        claimed = []
        for event in candidates:
            item = self.events[event.event_id]
            item["locked_by"] = locked_by
            item["lock_expires_at"] = now + timedelta(seconds=lease_seconds)
            claimed.append(event)
        return claimed

    def mark_published(self, event_id: str, published_message_id: str) -> None:
        if event_id in self.events:
            item = self.events[event_id]
            now = datetime.now(UTC)
            item["published_at"] = now
            item["published_message_id"] = published_message_id
            item["locked_by"] = None
            item["lock_expires_at"] = None
            
            event = item["event"]
            item["event"] = DomainEvent(
                event_type=event.event_type,
                payload=event.payload,
                tenant_id=event.tenant_id,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                aggregate_version=event.aggregate_version,
                partition_key=event.partition_key,
                correlation_id=event.correlation_id,
                producer=event.producer,
                schema_ref=event.schema_ref,
                event_version=event.event_version,
                event_id=event.event_id,
                occurred_at=event.occurred_at,
                published_at=now,
                causation_id=event.causation_id,
                actor_ref=event.actor_ref,
                policy_version=event.policy_version,
                sensitive_fields=event.sensitive_fields
            )

    def mark_failed(self, event_id: str, error_message: str, max_attempts: int = 10) -> None:
        if event_id in self.events:
            item = self.events[event_id]
            item["publish_attempts"] += 1
            item["last_error"] = error_message
            item["locked_by"] = None
            item["lock_expires_at"] = None
            
            schedule = [10, 30, 120, 600, 1800]
            attempts = item["publish_attempts"]
            if attempts >= max_attempts:
                item["available_at"] = datetime.max.replace(tzinfo=UTC)
            else:
                idx = min(attempts - 1, len(schedule) - 1)
                delay = schedule[idx]
                item["available_at"] = datetime.now(UTC) + timedelta(seconds=delay)


class DurableOutboxRepository:
    def __init__(self, engine: SqliteEngine) -> None:
        self._engine = engine

    def save(self, event: DomainEvent) -> None:
        errors = validate_event(event)
        if errors:
            raise ValueError(f"Event validation failed: {errors}")

        outbox_event_id = str(uuid4())
        retention_until = (event.occurred_at + timedelta(days=30)).isoformat()
        
        try:
            self._engine.execute(
                "INSERT INTO durable_outbox_events ("
                "  outbox_event_id, tenant_id, event_id, event_type, event_version, "
                "  aggregate_type, aggregate_id, aggregate_version, partition_key, "
                "  payload, sensitive_fields, correlation_id, causation_id, occurred_at, "
                "  retention_until, producer, actor_ref, policy_version, schema_ref, available_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    outbox_event_id,
                    event.tenant_id,
                    event.event_id,
                    event.event_type,
                    event.event_version,
                    event.aggregate_type,
                    event.aggregate_id,
                    event.aggregate_version,
                    event.partition_key,
                    json.dumps(event.payload),
                    json.dumps(event.sensitive_fields),
                    event.correlation_id,
                    event.causation_id,
                    event.occurred_at.isoformat() if isinstance(event.occurred_at, datetime) else str(event.occurred_at),
                    retention_until,
                    event.producer,
                    event.actor_ref,
                    event.policy_version,
                    event.schema_ref,
                    event.occurred_at.isoformat() if isinstance(event.occurred_at, datetime) else str(event.occurred_at),
                )
            )
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                raise ValueError(f"Duplicate event: unique constraint violation: {e}") from e
            raise e

    def get_unpublished_events(self) -> list[DomainEvent]:
        now = datetime.now(UTC).isoformat()
        rows = self._engine.query(
            "SELECT * FROM durable_outbox_events "
            "WHERE published_at IS NULL AND available_at <= ? "
            "AND (locked_by IS NULL OR lock_expires_at <= ?) "
            "ORDER BY occurred_at",
            (now, now)
        )
        return [self._row_to_event(row) for row in rows]

    def claim_batch(self, locked_by: str, lease_seconds: int = 60, batch_size: int = 200) -> list[DomainEvent]:
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        lock_expires = (now_dt + timedelta(seconds=lease_seconds)).isoformat()
        
        with self._engine.lock:
            rows = self._engine.query(
                "SELECT * FROM durable_outbox_events "
                "WHERE published_at IS NULL AND available_at <= ? "
                "AND (locked_by IS NULL OR lock_expires_at <= ?) "
                "ORDER BY occurred_at LIMIT ?",
                (now, now, batch_size)
            )
            claimed = []
            for row in rows:
                event_id = row["event_id"]
                self._engine.execute(
                    "UPDATE durable_outbox_events "
                    "SET locked_by = ?, lock_expires_at = ? "
                    "WHERE event_id = ?",
                    (locked_by, lock_expires, event_id)
                )
                claimed.append(self._row_to_event(row))
            return claimed

    def mark_published(self, event_id: str, published_message_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        self._engine.execute(
            "UPDATE durable_outbox_events "
            "SET published_at = ?, published_message_id = ?, locked_by = NULL, lock_expires_at = NULL "
            "WHERE event_id = ?",
            (now, published_message_id, event_id)
        )

    def mark_failed(self, event_id: str, error_message: str, max_attempts: int = 10) -> None:
        row = self._engine.query_one(
            "SELECT publish_attempts FROM durable_outbox_events WHERE event_id = ?",
            (event_id,)
        )
        if not row:
            return
        attempts = row["publish_attempts"] + 1
        
        schedule = [10, 30, 120, 600, 1800]
        if attempts >= max_attempts:
            available_at = datetime.max.replace(tzinfo=UTC).isoformat()
        else:
            idx = min(attempts - 1, len(schedule) - 1)
            delay = schedule[idx]
            available_at = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()

        self._engine.execute(
            "UPDATE durable_outbox_events "
            "SET publish_attempts = ?, last_error = ?, available_at = ?, locked_by = NULL, lock_expires_at = NULL "
            "WHERE event_id = ?",
            (attempts, error_message, available_at, event_id)
        )

    @staticmethod
    def _row_to_event(row: Any) -> DomainEvent:
        from datetime import datetime
        
        def parse_dt(val: str | None) -> datetime | None:
            if not val:
                return None
            try:
                return datetime.fromisoformat(val)
            except ValueError:
                return None

        sf_str = row["sensitive_fields"]
        try:
            sensitive_fields = json.loads(sf_str)
        except Exception:
            sensitive_fields = [s.strip() for s in sf_str.split(",") if s.strip()] if sf_str else []

        return DomainEvent(
            event_type=row["event_type"],
            payload=json.loads(row["payload"]),
            tenant_id=row["tenant_id"],
            aggregate_type=row["aggregate_type"],
            aggregate_id=row["aggregate_id"],
            aggregate_version=row["aggregate_version"],
            partition_key=row["partition_key"],
            correlation_id=row["correlation_id"],
            producer=row["producer"],
            schema_ref=row["schema_ref"],
            event_version=row["event_version"],
            event_id=row["event_id"],
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
            published_at=parse_dt(row["published_at"]),
            causation_id=row["causation_id"],
            actor_ref=row["actor_ref"],
            policy_version=row["policy_version"],
            sensitive_fields=sensitive_fields
        )
