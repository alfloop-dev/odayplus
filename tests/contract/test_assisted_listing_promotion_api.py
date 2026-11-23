from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.app.routes.listings import AssistedIntakeStore
from apps.api.oday_api.main import create_app
from tests.intake_workflow_fixture import SyntheticIntakeWorkflowPolicy

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
    app = create_app(assisted_intake_workflow_policy=SyntheticIntakeWorkflowPolicy())
    client = TestClient(app)

    # 1. Retained V1 READY history is a promotion precondition, not fresh URL
    # creation or evidence that the native SQL parser/identity flow completed.
    store = AssistedIntakeStore._instances[-1]
    intake_id, target_listing_id = str(uuid4()), "L-GOLD-99"
    at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    intake = {
        "intake_id": intake_id, "state": "READY", "version": 1,
        "intake_method": "URL", "scope": {"tenant_id": TENANT_A},
        "submitted_by": ACTOR_A, "submitted_at": at, "updated_at": at,
        "correlation_id": str(uuid4()), "policy_state": "APPROVED_RETRIEVAL",
        "original_url": "https://www.synthetic.example/detail-77120345.html",
        "processing_history": [], "fields": [], "audit": [],
        "matchResult": {"targetListingId": target_listing_id,
                        "confidence": 0.95, "contradictorySignals": []},
    }
    store.intakes[intake_id] = intake
    retained = client.get(f"/api/v1/intakes/{intake_id}", headers=HEADERS_A)
    assert retained.status_code == 200 and retained.json()["state"] == "READY", retained.text

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
    # This positive synthetic fixture records a completed hard-rule check.
    object.__setattr__(listing, "hard_rule_failures", [])
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
    # Explicit POC persistence has no canonical workflow.jobs dispatcher.
    # A successful review response here used to manufacture a score receipt.
    assert review_resp.status_code == 503, review_resp.text
    assert "SCORE_DELIVERY_UNAVAILABLE" in review_resp.text
    retained = client.get(f"/api/v1/promotion-decisions/{promo_decision_id}", headers=HEADERS_A)
    assert retained.status_code == 200, retained.text
    assert retained.json()["status"] == "PENDING_REVIEW"
    assert retained.json()["version"] == promo_data["version"]
    assert not retained.json().get("candidate_site_id")
    assert not retained.json().get("site_score_job_id")
    assert not any(c.candidate_site.listing_id == target_listing_id for c in repository.list_candidates())


@pytest.mark.parametrize("score_failure", [False, True])
def test_operator_intake_cannot_invent_delivery_without_a_canonical_dispatcher(monkeypatch, score_failure) -> None:
    monkeypatch.setenv("CI", "1")
    app = create_app(assisted_intake_workflow_policy=SyntheticIntakeWorkflowPolicy())
    client = TestClient(app)

    # Explicit historical Operator row. Keep the real V1 promotion adapter and
    # dispatcher refusal; neither retired URL POST is a fixture-creation API.
    intake = {
        "id": "IN-RETAINED-PROMOTION", "tenantId": OPERATOR_TENANT,
        "owner": OPERATOR_PROPOSER, "submitter": OPERATOR_PROPOSER,
        "submittedBy": OPERATOR_PROPOSER, "stage": "READY", "version": 1,
        "intakeMethod": "URL", "heatZoneId": "HZ-01",
        "originalUrl": "https://www.synthetic.example/detail-88520242.html",
        "parsedFields": {}, "matchResult": {"targetListingId": None}, "auditEvents": [],
    }
    app.state.operator_intake_repository.save_intake(intake)
    retained = client.get(
        f"/api/v1/operator/network-listings/intake/{intake['id']}",
        headers={"x-subject-id": OPERATOR_PROPOSER, "x-tenant-id": OPERATOR_TENANT,
                 "x-roles": "site_reviewer,data_owner,expansion_user",
                 "x-operator-role": "expansion-manager"},
    )
    assert retained.status_code == 200, retained.text
    assert retained.json()["stage"] == "READY"
    assert retained.json()["tenantId"] == OPERATOR_TENANT
    assert retained.json()["version"] == 1

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
    # Record the fixture verdict; absence must continue to fail in production.
    listing = app.state.listing_repository.get_listing(target_listing_id)
    assert listing is not None
    object.__setattr__(listing, "hard_rule_failures", [])
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
            **({"X-ODP-Test-Fault": "score-failure"} if score_failure else {}),
        },
    )
    assert reviewed.status_code == 503, reviewed.text
    assert "SCORE_DELIVERY_UNAVAILABLE" in reviewed.text
    retained = client.get(
        f"/api/v1/intakes/{intake['id']}/promotion-decision",
        headers={"x-subject-id": ACTOR_A_REVIEWER, "x-tenant-id": OPERATOR_TENANT,
                 "x-roles": "site_reviewer,data_owner,expansion_user"},
    )
    assert retained.status_code == 200, retained.text
    assert retained.json()["status"] == "PENDING_REVIEW"
    assert retained.json()["version"] == decision["version"]
    assert not retained.json().get("candidate_site_id")
    assert not retained.json().get("site_score_job_id")
    assert not any(c.candidate_site.listing_id == target_listing_id
                   for c in app.state.listing_repository.list_candidates())
