from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from models.shared_ml.production_runtime import (
    ModelInferenceResult,
    ProductionModelRuntime,
    production_model_execution_required,
    require_production_runtime,
)
from modules.sitescore.domain.scoring import (
    SITESCORE_FEATURE_VERSION,
    SITESCORE_MODEL_VERSION,
    RevenuePredictionBand,
    SiteScoreFeatureInput,
    SiteScoreReport,
    score_sites,
    score_sites_from_model_predictions,
)
from modules.sitescore.infrastructure.repositories import InMemorySiteScoreRepository
from shared.domain import Prediction, PredictionRun, SiteScoreRun


@dataclass(frozen=True)
class SiteScoreExecution:
    reports: tuple[SiteScoreReport, ...]
    model_inference: ModelInferenceResult | None


class SiteScoreReportService:
    """Scores candidate sites and persists versioned reports.

    POC scoring may use the deterministic baseline. Production scoring requires
    an executable registered OSS model and never falls back to that baseline.
    Persistence assigns the durable, monotonically increasing
    ``report_version`` per candidate site (see repository).
    """

    def __init__(
        self,
        *,
        repository: InMemorySiteScoreRepository | None = None,
        model_runtime: ProductionModelRuntime | None = None,
        require_production_model: bool | None = None,
    ) -> None:
        self.repository = repository or InMemorySiteScoreRepository()
        self.model_runtime = model_runtime
        self.require_production_model = (
            production_model_execution_required()
            if require_production_model is None
            else require_production_model
        )

    def score_candidates(
        self,
        features: Iterable[SiteScoreFeatureInput | Mapping[str, Any]],
        *,
        prediction_origin_time: datetime | None = None,
        scored_at: datetime | None = None,
    ) -> list[SiteScoreReport]:
        return list(
            self.score_candidates_with_execution(
                features,
                prediction_origin_time=prediction_origin_time,
                scored_at=scored_at,
            ).reports
        )

    def score_candidates_with_execution(
        self,
        features: Iterable[SiteScoreFeatureInput | Mapping[str, Any]],
        *,
        prediction_origin_time: datetime | None = None,
        scored_at: datetime | None = None,
    ) -> SiteScoreExecution:
        feature_rows = list(features)
        inference: ModelInferenceResult | None = None
        if self.require_production_model:
            runtime = require_production_runtime(
                self.model_runtime,
                service="sitescore",
            )
            inference = runtime.infer(
                service="sitescore",
                rows=[_feature_mapping(feature) for feature in feature_rows],
                expected_feature_schema_version=SITESCORE_FEATURE_VERSION,
            )
            reports = score_sites_from_model_predictions(
                feature_rows,
                [
                    RevenuePredictionBand(p10=lower, p50=point, p90=upper)
                    for lower, point, upper in zip(
                        inference.lower,
                        inference.point,
                        inference.upper,
                        strict=True,
                    )
                ],
                model_version=inference.binding.model_id,
                prediction_origin_time=prediction_origin_time,
                scored_at=scored_at,
            )
        else:
            reports = score_sites(
                feature_rows,
                prediction_origin_time=prediction_origin_time,
                scored_at=scored_at,
            )
        saved_reports = self._persist_reports(
            reports,
            prediction_origin_time=prediction_origin_time,
            model_version_id=(
                inference.binding.model_id if inference else SITESCORE_MODEL_VERSION
            ),
        )
        return SiteScoreExecution(
            reports=tuple(saved_reports),
            model_inference=inference,
        )

    def _persist_reports(
        self,
        reports: Iterable[SiteScoreReport],
        *,
        prediction_origin_time: datetime | None,
        model_version_id: str,
    ) -> list[SiteScoreReport]:
        saved_reports = [self.repository.save_report(report) for report in reports]

        if saved_reports:
            run_id = f"pred-run-sitescore-{uuid4()}"
            origin = prediction_origin_time or datetime.now(UTC)
            run = PredictionRun(
                prediction_run_id=run_id,
                model_version_id=model_version_id,
                feature_snapshot_time=origin,
                prediction_origin_time=origin,
                prediction_horizon="m12",
                run_status="succeeded",
            )
            self.repository.save_prediction_run(run)

            for report in saved_reports:
                run_summary = SiteScoreRun(
                    sitescore_run_id=report.sitescore_run_id,
                    candidate_site_id=report.candidate_site_id,
                    target_format_code=report.target_format_code,
                    prediction_run_id=run_id,
                    m1_p10=report.m1.p10,
                    m1_p50=report.m1.p50,
                    m1_p90=report.m1.p90,
                    m3_p10=report.m3.p10,
                    m3_p50=report.m3.p50,
                    m3_p90=report.m3.p90,
                    m6_p10=report.m6.p10,
                    m6_p50=report.m6.p50,
                    m6_p90=report.m6.p90,
                    m12_p10=report.m12.p10,
                    m12_p50=report.m12.p50,
                    m12_p90=report.m12.p90,
                    payback_p50_months=report.payback_p50_months,
                    decision_recommendation=report.recommendation.value.lower(),
                )
                self.repository.save_sitescore_run(run_summary)

                prediction = Prediction(
                    prediction_run_id=run_id,
                    entity_type="candidate_site",
                    entity_id=report.candidate_site_id,
                    target_name="revenue",
                    p10_value=report.m12.p10,
                    p50_value=report.m12.p50,
                    p90_value=report.m12.p90,
                    unit="TWD",
                    confidence=report.confidence,
                )
                self.repository.save_prediction(prediction)

        return saved_reports


def _feature_mapping(
    feature: SiteScoreFeatureInput | Mapping[str, Any],
) -> Mapping[str, Any]:
    if isinstance(feature, Mapping):
        return feature
    return dict(feature.__dict__)


def run_sitescore_reports(
    features: Iterable[SiteScoreFeatureInput | Mapping[str, Any]],
    *,
    repository: InMemorySiteScoreRepository | None = None,
    prediction_origin_time: datetime | None = None,
    scored_at: datetime | None = None,
    model_runtime: ProductionModelRuntime | None = None,
    require_production_model: bool | None = None,
) -> list[SiteScoreReport]:
    return SiteScoreReportService(
        repository=repository,
        model_runtime=model_runtime,
        require_production_model=require_production_model,
    ).score_candidates(
        features,
        prediction_origin_time=prediction_origin_time,
        scored_at=scored_at,
    )


__all__ = ["SiteScoreExecution", "SiteScoreReportService", "run_sitescore_reports"]
