"""Site Economics Module Public API.

Implements target-format and monthly site economics simulator for ODayPlus laundromat stores.
Provides contract: odayplus.site-economics.v1
Requires contract: emgi.site-market-context.v1
"""

from modules.site_economics.application.service import (
    SimulationOverrides,
    SiteEconomicsService,
)
from modules.site_economics.domain.contracts import (
    CONTRACT_CATEGORY,
    CONTRACT_ID,
    CONTRACT_VERSION,
    ENGINE_VERSION,
    ScenarioSummary,
    SimulationAssumptionsSnapshot,
    SiteEconomicsDocument,
    validate_site_economics_document,
)
from modules.site_economics.domain.formats import (
    DEFAULT_FORMAT_REGISTRY,
    TargetFormatRegistry,
    TargetFormatSpec,
    build_default_flagship_v1,
    build_default_g2_standard_v1,
    build_default_g3_compact_v1,
)
from modules.site_economics.domain.models import (
    CensoringType,
    DecisionAssessment,
    EconomicsDecision,
    FinancialMetricsSummary,
    FinancingSpec,
    FitoutSpec,
    MachineClass,
    MachineMixItem,
    MachineMixSpec,
    MachineModelSpec,
    MaintenanceSpec,
    MonthlyCashFlowItem,
    PaybackOutcome,
    RampCurveSpec,
    ResidualValueSpec,
    SeasonalitySpec,
    SiteOperatingParameters,
    TaxSpec,
    UtilitiesCostSpec,
)
from modules.site_economics.domain.simulator import (
    SimulationInput,
    SimulationResult,
    SiteEconomicsSimulator,
    compute_irr,
    compute_npv,
    compute_payback,
    compute_pmt,
)
from modules.site_economics.infrastructure.repositories import (
    InMemorySiteEconomicsRepository,
    SiteEconomicsRepository,
)

__all__ = [
    "CONTRACT_CATEGORY",
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "CensoringType",
    "DEFAULT_FORMAT_REGISTRY",
    "DecisionAssessment",
    "ENGINE_VERSION",
    "EconomicsDecision",
    "FinancialMetricsSummary",
    "FinancingSpec",
    "FitoutSpec",
    "InMemorySiteEconomicsRepository",
    "MachineClass",
    "MachineMixItem",
    "MachineMixSpec",
    "MachineModelSpec",
    "MaintenanceSpec",
    "MonthlyCashFlowItem",
    "PaybackOutcome",
    "RampCurveSpec",
    "ResidualValueSpec",
    "ScenarioSummary",
    "SeasonalitySpec",
    "SimulationAssumptionsSnapshot",
    "SimulationInput",
    "SimulationOverrides",
    "SimulationResult",
    "SiteEconomicsDocument",
    "SiteEconomicsRepository",
    "SiteEconomicsService",
    "SiteEconomicsSimulator",
    "SiteOperatingParameters",
    "TargetFormatRegistry",
    "TargetFormatSpec",
    "TaxSpec",
    "UtilitiesCostSpec",
    "build_default_flagship_v1",
    "build_default_g2_standard_v1",
    "build_default_g3_compact_v1",
    "compute_irr",
    "compute_npv",
    "compute_payback",
    "compute_pmt",
    "validate_site_economics_document",
]
