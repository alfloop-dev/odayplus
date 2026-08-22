"""Domain models for ODayPlus Site Economics Simulator.

Implements data structures for versioned machine mix, CAPEX, fitout,
utilities, maintenance, financing, tax, residual value, monthly cash flow
schedules, and financial valuation metrics (NPV, IRR, censored payback).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class MachineClass(StrEnum):
    """Equipment class matching EMGI canonical domain definitions."""

    WASHER = "WASHER"
    DRYER = "DRYER"
    COMBO = "COMBO"
    VENDING = "VENDING"
    OTHER = "OTHER"


class CensoringType(StrEnum):
    """Classification of payback censoring."""

    NOT_CENSORED = "NOT_CENSORED"
    RIGHT_CENSORED = "RIGHT_CENSORED"
    NEGATIVE_CASH_FLOW = "NEGATIVE_CASH_FLOW"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class EconomicsDecision(StrEnum):
    """Decision recommendation category for site investment."""

    GO = "GO"
    CONDITIONAL_GO = "CONDITIONAL_GO"
    REJECT = "REJECT"
    INVESTIGATE = "INVESTIGATE"


@dataclass(frozen=True, slots=True)
class MachineModelSpec:
    """Specification of an individual equipment model."""

    model_id: str
    machine_class: MachineClass
    model_name: str
    capacity_kg: float
    unit_capex: float
    baseline_turns_per_day: float
    max_turns_per_day: float
    base_cycle_price: float
    water_liters_per_cycle: float = 0.0
    electricity_kwh_per_cycle: float = 0.0
    gas_kg_per_cycle: float = 0.0
    detergent_cost_per_cycle: float = 0.0
    monthly_maintenance_per_unit: float = 500.0
    useful_life_months: int = 84
    residual_value_ratio: float = 0.10

    @property
    def residual_value_per_unit(self) -> float:
        return self.unit_capex * self.residual_value_ratio

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MachineModelSpec:
        return cls(
            model_id=str(data["model_id"]),
            machine_class=MachineClass(data["machine_class"]),
            model_name=str(data["model_name"]),
            capacity_kg=float(data["capacity_kg"]),
            unit_capex=float(data["unit_capex"]),
            baseline_turns_per_day=float(data["baseline_turns_per_day"]),
            max_turns_per_day=float(data["max_turns_per_day"]),
            base_cycle_price=float(data["base_cycle_price"]),
            water_liters_per_cycle=float(data.get("water_liters_per_cycle", 0.0)),
            electricity_kwh_per_cycle=float(data.get("electricity_kwh_per_cycle", 0.0)),
            gas_kg_per_cycle=float(data.get("gas_kg_per_cycle", 0.0)),
            detergent_cost_per_cycle=float(data.get("detergent_cost_per_cycle", 0.0)),
            monthly_maintenance_per_unit=float(data.get("monthly_maintenance_per_unit", 500.0)),
            useful_life_months=int(data.get("useful_life_months", 84)),
            residual_value_ratio=float(data.get("residual_value_ratio", 0.10)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "machine_class": self.machine_class.value,
            "model_name": self.model_name,
            "capacity_kg": self.capacity_kg,
            "unit_capex": self.unit_capex,
            "baseline_turns_per_day": self.baseline_turns_per_day,
            "max_turns_per_day": self.max_turns_per_day,
            "base_cycle_price": self.base_cycle_price,
            "water_liters_per_cycle": self.water_liters_per_cycle,
            "electricity_kwh_per_cycle": self.electricity_kwh_per_cycle,
            "gas_kg_per_cycle": self.gas_kg_per_cycle,
            "detergent_cost_per_cycle": self.detergent_cost_per_cycle,
            "monthly_maintenance_per_unit": self.monthly_maintenance_per_unit,
            "useful_life_months": self.useful_life_months,
            "residual_value_ratio": self.residual_value_ratio,
        }


@dataclass(frozen=True, slots=True)
class MachineMixItem:
    """An entry in the machine mix specifying quantity and potential overrides."""

    machine_model: MachineModelSpec
    quantity: int
    custom_price_per_cycle: float | None = None
    custom_turns_per_day: float | None = None

    @property
    def effective_price_per_cycle(self) -> float:
        if self.custom_price_per_cycle is not None:
            return self.custom_price_per_cycle
        return self.machine_model.base_cycle_price

    @property
    def effective_turns_per_day(self) -> float:
        if self.custom_turns_per_day is not None:
            return self.custom_turns_per_day
        return self.machine_model.baseline_turns_per_day

    @property
    def total_capex(self) -> float:
        return self.quantity * self.machine_model.unit_capex

    @property
    def total_residual_value(self) -> float:
        return self.quantity * self.machine_model.residual_value_per_unit

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MachineMixItem:
        return cls(
            machine_model=MachineModelSpec.from_dict(data["machine_model"]),
            quantity=int(data["quantity"]),
            custom_price_per_cycle=(
                float(data["custom_price_per_cycle"])
                if data.get("custom_price_per_cycle") is not None
                else None
            ),
            custom_turns_per_day=(
                float(data["custom_turns_per_day"])
                if data.get("custom_turns_per_day") is not None
                else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "machine_model": self.machine_model.to_dict(),
            "quantity": self.quantity,
        }
        if self.custom_price_per_cycle is not None:
            data["custom_price_per_cycle"] = self.custom_price_per_cycle
        if self.custom_turns_per_day is not None:
            data["custom_turns_per_day"] = self.custom_turns_per_day
        return data


@dataclass(frozen=True, slots=True)
class MachineMixSpec:
    """Versioned specification of full store machine mix."""

    spec_id: str
    version: str
    items: tuple[MachineMixItem, ...]
    installation_and_delivery_fee: float = 80_000.0

    @property
    def total_machines(self) -> int:
        return sum(item.quantity for item in self.items)

    @property
    def total_equipment_capex(self) -> float:
        return sum(item.total_capex for item in self.items) + self.installation_and_delivery_fee

    @property
    def total_residual_value(self) -> float:
        return sum(item.total_residual_value for item in self.items)

    @property
    def machine_counts_by_class(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items:
            cls_name = item.machine_model.machine_class.value
            counts[cls_name] = counts.get(cls_name, 0) + item.quantity
        return counts

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MachineMixSpec:
        items = tuple(MachineMixItem.from_dict(item) for item in data["items"])
        return cls(
            spec_id=str(data["spec_id"]),
            version=str(data["version"]),
            items=items,
            installation_and_delivery_fee=float(
                data.get("installation_and_delivery_fee", 80_000.0)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "version": self.version,
            "items": [item.to_dict() for item in self.items],
            "installation_and_delivery_fee": self.installation_and_delivery_fee,
            "total_machines": self.total_machines,
            "total_equipment_capex": self.total_equipment_capex,
            "total_residual_value": self.total_residual_value,
        }


@dataclass(frozen=True, slots=True)
class FitoutSpec:
    """Versioned fitout and civil engineering cost specification."""

    spec_id: str
    version: str
    base_fitout_cost: float = 650_000.0
    cost_per_ping: float = 32_000.0
    plumbing_upgrade_cost: float = 250_000.0
    electrical_upgrade_cost: float = 220_000.0
    gas_piping_upgrade_cost: float = 180_000.0
    facade_signage_cost: float = 150_000.0
    telemetry_smart_hub_cost: float = 80_000.0
    fitout_useful_life_months: int = 60

    def compute_total_fitout(self, area_ping: float) -> float:
        area_component = max(0.0, area_ping) * self.cost_per_ping
        return (
            self.base_fitout_cost
            + area_component
            + self.plumbing_upgrade_cost
            + self.electrical_upgrade_cost
            + self.gas_piping_upgrade_cost
            + self.facade_signage_cost
            + self.telemetry_smart_hub_cost
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FitoutSpec:
        return cls(
            spec_id=str(data["spec_id"]),
            version=str(data["version"]),
            base_fitout_cost=float(data.get("base_fitout_cost", 650_000.0)),
            cost_per_ping=float(data.get("cost_per_ping", 32_000.0)),
            plumbing_upgrade_cost=float(data.get("plumbing_upgrade_cost", 250_000.0)),
            electrical_upgrade_cost=float(data.get("electrical_upgrade_cost", 220_000.0)),
            gas_piping_upgrade_cost=float(data.get("gas_piping_upgrade_cost", 180_000.0)),
            facade_signage_cost=float(data.get("facade_signage_cost", 150_000.0)),
            telemetry_smart_hub_cost=float(data.get("telemetry_smart_hub_cost", 80_000.0)),
            fitout_useful_life_months=int(data.get("fitout_useful_life_months", 60)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "version": self.version,
            "base_fitout_cost": self.base_fitout_cost,
            "cost_per_ping": self.cost_per_ping,
            "plumbing_upgrade_cost": self.plumbing_upgrade_cost,
            "electrical_upgrade_cost": self.electrical_upgrade_cost,
            "gas_piping_upgrade_cost": self.gas_piping_upgrade_cost,
            "facade_signage_cost": self.facade_signage_cost,
            "telemetry_smart_hub_cost": self.telemetry_smart_hub_cost,
            "fitout_useful_life_months": self.fitout_useful_life_months,
        }


@dataclass(frozen=True, slots=True)
class UtilitiesCostSpec:
    """Versioned utility unit rates and base charge rates."""

    spec_id: str
    version: str
    water_rate_per_liter: float = 0.016
    electricity_rate_per_kwh: float = 4.80
    gas_rate_per_kg: float = 38.0
    base_monthly_meter_and_telecom: float = 3_000.0

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> UtilitiesCostSpec:
        return cls(
            spec_id=str(data["spec_id"]),
            version=str(data["version"]),
            water_rate_per_liter=float(data.get("water_rate_per_liter", 0.016)),
            electricity_rate_per_kwh=float(data.get("electricity_rate_per_kwh", 4.80)),
            gas_rate_per_kg=float(data.get("gas_rate_per_kg", 38.0)),
            base_monthly_meter_and_telecom=float(
                data.get("base_monthly_meter_and_telecom", 3_000.0)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "version": self.version,
            "water_rate_per_liter": self.water_rate_per_liter,
            "electricity_rate_per_kwh": self.electricity_rate_per_kwh,
            "gas_rate_per_kg": self.gas_rate_per_kg,
            "base_monthly_meter_and_telecom": self.base_monthly_meter_and_telecom,
        }


@dataclass(frozen=True, slots=True)
class MaintenanceSpec:
    """Versioned maintenance policy and repair reserve specification."""

    spec_id: str
    version: str
    preventative_contract_monthly_base: float = 4_000.0
    per_machine_monthly_fee: float = 450.0
    variable_repair_reserve_ratio: float = 0.025

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MaintenanceSpec:
        return cls(
            spec_id=str(data["spec_id"]),
            version=str(data["version"]),
            preventative_contract_monthly_base=float(
                data.get("preventative_contract_monthly_base", 4_000.0)
            ),
            per_machine_monthly_fee=float(data.get("per_machine_monthly_fee", 450.0)),
            variable_repair_reserve_ratio=float(data.get("variable_repair_reserve_ratio", 0.025)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "version": self.version,
            "preventative_contract_monthly_base": self.preventative_contract_monthly_base,
            "per_machine_monthly_fee": self.per_machine_monthly_fee,
            "variable_repair_reserve_ratio": self.variable_repair_reserve_ratio,
        }


@dataclass(frozen=True, slots=True)
class FinancingSpec:
    """Versioned financing terms and capital structure."""

    spec_id: str
    version: str
    debt_ratio: float = 0.60
    annual_interest_rate: float = 0.035
    loan_term_months: int = 60
    grace_period_months: int = 0

    @property
    def monthly_interest_rate(self) -> float:
        return self.annual_interest_rate / 12.0

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FinancingSpec:
        return cls(
            spec_id=str(data["spec_id"]),
            version=str(data["version"]),
            debt_ratio=float(data.get("debt_ratio", 0.60)),
            annual_interest_rate=float(data.get("annual_interest_rate", 0.035)),
            loan_term_months=int(data.get("loan_term_months", 60)),
            grace_period_months=int(data.get("grace_period_months", 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "version": self.version,
            "debt_ratio": self.debt_ratio,
            "annual_interest_rate": self.annual_interest_rate,
            "loan_term_months": self.loan_term_months,
            "grace_period_months": self.grace_period_months,
        }


@dataclass(frozen=True, slots=True)
class TaxSpec:
    """Versioned corporate tax and depreciation policy."""

    spec_id: str
    version: str
    corporate_tax_rate: float = 0.20
    tax_loss_carryforward_years: int = 10
    depreciation_method: str = "STRAIGHT_LINE"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TaxSpec:
        return cls(
            spec_id=str(data["spec_id"]),
            version=str(data["version"]),
            corporate_tax_rate=float(data.get("corporate_tax_rate", 0.20)),
            tax_loss_carryforward_years=int(data.get("tax_loss_carryforward_years", 10)),
            depreciation_method=str(data.get("depreciation_method", "STRAIGHT_LINE")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "version": self.version,
            "corporate_tax_rate": self.corporate_tax_rate,
            "tax_loss_carryforward_years": self.tax_loss_carryforward_years,
            "depreciation_method": self.depreciation_method,
        }


@dataclass(frozen=True, slots=True)
class ResidualValueSpec:
    """Versioned salvage value, deposit recovery, and reinstatement specification."""

    spec_id: str
    version: str
    equipment_salvage_ratio: float = 0.12
    fitout_salvage_ratio: float = 0.0
    recover_lease_deposit: bool = True
    decommissioning_and_reinstatement_cost: float = 60_000.0

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ResidualValueSpec:
        return cls(
            spec_id=str(data["spec_id"]),
            version=str(data["version"]),
            equipment_salvage_ratio=float(data.get("equipment_salvage_ratio", 0.12)),
            fitout_salvage_ratio=float(data.get("fitout_salvage_ratio", 0.0)),
            recover_lease_deposit=bool(data.get("recover_lease_deposit", True)),
            decommissioning_and_reinstatement_cost=float(
                data.get("decommissioning_and_reinstatement_cost", 60_000.0)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "version": self.version,
            "equipment_salvage_ratio": self.equipment_salvage_ratio,
            "fitout_salvage_ratio": self.fitout_salvage_ratio,
            "recover_lease_deposit": self.recover_lease_deposit,
            "decommissioning_and_reinstatement_cost": self.decommissioning_and_reinstatement_cost,
        }


@dataclass(frozen=True, slots=True)
class RampCurveSpec:
    """Versioned month-by-month demand ramp multipliers."""

    spec_id: str
    version: str
    monthly_multipliers: tuple[float, ...] = (0.40, 0.55, 0.70, 0.82, 0.90, 0.96, 1.00)

    def get_ramp_for_month(self, month: int) -> float:
        if month <= 0:
            return 0.0
        idx = month - 1
        if idx < len(self.monthly_multipliers):
            return self.monthly_multipliers[idx]
        return 1.0

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RampCurveSpec:
        return cls(
            spec_id=str(data["spec_id"]),
            version=str(data["version"]),
            monthly_multipliers=tuple(
                float(v)
                for v in data.get("monthly_multipliers", [0.40, 0.55, 0.70, 0.82, 0.90, 0.96, 1.00])
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "version": self.version,
            "monthly_multipliers": list(self.monthly_multipliers),
        }


@dataclass(frozen=True, slots=True)
class SeasonalitySpec:
    """Versioned 12-month seasonality adjustments for laundry operations."""

    spec_id: str
    version: str
    monthly_factors: tuple[float, ...] = (
        1.15,  # Jan (winter / cold)
        1.12,  # Feb (Chinese New Year / winter)
        1.08,  # Mar (spring rain)
        1.00,  # Apr
        1.05,  # May (plum rain)
        1.02,  # Jun
        0.90,  # Jul (hot summer / dry)
        0.92,  # Aug
        0.95,  # Sep
        0.98,  # Oct
        1.05,  # Nov
        1.14,  # Dec (winter)
    )

    def get_factor_for_calendar_month(self, calendar_month: int) -> float:
        idx = (calendar_month - 1) % 12
        return self.monthly_factors[idx]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SeasonalitySpec:
        return cls(
            spec_id=str(data["spec_id"]),
            version=str(data["version"]),
            monthly_factors=tuple(
                float(v)
                for v in data.get(
                    "monthly_factors",
                    [1.15, 1.12, 1.08, 1.00, 1.05, 1.02, 0.90, 0.92, 0.95, 0.98, 1.05, 1.14],
                )
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "version": self.version,
            "monthly_factors": list(self.monthly_factors),
        }


@dataclass(frozen=True, slots=True)
class SiteOperatingParameters:
    """Store-specific lease and operating configuration."""

    monthly_base_rent: float
    area_ping: float
    lease_term_months: int = 60
    lease_deposit_months: int = 2
    rent_free_months: int = 1
    annual_rent_escalation_pct: float = 0.03
    rent_escalation_interval_years: int = 3
    pre_opening_working_capital: float = 120_000.0
    royalty_revenue_pct: float = 0.05
    insurance_annual_cost: float = 20_000.0
    cleaning_monthly_cost: float = 9_000.0
    telemetry_iot_monthly_fee: float = 2_500.0
    opening_start_calendar_month: int = 1

    @property
    def lease_deposit_amount(self) -> float:
        return self.monthly_base_rent * self.lease_deposit_months

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SiteOperatingParameters:
        return cls(
            monthly_base_rent=float(data["monthly_base_rent"]),
            area_ping=float(data["area_ping"]),
            lease_term_months=int(data.get("lease_term_months", 60)),
            lease_deposit_months=int(data.get("lease_deposit_months", 2)),
            rent_free_months=int(data.get("rent_free_months", 1)),
            annual_rent_escalation_pct=float(data.get("annual_rent_escalation_pct", 0.03)),
            rent_escalation_interval_years=int(data.get("rent_escalation_interval_years", 3)),
            pre_opening_working_capital=float(data.get("pre_opening_working_capital", 120_000.0)),
            royalty_revenue_pct=float(data.get("royalty_revenue_pct", 0.05)),
            insurance_annual_cost=float(data.get("insurance_annual_cost", 20_000.0)),
            cleaning_monthly_cost=float(data.get("cleaning_monthly_cost", 9_000.0)),
            telemetry_iot_monthly_fee=float(data.get("telemetry_iot_monthly_fee", 2_500.0)),
            opening_start_calendar_month=int(data.get("opening_start_calendar_month", 1)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "monthly_base_rent": self.monthly_base_rent,
            "area_ping": self.area_ping,
            "lease_term_months": self.lease_term_months,
            "lease_deposit_months": self.lease_deposit_months,
            "rent_free_months": self.rent_free_months,
            "annual_rent_escalation_pct": self.annual_rent_escalation_pct,
            "rent_escalation_interval_years": self.rent_escalation_interval_years,
            "pre_opening_working_capital": self.pre_opening_working_capital,
            "royalty_revenue_pct": self.royalty_revenue_pct,
            "insurance_annual_cost": self.insurance_annual_cost,
            "cleaning_monthly_cost": self.cleaning_monthly_cost,
            "telemetry_iot_monthly_fee": self.telemetry_iot_monthly_fee,
            "opening_start_calendar_month": self.opening_start_calendar_month,
        }


@dataclass(frozen=True, slots=True)
class MonthlyCashFlowItem:
    """Detailed financial line items for one month in the projection horizon."""

    month: int
    year: int
    calendar_month: int
    is_terminal: bool
    ramp_multiplier: float
    seasonality_multiplier: float
    total_cycles_count: float
    gross_revenue: float
    rent_expense: float
    utilities_expense: float
    maintenance_expense: float
    store_operations_expense: float
    total_opex: float
    ebitda: float
    equipment_depreciation: float
    fitout_amortization: float
    ebit: float
    interest_expense: float
    taxable_income: float
    tax_expense: float
    net_income: float
    loan_principal_payment: float
    total_debt_service: float
    remaining_loan_principal: float
    unlevered_operating_cash_flow: float
    levered_operating_cash_flow: float
    terminal_salvage_inflow: float = 0.0
    terminal_deposit_return: float = 0.0
    terminal_decommissioning_outflow: float = 0.0
    terminal_loan_payoff: float = 0.0
    net_unlevered_cash_flow: float = 0.0
    net_levered_cash_flow: float = 0.0
    cumulative_unlevered_cash_flow: float = 0.0
    cumulative_levered_cash_flow: float = 0.0
    cumulative_discounted_unlevered_cf: float = 0.0
    cumulative_discounted_levered_cf: float = 0.0

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MonthlyCashFlowItem:
        return cls(
            month=int(data["month"]),
            year=int(data["year"]),
            calendar_month=int(data["calendar_month"]),
            is_terminal=bool(data["is_terminal"]),
            ramp_multiplier=float(data["ramp_multiplier"]),
            seasonality_multiplier=float(data["seasonality_multiplier"]),
            total_cycles_count=float(data["total_cycles_count"]),
            gross_revenue=float(data["gross_revenue"]),
            rent_expense=float(data["rent_expense"]),
            utilities_expense=float(data["utilities_expense"]),
            maintenance_expense=float(data["maintenance_expense"]),
            store_operations_expense=float(data["store_operations_expense"]),
            total_opex=float(data["total_opex"]),
            ebitda=float(data["ebitda"]),
            equipment_depreciation=float(data["equipment_depreciation"]),
            fitout_amortization=float(data["fitout_amortization"]),
            ebit=float(data["ebit"]),
            interest_expense=float(data["interest_expense"]),
            taxable_income=float(data["taxable_income"]),
            tax_expense=float(data["tax_expense"]),
            net_income=float(data["net_income"]),
            loan_principal_payment=float(data["loan_principal_payment"]),
            total_debt_service=float(data["total_debt_service"]),
            remaining_loan_principal=float(data["remaining_loan_principal"]),
            unlevered_operating_cash_flow=float(data["unlevered_operating_cash_flow"]),
            levered_operating_cash_flow=float(data["levered_operating_cash_flow"]),
            terminal_salvage_inflow=float(data.get("terminal_salvage_inflow", 0.0)),
            terminal_deposit_return=float(data.get("terminal_deposit_return", 0.0)),
            terminal_decommissioning_outflow=float(
                data.get("terminal_decommissioning_outflow", 0.0)
            ),
            terminal_loan_payoff=float(data.get("terminal_loan_payoff", 0.0)),
            net_unlevered_cash_flow=float(data.get("net_unlevered_cash_flow", 0.0)),
            net_levered_cash_flow=float(data.get("net_levered_cash_flow", 0.0)),
            cumulative_unlevered_cash_flow=float(data.get("cumulative_unlevered_cash_flow", 0.0)),
            cumulative_levered_cash_flow=float(data.get("cumulative_levered_cash_flow", 0.0)),
            cumulative_discounted_unlevered_cf=float(
                data.get("cumulative_discounted_unlevered_cf", 0.0)
            ),
            cumulative_discounted_levered_cf=float(
                data.get("cumulative_discounted_levered_cf", 0.0)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "month": self.month,
            "year": self.year,
            "calendar_month": self.calendar_month,
            "is_terminal": self.is_terminal,
            "ramp_multiplier": self.ramp_multiplier,
            "seasonality_multiplier": self.seasonality_multiplier,
            "total_cycles_count": self.total_cycles_count,
            "gross_revenue": self.gross_revenue,
            "rent_expense": self.rent_expense,
            "utilities_expense": self.utilities_expense,
            "maintenance_expense": self.maintenance_expense,
            "store_operations_expense": self.store_operations_expense,
            "total_opex": self.total_opex,
            "ebitda": self.ebitda,
            "equipment_depreciation": self.equipment_depreciation,
            "fitout_amortization": self.fitout_amortization,
            "ebit": self.ebit,
            "interest_expense": self.interest_expense,
            "taxable_income": self.taxable_income,
            "tax_expense": self.tax_expense,
            "net_income": self.net_income,
            "loan_principal_payment": self.loan_principal_payment,
            "total_debt_service": self.total_debt_service,
            "remaining_loan_principal": self.remaining_loan_principal,
            "unlevered_operating_cash_flow": self.unlevered_operating_cash_flow,
            "levered_operating_cash_flow": self.levered_operating_cash_flow,
            "terminal_salvage_inflow": self.terminal_salvage_inflow,
            "terminal_deposit_return": self.terminal_deposit_return,
            "terminal_decommissioning_outflow": self.terminal_decommissioning_outflow,
            "terminal_loan_payoff": self.terminal_loan_payoff,
            "net_unlevered_cash_flow": self.net_unlevered_cash_flow,
            "net_levered_cash_flow": self.net_levered_cash_flow,
            "cumulative_unlevered_cash_flow": self.cumulative_unlevered_cash_flow,
            "cumulative_levered_cash_flow": self.cumulative_levered_cash_flow,
            "cumulative_discounted_unlevered_cf": self.cumulative_discounted_unlevered_cf,
            "cumulative_discounted_levered_cf": self.cumulative_discounted_levered_cf,
        }


@dataclass(frozen=True, slots=True)
class PaybackOutcome:
    """Payback period evaluation including right-censoring detection."""

    payback_months: float | None
    is_censored: bool
    censoring_type: CensoringType
    censored_reason: str | None
    horizon_months: int

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PaybackOutcome:
        return cls(
            payback_months=(
                float(data["payback_months"]) if data.get("payback_months") is not None else None
            ),
            is_censored=bool(data["is_censored"]),
            censoring_type=CensoringType(data["censoring_type"]),
            censored_reason=data.get("censored_reason"),
            horizon_months=int(data["horizon_months"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "payback_months": self.payback_months,
            "is_censored": self.is_censored,
            "censoring_type": self.censoring_type.value,
            "censored_reason": self.censored_reason,
            "horizon_months": self.horizon_months,
        }


@dataclass(frozen=True, slots=True)
class FinancialMetricsSummary:
    """Comprehensive investment and returns metrics summary."""

    horizon_months: int
    annual_discount_rate: float
    total_initial_capex: float
    total_initial_cash_outlay: float
    equity_investment: float
    debt_financed: float
    unlevered_npv: float
    levered_npv: float
    unlevered_irr: float | None
    levered_irr: float | None
    simple_payback: PaybackOutcome
    discounted_payback: PaybackOutcome
    average_monthly_revenue: float
    average_monthly_ebitda: float
    average_ebitda_margin: float
    average_monthly_net_income: float
    average_net_margin: float
    total_cumulative_levered_cash_flow: float
    total_cumulative_unlevered_cash_flow: float
    breakeven_monthly_revenue: float
    breakeven_turns_per_day: float
    breakeven_capacity_utilization: float
    min_dscr: float | None
    average_dscr: float | None
    roic_annualized: float
    cash_on_cash_return_annualized: float
    profitability_index: float

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FinancialMetricsSummary:
        return cls(
            horizon_months=int(data["horizon_months"]),
            annual_discount_rate=float(data["annual_discount_rate"]),
            total_initial_capex=float(data["total_initial_capex"]),
            total_initial_cash_outlay=float(data["total_initial_cash_outlay"]),
            equity_investment=float(data["equity_investment"]),
            debt_financed=float(data["debt_financed"]),
            unlevered_npv=float(data["unlevered_npv"]),
            levered_npv=float(data["levered_npv"]),
            unlevered_irr=(
                float(data["unlevered_irr"]) if data.get("unlevered_irr") is not None else None
            ),
            levered_irr=(
                float(data["levered_irr"]) if data.get("levered_irr") is not None else None
            ),
            simple_payback=PaybackOutcome.from_dict(data["simple_payback"]),
            discounted_payback=PaybackOutcome.from_dict(data["discounted_payback"]),
            average_monthly_revenue=float(data["average_monthly_revenue"]),
            average_monthly_ebitda=float(data["average_monthly_ebitda"]),
            average_ebitda_margin=float(data["average_ebitda_margin"]),
            average_monthly_net_income=float(data["average_monthly_net_income"]),
            average_net_margin=float(data["average_net_margin"]),
            total_cumulative_levered_cash_flow=float(data["total_cumulative_levered_cash_flow"]),
            total_cumulative_unlevered_cash_flow=float(
                data["total_cumulative_unlevered_cash_flow"]
            ),
            breakeven_monthly_revenue=float(data["breakeven_monthly_revenue"]),
            breakeven_turns_per_day=float(data["breakeven_turns_per_day"]),
            breakeven_capacity_utilization=float(data["breakeven_capacity_utilization"]),
            min_dscr=(float(data["min_dscr"]) if data.get("min_dscr") is not None else None),
            average_dscr=(
                float(data["average_dscr"]) if data.get("average_dscr") is not None else None
            ),
            roic_annualized=float(data["roic_annualized"]),
            cash_on_cash_return_annualized=float(data["cash_on_cash_return_annualized"]),
            profitability_index=float(data["profitability_index"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "horizon_months": self.horizon_months,
            "annual_discount_rate": self.annual_discount_rate,
            "total_initial_capex": self.total_initial_capex,
            "total_initial_cash_outlay": self.total_initial_cash_outlay,
            "equity_investment": self.equity_investment,
            "debt_financed": self.debt_financed,
            "unlevered_npv": self.unlevered_npv,
            "levered_npv": self.levered_npv,
            "unlevered_irr": self.unlevered_irr,
            "levered_irr": self.levered_irr,
            "simple_payback": self.simple_payback.to_dict(),
            "discounted_payback": self.discounted_payback.to_dict(),
            "average_monthly_revenue": self.average_monthly_revenue,
            "average_monthly_ebitda": self.average_monthly_ebitda,
            "average_ebitda_margin": self.average_ebitda_margin,
            "average_monthly_net_income": self.average_monthly_net_income,
            "average_net_margin": self.average_net_margin,
            "total_cumulative_levered_cash_flow": self.total_cumulative_levered_cash_flow,
            "total_cumulative_unlevered_cash_flow": self.total_cumulative_unlevered_cash_flow,
            "breakeven_monthly_revenue": self.breakeven_monthly_revenue,
            "breakeven_turns_per_day": self.breakeven_turns_per_day,
            "breakeven_capacity_utilization": self.breakeven_capacity_utilization,
            "min_dscr": self.min_dscr,
            "average_dscr": self.average_dscr,
            "roic_annualized": self.roic_annualized,
            "cash_on_cash_return_annualized": self.cash_on_cash_return_annualized,
            "profitability_index": self.profitability_index,
        }


@dataclass(frozen=True, slots=True)
class DecisionAssessment:
    """Automated investment decision assessment and rationale."""

    recommendation: EconomicsDecision
    confidence_score: float
    reasons: tuple[str, ...]
    risk_flags: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DecisionAssessment:
        return cls(
            recommendation=EconomicsDecision(data["recommendation"]),
            confidence_score=float(data["confidence_score"]),
            reasons=tuple(str(r) for r in data.get("reasons", [])),
            risk_flags=tuple(str(rf) for rf in data.get("risk_flags", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendation": self.recommendation.value,
            "confidence_score": self.confidence_score,
            "reasons": list(self.reasons),
            "risk_flags": list(self.risk_flags),
        }
