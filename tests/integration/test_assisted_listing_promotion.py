from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.auth import Role
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


def test_promotion_saga_golden_flow() -> None:
    app = create_app()
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
    intake = submit_resp.json()
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
    assert reviewed_data["status"] == "COMPLETED"
    assert reviewed_data["reviewer_subject_id"] == "operator-expansion-manager"
    candidate_id = reviewed_data["candidate_site_id"]
    assert candidate_id is not None

    # Get candidates to verify it was saved in listing repository
    cand_resp = client.get("/api/v1/listings/candidates", headers=MANAGER_HEADERS)
    assert cand_resp.status_code == 200
    cands = cand_resp.json()["candidates"]
    candidate = next(c for c in cands if c["candidateSiteId"] == candidate_id)
    assert candidate["status"] == "CANDIDATE"

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

    # 5. Duplicate promotion prevention
    # If we submit another intake for the same listing, and try to promote it, it should fail
    # because a candidate already exists for that listing.
    dup_url = "https://www.synthetic.example/detail-77120345.html"  # same listing
    dup_submit_resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": dup_url, "heatZoneId": "HZ-01"},
        headers={
            **STAFF_HEADERS,
            "X-Correlation-Id": "corr-promote-dup",
            "Idempotency-Key": "idem-submit-dup",
        },
    )
    assert dup_submit_resp.status_code == 200
    dup_intake = dup_submit_resp.json()
    dup_intake_id = dup_intake["id"]

    # Decide the duplicate as revise (resolves it to same target listing L-xxxx)
    dup_decide_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{dup_intake_id}/decide",
        json={
            "action": "revise",
            "reason": "Revise listing",
            "riskSummary": "This revises listing L-xxxx",
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
            "targetListingId": target_listing_id,
        },
        headers={
            **MANAGER_HEADERS,
            "Idempotency-Key": f"idem-decide-{uuid4()}",
            "X-Correlation-Id": f"corr-decide-{uuid4()}",
        },
    )
    assert dup_decide_resp.status_code == 200
    dup_intake = dup_decide_resp.json()
    assert dup_intake["matchResult"]["targetListingId"] == target_listing_id

    # Trying to promote the duplicate listing should fail with 409 Conflict
    dup_promote_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{dup_intake_id}/promote",
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
    assert dup_promote_resp.status_code == 409
    assert "DUPLICATE_CANDIDATE" in dup_promote_resp.text


def test_promotion_saga_segregation_of_duties() -> None:
    app = create_app()
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
    intake = submit_resp.json()
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
