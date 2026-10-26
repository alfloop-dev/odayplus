"""Delivery state must not survive a settled business outcome (ODP-FR-SHARED-001).

``JobStatus`` records what happened to the work; ``JobDeliveryState`` records
what the queue is still doing about delivering it. Before this suite,
``update_status`` only cleared ``delivery_state`` for ``SUCCEEDED``: writing
``PARTIAL`` or ``CANCELLED`` with ``delivery_state=None`` emitted no assignment
at all, so a job that had previously been retried stayed persisted as
``status=PARTIAL, delivery_state=RETRYING`` — a finished job that still claims
to be awaiting redelivery.

Every case here first drives the job into a real ``RETRYING`` through the
worker's retryable-exception path in ``apps/worker/oday_worker/main.py`` so a
passing assertion cannot be explained by the job never having had a delivery
state at all.

Scope note: these are synthetic in-repo fixtures over a temp SQLite file and an
in-process queue. They exercise queue/API semantics only; no batch PARTIAL
producer, no live data, and no external service is involved.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from apps.worker.oday_worker.main import ODayWorker
from shared.infrastructure.persistence.factory import _durable_bundle, _memory_bundle
from shared.jobs.queue import (
    DELIVERY_SETTLED_JOB_STATUSES,
    JobDeliveryState,
    JobRequest,
    JobStatus,
)
from shared.jobs.registry import JobRegistry

# Neutral synthetic type: not in JOB_FEATURE_FLAG_MAP (so no kill-switch is
# consulted), not a ``.receipt`` suffix (so the worker will actually claim it),
# and not ``forecast`` (so the read route applies no tenant scoping).
PROBE_JOB_TYPE = "delivery-state-clear-probe"


class _TransientHandlerError(RuntimeError):
    """A retryable failure: not a NonRetryableJobError, so the loop requeues."""


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "job_delivery_state_clear.sqlite3")


def _retrying_registry() -> JobRegistry:
    registry = JobRegistry()
    registry.register(
        PROBE_JOB_TYPE,
        lambda job, persistence: (_ for _ in ()).throw(
            _TransientHandlerError("synthetic transient delivery failure")
        ),
    )
    return registry


def _drive_job_to_retrying(bundle, *, idempotency_key: str) -> str:
    """Enqueue a job and let the real worker loop retry it into RETRYING.

    Returns the job id. Asserts the precondition rather than assuming it, so a
    later "delivery_state is None" assertion means *cleared*, not *never set*.
    """
    job, created = bundle.job_queue.enqueue(
        JobRequest(
            job_type=PROBE_JOB_TYPE,
            payload={"probe": idempotency_key},
            idempotency_key=idempotency_key,
        ),
        correlation_id=f"corr-{idempotency_key}",
    )
    assert created is True
    assert job.delivery_state is None

    worker = ODayWorker(
        persistence=bundle,
        registry=_retrying_registry(),
        heartbeat_interval_seconds=60.0,
    )
    assert worker.run_once() is True

    retrying = bundle.job_queue.get(job.job_id)
    assert retrying is not None
    assert retrying.status == JobStatus.QUEUED
    assert retrying.delivery_state == JobDeliveryState.RETRYING
    return job.job_id


def _raw_row(bundle, job_id: str):
    row = bundle.engine.query_one(
        "SELECT status, delivery_state FROM durable_jobs WHERE job_id = ?",
        (job_id,),
    )
    assert row is not None
    return row


def _current_version(bundle, job_id: str) -> int:
    record = bundle.job_queue.get(job_id)
    assert record is not None
    return record.version


def test_settled_status_set_excludes_failed_and_in_flight_statuses() -> None:
    """The clearing rule is a named set, not a scattered status comparison."""
    assert DELIVERY_SETTLED_JOB_STATUSES == frozenset(
        {JobStatus.SUCCEEDED, JobStatus.PARTIAL, JobStatus.CANCELLED}
    )
    # FAILED is excluded on purpose: DEAD_LETTER is delivery information the
    # caller still needs after the outcome is known.
    assert JobStatus.FAILED not in DELIVERY_SETTLED_JOB_STATUSES
    assert JobStatus.QUEUED not in DELIVERY_SETTLED_JOB_STATUSES
    assert JobStatus.RUNNING not in DELIVERY_SETTLED_JOB_STATUSES


@pytest.mark.parametrize(
    "settled_status",
    [JobStatus.PARTIAL, JobStatus.CANCELLED, JobStatus.SUCCEEDED],
)
def test_durable_settled_status_clears_retrying_in_the_database(
    db_path, settled_status: JobStatus
) -> None:
    bundle = _durable_bundle(db_path)
    try:
        job_id = _drive_job_to_retrying(
            bundle, idempotency_key=f"durable-clear-{settled_status.value}"
        )
        # Precondition read straight off the row, not off a queue object.
        assert _raw_row(bundle, job_id)["delivery_state"] == JobDeliveryState.RETRYING.value

        bundle.job_queue.update_status(job_id, settled_status, delivery_state=None)

        row = _raw_row(bundle, job_id)
        assert row["status"] == settled_status.value
        assert row["delivery_state"] is None
    finally:
        bundle.engine.close()

    # Rebuild the queue over the same file: the cleared value is durable, and
    # no read-time inference puts RETRYING back.
    reopened = _durable_bundle(db_path)
    try:
        record = reopened.job_queue.get(job_id)
        assert record is not None
        assert record.status == settled_status
        assert record.delivery_state is None
        assert record.to_dict()["delivery_state"] is None
    finally:
        reopened.engine.close()


def test_durable_failed_keeps_explicitly_written_dead_letter(db_path) -> None:
    bundle = _durable_bundle(db_path)
    try:
        job_id = _drive_job_to_retrying(bundle, idempotency_key="durable-dead-letter")

        bundle.job_queue.update_status(
            job_id,
            JobStatus.FAILED,
            delivery_state=JobDeliveryState.DEAD_LETTER,
            error_message="retry budget exhausted",
        )

        row = _raw_row(bundle, job_id)
        assert row["status"] == JobStatus.FAILED.value
        assert row["delivery_state"] == JobDeliveryState.DEAD_LETTER.value
    finally:
        bundle.engine.close()

    reopened = _durable_bundle(db_path)
    try:
        record = reopened.job_queue.get(job_id)
        assert record is not None
        assert record.status == JobStatus.FAILED
        assert record.delivery_state == JobDeliveryState.DEAD_LETTER
    finally:
        reopened.engine.close()


@pytest.mark.parametrize("in_flight_status", [JobStatus.QUEUED, JobStatus.RUNNING])
def test_durable_unspecified_delivery_state_is_left_alone_in_flight(
    db_path, in_flight_status: JobStatus
) -> None:
    """``delivery_state=None`` still means "unspecified" for non-settled writes."""
    bundle = _durable_bundle(db_path)
    try:
        job_id = _drive_job_to_retrying(
            bundle, idempotency_key=f"durable-inflight-{in_flight_status.value}"
        )

        bundle.job_queue.update_status(job_id, in_flight_status)

        row = _raw_row(bundle, job_id)
        assert row["status"] == in_flight_status.value
        assert row["delivery_state"] == JobDeliveryState.RETRYING.value

        record = bundle.job_queue.get(job_id)
        assert record is not None
        assert record.delivery_state == JobDeliveryState.RETRYING
    finally:
        bundle.engine.close()


def test_durable_settled_clear_respects_fencing(db_path) -> None:
    """The new clearing branch does not bypass optimistic concurrency control."""
    from shared.infrastructure.persistence.job_queue import JobFenceRejectedError

    bundle = _durable_bundle(db_path)
    try:
        job_id = _drive_job_to_retrying(bundle, idempotency_key="durable-fence")
        stale_version = _current_version(bundle, job_id) - 1

        with pytest.raises(JobFenceRejectedError):
            bundle.job_queue.update_status(
                job_id,
                JobStatus.PARTIAL,
                delivery_state=None,
                expected_version=stale_version,
            )

        # The rejected write must not have cleared anything.
        assert _raw_row(bundle, job_id)["delivery_state"] == JobDeliveryState.RETRYING.value
    finally:
        bundle.engine.close()


@pytest.mark.parametrize(
    "settled_status",
    [JobStatus.PARTIAL, JobStatus.CANCELLED, JobStatus.SUCCEEDED],
)
def test_in_memory_twin_clears_retrying_identically(settled_status: JobStatus) -> None:
    """In-memory parity: otherwise a durable defect hides behind a green test."""
    bundle = _memory_bundle()
    job_id = _drive_job_to_retrying(
        bundle, idempotency_key=f"memory-clear-{settled_status.value}"
    )

    bundle.job_queue.update_status(job_id, settled_status, delivery_state=None)

    record = bundle.job_queue.get(job_id)
    assert record is not None
    assert record.status == settled_status
    assert record.delivery_state is None
    assert record.to_dict()["delivery_state"] is None


def test_in_memory_twin_keeps_dead_letter_and_in_flight_retrying() -> None:
    bundle = _memory_bundle()

    dead_letter_id = _drive_job_to_retrying(bundle, idempotency_key="memory-dead-letter")
    bundle.job_queue.update_status(
        dead_letter_id,
        JobStatus.FAILED,
        delivery_state=JobDeliveryState.DEAD_LETTER,
    )
    dead_lettered = bundle.job_queue.get(dead_letter_id)
    assert dead_lettered is not None
    assert dead_lettered.status == JobStatus.FAILED
    assert dead_lettered.delivery_state == JobDeliveryState.DEAD_LETTER

    in_flight_id = _drive_job_to_retrying(bundle, idempotency_key="memory-inflight")
    bundle.job_queue.update_status(in_flight_id, JobStatus.RUNNING)
    in_flight = bundle.job_queue.get(in_flight_id)
    assert in_flight is not None
    assert in_flight.status == JobStatus.RUNNING
    assert in_flight.delivery_state == JobDeliveryState.RETRYING


def test_job_read_api_serializes_cleared_and_retained_delivery_state(db_path) -> None:
    """The existing job read endpoint must publish the cleared value, not a stale one."""
    bundle = _durable_bundle(db_path)
    try:
        client = TestClient(create_app(persistence=bundle))

        # One job at a time: ``claim_next`` orders by ``created_at``, so a job
        # left sitting in QUEUED+RETRYING would be re-claimed instead of the
        # next one enqueued.
        partial_id = _drive_job_to_retrying(bundle, idempotency_key="api-partial")

        retrying_body = client.get(f"/api/v1/jobs/{partial_id}").json()
        assert retrying_body["status"] == JobStatus.QUEUED.value
        assert retrying_body["delivery_state"] == JobDeliveryState.RETRYING.value

        bundle.job_queue.update_status(partial_id, JobStatus.PARTIAL, delivery_state=None)

        dead_letter_id = _drive_job_to_retrying(bundle, idempotency_key="api-dead-letter")
        bundle.job_queue.update_status(
            dead_letter_id,
            JobStatus.FAILED,
            delivery_state=JobDeliveryState.DEAD_LETTER,
        )

        partial_response = client.get(f"/api/v1/jobs/{partial_id}")
        assert partial_response.status_code == 200
        partial_body = partial_response.json()
        assert partial_body["status"] == JobStatus.PARTIAL.value
        assert partial_body["delivery_state"] is None

        dead_letter_response = client.get(f"/api/v1/jobs/{dead_letter_id}")
        assert dead_letter_response.status_code == 200
        dead_letter_body = dead_letter_response.json()
        assert dead_letter_body["status"] == JobStatus.FAILED.value
        assert dead_letter_body["delivery_state"] == JobDeliveryState.DEAD_LETTER.value
    finally:
        bundle.engine.close()
