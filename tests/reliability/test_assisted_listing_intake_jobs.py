from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from apps.worker.assisted_listing_intake.worker import handle_assisted_listing_intake
from shared.auth import Role
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.infrastructure.persistence.job_queue import JobFenceRejectedError
from shared.jobs.queue import JobRequest, JobStatus
from tests.integration._authz import auth_headers

HEADERS = {
    **auth_headers(Role.EXPANSION_USER),
    "x-tenant-id": "tenant-a",
}


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "intake_durable_reliability.sqlite3")


def test_backpressure_threshold(db_path) -> None:
    bundle = _durable_bundle(db_path)
    try:
        app = create_app(persistence=bundle)
        client = TestClient(app)

        # Enqueue 200 dummy jobs to trigger backpressure
        for i in range(200):
            bundle.job_queue.enqueue(
                JobRequest(
                    job_type="assisted-listing-intake",
                    payload={"intake_id": f"IN-DUMMY-{i}", "url": f"https://www.example.com/{i}"}
                ),
                correlation_id="corr-dummy"
            )

        # Attempt to submit another job
        url = "https://www.synthetic.example/detail-77120345.html"
        resp = client.post(
            "/api/v1/operator/network-listings/intake/submit",
            json={"url": url, "heatZoneId": "HZ-01"},
            headers={
                **HEADERS,
                "X-Correlation-Id": "corr-async-bp",
                "Idempotency-Key": "idem-async-bp",
                "X-Async-Intake": "true",
            },
        )
        assert resp.status_code == 503
        assert resp.json()["detail"] == "BACKPRESSURE_ACTIVE"
        assert resp.headers["Retry-After"] == "30"
    finally:
        bundle.engine.close()


def test_fencing_optimistic_locking(db_path) -> None:
    bundle = _durable_bundle(db_path)
    try:
        # Enqueue an intake job
        job, _ = bundle.job_queue.enqueue(
            JobRequest(
                job_type="assisted-listing-intake",
                payload={"intake_id": "IN-FENCE-1", "url": "https://www.synthetic.example/detail-77120345.html"}
            ),
            correlation_id="corr-fence"
        )
        
        # Worker 1 claims job
        claimed_w1 = bundle.job_queue.claim_next(worker_id="worker-1")
        assert claimed_w1 is not None
        assert claimed_w1.fence_token == 1
        assert claimed_w1.version == 2
        
        # Worker 2 claims job (simulated lease timeout claim by modifying DB)
        bundle.engine.execute(
            "UPDATE durable_jobs SET fence_token = ?, version = ? WHERE job_id = ?",
            (2, 3, job.job_id)
        )
        
        # Worker 1 attempts to update status using its stale fence token 1
        with pytest.raises(JobFenceRejectedError):
            bundle.job_queue.update_status(
                job.job_id,
                JobStatus.RUNNING,
                expected_version=claimed_w1.version,
                fence_token=claimed_w1.fence_token
            )
            
        # Worker 1 heartbeat should also fail
        with pytest.raises(JobFenceRejectedError):
            bundle.job_queue.heartbeat(
                job.job_id,
                expected_version=claimed_w1.version,
                fence_token=claimed_w1.fence_token
            )
    finally:
        bundle.engine.close()


def test_lease_expiration_claiming(db_path) -> None:
    bundle = _durable_bundle(db_path)
    try:
        # Enqueue an intake job
        job, _ = bundle.job_queue.enqueue(
            JobRequest(
                job_type="assisted-listing-intake",
                payload={"intake_id": "IN-LEASE-1", "url": "https://www.synthetic.example/detail-77120345.html"}
            ),
            correlation_id="corr-lease"
        )
        
        # Worker 1 claims job
        claimed_w1 = bundle.job_queue.claim_next(worker_id="worker-1")
        assert claimed_w1 is not None
        
        # Modify database to make lease expired (e.g. lease_expires_at is in the past)
        past_time = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
        bundle.engine.execute(
            "UPDATE durable_jobs SET lease_expires_at = ? WHERE job_id = ?",
            (past_time, job.job_id)
        )
        
        # Worker 2 should be able to claim the expired job!
        claimed_w2 = bundle.job_queue.claim_next(worker_id="worker-2")
        assert claimed_w2 is not None
        assert claimed_w2.fence_token == claimed_w1.fence_token + 1
        assert claimed_w2.locked_by == "worker-2"
        
    finally:
        bundle.engine.close()


def test_poison_isolation_retry_limits(db_path) -> None:
    bundle = _durable_bundle(db_path)
    try:
        # Enqueue job
        job, _ = bundle.job_queue.enqueue(
            JobRequest(
                job_type="assisted-listing-intake",
                payload={"intake_id": "IN-POISON-1", "url": "https://www.synthetic.example/detail-77120345.html"}
            ),
            correlation_id="corr-poison"
        )
        
        # Pretend stage Retries exceeded
        # Identity Check has limit of 3
        job_claimed = bundle.job_queue.claim_next(worker_id="worker-1")
        
        # Set attempts to 3 in payload
        payload = dict(job_claimed.payload)
        payload["stage_attempts"] = {"CHECKING_IDENTITY": 3}
        bundle.job_queue.update_status(
            job_claimed.job_id,
            JobStatus.RUNNING,
            payload=payload,
            expected_version=job_claimed.version,
            fence_token=job_claimed.fence_token
        )
        
        # Run handler and verify it aborts with RuntimeError (exceeded max attempts)
        job_updated = bundle.job_queue.get(job_claimed.job_id)
        with pytest.raises(RuntimeError, match="CHECKING_IDENTITY exceeded max attempts"):
            handle_assisted_listing_intake(job_updated, bundle)
            
    finally:
        bundle.engine.close()
