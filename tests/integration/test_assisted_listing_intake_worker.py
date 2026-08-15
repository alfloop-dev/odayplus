from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.auth import Role
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.jobs.queue import JobStatus
from tests.integration._authz import auth_headers

HEADERS = {
    **auth_headers(Role.EXPANSION_USER),
    "x-tenant-id": "tenant-a",
}


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "intake_durable_worker.sqlite3")


def test_async_intake_worker_happy_path(db_path) -> None:
    # 1. Start application with a durable SQLite bundle
    bundle = _durable_bundle(db_path)
    try:
        app = create_app(persistence=bundle)
        client = TestClient(app)

        # 2. Submit async intake
        url = "https://www.synthetic.example/detail-77120345.html"
        resp = client.post(
            "/api/v1/operator/network-listings/intake/submit",
            json={"url": url, "heatZoneId": "HZ-01"},
            headers={
                **HEADERS,
                "X-Correlation-Id": "corr-async-happy",
                "Idempotency-Key": "idem-async-happy",
                "X-Async-Intake": "true",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["stage"] == "SUBMITTED"  # Asynchronously enqueued!

        # Verify job is enqueued in database
        active_jobs = bundle.job_queue.count_active_jobs()
        assert active_jobs == 1

        job = bundle.job_queue.claim_next(worker_id="test-worker")
        assert job is not None
        assert job.job_type == "assisted-listing-intake"
        assert job.payload["url"] == url
        assert job.status == JobStatus.RUNNING
        assert job.fence_token == 1

        # Execute job via our worker handler
        from apps.worker.assisted_listing_intake.worker import handle_assisted_listing_intake

        handle_assisted_listing_intake(job, bundle)

        # Verify job status can be marked as succeeded using latest database version
        latest_job = bundle.job_queue.get(job.job_id)
        assert latest_job is not None
        bundle.job_queue.update_status(
            job.job_id,
            JobStatus.SUCCEEDED,
            expected_version=latest_job.version,
            fence_token=latest_job.fence_token,
        )

        # Retrieve the intake record after processing
        get_resp = client.get(
            f"/api/v1/operator/network-listings/intake/{data['id']}", headers=HEADERS
        )
        assert get_resp.status_code == 200
        result = get_resp.json()
        assert result["stage"] == "READY"
        assert result["matchResult"]["outcome"] == "NEW"

    finally:
        bundle.engine.close()
