from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from modules.external_data.connectors import ExternalProviderMode
from modules.external_data.connectors.provider_registry import (
    LIVE_MODE_ENV_VAR,
    validate_external_providers,
)
from modules.external_data.geo import GeocodeCandidate, GeoPipeline, StaticGeocodeProvider
from modules.external_data.providers import (
    MOCK_PROVIDER_API_KEY,
    ListingPartnerFeedProvider,
    ListingProviderMockService,
)
from modules.external_data.providers.live import LISTING_FEED_ENDPOINT_ENV_VAR
from modules.external_data.workers import (
    ExternalFetchJobSpec,
    ExternalFetchResiliencePolicy,
    ExternalFetchScheduler,
    InMemoryExternalFetchStateStore,
    freshness_evidence_from_run,
    write_external_fetch_lineage_evidence,
)


def test_live_provider_mode_product_e2e_with_approved_mock_persists_lineage(tmp_path) -> None:
    scheduled_at = datetime(2026, 6, 28, 10, 0, tzinfo=UTC)
    with ListingProviderMockService() as mock:
        env = _live_listing_env(mock.listing_feed_url("fresh"))
        scheduler = _scheduler_for_live_listing(env)

        run = scheduler.run_once(
            ExternalFetchJobSpec(
                provider_id="listing.partner_feed",
                schedule_id="hourly-listing",
                freshness_sla=timedelta(hours=6),
            ),
            scheduled_at=scheduled_at,
            correlation_id="corr-ext-008-live-product",
        )
        evidence = freshness_evidence_from_run(run, freshness_sla=timedelta(hours=6))
        lineage_path = write_external_fetch_lineage_evidence(run, tmp_path / "external-lineage.json")

    assert run.status == "SUCCEEDED"
    assert run.data_status == "FRESH"
    assert run.raw_snapshot_id == "listing-mock-fresh-20260628"
    assert run.canonical_snapshot_id == "listing-mock-fresh-20260628"
    assert run.provider_observed_at == datetime(2026, 6, 28, 9, 30, tzinfo=UTC)
    assert run.ingested_at == scheduled_at
    assert evidence.to_dict() == {
        "provider_id": "listing.partner_feed",
        "source_snapshot_id": "listing-mock-fresh-20260628",
        "data_status": "FRESH",
        "provider_observed_at": "2026-06-28T09:30:00+00:00",
        "ingested_at": "2026-06-28T10:00:00+00:00",
        "freshness_sla_seconds": 21600,
        "correlation_id": "corr-ext-008-live-product",
        "quality_flags": [],
    }
    persisted = json.loads(lineage_path.read_text(encoding="utf-8"))
    assert persisted["idempotency_key"].startswith("listing.partner_feed:hourly-listing")
    assert persisted["source_snapshot_ids"] == ["listing-mock-fresh-20260628"]
    assert persisted["correlation_id"] == "corr-ext-008-live-product"
    assert mock.requests[0].api_key_seen is True
    assert mock.requests[0].correlation_id == "corr-ext-008-live-product"


def test_provider_mock_auth_quota_and_freshness_scenarios_are_product_visible() -> None:
    scheduled_at = datetime(2026, 6, 28, 10, 0, tzinfo=UTC)
    with ListingProviderMockService() as mock:
        unauthorized = _scheduler_for_live_listing(
            _live_listing_env(mock.listing_feed_url("fresh"), api_key="wrong-key")
        ).run_once(
            ExternalFetchJobSpec(provider_id="listing.partner_feed", schedule_id="hourly-listing-auth"),
            scheduled_at=scheduled_at,
            correlation_id="corr-ext-008-auth",
        )
        quota = _scheduler_for_live_listing(
            _live_listing_env(mock.listing_feed_url("quota")),
            policy=ExternalFetchResiliencePolicy(max_consecutive_failures=3, backoff_base=timedelta(minutes=2)),
        ).run_once(
            ExternalFetchJobSpec(provider_id="listing.partner_feed", schedule_id="hourly-listing-quota"),
            scheduled_at=scheduled_at,
            correlation_id="corr-ext-008-quota",
        )
        stale = _scheduler_for_live_listing(_live_listing_env(mock.listing_feed_url("stale"))).run_once(
            ExternalFetchJobSpec(
                provider_id="listing.partner_feed",
                schedule_id="hourly-listing-stale",
                freshness_sla=timedelta(hours=6),
            ),
            scheduled_at=scheduled_at,
            correlation_id="corr-ext-008-stale",
        )

    assert unauthorized.status == "FAILED"
    assert unauthorized.data_status == "BLOCKED"
    assert unauthorized.alerts[0].reason_code == "unauthorized"
    assert unauthorized.audit_events[0].event_type == "external_data.provider_degraded.v1"

    assert quota.status == "FAILED"
    assert quota.data_status == "BLOCKED"
    assert quota.alerts[0].reason_code == "rate_limited"
    assert quota.alerts[0].severity == "P1"
    assert quota.retry_after == scheduled_at + timedelta(minutes=2)

    assert stale.status == "SUCCEEDED"
    assert stale.data_status == "STALE"
    assert freshness_evidence_from_run(stale, freshness_sla=timedelta(hours=6)).quality_flags == ("stale",)


def test_license_gate_and_fixture_default_are_proven_without_live_secrets() -> None:
    fixture_provider = ListingPartnerFeedProvider()
    assert fixture_provider.mode is ExternalProviderMode.FIXTURE

    production_env = _all_live_provider_env()
    production_env["ODP_DEPLOY_ENV"] = "production"
    result = validate_external_providers(env=production_env, correlation_id="corr-ext-008-license")

    assert result.mode is ExternalProviderMode.LIVE
    assert {
        (error.provider_id, error.code)
        for error in result.errors
        if error.code == "license_blocked"
    } == {("competitor.manual_source", "license_blocked")}
    inventory = result.secret_inventory()
    assert inventory["listing.partner_feed"]["license"]["export_allowed"] is False
    assert "internal_decisioning" in inventory["listing.partner_feed"]["license"]["downstream_use_flags"]


def _scheduler_for_live_listing(
    env: dict[str, str],
    *,
    policy: ExternalFetchResiliencePolicy | None = None,
) -> ExternalFetchScheduler:
    return ExternalFetchScheduler(
        state_store=InMemoryExternalFetchStateStore(),
        provider_factories={
            "listing.partner_feed": lambda: ListingPartnerFeedProvider(
                env=env,
                geo_pipeline=_geo_pipeline(),
            )
        },
        resilience_policy=policy,
    )


def _live_listing_env(endpoint_url: str, *, api_key: str = MOCK_PROVIDER_API_KEY) -> dict[str, str]:
    return {
        LIVE_MODE_ENV_VAR: "live",
        LISTING_FEED_ENDPOINT_ENV_VAR: endpoint_url,
        "ODP_LISTING_PROVIDER_API_KEY": api_key,
    }


def _all_live_provider_env() -> dict[str, str]:
    return {
        LIVE_MODE_ENV_VAR: "live",
        "ODP_LISTING_PROVIDER_API_KEY": "listing-approved-mock-key",
        "ODP_POI_PROVIDER_API_KEY": "poi-approved-mock-key",
        "ODP_GEOCODE_PROVIDER_API_KEY": "geocode-approved-mock-key",
        "ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN": "admin-boundary-approved-mock-token",
        "ODP_COMPETITOR_MANUAL_SOURCE_ATTESTATION": "manual-attested",
    }


def _geo_pipeline() -> GeoPipeline:
    return GeoPipeline(
        StaticGeocodeProvider(
            {
                "台北市大安區復興南路二段100號": GeocodeCandidate(
                    latitude=25.026,
                    longitude=121.543,
                    precision="rooftop",
                    confidence=0.92,
                    provider="approved_mock",
                    admin_city="台北市",
                    admin_district="大安區",
                )
            }
        )
    )
