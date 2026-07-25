"""OR-Tools CP-SAT implementation of OR-SCHED-01."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

SOLVER_VERSION = "intervention-scheduling-ortools-cp-sat-v1"
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
class InterventionTask:
    task_id: str
    store_id: str
    required_skills: frozenset[str]
    priority_value: float
    allowed_slot_ids: tuple[str, ...] = ()
    sla_due_slot_index: int | None = None
    mandatory: bool = False
    hard_sla: bool = False
    conflict_group: str = "INTERVENTION"


@dataclass(frozen=True)
class Resource:
    resource_id: str
    skills: frozenset[str]
    max_tasks_per_day: int
    available_slot_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class TimeSlot:
    slot_id: str
    day: str
    start_minute: int
    end_minute: int


@dataclass(frozen=True)
class SchedulingConstraints:
    min_scheduled_tasks: int = 0
    max_scheduled_tasks: int | None = None
    travel_minutes_by_store_pair: Mapping[tuple[str, str], int] = field(
        default_factory=dict
    )
    late_sla_penalty: float = 100_000.0


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
class ScheduleAssignment:
    task_id: str
    store_id: str
    resource_id: str
    slot_id: str
    day: str
    start_minute: int
    end_minute: int
    meets_sla: bool
    priority_value: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "store_id": self.store_id,
            "resource_id": self.resource_id,
            "slot_id": self.slot_id,
            "day": self.day,
            "start_minute": self.start_minute,
            "end_minute": self.end_minute,
            "meets_sla": self.meets_sla,
            "priority_value": self.priority_value,
        }


@dataclass(frozen=True)
class SchedulingResult:
    solver_status: str
    objective_value: float
    assignments: tuple[ScheduleAssignment, ...]
    unscheduled_reasons: Mapping[str, str]
    sla_risk_task_ids: tuple[str, ...]
    resource_bottlenecks: tuple[str, ...]
    binding_constraints: tuple[str, ...]
    constraint_evaluation: Mapping[str, Mapping[str, Any]]
    diagnostics: tuple[SolverDiagnostic, ...] = ()
    solve_time_seconds: float = 0.0
    solver_name: str = "OR_TOOLS_CP_SAT"
    solver_version: str = SOLVER_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "solver_status": self.solver_status,
            "objective_value": self.objective_value,
            "assignments": [item.to_dict() for item in self.assignments],
            "unscheduled_reasons": dict(self.unscheduled_reasons),
            "sla_risk_task_ids": list(self.sla_risk_task_ids),
            "resource_bottlenecks": list(self.resource_bottlenecks),
            "binding_constraints": list(self.binding_constraints),
            "constraint_evaluation": dict(self.constraint_evaluation),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "solve_time_seconds": self.solve_time_seconds,
            "solver_name": self.solver_name,
            "solver_version": self.solver_version,
        }


def solve_intervention_schedule(
    *,
    tasks: tuple[InterventionTask, ...],
    resources: tuple[Resource, ...],
    slots: tuple[TimeSlot, ...],
    constraints: SchedulingConstraints | None = None,
    max_time_seconds: float = 20.0,
) -> SchedulingResult:
    constraints = constraints or SchedulingConstraints()
    validation = _validate(tasks, resources, slots, constraints)
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
                    message=(
                        "OR-Tools CP-SAT is not installed; intervention scheduling "
                        "was not run."
                    ),
                ),
            ),
        )

    slot_by_id = {slot.slot_id: slot for slot in slots}
    slot_index = {
        slot.slot_id: index
        for index, slot in enumerate(
            sorted(slots, key=lambda item: (item.day, item.start_minute, item.slot_id))
        )
    }
    model = cp_model.CpModel()
    variables: dict[tuple[str, str, str], Any] = {}
    for task in tasks:
        for resource in resources:
            if not task.required_skills.issubset(resource.skills):
                continue
            for slot in slots:
                if task.allowed_slot_ids and slot.slot_id not in task.allowed_slot_ids:
                    continue
                if (
                    resource.available_slot_ids
                    and slot.slot_id not in resource.available_slot_ids
                ):
                    continue
                if (
                    task.hard_sla
                    and task.sla_due_slot_index is not None
                    and slot_index[slot.slot_id] > task.sla_due_slot_index
                ):
                    continue
                key = (task.task_id, resource.resource_id, slot.slot_id)
                variables[key] = model.new_bool_var(
                    f"assign_{task.task_id}_{resource.resource_id}_{slot.slot_id}"
                )

    by_task: defaultdict[str, list[Any]] = defaultdict(list)
    by_resource_slot: defaultdict[tuple[str, str], list[Any]] = defaultdict(list)
    by_resource_day: defaultdict[tuple[str, str], list[Any]] = defaultdict(list)
    by_store_group_slot: defaultdict[tuple[str, str, str], list[Any]] = defaultdict(list)
    for (task_id, resource_id, slot_id), variable in variables.items():
        task = next(item for item in tasks if item.task_id == task_id)
        slot = slot_by_id[slot_id]
        by_task[task_id].append(variable)
        by_resource_slot[(resource_id, slot_id)].append(variable)
        by_resource_day[(resource_id, slot.day)].append(variable)
        by_store_group_slot[(task.store_id, task.conflict_group, slot_id)].append(
            variable
        )

    for task in tasks:
        task_variables = by_task.get(task.task_id, ())
        if task.mandatory:
            model.add(sum(task_variables) == 1)
        else:
            model.add(sum(task_variables) <= 1)

    for resource_slot_variables in by_resource_slot.values():
        model.add(sum(resource_slot_variables) <= 1)
    for resource in resources:
        days = {slot.day for slot in slots}
        for day in days:
            model.add(
                sum(by_resource_day.get((resource.resource_id, day), ()))
                <= resource.max_tasks_per_day
            )
    for conflict_variables in by_store_group_slot.values():
        model.add(sum(conflict_variables) <= 1)

    _add_travel_constraints(
        model=model,
        variables=variables,
        tasks=tasks,
        resources=resources,
        slot_by_id=slot_by_id,
        travel_minutes=constraints.travel_minutes_by_store_pair,
    )

    scheduled_count = sum(variables.values())
    model.add(scheduled_count >= constraints.min_scheduled_tasks)
    if constraints.max_scheduled_tasks is not None:
        model.add(scheduled_count <= constraints.max_scheduled_tasks)

    objective_terms = []
    task_by_id = {task.task_id: task for task in tasks}
    for (task_id, _, slot_id), variable in variables.items():
        task = task_by_id[task_id]
        value = task.priority_value
        if (
            task.sla_due_slot_index is not None
            and slot_index[slot_id] > task.sla_due_slot_index
        ):
            value -= constraints.late_sla_penalty
        objective_terms.append(_scaled(value) * variable)
    model.maximize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max_time_seconds
    solver.parameters.num_search_workers = 1
    status_code = solver.solve(model)
    status = _status(cp_model, status_code)
    if status not in {STATUS_OPTIMAL, STATUS_FEASIBLE}:
        diagnostics = _diagnose_infeasible(
            tasks, resources, slots, constraints, by_task
        )
        if status == STATUS_TIME_LIMIT:
            diagnostics = (
                SolverDiagnostic(
                    code="SOLVER_TIME_LIMIT",
                    constraint="solver_runtime",
                    message=(
                        "CP-SAT reached the time limit without a feasible schedule."
                    ),
                ),
            )
        return _empty_result(status, diagnostics, solve_time=solver.wall_time)

    assignments = tuple(
        sorted(
            (
                ScheduleAssignment(
                    task_id=task_id,
                    store_id=task_by_id[task_id].store_id,
                    resource_id=resource_id,
                    slot_id=slot_id,
                    day=slot_by_id[slot_id].day,
                    start_minute=slot_by_id[slot_id].start_minute,
                    end_minute=slot_by_id[slot_id].end_minute,
                    meets_sla=(
                        task_by_id[task_id].sla_due_slot_index is None
                        or slot_index[slot_id]
                        <= task_by_id[task_id].sla_due_slot_index
                    ),
                    priority_value=task_by_id[task_id].priority_value,
                )
                for (task_id, resource_id, slot_id), variable in variables.items()
                if solver.boolean_value(variable)
            ),
            key=lambda item: (item.day, item.start_minute, item.resource_id),
        )
    )
    assigned_task_ids = {assignment.task_id for assignment in assignments}
    unscheduled = {
        task.task_id: _unscheduled_reason(task, resources, slots)
        for task in tasks
        if task.task_id not in assigned_task_ids
    }
    sla_risk = tuple(
        sorted(
            task.task_id
            for task in tasks
            if task.task_id not in assigned_task_ids
            and task.sla_due_slot_index is not None
        )
    )
    evaluation = _constraint_evaluation(
        assignments, resources, constraints
    )
    bindings = tuple(
        name for name, result in evaluation.items() if result.get("binding")
    )
    bottlenecks = tuple(
        sorted(
            {
                name.split(".")[1]
                for name, result in evaluation.items()
                if name.startswith("resource_daily_capacity.")
                and result.get("binding")
            }
        )
    )
    objective_value = round(
        sum(
            assignment.priority_value
            - (
                constraints.late_sla_penalty
                if not assignment.meets_sla
                else 0.0
            )
            for assignment in assignments
        ),
        4,
    )
    return SchedulingResult(
        solver_status=status,
        objective_value=objective_value,
        assignments=assignments,
        unscheduled_reasons=unscheduled,
        sla_risk_task_ids=sla_risk,
        resource_bottlenecks=bottlenecks,
        binding_constraints=bindings,
        constraint_evaluation=evaluation,
        solve_time_seconds=solver.wall_time,
    )


def _add_travel_constraints(
    *,
    model: Any,
    variables: Mapping[tuple[str, str, str], Any],
    tasks: tuple[InterventionTask, ...],
    resources: tuple[Resource, ...],
    slot_by_id: Mapping[str, TimeSlot],
    travel_minutes: Mapping[tuple[str, str], int],
) -> None:
    task_by_id = {task.task_id: task for task in tasks}
    for resource in resources:
        assignments = [
            (key, variable)
            for key, variable in variables.items()
            if key[1] == resource.resource_id
        ]
        for left_index, (left_key, left_variable) in enumerate(assignments):
            left_task = task_by_id[left_key[0]]
            left_slot = slot_by_id[left_key[2]]
            for right_key, right_variable in assignments[left_index + 1 :]:
                right_task = task_by_id[right_key[0]]
                right_slot = slot_by_id[right_key[2]]
                if left_slot.day != right_slot.day:
                    continue
                if left_slot.start_minute <= right_slot.start_minute:
                    first_task, first_slot = left_task, left_slot
                    second_task, second_slot = right_task, right_slot
                else:
                    first_task, first_slot = right_task, right_slot
                    second_task, second_slot = left_task, left_slot
                required_travel = _travel_minutes(
                    first_task.store_id,
                    second_task.store_id,
                    travel_minutes,
                )
                gap = second_slot.start_minute - first_slot.end_minute
                if gap < required_travel:
                    model.add(left_variable + right_variable <= 1)


def _travel_minutes(
    origin: str,
    destination: str,
    travel_minutes: Mapping[tuple[str, str], int],
) -> int:
    if origin == destination:
        return 0
    return travel_minutes.get(
        (origin, destination),
        travel_minutes.get((destination, origin), 0),
    )


def _validate(
    tasks: tuple[InterventionTask, ...],
    resources: tuple[Resource, ...],
    slots: tuple[TimeSlot, ...],
    constraints: SchedulingConstraints,
) -> tuple[SolverDiagnostic, ...]:
    diagnostics: list[SolverDiagnostic] = []
    for name, identifiers in (
        ("task_id", [item.task_id for item in tasks]),
        ("resource_id", [item.resource_id for item in resources]),
        ("slot_id", [item.slot_id for item in slots]),
    ):
        if len(identifiers) != len(set(identifiers)):
            diagnostics.append(
                SolverDiagnostic(
                    code="INVALID_INPUT",
                    constraint=f"unique_{name}",
                    message=f"{name} values must be unique.",
                )
            )
    slot_ids = {slot.slot_id for slot in slots}
    if any(
        slot.start_minute < 0
        or slot.end_minute <= slot.start_minute
        for slot in slots
    ):
        diagnostics.append(
            SolverDiagnostic(
                code="INVALID_INPUT",
                constraint="time_slot_bounds",
                message="Every time slot must have a positive ordered duration.",
            )
        )
    if any(
        (task.allowed_slot_ids and not set(task.allowed_slot_ids) <= slot_ids)
        or task.priority_value < 0
        for task in tasks
    ):
        diagnostics.append(
            SolverDiagnostic(
                code="INVALID_INPUT",
                constraint="task_values",
                message="Task slots must exist and task priority must be non-negative.",
            )
        )
    if any(
        resource.max_tasks_per_day < 0
        or (
            resource.available_slot_ids
            and not set(resource.available_slot_ids) <= slot_ids
        )
        for resource in resources
    ):
        diagnostics.append(
            SolverDiagnostic(
                code="INVALID_INPUT",
                constraint="resource_values",
                message="Resource availability must reference known slots.",
            )
        )
    if (
        constraints.min_scheduled_tasks < 0
        or (
            constraints.max_scheduled_tasks is not None
            and constraints.max_scheduled_tasks < constraints.min_scheduled_tasks
        )
        or constraints.late_sla_penalty < 0
    ):
        diagnostics.append(
            SolverDiagnostic(
                code="INVALID_INPUT",
                constraint="scheduling_bounds",
                message="Scheduling bounds and penalties must be non-negative.",
            )
        )
    return tuple(diagnostics)


def _diagnose_infeasible(
    tasks: tuple[InterventionTask, ...],
    resources: tuple[Resource, ...],
    slots: tuple[TimeSlot, ...],
    constraints: SchedulingConstraints,
    by_task: Mapping[str, list[Any]],
) -> tuple[SolverDiagnostic, ...]:
    diagnostics: list[SolverDiagnostic] = []
    for task in tasks:
        if task.mandatory and not by_task.get(task.task_id):
            matching_skills = any(
                task.required_skills.issubset(resource.skills)
                for resource in resources
            )
            code = (
                "NO_SKILLED_RESOURCE"
                if not matching_skills
                else "NO_SLA_FEASIBLE_SLOT"
            )
            diagnostics.append(
                SolverDiagnostic(
                    code=code,
                    constraint=f"mandatory_task.{task.task_id}",
                    message=(
                        f"Mandatory task {task.task_id} has no eligible "
                        "resource and time-slot assignment."
                    ),
                    affected_entities=(task.task_id,),
                )
            )
    total_daily_capacity = sum(
        resource.max_tasks_per_day * len({slot.day for slot in slots})
        for resource in resources
    )
    required = max(
        constraints.min_scheduled_tasks,
        sum(1 for task in tasks if task.mandatory),
    )
    if total_daily_capacity < required:
        diagnostics.append(
            SolverDiagnostic(
                code="RESOURCE_CAPACITY_INFEASIBLE",
                constraint="resource_daily_capacity",
                message="Required work exceeds total resource daily capacity.",
            )
        )
    return tuple(diagnostics) or (
        SolverDiagnostic(
            code="COMBINED_CONSTRAINTS_INFEASIBLE",
            constraint="intervention_schedule",
            message=(
                "Skills, availability, SLA, travel, store conflicts, and "
                "resource capacity are jointly infeasible."
            ),
        ),
    )


def _constraint_evaluation(
    assignments: tuple[ScheduleAssignment, ...],
    resources: tuple[Resource, ...],
    constraints: SchedulingConstraints,
) -> dict[str, dict[str, Any]]:
    evaluation: dict[str, dict[str, Any]] = {
        "min_scheduled_tasks": _minimum(
            len(assignments), constraints.min_scheduled_tasks
        )
    }
    if constraints.max_scheduled_tasks is not None:
        evaluation["max_scheduled_tasks"] = _maximum(
            len(assignments), constraints.max_scheduled_tasks
        )
    days = {assignment.day for assignment in assignments}
    for resource in resources:
        for day in days:
            evaluation[
                f"resource_daily_capacity.{resource.resource_id}.{day}"
            ] = _maximum(
                sum(
                    1
                    for assignment in assignments
                    if assignment.resource_id == resource.resource_id
                    and assignment.day == day
                ),
                resource.max_tasks_per_day,
            )
    return evaluation


def _unscheduled_reason(
    task: InterventionTask,
    resources: tuple[Resource, ...],
    slots: tuple[TimeSlot, ...],
) -> str:
    if not any(task.required_skills.issubset(resource.skills) for resource in resources):
        return "NO_SKILLED_RESOURCE"
    if task.allowed_slot_ids and not any(
        slot.slot_id in task.allowed_slot_ids for slot in slots
    ):
        return "NO_ALLOWED_SLOT"
    if task.sla_due_slot_index is not None:
        return "CAPACITY_OR_TRAVEL_CONFLICT_WITH_SLA_RISK"
    return "LOWER_PRIORITY_OR_CAPACITY_CONFLICT"


def _empty_result(
    status: str,
    diagnostics: tuple[SolverDiagnostic, ...],
    *,
    solve_time: float = 0.0,
) -> SchedulingResult:
    return SchedulingResult(
        solver_status=status,
        objective_value=0.0,
        assignments=(),
        unscheduled_reasons={},
        sla_risk_task_ids=(),
        resource_bottlenecks=(),
        binding_constraints=(),
        constraint_evaluation={},
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
    "InterventionTask",
    "Resource",
    "ScheduleAssignment",
    "SchedulingConstraints",
    "SchedulingResult",
    "SolverDiagnostic",
    "TimeSlot",
    "solve_intervention_schedule",
]
