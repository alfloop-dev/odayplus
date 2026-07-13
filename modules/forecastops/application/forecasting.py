from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from modules.forecastops.domain.forecasting import (
    Alert,
    ForecastInput,
    ForecastOpsNotFoundError,
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
        from datetime import UTC
        from uuid import uuid4

        from modules.forecastops.domain.forecasting import FORECASTOPS_MODEL_VERSION
        from shared.domain import ForecastOutput as CanonicalForecastOutput
        from shared.domain import Prediction, PredictionRun

        run_id = f"pred-run-forecast-{uuid4()}"
        forecasts, alerts, handoffs = forecast_stores(
            inputs,
            prediction_origin_time=prediction_origin_time,
            scored_at=scored_at,
            prediction_run_id=run_id,
        )
        saved_forecasts = tuple(self.repository.save_forecast(forecast) for forecast in forecasts)

        if saved_forecasts:
            origin = prediction_origin_time or datetime.now(UTC)
            run = PredictionRun(
                prediction_run_id=run_id,
                model_version_id=FORECASTOPS_MODEL_VERSION,
                feature_snapshot_time=origin,
                prediction_origin_time=origin,
                prediction_horizon="w24",
                run_status="succeeded",
            )
            self.repository.save_prediction_run(run)

            for f in saved_forecasts:
                canonical_forecast = CanonicalForecastOutput(
                    forecast_output_id=f.forecast_output_id,
                    store_id=f.store_id,
                    prediction_run_id=run_id,
                    horizon_days=f.horizon_days,
                    target_metric=f.target_metric,
                    p10=f.p10,
                    p50=f.p50,
                    p90=f.p90,
                    trajectory_class=f.trajectory_class,
                    turning_point_probability=f.turning_point_probability,
                    sitescore_gap_ratio=f.sitescore_gap_ratio,
                )
                self.repository.save_canonical_forecast(canonical_forecast)

                pred = Prediction(
                    prediction_run_id=run_id,
                    entity_type="store",
                    entity_id=f.store_id,
                    target_name="revenue",
                    p10_value=f.p10,
                    p50_value=f.p50,
                    p90_value=f.p90,
                    unit="TWD",
                )
                self.repository.save_prediction(pred)

        return ForecastOpsResult(
            forecasts=saved_forecasts,
            alerts=tuple(self.repository.save_alert(alert) for alert in alerts),
            handoffs=tuple(self.repository.save_handoff(handoff) for handoff in handoffs),
        )

    def acknowledge_alert(
        self,
        alert_id: str,
        *,
        actor: str,
        note: str | None = None,
        now: datetime | None = None,
    ) -> Alert:
        """Acknowledge a persisted four-light alert and persist the acknowledgement."""

        alert = self.repository.get_alert(alert_id)
        if alert is None:
            raise ForecastOpsNotFoundError(f"alert {alert_id} not found")
        acknowledged = alert.acknowledge(actor=actor, note=note, now=now or datetime.now(UTC))
        return self.repository.save_alert(acknowledged)

    def execute_handoff(
        self,
        handoff_id: str,
        *,
        actor: str,
        intervention_id: str | None = None,
        now: datetime | None = None,
    ) -> InterventionHandoff:
        """Dispatch a proposed intervention handoff, linking the opened case."""

        handoff = self.repository.get_handoff(handoff_id)
        if handoff is None:
            raise ForecastOpsNotFoundError(f"handoff {handoff_id} not found")
        executed = handoff.execute(
            actor=actor, intervention_id=intervention_id, now=now or datetime.now(UTC)
        )
        return self.repository.save_handoff(executed)


__all__ = ["ForecastOpsResult", "ForecastOpsService"]
