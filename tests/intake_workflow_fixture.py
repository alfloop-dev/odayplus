"""Explicit synthetic routing/calendar for HTTP contract tests, never runtime defaults."""

from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from modules.listing.application.intake_completion import CompletionRequirement
from modules.listing.application.intake_workflow_bootstrap import IntakeWorkflowPlan
from modules.listing.domain.intake_states import SlaSchedule


class SyntheticIntakeWorkflowPolicy:
    def authorize_assignment_actor(self, schedule, scope, actor_id):
        self.authorize_sla_actor(schedule, scope, actor_id)

    def authorize_assignment_action(
        self, schedule, scope, *, actor_id, owner_subject_id, owner_role, action
    ):
        self.authorize_assignment_actor(schedule, scope, actor_id)
        UUID(owner_subject_id)
        if action == "claim" and UUID(actor_id) != UUID(owner_subject_id):
            raise PermissionError("Only synthetic assignee may claim")

    def resolve_transfer(
        self,
        scope,
        schedule,
        *,
        actor_id,
        current_owner_subject_id,
        current_owner_role,
        owner_subject_id,
        owner_role,
        requested_due_at,
        reason,
    ):
        self.authorize_assignment_action(
            schedule,
            scope,
            actor_id=actor_id,
            owner_subject_id=current_owner_subject_id,
            owner_role=current_owner_role,
            action="transfer",
        )
        return self.resolve_assignment(
            scope,
            schedule,
            actor_id=actor_id,
            owner_subject_id=owner_subject_id,
            owner_role=owner_role,
            requested_due_at=requested_due_at or schedule.due_at,
            reason=reason,
        )

    def completion_requirements(self, scope, schedule):
        return (
            CompletionRequirement.parse(
                {
                    "kind": "intake",
                    "allowed_states": ["READY", "CANCELLED"],
                    "minimum_count": 1,
                    "require_audit": True,
                }
            ),
        )

    def authorize_sla_actor(self, schedule, scope, actor_id):
        UUID(actor_id)
        assert schedule.tenant_id == scope["tenant_id"]

    def calendar_for(self, scope):
        # Explicit synthetic continuous calendar, used only by these tests.
        return self

    def pause_approval_account(self, schedule, scope, actor_id):
        self.authorize_sla_actor(schedule, scope, actor_id)
        return "00000000-0000-0000-0000-000000000999"

    def permits_pause(self, schedule, reason, expected_resume_at):
        return reason in {
            "Awaiting customer feedback",
            "Scoped workflow verification",
            "Synthetic approved hold",
        }

    def resume_schedule(self, schedule, interval, resumed_at):
        excluded = resumed_at - interval.started_at
        return replace(
            schedule,
            due_soon_at=schedule.due_soon_at + excluded,
            due_at=schedule.due_at + excluded,
            breach_at=schedule.breach_at + excluded,
        )

    def resolve_initial(self, scope, submitted_at: datetime) -> IntakeWorkflowPlan:
        schedule = SlaSchedule(
            tenant_id=scope["tenant_id"],
            policy_version="synthetic-policy-v1",
            calendar_version="synthetic-calendar-v1",
            started_at=submitted_at,
            due_soon_at=submitted_at + timedelta(hours=3),
            due_at=submitted_at + timedelta(hours=4),
            breach_at=submitted_at + timedelta(hours=5),
        )
        return IntakeWorkflowPlan(queue_key="synthetic-review-queue", schedule=schedule)

    def resolve_assignment(
        self, scope, schedule, *, actor_id, owner_subject_id, owner_role, requested_due_at, reason
    ) -> IntakeWorkflowPlan:
        UUID(actor_id)
        UUID(owner_subject_id)
        # This test policy does not permit deadline overrides. The body is a
        # proposal; the service must return/persist the server policy schedule.
        return IntakeWorkflowPlan(queue_key="synthetic-review-queue", schedule=replace(schedule))


def seed_synthetic_sla(record, *, paused=False, started_at=None):
    """Upgrade a test envelope to an explicitly resolved synthetic schedule."""
    at = started_at or datetime.now(UTC) - timedelta(hours=1)
    schedule = (
        SyntheticIntakeWorkflowPolicy()
        .resolve_initial({"tenant_id": record["tenant_id"]}, at)
        .schedule
    )

    def iso(value):
        return value.isoformat().replace("+00:00", "Z")

    record.update(
        {
            "policy_version": schedule.policy_version,
            "calendar_version": schedule.calendar_version,
            "schedule": {
                k: iso(v) if isinstance(v, datetime) else v for k, v in asdict(schedule).items()
            },
            "due_at": iso(schedule.due_at),
            "due_soon_at": iso(schedule.due_soon_at),
            "breach_at": iso(schedule.breach_at),
            "pause_intervals": [],
            "active_pause_interval_id": None,
        }
    )
    if paused:
        pause_id = str(uuid4())
        record["active_pause_interval_id"] = pause_id
        record["pause_intervals"] = [
            {
                "pause_interval_id": pause_id,
                "paused_at": iso(at + timedelta(minutes=30)),
                "expected_resume_at": iso(at + timedelta(hours=2)),
                "resumed_at": None,
                "reason": "Scoped workflow verification",
                "approved_by": "00000000-0000-0000-0000-000000000999",
                "created_by": "00000000-0000-0000-0000-000000000101",
                "version": 1,
            }
        ]
    return record


def seed_synthetic_assignment_workflow(store, assignment, intake):
    """Explicit valid workflow behind resource-scope probes."""
    from copy import deepcopy

    from modules.listing.application.intake_workflow_bootstrap import record_workflow_change

    assignment.setdefault("owner_role", "expansion_user")
    assignment.setdefault("created_at", datetime.now(UTC).isoformat())
    sid = str(uuid4())
    sla = {
        "sla_instance_id": sid,
        "assignment_id": assignment["assignment_id"],
        "intake_id": intake["intake_id"],
        "tenant_id": assignment["tenant_id"],
        "state": "ON_TRACK",
        "version": 1,
        "paused_duration_seconds": 0,
        "audit_event_id": str(uuid4()),
        "correlation_id": str(uuid4()),
    }
    seed_synthetic_sla(sla)
    assignment["due_at"] = sla["due_at"]
    store.slas[sid] = sla
    intake.setdefault("correlation_id", str(uuid4()))
    intake["state"] = "READY"
    record_workflow_change(
        store,
        intake,
        resource_type="intake",
        before=deepcopy(intake),
        after=intake,
        action="synthetic.work_resolved",
        actor_id="00000000-0000-0000-0000-000000000101",
    )
