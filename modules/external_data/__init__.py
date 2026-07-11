"""External Data Platform: connectors that land and canonicalize external data."""

from __future__ import annotations

from modules.external_data.providers import (
    DemographicsProvider,
    FixtureDemographicsProvider,
    FixtureWeatherProvider,
    LicenseViolationError,
    LiveDemographicsProvider,
    LiveWeatherProvider,
    ProviderMetadata,
    ProviderRegistry,
    WeatherProvider,
    provider_registry,
)
from modules.external_data.application.listing_feed_adapter import (
    LiveListingFeedAdapter,
    ListingFeedClient,
    ListingFeedClientError,
    UnauthorizedError,
    TimeoutError,
)

__all__ = [
    "DemographicsProvider",
    "FixtureDemographicsProvider",
    "FixtureWeatherProvider",
    "LicenseViolationError",
    "LiveDemographicsProvider",
    "LiveWeatherProvider",
    "ProviderMetadata",
    "ProviderRegistry",
    "WeatherProvider",
    "provider_registry",
    "LiveListingFeedAdapter",
    "ListingFeedClient",
    "ListingFeedClientError",
    "UnauthorizedError",
    "TimeoutError",
]
