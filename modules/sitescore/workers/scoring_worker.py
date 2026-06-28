from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from modules.sitescore.application.reporting import SiteScoreReportService
from modules.sitescore.domain.scoring import SiteScoreFeatureInput, SiteScoreReport
from modules.sitescore.infrastructure.repositories import InMemorySiteScoreRepository


@dataclass(frozen=True)
class SiteScoreBatchScoreResult:
    job_id: str
    status: str
    reports: tuple[SiteScoreReport, ...]
    completed_at: datetime
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "reports": [report.to_dict() for report in self.reports],
            "summaries": [report.to_summary_dict() for report in self.reports],
            "completed_at": self.completed_at.isoformat(),
            "warnings": list(self.warnings),
        }


class SiteScoreScoringWorker:
    def __init__(self, *, repository: InMemorySiteScoreRepository | None = None) -> None:
        self.service = SiteScoreReportService(repository=repository)

    def run(
        self,
        *,
        job_id: str | None = None,
        features: Iterable[SiteScoreFeatureInput | Mapping[str, Any]],
        prediction_origin_time: datetime | str | None = None,
    ) -> SiteScoreBatchScoreResult:
        effective_job_id = job_id or f"sitescore-score-{uuid4()}"
        reports = tuple(
            self.service.score_candidates(
                features,
                prediction_origin_time=_parse_datetime(prediction_origin_time)
                if prediction_origin_time is not None
                else None,
            )
        )
        warnings = tuple(
            f"{report.candidate_site_id}: {','.join(report.warnings)}"
            for report in reports
            if report.warnings
        )
        return SiteScoreBatchScoreResult(
            job_id=effective_job_id,
            status="succeeded",
            reports=reports,
            completed_at=datetime.now(UTC),
            warnings=warnings,
        )


def run_sitescore_batch_score(
    *,
    job_id: str | None = None,
    features: Iterable[SiteScoreFeatureInput | Mapping[str, Any]],
    prediction_origin_time: datetime | str | None = None,
    repository: InMemorySiteScoreRepository | None = None,
) -> SiteScoreBatchScoreResult:
    return SiteScoreScoringWorker(repository=repository).run(
        job_id=job_id,
        features=features,
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
    "SiteScoreBatchScoreResult",
    "SiteScoreScoringWorker",
    "run_sitescore_batch_score",
]
