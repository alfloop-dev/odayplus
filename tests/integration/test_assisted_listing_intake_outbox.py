from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from shared.domain.events import DomainEvent
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.outbox import (
    DurableOutboxRepository,
    InMemoryOutboxRepository,
)


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "outbox_durable.sqlite3")


@pytest.fixture
def engine(db_path) -> SqliteEngine:
    engine = SqliteEngine(db_path)
    yield engine
    engine.close()


def make_valid_event(event_type: str = "intake.state_changed", version: int = 1) -> DomainEvent:
    return DomainEvent(
        event_type=event_type,
        payload={
            "intake_id": str(uuid4()),
            "from_state": "SUBMITTED",
            "to_state": "READY",
            "transition_id": str(uuid4()),
            "reason_code": "auto_approve",
            "version": version,
            "occurred_at": datetime.now(UTC).isoformat()
        },
        tenant_id=str(uuid4()),
        aggregate_type="intake",
        aggregate_id=str(uuid4()),
        aggregate_version=version,
        partition_key="tenant:intake",
        correlation_id=str(uuid4()),
        producer="listing_intake_service",
        schema_ref="#/payloads/IntakeStateChangedV1"
    )


def test_in_memory_outbox_flow() -> None:
    repo = InMemoryOutboxRepository()
    event = make_valid_event()
    
    # Save
    repo.save(event)
    
    # Check unpublished
    unpublished = repo.get_unpublished_events()
    assert len(unpublished) == 1
    assert unpublished[0].event_id == event.event_id
    
    # Claim
    claimed = repo.claim_batch(locked_by="publisher-1", lease_seconds=10)
    assert len(claimed) == 1
    assert claimed[0].event_id == event.event_id
    
    # Once claimed, it's locked, so it shouldn't show up in get_unpublished_events
    assert len(repo.get_unpublished_events()) == 0
    
    # Mark published
    repo.mark_published(event.event_id, published_message_id="msg-123")
    assert len(repo.get_unpublished_events()) == 0
    
    # Verify published_at and published_message_id
    entry = repo.events[event.event_id]
    assert entry["published_at"] is not None
    assert entry["published_message_id"] == "msg-123"
    assert entry["event"].published_at is not None


def test_in_memory_outbox_failures_and_backoff() -> None:
    repo = InMemoryOutboxRepository()
    event = make_valid_event()
    repo.save(event)
    
    # Claim
    repo.claim_batch(locked_by="pub-1")
    
    # Mark failed
    repo.mark_failed(event.event_id, error_message="network timeout")
    
    # Should not be immediately available due to backoff delay (10s)
    assert len(repo.get_unpublished_events()) == 0
    
    # If we check attempts
    entry = repo.events[event.event_id]
    assert entry["publish_attempts"] == 1
    assert entry["last_error"] == "network timeout"
    
    # Simulate DLQ by marking failed up to 10 attempts
    for _ in range(9):
        repo.mark_failed(event.event_id, error_message="still failing")
    
    assert entry["publish_attempts"] == 10
    # Available at is set to max time (effectively disabled)
    assert entry["available_at"] == datetime.max.replace(tzinfo=UTC)
    assert len(repo.get_unpublished_events()) == 0


def test_in_memory_unique_constraint() -> None:
    repo = InMemoryOutboxRepository()
    
    # Create event
    tenant_id = str(uuid4())
    agg_id = str(uuid4())
    
    event1 = DomainEvent(
        event_type="intake.state_changed",
        payload={
            "intake_id": agg_id,
            "from_state": "SUBMITTED",
            "to_state": "READY",
            "transition_id": str(uuid4()),
            "reason_code": "auto_approve",
            "version": 1,
            "occurred_at": datetime.now(UTC).isoformat()
        },
        tenant_id=tenant_id,
        aggregate_type="intake",
        aggregate_id=agg_id,
        aggregate_version=1,
        partition_key="tenant:intake",
        correlation_id=str(uuid4()),
        producer="listing_intake_service",
        schema_ref="#/payloads/IntakeStateChangedV1"
    )
    
    # Same identifiers but different event_id (simulating duplicate save)
    event2 = DomainEvent(
        event_type="intake.state_changed",
        payload={
            "intake_id": agg_id,
            "from_state": "SUBMITTED",
            "to_state": "READY",
            "transition_id": str(uuid4()),
            "reason_code": "auto_approve",
            "version": 1,
            "occurred_at": datetime.now(UTC).isoformat()
        },
        tenant_id=tenant_id,
        aggregate_type="intake",
        aggregate_id=agg_id,
        aggregate_version=1,
        partition_key="tenant:intake",
        correlation_id=str(uuid4()),
        producer="listing_intake_service",
        schema_ref="#/payloads/IntakeStateChangedV1",
        event_id=str(uuid4())
    )
    
    repo.save(event1)
    with pytest.raises(ValueError, match="Duplicate event"):
        repo.save(event2)


def test_durable_outbox_flow(engine) -> None:
    repo = DurableOutboxRepository(engine)
    event = make_valid_event()
    
    # Save
    repo.save(event)
    
    # Check unpublished
    unpublished = repo.get_unpublished_events()
    assert len(unpublished) == 1
    assert unpublished[0].event_id == event.event_id
    
    # Claim
    claimed = repo.claim_batch(locked_by="publisher-1", lease_seconds=10)
    assert len(claimed) == 1
    assert claimed[0].event_id == event.event_id
    
    # Once claimed, it's locked, so it shouldn't show up in get_unpublished_events
    assert len(repo.get_unpublished_events()) == 0
    
    # Mark published
    repo.mark_published(event.event_id, published_message_id="msg-456")
    assert len(repo.get_unpublished_events()) == 0
    
    # Verify in DB
    row = engine.query_one("SELECT * FROM durable_outbox_events WHERE event_id = ?", (event.event_id,))
    assert row["published_at"] is not None
    assert row["published_message_id"] == "msg-456"


def test_durable_outbox_failures_and_backoff(engine) -> None:
    repo = DurableOutboxRepository(engine)
    event = make_valid_event()
    repo.save(event)
    
    # Claim
    repo.claim_batch(locked_by="pub-1")
    
    # Mark failed
    repo.mark_failed(event.event_id, error_message="database down")
    
    # Should not be immediately available due to backoff delay
    assert len(repo.get_unpublished_events()) == 0
    
    # Check attempts
    row = engine.query_one("SELECT * FROM durable_outbox_events WHERE event_id = ?", (event.event_id,))
    assert row["publish_attempts"] == 1
    assert row["last_error"] == "database down"
    
    # Simulate DLQ up to 10 attempts
    for _ in range(9):
        repo.mark_failed(event.event_id, error_message="still failing")
    
    row2 = engine.query_one("SELECT * FROM durable_outbox_events WHERE event_id = ?", (event.event_id,))
    assert row2["publish_attempts"] == 10
    # Available at is set to max time
    assert row2["available_at"].startswith("9999-")
    assert len(repo.get_unpublished_events()) == 0


def test_durable_unique_constraint(engine) -> None:
    repo = DurableOutboxRepository(engine)
    
    tenant_id = str(uuid4())
    agg_id = str(uuid4())
    
    event1 = DomainEvent(
        event_type="intake.state_changed",
        payload={
            "intake_id": agg_id,
            "from_state": "SUBMITTED",
            "to_state": "READY",
            "transition_id": str(uuid4()),
            "reason_code": "auto_approve",
            "version": 1,
            "occurred_at": datetime.now(UTC).isoformat()
        },
        tenant_id=tenant_id,
        aggregate_type="intake",
        aggregate_id=agg_id,
        aggregate_version=1,
        partition_key="tenant:intake",
        correlation_id=str(uuid4()),
        producer="listing_intake_service",
        schema_ref="#/payloads/IntakeStateChangedV1"
    )
    
    event2 = DomainEvent(
        event_type="intake.state_changed",
        payload={
            "intake_id": agg_id,
            "from_state": "SUBMITTED",
            "to_state": "READY",
            "transition_id": str(uuid4()),
            "reason_code": "auto_approve",
            "version": 1,
            "occurred_at": datetime.now(UTC).isoformat()
        },
        tenant_id=tenant_id,
        aggregate_type="intake",
        aggregate_id=agg_id,
        aggregate_version=1,
        partition_key="tenant:intake",
        correlation_id=str(uuid4()),
        producer="listing_intake_service",
        schema_ref="#/payloads/IntakeStateChangedV1",
        event_id=str(uuid4())
    )
    
    repo.save(event1)
    with pytest.raises(ValueError, match="Duplicate event"):
        repo.save(event2)
