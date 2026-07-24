"""OR-Tools CP-SAT implementation of OR-AD-01."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

SOLVER_VERSION = "ad-campaign-ortools-cp-sat-v1"
STATUS_OPTIMAL = "OPTIMAL"
STATUS_FEASIBLE = "FEASIBLE"
STATUS_INFEASIBLE = "INFEASIBLE"
STATUS_TIME_LIMIT = "TIME_LIMIT"
STATUS_FAILED = "FAILED"
_MONEY_SCALE = 1_000


def _cp_model() -> Any:
    from ortools.sat.python import cp_model

    return cp_model


@dataclass(frozen=True)
class CampaignOption:
    option_id: str
    store_id: str
    channel: str
    budget: float
    expected_incremental_gm: float
    downside_risk: float = 0.0
    execution_units: int = 1
    eligible: bool = True
    overlapping_intervention: bool = False
    legal_approved: bool = True
    material_ready: bool = True

    @property
    def admissible(self) -> bool:
        return (
            self.eligible
            and not self.overlapping_intervention
            and self.legal_approved
            and self.material_ready
        )

    def risk_adjusted_value(self, risk_aversion: float) -> float:
        return self.expected_incremental_gm - risk_aversion * self.downside_risk

    def to_dict(self) -> dict[str, Any]:
        return {
            "option_id": self.option_id,
            "store_id": self.store_id,
            "channel": self.channel,
            "budget": self.budget,
            "expected_incremental_gm": self.expected_incremental_gm,
            "downside_risk": self.downside_risk,
            "execution_units": self.execution_units,
        }


@dataclass(frozen=True)
class CampaignConstraints:
    max_budget: float
    max_campaigns: int
    max_execution_units: int
    min_campaigns: int = 0
    max_campaigns_by_channel: Mapping[str, int] = field(default_factory=dict)
    control_store_ids: tuple[str, ...] = ()
    min_control_stores: int = 0


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
class CampaignSelectionResult:
    solver_status: str
    objective_value: float
    selected_campaigns: tuple[CampaignOption, ...]
    expected_incremental_gm: float
    downside_risk: float
    budget_usage: float
    execution_units: int
    retained_control_stores: tuple[str, ...]
    binding_constraints: tuple[str, ...]
    constraint_evaluation: Mapping[str, Mapping[str, Any]]
    not_selected_reasons: Mapping[str, str]
    diagnostics: tuple[SolverDiagnostic, ...] = ()
    solve_time_seconds: float = 0.0
    solver_name: str = "OR_TOOLS_CP_SAT"
    solver_version: str = SOLVER_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "solver_status": self.solver_status,
            "objective_value": self.objective_value,
            "selected_campaigns": [item.to_dict() for item in self.selected_campaigns],
            "expected_incremental_gm": self.expected_incremental_gm,
            "downside_risk": self.downside_risk,
            "budget_usage": self.budget_usage,
            "execution_units": self.execution_units,
            "retained_control_stores": list(self.retained_control_stores),
            "binding_constraints": list(self.binding_constraints),
            "constraint_evaluation": dict(self.constraint_evaluation),
            "not_selected_reasons": dict(self.not_selected_reasons),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "solve_time_seconds": self.solve_time_seconds,
            "solver_name": self.solver_name,
            "solver_version": self.solver_version,
        }


def solve_ad_campaigns(
    *,
    options: tuple[CampaignOption, ...],
    constraints: CampaignConstraints,
    risk_aversion: float = 1.0,
    max_time_seconds: float = 10.0,
) -> CampaignSelectionResult:
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
                    message="OR-Tools CP-SAT is not installed; campaign selection was not run.",
                ),
            ),
        )

    model = cp_model.CpModel()
    variables = {
        option.option_id: model.new_bool_var(f"campaign_{index}")
        for index, option in enumerate(options)
    }
    by_store: defaultdict[str, list[Any]] = defaultdict(list)
    by_channel: defaultdict[str, list[Any]] = defaultdict(list)

    for option in options:
        variable = variables[option.option_id]
        by_store[option.store_id].append(variable)
        by_channel[option.channel].append(variable)
        if not option.admissible:
            model.add(variable == 0)

    for store_variables in by_store.values():
        model.add(sum(store_variables) <= 1)

    selected_count = sum(variables.values())
    model.add(selected_count >= constraints.min_campaigns)
    model.add(selected_count <= constraints.max_campaigns)
    model.add(
        sum(_scaled(option.budget) * variables[option.option_id] for option in options)
        <= _scaled(constraints.max_budget)
    )
    model.add(
        sum(option.execution_units * variables[option.option_id] for option in options)
        <= constraints.max_execution_units
    )
    for channel, maximum in constraints.max_campaigns_by_channel.items():
        model.add(sum(by_channel.get(channel, ())) <= maximum)

    for control_store in constraints.control_store_ids:
        if control_store not in by_store:
            by_store[control_store] = []
    retained_controls = sum(
        1 - sum(by_store[store_id])
        for store_id in constraints.control_store_ids
    )
    model.add(retained_controls >= constraints.min_control_stores)

    model.maximize(
        sum(
            _scaled(option.risk_adjusted_value(risk_aversion))
            * variables[option.option_id]
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
                    message="CP-SAT reached the time limit without a feasible campaign plan.",
                ),
            )
        return _empty_result(status, diagnostics, solve_time=solver.wall_time)

    selected = tuple(
        option for option in options if solver.boolean_value(variables[option.option_id])
    )
    selected_store_ids = {option.store_id for option in selected}
    controls = tuple(
        store_id
        for store_id in constraints.control_store_ids
        if store_id not in selected_store_ids
    )
    budget = round(sum(option.budget for option in selected), 4)
    incremental_gm = round(sum(option.expected_incremental_gm for option in selected), 4)
    downside = round(sum(option.downside_risk for option in selected), 4)
    execution_units = sum(option.execution_units for option in selected)
    channel_counts = {
        channel: sum(1 for option in selected if option.channel == channel)
        for channel in constraints.max_campaigns_by_channel
    }
    evaluation = _constraint_evaluation(
        selected_count=len(selected),
        budget=budget,
        execution_units=execution_units,
        retained_controls=len(controls),
        channel_counts=channel_counts,
        constraints=constraints,
    )
    bindings = tuple(
        name for name, result in evaluation.items() if result.get("binding")
    )
    selected_ids = {option.option_id for option in selected}
    not_selected = {
        option.option_id: _not_selected_reason(option, bindings)
        for option in options
        if option.option_id not in selected_ids
    }
    return CampaignSelectionResult(
        solver_status=status,
        objective_value=round(
            sum(option.risk_adjusted_value(risk_aversion) for option in selected),
            4,
        ),
        selected_campaigns=selected,
        expected_incremental_gm=incremental_gm,
        downside_risk=downside,
        budget_usage=budget,
        execution_units=execution_units,
        retained_control_stores=controls,
        binding_constraints=bindings,
        constraint_evaluation=evaluation,
        not_selected_reasons=not_selected,
        solve_time_seconds=solver.wall_time,
    )


def _validate(
    options: tuple[CampaignOption, ...],
    constraints: CampaignConstraints,
) -> tuple[SolverDiagnostic, ...]:
    diagnostics: list[SolverDiagnostic] = []
    option_ids = [option.option_id for option in options]
    if len(option_ids) != len(set(option_ids)):
        diagnostics.append(
            SolverDiagnostic(
                code="INVALID_INPUT",
                constraint="unique_option_id",
                message="Campaign option IDs must be unique.",
            )
        )
    if (
        constraints.max_budget < 0
        or constraints.min_campaigns < 0
        or constraints.max_campaigns < constraints.min_campaigns
        or constraints.max_execution_units < 0
        or constraints.min_control_stores < 0
        or constraints.min_control_stores > len(set(constraints.control_store_ids))
    ):
        diagnostics.append(
            SolverDiagnostic(
                code="INVALID_INPUT",
                constraint="campaign_constraints",
                message="Campaign bounds are inconsistent or negative.",
            )
        )
    if any(
        option.budget < 0
        or option.downside_risk < 0
        or option.execution_units < 0
        for option in options
    ):
        diagnostics.append(
            SolverDiagnostic(
                code="INVALID_INPUT",
                constraint="campaign_option_values",
                message="Budget, downside risk, and execution units must be non-negative.",
            )
        )
    return tuple(diagnostics)


def _diagnose_infeasible(
    options: tuple[CampaignOption, ...],
    constraints: CampaignConstraints,
) -> tuple[SolverDiagnostic, ...]:
    admissible = tuple(option for option in options if option.admissible)
    eligible_stores = {option.store_id for option in admissible}
    diagnostics: list[SolverDiagnostic] = []
    if len(eligible_stores) < constraints.min_campaigns:
        diagnostics.append(
            SolverDiagnostic(
                code="INSUFFICIENT_ELIGIBLE_STORES",
                constraint="min_campaigns",
                message=(
                    f"{constraints.min_campaigns} campaigns are required but only "
                    f"{len(eligible_stores)} stores have admissible options."
                ),
                affected_entities=tuple(sorted(eligible_stores)),
            )
        )
    cheapest = sorted(
        min(option.budget for option in admissible if option.store_id == store_id)
        for store_id in eligible_stores
    )
    if sum(cheapest[: constraints.min_campaigns]) > constraints.max_budget:
        diagnostics.append(
            SolverDiagnostic(
                code="BUDGET_INFEASIBLE",
                constraint="max_budget",
                message="The cheapest required campaign portfolio exceeds the budget.",
            )
        )
    min_units = sorted(
        min(option.execution_units for option in admissible if option.store_id == store_id)
        for store_id in eligible_stores
    )
    if sum(min_units[: constraints.min_campaigns]) > constraints.max_execution_units:
        diagnostics.append(
            SolverDiagnostic(
                code="CAPACITY_INFEASIBLE",
                constraint="max_execution_units",
                message="The required campaign count exceeds execution capacity.",
            )
        )
    return tuple(diagnostics) or (
        SolverDiagnostic(
            code="COMBINED_CONSTRAINTS_INFEASIBLE",
            constraint="campaign_portfolio",
            message="Campaign constraints are jointly infeasible; no hard limit was relaxed.",
        ),
    )


def _constraint_evaluation(
    *,
    selected_count: int,
    budget: float,
    execution_units: int,
    retained_controls: int,
    channel_counts: Mapping[str, int],
    constraints: CampaignConstraints,
) -> dict[str, dict[str, Any]]:
    evaluation: dict[str, dict[str, Any]] = {
        "min_campaigns": _minimum(selected_count, constraints.min_campaigns),
        "max_campaigns": _maximum(selected_count, constraints.max_campaigns),
        "max_budget": _maximum(budget, constraints.max_budget),
        "max_execution_units": _maximum(
            execution_units, constraints.max_execution_units
        ),
        "min_control_stores": _minimum(
            retained_controls, constraints.min_control_stores
        ),
    }
    for channel, maximum in constraints.max_campaigns_by_channel.items():
        evaluation[f"max_campaigns_by_channel.{channel}"] = _maximum(
            channel_counts.get(channel, 0), maximum
        )
    return evaluation


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


def _not_selected_reason(
    option: CampaignOption,
    bindings: tuple[str, ...],
) -> str:
    if not option.eligible:
        return "INELIGIBLE"
    if option.overlapping_intervention:
        return "INTERVENTION_OVERLAP"
    if not option.legal_approved:
        return "LEGAL_NOT_APPROVED"
    if not option.material_ready:
        return "MATERIAL_NOT_READY"
    if bindings:
        return f"LOWER_OBJECTIVE_OR_BINDING_{bindings[0].upper()}"
    return "LOWER_RISK_ADJUSTED_VALUE"


def _empty_result(
    status: str,
    diagnostics: tuple[SolverDiagnostic, ...],
    *,
    solve_time: float = 0.0,
) -> CampaignSelectionResult:
    return CampaignSelectionResult(
        solver_status=status,
        objective_value=0.0,
        selected_campaigns=(),
        expected_incremental_gm=0.0,
        downside_risk=0.0,
        budget_usage=0.0,
        execution_units=0,
        retained_control_stores=(),
        binding_constraints=(),
        constraint_evaluation={},
        not_selected_reasons={},
        diagnostics=diagnostics,
        solve_time_seconds=solve_time,
    )


def _status(cp_model: Any, status_code: int) -> str:
    return {
        cp_model.OPTIMAL: STATUS_OPTIMAL,
        cp_model.FEASIBLE: STATUS_FEASIBLE,
        cp_model.INFEASIBLE: STATUS_INFEASIBLE,
        cp_model.UNKNOWN: STATUS_TIME_LIMIT,
        cp_model.MODEL_INVALID: STATUS_FAILED,
    }.get(status_code, STATUS_FAILED)


def _scaled(value: float) -> int:
    return int(round(value * _MONEY_SCALE))


def _near(left: float, right: float, tolerance: float = 1e-6) -> bool:
    return abs(left - right) <= tolerance


__all__ = [
    "SOLVER_VERSION",
    "CampaignConstraints",
    "CampaignOption",
    "CampaignSelectionResult",
    "SolverDiagnostic",
    "solve_ad_campaigns",
]
