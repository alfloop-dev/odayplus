from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from modules.heatzone.v3.contract import (
    AbstainReasonCode,
    ExecutionMode,
    HeatZoneV3Input,
    HeatZoneV3ScoreResult,
    HeatZoneV3State,
    MODEL_VERSION,
)


@dataclass(frozen=True)
class HeatZoneV3ScoringWeights:
    """Configurable weights for HeatZone v3 multi-criteria scoring."""

    unmet_demand: float = 0.35
    format_fit: float = 0.25
    rent_feasibility: float = 0.20
    cannibalization_inverse: float = 0.20


DEFAULT_V3_WEIGHTS = HeatZoneV3ScoringWeights()


def check_support_and_abstention(feature: HeatZoneV3Input) -> tuple[bool, tuple[str, ...]]:
    """Evaluate whether the input cell or catchment is outside platform support.

    HeatZone v3 abstains outside support to fail closed before emitting scores.
    """
    reasons: list[str] = []

    # 1. Overall platform readiness gate
    readiness_str = (
        feature.overall_readiness.value
        if hasattr(feature.overall_readiness, "value")
        else str(feature.overall_readiness).lower()
    )
    if readiness_str == "blocked":
        reasons.append(AbstainReasonCode.READINESS_BLOCKED.value)
    elif readiness_str == "unknown":
        reasons.append(AbstainReasonCode.READINESS_UNKNOWN.value)

    # 2. Source quarantine
    if feature.is_quarantined:
        reasons.append(AbstainReasonCode.SOURCE_QUARANTINED.value)

    # 3. Coverage ratio threshold
    if feature.coverage_ratio < 0.50:
        reasons.append(AbstainReasonCode.INSUFFICIENT_COVERAGE.value)

    # 4. Declared support level
    if str(feature.support_level).lower() in {"unsupported", "outside_boundary", "withheld", "quarantined"}:
        reasons.append(AbstainReasonCode.OUT_OF_SUPPORT_BOUNDS.value)

    # 5. Critical domain missingness or blockage
    for domain, cov_state in feature.domain_coverage.items():
        cov_state_str = str(cov_state).lower()
        if cov_state_str in {"quarantined", "withheld"}:
            reasons.append(f"{AbstainReasonCode.MISSING_REQUIRED_DOMAINS.value}:{domain.lower()}_{cov_state_str}")
        elif cov_state_str in {"empty", "missing", "unobserved"} and domain.upper() in {"DEMOGRAPHICS", "COMPETITOR", "GEOGRAPHY"}:
            if feature.has_coverage_gaps or readiness_str != "ready":
                reasons.append(f"{AbstainReasonCode.MISSING_REQUIRED_DOMAINS.value}:{domain.lower()}_{cov_state_str}")

    # 6. Unacceptable confidence / quality floor
    if feature.confidence < 0.25:
        reasons.append(AbstainReasonCode.DATA_QUALITY_UNACCEPTABLE.value)

    if reasons:
        return True, tuple(reasons)
    return False, ()


def score_heatzone_v3_feature(
    feature: HeatZoneV3Input,
    *,
    priority_rank: int = 0,
    evaluated_at: datetime | None = None,
    weights: HeatZoneV3ScoringWeights | None = None,
    execution_mode: ExecutionMode = ExecutionMode.SHADOW,
    model_version: str = MODEL_VERSION,
) -> HeatZoneV3ScoreResult:
    """Score a single HeatZone v3 feature input using all 9 platform dimensions."""
    eval_time = evaluated_at or datetime.now(UTC)
    effective_weights = weights or DEFAULT_V3_WEIGHTS

    # Check platform support and abstention
    is_abstained, abstain_reasons = check_support_and_abstention(feature)

    # Dimension 1 & 2: Population & Households (Demographic Vitality)
    pop_norm = min(1.0, max(0.0, feature.population) / 8000.0)
    hh_norm = min(1.0, max(0.0, feature.household_count) / 3000.0)
    daytime_factor = max(0.5, min(1.5, feature.daytime_population_ratio if feature.daytime_population_ratio > 0 else 1.0))
    demographic_vitality = min(1.0, (pop_norm * 0.6 + hh_norm * 0.4) * (0.8 + 0.2 * (daytime_factor - 0.5)))

    # Dimension 3: Housing Density
    if feature.housing_units > 0:
        housing_density = min(1.0, feature.housing_units / 2500.0)
    else:
        housing_density = hh_norm

    # Dimension 4: POI Demand
    poi_demand = min(1.0, max(0.0, feature.poi_count) / 25.0)

    # Dimension 5: Competitor Capacity & Pressure
    comp_store_pressure = min(1.0, max(0.0, feature.active_competitor_count) / 8.0)
    comp_cap_pressure = min(1.0, max(0.0, feature.competitor_capacity) / 60.0)
    competitor_pressure = min(1.0, comp_store_pressure * 0.6 + comp_cap_pressure * 0.4)
    competition_gap = max(0.0, 1.0 - competitor_pressure)

    # Dimension 6: Rent Feasibility
    effective_rent = feature.median_rent_per_ping if feature.median_rent_per_ping > 0 else feature.mean_rent_per_ping
    if effective_rent <= 0:
        rent_feasibility = 0.40 * min(1.0, feature.active_listing_count / 5.0) if feature.active_listing_count > 0 else 0.35
    else:
        affordability = max(0.0, 1.0 - min(1.0, max(0.0, (effective_rent - 1200.0) / 4000.0)))
        rent_feasibility = min(1.0, affordability * 0.80 + min(1.0, feature.active_listing_count / 8.0) * 0.20)

    # Dimension 7: Listing Availability
    listing_availability = min(1.0, max(0.0, feature.active_listing_count) / 8.0)

    # Dimension 8: Own-Store Capacity & Cannibalization Risk
    store_risk = min(1.0, max(0.0, feature.own_store_count) / 3.0)
    machine_risk = min(1.0, max(0.0, feature.own_store_machine_capacity) / 35.0)
    cannibalization_risk = min(1.0, store_risk * 0.65 + machine_risk * 0.35)

    # Dimension 9: Coverage-adjusted Unmet Demand & Format Fit
    demand_base = demographic_vitality * 0.40 + poi_demand * 0.30 + competition_gap * 0.30
    unmet_demand = min(1.0, demand_base * (1.0 - cannibalization_risk * 0.45))

    format_fit = min(
        1.0,
        unmet_demand * 0.35
        + poi_demand * 0.25
        + listing_availability * 0.20
        + housing_density * 0.20,
    )

    # Composite Score
    confidence = max(0.0, min(1.0, feature.confidence * (feature.coverage_ratio if feature.coverage_ratio > 0 else 1.0)))

    if is_abstained:
        score = None
        state = HeatZoneV3State.ABSTAINED
    else:
        raw_score = (
            unmet_demand * effective_weights.unmet_demand
            + format_fit * effective_weights.format_fit
            + rent_feasibility * effective_weights.rent_feasibility
            + (1.0 - cannibalization_risk) * effective_weights.cannibalization_inverse
        )
        score = round(max(0.0, min(100.0, raw_score * 100.0)), 2)
        state = _state_for_v3(feature, unmet_demand, cannibalization_risk, confidence)

    warnings = _warnings_v3(feature, confidence, is_abstained)
    reasons = _reasons_v3(
        unmet_demand=unmet_demand,
        format_fit=format_fit,
        rent_feasibility=rent_feasibility,
        cannibalization_risk=cannibalization_risk,
        listing_availability=listing_availability,
        demographic_vitality=demographic_vitality,
        is_abstained=is_abstained,
    )

    input_dims = {
        "population": feature.population,
        "household_count": feature.household_count,
        "housing_units": feature.housing_units,
        "poi_count": feature.poi_count,
        "competitor_capacity": feature.competitor_capacity,
        "active_competitor_count": feature.active_competitor_count,
        "median_rent_per_ping": feature.median_rent_per_ping,
        "active_listing_count": feature.active_listing_count,
        "own_store_count": feature.own_store_count,
        "own_store_machine_capacity": feature.own_store_machine_capacity,
        "overall_readiness": feature.overall_readiness.value if hasattr(feature.overall_readiness, "value") else str(feature.overall_readiness),
        "coverage_ratio": feature.coverage_ratio,
    }

    manifest_refs = tuple(
        ref.to_dict() if hasattr(ref, "to_dict") else dict(ref)
        for ref in feature.component_manifest_refs
    )

    return HeatZoneV3ScoreResult(
        heat_zone_id=f"heatzone:v3:{feature.h3_index}",
        h3_index=feature.h3_index,
        h3_resolution=feature.h3_resolution,
        score=score,
        priority_rank=priority_rank,
        unmet_demand_score=round(unmet_demand, 4),
        format_fit_score=round(format_fit, 4),
        competitor_pressure_score=round(competitor_pressure, 4),
        cannibalization_risk_score=round(cannibalization_risk, 4),
        rent_feasibility_score=round(rent_feasibility, 4),
        listing_availability_score=round(listing_availability, 4),
        housing_density_score=round(housing_density, 4),
        demographic_vitality_score=round(demographic_vitality, 4),
        confidence=round(confidence, 4),
        state=state,
        abstained=is_abstained,
        abstain_reasons=abstain_reasons,
        is_shadow=execution_mode is ExecutionMode.SHADOW,
        execution_mode=execution_mode,
        model_version=model_version,
        reasons=reasons,
        warnings=warnings,
        input_dimensions=input_dims,
        evaluated_at=eval_time,
        component_manifest_refs=manifest_refs,
        county=feature.county,
        district=feature.district,
        admin_code=feature.admin_code,
    )


def score_heatzones_v3(
    features: Sequence[HeatZoneV3Input],
    *,
    evaluated_at: datetime | None = None,
    weights: HeatZoneV3ScoringWeights | None = None,
    execution_mode: ExecutionMode = ExecutionMode.SHADOW,
    model_version: str = MODEL_VERSION,
) -> list[HeatZoneV3ScoreResult]:
    """Score and rank a collection of HeatZone v3 inputs."""
    eval_time = evaluated_at or datetime.now(UTC)
    effective_weights = weights or DEFAULT_V3_WEIGHTS

    scored = [
        score_heatzone_v3_feature(
            f,
            priority_rank=0,
            evaluated_at=eval_time,
            weights=effective_weights,
            execution_mode=execution_mode,
            model_version=model_version,
        )
        for f in features
    ]

    valid_scored = [r for r in scored if not r.abstained and r.score is not None]
    abstained = [r for r in scored if r.abstained or r.score is None]

    valid_ranked = sorted(valid_scored, key=lambda item: (-(item.score or 0.0), item.h3_index))
    abstained_ranked = sorted(abstained, key=lambda item: item.h3_index)

    results: list[HeatZoneV3ScoreResult] = []
    current_rank = 1

    for item in valid_ranked:
        results.append(
            HeatZoneV3ScoreResult(
                **{**item.__dict__, "priority_rank": current_rank}
            )
        )
        current_rank += 1

    for item in abstained_ranked:
        results.append(
            HeatZoneV3ScoreResult(
                **{**item.__dict__, "priority_rank": current_rank}
            )
        )
        current_rank += 1

    return results


def _state_for_v3(
    feature: HeatZoneV3Input,
    unmet_demand: float,
    cannibalization_risk: float,
    confidence: float,
) -> HeatZoneV3State:
    if confidence < 0.35:
        return HeatZoneV3State.SUPPRESSED_LOW_CONFIDENCE
    if feature.own_store_count == 0 and feature.own_store_machine_capacity == 0:
        return HeatZoneV3State.UNTOUCHED
    if cannibalization_risk >= 0.75 or unmet_demand < 0.25:
        return HeatZoneV3State.SATURATED
    if unmet_demand >= 0.55:
        return HeatZoneV3State.STILL_EXPANDABLE
    return HeatZoneV3State.PARTIALLY_ABSORBED


def _warnings_v3(feature: HeatZoneV3Input, confidence: float, is_abstained: bool) -> tuple[str, ...]:
    warnings: list[str] = []
    if is_abstained:
        warnings.append("evaluation_abstained_outside_support")
    if confidence < 0.50:
        warnings.append("low_confidence_data")
    if feature.has_coverage_gaps:
        warnings.append("domain_coverage_gaps_present")
    if feature.rent_sample_count > 0 and feature.rent_sample_count < 3:
        warnings.append("sparse_rent_sample_count")
    if feature.is_quarantined:
        warnings.append("source_quarantined")
    return tuple(warnings)


def _reasons_v3(
    *,
    unmet_demand: float,
    format_fit: float,
    rent_feasibility: float,
    cannibalization_risk: float,
    listing_availability: float,
    demographic_vitality: float,
    is_abstained: bool,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if is_abstained:
        reasons.append("model_abstained")
        return tuple(reasons)
    if unmet_demand >= 0.65:
        reasons.append("high_unmet_demand")
    if format_fit >= 0.65:
        reasons.append("strong_format_fit")
    if demographic_vitality >= 0.60:
        reasons.append("high_demographic_density")
    if listing_availability >= 0.50:
        reasons.append("listing_supply_available")
    if rent_feasibility < 0.40:
        reasons.append("rent_feasibility_pressure")
    if cannibalization_risk >= 0.50:
        reasons.append("own_network_cannibalization_risk")
    return tuple(reasons)


__all__ = [
    "DEFAULT_V3_WEIGHTS",
    "HeatZoneV3ScoringWeights",
    "check_support_and_abstention",
    "score_heatzone_v3_feature",
    "score_heatzones_v3",
]
