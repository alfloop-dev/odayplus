from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from scripts.deployment import cloud_run_job_entrypoint as entrypoint
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
    bundle = replace(build_persistence(), job_queue=ExplodingQueue())
    monkeypatch.setattr(entrypoint, "bootstrap_runtime", lambda: bundle)

    assert entrypoint.run_scheduler() == entrypoint.EXIT_FAILED


def test_scheduler_requires_persisted_enqueue_receipt(monkeypatch) -> None:
    bundle = build_persistence()
    monkeypatch.setattr(entrypoint, "bootstrap_runtime", lambda: bundle)

    assert entrypoint.run_scheduler() == 0
    assert bundle.job_queue.count_active_jobs() == 1


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
    )
    monkeypatch.setattr(entrypoint, "build_migration_run", lambda **_kwargs: receipt)
    verified: list[str] = []
    monkeypatch.setattr(
        entrypoint, "_verify_runtime_schema", lambda database_url: verified.append(database_url)
    )
    monkeypatch.setenv("ODAY_DATABASE_URL", "postgresql://user:pass@db/oday")
    assert entrypoint.run_migration() == 0
    assert verified == ["postgresql://user:pass@db/oday"]


def test_database_urls_normalize_sqlalchemy_and_psycopg_drivers() -> None:
    assert entrypoint._database_urls("postgresql://user:pass@db/oday") == (
        "postgresql+psycopg://user:pass@db/oday",
        "postgresql://user:pass@db/oday",
    )
    assert entrypoint._database_urls("postgres://user:pass@db/oday") == (
        "postgresql+psycopg://user:pass@db/oday",
        "postgresql://user:pass@db/oday",
    )
