from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from modules.external_data.connectors import (
    ExternalProviderConfigError,
    ExternalProviderMode,
    provider_downstream_use_flags,
    provider_export_allowed,
)
from modules.external_data.connectors.provider_registry import (
    LIVE_MODE_ENV_VAR,
    validate_external_providers,
    validate_external_providers_or_raise,
)
from modules.external_data.geo import GeocodeCandidate, GeoPipeline, StaticGeocodeProvider
from modules.external_data.providers import (
    MOCK_PROVIDER_API_KEY,
    ListingPartnerFeedProvider,
    ListingProviderMockService,
)
from modules.external_data.providers.live import (
    LISTING_FEED_ENDPOINT_ENV_VAR,
    ListingProviderRateLimitError,
    ListingProviderTimeoutError,
)
from modules.external_data.workers import (
    ExternalFetchJobSpec,
    ExternalFetchResiliencePolicy,
    ExternalFetchScheduler,
    InMemoryExternalFetchStateStore,
    freshness_evidence_from_run,
    write_external_fetch_lineage_evidence,
)
from modules.external_data.application.listing_feed_adapter import (
    LiveListingFeedAdapter,
    ListingFeedClient,
)
from modules.listing.application.pipeline import ListingPipeline
from modules.listing.infrastructure.repositories import InMemoryListingRepository
from modules.sitescore import SiteScoreFeatureInput, SiteScoreReportService, InMemorySiteScoreRepository
from shared.audit import InMemoryAuditLog
from shared.workflow.sitescore import (
    CandidateSiteRealizationHook,
    DecisionAction,
    DecisionStatus,
    SiteScoreDecisionWorkflow,
)

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "source_data" / "external"


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


def test_scheduled_fetch_is_idempotent_and_uses_source_specific_freshness_sla() -> None:
    scheduled_at = datetime(2026, 6, 28, 10, 0, tzinfo=UTC)
    with ListingProviderMockService() as mock:
        scheduler = _scheduler_for_live_listing(_live_listing_env(mock.listing_feed_url("fresh")))
        spec = ExternalFetchJobSpec(
            provider_id="listing.partner_feed",
            schedule_id="hourly-listing-idempotent",
            freshness_sla=timedelta(hours=1),
        )

        first = scheduler.backfill(
            spec,
            start=datetime(2026, 6, 28, 9, 0, tzinfo=UTC),
            end=scheduled_at,
            correlation_id="corr-ext-004-first",
        )[0]
        replay = scheduler.backfill(
            spec,
            start=datetime(2026, 6, 28, 9, 0, tzinfo=UTC),
            end=scheduled_at,
            correlation_id="corr-ext-004-replay",
        )[0]
        stale_for_stricter_source = scheduler.run_once(
            ExternalFetchJobSpec(
                provider_id="listing.partner_feed",
                schedule_id="listing-tight-sla",
                freshness_sla=timedelta(minutes=15),
            ),
            scheduled_at=scheduled_at,
            correlation_id="corr-ext-006-tight-sla",
        )

    assert replay is first
    assert first.status == "SUCCEEDED"
    assert first.data_status == "FRESH"
    assert first.source_snapshot_ids == ("listing-mock-fresh-20260628",)
    assert first.last_success_watermark_before is None
    assert first.last_success_watermark_after == scheduled_at
    assert stale_for_stricter_source.status == "SUCCEEDED"
    assert stale_for_stricter_source.data_status == "STALE"
    assert stale_for_stricter_source.provider_observed_at == datetime(2026, 6, 28, 9, 30, tzinfo=UTC)
    assert len(mock.requests) == 2


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


def test_rate_limit_opens_circuit_and_preserves_blocked_freshness_state() -> None:
    scheduler = ExternalFetchScheduler(
        state_store=InMemoryExternalFetchStateStore(),
        provider_factories={
            "listing.partner_feed": lambda: ListingPartnerFeedProvider(
                client=_RateLimitListingClient(),
                geo_pipeline=_geo_pipeline(),
            )
        },
        resilience_policy=ExternalFetchResiliencePolicy(
            max_consecutive_failures=1,
            circuit_cooldown=timedelta(minutes=10),
            backoff_base=timedelta(minutes=1),
        ),
    )

    first = scheduler.run_once(
        ExternalFetchJobSpec(provider_id="listing.partner_feed", schedule_id="hourly-listing-rate-limit"),
        scheduled_at=datetime(2026, 6, 29, 8, 0, tzinfo=UTC),
        correlation_id="corr-ext-005-rate-limit",
    )
    blocked = scheduler.run_once(
        ExternalFetchJobSpec(provider_id="listing.partner_feed", schedule_id="hourly-listing-rate-limit"),
        scheduled_at=datetime(2026, 6, 29, 8, 5, tzinfo=UTC),
        correlation_id="corr-ext-005-circuit",
    )

    assert first.status == "FAILED"
    assert first.data_status == "BLOCKED"
    assert first.alerts[0].reason_code == "rate_limited"
    assert first.audit_events[0].event_id == first.alerts[0].event_id
    assert first.provider_observed_at is None
    assert first.last_success_watermark_after is None
    assert blocked.status == "FAILED"
    assert blocked.data_status == "BLOCKED"
    assert blocked.alerts[0].reason_code == "circuit_open"
    assert blocked.retry_after == datetime(2026, 6, 29, 8, 10, tzinfo=UTC)


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
    assert provider_export_allowed("listing.partner_feed") is False
    assert provider_export_allowed("admin_boundary.official_dataset") is True
    assert provider_downstream_use_flags("competitor.manual_source") == ("manual_review",)


def test_provider_registry_live_startup_fails_closed_without_secrets() -> None:
    result = validate_external_providers(
        env={LIVE_MODE_ENV_VAR: "live"},
        correlation_id="corr-ext-001-missing",
    )

    assert result.mode is ExternalProviderMode.LIVE
    assert result.ok is False
    assert {
        error.env_var for error in result.errors if error.code == "missing_credential"
    } == {
        "ODP_LISTING_PROVIDER_API_KEY",
        "ODP_POI_PROVIDER_API_KEY",
        "ODP_GEOCODE_PROVIDER_API_KEY",
        "ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN",
        "ODP_COMPETITOR_MANUAL_SOURCE_ATTESTATION",
    }
    inventory = result.secret_inventory()
    assert inventory["geocode.primary_api"]["auth_modes"] == ["api_key"]
    assert "ODP_GEOCODE_PROVIDER_API_KEY" in inventory["geocode.primary_api"]["env_vars"]
    assert "approved-mock" not in json.dumps(inventory)


def test_provider_registry_expired_credentials_include_correlation_id() -> None:
    env = _all_live_provider_env()
    env["ODP_LISTING_PROVIDER_AUTH_STATUS"] = "expired"

    try:
        validate_external_providers_or_raise(env=env, correlation_id="corr-ext-001-expired")
    except ExternalProviderConfigError as exc:
        error = exc.to_dict()
    else:  # pragma: no cover - assertion branch documents fail-closed contract
        raise AssertionError("expired live provider credential should fail startup")

    assert error["correlation_id"] == "corr-ext-001-expired"
    assert error["errors"][0]["provider_id"] == "listing.partner_feed"
    assert error["errors"][0]["code"] == "credential_expired"


def test_live_listing_adapter_quarantines_duplicates_and_malformed_records() -> None:
    provider = ListingPartnerFeedProvider(
        client=_PayloadListingClient(
            {
                "snapshot_id": "listing-contract-20260629",
                "records": [
                    _valid_listing("LST-CONTRACT-001"),
                    _valid_listing("LST-CONTRACT-001"),
                    {
                        "source_listing_id": "LST-CONTRACT-002",
                        "rent_amount": 30000.0,
                        "listing_status": "active",
                    },
                ],
            }
        ),
        geo_pipeline=_geo_pipeline(),
    )

    result = provider.fetch_and_ingest(
        ingestion_time=datetime(2026, 6, 29, 8, 0, tzinfo=UTC),
        correlation_id="corr-ext-002-contract",
    )

    assert result.raw_snapshot.snapshot_id == "listing-contract-20260629"
    assert result.raw_snapshot.record_count == 3
    assert result.raw_snapshot.idempotency_keys[0].endswith(":listing-contract-20260629:LST-CONTRACT-001")
    assert len(result.canonical_snapshot.canonical_records) == 1
    quarantine_codes = {
        issue.code
        for record in result.canonical_snapshot.quarantine_records
        for issue in record.issues
    }
    assert {"duplicate_idempotency_key", "missing_required_field"} <= quarantine_codes


def test_live_listing_timeout_is_product_visible_blocked_state() -> None:
    scheduler = ExternalFetchScheduler(
        state_store=InMemoryExternalFetchStateStore(),
        provider_factories={
            "listing.partner_feed": lambda: ListingPartnerFeedProvider(
                client=_TimeoutListingClient(),
                geo_pipeline=_geo_pipeline(),
            )
        },
        resilience_policy=ExternalFetchResiliencePolicy(backoff_base=timedelta(minutes=1)),
    )

    run = scheduler.run_once(
        ExternalFetchJobSpec(provider_id="listing.partner_feed", schedule_id="hourly-listing-timeout"),
        scheduled_at=datetime(2026, 6, 29, 8, 0, tzinfo=UTC),
        correlation_id="corr-ext-002-timeout",
    )

    assert run.status == "FAILED"
    assert run.data_status == "BLOCKED"
    assert run.alerts[0].reason_code == "timeout"
    assert run.alerts[0].correlation_id == "corr-ext-002-timeout"


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


def _get_geo_pipeline() -> GeoPipeline:
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
                )
            }
        )
    )


def _valid_listing(source_listing_id: str) -> dict[str, Any]:
    return {
        "source_listing_id": source_listing_id,
        "address_raw": "台北市大安區復興南路二段100號1樓",
        "rent_amount": 45000.0,
        "currency": "TWD",
        "area_ping": 25.5,
        "floor": "1F",
        "available_from": "2026-07-01",
        "listing_status": "active",
        "confidence": 0.86,
    }


class _PayloadListingClient:
    def __init__(self, payload: Mapping[str, Any] | Sequence[Any]) -> None:
        self.payload = payload

    def fetch_listing_feed(self, **_: Any) -> Mapping[str, Any] | Sequence[Any]:
        return self.payload


class _TimeoutListingClient:
    def fetch_listing_feed(self, **kwargs: Any) -> Mapping[str, Any]:
        raise ListingProviderTimeoutError(
            "provider timeout in contract test",
            provider_id="listing.partner_feed",
            correlation_id=str(kwargs["correlation_id"]),
            code="timeout",
        )


class _RateLimitListingClient:
    def fetch_listing_feed(self, **kwargs: Any) -> Mapping[str, Any]:
        raise ListingProviderRateLimitError(
            "provider quota exhausted in contract test",
            provider_id="listing.partner_feed",
            correlation_id=str(kwargs["correlation_id"]),
            code="quota_exhausted",
        )


def test_external_source_product_e2e_flow(tmp_path) -> None:
    """E2E Test exercising the complete flow:

    1. Live listing feed Ingestion
    2. Listing Ingest, Deduplication, Hard Rules filter, and Candidate conversion
    3. SiteScore scoring and closed-loop decision workflow
    4. Freezing inputs and realization updates.
    """
    # Initialize components
    listing_repo = InMemoryListingRepository()
    geo_pipeline = _get_geo_pipeline()
    listing_pipeline = ListingPipeline(repository=listing_repo, geo_pipeline=geo_pipeline)

    client = ListingFeedClient(api_url="mock://api", api_key="valid_token")
    adapter = LiveListingFeedAdapter(
        client=client,
        pipeline=listing_pipeline,
        snapshot_dir=str(tmp_path / "snapshots"),
        quarantine_dir=str(tmp_path / "quarantine"),
    )

    valid_fixture = json.loads((FIXTURES_ROOT / "listing_raw_snapshot.valid.json").read_text(encoding="utf-8"))

    # Step 1 & 2: Ingest live listing feed and process to CandidateSite
    result = adapter.process_feed(replay_payload=valid_fixture)

    assert result["status"] == "success"
    assert result["accepted_count"] == 1  # LST-001 is active and 1F, so converted
    assert result["quarantined_count"] == 1  # LST-002 stale goes to quarantine
    assert len(listing_repo.listings) == 2
    assert len(listing_repo.candidates) == 1

    candidate_draft = listing_repo.candidates[0]
    assert candidate_draft.listing.source_listing_id == "LST-001"
    assert candidate_draft.candidate_site.listing_id == candidate_draft.listing.listing_id

    # Step 3: Integrate data and construct Model Feature Input
    feature_input = SiteScoreFeatureInput(
        candidate_site_id=candidate_draft.candidate_site.candidate_site_id,
        feature_snapshot_time=datetime.now(UTC),
        heat_zone_id=candidate_draft.heat_zone_id,
        heat_zone_score=85.0,
        monthly_rent=candidate_draft.listing.rent_amount,
        area_ping=candidate_draft.listing.area_ping,
        comparable_store_count=3,
        comparable_monthly_revenue_p50=450_000.0,
        buildout_capex=2_000_000.0,
        gross_margin_ratio=0.62,
        average_confidence=candidate_draft.address.geocode_confidence,
        data_quality_score=0.96,
        source_snapshot_ids=(result["snapshot_id"],),
    )

    # Step 4: Run SiteScore Model scoring
    sitescore_repo = InMemorySiteScoreRepository()
    report_service = SiteScoreReportService(repository=sitescore_repo)
    report = report_service.score_candidates([feature_input], scored_at=datetime.now(UTC))[0]

    assert report.candidate_site_id == candidate_draft.candidate_site.candidate_site_id
    assert report.report_version == 1
    assert report.m12.p50 > report.m1.p50

    # Step 5: Decision loop with Realization Hook and Audit trail
    audit_log = InMemoryAuditLog()
    realization_hook = CandidateSiteRealizationHook()
    workflow = SiteScoreDecisionWorkflow(audit_log=audit_log, hooks=[realization_hook])

    decision = workflow.open_decision(report, created_by="agent-antigravity5")
    assert decision.status is DecisionStatus.SYSTEM_RECOMMENDED

    decision = workflow.submit_for_review(decision.decision_id, submitted_by="agent-antigravity5")
    assert decision.status is DecisionStatus.PENDING_REVIEW

    # Approve with explicit business reason
    outcome = workflow.decide(
        decision.decision_id,
        action=DecisionAction.APPROVE,
        actor="ops-director",
        reason="Excellent SiteScore metrics and reasonable rent per ping.",
    )

    assert outcome.decision.status is DecisionStatus.APPROVED
    assert outcome.decision.decision_id == decision.decision_id
    assert outcome.audit_event_id

    # Step 6: Verify frozen inputs & realization status updates
    event = outcome.realization_events[0]
    assert event.model_version == report.model_version
    assert event.policy_version == decision.policy_version
    assert event.input_snapshot_ids == report.source_snapshot_ids

    # Realization hook check: status set to 'approved'
    realized_site = realization_hook.get(candidate_draft.candidate_site.candidate_site_id)
    assert realized_site is not None
    assert realized_site.site_status == "approved"
    assert realized_site.baseline_trajectory == report.baseline_trajectory()

    # Audit log validation
    approve_events = [e for e in audit_log.list_events() if e.action == "approve"]
    assert len(approve_events) == 1
    assert approve_events[0].metadata["reason"] == "Excellent SiteScore metrics and reasonable rent per ping."
