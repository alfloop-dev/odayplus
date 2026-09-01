from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from modules.forecastops import (
    Alert,
    AlertDisposition,
    AlertLevel,
    FeedbackType,
    ForecastFeedback,
    ForecastOpsError,
    StoreDayObservation,
    backfill_alert_precision,
    calculate_alert_precision_metrics,
    default_forecast_alert_policy,
)
from shared.governance import InMemoryDecisionPolicyRepository

TENANT_ID = "tenant-precision-test"


def _make_alert(
    *,
    alert_id: str = "alert-001",
    store_id: str = "store-001",
    alert_level: AlertLevel = AlertLevel.RED,
    opened_at: datetime = datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
    disposition: str | AlertDisposition | None = None,
    deterioration_confirmed_at: datetime | None = None,
) -> Alert:
    return Alert(
        alert_id=alert_id,
        tenant_id=TENANT_ID,
        store_id=store_id,
        alert_level=alert_level,
        alert_reason_code="sitescore_gap",
        evidence_json={"sitescore_gap_ratio": -0.40},
        opened_at=opened_at,
        policy_id="four-light-policy",
        policy_version="four-light-policy-v1",
        policy_version_id=f"four-light-policy-v1:{TENANT_ID}",
        disposition=disposition,
        deterioration_confirmed_at=deterioration_confirmed_at,
    )


def test_alert_disposition_enum_parsing() -> None:
    assert AlertDisposition.from_str("TRUE_POSITIVE") is AlertDisposition.TRUE_POSITIVE
    assert AlertDisposition.from_str("true_positive") is AlertDisposition.TRUE_POSITIVE
    assert AlertDisposition.from_str("FALSE_POSITIVE") is AlertDisposition.FALSE_POSITIVE
    assert AlertDisposition.from_str("KNOWN_CONTEXT") is AlertDisposition.KNOWN_CONTEXT
    assert AlertDisposition.from_str("UNRESOLVED") is AlertDisposition.UNRESOLVED

    with pytest.raises(ForecastOpsError, match="Invalid alert disposition"):
        AlertDisposition.from_str("UNKNOWN_DISPOSITION")


def test_alert_lead_time_days_property() -> None:
    opened = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    confirmed = datetime(2026, 6, 8, 9, 0, tzinfo=UTC)

    # 1. TRUE_POSITIVE with confirmed deterioration: lead time is exactly 7 days
    tp_alert = _make_alert(
        disposition=AlertDisposition.TRUE_POSITIVE,
        deterioration_confirmed_at=confirmed,
        opened_at=opened,
    )
    assert tp_alert.lead_time_days == 7

    # 2. FALSE_POSITIVE: lead time is None
    fp_alert = _make_alert(
        disposition=AlertDisposition.FALSE_POSITIVE,
        deterioration_confirmed_at=confirmed,
        opened_at=opened,
    )
    assert fp_alert.lead_time_days is None

    # 3. KNOWN_CONTEXT: lead time is None
    kc_alert = _make_alert(
        disposition=AlertDisposition.KNOWN_CONTEXT,
        deterioration_confirmed_at=confirmed,
        opened_at=opened,
    )
    assert kc_alert.lead_time_days is None

    # 4. UNRESOLVED: lead time is None
    un_alert = _make_alert(
        disposition=AlertDisposition.UNRESOLVED,
        deterioration_confirmed_at=confirmed,
        opened_at=opened,
    )
    assert un_alert.lead_time_days is None

    # 5. Missing deterioration_confirmed_at: lead time is None
    no_det_alert = _make_alert(
        disposition=AlertDisposition.TRUE_POSITIVE,
        deterioration_confirmed_at=None,
        opened_at=opened,
    )
    assert no_det_alert.lead_time_days is None

    # 6. to_dict serialization includes lead_time_days
    alert_dict = tp_alert.to_dict()
    assert alert_dict["disposition"] == "TRUE_POSITIVE"
    assert alert_dict["deterioration_confirmed_at"] == "2026-06-08T09:00:00+00:00"
    assert alert_dict["lead_time_days"] == 7


def test_calculate_alert_precision_metrics_excludes_known_context_and_unresolved() -> None:
    """ODP-FR-FCT-006:
    Precision = TP / (TP + FP)
    Denominator strictly excludes KNOWN_CONTEXT and UNRESOLVED.
    """
    opened = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)

    alerts = [
        # 3 TRUE_POSITIVE (lead times: 3, 7, 10 days)
        _make_alert(
            alert_id="tp-1",
            disposition=AlertDisposition.TRUE_POSITIVE,
            deterioration_confirmed_at=opened + timedelta(days=3),
            opened_at=opened,
        ),
        _make_alert(
            alert_id="tp-2",
            disposition=AlertDisposition.TRUE_POSITIVE,
            deterioration_confirmed_at=opened + timedelta(days=7),
            opened_at=opened,
        ),
        _make_alert(
            alert_id="tp-3",
            disposition=AlertDisposition.TRUE_POSITIVE,
            deterioration_confirmed_at=opened + timedelta(days=10),
            opened_at=opened,
        ),
        # 1 FALSE_POSITIVE
        _make_alert(alert_id="fp-1", disposition=AlertDisposition.FALSE_POSITIVE, opened_at=opened),
        # 2 KNOWN_CONTEXT (e.g. remodeling - excluded from denominator)
        _make_alert(alert_id="kc-1", disposition=AlertDisposition.KNOWN_CONTEXT, opened_at=opened),
        _make_alert(alert_id="kc-2", disposition=AlertDisposition.KNOWN_CONTEXT, opened_at=opened),
        # 2 UNRESOLVED (not mature yet - excluded from denominator)
        _make_alert(alert_id="un-1", disposition=AlertDisposition.UNRESOLVED, opened_at=opened),
        _make_alert(alert_id="un-2", disposition=None, opened_at=opened),
    ]

    metrics = calculate_alert_precision_metrics(alerts)

    assert metrics["total_alerts"] == 8
    assert metrics["true_positive_count"] == 3
    assert metrics["false_positive_count"] == 1
    assert metrics["known_context_count"] == 2
    assert metrics["unresolved_count"] == 2
    # Denominator: 3 TP + 1 FP = 4 (KNOWN_CONTEXT & UNRESOLVED excluded!)
    assert metrics["evaluated_alert_count"] == 4
    # Precision: 3 / 4 = 0.75
    assert metrics["precision"] == 0.75

    # Lead time stats over [3, 7, 10]
    assert metrics["lead_time_sample_count"] == 3
    assert metrics["mean_lead_time_days"] == 6.67
    assert metrics["min_lead_time_days"] == 3
    assert metrics["max_lead_time_days"] == 10


def test_calculate_alert_precision_empty_and_zero_division() -> None:
    empty_metrics = calculate_alert_precision_metrics([])
    assert empty_metrics["total_alerts"] == 0
    assert empty_metrics["evaluated_alert_count"] == 0
    assert empty_metrics["precision"] is None
    assert empty_metrics["mean_lead_time_days"] is None

    only_kc = calculate_alert_precision_metrics([
        _make_alert(disposition=AlertDisposition.KNOWN_CONTEXT)
    ])
    assert only_kc["total_alerts"] == 1
    assert only_kc["evaluated_alert_count"] == 0
    assert only_kc["precision"] is None


def test_backfill_alert_precision_logic() -> None:
    """ODP-FR-FCT-006:
    Deterioration confirmed at policy threshold.
    Remodeling annotations become KNOWN_CONTEXT.
    Un-breached completed windows become FALSE_POSITIVE.
    Short windows remain UNRESOLVED.
    """
    opened = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    policy_repo = InMemoryDecisionPolicyRepository([default_forecast_alert_policy(TENANT_ID)])

    alerts = [
        # Store 1: Will deteriorate on June 8 (7 days lead time)
        _make_alert(alert_id="a-store-1", store_id="store-1", alert_level=AlertLevel.RED, opened_at=opened),
        # Store 2: Covered by CONTEXT_ANNOTATION (remodeling June 5-15) -> KNOWN_CONTEXT
        _make_alert(alert_id="a-store-2", store_id="store-2", alert_level=AlertLevel.RED, opened_at=opened),
        # Store 3: 30 days of clean observations, never deteriorated -> FALSE_POSITIVE
        _make_alert(alert_id="a-store-3", store_id="store-3", alert_level=AlertLevel.RED, opened_at=opened),
        # Store 4: Only 5 days of observations, no deterioration yet -> UNRESOLVED
        _make_alert(alert_id="a-store-4", store_id="store-4", alert_level=AlertLevel.RED, opened_at=opened),
    ]

    observations = [
        # Store 1: Mild at first, breaches RED threshold (gap <= -0.35) on June 8 (actual=60k vs 100k baseline = -0.40)
        StoreDayObservation(store_id="store-1", business_date=date(2026, 6, 1), actual_revenue=90_000.0, site_score_baseline_p50=100_000.0),
        StoreDayObservation(store_id="store-1", business_date=date(2026, 6, 5), actual_revenue=80_000.0, site_score_baseline_p50=100_000.0),
        StoreDayObservation(store_id="store-1", business_date=date(2026, 6, 8), actual_revenue=60_000.0, site_score_baseline_p50=100_000.0),

        # Store 2: Also drops to 50k, but has renovation annotation
        StoreDayObservation(store_id="store-2", business_date=date(2026, 6, 8), actual_revenue=50_000.0, site_score_baseline_p50=100_000.0),

        # Store 3: Healthy revenue for 30 days
        *(
            StoreDayObservation(store_id="store-3", business_date=date(2026, 6, d), actual_revenue=95_000.0, site_score_baseline_p50=100_000.0)
            for d in range(1, 31)
        ),

        # Store 4: Only 5 days, healthy
        *(
            StoreDayObservation(store_id="store-4", business_date=date(2026, 6, d), actual_revenue=95_000.0, site_score_baseline_p50=100_000.0)
            for d in range(1, 6)
        ),
    ]

    feedbacks = [
        ForecastFeedback.create(
            tenant_id=TENANT_ID,
            store_id="store-2",
            feedback_type=FeedbackType.CONTEXT_ANNOTATION,
            target_date_start=date(2026, 6, 5),
            target_date_end=date(2026, 6, 15),
            reason="Store remodeling and renovation",
            created_by="ops-lead",
        )
    ]

    updated_alerts, metrics = backfill_alert_precision(
        alerts,
        observations=observations,
        feedbacks=feedbacks,
        policy_repository=policy_repo,
        evaluation_horizon_days=28,
        actor="test_runner",
    )

    alert_map = {a.alert_id: a for a in updated_alerts}

    # Store 1: TRUE_POSITIVE with 7 days lead time
    a1 = alert_map["a-store-1"]
    assert a1.disposition == "TRUE_POSITIVE"
    assert a1.deterioration_confirmed_at == datetime(2026, 6, 8, 9, 0, tzinfo=UTC)
    assert a1.lead_time_days == 7

    # Store 2: KNOWN_CONTEXT due to renovation annotation
    a2 = alert_map["a-store-2"]
    assert a2.disposition == "KNOWN_CONTEXT"
    assert a2.deterioration_confirmed_at is None
    assert a2.lead_time_days is None

    # Store 3: FALSE_POSITIVE (30 days completed without deterioration)
    a3 = alert_map["a-store-3"]
    assert a3.disposition == "FALSE_POSITIVE"
    assert a3.deterioration_confirmed_at is None
    assert a3.lead_time_days is None

    # Store 4: UNRESOLVED (only 5 days observed, window of 28 days not reached)
    a4 = alert_map["a-store-4"]
    assert a4.disposition == "UNRESOLVED"
    assert a4.deterioration_confirmed_at is None
    assert a4.lead_time_days is None

    # Metrics summary
    assert metrics["total_alerts"] == 4
    assert metrics["true_positive_count"] == 1
    assert metrics["false_positive_count"] == 1
    assert metrics["known_context_count"] == 1
    assert metrics["unresolved_count"] == 1
    assert metrics["evaluated_alert_count"] == 2
    assert metrics["precision"] == 0.5
    assert metrics["mean_lead_time_days"] == 7.0
