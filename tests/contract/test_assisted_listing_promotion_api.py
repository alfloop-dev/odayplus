from __future__ import annotations

import json
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from apps.worker.oday_worker.main import ODayWorker
from modules.external_data.application.assisted_intake import RETRIEVAL_CORPUS
from modules.external_data.security.assisted_listing_retrieval import FetchResponse
from modules.opsboard.application.network_listings import NetworkListingService
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.jobs.queue import JobStatus

TENANT_A = "00000000-0000-0000-0000-000000000001"
ACTOR_A = "00000000-0000-0000-0000-000000000101"
ACTOR_A_REVIEWER = "00000000-0000-0000-0000-000000000102"
OPERATOR_TENANT = "tenant-a"
OPERATOR_PROPOSER = "operator-expansion-manager"

HEADERS_A = {
    "x-subject-id": ACTOR_A,
    "x-tenant-id": TENANT_A,
    "x-roles": "site_reviewer,data_owner,expansion_user",
    "x-operator-role": "expansion-manager",
}

HEADERS_A_REVIEWER = {
    "x-subject-id": ACTOR_A_REVIEWER,
    "x-tenant-id": TENANT_A,
    "x-roles": "site_reviewer,data_owner,expansion_user",
    "x-operator-role": "expansion-manager",
}


def _inject_synthetic_retrieval(monkeypatch, url: str) -> None:
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


def test_promotion_api_contract_flow(tmp_path, monkeypatch) -> None:
    bundle = _durable_bundle(str(tmp_path / "promotion-contract.sqlite3"))
    app = create_app(persistence=bundle)
    client = TestClient(app)

    # 1. Submit persists SUBMITTED; the authorized worker advances it.
    url = "https://www.synthetic.example/detail-77120345.html"
    _inject_synthetic_retrieval(monkeypatch, url)
    submit_payload = {
        "original_url": url,
        "scope": {"tenant_id": TENANT_A},
    }
    submit_headers = {
        **HEADERS_A,
        "Idempotency-Key": f"idem-api-submit-{uuid4()}",
    }
    submit_resp = client.post("/api/v1/intakes/url", json=submit_payload, headers=submit_headers)
    assert submit_resp.status_code == 202, submit_resp.text
    intake = submit_resp.json()
    assert intake["state"] == "SUBMITTED"
    intake_id = intake["intake_id"]
    assert ODayWorker(persistence=bundle).run_once() is True

    detail = client.get(f"/api/v1/intakes/{intake_id}", headers=HEADERS_A)
    assert detail.status_code == 200, detail.text
    assert detail.json()["state"] == "READY"
    service = NetworkListingService(
        listing_repository=bundle.listing_repository,
        intake_repository=app.state.operator_intake_repository,
    )
    created = service.decide_intake(
        intake_id=intake_id,
        action="create",
        actor_role_id="expansionManager",
        actor_name=ACTOR_A_REVIEWER,
        reason="Create the reviewed listing before promotion.",
        risk_summary="Creates one listing with durable intake lineage.",
        risk_acknowledged=True,
        idempotency_key=f"create-{uuid4()}",
        correlation_id=str(uuid4()),
    )

    # 2. Request promotion (None -> REQUESTED -> VALIDATING)
    promo_payload = {
        "target_format_code": "FORMAT-A",
        "reason": "Test promotion reason",
        "gate_snapshot_sha256": "e0a62b56e0a62b56e0a62b56e0a62b56e0a62b56e0a62b56e0a62b56e0a62b56",
        "risk_acknowledged": True,
    }
    promo_headers = {
        **HEADERS_A,
        "Idempotency-Key": f"idem-api-promo-{uuid4()}",
        "If-Match": f'W/"{created["version"]}"',
    }
    promo_resp = client.post(
        f"/api/v1/intakes/{intake_id}/promotion-requests",
        json=promo_payload,
        headers=promo_headers,
    )
    assert promo_resp.status_code == 202, promo_resp.text
    promo_data = promo_resp.json()
    assert promo_data["intake_id"] == intake_id
    assert promo_data["status"] == "PENDING_REVIEW"
    assert promo_data["proposer_subject_id"] == ACTOR_A
    promo_decision_id = promo_data["promotion_decision_id"]

    # 3. Get promotion decision receipt
    get_resp = client.get(
        f"/api/v1/promotion-decisions/{promo_decision_id}",
        headers=HEADERS_A,
    )
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json()["promotion_decision_id"] == promo_decision_id

    # 4. Review & approve (segregation of duties satisfied)
    review_payload = {
        "decision": "APPROVE",
        "reason": "Approved location fit",
        "risk_acknowledged": True,
    }
    review_headers = {
        **HEADERS_A_REVIEWER,
        "Idempotency-Key": f"idem-api-review-{uuid4()}",
        "If-Match": f'W/"{promo_data["version"]}"',
    }
    review_resp = client.post(
        f"/api/v1/promotion-decisions/{promo_decision_id}/actions/review",
        json=review_payload,
        headers=review_headers,
    )
    assert review_resp.status_code == 200, review_resp.text
    reviewed_data = review_resp.json()
    assert reviewed_data["status"] == "SCORE_QUEUED"
    assert reviewed_data["reviewer_subject_id"] == ACTOR_A_REVIEWER
    assert reviewed_data["candidate_site_id"] is not None
    assert reviewed_data["site_score_job_id"] is not None
    job = bundle.job_queue.get(reviewed_data["site_score_job_id"])
    assert job is not None
    assert job.status == JobStatus.QUEUED
    assert job.payload["candidate_site_id"] == reviewed_data["candidate_site_id"]
    assert ODayWorker(persistence=bundle).run_once() is True
    completed = bundle.job_queue.get(reviewed_data["site_score_job_id"])
    assert completed is not None
    assert completed.status == JobStatus.SUCCEEDED
    final = client.get(
        f"/api/v1/promotion-decisions/{promo_decision_id}",
        headers=HEADERS_A_REVIEWER,
    )
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "COMPLETED"
    bundle.engine.close()


def test_operator_intake_uses_v1_promotion_and_authoritative_job_receipt(
    tmp_path,
    monkeypatch,
) -> None:
    bundle = _durable_bundle(str(tmp_path / "operator-promotion-contract.sqlite3"))
    app = create_app(persistence=bundle)
    client = TestClient(app)

    url = "https://www.synthetic.example/detail-88520242.html"
    _inject_synthetic_retrieval(monkeypatch, url)
    submitted = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={
            "url": url,
            "heatZoneId": "HZ-01",
        },
        headers={
            "x-subject-id": OPERATOR_PROPOSER,
            "x-tenant-id": OPERATOR_TENANT,
            "x-roles": "site_reviewer,data_owner,expansion_user",
            "x-operator-role": "expansion-manager",
            "Idempotency-Key": f"operator-submit-{uuid4()}",
            "X-Correlation-Id": str(uuid4()),
        },
    )
    assert submitted.status_code == 200, submitted.text
    queued_intake = submitted.json()
    assert queued_intake["stage"] == "SUBMITTED"
    assert ODayWorker(persistence=bundle).run_once() is True
    readback = client.get(
        f"/api/v1/operator/network-listings/intake/{queued_intake['id']}",
        headers={
            "x-subject-id": OPERATOR_PROPOSER,
            "x-tenant-id": OPERATOR_TENANT,
            "x-roles": "site_reviewer,data_owner,expansion_user",
            "x-operator-role": "expansion-manager",
        },
    )
    assert readback.status_code == 200, readback.text
    intake = readback.json()
    assert intake["stage"] == "READY"
    assert intake["tenantId"] == OPERATOR_TENANT
    assert intake["matchResult"]["targetListingId"] == "L-2024"

    requested = client.post(
        f"/api/v1/intakes/{intake['id']}/promotion-requests",
        json={
            "target_format_code": "FORMAT-A",
            "reason": "Operator intake promotion through the reviewed v1 contract",
            "gate_snapshot_sha256": "a" * 64,
            "risk_acknowledged": True,
        },
        headers={
            "x-subject-id": OPERATOR_PROPOSER,
            "x-tenant-id": OPERATOR_TENANT,
            "x-roles": "site_reviewer,data_owner,expansion_user",
            "x-operator-role": "expansion-manager",
            "Idempotency-Key": f"operator-promotion-{uuid4()}",
            "If-Match": f'W/"{intake["version"]}"',
        },
    )
    assert requested.status_code == 202, requested.text
    decision = requested.json()
    assert decision["proposer_subject_id"] == OPERATOR_PROPOSER
    assert app.state.operator_intake_repository.get_promotion(
        decision["promotion_decision_id"]
    ) is not None
    persisted = NetworkListingService(
        listing_repository=bundle.listing_repository,
        intake_repository=app.state.operator_intake_repository,
    ).get_intake(intake["id"])
    assert persisted["version"] > intake["version"]

    hydrated = client.get(
        f"/api/v1/intakes/{intake['id']}/promotion-decision",
        headers={
            "x-subject-id": ACTOR_A_REVIEWER,
            "x-tenant-id": OPERATOR_TENANT,
            "x-roles": "site_reviewer,data_owner,expansion_user",
            "x-operator-role": "expansion-manager",
        },
    )
    assert hydrated.status_code == 200, hydrated.text
    assert hydrated.json()["promotion_decision_id"] == decision["promotion_decision_id"]
    assert hydrated.headers["etag"] == f'W/"{decision["version"]}"'

    reviewed = client.post(
        f"/api/v1/promotion-decisions/{decision['promotion_decision_id']}/actions/review",
        json={
            "decision": "APPROVE",
            "reason": "Independent manager approved the operator intake",
            "risk_acknowledged": True,
        },
        headers={
            "x-subject-id": ACTOR_A_REVIEWER,
            "x-tenant-id": OPERATOR_TENANT,
            "x-roles": "site_reviewer,data_owner,expansion_user",
            "x-operator-role": "expansion-manager",
            "Idempotency-Key": f"operator-review-{uuid4()}",
            "If-Match": f'W/"{decision["version"]}"',
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    job_id = reviewed.json()["site_score_job_id"]
    receipt = client.get(
        f"/api/v1/jobs/{job_id}/receipt",
        headers={
            "x-subject-id": ACTOR_A_REVIEWER,
            "x-tenant-id": OPERATOR_TENANT,
            "x-roles": "site_reviewer,data_owner,expansion_user",
            "x-operator-role": "expansion-manager",
        },
    )
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["job_id"] == job_id
    assert receipt.json()["version"] == 1
    assert receipt.json()["status"] == "QUEUED"
    assert ODayWorker(persistence=bundle).run_once() is True
    completed = client.get(
        f"/api/v1/jobs/{job_id}/receipt",
        headers={
            "x-subject-id": ACTOR_A_REVIEWER,
            "x-tenant-id": OPERATOR_TENANT,
            "x-roles": "site_reviewer,data_owner,expansion_user",
            "x-operator-role": "expansion-manager",
        },
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "SUCCEEDED"
    bundle.engine.close()
