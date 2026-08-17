"""Canonical ForecastOps training-to-runtime feature contract."""

from __future__ import annotations

FORECASTOPS_FEATURE_SCHEMA_ID = "forecast-training-view-v2"
FORECASTOPS_HORIZON_WEEKS = (4, 8, 12, 24)
FORECASTOPS_LABEL_NAME = "horizon_average_daily_net_revenue"

# FeaturePipelineRunner persists names in lexical order. Keep this tuple in the
# exact persisted encoder order so training and runtime share one contract.
FORECASTOPS_MODEL_FEATURES = (
    "horizon_weeks",
    "revenue_lag_1",
    "revenue_lag_7",
    "rolling_mean_28",
    "rolling_mean_7",
    "store_id",
    "tenant_id",
)

FORECASTOPS_MIN_HISTORY_DAYS = 28
FORECASTOPS_LATEST_OBSERVATION_LAG_DAYS = 1

__all__ = [
    "FORECASTOPS_FEATURE_SCHEMA_ID",
    "FORECASTOPS_HORIZON_WEEKS",
    "FORECASTOPS_LABEL_NAME",
    "FORECASTOPS_LATEST_OBSERVATION_LAG_DAYS",
    "FORECASTOPS_MIN_HISTORY_DAYS",
    "FORECASTOPS_MODEL_FEATURES",
]
