"""ODP-FIN-ML-002: statistical methods backed by declared libraries.

These lock the rigour added on top of the hand-written baselines:
  * scikit-learn log-log OLS elasticity estimation (priceops / solver.pricing),
  * statsmodels OLS standard error for the matched-pair DiD effect (adlift),
  * numpy residual-volatility prediction interval (forecastops).

The existing behaviour tests already pin the point estimates; these assert the
newly library-backed statistics behave correctly without changing them.
"""

from __future__ import annotations

from datetime import date

from modules.adlift import AdCampaign, StoreDayMetric, run_incrementality
from modules.forecastops import ForecastInput, StoreDayObservation, forecast_stores
from modules.priceops import PriceElasticityEstimate
from solver.pricing.demand import ElasticityFit, estimate_elasticity

# --- scikit-learn elasticity estimation -----------------------------------


def test_elasticity_estimate_recovers_known_constant_elasticity() -> None:
    # Noiseless constant-elasticity data: q = 1000 * (p / 10) ** -1.5.
    base_price, base_demand, true_elasticity = 10.0, 1000.0, -1.5
    observations = [
        (price, base_demand * (price / base_price) ** true_elasticity)
        for price in (8.0, 9.0, 10.0, 11.0, 12.0, 13.0)
    ]

    estimate = PriceElasticityEstimate.from_observations(observations)

    assert abs(estimate.elasticity_value - true_elasticity) < 1e-6
    assert estimate.confidence > 0.99  # R^2 ~ 1.0 for a perfect log-log fit


def test_estimate_elasticity_reports_fit_quality() -> None:
    fit = estimate_elasticity([(8.0, 1_200.0), (10.0, 1_000.0), (12.0, 850.0)])

    assert isinstance(fit, ElasticityFit)
    assert fit.sample_size == 3
    assert fit.elasticity < 0  # demand falls as price rises
    assert 0.0 <= fit.confidence <= 1.0
    assert fit.to_dict()["r_squared"] == fit.r_squared


def test_elasticity_estimate_degrades_without_price_variation() -> None:
    # A single price cannot identify an elasticity: fall back to zero confidence.
    estimate = PriceElasticityEstimate.from_observations([(10.0, 1_000.0), (10.0, 950.0)])

    assert estimate.elasticity_value == 0.0
    assert estimate.confidence == 0.0


# --- statsmodels DiD standard error ---------------------------------------

_PRE_DAYS = tuple(range(1, 11))
_CAMPAIGN_DAYS = tuple(range(11, 21))


def _metric(store_id: str, day: int, revenue: float) -> StoreDayMetric:
    return StoreDayMetric(
        store_id=store_id,
        business_date=date(2026, 5, day),
        revenue=revenue,
        gross_margin=revenue * 0.5,
    )


def _panel(rows: dict[str, tuple[float, float]]) -> list[StoreDayMetric]:
    metrics: list[StoreDayMetric] = []
    for store_id, (pre, post) in rows.items():
        metrics.extend(_metric(store_id, day, pre) for day in _PRE_DAYS)
        metrics.extend(_metric(store_id, day, post) for day in _CAMPAIGN_DAYS)
    return metrics


def _campaign(
    rows: dict[str, tuple[float, float]], treatment: tuple[str, ...], control: tuple[str, ...]
) -> AdCampaign:
    return AdCampaign(
        campaign_id="camp-se",
        name="SE Campaign",
        treatment_store_ids=treatment,
        candidate_control_store_ids=control,
        pre_period_start=date(2026, 5, 1),
        pre_period_end=date(2026, 5, 10),
        campaign_period_start=date(2026, 5, 11),
        campaign_period_end=date(2026, 5, 20),
        ad_spend=1_000.0,
        observations=tuple(_panel(rows)),
    )


def test_did_effect_interval_carries_standard_error_when_pairs_differ() -> None:
    # Two matched pairs with different lifts -> non-zero dispersion.
    # t-high lift +300 vs c-high +100 => DiD rev 200, gm 100.
    # t-low  lift +60  vs c-low  +20  => DiD rev 40,  gm 20.
    campaign = _campaign(
        {
            "t-high": (1_000.0, 1_300.0),
            "t-low": (200.0, 260.0),
            "c-high": (1_000.0, 1_100.0),
            "c-low": (200.0, 220.0),
        },
        treatment=("t-high", "t-low"),
        control=("c-high", "c-low"),
    )

    report = run_incrementality(campaign)
    interval = report.effect_interval

    assert interval.metric == "did_gm_per_store_day"
    assert interval.standard_error > 0
    assert interval.low < interval.point < interval.high
    # The point estimate is still the plain mean of the per-pair effects.
    assert interval.point == 60.0
    assert report.to_dict()["effect_interval"]["standard_error"] == interval.standard_error


def test_did_effect_interval_collapses_when_pairs_agree() -> None:
    # Identical pairs -> zero residual variance -> degenerate interval.
    campaign = _campaign(
        {
            "t1": (1_000.0, 1_300.0),
            "t2": (1_000.0, 1_300.0),
            "c1": (1_000.0, 1_100.0),
            "c2": (1_000.0, 1_100.0),
        },
        treatment=("t1", "t2"),
        control=("c1", "c2"),
    )

    interval = run_incrementality(campaign).effect_interval

    assert interval.standard_error == 0.0
    assert interval.low == interval.point == interval.high


# --- numpy residual-volatility prediction interval ------------------------


def _forecast_input(store_id: str, revenues: list[float]) -> ForecastInput:
    return ForecastInput(
        store_id=store_id,
        observations=tuple(
            StoreDayObservation(
                store_id=store_id,
                business_date=date(2026, 6, index + 1),
                actual_revenue=revenue,
                site_score_baseline_p50=1_000.0,
            )
            for index, revenue in enumerate(revenues)
        ),
    )


def test_prediction_band_width_reflects_series_volatility() -> None:
    smooth = _forecast_input("smooth", [1_000.0 + index * 5 for index in range(10)])
    noisy = _forecast_input(
        "noisy", [800.0, 1_200.0, 800.0, 1_200.0, 800.0, 1_200.0, 800.0, 1_200.0, 800.0, 1_200.0]
    )

    outputs, _alerts, _handoffs = forecast_stores([smooth, noisy])
    by_store = {output.store_id: output for output in outputs}

    def relative_width(store_id: str) -> float:
        band = by_store[store_id].w4
        return (band.p90 - band.p10) / band.p50

    # A noisy series must produce a wider prediction interval than a clean trend.
    assert relative_width("noisy") > relative_width("smooth")
    # Ordering invariant still holds for both.
    for output in outputs:
        assert output.w4.p10 <= output.w4.p50 <= output.w4.p90
