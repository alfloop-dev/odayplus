from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from modules.sitescore.domain.scoring import (
    SITESCORE_MODEL_VERSION,
    SiteScoreFeatureInput,
    SiteScoreReport,
    score_sites,
)
from modules.sitescore.infrastructure.repositories import InMemorySiteScoreRepository
from shared.domain import Prediction, PredictionRun, SiteScoreRun


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
        saved_reports = [self.repository.save_report(report) for report in reports]

        if saved_reports:
            run_id = f"pred-run-sitescore-{uuid4()}"
            origin = prediction_origin_time or datetime.now(UTC)
            run = PredictionRun(
                prediction_run_id=run_id,
                model_version_id=SITESCORE_MODEL_VERSION,
                feature_snapshot_time=origin,
                prediction_origin_time=origin,
                prediction_horizon="m12",
                run_status="succeeded",
            )
            self.repository.save_prediction_run(run)

            for r in saved_reports:
                run_summary = SiteScoreRun(
                    sitescore_run_id=r.sitescore_run_id,
                    candidate_site_id=r.candidate_site_id,
                    target_format_code=r.target_format_code,
                    prediction_run_id=run_id,
                    m1_p10=r.m1.p10,
                    m1_p50=r.m1.p50,
                    m1_p90=r.m1.p90,
                    m3_p10=r.m3.p10,
                    m3_p50=r.m3.p50,
                    m3_p90=r.m3.p90,
                    m6_p10=r.m6.p10,
                    m6_p50=r.m6.p50,
                    m6_p90=r.m6.p90,
                    m12_p10=r.m12.p10,
                    m12_p50=r.m12.p50,
                    m12_p90=r.m12.p90,
                    payback_p50_months=r.payback_p50_months,
                    decision_recommendation=r.recommendation.value.lower(),
                )
                self.repository.save_sitescore_run(run_summary)

                pred = Prediction(
                    prediction_run_id=run_id,
                    entity_type="candidate_site",
                    entity_id=r.candidate_site_id,
                    target_name="revenue",
                    p10_value=r.m12.p10,
                    p50_value=r.m12.p50,
                    p90_value=r.m12.p90,
                    unit="TWD",
                    confidence=r.confidence,
                )
                self.repository.save_prediction(pred)

        return saved_reports


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
