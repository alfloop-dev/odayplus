"""Candidate projections preserve persisted evidence without model/geocode defaults."""

from copy import deepcopy
from dataclasses import asdict, replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.listings import V1ListingRepositoryAdapter, create_listings_router
from modules.listing.application.promotion import PromotionService
from modules.listing.domain.intake_states import Actor, PrincipalRole, TransitionContext
from modules.listing.infrastructure.repositories import InMemoryListingRepository
from modules.opsboard.application.network_listings import (
    InMemoryAssistedIntakeRepository,
    NetworkListingPolicyError,
    NetworkListingService,
)
from modules.sitescore.application.candidate_features import feature_from_candidate
from modules.sitescore.tests.test_candidate_features import _draft
from shared.jobs.queue import InMemoryJobQueue, JobRequest


class _ProvenanceIntakeRepository(InMemoryAssistedIntakeRepository):
    def get_listing_intake(self, intake_id):
        return self.get_intake(intake_id)


class _LocalScoreDispatcher:
    """Local queue only: this unit test does not claim durable model admission."""

    def __init__(self):
        self.queue = InMemoryJobQueue()

    def enqueue(self, promotion, *, context):
        job, _ = self.queue.enqueue(
            JobRequest(
                job_type="intake-sitescore",
                payload={"tenant_id": promotion["tenant_id"],
                         "candidate_site_id": promotion["candidate_site_id"]},
                idempotency_key=f"provenance:{promotion['promotion_decision_id']}",
            ),
            correlation_id=context.correlation_id,
        )
        return {"job_id": job.job_id}


def test_new_candidate_domain_has_no_invented_score_or_model_reference():
    draft = _draft()
    assert draft.score is None
    assert draft.recommendation is None
    assert draft.model_version is None
    assert draft.dataset_snapshot_id is None


@pytest.mark.parametrize("coordinates", [(25.04231, 121.50872), (0.0, 0.0), (None, None)])
def test_candidate_api_preserves_recorded_coordinates_and_pending_result(coordinates):
    draft = _draft()
    draft.address = replace(draft.address, latitude=coordinates[0], longitude=coordinates[1])
    draft.score, draft.recommendation, draft.model_version, draft.dataset_snapshot_id = None, None, None, None
    repository = InMemoryListingRepository(candidates=[draft])
    app = FastAPI()
    app.include_router(create_listings_router(repository=repository))
    with TestClient(app) as client:
        response = client.get("/listings/candidates", headers={
            "x-subject-id": "synthetic-location-reader", "x-tenant-id": "tenant-a",
            "x-roles": "expansion_user",
        })
    assert response.status_code == 200, response.text
    card = response.json()["candidates"][0]
    assert (card["latitude"], card["longitude"]) == coordinates
    assert card["h3Index"] == draft.address.h3_res_9
    assert card["geocodeConfidence"] == draft.address.geocode_confidence
    assert card["score"] is None and card["recommendation"] is None
    assert card["modelVersion"] is None and card["datasetSnapshotId"] is None
    assert card["siteStatus"] == "new"


def test_network_projection_cannot_replace_recorded_location_or_result_with_metadata():
    draft = _draft()
    draft.listing = replace(draft.listing, address_id=draft.address.address_id)
    draft.candidate_site = replace(
        draft.candidate_site, listing_id=draft.listing.listing_id, address_id=draft.address.address_id,
    )
    draft.score, draft.recommendation, draft.model_version, draft.dataset_snapshot_id = None, None, None, None
    repository = InMemoryListingRepository(listings=[draft.listing], addresses=[draft.address], candidates=[draft])
    service = NetworkListingService(repository, seed_fixtures=False)
    service._save_candidate_metadata(draft.candidate_site.candidate_site_id, {
        "score": 99, "recommendation": "GO", "modelVersion": "stale-model",
        "datasetSnapshotId": "stale-snapshot", "latitude": 1, "longitude": 2,
    })
    projection = service._candidate_to_dict(draft)
    assert projection["latitude"] == draft.address.latitude
    assert projection["longitude"] == draft.address.longitude
    assert projection["score"] is None and projection["recommendation"] is None
    assert projection["modelVersion"] is None and projection["datasetSnapshotId"] is None
    restored = service._dict_to_candidate(projection)
    assert restored.score is None and restored.recommendation is None
    assert restored.model_version is None and restored.dataset_snapshot_id is None
    assert restored.address.latitude == draft.address.latitude
    assert restored.address.longitude == draft.address.longitude


@pytest.mark.parametrize("snapshot_id", ["synthetic-recorded-source", ""])
def test_production_listing_conversion_preserves_source_without_inventing_results(snapshot_id):
    """Production presentation mode, with real promotion and explicit local delivery.

    STATE_CONTRACTS sections 6–7 require an independent reviewer. The legacy
    writer can only resync that candidate; it no longer creates one. PostgreSQL
    admission/worker behavior is covered by the canonical wiring integration
    tests, not by this source and pending-result projection contract.
    """
    tenant_id = "00000000-0000-4000-8000-000000000001"
    proposer_id = "00000000-0000-4000-8000-000000000002"
    reviewer_id = "00000000-0000-4000-8000-000000000003"
    draft = _draft()
    address = replace(draft.address, raw_address="synthetic recorded address",
                      normalized_address="synthetic recorded address")
    listing = replace(draft.listing, listing_id="L-2024", address_id=draft.address.address_id,
                      snapshot_id=snapshot_id, source_id="synthetic-provider",
                      source_listing_id="synthetic-source-listing")
    # Recorded synthetic gate evidence, not a default for unknown production
    # checks. Neither a candidate nor an approved decision is seeded here.
    object.__setattr__(listing, "hard_rule_failures", [])
    object.__setattr__(listing, "property_id", "00000000-0000-4000-8000-000000000004")
    repository = InMemoryListingRepository(listings=[listing], addresses=[address])
    intakes = _ProvenanceIntakeRepository()
    intakes.save_intake({"id": "synthetic-intake", "tenantId": tenant_id,
                        "status": "READY", "matchResult": {"targetListingId": listing.listing_id}})
    dispatcher = _LocalScoreDispatcher()
    promotion = PromotionService(
        promotion_repository=intakes, listing_repository=V1ListingRepositoryAdapter(repository),
        intake_repository=intakes, score_dispatcher=dispatcher,
    )
    service = NetworkListingService(repository, intake_repository=intakes,
                                    seed_fixtures=False, tenant_id=tenant_id)
    service._save_listing_metadata(listing.listing_id, {"heatZoneId": draft.heat_zone_id})
    with pytest.raises(NetworkListingPolicyError, match="PROMOTION_APPROVAL_REQUIRED"):
        service.convert_listing(
            listing_id=listing.listing_id, actor_role_id="site_reviewer", actor_name=reviewer_id,
            idempotency_key="unapproved-convert", correlation_id="synthetic-conversion-evidence",
        )
    assert repository.list_candidates() == []
    assert intakes.list_promotions() == []

    requested = promotion.request_promotion(
        intake_id="synthetic-intake", target_format_code="synthetic-explicit-format",
        reason="synthetic source provenance check", gate_snapshot_sha256="a" * 64,
        context=TransitionContext(
            actor=Actor(actor_id=proposer_id, role=PrincipalRole.EXPANSION_STAFF, tenant_id=tenant_id),
            idempotency_key="synthetic-request", correlation_id="synthetic-conversion-evidence",
        ),
    )
    assert requested["status"] == "PENDING_REVIEW"
    assert repository.list_candidates() == []
    approved = promotion.review_promotion(
        promotion_decision_id=requested["promotion_decision_id"], decision="APPROVE",
        reason="synthetic independent review", risk_acknowledged=True,
        context=TransitionContext(
            actor=Actor(actor_id=reviewer_id, role=PrincipalRole.EXPANSION_MANAGER, tenant_id=tenant_id),
            idempotency_key="synthetic-review", correlation_id="synthetic-conversion-evidence",
        ),
    )
    assert approved["status"] == "SCORE_QUEUED"
    assert approved["proposer"] == proposer_id
    assert approved["reviewer"] == reviewer_id
    (before,) = deepcopy(repository.list_candidates())
    assert before.candidate_site.candidate_site_id == approved["candidate_site_id"]
    job = dispatcher.queue.get(approved["site_score_job_id"])
    assert job is not None
    assert job.payload == {"tenant_id": tenant_id, "candidate_site_id": approved["candidate_site_id"]}
    response = service.convert_listing(
        listing_id=listing.listing_id, actor_role_id="site_reviewer",
        actor_name=reviewer_id,
        idempotency_key="synthetic-convert", correlation_id="synthetic-conversion-evidence",
    )
    card = response["candidate"]
    assert response["created"] is False and response["candidateCount"] == 1
    assert card["id"] == approved["candidate_site_id"]
    assert card["targetFormatCode"] == "synthetic-explicit-format"
    assert card["status"] == "new"
    assert card["score"] is None and card["recommendation"] is None
    assert card["modelVersion"] is None and card["reviewId"] is None
    assert card["datasetSnapshotId"] == (snapshot_id or None)
    assert (card["latitude"], card["longitude"]) == (address.latitude, address.longitude)
    assert card["geocodeConfidence"] == address.geocode_confidence
    assert card["h3Index"] == address.h3_res_9
    persisted = repository.list_candidates()[0]
    assert asdict(persisted) == asdict(before)
    assert persisted.score is None and persisted.model_version is None
    assert persisted.dataset_snapshot_id == (snapshot_id or None)
    assert persisted.listing.snapshot_id == snapshot_id
    assert persisted.listing.source_id == listing.source_id
    assert persisted.listing.source_listing_id == listing.source_listing_id
    assert persisted.listing.confidence == listing.confidence
    assert persisted.address == address
    feature = feature_from_candidate(persisted, tenant_id=tenant_id)
    assert feature.source_snapshot_ids == ((snapshot_id,) if snapshot_id else ())
    assert feature.average_confidence == address.geocode_confidence
    assert len(intakes.list_promotions()) == 1
    assert intakes.get_promotion(approved["promotion_decision_id"]) == approved
    assert dispatcher.queue.count_active_jobs(tenant_id=tenant_id) == 1
