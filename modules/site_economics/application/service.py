"""Application service for site economics simulation and decision document generation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from modules.site_economics.domain.contracts import (
    ENGINE_VERSION,
    ScenarioSummary,
    SimulationAssumptionsSnapshot,
    SiteEconomicsDocument,
)
from modules.site_economics.domain.formats import (
    DEFAULT_FORMAT_REGISTRY,
    TargetFormatRegistry,
)
from modules.site_economics.domain.models import (
    FinancialMetricsSummary,
    SiteOperatingParameters,
)
from modules.site_economics.domain.simulator import (
    SimulationInput,
    SiteEconomicsSimulator,
)

try:
    from packages.oday_data_product_contracts_client.models.site_market_context import (
        SiteMarketContext,
        SiteMarketContextDocument,
    )
except ImportError:
    SiteMarketContext = Any  # type: ignore[misc,assignment]
    SiteMarketContextDocument = Any  # type: ignore[misc,assignment]


@dataclass(frozen=True, slots=True)
class SimulationOverrides:
    """Manual parameter overrides applied on top of format and context defaults."""

    format_code: str | None = None
    format_version: str | None = None
    monthly_rent: float | None = None
    area_ping: float | None = None
    lease_term_months: int | None = None
    annual_discount_rate: float | None = None
    debt_ratio: float | None = None
    annual_interest_rate: float | None = None
    demand_multiplier: float | None = None
    competitor_discount: float | None = None
    cannibalization_discount: float | None = None
    custom_equipment_capex: float | None = None
    custom_fitout_capex: float | None = None


class SiteEconomicsService:
    """High-level service coordinating format resolution, context intake, and simulation."""

    def __init__(
        self,
        registry: TargetFormatRegistry | None = None,
        simulator: SiteEconomicsSimulator | None = None,
    ) -> None:
        self.registry = registry or DEFAULT_FORMAT_REGISTRY
        self.simulator = simulator or SiteEconomicsSimulator()

    def evaluate_site(
        self,
        site_id: str,
        area_ping: float,
        monthly_rent: float,
        tenant_id: str = "",
        format_code: str | None = None,
        format_version: str | None = None,
        overrides: SimulationOverrides | None = None,
        horizon_months: int = 60,
        annual_discount_rate: float = 0.08,
    ) -> SiteEconomicsDocument:
        """Evaluate site economics from direct physical / financial parameters."""
        eff_area = (
            overrides.area_ping if overrides and overrides.area_ping is not None else area_ping
        )
        eff_rent = (
            overrides.monthly_rent
            if overrides and overrides.monthly_rent is not None
            else monthly_rent
        )
        eff_format_code = (
            overrides.format_code
            if overrides and overrides.format_code is not None
            else (format_code or self.registry.find_best_format_for_area(eff_area).format_code)
        )
        eff_format_version = (
            overrides.format_version
            if overrides and overrides.format_version is not None
            else format_version
        )

        format_spec = self.registry.get(eff_format_code, eff_format_version)

        operating_params = SiteOperatingParameters(
            monthly_base_rent=eff_rent,
            area_ping=eff_area,
            lease_term_months=(
                overrides.lease_term_months
                if overrides and overrides.lease_term_months is not None
                else 60
            ),
        )

        sim_input = SimulationInput(
            format_spec=format_spec,
            operating_params=operating_params,
            horizon_months=horizon_months,
            annual_discount_rate=(
                overrides.annual_discount_rate
                if overrides and overrides.annual_discount_rate is not None
                else annual_discount_rate
            ),
            demand_multiplier=(
                overrides.demand_multiplier
                if overrides and overrides.demand_multiplier is not None
                else 1.0
            ),
            competitor_discount=(
                overrides.competitor_discount
                if overrides and overrides.competitor_discount is not None
                else 0.0
            ),
            cannibalization_discount=(
                overrides.cannibalization_discount
                if overrides and overrides.cannibalization_discount is not None
                else 0.0
            ),
            custom_equipment_capex=overrides.custom_equipment_capex if overrides else None,
            custom_fitout_capex=overrides.custom_fitout_capex if overrides else None,
            custom_debt_ratio=overrides.debt_ratio if overrides else None,
            custom_interest_rate=overrides.annual_interest_rate if overrides else None,
            custom_rent_amount=eff_rent,
        )

        base_res = self.simulator.simulate(sim_input)
        scenarios = self._generate_scenarios(sim_input)

        assumptions = self._build_assumptions_snapshot(sim_input, base_res.metrics)

        return SiteEconomicsDocument(
            document_id=f"econ-doc-{site_id}-{uuid4().hex[:8]}",
            site_id=site_id,
            tenant_id=tenant_id,
            format_code=format_spec.format_code,
            format_version=format_spec.format_version,
            evaluated_at=datetime.now(UTC).isoformat(),
            horizon_months=horizon_months,
            annual_discount_rate=sim_input.annual_discount_rate,
            metrics=base_res.metrics,
            decision=base_res.decision,
            assumptions=assumptions,
            monthly_schedule=base_res.monthly_schedule,
            scenarios=scenarios,
            engine_version=ENGINE_VERSION,
        )

    def evaluate_site_market_context(
        self,
        market_context: SiteMarketContext | SiteMarketContextDocument | Mapping[str, Any],
        tenant_id: str = "",
        overrides: SimulationOverrides | None = None,
        horizon_months: int = 60,
        annual_discount_rate: float = 0.08,
    ) -> SiteEconomicsDocument:
        """Evaluate site economics using released emgi.site-market-context.v1 contract model."""
        ctx_data: Mapping[str, Any]
        if hasattr(market_context, "context"):
            ctx = market_context.context
            ctx_data = ctx.to_dict() if hasattr(ctx, "to_dict") else ctx
            ctx_id = getattr(market_context, "document_id", None) or ctx_data.get("context_id")
        elif hasattr(market_context, "to_dict"):
            ctx_data = market_context.to_dict()
            ctx_id = ctx_data.get("context_id")
        else:
            ctx_data = market_context
            ctx_id = ctx_data.get("context_id") or ctx_data.get("document_id")

        identity = ctx_data.get("identity", {})
        site_id = str(identity.get("site_id") or ctx_data.get("site_id") or "unknown-site")

        # Derive area and rent
        listing = ctx_data.get("listing", {})
        rent_domain = ctx_data.get("rent", {})
        demand = ctx_data.get("demand", {})
        competitor = ctx_data.get("competitor", {})

        area_ping = float(listing.get("average_area_ping") or 25.0)
        median_rent_per_ping = float(
            listing.get("median_asking_rent_per_ping")
            or rent_domain.get("median_rent_per_ping")
            or 2_200.0
        )
        monthly_rent = area_ping * median_rent_per_ping

        # Demand multiplier derived from population density & daytime population
        total_pop = float(demand.get("total_population") or 15_000.0)
        density = float(demand.get("density_per_sq_km") or 8_000.0)
        # Scale demand multiplier: 1.0 is baseline (~15,000 catchment pop, 8,000 density)
        pop_factor = max(0.5, min(1.8, (total_pop / 15_000.0) ** 0.5))
        dense_factor = max(0.7, min(1.5, (density / 8_000.0) ** 0.3))
        derived_demand_mult = round(pop_factor * dense_factor, 3)

        # Competitor discount
        active_comps = int(competitor.get("active_competitors") or 0)
        comp_density = float(competitor.get("competitor_density_per_sq_km") or 0.0)
        # Moderate saturation dampening
        derived_comp_discount = round(min(0.35, active_comps * 0.04 + comp_density * 0.02), 3)

        eff_area = (
            overrides.area_ping if overrides and overrides.area_ping is not None else area_ping
        )
        eff_rent = (
            overrides.monthly_rent
            if overrides and overrides.monthly_rent is not None
            else monthly_rent
        )
        eff_format_code = (
            overrides.format_code
            if overrides and overrides.format_code is not None
            else self.registry.find_best_format_for_area(eff_area).format_code
        )
        eff_format_version = (
            overrides.format_version if overrides and overrides.format_version is not None else None
        )

        format_spec = self.registry.get(eff_format_code, eff_format_version)

        operating_params = SiteOperatingParameters(
            monthly_base_rent=eff_rent,
            area_ping=eff_area,
            lease_term_months=(
                overrides.lease_term_months
                if overrides and overrides.lease_term_months is not None
                else 60
            ),
        )

        sim_input = SimulationInput(
            format_spec=format_spec,
            operating_params=operating_params,
            horizon_months=horizon_months,
            annual_discount_rate=(
                overrides.annual_discount_rate
                if overrides and overrides.annual_discount_rate is not None
                else annual_discount_rate
            ),
            demand_multiplier=(
                overrides.demand_multiplier
                if overrides and overrides.demand_multiplier is not None
                else derived_demand_mult
            ),
            competitor_discount=(
                overrides.competitor_discount
                if overrides and overrides.competitor_discount is not None
                else derived_comp_discount
            ),
            cannibalization_discount=(
                overrides.cannibalization_discount
                if overrides and overrides.cannibalization_discount is not None
                else 0.0
            ),
            custom_equipment_capex=overrides.custom_equipment_capex if overrides else None,
            custom_fitout_capex=overrides.custom_fitout_capex if overrides else None,
            custom_debt_ratio=overrides.debt_ratio if overrides else None,
            custom_interest_rate=overrides.annual_interest_rate if overrides else None,
            custom_rent_amount=eff_rent,
        )

        base_res = self.simulator.simulate(sim_input)
        scenarios = self._generate_scenarios(sim_input)

        assumptions = self._build_assumptions_snapshot(sim_input, base_res.metrics)

        return SiteEconomicsDocument(
            document_id=f"econ-doc-{site_id}-{uuid4().hex[:8]}",
            site_id=site_id,
            tenant_id=tenant_id,
            format_code=format_spec.format_code,
            format_version=format_spec.format_version,
            evaluated_at=datetime.now(UTC).isoformat(),
            horizon_months=horizon_months,
            annual_discount_rate=sim_input.annual_discount_rate,
            source_market_context_id=str(ctx_id) if ctx_id else None,
            metrics=base_res.metrics,
            decision=base_res.decision,
            assumptions=assumptions,
            monthly_schedule=base_res.monthly_schedule,
            scenarios=scenarios,
            engine_version=ENGINE_VERSION,
            metadata={
                "derived_demand_mult": derived_demand_mult,
                "derived_comp_discount": derived_comp_discount,
            },
        )

    def _generate_scenarios(self, base_input: SimulationInput) -> dict[str, ScenarioSummary]:
        scenarios: dict[str, ScenarioSummary] = {}

        # 1. Base Case
        base_res = self.simulator.simulate(base_input)
        scenarios["base"] = ScenarioSummary(
            scenario_name="Base Case",
            description="Standard baseline projection using current market context.",
            demand_multiplier=base_input.demand_multiplier,
            competitor_discount=base_input.competitor_discount,
            monthly_rent=base_input.operating_params.monthly_base_rent,
            levered_npv=base_res.metrics.levered_npv,
            unlevered_npv=base_res.metrics.unlevered_npv,
            levered_irr=base_res.metrics.levered_irr,
            simple_payback_months=base_res.metrics.simple_payback.payback_months,
            is_payback_censored=base_res.metrics.simple_payback.is_censored,
            average_monthly_ebitda=base_res.metrics.average_monthly_ebitda,
            recommendation=base_res.decision.recommendation.value,
        )

        # 2. Optimistic Case (+15% demand, -10% rent)
        opt_input = SimulationInput(
            format_spec=base_input.format_spec,
            operating_params=base_input.operating_params,
            horizon_months=base_input.horizon_months,
            annual_discount_rate=base_input.annual_discount_rate,
            demand_multiplier=base_input.demand_multiplier * 1.15,
            competitor_discount=max(0.0, base_input.competitor_discount * 0.7),
            custom_rent_amount=base_input.operating_params.monthly_base_rent * 0.90,
            custom_debt_ratio=base_input.custom_debt_ratio,
            custom_interest_rate=base_input.custom_interest_rate,
        )
        opt_res = self.simulator.simulate(opt_input)
        scenarios["optimistic"] = ScenarioSummary(
            scenario_name="Optimistic Growth",
            description="Favorable location with +15% demand uplift and successful rent negotiation (-10%).",
            demand_multiplier=opt_input.demand_multiplier,
            competitor_discount=opt_input.competitor_discount,
            monthly_rent=opt_input.custom_rent_amount or 0.0,
            levered_npv=opt_res.metrics.levered_npv,
            unlevered_npv=opt_res.metrics.unlevered_npv,
            levered_irr=opt_res.metrics.levered_irr,
            simple_payback_months=opt_res.metrics.simple_payback.payback_months,
            is_payback_censored=opt_res.metrics.simple_payback.is_censored,
            average_monthly_ebitda=opt_res.metrics.average_monthly_ebitda,
            recommendation=opt_res.decision.recommendation.value,
        )

        # 3. Pessimistic Case (-20% demand, +15% competitor impact)
        pess_input = SimulationInput(
            format_spec=base_input.format_spec,
            operating_params=base_input.operating_params,
            horizon_months=base_input.horizon_months,
            annual_discount_rate=base_input.annual_discount_rate,
            demand_multiplier=base_input.demand_multiplier * 0.80,
            competitor_discount=min(0.50, base_input.competitor_discount + 0.15),
            custom_rent_amount=base_input.operating_params.monthly_base_rent,
            custom_debt_ratio=base_input.custom_debt_ratio,
            custom_interest_rate=base_input.custom_interest_rate,
        )
        pess_res = self.simulator.simulate(pess_input)
        scenarios["pessimistic"] = ScenarioSummary(
            scenario_name="Pessimistic Headwinds",
            description="Adverse local conditions with -20% demand volume and aggressive competitor opening.",
            demand_multiplier=pess_input.demand_multiplier,
            competitor_discount=pess_input.competitor_discount,
            monthly_rent=pess_input.operating_params.monthly_base_rent,
            levered_npv=pess_res.metrics.levered_npv,
            unlevered_npv=pess_res.metrics.unlevered_npv,
            levered_irr=pess_res.metrics.levered_irr,
            simple_payback_months=pess_res.metrics.simple_payback.payback_months,
            is_payback_censored=pess_res.metrics.simple_payback.is_censored,
            average_monthly_ebitda=pess_res.metrics.average_monthly_ebitda,
            recommendation=pess_res.decision.recommendation.value,
        )

        # 4. Stress Test (-30% demand, +15% rent, +1.5% interest rate)
        stress_input = SimulationInput(
            format_spec=base_input.format_spec,
            operating_params=base_input.operating_params,
            horizon_months=base_input.horizon_months,
            annual_discount_rate=base_input.annual_discount_rate,
            demand_multiplier=base_input.demand_multiplier * 0.70,
            competitor_discount=min(0.60, base_input.competitor_discount + 0.20),
            custom_rent_amount=base_input.operating_params.monthly_base_rent * 1.15,
            custom_debt_ratio=base_input.custom_debt_ratio,
            custom_interest_rate=(
                (
                    base_input.custom_interest_rate
                    or base_input.format_spec.financing_spec.annual_interest_rate
                )
                + 0.015
            ),
        )
        stress_res = self.simulator.simulate(stress_input)
        scenarios["stress_test"] = ScenarioSummary(
            scenario_name="Macro Stress Test",
            description="Severe stagflation: -30% demand, +15% rent increase, and +150bps rate shock.",
            demand_multiplier=stress_input.demand_multiplier,
            competitor_discount=stress_input.competitor_discount,
            monthly_rent=stress_input.custom_rent_amount or 0.0,
            levered_npv=stress_res.metrics.levered_npv,
            unlevered_npv=stress_res.metrics.unlevered_npv,
            levered_irr=stress_res.metrics.levered_irr,
            simple_payback_months=stress_res.metrics.simple_payback.payback_months,
            is_payback_censored=stress_res.metrics.simple_payback.is_censored,
            average_monthly_ebitda=stress_res.metrics.average_monthly_ebitda,
            recommendation=stress_res.decision.recommendation.value,
        )

        return scenarios

    def _build_assumptions_snapshot(
        self, sim_input: SimulationInput, metrics: FinancialMetricsSummary
    ) -> SimulationAssumptionsSnapshot:
        format_spec = sim_input.format_spec
        params = sim_input.operating_params
        return SimulationAssumptionsSnapshot(
            format_code=format_spec.format_code,
            format_version=format_spec.format_version,
            monthly_base_rent=sim_input.custom_rent_amount or params.monthly_base_rent,
            area_ping=params.area_ping,
            total_equipment_capex=(
                sim_input.custom_equipment_capex
                if sim_input.custom_equipment_capex is not None
                else format_spec.machine_mix.total_equipment_capex
            ),
            total_fitout_capex=(
                sim_input.custom_fitout_capex
                if sim_input.custom_fitout_capex is not None
                else format_spec.fitout_spec.compute_total_fitout(params.area_ping)
            ),
            total_initial_cash_outlay=metrics.total_initial_cash_outlay,
            debt_ratio=(
                sim_input.custom_debt_ratio
                if sim_input.custom_debt_ratio is not None
                else format_spec.financing_spec.debt_ratio
            ),
            annual_interest_rate=(
                sim_input.custom_interest_rate
                if sim_input.custom_interest_rate is not None
                else format_spec.financing_spec.annual_interest_rate
            ),
            loan_term_months=format_spec.financing_spec.loan_term_months,
            corporate_tax_rate=format_spec.tax_spec.corporate_tax_rate,
            equipment_salvage_ratio=format_spec.residual_spec.equipment_salvage_ratio,
            demand_multiplier=sim_input.demand_multiplier,
            competitor_discount=sim_input.competitor_discount,
            cannibalization_discount=sim_input.cannibalization_discount,
            lease_term_months=params.lease_term_months,
            lease_deposit_months=params.lease_deposit_months,
            rent_free_months=params.rent_free_months,
        )
