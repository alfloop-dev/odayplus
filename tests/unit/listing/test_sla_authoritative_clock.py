from __future__ import annotations

import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from modules.listing.application.assignment_sla import (
    AssignmentSlaService,
    InMemoryAssignmentRepository,
    InMemorySlaRepository,
)
from modules.listing.domain.intake_states import (
    Actor,
    DomainValidationError,
    PrincipalRole,
    SlaSchedule,
    SlaState,
    TransitionContext,
)

START = datetime(2026, 10, 12, 0, tzinfo=UTC)


def schedule():
    # Explicit synthetic policy/calendar values, not production defaults.
    return SlaSchedule(
        tenant_id="tenant-a",
        policy_version="fixture-policy-v7",
        calendar_version="fixture-calendar-v3",
        started_at=START,
        due_soon_at=START + timedelta(hours=5, minutes=53),
        due_at=START + timedelta(hours=6),
        breach_at=START + timedelta(hours=11),
    )


def context(at=START, role=PrincipalRole.SVC_SLA, tenant="tenant-a", version=None):
    return TransitionContext(
        actor=Actor("fixture-actor", role, tenant),
        current_time=at,
        version_before=version,
    )


class FixtureCalendar:
    def __init__(self):
        self.calls = []
        self.allowed = True
        self.failure = None
        self.result = None

    def permits_pause(self, value, reason, expected_resume_at):
        self.calls.append(("pause", value, reason, expected_resume_at))
        return self.allowed

    def resume_schedule(self, value, interval, resumed_at):
        self.calls.append(("resume", value, interval, resumed_at))
        if self.failure:
            raise self.failure
        if self.result is not None:
            return self.result
        # This synthetic calendar is 24x7. The production service must delegate
        # and must not assume this arithmetic for a real business calendar.
        excluded = resumed_at - interval.started_at
        return replace(
            value,
            due_soon_at=value.due_soon_at + excluded,
            due_at=value.due_at + excluded,
            breach_at=value.breach_at + excluded,
        )


def service(calendar=None):
    return AssignmentSlaService(
        InMemoryAssignmentRepository(), InMemorySlaRepository(), calendar=calendar
    )


@pytest.mark.parametrize(
    "offset,state",
    [
        (timedelta(hours=4, minutes=30), SlaState.ON_TRACK),
        (timedelta(hours=5, minutes=52, seconds=59), SlaState.ON_TRACK),
        (timedelta(hours=5, minutes=53), SlaState.DUE_SOON),
        (timedelta(hours=6), SlaState.OVERDUE),
        (timedelta(hours=10, minutes=59, seconds=59), SlaState.OVERDUE),
        (timedelta(hours=11), SlaState.BREACHED),
    ],
)
def test_policy_checkpoints_and_exact_boundaries_replace_fixed_windows(offset, state):
    assert service().get_derived_sla_state(schedule(), START + offset) == state


@pytest.mark.parametrize(
    "field,value",
    [
        ("policy_version", ""),
        ("calendar_version", " "),
        ("tenant_id", ""),
        ("due_soon_at", START + timedelta(hours=7)),
        ("breach_at", START + timedelta(hours=5)),
        ("started_at", START + timedelta(hours=6)),
        ("due_at", START.replace(tzinfo=None)),
    ],
)
def test_schedule_rejects_missing_versions_and_invalid_checkpoints(field, value):
    with pytest.raises(ValueError):
        replace(schedule(), **{field: value})


@pytest.mark.parametrize("kind", ["missing", "raw_due", "foreign_schedule", "foreign_actor"])
def test_creation_refuses_unresolved_or_foreign_schedule_without_writes(kind):
    svc = service()
    value = {
        "missing": None,
        "raw_due": schedule().due_at,
        "foreign_schedule": replace(schedule(), tenant_id="tenant-b"),
        "foreign_actor": schedule(),
    }[kind]
    actor = context(tenant="tenant-b" if kind == "foreign_actor" else "tenant-a")
    with pytest.raises(DomainValidationError):
        svc.create_sla("SLA-1", "tenant-a", value, actor)
    assert svc.sla_repo.get_sla("SLA-1") is None
    assert svc.emitted_events == []


def test_creation_preserves_versioned_deadline_provenance():
    svc = service()
    value = schedule()
    sla = svc.create_sla("SLA-1", "tenant-a", value, context())
    assert sla.schedule == value and sla.due_at == value.due_at
    payload = svc.emitted_events[0]["payload"]
    assert payload["policy_version"] == value.policy_version
    assert payload["calendar_version"] == value.calendar_version
    assert payload["due_soon_at"] == value.due_soon_at.isoformat()


def test_evaluation_emits_once_per_version_and_keeps_noop_immutable():
    svc = service()
    sla = svc.create_sla("SLA-1", "tenant-a", schedule(), context())
    svc.update_sla_derived_state("SLA-1", context(schedule().due_soon_at))
    before = copy.deepcopy((sla, svc.emitted_events))
    svc.update_sla_derived_state("SLA-1", context(schedule().due_soon_at))
    assert (sla, svc.emitted_events) == before


@pytest.mark.parametrize("state", [SlaState.ON_TRACK, SlaState.PAUSED, SlaState.COMPLETED])
@pytest.mark.parametrize("kind", ["foreign", "stale", "staff"])
def test_noop_and_terminal_evaluation_still_checks_access_and_version(state, kind):
    svc = service()
    sla = svc.create_sla("SLA-1", "tenant-a", schedule(), context())
    sla.status = state
    before = copy.deepcopy((sla, svc.emitted_events))
    actor = context(
        tenant="tenant-b" if kind == "foreign" else "tenant-a",
        version=0 if kind == "stale" else None,
        role=PrincipalRole.EXPANSION_STAFF if kind == "staff" else PrincipalRole.SVC_SLA,
    )
    with pytest.raises(DomainValidationError):
        svc.update_sla_derived_state("SLA-1", actor)
    assert (sla, svc.emitted_events) == before


@pytest.mark.parametrize(
    "kind", ["no_calendar", "denied_policy", "empty_reason", "past_resume", "staff"]
)
def test_pause_requires_policy_calendar_and_valid_interval_before_any_mutation(kind):
    cal = FixtureCalendar()
    cal.allowed = kind != "denied_policy"
    svc = service(None if kind == "no_calendar" else cal)
    sla = svc.create_sla("SLA-1", "tenant-a", schedule(), context())
    before = copy.deepcopy((sla, svc.emitted_events))
    with pytest.raises(DomainValidationError):
        svc.pause_sla(
            "SLA-1",
            "" if kind == "empty_reason" else "fixture approved reason",
            START if kind == "past_resume" else START + timedelta(days=2),
            context(
                role=PrincipalRole.EXPANSION_STAFF
                if kind == "staff"
                else PrincipalRole.EXPANSION_MANAGER
            ),
        )
    assert (sla, svc.emitted_events) == before
    if kind in {"empty_reason", "past_resume", "staff"}:
        assert cal.calls == []


def test_paused_days_do_not_consume_active_sla_time():
    cal = FixtureCalendar()
    svc = service(cal)
    sla = svc.create_sla("SLA-1", "tenant-a", schedule(), context())
    paused_at = START + timedelta(hours=1)
    svc.pause_sla(
        "SLA-1",
        "fixture approved reason",
        START + timedelta(days=4),
        context(paused_at, PrincipalRole.EXPANSION_MANAGER),
    )
    svc.update_sla_derived_state("SLA-1", context(START + timedelta(days=3)))
    assert sla.status == SlaState.PAUSED
    svc.resume_sla("SLA-1", context(paused_at + timedelta(days=3)))
    assert sla.status == SlaState.ON_TRACK
    assert sla.due_at == schedule().due_at + timedelta(days=3)
    assert sla.paused_duration_seconds == 3 * 86400
    assert sla.pause_intervals[-1].ended_at == paused_at + timedelta(days=3)
    assert svc.emitted_events[-1]["payload"]["due_at"] == sla.due_at.isoformat()


def test_resume_uses_business_calendar_result_instead_of_wall_time_arithmetic():
    cal = FixtureCalendar()
    svc = service(cal)
    sla = svc.create_sla("SLA-1", "tenant-a", schedule(), context())
    svc.pause_sla(
        "SLA-1",
        "fixture approved reason",
        START + timedelta(days=5),
        context(START + timedelta(hours=1), PrincipalRole.EXPANSION_MANAGER),
    )
    cal.result = replace(
        schedule(),
        due_soon_at=START + timedelta(days=6),
        due_at=START + timedelta(days=7),
        breach_at=START + timedelta(days=8),
    )
    svc.resume_sla("SLA-1", context(START + timedelta(days=4)))
    assert sla.schedule == cal.result
    assert sla.due_at == START + timedelta(days=7)
    assert sla.paused_duration_seconds == 4 * 86400 - 3600


@pytest.mark.parametrize(
    "kind",
    [
        "provider_failure",
        "missing_calendar",
        "foreign_reply",
        "policy_switch",
        "calendar_switch",
        "backwards_time",
        "foreign_actor",
        "stale_version",
    ],
)
def test_failed_resumption_keeps_interval_resource_and_events_unchanged(kind):
    cal = FixtureCalendar()
    svc = service(cal)
    sla = svc.create_sla("SLA-1", "tenant-a", schedule(), context())
    svc.pause_sla(
        "SLA-1",
        "fixture approved reason",
        START + timedelta(days=2),
        context(START + timedelta(hours=1), PrincipalRole.EXPANSION_MANAGER),
    )
    if kind == "provider_failure":
        cal.failure = RuntimeError("fixture unavailable")
    if kind == "missing_calendar":
        svc.calendar = None
    if kind == "foreign_reply":
        cal.result = replace(schedule(), tenant_id="tenant-b")
    if kind == "policy_switch":
        cal.result = replace(schedule(), policy_version="fixture-other-policy")
    if kind == "calendar_switch":
        cal.result = replace(schedule(), calendar_version="fixture-other-calendar")
    actor = context(
        START if kind == "backwards_time" else START + timedelta(hours=3),
        tenant="tenant-b" if kind == "foreign_actor" else "tenant-a",
        version=0 if kind == "stale_version" else None,
    )
    before = copy.deepcopy((sla, svc.emitted_events))
    with pytest.raises((DomainValidationError, RuntimeError)):
        svc.resume_sla("SLA-1", actor)
    assert (sla, svc.emitted_events) == before


def test_missing_or_drifted_schedule_has_no_heuristic_fallback():
    svc = service()
    sla = svc.create_sla("SLA-1", "tenant-a", schedule(), context())
    sla.due_at += timedelta(minutes=1)
    with pytest.raises(DomainValidationError):
        svc.update_sla_derived_state("SLA-1", context(schedule().due_at))
    sla.schedule = None
    with pytest.raises(DomainValidationError):
        svc.update_sla_derived_state("SLA-1", context(schedule().due_at))


def test_naive_evaluation_clock_is_refused():
    with pytest.raises(DomainValidationError):
        service().get_derived_sla_state(schedule(), START.replace(tzinfo=None))


def test_repeated_initialization_does_not_reset_history_or_replace_policy():
    svc = service()
    sla = svc.create_sla("SLA-1", "tenant-a", schedule(), context())
    svc.update_sla_derived_state("SLA-1", context(schedule().due_at))
    before = copy.deepcopy((sla, svc.emitted_events))
    assert svc.create_sla("SLA-1", "tenant-a", schedule(), context()) is sla
    assert (sla, svc.emitted_events) == before
    with pytest.raises(DomainValidationError):
        svc.create_sla(
            "SLA-1", "tenant-a", replace(schedule(), policy_version="fixture-new-policy"), context()
        )
    with pytest.raises(DomainValidationError):
        svc.create_sla(
            "SLA-1",
            "tenant-b",
            replace(schedule(), tenant_id="tenant-b"),
            context(tenant="tenant-b"),
        )
    assert (sla, svc.emitted_events) == before


@pytest.mark.parametrize("state", [SlaState.PAUSED, SlaState.BREACHED, SlaState.COMPLETED])
def test_inadmissible_pause_state_does_not_call_calendar(state):
    cal = FixtureCalendar()
    svc = service(cal)
    sla = svc.create_sla("SLA-1", "tenant-a", schedule(), context())
    sla.status = state
    before = copy.deepcopy((sla, svc.emitted_events))
    with pytest.raises(DomainValidationError):
        svc.pause_sla(
            "SLA-1",
            "fixture approved reason",
            START + timedelta(days=2),
            context(role=PrincipalRole.EXPANSION_MANAGER),
        )
    assert (sla, svc.emitted_events) == before
    assert cal.calls == []


def test_calendar_cannot_shorten_deadlines_when_excluding_pause():
    cal = FixtureCalendar()
    svc = service(cal)
    sla = svc.create_sla("SLA-1", "tenant-a", schedule(), context())
    svc.pause_sla(
        "SLA-1",
        "fixture approved reason",
        START + timedelta(days=2),
        context(START + timedelta(hours=1), PrincipalRole.EXPANSION_MANAGER),
    )
    cal.result = replace(schedule(), due_soon_at=schedule().due_soon_at - timedelta(minutes=1))
    before = copy.deepcopy((sla, svc.emitted_events))
    with pytest.raises(DomainValidationError):
        svc.resume_sla("SLA-1", context(START + timedelta(hours=3)))
    assert (sla, svc.emitted_events) == before


def test_multiple_pauses_preserve_intervals_and_accumulate_only_closed_time():
    svc = service(FixtureCalendar())
    sla = svc.create_sla("SLA-1", "tenant-a", schedule(), context())
    for pause_at, resume_at in [(1, 2), (3, 5)]:
        svc.pause_sla(
            "SLA-1",
            "fixture approved reason",
            START + timedelta(hours=10),
            context(START + timedelta(hours=pause_at), PrincipalRole.EXPANSION_MANAGER),
        )
        svc.resume_sla("SLA-1", context(START + timedelta(hours=resume_at)))
    assert sla.paused_duration_seconds == 3 * 3600
    assert len(sla.pause_intervals) == 2
    assert sla.due_at == schedule().due_at + timedelta(hours=3)


def test_timezone_normalization_preserves_real_elapsed_time_across_dst():
    from zoneinfo import ZoneInfo

    from modules.listing.domain.intake_states import SlaPauseInterval

    zone = ZoneInfo("America/New_York")
    start = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0)
    end = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1)
    interval = SlaPauseInterval(start, end + timedelta(hours=2), "fixture reason", end)
    assert (interval.ended_at - interval.started_at).total_seconds() == 3600
