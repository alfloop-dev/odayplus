from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from modules.listing.application.intake_registry_calendar import (
    RegistryBusinessCalendar,
)
from modules.listing.application.intake_workflow_bootstrap import IntakeWorkflowUnavailable
from modules.listing.domain.intake_states import SlaPauseInterval
from shared.auth import Role, Scope
from tests.intake_registry_fixture import (
    APPROVER,
    AT,
    CALENDAR_ID,
    MANAGER,
    OWNER,
    POLICY_ID,
    TENANT,
    _calendar_row,
    _provider,
    _workflow_row,
)


def test_weekend_holiday_lunch_and_timezone_produce_registry_checkpoints():
    provider, _, _ = _provider()
    plan = provider.resolve_initial({"tenant_id": TENANT}, AT)
    assert plan.queue_key == "synthetic-queue"
    assert (
        plan.schedule.policy_version == POLICY_ID and plan.schedule.calendar_version == CALENDAR_ID
    )
    assert plan.schedule.started_at == AT
    assert plan.schedule.due_soon_at == datetime(2026, 10, 13, 1, 30, tzinfo=UTC)
    assert plan.schedule.due_at == datetime(2026, 10, 13, 2, 30, tzinfo=UTC)
    assert plan.schedule.breach_at == datetime(2026, 10, 13, 3, 30, tzinfo=UTC)
    calendar = RegistryBusinessCalendar.from_policy(_calendar_row())
    assert calendar.working_duration_between(AT, plan.schedule.due_at) == timedelta(minutes=120)


@pytest.mark.parametrize("binding", [
    {" another-role ": "expansion_user"}, {"another-role": "unknown_role"},
    {"": "expansion_user"}, {"another-role": None},
    {" expansion-staff ": "expansion_user"},
])
def test_transfer_directory_rejects_malformed_additional_policy_binding(binding):
    policy = _workflow_row()
    params = deepcopy(policy.parameters)
    params["assignee_role_bindings"].update(binding)
    provider, _, _ = _provider(policy=replace(policy, parameters=params))
    scope = {"tenant_id": TENANT}
    schedule = provider.resolve_initial(scope, AT).schedule
    with pytest.raises(IntakeWorkflowUnavailable):
        provider.list_assignment_transfer_targets(
            scope, schedule, actor_id=MANAGER,
            current_owner_subject_id=OWNER, current_owner_role="expansion-staff",
        )


def test_transfer_directory_preserves_distinct_authorized_roles_for_same_account():
    policy = _workflow_row()
    params = deepcopy(policy.parameters)
    params["assignee_role_bindings"].update({
        "manager-recipient": "site_reviewer", "alternate-manager": "site_reviewer",
    })
    provider, _, _ = _provider(policy=replace(policy, parameters=params))
    scope = {"tenant_id": TENANT}
    schedule = provider.resolve_initial(scope, AT).schedule
    items = provider.list_assignment_transfer_targets(
        scope, schedule, actor_id=MANAGER,
        current_owner_subject_id=OWNER, current_owner_role="expansion-staff",
    )
    assert {(row["subject_id"], row["owner_role"]) for row in items} == {
        (MANAGER, "manager-recipient"), (MANAGER, "alternate-manager"),
    }


@pytest.mark.parametrize("account_id", [APPROVER, MANAGER, OWNER])
def test_assignment_registry_requires_explicit_scope_for_each_authority(account_id):
    provider, identities, _ = _provider()
    scope = {"tenant_id": TENANT}
    schedule = provider.resolve_initial(scope, AT).schedule
    identities._scopes.pop(UUID(account_id))
    error = IntakeWorkflowUnavailable if account_id == APPROVER else PermissionError
    with pytest.raises(error):
        provider.resolve_assignment(
            scope, schedule, actor_id=MANAGER, owner_subject_id=OWNER,
            owner_role="expansion-staff", requested_due_at=schedule.due_at,
            reason="Synthetic assignment must retain an explicit scope grant",
        )


def test_lunch_break_and_microseconds_are_not_counted_as_work():
    calendar = RegistryBusinessCalendar.from_policy(_calendar_row())
    start = datetime(2026, 10, 9, 3, 59, 30, 123456, tzinfo=UTC)
    result = calendar.add_working_duration(start, timedelta(minutes=1))
    assert result == datetime(2026, 10, 9, 5, 0, 30, 123456, tzinfo=UTC)
    assert calendar.working_duration_between(start, result) == timedelta(minutes=1)


@pytest.mark.parametrize(
    "change",
    [
        {"timezone": "Not/AZone"},
        {"schema_version": True},
        {"duration_basis": "WALL_GUESS"},
        {"coverage_from": "2027-01-01"},
        {"coverage_through": "2040-01-01"},
        {"ambiguous_time": "GUESS"},
        {"nonexistent_time": "SHIFT"},
        {"weekly_windows": {"1": [["09:00", "17:00"]]}},
        {"date_overrides": {"2027-01-01": []}},
    ],
)
def test_invalid_calendar_records_fail_closed(change):
    row = _calendar_row()
    with pytest.raises(IntakeWorkflowUnavailable):
        RegistryBusinessCalendar.from_policy(replace(row, parameters={**row.parameters, **change}))


@pytest.mark.parametrize(
    "windows",
    [
        [["09:00", "09:00"]],
        [["17:00", "09:00"]],
        [["09:00", "13:00"], ["12:00", "17:00"]],
        [["9:00", "12:00"]],
        [["00:00", "24:01"]],
    ],
)
def test_invalid_day_windows_are_rejected(windows):
    row = _calendar_row()
    p = deepcopy(row.parameters)
    p["weekly_windows"]["1"] = windows
    with pytest.raises(IntakeWorkflowUnavailable):
        RegistryBusinessCalendar.from_policy(replace(row, parameters=p))


def test_calendar_exhaustion_does_not_guess_a_deadline():
    calendar = RegistryBusinessCalendar.from_policy(_calendar_row())
    with pytest.raises(IntakeWorkflowUnavailable):
        calendar.add_working_duration(datetime(2027, 1, 1, tzinfo=UTC), timedelta(minutes=1))
    with pytest.raises(IntakeWorkflowUnavailable):
        calendar.add_working_duration(AT, timedelta(days=10000))


def test_explicit_open_weekend_override_is_honored():
    row = _calendar_row()
    p = deepcopy(row.parameters)
    p["date_overrides"]["2026-10-10"] = [["10:00", "11:00"]]
    c = RegistryBusinessCalendar.from_policy(replace(row, parameters=p))
    assert c.add_working_duration(AT, timedelta(minutes=90)) == datetime(
        2026, 10, 10, 3, tzinfo=UTC
    )


@pytest.mark.parametrize("mode,hours", [("EARLIER", 2), ("LATER", 1)])
def test_explicit_dst_fold_selects_the_approved_interval(mode, hours):
    row = _calendar_row()
    p = deepcopy(row.parameters)
    p.update(timezone="America/New_York", ambiguous_time=mode)
    p["date_overrides"]["2026-11-01"] = [["01:00", "02:00"]]
    c = RegistryBusinessCalendar.from_policy(replace(row, parameters=p))
    assert c.working_duration_between(
        datetime(2026, 11, 1, 4, tzinfo=UTC), datetime(2026, 11, 1, 8, tzinfo=UTC)
    ) == timedelta(hours=hours)


@pytest.mark.parametrize(
    "day,windows", [("2026-03-08", [["02:00", "03:00"]]), ("2026-11-01", [["01:00", "02:00"]])]
)
def test_dst_gap_or_unapproved_fold_fails_closed(day, windows):
    row = _calendar_row()
    p = deepcopy(row.parameters)
    p["timezone"] = "America/New_York"
    p["date_overrides"][day] = windows
    c = RegistryBusinessCalendar.from_policy(replace(row, parameters=p))
    start = datetime.fromisoformat(day + "T04:00:00+00:00")
    with pytest.raises(IntakeWorkflowUnavailable):
        c.working_duration_between(start, start + timedelta(hours=6))


@pytest.mark.parametrize(
    "change",
    [
        {"approved_by": ""},
        {"approved_by": "architecture_owner"},
        {"approved_at": None},
        {"approved_at": AT + timedelta(seconds=1)},
        {"owner_role": "executive"},
        {"policy_kind": "forecast_alert"},
        {"effective_to": AT},
    ],
)
def test_missing_expired_or_unqualified_policy_approval_is_not_accepted(change):
    provider, _, _ = _provider(policy=replace(_workflow_row(), **change))
    with pytest.raises(IntakeWorkflowUnavailable):
        provider.resolve_initial({"tenant_id": TENANT}, AT)


@pytest.mark.parametrize("account_id", [APPROVER, MANAGER, OWNER])
def test_disabled_identity_is_rechecked_for_policy_or_assignment(account_id):
    provider, identities, _ = _provider()
    plan = provider.resolve_initial({"tenant_id": TENANT}, AT)
    account = identities.find_account_by_id(account_id)
    identities.save_account(replace(account, status="disabled"))
    expected = IntakeWorkflowUnavailable if account_id == APPROVER else PermissionError
    with pytest.raises(expected):
        provider.resolve_assignment(
            {"tenant_id": TENANT},
            plan.schedule,
            actor_id=MANAGER,
            owner_subject_id=OWNER,
            owner_role="expansion-staff",
            requested_due_at=plan.schedule.due_at,
            reason="Synthetic assignment",
        )


def test_target_scope_role_and_tenant_are_enforced():
    provider, identities, _ = _provider()
    scope = {"tenant_id": TENANT}
    plan = provider.resolve_initial(scope, AT)
    identities.set_account_scope(OWNER, Scope(tenant_id=TENANT, brand_ids=frozenset({"brand-one"})))
    with pytest.raises(PermissionError):
        provider.resolve_assignment(
            scope,
            plan.schedule,
            actor_id=MANAGER,
            owner_subject_id=OWNER,
            owner_role="expansion-staff",
            requested_due_at=plan.schedule.due_at,
            reason="Synthetic assignment",
        )
    identities.set_account_scope(OWNER, Scope(tenant_id=TENANT))
    identities.set_account_roles(OWNER, {Role.AUDITOR})
    with pytest.raises(PermissionError):
        provider.resolve_assignment(
            scope,
            plan.schedule,
            actor_id=MANAGER,
            owner_subject_id=OWNER,
            owner_role="expansion-staff",
            requested_due_at=plan.schedule.due_at,
            reason="Synthetic assignment",
        )


def test_unchecked_client_deadline_cannot_override_keep_policy():
    provider, _, _ = _provider()
    scope = {"tenant_id": TENANT}
    plan = provider.resolve_initial(scope, AT)
    assigned = provider.resolve_assignment(
        scope,
        plan.schedule,
        actor_id=MANAGER,
        owner_subject_id=OWNER,
        owner_role="expansion-staff",
        requested_due_at=datetime(2099, 1, 1, tzinfo=UTC),
        reason="Synthetic proposal",
    )
    assert assigned.schedule == plan.schedule


def test_authorized_bounded_deadline_uses_working_minutes_and_preserves_versions():
    row = _workflow_row()
    p = deepcopy(row.parameters)
    p["deadline_override"] = {
        "mode": "BOUNDED_WORK_MINUTES",
        "minimum_minutes": 60,
        "maximum_minutes": 240,
        "actor_roles": ["site_reviewer"],
    }
    provider, _, _ = _provider(policy=replace(row, parameters=p))
    scope = {"tenant_id": TENANT}
    plan = provider.resolve_initial(scope, AT)
    deadline = datetime(2026, 10, 13, 3, 30, tzinfo=UTC)
    assigned = provider.resolve_assignment(
        scope,
        plan.schedule,
        actor_id=MANAGER,
        owner_subject_id=OWNER,
        owner_role="expansion-staff",
        requested_due_at=deadline,
        reason="Synthetic approved override",
    )
    assert assigned.schedule.due_at == deadline
    assert assigned.schedule.due_soon_at == datetime(2026, 10, 13, 2, 30, tzinfo=UTC)
    assert assigned.schedule.breach_at == datetime(2026, 10, 13, 5, 30, tzinfo=UTC)
    assert assigned.schedule.policy_version == plan.schedule.policy_version


def test_pause_excludes_business_time_and_preserves_explicit_registry_versions():
    provider, _, _ = _provider()
    scope = {"tenant_id": TENANT}
    plan = provider.resolve_initial(scope, AT)
    calendar = provider.calendar_for(scope)
    resumed = datetime(2026, 10, 13, 2, tzinfo=UTC)
    interval = SlaPauseInterval(AT, resumed, "Synthetic approved hold")
    assert calendar.permits_pause(plan.schedule, interval.reason, resumed)
    updated = calendar.resume_schedule(plan.schedule, interval, resumed)
    assert updated.due_at == datetime(2026, 10, 13, 4, tzinfo=UTC)
    assert updated.started_at == plan.schedule.started_at
    assert updated.policy_version == POLICY_ID and updated.calendar_version == CALENDAR_ID
    with pytest.raises(PermissionError):
        provider.pause_approval_account(plan.schedule, scope, APPROVER)
    assert provider.pause_approval_account(plan.schedule, scope, MANAGER) == APPROVER
