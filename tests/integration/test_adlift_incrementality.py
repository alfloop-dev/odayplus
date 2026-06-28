from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.adlift import (
    AdCampaign,
    AdLiftService,
    EvidenceLevel,
    InMemoryAdLiftRepository,
    PreTrendStatus,
    Recommendation,
    StoreDayMetric,
    run_adlift_incrementality_batch,
    run_incrementality,
)

GENERATED_AT = datetime(2026, 6, 28, 9, 0, tzinfo=UTC)

PRE_DAYS = tuple(range(1, 11))  # 2026-05-01 .. 2026-05-10
CAMPAIGN_DAYS = tuple(range(11, 21))  # 2026-05-11 .. 2026-05-20
GROSS_MARGIN_RATE = 0.5


def _metric(store_id: str, day: int, revenue: float) -> StoreDayMetric:
    return StoreDayMetric(
        store_id=store_id,
        business_date=date(2026, 5, day),
        revenue=revenue,
        gross_margin=revenue * GROSS_MARGIN_RATE,
        source_snapshot_ids=(f"pos-store-{store_id}-202605{day:02d}",),
    )


def _flat_observations(
    *,
    treatment_pre: float,
    treatment_post: float,
    control_pre: float,
    control_post: float,
    treatment_stores: tuple[str, ...] = ("t1", "t2"),
    control_stores: tuple[str, ...] = ("c1", "c2"),
    contaminated_store: str | None = None,
    contaminating_intervention_id: str = "promo-999",
) -> list[StoreDayMetric]:
    metrics: list[StoreDayMetric] = []
    for store in treatment_stores:
        for day in PRE_DAYS:
            metrics.append(_metric(store, day, treatment_pre))
        for day in CAMPAIGN_DAYS:
            metric = _metric(store, day, treatment_post)
            if store == contaminated_store:
                metric = StoreDayMetric(
                    store_id=metric.store_id,
                    business_date=metric.business_date,
                    revenue=metric.revenue,
                    gross_margin=metric.gross_margin,
                    active_intervention_ids=(contaminating_intervention_id,),
                    source_snapshot_ids=metric.source_snapshot_ids,
                )
            metrics.append(metric)
    for store in control_stores:
        for day in PRE_DAYS:
            metrics.append(_metric(store, day, control_pre))
        for day in CAMPAIGN_DAYS:
            metrics.append(_metric(store, day, control_post))
    return metrics


def _campaign(
    observations: list[StoreDayMetric],
    *,
    treatment_stores: tuple[str, ...] = ("t1", "t2"),
    control_stores: tuple[str, ...] = ("c1", "c2"),
    ad_spend: float = 1_000.0,
    campaign_id: str = "camp-spring",
) -> AdCampaign:
    return AdCampaign(
        campaign_id=campaign_id,
        name="Spring Paid Search",
        treatment_store_ids=treatment_stores,
        candidate_control_store_ids=control_stores,
        pre_period_start=date(2026, 5, 1),
        pre_period_end=date(2026, 5, 10),
        campaign_period_start=date(2026, 5, 11),
        campaign_period_end=date(2026, 5, 20),
        ad_spend=ad_spend,
        observations=tuple(observations),
        channel="paid_search",
        audience="lapsed_members",
        campaign_intervention_id="ad-int-camp-spring",
    )


def test_difference_in_differences_isolates_ad_lift_from_market_movement() -> None:
    # Control rises +100/day (market); treatment rises +300/day (market + ad).
    observations = _flat_observations(
        treatment_pre=1_000,
        treatment_post=1_300,
        control_pre=1_000,
        control_post=1_100,
    )
    report = run_incrementality(_campaign(observations), generated_at=GENERATED_AT)

    # Matched-pair DiD per store-day = (1300-1000) - (1100-1000) = 200; 10 days x 2 pairs = 4000.
    assert report.incremental_revenue == 4_000.0
    assert report.incremental_gross_margin == 2_000.0
    # Surface revenue is the raw observed treatment campaign revenue, kept separate.
    assert report.surface_revenue == 1_300 * 10 * 2
    # IROMI = incremental gross margin / ad spend = 2000 / 1000 (uses GM, not revenue).
    assert report.iromi == 2.0
    assert report.measurement_method == "DID"
    assert report.pre_trend_status is PreTrendStatus.PASS
    assert report.evidence_level is EvidenceLevel.L3_DID_VALIDATED
    assert report.causal_claim_allowed is True
    assert report.recommendation is Recommendation.SCALE
    assert not report.contamination


def test_break_even_lift_recommends_continue() -> None:
    # Treatment +200/day vs control +100/day -> DiD 100/day; GM 50/day; iromi = 1.0.
    observations = _flat_observations(
        treatment_pre=1_000,
        treatment_post=1_200,
        control_pre=1_000,
        control_post=1_100,
    )
    report = run_incrementality(_campaign(observations), generated_at=GENERATED_AT)

    assert report.incremental_gross_margin == 1_000.0
    assert report.iromi == 1.0
    assert report.evidence_level is EvidenceLevel.L3_DID_VALIDATED
    assert report.recommendation is Recommendation.CONTINUE


def test_unprofitable_lift_recommends_stop() -> None:
    # Treatment and control move together -> DiD ~0 -> iromi below break-even.
    observations = _flat_observations(
        treatment_pre=1_000,
        treatment_post=1_050,
        control_pre=1_000,
        control_post=1_100,
    )
    report = run_incrementality(_campaign(observations), generated_at=GENERATED_AT)

    assert report.iromi < 1.0
    assert report.evidence_level is EvidenceLevel.L3_DID_VALIDATED
    assert report.recommendation is Recommendation.STOP


def test_matched_controls_pair_nearest_pre_period_level() -> None:
    observations: list[StoreDayMetric] = []
    for day in PRE_DAYS:
        observations.append(_metric("t-high", day, 1_000))
        observations.append(_metric("t-low", day, 200))
        observations.append(_metric("c-high", day, 980))
        observations.append(_metric("c-low", day, 220))
    for day in CAMPAIGN_DAYS:
        observations.append(_metric("t-high", day, 1_200))
        observations.append(_metric("t-low", day, 250))
        observations.append(_metric("c-high", day, 1_050))
        observations.append(_metric("c-low", day, 230))

    report = run_incrementality(
        _campaign(
            observations,
            treatment_stores=("t-high", "t-low"),
            control_stores=("c-high", "c-low"),
        ),
        generated_at=GENERATED_AT,
    )

    pairs = {match.treatment_store_id: match.control_store_id for match in report.matched_controls}
    assert pairs == {"t-high": "c-high", "t-low": "c-low"}
    assert set(report.control_store_ids) == {"c-high", "c-low"}


def test_pre_trend_failure_caps_evidence_at_l2_and_blocks_causal_claim() -> None:
    # Treatment trends up through the pre-period while control stays flat:
    # parallel-trends assumption is violated (ODP-ML-05 §8.3, AC-07-02).
    observations: list[StoreDayMetric] = []
    for store in ("t1", "t2"):
        for day in PRE_DAYS:
            observations.append(_metric(store, day, 1_000 + day * 50))
        for day in CAMPAIGN_DAYS:
            observations.append(_metric(store, day, 1_800))
    for store in ("c1", "c2"):
        for day in PRE_DAYS:
            observations.append(_metric(store, day, 1_000))
        for day in CAMPAIGN_DAYS:
            observations.append(_metric(store, day, 1_050))

    report = run_incrementality(_campaign(observations), generated_at=GENERATED_AT)

    assert report.pre_trend_status is PreTrendStatus.FAIL
    assert report.pre_trend.slope_divergence > report.pre_trend.threshold
    assert report.evidence_level is EvidenceLevel.L2_MATCHED_DESCRIPTIVE
    assert report.causal_claim_allowed is False
    assert report.recommendation is Recommendation.INCONCLUSIVE


def test_contamination_in_window_caps_evidence_at_l2() -> None:
    observations = _flat_observations(
        treatment_pre=1_000,
        treatment_post=1_300,
        control_pre=1_000,
        control_post=1_100,
        contaminated_store="t1",
    )
    report = run_incrementality(_campaign(observations), generated_at=GENERATED_AT)

    # Even though pre-trend passes, intervention overlap fails the balance check.
    assert report.pre_trend_status is PreTrendStatus.PASS
    assert report.evidence_level is EvidenceLevel.L2_MATCHED_DESCRIPTIVE
    assert report.causal_claim_allowed is False
    assert [finding.store_id for finding in report.contamination] == ["t1"]
    assert report.contamination[0].intervention_ids == ("promo-999",)
    assert report.contamination[0].role == "treatment"


def test_no_control_group_is_before_after_and_blocks_causal_claim() -> None:
    observations: list[StoreDayMetric] = []
    for store in ("t1", "t2"):
        for day in PRE_DAYS:
            observations.append(_metric(store, day, 1_000))
        for day in CAMPAIGN_DAYS:
            observations.append(_metric(store, day, 1_300))

    report = run_incrementality(
        _campaign(observations, control_stores=()),
        generated_at=GENERATED_AT,
    )

    assert report.control_store_ids == ()
    assert report.pre_trend_status is PreTrendStatus.NOT_TESTED
    assert report.evidence_level is EvidenceLevel.L1_BEFORE_AFTER
    assert report.causal_claim_allowed is False
    assert report.recommendation is Recommendation.INCONCLUSIVE
    # Before/after change is still reported, just not as a causal estimate.
    assert report.incremental_revenue == 300 * 10 * 2


def test_writeback_targets_interventionops_and_label_registry() -> None:
    observations = _flat_observations(
        treatment_pre=1_000,
        treatment_post=1_300,
        control_pre=1_000,
        control_post=1_100,
    )
    report = run_incrementality(_campaign(observations), generated_at=GENERATED_AT)

    writeback = report.intervention_writeback
    assert writeback["intervention_type"] == "ad_campaign"
    assert writeback["campaign_id"] == "camp-spring"
    assert writeback["evidence_level"] == "L3"
    assert writeback["iromi"] == 2.0

    label = report.label_registry_entry
    assert label["label_type"] == "ad_incrementality"
    assert label["measurement_method"] == "DID"
    assert label["incremental_gross_margin"] == 2_000.0
    assert label["label_maturity_time"] == "2026-05-20"


def test_report_card_projection_matches_component_contract() -> None:
    observations = _flat_observations(
        treatment_pre=1_000,
        treatment_post=1_300,
        control_pre=1_000,
        control_post=1_100,
    )
    report = run_incrementality(_campaign(observations), generated_at=GENERATED_AT)
    card = report.to_report_card()

    assert set(card) == {
        "campaign",
        "treatmentStores",
        "controlStores",
        "preTrendStatus",
        "incrementalRevenue",
        "incrementalGrossMargin",
        "iromi",
        "evidenceLevel",
        "continueStopRecommendation",
    }
    assert card["campaign"] == "Spring Paid Search"
    assert card["incrementalRevenue"] == 4_000.0
    assert card["evidenceLevel"] == "L3"
    assert card["continueStopRecommendation"] == "SCALE"
    assert report.to_dict()["report_card"] == card


def test_service_versions_reports_per_campaign() -> None:
    repository = InMemoryAdLiftRepository()
    service = AdLiftService(repository=repository)
    campaign = _campaign(
        _flat_observations(
            treatment_pre=1_000,
            treatment_post=1_300,
            control_pre=1_000,
            control_post=1_100,
        )
    )

    first = service.evaluate([campaign], generated_at=GENERATED_AT)
    second = service.evaluate([campaign], generated_at=GENERATED_AT)

    assert first.reports[0].report_version == 1
    assert second.reports[0].report_version == 2
    assert repository.latest_for_campaign("camp-spring").report_version == 2


def test_batch_worker_succeeds_and_serialises() -> None:
    repository = InMemoryAdLiftRepository()
    campaign = _campaign(
        _flat_observations(
            treatment_pre=1_000,
            treatment_post=1_300,
            control_pre=1_000,
            control_post=1_100,
        )
    )

    result = run_adlift_incrementality_batch(
        campaigns=[campaign],
        job_id="adlift-job-1",
        generated_at=GENERATED_AT.isoformat(),
        repository=repository,
    )

    assert result.job_id == "adlift-job-1"
    assert result.status == "succeeded"
    payload = result.to_dict()
    assert payload["reports"][0]["evidence_level"] == "L3"
    assert payload["reports"][0]["report_card"]["iromi"] == 2.0


def test_adlift_api_runs_incrementality_and_is_idempotent() -> None:
    client = TestClient(create_app())
    campaign_payload: dict = {
        "campaign_id": "camp-api-001",
        "name": "API Spring Campaign",
        "channel": "paid_search",
        "treatment_store_ids": ["t1", "t2"],
        "candidate_control_store_ids": ["c1", "c2"],
        "pre_period_start": "2026-05-01",
        "pre_period_end": "2026-05-10",
        "campaign_period_start": "2026-05-11",
        "campaign_period_end": "2026-05-20",
        "ad_spend": 1_000.0,
        "observations": [],
    }
    for store, pre, post in (("t1", 1_000, 1_300), ("t2", 1_000, 1_300), ("c1", 1_000, 1_100), ("c2", 1_000, 1_100)):
        for day in PRE_DAYS:
            campaign_payload["observations"].append(
                {
                    "store_id": store,
                    "business_date": f"2026-05-{day:02d}",
                    "revenue": pre,
                    "gross_margin_rate": GROSS_MARGIN_RATE,
                }
            )
        for day in CAMPAIGN_DAYS:
            campaign_payload["observations"].append(
                {
                    "store_id": store,
                    "business_date": f"2026-05-{day:02d}",
                    "revenue": post,
                    "gross_margin_rate": GROSS_MARGIN_RATE,
                }
            )

    body = {"generated_at": GENERATED_AT.isoformat(), "campaigns": [campaign_payload]}
    response = client.post(
        "/adlift/incrementality-jobs",
        json=body,
        headers={"x-correlation-id": "corr-adlift-1", "Idempotency-Key": "adlift-idem-1"},
    )

    assert response.status_code == 202
    data = response.json()
    assert data["created"] is True
    assert data["correlation_id"] == "corr-adlift-1"
    report = data["reports"][0]
    assert report["incremental_revenue"] == 4_000.0
    assert report["evidence_level"] == "L3"
    assert report["report_card"]["continueStopRecommendation"] == "SCALE"
    job_id = data["job_id"]

    replay = client.post(
        "/adlift/incrementality-jobs",
        json=body,
        headers={"x-correlation-id": "corr-adlift-1", "Idempotency-Key": "adlift-idem-1"},
    )
    assert replay.json()["created"] is False
    assert replay.json()["job_id"] == job_id

    reports = client.get("/adlift/reports", params={"evidence_level": "L3"})
    assert reports.json()["count"] == 1
    fetched = client.get("/adlift/reports/camp-api-001")
    assert fetched.json()["report_version"] == 1

    audit = client.get("/audit/events", params={"correlation_id": "corr-adlift-1"})
    assert any(
        event["event_type"] == "adlift.incrementality_evaluated.v1"
        for event in audit.json()["events"]
    )
