from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from models.shared_ml.production_runtime import (
    ProductionExecutionConfigurationError,
    production_execution_required,
)
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
    def __init__(
        self,
        *,
        repository: InMemoryAdLiftRepository | None = None,
        runtime_mode: str | None = None,
    ) -> None:
        self.production_required = production_execution_required(runtime_mode)
        strict_production_composition = runtime_mode is not None and self.production_required
        if strict_production_composition and (
            repository is None or isinstance(repository, InMemoryAdLiftRepository)
        ):
            raise ProductionExecutionConfigurationError(
                "AdLift production requires an injected durable repository"
            )
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
                if not self.production_required
                else run_incrementality(
                    campaign,
                    generated_at=generated_at,
                    require_statsmodels=True,
                )
            )
            for campaign in campaigns
        )
        return AdLiftResult(reports=reports)


__all__ = ["AdLiftResult", "AdLiftService"]
