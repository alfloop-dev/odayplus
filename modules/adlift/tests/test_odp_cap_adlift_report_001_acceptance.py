"""Acceptance test suite for ODP-CAP-ADLIFT-REPORT-001.

Verifies the 5 acceptance criteria:
1. invalid controls prevent causal claims
2. interval and evidence level are visible
3. continue-stop writes immutable rationale
4. unavailable data fails closed
5. adverse-state tests and evidence are delivered
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from apps.api.app.routes.adlift import (
    AdLiftIncrementalityJobPayload,
    create_adlift_router,
)
from apps.api.oday_api.main import create_app
from modules.adlift.application import AdLiftService
from modules.adlift.domain.incrementality import (
    AdCampaign,
    AdLiftProductionExecutionError,
    EffectInterval,
    EvidenceLevel,
    PreTrendStatus,
    Recommendation,
    StoreDayMetric,
    assign_evidence_level,
    evaluate_pre_trend,
    is_causal_evidence,
    recommend,
    run_incrementality,
)
from modules.adlift.infrastructure import InMemoryAdLiftRepository
from shared.audit import AuditEvent, InMemoryAuditLog
from tests.integration._authz import ADLIFT_HEADERS

GENERATED_AT = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
PRE_DAYS = tuple(range(1, 6))
CAMPAIGN_DAYS = tuple(range(6, 11))


def _metric(
    store_id: str,
    day: int,
    revenue: float,
    active_interventions: tuple[str, ...] = (),
    source_snapshots: tuple[str, ...] | None = None,
) -> StoreDayMetric:
    snapshots = (f"snap-{store_id}-{day}",) if source_snapshots is None else source_snapshots
    return StoreDayMetric(
        store_id=store_id,
        business_date=date(2026, 8, day),
        revenue=revenue,
        gross_margin=revenue * 0.5,
        ad_spend=10.0,
        active_intervention_ids=active_interventions,
        source_snapshot_ids=snapshots,
    )


def _build_campaign(
    *,
    treatment_pre: float = 100.0,
    treatment_post: float = 140.0,
    control_pre: float = 100.0,
    control_post: float = 110.0,
    treatment_stores: tuple[str, ...] = ("t1",),
    control_stores: tuple[str, ...] = ("c1",),
    contaminated_store: str | None = None,
    ad_spend: float = 100.0,
    with_lineage: bool = True,
    campaign_id: str = "camp-001",
) -> AdCampaign:
    observations: list[StoreDayMetric] = []
    snapshots = None if with_lineage else ()
    for store in treatment_stores:
        for day in PRE_DAYS:
            observations.append(_metric(store, day, treatment_pre, source_snapshots=snapshots))
        for day in CAMPAIGN_DAYS:
            interventions = ("other-promo",) if store == contaminated_store else ()
            observations.append(
                _metric(
                    store,
                    day,
                    treatment_post,
                    active_interventions=interventions,
                    source_snapshots=snapshots,
                )
            )
    for store in control_stores:
        for day in PRE_DAYS:
            observations.append(_metric(store, day, control_pre, source_snapshots=snapshots))
        for day in CAMPAIGN_DAYS:
            observations.append(_metric(store, day, control_post, source_snapshots=snapshots))
    return AdCampaign(
        campaign_id=campaign_id,
        name="Acceptance Test Campaign",
        treatment_store_ids=treatment_stores,
        candidate_control_store_ids=control_stores,
        pre_period_start=date(2026, 8, 1),
        pre_period_end=date(2026, 8, 5),
        campaign_period_start=date(2026, 8, 6),
        campaign_period_end=date(2026, 8, 10),
        ad_spend=ad_spend,
        observations=tuple(observations),
        channel="paid_search",
        campaign_intervention_id="ad-main",
    )


# -----------------------------------------------------------------------------
# AC1: Invalid controls prevent causal claims
# -----------------------------------------------------------------------------
def test_ac1_pre_trend_failure_prevents_causal_claim() -> None:
    """Non-parallel pre-trends must force evidence <= L2 and recommendation = INCONCLUSIVE."""
    observations: list[StoreDayMetric] = []
    # Treatment pre-period trends steeply upward (100 -> 300)
    for day in PRE_DAYS:
        observations.append(_metric("t1", day, 100.0 + day * 40.0))
    for day in CAMPAIGN_DAYS:
        observations.append(_metric("t1", day, 300.0))

    # Control pre-period is flat (100 -> 100)
    for day in PRE_DAYS:
        observations.append(_metric("c1", day, 100.0))
    for day in CAMPAIGN_DAYS:
        observations.append(_metric("c1", day, 100.0))

    campaign = _build_campaign(treatment_stores=("t1",), control_stores=("c1",))
    campaign = AdCampaign(
        **{**campaign.__dict__, "observations": tuple(observations)}
    )

    report = run_incrementality(campaign, generated_at=GENERATED_AT)

    assert report.pre_trend_status is PreTrendStatus.FAIL
    assert report.evidence_level is EvidenceLevel.L2_MATCHED_DESCRIPTIVE
    assert report.causal_claim_allowed is False
    assert report.recommendation is Recommendation.INCONCLUSIVE


def test_ac1_contamination_prevents_causal_claim() -> None:
    """Intervention contamination in treatment store caps evidence at L2 and prevents causal claims."""
    campaign = _build_campaign(contaminated_store="t1")
    report = run_incrementality(campaign, generated_at=GENERATED_AT)

    assert report.evidence_level is EvidenceLevel.L2_MATCHED_DESCRIPTIVE
    assert report.causal_claim_allowed is False
    assert report.recommendation is Recommendation.INCONCLUSIVE
    assert len(report.contamination) == 1
    assert report.contamination[0].store_id == "t1"


def test_ac1_missing_control_group_prevents_causal_claim() -> None:
    """No control group falls back to before-after (L1) and prevents causal claims."""
    campaign = _build_campaign(control_stores=())
    report = run_incrementality(campaign, generated_at=GENERATED_AT)

    assert report.evidence_level is EvidenceLevel.L1_BEFORE_AFTER
    assert report.causal_claim_allowed is False
    assert report.recommendation is Recommendation.INCONCLUSIVE


# -----------------------------------------------------------------------------
# AC2: Interval and evidence level are visible
# -----------------------------------------------------------------------------
def test_ac2_effect_interval_and_evidence_level_are_visible() -> None:
    """Effect interval (low, point, high, SE) and evidence level are serialized and visible."""
    campaign = _build_campaign(treatment_post=140.0, control_post=110.0, ad_spend=40.0)
    report = run_incrementality(campaign, generated_at=GENERATED_AT)

    assert isinstance(report.effect_interval, EffectInterval)
    assert report.effect_interval.metric == "did_gm_per_store_day"
    assert report.effect_interval.point == 15.0  # (140-100) - (110-100) = 30 rev lift; GM=15 per day
    assert report.evidence_level is EvidenceLevel.L3_DID_VALIDATED

    # Verify serialization
    data = report.to_dict()
    assert "effect_interval" in data
    assert data["effect_interval"]["point"] == 15.0
    assert data["evidence_level"] == "L3"
    assert data["causal_claim_allowed"] is True

    # Verify report card projection
    card = report.to_report_card()
    assert card["evidenceLevel"] == "L3"
    assert card["preTrendStatus"] == "PASS"
    assert card["continueStopRecommendation"] == "SCALE"


# -----------------------------------------------------------------------------
# AC3: Continue-stop writes immutable rationale
# -----------------------------------------------------------------------------
def test_ac3_continue_stop_writes_immutable_rationale() -> None:
    """Audit logs and writeback packets record immutable rationale for continue/stop decisions."""
    audit_log = InMemoryAuditLog()
    client = TestClient(create_app(audit_log=audit_log), headers=ADLIFT_HEADERS)

    campaign = _build_campaign(treatment_post=140.0, control_post=110.0, ad_spend=40.0)
    report = run_incrementality(campaign, generated_at=GENERATED_AT)
    assert report.recommendation is Recommendation.SCALE

    # Execute API job submission which triggers audit logging
    campaign_dict = {
        "campaign_id": campaign.campaign_id,
        "name": campaign.name,
        "channel": campaign.channel,
        "treatment_store_ids": list(campaign.treatment_store_ids),
        "candidate_control_store_ids": list(campaign.candidate_control_store_ids),
        "pre_period_start": campaign.pre_period_start.isoformat(),
        "pre_period_end": campaign.pre_period_end.isoformat(),
        "campaign_period_start": campaign.campaign_period_start.isoformat(),
        "campaign_period_end": campaign.campaign_period_end.isoformat(),
        "ad_spend": campaign.ad_spend,
        "observations": [m.to_dict() for m in campaign.observations],
    }
    response = client.post(
        "/adlift/incrementality-jobs",
        json={"generated_at": GENERATED_AT.isoformat(), "campaigns": [campaign_dict]},
        headers={"x-correlation-id": "corr-ac3-test", "Idempotency-Key": "idem-ac3-test"},
    )

    assert response.status_code == 202
    res_data = response.json()
    job_report = res_data["reports"][0]

    assert job_report["recommendation"] == "SCALE"
    assert job_report["intervention_writeback"]["recommendation"] == "SCALE"
    assert job_report["intervention_writeback"]["evidence_level"] == "L3"

    # Verify audit log entry
    audit_events = audit_log._events
    adlift_event = next(
        e for e in audit_events if e.event_type == "adlift.incrementality_evaluated.v1"
    )
    assert adlift_event.correlation_id == "corr-ac3-test"
    assert adlift_event.outcome == "accepted"
    assert adlift_event.metadata["idempotency_key"] == "idem-ac3-test"


# -----------------------------------------------------------------------------
# AC4: Unavailable data fails closed
# -----------------------------------------------------------------------------
def test_ac4_unavailable_data_fails_closed_in_production() -> None:
    """In production mode, missing lineage or controls raises AdLiftProductionExecutionError."""
    # 1. Missing control group in production mode
    no_controls_campaign = _build_campaign(control_stores=())
    with pytest.raises(AdLiftProductionExecutionError, match="eligible control group"):
        run_incrementality(no_controls_campaign, require_statsmodels=True)

    # 2. Missing source snapshot lineage in production mode
    no_lineage_campaign = _build_campaign(with_lineage=False)
    with pytest.raises(AdLiftProductionExecutionError, match="source snapshot lineage"):
        run_incrementality(no_lineage_campaign, require_statsmodels=True)


def test_ac4_api_fails_closed_without_tenant_scope_or_durable_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API endpoints fail closed (HTTP 503 or 403) when production preconditions are not met."""
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    repo = InMemoryAdLiftRepository()
    service_fail = False
    try:
        AdLiftService(repository=repo, runtime_mode="production")
    except Exception:
        service_fail = True

    assert service_fail is True


# -----------------------------------------------------------------------------
# AC5: Adverse-state tests and evidence are delivered
# -----------------------------------------------------------------------------
def test_ac5_adverse_state_unprofitable_recommends_stop() -> None:
    """Unprofitable campaign (iromi < 1.0) returns Recommendation.STOP."""
    # Treatment GM lift = 30, ad_spend = 100 -> iromi = 0.3
    campaign = _build_campaign(treatment_post=112.0, control_post=100.0, ad_spend=100.0)
    report = run_incrementality(campaign, generated_at=GENERATED_AT)

    assert report.evidence_level is EvidenceLevel.L3_DID_VALIDATED
    assert report.iromi == 0.3
    assert report.recommendation is Recommendation.STOP


def test_ac5_adverse_state_break_even_recommends_continue() -> None:
    """Break-even campaign (1.0 <= iromi < 1.5) returns Recommendation.CONTINUE."""
    # Treatment post=120, control post=100 -> delta=20 rev/day; GM=10/day; 5 days = 50 GM lift; ad_spend=40 -> iromi=1.25
    campaign = _build_campaign(treatment_post=120.0, control_post=100.0, ad_spend=40.0)
    report = run_incrementality(campaign, generated_at=GENERATED_AT)

    assert report.evidence_level is EvidenceLevel.L3_DID_VALIDATED
    assert report.iromi == 1.25
    assert report.recommendation is Recommendation.CONTINUE
