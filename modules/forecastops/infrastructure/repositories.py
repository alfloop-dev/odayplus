from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from modules.forecastops.domain.forecasting import (
    Alert,
    ForecastOutput,
    ForecastSeries,
    InterventionHandoff,
)
from shared.domain import ForecastOutput as CanonicalForecastOutput
from shared.domain import Prediction, PredictionRun


@dataclass
class InMemoryForecastOpsRepository:
    _series: dict[str, ForecastSeries] = field(default_factory=dict)
    _forecast_history: dict[str, list[ForecastOutput]] = field(default_factory=dict)
    _alerts: dict[str, Alert] = field(default_factory=dict)
    _handoffs: dict[str, InterventionHandoff] = field(default_factory=dict)
    _prediction_runs: dict[str, PredictionRun] = field(default_factory=dict)
    _predictions: dict[str, list[Prediction]] = field(default_factory=dict)
    _canonical_forecasts: dict[str, CanonicalForecastOutput] = field(default_factory=dict)

    def save_series(self, series: ForecastSeries) -> ForecastSeries:
        self._series[series.store_id] = series
        return series

    def list_series(self) -> list[ForecastSeries]:
        return list(self._series.values())

    def get_series(self, store_id: str) -> ForecastSeries | None:
        return self._series.get(store_id)

    def save_forecast(self, forecast: ForecastOutput) -> ForecastOutput:
        versions = self._forecast_history.setdefault(forecast.store_id, [])
        versioned = forecast.with_version(
            forecast_version=len(versions) + 1,
            forecast_output_id=f"forecast-output-{uuid4()}",
        )
        versions.append(versioned)
        return versioned

    def latest_forecasts(self) -> list[ForecastOutput]:
        return [versions[-1] for versions in self._forecast_history.values() if versions]

    def history(self, store_id: str) -> list[ForecastOutput]:
        return list(self._forecast_history.get(store_id, []))

    def save_alert(self, alert: Alert) -> Alert:
        self._alerts[alert.alert_id] = alert
        return alert

    def list_alerts(self) -> list[Alert]:
        return list(self._alerts.values())

    def save_handoff(self, handoff: InterventionHandoff) -> InterventionHandoff:
        self._handoffs[handoff.handoff_id] = handoff
        return handoff

    def list_handoffs(self) -> list[InterventionHandoff]:
        return list(self._handoffs.values())

    def save_prediction_run(self, run: PredictionRun) -> PredictionRun:
        self._prediction_runs[run.prediction_run_id] = run
        return run

    def get_prediction_run(self, prediction_run_id: str) -> PredictionRun | None:
        return self._prediction_runs.get(prediction_run_id)

    def save_prediction(self, prediction: Prediction) -> Prediction:
        self._predictions.setdefault(prediction.prediction_run_id, []).append(prediction)
        return prediction

    def get_predictions(self, prediction_run_id: str) -> list[Prediction]:
        return list(self._predictions.get(prediction_run_id, []))

    def save_canonical_forecast(self, forecast: CanonicalForecastOutput) -> CanonicalForecastOutput:
        self._canonical_forecasts[forecast.forecast_output_id] = forecast
        return forecast

    def get_canonical_forecast(self, forecast_output_id: str) -> CanonicalForecastOutput | None:
        return self._canonical_forecasts.get(forecast_output_id)


__all__ = ["InMemoryForecastOpsRepository"]
