from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.auth import Role
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.infrastructure.persistence.operator_network_listings import DurableAssistedIntakeRepository
from shared.jobs.queue import JobRequest, JobStatus
from tests.integration._authz import auth_headers

HEADERS = {
    **auth_headers(Role.EXPANSION_USER),
    "x-tenant-id": "tenant-a",
}


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "intake_durable_worker.sqlite3")


def test_existing_async_intake_worker_happy_path(db_path) -> None:
    # 1. Start application with a durable SQLite bundle
    bundle = _durable_bundle(db_path)
    try:
        app = create_app(persistence=bundle)
        client = TestClient(app)

        # Retained pre-cutover work must still execute. Seed the existing
        # repository + queue contracts explicitly, never the retired HTTP
        # fresh endpoint or a fabricated native READY result.
        url = "https://www.synthetic.example/detail-77120345.html"
        intake_id = "IN-HISTORICAL-WORKER"
        repository = DurableAssistedIntakeRepository(SqliteDocumentStore(bundle.engine))
        pending, created = bundle.job_queue.enqueue(
            JobRequest(job_type="assisted-listing-intake", payload={
                "intake_id": intake_id, "url": url, "heat_zone_id": "HZ-01",
                "actor_role_id": Role.EXPANSION_USER.value,
                "actor_name": HEADERS["x-subject-id"], "tenant_id": "tenant-a",
            }, idempotency_key="idem-async-happy"),
            correlation_id="corr-async-happy",
        )
        assert created
        repository.save_intake({
            "id": intake_id, "tenantId": "tenant-a", "originalUrl": url,
            "canonicalUrl": url, "heatZoneId": "HZ-01", "intakeMethod": "URL",
            "owner": HEADERS["x-subject-id"], "submitter": HEADERS["x-subject-id"],
            "stage": "SUBMITTED", "version": 1, "jobId": pending.job_id,
            "parsedFields": {}, "matchResult": None, "auditEvents": [],
        })
        resp = client.get(
            f"/api/v1/operator/network-listings/intake/{intake_id}", headers=HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["stage"] == "SUBMITTED"

        # Verify job is enqueued in database
        active_jobs = bundle.job_queue.count_active_jobs()
        assert active_jobs == 1

        job = bundle.job_queue.claim_next(worker_id="test-worker")
        assert job is not None
        assert job.job_id == pending.job_id
        assert job.job_type == "assisted-listing-intake"
        assert job.payload["intake_id"] == intake_id
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
            f"/api/v1/operator/network-listings/intake/{intake_id}", headers=HEADERS
        )
        assert get_resp.status_code == 200
        result = get_resp.json()
        assert result["stage"] == "READY"
        assert result["matchResult"]["outcome"] == "NEW"
        assert result["version"] > 1
        assert result["snapshotId"]
        assert result["parsedFields"]["rent"]["normalizedValue"] == 45000
        assert [row["id"] for row in repository.list_intakes()] == [intake_id]
        assert repository.list_idempotency_records() == []
        assert bundle.job_queue.count_active_jobs() == 0

    finally:
        bundle.engine.close()
