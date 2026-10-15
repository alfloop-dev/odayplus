"""Canonical scheduling must not introduce a SQLite workflow dependency."""

import pytest

from shared.infrastructure.persistence import engine as engine_module
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.jobs.queue import JobRequest


@pytest.mark.parametrize("method", ["claim_next", "lease"])
def test_sqlite_generic_queue_has_no_canonical_schema_dependency(tmp_path, method):
    engine = SqliteEngine(tmp_path / "queue.sqlite")
    try:
        queue = DurableJobQueue(engine)
        job, _ = queue.enqueue(JobRequest("ordinary", {}), correlation_id="synthetic")
        claimed = queue.claim_next() if method == "claim_next" else queue.lease(45)
        assert claimed.job_id == job.job_id
        assert claimed.attempts == 1
    finally:
        engine.close()


def test_canonical_schedule_cannot_be_enabled_on_sqlite(tmp_path):
    engine = SqliteEngine(tmp_path / "queue.sqlite")
    try:
        with pytest.raises(ValueError, match="requires PostgreSQL"):
            DurableJobQueue(engine, canonical_intake_score_schedule=True)
    finally:
        engine.close()


@pytest.mark.parametrize("method", ["claim_next", "lease"])
def test_sqlite_delivery_schedule_skips_future_work_without_workflow_schema(tmp_path, method):
    engine = SqliteEngine(tmp_path / "queue.sqlite")
    try:
        queue = DurableJobQueue(engine)
        pending, _ = queue.enqueue(JobRequest("scheduled", {}), correlation_id="synthetic")
        other, _ = queue.enqueue(JobRequest("ordinary", {}), correlation_id="synthetic")
        engine.execute("UPDATE durable_jobs SET available_at='2099-01-01T00:00:00+00:00' WHERE job_id=?", (pending.job_id,))
        claim = queue.claim_next if method == "claim_next" else lambda: queue.lease(45)
        assert claim().job_id == other.job_id
        assert claim() is None
        assert queue.get(pending.job_id).attempts == 0
        engine.execute("UPDATE durable_jobs SET available_at='2000-01-01T00:00:00+00:00' WHERE job_id=?", (pending.job_id,))
        assert claim().job_id == pending.job_id
    finally:
        engine.close()


def test_existing_sqlite_queue_upgrade_adds_schedule_without_losing_jobs(tmp_path, monkeypatch):
    path = tmp_path / "old-queue.sqlite"
    migrations = engine_module._SCHEMA_FILES
    with monkeypatch.context() as old:
        old.setattr(engine_module, "_SCHEMA_FILES", tuple(p for p in migrations if "000031" not in p))
        engine = SqliteEngine(path)
        job, _ = DurableJobQueue(engine).enqueue(JobRequest("ordinary", {}), correlation_id="synthetic")
        engine.close()
    engine = SqliteEngine(path)
    try:
        assert dict(engine.query_one("SELECT available_at FROM durable_jobs WHERE job_id=?", (job.job_id,))) == {"available_at": None}
        assert DurableJobQueue(engine).claim_next().job_id == job.job_id
    finally:
        engine.close()
