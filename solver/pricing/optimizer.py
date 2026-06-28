"""Constrained price optimizer.

ODP-MOD-06 specifies OR-Tools for the first optimizer version. OR-Tools is not a
dependency of this repo, so this module implements an equivalent exhaustive
search over the discrete price ladder: it builds the safe action set (only
feasible, on-ladder prices) and picks the price that maximises expected
incremental gross margin. Because candidates are filtered through the hard
constraints *before* scoring, the recommended price can never violate a hard
constraint (AC-06-01).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from solver.pricing.constraints import ConstraintViolation, PriceConstraints
from solver.pricing.demand import SimulationResult, simulate_price

SOLVER_VERSION = "priceops-exhaustive-ladder-v1"

# Solver status vocabulary (ODP-OR-01 §8.7 / §11.1).
STATUS_OPTIMAL = "optimal"
STATUS_FEASIBLE = "feasible"
STATUS_INFEASIBLE = "infeasible"

# A price move at or above this fraction of the current price is treated as
# high-risk and flagged ``requires_approval`` (ODP-OR-01 §5.4 human approval).
HIGH_RISK_DELTA_PCT = 0.10
MEDIUM_RISK_DELTA_PCT = 0.03
LOW_CONFIDENCE_THRESHOLD = 0.6
MEDIUM_CONFIDENCE_THRESHOLD = 0.8


def _risk_level(*, delta_pct: float, confidence: float) -> str:
    if delta_pct >= HIGH_RISK_DELTA_PCT or confidence < LOW_CONFIDENCE_THRESHOLD:
        return "high"
    if delta_pct >= MEDIUM_RISK_DELTA_PCT or confidence < MEDIUM_CONFIDENCE_THRESHOLD:
        return "medium"
    return "low"


@dataclass(frozen=True)
class PriceCandidate:
    """A feasible price on the ladder, scored against the current price."""

    price: float
    simulation: SimulationResult
    incremental_gross_margin: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "price": self.price,
            "incremental_gross_margin": self.incremental_gross_margin,
            "simulation": self.simulation.to_dict(),
        }


@dataclass(frozen=True)
class OptimizationResult:
    """Outcome of optimizing one store/machine price.

    ``recommended_price`` is always feasible. When no feasible move improves on
    the current price the optimizer holds (``recommended_price == current_price``
    and ``incremental_gross_margin == 0``); ``infeasible`` is only true when the
    constraint region itself is empty, in which case ``diagnostics`` explains why.
    """

    recommended_price: float
    current_price: float
    incremental_gross_margin: float
    expected_demand_change: float
    baseline_simulation: SimulationResult
    recommended_simulation: SimulationResult
    safe_action_set: tuple[float, ...]
    candidates: tuple[PriceCandidate, ...]
    solver_status: str
    risk_level: str
    requires_approval: bool
    binding_constraints: tuple[str, ...] = ()
    constraint_violations: tuple[ConstraintViolation, ...] = ()
    infeasible: bool = False
    diagnostics: tuple[str, ...] = ()
    solver_version: str = SOLVER_VERSION

    @property
    def price_changed(self) -> bool:
        return abs(self.recommended_price - self.current_price) > 1e-9

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommended_price": self.recommended_price,
            "current_price": self.current_price,
            "price_changed": self.price_changed,
            "incremental_gross_margin": self.incremental_gross_margin,
            "expected_demand_change": self.expected_demand_change,
            "baseline_simulation": self.baseline_simulation.to_dict(),
            "recommended_simulation": self.recommended_simulation.to_dict(),
            "safe_action_set": list(self.safe_action_set),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "solver_status": self.solver_status,
            "risk_level": self.risk_level,
            "requires_approval": self.requires_approval,
            "binding_constraints": list(self.binding_constraints),
            "constraint_violations": [v.to_dict() for v in self.constraint_violations],
            "infeasible": self.infeasible,
            "diagnostics": list(self.diagnostics),
            "solver_version": self.solver_version,
        }


def build_safe_action_set(constraints: PriceConstraints) -> list[float]:
    """Feasible, on-ladder prices between the lower and upper hard bounds.

    Every returned price passes ``constraints.is_feasible``; off-ladder or
    margin/delta-violating prices are excluded by construction.
    """
    step = constraints.price_ladder_step if constraints.price_ladder_step > 0 else 0.01
    lower = constraints.lower_bound
    upper = constraints.upper_bound
    if lower > upper + 1e-9:
        return []
    start_index = math.ceil(lower / step - 1e-6)
    end_index = math.floor(upper / step + 1e-6)
    prices: list[float] = []
    for index in range(start_index, end_index + 1):
        price = round(index * step, 4)
        if constraints.is_feasible(price):
            prices.append(price)
    return prices


def diagnose_infeasible(constraints: PriceConstraints) -> list[str]:
    """Human-readable reasons the constraint region is empty."""
    reasons: list[str] = []
    if constraints.margin_floor_price > constraints.upper_bound + 1e-9:
        reasons.append(
            "margin floor price exceeds the max-increase ceiling; "
            "unit cost too high for the allowed price band"
        )
    if constraints.min_price is not None and constraints.max_price is not None:
        if constraints.min_price > constraints.max_price + 1e-9:
            reasons.append("configured min price exceeds configured max price")
    if constraints.lower_bound > constraints.upper_bound + 1e-9 and not reasons:
        reasons.append("lower bound exceeds upper bound after applying all hard constraints")
    return reasons


def optimize_price(
    *,
    constraints: PriceConstraints,
    baseline_demand: float,
    elasticity: float,
    confidence: float = 1.0,
) -> OptimizationResult:
    """Pick the feasible price maximising expected incremental gross margin."""
    baseline_simulation = simulate_price(
        price=constraints.current_price,
        baseline_demand=baseline_demand,
        baseline_price=constraints.current_price,
        unit_cost=constraints.unit_cost,
        elasticity=elasticity,
        confidence=confidence,
    )
    baseline_gm = baseline_simulation.expected_gross_margin
    safe_prices = build_safe_action_set(constraints)

    if not safe_prices:
        return OptimizationResult(
            recommended_price=constraints.current_price,
            current_price=constraints.current_price,
            incremental_gross_margin=0.0,
            expected_demand_change=0.0,
            baseline_simulation=baseline_simulation,
            recommended_simulation=baseline_simulation,
            safe_action_set=(),
            candidates=(),
            solver_status=STATUS_INFEASIBLE,
            risk_level="high",
            requires_approval=True,
            binding_constraints=(),
            constraint_violations=tuple(constraints.violations(constraints.current_price)),
            infeasible=True,
            diagnostics=tuple(diagnose_infeasible(constraints)),
        )

    candidates: list[PriceCandidate] = []
    for price in safe_prices:
        simulation = simulate_price(
            price=price,
            baseline_demand=baseline_demand,
            baseline_price=constraints.current_price,
            unit_cost=constraints.unit_cost,
            elasticity=elasticity,
            confidence=confidence,
        )
        incremental = round(simulation.expected_gross_margin - baseline_gm, 4)
        candidates.append(
            PriceCandidate(
                price=price,
                simulation=simulation,
                incremental_gross_margin=incremental,
            )
        )

    # Hold (no change) is always an admissible action with zero incremental
    # margin, so the optimizer never recommends a loss-making move.
    best = max(
        candidates,
        key=lambda candidate: (candidate.incremental_gross_margin, -candidate.price),
    )
    if best.incremental_gross_margin <= 0.0:
        recommended_price = constraints.current_price
        recommended_simulation = baseline_simulation
        incremental = 0.0
        solver_status = STATUS_FEASIBLE  # holding is feasible but not an improving move
    else:
        recommended_price = best.price
        recommended_simulation = best.simulation
        incremental = best.incremental_gross_margin
        solver_status = STATUS_OPTIMAL

    delta_pct = (
        abs(recommended_price - constraints.current_price) / constraints.current_price
        if constraints.current_price > 0
        else 0.0
    )
    price_changed = abs(recommended_price - constraints.current_price) > 1e-9
    risk_level = _risk_level(delta_pct=delta_pct, confidence=confidence) if price_changed else "low"
    expected_demand_change = round(
        recommended_simulation.demand.p50 - baseline_simulation.demand.p50, 4
    )

    return OptimizationResult(
        recommended_price=recommended_price,
        current_price=constraints.current_price,
        incremental_gross_margin=incremental,
        expected_demand_change=expected_demand_change,
        baseline_simulation=baseline_simulation,
        recommended_simulation=recommended_simulation,
        safe_action_set=tuple(safe_prices),
        candidates=tuple(candidates),
        solver_status=solver_status,
        risk_level=risk_level,
        requires_approval=price_changed and risk_level == "high",
        binding_constraints=tuple(constraints.binding_constraints(recommended_price))
        if price_changed
        else (),
    )


__all__ = [
    "SOLVER_VERSION",
    "STATUS_FEASIBLE",
    "STATUS_INFEASIBLE",
    "STATUS_OPTIMAL",
    "OptimizationResult",
    "PriceCandidate",
    "build_safe_action_set",
    "diagnose_infeasible",
    "optimize_price",
]
