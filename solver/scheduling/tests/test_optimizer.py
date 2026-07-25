from solver.scheduling import (
    InterventionTask,
    Resource,
    SchedulingConstraints,
    TimeSlot,
    solve_intervention_schedule,
)


def _slots() -> tuple[TimeSlot, ...]:
    return (
        TimeSlot("morning", "2026-08-03", 9 * 60, 10 * 60),
        TimeSlot("midday", "2026-08-03", 10 * 60 + 15, 11 * 60 + 15),
        TimeSlot("afternoon", "2026-08-03", 13 * 60, 14 * 60),
    )


def test_cp_sat_schedules_skills_sla_travel_and_capacity() -> None:
    tasks = (
        InterventionTask(
            "repair-red",
            "store-a",
            frozenset({"repair"}),
            1_000,
            sla_due_slot_index=0,
            mandatory=True,
            hard_sla=True,
        ),
        InterventionTask(
            "clean",
            "store-b",
            frozenset({"cleaning"}),
            500,
        ),
        InterventionTask(
            "repair-low",
            "store-c",
            frozenset({"repair"}),
            100,
        ),
    )
    resources = (
        Resource("tech", frozenset({"repair"}), 2),
        Resource("supervisor", frozenset({"cleaning"}), 1),
    )
    result = solve_intervention_schedule(
        tasks=tasks,
        resources=resources,
        slots=_slots(),
        constraints=SchedulingConstraints(
            min_scheduled_tasks=2,
            max_scheduled_tasks=2,
            travel_minutes_by_store_pair={
                ("store-a", "store-c"): 30,
            },
        ),
    )

    assert result.solver_status == "OPTIMAL"
    assert {item.task_id for item in result.assignments} == {
        "repair-red",
        "clean",
    }
    assert next(
        item for item in result.assignments if item.task_id == "repair-red"
    ).slot_id == "morning"
    assert result.unscheduled_reasons["repair-low"] == (
        "LOWER_PRIORITY_OR_CAPACITY_CONFLICT"
    )
    assert result.constraint_evaluation["max_scheduled_tasks"]["binding"] is True


def test_cp_sat_reports_missing_skill_for_mandatory_task() -> None:
    result = solve_intervention_schedule(
        tasks=(
            InterventionTask(
                "legal-review",
                "store-a",
                frozenset({"legal"}),
                100,
                mandatory=True,
            ),
        ),
        resources=(Resource("tech", frozenset({"repair"}), 2),),
        slots=_slots(),
    )

    assert result.solver_status == "INFEASIBLE"
    assert result.assignments == ()
    assert result.diagnostics[0].code == "NO_SKILLED_RESOURCE"


def test_cp_sat_accounts_for_travel_time_between_stores() -> None:
    result = solve_intervention_schedule(
        tasks=(
            InterventionTask(
                "repair-a",
                "store-a",
                frozenset({"repair"}),
                500,
                allowed_slot_ids=("morning",),
                mandatory=True,
            ),
            InterventionTask(
                "repair-c",
                "store-c",
                frozenset({"repair"}),
                400,
                allowed_slot_ids=("midday", "afternoon"),
                mandatory=True,
            ),
        ),
        resources=(Resource("tech", frozenset({"repair"}), 2),),
        slots=_slots(),
        constraints=SchedulingConstraints(
            travel_minutes_by_store_pair={("store-a", "store-c"): 30}
        ),
    )

    assert result.solver_status == "OPTIMAL"
    assigned_slots = {
        item.task_id: item.slot_id for item in result.assignments
    }
    assert assigned_slots == {
        "repair-a": "morning",
        "repair-c": "afternoon",
    }
