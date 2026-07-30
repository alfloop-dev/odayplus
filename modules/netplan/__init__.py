"""NetPlan public API.

Scenario building, constrained network optimization, alternatives,
infeasibility diagnosis, approval lifecycle, execution, and outcome tracking.
"""

from modules.netplan.application import (
    NetPlanApprovalError,
    NetPlanNotFoundError,
    NetPlanProductionExecution,
    NetPlanProductionExecutionError,
    NetPlanProductionExecutor,
    NetPlanService,
    ScenarioBuildRequest,
)
from modules.netplan.domain import (
    NETPLAN_FEATURE_VERSION,
    NETPLAN_MODEL_VERSION,
    NETPLAN_SOLVER_VERSION,
    AUTHORIZED_HUMAN_OPS_ACTORS,
    VALID_TRANSITIONS,
    ApprovalRecord,
    CandidateSiteInput,
    ExecutionRecord,
    ExistingStoreInput,
    InvalidNetPlanTransitionError,
    NetPlanScenario,
    NetPlanScenarioStatus,
    OutcomeRecord,
    ScenarioSolveRecord,
    StatusTransition,
    build_outcome_record,
    build_scenario_options,
    is_authentic_human_ops_actor,
)
from modules.netplan.infrastructure import InMemoryNetPlanRepository
from modules.netplan.workers import (
    NetPlanBatchResult,
    NetPlanSolverWorker,
    run_netplan_solver_batch,
)
from solver.netplan import (
    ActionOption,
    ManagementBaselineComparisonReceipt,
    ManagementBaselineInput,
    NetPlanConstraints,
    NetworkAction,
    compare_solver_against_management_baseline,
)

__all__ = [
    "NETPLAN_FEATURE_VERSION",
    "NETPLAN_MODEL_VERSION",
    "NETPLAN_SOLVER_VERSION",
    "AUTHORIZED_HUMAN_OPS_ACTORS",
    "VALID_TRANSITIONS",
    "ActionOption",
    "ApprovalRecord",
    "CandidateSiteInput",
    "ExecutionRecord",
    "ExistingStoreInput",
    "InMemoryNetPlanRepository",
    "InvalidNetPlanTransitionError",
    "ManagementBaselineComparisonReceipt",
    "ManagementBaselineInput",
    "NetPlanApprovalError",
    "NetPlanBatchResult",
    "NetPlanConstraints",
    "NetPlanNotFoundError",
    "NetPlanProductionExecution",
    "NetPlanProductionExecutionError",
    "NetPlanProductionExecutor",
    "NetPlanScenario",
    "NetPlanScenarioStatus",
    "NetPlanService",
    "NetPlanSolverWorker",
    "NetworkAction",
    "OutcomeRecord",
    "ScenarioBuildRequest",
    "ScenarioSolveRecord",
    "StatusTransition",
    "build_outcome_record",
    "build_scenario_options",
    "compare_solver_against_management_baseline",
    "is_authentic_human_ops_actor",
    "run_netplan_solver_batch",
]
