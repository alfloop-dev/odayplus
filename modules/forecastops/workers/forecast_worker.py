from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from modules.forecastops.application.forecasting import ForecastOpsService
from modules.forecastops.domain.forecasting import ForecastInput
from modules.forecastops.infrastructure.repositories import InMemoryForecastOpsRepository


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
    def __init__(self, *, repository: InMemoryForecastOpsRepository | None = None) -> None:
        self.service = ForecastOpsService(repository=repository)

    def run(
        self,
        *,
        inputs: Iterable[ForecastInput | Mapping[str, Any]],
        job_id: str | None = None,
        prediction_origin_time: datetime | str | None = None,
    ) -> ForecastOpsBatchResult:
        completed_at = datetime.now(UTC)
        result = self.service.forecast(
            inputs,
            prediction_origin_time=_parse_datetime(prediction_origin_time)
            if prediction_origin_time is not None
            else None,
            scored_at=completed_at,
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
    repository: InMemoryForecastOpsRepository | None = None,
) -> ForecastOpsBatchResult:
    return ForecastOpsForecastWorker(repository=repository).run(
        inputs=inputs,
        job_id=job_id,
        prediction_origin_time=prediction_origin_time,
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
