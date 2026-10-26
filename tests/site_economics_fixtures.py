"""Explicit engineering fixtures, never measured G2 data or approved finances.

The prior mixed catalog remains useful for the existing arithmetic controls.
It is named as an engineering mixed format when used in new simulations. The
separate six-combo fixture supplies every machine assumption deliberately; no
synthetic machine or price is installed in the product's default registry.
"""
from dataclasses import replace

from modules.site_economics.domain.formats import (
    WASHER_LARGE_20KG_V1,
    WASHER_MEDIUM_14KG_V1,
    TargetFormatRegistry,
    TargetFormatSpec,
    build_default_g2_standard_v1,
)
from modules.site_economics.domain.models import MachineClass, MachineMixItem, MachineMixSpec


def legacy_arithmetic_format() -> TargetFormatSpec:
    return replace(build_default_g2_standard_v1(), format_code="ENGINEERING_MIXED",
        format_name="Synthetic mixed equipment arithmetic control",
        description="Retains original financial test numbers; not the specified ODay G2")


def synthetic_g2_format() -> TargetFormatSpec:
    # Model fields and prices here are deliberate test assumptions, not claims
    # that a washer model is an actual combo or that either price was approved.
    large = replace(WASHER_LARGE_20KG_V1, model_id="SYNTHETIC-COMBO-LARGE",
        model_name="Synthetic large all-in-one", machine_class=MachineClass.COMBO)
    medium = replace(WASHER_MEDIUM_14KG_V1, model_id="SYNTHETIC-COMBO-MEDIUM",
        model_name="Synthetic medium all-in-one", machine_class=MachineClass.COMBO)
    return replace(build_default_g2_standard_v1(), format_version="2.0.0-engineering",
        format_name="Explicit synthetic six-combo engineering format",
        description="Synthetic assumptions only; no production source or financial admission",
        machine_mix=MachineMixSpec(spec_id="SYNTHETIC-G2-SIX-COMBOS", version="2.0.0-engineering",
            items=(MachineMixItem(large, 3), MachineMixItem(medium, 3)),
            installation_and_delivery_fee=80_000.0))


def synthetic_g2_registry() -> TargetFormatRegistry:
    registry = TargetFormatRegistry()
    registry.register(synthetic_g2_format())
    registry.register(legacy_arithmetic_format())
    return registry
