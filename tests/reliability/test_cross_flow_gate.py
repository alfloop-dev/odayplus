"""Cross-flow platform runtime gate (ODP-FLOW-011).

This is the capstone gate that proves the first-version deployment units
(ODP-SD-03 §4: core-api, worker, scheduler) compose against ONE durable
persistence bundle and drive a job through the shared state machine
(ODP-SD-08 §3.2) end to end, across service boundaries (ODP-AC-SD03-004),
with an audit trail (ODP-AC-SD08-003) that survives a process restart
(backup/recovery).

Flows exercised on a single migrated durable database:

1. Integration/External: the *scheduler* enqueues ``external-fetch``; the
   *worker* claims and executes it and the durable watermark advances.
2. Operations: a ``forecast`` job enqueued through the *core-api* ``/jobs``
   endpoint (crossing the API boundary + writing an audit event) is claimed and
   executed by the *worker* and a forecast is persisted.
3. Composition: domain jobs dispatch through a modular registry, not a
   monolithic switch; every enqueued ``job_type`` has a registered handler.
4. Recovery: reopening the database at the same path preserves the watermark.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from apps.api.server import SERVICE_BOUNDARIES, bootstrap_runtime, build_server, build_worker
from apps.worker.oday_worker.handlers import build_default_registry
from apps.worker.oday_worker.main import ODayWorker
from modules.forecastops import ForecastOpsService, StoreDayObservation
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.jobs.queue import JobStatus

PROVIDER_ID = "listing.partner_feed"


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "cross-flow.sqlite3")


def _drain(worker: ODayWorker, limit: int = 25) -> int:
    """Run the worker until the queue is empty; return jobs executed."""
    executed = 0
    for _ in range(limit):
        if not worker.run_once():
            break
        executed += 1
    return executed


def _seed_forecast_series(bundle, store_id: str) -> None:
    start = date(2026, 4, 1)
    ForecastOpsService(repository=bundle.forecastops_repository).ingest_timeseries(
        StoreDayObservation(
            store_id=store_id,
            business_date=start + timedelta(days=index),
            actual_revenue=90_000 + index * 150 + (index % 7) * 800,
            source_snapshot_ids=(f"pos-cross-flow-{index:03d}",),
        )
        for index in range(70)
    )


def test_registry_composes_without_monolithic_switch() -> None:
    """Acceptance 1: domain jobs compose via a registry, not an if/elif chain."""
    registry = build_default_registry()
    # More than one independently-registered handler == not a single monolith.
    assert set(registry.job_types()) >= {"forecast", "external-fetch"}
    assert registry.has("forecast")
    # A duplicate registration is rejected: two domains cannot claim one type.
    with pytest.raises(ValueError):
        registry.register("forecast", lambda job, persistence: None)


def test_service_boundaries_declare_runtime_units() -> None:
    """Acceptance 2: the runtime composes the SD-03 §4 deployment units."""
    units = {boundary.unit for boundary in SERVICE_BOUNDARIES}
    assert {"core-api", "worker", "scheduler"}.issubset(units)


def test_cross_flow_gate_migrations_seed_api_worker_scheduler(db_path) -> None:
    """Acceptance 2-4: migrations + seed + api + worker + scheduler run together."""
    fastapi = pytest.importorskip("fastapi")  # noqa: F841
    from fastapi.testclient import TestClient

    # --- migrations + seed: durable bundle applies schema on bootstrap, and the
    # scheduler primes the baseline recurring job. api/worker/scheduler all bind
    # to this ONE bundle.
    bundle = bootstrap_runtime(persistence=_durable_bundle(db_path), prime_scheduled_jobs=True)
    try:
        app = build_server(persistence=bundle)
        worker = build_worker(bundle)

        # Flow 1 (Integration/External): the scheduler primed an external-fetch
        # job but the worker has not run yet, so no watermark exists.
        assert bundle.external_fetch_state_store.last_success_watermark(PROVIDER_ID) is None

        # Flow 2 (Operations): enqueue a forecast job through the core-api
        # boundary. This crosses the API service boundary and writes an audit
        # event (ODP-AC-SD08-003).
        client = TestClient(app)
        _seed_forecast_series(bundle, "store-gate-001")
        response = client.post(
            "/jobs",
            json={"job_type": "forecast", "payload": {"store_id": "store-gate-001"}},
            headers={"Idempotency-Key": "cross-flow-forecast-1"},
        )
        assert response.status_code == 202, response.text
        body = response.json()
        forecast_job_id = body["job_id"]
        correlation_id = body["correlation_id"]
        assert body["status"] == JobStatus.QUEUED.value

        # The worker drains BOTH jobs across BOTH flows (external-fetch + forecast).
        assert _drain(worker) == 2

        # Job state machine reached the terminal success state for the forecast.
        assert bundle.job_queue.get(forecast_job_id).status == JobStatus.SUCCEEDED

        # Durable side effects: external watermark advanced; a forecast persisted.
        watermark = bundle.external_fetch_state_store.last_success_watermark(PROVIDER_ID)
        assert watermark is not None
        assert bundle.forecastops_repository.latest_forecasts()

        # Audit trail: the API job enqueue recorded an audit event under the
        # request's correlation id.
        events = bundle.audit_log.list_events(correlation_id=correlation_id)
        assert any(event.event_type == "job.enqueue" for event in events)

        # Idempotency: re-posting the same key does not create a second job.
        replay = client.post(
            "/jobs",
            json={"job_type": "forecast", "payload": {"store_id": "store-gate-001"}},
            headers={"Idempotency-Key": "cross-flow-forecast-1"},
        )
        assert replay.status_code == 202
        assert replay.json()["job_id"] == forecast_job_id
    finally:
        bundle.engine.close()

    # --- Recovery: a fresh process (new bundle, same on-disk DB) still sees the
    # advanced watermark. Backup/recovery of durable runtime state.
    reopened = _durable_bundle(db_path)
    try:
        persisted = reopened.external_fetch_state_store.last_success_watermark(PROVIDER_ID)
        assert persisted is not None
        assert persisted == watermark
    finally:
        reopened.engine.close()
