from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from shared.domain.events import CONSUMER_MODE, DomainEvent, validate_event


@pytest.fixture
def snapshot_event():
    def create(event_type, producer, changes):
        tenant, intake, snapshot = str(uuid4()), str(uuid4()), str(uuid4())
        return DomainEvent(event_type=event_type, producer=producer, tenant_id=tenant,
            aggregate_type="source_snapshot", aggregate_id=snapshot, aggregate_version=1,
            partition_key=f"{tenant}:{intake}", correlation_id=str(uuid4()),
            schema_ref="#/payloads/SnapshotCreatedV1", sensitive_fields=["payload.object_uri"],
            payload={"source_snapshot_id": snapshot, "intake_id": intake, "source_id": "synthetic-source",
                "content_sha256": "a" * 64, "captured_at": "2026-10-17T00:00:00+00:00",
                "retention_class": "SOURCE_RAW", "residency_mode": "TW_ONLY", "object_generation": 9,
                **changes})
    return create


@pytest.mark.parametrize("event_type,producer", [("snapshot.created", "retrieval_service"),
    ("snapshot.manual_entry_created", "assisted_entry_service")])
@pytest.mark.parametrize("mode", ["producer", CONSUMER_MODE])
@pytest.mark.parametrize("field,value", [("content_sha256", "invalid-digest"),
    ("content_sha256", "A" * 64), ("captured_at", "2026-99-99T99:99:99+00:00"),
    ("captured_at", "2026-02-30T01:02:03Z"), ("captured_at", "2026-10-17T01:02:03"),
    ("captured_at", "2026-10-17T01:02:03+00:60"), ("captured_at", "2026-10-17T01:02:03+24:00"),
    ("captured_at", "2026-10-17 01:02:03+00:00")])
def test_snapshot_declared_digest_and_calendar_constraints_apply_in_both_modes(snapshot_event,
        event_type, producer, mode, field, value):
    errors = validate_event(snapshot_event(event_type, producer, {field: value}), mode=mode)
    assert any(field in error and ("pattern" if field == "content_sha256" else "format") in error for error in errors)


@pytest.mark.parametrize("event_type,producer", [("snapshot.created", "retrieval_service"),
    ("snapshot.manual_entry_created", "assisted_entry_service")])
@pytest.mark.parametrize("captured_at", ["2026-10-17T00:00:00Z", "2026-10-17t08:00:00z",
    "2026-10-17T08:00:00.123456+08:00", "2024-02-29T12:34:56-03:30"])
def test_snapshot_accepts_valid_calendar_dates_and_rfc3339_offsets(snapshot_event, event_type, producer, captured_at):
    event = snapshot_event(event_type, producer, {"captured_at": captured_at})
    assert validate_event(event) == []
    assert validate_event(event, mode=CONSUMER_MODE) == []


def test_declared_patterns_keep_json_schema_search_semantics():
    from shared.domain.events import EventContractValidator

    validator = EventContractValidator()
    schema = {"type": "string", "pattern": "observed"}
    assert validator.validate_schema("prefix-observed-suffix", schema) == []
    assert validator.validate_schema("missing", schema)


@pytest.mark.parametrize("invalid_field", [None, "source_listing_id", "listing_id"])
def test_candidate_created_uses_the_effective_closed_payload_registry(invalid_field) -> None:
    """The later complete registry overrides the catalog's old field summary."""
    payload = {
        "candidate_site_id": str(uuid4()), "property_id": str(uuid4()),
        "listing_id": str(uuid4()), "promotion_decision_id": str(uuid4()),
        "target_format_code": "FMT-MICRO-STORE", "status": "NEW", "version": 1,
    }
    if invalid_field == "source_listing_id":
        payload[invalid_field] = str(uuid4())
    elif invalid_field == "listing_id":
        del payload[invalid_field]
    tenant_id = str(uuid4())
    event = DomainEvent(
        event_type="candidate.created", payload=payload, tenant_id=tenant_id,
        aggregate_type="candidate_site", aggregate_id=payload["candidate_site_id"],
        aggregate_version=1, partition_key=f"{tenant_id}:{payload['candidate_site_id']}",
        correlation_id=str(uuid4()), producer="candidate_promotion_service",
        schema_ref="#/payloads/CandidateCreatedV1",
    )
    errors = validate_event(event)
    if invalid_field is None:
        assert errors == []
    else:
        assert errors and any(invalid_field in error for error in errors)


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


def _assignment_claimed_event(extra_payload: dict[str, object] | None = None) -> DomainEvent:
    payload: dict[str, object] = {
        "assignment_id": str(uuid4()),
        "intake_id": str(uuid4()),
        "from_status": "ASSIGNED",
        "to_status": "CLAIMED",
        "owner_subject_id": str(uuid4()),
        "version": 1,
        "occurred_at": datetime.now(UTC).isoformat(),
    }
    payload.update(extra_payload or {})
    return DomainEvent(
        event_type="assignment.claimed",
        payload=payload,
        tenant_id=str(uuid4()),
        aggregate_type="assignment",
        aggregate_id=str(uuid4()),
        aggregate_version=1,
        partition_key="tenant:intake",
        correlation_id=str(uuid4()),
        producer="workflow_service",
        schema_ref="#/payloads/AssignmentStateChangedV1",
    )


def test_producer_mode_rejects_unknown_payload_field() -> None:
    # The payload schemas are closed, so an unknown field is a producer defect and
    # must still be caught at publish time.
    event = _assignment_claimed_event({"claimed_via": "mobile"})

    errors = validate_event(event)
    assert any("Additional property not allowed: claimed_via" in err for err in errors)


def test_consumer_mode_ignores_unknown_payload_field() -> None:
    # compatibility_policy in ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1.yaml:
    # "consumers must ignore unknown optional fields". A consumer on an older
    # minor version must keep accepting events from a newer producer.
    event = _assignment_claimed_event({"claimed_via": "mobile"})

    errors = validate_event(event, mode=CONSUMER_MODE)
    assert not errors, f"Consumer must ignore unknown optional fields: {errors}"


def test_consumer_mode_still_enforces_required_fields_and_enums() -> None:
    # Ignoring unknown fields must not degrade into accepting anything.
    missing_required = _assignment_claimed_event()
    del missing_required.payload["owner_subject_id"]
    errors = validate_event(missing_required, mode=CONSUMER_MODE)
    assert any("Missing required field: owner_subject_id" in err for err in errors)

    bad_enum = _assignment_claimed_event()
    bad_enum.payload["to_status"] = "NOT_A_STATUS"
    errors = validate_event(bad_enum, mode=CONSUMER_MODE)
    assert any("is not in enum" in err for err in errors)


def test_unknown_validation_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown validation mode"):
        validate_event(_assignment_claimed_event(), mode="lenient")


def test_consumer_does_not_dlq_event_carrying_unknown_optional_field() -> None:
    # End-to-end forward compatibility: the worker consumer must process, not
    # dead-letter, an event produced by a newer minor version.
    from apps.worker.consumers.assisted_listing_intake import AssistedListingIntakeConsumer
    from shared.infrastructure.persistence.factory import build_persistence

    persistence = build_persistence(mode="memory")
    consumer = AssistedListingIntakeConsumer(persistence, max_attempts=3)

    received: list[DomainEvent] = []
    consumer.register_handler("assignment.claimed", received.append)

    event = _assignment_claimed_event({"claimed_via": "mobile"})
    consumer.consume(event)

    assert len(received) == 1, "Unknown optional field must not send the event to the DLQ"
    assert received[0].event_id == event.event_id
