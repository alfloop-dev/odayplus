from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from modules.forecastops import (
    ForecastBand,
    ForecastInput,
    ForecastOpsService,
    StoreDayObservation,
    default_forecast_alert_policy,
)
from modules.forecastops.application.production_model import _feature_row
from modules.forecastops.domain.forecasting import ForecastOutput
from shared.governance import InMemoryDecisionPolicyRepository

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
TENANT_ID = "tenant-quality-test"


def _policy_repo() -> InMemoryDecisionPolicyRepository:
    return InMemoryDecisionPolicyRepository([default_forecast_alert_policy(TENANT_ID)])


def test_store_day_observation_defaults_quality_score_to_none() -> None:
    obs = StoreDayObservation(
        store_id="store-1",
        business_date=date(2026, 7, 1),
        actual_revenue=10_000.0,
    )
    assert obs.data_quality_score is None
    assert obs.to_dict()["data_quality_score"] is None


def test_store_day_observation_from_mapping_unsupplied_quality_is_none() -> None:
    obs_missing = StoreDayObservation.from_mapping(
        {"store_id": "store-1", "date": "2026-07-01", "revenue": 10_000.0}
    )
    assert obs_missing.data_quality_score is None
    assert obs_missing.to_dict()["data_quality_score"] is None

    obs_none = StoreDayObservation.from_mapping(
        {
            "store_id": "store-1",
            "date": "2026-07-01",
            "revenue": 10_000.0,
            "data_quality_score": None,
        }
    )
    assert obs_none.data_quality_score is None
    assert obs_none.to_dict()["data_quality_score"] is None


def test_store_day_observation_from_mapping_measured_quality_is_bounded() -> None:
    obs_valid = StoreDayObservation.from_mapping(
        {
            "store_id": "store-1",
            "date": "2026-07-01",
            "revenue": 10_000.0,
            "data_quality_score": 0.95,
        }
    )
    assert obs_valid.data_quality_score == 0.95

    obs_alias = StoreDayObservation.from_mapping(
        {
            "store_id": "store-1",
            "date": "2026-07-01",
            "revenue": 10_000.0,
            "data_quality": 0.85,
        }
    )
    assert obs_alias.data_quality_score == 0.85

    obs_overflow = StoreDayObservation.from_mapping(
        {
            "store_id": "store-1",
            "date": "2026-07-01",
            "revenue": 10_000.0,
            "data_quality_score": 1.5,
        }
    )
    assert obs_overflow.data_quality_score == 1.0

    obs_underflow = StoreDayObservation.from_mapping(
        {
            "store_id": "store-1",
            "date": "2026-07-01",
            "revenue": 10_000.0,
            "data_quality_score": -0.5,
        }
    )
    assert obs_underflow.data_quality_score == 0.0


def test_forecast_output_defaults_quality_score_to_none() -> None:
    band = ForecastBand(p10=90.0, p50=100.0, p90=110.0)
    output = ForecastOutput(
        forecast_output_id="out-1",
        tenant_id=TENANT_ID,
        store_id="store-1",
        prediction_run_id="run-1",
        horizon_days=28,
        target_metric="revenue",
        p10=90.0,
        p50=100.0,
        p90=110.0,
        w4=band,
        w8=band,
        w12=band,
        w24=band,
        trajectory_class="plateau",
        turning_point_probability=0.0,
        sitescore_gap_ratio=0.0,
        actual_revenue=100.0,
        sitescore_baseline_p50=100.0,
        model_version="v1",
        feature_version="v1",
        policy_version="v1",
        prediction_origin_time=NOW,
        scored_at=NOW,
    )
    assert output.data_quality_score is None
    assert output.to_dict()["data_quality_score"] is None


def test_baseline_forecast_produces_explicitly_absent_quality_when_observations_unmeasured() -> None:
    service = ForecastOpsService(policy_repository=_policy_repo())
    start = date(2026, 7, 1)
    observations = tuple(
        StoreDayObservation(
            store_id="store-1",
            business_date=start + timedelta(days=i),
            actual_revenue=10_000.0 + i * 100.0,
        )
        for i in range(14)
    )
    forecast_input = ForecastInput(
        tenant_id=TENANT_ID,
        store_id="store-1",
        observations=observations,
        prediction_origin_time=NOW,
    )
    result = service.forecast([forecast_input], scored_at=NOW)
    forecast = result.forecasts[0]
    assert forecast.data_quality_score is None
    assert forecast.to_dict()["data_quality_score"] is None


def test_baseline_forecast_produces_explicitly_absent_quality_when_any_observation_unmeasured() -> None:
    service = ForecastOpsService(policy_repository=_policy_repo())
    start = date(2026, 7, 1)
    observations = tuple(
        StoreDayObservation(
            store_id="store-1",
            business_date=start + timedelta(days=i),
            actual_revenue=10_000.0 + i * 100.0,
            data_quality_score=0.95 if i != 5 else None,
        )
        for i in range(14)
    )
    forecast_input = ForecastInput(
        tenant_id=TENANT_ID,
        store_id="store-1",
        observations=observations,
        prediction_origin_time=NOW,
    )
    result = service.forecast([forecast_input], scored_at=NOW)
    forecast = result.forecasts[0]
    assert forecast.data_quality_score is None
    assert forecast.to_dict()["data_quality_score"] is None


def test_baseline_forecast_computes_min_quality_when_all_observations_measured() -> None:
    service = ForecastOpsService(policy_repository=_policy_repo())
    start = date(2026, 7, 1)
    scores = [0.98, 0.92, 0.95, 0.88, 0.99, 0.94, 0.90, 0.91, 0.93, 0.89, 0.96, 0.97, 0.85, 0.99]
    observations = tuple(
        StoreDayObservation(
            store_id="store-1",
            business_date=start + timedelta(days=i),
            actual_revenue=10_000.0 + i * 100.0,
            data_quality_score=scores[i],
        )
        for i in range(14)
    )
    forecast_input = ForecastInput(
        tenant_id=TENANT_ID,
        store_id="store-1",
        observations=observations,
        prediction_origin_time=NOW,
    )
    result = service.forecast([forecast_input], scored_at=NOW)
    forecast = result.forecasts[0]
    assert forecast.data_quality_score == min(scores)


def test_production_feature_row_handles_absent_and_measured_quality() -> None:
    origin = NOW
    start = origin.date() - timedelta(days=28)

    # All observations measured
    scores = [0.95 - (i % 5) * 0.02 for i in range(28)]
    measured_observations = tuple(
        StoreDayObservation(
            store_id="store-1",
            business_date=start + timedelta(days=i),
            actual_revenue=10_000.0,
            data_quality_score=scores[i],
            source_snapshot_ids=(f"snap-{i}",),
        )
        for i in range(28)
    )
    forecast_input_measured = ForecastInput(
        tenant_id=TENANT_ID,
        store_id="store-1",
        observations=measured_observations,
        prediction_origin_time=origin,
    )
    row_measured = _feature_row(forecast_input_measured, horizon_weeks=4)
    assert row_measured["data_quality_score"] == min(scores)

    # Some or all unmeasured
    unmeasured_observations = tuple(
        StoreDayObservation(
            store_id="store-1",
            business_date=start + timedelta(days=i),
            actual_revenue=10_000.0,
            data_quality_score=scores[i] if i != 3 else None,
            source_snapshot_ids=(f"snap-{i}",),
        )
        for i in range(28)
    )
    forecast_input_unmeasured = ForecastInput(
        tenant_id=TENANT_ID,
        store_id="store-1",
        observations=unmeasured_observations,
        prediction_origin_time=origin,
    )
    row_unmeasured = _feature_row(forecast_input_unmeasured, horizon_weeks=4)
    assert row_unmeasured["data_quality_score"] is None
