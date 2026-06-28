from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from modules.external_data.geo import GeoFeatureSnapshot
from modules.heatzone.domain import HeatZoneFeatureInput, HeatZoneScoreResult, score_heatzones


@dataclass(frozen=True)
class HeatZoneBatchScoreResult:
    job_id: str
    status: str
    scores: tuple[HeatZoneScoreResult, ...]
    completed_at: datetime
    warnings: tuple[str, ...] = ()

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
    def run(
        self,
        *,
        job_id: str | None = None,
        features: Iterable[HeatZoneFeatureInput | GeoFeatureSnapshot | Mapping[str, Any]],
        prediction_origin_time: datetime | str | None = None,
    ) -> HeatZoneBatchScoreResult:
        effective_job_id = job_id or f"heatzone-score-{uuid4()}"
        scores = tuple(
            score_heatzones(
                features,
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
        )


def run_heatzone_batch_score(
    *,
    job_id: str | None = None,
    features: Iterable[HeatZoneFeatureInput | GeoFeatureSnapshot | Mapping[str, Any]],
    prediction_origin_time: datetime | str | None = None,
) -> HeatZoneBatchScoreResult:
    return HeatZoneScoringWorker().run(
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
    "HeatZoneBatchScoreResult",
    "HeatZoneScoringWorker",
    "run_heatzone_batch_score",
]
