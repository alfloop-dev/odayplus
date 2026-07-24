from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LiquidityTrainingRecord:
    duration_days: float
    sold: bool
    features: Mapping[str, float]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> LiquidityTrainingRecord:
        features = data.get("features")
        if not isinstance(features, Mapping) or not features:
            raise ValueError("liquidity training record requires non-empty numeric features")
        return cls(
            duration_days=float(data.get("duration_days", data.get("days_on_market", 0.0))),
            sold=bool(data.get("sold", data.get("event_observed", False))),
            features={str(name): float(value) for name, value in features.items()},
        )


@dataclass(frozen=True)
class LiquidityPrediction:
    sale_probability_30d: float
    sale_probability_90d: float
    expected_days: float
    model_version: str
    feature_names: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sale_probability_30d": self.sale_probability_30d,
            "sale_probability_90d": self.sale_probability_90d,
            "expected_days": self.expected_days,
            "model_version": self.model_version,
            "feature_names": list(self.feature_names),
        }


@dataclass(frozen=True)
class SurvivalModelCapability:
    adapter_name: str
    dependency: str
    available: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_name": self.adapter_name,
            "dependency": self.dependency,
            "available": self.available,
            "reason": self.reason,
        }


__all__ = [
    "LiquidityPrediction",
    "LiquidityTrainingRecord",
    "SurvivalModelCapability",
]
