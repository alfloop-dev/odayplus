"""Explicit synthetic routing/calendar for HTTP contract tests, never runtime defaults."""

from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID

from modules.listing.application.intake_workflow_bootstrap import IntakeWorkflowPlan
from modules.listing.domain.intake_states import SlaSchedule


class SyntheticIntakeWorkflowPolicy:
    def resolve_initial(self, scope, submitted_at: datetime) -> IntakeWorkflowPlan:
        schedule = SlaSchedule(
            tenant_id=scope["tenant_id"], policy_version="synthetic-policy-v1",
            calendar_version="synthetic-calendar-v1", started_at=submitted_at,
            due_soon_at=submitted_at + timedelta(hours=3),
            due_at=submitted_at + timedelta(hours=4),
            breach_at=submitted_at + timedelta(hours=5),
        )
        return IntakeWorkflowPlan(queue_key="synthetic-review-queue", schedule=schedule)

    def resolve_assignment(self, scope, schedule, *, actor_id, owner_subject_id, owner_role,
                           requested_due_at, reason) -> IntakeWorkflowPlan:
        UUID(actor_id)
        UUID(owner_subject_id)
        # This test policy does not permit deadline overrides. The body is a
        # proposal; the service must return/persist the server policy schedule.
        return IntakeWorkflowPlan(queue_key="synthetic-review-queue", schedule=replace(schedule))
