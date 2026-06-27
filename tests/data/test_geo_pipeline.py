from __future__ import annotations

from datetime import UTC, datetime

from modules.external_data.geo import (
    GeocodeCandidate,
    GeoPipeline,
    StaticGeocodeProvider,
    build_geo_cell,
    normalize_address,
)
from modules.integration.workers.geocode_worker import run_geocode_job


def test_address_normalization_geocode_and_h3_are_reproducible() -> None:
    provider = StaticGeocodeProvider(
        {
            "台北市大安區和平東路二段100號": GeocodeCandidate(
                latitude=25.024,
                longitude=121.543,
                precision="rooftop",
                confidence=0.92,
                provider="fixture",
                admin_city="台北市",
                admin_district="大安區",
            )
        }
    )
    pipeline = GeoPipeline(provider)

    normalized = normalize_address(" 臺北市 大安區 和平東路二段100號 2F ")
    result = pipeline.geocode_record({"address_raw": normalized.raw_address})

    assert normalized.normalized_address == "台北市大安區和平東路二段100號"
    assert result.address.city == "台北市"
    assert result.address.district == "大安區"
    assert result.address.geocode_precision == "rooftop"
    assert result.address.geocode_confidence == 0.92
    assert result.address.h3_res_8.startswith("h3r8_")
    assert result.address.h3_res_9.startswith("h3r9_")
    assert result.address.h3_res_10.startswith("h3r10_")
    assert result.quality_flags == ()

    geo_cell = build_geo_cell(result)
    assert geo_cell.h3_index == result.address.h3_res_9
    assert geo_cell.parent_h3_index == result.address.h3_res_8


def test_geo_pipeline_flags_out_of_market_and_low_confidence() -> None:
    pipeline = GeoPipeline()
    result = pipeline.geocode_record(
        {
            "address_raw": "台北市大安區和平東路二段100號",
            "latitude": 40.7128,
            "longitude": -74.006,
            "confidence": 0.4,
        }
    )

    assert result.address.h3_res_9 == ""
    assert "coordinates_out_of_market" in result.quality_flags
    assert "low_geocode_confidence" in result.quality_flags


def test_external_geo_feature_job_rolls_up_poi_competitor_and_listing_snapshots() -> None:
    provider = StaticGeocodeProvider(
        {
            "台北市大安區和平東路二段100號": GeocodeCandidate(
                25.024,
                121.543,
                "rooftop",
                0.95,
                "fixture",
                "台北市",
                "大安區",
            ),
            "台北市大安區和平東路二段120號": GeocodeCandidate(
                25.0241,
                121.5431,
                "rooftop",
                0.9,
                "fixture",
                "台北市",
                "大安區",
            ),
        }
    )
    snapshot_time = datetime(2026, 6, 27, 9, 0, tzinfo=UTC)

    result = run_geocode_job(
        job_id="geo-job-1",
        provider=provider,
        address_records=[{"address_raw": "台北市大安區和平東路二段100號"}],
        poi_records=[
            {
                "source_poi_id": "poi-1",
                "poi_name": "School",
                "poi_category": "education",
                "address_raw": "台北市大安區和平東路二段100號",
                "confidence": 0.9,
                "snapshot_id": "snap-poi",
            }
        ],
        competitor_records=[
            {
                "source_competitor_id": "comp-1",
                "store_name": "Competitor",
                "address_raw": "台北市大安區和平東路二段120號",
                "estimated_capacity": 8,
                "confidence": 0.8,
                "snapshot_id": "snap-comp",
            }
        ],
        listing_records=[
            {
                "source_listing_id": "list-1",
                "address_raw": "台北市大安區和平東路二段100號",
                "rent_amount": 50000,
                "listing_status": "active",
                "confidence": 0.85,
                "snapshot_id": "snap-list",
            },
            {
                "source_listing_id": "list-2",
                "address_raw": "台北市大安區和平東路二段100號",
                "rent_amount": 40000,
                "listing_status": "leased",
                "snapshot_id": "snap-list",
            },
        ],
        feature_snapshot_time=snapshot_time,
    )

    assert result.status == "succeeded"
    assert len(result.geocoded) == 1
    assert len(result.feature_snapshots) == 1
    feature = result.feature_snapshots[0]
    assert feature.feature_snapshot_time == snapshot_time
    assert feature.poi_count == 1
    assert feature.competitor_count == 1
    assert feature.active_listing_count == 1
    assert feature.median_listing_rent == 50000.0
    assert feature.competitor_capacity == 8.0
    assert feature.source_snapshot_ids == ("snap-comp", "snap-list", "snap-poi")
