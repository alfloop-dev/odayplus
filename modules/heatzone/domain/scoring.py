from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from modules.external_data.geo import GeoFeatureSnapshot

HEATZONE_MODEL_VERSION = "heatzone-baseline-v1"
HEATZONE_FEATURE_VERSION = "geo-grid-view-v1"


class HeatZoneState(StrEnum):
    UNTOUCHED = "UNTOUCHED"
    PARTIALLY_ABSORBED = "PARTIALLY_ABSORBED"
    SATURATED = "SATURATED"
    UNDER_REALIZED = "UNDER_REALIZED"
    STILL_EXPANDABLE = "STILL_EXPANDABLE"
    SUPPRESSED_LOW_CONFIDENCE = "SUPPRESSED_LOW_CONFIDENCE"


@dataclass(frozen=True)
class HeatZoneScoringWeights:
    unmet_demand: float = 0.35
    format_fit: float = 0.25
    rent_feasibility: float = 0.20
    cannibalization_inverse: float = 0.20


DEFAULT_SCORING_WEIGHTS = HeatZoneScoringWeights()


@dataclass(frozen=True)
class HeatZoneFeatureInput:
    h3_index: str
    h3_resolution: int = 9
    feature_snapshot_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    view_version: str = HEATZONE_FEATURE_VERSION
    poi_count: int = 0
    competitor_count: int = 0
    active_listing_count: int = 0
    median_listing_rent: float = 0.0
    competitor_capacity: float = 0.0
    average_confidence: float = 1.0
    source_snapshot_ids: tuple[str, ...] = ()
    existing_store_count: int = 0
    realized_revenue_ratio: float | None = None
    data_quality_score: float = 1.0
    admin_city: str = ""
    admin_district: str = ""

    @classmethod
    def from_geo_feature_snapshot(
        cls,
        snapshot: GeoFeatureSnapshot,
        *,
        existing_store_count: int = 0,
        realized_revenue_ratio: float | None = None,
        admin_city: str = "",
        admin_district: str = "",
    ) -> HeatZoneFeatureInput:
        return cls(
            h3_index=snapshot.h3_index,
            h3_resolution=snapshot.h3_resolution,
            feature_snapshot_time=snapshot.feature_snapshot_time,
            view_version=snapshot.view_version,
            poi_count=snapshot.poi_count,
            competitor_count=snapshot.competitor_count,
            active_listing_count=snapshot.active_listing_count,
            median_listing_rent=snapshot.median_listing_rent,
            competitor_capacity=snapshot.competitor_capacity,
            average_confidence=_value_or_default(snapshot.average_confidence, 1.0),
            source_snapshot_ids=snapshot.source_snapshot_ids,
            existing_store_count=existing_store_count,
            realized_revenue_ratio=realized_revenue_ratio,
            admin_city=admin_city,
            admin_district=admin_district,
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> HeatZoneFeatureInput:
        return cls(
            h3_index=str(data["h3_index"]),
            h3_resolution=int(data.get("h3_resolution", 9)),
            feature_snapshot_time=_parse_datetime(
                data.get("feature_snapshot_time") or datetime.now(UTC)
            ),
            view_version=str(data.get("view_version") or HEATZONE_FEATURE_VERSION),
            poi_count=int(_first_present(data, "poi_count", "poi_total_count", default=0)),
            competitor_count=int(
                _first_present(data, "competitor_count", "competitor_count_500m", default=0)
            ),
            active_listing_count=int(
                _first_present(data, "active_listing_count", "listing_count_active", default=0)
            ),
            median_listing_rent=float(
                _first_present(data, "median_listing_rent", "rent_p50_per_ping", default=0.0)
            ),
            competitor_capacity=float(
                _first_present(
                    data,
                    "competitor_capacity",
                    "competitor_capacity_proxy_500m",
                    default=0.0,
                )
            ),
            average_confidence=_bounded(
                _first_present(data, "average_confidence", "confidence", default=1.0)
            ),
            source_snapshot_ids=tuple(str(v) for v in data.get("source_snapshot_ids", ())),
            existing_store_count=int(_value_or_default(data.get("existing_store_count"), 0)),
            realized_revenue_ratio=_optional_float(data.get("realized_revenue_ratio")),
            data_quality_score=_bounded(_data_quality_score(data)),
            admin_city=str(data.get("admin_city") or ""),
            admin_district=str(data.get("admin_district") or ""),
        )


@dataclass(frozen=True)
class HeatZoneScoreResult:
    heat_zone_id: str
    h3_index: str
    h3_resolution: int
    score: float
    priority_rank: int
    unmet_demand_score: float
    format_fit_score: float
    cannibalization_risk_score: float
    rent_feasibility_score: float
    listing_availability_score: float
    confidence: float
    state: HeatZoneState
    feature_snapshot_time: datetime
    prediction_origin_time: datetime
    last_scored_at: datetime
    model_version: str
    feature_version: str
    source_snapshot_ids: tuple[str, ...]
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    admin_city: str = ""
    admin_district: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "heat_zone_id": self.heat_zone_id,
            "h3_index": self.h3_index,
            "h3_resolution": self.h3_resolution,
            "score": self.score,
            "priority_rank": self.priority_rank,
            "unmet_demand_score": self.unmet_demand_score,
            "format_fit_score": self.format_fit_score,
            "cannibalization_risk_score": self.cannibalization_risk_score,
            "rent_feasibility_score": self.rent_feasibility_score,
            "listing_availability_score": self.listing_availability_score,
            "confidence": self.confidence,
            "state": self.state.value,
            "feature_snapshot_time": self.feature_snapshot_time.isoformat(),
            "prediction_origin_time": self.prediction_origin_time.isoformat(),
            "last_scored_at": self.last_scored_at.isoformat(),
            "model_version": self.model_version,
            "feature_version": self.feature_version,
            "source_snapshot_ids": list(self.source_snapshot_ids),
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "admin_city": self.admin_city,
            "admin_district": self.admin_district,
        }

    def to_map_feature(self) -> dict[str, Any]:
        return {
            "type": "Feature",
            "id": self.heat_zone_id,
            "geometry": None,
            "properties": {
                "heat_zone_id": self.heat_zone_id,
                "h3_index": self.h3_index,
                "score": self.score,
                "priority_rank": self.priority_rank,
                "unmet_demand_score": self.unmet_demand_score,
                "format_fit_score": self.format_fit_score,
                "cannibalization_risk": self.cannibalization_risk_score,
                "rent_feasibility": self.rent_feasibility_score,
                "listing_availability": self.listing_availability_score,
                "confidence": self.confidence,
                "status": self.state.value,
                "last_scored_at": self.last_scored_at.isoformat(),
                "model_version": self.model_version,
                "feature_version": self.feature_version,
                "admin_city": self.admin_city,
                "admin_district": self.admin_district,
                "warnings": list(self.warnings),
            },
        }


def score_heatzones(
    features: Iterable[HeatZoneFeatureInput | GeoFeatureSnapshot | Mapping[str, Any]],
    *,
    prediction_origin_time: datetime | None = None,
    scored_at: datetime | None = None,
    weights: HeatZoneScoringWeights | None = None,
) -> list[HeatZoneScoreResult]:
    origin = prediction_origin_time or datetime.now(UTC)
    scored_time = scored_at or datetime.now(UTC)
    effective_weights = weights or DEFAULT_SCORING_WEIGHTS
    inputs = [_coerce_feature(feature) for feature in features]
    scored = [
        _score_feature(
            feature,
            priority_rank=0,
            prediction_origin_time=origin,
            scored_at=scored_time,
            weights=effective_weights,
        )
        for feature in inputs
    ]
    ranked = sorted(scored, key=lambda item: (-item.score, item.h3_index))
    return [
        HeatZoneScoreResult(
            **{**result.__dict__, "priority_rank": index + 1}
        )
        for index, result in enumerate(ranked)
    ]


def _score_feature(
    feature: HeatZoneFeatureInput,
    *,
    priority_rank: int,
    prediction_origin_time: datetime,
    scored_at: datetime,
    weights: HeatZoneScoringWeights,
) -> HeatZoneScoreResult:
    poi_demand = _bounded(feature.poi_count / 20.0)
    listing_availability = _bounded(feature.active_listing_count / 8.0)
    competitor_pressure = _bounded((feature.competitor_count / 8.0) + (feature.competitor_capacity / 40.0))
    competition_gap = 1.0 - competitor_pressure
    cannibalization_risk = _bounded(feature.existing_store_count / 3.0)
    unmet_demand = _bounded((poi_demand * 0.7 + competition_gap * 0.3) * (1.0 - cannibalization_risk * 0.45))
    format_fit = _bounded(poi_demand * 0.6 + listing_availability * 0.4)
    rent_feasibility = _rent_feasibility(feature.median_listing_rent, listing_availability)
    raw_score = (
        unmet_demand * weights.unmet_demand
        + format_fit * weights.format_fit
        + rent_feasibility * weights.rent_feasibility
        + (1.0 - cannibalization_risk) * weights.cannibalization_inverse
    )
    confidence = _bounded(feature.average_confidence * feature.data_quality_score)
    warnings = _warnings(feature, confidence)
    reasons = _reasons(
        unmet_demand=unmet_demand,
        format_fit=format_fit,
        rent_feasibility=rent_feasibility,
        cannibalization_risk=cannibalization_risk,
        listing_availability=listing_availability,
    )
    state = _state_for(feature, unmet_demand, cannibalization_risk, confidence)
    return HeatZoneScoreResult(
        heat_zone_id=f"heatzone:{feature.h3_index}",
        h3_index=feature.h3_index,
        h3_resolution=feature.h3_resolution,
        score=round(raw_score * 100.0, 2),
        priority_rank=priority_rank,
        unmet_demand_score=round(unmet_demand, 4),
        format_fit_score=round(format_fit, 4),
        cannibalization_risk_score=round(cannibalization_risk, 4),
        rent_feasibility_score=round(rent_feasibility, 4),
        listing_availability_score=round(listing_availability, 4),
        confidence=round(confidence, 4),
        state=state,
        feature_snapshot_time=feature.feature_snapshot_time,
        prediction_origin_time=prediction_origin_time,
        last_scored_at=scored_at,
        model_version=HEATZONE_MODEL_VERSION,
        feature_version=feature.view_version,
        source_snapshot_ids=feature.source_snapshot_ids,
        reasons=reasons,
        warnings=warnings,
        admin_city=feature.admin_city,
        admin_district=feature.admin_district,
    )


def _coerce_feature(
    feature: HeatZoneFeatureInput | GeoFeatureSnapshot | Mapping[str, Any],
) -> HeatZoneFeatureInput:
    if isinstance(feature, HeatZoneFeatureInput):
        return feature
    if isinstance(feature, GeoFeatureSnapshot):
        return HeatZoneFeatureInput.from_geo_feature_snapshot(feature)
    return HeatZoneFeatureInput.from_mapping(feature)


def _rent_feasibility(median_listing_rent: float, listing_availability: float) -> float:
    if median_listing_rent <= 0:
        return 0.35 * listing_availability
    affordability = 1.0 - _bounded((median_listing_rent - 40_000.0) / 100_000.0)
    return _bounded(affordability * 0.8 + listing_availability * 0.2)


def _state_for(
    feature: HeatZoneFeatureInput,
    unmet_demand: float,
    cannibalization_risk: float,
    confidence: float,
) -> HeatZoneState:
    if confidence < 0.35:
        return HeatZoneState.SUPPRESSED_LOW_CONFIDENCE
    if feature.existing_store_count == 0:
        return HeatZoneState.UNTOUCHED
    if feature.realized_revenue_ratio is not None and feature.realized_revenue_ratio < 0.75:
        return HeatZoneState.UNDER_REALIZED
    if cannibalization_risk >= 0.75 or unmet_demand < 0.25:
        return HeatZoneState.SATURATED
    if unmet_demand >= 0.55:
        return HeatZoneState.STILL_EXPANDABLE
    return HeatZoneState.PARTIALLY_ABSORBED


def _reasons(
    *,
    unmet_demand: float,
    format_fit: float,
    rent_feasibility: float,
    cannibalization_risk: float,
    listing_availability: float,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if unmet_demand >= 0.65:
        reasons.append("high_unmet_demand")
    if format_fit >= 0.65:
        reasons.append("good_format_fit")
    if listing_availability >= 0.5:
        reasons.append("listing_supply_available")
    if rent_feasibility < 0.4:
        reasons.append("rent_feasibility_pressure")
    if cannibalization_risk >= 0.5:
        reasons.append("cannibalization_risk")
    return tuple(reasons)


def _warnings(feature: HeatZoneFeatureInput, confidence: float) -> tuple[str, ...]:
    warnings: list[str] = []
    if confidence < 0.5:
        warnings.append("low_confidence")
    if not feature.source_snapshot_ids:
        warnings.append("missing_source_snapshot_ids")
    if feature.feature_snapshot_time > datetime.now(UTC):
        warnings.append("future_feature_snapshot_time")
    return tuple(warnings)


def _bounded(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return max(0.0, min(1.0, number))


def _first_present(data: Mapping[str, Any], *keys: str, default: Any) -> Any:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return default


def _value_or_default(value: Any, default: Any) -> Any:
    if value is None:
        return default
    return value


def _data_quality_score(data: Mapping[str, Any]) -> Any:
    score = data.get("data_quality_score")
    if score is not None:
        return score
    data_quality = data.get("data_quality")
    if isinstance(data_quality, int | float | str):
        return data_quality
    return 1.0


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


__all__ = [
    "HEATZONE_FEATURE_VERSION",
    "HEATZONE_MODEL_VERSION",
    "HeatZoneFeatureInput",
    "HeatZoneScoreResult",
    "HeatZoneScoringWeights",
    "HeatZoneState",
    "score_heatzones",
]
