"""Tenant identity through the scheduled ingestion path
(ODP-DEV-INGESTION-TENANT-SCHEDULER-001).

The manual/API ingest route resolves its tenant from a verified principal
(``apps/api/app/routes/external_data.py``). The scheduled route has no
principal, so before this task it ran every ingest under the unscoped default
tenant: the scheduler enqueued an untenanted payload, the worker never looked
for one, and ``run_scheduled`` dropped straight through to ``tenant_id=""``.

These tests pin the four behaviours that replace that silent default:

1. a scheduler tick with no configured tenant enqueues nothing and says why;
2. the configured tenant reaches the payload, the handler, the service, the
   persisted run, and the fetch watermark;
3. a run belonging to another tenant is never returned as a replay, and one
   tenant's watermark is never visible to another;
4. every missing-tenant path fails with an actionable, non-retryable error.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from apps.scheduler.oday_scheduler.main import (
    FALLBACK_TENANT_ENV_VAR,
    SCHEDULED_TENANT_ENV_VAR,
    ODayScheduler,
    SchedulerTenantConfigurationError,
    resolve_scheduled_tenant_id,
)
from apps.worker.oday_worker.main import ODayWorker
from modules.external_data.application.ingestion_service import (
    CrossTenantIngestionRunError,
    ExternalIngestionService,
    ScheduledIngestionTenantError,
)
from modules.external_data.application.ingestion_store import InMemoryIngestionRunStore
from modules.external_data.workers.scheduled_fetch import (
    ExternalFetchJobSpec,
    InMemoryExternalFetchStateStore,
    TenantScopedExternalFetchStateStore,
)
from shared.audit import InMemoryAuditLog
from shared.infrastructure.persistence.factory import _durable_bundle, build_persistence
from shared.jobs.queue import JobRequest, JobStatus

PROVIDER_ID = "listing.partner_feed"
TENANT_A = "tenant-alpha"
TENANT_B = "tenant-beta"
WINDOW_START = datetime(2026, 6, 28, 8, 0, tzinfo=UTC)
WINDOW_END = datetime(2026, 6, 28, 9, 0, tzinfo=UTC)


def _spec(schedule_id: str = "hourly-listing") -> ExternalFetchJobSpec:
    return ExternalFetchJobSpec(
        provider_id=PROVIDER_ID,
        schedule_id=schedule_id,
        interval=timedelta(hours=1),
        freshness_sla=timedelta(hours=24),
    )


def _external_fetch_jobs(bundle) -> list:
    return [rec for rec in bundle.job_queue._jobs.values() if rec.job_type == "external-fetch"]


# -- 1. no silent default tenant ---------------------------------------------


def test_scheduler_tick_without_configured_tenant_enqueues_nothing() -> None:
    """A tenant-less deployment fails closed instead of scheduling an unscoped run."""
    bundle = build_persistence()
    scheduler = ODayScheduler(persistence=bundle, env={})

    with pytest.raises(SchedulerTenantConfigurationError) as excinfo:
        scheduler.run_once()

    # Actionable: it names both env vars and the constructor escape hatch.
    message = str(excinfo.value)
    assert SCHEDULED_TENANT_ENV_VAR in message
    assert FALLBACK_TENANT_ENV_VAR in message
    assert "ODayScheduler(tenant_id=...)" in message
    assert excinfo.value.code == "scheduled_ingestion_tenant_missing"

    # Fail-closed: no job was enqueued at all, so nothing downstream can guess.
    assert _external_fetch_jobs(bundle) == []


def test_scheduler_loop_keeps_reporting_missing_tenant_without_enqueueing() -> None:
    """``loop`` must not crash on the misconfiguration, and must not enqueue."""
    bundle = build_persistence()
    scheduler = ODayScheduler(persistence=bundle, env={})

    class _OneShot:
        def __init__(self) -> None:
            self._calls = 0

        def is_set(self) -> bool:
            self._calls += 1
            return self._calls > 1

    scheduler.loop(stop_event=_OneShot(), interval=0.0)

    assert _external_fetch_jobs(bundle) == []


def test_run_scheduled_rejects_an_empty_tenant() -> None:
    """The service refuses a scheduled ingest that carries no tenant scope."""
    service = ExternalIngestionService(
        store=InMemoryIngestionRunStore(), audit_log=InMemoryAuditLog()
    )

    for missing in ("", "   "):
        with pytest.raises(ScheduledIngestionTenantError) as excinfo:
            service.run_scheduled(_spec(), scheduled_at=WINDOW_END, tenant_id=missing)
        assert SCHEDULED_TENANT_ENV_VAR in str(excinfo.value)
        assert PROVIDER_ID in str(excinfo.value)

    assert service.store.list_runs() == []


def test_external_fetch_job_without_tenant_is_dead_lettered_not_retried() -> None:
    """An untenanted payload is a misconfiguration no retry can fix."""
    bundle = build_persistence()
    job, _ = bundle.job_queue.enqueue(
        JobRequest(
            job_type="external-fetch",
            payload={"provider_id": PROVIDER_ID, "schedule_id": "hourly-listing"},
        ),
        correlation_id="corr-missing-tenant",
    )

    assert ODayWorker(persistence=bundle).run_once() is True

    executed = bundle.job_queue.get(job.job_id)
    assert executed.status == JobStatus.FAILED  # dead-lettered on attempt 1
    assert "_retry_count" not in executed.payload
    assert bundle.ingestion_run_store.list_runs() == []


# -- 2. the configured tenant reaches every scheduled ingest call ------------


def test_scheduled_tenant_is_read_from_environment_with_fallback() -> None:
    assert resolve_scheduled_tenant_id({SCHEDULED_TENANT_ENV_VAR: TENANT_A}) == TENANT_A
    assert resolve_scheduled_tenant_id({FALLBACK_TENANT_ENV_VAR: TENANT_B}) == TENANT_B
    # The ingestion-specific variable wins over the process-wide fallback.
    assert (
        resolve_scheduled_tenant_id(
            {SCHEDULED_TENANT_ENV_VAR: TENANT_A, FALLBACK_TENANT_ENV_VAR: TENANT_B}
        )
        == TENANT_A
    )
    assert resolve_scheduled_tenant_id({SCHEDULED_TENANT_ENV_VAR: "  "}) == ""


def test_configured_tenant_reaches_payload_run_record_and_watermark() -> None:
    """Scheduler env -> queue payload -> handler -> persisted run -> watermark."""
    bundle = build_persistence()
    ODayScheduler(
        persistence=bundle, env={SCHEDULED_TENANT_ENV_VAR: TENANT_A}
    ).run_once()

    queued = _external_fetch_jobs(bundle)
    assert len(queued) == 1
    assert queued[0].payload["tenant_id"] == TENANT_A

    assert ODayWorker(persistence=bundle).run_once() is True

    runs = bundle.ingestion_run_store.list_runs()
    assert len(runs) == 1
    assert runs[0].tenant_id == TENANT_A
    assert runs[0].trigger == "scheduled"

    scoped = TenantScopedExternalFetchStateStore(
        bundle.external_fetch_state_store, TENANT_A
    )
    assert scoped.last_success_watermark(PROVIDER_ID) is not None


def test_two_tenants_in_the_same_window_are_scheduled_separately() -> None:
    """The tenant is part of the queue idempotency key, so neither is dropped."""
    bundle = build_persistence()
    ODayScheduler(persistence=bundle, tenant_id=TENANT_A).run_once()
    ODayScheduler(persistence=bundle, tenant_id=TENANT_B).run_once()

    tenants = sorted(job.payload["tenant_id"] for job in _external_fetch_jobs(bundle))
    assert tenants == [TENANT_A, TENANT_B]


def test_scheduled_audit_event_carries_the_tenant() -> None:
    audit_log = InMemoryAuditLog()
    service = ExternalIngestionService(
        store=InMemoryIngestionRunStore(), audit_log=audit_log
    )

    service.run_scheduled(_spec(), scheduled_at=WINDOW_END, tenant_id=TENANT_A)

    events = [
        event
        for event in audit_log.list_events()
        if event.event_type == "external_data.ingested.v1"
    ]
    assert events and events[-1].metadata["tenant_id"] == TENANT_A


# -- 3. cross-tenant run visibility is rejected ------------------------------


def test_cross_tenant_window_replay_is_refused_on_a_shared_store() -> None:
    """Window keys are not tenant-qualified; the guard is what keeps them apart."""
    store = InMemoryIngestionRunStore()
    service = ExternalIngestionService(store=store, audit_log=InMemoryAuditLog())

    first = service.ingest(
        provider_id=PROVIDER_ID,
        schedule_id="hourly-listing",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        tenant_id=TENANT_A,
    )
    assert first.created is True

    with pytest.raises(CrossTenantIngestionRunError) as excinfo:
        service.ingest(
            provider_id=PROVIDER_ID,
            schedule_id="hourly-listing",
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            tenant_id=TENANT_B,
        )

    assert excinfo.value.record_tenant_id == TENANT_A
    assert excinfo.value.expected_tenant_id == TENANT_B
    assert excinfo.value.code == "cross_tenant_ingestion_run"
    # Tenant B never saw tenant A's counts, lineage, or snapshot ids.
    assert [record.tenant_id for record in store.list_runs()] == [TENANT_A]


def test_cross_tenant_api_key_replay_is_refused() -> None:
    store = InMemoryIngestionRunStore()
    service = ExternalIngestionService(store=store, audit_log=InMemoryAuditLog())

    service.ingest(
        provider_id=PROVIDER_ID,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        api_idempotency_key="shared-key",
        tenant_id=TENANT_A,
    )

    with pytest.raises(CrossTenantIngestionRunError):
        service.ingest(
            provider_id=PROVIDER_ID,
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            api_idempotency_key="shared-key",
            tenant_id=TENANT_B,
        )


def test_unscoped_reader_cannot_replay_a_tenant_run() -> None:
    """An unscoped (``tenant_id=""``) call is a *different* scope, not a wildcard."""
    store = InMemoryIngestionRunStore()
    service = ExternalIngestionService(store=store, audit_log=InMemoryAuditLog())

    service.ingest(
        provider_id=PROVIDER_ID,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        tenant_id=TENANT_A,
    )

    with pytest.raises(CrossTenantIngestionRunError):
        service.ingest(
            provider_id=PROVIDER_ID,
            window_start=WINDOW_START,
            window_end=WINDOW_END,
        )


def test_tenant_fetch_state_is_isolated_within_one_shared_backend() -> None:
    """Watermark, window dedupe, and circuit state are per tenant."""
    backend = InMemoryExternalFetchStateStore()
    scoped_a = TenantScopedExternalFetchStateStore(backend, TENANT_A)
    scoped_b = TenantScopedExternalFetchStateStore(backend, TENANT_B)

    service = ExternalIngestionService(
        store=InMemoryIngestionRunStore(),
        state_store=backend,
        audit_log=InMemoryAuditLog(),
    )
    outcome = service.run_scheduled(_spec(), scheduled_at=WINDOW_END, tenant_id=TENANT_A)
    assert outcome.created is True

    assert scoped_a.last_success_watermark(PROVIDER_ID) is not None
    assert scoped_b.last_success_watermark(PROVIDER_ID) is None
    assert backend.last_success_watermark(PROVIDER_ID) is None

    # The run is readable through its own scope with its plain provider id...
    run = scoped_a.get_run(outcome.record.idempotency_key)
    assert run is not None
    assert run.provider_id == PROVIDER_ID
    # ...and invisible through any other scope.
    assert scoped_b.get_run(outcome.record.idempotency_key) is None
    assert backend.get_run(outcome.record.idempotency_key) is None


def test_tenant_scheduler_state_is_not_seeded_from_another_tenants_runs() -> None:
    """Re-seeding a tenant's scheduler must skip foreign runs on a shared store."""
    store = InMemoryIngestionRunStore()
    service = ExternalIngestionService(store=store, audit_log=InMemoryAuditLog())

    service.ingest(
        provider_id=PROVIDER_ID,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        tenant_id=TENANT_A,
    )

    scheduler_b, _ = service._get_scheduler_and_captures(TENANT_B)
    assert scheduler_b.state_store.last_success_watermark(PROVIDER_ID) is None

    scheduler_a, _ = service._get_scheduler_and_captures(TENANT_A)
    assert scheduler_a.state_store.last_success_watermark(PROVIDER_ID) is not None


def test_durable_scheduled_run_lands_only_in_the_tenant_scoped_store(tmp_path) -> None:
    """On the durable bundle the run is physically scoped, not just tagged."""
    bundle = _durable_bundle(str(tmp_path / "tenant-scope.sqlite3"))
    try:
        ODayScheduler(persistence=bundle, tenant_id=TENANT_A).run_once()
        assert ODayWorker(persistence=bundle).run_once() is True

        scoped = bundle.ingestion_run_store_for_tenant(TENANT_A)
        assert [record.tenant_id for record in scoped.list_runs()] == [TENANT_A]
        # Neither the unscoped store nor another tenant's view can see it.
        assert bundle.ingestion_run_store.list_runs() == []
        assert bundle.ingestion_run_store_for_tenant(TENANT_B).list_runs() == []
    finally:
        bundle.engine.close()


def test_tenant_scoped_state_store_requires_a_tenant() -> None:
    with pytest.raises(ValueError):
        TenantScopedExternalFetchStateStore(InMemoryExternalFetchStateStore(), "  ")
