"""ForecastOps adapter for approved registered OSS estimator artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from statistics import fmean
from typing import Any

from models.shared_ml.production_runtime import (
    ModelInferenceResult,
    ProductionModelRuntime,
)
from modules.forecastops.domain.forecasting import (
    FORECAST_HORIZON_WEEKS,
    FORECASTOPS_FEATURE_VERSION,
    ForecastBand,
    ForecastEngineResult,
    ForecastInput,
)


@dataclass
class RegisteredEstimatorForecastEngine:
    """Execute ForecastOps horizons through the approved MLflow artifact."""

    runtime: ProductionModelRuntime
    engine_name: str = "mlflow_registered_oss"
    model_name: str = "forecastops"
    last_inference: ModelInferenceResult | None = field(default=None, init=False)

    def fit_predict(self, forecast_input: ForecastInput) -> ForecastEngineResult:
        if not forecast_input.observations:
            rows: list[dict[str, Any]] = []
        else:
            rows = [
                _feature_row(forecast_input, horizon_weeks=horizon)
                for horizon in FORECAST_HORIZON_WEEKS
            ]
        inference = self.runtime.infer(
            service="forecastops",
            rows=rows,
            expected_feature_schema_version=FORECASTOPS_FEATURE_VERSION,
        )
        self.last_inference = inference
        bands = {
            horizon: ForecastBand(p10=lower, p50=point, p90=upper)
            for horizon, lower, point, upper in zip(
                FORECAST_HORIZON_WEEKS,
                inference.lower,
                inference.point,
                inference.upper,
                strict=True,
            )
        }
        return ForecastEngineResult(
            bands=bands,
            engine_name=inference.engine,
            model_name=inference.binding.model_name,
            model_version=inference.binding.model_id,
            metadata=inference.to_audit_metadata(),
        )


def _feature_row(
    forecast_input: ForecastInput,
    *,
    horizon_weeks: int,
) -> dict[str, Any]:
    observations = list(forecast_input.observations)
    latest = observations[-1]
    latest_date = datetime.combine(latest.business_date, time.min, tzinfo=UTC)
    revenues = [float(item.actual_revenue) for item in observations]
    cycles = [float(item.machine_cycles) for item in observations]
    source_snapshot_ids = sorted(
        {
            snapshot_id
            for item in observations
            for snapshot_id in item.source_snapshot_ids
        }
    )
    elapsed_days = max(
        1,
        (observations[-1].business_date - observations[0].business_date).days,
    )
    return {
        "store_id": forecast_input.store_id,
        "horizon_weeks": horizon_weeks,
        "horizon_days": horizon_weeks * 7,
        "latest_actual_revenue": revenues[-1],
        "trailing_mean_revenue": fmean(revenues),
        "trailing_7d_mean_revenue": fmean(revenues[-7:]),
        "trailing_28d_mean_revenue": fmean(revenues[-28:]),
        "revenue_trend_per_day": (revenues[-1] - revenues[0]) / elapsed_days,
        "latest_machine_cycles": cycles[-1],
        "trailing_mean_machine_cycles": fmean(cycles),
        "data_quality_score": min(
            float(item.data_quality_score) for item in observations
        ),
        "site_score_baseline_p50": latest.site_score_baseline_p50,
        "observation_count": len(observations),
        "feature_snapshot_time": latest_date.isoformat(),
        "prediction_origin_time": forecast_input.prediction_origin_time.isoformat(),
        "view_version": FORECASTOPS_FEATURE_VERSION,
        "source_snapshot_ids": source_snapshot_ids,
    }


__all__ = ["RegisteredEstimatorForecastEngine"]
