from __future__ import annotations

import urllib.error
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

import pytest

from modules.external_data.connectors import ExternalProviderConfigError, ExternalProviderMode
from modules.external_data.connectors.provider_registry import LIVE_MODE_ENV_VAR
from modules.external_data.geo import GeocodeCandidate, GeoPipeline, StaticGeocodeProvider
from modules.external_data.providers import (
    HttpListingFeedClient,
    ListingPartnerFeedProvider,
    ListingProviderAuthError,
    ListingProviderError,
    ListingProviderRateLimitError,
    ListingProviderTimeoutError,
    record_idempotency_key,
)
from modules.integration.connectors.base import ConnectorRun
from shared.domain import Listing

INGESTION_TIME = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)

VALID_RECORD = {
    "source_listing_id": "LST-LIVE-001",
    "address_raw": "台北市大安區復興南路二段100號1樓",
    "rent_amount": 45000.0,
    "currency": "TWD",
    "area_ping": 25.5,
    "floor": "1F",
    "available_from": "2026-07-01",
    "listing_status": "active",
    "confidence": 0.8,
}


class MockListingFeedClient:
    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def fetch_listing_feed(self, **kwargs: Any) -> Mapping[str, Any]:
        self.calls.append(kwargs)
        return self.payload


class FailingListingFeedClient:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def fetch_listing_feed(self, **kwargs: Any) -> Mapping[str, Any]:
        raise self.exc


def _geo_pipeline() -> GeoPipeline:
    return GeoPipeline(
        StaticGeocodeProvider(
            {
                "台北市大安區復興南路二段100號": GeocodeCandidate(
                    latitude=25.026,
                    longitude=121.543,
                    precision="rooftop",
                    confidence=0.92,
                    provider="fixture",
                    admin_city="台北市",
                    admin_district="大安區",
                ),
                "新北市板橋區中山路一段50號": GeocodeCandidate(
                    latitude=25.012,
                    longitude=121.464,
                    precision="rooftop",
                    confidence=0.9,
                    provider="fixture",
                    admin_city="新北市",
                    admin_district="板橋區",
                ),
            }
        )
    )


def _live_env(**overrides: str) -> dict[str, str]:
    env = {
        LIVE_MODE_ENV_VAR: "live",
        "ODP_LISTING_PROVIDER_API_KEY": "listing-live-secret",
    }
    env.update(overrides)
    return env


def test_fixture_replay_is_default_and_preserves_snapshots_and_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(LIVE_MODE_ENV_VAR, raising=False)
    monkeypatch.delenv("ODP_LISTING_PROVIDER_API_KEY", raising=False)

    provider = ListingPartnerFeedProvider(geo_pipeline=_geo_pipeline())
    result = provider.fetch_and_ingest(
        ingestion_time=INGESTION_TIME,
        correlation_id="corr-fixture-listing",
    )

    assert result.mode is ExternalProviderMode.FIXTURE
    assert result.raw_snapshot.replay_fixture.endswith("listing_raw_snapshot.valid.json")
    assert result.raw_snapshot.snapshot_id == "listing-2026-06-26"
    assert result.raw_snapshot.record_count == 2
    assert result.connector_run.accepted_count == 2
    assert result.connector_run.quarantined_count == 0
    assert isinstance(result.connector_run, ConnectorRun)

    accepted = result.connector_run.accepted[0]
    assert isinstance(accepted.canonical, Listing)
    assert accepted.canonical.snapshot_id == "listing-2026-06-26"
    assert accepted.lineage.source_record_id == "LST-001"
    assert accepted.lineage.source_system == "SRC-EXT-LISTING-PARTNER"
    assert accepted.lineage.ingestion_time == INGESTION_TIME
    assert result.canonical_snapshot.canonical_records[0]["source_listing_id"] == "LST-001"


def test_mocked_live_client_fetches_and_normalizes_listing_feed_without_secret_rendering() -> None:
    payload = {"snapshot_id": "listing-live-snap-1", "records": [VALID_RECORD]}
    client = MockListingFeedClient(payload)

    provider = ListingPartnerFeedProvider(
        client=client,
        env=_live_env(),
        geo_pipeline=_geo_pipeline(),
    )
    result = provider.fetch_and_ingest(
        ingestion_time=INGESTION_TIME,
        correlation_id="corr-live-listing",
    )

    assert result.mode is ExternalProviderMode.LIVE
    assert result.raw_snapshot.snapshot_id == "listing-live-snap-1"
    assert result.raw_snapshot.records[0]["snapshot_id"] == "listing-live-snap-1"
    assert result.raw_snapshot.idempotency_keys == (
        "listing.partner_feed:listing-live-snap-1:LST-LIVE-001",
    )
    assert result.connector_run.accepted_count == 1
    assert client.calls[0]["credential"].env_var == "ODP_LISTING_PROVIDER_API_KEY"
    assert "listing-live-secret" not in repr(client.calls[0]["credential"])
    assert "listing-live-secret" not in repr(result)


def test_missing_live_listing_credential_fails_closed_without_secret_values() -> None:
    provider = ListingPartnerFeedProvider(
        client=MockListingFeedClient({"records": []}),
        env={LIVE_MODE_ENV_VAR: "live"},
        geo_pipeline=_geo_pipeline(),
    )

    with pytest.raises(ExternalProviderConfigError) as exc_info:
        provider.fetch_and_ingest(correlation_id="corr-missing-listing")

    message = str(exc_info.value)
    assert "correlation_id=corr-missing-listing" in message
    assert "ODP_LISTING_PROVIDER_API_KEY" in message
    assert "missing_credential" in exc_info.value.to_dict()["errors"][0]["code"]


def test_invalid_live_listing_credential_status_fails_closed_without_secret_values() -> None:
    provider = ListingPartnerFeedProvider(
        client=MockListingFeedClient({"records": []}),
        env=_live_env(ODP_LISTING_PROVIDER_AUTH_STATUS="unauthorized"),
        geo_pipeline=_geo_pipeline(),
    )

    with pytest.raises(ExternalProviderConfigError) as exc_info:
        provider.fetch_and_ingest(correlation_id="corr-unauthorized-status")

    rendered = repr(exc_info.value.to_dict())
    assert exc_info.value.result.errors[0].code == "credential_unauthorized"
    assert "ODP_LISTING_PROVIDER_AUTH_STATUS" in str(exc_info.value)
    assert "listing-live-secret" not in rendered
    assert "listing-live-secret" not in str(exc_info.value)


def test_duplicate_listing_feed_records_enter_quarantine_by_idempotency_key() -> None:
    payload = {
        "snapshot_id": "listing-live-snap-dup",
        "records": [VALID_RECORD, dict(VALID_RECORD)],
    }
    provider = ListingPartnerFeedProvider(
        client=MockListingFeedClient(payload),
        env=_live_env(),
        geo_pipeline=_geo_pipeline(),
    )

    result = provider.fetch_and_ingest(ingestion_time=INGESTION_TIME)

    assert result.connector_run.accepted_count == 1
    assert result.connector_run.quarantined_count == 1
    rejected = result.connector_run.quarantined[0]
    assert rejected.lineage.quarantine_reasons == ("duplicate_idempotency_key",)
    assert rejected.issues[0].code == "duplicate_idempotency_key"
    assert result.raw_snapshot.idempotency_keys[0] == result.raw_snapshot.idempotency_keys[1]


def test_malformed_listing_payload_enters_connector_quarantine() -> None:
    payload = {
        "snapshot_id": "listing-live-snap-bad",
        "records": [
            {
                "source_listing_id": "LST-BAD-001",
                "rent_amount": -100.0,
                "listing_status": "active",
            }
        ],
    }
    provider = ListingPartnerFeedProvider(
        client=MockListingFeedClient(payload),
        env=_live_env(),
        geo_pipeline=_geo_pipeline(),
    )

    result = provider.fetch_and_ingest(ingestion_time=INGESTION_TIME)

    assert result.connector_run.accepted_count == 0
    assert result.connector_run.quarantined_count == 1
    rejected = result.connector_run.quarantined[0]
    assert {"missing_required_field", "invalid_amount"} <= set(
        rejected.lineage.quarantine_reasons
    )


def test_provider_auth_error_from_live_client_fails_closed_without_secret_values() -> None:
    client = FailingListingFeedClient(
        ListingProviderAuthError(
            "live listing provider authorization failed",
            provider_id="listing.partner_feed",
            correlation_id="corr-client-auth",
            code="unauthorized",
        )
    )
    provider = ListingPartnerFeedProvider(
        client=client,
        env=_live_env(),
        geo_pipeline=_geo_pipeline(),
    )

    with pytest.raises(ListingProviderAuthError) as exc_info:
        provider.fetch_and_ingest(correlation_id="corr-client-auth")

    assert "correlation_id=corr-client-auth" in str(exc_info.value)
    assert "unauthorized" in str(exc_info.value)
    assert "listing-live-secret" not in str(exc_info.value)


def test_provider_timeout_from_live_client_fails_closed_without_secret_values() -> None:
    client = FailingListingFeedClient(
        ListingProviderTimeoutError(
            "live listing provider request timed out",
            provider_id="listing.partner_feed",
            correlation_id="corr-client-timeout",
            code="timeout",
        )
    )
    provider = ListingPartnerFeedProvider(
        client=client,
        env=_live_env(),
        geo_pipeline=_geo_pipeline(),
    )

    with pytest.raises(ListingProviderTimeoutError) as exc_info:
        provider.fetch_and_ingest(correlation_id="corr-client-timeout")

    assert "timeout" in str(exc_info.value)
    assert "listing-live-secret" not in str(exc_info.value)


def test_provider_rate_limit_from_live_client_fails_closed_without_secret_values() -> None:
    client = FailingListingFeedClient(
        ListingProviderRateLimitError(
            "live listing provider rate limit reached",
            provider_id="listing.partner_feed",
            correlation_id="corr-client-rate",
            code="rate_limited",
        )
    )
    provider = ListingPartnerFeedProvider(
        client=client,
        env=_live_env(),
        geo_pipeline=_geo_pipeline(),
    )

    with pytest.raises(ListingProviderRateLimitError) as exc_info:
        provider.fetch_and_ingest(correlation_id="corr-client-rate")

    assert "rate_limited" in str(exc_info.value)
    assert "listing-live-secret" not in str(exc_info.value)


def test_http_listing_client_classifies_5xx_without_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_503(*_args: Any, **_kwargs: Any) -> None:
        raise urllib.error.HTTPError(
            url="https://listing.example.test/feed",
            code=503,
            msg="unavailable",
            hdrs={},
            fp=None,
        )

    monkeypatch.setattr("urllib.request.urlopen", raise_503)
    provider = ListingPartnerFeedProvider(
        client=HttpListingFeedClient("https://listing.example.test/feed"),
        env=_live_env(),
        geo_pipeline=_geo_pipeline(),
    )

    with pytest.raises(ListingProviderError) as exc_info:
        provider.fetch_and_ingest(correlation_id="corr-client-5xx")

    assert exc_info.value.code == "server_error"
    assert "listing-live-secret" not in str(exc_info.value)


def test_record_idempotency_key_and_listing_connector_compatibility() -> None:
    payload = {"snapshot_id": "listing-live-snap-compat", "records": [VALID_RECORD]}
    provider = ListingPartnerFeedProvider(
        client=MockListingFeedClient(payload),
        env=_live_env(),
        geo_pipeline=_geo_pipeline(),
    )

    result = provider.fetch_and_ingest(ingestion_time=INGESTION_TIME)
    accepted = result.connector_run.accepted[0]

    assert record_idempotency_key("listing.partner_feed", result.raw_snapshot.records[0])
    assert accepted.canonical_target == "listing"
    assert isinstance(accepted.canonical, Listing)
    assert accepted.geocode is not None
    assert accepted.lineage.contract_id == "listing_raw_snapshot"
    assert asdict(accepted.canonical)["snapshot_id"] == "listing-live-snap-compat"
