from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from shared.domain.events import DomainEvent, validate_event


def test_valid_event_contract() -> None:
    # Construct a valid event based on intake.submitted
    event = DomainEvent(
        event_type="intake.submitted",
        payload={
            "intake_id": str(uuid4()),
            "intake_method": "URL",
            "submitter_subject_id": str(uuid4()),
            "submitted_at": datetime.now(UTC).isoformat(),
            "original_url": "https://example.com/listings/123",
            "source_id": "partner-1",
            "scope": {
                "tenant_id": str(uuid4()),
                "brand_id": str(uuid4()),
                "region_id": str(uuid4()),
            },
        },
        tenant_id=str(uuid4()),
        aggregate_type="intake",
        aggregate_id=str(uuid4()),
        aggregate_version=1,
        partition_key="tenant:intake",
        correlation_id=str(uuid4()),
        producer="listing_intake_service",
        schema_ref="#/payloads/IntakeSubmittedV1",
        sensitive_fields=["payload.original_url"],
    )

    errors = validate_event(event)
    assert not errors, f"Event should be valid: {errors}"


def test_invalid_envelope_missing_fields() -> None:
    # Missing required tenant_id
    event_dict = {
        "event_id": str(uuid4()),
        "event_type": "intake.submitted",
        "event_version": 1,
        "occurred_at": datetime.now(UTC).isoformat(),
        "producer": "listing_intake_service",
        # "tenant_id" is missing
        "aggregate_type": "intake",
        "aggregate_id": str(uuid4()),
        "aggregate_version": 1,
        "partition_key": "tenant:intake",
        "correlation_id": str(uuid4()),
        "payload": {},
    }

    errors = validate_event(event_dict)
    assert any("Envelope missing required field" in err for err in errors)


def test_invalid_envelope_bad_format() -> None:
    # Bad UUID format
    event = DomainEvent(
        event_type="intake.submitted",
        payload={},
        tenant_id="not-a-uuid",
        aggregate_type="intake",
        aggregate_id=str(uuid4()),
        aggregate_version=1,
        partition_key="tenant:intake",
        correlation_id=str(uuid4()),
        producer="listing_intake_service",
        schema_ref="#/payloads/IntakeSubmittedV1",
    )

    errors = validate_event(event)
    assert any("must be a valid UUID" in err for err in errors)


def test_invalid_payload_schema() -> None:
    # Missing required scope in IntakeSubmittedV1 payload
    event = DomainEvent(
        event_type="intake.submitted",
        payload={
            "intake_id": str(uuid4()),
            "intake_method": "URL",
            "submitter_subject_id": str(uuid4()),
            "submitted_at": datetime.now(UTC).isoformat(),
        },
        tenant_id=str(uuid4()),
        aggregate_type="intake",
        aggregate_id=str(uuid4()),
        aggregate_version=1,
        partition_key="tenant:intake",
        correlation_id=str(uuid4()),
        producer="listing_intake_service",
        schema_ref="#/payloads/IntakeSubmittedV1",
    )

    errors = validate_event(event)
    assert any("Missing required field: scope" in err for err in errors)


def test_invalid_payload_type() -> None:
    # intake_method has incorrect type/enum value
    event = DomainEvent(
        event_type="intake.submitted",
        payload={
            "intake_id": str(uuid4()),
            "intake_method": "INVALID_METHOD",
            "submitter_subject_id": str(uuid4()),
            "submitted_at": datetime.now(UTC).isoformat(),
            "scope": {"tenant_id": str(uuid4())},
        },
        tenant_id=str(uuid4()),
        aggregate_type="intake",
        aggregate_id=str(uuid4()),
        aggregate_version=1,
        partition_key="tenant:intake",
        correlation_id=str(uuid4()),
        producer="listing_intake_service",
        schema_ref="#/payloads/IntakeSubmittedV1",
        sensitive_fields=["payload.original_url"],
    )

    errors = validate_event(event)
    assert any("is not in enum" in err for err in errors)


def test_missing_sensitive_fields_declaration() -> None:
    # intake.submitted has sensitive_fields: [payload.original_url]
    # If the event doesn't list it in sensitive_fields, validation fails
    event = DomainEvent(
        event_type="intake.submitted",
        payload={
            "intake_id": str(uuid4()),
            "intake_method": "URL",
            "submitter_subject_id": str(uuid4()),
            "submitted_at": datetime.now(UTC).isoformat(),
            "original_url": "https://example.com/listings/123",
            "scope": {"tenant_id": str(uuid4())},
        },
        tenant_id=str(uuid4()),
        aggregate_type="intake",
        aggregate_id=str(uuid4()),
        aggregate_version=1,
        partition_key="tenant:intake",
        correlation_id=str(uuid4()),
        producer="listing_intake_service",
        schema_ref="#/payloads/IntakeSubmittedV1",
        sensitive_fields=[],  # Empty sensitive fields list
    )

    errors = validate_event(event)
    assert any("Expected sensitive field" in err for err in errors)


def test_assignment_claimed_addendum_event() -> None:
    # assignment.claimed is defined in the addendum
    event = DomainEvent(
        event_type="assignment.claimed",
        payload={
            "assignment_id": str(uuid4()),
            "intake_id": str(uuid4()),
            "from_status": "ASSIGNED",
            "to_status": "CLAIMED",
            "owner_subject_id": str(uuid4()),
            "version": 1,
            "occurred_at": datetime.now(UTC).isoformat(),
        },
        tenant_id=str(uuid4()),
        aggregate_type="assignment",
        aggregate_id=str(uuid4()),
        aggregate_version=1,
        partition_key="tenant:intake",
        correlation_id=str(uuid4()),
        producer="workflow_service",
        schema_ref="#/payloads/AssignmentStateChangedV1",
    )

    errors = validate_event(event)
    assert not errors, f"Addendum event should be valid: {errors}"


def test_consumer_processing() -> None:
    from apps.worker.consumers.assisted_listing_intake import AssistedListingIntakeConsumer
    from shared.infrastructure.persistence.factory import build_persistence

    persistence = build_persistence(mode="memory")
    consumer = AssistedListingIntakeConsumer(persistence, max_attempts=3)

    received_events = []

    def dummy_handler(event: DomainEvent) -> None:
        received_events.append(event)

    consumer.register_handler("assignment.claimed", dummy_handler)

    event = DomainEvent(
        event_type="assignment.claimed",
        payload={
            "assignment_id": str(uuid4()),
            "intake_id": str(uuid4()),
            "from_status": "ASSIGNED",
            "to_status": "CLAIMED",
            "owner_subject_id": str(uuid4()),
            "version": 1,
            "occurred_at": datetime.now(UTC).isoformat(),
        },
        tenant_id=str(uuid4()),
        aggregate_type="assignment",
        aggregate_id=str(uuid4()),
        aggregate_version=1,
        partition_key="tenant:intake",
        correlation_id=str(uuid4()),
        producer="workflow_service",
        schema_ref="#/payloads/AssignmentStateChangedV1",
    )

    consumer.consume(event)
    assert len(received_events) == 1
    assert received_events[0].event_id == event.event_id

    consumer.consume(event)
    assert len(received_events) == 1


def test_consumer_retry_and_dlq() -> None:
    from apps.worker.consumers.assisted_listing_intake import AssistedListingIntakeConsumer
    from shared.infrastructure.persistence.factory import build_persistence

    persistence = build_persistence(mode="memory")
    consumer = AssistedListingIntakeConsumer(persistence, max_attempts=3)

    call_count = 0

    def failing_handler(event: DomainEvent) -> None:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("continuous failure")

    consumer.register_handler("assignment.claimed", failing_handler)

    event = DomainEvent(
        event_type="assignment.claimed",
        payload={
            "assignment_id": str(uuid4()),
            "intake_id": str(uuid4()),
            "from_status": "ASSIGNED",
            "to_status": "CLAIMED",
            "owner_subject_id": str(uuid4()),
            "version": 1,
            "occurred_at": datetime.now(UTC).isoformat(),
        },
        tenant_id=str(uuid4()),
        aggregate_type="assignment",
        aggregate_id=str(uuid4()),
        aggregate_version=1,
        partition_key="tenant:intake",
        correlation_id=str(uuid4()),
        producer="workflow_service",
        schema_ref="#/payloads/AssignmentStateChangedV1",
    )

    with pytest.raises(RuntimeError, match="continuous failure"):
        consumer.consume(event)

    assert call_count == 3
    assert event.event_id in consumer._memory_dlq
    assert "continuous failure" in consumer._memory_dlq[event.event_id]["reason"]
