from __future__ import annotations

from datetime import date

import numpy as np
import pytest
import statsmodels.api as sm

from modules.adlift import (
    AdCampaign,
    CausalChallengerRequest,
    ChallengerUnavailableError,
    DoubleMLStyleAdapter,
    EconMLStyleAdapter,
    StoreDayMetric,
    run_incrementality,
)


def _metric(store_id: str, day: int, revenue: float) -> StoreDayMetric:
    return StoreDayMetric(
        store_id=store_id,
        business_date=date(2026, 7, day),
        revenue=revenue,
        gross_margin=revenue * 0.5,
    )


def _campaign() -> AdCampaign:
    observations: list[StoreDayMetric] = []
    pairs = (
        ("t1", "c1", 100.0, 2, 30.0, 10.0),
        ("t2", "c2", 300.0, 3, 80.0, 20.0),
        ("t3", "c3", 600.0, 4, 130.0, 30.0),
    )
    for treatment, control, base, post_days, treatment_lift, control_lift in pairs:
        for day in (1, 2, 3, 4):
            observations.append(_metric(treatment, day, base))
            observations.append(_metric(control, day, base))
        for offset in range(post_days):
            day = 11 + offset
            observations.append(_metric(treatment, day, base + treatment_lift))
            observations.append(_metric(control, day, base + control_lift))
    return AdCampaign(
        campaign_id="statsmodels-did",
        name="Statsmodels DiD",
        treatment_store_ids=("t1", "t2", "t3"),
        candidate_control_store_ids=("c1", "c2", "c3"),
        pre_period_start=date(2026, 7, 1),
        pre_period_end=date(2026, 7, 4),
        campaign_period_start=date(2026, 7, 11),
        campaign_period_end=date(2026, 7, 15),
        ad_spend=100.0,
        observations=tuple(observations),
    )


def test_matched_did_uses_statsmodels_wls_for_point_and_total_effect() -> None:
    report = run_incrementality(_campaign())
    pair_effects = np.asarray([20.0, 60.0, 100.0])
    treated_days = np.asarray([2.0, 3.0, 4.0])
    expected = sm.WLS(
        pair_effects,
        np.ones((len(pair_effects), 1)),
        weights=treated_days,
    ).fit()

    assert report.incremental_revenue == pytest.approx(float(expected.params[0]) * 9)
    assert report.incremental_gross_margin == pytest.approx(float(expected.params[0]) * 0.5 * 9)
    assert report.effect_interval.point == pytest.approx(
        float(expected.params[0]) * 0.5,
        abs=1e-4,
    )
    assert report.effect_interval.standard_error > 0
    assert report.estimator_metadata == {
        "library": "statsmodels",
        "estimator": "WLS",
        "design": "matched_pair_difference_in_differences",
        "formula": "pair_did_effect ~ 1",
        "weights": "treated_campaign_days",
        "pair_count": 3,
        "treated_campaign_days": 9,
    }
    assert report.to_dict()["estimator_metadata"]["library"] == "statsmodels"


@pytest.mark.parametrize("adapter_type", [DoubleMLStyleAdapter, EconMLStyleAdapter])
def test_unconfigured_optional_challenger_is_never_reported_available(adapter_type: type) -> None:
    adapter = adapter_type()
    capability = adapter.capability()

    assert capability.available is False
    assert capability.reason in {
        f"dependency_missing:{capability.dependency}",
        "estimator_factory_not_configured",
    }
    with pytest.raises(ChallengerUnavailableError, match="challenger unavailable"):
        adapter.fit_estimate(
            CausalChallengerRequest(
                outcome=(1.0, 2.0),
                treatment=(0.0, 1.0),
                features=((0.1,), (0.2,)),
                feature_names=("demand",),
            )
        )
