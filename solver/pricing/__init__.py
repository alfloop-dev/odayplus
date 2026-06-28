"""Pricing solver: hard constraints, demand simulation and constrained optimization.

This package is the numeric engine behind PriceOps (ODP-MOD-06). It knows nothing
about plan lifecycle, approval or persistence — that orchestration lives in
``modules.priceops``. Keeping the solver pure makes constraint satisfaction and
optimization independently testable.
"""

from solver.pricing.constraints import (
    PRICING_POLICY_VERSION,
    VIOLATION_ABOVE_MAX,
    VIOLATION_BELOW_MIN,
    VIOLATION_MARGIN_FLOOR,
    VIOLATION_MAX_DECREASE,
    VIOLATION_MAX_INCREASE,
    VIOLATION_OFF_LADDER,
    ConstraintViolation,
    PriceConstraints,
)
from solver.pricing.demand import (
    Band,
    SimulationResult,
    expected_demand,
    simulate_price,
)
from solver.pricing.optimizer import (
    SOLVER_VERSION,
    STATUS_FEASIBLE,
    STATUS_INFEASIBLE,
    STATUS_OPTIMAL,
    OptimizationResult,
    PriceCandidate,
    build_safe_action_set,
    diagnose_infeasible,
    optimize_price,
)

__all__ = [
    "PRICING_POLICY_VERSION",
    "SOLVER_VERSION",
    "STATUS_FEASIBLE",
    "STATUS_INFEASIBLE",
    "STATUS_OPTIMAL",
    "VIOLATION_ABOVE_MAX",
    "VIOLATION_BELOW_MIN",
    "VIOLATION_MARGIN_FLOOR",
    "VIOLATION_MAX_DECREASE",
    "VIOLATION_MAX_INCREASE",
    "VIOLATION_OFF_LADDER",
    "Band",
    "ConstraintViolation",
    "OptimizationResult",
    "PriceCandidate",
    "PriceConstraints",
    "SimulationResult",
    "build_safe_action_set",
    "diagnose_infeasible",
    "expected_demand",
    "optimize_price",
    "simulate_price",
]
