from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from apps.scheduler.oday_scheduler.main import ODayScheduler
from product_ops.deployment import cloud_run_job_entrypoint as entrypoint
from shared.infrastructure.persistence.factory import build_persistence
from shared.jobs.queue import InMemoryJobQueue, JobRequest, JobStatus


def test_worker_retry_receipt_returns_nonzero_even_when_run_once_returns_true(
    monkeypatch,
) -> None:
    bundle = build_persistence()
    job, _ = bundle.job_queue.enqueue(
        JobRequest(job_type="not-registered", payload={}),
        correlation_id="corr-retry",
    )
    monkeypatch.setattr(entrypoint, "bootstrap_runtime", lambda: bundle)

    result = entrypoint.run_worker(max_jobs=1, require_job=True)

    assert result == entrypoint.EXIT_RETRY_QUEUED
    persisted = bundle.job_queue.get(job.job_id)
    assert persisted is not None
    assert persisted.status == JobStatus.QUEUED
    assert persisted.payload["_retry_count"] == 1


def test_worker_failed_receipt_returns_nonzero_after_retry_exhaustion(monkeypatch) -> None:
    bundle = build_persistence()
    job, _ = bundle.job_queue.enqueue(
        JobRequest(job_type="not-registered", payload={"_retry_count": 3}),
        correlation_id="corr-failed",
    )
    monkeypatch.setattr(entrypoint, "bootstrap_runtime", lambda: bundle)

    result = entrypoint.run_worker(max_jobs=1, require_job=True)

    assert result == entrypoint.EXIT_FAILED
    persisted = bundle.job_queue.get(job.job_id)
    assert persisted is not None
    assert persisted.status == JobStatus.FAILED


def test_worker_empty_queue_requires_an_explicit_idle_contract(monkeypatch) -> None:
    bundle = build_persistence()
    monkeypatch.setattr(entrypoint, "bootstrap_runtime", lambda: bundle)

    assert entrypoint.run_worker(max_jobs=1, require_job=True) == entrypoint.EXIT_FAILED
    assert entrypoint.run_worker(max_jobs=1, require_job=False) == 0


class ExplodingQueue(InMemoryJobQueue):
    def enqueue(self, request, *, correlation_id):
        raise RuntimeError("queue unavailable")


def test_scheduler_fails_when_run_once_swallows_enqueue_exception(monkeypatch) -> None:
    """A declared recurring job that silently enqueues nothing still fails.

    ``ODayScheduler.run_once`` catches and logs enqueue exceptions rather than
    propagating them, so the receipt — not the return value — is what catches a
    dead queue. XR-CUTOVER-001 left the scheduler with no recurring job, which
    would make an exploding queue unreachable, so the recurring declaration is
    restored here to keep exercising the guard rather than deleting it.
    """
    bundle = replace(build_persistence(), job_queue=ExplodingQueue())
    monkeypatch.setattr(entrypoint, "bootstrap_runtime", lambda: bundle)
    monkeypatch.setenv("ODP_SCHEDULED_INGESTION_TENANT_ID", "tenant-ops")
    monkeypatch.setattr(ODayScheduler, "RECURRING_JOB_TYPES", ("some-future-job",))

    assert entrypoint.run_scheduler() == entrypoint.EXIT_FAILED


def test_scheduler_fails_when_tenant_unconfigured(monkeypatch) -> None:
    bundle = build_persistence()
    monkeypatch.setattr(entrypoint, "bootstrap_runtime", lambda: bundle)
    monkeypatch.delenv("ODP_SCHEDULED_INGESTION_TENANT_ID", raising=False)
    monkeypatch.delenv("ODP_TENANT_ID", raising=False)

    assert entrypoint.run_scheduler() == entrypoint.EXIT_FAILED


def test_scheduler_tick_enqueues_nothing_after_the_cutover(monkeypatch, capsys) -> None:
    """XR-CUTOVER-001: an empty tick is healthy, not a missing receipt.

    ``external-fetch`` was the scheduler's only recurring job and the providers
    behind it were decommissioned, so a tick has nothing to enqueue. Keeping the
    old "no_enqueue_receipt" failure would make every cron run report failure
    forever.
    """
    bundle = build_persistence()
    monkeypatch.setattr(entrypoint, "bootstrap_runtime", lambda: bundle)
    monkeypatch.setenv("ODP_SCHEDULED_INGESTION_TENANT_ID", "tenant-ops")

    assert entrypoint.run_scheduler() == 0
    assert bundle.job_queue.count_active_jobs() == 0

    receipt = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert receipt["receipt_kind"] == "scheduler"
    assert receipt["status"] == "ok"
    assert receipt["reason"] == "no_scheduled_work"



def test_migration_receipt_propagates_failure(monkeypatch) -> None:
    def fail_migration(**_kwargs):
        raise entrypoint.OpsPlanError("migration failed")

    monkeypatch.setattr(entrypoint, "build_migration_run", fail_migration)
    monkeypatch.setenv("ODAY_DATABASE_URL", "postgresql://user:pass@db/oday")
    assert entrypoint.run_migration() == entrypoint.EXIT_FAILED


def test_migration_receipt_requires_successful_runner(monkeypatch) -> None:
    receipt = SimpleNamespace(
        target_revision="head",
        manifest_sha256="sha256",
        checksum_status="verified",
        returncode=0,
        assisted_intake_manifest_sha256="intake-sha256",
        assisted_intake_steps=(
            "001_baseline.sql",
            "002_consistency.sql",
            "003_promotion_state.sql",
            "004_tenant_rls_lineage.sql",
        ),
        assisted_intake_schema_status="verified",
    )
    migration_options: list[dict[str, object]] = []

    def migration_run(**kwargs):
        migration_options.append(kwargs)
        return receipt

    monkeypatch.setattr(entrypoint, "build_migration_run", migration_run)
    verified: list[str] = []
    monkeypatch.setattr(
        entrypoint, "_verify_runtime_schema", lambda database_url: verified.append(database_url)
    )
    monkeypatch.setenv("ODAY_DATABASE_URL", "postgresql://user:pass@db/oday")
    assert entrypoint.run_migration() == 0
    assert verified == ["postgresql://user:pass@db/oday"]
    assert migration_options == [
        {
            "environment": "",
            "target_revision": "head",
            "dry_run": False,
            "include_assisted_intake": True,
        }
    ]


def test_database_urls_normalize_sqlalchemy_and_psycopg_drivers() -> None:
    assert entrypoint._database_urls("postgresql://user:pass@db/oday") == (
        "postgresql+psycopg://user:pass@db/oday",
        "postgresql://user:pass@db/oday",
    )
    assert entrypoint._database_urls("postgres://user:pass@db/oday") == (
        "postgresql+psycopg://user:pass@db/oday",
        "postgresql://user:pass@db/oday",
    )


# --- ODP-DEPLOY-WORKER-JOB-EXECUTION-001 ------------------------------------
# Regression for Deploy Dev run 30412416116 / Cloud Run execution
# oday-worker-r-79cf9b67e62c-6fhw5. The dev worker job is deployed with
# ODP_PRODUCTION_PROVIDER_IDS=poi.commercial_api,geocode.primary_api,
# admin_boundary.official_dataset while the scheduler enqueues an hourly
# external-fetch for listing.partner_feed. The handler flattened that
# fail-closed allowlist rejection into a plain RuntimeError, so the worker
# retried it three times, opened the provider circuit (which then masked the
# real reason code), dead-lettered the job and exited 1 -- blocking every
# deployment, on every tick, forever.

LIVE_DEV_WORKER_ENV = {
    "ODP_EXTERNAL_PROVIDER_MODE": "live",
    "ODP_DEPLOY_ENV": "dev",
    "ODP_PRODUCTION_PROVIDER_IDS": (
        "poi.commercial_api,geocode.primary_api,admin_boundary.official_dataset"
    ),
}

SCHEDULED_FETCH_PAYLOAD = {
    "tenant_id": "tenant-dev",
    "provider_id": "listing.partner_feed",
    "schedule_id": "hourly-listing",
    "freshness_sla_hours": 6,
}


def _apply_live_dev_worker_env(monkeypatch) -> None:
    for name, value in LIVE_DEV_WORKER_ENV.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize(
    "provider_id",
    ["listing.partner_feed", "listing.no_such_feed"],
)
def test_worker_dead_letters_a_legacy_fetch_on_the_first_attempt(
    monkeypatch, capsys, provider_id: str
) -> None:
    """XR-CUTOVER-001: a queued external-fetch fails once, terminally.

    Before the cutover this job type had a spectrum of outcomes — a deselected
    provider was skipped as a *decision* (SUCCEEDED, exit 0), an unregistered
    one dead-lettered as a *fault*. Neither distinction survives: there is no
    provider selection left to consult and no fetch to attempt, so every
    provider id collapses to the same terminal failure. What must not regress
    is the retry behaviour the deploy gate cared about: one attempt, no retry
    budget burned, queue drained.
    """
    _apply_live_dev_worker_env(monkeypatch)
    monkeypatch.setenv("ODAY_RELEASE_SHA", "79cf9b67e62ce9fbd762b6695a214965ea9fe258")
    bundle = build_persistence()
    job, _ = bundle.job_queue.enqueue(
        JobRequest(
            job_type="external-fetch",
            payload={**SCHEDULED_FETCH_PAYLOAD, "provider_id": provider_id},
        ),
        correlation_id="corr-legacy-fetch",
    )
    monkeypatch.setattr(entrypoint, "bootstrap_runtime", lambda: bundle)

    result = entrypoint.run_worker(max_jobs=1, require_job=True)

    assert result == entrypoint.EXIT_FAILED
    persisted = bundle.job_queue.get(job.job_id)
    assert persisted is not None
    assert persisted.status == JobStatus.FAILED
    # The pre-fix failure burned three retries before dead-lettering.
    assert persisted.attempts == 1
    assert "_retry_count" not in persisted.payload
    assert bundle.job_queue.count_active_jobs() == 0
    # The error names the cutover, so an operator removes the schedule instead
    # of hunting for a provider misconfiguration.
    assert "XR-CUTOVER-001" in (persisted.error_message or "")
    assert provider_id in (persisted.error_message or "")

    receipt = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert receipt["receipt_kind"] == "worker"
    assert receipt["queue_active"] == 0
    assert receipt["processed"] == [
        {
            "attempts": 1,
            "has_error": True,
            "job_id": job.job_id,
            "job_status": "failed",
            "job_type": "external-fetch",
            "outcome": "failed",
            "retry_count": 0,
        }
    ]


def test_a_legacy_fetch_fabricates_no_ingestion_run(monkeypatch) -> None:
    """Failing the job must not leave a run record behind.

    The pre-cutover handler persisted a BLOCKED run for auditability. There is
    no run to record now — nothing was fetched and no window was claimed — so a
    record would be a fabricated one.
    """
    _apply_live_dev_worker_env(monkeypatch)
    bundle = build_persistence()
    bundle.job_queue.enqueue(
        JobRequest(job_type="external-fetch", payload=dict(SCHEDULED_FETCH_PAYLOAD)),
        correlation_id="corr-legacy-audit",
    )
    monkeypatch.setattr(entrypoint, "bootstrap_runtime", lambda: bundle)

    assert entrypoint.run_worker(max_jobs=1, require_job=True) == entrypoint.EXIT_FAILED

    assert bundle.ingestion_run_store.list_runs(provider_id="listing.partner_feed") == []
    assert not hasattr(bundle, "external_fetch_state_store")
