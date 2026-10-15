from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.app.routes.listings import V1ListingRepositoryAdapter
from apps.api.oday_api.main import create_app
from modules.listing.application.promotion import PromotionService
from modules.listing.domain.intake_states import (
    Actor,
    PrincipalRole,
    TransitionContext,
)
from modules.listing.domain.models import ListingDedupKey
from modules.listing.tests.test_promotion_candidate_gate import _ExplicitTestDispatcher
from shared.auth import Role
from shared.domain import AddressLocation, Listing
from shared.infrastructure.persistence.factory import _durable_bundle, _memory_bundle
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


class _PromotionRepository:
    def __init__(self) -> None:
        self.promotions: dict[str, dict] = {}

    def save_promotion(self, promotion: dict) -> None:
        self.promotions[promotion["promotion_decision_id"]] = dict(promotion)

    def get_promotion(self, promotion_id: str) -> dict | None:
        promotion = self.promotions.get(promotion_id)
        return None if promotion is None else dict(promotion)

    def list_promotions(self) -> list[dict]:
        return [dict(promotion) for promotion in self.promotions.values()]


class _IntakeRepository:
    def __init__(self, intake: dict) -> None:
        self.intake = intake

    def get_listing_intake(self, intake_id: str) -> dict | None:
        return self.intake if self.intake["id"] == intake_id else None


@pytest.mark.parametrize("target_format", ["FMT-MICRO-STORE", "FMT-STANDARD-STORE"])
def test_durable_promotion_preserves_requested_format_and_candidate(
    tmp_path, target_format,
) -> None:
    bundle = _durable_bundle(tmp_path / "promotion-score-failure.sqlite3")
    listing = Listing(
        listing_id="listing-score-failure",
        source_listing_id="provider-score-failure",
        source_id="approved-provider",
        address_id="address-score-failure",
        rent_amount=54_000,
        area_ping=22,
        floor="1F",
        frontage_m=5,
        confidence=0.9,
    )
    object.__setattr__(listing, "hard_rule_failures", [])
    address = AddressLocation(
        address_id=listing.address_id,
        raw_address="新北市板橋區府中路 26 號 1F",
        normalized_address="新北市板橋區府中路26號1樓",
        geocode_confidence=0.95,
        h3_res_9="8929a1d4d67ffff",
    )
    key = ListingDedupKey(
        source_id=listing.source_id,
        source_listing_id=listing.source_listing_id,
        normalized_address=address.normalized_address,
        rent_amount=listing.rent_amount,
        area_ping=listing.area_ping,
    )
    promotions = _PromotionRepository()
    intake = {
        "id": "intake-score-failure",
        "tenantId": "tenant-a",
        "matchResult": {"targetListingId": listing.listing_id},
    }
    adapter = V1ListingRepositoryAdapter(bundle.listing_repository)

    service = PromotionService(
        promotion_repository=promotions,
        listing_repository=adapter,
        intake_repository=_IntakeRepository(intake),
        score_dispatcher=_ExplicitTestDispatcher(),
    )
    try:
        bundle.listing_repository.save_listing(listing, address, key)
        requested = service.request_promotion(
            intake_id=intake["id"],
            target_format_code=target_format,
            reason="request durable candidate",
            gate_snapshot_sha256="a" * 64,
            context=TransitionContext(
                actor=Actor(
                    actor_id="proposer",
                    role=PrincipalRole.EXPANSION_STAFF,
                    tenant_id="tenant-a",
                ),
                idempotency_key="request-score-failure",
                correlation_id="corr-score-failure",
            ),
        )

        service.review_promotion(
            promotion_decision_id=requested["promotion_decision_id"],
            decision="APPROVE",
            reason="independent review",
            risk_acknowledged=True,
            context=TransitionContext(
                actor=Actor(
                    actor_id="reviewer",
                    role=PrincipalRole.EXPANSION_MANAGER,
                    tenant_id="tenant-a",
                ),
                idempotency_key="review-score-failure",
                correlation_id="corr-score-failure",
            ),
        )

        failed = promotions.get_promotion(requested["promotion_decision_id"])
        candidates = bundle.listing_repository.list_candidates()
        assert failed is not None
        assert failed["status"] == "SCORE_QUEUED"
        assert failed["target_format_code"] == target_format
        assert len(candidates) == 1
        assert candidates[0].candidate_site.target_format_code == target_format
        assert candidates[0].candidate_site.site_status == "new"
        assert candidates[0].score is None
        assert service.score_dispatcher.queue.get(failed["site_score_job_id"]) is not None
        assert candidates[0].listing == listing
        assert candidates[0].address == address
    finally:
        bundle.engine.close()


def _synthetic_measured_listing_app():
    """Keep local fixture rows and add an explicitly measured test listing.

    Manual create intentionally does not manufacture geocoder evidence. These
    saga tests exercise approval against a synthetic pre-existing measurement,
    then bind the intake through the real revise decision endpoint.
    """
    from modules.opsboard.application.network_listings import NetworkListingService

    bundle = _memory_bundle()
    NetworkListingService(listing_repository=bundle.listing_repository)
    listing = Listing(
        listing_id="L-SYNTHETIC-MEASURED-77120345", source_id="SRC-SYNTHETIC",
        source_listing_id="synthetic-77120345", listing_status="active",
        address_id="ADDR-SYNTHETIC-MEASURED-77120345", rent_amount=45000,
        area_ping=16, floor="1F", frontage_m=5.5,
        snapshot_id="SNAP-SYNTHETIC-MEASURED-77120345", confidence=0.92,
    )
    object.__setattr__(listing, "property_id", "00000000-0000-0000-0000-000000009001")
    object.__setattr__(listing, "hard_rule_failures", [])
    address = AddressLocation(
        address_id=listing.address_id, raw_address="新北市新莊區興德路 30 號 1F",
        normalized_address="新北市新莊區興德路30號1F",
        latitude=25.062916, longitude=121.452638, geocode_confidence=0.94,
        h3_res_9="8929a1d4d67ffff",
    )
    bundle.listing_repository.save_listing(listing, address, ListingDedupKey(
        source_id=listing.source_id, source_listing_id=listing.source_listing_id,
        normalized_address=address.normalized_address, rent_amount=listing.rent_amount,
        area_ping=listing.area_ping,
    ))
    return create_app(persistence=bundle), listing, address


def test_poc_promotion_retains_review_until_canonical_delivery_is_available() -> None:
    app, measured_listing, measured_address = _synthetic_measured_listing_app()
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

    # 2. Bind this intake to the existing measured fixture via a real decision.
    decide_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/decide",
        json={
            "action": "revise",
            "targetListingId": measured_listing.listing_id,
            "reason": "Synthetic observation confirms the measured listing",
            "riskSummary": "Update supplied facts while preserving recorded geocode evidence",
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
    assert target_listing_id == measured_listing.listing_id
    revised = app.state.listing_repository.get_listing(target_listing_id)
    assert revised.rent_amount == 45000
    persisted_address = app.state.listing_repository.get_address(revised.address_id)
    assert persisted_address.geocode_confidence == measured_address.geocode_confidence
    assert persisted_address.h3_res_9 == measured_address.h3_res_9

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
    assert review_resp.status_code == 503, review_resp.text
    assert "SCORE_DELIVERY_UNAVAILABLE" in review_resp.text
    current = client.get(f"/api/v1/promotion-decisions/{promo_decision_id}", headers=MANAGER_HEADERS)
    assert current.status_code == 200, current.text
    assert current.json()["status"] == "PENDING_REVIEW"
    assert current.json()["version"] == result["version"]
    assert not current.json().get("candidate_site_id")
    assert not current.json().get("site_score_job_id")
    assert not any(c.candidate_site.listing_id == target_listing_id
                   for c in app.state.listing_repository.list_candidates())

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

    # Actual queued promotion, duplicate protection and worker recovery are
    # covered by the PostgreSQL delivery suite and domain queue tests.



def test_promotion_saga_segregation_of_duties() -> None:
    app, measured_listing, _ = _synthetic_measured_listing_app()
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
            "action": "revise",
            "targetListingId": measured_listing.listing_id,
            "reason": "Synthetic observation confirms the measured listing",
            "riskSummary": "Update supplied facts while preserving recorded geocode evidence",
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
    assert intake["matchResult"]["targetListingId"] == measured_listing.listing_id

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
