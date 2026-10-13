from __future__ import annotations

from contextlib import nullcontext
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.app.routes.listings import V1ListingRepositoryAdapter
from apps.api.oday_api.main import create_app
from modules.listing.application.promotion import PromotionScoreQueueError, PromotionService
from modules.listing.domain.intake_states import (
    Actor,
    PrincipalRole,
    TransitionContext,
)
from modules.listing.domain.models import ListingDedupKey
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


@pytest.mark.parametrize(
    ("score_failure", "target_format"),
    [(False, "FMT-MICRO-STORE"), (True, "FMT-STANDARD-STORE")],
)
def test_durable_promotion_preserves_requested_format_and_candidate(
    tmp_path, score_failure, target_format,
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

    def fail_score_queue() -> None:
        raise RuntimeError("ODP_TEST_SCORE_FAILURE")

    service = PromotionService(
        promotion_repository=promotions,
        listing_repository=adapter,
        intake_repository=_IntakeRepository(intake),
        score_queue_hook=fail_score_queue if score_failure else None,
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

        with pytest.raises(PromotionScoreQueueError, match="ODP_TEST_SCORE_FAILURE") if score_failure else nullcontext():
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
        assert failed["status"] == ("SCORE_FAILED" if score_failure else "COMPLETED")
        assert failed["target_format_code"] == target_format
        assert len(candidates) == 1
        assert candidates[0].candidate_site.target_format_code == target_format
        assert candidates[0].candidate_site.site_status == ("SCORING_FAILED" if score_failure else "ready")
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


def test_promotion_saga_golden_flow() -> None:
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
    dup_url = "https://www.synthetic.example/detail-99999999.html"  # distinct intake, same target listing
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
