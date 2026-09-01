from __future__ import annotations

from modules.heatzone.v3.absorption import (
    MIN_OBSERVATION_DAYS_KEY,
    UNDER_REALIZED_RATIO_KEY,
    AbsorbingStoreObservation,
    AbsorptionInputError,
    AbsorptionResult,
    compute_absorbed_demand,
)
from modules.heatzone.v3.adapter import (
    from_catchment_profile,
    from_legacy_feature_input,
    from_market_cell_profile,
)
from modules.heatzone.v3.contract import (
    CONTRACT_ID,
    CONTRACT_VERSION,
    MODEL_VERSION,
    AbstainReasonCode,
    ExecutionMode,
    HeatZoneV3BatchResult,
    HeatZoneV3Input,
    HeatZoneV3ScoreResult,
    HeatZoneV3ShadowComparison,
    HeatZoneV3State,
)
from modules.heatzone.v3.scoring import (
    DEFAULT_V3_WEIGHTS,
    HeatZoneV3ScoringWeights,
    check_support_and_abstention,
    score_heatzone_v3_feature,
    score_heatzones_v3,
)
from modules.heatzone.v3.shadow import (
    HeatZoneV3ShadowRunner,
    compute_shadow_comparisons_and_metrics,
)

__all__ = [
    "AbstainReasonCode",
    "AbsorbingStoreObservation",
    "AbsorptionInputError",
    "AbsorptionResult",
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "DEFAULT_V3_WEIGHTS",
    "ExecutionMode",
    "HeatZoneV3BatchResult",
    "HeatZoneV3Input",
    "HeatZoneV3ScoreResult",
    "HeatZoneV3ScoringWeights",
    "HeatZoneV3ShadowComparison",
    "HeatZoneV3ShadowRunner",
    "HeatZoneV3State",
    "MIN_OBSERVATION_DAYS_KEY",
    "MODEL_VERSION",
    "UNDER_REALIZED_RATIO_KEY",
    "check_support_and_abstention",
    "compute_absorbed_demand",
    "compute_shadow_comparisons_and_metrics",
    "from_catchment_profile",
    "from_legacy_feature_input",
    "from_market_cell_profile",
    "score_heatzone_v3_feature",
    "score_heatzones_v3",
]
