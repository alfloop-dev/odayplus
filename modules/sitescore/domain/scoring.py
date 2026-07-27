from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

SITESCORE_MODEL_VERSION = "sitescore-baseline-v1"
SITESCORE_FEATURE_VERSION = "candidate-site-view-v1"

# Mature monthly revenue ceiling (TWD) for a fully-demanded ODay G2 site.
FORMAT_REVENUE_CAPACITY = 500_000.0
# Horizon ramp multipliers applied to the mature monthly revenue.
HORIZON_RAMP = {"m1": 0.45, "m3": 0.70, "m6": 0.90, "m12": 1.00}

# Decision thresholds (months / ratios).
GO_PAYBACK_MONTHS = 36.0
REJECT_PAYBACK_MONTHS = 72.0
GO_MIN_CONFIDENCE = 0.55
INVESTIGATE_MAX_CONFIDENCE = 0.40
GO_MIN_RENT_REASONABLENESS = 0.50
REJECT_MAX_RENT_REASONABLENESS = 0.25
GO_MAX_CANNIBALIZATION = 0.60
REJECT_MIN_CANNIBALIZATION = 0.85
PAYBACK_CAP_MONTHS = 999.0


class SiteScoreRecommendation(StrEnum):
    GO = "GO"
    WAIT = "WAIT"
    REJECT = "REJECT"
    INVESTIGATE = "INVESTIGATE"


@dataclass(frozen=True)
class Interval:
    """P10/P50/P90 prediction band."""

    p10: float
    p50: float
    p90: float

    def to_dict(self) -> dict[str, float]:
        return {"p10": self.p10, "p50": self.p50, "p90": self.p90}


@dataclass(frozen=True)
class RevenuePredictionBand:
    """Model-produced mature monthly revenue prediction."""

    p10: float
    p50: float
    p90: float


@dataclass(frozen=True)
class SiteScoreFeatureInput:
    """Feature vector for scoring one candidate site."""

    candidate_site_id: str
    target_format_code: str = "ODAY_G2"
    feature_snapshot_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    view_version: str = SITESCORE_FEATURE_VERSION
    heat_zone_id: str = ""
    heat_zone_score: float = 0.0  # 0..100 demand signal from HeatZone Radar
    poi_demand_index: float = 0.0  # 0..1 fallback demand when heat_zone_score missing
    monthly_rent: float = 0.0
    area_ping: float = 0.0
    frontage_m: float = 0.0
    competitor_count: int = 0
    own_store_count_nearby: int = 0  # cannibalization driver
    comparable_store_count: int = 0
    comparable_monthly_revenue_p50: float = 0.0
    buildout_capex: float = 2_500_000.0
    gross_margin_ratio: float = 0.55
    average_confidence: float = 1.0
    data_quality_score: float = 1.0
    source_snapshot_ids: tuple[str, ...] = ()

    @property
    def rent_per_ping(self) -> float:
        if self.area_ping <= 0:
            return 0.0
        return self.monthly_rent / self.area_ping

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> SiteScoreFeatureInput:
        return cls(
            candidate_site_id=str(data["candidate_site_id"]),
            target_format_code=str(data.get("target_format_code") or "ODAY_G2"),
            feature_snapshot_time=_parse_datetime(
                data.get("feature_snapshot_time") or datetime.now(UTC)
            ),
            view_version=str(data.get("view_version") or SITESCORE_FEATURE_VERSION),
            heat_zone_id=str(data.get("heat_zone_id") or ""),
            heat_zone_score=float(
                _first_present(data, "heat_zone_score", "heatZoneScore", default=0.0)
            ),
            poi_demand_index=_bounded(
                _first_present(data, "poi_demand_index", "poiDemandIndex", default=0.0)
            ),
            monthly_rent=float(
                _first_present(data, "monthly_rent", "rent_amount", "rent", default=0.0)
            ),
            area_ping=float(_first_present(data, "area_ping", "area", default=0.0)),
            frontage_m=float(_first_present(data, "frontage_m", "frontage", default=0.0)),
            competitor_count=int(_first_present(data, "competitor_count", default=0)),
            own_store_count_nearby=int(
                _first_present(data, "own_store_count_nearby", "own_store_count", default=0)
            ),
            comparable_store_count=int(
                _first_present(data, "comparable_store_count", "comparable_stores", default=0)
            ),
            comparable_monthly_revenue_p50=float(
                _first_present(
                    data, "comparable_monthly_revenue_p50", "comparable_revenue", default=0.0
                )
            ),
            buildout_capex=float(
                _first_present(data, "buildout_capex", "capex", default=2_500_000.0)
            ),
            gross_margin_ratio=_bounded(
                _first_present(data, "gross_margin_ratio", "gross_margin", default=0.55)
            ),
            average_confidence=_bounded(
                _first_present(data, "average_confidence", "confidence", default=1.0)
            ),
            data_quality_score=_bounded(
                _first_present(data, "data_quality_score", "data_quality", default=1.0)
            ),
            source_snapshot_ids=tuple(str(v) for v in data.get("source_snapshot_ids", ())),
        )


@dataclass(frozen=True)
class SiteScoreReport:
    """Versioned SiteScore report for a candidate site."""

    sitescore_run_id: str
    candidate_site_id: str
    target_format_code: str
    recommendation: SiteScoreRecommendation
    m1: Interval
    m3: Interval
    m6: Interval
    m12: Interval
    payback_period: Interval
    payback_p50_months: float
    rent_reasonableness: float
    cannibalization_risk: float
    comparable_stores: int
    key_positive_factors: tuple[str, ...]
    key_negative_factors: tuple[str, ...]
    confidence: float
    model_version: str
    feature_version: str
    feature_snapshot_time: datetime
    prediction_origin_time: datetime
    scored_at: datetime
    heat_zone_id: str = ""
    source_snapshot_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    report_version: int = 1
    report_id: str = field(default_factory=lambda: str(uuid4()))

    @property
    def horizons(self) -> dict[str, Interval]:
        return {"m1": self.m1, "m3": self.m3, "m6": self.m6, "m12": self.m12}

    def baseline_trajectory(self) -> dict[str, float]:
        """P50 monthly-revenue baseline used for later realization tracking."""
        return {key: interval.p50 for key, interval in self.horizons.items()}

    def with_version(self, *, report_version: int, report_id: str) -> SiteScoreReport:
        return SiteScoreReport(
            **{**self.__dict__, "report_version": report_version, "report_id": report_id}
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sitescore_run_id": self.sitescore_run_id,
            "report_id": self.report_id,
            "report_version": self.report_version,
            "candidate_site_id": self.candidate_site_id,
            "target_format_code": self.target_format_code,
            "recommendation": self.recommendation.value,
            "m1": self.m1.to_dict(),
            "m3": self.m3.to_dict(),
            "m6": self.m6.to_dict(),
            "m12": self.m12.to_dict(),
            "payback_period": self.payback_period.to_dict(),
            "payback_p50_months": self.payback_p50_months,
            "rent_reasonableness": self.rent_reasonableness,
            "cannibalization_risk": self.cannibalization_risk,
            "comparable_stores": self.comparable_stores,
            "key_positive_factors": list(self.key_positive_factors),
            "key_negative_factors": list(self.key_negative_factors),
            "confidence": self.confidence,
            "model_version": self.model_version,
            "feature_version": self.feature_version,
            "feature_snapshot_time": self.feature_snapshot_time.isoformat(),
            "prediction_origin_time": self.prediction_origin_time.isoformat(),
            "scored_at": self.scored_at.isoformat(),
            "heat_zone_id": self.heat_zone_id,
            "source_snapshot_ids": list(self.source_snapshot_ids),
            "warnings": list(self.warnings),
        }

    def to_summary_dict(self) -> dict[str, Any]:
        """Camel-cased shape matching the SiteScoreReportSummary contract (ODP-UX)."""
        return {
            "candidateSiteId": self.candidate_site_id,
            "reportVersion": self.report_version,
            "recommendation": self.recommendation.value,
            "m1": self.m1.to_dict(),
            "m3": self.m3.to_dict(),
            "m6": self.m6.to_dict(),
            "m12": self.m12.to_dict(),
            "paybackPeriod": self.payback_period.to_dict(),
            "rentReasonableness": self.rent_reasonableness,
            "cannibalizationRisk": self.cannibalization_risk,
            "comparableStores": self.comparable_stores,
            "keyPositiveFactors": list(self.key_positive_factors),
            "keyNegativeFactors": list(self.key_negative_factors),
            "confidence": self.confidence,
            "modelVersion": self.model_version,
            "featureSnapshotTime": self.feature_snapshot_time.isoformat(),
        }


def score_sites(
    features: Iterable[SiteScoreFeatureInput | Mapping[str, Any]],
    *,
    prediction_origin_time: datetime | None = None,
    scored_at: datetime | None = None,
) -> list[SiteScoreReport]:
    origin = prediction_origin_time or datetime.now(UTC)
    scored_time = scored_at or datetime.now(UTC)
    return [
        _score_feature(
            _coerce_feature(feature), prediction_origin_time=origin, scored_at=scored_time
        )
        for feature in features
    ]


def score_sites_from_model_predictions(
    features: Iterable[SiteScoreFeatureInput | Mapping[str, Any]],
    predictions: Iterable[RevenuePredictionBand],
    *,
    model_version: str,
    prediction_origin_time: datetime | None = None,
    scored_at: datetime | None = None,
) -> list[SiteScoreReport]:
    """Build SiteScore reports from executable model revenue predictions.

    Business decisions and payback policy remain in the SiteScore domain, but
    the revenue trajectory is supplied by the registered model rather than the
    deterministic POC baseline.
    """

    origin = prediction_origin_time or datetime.now(UTC)
    scored_time = scored_at or datetime.now(UTC)
    feature_rows = [_coerce_feature(feature) for feature in features]
    prediction_rows = list(predictions)
    if len(feature_rows) != len(prediction_rows):
        raise ValueError("SiteScore feature and model prediction counts differ")
    return [
        _score_feature(
            feature,
            prediction_origin_time=origin,
            scored_at=scored_time,
            mature_revenue_prediction=prediction,
            model_version=model_version,
        )
        for feature, prediction in zip(feature_rows, prediction_rows, strict=True)
    ]


def score_site(
    feature: SiteScoreFeatureInput | Mapping[str, Any],
    *,
    prediction_origin_time: datetime | None = None,
    scored_at: datetime | None = None,
) -> SiteScoreReport:
    return score_sites(
        [feature], prediction_origin_time=prediction_origin_time, scored_at=scored_at
    )[0]


def _score_feature(
    feature: SiteScoreFeatureInput,
    *,
    prediction_origin_time: datetime,
    scored_at: datetime,
    mature_revenue_prediction: RevenuePredictionBand | None = None,
    model_version: str = SITESCORE_MODEL_VERSION,
) -> SiteScoreReport:
    demand = (
        _bounded(feature.heat_zone_score / 100.0)
        if feature.heat_zone_score
        else feature.poi_demand_index
    )
    confidence = _confidence(feature)
    if mature_revenue_prediction is None:
        mature_p50 = _mature_revenue(feature, demand)
        spread = (1.0 - confidence) * 0.40 + 0.12
        horizons = {key: _interval(mature_p50 * ramp, spread) for key, ramp in HORIZON_RAMP.items()}
    else:
        mature_p50 = max(0.0, float(mature_revenue_prediction.p50))
        horizons = {
            key: Interval(
                p10=round(max(0.0, mature_revenue_prediction.p10 * ramp), 2),
                p50=round(max(0.0, mature_revenue_prediction.p50 * ramp), 2),
                p90=round(max(0.0, mature_revenue_prediction.p90 * ramp), 2),
            )
            for key, ramp in HORIZON_RAMP.items()
        }
    payback, payback_p50 = _payback(horizons["m12"], feature)
    rent_reasonableness = _rent_reasonableness(feature.monthly_rent, mature_p50)
    cannibalization = _bounded(feature.own_store_count_nearby / 3.0)

    recommendation = _recommend(
        payback_p50_months=payback_p50,
        gross_margin_p50=horizons["m12"].p50 * feature.gross_margin_ratio - feature.monthly_rent,
        rent_reasonableness=rent_reasonableness,
        cannibalization=cannibalization,
        confidence=confidence,
        comparable_store_count=feature.comparable_store_count,
    )
    positives, negatives = _factors(
        demand=demand,
        rent_reasonableness=rent_reasonableness,
        cannibalization=cannibalization,
        payback_p50_months=payback_p50,
        comparable_store_count=feature.comparable_store_count,
        confidence=confidence,
    )
    return SiteScoreReport(
        sitescore_run_id=f"sitescore-run-{uuid4()}",
        candidate_site_id=feature.candidate_site_id,
        target_format_code=feature.target_format_code,
        recommendation=recommendation,
        m1=horizons["m1"],
        m3=horizons["m3"],
        m6=horizons["m6"],
        m12=horizons["m12"],
        payback_period=payback,
        payback_p50_months=payback_p50,
        rent_reasonableness=round(rent_reasonableness, 4),
        cannibalization_risk=round(cannibalization, 4),
        comparable_stores=feature.comparable_store_count,
        key_positive_factors=positives,
        key_negative_factors=negatives,
        confidence=round(confidence, 4),
        model_version=model_version,
        feature_version=feature.view_version,
        feature_snapshot_time=feature.feature_snapshot_time,
        prediction_origin_time=prediction_origin_time,
        scored_at=scored_at,
        heat_zone_id=feature.heat_zone_id,
        source_snapshot_ids=feature.source_snapshot_ids,
        warnings=_warnings(feature, confidence),
    )


def _mature_revenue(feature: SiteScoreFeatureInput, demand: float) -> float:
    demand_revenue = FORMAT_REVENUE_CAPACITY * demand
    if feature.comparable_monthly_revenue_p50 > 0:
        return feature.comparable_monthly_revenue_p50 * 0.6 + demand_revenue * 0.4
    return demand_revenue


def _interval(p50: float, spread: float) -> Interval:
    bounded_spread = max(0.0, min(0.9, spread))
    return Interval(
        p10=round(p50 * (1.0 - bounded_spread), 2),
        p50=round(p50, 2),
        p90=round(p50 * (1.0 + bounded_spread), 2),
    )


def _payback(m12: Interval, feature: SiteScoreFeatureInput) -> tuple[Interval, float]:
    capex = max(0.0, feature.buildout_capex)

    def months(monthly_revenue: float) -> float:
        gross_margin = monthly_revenue * feature.gross_margin_ratio - feature.monthly_rent
        if gross_margin <= 0:
            return PAYBACK_CAP_MONTHS
        return min(PAYBACK_CAP_MONTHS, round(capex / gross_margin, 2))

    # Higher revenue (p90) → faster payback (optimistic p10 of the payback band).
    return (
        Interval(p10=months(m12.p90), p50=months(m12.p50), p90=months(m12.p10)),
        months(m12.p50),
    )


def _rent_reasonableness(monthly_rent: float, mature_p50: float) -> float:
    if mature_p50 <= 0:
        return 0.0
    rent_ratio = monthly_rent / mature_p50
    return _bounded(1.0 - (rent_ratio - 0.15) / 0.30)


def _confidence(feature: SiteScoreFeatureInput) -> float:
    confidence = _bounded(feature.average_confidence * feature.data_quality_score)
    if feature.comparable_store_count == 0:
        confidence *= 0.5
    return _bounded(confidence)


def _recommend(
    *,
    payback_p50_months: float,
    gross_margin_p50: float,
    rent_reasonableness: float,
    cannibalization: float,
    confidence: float,
    comparable_store_count: int,
) -> SiteScoreRecommendation:
    if confidence < INVESTIGATE_MAX_CONFIDENCE or comparable_store_count == 0:
        return SiteScoreRecommendation.INVESTIGATE
    if (
        gross_margin_p50 <= 0
        or payback_p50_months > REJECT_PAYBACK_MONTHS
        or rent_reasonableness < REJECT_MAX_RENT_REASONABLENESS
        or cannibalization >= REJECT_MIN_CANNIBALIZATION
    ):
        return SiteScoreRecommendation.REJECT
    if (
        payback_p50_months <= GO_PAYBACK_MONTHS
        and rent_reasonableness >= GO_MIN_RENT_REASONABLENESS
        and cannibalization < GO_MAX_CANNIBALIZATION
        and confidence >= GO_MIN_CONFIDENCE
    ):
        return SiteScoreRecommendation.GO
    return SiteScoreRecommendation.WAIT


def _factors(
    *,
    demand: float,
    rent_reasonableness: float,
    cannibalization: float,
    payback_p50_months: float,
    comparable_store_count: int,
    confidence: float,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    positives: list[str] = []
    negatives: list[str] = []
    if demand >= 0.65:
        positives.append("strong_local_demand")
    if rent_reasonableness >= 0.6:
        positives.append("rent_within_benchmark")
    if payback_p50_months <= GO_PAYBACK_MONTHS:
        positives.append("payback_within_target")
    if comparable_store_count >= 3:
        positives.append("sufficient_comparable_evidence")
    if demand < 0.4:
        negatives.append("weak_local_demand")
    if rent_reasonableness < REJECT_MAX_RENT_REASONABLENESS:
        negatives.append("rent_above_benchmark")
    if payback_p50_months > REJECT_PAYBACK_MONTHS:
        negatives.append("payback_too_long")
    if cannibalization >= 0.5:
        negatives.append("cannibalization_risk")
    if comparable_store_count == 0:
        negatives.append("no_comparable_evidence")
    if confidence < GO_MIN_CONFIDENCE:
        negatives.append("low_confidence")
    return tuple(positives), tuple(negatives)


def _warnings(feature: SiteScoreFeatureInput, confidence: float) -> tuple[str, ...]:
    warnings: list[str] = []
    if confidence < 0.5:
        warnings.append("low_confidence")
    if not feature.source_snapshot_ids:
        warnings.append("missing_source_snapshot_ids")
    if feature.feature_snapshot_time > datetime.now(UTC):
        warnings.append("future_feature_snapshot_time")
    return tuple(warnings)


def _coerce_feature(
    feature: SiteScoreFeatureInput | Mapping[str, Any],
) -> SiteScoreFeatureInput:
    if isinstance(feature, SiteScoreFeatureInput):
        return feature
    return SiteScoreFeatureInput.from_mapping(feature)


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


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


__all__ = [
    "FORMAT_REVENUE_CAPACITY",
    "GO_PAYBACK_MONTHS",
    "REJECT_PAYBACK_MONTHS",
    "SITESCORE_FEATURE_VERSION",
    "SITESCORE_MODEL_VERSION",
    "Interval",
    "RevenuePredictionBand",
    "SiteScoreFeatureInput",
    "SiteScoreRecommendation",
    "SiteScoreReport",
    "score_site",
    "score_sites",
    "score_sites_from_model_predictions",
]
