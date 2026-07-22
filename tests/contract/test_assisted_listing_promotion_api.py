from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from apps.api.app.routes.listings import AssistedIntakeStore
from apps.api.oday_api.main import create_app

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


def test_promotion_api_contract_flow() -> None:
    app = create_app()
    client = TestClient(app)

    # 1. Submit an intake to /api/v1/intakes/url (resolves to READY)
    submit_payload = {
        "original_url": "https://www.synthetic.example/detail-77120345.html",
        "scope": {"tenant_id": TENANT_A},
    }
    submit_headers = {
        **HEADERS_A,
        "Idempotency-Key": f"idem-api-submit-{uuid4()}",
    }
    submit_resp = client.post("/api/v1/intakes/url", json=submit_payload, headers=submit_headers)
    assert submit_resp.status_code == 202, submit_resp.text
    intake = submit_resp.json()
    intake_id = intake["intake_id"]

    # Transition the intake state to READY in store to satisfy the promotion prerequisite
    store = AssistedIntakeStore._instances[-1]
    store.intakes[intake_id]["state"] = "READY"
    target_listing_id = "L-GOLD-99"
    store.intakes[intake_id]["matchResult"] = {
        "targetListingId": target_listing_id,
        "confidence": 0.95,
        "contradictorySignals": [],
    }

    # Seed the listing repository
    repository = getattr(app.state, "listing_repository", None)
    if repository is None:
        from modules.listing.infrastructure.repositories import InMemoryListingRepository
        repository = InMemoryListingRepository()
        app.state.listing_repository = repository

    from modules.listing.domain.models import ListingDedupKey
    from shared.domain.models import AddressLocation, Listing

    address = AddressLocation(
        address_id="A-99",
        raw_address="100 Synthetic Way",
        normalized_address="100 Synthetic Way",
        geocode_confidence=1.0,
        h3_res_9="HZ-01",
    )
    listing = Listing(
        listing_id=target_listing_id,
        source_listing_id=target_listing_id,
        source_id="S-99",
        listing_status="watching",
        address_id="A-99",
        rent_amount=50000.0,
        currency="TWD",
        area_ping=25.0,
        floor=1,
        frontage_m=5.0,
        depth_m=12.0,
        corner_flag=False,
        parking_flag=False,
        utility_electricity_flag=True,
        utility_drainage_flag=True,
        utility_gas_flag=False,
        available_from="2026-08-01",
        snapshot_id="SN-99",
        confidence=1.0,
    )
    key = ListingDedupKey(
        source_id="S-99",
        source_listing_id=target_listing_id,
        normalized_address="100 Synthetic Way",
        rent_amount=50000.0,
        area_ping=25.0,
    )
    repository.save_listing(listing, address, key)

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
        "If-Match": f'W/"{intake["version"]}"',
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
    assert reviewed_data["status"] == "COMPLETED"
    assert reviewed_data["reviewer_subject_id"] == ACTOR_A_REVIEWER
    assert reviewed_data["candidate_site_id"] is not None
    assert reviewed_data["site_score_job_id"] is not None
    candidate = repository.list_candidates()[0]
    assert candidate.dataset_snapshot_id == "FS-SN-99"
    job = store.jobs[reviewed_data["site_score_job_id"]]
    assert job["status"] == "SUCCEEDED"
    assert job["checkpoint"] == "SCORE_QUEUED"


def test_operator_intake_uses_v1_promotion_and_authoritative_job_receipt() -> None:
    app = create_app()
    client = TestClient(app)

    submitted = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={
            "url": "https://www.synthetic.example/detail-88520242.html",
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
    intake = submitted.json()
    assert intake["stage"] == "READY"
    assert intake["tenantId"] == OPERATOR_TENANT
    assert intake["version"] == 1

    from modules.listing.domain.models import ListingDedupKey
    from shared.domain.models import AddressLocation, Listing

    target_listing_id = "L-OPERATOR-PROMOTION"
    app.state.listing_repository.save_listing(
        Listing(
            listing_id=target_listing_id,
            source_listing_id="SRC-OPERATOR-PROMOTION",
            source_id="S-OPERATOR",
            listing_status="watching",
            address_id="A-OPERATOR-PROMOTION",
            rent_amount=58000.0,
            currency="TWD",
            area_ping=18.0,
            floor=1,
            frontage_m=6.0,
            depth_m=10.0,
            corner_flag=False,
            parking_flag=False,
            utility_electricity_flag=True,
            utility_drainage_flag=True,
            utility_gas_flag=False,
            available_from="2026-08-01",
            snapshot_id="SN-OPERATOR-PROMOTION",
            confidence=0.95,
        ),
        AddressLocation(
            address_id="A-OPERATOR-PROMOTION",
            raw_address="台北市信義區松仁路 96 號 1F",
            normalized_address="台北市信義區松仁路96號1樓",
            geocode_confidence=0.95,
            h3_res_9="892d5444d63ffff",
        ),
        ListingDedupKey(
            source_id="S-OPERATOR",
            source_listing_id="SRC-OPERATOR-PROMOTION",
            normalized_address="台北市信義區松仁路96號1樓",
            rent_amount=58000.0,
            area_ping=18.0,
        ),
    )
    operator_record = app.state.operator_intake_repository.intakes[intake["id"]]
    operator_record["matchResult"]["targetListingId"] = target_listing_id
    app.state.operator_intake_repository.save_intake(operator_record)

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
            "If-Match": 'W/"1"',
        },
    )
    assert requested.status_code == 202, requested.text
    decision = requested.json()
    assert decision["proposer_subject_id"] == OPERATOR_PROPOSER
    assert app.state.operator_intake_repository.get_promotion(
        decision["promotion_decision_id"]
    ) is not None
    persisted = app.state.operator_intake_repository.intakes[intake["id"]]
    assert persisted["version"] == 2

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

    # The retry route must resolve the linked operator intake too; otherwise
    # SCORE_FAILED UI replay would fail with DEPENDENCY_CONFLICT.
    store = next(store for store in reversed(AssistedIntakeStore._instances) if job_id in store.jobs)
    store.jobs[job_id]["status"] = "FAILED"
    retry_key = f"operator-job-retry-{uuid4()}"
    retry_body = {
        "checkpoint": "SCORE_QUEUED",
        "reason": "Retry authoritative SiteScore checkpoint",
        "risk_acknowledged": True,
    }
    retry_headers = {
        "x-subject-id": ACTOR_A_REVIEWER,
        "x-tenant-id": OPERATOR_TENANT,
        "x-roles": "site_reviewer,data_owner,expansion_user",
        "x-operator-role": "expansion-manager",
        "Idempotency-Key": retry_key,
        "If-Match": 'W/"1"',
    }
    retried = client.post(
        f"/api/v1/jobs/{job_id}/retry",
        json=retry_body,
        headers=retry_headers,
    )
    assert retried.status_code == 202, retried.text
    assert retried.json()["status"] == "QUEUED"
    assert retried.json()["version"] == 2
    assert retried.headers["idempotency-replayed"] == "false"
    assert retried.headers["etag"] == 'W/"2"'

    replayed = client.post(
        f"/api/v1/jobs/{job_id}/retry",
        json=retry_body,
        headers=retry_headers,
    )
    assert replayed.status_code == 202, replayed.text
    assert replayed.json() == retried.json()
    assert replayed.headers["idempotency-replayed"] == "true"
    assert replayed.headers["etag"] == 'W/"2"'
