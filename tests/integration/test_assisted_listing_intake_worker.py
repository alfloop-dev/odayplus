from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from apps.worker.oday_worker.main import ODayWorker
from modules.external_data.application.assisted_intake import RETRIEVAL_CORPUS
from modules.external_data.security.assisted_listing_retrieval import FetchResponse
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


def test_async_intake_worker_happy_path(db_path, monkeypatch) -> None:
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

        job = bundle.job_queue.get(data["jobId"])
        assert job is not None
        assert job.job_type == "assisted-listing-intake"
        assert job.payload["url"] == url
        assert job.status == JobStatus.QUEUED
        assert job.fence_token == 0

        # Let the authorized worker claim and execute the command. Synthetic
        # retrieval is dependency-injected from
        # the deterministic corpus; no outbound network is used.
        raw = RETRIEVAL_CORPUS[url].raw
        from modules.external_data.security import assisted_listing_retrieval

        monkeypatch.setattr(
            assisted_listing_retrieval,
            "_resolve_host",
            lambda _host: ("93.184.216.34",),
        )
        monkeypatch.setattr(
            assisted_listing_retrieval.DefaultRetrievalFetcher,
            "__call__",
            lambda _self, _url, *, timeout_seconds, max_response_bytes: FetchResponse(
                status_code=200,
                headers={"Content-Type": "text/html"},
                body=json.dumps(raw).encode(),
            ),
        )
        assert ODayWorker(persistence=bundle).run_once() is True
        completed_job = bundle.job_queue.get(job.job_id)
        assert completed_job is not None
        assert completed_job.status == JobStatus.SUCCEEDED

        # Retrieve the intake record after processing
        get_resp = client.get(
            f"/api/v1/operator/network-listings/intake/{data['id']}", headers=HEADERS
        )
        assert get_resp.status_code == 200
        result = get_resp.json()
        assert result["stage"] == "READY"
        assert result["matchResult"]["outcome"] == "NEW"
        persisted = bundle.operator_intake_repository.list_intakes()[0]
        assert any(
            receipt["category"] == "job"
            and receipt["action"] == "RUN"
            and receipt["receipt"]["status"] == "RUNNING"
            for receipt in persisted["lifecycleReceipts"]
        )

    finally:
        bundle.engine.close()
