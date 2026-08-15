from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Protocol, runtime_checkable

from modules.forecastops.domain.forecasting import (
    Alert,
    ForecastOutput,
    ForecastSeries,
    InterventionHandoff,
)
from shared.domain import ForecastOutput as CanonicalForecastOutput
from shared.domain import Prediction, PredictionRun


def _require_tenant_id(tenant_id: str | None) -> str:
    normalized = str(tenant_id or "").strip()
    if not normalized:
        raise ValueError("tenant_id is required for ForecastOps persistence")
    return normalized


@runtime_checkable
class ForecastOpsRepository(Protocol):
    def save_series(self, series: ForecastSeries) -> ForecastSeries: ...
    def list_series(self, tenant_id: str) -> list[ForecastSeries]: ...
    def get_series(self, tenant_id: str, store_id: str) -> ForecastSeries | None: ...
    def save_forecast(self, forecast: ForecastOutput) -> ForecastOutput: ...
    def latest_forecasts(self, tenant_id: str) -> list[ForecastOutput]: ...
    def history(self, tenant_id: str, store_id: str) -> list[ForecastOutput]: ...
    def save_alert(self, alert: Alert) -> Alert: ...
    def list_alerts(self, tenant_id: str) -> list[Alert]: ...
    def list_alerts_by_store(self, tenant_id: str, store_id: str) -> list[Alert]: ...
    def get_alert(self, tenant_id: str, alert_id: str) -> Alert | None: ...
    def save_handoff(self, handoff: InterventionHandoff) -> InterventionHandoff: ...
    def list_handoffs(self, tenant_id: str) -> list[InterventionHandoff]: ...
    def get_handoff(self, tenant_id: str, handoff_id: str) -> InterventionHandoff | None: ...
    def save_prediction_run(self, tenant_id: str, run: PredictionRun) -> PredictionRun: ...
    def get_prediction_run(
        self, tenant_id: str, prediction_run_id: str
    ) -> PredictionRun | None: ...
    def save_prediction(self, tenant_id: str, prediction: Prediction) -> Prediction: ...
    def get_predictions(self, tenant_id: str, prediction_run_id: str) -> list[Prediction]: ...
    def save_canonical_forecast(
        self, tenant_id: str, forecast: CanonicalForecastOutput
    ) -> CanonicalForecastOutput: ...
    def get_canonical_forecast(
        self, tenant_id: str, forecast_output_id: str
    ) -> CanonicalForecastOutput | None: ...


@dataclass
class InMemoryForecastOpsRepository:
    _series: dict[tuple[str, str], ForecastSeries] = field(default_factory=dict)
    _forecast_history: dict[tuple[str, str], list[ForecastOutput]] = field(default_factory=dict)
    _alerts: dict[tuple[str, str], Alert] = field(default_factory=dict)
    _handoffs: dict[tuple[str, str], InterventionHandoff] = field(default_factory=dict)
    _prediction_runs: dict[tuple[str, str], PredictionRun] = field(default_factory=dict)
    _predictions: dict[tuple[str, str], list[Prediction]] = field(default_factory=dict)
    _canonical_forecasts: dict[tuple[str, str], CanonicalForecastOutput] = field(
        default_factory=dict
    )
    _lock: RLock = field(default_factory=RLock, repr=False)

    def save_series(self, series: ForecastSeries) -> ForecastSeries:
        tenant_id = _require_tenant_id(series.tenant_id)
        self._series[(tenant_id, series.store_id)] = series
        return series

    def list_series(self, tenant_id: str) -> list[ForecastSeries]:
        tenant_id = _require_tenant_id(tenant_id)
        return [
            series
            for (owner_tenant_id, _), series in self._series.items()
            if owner_tenant_id == tenant_id
        ]

    def get_series(self, tenant_id: str, store_id: str) -> ForecastSeries | None:
        tenant_id = _require_tenant_id(tenant_id)
        return self._series.get((tenant_id, store_id))

    def save_forecast(self, forecast: ForecastOutput) -> ForecastOutput:
        tenant_id = _require_tenant_id(forecast.tenant_id)
        with self._lock:
            versions = self._forecast_history.setdefault((tenant_id, forecast.store_id), [])
            existing = next(
                (
                    item
                    for item in versions
                    if item.forecast_output_id == forecast.forecast_output_id
                ),
                None,
            )
            if existing is not None:
                return existing
            versioned = forecast.with_version(
                forecast_version=len(versions) + 1,
                forecast_output_id=forecast.forecast_output_id,
            )
            versions.append(versioned)
            return versioned

    def latest_forecasts(self, tenant_id: str) -> list[ForecastOutput]:
        tenant_id = _require_tenant_id(tenant_id)
        return [
            versions[-1]
            for (owner_tenant_id, _), versions in self._forecast_history.items()
            if owner_tenant_id == tenant_id and versions
        ]

    def history(self, tenant_id: str, store_id: str) -> list[ForecastOutput]:
        tenant_id = _require_tenant_id(tenant_id)
        return list(self._forecast_history.get((tenant_id, store_id), []))

    def save_alert(self, alert: Alert) -> Alert:
        tenant_id = _require_tenant_id(alert.tenant_id)
        self._alerts[(tenant_id, alert.alert_id)] = alert
        return alert

    def list_alerts(self, tenant_id: str) -> list[Alert]:
        tenant_id = _require_tenant_id(tenant_id)
        return [
            alert
            for (owner_tenant_id, _), alert in self._alerts.items()
            if owner_tenant_id == tenant_id
        ]

    def list_alerts_by_store(self, tenant_id: str, store_id: str) -> list[Alert]:
        return [alert for alert in self.list_alerts(tenant_id) if alert.store_id == store_id]

    def get_alert(self, tenant_id: str, alert_id: str) -> Alert | None:
        tenant_id = _require_tenant_id(tenant_id)
        return self._alerts.get((tenant_id, alert_id))

    def save_handoff(self, handoff: InterventionHandoff) -> InterventionHandoff:
        tenant_id = _require_tenant_id(handoff.tenant_id)
        self._handoffs[(tenant_id, handoff.handoff_id)] = handoff
        return handoff

    def list_handoffs(self, tenant_id: str) -> list[InterventionHandoff]:
        tenant_id = _require_tenant_id(tenant_id)
        return [
            handoff
            for (owner_tenant_id, _), handoff in self._handoffs.items()
            if owner_tenant_id == tenant_id
        ]

    def get_handoff(self, tenant_id: str, handoff_id: str) -> InterventionHandoff | None:
        tenant_id = _require_tenant_id(tenant_id)
        return self._handoffs.get((tenant_id, handoff_id))

    def save_prediction_run(self, tenant_id: str, run: PredictionRun) -> PredictionRun:
        tenant_id = _require_tenant_id(tenant_id)
        self._prediction_runs[(tenant_id, run.prediction_run_id)] = run
        return run

    def get_prediction_run(self, tenant_id: str, prediction_run_id: str) -> PredictionRun | None:
        tenant_id = _require_tenant_id(tenant_id)
        return self._prediction_runs.get((tenant_id, prediction_run_id))

    def save_prediction(self, tenant_id: str, prediction: Prediction) -> Prediction:
        tenant_id = _require_tenant_id(tenant_id)
        predictions = self._predictions.setdefault(
            (tenant_id, prediction.prediction_run_id),
            [],
        )
        existing = next(
            (item for item in predictions if item.prediction_id == prediction.prediction_id),
            None,
        )
        if existing is not None:
            return existing
        predictions.append(prediction)
        return prediction

    def get_predictions(self, tenant_id: str, prediction_run_id: str) -> list[Prediction]:
        tenant_id = _require_tenant_id(tenant_id)
        return list(self._predictions.get((tenant_id, prediction_run_id), []))

    def save_canonical_forecast(
        self, tenant_id: str, forecast: CanonicalForecastOutput
    ) -> CanonicalForecastOutput:
        tenant_id = _require_tenant_id(tenant_id)
        self._canonical_forecasts[(tenant_id, forecast.forecast_output_id)] = forecast
        return forecast

    def get_canonical_forecast(
        self, tenant_id: str, forecast_output_id: str
    ) -> CanonicalForecastOutput | None:
        tenant_id = _require_tenant_id(tenant_id)
        return self._canonical_forecasts.get((tenant_id, forecast_output_id))


__all__ = ["ForecastOpsRepository", "InMemoryForecastOpsRepository"]
