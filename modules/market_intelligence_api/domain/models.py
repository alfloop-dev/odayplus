"""Domain models for Market Intelligence BFF API.

Contract: `odayplus.market-intelligence-api.v2`.
Task ID: `ODP-API-001`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from packages.oday_data_product_contracts_client.models.coverage_surface import (
    CoverageCell,
    CoverageSurface,
    DataGap,
    ReadinessReason,
)
from packages.oday_data_product_contracts_client.models.data_acquisition_plan import (
    AcquisitionGap,
    DataAcquisitionPlan,
    ExperimentStatus,
    PlanStatus,
    SourceValueExperiment,
)
from packages.oday_data_product_contracts_client.models.market_cell_profile import (
    MarketCellProfile,
)
from packages.oday_data_product_contracts_client.models.site_market_context import (
    DomainStatus,
    PeriodGrain,
    ReadinessLevel,
    SiteMarketContext,
)


class CompareScope(StrEnum):
    """Scope of candidate comparison."""

    SITE = "site"
    MARKET_CELL = "market_cell"
    HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class DomainReadiness:
    """Readiness and missingness state for a single domain."""

    domain: str
    status: str
    readiness: str
    uncertainty_pct: float | None = None
    missing_reason: str | None = None
    observation_count: int = 0
    negative_evidence_valid: bool = False

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "domain": self.domain,
            "status": self.status,
            "readiness": self.readiness,
            "observation_count": self.observation_count,
            "negative_evidence_valid": self.negative_evidence_valid,
        }
        if self.uncertainty_pct is not None:
            data["uncertainty_pct"] = self.uncertainty_pct
        if self.missing_reason is not None:
            data["missing_reason"] = self.missing_reason
        return data


@dataclass(frozen=True, slots=True)
class CandidateSiteSummary:
    """Aggregated site summary for compare and exploratory dashboards."""

    site_id: str
    site_name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    primary_h3_index: str | None = None
    h3_resolution: int = 9
    district: str | None = None
    county: str | None = None
    # Demand
    demand_status: str = "unavailable"
    population: float | None = None
    household_count: float | None = None
    daytime_population_ratio: float | None = None
    # Competitor
    competitor_status: str = "unavailable"
    active_competitors: int | None = None
    competitor_density_per_sq_km: float | None = None
    brands_present: list[str] = field(default_factory=list)
    # Rent
    rent_status: str = "unavailable"
    mean_rent_per_ping: float | None = None
    median_rent_per_ping: float | None = None
    p25_rent_per_ping: float | None = None
    p75_rent_per_ping: float | None = None
    # Listings
    listing_status: str = "unavailable"
    active_listings_count: int | None = None
    mean_asking_rent_per_ping: float | None = None
    # POI
    poi_status: str = "unavailable"
    total_poi_count: int | None = None
    convenience_stores_count: int | None = None
    commercial_centers_count: int | None = None
    transit_stations_count: int | None = None
    # Mobility & Traffic
    mobility_status: str = "unavailable"
    unique_visitors_daily: float | None = None
    stay_duration_minutes_mean: float | None = None
    traffic_status: str = "unavailable"
    hourly_volume_vph: int | None = None
    # Events
    event_status: str = "unavailable"
    active_events_count: int | None = None
    # Readiness & Governance
    overall_readiness: str = "blocked"
    missing_domains: list[str] = field(default_factory=list)
    data_gaps_count: int = 0
    uncertainty_pct: float | None = None
    reasons: list[str] = field(default_factory=list)
    period_grain: str = "MONTHLY"
    period_key: str = ""
    raw_context: SiteMarketContext | None = None

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "site_id": self.site_id,
            "site_name": self.site_name,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "primary_h3_index": self.primary_h3_index,
            "h3_resolution": self.h3_resolution,
            "district": self.district,
            "county": self.county,
            "demand": {
                "status": self.demand_status,
                "population": self.population,
                "household_count": self.household_count,
                "daytime_population_ratio": self.daytime_population_ratio,
            },
            "competitor": {
                "status": self.competitor_status,
                "active_competitors": self.active_competitors,
                "competitor_density_per_sq_km": self.competitor_density_per_sq_km,
                "brands_present": list(self.brands_present),
            },
            "rent": {
                "status": self.rent_status,
                "mean_rent_per_ping": self.mean_rent_per_ping,
                "median_rent_per_ping": self.median_rent_per_ping,
                "p25_rent_per_ping": self.p25_rent_per_ping,
                "p75_rent_per_ping": self.p75_rent_per_ping,
            },
            "listing": {
                "status": self.listing_status,
                "active_listings_count": self.active_listings_count,
                "mean_asking_rent_per_ping": self.mean_asking_rent_per_ping,
            },
            "poi": {
                "status": self.poi_status,
                "total_poi_count": self.total_poi_count,
                "convenience_stores_count": self.convenience_stores_count,
                "commercial_centers_count": self.commercial_centers_count,
                "transit_stations_count": self.transit_stations_count,
            },
            "mobility": {
                "status": self.mobility_status,
                "unique_visitors_daily": self.unique_visitors_daily,
                "stay_duration_minutes_mean": self.stay_duration_minutes_mean,
            },
            "traffic": {
                "status": self.traffic_status,
                "hourly_volume_vph": self.hourly_volume_vph,
            },
            "event": {
                "status": self.event_status,
                "active_events_count": self.active_events_count,
            },
            "overall_readiness": self.overall_readiness,
            "missing_domains": list(self.missing_domains),
            "data_gaps_count": self.data_gaps_count,
            "uncertainty_pct": self.uncertainty_pct,
            "reasons": list(self.reasons),
            "period_grain": self.period_grain,
            "period_key": self.period_key,
        }
        if include_raw and self.raw_context is not None:
            data["raw_context"] = self.raw_context.to_dict()
        return data

    @classmethod
    def from_site_context(
        cls,
        ctx: SiteMarketContext,
        *,
        uncertainty_pct: float | None = None,
        data_gaps_count: int = 0,
    ) -> CandidateSiteSummary:
        missing: list[str] = []
        if ctx.demand.status != DomainStatus.available:
            missing.append("demand")
        if ctx.competitor.status != DomainStatus.available:
            missing.append("competitor")
        if ctx.rent.status != DomainStatus.available:
            missing.append("rent")
        if ctx.poi.status != DomainStatus.available:
            missing.append("poi")
        if ctx.mobility.status != DomainStatus.available:
            missing.append("mobility")
        if ctx.traffic.status != DomainStatus.available:
            missing.append("traffic")
        if ctx.listing.status != DomainStatus.available:
            missing.append("listing")
        if ctx.event.status != DomainStatus.available:
            missing.append("event")

        if hasattr(ctx.coverage, "readiness_reasons") and ctx.coverage.readiness_reasons:
            reasons = [r.detail for r in ctx.coverage.readiness_reasons]
        elif hasattr(ctx.coverage, "reasons") and ctx.coverage.reasons:
            reasons = [r.detail for r in ctx.coverage.reasons]
        else:
            reasons = []

        if hasattr(ctx.coverage, "overall_readiness"):
            overall_readiness = getattr(ctx.coverage.overall_readiness, "value", str(ctx.coverage.overall_readiness))
        elif hasattr(ctx.coverage, "readiness"):
            overall_readiness = getattr(ctx.coverage.readiness, "value", str(ctx.coverage.readiness))
        else:
            overall_readiness = "ready"

        return cls(
            site_id=ctx.identity.site_id,
            site_name=ctx.identity.site_name,
            latitude=ctx.identity.latitude,
            longitude=ctx.identity.longitude,
            primary_h3_index=ctx.identity.primary_h3_index,
            h3_resolution=ctx.identity.h3_resolution,
            district=ctx.identity.district,
            county=ctx.identity.county,
            demand_status=ctx.demand.status.value,
            population=ctx.demand.total_population if ctx.demand.status == DomainStatus.available else None,
            household_count=ctx.demand.household_count if ctx.demand.status == DomainStatus.available else None,
            daytime_population_ratio=ctx.demand.daytime_population_ratio if ctx.demand.status == DomainStatus.available else None,
            competitor_status=ctx.competitor.status.value,
            active_competitors=ctx.competitor.active_competitors if ctx.competitor.status == DomainStatus.available else None,
            competitor_density_per_sq_km=ctx.competitor.competitor_density_per_sq_km if ctx.competitor.status == DomainStatus.available else None,
            brands_present=list(ctx.competitor.brands_present) if ctx.competitor.status == DomainStatus.available else [],
            rent_status=ctx.rent.status.value,
            mean_rent_per_ping=ctx.rent.mean_rent_per_ping if ctx.rent.status == DomainStatus.available else None,
            median_rent_per_ping=ctx.rent.median_rent_per_ping if ctx.rent.status == DomainStatus.available else None,
            p25_rent_per_ping=ctx.rent.p25_rent_per_ping if ctx.rent.status == DomainStatus.available else None,
            p75_rent_per_ping=ctx.rent.p75_rent_per_ping if ctx.rent.status == DomainStatus.available else None,
            listing_status=ctx.listing.status.value,
            active_listings_count=ctx.listing.active_listings_count if ctx.listing.status == DomainStatus.available else None,
            mean_asking_rent_per_ping=ctx.listing.mean_asking_rent_per_ping if ctx.listing.status == DomainStatus.available else None,
            poi_status=ctx.poi.status.value,
            total_poi_count=ctx.poi.total_poi_count if ctx.poi.status == DomainStatus.available else None,
            convenience_stores_count=ctx.poi.convenience_stores_count if ctx.poi.status == DomainStatus.available else None,
            commercial_centers_count=ctx.poi.commercial_centers_count if ctx.poi.status == DomainStatus.available else None,
            transit_stations_count=ctx.poi.transit_stations_count if ctx.poi.status == DomainStatus.available else None,
            mobility_status=ctx.mobility.status.value,
            unique_visitors_daily=(
                getattr(ctx.mobility, "activity_population", None)
                or getattr(ctx.mobility, "resident_population", None)
                or getattr(ctx.mobility, "visitor_population", None)
                or getattr(ctx.mobility, "unique_visitors_daily", None)
            ) if ctx.mobility.status == DomainStatus.available else None,
            stay_duration_minutes_mean=(
                getattr(ctx.mobility, "dwell_time_minutes", None)
                or getattr(ctx.mobility, "stay_duration_minutes_mean", None)
            ) if ctx.mobility.status == DomainStatus.available else None,
            traffic_status=ctx.traffic.status.value,
            hourly_volume_vph=(
                ctx.traffic.hourly_volume_vph
                if ctx.traffic.status == DomainStatus.available
                else None
            ),
            event_status=ctx.event.status.value,
            active_events_count=ctx.event.active_events_count if ctx.event.status == DomainStatus.available else None,
            overall_readiness=overall_readiness,
            missing_domains=missing,
            data_gaps_count=data_gaps_count,
            uncertainty_pct=uncertainty_pct,
            reasons=reasons,
            period_grain=ctx.period_grain.value,
            period_key=ctx.period_key,
            raw_context=ctx,
        )


@dataclass(frozen=True, slots=True)
class CandidateCellSummary:
    """Aggregated market cell summary for compare and spatial exploration."""

    cell_id: str
    h3_index: str
    h3_resolution: int
    admin_code: str | None = None
    county: str | None = None
    district: str | None = None
    # Demand
    demand_status: str = "unavailable"
    population: float | None = None
    household_count: float | None = None
    # Competitor
    competitor_status: str = "unavailable"
    active_competitors: int | None = None
    total_competitors: int | None = None
    # Rent
    rent_status: str = "unavailable"
    mean_rent_per_ping: float | None = None
    median_rent_per_ping: float | None = None
    # Listings
    listing_status: str = "unavailable"
    active_listings_count: int | None = None
    # POI
    poi_status: str = "unavailable"
    total_poi_count: int | None = None
    # Mobility
    mobility_status: str = "unavailable"
    total_foot_traffic: float | None = None
    # Events
    event_status: str = "unavailable"
    active_events_count: int | None = None
    # Readiness
    overall_readiness: str = "blocked"
    missing_domains: list[str] = field(default_factory=list)
    data_gaps_count: int = 0
    uncertainty_pct: float | None = None
    period_grain: str = "MONTHLY"
    period_key: str = ""
    raw_cell: MarketCellProfile | None = None

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "cell_id": self.cell_id,
            "h3_index": self.h3_index,
            "h3_resolution": self.h3_resolution,
            "admin_code": self.admin_code,
            "county": self.county,
            "district": self.district,
            "demand": {
                "status": self.demand_status,
                "population": self.population,
                "household_count": self.household_count,
            },
            "competitor": {
                "status": self.competitor_status,
                "active_competitors": self.active_competitors,
                "total_competitors": self.total_competitors,
            },
            "rent": {
                "status": self.rent_status,
                "mean_rent_per_ping": self.mean_rent_per_ping,
                "median_rent_per_ping": self.median_rent_per_ping,
            },
            "listing": {
                "status": self.listing_status,
                "active_listings_count": self.active_listings_count,
            },
            "poi": {
                "status": self.poi_status,
                "total_poi_count": self.total_poi_count,
            },
            "mobility": {
                "status": self.mobility_status,
                "total_foot_traffic": self.total_foot_traffic,
            },
            "event": {
                "status": self.event_status,
                "active_events_count": self.active_events_count,
            },
            "overall_readiness": self.overall_readiness,
            "missing_domains": list(self.missing_domains),
            "data_gaps_count": self.data_gaps_count,
            "uncertainty_pct": self.uncertainty_pct,
            "period_grain": self.period_grain,
            "period_key": self.period_key,
        }
        if include_raw and self.raw_cell is not None:
            data["raw_cell"] = self.raw_cell.to_dict()
        return data

    @classmethod
    def from_cell_profile(
        cls,
        cell: MarketCellProfile,
        *,
        uncertainty_pct: float | None = None,
        data_gaps_count: int = 0,
    ) -> CandidateCellSummary:
        missing: list[str] = []
        if not cell.demographics or cell.demographics.total_population is None:
            missing.append("demand")
        if cell.competitors is None:
            missing.append("competitor")
        if not cell.rent or cell.rent.mean_rent_per_ping is None:
            missing.append("rent")
        if not cell.mobility or not any(
            value is not None
            for value in (
                cell.mobility.activity_population,
                cell.mobility.resident_population,
                cell.mobility.visitor_population,
                cell.mobility.worker_population,
            )
        ):
            missing.append("mobility")

        # MarketCellProfile v1 does not publish POI, listing, or event
        # components. Keep those compare dimensions explicitly unavailable
        # instead of reading legacy/invented attributes.
        missing.extend(("poi", "listing", "event"))

        if cell.coverage:
            if hasattr(cell.coverage, "overall_readiness"):
                cell_readiness = getattr(cell.coverage.overall_readiness, "value", str(cell.coverage.overall_readiness))
            elif hasattr(cell.coverage, "readiness"):
                cell_readiness = getattr(cell.coverage.readiness, "value", str(cell.coverage.readiness))
            else:
                cell_readiness = "ready"
        else:
            cell_readiness = "unknown"

        return cls(
            cell_id=cell.cell_id,
            h3_index=cell.h3_index,
            h3_resolution=cell.h3_resolution,
            admin_code=cell.admin_code,
            county=cell.county,
            district=cell.district,
            demand_status="available" if cell.demographics and cell.demographics.total_population is not None else "unavailable",
            population=cell.demographics.total_population if cell.demographics else None,
            household_count=cell.demographics.household_count if cell.demographics else None,
            competitor_status="available" if cell.competitors else "unavailable",
            active_competitors=cell.competitors.active_competitors if cell.competitors else None,
            total_competitors=cell.competitors.total_competitors if cell.competitors else None,
            rent_status="available" if cell.rent and cell.rent.mean_rent_per_ping is not None else "unavailable",
            mean_rent_per_ping=cell.rent.mean_rent_per_ping if cell.rent else None,
            median_rent_per_ping=cell.rent.median_rent_per_ping if cell.rent else None,
            listing_status="unavailable",
            active_listings_count=None,
            poi_status="unavailable",
            total_poi_count=None,
            mobility_status=(
                "available"
                if cell.mobility and any(
                    value is not None
                    for value in (
                        cell.mobility.activity_population,
                        cell.mobility.resident_population,
                        cell.mobility.visitor_population,
                        cell.mobility.worker_population,
                    )
                )
                else "unavailable"
            ),
            total_foot_traffic=(
                cell.mobility.activity_population
                if cell.mobility
                else None
            ),
            event_status="unavailable",
            active_events_count=None,
            overall_readiness=cell_readiness,
            missing_domains=missing,
            data_gaps_count=data_gaps_count,
            uncertainty_pct=uncertainty_pct,
            period_grain=cell.period_grain.value,
            period_key=cell.period_key,
            raw_cell=cell,
        )


@dataclass(frozen=True, slots=True)
class CandidateCompareRequest:
    """Request payload for multi-site/cell candidate comparison."""

    site_ids: list[str] = field(default_factory=list)
    cell_ids: list[str] = field(default_factory=list)
    period_grain: str = "MONTHLY"
    period_key: str | None = None
    tenant_id: str | None = None
    include_raw_context: bool = False

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CandidateCompareRequest:
        return cls(
            site_ids=list(data.get("site_ids", [])),
            cell_ids=list(data.get("cell_ids", [])),
            period_grain=str(data.get("period_grain", "MONTHLY")),
            period_key=data.get("period_key"),
            tenant_id=data.get("tenant_id"),
            include_raw_context=bool(data.get("include_raw_context", False)),
        )


@dataclass(frozen=True, slots=True)
class DomainComparisonDelta:
    """Domain-level delta and leader identification across candidates."""

    domain: str
    metric_name: str
    best_candidate_id: str | None
    values_by_candidate: dict[str, Any]
    missing_candidate_ids: list[str] = field(default_factory=list)
    summary_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "metric_name": self.metric_name,
            "best_candidate_id": self.best_candidate_id,
            "values_by_candidate": self.values_by_candidate,
            "missing_candidate_ids": list(self.missing_candidate_ids),
            "summary_text": self.summary_text,
        }


@dataclass(frozen=True, slots=True)
class CandidateCompareResult:
    """Full result of candidate comparison."""

    compare_id: str
    generated_at: str
    scope: CompareScope
    candidates: list[CandidateSiteSummary | CandidateCellSummary]
    domain_comparisons: dict[str, DomainComparisonDelta]
    best_in_class: dict[str, str | None]
    readiness_breakdown: dict[str, str]
    missing_domains_by_candidate: dict[str, list[str]]
    total_candidates: int
    period_grain: str
    period_key: str | None

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        return {
            "compare_id": self.compare_id,
            "generated_at": self.generated_at,
            "scope": self.scope.value,
            "total_candidates": self.total_candidates,
            "period_grain": self.period_grain,
            "period_key": self.period_key,
            "candidates": [c.to_dict(include_raw=include_raw) for c in self.candidates],
            "domain_comparisons": {k: v.to_dict() for k, v in self.domain_comparisons.items()},
            "best_in_class": self.best_in_class,
            "readiness_breakdown": self.readiness_breakdown,
            "missing_domains_by_candidate": self.missing_domains_by_candidate,
        }


@dataclass(frozen=True, slots=True)
class DomainEvidence:
    """Detailed evidence and provenance for a single domain."""

    domain: str
    status: str
    sources: list[str]
    observation_count: int | None
    freshness_state: str | None
    age_seconds: int | None = None
    confidence_pct: float | None = None
    negative_evidence_valid: bool | None = None
    lineage_refs: list[str] = field(default_factory=list)
    provenance_notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "domain": self.domain,
            "status": self.status,
            "sources": list(self.sources),
            "observation_count": self.observation_count,
            "freshness_state": self.freshness_state,
            "negative_evidence_valid": self.negative_evidence_valid,
            "lineage_refs": list(self.lineage_refs),
        }
        if self.age_seconds is not None:
            data["age_seconds"] = self.age_seconds
        if self.confidence_pct is not None:
            data["confidence_pct"] = self.confidence_pct
        if self.provenance_notes is not None:
            data["provenance_notes"] = self.provenance_notes
        return data


@dataclass(frozen=True, slots=True)
class SiteEvidenceChain:
    """Full evidence chain and lineage report for a site context."""

    site_id: str
    tenant_id: str | None
    generated_at: str
    overall_confidence_pct: float | None
    domains: dict[str, DomainEvidence]
    component_manifest_refs: list[dict[str, Any]]
    period_grain: str
    period_key: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "site_id": self.site_id,
            "tenant_id": self.tenant_id,
            "generated_at": self.generated_at,
            "period_grain": self.period_grain,
            "period_key": self.period_key,
            "overall_confidence_pct": self.overall_confidence_pct,
            "domains": {k: v.to_dict() for k, v in self.domains.items()},
            "component_manifest_refs": self.component_manifest_refs,
        }


@dataclass(frozen=True, slots=True)
class CellEvidenceChain:
    """Full evidence chain and lineage report for a market cell."""

    cell_id: str
    h3_index: str
    tenant_id: str | None
    generated_at: str
    domains: dict[str, DomainEvidence]
    component_manifest_refs: list[dict[str, Any]]
    period_grain: str
    period_key: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell_id": self.cell_id,
            "h3_index": self.h3_index,
            "tenant_id": self.tenant_id,
            "generated_at": self.generated_at,
            "period_grain": self.period_grain,
            "period_key": self.period_key,
            "domains": {k: v.to_dict() for k, v in self.domains.items()},
            "component_manifest_refs": self.component_manifest_refs,
        }


@dataclass(frozen=True, slots=True)
class CoverageFilter:
    """Filter parameters for querying coverage surfaces."""

    admin_code: str | None = None
    h3_index: str | None = None
    business_date: str | None = None
    readiness: str | None = None
    state: str | None = None
    tenant_id: str | None = None
    limit: int = 100


@dataclass(frozen=True, slots=True)
class DataGapFilter:
    """Filter parameters for querying data gaps."""

    domain: str | None = None
    gap_kind: str | None = None
    reason_code: str | None = None
    tenant_id: str | None = None
    limit: int = 100


@dataclass(frozen=True, slots=True)
class AcquisitionPlanFilter:
    """Filter parameters for querying data acquisition plans."""

    status: str | None = None
    site_context_id: str | None = None
    coverage_surface_id: str | None = None
    tenant_id: str | None = None
    limit: int = 50


__all__ = [
    "AcquisitionPlanFilter",
    "CandidateCellSummary",
    "CandidateCompareRequest",
    "CandidateCompareResult",
    "CandidateSiteSummary",
    "CellEvidenceChain",
    "CompareScope",
    "CoverageFilter",
    "DataGapFilter",
    "DomainComparisonDelta",
    "DomainReadiness",
    "DomainEvidence",
    "SiteEvidenceChain",
]
