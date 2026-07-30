"""NetPlan solver: discrete network action optimization and diagnostics."""

from solver.netplan.model import (
    NETPLAN_POLICY_VERSION,
    ActionOption,
    InfeasibilityDiagnosis,
    ManagementBaselineComparisonReceipt,
    ManagementBaselineInput,
    NetPlanConstraints,
    NetworkAction,
)
from solver.netplan.optimizer import (
    SOLVER_VERSION,
    STATUS_FEASIBLE,
    STATUS_INFEASIBLE,
    STATUS_OPTIMAL,
    NetworkPlanCandidate,
    NetworkPlanSolveResult,
    build_feasible_candidates,
    compare_solver_against_management_baseline,
    diagnose_infeasible,
    solve_network_plan,
)

__all__ = [
    "NETPLAN_POLICY_VERSION",
    "SOLVER_VERSION",
    "STATUS_FEASIBLE",
    "STATUS_INFEASIBLE",
    "STATUS_OPTIMAL",
    "ActionOption",
    "InfeasibilityDiagnosis",
    "ManagementBaselineComparisonReceipt",
    "ManagementBaselineInput",
    "NetPlanConstraints",
    "NetworkAction",
    "NetworkPlanCandidate",
    "NetworkPlanSolveResult",
    "build_feasible_candidates",
    "compare_solver_against_management_baseline",
    "diagnose_infeasible",
    "solve_network_plan",
]
