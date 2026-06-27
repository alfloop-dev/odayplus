from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.external_data.geo import GeoFeatureSnapshot
from modules.heatzone.domain import HeatZoneFeatureInput, HeatZoneState, score_heatzones
from modules.heatzone.workers import run_heatzone_batch_score

SNAPSHOT_TIME = datetime(2026, 6, 27, 8, 0, tzinfo=UTC)
PREDICTION_TIME = datetime(2026, 6, 27, 9, 0, tzinfo=UTC)


def test_heatzone_worker_scores_ranks_and_states_geo_features() -> None:
    result = run_heatzone_batch_score(
        job_id="hz-job-1",
        prediction_origin_time=PREDICTION_TIME,
        features=[
            GeoFeatureSnapshot(
                h3_index="h3r9_0100_0100",
                h3_resolution=9,
                feature_snapshot_time=SNAPSHOT_TIME,
                view_version="geo-grid-view-v1",
                poi_count=18,
                competitor_count=1,
                active_listing_count=6,
                median_listing_rent=55_000,
                competitor_capacity=4,
                average_confidence=0.92,
                source_snapshot_ids=("poi-20260627", "listing-20260627"),
            ),
            HeatZoneFeatureInput(
                h3_index="h3r9_0100_0101",
                feature_snapshot_time=SNAPSHOT_TIME,
                poi_count=8,
                competitor_count=6,
                active_listing_count=1,
                median_listing_rent=130_000,
                competitor_capacity=18,
                average_confidence=0.82,
                source_snapshot_ids=("geo-20260627",),
                existing_store_count=3,
            ),
        ],
    )

    assert result.job_id == "hz-job-1"
    assert result.status == "succeeded"
    assert [score.priority_rank for score in result.scores] == [1, 2]
    assert result.scores[0].h3_index == "h3r9_0100_0100"
    assert result.scores[0].state == HeatZoneState.UNTOUCHED
    assert result.scores[0].score > result.scores[1].score
    assert result.scores[0].unmet_demand_score > 0
    assert result.scores[0].format_fit_score > 0
    assert result.scores[0].confidence == 0.92
    assert result.scores[1].state == HeatZoneState.SATURATED
    assert result.to_dict()["map_features"][0]["properties"]["status"] == "UNTOUCHED"


def test_heatzone_state_rules_cover_absorbed_under_realized_and_expandable() -> None:
    scores = score_heatzones(
        [
            {
                "h3_index": "h3r9_absorbed",
                "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
                "poi_count": 9,
                "competitor_count": 2,
                "active_listing_count": 2,
                "median_listing_rent": 65_000,
                "average_confidence": 0.8,
                "source_snapshot_ids": ["geo"],
                "existing_store_count": 1,
            },
            {
                "h3_index": "h3r9_under",
                "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
                "poi_count": 16,
                "active_listing_count": 5,
                "average_confidence": 0.8,
                "source_snapshot_ids": ["geo"],
                "existing_store_count": 1,
                "realized_revenue_ratio": 0.5,
            },
            {
                "h3_index": "h3r9_expand",
                "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
                "poi_count": 20,
                "competitor_count": 0,
                "active_listing_count": 5,
                "average_confidence": 0.8,
                "source_snapshot_ids": ["geo"],
                "existing_store_count": 1,
            },
        ],
        prediction_origin_time=PREDICTION_TIME,
    )

    by_h3 = {score.h3_index: score for score in scores}
    assert by_h3["h3r9_absorbed"].state == HeatZoneState.PARTIALLY_ABSORBED
    assert by_h3["h3r9_under"].state == HeatZoneState.UNDER_REALIZED
    assert by_h3["h3r9_expand"].state == HeatZoneState.STILL_EXPANDABLE


def test_heatzone_api_scores_batch_and_returns_map_results_within_fixture_target() -> None:
    client = TestClient(create_app())
    payload = {
        "prediction_origin_time": PREDICTION_TIME.isoformat(),
        "features": [
            {
                "h3_index": "h3r9_0200_0200",
                "h3_resolution": 9,
                "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
                "view_version": "geo-grid-view-v1",
                "poi_count": 15,
                "competitor_count": 1,
                "active_listing_count": 4,
                "median_listing_rent": 50_000,
                "average_confidence": 0.9,
                "source_snapshot_ids": ["poi", "listing"],
            }
            for _ in range(20)
        ],
    }

    start = perf_counter()
    response = client.post(
        "/heatzones/score-jobs",
        json=payload,
        headers={"x-correlation-id": "corr-hz-1", "Idempotency-Key": "hz-idem-1"},
    )
    elapsed = perf_counter() - start
    listing = client.get("/heatzones")
    map_response = client.get("/heatzones/map")

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"].startswith("heatzone-score-")
    assert body["status"] == "succeeded"
    assert body["created"] is True
    assert body["correlation_id"] == "corr-hz-1"
    assert len(body["scores"]) == 20
    assert elapsed < 0.95

    assert listing.status_code == 200
    assert listing.json()["count"] == 20
    assert map_response.status_code == 200
    map_body = map_response.json()
    assert map_body["type"] == "FeatureCollection"
    assert map_body["count"] == 20
    assert map_body["features"][0]["properties"]["h3_index"] == "h3r9_0200_0200"
    assert map_body["features"][0]["properties"]["status"] == "UNTOUCHED"
