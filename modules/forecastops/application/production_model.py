"""ForecastOps adapter for approved registered OSS estimator artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from statistics import fmean
from typing import Any

from models.shared_ml.production_runtime import (
    ModelInferenceResult,
    ProductionModelInputError,
    ProductionModelRuntime,
)
from modules.forecastops.domain.forecasting import (
    FORECAST_HORIZON_WEEKS,
    ForecastBand,
    ForecastEngineResult,
    ForecastInput,
)
from modules.forecastops.model_contract import (
    FORECASTOPS_FEATURE_SCHEMA_ID,
    FORECASTOPS_LATEST_OBSERVATION_LAG_DAYS,
    FORECASTOPS_MIN_HISTORY_DAYS,
    FORECASTOPS_MODEL_FEATURES,
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
            expected_feature_schema_version=FORECASTOPS_FEATURE_SCHEMA_ID,
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
    tenant_id = (forecast_input.tenant_id or "").strip()
    if not tenant_id:
        raise ProductionModelInputError("forecastops: production inference requires tenant_id")
    observations = sorted(
        forecast_input.observations,
        key=lambda item: item.business_date,
    )
    origin = forecast_input.prediction_origin_time
    if origin.tzinfo is None:
        origin = origin.replace(tzinfo=UTC)
    else:
        origin = origin.astimezone(UTC)
    expected_latest_date = origin.date() - timedelta(
        days=FORECASTOPS_LATEST_OBSERVATION_LAG_DAYS
    )
    if any(item.business_date >= origin.date() for item in observations):
        raise ProductionModelInputError(
            "forecastops: observations on or after prediction origin are prohibited"
        )
    if len(observations) < FORECASTOPS_MIN_HISTORY_DAYS:
        raise ProductionModelInputError(
            "forecastops: production inference requires at least "
            f"{FORECASTOPS_MIN_HISTORY_DAYS} days of live history"
        )
    observations = observations[-FORECASTOPS_MIN_HISTORY_DAYS:]
    latest_business_date = observations[-1].business_date
    if latest_business_date != expected_latest_date:
        relation = "stale" if latest_business_date < expected_latest_date else "future"
        raise ProductionModelInputError(
            "forecastops: production inference requires history through "
            f"{expected_latest_date.isoformat()}; latest observation is "
            f"{latest_business_date.isoformat()} ({relation})"
        )
    for previous, current in zip(observations, observations[1:], strict=False):
        if (current.business_date - previous.business_date).days != 1:
            raise ProductionModelInputError(
                "forecastops: production inference requires contiguous daily history"
            )
    if any(not item.source_snapshot_ids for item in observations):
        raise ProductionModelInputError(
            "forecastops: production inference requires source lineage for every history day"
        )

    latest = observations[-1]
    feature_snapshot_time = datetime.combine(
        latest.business_date + timedelta(days=1),
        time.min,
        tzinfo=UTC,
    )
    if feature_snapshot_time > origin:
        raise ProductionModelInputError(
            "forecastops: feature snapshot cannot be after prediction origin"
        )
    revenues = [float(item.actual_revenue) for item in observations]
    source_snapshot_ids = sorted(
        {snapshot_id for item in observations for snapshot_id in item.source_snapshot_ids}
    )
    model_features: dict[str, Any] = {
        "horizon_weeks": horizon_weeks,
        "revenue_lag_1": revenues[-1],
        "revenue_lag_7": revenues[-7],
        "rolling_mean_28": fmean(revenues),
        "rolling_mean_7": fmean(revenues[-7:]),
        "store_id": forecast_input.store_id,
        "tenant_id": tenant_id,
    }
    if tuple(model_features) != FORECASTOPS_MODEL_FEATURES:
        raise RuntimeError("ForecastOps runtime feature order violates its contract")
    return {
        **model_features,
        "horizon_weeks": horizon_weeks,
        "horizon_days": horizon_weeks * 7,
        "data_quality_score": (
            None
            if any(item.data_quality_score is None for item in observations)
            else min(float(item.data_quality_score) for item in observations)
        ),
        "site_score_baseline_p50": latest.site_score_baseline_p50,
        "observation_count": len(observations),
        "feature_snapshot_time": feature_snapshot_time.isoformat(),
        "prediction_origin_time": origin.isoformat(),
        "view_version": FORECASTOPS_FEATURE_SCHEMA_ID,
        "source_snapshot_ids": source_snapshot_ids,
    }


__all__ = ["RegisteredEstimatorForecastEngine"]
