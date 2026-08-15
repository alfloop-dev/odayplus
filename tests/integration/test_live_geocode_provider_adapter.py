from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from modules.external_data.connectors import ExternalProviderConfigError, ExternalProviderMode
from modules.external_data.connectors.provider_registry import LIVE_MODE_ENV_VAR
from modules.external_data.geo import GeoPipeline
from modules.external_data.providers import (
    GeocodeProviderAuthError,
    GeocodeProviderRateLimitError,
    GeocodeProviderTimeoutError,
    PrimaryGeocodeProvider,
)

INGESTION_TIME = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)


class MockGeocodeClient:
    def __init__(self, *responses: Mapping[str, Any] | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def geocode(self, **kwargs: Any) -> Mapping[str, Any]:
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _live_env(**overrides: str) -> dict[str, str]:
    env = {
        LIVE_MODE_ENV_VAR: "live",
        "ODP_GEOCODE_PROVIDER_API_KEY": "geocode-live-secret",
    }
    env.update(overrides)
    return env


def _success_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request_id": "geo-live-req-001",
        "observed_at": "2026-06-28T11:59:00Z",
        "result": {
            "latitude": 25.026,
            "longitude": 121.543,
            "precision": "address",
            "confidence": 0.94,
            "city": "台北市",
            "district": "大安區",
            "provider_id": "geocode.primary_api",
        },
    }
    payload.update(overrides)
    return payload


def test_fixture_replay_is_default_and_preserves_geocode_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(LIVE_MODE_ENV_VAR, raising=False)
    monkeypatch.delenv("ODP_GEOCODE_PROVIDER_API_KEY", raising=False)

    provider = PrimaryGeocodeProvider(correlation_id="corr-geocode-fixture")
    result = GeoPipeline(provider).geocode_record(
        {"address_raw": " 臺北市 信義區 信義路五段7號 3F "},
        as_of=INGESTION_TIME,
    )

    assert provider.mode is ExternalProviderMode.FIXTURE
    assert result.address.normalized_address == "台北市信義區信義路五段7號"
    assert result.geocode_provider == "geocode.primary_api"
    assert result.address.geocode_precision == "rooftop"
    assert result.address.geocode_confidence == 0.97
    assert result.admin_match_flag is True
    assert result.quality_flags == ()
    assert result.h3_resolution_map[9] == result.address.h3_res_9
    assert result.provider_request_id == "geo-replay-req-001"
    assert result.provider_observed_at == datetime(2026, 6, 28, 11, 58, tzinfo=UTC)


def test_mocked_live_geocoder_success_uses_registry_metadata_and_redacts_secret() -> None:
    client = MockGeocodeClient(_success_payload())
    provider = PrimaryGeocodeProvider(
        client=client,
        env=_live_env(),
        retry_budget=2,
        correlation_id="corr-geocode-live",
    )

    result = GeoPipeline(provider).geocode_record(
        {"address_raw": "台北市大安區復興南路二段100號"},
        as_of=INGESTION_TIME,
    )

    assert result.geocode_provider == "geocode.primary_api"
    assert result.address.geocode_precision == "rooftop"
    assert result.address.geocode_confidence == 0.94
    assert result.provider_request_id == "geo-live-req-001"
    assert result.provider_observed_at == datetime(2026, 6, 28, 11, 59, tzinfo=UTC)
    assert client.calls[0]["provider"].source_contract_id == "geocode_result_snapshot"
    assert client.calls[0]["credential"].env_var == "ODP_GEOCODE_PROVIDER_API_KEY"
    assert client.calls[0]["retry_budget"] == 2
    assert "geocode-live-secret" not in repr(client.calls[0]["credential"])
    assert "geocode-live-secret" not in repr(result)


def test_missing_live_geocode_credential_fails_closed_without_secret_values() -> None:
    provider = PrimaryGeocodeProvider(
        client=MockGeocodeClient(_success_payload()),
        env={LIVE_MODE_ENV_VAR: "live"},
        correlation_id="corr-geocode-missing",
    )

    with pytest.raises(ExternalProviderConfigError) as exc_info:
        GeoPipeline(provider).geocode_record({"address_raw": "台北市大安區復興南路二段100號"})

    message = str(exc_info.value)
    assert "correlation_id=corr-geocode-missing" in message
    assert "ODP_GEOCODE_PROVIDER_API_KEY" in message
    assert exc_info.value.result.errors[0].code == "missing_credential"


def test_expired_live_geocode_credential_status_fails_closed_without_secret_values() -> None:
    provider = PrimaryGeocodeProvider(
        client=MockGeocodeClient(_success_payload()),
        env=_live_env(ODP_GEOCODE_PROVIDER_AUTH_STATUS="expired"),
        correlation_id="corr-geocode-expired",
    )

    with pytest.raises(ExternalProviderConfigError) as exc_info:
        GeoPipeline(provider).geocode_record({"address_raw": "台北市大安區復興南路二段100號"})

    rendered = repr(exc_info.value.to_dict())
    assert exc_info.value.result.errors[0].code == "credential_expired"
    assert "ODP_GEOCODE_PROVIDER_AUTH_STATUS" in str(exc_info.value)
    assert "geocode-live-secret" not in rendered
    assert "geocode-live-secret" not in str(exc_info.value)


def test_provider_auth_error_from_live_geocoder_fails_closed_without_secret_values() -> None:
    client = MockGeocodeClient(
        GeocodeProviderAuthError(
            "live geocode provider authorization failed",
            provider_id="geocode.primary_api",
            correlation_id="corr-geocode-auth",
            code="unauthorized",
        )
    )
    provider = PrimaryGeocodeProvider(
        client=client,
        env=_live_env(),
        correlation_id="corr-geocode-auth",
    )

    with pytest.raises(GeocodeProviderAuthError) as exc_info:
        GeoPipeline(provider).geocode_record({"address_raw": "台北市大安區復興南路二段100號"})

    assert "correlation_id=corr-geocode-auth" in str(exc_info.value)
    assert "unauthorized" in str(exc_info.value)
    assert "geocode-live-secret" not in str(exc_info.value)


def test_provider_timeout_from_live_geocoder_fails_closed_without_secret_values() -> None:
    client = MockGeocodeClient(
        GeocodeProviderTimeoutError(
            "live geocode provider request timed out",
            provider_id="geocode.primary_api",
            correlation_id="corr-geocode-timeout",
            code="timeout",
        )
    )
    provider = PrimaryGeocodeProvider(
        client=client,
        env=_live_env(),
        correlation_id="corr-geocode-timeout",
    )

    with pytest.raises(GeocodeProviderTimeoutError) as exc_info:
        GeoPipeline(provider).geocode_record({"address_raw": "台北市大安區復興南路二段100號"})

    assert "timeout" in str(exc_info.value)
    assert "geocode-live-secret" not in str(exc_info.value)


def test_rate_limit_retry_budget_retries_then_preserves_success_lineage() -> None:
    client = MockGeocodeClient(
        GeocodeProviderRateLimitError(
            "live geocode provider rate limit reached",
            provider_id="geocode.primary_api",
            correlation_id="corr-geocode-rate",
            code="rate_limited",
        ),
        _success_payload(request_id="geo-live-req-retry"),
    )
    provider = PrimaryGeocodeProvider(
        client=client,
        env=_live_env(),
        retry_budget=1,
        correlation_id="corr-geocode-rate",
    )

    result = GeoPipeline(provider).geocode_record(
        {"address_raw": "台北市大安區復興南路二段100號"},
        as_of=INGESTION_TIME,
    )

    import h3

    assert len(client.calls) == 2
    assert result.provider_request_id == "geo-live-req-retry"
    assert result.address.h3_res_9 and h3.is_valid_cell(result.address.h3_res_9)


def test_malformed_live_geocoder_response_sets_quality_flags_without_fabricating_h3() -> None:
    client = MockGeocodeClient(
        {
            "request_id": "geo-live-req-bad",
            "result": {
                "latitude": "not-a-number",
                "longitude": 121.543,
                "confidence": 0.9,
            },
        }
    )
    provider = PrimaryGeocodeProvider(
        client=client,
        env=_live_env(),
        correlation_id="corr-geocode-bad",
    )

    result = GeoPipeline(provider).geocode_record(
        {"address_raw": "台北市大安區復興南路二段100號"},
        as_of=INGESTION_TIME,
    )

    assert result.geocode_provider == "geocode.primary_api"
    assert result.provider_request_id == "geo-live-req-bad"
    assert result.address.h3_res_9 == ""
    assert "malformed_provider_response" in result.quality_flags
    assert "coordinates_out_of_market" in result.quality_flags
    assert "low_geocode_confidence" in result.quality_flags


def test_out_of_market_live_geocoder_response_preserves_provider_identity_and_flags() -> None:
    client = MockGeocodeClient(
        _success_payload(
            result={
                "latitude": 40.7128,
                "longitude": -74.006,
                "precision": "rooftop",
                "confidence": 0.91,
                "city": "台北市",
                "district": "大安區",
                "provider_id": "geocode.primary_api",
            }
        )
    )
    provider = PrimaryGeocodeProvider(
        client=client,
        env=_live_env(),
        correlation_id="corr-geocode-oob",
    )

    result = GeoPipeline(provider).geocode_record(
        {"address_raw": "台北市大安區復興南路二段100號"},
        as_of=INGESTION_TIME,
    )

    assert result.geocode_provider == "geocode.primary_api"
    assert result.address.geocode_confidence == 0.91
    assert result.h3_resolution_map == {}
    assert "coordinates_out_of_market" in result.quality_flags


def test_low_confidence_live_geocoder_response_preserves_admin_match_and_precision() -> None:
    client = MockGeocodeClient(
        _success_payload(
            result={
                "latitude": 25.026,
                "longitude": 121.543,
                "precision": "street",
                "confidence": 0.42,
                "city": "台北市",
                "district": "大安區",
                "provider_id": "geocode.primary_api",
            }
        )
    )
    provider = PrimaryGeocodeProvider(
        client=client,
        env=_live_env(),
        correlation_id="corr-geocode-low",
    )

    result = GeoPipeline(provider).geocode_record(
        {"address_raw": "台北市大安區復興南路二段100號"},
        as_of=INGESTION_TIME,
    )

    import h3

    assert result.admin_match_flag is True
    assert result.address.geocode_precision == "street"
    assert result.address.geocode_confidence == 0.42
    assert result.address.h3_res_9 and h3.is_valid_cell(result.address.h3_res_9)
    assert "low_geocode_confidence" in result.quality_flags
