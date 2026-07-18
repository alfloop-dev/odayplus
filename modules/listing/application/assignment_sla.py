from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Protocol

from modules.listing.domain.intake_states import (
    AssignmentAggregate,
    AssignmentState,
    AssignmentStateMachine,
    SlaInstanceAggregate,
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

    def __init__(self, assignment_repo: AssignmentRepository, sla_repo: SlaRepository) -> None:
        self.assignment_repo = assignment_repo
        self.sla_repo = sla_repo
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

    def create_sla(self, sla_id: str, tenant_id: str, due_at: datetime, context: TransitionContext) -> SlaInstanceAggregate:
        SlaStateMachine.transition(None, SlaState.ON_TRACK, context)

        sla = SlaInstanceAggregate(
            id=sla_id,
            tenant_id=tenant_id,
            status=SlaState.ON_TRACK,
            version=1,
            due_at=due_at,
        )
        self.sla_repo.save_sla(sla)

        self._record_event(
            "sla.state_changed.v1",
            sla_id,
            tenant_id,
            context.correlation_id,
            {"status": SlaState.ON_TRACK.value, "version": 1}
        )
        return sla

    def get_derived_sla_state(self, due_at: datetime, current_time: datetime) -> SlaState:
        """Helper to derive SLA state strictly from time difference."""
        if current_time >= due_at + timedelta(hours=24):
            return SlaState.BREACHED
        elif current_time > due_at:
            return SlaState.OVERDUE
        elif due_at - current_time <= timedelta(hours=2):
            return SlaState.DUE_SOON
        else:
            return SlaState.ON_TRACK

    def update_sla_derived_state(self, sla_id: str, context: TransitionContext) -> SlaInstanceAggregate:
        sla = self.sla_repo.get_sla(sla_id)
        if not sla:
            raise ValueError(f"SLA {sla_id} not found")

        if sla.status in {SlaState.COMPLETED, SlaState.PAUSED}:
            # Completed or paused SLAs do not auto-derive state changes from running clock
            return sla

        derived = self.get_derived_sla_state(sla.due_at, context.current_time)
        if derived != sla.status:
            SlaStateMachine.transition(sla, derived, context)
            self.sla_repo.save_sla(sla)

            self._record_event(
                "sla.state_changed.v1",
                sla_id,
                sla.tenant_id,
                context.correlation_id,
                {"status": derived.value, "version": sla.version}
            )
        return sla

    def pause_sla(self, sla_id: str, paused_reason: str, resume_at: datetime, context: TransitionContext) -> SlaInstanceAggregate:
        sla = self.sla_repo.get_sla(sla_id)
        if not sla:
            raise ValueError(f"SLA {sla_id} not found")

        SlaStateMachine.transition(sla, SlaState.PAUSED, context)
        sla.paused_reason = paused_reason
        sla.resume_at = resume_at
        self.sla_repo.save_sla(sla)

        self._record_event(
            "sla.state_changed.v1",
            sla_id,
            sla.tenant_id,
            context.correlation_id,
            {"status": SlaState.PAUSED.value, "version": sla.version, "reason": paused_reason}
        )
        return sla

    def resume_sla(self, sla_id: str, context: TransitionContext) -> SlaInstanceAggregate:
        sla = self.sla_repo.get_sla(sla_id)
        if not sla:
            raise ValueError(f"SLA {sla_id} not found")

        if sla.status != SlaState.PAUSED:
            raise ValueError("SLA is not paused")

        # Resume to derived state from clock
        derived = self.get_derived_sla_state(sla.due_at, context.current_time)
        SlaStateMachine.transition(sla, derived, context)
        sla.paused_reason = None
        sla.resume_at = None
        self.sla_repo.save_sla(sla)

        self._record_event(
            "sla.state_changed.v1",
            sla_id,
            sla.tenant_id,
            context.correlation_id,
            {"status": derived.value, "version": sla.version}
        )
        return sla

    def complete_sla(self, sla_id: str, context: TransitionContext) -> SlaInstanceAggregate:
        sla = self.sla_repo.get_sla(sla_id)
        if not sla:
            raise ValueError(f"SLA {sla_id} not found")

        SlaStateMachine.transition(sla, SlaState.COMPLETED, context)
        self.sla_repo.save_sla(sla)

        self._record_event(
            "sla.completed.v1",
            sla_id,
            sla.tenant_id,
            context.correlation_id,
            {}
        )
        self._record_event(
            "sla.state_changed.v1",
            sla_id,
            sla.tenant_id,
            context.correlation_id,
            {"status": SlaState.COMPLETED.value, "version": sla.version}
        )
        return sla
