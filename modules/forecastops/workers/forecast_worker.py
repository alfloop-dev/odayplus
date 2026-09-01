from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from models.shared_ml.production_runtime import ProductionModelRuntime
from modules.forecastops.application.forecasting import ForecastOpsService
from modules.forecastops.domain.forecasting import ForecastEngine, ForecastInput
from modules.forecastops.infrastructure.repositories import ForecastOpsRepository
from modules.forecastops.runtime import forecastops_production_required
from shared.governance import DecisionPolicyRepository


@dataclass(frozen=True)
class ForecastOpsBatchResult:
    job_id: str
    status: str
    result: dict[str, Any]
    completed_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            **self.result,
            "completed_at": self.completed_at.isoformat(),
        }


class ForecastOpsForecastWorker:
    def __init__(
        self,
        *,
        repository: ForecastOpsRepository | None = None,
        engine: str | ForecastEngine | None = None,
        model_name: str | None = None,
        engine_options: Mapping[str, Any] | None = None,
        model_runtime: ProductionModelRuntime | None = None,
        runtime_mode: str | None = None,
        policy_repository: DecisionPolicyRepository | None = None,
    ) -> None:
        production_required = forecastops_production_required(runtime_mode)
        selected_engine = engine
        selected_model = model_name
        if not production_required:
            selected_engine = selected_engine or os.getenv("ODP_FORECAST_ENGINE") or None
            selected_model = selected_model or os.getenv("ODP_FORECAST_MODEL") or None
        self.service = ForecastOpsService(
            repository=repository,
            engine=selected_engine,
            model_name=selected_model,
            engine_options=engine_options,
            model_runtime=model_runtime,
            runtime_mode=runtime_mode,
            policy_repository=policy_repository,
        )

    def run(
        self,
        *,
        inputs: Iterable[ForecastInput | Mapping[str, Any]],
        job_id: str | None = None,
        prediction_origin_time: datetime | str | None = None,
        scored_at: datetime | str | None = None,
        engine: str | ForecastEngine | None = None,
        model_name: str | None = None,
        engine_options: Mapping[str, Any] | None = None,
        policy_repository: DecisionPolicyRepository | None = None,
    ) -> ForecastOpsBatchResult:
        completed_at = (
            _parse_datetime(scored_at)
            if scored_at is not None
            else datetime.now(UTC)
        )
        result = self.service.forecast(
            inputs,
            prediction_origin_time=_parse_datetime(prediction_origin_time)
            if prediction_origin_time is not None
            else None,
            prediction_run_id=(
                f"pred-run-forecast-{job_id}" if job_id is not None else None
            ),
            scored_at=completed_at,
            engine=engine,
            model_name=model_name,
            engine_options=engine_options,
            policy_repository=policy_repository,
        )
        return ForecastOpsBatchResult(
            job_id=job_id or f"forecastops-forecast-{uuid4()}",
            status="succeeded",
            result=result.to_dict(),
            completed_at=completed_at,
        )


def run_forecastops_batch_forecast(
    *,
    inputs: Iterable[ForecastInput | Mapping[str, Any]],
    job_id: str | None = None,
    prediction_origin_time: datetime | str | None = None,
    scored_at: datetime | str | None = None,
    repository: ForecastOpsRepository | None = None,
    engine: str | ForecastEngine | None = None,
    model_name: str | None = None,
    engine_options: Mapping[str, Any] | None = None,
    model_runtime: ProductionModelRuntime | None = None,
    runtime_mode: str | None = None,
    policy_repository: DecisionPolicyRepository | None = None,
) -> ForecastOpsBatchResult:
    return ForecastOpsForecastWorker(
        repository=repository,
        engine=engine,
        model_name=model_name,
        engine_options=engine_options,
        model_runtime=model_runtime,
        runtime_mode=runtime_mode,
        policy_repository=policy_repository,
    ).run(
        inputs=inputs,
        job_id=job_id,
        prediction_origin_time=prediction_origin_time,
        scored_at=scored_at,
    )


def _parse_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


__all__ = [
    "ForecastOpsBatchResult",
    "ForecastOpsForecastWorker",
    "run_forecastops_batch_forecast",
]
