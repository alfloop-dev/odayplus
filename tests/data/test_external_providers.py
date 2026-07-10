from __future__ import annotations

import pytest
import h3
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

def test_weather_providers() -> None:
    # Test Fixture Mode (using default valid JSON loading)
    fixture_prov = FixtureWeatherProvider()
    res1 = fixture_prov.get_daily_weather("STA-001", "2026-07-10")
    assert res1 is not None
    assert res1["station_id"] == "STA-001"
    assert res1["date"] == "2026-07-10"
    assert res1["temperature_max"] == 33.5
    assert res1["temperature_min"] == 25.0
    assert res1["precipitation"] == 5.2
    assert res1["humidity_avg"] == 78

    res_none = fixture_prov.get_daily_weather("STA-UNKNOWN", "2026-07-10")
    assert res_none is None

    # Test Live Mode
    live_prov = LiveWeatherProvider()
    res_live1 = live_prov.get_daily_weather("STA-999", "2026-07-10")
    assert res_live1 is not None
    assert res_live1["station_id"] == "STA-999"
    assert res_live1["date"] == "2026-07-10"
    assert "temperature_max" in res_live1
    assert "precipitation" in res_live1
    assert res_live1["snapshot_id"].startswith("weather-live-")

def test_demographics_providers() -> None:
    # Test Fixture Mode (using default valid JSON loading)
    fixture_prov = FixtureDemographicsProvider()
    res1 = fixture_prov.get_demographics("89263064c2fffff")
    assert res1 is not None
    assert res1["h3_index"] == "89263064c2fffff"
    assert res1["population_total"] == 4500
    assert res1["household_total"] == 1800
    assert res1["median_income"] == 85000
    assert res1["age_median"] == 39.5

    res_none = fixture_prov.get_demographics("894ba0a4e27ffff")
    assert res_none is None

    # Test Live Mode
    live_prov = LiveDemographicsProvider()
    test_h3 = "894ba0a4e27ffff"
    res_live1 = live_prov.get_demographics(test_h3)
    assert res_live1 is not None
    assert res_live1["h3_index"] == test_h3
    assert res_live1["population_total"] > 0
    assert res_live1["household_total"] > 0
    assert res_live1["snapshot_id"] == f"demographics-live-{test_h3}"

def test_provider_registry_listing_and_retrieval() -> None:
    assert "CONN-WEATHER-FIXTURE" in provider_registry.list_providers()
    assert "CONN-WEATHER-LIVE" in provider_registry.list_providers()
    assert "CONN-DEMOGRAPHICS-FIXTURE" in provider_registry.list_providers()
    assert "CONN-DEMOGRAPHICS-LIVE" in provider_registry.list_providers()

    assert set(provider_registry.list_providers("weather")) == {"CONN-WEATHER-FIXTURE", "CONN-WEATHER-LIVE"}
    assert set(provider_registry.list_providers("demographics")) == {"CONN-DEMOGRAPHICS-FIXTURE", "CONN-DEMOGRAPHICS-LIVE"}

    prov = provider_registry.get_provider("CONN-WEATHER-FIXTURE")
    assert isinstance(prov, FixtureWeatherProvider)

    meta = provider_registry.get_metadata("CONN-WEATHER-LIVE")
    assert meta.license_type == "commercial"
    assert meta.source_category == "weather"

def test_provider_licensing_enforcement() -> None:
    # CONN-WEATHER-FIXTURE allows training
    assert provider_registry.verify_usage("CONN-WEATHER-FIXTURE", "training") is True
    assert provider_registry.verify_usage("CONN-WEATHER-FIXTURE", "prediction") is True

    # CONN-WEATHER-LIVE prohibits training
    with pytest.raises(LicenseViolationError) as exc_info:
        provider_registry.verify_usage("CONN-WEATHER-LIVE", "training")
    assert "prohibited" in str(exc_info.value) or "not allowed" in str(exc_info.value)

    # Custom blocked provider
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
        )
    )
    with pytest.raises(LicenseViolationError) as exc_info:
        custom_registry.verify_usage("BLOCKED-SOURCE", "prediction")
    assert "blocked" in str(exc_info.value)
