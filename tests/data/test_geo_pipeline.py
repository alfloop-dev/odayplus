from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from modules.external_data.connectors import ExternalProviderConfigError
from modules.external_data.connectors.provider_registry import LIVE_MODE_ENV_VAR
from modules.external_data.geo import (
    GeocodeCandidate,
    GeoPipeline,
    StaticGeocodeProvider,
    build_geo_cell,
    normalize_address,
)
from modules.external_data.providers import (
    GeocodeProviderRateLimitError,
    GeocodeProviderTimeoutError,
    PrimaryGeocodeProvider,
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
    import h3

    assert result.address.district == "大安區"
    assert result.address.geocode_precision == "rooftop"
    assert result.address.geocode_confidence == 0.92
    assert h3.is_valid_cell(result.address.h3_res_8)
    assert h3.is_valid_cell(result.address.h3_res_9)
    assert h3.is_valid_cell(result.address.h3_res_10)
    assert h3.get_resolution(result.address.h3_res_8) == 8
    assert h3.get_resolution(result.address.h3_res_9) == 9
    assert h3.get_resolution(result.address.h3_res_10) == 10
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


def test_geo_pipeline_flags_stale_source_snapshot() -> None:
    pipeline = GeoPipeline()
    result = pipeline.geocode_record(
        {
            "address_raw": "台北市大安區和平東路二段100號",
            "latitude": 25.024,
            "longitude": 121.543,
            "source_snapshot_time": "2026-01-01T00:00:00Z",
        },
        as_of=datetime(2026, 6, 27, tzinfo=UTC),
    )

    import h3

    assert h3.is_valid_cell(result.address.h3_res_9)
    assert h3.get_resolution(result.address.h3_res_9) == 9
    assert "stale_source_snapshot" in result.quality_flags


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


def test_primary_geocoder_replays_recorded_response_with_lineage() -> None:
    provider = PrimaryGeocodeProvider(mode="fixture", correlation_id="corr-ext-003-replay")
    pipeline = GeoPipeline(provider)

    result = pipeline.geocode_record({"address_raw": "台北市信義區信義路五段7號"})

    assert result.address.geocode_confidence == 0.97
    assert result.geocode_provider == "geocode.primary_api"
    assert result.provider_request_id == "geo-replay-req-001"
    assert result.provider_observed_at == datetime(2026, 6, 28, 11, 58, tzinfo=UTC)
    assert result.quality_flags == ()


def test_primary_geocoder_low_confidence_maps_quality_flag_and_lineage() -> None:
    provider = PrimaryGeocodeProvider(
        client=_GeocodePayloadClient(
            {
                "request_id": "geo-low-confidence-001",
                "observed_at": "2026-06-29T07:30:00Z",
                "result": {
                    "latitude": 25.033,
                    "longitude": 121.565,
                    "precision": "street",
                    "confidence": 0.42,
                    "provider_id": "geocode.primary_api",
                    "city": "台北市",
                    "district": "信義區",
                },
            }
        ),
        env=_live_geocode_env(),
        mode="live",
        correlation_id="corr-ext-003-low-confidence",
    )
    pipeline = GeoPipeline(provider)

    result = pipeline.geocode_record({"address_raw": "台北市信義區信義路五段7號"})

    assert result.address.geocode_confidence == 0.42
    assert "low_geocode_confidence" in result.quality_flags
    assert result.provider_request_id == "geo-low-confidence-001"
    assert result.provider_observed_at == datetime(2026, 6, 29, 7, 30, tzinfo=UTC)


def test_primary_geocoder_rate_limit_uses_retry_budget() -> None:
    client = _RateLimitThenSuccessGeocodeClient()
    provider = PrimaryGeocodeProvider(
        client=client,
        env=_live_geocode_env(),
        mode="live",
        retry_budget=1,
        correlation_id="corr-ext-003-rate-limit",
    )

    result = GeoPipeline(provider).geocode_record({"address_raw": "台北市信義區信義路五段7號"})

    assert client.calls == 2
    assert result.provider_request_id == "geo-retry-success-001"
    assert result.address.geocode_confidence == 0.91


def test_primary_geocoder_timeout_and_unauthorized_fail_closed() -> None:
    timeout_provider = PrimaryGeocodeProvider(
        client=_TimeoutGeocodeClient(),
        env=_live_geocode_env(),
        mode="live",
        correlation_id="corr-ext-003-timeout",
    )

    try:
        timeout_provider.lookup(normalize_address("台北市信義區信義路五段7號"))
    except GeocodeProviderTimeoutError as exc:
        assert exc.correlation_id == "corr-ext-003-timeout"
        assert exc.code == "timeout"
    else:  # pragma: no cover - assertion branch documents fail-closed contract
        raise AssertionError("timeout geocoder should fail closed")

    unauthorized_env = _live_geocode_env()
    unauthorized_env["ODP_GEOCODE_PROVIDER_AUTH_STATUS"] = "unauthorized"
    unauthorized_provider = PrimaryGeocodeProvider(
        client=_GeocodePayloadClient({}),
        env=unauthorized_env,
        mode="live",
        correlation_id="corr-ext-003-unauthorized",
    )

    try:
        unauthorized_provider.lookup(normalize_address("台北市信義區信義路五段7號"))
    except ExternalProviderConfigError as exc:
        error = exc.to_dict()
    else:  # pragma: no cover - assertion branch documents fail-closed contract
        raise AssertionError("unauthorized geocoder should fail closed")

    assert error["correlation_id"] == "corr-ext-003-unauthorized"
    assert error["errors"][0]["provider_id"] == "geocode.primary_api"
    assert error["errors"][0]["code"] == "credential_unauthorized"


def _live_geocode_env() -> dict[str, str]:
    return {
        LIVE_MODE_ENV_VAR: "live",
        "ODP_GEOCODE_PROVIDER_API_KEY": "approved-mock-geocode-key",
    }


class _GeocodePayloadClient:
    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = payload

    def geocode(self, **_: Any) -> Mapping[str, Any]:
        return self.payload


class _RateLimitThenSuccessGeocodeClient:
    def __init__(self) -> None:
        self.calls = 0

    def geocode(self, **kwargs: Any) -> Mapping[str, Any]:
        self.calls += 1
        if self.calls == 1:
            raise GeocodeProviderRateLimitError(
                "rate limited in geocode contract test",
                provider_id="geocode.primary_api",
                correlation_id=str(kwargs["correlation_id"]),
                code="rate_limited",
            )
        return {
            "provider_request_id": "geo-retry-success-001",
            "provider_observed_at": "2026-06-29T07:45:00Z",
            "latitude": 25.033964,
            "longitude": 121.564468,
            "geocode_precision": "rooftop",
            "confidence": 0.91,
            "geocode_provider": "geocode.primary_api",
        }


class _TimeoutGeocodeClient:
    def geocode(self, **kwargs: Any) -> Mapping[str, Any]:
        raise GeocodeProviderTimeoutError(
            "timeout in geocode contract test",
            provider_id="geocode.primary_api",
            correlation_id=str(kwargs["correlation_id"]),
            code="timeout",
        )
