"""Application service for Market Intelligence BFF.

Contract: `odayplus.market-intelligence-api.v2`.
Task ID: `ODP-API-001`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from modules.external_data.application.market_data_facade import (
    MarketDataFacade,
)
from modules.market_intelligence_api.application.auth import (
    MarketIntelligenceError,
    MarketIntelligenceNotFoundError,
    authorize_market_intelligence,
)
from modules.market_intelligence_api.domain.contracts import (
    CONTRACT_ID,
    CONTRACT_VERSION,
    REQUIRED_CONTRACTS,
)
from modules.market_intelligence_api.domain.models import (
    AcquisitionPlanFilter,
    CandidateCellSummary,
    CandidateCompareRequest,
    CandidateCompareResult,
    CandidateSiteSummary,
    CellEvidenceChain,
    CompareScope,
    CoverageFilter,
    DataGapFilter,
    DomainComparisonDelta,
    DomainEvidence,
    SiteEvidenceChain,
)
from modules.market_intelligence_api.infrastructure.repositories import (
    DataPlatformMarketIntelligenceRepository,
    MarketIntelligenceRepository,
)
from packages.oday_data_product_contracts_client.models.coverage_surface import (
    CoverageSurface,
    DataGap,
)
from packages.oday_data_product_contracts_client.models.data_acquisition_plan import (
    DataAcquisitionPlan,
)
from packages.oday_data_product_contracts_client.models.market_cell_profile import (
    MarketCellProfile,
)
from packages.oday_data_product_contracts_client.models.site_market_context import (
    PeriodGrain,
    SiteMarketContext,
)
from shared.auth import Action, Principal
from shared.auth.engine import AuthorizationEngine

_CANONICAL_DOMAIN_ALIASES: dict[str, tuple[str, ...]] = {
    "demand": ("demographics", "demand"),
    "competitor": ("competitor", "competition"),
    "rent": ("rent",),
    "poi": ("poi",),
    "mobility": ("mobility", "transport"),
    "traffic": ("transport", "traffic"),
    "listing": ("property", "listing"),
    "event": ("events", "event"),
}


def _normalize_domain_name(value: Any) -> str:
    candidate = getattr(value, "value", value)
    return str(candidate).casefold().replace("-", "_").replace(" ", "_")


def _canonical_domain_freshness(coverage: Any, domain: str) -> str:
    """Read the canonical per-domain freshness map without inventing a value."""
    values = getattr(coverage, "domain_freshness", None) if coverage is not None else None
    if isinstance(values, Mapping):
        wanted = set(
            _CANONICAL_DOMAIN_ALIASES.get(
                _normalize_domain_name(domain),
                (_normalize_domain_name(domain),),
            )
        )
        for key, value in values.items():
            normalized = _normalize_domain_name(key)
            if normalized in wanted:
                return getattr(value, "value", str(value))
    # `unknown` is a canonical freshness state and explicitly means that the
    # producer did not publish a freshness verdict for this domain.
    return "unknown"


def _canonical_support_values(
    support: Any,
    *,
    coverage: Any,
    domain: str,
) -> dict[str, Any]:
    """Project only fields present in canonical SourceSupportSummary.

    The BFF evidence shape has a few legacy presentation fields (freshness,
    age, negative evidence, confidence).  Only freshness can be sourced from
    the canonical coverage map; age and negative-evidence validity are not
    fields of site/cell source support and therefore remain unknown.
    """
    if support is None:
        return {
            "sources": [],
            "observation_count": None,
            "freshness_state": _canonical_domain_freshness(coverage, domain),
            "age_seconds": None,
            "confidence_pct": None,
            "negative_evidence_valid": None,
            "lineage_refs": [],
        }

    uncertainty_pct = getattr(support, "uncertainty_pct", None)
    confidence_pct = 100.0 - uncertainty_pct if isinstance(uncertainty_pct, (int, float)) else None
    return {
        "sources": list(getattr(support, "source_dataset_ids", []) or []),
        "observation_count": getattr(support, "observation_count", None),
        "freshness_state": _canonical_domain_freshness(coverage, domain),
        "age_seconds": None,
        "confidence_pct": confidence_pct,
        "negative_evidence_valid": None,
        "lineage_refs": list(getattr(support, "source_manifest_ids", []) or []),
    }


def _domain_evidence(
    domain: str,
    status: Any,
    support: Any,
    *,
    coverage: Any,
) -> DomainEvidence:
    """Build evidence without substituting domain measures for source facts."""
    values = _canonical_support_values(support, coverage=coverage, domain=domain)
    return DomainEvidence(
        domain=domain,
        status=getattr(status, "value", str(status)),
        provenance_notes=None,
        **values,
    )


class MarketIntelligenceService:
    """Core BFF service implementing `odayplus.market-intelligence-api.v2`."""

    def __init__(
        self,
        repository: MarketIntelligenceRepository | None = None,
        *,
        facade: MarketDataFacade | None = None,
        auth_engine: AuthorizationEngine | None = None,
        enforce_auth: bool = True,
    ) -> None:
        if repository is not None:
            self._repo = repository
        elif facade is not None:
            self._repo = DataPlatformMarketIntelligenceRepository(facade)
        else:
            raise MarketIntelligenceError(
                "MarketIntelligenceService requires an explicit MarketIntelligenceRepository or MarketDataFacade",
                code="missing_repository",
            )
        self._auth_engine = auth_engine or AuthorizationEngine()
        self._enforce_auth = enforce_auth

    @property
    def repository(self) -> MarketIntelligenceRepository:
        return self._repo

    @property
    def auth_engine(self) -> AuthorizationEngine:
        return self._auth_engine

    @property
    def contract(self) -> str:
        return CONTRACT_ID

    @property
    def version(self) -> str:
        return CONTRACT_VERSION

    # -----------------------------------------------------------------------
    # Product Reads: Site Market Context
    # -----------------------------------------------------------------------

    def get_site_context(
        self,
        site_id: str,
        *,
        period_grain: PeriodGrain | str = PeriodGrain.MONTHLY,
        period_key: str | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> SiteMarketContext:
        """Authorized read of site market context for a single site."""
        effective_tenant = authorize_market_intelligence(
            "site_market_context",
            site_id,
            action=Action.VIEW,
            tenant_id=tenant_id,
            principal=principal,
            auth_engine=self._auth_engine,
            enforce_auth=self._enforce_auth,
        )
        return self._repo.get_site_context(
            site_id,
            period_grain=period_grain,
            period_key=period_key,
            tenant_id=effective_tenant,
            principal=principal,
        )

    def batch_get_site_contexts(
        self,
        site_ids: Sequence[str],
        *,
        period_grain: PeriodGrain | str = PeriodGrain.MONTHLY,
        period_key: str | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> list[SiteMarketContext]:
        """Authorized batch read of site market contexts."""
        effective_tenant = authorize_market_intelligence(
            "site_market_contexts",
            ",".join(site_ids),
            action=Action.VIEW,
            tenant_id=tenant_id,
            principal=principal,
            auth_engine=self._auth_engine,
            enforce_auth=self._enforce_auth,
        )
        results: list[SiteMarketContext] = []
        for site_id in site_ids:
            try:
                ctx = self._repo.get_site_context(
                    site_id,
                    period_grain=period_grain,
                    period_key=period_key,
                    tenant_id=effective_tenant,
                    principal=principal,
                )
                results.append(ctx)
            except MarketIntelligenceNotFoundError:
                continue
        return results

    # -----------------------------------------------------------------------
    # Product Reads: Market Cell Profile
    # -----------------------------------------------------------------------

    def get_market_cell(
        self,
        cell_id: str,
        *,
        period_grain: PeriodGrain | str = PeriodGrain.MONTHLY,
        period_key: str | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> MarketCellProfile:
        """Authorized read of a market cell profile."""
        effective_tenant = authorize_market_intelligence(
            "market_cell_profile",
            cell_id,
            action=Action.VIEW,
            tenant_id=tenant_id,
            principal=principal,
            auth_engine=self._auth_engine,
            enforce_auth=self._enforce_auth,
        )
        return self._repo.get_market_cell_profile(
            cell_id,
            period_grain=period_grain,
            period_key=period_key,
            tenant_id=effective_tenant,
            principal=principal,
        )

    def list_market_cells(
        self,
        cell_ids: Sequence[str] | None = None,
        *,
        h3_resolution: int | None = None,
        admin_code: str | None = None,
        period_grain: PeriodGrain | str = PeriodGrain.MONTHLY,
        period_key: str | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> list[MarketCellProfile]:
        """Authorized list / query of market cell profiles."""
        effective_tenant = authorize_market_intelligence(
            "market_cell_profiles",
            ",".join(cell_ids) if cell_ids else admin_code,
            action=Action.VIEW,
            tenant_id=tenant_id,
            principal=principal,
            auth_engine=self._auth_engine,
            enforce_auth=self._enforce_auth,
        )
        if cell_ids:
            results: list[MarketCellProfile] = []
            for cell_id in cell_ids:
                try:
                    cell = self._repo.get_market_cell_profile(
                        cell_id,
                        period_grain=period_grain,
                        period_key=period_key,
                        tenant_id=effective_tenant,
                        principal=principal,
                    )
                    if h3_resolution is not None and cell.h3_resolution != h3_resolution:
                        continue
                    if admin_code is not None and cell.admin_code != admin_code:
                        continue
                    results.append(cell)
                except MarketIntelligenceNotFoundError:
                    continue
            return results

        # If no cell_ids provided, try fetching document by admin_code / period
        try:
            doc = self._repo.get_market_cell_profile_document(
                period_grain=period_grain,
                period_key=period_key,
                tenant_id=effective_tenant,
                principal=principal,
            )
            filtered = list(doc.cells)
            if h3_resolution is not None:
                filtered = [c for c in filtered if c.h3_resolution == h3_resolution]
            if admin_code is not None:
                filtered = [c for c in filtered if c.admin_code == admin_code]
            return filtered
        except MarketIntelligenceNotFoundError:
            return []

    # -----------------------------------------------------------------------
    # Candidate Compare
    # -----------------------------------------------------------------------

    def compare_candidates(
        self,
        request: CandidateCompareRequest,
        *,
        principal: Principal | None = None,
    ) -> CandidateCompareResult:
        """Perform side-by-side comparison across multiple candidate sites or market cells.

        Invariants:
        1. Explicit missingness: missing domains are NEVER rendered as zero.
        2. Side-by-side comparison across demand, competitor, rent, POI, mobility, events, readiness.
        3. Identifies domain leaders while explicitly listing missing candidates.
        """
        effective_tenant = authorize_market_intelligence(
            "candidate_compare",
            f"sites={','.join(request.site_ids)}:cells={','.join(request.cell_ids)}",
            action=Action.VIEW,
            tenant_id=request.tenant_id,
            principal=principal,
            auth_engine=self._auth_engine,
            enforce_auth=self._enforce_auth,
        )

        candidates: list[CandidateSiteSummary | CandidateCellSummary] = []
        scope = (
            CompareScope.SITE
            if request.site_ids and not request.cell_ids
            else (
                CompareScope.MARKET_CELL
                if request.cell_ids and not request.site_ids
                else CompareScope.HYBRID
            )
        )

        # 1. Fetch site contexts
        for site_id in request.site_ids:
            try:
                ctx = self._repo.get_site_context(
                    site_id,
                    period_grain=request.period_grain,
                    period_key=request.period_key,
                    tenant_id=effective_tenant,
                    principal=principal,
                )
                summary = CandidateSiteSummary.from_site_context(ctx)
                candidates.append(summary)
            except MarketIntelligenceNotFoundError:
                # If site not found, include as missing candidate
                candidates.append(
                    CandidateSiteSummary(
                        site_id=site_id,
                        overall_readiness="unavailable",
                        missing_domains=[
                            "demand",
                            "competitor",
                            "rent",
                            "poi",
                            "mobility",
                            "traffic",
                            "listing",
                            "event",
                        ],
                        reasons=[f"Site context not found for site_id={site_id}"],
                        period_grain=request.period_grain,
                        period_key=request.period_key or "",
                    )
                )

        # 2. Fetch market cells
        for cell_id in request.cell_ids:
            try:
                cell = self._repo.get_market_cell_profile(
                    cell_id,
                    period_grain=request.period_grain,
                    period_key=request.period_key,
                    tenant_id=effective_tenant,
                    principal=principal,
                )
                summary = CandidateCellSummary.from_cell_profile(cell)
                candidates.append(summary)
            except MarketIntelligenceNotFoundError:
                candidates.append(
                    CandidateCellSummary(
                        cell_id=cell_id,
                        h3_index=cell_id,
                        h3_resolution=9,
                        overall_readiness="unavailable",
                        missing_domains=[
                            "demand",
                            "competitor",
                            "rent",
                            "poi",
                            "mobility",
                            "listing",
                            "event",
                        ],
                        period_grain=request.period_grain,
                        period_key=request.period_key or "",
                    )
                )

        # 3. Compute comparative metrics
        domain_comparisons: dict[str, DomainComparisonDelta] = {}
        best_in_class: dict[str, str | None] = {}
        readiness_breakdown: dict[str, str] = {}
        missing_domains_by_candidate: dict[str, list[str]] = {}

        for c in candidates:
            cand_id = c.site_id if isinstance(c, CandidateSiteSummary) else c.cell_id
            readiness_breakdown[cand_id] = c.overall_readiness
            missing_domains_by_candidate[cand_id] = list(c.missing_domains)

        # Demand delta (highest population)
        pop_values: dict[str, Any] = {}
        pop_missing: list[str] = []
        best_pop_id: str | None = None
        max_pop: float = -1.0

        for c in candidates:
            cand_id = c.site_id if isinstance(c, CandidateSiteSummary) else c.cell_id
            if c.population is not None and c.demand_status == "available":
                pop_values[cand_id] = c.population
                if c.population > max_pop:
                    max_pop = c.population
                    best_pop_id = cand_id
            else:
                pop_values[cand_id] = None
                pop_missing.append(cand_id)

        domain_comparisons["demand"] = DomainComparisonDelta(
            domain="demand",
            metric_name="total_population",
            best_candidate_id=best_pop_id,
            values_by_candidate=pop_values,
            missing_candidate_ids=pop_missing,
            summary_text=f"Leader: {best_pop_id} with population {max_pop:.0f}"
            if best_pop_id
            else "No demand data available",
        )
        best_in_class["demand"] = best_pop_id

        # Competitor delta (lowest active competitors among available)
        comp_values: dict[str, Any] = {}
        comp_missing: list[str] = []
        best_comp_id: str | None = None
        min_comp: int = 999999

        for c in candidates:
            cand_id = c.site_id if isinstance(c, CandidateSiteSummary) else c.cell_id
            if c.active_competitors is not None and c.competitor_status == "available":
                comp_values[cand_id] = c.active_competitors
                if c.active_competitors < min_comp:
                    min_comp = c.active_competitors
                    best_comp_id = cand_id
            else:
                comp_values[cand_id] = None
                comp_missing.append(cand_id)

        domain_comparisons["competitor"] = DomainComparisonDelta(
            domain="competitor",
            metric_name="active_competitors",
            best_candidate_id=best_comp_id,
            values_by_candidate=comp_values,
            missing_candidate_ids=comp_missing,
            summary_text=f"Least competitive: {best_comp_id} with {min_comp} competitors"
            if best_comp_id
            else "No competitor data available",
        )
        best_in_class["competitor"] = best_comp_id

        # Rent delta (lowest mean rent per ping among available)
        rent_values: dict[str, Any] = {}
        rent_missing: list[str] = []
        best_rent_id: str | None = None
        min_rent: float = 99999999.0

        for c in candidates:
            cand_id = c.site_id if isinstance(c, CandidateSiteSummary) else c.cell_id
            if c.mean_rent_per_ping is not None and c.rent_status == "available":
                rent_values[cand_id] = c.mean_rent_per_ping
                if c.mean_rent_per_ping < min_rent:
                    min_rent = c.mean_rent_per_ping
                    best_rent_id = cand_id
            else:
                rent_values[cand_id] = None
                rent_missing.append(cand_id)

        domain_comparisons["rent"] = DomainComparisonDelta(
            domain="rent",
            metric_name="mean_rent_per_ping",
            best_candidate_id=best_rent_id,
            values_by_candidate=rent_values,
            missing_candidate_ids=rent_missing,
            summary_text=f"Lowest rent: {best_rent_id} at {min_rent:.1f}/ping"
            if best_rent_id
            else "No rent data available",
        )
        best_in_class["rent"] = best_rent_id

        # POI delta (highest POI count)
        poi_values: dict[str, Any] = {}
        poi_missing: list[str] = []
        best_poi_id: str | None = None
        max_poi: int = -1

        for c in candidates:
            cand_id = c.site_id if isinstance(c, CandidateSiteSummary) else c.cell_id
            if c.total_poi_count is not None and c.poi_status == "available":
                poi_values[cand_id] = c.total_poi_count
                if c.total_poi_count > max_poi:
                    max_poi = c.total_poi_count
                    best_poi_id = cand_id
            else:
                poi_values[cand_id] = None
                poi_missing.append(cand_id)

        domain_comparisons["poi"] = DomainComparisonDelta(
            domain="poi",
            metric_name="total_poi_count",
            best_candidate_id=best_poi_id,
            values_by_candidate=poi_values,
            missing_candidate_ids=poi_missing,
            summary_text=f"Highest commercial amenity density: {best_poi_id} with {max_poi} POIs"
            if best_poi_id
            else "No POI data available",
        )
        best_in_class["poi"] = best_poi_id

        # Mobility delta
        mob_values: dict[str, Any] = {}
        mob_missing: list[str] = []
        best_mob_id: str | None = None
        max_mob: float = -1.0

        for c in candidates:
            cand_id = c.site_id if isinstance(c, CandidateSiteSummary) else c.cell_id
            # `activity_population` is the only common canonical mobility
            # measure published by both product contracts. Resident and
            # visitor populations are distinct measures and must not be
            # substituted for it when it is absent (especially when it is 0).
            val = c.activity_population
            if val is not None and c.mobility_status == "available":
                mob_values[cand_id] = val
                if val > max_mob:
                    max_mob = val
                    best_mob_id = cand_id
            else:
                mob_values[cand_id] = None
                mob_missing.append(cand_id)

        domain_comparisons["mobility"] = DomainComparisonDelta(
            domain="mobility",
            metric_name="activity_population",
            best_candidate_id=best_mob_id,
            values_by_candidate=mob_values,
            missing_candidate_ids=mob_missing,
            summary_text=f"Highest activity population: {best_mob_id} ({max_mob:.0f})"
            if best_mob_id
            else "No activity population data available",
        )
        best_in_class["mobility"] = best_mob_id

        return CandidateCompareResult(
            compare_id=f"cmp-{uuid4()}",
            generated_at=datetime.now(UTC).isoformat(),
            scope=scope,
            candidates=candidates,
            domain_comparisons=domain_comparisons,
            best_in_class=best_in_class,
            readiness_breakdown=readiness_breakdown,
            missing_domains_by_candidate=missing_domains_by_candidate,
            total_candidates=len(candidates),
            period_grain=request.period_grain,
            period_key=request.period_key,
        )

    # -----------------------------------------------------------------------
    # Evidence & Lineage
    # -----------------------------------------------------------------------

    def get_site_evidence(
        self,
        site_id: str,
        *,
        period_grain: PeriodGrain | str = PeriodGrain.MONTHLY,
        period_key: str | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> SiteEvidenceChain:
        """Authorized read of full evidence chain, provenance, and source support for a site."""
        effective_tenant = authorize_market_intelligence(
            "site_evidence",
            site_id,
            action=Action.VIEW,
            tenant_id=tenant_id,
            principal=principal,
            auth_engine=self._auth_engine,
            enforce_auth=self._enforce_auth,
        )
        ctx = self._repo.get_site_context(
            site_id,
            period_grain=period_grain,
            period_key=period_key,
            tenant_id=effective_tenant,
            principal=principal,
        )

        domains: dict[str, DomainEvidence] = {
            "demand": _domain_evidence(
                "demand", ctx.demand.status, ctx.demand.source_support, coverage=ctx.coverage
            ),
            "competitor": _domain_evidence(
                "competitor",
                ctx.competitor.status,
                ctx.competitor.source_support,
                coverage=ctx.coverage,
            ),
            "rent": _domain_evidence(
                "rent", ctx.rent.status, ctx.rent.source_support, coverage=ctx.coverage
            ),
            "poi": _domain_evidence(
                "poi", ctx.poi.status, ctx.poi.source_support, coverage=ctx.coverage
            ),
            "mobility": _domain_evidence(
                "mobility", ctx.mobility.status, ctx.mobility.source_support, coverage=ctx.coverage
            ),
            "traffic": _domain_evidence(
                "traffic", ctx.traffic.status, ctx.traffic.source_support, coverage=ctx.coverage
            ),
            "listing": _domain_evidence(
                "listing", ctx.listing.status, ctx.listing.source_support, coverage=ctx.coverage
            ),
            "event": _domain_evidence(
                "event", ctx.event.status, ctx.event.source_support, coverage=ctx.coverage
            ),
        }

        manifest_refs = [ref.to_dict() for ref in ctx.component_manifest_refs]

        return SiteEvidenceChain(
            site_id=site_id,
            tenant_id=effective_tenant,
            generated_at=datetime.now(UTC).isoformat(),
            overall_confidence_pct=None,
            domains=domains,
            component_manifest_refs=manifest_refs,
            period_grain=ctx.period_grain.value,
            period_key=ctx.period_key,
        )

    def get_cell_evidence(
        self,
        cell_id: str,
        *,
        period_grain: PeriodGrain | str = PeriodGrain.MONTHLY,
        period_key: str | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> CellEvidenceChain:
        """Authorized read of full evidence chain for a market cell."""
        effective_tenant = authorize_market_intelligence(
            "cell_evidence",
            cell_id,
            action=Action.VIEW,
            tenant_id=tenant_id,
            principal=principal,
            auth_engine=self._auth_engine,
            enforce_auth=self._enforce_auth,
        )
        cell = self._repo.get_market_cell_profile(
            cell_id,
            period_grain=period_grain,
            period_key=period_key,
            tenant_id=effective_tenant,
            principal=principal,
        )

        dem_avail = cell.demographics.total_population is not None
        comp_avail = cell.competitors is not None
        domains: dict[str, DomainEvidence] = {
            "demand": _domain_evidence(
                "demand",
                "available" if dem_avail else "unavailable",
                cell.demographics.source_support,
                coverage=cell.coverage,
            ),
            "competitor": _domain_evidence(
                "competitor",
                "available" if comp_avail else "unavailable",
                cell.competitors.source_support if comp_avail else None,
                coverage=cell.coverage,
            ),
            "rent": _domain_evidence(
                "rent",
                "available" if cell.rent.mean_rent_per_ping is not None else "unavailable",
                cell.rent.source_support,
                coverage=cell.coverage,
            ),
            "mobility": _domain_evidence(
                "mobility",
                "available"
                if any(
                    value is not None
                    for value in (
                        cell.mobility.activity_population,
                        cell.mobility.resident_population,
                        cell.mobility.visitor_population,
                        cell.mobility.worker_population,
                    )
                )
                else "unavailable",
                cell.mobility.source_support,
                coverage=cell.coverage,
            ),
        }

        manifest_refs = (
            [ref.to_dict() for ref in cell.component_manifest_refs]
            if hasattr(cell, "component_manifest_refs")
            else []
        )

        return CellEvidenceChain(
            cell_id=cell.cell_id,
            h3_index=cell.h3_index,
            tenant_id=effective_tenant,
            generated_at=datetime.now(UTC).isoformat(),
            domains=domains,
            component_manifest_refs=manifest_refs,
            period_grain=cell.period_grain.value,
            period_key=cell.period_key,
        )

    # -----------------------------------------------------------------------
    # Coverage & Data Gaps
    # -----------------------------------------------------------------------

    def get_coverage_surface(
        self,
        surface_id: str | None = None,
        *,
        filters: CoverageFilter | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> CoverageSurface:
        """Authorized read of domain coverage surfaces."""
        effective_tenant = authorize_market_intelligence(
            "coverage_surface",
            surface_id,
            action=Action.VIEW,
            tenant_id=tenant_id,
            principal=principal,
            auth_engine=self._auth_engine,
            enforce_auth=self._enforce_auth,
        )
        return self._repo.get_coverage_surface(
            surface_id=surface_id,
            filters=filters,
            tenant_id=effective_tenant,
            principal=principal,
        )

    def list_data_gaps(
        self,
        *,
        filters: DataGapFilter | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> list[DataGap]:
        """Authorized read of addressable data gaps across space and domain dimensions."""
        effective_tenant = authorize_market_intelligence(
            "data_gaps",
            None,
            action=Action.VIEW,
            tenant_id=tenant_id,
            principal=principal,
            auth_engine=self._auth_engine,
            enforce_auth=self._enforce_auth,
        )
        return self._repo.list_data_gaps(
            filters=filters,
            tenant_id=effective_tenant,
            principal=principal,
        )

    def get_data_gap(
        self,
        gap_id: str,
        *,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> DataGap:
        """Authorized read of a single data gap."""
        effective_tenant = authorize_market_intelligence(
            "data_gap",
            gap_id,
            action=Action.VIEW,
            tenant_id=tenant_id,
            principal=principal,
            auth_engine=self._auth_engine,
            enforce_auth=self._enforce_auth,
        )
        return self._repo.get_data_gap(
            gap_id,
            tenant_id=effective_tenant,
            principal=principal,
        )

    # -----------------------------------------------------------------------
    # Data Acquisition Plans
    # -----------------------------------------------------------------------

    def list_acquisition_plans(
        self,
        *,
        filters: AcquisitionPlanFilter | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> list[DataAcquisitionPlan]:
        """Authorized query of data acquisition plans."""
        effective_tenant = authorize_market_intelligence(
            "acquisition_plans",
            None,
            action=Action.VIEW,
            tenant_id=tenant_id,
            principal=principal,
            auth_engine=self._auth_engine,
            enforce_auth=self._enforce_auth,
        )
        return self._repo.list_acquisition_plans(
            filters=filters,
            tenant_id=effective_tenant,
            principal=principal,
        )

    def get_acquisition_plan(
        self,
        plan_id: str,
        *,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> DataAcquisitionPlan:
        """Authorized read of a specific data acquisition plan."""
        effective_tenant = authorize_market_intelligence(
            "acquisition_plan",
            plan_id,
            action=Action.VIEW,
            tenant_id=tenant_id,
            principal=principal,
            auth_engine=self._auth_engine,
            enforce_auth=self._enforce_auth,
        )
        return self._repo.get_acquisition_plan(
            plan_id,
            tenant_id=effective_tenant,
            principal=principal,
        )

    def propose_acquisition_plan(
        self,
        plan: DataAcquisitionPlan,
        *,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> DataAcquisitionPlan:
        """Propose or record a new data acquisition plan or bounded source experiment."""
        effective_tenant = authorize_market_intelligence(
            "acquisition_plan",
            plan.plan_id,
            action=Action.CREATE,
            tenant_id=tenant_id,
            principal=principal,
            auth_engine=self._auth_engine,
            enforce_auth=self._enforce_auth,
        )
        return self._repo.save_acquisition_plan(
            plan,
            tenant_id=effective_tenant,
            principal=principal,
        )

    # -----------------------------------------------------------------------
    # Health & Diagnostics
    # -----------------------------------------------------------------------

    def get_diagnostics(
        self,
        *,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> dict[str, Any]:
        """Return authorized runtime diagnostics for the Market Intelligence BFF."""
        authorize_market_intelligence(
            "market_intelligence_diagnostics",
            action=Action.VIEW,
            tenant_id=tenant_id,
            principal=principal,
            auth_engine=self._auth_engine,
            enforce_auth=self._enforce_auth,
        )
        facade_diag = self._repo.facade.get_diagnostics() if hasattr(self._repo, "facade") else {}
        return {
            "contract": self.contract,
            "version": self.version,
            "enforce_auth": self._enforce_auth,
            "required_contracts": list(REQUIRED_CONTRACTS),
            "facade_diagnostics": facade_diag,
        }

    def check_health(self) -> dict[str, Any]:
        """Check release integrity and return service health."""
        facade_health = (
            self._repo.facade.check_health()
            if hasattr(self._repo, "facade")
            else {"status": "healthy"}
        )
        return {
            "status": "healthy" if facade_health.get("status") == "healthy" else "degraded",
            "service": "market_intelligence_bff",
            "contract": self.contract,
            "version": self.version,
            "facade": facade_health,
        }


__all__ = [
    "MarketIntelligenceService",
]
