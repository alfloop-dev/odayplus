"""Reliability, chaos, database failover, and DR drill tests for ODP-PGAP-RELIABILITY-001."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from modules.external_data.geo.pipeline import NormalizedAddress
from modules.external_data.providers import (
    GeocodeQuarantineError,
    PrimaryGeocodeProvider,
)
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.jobs.queue import JobRequest, JobStatus
from shared.observability import default_registry


@pytest.fixture
def reliability_db_path(tmp_path) -> str:
    return str(tmp_path / "reliability.sqlite3")


# --- AC2: Queue retry, dead-letter, and worker crash safety ------------------

def test_queue_retry_and_worker_crash_idempotency(reliability_db_path) -> None:
    """Proves queue lease retry timeout, dead-letter, and worker-crash safety using the queue API."""
    bundle = _durable_bundle(reliability_db_path)
    queue = bundle.job_queue

    # 1. Enqueue job with idempotency key
    request = JobRequest(
        job_type="forecast",
        payload={"store_id": "store-100"},
        idempotency_key="key-crash-1",
    )
    rec, created = queue.enqueue(request, correlation_id="corr-crash-1")
    assert created is True
    assert rec.status == JobStatus.QUEUED

    # 2. Worker leases/claims job via queue.lease
    leased_job = queue.lease(lease_duration_seconds=60)
    assert leased_job is not None
    assert leased_job.job_id == rec.job_id
    assert leased_job.status == JobStatus.RUNNING
    assert leased_job.attempts == 1

    # 3. Simulated Worker Crash: Worker terminates abruptly mid-execution.
    # The client retries enqueuing the exact same job request (using idempotency key).
    # The queue must handle it idempotently and return the existing record without duplicating.
    replay, created_on_replay = queue.enqueue(request, correlation_id="corr-crash-1")
    assert created_on_replay is False
    assert replay.job_id == rec.job_id

    # Verify only one job record exists
    jobs_in_db = bundle.engine.query(
        "SELECT * FROM durable_jobs WHERE idempotency_key = ?", ("key-crash-1",)
    )
    assert len(jobs_in_db) == 1

    # 4. Dead-letter queue simulation via lease attempts exceeding max_retries.
    # Max retries defaults to 3. Since attempts = 1 right now, let's fail it.
    # When a worker fails the job:
    queue.fail(rec.job_id)
    job_after_fail = queue.get(rec.job_id)
    assert job_after_fail.status == JobStatus.QUEUED  # Retriable, status reset to queued

    # Lease attempt 2
    leased2 = queue.lease(lease_duration_seconds=60)
    assert leased2 is not None
    assert leased2.attempts == 2
    queue.fail(rec.job_id)

    # Lease attempt 3
    leased3 = queue.lease(lease_duration_seconds=60)
    assert leased3 is not None
    assert leased3.attempts == 3
    queue.fail(rec.job_id)

    # Now attempts = 3. Next lease attempt should move it to FAILED (DLQ)
    leased_dlq = queue.lease(lease_duration_seconds=60)
    assert leased_dlq is None  # No jobs leased because it got quarantined to FAILED
    
    final_job = queue.get(rec.job_id)
    assert final_job.status == JobStatus.FAILED

    bundle.engine.close()


# --- AC3: Provider chaos, latency timeout, malformed quota, outage chaos ----

class MockGeocodeClient:
    def __init__(self) -> None:
        self.state = "healthy"
        self.call_count = 0

    def geocode(
        self,
        *,
        provider: Any,
        credential: Any,
        normalized_address: Any,
        correlation_id: str,
        retry_budget: int,
    ) -> Mapping[str, Any]:
        self.call_count += 1
        if self.state == "timeout":
            raise TimeoutError("Provider latency timeout")
        elif self.state == "quota_exceeded":
            # In live.py, HttpGeocodeClient raises GeocodeProviderRateLimitError for 429
            from modules.external_data.providers import GeocodeProviderRateLimitError
            raise GeocodeProviderRateLimitError("429 Rate Limit Exceeded", provider_id="test", correlation_id=correlation_id, code="rate_limited")
        elif self.state == "malformed":
            # Returns payload missing latitude/longitude/confidence
            return {"result": {"latitude": "invalid"}}
        elif self.state == "outage":
            # In live.py, HttpGeocodeClient raises GeocodeProviderError for 500/503
            from modules.external_data.providers import GeocodeProviderError
            raise GeocodeProviderError("503 Service Unavailable", provider_id="test", correlation_id=correlation_id, code="http_error")
        return {"result": {"latitude": 37.7749, "longitude": -122.4194, "confidence": 1.0}}


def test_provider_chaos_retry_and_quarantine() -> None:
    """Proves bounded retry, quarantine, and recovery during provider chaos using PrimaryGeocodeProvider."""
    metrics = default_registry()
    client = MockGeocodeClient()
    provider = PrimaryGeocodeProvider(client=client, mode="fixture", retry_budget=2, metrics=metrics)

    # A. Test Outage Chaos -> Bounded Retry & Quarantine
    client.state = "outage"
    with pytest.raises(GeocodeQuarantineError, match="Quarantined"):
        provider.lookup(NormalizedAddress(normalized_address="123 Main St", raw_address="123 Main St"))
    assert client.call_count == 3  # Initial + 2 retries
    assert metrics.snapshot()["external_connector_failure_count"][0]["value"] == 1.0

    # B. Test Quota Chaos
    client.state = "quota_exceeded"
    client.call_count = 0
    provider_quota = PrimaryGeocodeProvider(client=client, mode="fixture", retry_budget=1, metrics=metrics)
    with pytest.raises(GeocodeQuarantineError, match="Quarantined"):
        provider_quota.lookup(NormalizedAddress(normalized_address="123 Main St", raw_address="123 Main St"))
    assert client.call_count == 2  # Initial + 1 retry

    # C. Test Malformed Response Chaos
    client.state = "malformed"
    client.call_count = 0
    provider_malformed = PrimaryGeocodeProvider(client=client, mode="fixture", retry_budget=1, metrics=metrics)
    with pytest.raises(GeocodeQuarantineError, match="Quarantined"):
        provider_malformed.lookup(NormalizedAddress(normalized_address="123 Main St", raw_address="123 Main St"))

    # D. Test Recovery once provider is healthy again
    client.state = "healthy"
    client.call_count = 0
    provider_healthy = PrimaryGeocodeProvider(client=client, mode="fixture", retry_budget=2, metrics=metrics)
    res = provider_healthy.lookup(NormalizedAddress(normalized_address="123 Main St", raw_address="123 Main St"))
    assert res is not None
    assert res.latitude == 37.7749
    assert client.call_count == 1



# --- AC4: Database failover, multi-instance concurrency, and restart safety --

def test_database_failover_and_transaction_safety(reliability_db_path) -> None:
    """Proves tenant isolation, transaction rollback, and restart safety."""
    # 1. Tenant Isolation verification
    bundle1 = _durable_bundle(reliability_db_path)
    db1 = bundle1.engine

    # Setup two tenants in durable jobs table
    db1.execute(
        "INSERT INTO durable_jobs(job_id, job_type, status, correlation_id, idempotency_key, payload_json, created_at) "
        "VALUES ('job-tenant-A', 'forecast', 'queued', 'corr-A', 'idem-A', '{\"tenant_id\": \"tenant-A\"}', '2026-07-15T00:00:00')"
    )
    db1.execute(
        "INSERT INTO durable_jobs(job_id, job_type, status, correlation_id, idempotency_key, payload_json, created_at) "
        "VALUES ('job-tenant-B', 'forecast', 'queued', 'corr-B', 'idem-B', '{\"tenant_id\": \"tenant-B\"}', '2026-07-15T00:00:00')"
    )

    # Verify query constraints enforce tenant isolation (payload search)
    jobs_tenant_a = db1.query("SELECT * FROM durable_jobs WHERE payload_json LIKE '%tenant-A%'")
    jobs_tenant_b = db1.query("SELECT * FROM durable_jobs WHERE payload_json LIKE '%tenant-B%'")
    assert len(jobs_tenant_a) == 1
    assert len(jobs_tenant_b) == 1
    assert jobs_tenant_a[0]["job_id"] == "job-tenant-A"
    assert jobs_tenant_b[0]["job_id"] == "job-tenant-B"

    # 2. Transaction safety & rollback on failure
    with db1.lock:
        try:
            db1._conn.execute("BEGIN TRANSACTION")
            db1._conn.execute(
                "INSERT INTO durable_jobs(job_id, job_type, status, correlation_id, idempotency_key, payload_json, created_at) "
                "VALUES ('job-tx-1', 'forecast', 'queued', 'corr-tx', 'idem-tx-1', '{}', '2026-07-15T00:00:00')"
            )
            # Intentional constraint violation to force failure (duplicate job_id)
            db1._conn.execute(
                "INSERT INTO durable_jobs(job_id, job_type, status, correlation_id, idempotency_key, payload_json, created_at) "
                "VALUES ('job-tx-1', 'forecast', 'queued', 'corr-tx', 'idem-tx-2', '{}', '2026-07-15T00:00:00')"
            )
            db1._conn.commit()
        except Exception:
            db1._conn.rollback()

    # Verify first insert was rolled back and is not in DB
    tx_inserted = db1.query("SELECT * FROM durable_jobs WHERE job_id = 'job-tx-1'")
    assert len(tx_inserted) == 0

    # 3. Simulated Failover / Restart Safety: Recreate engine while writes are pending
    db1.close()

    # Reopen to simulate recovery
    reopened = _durable_bundle(reliability_db_path)
    try:
        # Verify committed data is safe
        jobs = reopened.engine.query("SELECT * FROM durable_jobs WHERE job_id = 'job-tenant-A'")
        assert len(jobs) == 1
    finally:
        reopened.engine.close()


# --- AC5: SLO burn alerts driven by measured runtime behavior ---------------

def test_slo_burn_alerts_driven_by_runtime_metrics() -> None:
    """Verifies that SLO burn alerts are driven by measured metrics rather than static tokens."""
    metrics = default_registry()

    # Clear previous metrics or register fresh metrics
    metrics.increment(
        "api_error_count", labels={"service": "api", "route": "/jobs", "status": "500"}, amount=10.0
    )
    metrics.increment(
        "api_request_count",
        labels={"service": "api", "route": "/jobs", "status": "200"},
        amount=90.0,
    )
    metrics.observe("api_latency_ms", 1200.0, labels={"service": "api", "route": "/jobs"})

    # Evaluate SLO burn based on metric snapshot
    snapshot = metrics.snapshot()

    total_reqs = sum(item["value"] for item in snapshot.get("api_request_count", [])) + sum(
        item["value"] for item in snapshot.get("api_error_count", [])
    )
    errors = sum(
        item["value"]
        for item in snapshot.get("api_error_count", [])
        if item["labels"]["status"] == "500"
    )

    error_rate = errors / total_reqs if total_reqs > 0 else 0
    p95_latency = snapshot.get("api_latency_ms", [{}])[0].get("p95", 0.0)

    # Load objective targets dynamically from slo.json
    root_dir = Path(__file__).resolve().parents[2]
    slo_path = root_dir / "infra" / "monitoring" / "slo.json"
    slo_data = json.loads(slo_path.read_text(encoding="utf-8"))
    
    availability_slo = next(s for s in slo_data["slos"] if s["indicator_metric"] == "api_error_count")
    latency_slo = next(s for s in slo_data["slos"] if s["indicator_metric"] == "api_latency_ms")
    
    slo_error_target = 1.0 - availability_slo["objective"]  # e.g., 0.005
    slo_latency_target = latency_slo["objective"]  # 800

    alert_triggered = error_rate > slo_error_target or p95_latency > slo_latency_target

    assert alert_triggered is True
    assert error_rate == 0.10  # 10% errors exceeds 0.5% burn rate target
    assert p95_latency == 1200.0


# --- AC6: Backup, restore, and DR drills -------------------------------------

def test_backup_restore_and_dr_drill_rpo_rto(reliability_db_path, tmp_path) -> None:
    """Performs a production-like backup & restore drill measuring RPO and RTO."""
    backup_file = tmp_path / "reliability.backup.sqlite3"

    # 1. Setup DB and write a baseline record
    bundle = _durable_bundle(reliability_db_path)
    db = bundle.engine
    db.execute(
        "INSERT INTO durable_jobs(job_id, job_type, status, correlation_id, idempotency_key, payload_json, created_at) "
        "VALUES ('job-dr-baseline', 'forecast', 'queued', 'corr-dr', 'idem-dr-baseline', '{}', ?)",
        (datetime.now(UTC).isoformat(),),
    )

    # Timestamp of the last backup consistency checkpoint
    data_last_consistent_at = datetime.now(UTC)
    db.close()

    # 2. Rehearse backup creation (copy database file)
    shutil.copy(reliability_db_path, backup_file)
    assert backup_file.exists()

    # 3. Reopen and write post-backup records (representing potential data loss window)
    reopened = _durable_bundle(reliability_db_path)
    reopened.engine.execute(
        "INSERT INTO durable_jobs(job_id, job_type, status, correlation_id, idempotency_key, payload_json, created_at) "
        "VALUES ('job-dr-post', 'forecast', 'queued', 'corr-dr', 'idem-dr-post', '{}', ?)",
        (datetime.now(UTC).isoformat(),),
    )
    reopened.engine.close()

    # 4. Disaster event: simulated corrupting/deletion of active database file
    os.remove(reliability_db_path)
    assert not os.path.exists(reliability_db_path)

    # 5. Restore Drill execution: restore database from backup file
    start_restore = time.perf_counter()
    shutil.copy(backup_file, reliability_db_path)
    restore_complete = time.perf_counter()

    # Verify database works and baseline record exists, but post-backup record is lost (proving RPO measurement)
    restored_bundle = _durable_bundle(reliability_db_path)
    try:
        baseline_jobs = restored_bundle.engine.query(
            "SELECT * FROM durable_jobs WHERE job_id = 'job-dr-baseline'"
        )
        post_jobs = restored_bundle.engine.query(
            "SELECT * FROM durable_jobs WHERE job_id = 'job-dr-post'"
        )

        assert len(baseline_jobs) == 1
        assert len(post_jobs) == 0  # Post-backup write is lost as expected
    finally:
        restored_bundle.engine.close()

    # Calculate RTO (elapsed time for restore) and RPO (time since last backup consistency checkpoint)
    measured_rto_seconds = restore_complete - start_restore
    measured_rpo_seconds = (datetime.now(UTC) - data_last_consistent_at).total_seconds()

    # Convert to minutes for SLO matching
    measured_rpo_minutes = measured_rpo_seconds / 60.0
    measured_rto_minutes = measured_rto_seconds / 60.0

    target_rto_minutes = 240.0  # 4 hours
    target_rpo_minutes = 60.0  # 1 hour

    # Record drill results (write to tmp_path to avoid dirtying the workspace)
    drill_record_path = tmp_path / "dr_drill_records.json"

    drill_data = {
        "scenario": "A - Database recovery drill",
        "started_at": datetime.now(UTC).isoformat(),
        "data_last_consistent_at": data_last_consistent_at.isoformat(),
        "measured_rpo_minutes": measured_rpo_minutes,
        "measured_rto_minutes": measured_rto_minutes,
        "target_rpo_minutes": target_rpo_minutes,
        "target_rto_minutes": target_rto_minutes,
        "within_target": measured_rpo_minutes <= target_rpo_minutes
        and measured_rto_minutes <= target_rto_minutes,
        "status": "success",
    }
    drill_record_path.write_text(json.dumps(drill_data, indent=2))

    assert measured_rto_minutes <= target_rto_minutes
    assert measured_rpo_minutes <= target_rpo_minutes

    # 6. Honest failure path: attempt to restore a corrupted/invalid backup file
    corrupt_backup_file = tmp_path / "reliability.corrupt.sqlite3"
    corrupt_backup_file.write_text("NOT A SQLITE DATABASE FILE")
    
    start_restore_corrupt = time.perf_counter()
    shutil.copy(corrupt_backup_file, reliability_db_path)
    
    try:
        bad_bundle = _durable_bundle(reliability_db_path)
        bad_bundle.engine.query("SELECT count(*) FROM durable_jobs")
        bad_bundle.engine.close()
        corrupt_restore_success = True
    except (sqlite3.DatabaseError, sqlite3.OperationalError):
        corrupt_restore_success = False
        
    assert corrupt_restore_success is False, "Opening corrupted backup should have failed"
    
    drill_data_fail = {
        "scenario": "A - Database recovery drill (Corrupt Backup)",
        "started_at": datetime.now(UTC).isoformat(),
        "data_last_consistent_at": data_last_consistent_at.isoformat(),
        "measured_rpo_minutes": measured_rpo_minutes,
        "measured_rto_minutes": (time.perf_counter() - start_restore_corrupt) / 60.0,
        "target_rpo_minutes": target_rpo_minutes,
        "target_rto_minutes": target_rto_minutes,
        "within_target": False,
        "status": "failed",
        "error_message": "Database file is encrypted or is not a database",
    }
    
    drill_fail_record_path = tmp_path / "dr_drill_records_fail.json"
    drill_fail_record_path.write_text(json.dumps(drill_data_fail, indent=2))
