"""CP-SAT-compatible NetPlan optimizer.

The production contract calls for CP-SAT style constrained optimization. This
repo intentionally keeps runtime dependencies small, so the first solver uses a
deterministic exhaustive search over discrete action options. The public result
surface mirrors a CP-SAT solve: status, objective, binding constraints,
alternative plans, and structured infeasibility diagnostics.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import product
from typing import Any

from ortools.linear_solver import pywraplp

from solver.netplan.model import (
    ActionOption,
    InfeasibilityDiagnosis,
    NetPlanConstraints,
    NetworkAction,
)

SOLVER_VERSION = "netplan-ortools-mip-v1"
STATUS_OPTIMAL = "optimal"
STATUS_FEASIBLE = "feasible"
STATUS_INFEASIBLE = "infeasible"


@dataclass(frozen=True)
class NetworkPlanCandidate:
    actions: tuple[ActionOption, ...]
    objective_value: float
    expected_gross_margin: float
    budget_usage: float
    average_risk: float
    capacity_delta: int
    action_counts: dict[NetworkAction, int]
    binding_constraints: tuple[str, ...]

    @property
    def action_signature(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((action.entity_id, action.action.value) for action in self.actions))

    def to_dict(self) -> dict[str, Any]:
        return {
            "actions": [action.to_dict() for action in self.actions],
            "objective_value": self.objective_value,
            "expected_gross_margin": self.expected_gross_margin,
            "budget_usage": self.budget_usage,
            "average_risk": self.average_risk,
            "capacity_delta": self.capacity_delta,
            "action_counts": {k.value: v for k, v in self.action_counts.items()},
            "binding_constraints": list(self.binding_constraints),
        }


@dataclass(frozen=True)
class NetworkPlanSolveResult:
    solver_status: str
    objective_value: float
    selected_actions: tuple[ActionOption, ...]
    expected_gross_margin: float
    budget_usage: float
    average_risk: float
    capacity_delta: int
    action_counts: dict[NetworkAction, int]
    binding_constraints: tuple[str, ...]
    alternatives: tuple[NetworkPlanCandidate, ...] = ()
    infeasible: bool = False
    diagnostics: tuple[InfeasibilityDiagnosis, ...] = ()
    solver_version: str = SOLVER_VERSION

    @property
    def alternative_plan_available(self) -> bool:
        return bool(self.alternatives)

    def to_dict(self) -> dict[str, Any]:
        return {
            "solver_status": self.solver_status,
            "objective_value": self.objective_value,
            "selected_actions": [action.to_dict() for action in self.selected_actions],
            "expected_gross_margin": self.expected_gross_margin,
            "budget_usage": self.budget_usage,
            "average_risk": self.average_risk,
            "capacity_delta": self.capacity_delta,
            "action_counts": {k.value: v for k, v in self.action_counts.items()},
            "binding_constraints": list(self.binding_constraints),
            "alternative_plan_available": self.alternative_plan_available,
            "alternatives": [candidate.to_dict() for candidate in self.alternatives],
            "infeasible": self.infeasible,
            "diagnostics": [diagnosis.to_dict() for diagnosis in self.diagnostics],
            "solver_version": self.solver_version,
        }


def _candidate_from_selected(
    selected: list[ActionOption],
    constraints: NetPlanConstraints,
    risk_penalty: float,
) -> NetworkPlanCandidate:
    expected_gm = round(sum(option.expected_gross_margin for option in selected), 4)
    budget = round(sum(option.budget_cost for option in selected), 4)
    average_risk = round(sum(option.risk_score for option in selected) / len(selected), 4) if selected else 0.0
    capacity = sum(option.capacity_delta for option in selected)
    counts = Counter(option.action for option in selected)
    objective = round(expected_gm - risk_penalty * average_risk, 4)
    return NetworkPlanCandidate(
        actions=tuple(selected),
        objective_value=objective,
        expected_gross_margin=expected_gm,
        budget_usage=budget,
        average_risk=average_risk,
        capacity_delta=capacity,
        action_counts=dict(counts),
        binding_constraints=_binding_constraints(
            budget=budget,
            expected_gm=expected_gm,
            average_risk=average_risk,
            capacity=capacity,
            counts=counts,
            constraints=constraints,
        ),
    )


def solve_network_plan(
    *,
    options_by_entity: dict[str, tuple[ActionOption, ...]],
    constraints: NetPlanConstraints,
    risk_penalty: float = 100_000.0,
    alternative_limit: int = 3,
) -> NetworkPlanSolveResult:
    # Handle empty/missing inputs
    if not options_by_entity or any(not options for options in options_by_entity.values()):
        return NetworkPlanSolveResult(
            solver_status=STATUS_INFEASIBLE,
            objective_value=0.0,
            selected_actions=(),
            expected_gross_margin=0.0,
            budget_usage=0.0,
            average_risk=0.0,
            capacity_delta=0,
            action_counts={},
            binding_constraints=(),
            infeasible=True,
            diagnostics=tuple(diagnose_infeasible(options_by_entity, constraints)),
        )

    # Initialize SCIP solver
    solver = pywraplp.Solver.CreateSolver("SCIP")
    if not solver:
        # Fallback to exhaustive if SCIP is not available
        candidates = build_feasible_candidates(
            options_by_entity=options_by_entity,
            constraints=constraints,
            risk_penalty=risk_penalty,
        )
        if not candidates:
            return NetworkPlanSolveResult(
                solver_status=STATUS_INFEASIBLE,
                objective_value=0.0,
                selected_actions=(),
                expected_gross_margin=0.0,
                budget_usage=0.0,
                average_risk=0.0,
                capacity_delta=0,
                action_counts={},
                binding_constraints=(),
                infeasible=True,
                diagnostics=tuple(diagnose_infeasible(options_by_entity, constraints)),
            )
        ordered = sorted(
            candidates,
            key=lambda item: (
                item.objective_value,
                item.expected_gross_margin,
                -item.budget_usage,
                -item.average_risk,
            ),
            reverse=True,
        )
        best = ordered[0]
        alternatives = tuple(
            candidate
            for candidate in ordered[1:]
            if candidate.action_signature != best.action_signature
        )[:alternative_limit]
        status = STATUS_OPTIMAL if best.objective_value >= ordered[-1].objective_value else STATUS_FEASIBLE
        return NetworkPlanSolveResult(
            solver_status=status,
            objective_value=best.objective_value,
            selected_actions=best.actions,
            expected_gross_margin=best.expected_gross_margin,
            budget_usage=best.budget_usage,
            average_risk=best.average_risk,
            capacity_delta=best.capacity_delta,
            action_counts=best.action_counts,
            binding_constraints=best.binding_constraints,
            alternatives=alternatives,
        )

    # Create variables
    x = {}
    for entity_id, options in options_by_entity.items():
        x[entity_id] = []
        for j, option in enumerate(options):
            var = solver.BoolVar(f"x_{entity_id}_{j}")
            x[entity_id].append(var)

    # Constraints
    # 1. Exactly one option is selected for each entity
    for entity_id in options_by_entity:
        solver.Add(sum(x[entity_id]) == 1)

    # 2. Budget constraint
    solver.Add(
        sum(
            x[entity_id][j] * option.budget_cost
            for entity_id, options in options_by_entity.items()
            for j, option in enumerate(options)
        )
        <= constraints.max_budget
    )

    # 3. Min expected gross margin
    if constraints.min_expected_gross_margin is not None:
        solver.Add(
            sum(
                x[entity_id][j] * option.expected_gross_margin
                for entity_id, options in options_by_entity.items()
                for j, option in enumerate(options)
            )
            >= constraints.min_expected_gross_margin
        )

    # 4. Min capacity delta
    if constraints.min_capacity_delta is not None:
        solver.Add(
            sum(
                x[entity_id][j] * option.capacity_delta
                for entity_id, options in options_by_entity.items()
                for j, option in enumerate(options)
            )
            >= constraints.min_capacity_delta
        )

    # 5. Max average risk
    N = len(options_by_entity)
    if constraints.max_average_risk is not None:
        solver.Add(
            sum(
                x[entity_id][j] * option.risk_score
                for entity_id, options in options_by_entity.items()
                for j, option in enumerate(options)
            )
            <= constraints.max_average_risk * N
        )

    # 6. Action count constraints
    for action, minimum in constraints.min_action_counts.items():
        solver.Add(
            sum(
                x[entity_id][j]
                for entity_id, options in options_by_entity.items()
                for j, option in enumerate(options)
                if option.action == action
            )
            >= minimum
        )

    for action, maximum in constraints.max_action_counts.items():
        solver.Add(
            sum(
                x[entity_id][j]
                for entity_id, options in options_by_entity.items()
                for j, option in enumerate(options)
                if option.action == action
            )
            <= maximum
        )

    # Objective
    objective = solver.Objective()
    for entity_id, options in options_by_entity.items():
        for j, option in enumerate(options):
            coef = option.expected_gross_margin - (risk_penalty * option.risk_score / N)
            objective.SetCoefficient(x[entity_id][j], coef)
    objective.SetMaximization()

    # Solve primary
    status = solver.Solve()
    if status not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        return NetworkPlanSolveResult(
            solver_status=STATUS_INFEASIBLE,
            objective_value=0.0,
            selected_actions=(),
            expected_gross_margin=0.0,
            budget_usage=0.0,
            average_risk=0.0,
            capacity_delta=0,
            action_counts={},
            binding_constraints=(),
            infeasible=True,
            diagnostics=tuple(diagnose_infeasible(options_by_entity, constraints)),
        )

    def get_selected_actions():
        selected = []
        for entity_id, options in options_by_entity.items():
            for j, option in enumerate(options):
                if x[entity_id][j].solution_value() > 0.5:
                    selected.append(option)
        return selected

    selected = sorted(get_selected_actions(), key=lambda o: o.entity_id)
    best_candidate = _candidate_from_selected(selected, constraints, risk_penalty)

    # Find alternatives
    alternatives = []
    for _ in range(alternative_limit):
        current_selected_vars = []
        for entity_id, options in options_by_entity.items():
            for j, option in enumerate(options):
                if x[entity_id][j].solution_value() > 0.5:
                    current_selected_vars.append(x[entity_id][j])
        
        solver.Add(sum(current_selected_vars) <= N - 1)
        alt_status = solver.Solve()
        if alt_status in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
            alt_selected = sorted(get_selected_actions(), key=lambda o: o.entity_id)
            alt_candidate = _candidate_from_selected(alt_selected, constraints, risk_penalty)
            if alt_candidate.action_signature not in [best_candidate.action_signature] + [a.action_signature for a in alternatives]:
                alternatives.append(alt_candidate)
        else:
            break

    solver_status = STATUS_OPTIMAL if status == pywraplp.Solver.OPTIMAL else STATUS_FEASIBLE
    return NetworkPlanSolveResult(
        solver_status=solver_status,
        objective_value=best_candidate.objective_value,
        selected_actions=best_candidate.actions,
        expected_gross_margin=best_candidate.expected_gross_margin,
        budget_usage=best_candidate.budget_usage,
        average_risk=best_candidate.average_risk,
        capacity_delta=best_candidate.capacity_delta,
        action_counts=best_candidate.action_counts,
        binding_constraints=best_candidate.binding_constraints,
        alternatives=tuple(alternatives),
    )


def build_feasible_candidates(
    *,
    options_by_entity: dict[str, tuple[ActionOption, ...]],
    constraints: NetPlanConstraints,
    risk_penalty: float,
) -> list[NetworkPlanCandidate]:
    if not options_by_entity or any(not options for options in options_by_entity.values()):
        return []

    candidates: list[NetworkPlanCandidate] = []
    entities = sorted(options_by_entity)
    for selected in product(*(options_by_entity[entity] for entity in entities)):
        candidate = _candidate(selected, constraints=constraints, risk_penalty=risk_penalty)
        if _is_feasible(candidate, constraints):
            candidates.append(candidate)
    return candidates


def diagnose_infeasible(
    options_by_entity: dict[str, tuple[ActionOption, ...]],
    constraints: NetPlanConstraints,
) -> list[InfeasibilityDiagnosis]:
    diagnostics: list[InfeasibilityDiagnosis] = []
    missing = tuple(sorted(entity for entity, options in options_by_entity.items() if not options))
    if missing:
        diagnostics.append(
            InfeasibilityDiagnosis(
                violated_constraint="entity_action_domain",
                affected_stores=missing,
                required_relaxation="provide at least one admissible action for each planning entity",
                business_impact="solver cannot produce a complete quarter action list",
                suggested_action="repair scenario inputs or remove blocked entities from this run",
            )
        )

    all_options = [option for options in options_by_entity.values() for option in options]
    if not all_options:
        diagnostics.append(
            InfeasibilityDiagnosis(
                violated_constraint="empty_scenario",
                affected_stores=(),
                required_relaxation="add candidate sites or existing stores",
                business_impact="no network decision can be evaluated",
                suggested_action="rebuild the scenario from network_plan_view inputs",
            )
        )
        return diagnostics

    cheapest_by_entity = [
        min(options, key=lambda option: option.budget_cost)
        for options in options_by_entity.values()
        if options
    ]
    min_budget = round(sum(option.budget_cost for option in cheapest_by_entity), 4)
    min_required_budget = _min_budget_with_required_action_counts(options_by_entity, constraints)
    budget_floor = min_required_budget if min_required_budget is not None else min_budget
    if budget_floor > constraints.max_budget:
        diagnostics.append(
            InfeasibilityDiagnosis(
                violated_constraint="max_budget",
                affected_stores=tuple(sorted(option.entity_id for option in cheapest_by_entity)),
                required_relaxation=(
                    f"increase budget by at least {round(budget_floor - constraints.max_budget, 4)}"
                ),
                business_impact="every complete action portfolio exceeds the budget ceiling",
                suggested_action="raise scenario budget or allow lower-cost KEEP/EXIT actions",
            )
        )

    max_gm = _max_metric(options_by_entity, lambda option: option.expected_gross_margin)
    if constraints.min_expected_gross_margin is not None and max_gm < constraints.min_expected_gross_margin:
        diagnostics.append(
            InfeasibilityDiagnosis(
                violated_constraint="min_expected_gross_margin",
                affected_stores=tuple(sorted(options_by_entity)),
                required_relaxation=f"lower GM floor by at least {round(constraints.min_expected_gross_margin - max_gm, 4)}",
                business_impact="best-case portfolio cannot reach the required network gross margin",
                suggested_action="add higher-quality open/improve candidates or lower the GM floor",
            )
        )

    max_capacity = int(_max_metric(options_by_entity, lambda option: option.capacity_delta))
    if constraints.min_capacity_delta is not None and max_capacity < constraints.min_capacity_delta:
        diagnostics.append(
            InfeasibilityDiagnosis(
                violated_constraint="min_capacity_delta",
                affected_stores=tuple(sorted(options_by_entity)),
                required_relaxation=f"lower capacity target by at least {constraints.min_capacity_delta - max_capacity}",
                business_impact="planned footprint cannot create the required capacity",
                suggested_action="add OPEN/MOVE candidates or reduce the capacity target",
            )
        )

    for action, minimum in constraints.min_action_counts.items():
        available = sum(1 for options in options_by_entity.values() if any(o.action is action for o in options))
        if available < minimum:
            diagnostics.append(
                InfeasibilityDiagnosis(
                    violated_constraint=f"min_action_counts.{action.value}",
                    affected_stores=tuple(sorted(options_by_entity)),
                    required_relaxation=f"lower required {action.value} count by {minimum - available}",
                    business_impact=f"not enough entities can take {action.value}",
                    suggested_action="add eligible entities or relax the action-count policy",
                )
            )

    return diagnostics or [
        InfeasibilityDiagnosis(
            violated_constraint="combined_constraints",
            affected_stores=tuple(sorted(options_by_entity)),
            required_relaxation="relax at least one hard constraint",
            business_impact="constraints are individually plausible but jointly infeasible",
            suggested_action="inspect budget, risk, GM, capacity, and action-count limits together",
        )
    ]


def _candidate(
    selected: tuple[ActionOption, ...],
    *,
    constraints: NetPlanConstraints,
    risk_penalty: float,
) -> NetworkPlanCandidate:
    expected_gm = round(sum(option.expected_gross_margin for option in selected), 4)
    budget = round(sum(option.budget_cost for option in selected), 4)
    average_risk = round(sum(option.risk_score for option in selected) / len(selected), 4)
    capacity = sum(option.capacity_delta for option in selected)
    counts = Counter(option.action for option in selected)
    objective = round(expected_gm - risk_penalty * average_risk, 4)
    return NetworkPlanCandidate(
        actions=selected,
        objective_value=objective,
        expected_gross_margin=expected_gm,
        budget_usage=budget,
        average_risk=average_risk,
        capacity_delta=capacity,
        action_counts=dict(counts),
        binding_constraints=_binding_constraints(
            budget=budget,
            expected_gm=expected_gm,
            average_risk=average_risk,
            capacity=capacity,
            counts=counts,
            constraints=constraints,
        ),
    )


def _is_feasible(candidate: NetworkPlanCandidate, constraints: NetPlanConstraints) -> bool:
    if candidate.budget_usage > constraints.max_budget + 1e-9:
        return False
    if (
        constraints.min_expected_gross_margin is not None
        and candidate.expected_gross_margin < constraints.min_expected_gross_margin - 1e-9
    ):
        return False
    if (
        constraints.min_capacity_delta is not None
        and candidate.capacity_delta < constraints.min_capacity_delta
    ):
        return False
    if (
        constraints.max_average_risk is not None
        and candidate.average_risk > constraints.max_average_risk + 1e-9
    ):
        return False
    for action, minimum in constraints.min_action_counts.items():
        if candidate.action_counts.get(action, 0) < minimum:
            return False
    for action, maximum in constraints.max_action_counts.items():
        if candidate.action_counts.get(action, 0) > maximum:
            return False
    return True


def _binding_constraints(
    *,
    budget: float,
    expected_gm: float,
    average_risk: float,
    capacity: int,
    counts: Counter[NetworkAction],
    constraints: NetPlanConstraints,
) -> tuple[str, ...]:
    bindings: list[str] = []
    if _near(budget, constraints.max_budget):
        bindings.append("max_budget")
    if constraints.min_expected_gross_margin is not None and _near(expected_gm, constraints.min_expected_gross_margin):
        bindings.append("min_expected_gross_margin")
    if constraints.max_average_risk is not None and _near(average_risk, constraints.max_average_risk):
        bindings.append("max_average_risk")
    if constraints.min_capacity_delta is not None and capacity == constraints.min_capacity_delta:
        bindings.append("min_capacity_delta")
    for action, minimum in constraints.min_action_counts.items():
        if counts.get(action, 0) == minimum:
            bindings.append(f"min_action_counts.{action.value}")
    for action, maximum in constraints.max_action_counts.items():
        if counts.get(action, 0) == maximum:
            bindings.append(f"max_action_counts.{action.value}")
    return tuple(bindings)


def _max_metric(options_by_entity: dict[str, tuple[ActionOption, ...]], metric: Any) -> float:
    grouped: defaultdict[str, list[float]] = defaultdict(list)
    for entity, options in options_by_entity.items():
        grouped[entity].extend(float(metric(option)) for option in options)
    return round(sum(max(values) for values in grouped.values() if values), 4)


def _min_budget_with_required_action_counts(
    options_by_entity: dict[str, tuple[ActionOption, ...]],
    constraints: NetPlanConstraints,
) -> float | None:
    if not constraints.min_action_counts:
        return None
    entities = sorted(options_by_entity)
    best: float | None = None
    for selected in product(*(options_by_entity[entity] for entity in entities)):
        counts = Counter(option.action for option in selected)
        if all(counts.get(action, 0) >= minimum for action, minimum in constraints.min_action_counts.items()):
            budget = round(sum(option.budget_cost for option in selected), 4)
            best = budget if best is None else min(best, budget)
    return best


def _near(left: float, right: float, *, tolerance: float = 1e-6) -> bool:
    return abs(left - right) <= tolerance


__all__ = [
    "SOLVER_VERSION",
    "STATUS_FEASIBLE",
    "STATUS_INFEASIBLE",
    "STATUS_OPTIMAL",
    "NetworkPlanCandidate",
    "NetworkPlanSolveResult",
    "build_feasible_candidates",
    "diagnose_infeasible",
    "solve_network_plan",
]
