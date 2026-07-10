from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class ProviderMetadata:
    source_id: str
    source_name: str
    source_category: str  # weather, demographics
    provider: str
    acquisition_method: str  # api, file, manual, feed, public_dataset
    license_type: str  # public, open, commercial, partner, manual, internal
    allowed_usage: tuple[str, ...] = ("display", "feature", "training", "prediction", "report", "audit")
    prohibited_usage: tuple[str, ...] = ()
    retention_policy: str = "90 days"
    refresh_frequency: str = "daily"
    owner: str = "Data Architect"
    integration_owner: str = "Connector Owner"
    dq_profile: str = "standard"
    pii_classification: str = "none"
    cost_model: str = "free"
    status: str = "production"  # candidate, staging, production, deprecated, blocked

class WeatherProvider(Protocol):
    def get_daily_weather(self, station_id: str, date_str: str) -> dict[str, Any] | None:
        """Fetch weather data for a given station and date.
        Returns a dict conforming to weather_daily_snapshot schema or None.
        """
        ...

class DemographicsProvider(Protocol):
    def get_demographics(self, h3_index: str) -> dict[str, Any] | None:
        """Fetch demographic data for a given H3 cell index.
        Returns a dict conforming to demographics_snapshot schema or None.
        """
        ...

class FixtureWeatherProvider:
    def __init__(self, data_map: dict[tuple[str, str], dict[str, Any]] | None = None) -> None:
        self.data_map = {}
        if data_map is not None:
            self.data_map = data_map
        else:
            # Attempt to load from the JSON fixture
            fixture_path = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "source_data" / "external" / "weather_daily_snapshot.valid.json"
            if fixture_path.exists():
                try:
                    with open(fixture_path, encoding="utf-8") as f:
                        content = json.load(f)
                        for record in content.get("records", []):
                            key = (record["station_id"], record["date"])
                            self.data_map[key] = record
                except Exception:
                    pass

    def get_daily_weather(self, station_id: str, date_str: str) -> dict[str, Any] | None:
        return self.data_map.get((station_id, date_str))

class LiveWeatherProvider:
    def __init__(self, api_url: str = "https://api.weather.example.com/v1", api_key: str = "live_key_placeholder") -> None:
        self.api_url = api_url
        self.api_key = api_key

    def get_daily_weather(self, station_id: str, date_str: str) -> dict[str, Any] | None:
        # Live HTTP call is intentionally stubbed for the offline/dev profile.
        # In a wired environment this method would issue an authenticated GET
        # against f"{self.api_url}/stations/{station_id}/daily/{date_str}" and
        # return response.json().
        # Return simulated live data that changes deterministically based on inputs
        import hashlib
        h = int(hashlib.md5(f"{station_id}:{date_str}".encode()).hexdigest(), 16)
        temp_max = 30.0 + (h % 100) / 20.0
        temp_min = 20.0 + (h % 100) / 25.0
        precipitation = (h % 50) / 5.0
        humidity_avg = 50 + (h % 40)
        return {
            "station_id": station_id,
            "date": date_str,
            "temperature_max": round(temp_max, 1),
            "temperature_min": round(temp_min, 1),
            "precipitation": round(precipitation, 1),
            "humidity_avg": int(humidity_avg),
            "snapshot_id": f"weather-live-{station_id}-{date_str}"
        }

class FixtureDemographicsProvider:
    def __init__(self, data_map: dict[str, dict[str, Any]] | None = None) -> None:
        self.data_map = {}
        if data_map is not None:
            self.data_map = data_map
        else:
            # Attempt to load from the JSON fixture
            fixture_path = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "source_data" / "external" / "demographics_snapshot.valid.json"
            if fixture_path.exists():
                try:
                    with open(fixture_path, encoding="utf-8") as f:
                        content = json.load(f)
                        for record in content.get("records", []):
                            self.data_map[record["h3_index"]] = record
                except Exception:
                    pass

    def get_demographics(self, h3_index: str) -> dict[str, Any] | None:
        return self.data_map.get(h3_index)

class LiveDemographicsProvider:
    def __init__(self, api_url: str = "https://api.demographics.example.com/v1") -> None:
        self.api_url = api_url

    def get_demographics(self, h3_index: str) -> dict[str, Any] | None:
        # Simulate querying a live census/demographics API
        import hashlib
        h = int(hashlib.md5(h3_index.encode()).hexdigest(), 16)
        pop = 1000 + (h % 10000)
        households = int(pop / (2.1 + (h % 10) / 10.0))
        income = 50000 + (h % 100000)
        age = 30.0 + (h % 300) / 10.0
        return {
            "h3_index": h3_index,
            "population_total": pop,
            "household_total": households,
            "median_income": income,
            "age_median": round(age, 1),
            "snapshot_id": f"demographics-live-{h3_index}"
        }

class LicenseViolationError(Exception):
    """Raised when a data provider is used in a prohibited or unlicensed way."""
    pass

class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, tuple[Any, ProviderMetadata]] = {}

    def register(self, source_id: str, provider: Any, metadata: ProviderMetadata) -> None:
        self._providers[source_id] = (provider, metadata)

    def get_provider(self, source_id: str) -> Any:
        if source_id not in self._providers:
            raise KeyError(f"Provider {source_id!r} not found in registry")
        return self._providers[source_id][0]

    def get_metadata(self, source_id: str) -> ProviderMetadata:
        if source_id not in self._providers:
            raise KeyError(f"Provider {source_id!r} not found in registry")
        return self._providers[source_id][1]

    def list_providers(self, category: str | None = None) -> list[str]:
        if category is None:
            return list(self._providers.keys())
        return [
            source_id
            for source_id, (_, meta) in self._providers.items()
            if meta.source_category == category
        ]

    def verify_usage(self, source_id: str, usage: str) -> bool:
        """Verifies if the requested usage is permitted for this provider's license."""
        meta = self.get_metadata(source_id)
        if meta.status == "blocked":
            raise LicenseViolationError(f"Provider {source_id} is blocked")
        if usage in meta.prohibited_usage:
            raise LicenseViolationError(f"Usage {usage!r} is explicitly prohibited for {source_id}")
        if usage not in meta.allowed_usage:
            raise LicenseViolationError(f"Usage {usage!r} is not allowed by the license of {source_id}")
        return True

# Global Registry Instance
provider_registry = ProviderRegistry()

# Register Weather Providers
provider_registry.register(
    "CONN-WEATHER-FIXTURE",
    FixtureWeatherProvider(),
    ProviderMetadata(
        source_id="CONN-WEATHER-FIXTURE",
        source_name="Fixture Weather Connector",
        source_category="weather",
        provider="Internal Fixtures",
        acquisition_method="file",
        license_type="internal",
        allowed_usage=("display", "feature", "training", "prediction", "report", "audit"),
    )
)

provider_registry.register(
    "CONN-WEATHER-LIVE",
    LiveWeatherProvider(),
    ProviderMetadata(
        source_id="CONN-WEATHER-LIVE",
        source_name="Live Weather API Connector",
        source_category="weather",
        provider="External Weather Service",
        acquisition_method="api",
        license_type="commercial",
        allowed_usage=("display", "feature", "prediction", "report"),
        prohibited_usage=("training",),
    )
)

# Register Demographics Providers
provider_registry.register(
    "CONN-DEMOGRAPHICS-FIXTURE",
    FixtureDemographicsProvider(),
    ProviderMetadata(
        source_id="CONN-DEMOGRAPHICS-FIXTURE",
        source_name="Fixture Demographics Connector",
        source_category="demographics",
        provider="Internal Fixtures",
        acquisition_method="file",
        license_type="internal",
        allowed_usage=("display", "feature", "training", "prediction", "report", "audit"),
    )
)

provider_registry.register(
    "CONN-DEMOGRAPHICS-LIVE",
    LiveDemographicsProvider(),
    ProviderMetadata(
        source_id="CONN-DEMOGRAPHICS-LIVE",
        source_name="Live Demographics API Connector",
        source_category="demographics",
        provider="External Census Service",
        acquisition_method="api",
        license_type="open",
        allowed_usage=("display", "feature", "training", "prediction", "report", "audit"),
    )
)
