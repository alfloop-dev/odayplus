"""CP-SAT-compatible NetPlan optimizer.

The production contract calls for CP-SAT style constrained optimization. This
repo intentionally keeps runtime dependencies small, so the first solver uses a
deterministic exhaustive search over discrete action options. The public result
surface mirrors a CP-SAT solve: status, objective, binding constraints,
alternative plans, and structured infeasibility diagnostics.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from itertools import product
from typing import Any

from solver.netplan.model import (
    BUSINESS_UAT_UNVERIFIED,
    BUSINESS_UAT_VERIFIED,
    GOVERNED_DISABLED,
    GOVERNED_ENABLED,
    ActionOption,
    InfeasibilityDiagnosis,
    ManagementApprovalExpectation,
    ManagementApprovalReceiptVerifier,
    ManagementBaselineComparisonReceipt,
    ManagementBaselineInput,
    NetPlanConstraints,
    NetworkAction,
    canonical_sha256,
)
from solver.process_isolation import run_in_process_isolation


def _pywraplp():  # noqa: D401
    """Import ortools lazily.

    ortools is a heavy optional dependency only needed to *run* a solve. Importing
    this module must not require it: it is pulled transitively into the API startup
    path (persistence -> modules.netplan -> solver.netplan) and the API container
    ships without ortools. Deferring the import keeps API boot independent of it.
    """
    from ortools.linear_solver import pywraplp

    return pywraplp


SOLVER_VERSION = "netplan-ortools-mip-v1"
STATUS_OPTIMAL = "optimal"
STATUS_FEASIBLE = "feasible"
STATUS_INFEASIBLE = "infeasible"
DEFAULT_ALTERNATIVE_LIMIT = 3


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
    expected_gm = sum(option.expected_gross_margin for option in selected)
    budget = sum(option.budget_cost for option in selected)
    average_risk = sum(option.risk_score for option in selected) / len(selected) if selected else 0.0
    capacity = sum(option.capacity_delta for option in selected)
    counts = Counter(option.action for option in selected)
    objective = expected_gm - risk_penalty * average_risk
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


def _solve_network_plan_impl(
    *,
    options_by_entity: dict[str, tuple[ActionOption, ...]],
    constraints: NetPlanConstraints,
    risk_penalty: float = 100_000.0,
    alternative_limit: int = 3,
) -> NetworkPlanSolveResult:
    if alternative_limit < 0:
        raise ValueError("alternative_limit must be non-negative")
    _validate_option_domain(options_by_entity)

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
    solver = _pywraplp().Solver.CreateSolver("SCIP")
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
        ordered = _rank_feasible_candidates(candidates)
        best = ordered[0]
        alternatives = tuple(
            candidate
            for candidate in ordered[1:]
            if candidate.action_signature != best.action_signature
        )[:alternative_limit]
        return NetworkPlanSolveResult(
            solver_status=STATUS_OPTIMAL,
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
        for j, _option in enumerate(options):
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
    if status not in (_pywraplp().Solver.OPTIMAL, _pywraplp().Solver.FEASIBLE):
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

    # The backend solve proves runtime compatibility, while the independently
    # enumerated set is authoritative for exact hard constraints, optimality,
    # and deterministic alternative content. Backend numeric tolerances must
    # never admit a raw constraint violation into the public result.
    ranked_candidates = _rank_feasible_candidates(
        build_feasible_candidates(
            options_by_entity=options_by_entity,
            constraints=constraints,
            risk_penalty=risk_penalty,
        )
    )
    if not ranked_candidates:
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
    best_candidate = ranked_candidates[0]
    alternatives = [
        candidate
        for candidate in ranked_candidates
        if candidate.action_signature != best_candidate.action_signature
    ][:alternative_limit]

    return NetworkPlanSolveResult(
        solver_status=STATUS_OPTIMAL,
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
    _validate_option_domain(options_by_entity)
    if not options_by_entity or any(not options for options in options_by_entity.values()):
        return []

    candidates: list[NetworkPlanCandidate] = []
    entities = sorted(options_by_entity)
    for selected in product(*(options_by_entity[entity] for entity in entities)):
        candidate = _candidate(selected, constraints=constraints, risk_penalty=risk_penalty)
        if _is_feasible(candidate, constraints):
            candidates.append(candidate)
    return candidates


def _rank_feasible_candidates(
    candidates: list[NetworkPlanCandidate],
) -> list[NetworkPlanCandidate]:
    """Return one deterministic ordering for independently enumerated plans."""
    return sorted(
        candidates,
        key=lambda item: (
            -item.objective_value,
            -item.expected_gross_margin,
            item.budget_usage,
            item.average_risk,
            item.action_signature,
        ),
    )


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
    min_budget = sum(option.budget_cost for option in cheapest_by_entity)
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

    min_risk = _min_metric(options_by_entity, lambda option: option.risk_score)
    if constraints.max_average_risk is not None and min_risk > constraints.max_average_risk:
        diagnostics.append(
            InfeasibilityDiagnosis(
                violated_constraint="max_average_risk",
                affected_stores=tuple(sorted(options_by_entity)),
                required_relaxation=f"raise risk ceiling by at least {round(min_risk - constraints.max_average_risk, 4)}",
                business_impact="every complete action portfolio exceeds the average risk threshold",
                suggested_action="increase max_average_risk or add lower-risk action options",
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

    for action, maximum in constraints.max_action_counts.items():
        forced = sum(1 for options in options_by_entity.values() if options and all(o.action is action for o in options))
        if forced > maximum:
            diagnostics.append(
                InfeasibilityDiagnosis(
                    violated_constraint=f"max_action_counts.{action.value}",
                    affected_stores=tuple(sorted(options_by_entity)),
                    required_relaxation=f"increase allowed {action.value} count by {forced - maximum}",
                    business_impact=f"more entities are constrained to {action.value} than the maximum allowed",
                    suggested_action=f"allow alternative actions for constrained entities or raise max {action.value} count",
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
    expected_gm = sum(option.expected_gross_margin for option in selected)
    budget = sum(option.budget_cost for option in selected)
    average_risk = sum(option.risk_score for option in selected) / len(selected)
    capacity = sum(option.capacity_delta for option in selected)
    counts = Counter(option.action for option in selected)
    objective = expected_gm - risk_penalty * average_risk
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
    if candidate.budget_usage > constraints.max_budget:
        return False
    if (
        constraints.min_expected_gross_margin is not None
        and candidate.expected_gross_margin < constraints.min_expected_gross_margin
    ):
        return False
    if (
        constraints.min_capacity_delta is not None
        and candidate.capacity_delta < constraints.min_capacity_delta
    ):
        return False
    if (
        constraints.max_average_risk is not None
        and candidate.average_risk > constraints.max_average_risk
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


def compute_solver_problem_hash(
    options_by_entity: dict[str, tuple[ActionOption, ...]],
    constraints: NetPlanConstraints,
    risk_penalty: float,
    alternative_limit: int = DEFAULT_ALTERNATIVE_LIMIT,
) -> str:
    if alternative_limit < 0:
        raise ValueError("alternative_limit must be non-negative")
    _validate_option_domain(options_by_entity)
    payload = {
        "entities": sorted(options_by_entity.keys()),
        "options": {
            k: [opt.to_dict() for opt in options]
            for k, options in sorted(options_by_entity.items())
        },
        "constraints": constraints.to_dict(),
        "risk_penalty": float(risk_penalty),
        "alternative_limit": alternative_limit,
    }
    return canonical_sha256(payload)


def _validate_option_domain(
    options_by_entity: dict[str, tuple[ActionOption, ...]],
) -> None:
    """Reject action-only identities that cannot bind one authoritative option."""
    for entity_id, options in options_by_entity.items():
        seen_actions: set[NetworkAction] = set()
        for option in options:
            if option.entity_id != entity_id:
                raise ValueError(
                    f"option entity_id {option.entity_id!r} does not match domain {entity_id!r}"
                )
            if option.action in seen_actions:
                raise ValueError(
                    "duplicate (entity_id, action) option identity: "
                    f"({entity_id!r}, {option.action.value!r})"
                )
            seen_actions.add(option.action)


def _compute_solver_result_hash(solve_result: NetworkPlanSolveResult) -> str:
    return canonical_sha256(solve_result.to_dict())


def _candidate_fields_match(
    actual: NetworkPlanCandidate,
    expected: NetworkPlanCandidate,
) -> bool:
    return (
        actual.actions == expected.actions
        and actual.objective_value == expected.objective_value
        and actual.expected_gross_margin == expected.expected_gross_margin
        and actual.budget_usage == expected.budget_usage
        and actual.average_risk == expected.average_risk
        and actual.capacity_delta == expected.capacity_delta
        and actual.action_counts == expected.action_counts
        and actual.binding_constraints == expected.binding_constraints
    )


def _verify_solve_result(
    *,
    options_by_entity: dict[str, tuple[ActionOption, ...]],
    constraints: NetPlanConstraints,
    solve_result: NetworkPlanSolveResult,
    risk_penalty: float,
    alternative_limit: int,
) -> tuple[tuple[str, ...], NetworkPlanCandidate | None]:
    violations: list[str] = []
    feasible_candidates = build_feasible_candidates(
        options_by_entity=options_by_entity,
        constraints=constraints,
        risk_penalty=risk_penalty,
    )

    if not feasible_candidates:
        if solve_result.solver_status != STATUS_INFEASIBLE:
            violations.append("solve_status_mismatch")
        if not solve_result.infeasible:
            violations.append("solve_feasibility_flag_mismatch")
        if solve_result.selected_actions:
            violations.append("infeasible_result_has_selected_actions")
        if solve_result.objective_value != 0.0:
            violations.append("solve_objective_mismatch")
        if any(
            (
                solve_result.expected_gross_margin != 0.0,
                solve_result.budget_usage != 0.0,
                solve_result.average_risk != 0.0,
                solve_result.capacity_delta != 0,
                bool(solve_result.action_counts),
                bool(solve_result.binding_constraints),
                bool(solve_result.alternatives),
            )
        ):
            violations.append("infeasible_result_metrics_mismatch")
        if solve_result.solver_version != SOLVER_VERSION:
            violations.append("solver_version_mismatch")
        expected_diagnostics = tuple(diagnose_infeasible(options_by_entity, constraints))
        if solve_result.diagnostics != expected_diagnostics:
            violations.append("infeasibility_diagnosis_mismatch")
        return tuple(violations), None

    if solve_result.solver_status != STATUS_OPTIMAL:
        violations.append("solve_status_mismatch")
    if solve_result.infeasible:
        violations.append("solve_feasibility_flag_mismatch")
    if solve_result.diagnostics:
        violations.append("feasible_result_has_diagnostics")
    if solve_result.solver_version != SOLVER_VERSION:
        violations.append("solver_version_mismatch")

    selected_by_entity: dict[str, ActionOption] = {}
    selection_integrity_failed = False
    for selected in solve_result.selected_actions:
        if selected.entity_id in selected_by_entity:
            violations.append("duplicate_selected_entity")
            selection_integrity_failed = True
            continue
        selected_by_entity[selected.entity_id] = selected
        if selected not in options_by_entity.get(selected.entity_id, ()):
            violations.append("selected_option_not_in_problem")
            selection_integrity_failed = True

    if set(selected_by_entity) != set(options_by_entity):
        violations.append("unbound_solve_result_domain")
        selection_integrity_failed = True
    if selection_integrity_failed:
        return tuple(dict.fromkeys(violations)), None

    recomputed = _candidate_from_selected(
        [selected_by_entity[entity_id] for entity_id in sorted(selected_by_entity)],
        constraints,
        risk_penalty,
    )
    if not _is_feasible(recomputed, constraints):
        violations.append("selected_actions_infeasible")
    if solve_result.objective_value != recomputed.objective_value:
        violations.append("solve_objective_mismatch")
    if solve_result.expected_gross_margin != recomputed.expected_gross_margin:
        violations.append("solve_expected_gross_margin_mismatch")
    if solve_result.budget_usage != recomputed.budget_usage:
        violations.append("solve_budget_usage_mismatch")
    if solve_result.average_risk != recomputed.average_risk:
        violations.append("solve_average_risk_mismatch")
    if solve_result.capacity_delta != recomputed.capacity_delta:
        violations.append("solve_capacity_delta_mismatch")
    if solve_result.action_counts != recomputed.action_counts:
        violations.append("solve_action_counts_mismatch")
    if solve_result.binding_constraints != recomputed.binding_constraints:
        violations.append("solve_binding_constraints_mismatch")

    ranked_candidates = _rank_feasible_candidates(feasible_candidates)
    best_objective = ranked_candidates[0].objective_value
    if recomputed.objective_value != best_objective:
        violations.append("optimality_claim_mismatch")

    expected_alternatives = tuple(
        candidate
        for candidate in ranked_candidates
        if candidate.action_signature != recomputed.action_signature
    )[:alternative_limit]
    if len(solve_result.alternatives) != len(expected_alternatives):
        violations.append("alternative_count_mismatch")

    seen_signatures = {recomputed.action_signature}
    for position, alternative in enumerate(solve_result.alternatives):
        alternative_selected = list(alternative.actions)
        alternative_entities = {action.entity_id for action in alternative_selected}
        if len(alternative_selected) != len(options_by_entity) or alternative_entities != set(
            options_by_entity
        ):
            violations.append("alternative_domain_mismatch")
            continue
        if any(
            action not in options_by_entity.get(action.entity_id, ())
            for action in alternative_selected
        ):
            violations.append("alternative_option_not_in_problem")
            continue
        recomputed_alternative = _candidate_from_selected(
            sorted(alternative_selected, key=lambda action: action.entity_id),
            constraints,
            risk_penalty,
        )
        if not _is_feasible(recomputed_alternative, constraints):
            violations.append("alternative_infeasible")
        if not _candidate_fields_match(alternative, recomputed_alternative):
            violations.append("alternative_metrics_mismatch")
        if alternative.action_signature in seen_signatures:
            violations.append("duplicate_alternative")
        seen_signatures.add(alternative.action_signature)
        if position >= len(expected_alternatives) or not _candidate_fields_match(
            alternative,
            expected_alternatives[position],
        ):
            violations.append("alternative_content_mismatch")

    return tuple(dict.fromkeys(violations)), recomputed


def validate_network_plan_solve_result(
    *,
    options_by_entity: dict[str, tuple[ActionOption, ...]],
    constraints: NetPlanConstraints,
    solve_result: NetworkPlanSolveResult,
    risk_penalty: float = 100_000.0,
    alternative_limit: int = DEFAULT_ALTERNATIVE_LIMIT,
) -> tuple[str, ...]:
    """Independently recompute and validate one persisted solver result."""
    violations, _ = _verify_solve_result(
        options_by_entity=options_by_entity,
        constraints=constraints,
        solve_result=solve_result,
        risk_penalty=risk_penalty,
        alternative_limit=alternative_limit,
    )
    return violations


def compare_solver_against_management_baseline(
    *,
    options_by_entity: dict[str, tuple[ActionOption, ...]],
    constraints: NetPlanConstraints,
    solve_result: NetworkPlanSolveResult,
    baseline: ManagementBaselineInput,
    risk_penalty: float = 100_000.0,
    alternative_limit: int = DEFAULT_ALTERNATIVE_LIMIT,
    approval_verifier: ManagementApprovalReceiptVerifier | None = None,
) -> ManagementBaselineComparisonReceipt:
    baseline_canonical_hash = baseline.compute_canonical_hash(constraints=constraints, risk_penalty=risk_penalty)
    solver_problem_hash = compute_solver_problem_hash(
        options_by_entity,
        constraints,
        risk_penalty,
        alternative_limit,
    )
    solver_result_hash = _compute_solver_result_hash(solve_result)
    scenario_hash = canonical_sha256(
        {
            "scenario_id": baseline.scenario_id,
            "scope": baseline.scope,
            "release_id": baseline.release_id,
            "policy_version": constraints.policy_version,
        }
    )
    source_snapshot_hash = canonical_sha256(
        {"source_snapshot_ids": sorted(baseline.source_snapshot_ids)}
    )
    actions_domain_hash = canonical_sha256(
        {
            "actions_by_entity": {
                entity_id: action.value
                for entity_id, action in sorted(baseline.actions_by_entity.items())
            }
        }
    )

    def receipt(
        *,
        baseline_feasible: bool,
        baseline_objective_value: float | None,
        solver_objective_value: float,
        objective_gain_over_baseline: float | None,
        superior_or_equal: bool,
        violations: tuple[str, ...],
        approval_verified: bool = False,
        approval_receipt_hash: str = "",
    ) -> ManagementBaselineComparisonReceipt:
        comparison = ManagementBaselineComparisonReceipt(
            baseline_id=baseline.baseline_id,
            baseline_feasible=baseline_feasible,
            baseline_objective_value=baseline_objective_value,
            solver_objective_value=solver_objective_value,
            objective_gain_over_baseline=objective_gain_over_baseline,
            superior_or_equal=superior_or_equal,
            baseline_canonical_hash=baseline_canonical_hash,
            solver_problem_hash=solver_problem_hash,
            solver_result_hash=solver_result_hash,
            scenario_hash=scenario_hash,
            source_snapshot_hash=source_snapshot_hash,
            actions_domain_hash=actions_domain_hash,
            approval_receipt_hash=approval_receipt_hash,
            business_uat_status=(
                BUSINESS_UAT_VERIFIED
                if approval_verified and not violations and superior_or_equal
                else BUSINESS_UAT_UNVERIFIED
            ),
            governance_status=(
                GOVERNED_ENABLED
                if approval_verified and not violations and superior_or_equal
                else GOVERNED_DISABLED
            ),
            approval_verified=approval_verified,
            baseline_constraint_violations=violations,
        )
        hash_payload = comparison.to_dict()
        hash_payload.pop("comparison_output_hash")
        return replace(
            comparison,
            comparison_output_hash=canonical_sha256(hash_payload),
        )

    if approval_verifier is None:
        return receipt(
            baseline_feasible=False,
            baseline_objective_value=None,
            solver_objective_value=solve_result.objective_value,
            objective_gain_over_baseline=None,
            superior_or_equal=False,
            violations=("authoritative_approval_verifier_missing",),
        )

    verification = approval_verifier.verify(
        ManagementApprovalExpectation(
            receipt_id=baseline.approval_receipt_id,
            scenario_id=baseline.scenario_id,
            baseline_id=baseline.baseline_id,
            baseline_name=baseline.baseline_name,
            scope=baseline.scope,
            release_id=baseline.release_id,
            policy_version=constraints.policy_version,
            actions_by_entity=baseline.actions_by_entity,
            source_snapshot_ids=baseline.source_snapshot_ids,
            baseline_content_hash=baseline_canonical_hash,
            solver_problem_hash=solver_problem_hash,
        ),
    )
    approval_receipt_hash = verification.receipt.receipt_hash if verification.receipt else ""
    if (
        verification.receipt is None
        or not verification.authority_attests_receipt(verification.receipt)
    ):
        return receipt(
            baseline_feasible=False,
            baseline_objective_value=None,
            solver_objective_value=solve_result.objective_value,
            objective_gain_over_baseline=None,
            superior_or_equal=False,
            violations=verification.violations
            or ("authority_verification_attestation_missing",),
            approval_receipt_hash=approval_receipt_hash,
        )

    solve_violations, recomputed_solve = _verify_solve_result(
        options_by_entity=options_by_entity,
        constraints=constraints,
        solve_result=solve_result,
        risk_penalty=risk_penalty,
        alternative_limit=alternative_limit,
    )
    if solve_violations or recomputed_solve is None:
        return receipt(
            baseline_feasible=False,
            baseline_objective_value=None,
            solver_objective_value=solve_result.objective_value,
            objective_gain_over_baseline=None,
            superior_or_equal=False,
            violations=solve_violations or ("solve_result_not_feasible",),
            approval_verified=True,
            approval_receipt_hash=approval_receipt_hash,
        )

    baseline_entities = set(baseline.actions_by_entity.keys())
    problem_entities = set(options_by_entity.keys())

    if baseline_entities != problem_entities:
        violations: list[str] = []
        if not baseline_entities.issuperset(problem_entities):
            violations.append("incomplete_action_domain")
        if not baseline_entities.issubset(problem_entities):
            violations.append("extra_baseline_entities")
        if not violations:
            violations.append("domain_mismatch")

        return receipt(
            baseline_feasible=False,
            baseline_objective_value=None,
            solver_objective_value=recomputed_solve.objective_value,
            objective_gain_over_baseline=None,
            superior_or_equal=False,
            violations=tuple(violations),
            approval_verified=True,
            approval_receipt_hash=approval_receipt_hash,
        )

    baseline_options: list[ActionOption] = []
    missing_matches: list[str] = []
    for entity_id in sorted(options_by_entity):
        target_action = baseline.actions_by_entity[entity_id]
        matched = next((opt for opt in options_by_entity[entity_id] if opt.action is target_action), None)
        if matched is None:
            missing_matches.append(entity_id)
        else:
            baseline_options.append(matched)

    if missing_matches or len(baseline_options) != len(options_by_entity):
        return receipt(
            baseline_feasible=False,
            baseline_objective_value=None,
            solver_objective_value=recomputed_solve.objective_value,
            objective_gain_over_baseline=None,
            superior_or_equal=False,
            violations=("missing_action_option_for_entity",),
            approval_verified=True,
            approval_receipt_hash=approval_receipt_hash,
        )

    baseline_candidate = _candidate_from_selected(baseline_options, constraints, risk_penalty)
    is_feasible = _is_feasible(baseline_candidate, constraints)

    if not is_feasible:
        violations: list[str] = []
        if baseline_candidate.budget_usage > constraints.max_budget:
            violations.append("max_budget")
        if (
            constraints.min_expected_gross_margin is not None
            and baseline_candidate.expected_gross_margin < constraints.min_expected_gross_margin
        ):
            violations.append("min_expected_gross_margin")
        if (
            constraints.min_capacity_delta is not None
            and baseline_candidate.capacity_delta < constraints.min_capacity_delta
        ):
            violations.append("min_capacity_delta")
        if (
            constraints.max_average_risk is not None
            and baseline_candidate.average_risk > constraints.max_average_risk
        ):
            violations.append("max_average_risk")
        for action, minimum in constraints.min_action_counts.items():
            if baseline_candidate.action_counts.get(action, 0) < minimum:
                violations.append(f"min_action_counts.{action.value}")
        for action, maximum in constraints.max_action_counts.items():
            if baseline_candidate.action_counts.get(action, 0) > maximum:
                violations.append(f"max_action_counts.{action.value}")

        return receipt(
            baseline_feasible=False,
            baseline_objective_value=baseline_candidate.objective_value,
            solver_objective_value=recomputed_solve.objective_value,
            objective_gain_over_baseline=None,
            superior_or_equal=False,
            violations=tuple(violations),
            approval_verified=True,
            approval_receipt_hash=approval_receipt_hash,
        )

    gain = recomputed_solve.objective_value - baseline_candidate.objective_value
    superior_or_equal = solve_result.solver_status == STATUS_OPTIMAL and (
        recomputed_solve.objective_value >= baseline_candidate.objective_value
    )
    return receipt(
        baseline_feasible=True,
        baseline_objective_value=baseline_candidate.objective_value,
        solver_objective_value=recomputed_solve.objective_value,
        objective_gain_over_baseline=gain,
        superior_or_equal=superior_or_equal,
        violations=() if superior_or_equal else ("solver_not_superior_to_baseline",),
        approval_verified=True,
        approval_receipt_hash=approval_receipt_hash,
    )


def _max_metric(options_by_entity: dict[str, tuple[ActionOption, ...]], metric: Any) -> float:
    grouped: defaultdict[str, list[float]] = defaultdict(list)
    for entity, options in options_by_entity.items():
        grouped[entity].extend(float(metric(option)) for option in options)
    return sum(max(values) for values in grouped.values() if values)


def _min_metric(options_by_entity: dict[str, tuple[ActionOption, ...]], metric: Any) -> float:
    grouped: defaultdict[str, list[float]] = defaultdict(list)
    for entity, options in options_by_entity.items():
        grouped[entity].extend(float(metric(option)) for option in options)
    return sum(min(values) for values in grouped.values() if values) / len(grouped) if grouped else 0.0


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
            budget = sum(option.budget_cost for option in selected)
            best = budget if best is None else min(best, budget)
    return best


def _near(left: float, right: float, *, tolerance: float = 1e-6) -> bool:
    return abs(left - right) <= tolerance


def solve_network_plan(
    *,
    options_by_entity: dict[str, tuple[ActionOption, ...]],
    constraints: NetPlanConstraints,
    risk_penalty: float = 100_000.0,
    alternative_limit: int = DEFAULT_ALTERNATIVE_LIMIT,
    isolate_process: bool = True,
) -> NetworkPlanSolveResult:
    """Public solver entrypoint with process isolation contract."""
    if isolate_process:
        return run_in_process_isolation(
            _solve_network_plan_impl,
            options_by_entity=options_by_entity,
            constraints=constraints,
            risk_penalty=risk_penalty,
            alternative_limit=alternative_limit,
        )
    return _solve_network_plan_impl(
        options_by_entity=options_by_entity,
        constraints=constraints,
        risk_penalty=risk_penalty,
        alternative_limit=alternative_limit,
    )


__all__ = [
    "DEFAULT_ALTERNATIVE_LIMIT",
    "SOLVER_VERSION",
    "STATUS_FEASIBLE",
    "STATUS_INFEASIBLE",
    "STATUS_OPTIMAL",
    "NetworkPlanCandidate",
    "NetworkPlanSolveResult",
    "build_feasible_candidates",
    "compare_solver_against_management_baseline",
    "compute_solver_problem_hash",
    "diagnose_infeasible",
    "solve_network_plan",
]
