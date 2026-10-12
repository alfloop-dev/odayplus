"""Read-only Intake policy/calendar resolution from authoritative registries.

No policies, accounts, hours, holidays or approval records are created here.
The parameter format is an internal, versioned adapter contract; governance
must supply reviewed records before this runtime can produce a workflow plan.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from modules.listing.application.intake_workflow_bootstrap import (
    IntakeWorkflowPlan,
    IntakeWorkflowUnavailable,
)
from modules.listing.domain.intake_states import SlaPauseInterval, SlaSchedule
from shared.auth import Role, Scope
from shared.governance import DecisionPolicy

WORKFLOW_POLICY_KIND = "intake_workflow"
CALENDAR_POLICY_KIND = "intake_business_calendar"
MAX_CALENDAR_DAYS = 3660  # Bound corrupt registry inputs, not a business deadline.


def _instant(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise IntakeWorkflowUnavailable("Timezone-aware registry clock is required")
    return value.astimezone(UTC)


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IntakeWorkflowUnavailable(f"Missing registry {name}")
    return value.strip()


def _minutes(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise IntakeWorkflowUnavailable(f"Invalid registry {name}")
    return value


@dataclass(frozen=True)
class RegistryBusinessCalendar:
    version_id: str
    tenant_id: str
    timezone: ZoneInfo
    first_date: date
    last_date: date
    weekly_windows: Mapping[int, tuple[tuple[int, int], ...]]
    date_overrides: Mapping[date, tuple[tuple[int, int], ...]]
    ambiguous_time: str

    @staticmethod
    def _minute_of_day(value: Any, *, end: bool) -> int:
        if not isinstance(value, str):
            raise ValueError("calendar time must be HH:MM")
        parts = value.split(":")
        if len(parts) != 2 or any(len(p) != 2 or not p.isdigit() for p in parts):
            raise ValueError("calendar time must be HH:MM")
        hour, minute = map(int, parts)
        if end and (hour, minute) == (24, 0):
            return 1440
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("calendar time outside day")
        return hour * 60 + minute

    @classmethod
    def _windows(cls, values: Any) -> tuple[tuple[int, int], ...]:
        if not isinstance(values, list):
            raise ValueError("calendar windows must be a list")
        result = []
        for value in values:
            if not isinstance(value, list) or len(value) != 2:
                raise ValueError("calendar window requires start/end")
            start, end = (
                cls._minute_of_day(value[0], end=False),
                cls._minute_of_day(value[1], end=True),
            )
            if start >= end:
                raise ValueError("calendar windows cannot be empty or overnight")
            result.append((start, end))
        result.sort()
        if any(a[1] > b[0] for a, b in zip(result, result[1:], strict=False)):
            raise ValueError("calendar windows overlap")
        return tuple(result)

    @classmethod
    def from_policy(cls, policy: DecisionPolicy) -> RegistryBusinessCalendar:
        try:
            p = policy.parameters
            if (
                type(p["schema_version"]) is not int
                or p["schema_version"] != 1
                or p["duration_basis"] != "UTC_ELAPSED"
            ):
                raise ValueError("unsupported calendar format/duration basis")
            first, last = (
                date.fromisoformat(p["coverage_from"]),
                date.fromisoformat(p["coverage_through"]),
            )
            if not 0 <= (last - first).days <= MAX_CALENDAR_DAYS:
                raise ValueError("invalid calendar coverage")
            weekly = p["weekly_windows"]
            if not isinstance(weekly, Mapping) or set(weekly) != {str(i) for i in range(1, 8)}:
                raise ValueError("all seven weekdays must be explicitly configured")
            overrides = p["date_overrides"]
            if not isinstance(overrides, Mapping):
                raise ValueError("calendar date overrides must be an object")
            parsed_overrides = {
                date.fromisoformat(k): cls._windows(v) for k, v in overrides.items()
            }
            if any(d < first or d > last for d in parsed_overrides):
                raise ValueError("calendar override outside coverage")
            fold = p["ambiguous_time"]
            if fold not in {"EARLIER", "LATER", "REJECT"}:
                raise ValueError("explicit DST ambiguity behavior required")
            if p["nonexistent_time"] != "REJECT":
                raise ValueError("nonexistent calendar boundaries must be rejected")
            return cls(
                policy.policy_version_id,
                policy.tenant_id,
                ZoneInfo(p["timezone"]),
                first,
                last,
                {int(k): cls._windows(v) for k, v in weekly.items()},
                parsed_overrides,
                fold,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise IntakeWorkflowUnavailable(
                "Registry business calendar is incomplete or invalid"
            ) from exc

    def _boundary(self, day: date, minute: int) -> datetime:
        wall = datetime.combine(day, time()) + timedelta(minutes=minute)
        candidates = set()
        for fold in (0, 1):
            candidate = wall.replace(tzinfo=self.timezone, fold=fold).astimezone(UTC)
            if candidate.astimezone(self.timezone).replace(tzinfo=None) == wall:
                candidates.add(candidate)
        if not candidates:
            raise IntakeWorkflowUnavailable("Nonexistent business-calendar boundary")
        if len(candidates) > 1 and self.ambiguous_time == "REJECT":
            raise IntakeWorkflowUnavailable("Ambiguous business-calendar boundary")
        return max(candidates) if self.ambiguous_time == "LATER" else min(candidates)

    def _date(self, instant: datetime, *, allow_end: bool = False) -> date:
        local = _instant(instant).astimezone(self.timezone)
        day = local.date()
        if (
            allow_end
            and day == self.last_date + timedelta(days=1)
            and local.timetz().replace(tzinfo=None) == time()
        ):
            return self.last_date
        if not self.first_date <= day <= self.last_date:
            raise IntakeWorkflowUnavailable("Registry calendar coverage is unavailable")
        return day

    def _day_windows(self, day: date):
        values = self.date_overrides.get(day, self.weekly_windows[day.isoweekday()])
        for start, end in values:
            left, right = self._boundary(day, start), self._boundary(day, end)
            if left >= right:
                raise IntakeWorkflowUnavailable("Invalid resolved calendar interval")
            yield left, right

    def add_working_duration(self, instant: datetime, duration: timedelta) -> datetime:
        cursor = _instant(instant)
        day = self._date(cursor)
        if duration < timedelta():
            raise IntakeWorkflowUnavailable("Working duration cannot be negative")
        if not duration:
            return cursor
        remaining = duration
        while day <= self.last_date:
            for start, end in self._day_windows(day):
                begin = max(cursor, start)
                if begin >= end:
                    continue
                available = end - begin
                if remaining <= available:
                    return begin + remaining
                remaining -= available
            if day == self.last_date:
                break
            day += timedelta(days=1)
        raise IntakeWorkflowUnavailable("Working duration exceeds approved calendar coverage")

    def working_duration_between(self, started_at: datetime, ended_at: datetime) -> timedelta:
        start, end = _instant(started_at), _instant(ended_at)
        if end < start:
            raise IntakeWorkflowUnavailable("Calendar interval ends before it starts")
        day, last = self._date(start), self._date(end, allow_end=True)
        total = timedelta()
        while day <= last:
            for left, right in self._day_windows(day):
                a, b = max(left, start), min(right, end)
                if b > a:
                    total += b - a
            if day == last:
                break
            day += timedelta(days=1)
        return total


class RegistryIntakeWorkflowPolicy:
    """Resolve immutable registry versions and verify live identity/scope grants."""

    def __init__(self, policy_repository: Any, identity_store: Any) -> None:
        self._policies = policy_repository
        self._identities = identity_store

    @staticmethod
    def _scope_allows(grant: Scope, requested: Mapping[str, Any]) -> bool:
        return (
            grant.tenant_id == requested.get("tenant_id")
            and grant.permits_module("listing")
            and all(
                (
                    grant.permits_brand(requested.get("brand_id")),
                    grant.permits_region(requested.get("region_id")),
                    grant.permits_store(requested.get("store_id")),
                    grant.permits_assigned_area(requested.get("assigned_area_id")),
                    grant.permits_heat_zone(requested.get("heat_zone_id")),
                )
            )
        )

    def _account(self, account_id: str, scope: Mapping[str, Any]):
        try:
            account = self._identities.find_account_by_id(UUID(account_id))
            if (
                account is None
                or not account.is_active
                or str(account.tenant_id) != scope["tenant_id"]
            ):
                raise PermissionError("Account is not active in the workflow tenant")
            grant = self._identities.get_account_scope(account.account_id)
            if not self._scope_allows(grant, scope):
                raise PermissionError("Account is outside the workflow scope")
            return account, self._identities.get_account_roles(account.account_id)
        except (TypeError, ValueError) as exc:
            raise PermissionError("Workflow account reference is invalid") from exc

    def _check_policy(
        self, policy: DecisionPolicy | None, *, kind: str, scope: Mapping[str, Any], at: datetime
    ) -> DecisionPolicy:
        if policy is None or policy.policy_kind != kind or policy.tenant_id != scope["tenant_id"]:
            raise IntakeWorkflowUnavailable("Authoritative registry policy is unavailable")
        try:
            _instant(policy.effective_from)
            if policy.effective_to is not None:
                _instant(policy.effective_to)
            covers = policy.covers(_instant(at))
        except (TypeError, ValueError) as exc:
            raise IntakeWorkflowUnavailable("Registry effective window is invalid") from exc
        if not covers:
            raise IntakeWorkflowUnavailable(
                "Registry version does not cover the governing timestamp"
            )
        if policy.approved_at is None or _instant(policy.approved_at) > _instant(at):
            raise IntakeWorkflowUnavailable(
                "Registry approval does not cover the governing timestamp"
            )
        try:
            _account, roles = self._account(policy.approved_by, scope)
            if Role(policy.owner_role) not in roles:
                raise PermissionError("Policy owner role is not held by its approval account")
        except (PermissionError, ValueError) as exc:
            raise IntakeWorkflowUnavailable(
                "Registry approval identity/scope is not valid"
            ) from exc
        return policy

    def _calendar(
        self, policy: DecisionPolicy, scope: Mapping[str, Any], at: datetime
    ) -> RegistryBusinessCalendar:
        ref = _required_text(
            policy.parameters.get("calendar_version_id"), "calendar version reference"
        )
        row = self._check_policy(
            self._policies.find_version(ref), kind=CALENDAR_POLICY_KIND, scope=scope, at=at
        )
        return RegistryBusinessCalendar.from_policy(row)

    @staticmethod
    def _thresholds(policy: DecisionPolicy) -> tuple[int, int, int]:
        p = policy.parameters
        if type(p.get("schema_version")) is not int or p.get("schema_version") != 1:
            raise IntakeWorkflowUnavailable("Unsupported Intake policy parameter format")
        warn = _minutes(p.get("warning_work_minutes"), "warning threshold")
        due = _minutes(p.get("due_work_minutes"), "due threshold")
        breach = _minutes(p.get("breach_work_minutes"), "breach threshold")
        if not 0 <= warn <= due <= breach or due == 0:
            raise IntakeWorkflowUnavailable("Invalid ordered SLA policy thresholds")
        return warn, due, breach

    def resolve_initial(
        self, scope: Mapping[str, Any], submitted_at: datetime
    ) -> IntakeWorkflowPlan:
        at = _instant(submitted_at)
        policy = self._check_policy(
            self._policies.find_effective(
                policy_kind=WORKFLOW_POLICY_KIND, tenant_id=scope["tenant_id"], at=at
            ),
            kind=WORKFLOW_POLICY_KIND,
            scope=scope,
            at=at,
        )
        if not all(policy.reads(x) for x in ("scope", "submitted_at")):
            raise IntakeWorkflowUnavailable("Intake policy does not declare its runtime inputs")
        calendar = self._calendar(policy, scope, at)
        warn, due, breach = self._thresholds(policy)
        queue = _required_text(policy.parameters.get("queue_key"), "routing queue")
        return IntakeWorkflowPlan(
            queue,
            SlaSchedule(
                tenant_id=scope["tenant_id"],
                policy_version=policy.policy_version_id,
                calendar_version=calendar.version_id,
                started_at=at,
                due_soon_at=calendar.add_working_duration(at, timedelta(minutes=warn)),
                due_at=calendar.add_working_duration(at, timedelta(minutes=due)),
                breach_at=calendar.add_working_duration(at, timedelta(minutes=breach)),
            ),
        )

    def _bound(self, schedule: SlaSchedule, scope: Mapping[str, Any]):
        if schedule.tenant_id != scope.get("tenant_id"):
            raise IntakeWorkflowUnavailable("SLA and authorized scope tenant differ")
        policy = self._check_policy(
            self._policies.find_version(schedule.policy_version),
            kind=WORKFLOW_POLICY_KIND,
            scope=scope,
            at=schedule.started_at,
        )
        calendar = self._calendar(policy, scope, schedule.started_at)
        if calendar.version_id != schedule.calendar_version:
            raise IntakeWorkflowUnavailable("SLA calendar version binding changed")
        return policy, calendar

    def resolve_assignment(
        self,
        scope: Mapping[str, Any],
        schedule: SlaSchedule,
        *,
        actor_id: str,
        owner_subject_id: str,
        owner_role: str,
        requested_due_at: datetime,
        reason: str,
    ) -> IntakeWorkflowPlan:
        policy, calendar = self._bound(schedule, scope)
        if not all(
            policy.reads(x) for x in ("actor_account", "target_account", "requested_due_at")
        ):
            raise IntakeWorkflowUnavailable("Assignment policy does not declare its runtime inputs")
        _actor, actor_roles = self._account(actor_id, scope)
        _owner, owner_roles = self._account(owner_subject_id, scope)
        managers = {Role.SITE_REVIEWER, Role.DATA_OWNER, Role.EXECUTIVE}
        if not actor_roles & managers and not (
            actor_id == owner_subject_id and Role.EXPANSION_USER in actor_roles
        ):
            raise PermissionError("Actor cannot assign this workflow")
        bindings = policy.parameters.get("assignee_role_bindings")
        try:
            if not isinstance(bindings, Mapping) or Role(bindings[owner_role]) not in owner_roles:
                raise PermissionError("Target does not hold the policy-bound assignment role")
        except (KeyError, ValueError) as exc:
            raise PermissionError("Assignment role is not authorized by policy") from exc
        if not isinstance(reason, str) or not reason.strip():
            raise PermissionError("Assignment justification is required")
        requested = _instant(requested_due_at)
        override = policy.parameters.get("deadline_override")
        if not isinstance(override, Mapping):
            raise IntakeWorkflowUnavailable("Explicit deadline-override policy is required")
        result = schedule
        if override.get("mode") == "BOUNDED_WORK_MINUTES":
            roles = {Role(x) for x in override.get("actor_roles", [])}
            if not actor_roles & roles:
                raise PermissionError("Actor cannot override SLA deadline")
            duration = calendar.working_duration_between(schedule.started_at, requested)
            minimum, maximum = (
                _minutes(override.get("minimum_minutes"), "override minimum"),
                _minutes(override.get("maximum_minutes"), "override maximum"),
            )
            if minimum > maximum or not timedelta(minutes=minimum) <= duration <= timedelta(
                minutes=maximum
            ):
                raise PermissionError("Proposed deadline is outside the approved policy bounds")
            resolved_due = calendar.add_working_duration(schedule.started_at, duration)
            if resolved_due != requested:
                raise PermissionError(
                    "Proposed deadline is not an authoritative calendar checkpoint"
                )
            warn, due, breach = self._thresholds(policy)
            result = replace(
                schedule,
                due_soon_at=calendar.add_working_duration(
                    schedule.started_at, max(timedelta(), duration - timedelta(minutes=due - warn))
                ),
                due_at=resolved_due,
                breach_at=calendar.add_working_duration(
                    resolved_due, timedelta(minutes=breach - due)
                ),
            )
        elif override.get("mode") != "KEEP_POLICY":
            raise IntakeWorkflowUnavailable("Unsupported deadline-override policy")
        return IntakeWorkflowPlan(
            _required_text(policy.parameters.get("queue_key"), "routing queue"), result
        )

    def permits_pause(
        self, schedule: SlaSchedule, reason: str, expected_resume_at: datetime
    ) -> bool:
        return self.calendar_for({"tenant_id": schedule.tenant_id}).permits_pause(
            schedule, reason, expected_resume_at
        )

    def calendar_for(self, scope: Mapping[str, Any]) -> ScopedRegistryCalendar:
        return ScopedRegistryCalendar(self, deepcopy(dict(scope)))

    def _permits_pause(
        self,
        scope: Mapping[str, Any],
        schedule: SlaSchedule,
        reason: str,
        expected_resume_at: datetime,
    ) -> bool:
        policy, calendar = self._bound(schedule, scope)
        reasons = policy.parameters.get("approved_pause_reasons")
        if not isinstance(reasons, list) or any(
            not isinstance(x, str) or not x.strip() for x in reasons
        ):
            raise IntakeWorkflowUnavailable("Approved pause reasons are unavailable")
        calendar._date(_instant(expected_resume_at))
        return reason.strip() in reasons

    def pause_approval_account(
        self, schedule: SlaSchedule, scope: Mapping[str, Any], actor_id: str
    ) -> str:
        self.authorize_sla_actor(schedule, scope, actor_id)
        policy, _calendar = self._bound(schedule, scope)
        if policy.approved_by == actor_id:
            raise PermissionError("Pause creator must differ from the policy approval account")
        self._account(actor_id, scope)
        return policy.approved_by

    def authorize_sla_actor(
        self, schedule: SlaSchedule, scope: Mapping[str, Any], actor_id: str
    ) -> None:
        self._bound(schedule, scope)
        _account, roles = self._account(actor_id, scope)
        if not roles & {Role.SITE_REVIEWER, Role.DATA_OWNER, Role.EXECUTIVE}:
            raise PermissionError("Current account cannot operate the SLA")

    def resume_schedule(
        self, schedule: SlaSchedule, interval: SlaPauseInterval, resumed_at: datetime
    ) -> SlaSchedule:
        return self.calendar_for({"tenant_id": schedule.tenant_id}).resume_schedule(
            schedule, interval, resumed_at
        )

    def _resume_schedule(
        self,
        scope: Mapping[str, Any],
        schedule: SlaSchedule,
        interval: SlaPauseInterval,
        resumed_at: datetime,
    ) -> SlaSchedule:
        _policy, calendar = self._bound(schedule, scope)
        if interval.started_at < schedule.started_at:
            raise IntakeWorkflowUnavailable("Pause predates the SLA")
        excluded = calendar.working_duration_between(interval.started_at, resumed_at)
        if not excluded:
            return schedule
        return replace(
            schedule,
            **{
                name: calendar.add_working_duration(getattr(schedule, name), excluded)
                for name in ("due_soon_at", "due_at", "breach_at")
            },
        )


@dataclass(frozen=True)
class ScopedRegistryCalendar:
    provider: RegistryIntakeWorkflowPolicy
    scope: Mapping[str, Any]

    def permits_pause(
        self, schedule: SlaSchedule, reason: str, expected_resume_at: datetime
    ) -> bool:
        return self.provider._permits_pause(self.scope, schedule, reason, expected_resume_at)

    def resume_schedule(
        self, schedule: SlaSchedule, interval: SlaPauseInterval, resumed_at: datetime
    ) -> SlaSchedule:
        return self.provider._resume_schedule(self.scope, schedule, interval, resumed_at)
