"""Persist authoritative SLA commands in the caller's Intake transaction."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from modules.listing.application.assignment_sla import (
    AssignmentSlaService,
    InMemoryAssignmentRepository,
    InMemorySlaRepository,
)
from modules.listing.application.intake_workflow_bootstrap import (
    IntakeWorkflowUnavailable,
    enqueue_workflow_event,
    record_workflow_change,
    schedule_from_record,
)
from modules.listing.domain.intake_states import (
    Actor,
    DenialCode,
    DomainValidationError,
    PrincipalRole,
    SlaInstanceAggregate,
    SlaPauseInterval,
    SlaState,
    TransitionContext,
)


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def sla_from_record(record: Mapping[str, Any], scope: Mapping[str, Any]) -> SlaInstanceAggregate:
    before = deepcopy(dict(record))
    schedule = schedule_from_record(before)
    if (
        schedule.tenant_id != scope.get("tenant_id")
        or before.get("tenant_id") != schedule.tenant_id
        or before.get("policy_version") != schedule.policy_version
        or before.get("calendar_version") != schedule.calendar_version
        or _parse(before["due_at"]) != schedule.due_at
    ):
        raise IntakeWorkflowUnavailable("SLA envelope and authoritative schedule differ")
    intervals = []
    for entry in before.get("pause_intervals", []):
        UUID(entry["pause_interval_id"])
        UUID(entry["approved_by"])
        UUID(entry["created_by"])
        if UUID(entry["approved_by"]) == UUID(entry["created_by"]):
            raise IntakeWorkflowUnavailable("Pause approval cannot be self approval")
        intervals.append(
            SlaPauseInterval(
                _parse(entry["paused_at"]),
                _parse(entry["expected_resume_at"]),
                entry["reason"],
                _parse(entry["resumed_at"]) if entry.get("resumed_at") else None,
            )
        )
    active = [entry for entry in before.get("pause_intervals", []) if not entry.get("resumed_at")]
    if (
        len(active) > 1
        or (active and active[-1] is not before["pause_intervals"][-1])
        or (before["state"] == "PAUSED") != bool(active)
        or before.get("active_pause_interval_id")
        != (active[0]["pause_interval_id"] if active else None)
    ):
        raise IntakeWorkflowUnavailable("Stored active pause interval is inconsistent")
    return SlaInstanceAggregate(
        id=before["sla_instance_id"],
        tenant_id=schedule.tenant_id,
        status=SlaState(before["state"]),
        version=before["version"],
        due_at=schedule.due_at,
        schedule=schedule,
        pause_intervals=intervals,
        paused_duration_seconds=before["paused_duration_seconds"],
    )


def change_sla(
    store: Any,
    record: Mapping[str, Any],
    policy: Any,
    *,
    scope: Mapping[str, Any],
    actor_id: str,
    role: PrincipalRole,
    reason: str,
    correlation_id: str,
    current_time: datetime,
    expected_resume_at: datetime | None = None,
) -> dict[str, Any]:
    """Validate on an isolated aggregate, then save state, interval and evidence.

    The memory repository is a command-local domain adapter. Durable persistence
    and rollback belong to the surrounding store transaction, including replay.
    Legacy envelopes without a resolved schedule never acquire default dates.
    """
    if policy is None:
        raise IntakeWorkflowUnavailable("Authoritative SLA policy/calendar is unavailable")
    if not reason.strip():
        raise DomainValidationError(
            DenialCode.WORKFLOW_STATE_DENIED, "SLA command justification is required"
        )
    UUID(correlation_id)
    before = deepcopy(dict(record))
    if not before.get("intake_id") or before["intake_id"] not in store.intakes:
        raise IntakeWorkflowUnavailable("SLA command requires its authoritative Intake binding")
    UUID(before["intake_id"])
    schedule = schedule_from_record(before)
    if (
        schedule.tenant_id != scope.get("tenant_id")
        or before.get("tenant_id") != schedule.tenant_id
        or before.get("policy_version") != schedule.policy_version
        or before.get("calendar_version") != schedule.calendar_version
        or _parse(before["due_at"]) != schedule.due_at
    ):
        raise IntakeWorkflowUnavailable("SLA envelope and authoritative schedule differ")
    authorized_scope = deepcopy(dict(scope))
    policy.authorize_sla_actor(schedule, authorized_scope, actor_id)
    calendar = policy.calendar_for(authorized_scope)
    aggregate = sla_from_record(before, scope)
    repository = InMemorySlaRepository()
    repository.save_sla(aggregate)
    service = AssignmentSlaService(InMemoryAssignmentRepository(), repository, calendar=calendar)
    context = TransitionContext(
        actor=Actor(actor_id, role, schedule.tenant_id),
        version_before=aggregate.version,
        correlation_id=correlation_id,
        reason=reason,
        current_time=current_time,
    )
    updated = deepcopy(before)
    if expected_resume_at is not None:
        approval = policy.pause_approval_account(schedule, authorized_scope, actor_id)
        UUID(approval)
        UUID(actor_id)
        if UUID(approval) == UUID(actor_id):
            raise PermissionError("Pause creator must differ from policy approval account")
        if current_time < schedule.started_at:
            raise IntakeWorkflowUnavailable("Pause cannot predate SLA initialization")
        service.pause_sla(aggregate.id, reason, expected_resume_at, context)
        pause_id = str(uuid4())
        updated.setdefault("pause_intervals", []).append(
            {
                "pause_interval_id": pause_id,
                "sla_instance_id": aggregate.id,
                "tenant_id": schedule.tenant_id,
                "paused_at": _iso(current_time),
                "expected_resume_at": _iso(expected_resume_at),
                "resumed_at": None,
                "reason": reason,
                "approved_by": approval,
                "created_by": actor_id,
                "approval_policy_version": schedule.policy_version,
                "version": 1,
            }
        )
        updated["active_pause_interval_id"] = pause_id
        action = "sla.pause"
    else:
        if not aggregate.pause_intervals or current_time <= aggregate.pause_intervals[-1].started_at:
            raise IntakeWorkflowUnavailable("Resume must follow the active pause start")
        service.resume_sla(aggregate.id, context)
        updated["pause_intervals"][-1].update(
            {
                "resumed_at": _iso(current_time),
                "resumed_by": actor_id,
                "resume_reason": reason,
                "version": updated["pause_intervals"][-1]["version"] + 1,
            }
        )
        updated["active_pause_interval_id"] = None
        action = "sla.resume"
    if authorized_scope != scope:
        raise IntakeWorkflowUnavailable("Provider changed the authorized SLA scope")
    resolved = aggregate.schedule
    assert resolved is not None  # Domain service enforces the schedule binding.
    updated.update(
        {
            "state": aggregate.status.value,
            "version": aggregate.version,
            "due_at": _iso(resolved.due_at),
            "due_soon_at": _iso(resolved.due_soon_at),
            "breach_at": _iso(resolved.breach_at),
            "updated_at": _iso(current_time),
            "paused_duration_seconds": aggregate.paused_duration_seconds,
            "correlation_id": correlation_id,
            "schedule": {
                k: _iso(v) if isinstance(v, datetime) else v for k, v in asdict(resolved).items()
            },
        }
    )
    intake = store.intakes.get(before.get("intake_id"))
    evidence_context = {
        "scope": dict(scope),
        "intake_id": before.get("intake_id"),
        "correlation_id": correlation_id,
        "audit": intake.setdefault("audit", []) if intake else [],
    }
    record_workflow_change(
        store,
        evidence_context,
        resource_type="sla_instance",
        before=before,
        after=updated,
        action=action,
        actor_id=actor_id,
    )
    enqueue_workflow_event(
        store,
        evidence_context,
        event_type="sla.state_changed",
        producer="sla_service",
        aggregate_type="sla_instance",
        aggregate_id=aggregate.id,
        aggregate_version=aggregate.version,
        payload={
            "sla_instance_id": aggregate.id,
            "intake_id": before["intake_id"],
            "from_state": before["state"],
            "to_state": aggregate.status.value,
            "due_at": updated["due_at"],
            "version": aggregate.version,
            "occurred_at": _iso(current_time),
            "paused_duration_seconds": aggregate.paused_duration_seconds,
        },
    )
    if intake is not None:
        if intake.get("due_at") != updated["due_at"]:
            prior_intake = deepcopy(intake)
            intake.update(
                {
                    "due_at": updated["due_at"],
                    "version": intake["version"] + 1,
                    "updated_at": _iso(current_time),
                }
            )
            intake.setdefault("processing_history", []).append(
                {
                    "transition_id": str(uuid4()),
                    "from_state": intake["state"],
                    "to_state": intake["state"],
                    "occurred_at": _iso(current_time),
                    "actor": actor_id,
                    "version_after": intake["version"],
                }
            )
            record_workflow_change(
                store,
                evidence_context,
                resource_type="intake",
                before=prior_intake,
                after=intake,
                action="sla.deadline_adjusted",
                actor_id=actor_id,
            )
    assignment = store.assignments.get(before.get("assignment_id"))
    if (
        assignment is not None
        and assignment.get("intake_id") == before.get("intake_id")
        and assignment.get("due_at") != updated["due_at"]
    ):
        prior_assignment = deepcopy(assignment)
        assignment.update(
            {
                "due_at": updated["due_at"],
                "version": assignment["version"] + 1,
                "updated_at": _iso(current_time),
                "correlation_id": correlation_id,
            }
        )
        record_workflow_change(
            store,
            evidence_context,
            resource_type="assignment",
            before=prior_assignment,
            after=assignment,
            action="sla.deadline_adjusted",
            actor_id=actor_id,
        )
    store.slas[aggregate.id] = updated
    return updated
