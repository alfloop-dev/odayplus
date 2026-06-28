from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from modules.forecastops.domain.forecasting import (
    Alert,
    ForecastInput,
    ForecastOutput,
    ForecastSeries,
    InterventionHandoff,
    StoreDayObservation,
    build_store_timeseries,
    forecast_stores,
)
from modules.forecastops.infrastructure.repositories import InMemoryForecastOpsRepository


@dataclass(frozen=True)
class ForecastOpsResult:
    forecasts: tuple[ForecastOutput, ...]
    alerts: tuple[Alert, ...]
    handoffs: tuple[InterventionHandoff, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "forecasts": [forecast.to_dict() for forecast in self.forecasts],
            "alerts": [alert.to_dict() for alert in self.alerts],
            "handoffs": [handoff.to_dict() for handoff in self.handoffs],
        }


class ForecastOpsService:
    def __init__(self, *, repository: InMemoryForecastOpsRepository | None = None) -> None:
        self.repository = repository or InMemoryForecastOpsRepository()

    def ingest_timeseries(
        self, observations: Iterable[StoreDayObservation | Mapping[str, Any]]
    ) -> list[ForecastSeries]:
        series = build_store_timeseries(observations)
        return [self.repository.save_series(item) for item in series]

    def forecast(
        self,
        inputs: Iterable[ForecastInput | Mapping[str, Any]],
        *,
        prediction_origin_time: datetime | None = None,
        scored_at: datetime | None = None,
    ) -> ForecastOpsResult:
        forecasts, alerts, handoffs = forecast_stores(
            inputs,
            prediction_origin_time=prediction_origin_time,
            scored_at=scored_at,
        )
        return ForecastOpsResult(
            forecasts=tuple(self.repository.save_forecast(forecast) for forecast in forecasts),
            alerts=tuple(self.repository.save_alert(alert) for alert in alerts),
            handoffs=tuple(self.repository.save_handoff(handoff) for handoff in handoffs),
        )


__all__ = ["ForecastOpsResult", "ForecastOpsService"]
