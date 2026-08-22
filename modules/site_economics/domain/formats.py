"""Versioned target-format specifications and registry for ODayPlus laundromat stores."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from modules.site_economics.domain.models import (
    FinancingSpec,
    FitoutSpec,
    MachineClass,
    MachineMixItem,
    MachineMixSpec,
    MachineModelSpec,
    MaintenanceSpec,
    RampCurveSpec,
    ResidualValueSpec,
    SeasonalitySpec,
    TaxSpec,
    UtilitiesCostSpec,
)

# Standard Machine Catalog
WASHER_LARGE_20KG_V1 = MachineModelSpec(
    model_id="W-20KG-V1",
    machine_class=MachineClass.WASHER,
    model_name="ODay High-Efficiency Commercial Washer 20kg",
    capacity_kg=20.0,
    unit_capex=280_000.0,
    baseline_turns_per_day=7.0,
    max_turns_per_day=20.0,
    base_cycle_price=120.0,
    water_liters_per_cycle=65.0,
    electricity_kwh_per_cycle=0.85,
    gas_kg_per_cycle=0.0,
    detergent_cost_per_cycle=5.0,
    monthly_maintenance_per_unit=550.0,
    useful_life_months=84,
    residual_value_ratio=0.12,
)

WASHER_MEDIUM_14KG_V1 = MachineModelSpec(
    model_id="W-14KG-V1",
    machine_class=MachineClass.WASHER,
    model_name="ODay Commercial Washer 14kg",
    capacity_kg=14.0,
    unit_capex=220_000.0,
    baseline_turns_per_day=6.5,
    max_turns_per_day=20.0,
    base_cycle_price=90.0,
    water_liters_per_cycle=50.0,
    electricity_kwh_per_cycle=0.65,
    gas_kg_per_cycle=0.0,
    detergent_cost_per_cycle=4.0,
    monthly_maintenance_per_unit=450.0,
    useful_life_months=84,
    residual_value_ratio=0.12,
)

WASHER_JUMBO_27KG_V1 = MachineModelSpec(
    model_id="W-27KG-V1",
    machine_class=MachineClass.WASHER,
    model_name="ODay Heavy Commercial Washer 27kg (Bedding/Quilts)",
    capacity_kg=27.0,
    unit_capex=360_000.0,
    baseline_turns_per_day=5.5,
    max_turns_per_day=18.0,
    base_cycle_price=160.0,
    water_liters_per_cycle=90.0,
    electricity_kwh_per_cycle=1.20,
    gas_kg_per_cycle=0.0,
    detergent_cost_per_cycle=7.0,
    monthly_maintenance_per_unit=700.0,
    useful_life_months=84,
    residual_value_ratio=0.12,
)

DRYER_STACK_15KG_V1 = MachineModelSpec(
    model_id="D-STACK-15KG-V1",
    machine_class=MachineClass.DRYER,
    model_name="ODay Stack Gas Dryer (15kg x 2 Pockets)",
    capacity_kg=30.0,  # 15kg * 2
    unit_capex=310_000.0,
    baseline_turns_per_day=12.0,  # 6 turns per pocket
    max_turns_per_day=36.0,
    base_cycle_price=70.0,  # per 30-min dry cycle
    water_liters_per_cycle=0.0,
    electricity_kwh_per_cycle=0.45,
    gas_kg_per_cycle=0.40,
    detergent_cost_per_cycle=0.0,
    monthly_maintenance_per_unit=600.0,
    useful_life_months=84,
    residual_value_ratio=0.12,
)

DRYER_LARGE_25KG_V1 = MachineModelSpec(
    model_id="D-LARGE-25KG-V1",
    machine_class=MachineClass.DRYER,
    model_name="ODay High-Capacity Gas Dryer 25kg",
    capacity_kg=25.0,
    unit_capex=260_000.0,
    baseline_turns_per_day=6.0,
    max_turns_per_day=18.0,
    base_cycle_price=90.0,
    water_liters_per_cycle=0.0,
    electricity_kwh_per_cycle=0.40,
    gas_kg_per_cycle=0.55,
    detergent_cost_per_cycle=0.0,
    monthly_maintenance_per_unit=500.0,
    useful_life_months=84,
    residual_value_ratio=0.12,
)

COMBO_ALL_IN_ONE_12KG_V1 = MachineModelSpec(
    model_id="COMBO-12KG-V1",
    machine_class=MachineClass.COMBO,
    model_name="ODay Continuous Wash-and-Dry Combo 12kg",
    capacity_kg=12.0,
    unit_capex=290_000.0,
    baseline_turns_per_day=4.5,
    max_turns_per_day=12.0,
    base_cycle_price=180.0,
    water_liters_per_cycle=55.0,
    electricity_kwh_per_cycle=1.10,
    gas_kg_per_cycle=0.35,
    detergent_cost_per_cycle=5.0,
    monthly_maintenance_per_unit=650.0,
    useful_life_months=84,
    residual_value_ratio=0.10,
)

VENDING_DETERGENT_SMART_V1 = MachineModelSpec(
    model_id="VEND-SMART-V1",
    machine_class=MachineClass.VENDING,
    model_name="ODay IoT Detergent / Softener & Ancillary Vending",
    capacity_kg=0.0,
    unit_capex=65_000.0,
    baseline_turns_per_day=18.0,  # purchases / day
    max_turns_per_day=100.0,
    base_cycle_price=30.0,
    water_liters_per_cycle=0.0,
    electricity_kwh_per_cycle=0.05,
    gas_kg_per_cycle=0.0,
    detergent_cost_per_cycle=10.0,  # wholesale purchase COGS
    monthly_maintenance_per_unit=200.0,
    useful_life_months=84,
    residual_value_ratio=0.05,
)

PET_WASHER_10KG_V1 = MachineModelSpec(
    model_id="PET-W-10KG-V1",
    machine_class=MachineClass.OTHER,
    model_name="ODay Dedicated Pet Laundry & Disinfection Washer 10kg",
    capacity_kg=10.0,
    unit_capex=190_000.0,
    baseline_turns_per_day=3.5,
    max_turns_per_day=15.0,
    base_cycle_price=150.0,
    water_liters_per_cycle=45.0,
    electricity_kwh_per_cycle=0.70,
    gas_kg_per_cycle=0.0,
    detergent_cost_per_cycle=8.0,
    monthly_maintenance_per_unit=600.0,
    useful_life_months=84,
    residual_value_ratio=0.10,
)


@dataclass(frozen=True, slots=True)
class TargetFormatSpec:
    """Complete specification of a store target format and its versioned parameter set."""

    format_code: str
    format_name: str
    format_version: str
    description: str
    target_area_ping_min: float
    target_area_ping_max: float
    recommended_area_ping: float
    machine_mix: MachineMixSpec
    fitout_spec: FitoutSpec
    utilities_spec: UtilitiesCostSpec
    maintenance_spec: MaintenanceSpec
    financing_spec: FinancingSpec
    tax_spec: TaxSpec
    residual_spec: ResidualValueSpec
    ramp_spec: RampCurveSpec
    seasonality_spec: SeasonalitySpec

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TargetFormatSpec:
        return cls(
            format_code=str(data["format_code"]),
            format_name=str(data["format_name"]),
            format_version=str(data["format_version"]),
            description=str(data.get("description", "")),
            target_area_ping_min=float(data["target_area_ping_min"]),
            target_area_ping_max=float(data["target_area_ping_max"]),
            recommended_area_ping=float(data["recommended_area_ping"]),
            machine_mix=MachineMixSpec.from_dict(data["machine_mix"]),
            fitout_spec=FitoutSpec.from_dict(data["fitout_spec"]),
            utilities_spec=UtilitiesCostSpec.from_dict(data["utilities_spec"]),
            maintenance_spec=MaintenanceSpec.from_dict(data["maintenance_spec"]),
            financing_spec=FinancingSpec.from_dict(data["financing_spec"]),
            tax_spec=TaxSpec.from_dict(data["tax_spec"]),
            residual_spec=ResidualValueSpec.from_dict(data["residual_spec"]),
            ramp_spec=RampCurveSpec.from_dict(data["ramp_spec"]),
            seasonality_spec=SeasonalitySpec.from_dict(data["seasonality_spec"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_code": self.format_code,
            "format_name": self.format_name,
            "format_version": self.format_version,
            "description": self.description,
            "target_area_ping_min": self.target_area_ping_min,
            "target_area_ping_max": self.target_area_ping_max,
            "recommended_area_ping": self.recommended_area_ping,
            "machine_mix": self.machine_mix.to_dict(),
            "fitout_spec": self.fitout_spec.to_dict(),
            "utilities_spec": self.utilities_spec.to_dict(),
            "maintenance_spec": self.maintenance_spec.to_dict(),
            "financing_spec": self.financing_spec.to_dict(),
            "tax_spec": self.tax_spec.to_dict(),
            "residual_spec": self.residual_spec.to_dict(),
            "ramp_spec": self.ramp_spec.to_dict(),
            "seasonality_spec": self.seasonality_spec.to_dict(),
        }


def build_default_g2_standard_v1() -> TargetFormatSpec:
    """Standard 25-ping ODay G2 laundromat format (v1.0.0)."""
    machine_items = (
        MachineMixItem(machine_model=WASHER_LARGE_20KG_V1, quantity=3),
        MachineMixItem(machine_model=WASHER_MEDIUM_14KG_V1, quantity=2),
        MachineMixItem(machine_model=DRYER_STACK_15KG_V1, quantity=4),
        MachineMixItem(machine_model=COMBO_ALL_IN_ONE_12KG_V1, quantity=1),
        MachineMixItem(machine_model=VENDING_DETERGENT_SMART_V1, quantity=1),
    )
    machine_mix = MachineMixSpec(
        spec_id="MIX-G2-STD-V1",
        version="1.0.0",
        items=machine_items,
        installation_and_delivery_fee=80_000.0,
    )
    fitout = FitoutSpec(
        spec_id="FIT-G2-STD-V1",
        version="1.0.0",
        base_fitout_cost=350_000.0,
        cost_per_ping=22_000.0,
        plumbing_upgrade_cost=140_000.0,
        electrical_upgrade_cost=120_000.0,
        gas_piping_upgrade_cost=90_000.0,
        facade_signage_cost=80_000.0,
        telemetry_smart_hub_cost=50_000.0,
        fitout_useful_life_months=60,
    )
    return TargetFormatSpec(
        format_code="ODAY_G2",
        format_name="ODay Generation 2 Standard Store",
        format_version="1.0.0",
        description="Standard urban / suburban self-service laundromat format for 20-35 ping locations.",
        target_area_ping_min=18.0,
        target_area_ping_max=35.0,
        recommended_area_ping=25.0,
        machine_mix=machine_mix,
        fitout_spec=fitout,
        utilities_spec=UtilitiesCostSpec(spec_id="UTIL-V1", version="1.0.0"),
        maintenance_spec=MaintenanceSpec(spec_id="MAINT-V1", version="1.0.0"),
        financing_spec=FinancingSpec(
            spec_id="FIN-V1",
            version="1.0.0",
            debt_ratio=0.60,
            annual_interest_rate=0.035,
            loan_term_months=60,
        ),
        tax_spec=TaxSpec(spec_id="TAX-V1", version="1.0.0", corporate_tax_rate=0.20),
        residual_spec=ResidualValueSpec(
            spec_id="RES-V1", version="1.0.0", equipment_salvage_ratio=0.12
        ),
        ramp_spec=RampCurveSpec(spec_id="RAMP-V1", version="1.0.0"),
        seasonality_spec=SeasonalitySpec(spec_id="SEAS-V1", version="1.0.0"),
    )


def build_default_g3_compact_v1() -> TargetFormatSpec:
    """Compact 18-ping urban micro-format (v1.0.0)."""
    machine_items = (
        MachineMixItem(machine_model=WASHER_LARGE_20KG_V1, quantity=2),
        MachineMixItem(machine_model=WASHER_MEDIUM_14KG_V1, quantity=2),
        MachineMixItem(machine_model=DRYER_STACK_15KG_V1, quantity=3),
        MachineMixItem(machine_model=VENDING_DETERGENT_SMART_V1, quantity=1),
    )
    machine_mix = MachineMixSpec(
        spec_id="MIX-G3-CMP-V1",
        version="1.0.0",
        items=machine_items,
        installation_and_delivery_fee=60_000.0,
    )
    fitout = FitoutSpec(
        spec_id="FIT-G3-CMP-V1",
        version="1.0.0",
        base_fitout_cost=280_000.0,
        cost_per_ping=20_000.0,
        plumbing_upgrade_cost=110_000.0,
        electrical_upgrade_cost=90_000.0,
        gas_piping_upgrade_cost=70_000.0,
        facade_signage_cost=60_000.0,
        telemetry_smart_hub_cost=40_000.0,
        fitout_useful_life_months=60,
    )
    return TargetFormatSpec(
        format_code="ODAY_G3_COMPACT",
        format_name="ODay Generation 3 Compact Urban Store",
        format_version="1.0.0",
        description="High-density micro-store format for high-rent street-front locations under 20 pings.",
        target_area_ping_min=14.0,
        target_area_ping_max=22.0,
        recommended_area_ping=18.0,
        machine_mix=machine_mix,
        fitout_spec=fitout,
        utilities_spec=UtilitiesCostSpec(spec_id="UTIL-V1", version="1.0.0"),
        maintenance_spec=MaintenanceSpec(spec_id="MAINT-V1", version="1.0.0"),
        financing_spec=FinancingSpec(
            spec_id="FIN-V1",
            version="1.0.0",
            debt_ratio=0.55,
            annual_interest_rate=0.035,
            loan_term_months=60,
        ),
        tax_spec=TaxSpec(spec_id="TAX-V1", version="1.0.0", corporate_tax_rate=0.20),
        residual_spec=ResidualValueSpec(
            spec_id="RES-V1", version="1.0.0", equipment_salvage_ratio=0.12
        ),
        ramp_spec=RampCurveSpec(spec_id="RAMP-V1", version="1.0.0"),
        seasonality_spec=SeasonalitySpec(spec_id="SEAS-V1", version="1.0.0"),
    )


def build_default_flagship_v1() -> TargetFormatSpec:
    """Flagship 45-ping lifestyle destination format with pet & jumbo machines (v1.0.0)."""
    machine_items = (
        MachineMixItem(machine_model=WASHER_JUMBO_27KG_V1, quantity=2),
        MachineMixItem(machine_model=WASHER_LARGE_20KG_V1, quantity=4),
        MachineMixItem(machine_model=WASHER_MEDIUM_14KG_V1, quantity=3),
        MachineMixItem(machine_model=DRYER_STACK_15KG_V1, quantity=6),
        MachineMixItem(machine_model=DRYER_LARGE_25KG_V1, quantity=2),
        MachineMixItem(machine_model=COMBO_ALL_IN_ONE_12KG_V1, quantity=2),
        MachineMixItem(machine_model=PET_WASHER_10KG_V1, quantity=2),
        MachineMixItem(machine_model=VENDING_DETERGENT_SMART_V1, quantity=2),
    )
    machine_mix = MachineMixSpec(
        spec_id="MIX-FLAG-V1",
        version="1.0.0",
        items=machine_items,
        installation_and_delivery_fee=140_000.0,
    )
    fitout = FitoutSpec(
        spec_id="FIT-FLAG-V1",
        version="1.0.0",
        base_fitout_cost=500_000.0,
        cost_per_ping=25_000.0,
        plumbing_upgrade_cost=200_000.0,
        electrical_upgrade_cost=180_000.0,
        gas_piping_upgrade_cost=140_000.0,
        facade_signage_cost=120_000.0,
        telemetry_smart_hub_cost=80_000.0,
        fitout_useful_life_months=60,
    )
    return TargetFormatSpec(
        format_code="ODAY_FLAGSHIP",
        format_name="ODay Destination Flagship Store",
        format_version="1.0.0",
        description="Premium flagship format featuring jumbo bedding washers, dedicated pet care, and smart retail.",
        target_area_ping_min=35.0,
        target_area_ping_max=80.0,
        recommended_area_ping=45.0,
        machine_mix=machine_mix,
        fitout_spec=fitout,
        utilities_spec=UtilitiesCostSpec(spec_id="UTIL-V1", version="1.0.0"),
        maintenance_spec=MaintenanceSpec(spec_id="MAINT-V1", version="1.0.0"),
        financing_spec=FinancingSpec(
            spec_id="FIN-V1",
            version="1.0.0",
            debt_ratio=0.65,
            annual_interest_rate=0.035,
            loan_term_months=60,
        ),
        tax_spec=TaxSpec(spec_id="TAX-V1", version="1.0.0", corporate_tax_rate=0.20),
        residual_spec=ResidualValueSpec(
            spec_id="RES-V1", version="1.0.0", equipment_salvage_ratio=0.15
        ),
        ramp_spec=RampCurveSpec(spec_id="RAMP-V1", version="1.0.0"),
        seasonality_spec=SeasonalitySpec(spec_id="SEAS-V1", version="1.0.0"),
    )


class TargetFormatRegistry:
    """Catalog of registered, versioned target formats."""

    def __init__(self) -> None:
        self._formats: dict[tuple[str, str], TargetFormatSpec] = {}
        self._latest: dict[str, str] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        self.register(build_default_g2_standard_v1())
        self.register(build_default_g3_compact_v1())
        self.register(build_default_flagship_v1())

    def register(self, format_spec: TargetFormatSpec) -> None:
        key = (format_spec.format_code.upper(), format_spec.format_version)
        self._formats[key] = format_spec
        # Track latest version registered
        self._latest[format_spec.format_code.upper()] = format_spec.format_version

    def get(self, format_code: str, version: str | None = None) -> TargetFormatSpec:
        code_upper = format_code.upper()
        if version is None:
            if code_upper not in self._latest:
                raise KeyError(f"Unknown target format code: {format_code}")
            version = self._latest[code_upper]
        key = (code_upper, version)
        if key not in self._formats:
            raise KeyError(f"Target format not found for code '{format_code}' version '{version}'")
        return self._formats[key]

    def list_formats(self) -> list[TargetFormatSpec]:
        return list(self._formats.values())

    def list_codes(self) -> list[str]:
        return sorted(list(self._latest.keys()))

    def find_best_format_for_area(self, area_ping: float) -> TargetFormatSpec:
        """Find best matching standard format based on available shop area in pings."""
        if area_ping < 20.0:
            return self.get("ODAY_G3_COMPACT")
        elif area_ping > 35.0:
            return self.get("ODAY_FLAGSHIP")
        else:
            return self.get("ODAY_G2")


# Global default registry instance
DEFAULT_FORMAT_REGISTRY = TargetFormatRegistry()
