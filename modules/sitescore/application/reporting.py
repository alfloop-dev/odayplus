from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from modules.sitescore.domain.scoring import (
    SiteScoreFeatureInput,
    SiteScoreReport,
    score_sites,
)
from modules.sitescore.infrastructure.repositories import InMemorySiteScoreRepository


class SiteScoreReportService:
    """Scores candidate sites and persists versioned reports.

    Scoring is deterministic; persistence assigns the durable, monotonically
    increasing ``report_version`` per candidate site (see repository).
    """

    def __init__(self, *, repository: InMemorySiteScoreRepository | None = None) -> None:
        self.repository = repository or InMemorySiteScoreRepository()

    def score_candidates(
        self,
        features: Iterable[SiteScoreFeatureInput | Mapping[str, Any]],
        *,
        prediction_origin_time: datetime | None = None,
        scored_at: datetime | None = None,
    ) -> list[SiteScoreReport]:
        reports = score_sites(
            features,
            prediction_origin_time=prediction_origin_time,
            scored_at=scored_at,
        )
        return [self.repository.save_report(report) for report in reports]


def run_sitescore_reports(
    features: Iterable[SiteScoreFeatureInput | Mapping[str, Any]],
    *,
    repository: InMemorySiteScoreRepository | None = None,
    prediction_origin_time: datetime | None = None,
    scored_at: datetime | None = None,
) -> list[SiteScoreReport]:
    return SiteScoreReportService(repository=repository).score_candidates(
        features,
        prediction_origin_time=prediction_origin_time,
        scored_at=scored_at,
    )


__all__ = ["SiteScoreReportService", "run_sitescore_reports"]
