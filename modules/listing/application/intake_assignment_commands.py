"""Transactional Assignment transitions backed by current policy authority."""

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
from modules.listing.application.intake_completion import verify_completion
from modules.listing.application.intake_sla_commands import sla_from_record
from modules.listing.application.intake_workflow_bootstrap import (
    IntakeWorkflowPlan,
    IntakeWorkflowUnavailable,
    enqueue_workflow_event,
    record_workflow_change,
    schedule_from_record,
)
from modules.listing.domain.intake_states import (
    Actor,
    AssignmentAggregate,
    AssignmentState,
    AssignmentStateMachine,
    PrincipalRole,
    TransitionContext,
)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def assignment_workflow(store: Any, record: Mapping[str, Any]):
    intake = store.intakes.get(record.get("intake_id"))
    if not intake or record.get("tenant_id") != intake["scope"]["tenant_id"]:
        raise IntakeWorkflowUnavailable("Assignment requires its authoritative Intake binding")
    slas = [
        row for row in store.slas.values() if row.get("assignment_id") == record["assignment_id"]
    ]
    if len(slas) != 1 or slas[0].get("intake_id") != intake["intake_id"]:
        raise IntakeWorkflowUnavailable("Assignment requires exactly one bound SLA")
    schedule = schedule_from_record(slas[0])
    if schedule.tenant_id != intake["scope"]["tenant_id"]:
        raise IntakeWorkflowUnavailable("Assignment SLA tenant differs")
    if datetime.fromisoformat(record["due_at"].replace("Z", "+00:00")) != schedule.due_at:
        raise IntakeWorkflowUnavailable("Assignment and SLA deadlines differ")
    if slas[0]["state"] == "COMPLETED" and record["status"] != "COMPLETED":
        raise IntakeWorkflowUnavailable("Assignment and SLA completion states differ")
    return intake, slas[0], schedule


def authorize_assignment_read(store: Any, record: Mapping[str, Any], policy: Any, actor_id: str):
    if policy is None:
        raise IntakeWorkflowUnavailable("Assignment registry is unavailable")
    intake, sla, schedule = assignment_workflow(store, record)
    scope = deepcopy(intake["scope"])
    policy.authorize_assignment_actor(schedule, scope, actor_id)
    if scope != intake["scope"]:
        raise IntakeWorkflowUnavailable("Provider changed the authorized assignment scope")
    return intake, sla, schedule


def list_assignment_transfer_targets(
    store: Any, record: Mapping[str, Any], policy: Any, actor_id: str,
) -> dict[str, Any]:
    intake, _sla, schedule = authorize_assignment_read(store, record, policy, actor_id)
    scope = deepcopy(intake["scope"])
    items = policy.list_assignment_transfer_targets(
        scope, schedule, actor_id=actor_id,
        current_owner_subject_id=record["owner_subject_id"],
        current_owner_role=record["owner_role"],
    )
    if scope != intake["scope"]:
        raise IntakeWorkflowUnavailable("Provider changed the authorized assignment scope")
    if AssignmentState.TRANSFERRED not in AssignmentStateMachine.LEGAL_TRANSITIONS.get(
        AssignmentState(record["status"]), set(),
    ):
        # No selectable recipient can make a forbidden state transition valid.
        items = []
    return {
        "assignment_id": record["assignment_id"],
        "assignment_version": record["version"],
        "items": items,
    }


def change_assignment(
    store: Any,
    record: Mapping[str, Any],
    policy: Any,
    *,
    action: str,
    actor_id: str,
    role: PrincipalRole,
    reason: str,
    correlation_id: str,
    current_time: datetime,
    target: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The caller includes command/replay flush in the same transaction."""
    UUID(actor_id)
    UUID(correlation_id)
    if not reason.strip() or current_time.utcoffset() is None:
        raise ValueError("Assignment reason and timezone-aware service clock are required")
    intake, sla, schedule = authorize_assignment_read(store, record, policy, actor_id)
    before, prior_sla = deepcopy(dict(record)), deepcopy(sla)
    scope = deepcopy(intake["scope"])
    policy.authorize_assignment_action(
        schedule,
        scope,
        actor_id=actor_id,
        owner_subject_id=before["owner_subject_id"],
        owner_role=before["owner_role"],
        action=action,
    )
    aggregate = AssignmentAggregate(
        id=before["assignment_id"],
        tenant_id=before["tenant_id"],
        status=AssignmentState(before["status"]),
        version=before["version"],
        assignee_id=str(UUID(before["owner_subject_id"])),
        owner_id=before.get("claimed_by"),
        due_at=schedule.due_at,
    )
    assignment_repo, sla_repo = InMemoryAssignmentRepository(), InMemorySlaRepository()
    assignment_repo.save_assignment(aggregate)
    service = AssignmentSlaService(assignment_repo, sla_repo, calendar=policy.calendar_for(scope))
    context = TransitionContext(
        actor=Actor(str(UUID(actor_id)), role, before["tenant_id"]),
        version_before=aggregate.version,
        correlation_id=correlation_id,
        reason=reason,
        current_time=current_time,
    )
    updated = deepcopy(before)
    transitions = []
    proofs = []
    if action == "claim":
        if aggregate.status == AssignmentState.TRANSFERRED:
            service.accept_transfer(aggregate.id, context)
            transitions.append(
                (
                    "assignment.accept",
                    "assignment.assigned",
                    aggregate.status.value,
                    aggregate.version,
                )
            )
            context = TransitionContext(
                actor=context.actor,
                version_before=aggregate.version,
                correlation_id=correlation_id,
                reason=reason,
                current_time=current_time,
            )
        service.claim_task(aggregate.id, context)
        updated.update({"claimed_by": str(UUID(actor_id)), "claimed_at": _iso(current_time)})
        transitions.append(
            ("assignment.claim", "assignment.claimed", aggregate.status.value, aggregate.version)
        )
    elif action == "transfer":
        if target is None or not target.get("handoff_note", "").strip():
            raise ValueError("Transfer requires an authorized target and handoff note")
        plan = policy.resolve_transfer(
            scope,
            schedule,
            actor_id=actor_id,
            current_owner_subject_id=before["owner_subject_id"],
            current_owner_role=before["owner_role"],
            owner_subject_id=target["owner_subject_id"],
            owner_role=target["owner_role"],
            requested_due_at=target.get("due_at"),
            reason=reason,
        )
        if not isinstance(plan, IntakeWorkflowPlan) or (
            plan.schedule.tenant_id,
            plan.schedule.started_at,
            plan.schedule.policy_version,
            plan.schedule.calendar_version,
        ) != (
            schedule.tenant_id,
            schedule.started_at,
            schedule.policy_version,
            schedule.calendar_version,
        ):
            raise IntakeWorkflowUnavailable("Transfer changed the authoritative schedule binding")
        schedule = plan.schedule
        service.transfer_task(aggregate.id, str(UUID(target["owner_subject_id"])), context)
        updated.update(
            {
                "owner_subject_id": aggregate.assignee_id,
                "owner_role": target["owner_role"],
                "handoff_note": target["handoff_note"],
                "transfer_reason": reason,
                "transferred_by": str(UUID(actor_id)),
                "transferred_at": _iso(current_time),
                "claimed_by": None,
                "claimed_at": None,
                "queue_key": plan.queue_key,
            }
        )
        transitions.append(
            (
                "assignment.transfer",
                "assignment.transferred",
                aggregate.status.value,
                aggregate.version,
            )
        )
    elif action == "complete":
        proofs = verify_completion(store, intake, policy.completion_requirements(scope, schedule))
        sla_aggregate = sla_from_record(sla, scope)
        sla_repo.save_sla(sla_aggregate)
        service.complete_task(aggregate.id, context)
        service.complete_sla(
            sla_aggregate.id,
            TransitionContext(
                actor=Actor(str(UUID(actor_id)), PrincipalRole.SVC_SLA, before["tenant_id"]),
                version_before=sla_aggregate.version,
                correlation_id=correlation_id,
                reason=reason,
                current_time=current_time,
            ),
        )
        schedule = sla_aggregate.schedule
        updated.update(
            {
                "completed_by": str(UUID(actor_id)),
                "completed_at": _iso(current_time),
                "completion_reason": reason,
                "completion_evidence": proofs,
            }
        )
        transitions.append(
            (
                "assignment.complete",
                "assignment.completed",
                aggregate.status.value,
                aggregate.version,
            )
        )
    else:
        raise ValueError("Unknown Assignment command")
    if scope != intake["scope"]:
        raise IntakeWorkflowUnavailable("Provider changed the authorized assignment scope")
    timestamp = _iso(current_time)
    updated.update(
        {
            "status": aggregate.status.value,
            "version": aggregate.version,
            "due_at": _iso(schedule.due_at),
            "updated_at": timestamp,
            "correlation_id": correlation_id,
        }
    )
    evidence_context = {
        "scope": intake["scope"],
        "intake_id": intake["intake_id"],
        "correlation_id": correlation_id,
        "audit": intake.setdefault("audit", []),
    }
    last = before
    for audit_action, event_type, state, version in transitions:
        checkpoint = deepcopy(updated)
        checkpoint.update({"status": state, "version": version})
        if audit_action == "assignment.accept":
            checkpoint.update({"claimed_by": None, "claimed_at": None})
        elif audit_action == "assignment.claim":
            checkpoint.update({"claimed_by": str(UUID(actor_id)), "claimed_at": timestamp})
        checkpoint.setdefault("transition_history", []).append(
            {
                "transition_id": str(uuid4()),
                "from_status": last["status"],
                "to_status": state,
                "version_after": version,
                "actor_subject_id": str(UUID(actor_id)),
                "reason": reason,
                "occurred_at": timestamp,
            }
        )
        record_workflow_change(
            store,
            evidence_context,
            resource_type="assignment",
            before=last,
            after=checkpoint,
            action=audit_action,
            actor_id=str(UUID(actor_id)),
        )
        payload = (
            {
                k: checkpoint[k]
                for k in (
                    "assignment_id",
                    "intake_id",
                    "status",
                    "owner_subject_id",
                    "due_at",
                    "version",
                )
            }
            if event_type in {"assignment.assigned", "assignment.transferred"}
            else {
                "assignment_id": aggregate.id,
                "intake_id": intake["intake_id"],
                "from_status": last["status"],
                "to_status": state,
                "owner_subject_id": checkpoint["owner_subject_id"],
                "version": version,
                "occurred_at": timestamp,
            }
        )
        enqueue_workflow_event(
            store,
            evidence_context,
            event_type=event_type,
            producer="workflow_service",
            aggregate_type="assignment",
            aggregate_id=aggregate.id,
            aggregate_version=version,
            payload=payload,
        )
        updated, last = checkpoint, checkpoint
    new_sla = deepcopy(sla)
    if action in {"transfer", "complete"}:
        new_sla.update(
            {
                "version": sla["version"] + 1,
                "schedule": {
                    k: _iso(v) if isinstance(v, datetime) else v
                    for k, v in asdict(schedule).items()
                },
                "due_at": _iso(schedule.due_at),
                "due_soon_at": _iso(schedule.due_soon_at),
                "breach_at": _iso(schedule.breach_at),
                "updated_at": timestamp,
                "correlation_id": correlation_id,
            }
        )
        if action == "complete":
            new_sla.update(
                {
                    "state": "COMPLETED",
                    "completed_at": timestamp,
                    "completion_evidence": proofs,
                    "paused_duration_seconds": sla_aggregate.paused_duration_seconds,
                    "active_pause_interval_id": None,
                }
            )
            if prior_sla["state"] == "PAUSED":
                new_sla["pause_intervals"][-1].update(
                    {
                        "resumed_at": timestamp,
                        "closed_by": str(UUID(actor_id)),
                        "close_reason": "assignment.complete",
                        "version": new_sla["pause_intervals"][-1]["version"] + 1,
                    }
                )
        elif new_sla["state"] not in {"PAUSED", "COMPLETED"}:
            new_sla["state"] = service.get_derived_sla_state(schedule, current_time).value
        record_workflow_change(
            store,
            evidence_context,
            resource_type="sla_instance",
            before=prior_sla,
            after=new_sla,
            action="sla.complete" if action == "complete" else "sla.transfer_schedule",
            actor_id=str(UUID(actor_id)),
        )
        enqueue_workflow_event(
            store,
            evidence_context,
            event_type="sla.state_changed",
            producer="sla_service",
            aggregate_type="sla_instance",
            aggregate_id=sla["sla_instance_id"],
            aggregate_version=new_sla["version"],
            payload={
                "sla_instance_id": sla["sla_instance_id"],
                "intake_id": intake["intake_id"],
                "from_state": prior_sla["state"],
                "to_state": new_sla["state"],
                "due_at": new_sla["due_at"],
                "version": new_sla["version"],
                "occurred_at": timestamp,
                "paused_duration_seconds": new_sla["paused_duration_seconds"],
            },
        )
        store.slas[new_sla["sla_instance_id"]] = new_sla
    prior_intake = deepcopy(intake)
    intake.update(
        {
            "assigned_to": updated["owner_subject_id"],
            "due_at": updated["due_at"],
            "version": intake["version"] + 1,
            "updated_at": timestamp,
        }
    )
    record_workflow_change(
        store,
        evidence_context,
        resource_type="intake",
        before=prior_intake,
        after=intake,
        action=f"assignment.{action}",
        actor_id=str(UUID(actor_id)),
    )
    store.assignments[aggregate.id] = updated
    return updated
