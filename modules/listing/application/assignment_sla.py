from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Protocol

from modules.listing.domain.intake_states import (
    AssignmentAggregate,
    AssignmentState,
    AssignmentStateMachine,
    DenialCode,
    DomainValidationError,
    PrincipalRole,
    SlaInstanceAggregate,
    SlaPauseInterval,
    SlaSchedule,
    SlaState,
    SlaStateMachine,
    TransitionContext,
)


class AssignmentRepository(Protocol):
    def get_assignment(self, assignment_id: str) -> AssignmentAggregate | None:
        ...

    def save_assignment(self, assignment: AssignmentAggregate) -> None:
        ...


class SlaRepository(Protocol):
    def get_sla(self, sla_id: str) -> SlaInstanceAggregate | None:
        ...

    def save_sla(self, sla: SlaInstanceAggregate) -> None:
        ...


class SlaCalendar(Protocol):
    """Server-owned policy/calendar adapter; never a frontend deadline fallback."""

    def permits_pause(
        self, schedule: SlaSchedule, reason: str, expected_resume_at: datetime
    ) -> bool: ...

    def resume_schedule(
        self, schedule: SlaSchedule, interval: SlaPauseInterval, resumed_at: datetime
    ) -> SlaSchedule: ...


class InMemoryAssignmentRepository:
    def __init__(self) -> None:
        self._store: dict[str, AssignmentAggregate] = {}

    def get_assignment(self, assignment_id: str) -> AssignmentAggregate | None:
        return self._store.get(assignment_id)

    def save_assignment(self, assignment: AssignmentAggregate) -> None:
        self._store[assignment.id] = assignment


class InMemorySlaRepository:
    def __init__(self) -> None:
        self._store: dict[str, SlaInstanceAggregate] = {}

    def get_sla(self, sla_id: str) -> SlaInstanceAggregate | None:
        return self._store.get(sla_id)

    def save_sla(self, sla: SlaInstanceAggregate) -> None:
        self._store[sla.id] = sla


class AssignmentSlaService:
    """Manages Task Assignments and SLAs, including state derivation and pausing."""

    def __init__(self, assignment_repo: AssignmentRepository, sla_repo: SlaRepository,
                 *, calendar: SlaCalendar | None = None) -> None:
        self.assignment_repo = assignment_repo
        self.sla_repo = sla_repo
        self.calendar = calendar
        self.emitted_events: list[dict[str, Any]] = []

    def _record_event(self, event_type: str, entity_id: str, tenant_id: str, correlation_id: str | None, payload: dict[str, Any]) -> None:
        self.emitted_events.append({
            "event_type": event_type,
            "entity_id": entity_id,
            "tenant_id": tenant_id,
            "correlation_id": correlation_id,
            "payload": payload,
        })

    def create_assignment(self, assignment_id: str, tenant_id: str, context: TransitionContext) -> AssignmentAggregate:
        AssignmentStateMachine.transition(None, AssignmentState.UNASSIGNED, context)

        assignment = AssignmentAggregate(
            id=assignment_id,
            tenant_id=tenant_id,
            status=AssignmentState.UNASSIGNED,
            version=1,
        )
        self.assignment_repo.save_assignment(assignment)

        self._record_event(
            "assignment.created.v1",
            assignment_id,
            tenant_id,
            context.correlation_id,
            {"queue_reason": "intake_created"}
        )
        return assignment

    def assign_task(self, assignment_id: str, assignee_id: str, due_at: datetime, context: TransitionContext) -> AssignmentAggregate:
        assignment = self.assignment_repo.get_assignment(assignment_id)
        if not assignment:
            raise ValueError(f"Assignment {assignment_id} not found")

        AssignmentStateMachine.transition(assignment, AssignmentState.ASSIGNED, context)
        assignment.assignee_id = assignee_id
        assignment.due_at = due_at
        self.assignment_repo.save_assignment(assignment)

        self._record_event(
            "assignment.assigned.v1",
            assignment_id,
            assignment.tenant_id,
            context.correlation_id,
            {"assignee_id": assignee_id, "due_at": due_at.isoformat()}
        )
        return assignment

    def claim_task(self, assignment_id: str, context: TransitionContext) -> AssignmentAggregate:
        assignment = self.assignment_repo.get_assignment(assignment_id)
        if not assignment:
            raise ValueError(f"Assignment {assignment_id} not found")

        AssignmentStateMachine.transition(assignment, AssignmentState.CLAIMED, context)
        assignment.owner_id = context.actor.actor_id
        self.assignment_repo.save_assignment(assignment)

        self._record_event(
            "assignment.claimed.v1",
            assignment_id,
            assignment.tenant_id,
            context.correlation_id,
            {"owner_id": context.actor.actor_id}
        )
        return assignment

    def transfer_task(self, assignment_id: str, target_assignee_id: str, context: TransitionContext) -> AssignmentAggregate:
        assignment = self.assignment_repo.get_assignment(assignment_id)
        if not assignment:
            raise ValueError(f"Assignment {assignment_id} not found")

        AssignmentStateMachine.transition(assignment, AssignmentState.TRANSFERRED, context)
        old_assignee = assignment.assignee_id
        assignment.assignee_id = target_assignee_id
        self.assignment_repo.save_assignment(assignment)

        self._record_event(
            "assignment.transferred.v1",
            assignment_id,
            assignment.tenant_id,
            context.correlation_id,
            {"from_assignee_id": old_assignee, "to_assignee_id": target_assignee_id}
        )
        return assignment

    def accept_transfer(self, assignment_id: str, context: TransitionContext) -> AssignmentAggregate:
        assignment = self.assignment_repo.get_assignment(assignment_id)
        if not assignment:
            raise ValueError(f"Assignment {assignment_id} not found")

        AssignmentStateMachine.transition(assignment, AssignmentState.ASSIGNED, context)
        self.assignment_repo.save_assignment(assignment)

        self._record_event(
            "assignment.assigned.v1",
            assignment_id,
            assignment.tenant_id,
            context.correlation_id,
            {"assignee_id": assignment.assignee_id, "transfer_accepted": True}
        )
        return assignment

    def escalate_task(self, assignment_id: str, context: TransitionContext) -> AssignmentAggregate:
        assignment = self.assignment_repo.get_assignment(assignment_id)
        if not assignment:
            raise ValueError(f"Assignment {assignment_id} not found")

        AssignmentStateMachine.transition(assignment, AssignmentState.ESCALATED, context)
        self.assignment_repo.save_assignment(assignment)

        self._record_event(
            "assignment.escalated.v1",
            assignment_id,
            assignment.tenant_id,
            context.correlation_id,
            {"due_at": assignment.due_at.isoformat() if assignment.due_at else None}
        )
        return assignment

    def complete_task(self, assignment_id: str, context: TransitionContext) -> AssignmentAggregate:
        assignment = self.assignment_repo.get_assignment(assignment_id)
        if not assignment:
            raise ValueError(f"Assignment {assignment_id} not found")

        AssignmentStateMachine.transition(assignment, AssignmentState.COMPLETED, context)
        self.assignment_repo.save_assignment(assignment)

        self._record_event(
            "assignment.completed.v1",
            assignment_id,
            assignment.tenant_id,
            context.correlation_id,
            {"owner_id": assignment.owner_id}
        )
        return assignment

    # SLA Tracking Functions


    def create_sla(
        self, sla_id: str, tenant_id: str, schedule: SlaSchedule, context: TransitionContext
    ) -> SlaInstanceAggregate:
        if context.actor.tenant_id != tenant_id:
            raise DomainValidationError(DenialCode.TENANT_SCOPE_DENIED, "SLA tenant mismatch")
        if not isinstance(schedule, SlaSchedule):
            raise DomainValidationError(
                DenialCode.WORKFLOW_STATE_DENIED, "Resolved SLA schedule is required"
            )
        if schedule.tenant_id != tenant_id:
            raise DomainValidationError(DenialCode.TENANT_SCOPE_DENIED, "SLA schedule tenant mismatch")
        SlaStateMachine.transition(None, SlaState.ON_TRACK, context)

        existing = self.sla_repo.get_sla(sla_id)
        if existing is not None:
            self._validate_sla_context(existing, context)
            if existing.schedule != schedule:
                raise DomainValidationError(
                    DenialCode.WORKFLOW_STATE_DENIED,
                    "Existing SLA schedule cannot be replaced by initialization",
                )
            return existing

        sla = SlaInstanceAggregate(
            id=sla_id,
            tenant_id=tenant_id,
            status=SlaState.ON_TRACK,
            version=1,
            due_at=schedule.due_at,
            schedule=schedule,
        )
        self.sla_repo.save_sla(sla)

        self._record_event(
            "sla.state_changed.v1",
            sla_id,
            tenant_id,
            context.correlation_id,
            {
                "status": SlaState.ON_TRACK.value,
                "version": 1,
                "policy_version": schedule.policy_version,
                "calendar_version": schedule.calendar_version,
                "due_soon_at": schedule.due_soon_at.isoformat(),
                "due_at": schedule.due_at.isoformat(),
                "breach_at": schedule.breach_at.isoformat(),
            },
        )
        return sla


    @classmethod
    def get_derived_sla_state(cls, schedule: SlaSchedule, current_time: datetime) -> SlaState:
        """Evaluate authoritative checkpoints, including their exact boundaries."""
        if not isinstance(schedule, SlaSchedule):
            raise DomainValidationError(
                DenialCode.WORKFLOW_STATE_DENIED, "Resolved SLA schedule is required"
            )
        current_time = cls._aware_time(current_time)
        if current_time >= schedule.breach_at:
            return SlaState.BREACHED
        elif current_time >= schedule.due_at:
            return SlaState.OVERDUE
        elif current_time >= schedule.due_soon_at:
            return SlaState.DUE_SOON
        else:
            return SlaState.ON_TRACK


    @staticmethod
    def _aware_time(value: datetime) -> datetime:
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise DomainValidationError(
                DenialCode.WORKFLOW_STATE_DENIED, "SLA clock must be timezone-aware"
            )
        return value.astimezone(UTC)


    @staticmethod
    def _validate_sla_context(sla: SlaInstanceAggregate, context: TransitionContext) -> None:
        if sla.tenant_id != context.actor.tenant_id:
            raise DomainValidationError(DenialCode.TENANT_SCOPE_DENIED, "SLA tenant mismatch")
        if context.version_before is not None and context.version_before != sla.version:
            raise DomainValidationError(DenialCode.WORKFLOW_STATE_DENIED, "SLA version conflict")


    @staticmethod
    def _schedule(sla: SlaInstanceAggregate) -> SlaSchedule:
        if sla.schedule is None or sla.schedule.due_at != sla.due_at:
            raise DomainValidationError(
                DenialCode.WORKFLOW_STATE_DENIED, "Resolved SLA schedule missing or inconsistent"
            )
        if sla.schedule.tenant_id != sla.tenant_id:
            raise DomainValidationError(DenialCode.TENANT_SCOPE_DENIED, "SLA schedule tenant mismatch")
        return sla.schedule


    def update_sla_derived_state(self, sla_id: str, context: TransitionContext) -> SlaInstanceAggregate:
        sla = self.sla_repo.get_sla(sla_id)
        if not sla:
            raise ValueError(f"SLA {sla_id} not found")

        self._validate_sla_context(sla, context)
        if context.actor.role not in {PrincipalRole.SVC_SLA, PrincipalRole.EMERGENCY_ADMIN}:
            raise DomainValidationError(
                DenialCode.ROLE_DENIED, "Only SLA worker may evaluate the clock"
            )
        if sla.status in {SlaState.COMPLETED, SlaState.PAUSED}:
            # Completed or paused SLAs do not auto-derive state changes from running clock
            return sla

        derived = self.get_derived_sla_state(self._schedule(sla), context.current_time)
        if derived != sla.status:
            SlaStateMachine.transition(sla, derived, context)
            self.sla_repo.save_sla(sla)

            self._record_event(
                "sla.state_changed.v1",
                sla_id,
                sla.tenant_id,
                context.correlation_id,
                {"status": derived.value, "version": sla.version},
            )
        return sla


    def pause_sla(
        self, sla_id: str, paused_reason: str, resume_at: datetime, context: TransitionContext
    ) -> SlaInstanceAggregate:
        sla = self.sla_repo.get_sla(sla_id)
        if not sla:
            raise ValueError(f"SLA {sla_id} not found")

        self._validate_sla_context(sla, context)
        if sla.status not in {SlaState.ON_TRACK, SlaState.DUE_SOON, SlaState.OVERDUE}:
            raise DomainValidationError(DenialCode.WORKFLOW_STATE_DENIED, "SLA state cannot be paused")
        started_at, resume_at = self._aware_time(context.current_time), self._aware_time(resume_at)
        if not paused_reason.strip() or resume_at <= started_at:
            raise DomainValidationError(
                DenialCode.WORKFLOW_STATE_DENIED, "Pause requires a reason and future resume time"
            )
        schedule = self._schedule(sla)
        if self.get_derived_sla_state(schedule, started_at) == SlaState.BREACHED:
            raise DomainValidationError(
                DenialCode.WORKFLOW_STATE_DENIED, "Breached SLA cannot be paused from a stale state"
            )
        if context.actor.role not in {
            PrincipalRole.EXPANSION_MANAGER,
            PrincipalRole.DATA_STEWARD,
            PrincipalRole.SVC_SLA,
            PrincipalRole.EMERGENCY_ADMIN,
        }:
            raise DomainValidationError(
                DenialCode.ROLE_DENIED, "Only an authorized SLA manager may pause"
            )
        if self.calendar is None or not self.calendar.permits_pause(schedule, paused_reason, resume_at):
            raise DomainValidationError(
                DenialCode.WORKFLOW_STATE_DENIED, "SLA policy/calendar does not authorize pause"
            )
        SlaStateMachine.transition(sla, SlaState.PAUSED, context)
        sla.paused_reason = paused_reason
        sla.resume_at = resume_at
        sla.pause_intervals.append(SlaPauseInterval(started_at, resume_at, paused_reason))
        self.sla_repo.save_sla(sla)

        self._record_event(
            "sla.state_changed.v1",
            sla_id,
            sla.tenant_id,
            context.correlation_id,
            {"status": SlaState.PAUSED.value, "version": sla.version, "reason": paused_reason},
        )
        return sla


    def resume_sla(self, sla_id: str, context: TransitionContext) -> SlaInstanceAggregate:
        sla = self.sla_repo.get_sla(sla_id)
        if not sla:
            raise ValueError(f"SLA {sla_id} not found")

        self._validate_sla_context(sla, context)
        if context.actor.role not in {
            PrincipalRole.EXPANSION_MANAGER,
            PrincipalRole.DATA_STEWARD,
            PrincipalRole.SVC_SLA,
            PrincipalRole.EMERGENCY_ADMIN,
        }:
            raise DomainValidationError(
                DenialCode.ROLE_DENIED, "Only an authorized SLA manager/worker may resume"
            )
        if sla.status != SlaState.PAUSED:
            raise ValueError("SLA is not paused")

        if not sla.pause_intervals or sla.pause_intervals[-1].ended_at is not None:
            raise DomainValidationError(
                DenialCode.WORKFLOW_STATE_DENIED, "Active SLA pause interval missing"
            )
        interval = sla.pause_intervals[-1]
        resumed_at = self._aware_time(context.current_time)
        if resumed_at < interval.started_at:
            raise DomainValidationError(DenialCode.WORKFLOW_STATE_DENIED, "SLA resume predates pause")
        schedule = self._schedule(sla)
        if self.calendar is None:
            raise DomainValidationError(
                DenialCode.WORKFLOW_STATE_DENIED, "Resolved SLA calendar is required"
            )
        resumed = self.calendar.resume_schedule(schedule, interval, resumed_at)
        if not isinstance(resumed, SlaSchedule) or (
            resumed.tenant_id,
            resumed.policy_version,
            resumed.calendar_version,
            resumed.started_at,
        ) != (
            schedule.tenant_id,
            schedule.policy_version,
            schedule.calendar_version,
            schedule.started_at,
        ):
            raise DomainValidationError(
                DenialCode.WORKFLOW_STATE_DENIED, "Resumed SLA schedule changed its binding"
            )
        if any(
            getattr(resumed, name) < getattr(schedule, name)
            for name in ("due_soon_at", "due_at", "breach_at")
        ):
            raise DomainValidationError(
                DenialCode.WORKFLOW_STATE_DENIED,
                "Pause exclusion cannot move SLA checkpoints backwards",
            )
        derived = self.get_derived_sla_state(resumed, resumed_at)
        SlaStateMachine.transition(sla, derived, context)
        sla.schedule, sla.due_at = resumed, resumed.due_at
        sla.pause_intervals[-1] = replace(interval, ended_at=resumed_at)
        sla.paused_duration_seconds = int(
            sum(
                (pause.ended_at - pause.started_at).total_seconds()
                for pause in sla.pause_intervals
                if pause.ended_at is not None
            )
        )
        sla.paused_reason = None
        sla.resume_at = None
        self.sla_repo.save_sla(sla)

        self._record_event(
            "sla.state_changed.v1",
            sla_id,
            sla.tenant_id,
            context.correlation_id,
            {
                "status": derived.value,
                "version": sla.version,
                "paused_duration_seconds": sla.paused_duration_seconds,
                "policy_version": resumed.policy_version,
                "calendar_version": resumed.calendar_version,
                "due_soon_at": resumed.due_soon_at.isoformat(),
                "due_at": resumed.due_at.isoformat(),
                "breach_at": resumed.breach_at.isoformat(),
            },
        )
        return sla


    def complete_sla(self, sla_id: str, context: TransitionContext) -> SlaInstanceAggregate:
        sla = self.sla_repo.get_sla(sla_id)
        if not sla:
            raise ValueError(f"SLA {sla_id} not found")
        self._validate_sla_context(sla, context)
        if context.actor.role not in {PrincipalRole.SVC_SLA, PrincipalRole.EMERGENCY_ADMIN}:
            raise DomainValidationError(DenialCode.ROLE_DENIED, "Only Workflow/SLA service may complete the SLA")
        schedule = self._schedule(sla)
        at = self._aware_time(context.current_time)
        if at < schedule.started_at:
            raise DomainValidationError(DenialCode.WORKFLOW_STATE_DENIED, "SLA completion predates initialization")
        intervals = list(sla.pause_intervals)
        if sla.status == SlaState.PAUSED:
            if not intervals or intervals[-1].ended_at is not None or at <= intervals[-1].started_at:
                raise DomainValidationError(DenialCode.WORKFLOW_STATE_DENIED, "Active completion pause interval is invalid")
            if self.calendar is None:
                raise DomainValidationError(DenialCode.WORKFLOW_STATE_DENIED, "Resolved completion calendar is required")
            resolved = self.calendar.resume_schedule(schedule, intervals[-1], at)
            if not isinstance(resolved, SlaSchedule) or (
                resolved.tenant_id, resolved.policy_version, resolved.calendar_version, resolved.started_at
            ) != (schedule.tenant_id, schedule.policy_version, schedule.calendar_version, schedule.started_at) or any(
                getattr(resolved, name) < getattr(schedule, name) for name in ("due_soon_at", "due_at", "breach_at")
            ):
                raise DomainValidationError(DenialCode.WORKFLOW_STATE_DENIED, "Invalid completed SLA calendar binding")
            schedule = resolved
            intervals[-1] = replace(intervals[-1], ended_at=at)
        SlaStateMachine.transition(sla, SlaState.COMPLETED, context)
        sla.schedule, sla.due_at, sla.pause_intervals = schedule, schedule.due_at, intervals
        sla.paused_duration_seconds = int(sum((p.ended_at-p.started_at).total_seconds() for p in intervals if p.ended_at is not None))
        sla.paused_reason = sla.resume_at = None
        self.sla_repo.save_sla(sla)

        self._record_event("sla.completed.v1", sla_id, sla.tenant_id, context.correlation_id, {})
        self._record_event(
            "sla.state_changed.v1",
            sla_id,
            sla.tenant_id,
            context.correlation_id,
            {"status": SlaState.COMPLETED.value, "version": sla.version},
        )
        return sla
