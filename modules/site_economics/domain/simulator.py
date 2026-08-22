"""Financial simulation engine for ODayPlus site economics.

Performs deterministic, monthly cash flow projections, debt amortization,
tax schedules, discounted cash flow (DCF), NPV, IRR, and censored payback analysis.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from modules.site_economics.domain.formats import (
    DEFAULT_FORMAT_REGISTRY,
    TargetFormatSpec,
)
from modules.site_economics.domain.models import (
    CensoringType,
    DecisionAssessment,
    EconomicsDecision,
    FinancialMetricsSummary,
    MonthlyCashFlowItem,
    PaybackOutcome,
    SiteOperatingParameters,
)


@dataclass(frozen=True, slots=True)
class SimulationInput:
    """Complete inputs required for running site economics simulation."""

    format_spec: TargetFormatSpec
    operating_params: SiteOperatingParameters
    horizon_months: int = 60
    annual_discount_rate: float = 0.08
    demand_multiplier: float = 1.0
    competitor_discount: float = 0.0
    cannibalization_discount: float = 0.0
    custom_equipment_capex: float | None = None
    custom_fitout_capex: float | None = None
    custom_debt_ratio: float | None = None
    custom_interest_rate: float | None = None
    custom_rent_amount: float | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SimulationInput:
        format_spec = (
            TargetFormatSpec.from_dict(data["format_spec"])
            if isinstance(data.get("format_spec"), Mapping)
            else DEFAULT_FORMAT_REGISTRY.get(str(data.get("format_code", "ODAY_G2")))
        )
        return cls(
            format_spec=format_spec,
            operating_params=SiteOperatingParameters.from_dict(data["operating_params"]),
            horizon_months=int(data.get("horizon_months", 60)),
            annual_discount_rate=float(data.get("annual_discount_rate", 0.08)),
            demand_multiplier=float(data.get("demand_multiplier", 1.0)),
            competitor_discount=float(data.get("competitor_discount", 0.0)),
            cannibalization_discount=float(data.get("cannibalization_discount", 0.0)),
            custom_equipment_capex=(
                float(data["custom_equipment_capex"])
                if data.get("custom_equipment_capex") is not None
                else None
            ),
            custom_fitout_capex=(
                float(data["custom_fitout_capex"])
                if data.get("custom_fitout_capex") is not None
                else None
            ),
            custom_debt_ratio=(
                float(data["custom_debt_ratio"])
                if data.get("custom_debt_ratio") is not None
                else None
            ),
            custom_interest_rate=(
                float(data["custom_interest_rate"])
                if data.get("custom_interest_rate") is not None
                else None
            ),
            custom_rent_amount=(
                float(data["custom_rent_amount"])
                if data.get("custom_rent_amount") is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """Output of site economics simulation."""

    metrics: FinancialMetricsSummary
    decision: DecisionAssessment
    monthly_schedule: tuple[MonthlyCashFlowItem, ...]
    unlevered_cash_flows: tuple[float, ...]
    levered_cash_flows: tuple[float, ...]
    simulation_input: SimulationInput


def compute_pmt(principal: float, annual_rate: float, term_months: int) -> float:
    """Compute equal monthly debt installment (PMT)."""
    if principal <= 0.0 or term_months <= 0:
        return 0.0
    monthly_rate = annual_rate / 12.0
    if monthly_rate <= 0.0:
        return principal / term_months
    factor = (1.0 + monthly_rate) ** term_months
    return principal * (monthly_rate * factor) / (factor - 1.0)


def compute_npv(cash_flows: Sequence[float], annual_discount_rate: float) -> float:
    """Compute Net Present Value from monthly cash flows (t = 0, 1, ..., N)."""
    if not cash_flows:
        return 0.0
    monthly_rate = (1.0 + annual_discount_rate) ** (1.0 / 12.0) - 1.0
    npv = 0.0
    for t, cf in enumerate(cash_flows):
        npv += cf / ((1.0 + monthly_rate) ** t)
    return npv


def compute_irr(
    cash_flows: Sequence[float],
    max_iterations: int = 300,
    tolerance: float = 1e-7,
) -> float | None:
    """Compute annualized Internal Rate of Return (IRR) using robust root finding."""
    if len(cash_flows) < 2:
        return None
    has_pos = any(cf > 0 for cf in cash_flows)
    has_neg = any(cf < 0 for cf in cash_flows)
    if not (has_pos and has_neg):
        return None

    def npv_at_r(r_m: float) -> float:
        val = 0.0
        for t, cf in enumerate(cash_flows):
            val += cf / ((1.0 + r_m) ** t)
        return val

    def d_npv_at_r(r_m: float) -> float:
        val = 0.0
        for t, cf in enumerate(cash_flows):
            if t > 0:
                val -= t * cf / ((1.0 + r_m) ** (t + 1))
        return val

    for start_r in (0.01, 0.02, 0.05, -0.02, 0.10, -0.05):
        r = start_r
        converged = False
        for _ in range(max_iterations):
            if r <= -0.99 or r > 5.0:
                break
            f = npv_at_r(r)
            if abs(f) < tolerance:
                converged = True
                break
            df = d_npv_at_r(r)
            if abs(df) < 1e-12:
                break
            step = f / df
            r -= step
        if converged and r > -0.99:
            annual_irr = ((1.0 + r) ** 12.0) - 1.0
            return annual_irr

    low, high = -0.50, 2.0
    f_low = npv_at_r(low)
    f_high = npv_at_r(high)

    if f_low * f_high > 0:
        low, high = -0.90, 5.0
        f_low = npv_at_r(low)
        f_high = npv_at_r(high)

    if f_low * f_high <= 0:
        for _ in range(max_iterations):
            mid = 0.5 * (low + high)
            f_mid = npv_at_r(mid)
            if abs(f_mid) < tolerance or (high - low) < tolerance:
                annual_irr = ((1.0 + mid) ** 12.0) - 1.0
                return annual_irr
            if f_low * f_mid <= 0:
                high = mid
                f_high = f_mid
            else:
                low = mid
                f_low = f_mid

    return None


def compute_payback(
    cash_flows: Sequence[float],
    annual_discount_rate: float | None = None,
    horizon_months: int = 60,
) -> PaybackOutcome:
    """Compute simple or discounted payback period with exact right-censoring detection."""
    if not cash_flows or len(cash_flows) < 2:
        return PaybackOutcome(
            payback_months=None,
            is_censored=True,
            censoring_type=CensoringType.INSUFFICIENT_DATA,
            censored_reason="Cash flow series is empty or has fewer than 2 periods.",
            horizon_months=horizon_months,
        )

    initial_cf = cash_flows[0]
    if initial_cf >= 0:
        return PaybackOutcome(
            payback_months=0.0,
            is_censored=False,
            censoring_type=CensoringType.NOT_CENSORED,
            censored_reason=None,
            horizon_months=horizon_months,
        )

    subsequent_cfs = cash_flows[1:]
    if all(cf <= 0.0 for cf in subsequent_cfs):
        return PaybackOutcome(
            payback_months=None,
            is_censored=True,
            censoring_type=CensoringType.NEGATIVE_CASH_FLOW,
            censored_reason="All operating cash flows are negative or zero; investment never breaks even.",
            horizon_months=horizon_months,
        )

    monthly_rate = (
        ((1.0 + annual_discount_rate) ** (1.0 / 12.0) - 1.0)
        if annual_discount_rate is not None and annual_discount_rate > 0
        else 0.0
    )

    cum = initial_cf
    prev_cum = cum
    for t in range(1, len(cash_flows)):
        cf = cash_flows[t]
        disc_cf = cf / ((1.0 + monthly_rate) ** t) if monthly_rate > 0 else cf
        cum += disc_cf
        if cum >= 0.0:
            if disc_cf > 0:
                fraction = (-prev_cum) / disc_cf
                payback_months = (t - 1) + fraction
            else:
                payback_months = float(t)
            return PaybackOutcome(
                payback_months=round(payback_months, 2),
                is_censored=False,
                censoring_type=CensoringType.NOT_CENSORED,
                censored_reason=None,
                horizon_months=horizon_months,
            )
        prev_cum = cum

    return PaybackOutcome(
        payback_months=None,
        is_censored=True,
        censoring_type=CensoringType.RIGHT_CENSORED,
        censored_reason=f"Cumulative cash flow ({round(cum, 2)} TWD) does not reach breakeven within {horizon_months} months evaluation horizon.",
        horizon_months=horizon_months,
    )


class SiteEconomicsSimulator:
    """Deterministic financial simulator for ODayPlus laundromat stores."""

    def simulate(self, sim_input: SimulationInput) -> SimulationResult:
        format_spec = sim_input.format_spec
        params = sim_input.operating_params
        horizon = sim_input.horizon_months
        discount_rate = sim_input.annual_discount_rate

        # 1. Initial Outlay & Capital Structure
        equipment_capex = (
            sim_input.custom_equipment_capex
            if sim_input.custom_equipment_capex is not None
            else format_spec.machine_mix.total_equipment_capex
        )
        fitout_capex = (
            sim_input.custom_fitout_capex
            if sim_input.custom_fitout_capex is not None
            else format_spec.fitout_spec.compute_total_fitout(params.area_ping)
        )
        total_initial_capex = equipment_capex + fitout_capex
        deposit_amount = params.lease_deposit_amount
        pre_opening = params.pre_opening_working_capital
        total_initial_cash_outlay = total_initial_capex + deposit_amount + pre_opening

        debt_ratio = (
            sim_input.custom_debt_ratio
            if sim_input.custom_debt_ratio is not None
            else format_spec.financing_spec.debt_ratio
        )
        debt_ratio = max(0.0, min(1.0, debt_ratio))
        debt_financed = total_initial_cash_outlay * debt_ratio
        equity_investment = total_initial_cash_outlay - debt_financed

        # 2. Debt Amortization Schedule
        interest_rate = (
            sim_input.custom_interest_rate
            if sim_input.custom_interest_rate is not None
            else format_spec.financing_spec.annual_interest_rate
        )
        loan_term = min(horizon, format_spec.financing_spec.loan_term_months)
        monthly_pmt = compute_pmt(debt_financed, interest_rate, loan_term)
        monthly_interest_rate = interest_rate / 12.0
        remaining_loan = debt_financed

        # 3. Depreciation & Amortization
        equipment_residual_rate = format_spec.residual_spec.equipment_salvage_ratio
        equipment_residual_value = equipment_capex * equipment_residual_rate
        depreciable_equipment = max(0.0, equipment_capex - equipment_residual_value)
        useful_life = (
            format_spec.machine_mix.items[0].machine_model.useful_life_months
            if format_spec.machine_mix.items
            else 84
        )
        monthly_equipment_depreciation = (
            depreciable_equipment / useful_life if useful_life > 0 else 0.0
        )
        fitout_amort_months = min(
            params.lease_term_months, format_spec.fitout_spec.fitout_useful_life_months
        )
        monthly_fitout_amortization = (
            fitout_capex / fitout_amort_months if fitout_amort_months > 0 else 0.0
        )

        # 4. Mature Base Monthly Revenue and Variable Unit Costs
        demand_mult = max(0.0, sim_input.demand_multiplier)
        comp_discount = max(0.0, min(0.95, sim_input.competitor_discount))
        cannib_discount = max(0.0, min(0.95, sim_input.cannibalization_discount))
        net_demand_factor = demand_mult * (1.0 - comp_discount) * (1.0 - cannib_discount)

        days_in_month = 365.25 / 12.0

        base_monthly_cycles = 0.0
        base_mature_gross_revenue = 0.0
        base_variable_water_cost = 0.0
        base_variable_elec_cost = 0.0
        base_variable_gas_cost = 0.0
        base_variable_detergent_cost = 0.0
        base_monthly_machine_maint = 0.0

        for item in format_spec.machine_mix.items:
            turns_day = min(
                item.machine_model.max_turns_per_day,
                item.effective_turns_per_day * net_demand_factor,
            )
            cycles_month = turns_day * item.quantity * days_in_month
            base_monthly_cycles += cycles_month
            base_mature_gross_revenue += cycles_month * item.effective_price_per_cycle
            base_variable_water_cost += (
                cycles_month
                * item.machine_model.water_liters_per_cycle
                * format_spec.utilities_spec.water_rate_per_liter
            )
            base_variable_elec_cost += (
                cycles_month
                * item.machine_model.electricity_kwh_per_cycle
                * format_spec.utilities_spec.electricity_rate_per_kwh
            )
            base_variable_gas_cost += (
                cycles_month
                * item.machine_model.gas_kg_per_cycle
                * format_spec.utilities_spec.gas_rate_per_kg
            )
            base_variable_detergent_cost += (
                cycles_month * item.machine_model.detergent_cost_per_cycle
            )
            base_monthly_machine_maint += (
                item.quantity * item.machine_model.monthly_maintenance_per_unit
            )

        base_rent = (
            sim_input.custom_rent_amount
            if sim_input.custom_rent_amount is not None
            else params.monthly_base_rent
        )

        # 5. Month-by-month Schedule Simulation
        schedule: list[MonthlyCashFlowItem] = []
        unlevered_cfs: list[float] = [-total_initial_cash_outlay]
        levered_cfs: list[float] = [-equity_investment]

        cum_unlevered = -total_initial_cash_outlay
        cum_levered = -equity_investment
        cum_disc_unlevered = -total_initial_cash_outlay
        cum_disc_levered = -equity_investment
        monthly_disc_rate = (1.0 + discount_rate) ** (1.0 / 12.0) - 1.0

        tax_loss_carryforward = 0.0
        dscr_values: list[float] = []

        for m in range(1, horizon + 1):
            year = (m - 1) // 12 + 1
            cal_month = (params.opening_start_calendar_month + m - 2) % 12 + 1
            is_terminal = m == horizon

            ramp_factor = format_spec.ramp_spec.get_ramp_for_month(m)
            seas_factor = format_spec.seasonality_spec.get_factor_for_calendar_month(cal_month)
            composite_scale = ramp_factor * seas_factor

            cycles_count = base_monthly_cycles * composite_scale
            gross_revenue = base_mature_gross_revenue * composite_scale

            if m <= params.rent_free_months:
                rent_expense = 0.0
            else:
                escalation_periods = (year - 1) // max(1, params.rent_escalation_interval_years)
                rent_expense = base_rent * (
                    (1.0 + params.annual_rent_escalation_pct) ** escalation_periods
                )

            util_water = base_variable_water_cost * composite_scale
            util_elec = base_variable_elec_cost * composite_scale
            util_gas = base_variable_gas_cost * composite_scale
            util_fixed = format_spec.utilities_spec.base_monthly_meter_and_telecom
            utilities_expense = util_water + util_elec + util_gas + util_fixed

            maint_contract = format_spec.maintenance_spec.preventative_contract_monthly_base
            maint_per_unit = base_monthly_machine_maint
            maint_reserve = (
                gross_revenue * format_spec.maintenance_spec.variable_repair_reserve_ratio
            )
            maintenance_expense = maint_contract + maint_per_unit + maint_reserve

            royalty_expense = gross_revenue * params.royalty_revenue_pct
            insurance_monthly = params.insurance_annual_cost / 12.0
            cleaning_expense = params.cleaning_monthly_cost
            iot_expense = params.telemetry_iot_monthly_fee
            detergent_cogs = base_variable_detergent_cost * composite_scale
            store_ops_expense = (
                royalty_expense
                + insurance_monthly
                + cleaning_expense
                + iot_expense
                + detergent_cogs
            )

            total_opex = rent_expense + utilities_expense + maintenance_expense + store_ops_expense
            ebitda = gross_revenue - total_opex

            equip_depr = monthly_equipment_depreciation if m <= useful_life else 0.0
            fitout_amort = monthly_fitout_amortization if m <= fitout_amort_months else 0.0
            ebit = ebitda - (equip_depr + fitout_amort)

            if m <= loan_term and remaining_loan > 0.0:
                interest_payment = remaining_loan * monthly_interest_rate
                principal_payment = min(remaining_loan, max(0.0, monthly_pmt - interest_payment))
                remaining_loan -= principal_payment
                debt_service = principal_payment + interest_payment
            else:
                interest_payment = 0.0
                principal_payment = 0.0
                debt_service = 0.0

            if debt_service > 0.0:
                dscr_m = ebitda / debt_service if debt_service > 0 else 0.0
                dscr_values.append(dscr_m)

            taxable_income = ebit - interest_payment
            if taxable_income < 0.0:
                tax_loss_carryforward += abs(taxable_income)
                tax_expense = 0.0
            else:
                if tax_loss_carryforward > 0.0:
                    offset = min(taxable_income, tax_loss_carryforward)
                    taxable_after_offset = taxable_income - offset
                    tax_loss_carryforward -= offset
                else:
                    taxable_after_offset = taxable_income
                tax_expense = max(
                    0.0, taxable_after_offset * format_spec.tax_spec.corporate_tax_rate
                )

            net_income = taxable_income - tax_expense

            unlevered_tax = max(0.0, ebit * format_spec.tax_spec.corporate_tax_rate)
            unlevered_op_cf = ebitda - unlevered_tax
            levered_op_cf = ebitda - tax_expense - debt_service

            terminal_salvage = 0.0
            terminal_deposit = 0.0
            terminal_decomm = 0.0
            if is_terminal:
                terminal_salvage = equipment_residual_value
                if format_spec.residual_spec.recover_lease_deposit:
                    terminal_deposit = deposit_amount
                terminal_decomm = format_spec.residual_spec.decommissioning_and_reinstatement_cost

            terminal_net = terminal_salvage + terminal_deposit - terminal_decomm
            net_unlevered = unlevered_op_cf + terminal_net
            net_levered = levered_op_cf + terminal_net

            cum_unlevered += net_unlevered
            cum_levered += net_levered

            disc_factor = (1.0 + monthly_disc_rate) ** m
            cum_disc_unlevered += net_unlevered / disc_factor
            cum_disc_levered += net_levered / disc_factor

            unlevered_cfs.append(net_unlevered)
            levered_cfs.append(net_levered)

            item = MonthlyCashFlowItem(
                month=m,
                year=year,
                calendar_month=cal_month,
                is_terminal=is_terminal,
                ramp_multiplier=round(ramp_factor, 4),
                seasonality_multiplier=round(seas_factor, 4),
                total_cycles_count=round(cycles_count, 1),
                gross_revenue=round(gross_revenue, 2),
                rent_expense=round(rent_expense, 2),
                utilities_expense=round(utilities_expense, 2),
                maintenance_expense=round(maintenance_expense, 2),
                store_operations_expense=round(store_ops_expense, 2),
                total_opex=round(total_opex, 2),
                ebitda=round(ebitda, 2),
                equipment_depreciation=round(equip_depr, 2),
                fitout_amortization=round(fitout_amort, 2),
                ebit=round(ebit, 2),
                interest_expense=round(interest_payment, 2),
                taxable_income=round(taxable_income, 2),
                tax_expense=round(tax_expense, 2),
                net_income=round(net_income, 2),
                loan_principal_payment=round(principal_payment, 2),
                total_debt_service=round(debt_service, 2),
                remaining_loan_principal=round(remaining_loan, 2),
                unlevered_operating_cash_flow=round(unlevered_op_cf, 2),
                levered_operating_cash_flow=round(levered_op_cf, 2),
                terminal_salvage_inflow=round(terminal_salvage, 2),
                terminal_deposit_return=round(terminal_deposit, 2),
                terminal_decommissioning_outflow=round(terminal_decomm, 2),
                net_unlevered_cash_flow=round(net_unlevered, 2),
                net_levered_cash_flow=round(net_levered, 2),
                cumulative_unlevered_cash_flow=round(cum_unlevered, 2),
                cumulative_levered_cash_flow=round(cum_levered, 2),
                cumulative_discounted_unlevered_cf=round(cum_disc_unlevered, 2),
                cumulative_discounted_levered_cf=round(cum_disc_levered, 2),
            )
            schedule.append(item)

        # 6. Valuation & Returns Metrics
        unlevered_npv = compute_npv(unlevered_cfs, discount_rate)
        levered_npv = compute_npv(levered_cfs, discount_rate)
        unlevered_irr = compute_irr(unlevered_cfs)
        levered_irr = compute_irr(levered_cfs)

        simple_payback = compute_payback(
            levered_cfs, annual_discount_rate=0.0, horizon_months=horizon
        )
        discounted_payback = compute_payback(
            levered_cfs, annual_discount_rate=discount_rate, horizon_months=horizon
        )

        avg_revenue = sum(it.gross_revenue for it in schedule) / horizon
        avg_ebitda = sum(it.ebitda for it in schedule) / horizon
        avg_ebitda_margin = avg_ebitda / avg_revenue if avg_revenue > 0 else 0.0
        avg_net_income = sum(it.net_income for it in schedule) / horizon
        avg_net_margin = avg_net_income / avg_revenue if avg_revenue > 0 else 0.0

        # Breakeven Analysis (Mature month)
        fixed_opex = (
            base_rent
            + format_spec.utilities_spec.base_monthly_meter_and_telecom
            + format_spec.maintenance_spec.preventative_contract_monthly_base
            + base_monthly_machine_maint
            + (params.insurance_annual_cost / 12.0)
            + params.cleaning_monthly_cost
            + params.telemetry_iot_monthly_fee
        )
        avg_cycle_price = (
            base_mature_gross_revenue / base_monthly_cycles if base_monthly_cycles > 0 else 100.0
        )
        avg_var_cost_per_cycle = (
            (
                base_variable_water_cost
                + base_variable_elec_cost
                + base_variable_gas_cost
                + base_variable_detergent_cost
            )
            / base_monthly_cycles
            if base_monthly_cycles > 0
            else 20.0
        )
        royalty_rate = params.royalty_revenue_pct
        maint_reserve_rate = format_spec.maintenance_spec.variable_repair_reserve_ratio
        unit_contribution_margin = (
            avg_cycle_price * (1.0 - royalty_rate - maint_reserve_rate) - avg_var_cost_per_cycle
        )

        be_cycles = (
            (fixed_opex + monthly_pmt) / unit_contribution_margin
            if unit_contribution_margin > 0
            else 0.0
        )
        breakeven_revenue = be_cycles * avg_cycle_price
        total_machines = max(1, format_spec.machine_mix.total_machines)
        breakeven_turns_day = be_cycles / (total_machines * days_in_month)
        max_capacity_cycles = (
            sum(
                it.quantity * it.machine_model.max_turns_per_day
                for it in format_spec.machine_mix.items
            )
            * days_in_month
        )
        breakeven_capacity_util = (
            be_cycles / max_capacity_cycles if max_capacity_cycles > 0 else 0.0
        )

        min_dscr = min(dscr_values) if dscr_values else None
        avg_dscr = sum(dscr_values) / len(dscr_values) if dscr_values else None

        avg_annual_ebit = (sum(it.ebit for it in schedule) / horizon) * 12.0
        tax_rate = format_spec.tax_spec.corporate_tax_rate
        roic = (
            (avg_annual_ebit * (1.0 - tax_rate)) / total_initial_cash_outlay
            if total_initial_cash_outlay > 0
            else 0.0
        )

        avg_annual_levered_cf = (sum(it.net_levered_cash_flow for it in schedule) / horizon) * 12.0
        cash_on_cash = avg_annual_levered_cf / equity_investment if equity_investment > 0 else 0.0
        profitability_index = (
            (levered_npv + equity_investment) / equity_investment if equity_investment > 0 else 0.0
        )

        summary = FinancialMetricsSummary(
            horizon_months=horizon,
            annual_discount_rate=discount_rate,
            total_initial_capex=round(total_initial_capex, 2),
            total_initial_cash_outlay=round(total_initial_cash_outlay, 2),
            equity_investment=round(equity_investment, 2),
            debt_financed=round(debt_financed, 2),
            unlevered_npv=round(unlevered_npv, 2),
            levered_npv=round(levered_npv, 2),
            unlevered_irr=round(unlevered_irr, 4) if unlevered_irr is not None else None,
            levered_irr=round(levered_irr, 4) if levered_irr is not None else None,
            simple_payback=simple_payback,
            discounted_payback=discounted_payback,
            average_monthly_revenue=round(avg_revenue, 2),
            average_monthly_ebitda=round(avg_ebitda, 2),
            average_ebitda_margin=round(avg_ebitda_margin, 4),
            average_monthly_net_income=round(avg_net_income, 2),
            average_net_margin=round(avg_net_margin, 4),
            total_cumulative_levered_cash_flow=round(cum_levered, 2),
            total_cumulative_unlevered_cash_flow=round(cum_unlevered, 2),
            breakeven_monthly_revenue=round(breakeven_revenue, 2),
            breakeven_turns_per_day=round(breakeven_turns_day, 2),
            breakeven_capacity_utilization=round(breakeven_capacity_util, 4),
            min_dscr=round(min_dscr, 2) if min_dscr is not None else None,
            average_dscr=round(avg_dscr, 2) if avg_dscr is not None else None,
            roic_annualized=round(roic, 4),
            cash_on_cash_return_annualized=round(cash_on_cash, 4),
            profitability_index=round(profitability_index, 4),
        )

        decision = self._evaluate_decision(summary)

        return SimulationResult(
            metrics=summary,
            decision=decision,
            monthly_schedule=tuple(schedule),
            unlevered_cash_flows=tuple(unlevered_cfs),
            levered_cash_flows=tuple(levered_cfs),
            simulation_input=sim_input,
        )

    def _evaluate_decision(self, metrics: FinancialMetricsSummary) -> DecisionAssessment:
        reasons: list[str] = []
        risk_flags: list[str] = []

        is_payback_censored = metrics.simple_payback.is_censored
        payback_months = metrics.simple_payback.payback_months or 999.0

        if is_payback_censored:
            risk_flags.append(
                f"Payback is censored ({metrics.simple_payback.censoring_type.value})"
            )

        if metrics.levered_npv < 0:
            risk_flags.append(f"Negative Levered NPV ({metrics.levered_npv:,.0f} TWD)")

        if metrics.min_dscr is not None and metrics.min_dscr < 1.15:
            risk_flags.append(
                f"Tight debt service coverage (min DSCR {metrics.min_dscr:.2f} < 1.15)"
            )

        if metrics.average_ebitda_margin < 0.25:
            risk_flags.append(
                f"Low EBITDA margin ({metrics.average_ebitda_margin * 100:.1f}% < 25%)"
            )

        if (
            not is_payback_censored
            and payback_months <= 36.0
            and metrics.levered_npv > 0
            and (metrics.levered_irr is not None and metrics.levered_irr >= 0.12)
            and (metrics.min_dscr is None or metrics.min_dscr >= 1.20)
        ):
            rec = EconomicsDecision.GO
            confidence = 0.90
            reasons.append(
                f"Strong unit economics with {payback_months:.1f} months payback and positive NPV."
            )
            reasons.append(f"Robust annualized levered IRR of {metrics.levered_irr * 100:.1f}%.")
        elif (
            not is_payback_censored
            and payback_months <= 48.0
            and metrics.levered_npv > 0
            and (metrics.levered_irr is not None and metrics.levered_irr >= 0.07)
        ):
            rec = EconomicsDecision.CONDITIONAL_GO
            confidence = 0.75
            reasons.append(
                f"Viable economics with {payback_months:.1f} months payback. Monitor initial ramp."
            )
        elif is_payback_censored or metrics.levered_npv < -200_000.0 or payback_months > 72.0:
            rec = EconomicsDecision.REJECT
            confidence = 0.85
            reasons.append(
                "Projected returns fail hurdle threshold or payback period exceeds economic ceiling."
            )
        else:
            rec = EconomicsDecision.INVESTIGATE
            confidence = 0.60
            reasons.append(
                "Marginal economics; investigate rent concession or alternative machine layout."
            )

        return DecisionAssessment(
            recommendation=rec,
            confidence_score=confidence,
            reasons=tuple(reasons),
            risk_flags=tuple(risk_flags),
        )
