"""Disabled-mode no-contact regression tests for Listing and Geocode adapters.

These tests verify that when ODP_EXTERNAL_PROVIDER_MODE=disabled, the
adapter-level refusal fires *before* any credential is read or any
external client call is made — even when a valid-looking credential is
present in the environment.  This closes the direct-adapter bypass
identified in review of ODP-XR-PROVIDER-OFF-DEPLOYMENT-002 and satisfies
rollout plan §9.1: "disabled provider mode is explicit and fail-closed".
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from modules.external_data.connectors.provider_registry import (
    LIVE_MODE_ENV_VAR,
    ExternalProviderMode,
)
from modules.external_data.geo import GeoPipeline, NormalizedAddress, StaticGeocodeProvider
from modules.external_data.providers import (
    ExternalProviderDisabledError,
    ListingPartnerFeedProvider,
    PrimaryGeocodeProvider,
)


INGESTION_TIME = datetime(2026, 8, 26, 6, 0, tzinfo=UTC)


# ── Helper: spy client that records whether it was ever called ──────────


class SpyListingFeedClient:
    """A listing feed client that tracks whether fetch was called.

    If fetch is called, the test has already failed: disabled mode must
    never reach the client boundary.
    """

    def __init__(self) -> None:
        self.called = False

    def fetch_listing_feed(self, **kwargs: Any) -> Mapping[str, Any]:
        self.called = True
        raise AssertionError("disabled mode must not call the listing feed client")


class SpyGeocodeClient:
    """A geocode client that tracks whether geocode was called."""

    def __init__(self) -> None:
        self.called = False

    def geocode(self, **kwargs: Any) -> Mapping[str, Any]:
        self.called = True
        raise AssertionError("disabled mode must not call the geocode client")


# ── Disabled-mode env helpers ───────────────────────────────────────────


def _disabled_env_with_credential(**overrides: str) -> dict[str, str]:
    """Env that has mode=disabled *and* a valid-looking credential.

    This is exactly the scenario the reviewer flagged: accidental secrets
    should not be read or used when mode is disabled.
    """
    env: dict[str, str] = {
        LIVE_MODE_ENV_VAR: "disabled",
        "ODP_LISTING_PROVIDER_API_KEY": "accidental-secret",
        "ODP_LISTING_PROVIDER_FEED_URL": "https://partner.example.com/feed",
        "ODP_GEOCODE_PROVIDER_API_KEY": "accidental-secret",
        "ODP_GEOCODE_PROVIDER_URL": "https://geocode.example.com/api",
    }
    env.update(overrides)
    return env


def _disabled_env_without_credential(**overrides: str) -> dict[str, str]:
    """Env with mode=disabled and no credentials at all."""
    env: dict[str, str] = {
        LIVE_MODE_ENV_VAR: "disabled",
    }
    env.update(overrides)
    return env


def _disabled_env_via_alias(alias: str, **overrides: str) -> dict[str, str]:
    """Env with a disabled-mode alias (off, none) and a credential."""
    env: dict[str, str] = {
        LIVE_MODE_ENV_VAR: alias,
        "ODP_LISTING_PROVIDER_API_KEY": "accidental-secret",
        "ODP_GEOCODE_PROVIDER_API_KEY": "accidental-secret",
    }
    env.update(overrides)
    return env


# ══════════════════════════════════════════════════════════════════════
# Listing adapter — disabled mode no-contact tests
# ══════════════════════════════════════════════════════════════════════


class TestListingDisabledModeNoContact:
    """ListingPartnerFeedProvider must refuse before reading credentials
    or calling the client when mode=disabled."""

    def test_fetch_and_ingest_raises_disabled_error_with_credential_present(self) -> None:
        """Core regression: disabled+credential must not fetch."""
        env = _disabled_env_with_credential()
        spy = SpyListingFeedClient()
        provider = ListingPartnerFeedProvider(client=spy, env=env)

        assert provider.mode is ExternalProviderMode.DISABLED

        with pytest.raises(ExternalProviderDisabledError) as exc_info:
            provider.fetch_and_ingest(
                ingestion_time=INGESTION_TIME,
                correlation_id="corr-disabled-listing-001",
            )

        assert exc_info.value.provider_id == "listing.partner_feed"
        assert exc_info.value.code == "provider_disabled"
        assert not spy.called

    def test_fetch_and_ingest_raises_disabled_error_without_credential(self) -> None:
        """Even without credentials, disabled mode refuses explicitly."""
        env = _disabled_env_without_credential()
        provider = ListingPartnerFeedProvider(env=env)

        assert provider.mode is ExternalProviderMode.DISABLED

        with pytest.raises(ExternalProviderDisabledError) as exc_info:
            provider.fetch_and_ingest(
                ingestion_time=INGESTION_TIME,
                correlation_id="corr-disabled-listing-002",
            )

        assert exc_info.value.provider_id == "listing.partner_feed"
        assert exc_info.value.code == "provider_disabled"

    @pytest.mark.parametrize("alias", ["off", "none"])
    def test_disabled_aliases_also_refuse(self, alias: str) -> None:
        """The 'off' and 'none' aliases map to DISABLED and must refuse."""
        env = _disabled_env_via_alias(alias)
        spy = SpyListingFeedClient()
        provider = ListingPartnerFeedProvider(client=spy, env=env)

        assert provider.mode is ExternalProviderMode.DISABLED

        with pytest.raises(ExternalProviderDisabledError):
            provider.fetch_and_ingest(
                ingestion_time=INGESTION_TIME,
                correlation_id=f"corr-disabled-listing-alias-{alias}",
            )

        assert not spy.called

    def test_unrecognised_mode_value_treated_as_disabled(self) -> None:
        """An unknown mode value must fail-closed as DISABLED, not raise ValueError."""
        env = {
            LIVE_MODE_ENV_VAR: "typo-mode",
            "ODP_LISTING_PROVIDER_API_KEY": "accidental-secret",
        }
        spy = SpyListingFeedClient()
        provider = ListingPartnerFeedProvider(client=spy, env=env)

        assert provider.mode is ExternalProviderMode.DISABLED

        with pytest.raises(ExternalProviderDisabledError):
            provider.fetch_and_ingest(
                ingestion_time=INGESTION_TIME,
                correlation_id="corr-disabled-listing-unknown",
            )

        assert not spy.called

    def test_default_client_does_not_read_endpoint_env_when_disabled(self) -> None:
        """Disabled mode must not read the endpoint env var to build an HttpListingFeedClient."""
        env = _disabled_env_with_credential()
        provider = ListingPartnerFeedProvider(env=env)

        # The provider should have a fixture replay client, not an HTTP client
        from modules.external_data.providers.live import (
            HttpListingFeedClient,
            ListingFixtureReplayClient,
        )

        assert isinstance(provider.client, ListingFixtureReplayClient)
        assert not isinstance(provider.client, HttpListingFeedClient)


# ══════════════════════════════════════════════════════════════════════
# Geocode adapter — disabled mode no-contact tests
# ══════════════════════════════════════════════════════════════════════


class TestGeocodeDisabledModeNoContact:
    """PrimaryGeocodeProvider must refuse before reading credentials
    or calling the client when mode=disabled."""

    def _normalized_address(self) -> NormalizedAddress:
        from modules.external_data.geo import normalize_address

        return normalize_address("台北市大安區復興南路二段100號")

    def test_lookup_raises_disabled_error_with_credential_present(self) -> None:
        """Core regression: disabled+credential must not geocode."""
        env = _disabled_env_with_credential()
        spy = SpyGeocodeClient()
        provider = PrimaryGeocodeProvider(
            client=spy,
            env=env,
            correlation_id="corr-disabled-geocode-001",
        )

        assert provider.mode is ExternalProviderMode.DISABLED

        with pytest.raises(ExternalProviderDisabledError) as exc_info:
            provider.lookup(self._normalized_address())

        assert exc_info.value.provider_id == "geocode.primary_api"
        assert exc_info.value.code == "provider_disabled"
        assert not spy.called

    def test_lookup_with_payload_raises_disabled_error_with_credential_present(
        self,
    ) -> None:
        """The lookup_with_payload path must also refuse when disabled."""
        env = _disabled_env_with_credential()
        spy = SpyGeocodeClient()
        provider = PrimaryGeocodeProvider(
            client=spy,
            env=env,
            correlation_id="corr-disabled-geocode-002",
        )

        assert provider.mode is ExternalProviderMode.DISABLED

        with pytest.raises(ExternalProviderDisabledError) as exc_info:
            provider.lookup_with_payload(self._normalized_address())

        assert exc_info.value.provider_id == "geocode.primary_api"
        assert exc_info.value.code == "provider_disabled"
        assert not spy.called

    def test_lookup_raises_disabled_error_without_credential(self) -> None:
        """Even without credentials, disabled mode refuses explicitly."""
        env = _disabled_env_without_credential()
        provider = PrimaryGeocodeProvider(
            env=env,
            correlation_id="corr-disabled-geocode-003",
        )

        assert provider.mode is ExternalProviderMode.DISABLED

        with pytest.raises(ExternalProviderDisabledError):
            provider.lookup(self._normalized_address())

    @pytest.mark.parametrize("alias", ["off", "none"])
    def test_disabled_aliases_also_refuse(self, alias: str) -> None:
        """The 'off' and 'none' aliases map to DISABLED and must refuse."""
        env = _disabled_env_via_alias(alias)
        spy = SpyGeocodeClient()
        provider = PrimaryGeocodeProvider(
            client=spy,
            env=env,
            correlation_id=f"corr-disabled-geocode-alias-{alias}",
        )

        assert provider.mode is ExternalProviderMode.DISABLED

        with pytest.raises(ExternalProviderDisabledError):
            provider.lookup(self._normalized_address())

        assert not spy.called

    def test_unrecognised_mode_value_treated_as_disabled(self) -> None:
        """An unknown mode value must fail-closed as DISABLED."""
        env = {
            LIVE_MODE_ENV_VAR: "bogus_mode",
            "ODP_GEOCODE_PROVIDER_API_KEY": "accidental-secret",
        }
        spy = SpyGeocodeClient()
        provider = PrimaryGeocodeProvider(
            client=spy,
            env=env,
            correlation_id="corr-disabled-geocode-unknown",
        )

        assert provider.mode is ExternalProviderMode.DISABLED

        with pytest.raises(ExternalProviderDisabledError):
            provider.lookup(self._normalized_address())

        assert not spy.called

    def test_default_client_does_not_read_endpoint_env_when_disabled(self) -> None:
        """Disabled mode must not read the endpoint env var to build an HttpGeocodeClient."""
        env = _disabled_env_with_credential()
        provider = PrimaryGeocodeProvider(
            env=env,
            correlation_id="corr-disabled-geocode-client",
        )

        from modules.external_data.providers.live import (
            GeocodeFixtureReplayClient,
            HttpGeocodeClient,
        )

        assert isinstance(provider.client, GeocodeFixtureReplayClient)
        assert not isinstance(provider.client, HttpGeocodeClient)

    def test_retry_budget_does_not_suppress_disabled_error(self) -> None:
        """Even with retry_budget > 0, the disabled-mode error must not be retried."""
        env = _disabled_env_with_credential()
        spy = SpyGeocodeClient()
        provider = PrimaryGeocodeProvider(
            client=spy,
            env=env,
            retry_budget=3,
            correlation_id="corr-disabled-geocode-retry",
        )

        with pytest.raises(ExternalProviderDisabledError):
            provider.lookup(self._normalized_address())

        assert not spy.called
