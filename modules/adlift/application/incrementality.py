from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from modules.adlift.domain.incrementality import (
    AdCampaign,
    IncrementalityReport,
    run_incrementality,
)
from modules.adlift.infrastructure.repositories import InMemoryAdLiftRepository


@dataclass(frozen=True)
class AdLiftResult:
    reports: tuple[IncrementalityReport, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"reports": [report.to_dict() for report in self.reports]}


class AdLiftService:
    def __init__(self, *, repository: InMemoryAdLiftRepository | None = None) -> None:
        self.repository = repository or InMemoryAdLiftRepository()

    def evaluate(
        self,
        campaigns: Iterable[AdCampaign | Mapping[str, Any]],
        *,
        generated_at: datetime | None = None,
    ) -> AdLiftResult:
        reports = tuple(
            self.repository.save_report(
                run_incrementality(campaign, generated_at=generated_at)
            )
            for campaign in campaigns
        )
        return AdLiftResult(reports=reports)


__all__ = ["AdLiftResult", "AdLiftService"]
