"""CVXPY implementation of OR-NET-02 robust/scenario NetPlan."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from time import monotonic
from typing import Any

from solver.netplan.model import NetworkAction

SOLVER_VERSION = "robust-netplan-cvxpy-v1"
STATUS_OPTIMAL = "OPTIMAL"
STATUS_FEASIBLE = "FEASIBLE"
STATUS_INFEASIBLE = "INFEASIBLE"
STATUS_UNBOUNDED = "UNBOUNDED"
STATUS_SOLVER_UNAVAILABLE = "SOLVER_UNAVAILABLE"
STATUS_FAILED = "FAILED"


class RobustObjective(StrEnum):
    WEIGHTED_EXPECTED = "WEIGHTED_EXPECTED"
    MAX_MIN = "MAX_MIN"
    CVAR = "CVAR"


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    weight: float


@dataclass(frozen=True)
class ScenarioActionOption:
    option_id: str
    entity_id: str
    action: NetworkAction
    scenario_values: Mapping[str, float]
    budget_cost: float
    risk_score: float = 0.0
    capacity_delta: int = 0
    admissible: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "option_id": self.option_id,
            "entity_id": self.entity_id,
            "action": self.action.value,
            "scenario_values": dict(self.scenario_values),
            "budget_cost": self.budget_cost,
            "risk_score": self.risk_score,
            "capacity_delta": self.capacity_delta,
        }


@dataclass(frozen=True)
class RobustNetPlanConstraints:
    max_budget: float
    min_value_by_scenario: Mapping[str, float] = field(default_factory=dict)
    min_capacity_delta: int | None = None
    max_average_risk: float | None = None
    min_action_counts: Mapping[NetworkAction, int] = field(default_factory=dict)
    max_action_counts: Mapping[NetworkAction, int] = field(default_factory=dict)


@dataclass(frozen=True)
class SolverDiagnostic:
    code: str
    constraint: str
    message: str
    affected_entities: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "constraint": self.constraint,
            "message": self.message,
            "affected_entities": list(self.affected_entities),
        }


@dataclass(frozen=True)
class RobustNetPlanResult:
    solver_status: str
    objective_type: RobustObjective
    objective_value: float
    selected_actions: tuple[ScenarioActionOption, ...]
    scenario_values: Mapping[str, float]
    expected_value: float
    downside_value: float
    downside_risk: float
    cvar_value: float | None
    budget_usage: float
    average_risk: float
    capacity_delta: int
    action_counts: Mapping[NetworkAction, int]
    stable_action_ids: tuple[str, ...]
    binding_constraints: tuple[str, ...]
    constraint_evaluation: Mapping[str, Mapping[str, Any]]
    diagnostics: tuple[SolverDiagnostic, ...] = ()
    solve_time_seconds: float = 0.0
    solver_name: str | None = None
    solver_version: str = SOLVER_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "solver_status": self.solver_status,
            "objective_type": self.objective_type.value,
            "objective_value": self.objective_value,
            "selected_actions": [item.to_dict() for item in self.selected_actions],
            "scenario_values": dict(self.scenario_values),
            "expected_value": self.expected_value,
            "downside_value": self.downside_value,
            "downside_risk": self.downside_risk,
            "cvar_value": self.cvar_value,
            "budget_usage": self.budget_usage,
            "average_risk": self.average_risk,
            "capacity_delta": self.capacity_delta,
            "action_counts": {key.value: value for key, value in self.action_counts.items()},
            "stable_action_ids": list(self.stable_action_ids),
            "binding_constraints": list(self.binding_constraints),
            "constraint_evaluation": dict(self.constraint_evaluation),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "solve_time_seconds": self.solve_time_seconds,
            "solver_name": self.solver_name,
            "solver_version": self.solver_version,
        }


def solve_robust_network_plan(
    *,
    options_by_entity: Mapping[str, tuple[ScenarioActionOption, ...]],
    scenarios: tuple[Scenario, ...],
    constraints: RobustNetPlanConstraints,
    objective: RobustObjective = RobustObjective.WEIGHTED_EXPECTED,
    downside_weight: float = 1.0,
    cvar_confidence: float = 0.8,
    preferred_solver: str | None = None,
) -> RobustNetPlanResult:
    validation = _validate(
        options_by_entity=options_by_entity,
        scenarios=scenarios,
        constraints=constraints,
        downside_weight=downside_weight,
        cvar_confidence=cvar_confidence,
    )
    if validation:
        return _empty_result(
            status=STATUS_FAILED,
            objective=objective,
            diagnostics=validation,
        )

    cp = _load_cvxpy()
    if cp is None:
        return _empty_result(
            status=STATUS_SOLVER_UNAVAILABLE,
            objective=objective,
            diagnostics=(
                SolverDiagnostic(
                    code="SOLVER_UNAVAILABLE",
                    constraint="solver_runtime",
                    message="CVXPY is not installed; robust NetPlan failed closed.",
                ),
            ),
        )
    solver_name = _select_mip_solver(cp.installed_solvers(), preferred_solver)
    if solver_name is None:
        return _empty_result(
            status=STATUS_SOLVER_UNAVAILABLE,
            objective=objective,
            diagnostics=(
                SolverDiagnostic(
                    code="MIP_SOLVER_UNAVAILABLE",
                    constraint="solver_runtime",
                    message=(
                        "CVXPY is installed but no supported mixed-integer solver "
                        "is available; robust NetPlan failed closed."
                    ),
                ),
            ),
        )

    flattened = tuple(
        option
        for entity_id in sorted(options_by_entity)
        for option in options_by_entity[entity_id]
    )
    index_by_option = {
        option.option_id: index for index, option in enumerate(flattened)
    }
    x = cp.Variable(len(flattened), boolean=True, name="selected_action")
    model_constraints: list[Any] = []
    for entity_id in sorted(options_by_entity):
        indices = [
            index_by_option[option.option_id]
            for option in options_by_entity[entity_id]
        ]
        model_constraints.append(cp.sum(x[indices]) == 1)
    for option in flattened:
        if not option.admissible:
            model_constraints.append(x[index_by_option[option.option_id]] == 0)

    budget_expression = cp.sum(
        cp.multiply(
            [option.budget_cost for option in flattened],
            x,
        )
    )
    model_constraints.append(budget_expression <= constraints.max_budget)

    capacity_expression = cp.sum(
        cp.multiply(
            [option.capacity_delta for option in flattened],
            x,
        )
    )
    if constraints.min_capacity_delta is not None:
        model_constraints.append(
            capacity_expression >= constraints.min_capacity_delta
        )

    risk_expression = cp.sum(
        cp.multiply(
            [option.risk_score for option in flattened],
            x,
        )
    )
    entity_count = len(options_by_entity)
    if constraints.max_average_risk is not None:
        model_constraints.append(
            risk_expression <= constraints.max_average_risk * entity_count
        )

    for action, minimum in constraints.min_action_counts.items():
        model_constraints.append(
            cp.sum(
                x[
                    [
                        index
                        for index, option in enumerate(flattened)
                        if option.action is action
                    ]
                ]
            )
            >= minimum
        )
    for action, maximum in constraints.max_action_counts.items():
        model_constraints.append(
            cp.sum(
                x[
                    [
                        index
                        for index, option in enumerate(flattened)
                        if option.action is action
                    ]
                ]
            )
            <= maximum
        )

    normalized_weights = _normalized_weights(scenarios)
    scenario_expressions = {
        scenario.scenario_id: cp.sum(
            cp.multiply(
                [
                    option.scenario_values[scenario.scenario_id]
                    for option in flattened
                ],
                x,
            )
        )
        for scenario in scenarios
    }
    for scenario_id, minimum in constraints.min_value_by_scenario.items():
        model_constraints.append(scenario_expressions[scenario_id] >= minimum)

    expected_expression = sum(
        normalized_weights[scenario.scenario_id]
        * scenario_expressions[scenario.scenario_id]
        for scenario in scenarios
    )
    minimum_value = cp.Variable(name="minimum_scenario_value")
    for expression in scenario_expressions.values():
        model_constraints.append(minimum_value <= expression)

    cvar_expression: Any | None = None
    if objective is RobustObjective.WEIGHTED_EXPECTED:
        optimization_expression = expected_expression + downside_weight * minimum_value
    elif objective is RobustObjective.MAX_MIN:
        optimization_expression = minimum_value + 1e-7 * expected_expression
    else:
        eta = cp.Variable(name="lower_tail_var")
        shortfall = {
            scenario.scenario_id: cp.Variable(
                nonneg=True,
                name=f"shortfall_{scenario.scenario_id}",
            )
            for scenario in scenarios
        }
        for scenario in scenarios:
            model_constraints.append(
                shortfall[scenario.scenario_id]
                >= eta - scenario_expressions[scenario.scenario_id]
            )
        cvar_expression = eta - (
            sum(
                normalized_weights[scenario.scenario_id]
                * shortfall[scenario.scenario_id]
                for scenario in scenarios
            )
            / (1.0 - cvar_confidence)
        )
        optimization_expression = (
            (1.0 - downside_weight) * expected_expression
            + downside_weight * cvar_expression
        )

    problem = cp.Problem(cp.Maximize(optimization_expression), model_constraints)
    started = monotonic()
    try:
        problem.solve(solver=solver_name, verbose=False)
    except Exception as exc:
        return _empty_result(
            status=STATUS_FAILED,
            objective=objective,
            diagnostics=(
                SolverDiagnostic(
                    code="SOLVER_EXECUTION_FAILED",
                    constraint="solver_runtime",
                    message=f"{solver_name} failed to solve robust NetPlan: {exc}",
                ),
            ),
            solver_name=solver_name,
            solve_time=monotonic() - started,
        )
    solve_time = monotonic() - started
    status = _cvxpy_status(cp, problem.status)
    if status not in {STATUS_OPTIMAL, STATUS_FEASIBLE}:
        return _empty_result(
            status=status,
            objective=objective,
            diagnostics=_diagnose_infeasible(options_by_entity, scenarios, constraints),
            solver_name=solver_name,
            solve_time=solve_time,
        )

    selected = tuple(
        option
        for index, option in enumerate(flattened)
        if x.value is not None and float(x.value[index]) > 0.5
    )
    if len(selected) != entity_count:
        return _empty_result(
            status=STATUS_FAILED,
            objective=objective,
            diagnostics=(
                SolverDiagnostic(
                    code="NON_INTEGRAL_SOLUTION",
                    constraint="action_exclusivity",
                    message=(
                        "The selected backend did not return one integral action per "
                        "entity; no rounded plan was emitted."
                    ),
                ),
            ),
            solver_name=solver_name,
            solve_time=solve_time,
        )

    realized_scenarios = {
        scenario.scenario_id: round(
            sum(
                option.scenario_values[scenario.scenario_id]
                for option in selected
            ),
            4,
        )
        for scenario in scenarios
    }
    expected_value = round(
        sum(
            normalized_weights[scenario_id] * value
            for scenario_id, value in realized_scenarios.items()
        ),
        4,
    )
    downside_value = round(min(realized_scenarios.values()), 4)
    cvar_value = (
        round(float(cvar_expression.value), 4)
        if cvar_expression is not None and cvar_expression.value is not None
        else None
    )
    budget_usage = round(sum(option.budget_cost for option in selected), 4)
    average_risk = round(
        sum(option.risk_score for option in selected) / entity_count,
        4,
    )
    capacity_delta = sum(option.capacity_delta for option in selected)
    action_counts = dict(Counter(option.action for option in selected))
    evaluation = _constraint_evaluation(
        scenario_values=realized_scenarios,
        budget_usage=budget_usage,
        average_risk=average_risk,
        capacity_delta=capacity_delta,
        action_counts=action_counts,
        constraints=constraints,
    )
    bindings = tuple(
        name for name, result in evaluation.items() if result.get("binding")
    )
    stable_actions = tuple(
        sorted(
            option.option_id
            for option in selected
            if _is_scenario_stable(
                option=option,
                entity_options=options_by_entity[option.entity_id],
                scenarios=scenarios,
            )
        )
    )
    return RobustNetPlanResult(
        solver_status=status,
        objective_type=objective,
        objective_value=round(float(problem.value), 4),
        selected_actions=selected,
        scenario_values=realized_scenarios,
        expected_value=expected_value,
        downside_value=downside_value,
        downside_risk=round(expected_value - downside_value, 4),
        cvar_value=cvar_value,
        budget_usage=budget_usage,
        average_risk=average_risk,
        capacity_delta=capacity_delta,
        action_counts=action_counts,
        stable_action_ids=stable_actions,
        binding_constraints=bindings,
        constraint_evaluation=evaluation,
        solve_time_seconds=solve_time,
        solver_name=f"CVXPY_{solver_name}",
    )


def _load_cvxpy() -> Any | None:
    try:
        import cvxpy
    except ImportError:
        return None
    return cvxpy


def _select_mip_solver(
    installed_solvers: list[str],
    preferred_solver: str | None,
) -> str | None:
    supported = (
        "SCIP",
        "HIGHS",
        "CBC",
        "GLPK_MI",
        "SCIPY",
        "ECOS_BB",
        "CPLEX",
        "GUROBI",
        "MOSEK",
        "XPRESS",
    )
    installed = set(installed_solvers)
    if preferred_solver is not None:
        return preferred_solver if preferred_solver in installed and preferred_solver in supported else None
    return next((solver for solver in supported if solver in installed), None)


def _validate(
    *,
    options_by_entity: Mapping[str, tuple[ScenarioActionOption, ...]],
    scenarios: tuple[Scenario, ...],
    constraints: RobustNetPlanConstraints,
    downside_weight: float,
    cvar_confidence: float,
) -> tuple[SolverDiagnostic, ...]:
    diagnostics: list[SolverDiagnostic] = []
    scenario_ids = [scenario.scenario_id for scenario in scenarios]
    if not scenarios or len(scenario_ids) != len(set(scenario_ids)):
        diagnostics.append(
            SolverDiagnostic(
                code="INVALID_INPUT",
                constraint="scenarios",
                message="Scenarios must be non-empty and uniquely identified.",
            )
        )
    if any(scenario.weight < 0 for scenario in scenarios) or sum(
        scenario.weight for scenario in scenarios
    ) <= 0:
        diagnostics.append(
            SolverDiagnostic(
                code="INVALID_INPUT",
                constraint="scenario_weights",
                message="Scenario weights must be non-negative and sum to a positive value.",
            )
        )
    if not options_by_entity or any(not options for options in options_by_entity.values()):
        diagnostics.append(
            SolverDiagnostic(
                code="INVALID_INPUT",
                constraint="entity_action_domain",
                message="Every planning entity must have at least one action option.",
            )
        )
    flattened = [
        option for options in options_by_entity.values() for option in options
    ]
    option_ids = [option.option_id for option in flattened]
    if len(option_ids) != len(set(option_ids)):
        diagnostics.append(
            SolverDiagnostic(
                code="INVALID_INPUT",
                constraint="unique_option_id",
                message="Scenario action option IDs must be unique.",
            )
        )
    expected_scenarios = set(scenario_ids)
    if any(
        option.entity_id != entity_id
        or set(option.scenario_values) != expected_scenarios
        for entity_id, options in options_by_entity.items()
        for option in options
    ):
        diagnostics.append(
            SolverDiagnostic(
                code="INVALID_INPUT",
                constraint="scenario_value_coverage",
                message=(
                    "Each option must belong to its entity key and provide one value "
                    "for every scenario."
                ),
            )
        )
    if (
        constraints.max_budget < 0
        or any(
            option.budget_cost < 0 or not 0 <= option.risk_score <= 1
            for option in flattened
        )
        or not 0 <= downside_weight <= 1
        or not 0 < cvar_confidence < 1
        or not set(constraints.min_value_by_scenario) <= expected_scenarios
    ):
        diagnostics.append(
            SolverDiagnostic(
                code="INVALID_INPUT",
                constraint="robust_bounds",
                message="Robust NetPlan bounds, risks, and risk parameters are invalid.",
            )
        )
    return tuple(diagnostics)


def _normalized_weights(scenarios: tuple[Scenario, ...]) -> dict[str, float]:
    total = sum(scenario.weight for scenario in scenarios)
    return {
        scenario.scenario_id: scenario.weight / total for scenario in scenarios
    }


def _diagnose_infeasible(
    options_by_entity: Mapping[str, tuple[ScenarioActionOption, ...]],
    scenarios: tuple[Scenario, ...],
    constraints: RobustNetPlanConstraints,
) -> tuple[SolverDiagnostic, ...]:
    diagnostics: list[SolverDiagnostic] = []
    if any(not any(option.admissible for option in options) for options in options_by_entity.values()):
        blocked = tuple(
            sorted(
                entity_id
                for entity_id, options in options_by_entity.items()
                if not any(option.admissible for option in options)
            )
        )
        diagnostics.append(
            SolverDiagnostic(
                code="NO_ADMISSIBLE_ACTION",
                constraint="entity_action_domain",
                message="At least one entity has no admissible action.",
                affected_entities=blocked,
            )
        )
    minimum_budget = sum(
        min(
            option.budget_cost
            for option in options
            if option.admissible
        )
        for options in options_by_entity.values()
        if any(option.admissible for option in options)
    )
    if minimum_budget > constraints.max_budget:
        diagnostics.append(
            SolverDiagnostic(
                code="BUDGET_INFEASIBLE",
                constraint="max_budget",
                message="The cheapest complete robust portfolio exceeds the budget.",
            )
        )
    for scenario in scenarios:
        minimum = constraints.min_value_by_scenario.get(scenario.scenario_id)
        if minimum is None:
            continue
        upper_bound = sum(
            max(
                option.scenario_values[scenario.scenario_id]
                for option in options
                if option.admissible
            )
            for options in options_by_entity.values()
            if any(option.admissible for option in options)
        )
        if upper_bound < minimum:
            diagnostics.append(
                SolverDiagnostic(
                    code="SCENARIO_FLOOR_INFEASIBLE",
                    constraint=f"min_value_by_scenario.{scenario.scenario_id}",
                    message=(
                        f"Best-case value {upper_bound} cannot reach scenario floor "
                        f"{minimum}."
                    ),
                )
            )
    return tuple(diagnostics) or (
        SolverDiagnostic(
            code="COMBINED_CONSTRAINTS_INFEASIBLE",
            constraint="robust_netplan",
            message=(
                "Scenario floors, budget, capacity, risk, and action counts are "
                "jointly infeasible; no hard constraint was relaxed."
            ),
        ),
    )


def _constraint_evaluation(
    *,
    scenario_values: Mapping[str, float],
    budget_usage: float,
    average_risk: float,
    capacity_delta: int,
    action_counts: Mapping[NetworkAction, int],
    constraints: RobustNetPlanConstraints,
) -> dict[str, dict[str, Any]]:
    evaluation: dict[str, dict[str, Any]] = {
        "max_budget": _maximum(budget_usage, constraints.max_budget),
    }
    if constraints.min_capacity_delta is not None:
        evaluation["min_capacity_delta"] = _minimum(
            capacity_delta, constraints.min_capacity_delta
        )
    if constraints.max_average_risk is not None:
        evaluation["max_average_risk"] = _maximum(
            average_risk, constraints.max_average_risk
        )
    for scenario_id, minimum in constraints.min_value_by_scenario.items():
        evaluation[f"min_value_by_scenario.{scenario_id}"] = _minimum(
            scenario_values[scenario_id], minimum
        )
    for action, minimum in constraints.min_action_counts.items():
        evaluation[f"min_action_counts.{action.value}"] = _minimum(
            action_counts.get(action, 0), minimum
        )
    for action, maximum in constraints.max_action_counts.items():
        evaluation[f"max_action_counts.{action.value}"] = _maximum(
            action_counts.get(action, 0), maximum
        )
    return evaluation


def _is_scenario_stable(
    *,
    option: ScenarioActionOption,
    entity_options: tuple[ScenarioActionOption, ...],
    scenarios: tuple[Scenario, ...],
) -> bool:
    return all(
        option.scenario_values[scenario.scenario_id]
        >= max(
            candidate.scenario_values[scenario.scenario_id]
            for candidate in entity_options
            if candidate.admissible
        )
        for scenario in scenarios
    )


def _cvxpy_status(cp: Any, status: str) -> str:
    return {
        cp.OPTIMAL: STATUS_OPTIMAL,
        cp.OPTIMAL_INACCURATE: STATUS_FEASIBLE,
        cp.INFEASIBLE: STATUS_INFEASIBLE,
        cp.INFEASIBLE_INACCURATE: STATUS_INFEASIBLE,
        cp.UNBOUNDED: STATUS_UNBOUNDED,
        cp.UNBOUNDED_INACCURATE: STATUS_UNBOUNDED,
    }.get(status, STATUS_FAILED)


def _empty_result(
    *,
    status: str,
    objective: RobustObjective,
    diagnostics: tuple[SolverDiagnostic, ...],
    solver_name: str | None = None,
    solve_time: float = 0.0,
) -> RobustNetPlanResult:
    return RobustNetPlanResult(
        solver_status=status,
        objective_type=objective,
        objective_value=0.0,
        selected_actions=(),
        scenario_values={},
        expected_value=0.0,
        downside_value=0.0,
        downside_risk=0.0,
        cvar_value=None,
        budget_usage=0.0,
        average_risk=0.0,
        capacity_delta=0,
        action_counts={},
        stable_action_ids=(),
        binding_constraints=(),
        constraint_evaluation={},
        diagnostics=diagnostics,
        solve_time_seconds=solve_time,
        solver_name=solver_name,
    )


def _minimum(actual: float, limit: float) -> dict[str, Any]:
    return {
        "actual": actual,
        "operator": ">=",
        "limit": limit,
        "satisfied": actual >= limit - 1e-6,
        "binding": _near(actual, limit),
    }


def _maximum(actual: float, limit: float) -> dict[str, Any]:
    return {
        "actual": actual,
        "operator": "<=",
        "limit": limit,
        "satisfied": actual <= limit + 1e-6,
        "binding": _near(actual, limit),
    }


def _near(left: float, right: float, tolerance: float = 1e-5) -> bool:
    return abs(left - right) <= tolerance


__all__ = [
    "SOLVER_VERSION",
    "RobustNetPlanConstraints",
    "RobustNetPlanResult",
    "RobustObjective",
    "Scenario",
    "ScenarioActionOption",
    "SolverDiagnostic",
    "solve_robust_network_plan",
]
