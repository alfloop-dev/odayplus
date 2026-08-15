from __future__ import annotations

import json
import threading
import urllib.error
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from unittest.mock import patch

import pytest

from modules.external_data import (
    FixtureDemographicsProvider,
    FixtureWeatherProvider,
    LicenseViolationError,
    LiveDemographicsProvider,
    LiveWeatherProvider,
    ProviderMetadata,
    ProviderRegistry,
    provider_registry,
)
from modules.external_data.providers.weather_demographics import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderTransportError,
    build_provider_registry,
)


@contextmanager
def _recorded_provider(
    responses: dict[str, tuple[int, dict[str, Any]]],
) -> Iterator[tuple[str, list[dict[str, str]]]]:
    requests: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            requests.append(
                {
                    "path": self.path,
                    "correlation_id": self.headers.get("X-Correlation-Id", ""),
                    "api_key": self.headers.get("X-API-Key", ""),
                }
            )
            status, payload = responses.get(
                self.path,
                (404, {"error": "not found"}),
            )
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *args: object) -> None:
            del args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_fixture_weather_and_demographics_records() -> None:
    weather = FixtureWeatherProvider()
    weather_record = weather.get_daily_weather("STA-001", "2026-07-10")
    assert weather_record == {
        "station_id": "STA-001",
        "date": "2026-07-10",
        "temperature_max": 33.5,
        "temperature_min": 25.0,
        "precipitation": 5.2,
        "humidity_avg": 78,
        "snapshot_id": "weather-2026-07-10",
    }
    assert weather.get_daily_weather("STA-UNKNOWN", "2026-07-10") is None

    demographics = FixtureDemographicsProvider()
    demographic_record = demographics.get_demographics("89263064c2fffff")
    assert demographic_record == {
        "h3_index": "89263064c2fffff",
        "population_total": 4500,
        "household_total": 1800,
        "median_income": 85000,
        "age_median": 39.5,
        "snapshot_id": "demographics-2026-q2",
    }
    assert demographics.get_demographics("894ba0a4e27ffff") is None


def test_live_providers_execute_recorded_http_and_preserve_lineage() -> None:
    weather_payload = {
        "record": {
            "station_id": "STA-999",
            "date": "2026-07-10",
            "temperature_max": 33.1,
            "temperature_min": 26.4,
            "precipitation": 2.5,
            "humidity_avg": 71,
            "snapshot_id": "weather-recorded-20260710",
        }
    }
    demographics_payload = {
        "record": {
            "h3_index": "894ba0a4e27ffff",
            "population_total": 7200,
            "household_total": 2600,
            "median_income": 91000,
            "age_median": 38.2,
            "snapshot_id": "demographics-recorded-2026q2",
        }
    }
    responses = {
        "/stations/STA-999/daily/2026-07-10": (200, weather_payload),
        "/cells/894ba0a4e27ffff": (200, demographics_payload),
    }
    with _recorded_provider(responses) as (endpoint, requests):
        weather = LiveWeatherProvider(
            endpoint_url=endpoint,
            api_key="weather-secret",
            env={"ODP_DEPLOY_ENV": "test"},
            correlation_id_factory=lambda: "corr-weather-recorded",
        )
        demographics = LiveDemographicsProvider(
            endpoint_url=endpoint,
            env={"ODP_DEPLOY_ENV": "test"},
            correlation_id_factory=lambda: "corr-demographics-recorded",
        )

        weather_record = weather.get_daily_weather("STA-999", "2026-07-10")
        demographics_record = demographics.get_demographics("894ba0a4e27ffff")

    assert weather_record == weather_payload["record"]
    assert weather_record is not None
    assert weather_record.lineage.provider_id == "weather.live_api"
    assert weather_record.lineage.correlation_id == "corr-weather-recorded"
    assert weather_record.lineage.snapshot_id == "weather-recorded-20260710"
    assert demographics_record == demographics_payload["record"]
    assert demographics_record is not None
    assert demographics_record.lineage.provider_id == "demographics.live_api"
    assert demographics_record.lineage.correlation_id == "corr-demographics-recorded"
    assert requests == [
        {
            "path": "/stations/STA-999/daily/2026-07-10",
            "correlation_id": "corr-weather-recorded",
            "api_key": "weather-secret",
        },
        {
            "path": "/cells/894ba0a4e27ffff",
            "correlation_id": "corr-demographics-recorded",
            "api_key": "",
        },
    ]


@pytest.mark.parametrize(
    ("provider", "expected_code"),
    [
        (
            LiveWeatherProvider(
                env={"ODP_DEPLOY_ENV": "production"},
                correlation_id_factory=lambda: "corr-weather-missing",
            ),
            "missing_endpoint",
        ),
        (
            LiveDemographicsProvider(
                endpoint_url="https://api.demographics.example.com/v1",
                env={"ODP_DEPLOY_ENV": "production"},
                correlation_id_factory=lambda: "corr-demographics-placeholder",
            ),
            "placeholder_endpoint",
        ),
        (
            LiveWeatherProvider(
                endpoint_url="http://weather.internal/v1",
                env={"ODP_DEPLOY_ENV": "production"},
                correlation_id_factory=lambda: "corr-weather-http",
            ),
            "insecure_endpoint",
        ),
    ],
)
def test_live_provider_configuration_fails_closed(
    provider: LiveWeatherProvider | LiveDemographicsProvider,
    expected_code: str,
) -> None:
    with pytest.raises(ProviderConfigurationError) as exc_info:
        if isinstance(provider, LiveWeatherProvider):
            provider.get_daily_weather("STA-999", "2026-07-10")
        else:
            provider.get_demographics("894ba0a4e27ffff")

    assert exc_info.value.code == expected_code
    assert exc_info.value.provider_id
    assert exc_info.value.correlation_id


def test_live_provider_timeout_is_classified_with_lineage() -> None:
    provider = LiveWeatherProvider(
        endpoint_url="https://weather.partner-data.com/v1",
        env={"ODP_DEPLOY_ENV": "production"},
        correlation_id_factory=lambda: "corr-weather-timeout",
    )
    with patch(
        "modules.external_data.providers.weather_demographics.urllib.request.urlopen",
        side_effect=urllib.error.URLError(TimeoutError("timed out")),
    ):
        with pytest.raises(ProviderTimeoutError) as exc_info:
            provider.get_daily_weather("STA-999", "2026-07-10")

    assert exc_info.value.retryable is True
    assert exc_info.value.code == "timeout"
    assert exc_info.value.provider_id == "weather.live_api"
    assert exc_info.value.correlation_id == "corr-weather-timeout"
    assert exc_info.value.endpoint_origin == "https://weather.partner-data.com"


def test_live_provider_transport_failure_is_classified_with_lineage() -> None:
    provider = LiveDemographicsProvider(
        endpoint_url="https://demographics.partner-data.com/v1",
        env={"ODP_DEPLOY_ENV": "production"},
        correlation_id_factory=lambda: "corr-demographics-transport",
    )
    with patch(
        "modules.external_data.providers.weather_demographics.urllib.request.urlopen",
        side_effect=urllib.error.URLError(ConnectionError("connection refused")),
    ):
        with pytest.raises(ProviderTransportError) as exc_info:
            provider.get_demographics("894ba0a4e27ffff")

    assert exc_info.value.retryable is True
    assert exc_info.value.code == "transport_error"
    assert exc_info.value.provider_id == "demographics.live_api"
    assert exc_info.value.correlation_id == "corr-demographics-transport"
    assert exc_info.value.endpoint_origin == "https://demographics.partner-data.com"


def test_live_provider_schema_failure_does_not_return_synthetic_payload() -> None:
    responses = {
        "/cells/894ba0a4e27ffff": (
            200,
            {
                "record": {
                    "h3_index": "894ba0a4e27ffff",
                    "snapshot_id": "incomplete",
                }
            },
        ),
    }
    with _recorded_provider(responses) as (endpoint, _requests):
        provider = LiveDemographicsProvider(
            endpoint_url=endpoint,
            env={"ODP_DEPLOY_ENV": "test"},
            correlation_id_factory=lambda: "corr-demographics-schema",
        )
        with pytest.raises(ProviderResponseError) as exc_info:
            provider.get_demographics("894ba0a4e27ffff")

    assert exc_info.value.code == "schema_invalid"
    assert exc_info.value.provider_id == "demographics.live_api"
    assert exc_info.value.correlation_id == "corr-demographics-schema"
    assert provider.last_lineage is None


def test_live_provider_auth_failure_is_classified() -> None:
    responses = {
        "/stations/STA-999/daily/2026-07-10": (
            401,
            {"error": "unauthorized"},
        ),
    }
    with _recorded_provider(responses) as (endpoint, _requests):
        provider = LiveWeatherProvider(
            endpoint_url=endpoint,
            env={"ODP_DEPLOY_ENV": "test"},
            correlation_id_factory=lambda: "corr-weather-auth",
        )
        with pytest.raises(ProviderAuthenticationError) as exc_info:
            provider.get_daily_weather("STA-999", "2026-07-10")

    assert exc_info.value.code == "unauthorized"
    assert exc_info.value.status_code == 401


def test_production_registry_excludes_fixtures_and_blocks_unconfigured_live() -> None:
    registry = build_provider_registry({"ODP_DEPLOY_ENV": "production"})

    assert registry.list_providers() == [
        "CONN-WEATHER-LIVE",
        "CONN-DEMOGRAPHICS-LIVE",
    ]
    assert registry.get_metadata("CONN-WEATHER-LIVE").status == "blocked"
    assert registry.get_metadata("CONN-DEMOGRAPHICS-LIVE").status == "blocked"
    with pytest.raises(KeyError):
        registry.get_provider("CONN-WEATHER-FIXTURE")

    with pytest.raises(ProviderConfigurationError) as exc_info:
        registry.register(
            "FORBIDDEN-FIXTURE",
            FixtureWeatherProvider(data_map={}),
            ProviderMetadata(
                source_id="FORBIDDEN-FIXTURE",
                source_name="Forbidden",
                source_category="weather",
                provider="Fixture",
                acquisition_method="file",
                license_type="internal",
            ),
        )
    assert exc_info.value.code == "fixture_forbidden"


def test_local_registry_listing_retrieval_and_licensing() -> None:
    assert set(provider_registry.list_providers("weather")) == {
        "CONN-WEATHER-FIXTURE",
        "CONN-WEATHER-LIVE",
    }
    assert set(provider_registry.list_providers("demographics")) == {
        "CONN-DEMOGRAPHICS-FIXTURE",
        "CONN-DEMOGRAPHICS-LIVE",
    }
    assert isinstance(
        provider_registry.get_provider("CONN-WEATHER-FIXTURE"),
        FixtureWeatherProvider,
    )
    assert provider_registry.get_metadata("CONN-WEATHER-FIXTURE").status == "development"

    assert provider_registry.verify_usage("CONN-WEATHER-FIXTURE", "training") is True
    with pytest.raises(LicenseViolationError):
        provider_registry.verify_usage("CONN-WEATHER-LIVE", "training")

    custom_registry = ProviderRegistry()
    custom_registry.register(
        "BLOCKED-SOURCE",
        None,
        ProviderMetadata(
            source_id="BLOCKED-SOURCE",
            source_name="Blocked Source",
            source_category="weather",
            provider="Unreliable Provider",
            acquisition_method="api",
            license_type="commercial",
            status="blocked",
        ),
    )
    with pytest.raises(LicenseViolationError):
        custom_registry.verify_usage("BLOCKED-SOURCE", "prediction")
