from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from packages.oday_data_contracts_client.models.machine_capacity import MachineCapacityRecord
from packages.oday_data_contracts_client.models.store_coverage import StoreDayCoverage
from packages.oday_data_product_contracts_client.models.market_cell_profile import (
    ProductComponentRef,
    ReadinessLevel,
)

CONTRACT_ID = "odayplus.heatzone-v3.v1"
CONTRACT_VERSION = "1.0.0"
MODEL_VERSION = "heatzone-v3-shadow"


class HeatZoneV3State(StrEnum):
    """Categorical evaluation state of an H3 cell or catchment under HeatZone v3."""

    UNTOUCHED = "UNTOUCHED"
    PARTIALLY_ABSORBED = "PARTIALLY_ABSORBED"
    SATURATED = "SATURATED"
    UNDER_REALIZED = "UNDER_REALIZED"
    STILL_EXPANDABLE = "STILL_EXPANDABLE"
    SUPPRESSED_LOW_CONFIDENCE = "SUPPRESSED_LOW_CONFIDENCE"
    ABSTAINED = "ABSTAINED"


class AbstainReasonCode(StrEnum):
    """Explicit reason codes explaining why HeatZone v3 abstains from scoring."""

    READINESS_BLOCKED = "READINESS_BLOCKED"
    READINESS_UNKNOWN = "READINESS_UNKNOWN"
    SOURCE_QUARANTINED = "SOURCE_QUARANTINED"
    INSUFFICIENT_COVERAGE = "INSUFFICIENT_COVERAGE"
    MISSING_REQUIRED_DOMAINS = "MISSING_REQUIRED_DOMAINS"
    OUT_OF_SUPPORT_BOUNDS = "OUT_OF_SUPPORT_BOUNDS"
    DATA_QUALITY_UNACCEPTABLE = "DATA_QUALITY_UNACCEPTABLE"


class ExecutionMode(StrEnum):
    """Execution mode for HeatZone v3."""

    SHADOW = "SHADOW"
    EVALUATION = "EVALUATION"
    STANDALONE = "STANDALONE"


@dataclass(frozen=True)
class HeatZoneV3Input:
    """Unified multi-dimensional feature input for HeatZone v3 scoring.

    Incorporates all 9 required acceptance dimensions:
    1. Population (total, daytime ratio)
    2. Households (household count)
    3. Housing (residential units/density)
    4. POI (category distribution, count, density)
    5. Competitor capacity (total capacity, active count, store brands, price tiers)
    6. Rent (median rent per ping, mean, sample count)
    7. Listing (active listing count, asking rent)
    8. Own-store capacity (machine counts, existing stores, coverage proof)
    9. Coverage & Platform Support (overall readiness, domain coverage, source support)
    """

    h3_index: str
    h3_resolution: int = 8
    cell_id: str = ""
    tenant_id: str = "default"

    # Dimension 1: Population
    population: float = 0.0
    daytime_population_ratio: float = 1.0

    # Dimension 2: Households
    household_count: float = 0.0

    # Dimension 3: Housing
    housing_units: float = 0.0

    # Dimension 4: POI
    poi_count: int = 0
    poi_categories: dict[str, int] = field(default_factory=dict)

    # Dimension 5: Competitor Capacity
    competitor_capacity: float = 0.0
    active_competitor_count: int = 0
    competitor_brands: list[str] = field(default_factory=list)
    competitor_price_tiers: dict[str, int] = field(default_factory=dict)

    # Dimension 6: Rent
    median_rent_per_ping: float = 0.0
    mean_rent_per_ping: float = 0.0
    rent_sample_count: int = 0

    # Dimension 7: Listing
    active_listing_count: int = 0
    median_listing_rent: float = 0.0

    # Dimension 8: Own-Store Capacity
    own_store_count: int = 0
    own_store_machine_capacity: float = 0.0
    own_store_capacities: list[MachineCapacityRecord] = field(default_factory=list)
    store_coverage_records: list[StoreDayCoverage] = field(default_factory=list)

    # Dimension 9: Coverage & Platform Support
    overall_readiness: ReadinessLevel = ReadinessLevel.ready
    domain_coverage: dict[str, str] = field(default_factory=dict)
    domain_freshness: dict[str, str] = field(default_factory=dict)
    has_coverage_gaps: bool = False
    readiness_reasons: list[str] = field(default_factory=list)
    coverage_ratio: float = 1.0
    is_quarantined: bool = False
    support_level: str = "supported"
    confidence: float = 1.0

    # Spatial and Temporal coordinates
    centroid_lat: float | None = None
    centroid_lng: float | None = None
    county: str = ""
    district: str = ""
    admin_code: str = ""
    period_grain: str = "MONTHLY"
    period_key: str = "2026-08"
    as_of_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    component_manifest_refs: list[ProductComponentRef] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HeatZoneV3ScoreResult:
    """Evaluated HeatZone v3 result for a single cell or catchment."""

    heat_zone_id: str
    h3_index: str
    h3_resolution: int
    score: float | None
    priority_rank: int
    unmet_demand_score: float
    format_fit_score: float
    competitor_pressure_score: float
    cannibalization_risk_score: float
    rent_feasibility_score: float
    listing_availability_score: float
    housing_density_score: float
    demographic_vitality_score: float
    confidence: float
    state: HeatZoneV3State
    abstained: bool
    abstain_reasons: tuple[str, ...]
    is_shadow: bool = True
    execution_mode: ExecutionMode = ExecutionMode.SHADOW
    model_version: str = MODEL_VERSION
    contract_version: str = CONTRACT_ID
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    input_dimensions: dict[str, Any] = field(default_factory=dict)
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    component_manifest_refs: tuple[dict[str, Any], ...] = ()
    county: str = ""
    district: str = ""
    admin_code: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "heat_zone_id": self.heat_zone_id,
            "h3_index": self.h3_index,
            "h3_resolution": self.h3_resolution,
            "score": self.score,
            "priority_rank": self.priority_rank,
            "unmet_demand_score": self.unmet_demand_score,
            "format_fit_score": self.format_fit_score,
            "competitor_pressure_score": self.competitor_pressure_score,
            "cannibalization_risk_score": self.cannibalization_risk_score,
            "rent_feasibility_score": self.rent_feasibility_score,
            "listing_availability_score": self.listing_availability_score,
            "housing_density_score": self.housing_density_score,
            "demographic_vitality_score": self.demographic_vitality_score,
            "confidence": self.confidence,
            "state": self.state.value,
            "abstained": self.abstained,
            "abstain_reasons": list(self.abstain_reasons),
            "is_shadow": self.is_shadow,
            "execution_mode": self.execution_mode.value,
            "model_version": self.model_version,
            "contract_version": self.contract_version,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "input_dimensions": self.input_dimensions,
            "evaluated_at": self.evaluated_at.isoformat(),
            "component_manifest_refs": list(self.component_manifest_refs),
            "county": self.county,
            "district": self.district,
            "admin_code": self.admin_code,
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
                "abstained": self.abstained,
                "is_shadow": self.is_shadow,
                "evaluated_at": self.evaluated_at.isoformat(),
                "model_version": self.model_version,
                "county": self.county,
                "district": self.district,
                "warnings": list(self.warnings),
                "reasons": list(self.reasons),
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> HeatZoneV3ScoreResult:
        evaluated_at_raw = data.get("evaluated_at")
        if isinstance(evaluated_at_raw, datetime):
            evaluated_at = evaluated_at_raw
        elif evaluated_at_raw:
            evaluated_at = datetime.fromisoformat(str(evaluated_at_raw).replace("Z", "+00:00"))
        else:
            evaluated_at = datetime.now(UTC)

        return cls(
            heat_zone_id=str(data["heat_zone_id"]),
            h3_index=str(data["h3_index"]),
            h3_resolution=int(data.get("h3_resolution", 8)),
            score=float(data["score"]) if data.get("score") is not None else None,
            priority_rank=int(data.get("priority_rank", 0)),
            unmet_demand_score=float(data.get("unmet_demand_score", 0.0)),
            format_fit_score=float(data.get("format_fit_score", 0.0)),
            competitor_pressure_score=float(data.get("competitor_pressure_score", 0.0)),
            cannibalization_risk_score=float(data.get("cannibalization_risk_score", 0.0)),
            rent_feasibility_score=float(data.get("rent_feasibility_score", 0.0)),
            listing_availability_score=float(data.get("listing_availability_score", 0.0)),
            housing_density_score=float(data.get("housing_density_score", 0.0)),
            demographic_vitality_score=float(data.get("demographic_vitality_score", 0.0)),
            confidence=float(data.get("confidence", 1.0)),
            state=HeatZoneV3State(data.get("state", HeatZoneV3State.UNTOUCHED)),
            abstained=bool(data.get("abstained", False)),
            abstain_reasons=tuple(str(r) for r in data.get("abstain_reasons", ())),
            is_shadow=bool(data.get("is_shadow", True)),
            execution_mode=ExecutionMode(data.get("execution_mode", ExecutionMode.SHADOW)),
            model_version=str(data.get("model_version", MODEL_VERSION)),
            contract_version=str(data.get("contract_version", CONTRACT_ID)),
            reasons=tuple(str(r) for r in data.get("reasons", ())),
            warnings=tuple(str(w) for w in data.get("warnings", ())),
            input_dimensions=dict(data.get("input_dimensions", {})),
            evaluated_at=evaluated_at,
            component_manifest_refs=tuple(dict(c) for c in data.get("component_manifest_refs", ())),
            county=str(data.get("county", "")),
            district=str(data.get("district", "")),
            admin_code=str(data.get("admin_code", "")),
        )


@dataclass(frozen=True)
class HeatZoneV3ShadowComparison:
    """Side-by-side comparison between HeatZone v3 shadow evaluation and legacy baseline."""

    h3_index: str
    v3_score: float | None
    v3_rank: int | None
    v3_state: str
    baseline_score: float | None
    baseline_rank: int | None
    baseline_state: str | None
    score_delta: float | None
    rank_delta: int | None
    v3_abstained: bool
    agreement: bool
    reasons_drift: tuple[str, ...] = ()
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "h3_index": self.h3_index,
            "v3_score": self.v3_score,
            "v3_rank": self.v3_rank,
            "v3_state": self.v3_state,
            "baseline_score": self.baseline_score,
            "baseline_rank": self.baseline_rank,
            "baseline_state": self.baseline_state,
            "score_delta": self.score_delta,
            "rank_delta": self.rank_delta,
            "v3_abstained": self.v3_abstained,
            "agreement": self.agreement,
            "reasons_drift": list(self.reasons_drift),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> HeatZoneV3ShadowComparison:
        return cls(
            h3_index=str(data["h3_index"]),
            v3_score=float(data["v3_score"]) if data.get("v3_score") is not None else None,
            v3_rank=int(data["v3_rank"]) if data.get("v3_rank") is not None else None,
            v3_state=str(data.get("v3_state", "")),
            baseline_score=float(data["baseline_score"]) if data.get("baseline_score") is not None else None,
            baseline_rank=int(data["baseline_rank"]) if data.get("baseline_rank") is not None else None,
            baseline_state=str(data["baseline_state"]) if data.get("baseline_state") is not None else None,
            score_delta=float(data["score_delta"]) if data.get("score_delta") is not None else None,
            rank_delta=int(data["rank_delta"]) if data.get("rank_delta") is not None else None,
            v3_abstained=bool(data.get("v3_abstained", False)),
            agreement=bool(data.get("agreement", False)),
            reasons_drift=tuple(str(r) for r in data.get("reasons_drift", ())),
            notes=str(data.get("notes", "")),
        )


@dataclass(frozen=True)
class HeatZoneV3BatchResult:
    """Document containing batch evaluated HeatZone v3 shadow scores and comparisons."""

    document_id: str
    total_evaluated: int
    scored_count: int
    abstained_count: int
    scores: tuple[HeatZoneV3ScoreResult, ...]
    comparisons: tuple[HeatZoneV3ShadowComparison, ...]
    shadow_metrics: dict[str, Any]
    contract_version: str = CONTRACT_ID
    execution_mode: ExecutionMode = ExecutionMode.SHADOW
    is_shadow: bool = True
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    manifest_id: str | None = None
    tenant_id: str = "default"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "contract_version": self.contract_version,
            "execution_mode": self.execution_mode.value,
            "is_shadow": self.is_shadow,
            "total_evaluated": self.total_evaluated,
            "scored_count": self.scored_count,
            "abstained_count": self.abstained_count,
            "scores": [s.to_dict() for s in self.scores],
            "map_features": [s.to_map_feature() for s in self.scores],
            "comparisons": [c.to_dict() for c in self.comparisons],
            "shadow_metrics": self.shadow_metrics,
            "evaluated_at": self.evaluated_at.isoformat(),
            "manifest_id": self.manifest_id,
            "tenant_id": self.tenant_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> HeatZoneV3BatchResult:
        evaluated_at_raw = data.get("evaluated_at")
        if isinstance(evaluated_at_raw, datetime):
            evaluated_at = evaluated_at_raw
        elif evaluated_at_raw:
            evaluated_at = datetime.fromisoformat(str(evaluated_at_raw).replace("Z", "+00:00"))
        else:
            evaluated_at = datetime.now(UTC)

        return cls(
            document_id=str(data["document_id"]),
            contract_version=str(data.get("contract_version", CONTRACT_ID)),
            execution_mode=ExecutionMode(data.get("execution_mode", ExecutionMode.SHADOW)),
            is_shadow=bool(data.get("is_shadow", True)),
            total_evaluated=int(data.get("total_evaluated", 0)),
            scored_count=int(data.get("scored_count", 0)),
            abstained_count=int(data.get("abstained_count", 0)),
            scores=tuple(HeatZoneV3ScoreResult.from_dict(s) for s in data.get("scores", ())),
            comparisons=tuple(HeatZoneV3ShadowComparison.from_dict(c) for c in data.get("comparisons", ())),
            shadow_metrics=dict(data.get("shadow_metrics", {})),
            evaluated_at=evaluated_at,
            manifest_id=str(data["manifest_id"]) if data.get("manifest_id") is not None else None,
            tenant_id=str(data.get("tenant_id", "default")),
            metadata=dict(data.get("metadata", {})),
        )


__all__ = [
    "AbstainReasonCode",
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "ExecutionMode",
    "HeatZoneV3BatchResult",
    "HeatZoneV3Input",
    "HeatZoneV3ScoreResult",
    "HeatZoneV3ShadowComparison",
    "HeatZoneV3State",
    "MODEL_VERSION",
]
