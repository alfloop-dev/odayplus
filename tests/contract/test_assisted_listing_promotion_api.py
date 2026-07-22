from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from apps.api.app.routes.listings import AssistedIntakeStore
from apps.api.oday_api.main import create_app

TENANT_A = "00000000-0000-0000-0000-000000000001"
ACTOR_A = "00000000-0000-0000-0000-000000000101"
ACTOR_A_REVIEWER = "00000000-0000-0000-0000-000000000102"

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
    assert job["status"] == "COMPLETED"
    assert job["checkpoint"] == "SCORE_QUEUED"
