"""OR-Tools CP-SAT implementation of OR-ROUTE-01."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

SOLVER_VERSION = "routeplan-ortools-cp-sat-v1"
STATUS_OPTIMAL = "OPTIMAL"
STATUS_FEASIBLE = "FEASIBLE"
STATUS_INFEASIBLE = "INFEASIBLE"
STATUS_TIME_LIMIT = "TIME_LIMIT"
STATUS_FAILED = "FAILED"
_VALUE_SCALE = 1_000


def _cp_model() -> Any:
    from ortools.sat.python import cp_model

    return cp_model


@dataclass(frozen=True)
class RouteOption:
    option_id: str
    site_id: str
    quarter: str
    region: str
    expected_npv: float
    capital_cost: float
    labor_units: int
    construction_units: int
    cannibalization: float = 0.0
    execution_risk: float = 0.0
    admissible: bool = True

    def objective_value(
        self,
        *,
        cannibalization_penalty: float,
        risk_penalty: float,
    ) -> float:
        return (
            self.expected_npv
            - cannibalization_penalty * self.cannibalization
            - risk_penalty * self.execution_risk
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "option_id": self.option_id,
            "site_id": self.site_id,
            "quarter": self.quarter,
            "region": self.region,
            "expected_npv": self.expected_npv,
            "capital_cost": self.capital_cost,
            "labor_units": self.labor_units,
            "construction_units": self.construction_units,
            "cannibalization": self.cannibalization,
            "execution_risk": self.execution_risk,
        }


@dataclass(frozen=True)
class RouteConstraints:
    quarters: tuple[str, ...]
    capital_budget_by_quarter: Mapping[str, float]
    labor_capacity_by_quarter: Mapping[str, int]
    construction_capacity_by_quarter: Mapping[str, int]
    max_cannibalization_by_quarter: Mapping[str, float] = field(default_factory=dict)
    min_openings_by_quarter: Mapping[str, int] = field(default_factory=dict)
    max_openings_by_quarter: Mapping[str, int] = field(default_factory=dict)
    min_region_openings: Mapping[str, int] = field(default_factory=dict)
    max_region_openings: Mapping[str, int] = field(default_factory=dict)
    minimum_region_spacing: Mapping[str, int] = field(default_factory=dict)
    min_total_openings: int = 0
    max_total_openings: int | None = None


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
class RoutePlanAlternative:
    objective_value: float
    scheduled_openings: tuple[RouteOption, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective_value": self.objective_value,
            "scheduled_openings": [item.to_dict() for item in self.scheduled_openings],
        }


@dataclass(frozen=True)
class RoutePlanResult:
    solver_status: str
    objective_value: float
    scheduled_openings: tuple[RouteOption, ...]
    total_expected_npv: float
    total_capital_cost: float
    binding_constraints: tuple[str, ...]
    constraint_evaluation: Mapping[str, Mapping[str, Any]]
    unscheduled_reasons: Mapping[str, str]
    alternatives: tuple[RoutePlanAlternative, ...] = ()
    diagnostics: tuple[SolverDiagnostic, ...] = ()
    solve_time_seconds: float = 0.0
    solver_name: str = "OR_TOOLS_CP_SAT"
    solver_version: str = SOLVER_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "solver_status": self.solver_status,
            "objective_value": self.objective_value,
            "scheduled_openings": [item.to_dict() for item in self.scheduled_openings],
            "total_expected_npv": self.total_expected_npv,
            "total_capital_cost": self.total_capital_cost,
            "binding_constraints": list(self.binding_constraints),
            "constraint_evaluation": dict(self.constraint_evaluation),
            "unscheduled_reasons": dict(self.unscheduled_reasons),
            "alternatives": [item.to_dict() for item in self.alternatives],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "solve_time_seconds": self.solve_time_seconds,
            "solver_name": self.solver_name,
            "solver_version": self.solver_version,
        }


def solve_routeplan(
    *,
    options: tuple[RouteOption, ...],
    constraints: RouteConstraints,
    cannibalization_penalty: float = 100_000.0,
    risk_penalty: float = 100_000.0,
    alternative_limit: int = 1,
    max_time_seconds: float = 20.0,
) -> RoutePlanResult:
    validation = _validate(options, constraints)
    if validation:
        return _empty_result(STATUS_FAILED, validation)

    try:
        cp_model = _cp_model()
    except ImportError:
        return _empty_result(
            STATUS_FAILED,
            (
                SolverDiagnostic(
                    code="SOLVER_UNAVAILABLE",
                    constraint="solver_runtime",
                    message="OR-Tools CP-SAT is not installed; RoutePlan was not run.",
                ),
            ),
        )

    model = cp_model.CpModel()
    variables = {
        option.option_id: model.new_bool_var(f"route_{index}")
        for index, option in enumerate(options)
    }
    by_site: defaultdict[str, list[Any]] = defaultdict(list)
    by_quarter: defaultdict[str, list[tuple[RouteOption, Any]]] = defaultdict(list)
    by_region: defaultdict[str, list[Any]] = defaultdict(list)
    for option in options:
        variable = variables[option.option_id]
        by_site[option.site_id].append(variable)
        by_quarter[option.quarter].append((option, variable))
        by_region[option.region].append(variable)
        if not option.admissible:
            model.add(variable == 0)

    for site_variables in by_site.values():
        model.add(sum(site_variables) <= 1)

    selected_count = sum(variables.values())
    model.add(selected_count >= constraints.min_total_openings)
    if constraints.max_total_openings is not None:
        model.add(selected_count <= constraints.max_total_openings)

    for quarter in constraints.quarters:
        quarter_options = by_quarter.get(quarter, ())
        model.add(
            sum(_scaled(option.capital_cost) * variable for option, variable in quarter_options)
            <= _scaled(constraints.capital_budget_by_quarter[quarter])
        )
        model.add(
            sum(option.labor_units * variable for option, variable in quarter_options)
            <= constraints.labor_capacity_by_quarter[quarter]
        )
        model.add(
            sum(option.construction_units * variable for option, variable in quarter_options)
            <= constraints.construction_capacity_by_quarter[quarter]
        )
        if quarter in constraints.max_cannibalization_by_quarter:
            model.add(
                sum(_scaled(option.cannibalization) * variable for option, variable in quarter_options)
                <= _scaled(constraints.max_cannibalization_by_quarter[quarter])
            )
        if quarter in constraints.min_openings_by_quarter:
            model.add(
                sum(variable for _, variable in quarter_options)
                >= constraints.min_openings_by_quarter[quarter]
            )
        if quarter in constraints.max_openings_by_quarter:
            model.add(
                sum(variable for _, variable in quarter_options)
                <= constraints.max_openings_by_quarter[quarter]
            )

    for region, minimum in constraints.min_region_openings.items():
        model.add(sum(by_region.get(region, ())) >= minimum)
    for region, maximum in constraints.max_region_openings.items():
        model.add(sum(by_region.get(region, ())) <= maximum)

    quarter_index = {quarter: index for index, quarter in enumerate(constraints.quarters)}
    for region, spacing in constraints.minimum_region_spacing.items():
        region_options = [option for option in options if option.region == region]
        for left_index, left in enumerate(region_options):
            for right in region_options[left_index + 1 :]:
                distance = abs(quarter_index[left.quarter] - quarter_index[right.quarter])
                if left.site_id != right.site_id and distance < spacing:
                    model.add(
                        variables[left.option_id] + variables[right.option_id] <= 1
                    )

    score_by_option = {
        option.option_id: option.objective_value(
            cannibalization_penalty=cannibalization_penalty,
            risk_penalty=risk_penalty,
        )
        for option in options
    }
    model.maximize(
        sum(
            _scaled(score_by_option[option.option_id]) * variables[option.option_id]
            for option in options
        )
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max_time_seconds
    solver.parameters.num_search_workers = 1
    status_code = solver.solve(model)
    status = _status(cp_model, status_code)
    if status not in {STATUS_OPTIMAL, STATUS_FEASIBLE}:
        diagnostics = _diagnose_infeasible(options, constraints)
        if status == STATUS_TIME_LIMIT:
            diagnostics = (
                SolverDiagnostic(
                    code="SOLVER_TIME_LIMIT",
                    constraint="solver_runtime",
                    message="CP-SAT reached the time limit without a feasible RoutePlan.",
                ),
            )
        return _empty_result(status, diagnostics, solve_time=solver.wall_time)

    selected = _selected(options, variables, solver)
    alternatives: list[RoutePlanAlternative] = []
    previous_selections: list[tuple[str, ...]] = [
        tuple(item.option_id for item in selected)
    ]
    for _ in range(max(0, alternative_limit)):
        last_selection = previous_selections[-1]
        if not last_selection:
            break
        model.add(
            sum(variables[option_id] for option_id in last_selection)
            <= len(last_selection) - 1
        )
        alternative_status = solver.solve(model)
        if _status(cp_model, alternative_status) not in {
            STATUS_OPTIMAL,
            STATUS_FEASIBLE,
        }:
            break
        alternative = _selected(options, variables, solver)
        signature = tuple(item.option_id for item in alternative)
        if signature in previous_selections:
            break
        previous_selections.append(signature)
        alternatives.append(
            RoutePlanAlternative(
                objective_value=round(
                    sum(score_by_option[item.option_id] for item in alternative),
                    4,
                ),
                scheduled_openings=alternative,
            )
        )

    evaluation = _constraint_evaluation(selected, constraints)
    bindings = tuple(
        name for name, result in evaluation.items() if result.get("binding")
    )
    selected_sites = {item.site_id for item in selected}
    unscheduled = {
        site_id: _unscheduled_reason(
            tuple(item for item in options if item.site_id == site_id),
            bindings,
        )
        for site_id in sorted(by_site)
        if site_id not in selected_sites
    }
    return RoutePlanResult(
        solver_status=status,
        objective_value=round(sum(score_by_option[item.option_id] for item in selected), 4),
        scheduled_openings=selected,
        total_expected_npv=round(sum(item.expected_npv for item in selected), 4),
        total_capital_cost=round(sum(item.capital_cost for item in selected), 4),
        binding_constraints=bindings,
        constraint_evaluation=evaluation,
        unscheduled_reasons=unscheduled,
        alternatives=tuple(alternatives),
        solve_time_seconds=solver.wall_time,
    )


def _validate(
    options: tuple[RouteOption, ...],
    constraints: RouteConstraints,
) -> tuple[SolverDiagnostic, ...]:
    diagnostics: list[SolverDiagnostic] = []
    if not constraints.quarters or len(constraints.quarters) != len(set(constraints.quarters)):
        diagnostics.append(
            SolverDiagnostic(
                code="INVALID_INPUT",
                constraint="quarters",
                message="Planning quarters must be non-empty and unique.",
            )
        )
    required_maps = (
        constraints.capital_budget_by_quarter,
        constraints.labor_capacity_by_quarter,
        constraints.construction_capacity_by_quarter,
    )
    if any(set(mapping) != set(constraints.quarters) for mapping in required_maps):
        diagnostics.append(
            SolverDiagnostic(
                code="INVALID_INPUT",
                constraint="quarter_capacity_maps",
                message="Budget, labor, and construction maps must cover every quarter exactly.",
            )
        )
    option_ids = [item.option_id for item in options]
    known_quarters = set(constraints.quarters)
    if len(option_ids) != len(set(option_ids)):
        diagnostics.append(
            SolverDiagnostic(
                code="INVALID_INPUT",
                constraint="unique_option_id",
                message="Route option IDs must be unique.",
            )
        )
    if any(item.quarter not in known_quarters for item in options):
        diagnostics.append(
            SolverDiagnostic(
                code="INVALID_INPUT",
                constraint="option_quarter",
                message="Every route option must reference a planning quarter.",
            )
        )
    if (
        constraints.min_total_openings < 0
        or (
            constraints.max_total_openings is not None
            and constraints.max_total_openings < constraints.min_total_openings
        )
        or any(
            item.capital_cost < 0
            or item.labor_units < 0
            or item.construction_units < 0
            or item.cannibalization < 0
            or item.execution_risk < 0
            for item in options
        )
    ):
        diagnostics.append(
            SolverDiagnostic(
                code="INVALID_INPUT",
                constraint="route_bounds",
                message="RoutePlan bounds and option resource values must be non-negative.",
            )
        )
    return tuple(diagnostics)


def _diagnose_infeasible(
    options: tuple[RouteOption, ...],
    constraints: RouteConstraints,
) -> tuple[SolverDiagnostic, ...]:
    admissible_sites = {item.site_id for item in options if item.admissible}
    diagnostics: list[SolverDiagnostic] = []
    if len(admissible_sites) < constraints.min_total_openings:
        diagnostics.append(
            SolverDiagnostic(
                code="INSUFFICIENT_ADMISSIBLE_SITES",
                constraint="min_total_openings",
                message=(
                    f"{constraints.min_total_openings} openings are required but only "
                    f"{len(admissible_sites)} sites are admissible."
                ),
                affected_entities=tuple(sorted(admissible_sites)),
            )
        )
    for region, minimum in constraints.min_region_openings.items():
        sites = {
            item.site_id
            for item in options
            if item.admissible and item.region == region
        }
        if len(sites) < minimum:
            diagnostics.append(
                SolverDiagnostic(
                    code="REGION_MINIMUM_INFEASIBLE",
                    constraint=f"min_region_openings.{region}",
                    message=f"Region {region} lacks enough admissible sites.",
                    affected_entities=tuple(sorted(sites)),
                )
            )
    return tuple(diagnostics) or (
        SolverDiagnostic(
            code="COMBINED_CONSTRAINTS_INFEASIBLE",
            constraint="routeplan",
            message=(
                "Capital, labor, construction, geography, availability, and "
                "cannibalization constraints are jointly infeasible."
            ),
        ),
    )


def _selected(
    options: tuple[RouteOption, ...],
    variables: Mapping[str, Any],
    solver: Any,
) -> tuple[RouteOption, ...]:
    return tuple(
        sorted(
            (
                item
                for item in options
                if solver.boolean_value(variables[item.option_id])
            ),
            key=lambda item: (item.quarter, item.site_id),
        )
    )


def _constraint_evaluation(
    selected: tuple[RouteOption, ...],
    constraints: RouteConstraints,
) -> dict[str, dict[str, Any]]:
    evaluation: dict[str, dict[str, Any]] = {
        "min_total_openings": _minimum(
            len(selected), constraints.min_total_openings
        )
    }
    if constraints.max_total_openings is not None:
        evaluation["max_total_openings"] = _maximum(
            len(selected), constraints.max_total_openings
        )
    for quarter in constraints.quarters:
        quarter_items = tuple(item for item in selected if item.quarter == quarter)
        evaluation[f"capital_budget.{quarter}"] = _maximum(
            round(sum(item.capital_cost for item in quarter_items), 4),
            constraints.capital_budget_by_quarter[quarter],
        )
        evaluation[f"labor_capacity.{quarter}"] = _maximum(
            sum(item.labor_units for item in quarter_items),
            constraints.labor_capacity_by_quarter[quarter],
        )
        evaluation[f"construction_capacity.{quarter}"] = _maximum(
            sum(item.construction_units for item in quarter_items),
            constraints.construction_capacity_by_quarter[quarter],
        )
        if quarter in constraints.max_cannibalization_by_quarter:
            evaluation[f"max_cannibalization.{quarter}"] = _maximum(
                round(sum(item.cannibalization for item in quarter_items), 4),
                constraints.max_cannibalization_by_quarter[quarter],
            )
        if quarter in constraints.min_openings_by_quarter:
            evaluation[f"min_openings.{quarter}"] = _minimum(
                len(quarter_items), constraints.min_openings_by_quarter[quarter]
            )
        if quarter in constraints.max_openings_by_quarter:
            evaluation[f"max_openings.{quarter}"] = _maximum(
                len(quarter_items), constraints.max_openings_by_quarter[quarter]
            )
    for region, minimum in constraints.min_region_openings.items():
        evaluation[f"min_region_openings.{region}"] = _minimum(
            sum(1 for item in selected if item.region == region), minimum
        )
    for region, maximum in constraints.max_region_openings.items():
        evaluation[f"max_region_openings.{region}"] = _maximum(
            sum(1 for item in selected if item.region == region), maximum
        )
    return evaluation


def _unscheduled_reason(
    site_options: tuple[RouteOption, ...],
    bindings: tuple[str, ...],
) -> str:
    if not any(item.admissible for item in site_options):
        return "SITE_NOT_ADMISSIBLE"
    if bindings:
        return f"LOWER_OBJECTIVE_OR_BINDING_{bindings[0].upper()}"
    return "LOWER_RISK_ADJUSTED_NPV"


def _empty_result(
    status: str,
    diagnostics: tuple[SolverDiagnostic, ...],
    *,
    solve_time: float = 0.0,
) -> RoutePlanResult:
    return RoutePlanResult(
        solver_status=status,
        objective_value=0.0,
        scheduled_openings=(),
        total_expected_npv=0.0,
        total_capital_cost=0.0,
        binding_constraints=(),
        constraint_evaluation={},
        unscheduled_reasons={},
        diagnostics=diagnostics,
        solve_time_seconds=solve_time,
    )


def _minimum(actual: float, limit: float) -> dict[str, Any]:
    return {
        "actual": actual,
        "operator": ">=",
        "limit": limit,
        "satisfied": actual >= limit,
        "binding": _near(actual, limit),
    }


def _maximum(actual: float, limit: float) -> dict[str, Any]:
    return {
        "actual": actual,
        "operator": "<=",
        "limit": limit,
        "satisfied": actual <= limit,
        "binding": _near(actual, limit),
    }


def _status(cp_model: Any, status_code: int) -> str:
    return {
        cp_model.OPTIMAL: STATUS_OPTIMAL,
        cp_model.FEASIBLE: STATUS_FEASIBLE,
        cp_model.INFEASIBLE: STATUS_INFEASIBLE,
        cp_model.UNKNOWN: STATUS_TIME_LIMIT,
        cp_model.MODEL_INVALID: STATUS_FAILED,
    }.get(status_code, STATUS_FAILED)


def _scaled(value: float) -> int:
    return int(round(value * _VALUE_SCALE))


def _near(left: float, right: float, tolerance: float = 1e-6) -> bool:
    return abs(left - right) <= tolerance


__all__ = [
    "SOLVER_VERSION",
    "RouteConstraints",
    "RouteOption",
    "RoutePlanAlternative",
    "RoutePlanResult",
    "SolverDiagnostic",
    "solve_routeplan",
]
