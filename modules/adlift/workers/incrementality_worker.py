from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from modules.adlift.application.incrementality import AdLiftService
from modules.adlift.domain.incrementality import AdCampaign
from modules.adlift.infrastructure.repositories import InMemoryAdLiftRepository


@dataclass(frozen=True)
class AdLiftBatchResult:
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


class AdLiftIncrementalityWorker:
    def __init__(self, *, repository: InMemoryAdLiftRepository | None = None) -> None:
        self.service = AdLiftService(repository=repository)

    def run(
        self,
        *,
        campaigns: Iterable[AdCampaign | Mapping[str, Any]],
        job_id: str | None = None,
        generated_at: datetime | str | None = None,
    ) -> AdLiftBatchResult:
        completed_at = datetime.now(UTC)
        result = self.service.evaluate(
            campaigns,
            generated_at=_parse_datetime(generated_at) if generated_at is not None else None,
        )
        return AdLiftBatchResult(
            job_id=job_id or f"adlift-incrementality-{uuid4()}",
            status="succeeded",
            result=result.to_dict(),
            completed_at=completed_at,
        )


def run_adlift_incrementality_batch(
    *,
    campaigns: Iterable[AdCampaign | Mapping[str, Any]],
    job_id: str | None = None,
    generated_at: datetime | str | None = None,
    repository: InMemoryAdLiftRepository | None = None,
) -> AdLiftBatchResult:
    return AdLiftIncrementalityWorker(repository=repository).run(
        campaigns=campaigns,
        job_id=job_id,
        generated_at=generated_at,
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
    "AdLiftBatchResult",
    "AdLiftIncrementalityWorker",
    "run_adlift_incrementality_batch",
]
