from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from apps.worker.oday_worker.main import ODayWorker
from modules.opsboard.application.network_listings import NetworkListingService
from shared.auth import Role
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.jobs.queue import JobStatus
from tests.integration._authz import auth_headers

HEADERS = {
    **auth_headers(Role.EXPANSION_USER),
    "x-tenant-id": "tenant-a",
}

MANAGER_HEADERS = {
    **auth_headers(
        Role.SITE_REVIEWER,
        Role.EXPANSION_USER,
        subject="operator-expansion-manager",
    ),
    "x-operator-role": "expansion-manager",
    "x-tenant-id": "tenant-a",
}

STAFF_HEADERS = {
    **auth_headers(
        Role.EXPANSION_USER,
        subject="operator-expansion-staff",
    ),
    "x-operator-role": "expansion-user",
    "x-tenant-id": "tenant-a",
}


def _advance_submitted_intake(
    client: TestClient,
    submitted: dict,
    *,
    headers: dict[str, str],
) -> dict:
    assert submitted["stage"] == "SUBMITTED"
    queue = client.app.state.job_queue
    job = queue.claim_next(worker_id="promotion-intake-worker")
    assert job is not None
    assert job.status == JobStatus.RUNNING
    assert job.payload["intake_id"] == submitted["id"]

    from modules.external_data.application.assisted_intake import retrieve

    service = NetworkListingService(
        listing_repository=client.app.state.listing_repository,
        intake_repository=client.app.state.operator_intake_repository,
    )
    service.process_queued_intake(
        intake_id=submitted["id"],
        retrieval_provider=retrieve,
        correlation_id=job.correlation_id,
        attempt=job.attempts,
    )
    assert queue.complete(job.job_id)
    readback = client.get(
        f"/api/v1/operator/network-listings/intake/{submitted['id']}",
        headers=headers,
    )
    assert readback.status_code == 200, readback.text
    return readback.json()


def test_promotion_saga_golden_flow(tmp_path) -> None:
    bundle = _durable_bundle(str(tmp_path / "promotion-golden.sqlite3"))
    app = create_app(persistence=bundle)
    client = TestClient(app)

    # 1. Submit a listing to create a READY intake
    url = "https://www.synthetic.example/detail-77120345.html"
    submit_resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url, "heatZoneId": "HZ-01"},
        headers={
            **STAFF_HEADERS,  # proposer is staff
            "X-Correlation-Id": "corr-promote-flow",
            "Idempotency-Key": "idem-submit-promote",
        },
    )
    assert submit_resp.status_code == 200, submit_resp.text
    submitted = submit_resp.json()
    assert submitted["stage"] == "SUBMITTED"
    intake = _advance_submitted_intake(
        client,
        submitted,
        headers=STAFF_HEADERS,
    )
    assert intake["stage"] == "READY"
    intake_id = intake["id"]

    # 2. Decide the intake (action="create" to resolve it to a listing)
    decide_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/decide",
        json={
            "action": "create",
            "reason": "Creating new listing",
            "riskSummary": "This creates new listing L-xxxx",
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
        headers={
            **MANAGER_HEADERS,
            "Idempotency-Key": f"idem-decide-{uuid4()}",
            "X-Correlation-Id": f"corr-decide-{uuid4()}",
        },
    )
    assert decide_resp.status_code == 200, decide_resp.text
    intake = decide_resp.json()
    target_listing_id = intake["matchResult"]["targetListingId"]
    assert target_listing_id is not None

    # 3. Promote the intake (Propose step)
    promote_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/promote",
        json={
            "actorRoleId": "expansionUser",
            "actorName": "operator-expansion-staff",
            "reason": "Suitable location for expansion",
            "riskSummary": "Promoting creates a new candidate site",
            "riskAcknowledged": True,
        },
        headers={
            **STAFF_HEADERS,
            "X-Correlation-Id": "corr-promote-exec",
            "Idempotency-Key": "idem-promote-exec",
        },
    )
    assert promote_resp.status_code == 200, promote_resp.text
    result = promote_resp.json()
    assert result["created"] is False
    assert result["status"] == "PENDING_REVIEW"
    promo_decision_id = result["promotion_decision_id"]

    # 3b. Review & Approve step (by a separate manager)
    review_resp = client.post(
        f"/api/v1/promotion-decisions/{promo_decision_id}/actions/review",
        json={
            "decision": "APPROVE",
            "reason": "Approved location fit",
            "risk_acknowledged": True,
        },
        headers={
            **MANAGER_HEADERS,
            "Idempotency-Key": "idem-promote-review-exec",
            "If-Match": f'W/"{result["version"]}"',
        },
    )
    assert review_resp.status_code == 200, review_resp.text
    reviewed_data = review_resp.json()
    assert reviewed_data["status"] == "SCORE_QUEUED"
    assert reviewed_data["reviewer_subject_id"] == "operator-expansion-manager"
    candidate_id = reviewed_data["candidate_site_id"]
    assert candidate_id is not None

    # Get candidates to verify it was saved in listing repository
    cand_resp = client.get("/api/v1/listings/candidates", headers=MANAGER_HEADERS)
    assert cand_resp.status_code == 200
    cands = cand_resp.json()["candidates"]
    candidate = next(c for c in cands if c["candidateSiteId"] == candidate_id)
    assert candidate["status"] == "CANDIDATE"
    assert ODayWorker(persistence=bundle).run_once() is True
    completed = client.get(
        f"/api/v1/promotion-decisions/{promo_decision_id}",
        headers=MANAGER_HEADERS,
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "COMPLETED"

    # 4. Idempotency test (promoting the same intake with same key should return same result)
    replay_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/promote",
        json={
            "actorRoleId": "expansionUser",
            "actorName": "operator-expansion-staff",
            "reason": "Suitable location for expansion",
            "riskSummary": "Promoting creates a new candidate site",
            "riskAcknowledged": True,
        },
        headers={
            **STAFF_HEADERS,
            "X-Correlation-Id": "corr-promote-exec",
            "Idempotency-Key": "idem-promote-exec",
        },
    )
    assert replay_resp.status_code == 200, replay_resp.text
    assert replay_resp.json()["promotion_decision_id"] == promo_decision_id

    # A new command key still resolves to the authoritative completed decision;
    # it must not create a second Candidate for the same listing.
    dup_promote_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/promote",
        json={
            "actorRoleId": "expansionUser",
            "actorName": "operator-expansion-staff",
            "reason": "Suitable location",
            "riskSummary": "Creating candidate site",
            "riskAcknowledged": True,
        },
        headers={
            **STAFF_HEADERS,
            "X-Correlation-Id": "corr-promote-dup-exec",
            "Idempotency-Key": "idem-promote-dup-exec",
        },
    )
    assert dup_promote_resp.status_code == 200, dup_promote_resp.text
    assert (
        dup_promote_resp.json()["promotion_decision_id"]
        == promo_decision_id
    )
    candidates_after = client.get(
        "/api/v1/listings/candidates",
        headers=MANAGER_HEADERS,
    ).json()["candidates"]
    assert [
        row["candidateSiteId"]
        for row in candidates_after
        if row["candidateSiteId"] == candidate_id
    ] == [candidate_id]
    bundle.engine.close()


def test_promotion_saga_segregation_of_duties(tmp_path) -> None:
    bundle = _durable_bundle(str(tmp_path / "promotion-sod.sqlite3"))
    app = create_app(persistence=bundle)
    client = TestClient(app)

    # Submit intake with manager role
    url = "https://www.synthetic.example/detail-77120345.html"
    submit_resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url, "heatZoneId": "HZ-01"},
        headers={
            **MANAGER_HEADERS,  # proposer is manager
            "X-Correlation-Id": "corr-promote-segg",
            "Idempotency-Key": "idem-submit-segg",
        },
    )
    assert submit_resp.status_code == 200, submit_resp.text
    submitted = submit_resp.json()
    assert submitted["stage"] == "SUBMITTED"
    intake = _advance_submitted_intake(
        client,
        submitted,
        headers=MANAGER_HEADERS,
    )
    intake_id = intake["id"]

    # Decide the intake to resolve it to a listing
    decide_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/decide",
        json={
            "action": "create",
            "reason": "Creating new listing",
            "riskSummary": "This creates new listing L-xxxx",
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
        headers={
            **MANAGER_HEADERS,
            "Idempotency-Key": f"idem-decide-{uuid4()}",
            "X-Correlation-Id": f"corr-decide-{uuid4()}",
        },
    )
    assert decide_resp.status_code == 200
    intake = decide_resp.json()
    assert intake["matchResult"]["targetListingId"] is not None

    # Request promotion with manager actorName
    promote_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/promote",
        json={
            "actorRoleId": "expansionManager",
            "actorName": "operator-expansion-manager",  # proposer is manager
            "reason": "Suitable location",
            "riskSummary": "Creating candidate site",
            "riskAcknowledged": True,
        },
        headers={
            **MANAGER_HEADERS,
            "X-Correlation-Id": "corr-promote-segg-exec",
            "Idempotency-Key": "idem-promote-segg-exec",
        },
    )
    assert promote_resp.status_code == 200
    promo_data = promote_resp.json()
    promo_decision_id = promo_data["promotion_decision_id"]

    # Try to approve the promotion with the SAME manager (self-review)
    # This should fail with 403 Forbidden (SELF_REVIEW_DENIED)
    review_resp = client.post(
        f"/api/v1/promotion-decisions/{promo_decision_id}/actions/review",
        json={
            "decision": "APPROVE",
            "reason": "Manager trying to self-approve",
            "risk_acknowledged": True,
        },
        headers={
            **MANAGER_HEADERS,  # same actor subject "operator-expansion-manager"
            "Idempotency-Key": f"idem-review-segg-{uuid4()}",
            "If-Match": f'W/"{promo_data["version"]}"',
        },
    )
    assert review_resp.status_code == 403
    assert "SELF_REVIEW_DENIED" in review_resp.text
    bundle.engine.close()
