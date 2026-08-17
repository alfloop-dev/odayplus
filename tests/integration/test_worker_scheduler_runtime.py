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

import threading
import time
from datetime import date, timedelta

import pytest

from apps.scheduler.oday_scheduler.main import ODayScheduler
from apps.worker.oday_worker.main import ODayWorker
from modules.external_data.workers.scheduled_fetch import (
    TenantScopedExternalFetchStateStore,
)
from modules.forecastops import ForecastOpsService, StoreDayObservation
from modules.forecastops.runtime import ForecastOpsRuntimeConfigurationError
from modules.forecastops.workers import ForecastOpsForecastWorker
from shared.infrastructure.persistence.factory import _durable_bundle, build_persistence
from shared.jobs.queue import JobRequest, JobStatus
from shared.jobs.registry import JobRegistry

PROVIDER_ID = "listing.partner_feed"
TENANT_ID = "tenant-test"
OTHER_TENANT_ID = "tenant-other"


def _tenant_watermark(bundle, tenant_id: str = TENANT_ID):
    """Watermark as the scheduled path writes it: namespaced per tenant."""
    scoped = TenantScopedExternalFetchStateStore(
        bundle.external_fetch_state_store, tenant_id
    )
    return scoped.last_success_watermark(PROVIDER_ID)


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "durable.sqlite3")


def _queued_of_type(bundle, job_type: str) -> list:
    return [rec for rec in bundle.job_queue._jobs.values() if rec.job_type == job_type]


def _seed_forecast_series(bundle, store_id: str) -> None:
    start = date(2026, 4, 1)
    ForecastOpsService(repository=bundle.forecastops_repository).ingest_timeseries(
        (
            StoreDayObservation(
                store_id=store_id,
                business_date=start + timedelta(days=index),
                actual_revenue=80_000 + index * 250 + (index % 7) * 900,
                source_snapshot_ids=(f"pos-{index:03d}",),
            )
            for index in range(70)
        ),
        tenant_id=TENANT_ID,
    )


def test_scheduler_enqueue_then_worker_claim_execute_success() -> None:
    """Scheduler enqueues external-fetch; worker claims, runs it, marks SUCCEEDED."""
    bundle = build_persistence()  # in-memory
    scheduler = ODayScheduler(persistence=bundle, tenant_id=TENANT_ID)
    worker = ODayWorker(persistence=bundle)

    scheduler.run_once()
    queued = _queued_of_type(bundle, "external-fetch")
    assert len(queued) == 1
    assert queued[0].status == JobStatus.QUEUED
    job_id = queued[0].job_id

    assert worker.run_once() is True

    executed = bundle.job_queue.get(job_id)
    assert executed.status == JobStatus.SUCCEEDED
    # The fetch advanced the success watermark for this tenant's provider,
    # and only for that tenant.
    assert _tenant_watermark(bundle) is not None
    assert _tenant_watermark(bundle, OTHER_TENANT_ID) is None
    assert bundle.external_fetch_state_store.last_success_watermark(PROVIDER_ID) is None

    # No more queued work -> the next claim is a no-op.
    assert worker.run_once() is False


def test_worker_forecast_job_claims_and_succeeds() -> None:
    """A forecast job is claimed, executed against the repository, and succeeds."""
    bundle = build_persistence()
    _seed_forecast_series(bundle, "store-001")
    job, created = bundle.job_queue.enqueue(
        JobRequest(
            job_type="forecast",
            payload={"tenant_id": TENANT_ID, "store_id": "store-001"},
        ),
        correlation_id="corr-forecast",
    )
    assert created is True

    worker = ODayWorker(persistence=bundle)
    assert worker.run_once() is True
    assert bundle.job_queue.get(job.job_id).status == JobStatus.SUCCEEDED


def test_production_forecast_worker_rejects_unregistered_baseline_configuration(
    db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setenv("ODP_PRODUCT_MODE", "production")
    monkeypatch.setenv("ODP_FORECAST_ENGINE", "statsforecast")
    monkeypatch.setenv("ODP_FORECAST_MODEL", "seasonal_naive")
    bundle = _durable_bundle(db_path)
    try:
        with pytest.raises(
            ForecastOpsRuntimeConfigurationError,
            match="registered MLflow estimator runtime",
        ):
            ForecastOpsForecastWorker(repository=bundle.forecastops_repository)
    finally:
        bundle.engine.close()


def test_worker_renews_lease_and_completes_with_latest_version() -> None:
    bundle = build_persistence()
    registry = JobRegistry()

    def slow_handler(_job, _persistence) -> None:
        time.sleep(0.06)

    registry.register("slow-success", slow_handler)
    job, _ = bundle.job_queue.enqueue(
        JobRequest(job_type="slow-success", payload={}),
        correlation_id="corr-slow-success",
    )
    worker = ODayWorker(
        persistence=bundle,
        registry=registry,
        heartbeat_interval_seconds=0.01,
    )

    assert worker.run_once() is True
    persisted = bundle.job_queue.get(job.job_id)
    assert persisted is not None
    assert persisted.status == JobStatus.SUCCEEDED
    assert persisted.version >= 4
    assert persisted.locked_by is None
    assert persisted.lease_expires_at is None


def test_stale_worker_cannot_overwrite_replacement_worker(tmp_path) -> None:
    bundle = _durable_bundle(str(tmp_path / "stale-worker.sqlite3"))
    registry = JobRegistry()
    started = threading.Event()
    release = threading.Event()

    def blocked_handler(_job, _persistence) -> None:
        started.set()
        assert release.wait(timeout=5)

    registry.register("lease-stolen", blocked_handler)
    job, _ = bundle.job_queue.enqueue(
        JobRequest(job_type="lease-stolen", payload={}),
        correlation_id="corr-lease-stolen",
    )
    worker = ODayWorker(
        persistence=bundle,
        registry=registry,
        heartbeat_interval_seconds=0.01,
    )
    thread = threading.Thread(target=worker.run_once)
    try:
        thread.start()
        assert started.wait(timeout=5)
        time.sleep(0.03)
        current = bundle.job_queue.get(job.job_id)
        assert current is not None
        bundle.engine.execute(
            "UPDATE durable_jobs SET fence_token = ?, version = ?, locked_by = ? WHERE job_id = ?",
            (
                current.fence_token + 1,
                current.version + 1,
                "replacement-worker",
                job.job_id,
            ),
        )
        release.set()
        thread.join(timeout=5)
        assert not thread.is_alive()

        persisted = bundle.job_queue.get(job.job_id)
        assert persisted is not None
        assert persisted.status == JobStatus.RUNNING
        assert persisted.locked_by == "replacement-worker"
        assert persisted.fence_token == current.fence_token + 1
    finally:
        release.set()
        thread.join(timeout=5)
        bundle.engine.close()


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
    scheduler = ODayScheduler(persistence=bundle, tenant_id=TENANT_ID)

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
        ODayScheduler(persistence=bundle, tenant_id=TENANT_ID).run_once()
        assert ODayWorker(persistence=bundle).run_once() is True
        watermark = _tenant_watermark(bundle)
        assert watermark is not None
    finally:
        bundle.engine.close()

    # Simulate process restart: new bundle pointed at the same on-disk file.
    reopened = _durable_bundle(db_path)
    try:
        persisted = _tenant_watermark(reopened)
        assert persisted is not None
        assert persisted == watermark
        # A different tenant does not inherit the restored watermark.
        assert _tenant_watermark(reopened, OTHER_TENANT_ID) is None
    finally:
        reopened.engine.close()
