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
from modules.external_data.geo import GeoFeatureSnapshot
from modules.heatzone.domain import (
    HEATZONE_FEATURE_VERSION,
    HeatZoneFeatureInput,
    HeatZoneScoreResult,
    score_heatzones,
    score_heatzones_from_model_predictions,
)


@dataclass(frozen=True)
class HeatZoneBatchScoreResult:
    job_id: str
    status: str
    scores: tuple[HeatZoneScoreResult, ...]
    completed_at: datetime
    warnings: tuple[str, ...] = ()
    model_inference: ModelInferenceResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "scores": [score.to_dict() for score in self.scores],
            "map_features": [score.to_map_feature() for score in self.scores],
            "completed_at": self.completed_at.isoformat(),
            "warnings": list(self.warnings),
        }


class HeatZoneScoringWorker:
    def __init__(
        self,
        *,
        model_runtime: ProductionModelRuntime | None = None,
        require_production_model: bool | None = None,
    ) -> None:
        self.model_runtime = model_runtime
        self.require_production_model = (
            production_model_execution_required()
            if require_production_model is None
            else require_production_model
        )

    def run(
        self,
        *,
        job_id: str | None = None,
        features: Iterable[HeatZoneFeatureInput | GeoFeatureSnapshot | Mapping[str, Any]],
        prediction_origin_time: datetime | str | None = None,
    ) -> HeatZoneBatchScoreResult:
        effective_job_id = job_id or f"heatzone-score-{uuid4()}"
        feature_rows = list(features)
        inference: ModelInferenceResult | None = None
        if self.require_production_model:
            runtime = require_production_runtime(
                self.model_runtime,
                service="heatzone",
            )
            inference = runtime.infer(
                service="heatzone",
                rows=[_feature_mapping(feature) for feature in feature_rows],
                expected_feature_schema_version=HEATZONE_FEATURE_VERSION,
            )
            scores = tuple(
                score_heatzones_from_model_predictions(
                    feature_rows,
                    inference.point,
                    model_version=inference.binding.model_id,
                    prediction_origin_time=_parse_datetime(prediction_origin_time)
                    if prediction_origin_time is not None
                    else None,
                )
            )
        else:
            scores = tuple(
                score_heatzones(
                    feature_rows,
                    prediction_origin_time=_parse_datetime(prediction_origin_time)
                    if prediction_origin_time is not None
                    else None,
                )
            )
        warnings = tuple(
            f"{score.h3_index}: {','.join(score.warnings)}"
            for score in scores
            if score.warnings
        )
        return HeatZoneBatchScoreResult(
            job_id=effective_job_id,
            status="succeeded",
            scores=scores,
            completed_at=datetime.now(UTC),
            warnings=warnings,
            model_inference=inference,
        )


def run_heatzone_batch_score(
    *,
    job_id: str | None = None,
    features: Iterable[HeatZoneFeatureInput | GeoFeatureSnapshot | Mapping[str, Any]],
    prediction_origin_time: datetime | str | None = None,
    model_runtime: ProductionModelRuntime | None = None,
    require_production_model: bool | None = None,
) -> HeatZoneBatchScoreResult:
    return HeatZoneScoringWorker(
        model_runtime=model_runtime,
        require_production_model=require_production_model,
    ).run(
        job_id=job_id,
        features=features,
        prediction_origin_time=prediction_origin_time,
    )


def _feature_mapping(
    feature: HeatZoneFeatureInput | GeoFeatureSnapshot | Mapping[str, Any],
) -> Mapping[str, Any]:
    if isinstance(feature, Mapping):
        return feature
    if isinstance(feature, GeoFeatureSnapshot):
        feature = HeatZoneFeatureInput.from_geo_feature_snapshot(feature)
    return dict(feature.__dict__)


def _parse_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


__all__ = [
    "HeatZoneBatchScoreResult",
    "HeatZoneScoringWorker",
    "run_heatzone_batch_score",
]
