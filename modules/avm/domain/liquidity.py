from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LiquidityTrainingRecord:
    duration_days: float
    sold: bool
    features: Mapping[str, float]
    settlement_price: float | None = None
    no_deal_reason_code: str | None = None
    deal_terms: Mapping[str, Any] = field(default_factory=dict)
    valuation_id: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> LiquidityTrainingRecord:
        features = data.get("features")
        if not isinstance(features, Mapping) or not features:
            raise ValueError("liquidity training record requires non-empty numeric features")
        settlement_price_raw = data.get("settlement_price")
        settlement_price = float(settlement_price_raw) if settlement_price_raw is not None else None
        no_deal_reason_code = (
            str(data["no_deal_reason_code"])
            if data.get("no_deal_reason_code") is not None
            else None
        )
        deal_terms = data.get("deal_terms", {})
        if not isinstance(deal_terms, Mapping):
            deal_terms = {}
        valuation_id = str(data["valuation_id"]) if data.get("valuation_id") is not None else None
        return cls(
            duration_days=float(data.get("duration_days", data.get("days_on_market", 0.0))),
            sold=bool(data.get("sold", data.get("event_observed", False))),
            features={str(name): float(value) for name, value in features.items()},
            settlement_price=settlement_price,
            no_deal_reason_code=no_deal_reason_code,
            deal_terms={str(k): v for k, v in deal_terms.items()},
            valuation_id=valuation_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration_days": self.duration_days,
            "sold": self.sold,
            "features": dict(self.features),
            "settlement_price": self.settlement_price,
            "no_deal_reason_code": self.no_deal_reason_code,
            "deal_terms": dict(self.deal_terms),
            "valuation_id": self.valuation_id,
        }


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
