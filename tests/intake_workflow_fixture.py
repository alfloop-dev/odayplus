"""Explicit synthetic routing/calendar for HTTP contract tests, never runtime defaults."""

from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from modules.listing.application.intake_workflow_bootstrap import IntakeWorkflowPlan
from modules.listing.domain.intake_states import SlaSchedule


class SyntheticIntakeWorkflowPolicy:
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
