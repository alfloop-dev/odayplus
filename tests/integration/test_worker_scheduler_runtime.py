"""Worker + scheduler runtime wiring (ODP-GAP-RUNTIME-001).

These tests exercise the runtime layer that replaced the heartbeat-only worker
and scheduler processes:

1. ``ODayScheduler`` enqueues a scheduled external-fetch job and ``ODayWorker``
   claims, executes, and marks it ``SUCCEEDED`` end-to-end.
2. ``ODayWorker`` retries a failing job three times and then dead-letters it to
   ``FAILED`` (the claim -> execute -> retry -> dead-letter path).
3. Scheduler enqueue is idempotent within a schedule window.
4. ``DurableExternalFetchStateStore`` persists the success watermark across a
   simulated process restart (build_persistence(durable) -> close -> reopen).
"""

from __future__ import annotations

import pytest

from apps.scheduler.oday_scheduler.main import ODayScheduler
from apps.worker.oday_worker.main import ODayWorker
from shared.infrastructure.persistence.factory import _durable_bundle, build_persistence
from shared.jobs.queue import JobRequest, JobStatus

PROVIDER_ID = "listing.partner_feed"


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "durable.sqlite3")


def _queued_of_type(bundle, job_type: str) -> list:
    return [rec for rec in bundle.job_queue._jobs.values() if rec.job_type == job_type]


def test_scheduler_enqueue_then_worker_claim_execute_success() -> None:
    """Scheduler enqueues external-fetch; worker claims, runs it, marks SUCCEEDED."""
    bundle = build_persistence()  # in-memory
    scheduler = ODayScheduler(persistence=bundle)
    worker = ODayWorker(persistence=bundle)

    scheduler.run_once()
    queued = _queued_of_type(bundle, "external-fetch")
    assert len(queued) == 1
    assert queued[0].status == JobStatus.QUEUED
    job_id = queued[0].job_id

    assert worker.run_once() is True

    executed = bundle.job_queue.get(job_id)
    assert executed.status == JobStatus.SUCCEEDED
    # The fetch advanced the durable success watermark for the provider.
    assert bundle.external_fetch_state_store.last_success_watermark(PROVIDER_ID) is not None

    # No more queued work -> the next claim is a no-op.
    assert worker.run_once() is False


def test_worker_forecast_job_claims_and_succeeds() -> None:
    """A forecast job is claimed, executed against the repository, and succeeds."""
    bundle = build_persistence()
    job, created = bundle.job_queue.enqueue(
        JobRequest(job_type="forecast", payload={"store_id": "store-001"}),
        correlation_id="corr-forecast",
    )
    assert created is True

    worker = ODayWorker(persistence=bundle)
    assert worker.run_once() is True
    assert bundle.job_queue.get(job.job_id).status == JobStatus.SUCCEEDED


def test_worker_retries_three_times_then_dead_letters() -> None:
    """A permanently failing job requeues 3 times, then is marked FAILED."""
    bundle = build_persistence()
    job, _ = bundle.job_queue.enqueue(
        JobRequest(job_type="does-not-exist", payload={}),
        correlation_id="corr-fail",
    )

    worker = ODayWorker(persistence=bundle)

    # Attempts 1-3 requeue with an incrementing retry counter.
    for expected_retry in (1, 2, 3):
        assert worker.run_once() is True
        record = bundle.job_queue.get(job.job_id)
        assert record.status == JobStatus.QUEUED
        assert record.payload["_retry_count"] == expected_retry

    # Fourth attempt exhausts retries and dead-letters the job.
    assert worker.run_once() is True
    assert bundle.job_queue.get(job.job_id).status == JobStatus.FAILED

    # Dead-lettered job is no longer claimable.
    assert worker.run_once() is False


def test_scheduler_enqueue_is_idempotent_within_window() -> None:
    """Two scheduler ticks in the same window produce a single external-fetch job."""
    bundle = build_persistence()
    scheduler = ODayScheduler(persistence=bundle)

    scheduler.run_once()
    scheduler.run_once()

    assert len(_queued_of_type(bundle, "external-fetch")) == 1


def test_queue_enqueue_is_idempotent_by_key() -> None:
    """Deterministic queue-level idempotency: same key never duplicates a job."""
    bundle = build_persistence()
    request = JobRequest(
        job_type="external-fetch",
        payload={"provider_id": PROVIDER_ID},
        idempotency_key="scheduled-fetch:fixed-window",
    )

    first, created_first = bundle.job_queue.enqueue(request, correlation_id="c1")
    second, created_second = bundle.job_queue.enqueue(request, correlation_id="c2")

    assert created_first is True
    assert created_second is False
    assert first.job_id == second.job_id
    assert len(_queued_of_type(bundle, "external-fetch")) == 1


def test_durable_watermark_persists_across_restart(db_path) -> None:
    """Success watermark written through the worker survives a process restart."""
    bundle = _durable_bundle(db_path)
    try:
        ODayScheduler(persistence=bundle).run_once()
        assert ODayWorker(persistence=bundle).run_once() is True
        watermark = bundle.external_fetch_state_store.last_success_watermark(PROVIDER_ID)
        assert watermark is not None
    finally:
        bundle.engine.close()

    # Simulate process restart: new bundle pointed at the same on-disk file.
    reopened = _durable_bundle(db_path)
    try:
        persisted = reopened.external_fetch_state_store.last_success_watermark(PROVIDER_ID)
        assert persisted is not None
        assert persisted == watermark
    finally:
        reopened.engine.close()
