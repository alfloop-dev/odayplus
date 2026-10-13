"""Candidate projections preserve persisted evidence without model/geocode defaults."""

from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.listings import create_listings_router
from modules.listing.infrastructure.repositories import InMemoryListingRepository
from modules.opsboard.application.network_listings import NetworkListingService
from modules.sitescore.tests.test_candidate_features import _draft


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
    draft = _draft()
    listing = replace(draft.listing, listing_id="L-2024", address_id=draft.address.address_id,
                      snapshot_id=snapshot_id)
    repository = InMemoryListingRepository(listings=[listing], addresses=[draft.address])
    service = NetworkListingService(repository, seed_fixtures=False,
                                    tenant_id="00000000-0000-4000-8000-000000000001")
    service._save_listing_metadata(listing.listing_id, {"heatZoneId": draft.heat_zone_id})
    service._load_listings()
    response = service.convert_listing(
        listing_id=listing.listing_id, actor_role_id="site_reviewer",
        actor_name="00000000-0000-4000-8000-000000000002",
        idempotency_key="synthetic-convert", correlation_id="synthetic-conversion-evidence",
    )
    card = response["candidate"]
    assert card["status"] == "new"
    assert card["score"] is None and card["recommendation"] is None
    assert card["modelVersion"] is None and card["reviewId"] is None
    assert card["datasetSnapshotId"] == (snapshot_id or None)
    persisted = repository.list_candidates()[0]
    assert persisted.score is None and persisted.model_version is None
    assert persisted.dataset_snapshot_id == (snapshot_id or None)
