from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any

from modules.netplan.domain.planning import NetPlanScenario
from solver.evolutionary.pareto import (
    EvolutionaryPortfolioOption,
    solve_portfolio_frontier,
)
from solver.netplan import (
    STATUS_FEASIBLE,
    STATUS_INFEASIBLE,
    STATUS_OPTIMAL,
    NetworkPlanSolveResult,
)
from solver.netplan.optimizer import diagnose_infeasible
from solver.netplan.robust import (
    STATUS_FAILED as ROBUST_FAILED,
)
from solver.netplan.robust import (
    STATUS_SOLVER_UNAVAILABLE,
    STATUS_UNBOUNDED,
    RobustNetPlanConstraints,
    RobustObjective,
    Scenario,
    ScenarioActionOption,
    solve_robust_network_plan,
)


class NetPlanProductionExecutionError(RuntimeError):
    """Raised when the production OSS solver contract cannot complete."""


@dataclass(frozen=True)
class NetPlanProductionExecution:
    result: NetworkPlanSolveResult
    metadata: dict[str, Any]


class NetPlanProductionExecutor:
    """Run OR-Tools, CVXPY, and NSGA-II against one live scenario."""

    def execute(
        self,
        scenario: NetPlanScenario,
        *,
        alternative_limit: int,
    ) -> NetPlanProductionExecution:
        source_snapshot_ids = sorted(
            {
                snapshot_id
                for options in scenario.options_by_entity.values()
                for option in options
                for snapshot_id in option.source_snapshot_ids
            }
        )
        if not source_snapshot_ids:
            raise NetPlanProductionExecutionError(
                "production NetPlan requires source snapshot lineage"
            )
        missing_lineage = sorted(
            option.entity_id
            for options in scenario.options_by_entity.values()
            for option in options
            if not option.source_snapshot_ids
        )
        if missing_lineage:
            raise NetPlanProductionExecutionError(
                "production NetPlan options are missing source lineage: "
                + ", ".join(missing_lineage)
            )

        try:
            primary = _solve_ortools_cp_sat(scenario)
        except Exception as exc:
            if isinstance(exc, NetPlanProductionExecutionError):
                raise
            raise NetPlanProductionExecutionError("OR-Tools NetPlan execution failed") from exc
        if primary.solver_status not in {
            STATUS_OPTIMAL,
            STATUS_FEASIBLE,
            STATUS_INFEASIBLE,
        }:
            raise NetPlanProductionExecutionError(
                f"OR-Tools returned unsupported status {primary.solver_status!r}"
            )

        robust = _run_robust_contract(scenario)
        if robust.solver_status in {
            STATUS_SOLVER_UNAVAILABLE,
            ROBUST_FAILED,
            STATUS_UNBOUNDED,
        }:
            raise NetPlanProductionExecutionError(
                "CVXPY robust NetPlan failed closed: "
                + "; ".join(item.message for item in robust.diagnostics)
            )

        try:
            frontier = solve_portfolio_frontier(
                options=tuple(
                    EvolutionaryPortfolioOption(
                        option_id=f"{option.entity_id}:{option.action.value}",
                        expected_gross_margin=option.expected_gross_margin,
                        budget_cost=option.budget_cost,
                        risk_score=option.risk_score,
                    )
                    for entity_id in sorted(scenario.options_by_entity)
                    for option in scenario.options_by_entity[entity_id]
                ),
                max_budget=scenario.constraints.max_budget,
                min_selected=len(scenario.options_by_entity),
                max_selected=len(scenario.options_by_entity),
            )
        except Exception as exc:
            raise NetPlanProductionExecutionError(
                "pymoo NSGA-II NetPlan frontier failed to execute"
            ) from exc
        if frontier.status not in {"optimal_frontier", "infeasible"}:
            raise NetPlanProductionExecutionError(
                f"pymoo NSGA-II returned unsupported status {frontier.status!r}"
            )

        metadata = {
            "mode": "production_oss",
            "model_version": scenario.model_version,
            "feature_version": scenario.feature_version,
            "policy_version": scenario.constraints.policy_version,
            "source_snapshot_ids": source_snapshot_ids,
            "engines": {
                "authoritative": {
                    "library": "ortools",
                    "library_version": version("ortools"),
                    "solver": "CP-SAT",
                    "contract_version": primary.solver_version,
                    "status": primary.solver_status,
                },
                "robust": {
                    "library": "cvxpy",
                    "library_version": version("cvxpy"),
                    "solver": robust.solver_name,
                    "contract_version": robust.solver_version,
                    "status": robust.solver_status,
                    "objective": robust.objective_type.value,
                    "scenario_values": dict(robust.scenario_values),
                    "selected_action_ids": [
                        option.option_id for option in robust.selected_actions
                    ],
                    "stable_action_ids": list(robust.stable_action_ids),
                },
                "frontier": {
                    "library": "pymoo",
                    "library_version": version("pymoo"),
                    "solver": frontier.engine,
                    "status": frontier.status,
                    "population_size": frontier.population_size,
                    "generations": frontier.generations,
                    "seed": frontier.seed,
                    "candidate_count": len(frontier.candidates),
                    "candidates": [
                        {
                            "option_ids": list(candidate.option_ids),
                            "expected_gross_margin": candidate.expected_gross_margin,
                            "budget_cost": candidate.budget_cost,
                            "average_risk": candidate.average_risk,
                        }
                        for candidate in frontier.candidates[:alternative_limit]
                    ],
                },
            },
        }
        return NetPlanProductionExecution(result=primary, metadata=metadata)


def _solve_ortools_cp_sat(scenario: NetPlanScenario) -> NetworkPlanSolveResult:
    try:
        from ortools.sat.python import cp_model
    except Exception as exc:
        raise NetPlanProductionExecutionError("OR-Tools CP-SAT runtime is unavailable") from exc
    if not scenario.options_by_entity or any(
        not options for options in scenario.options_by_entity.values()
    ):
        return _infeasible_primary(scenario)

    money_scale = 100
    risk_scale = 1_000_000
    model = cp_model.CpModel()
    variables = {
        (entity_id, index): model.new_bool_var(f"action_{entity_id}_{index}")
        for entity_id, options in scenario.options_by_entity.items()
        for index, _option in enumerate(options)
    }
    for entity_id, options in scenario.options_by_entity.items():
        model.add(sum(variables[(entity_id, index)] for index in range(len(options))) == 1)
    model.add(
        sum(
            variables[(entity_id, index)] * round(option.budget_cost * money_scale)
            for entity_id, options in scenario.options_by_entity.items()
            for index, option in enumerate(options)
        )
        <= round(scenario.constraints.max_budget * money_scale)
    )
    if scenario.constraints.min_expected_gross_margin is not None:
        model.add(
            sum(
                variables[(entity_id, index)] * round(option.expected_gross_margin * money_scale)
                for entity_id, options in scenario.options_by_entity.items()
                for index, option in enumerate(options)
            )
            >= round(scenario.constraints.min_expected_gross_margin * money_scale)
        )
    if scenario.constraints.min_capacity_delta is not None:
        model.add(
            sum(
                variables[(entity_id, index)] * option.capacity_delta
                for entity_id, options in scenario.options_by_entity.items()
                for index, option in enumerate(options)
            )
            >= scenario.constraints.min_capacity_delta
        )
    entity_count = len(scenario.options_by_entity)
    if scenario.constraints.max_average_risk is not None:
        model.add(
            sum(
                variables[(entity_id, index)] * round(option.risk_score * risk_scale)
                for entity_id, options in scenario.options_by_entity.items()
                for index, option in enumerate(options)
            )
            <= round(scenario.constraints.max_average_risk * entity_count * risk_scale)
        )
    for action, minimum in scenario.constraints.min_action_counts.items():
        model.add(
            sum(
                variables[(entity_id, index)]
                for entity_id, options in scenario.options_by_entity.items()
                for index, option in enumerate(options)
                if option.action is action
            )
            >= minimum
        )
    for action, maximum in scenario.constraints.max_action_counts.items():
        model.add(
            sum(
                variables[(entity_id, index)]
                for entity_id, options in scenario.options_by_entity.items()
                for index, option in enumerate(options)
                if option.action is action
            )
            <= maximum
        )
    model.maximize(
        sum(
            variables[(entity_id, index)]
            * (
                round(option.expected_gross_margin * money_scale)
                - round(option.risk_score * 100_000 * money_scale / entity_count)
            )
            for entity_id, options in scenario.options_by_entity.items()
            for index, option in enumerate(options)
        )
    )
    solver = cp_model.CpSolver()
    status = solver.solve(model)
    if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        return _infeasible_primary(scenario)
    selected = tuple(
        option
        for entity_id, options in scenario.options_by_entity.items()
        for index, option in enumerate(options)
        if solver.value(variables[(entity_id, index)])
    )
    expected = round(sum(option.expected_gross_margin for option in selected), 4)
    budget = round(sum(option.budget_cost for option in selected), 4)
    average_risk = round(
        sum(option.risk_score for option in selected) / len(selected),
        4,
    )
    capacity = sum(option.capacity_delta for option in selected)
    counts = dict(Counter(option.action for option in selected))
    bindings: list[str] = []
    if abs(budget - scenario.constraints.max_budget) <= 0.01:
        bindings.append("max_budget")
    if (
        scenario.constraints.min_expected_gross_margin is not None
        and abs(expected - scenario.constraints.min_expected_gross_margin) <= 0.01
    ):
        bindings.append("min_expected_gross_margin")
    return NetworkPlanSolveResult(
        solver_status=(STATUS_OPTIMAL if status == cp_model.OPTIMAL else STATUS_FEASIBLE),
        objective_value=round(float(solver.objective_value) / money_scale, 4),
        selected_actions=selected,
        expected_gross_margin=expected,
        budget_usage=budget,
        average_risk=average_risk,
        capacity_delta=capacity,
        action_counts=counts,
        binding_constraints=tuple(bindings),
        solver_version="netplan-ortools-cp-sat-v2",
    )


def _infeasible_primary(scenario: NetPlanScenario) -> NetworkPlanSolveResult:
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
        diagnostics=tuple(
            diagnose_infeasible(
                scenario.options_by_entity,
                scenario.constraints,
            )
        ),
        solver_version="netplan-ortools-cp-sat-v2",
    )


def _run_robust_contract(scenario: NetPlanScenario) -> Any:
    scenarios = (
        Scenario("downside", 0.25),
        Scenario("expected", 0.5),
        Scenario("upside", 0.25),
    )
    options = {
        entity_id: tuple(
            ScenarioActionOption(
                option_id=f"{option.entity_id}:{option.action.value}",
                entity_id=option.entity_id,
                action=option.action,
                scenario_values={
                    "downside": max(
                        0.0,
                        option.expected_gross_margin * (1.0 - option.risk_score),
                    ),
                    "expected": option.expected_gross_margin,
                    "upside": option.expected_gross_margin
                    * (1.0 + (1.0 - option.risk_score) * 0.1),
                },
                budget_cost=option.budget_cost,
                risk_score=option.risk_score,
                capacity_delta=option.capacity_delta,
            )
            for option in entity_options
        )
        for entity_id, entity_options in scenario.options_by_entity.items()
    }
    minimums = (
        {"expected": scenario.constraints.min_expected_gross_margin}
        if scenario.constraints.min_expected_gross_margin is not None
        else {}
    )
    return solve_robust_network_plan(
        options_by_entity=options,
        scenarios=scenarios,
        constraints=RobustNetPlanConstraints(
            max_budget=scenario.constraints.max_budget,
            min_value_by_scenario=minimums,
            min_capacity_delta=scenario.constraints.min_capacity_delta,
            max_average_risk=scenario.constraints.max_average_risk,
            min_action_counts=scenario.constraints.min_action_counts,
            max_action_counts=scenario.constraints.max_action_counts,
        ),
        objective=RobustObjective.CVAR,
        downside_weight=0.5,
    )


__all__ = [
    "NetPlanProductionExecution",
    "NetPlanProductionExecutionError",
    "NetPlanProductionExecutor",
]
