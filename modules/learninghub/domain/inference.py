from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class InferenceComparisonMode(StrEnum):
    SHADOW = "SHADOW"
    CANARY = "CANARY"


@dataclass(frozen=True)
class InferencePrediction:
    input_id: str
    model_version: str
    value: float
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_id": self.input_id,
            "model_version": self.model_version,
            "value": self.value,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class InferenceDelta:
    input_id: str
    champion_value: float
    challenger_value: float

    @property
    def absolute_delta(self) -> float:
        return abs(self.challenger_value - self.champion_value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_id": self.input_id,
            "champion_value": self.champion_value,
            "challenger_value": self.challenger_value,
            "absolute_delta": self.absolute_delta,
        }


@dataclass(frozen=True)
class InferenceComparison:
    comparison_id: str
    model_name: str
    champion_version: str
    challenger_version: str
    mode: InferenceComparisonMode
    input_fingerprint: str
    champion_predictions: Sequence[InferencePrediction]
    challenger_predictions: Sequence[InferencePrediction]
    deltas: Sequence[InferenceDelta]
    tolerance: float
    requested_by: str = "system"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def max_delta(self) -> float:
        if not self.deltas:
            return 0.0
        return max(delta.absolute_delta for delta in self.deltas)

    @property
    def mean_delta(self) -> float:
        if not self.deltas:
            return 0.0
        return sum(delta.absolute_delta for delta in self.deltas) / len(self.deltas)

    @property
    def rollback_recommended(self) -> bool:
        return self.max_delta > self.tolerance

    def to_dict(self) -> dict[str, Any]:
        return {
            "comparison_id": self.comparison_id,
            "model_name": self.model_name,
            "champion_version": self.champion_version,
            "challenger_version": self.challenger_version,
            "mode": self.mode.value,
            "input_fingerprint": self.input_fingerprint,
            "champion_predictions": [
                prediction.to_dict() for prediction in self.champion_predictions
            ],
            "challenger_predictions": [
                prediction.to_dict() for prediction in self.challenger_predictions
            ],
            "deltas": [delta.to_dict() for delta in self.deltas],
            "tolerance": self.tolerance,
            "max_delta": self.max_delta,
            "mean_delta": self.mean_delta,
            "rollback_recommended": self.rollback_recommended,
            "requested_by": self.requested_by,
            "created_at": self.created_at.isoformat(),
        }


__all__ = [
    "InferenceComparison",
    "InferenceComparisonMode",
    "InferenceDelta",
    "InferencePrediction",
]
