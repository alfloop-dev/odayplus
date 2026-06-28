from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from modules.forecastops.domain.forecasting import (
    Alert,
    ForecastOutput,
    ForecastSeries,
    InterventionHandoff,
)


@dataclass
class InMemoryForecastOpsRepository:
    _series: dict[str, ForecastSeries] = field(default_factory=dict)
    _forecast_history: dict[str, list[ForecastOutput]] = field(default_factory=dict)
    _alerts: dict[str, Alert] = field(default_factory=dict)
    _handoffs: dict[str, InterventionHandoff] = field(default_factory=dict)

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


__all__ = ["InMemoryForecastOpsRepository"]
