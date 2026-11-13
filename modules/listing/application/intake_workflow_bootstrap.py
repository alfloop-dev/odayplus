"""Initialize the service-owned workflow in the Intake submission transaction."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from modules.listing.application.assignment_sla import SlaCalendar
from modules.listing.domain.intake_states import SlaSchedule


class IntakeWorkflowUnavailable(RuntimeError):
    """A verified routing/policy/calendar binding could not be resolved."""


@dataclass(frozen=True)
class IntakeWorkflowPlan:
    queue_key: str
    schedule: SlaSchedule

    def __post_init__(self) -> None:
        if not isinstance(self.queue_key, str) or not self.queue_key.strip():
            raise ValueError("Resolved workflow queue is required")
        if not isinstance(self.schedule, SlaSchedule):
            raise ValueError("Resolved policy/calendar schedule is required")


class IntakeWorkflowPolicy(Protocol):
    """Server-owned registry adapter. There is no production default policy."""

    def resolve_initial(
        self, scope: Mapping[str, Any], submitted_at: datetime
    ) -> IntakeWorkflowPlan: ...

    def resolve_assignment(
        self, scope: Mapping[str, Any], schedule: SlaSchedule, *, actor_id: str,
        owner_subject_id: str, owner_role: str, requested_due_at: datetime, reason: str
    ) -> IntakeWorkflowPlan:
        """Verify target membership/role and resolve any authorized due override."""
        ...

    def calendar_for(self, scope: Mapping[str, Any]) -> SlaCalendar: ...

    def authorize_sla_actor(self, schedule: SlaSchedule, scope: Mapping[str, Any], actor_id: str) -> None: ...

    def pause_approval_account(self, schedule: SlaSchedule, scope: Mapping[str, Any], actor_id: str) -> str: ...

    def authorize_assignment_actor(self, schedule: SlaSchedule, scope: Mapping[str, Any], actor_id: str) -> None: ...

    def authorize_assignment_action(self, schedule: SlaSchedule, scope: Mapping[str, Any], *, actor_id: str,
                                    owner_subject_id: str, owner_role: str, action: str) -> None: ...

    def resolve_transfer(self, scope: Mapping[str, Any], schedule: SlaSchedule, *, actor_id: str,
                         current_owner_subject_id: str, current_owner_role: str, owner_subject_id: str,
                         owner_role: str, requested_due_at: datetime, reason: str) -> IntakeWorkflowPlan: ...

    def list_assignment_transfer_targets(
        self, scope: Mapping[str, Any], schedule: SlaSchedule, *, actor_id: str,
        current_owner_subject_id: str, current_owner_role: str,
    ) -> list[dict[str, str]]: ...

    def completion_requirements(self, scope: Mapping[str, Any], schedule: SlaSchedule) -> tuple[Any, ...]: ...


def schedule_from_record(record: Mapping[str, Any]) -> SlaSchedule:
    value = record.get("schedule")
    if not isinstance(value, Mapping):
        raise IntakeWorkflowUnavailable("Resolved SLA schedule is unavailable")
    try:
        fields = dict(value)
        for name in ("started_at", "due_soon_at", "due_at", "breach_at"):
            fields[name] = datetime.fromisoformat(fields[name].replace("Z", "+00:00"))
        return SlaSchedule(**fields)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise IntakeWorkflowUnavailable("Resolved SLA schedule is invalid") from exc


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def record_workflow_change(store: Any, intake: dict[str, Any], *, resource_type: str,
                           before: dict[str, Any] | None, after: dict[str, Any],
                           action: str, actor_id: str, result: str = "SUCCEEDED",
                           service_principal: str = "intake_workflow", correlation_id: str | None = None) -> str:
    """Append transaction-owned audit evidence; never return an unpersisted id."""
    resource_key = {"intake": "intake_id", "assignment": "assignment_id", "sla_instance": "sla_instance_id",
                    "correction": "correction_id", "identity_decision": "decision_id",
                    "promotion_decision": "promotion_decision_id", "job": "job_id"}[resource_type]
    audit_id = str(uuid4())
    after["audit_event_id"] = audit_id
    if resource_type == "sla_instance":
        after["receipt"] = audit_id
    audit = {
        "audit_event_id": audit_id, "tenant_id": intake["scope"]["tenant_id"],
        "intake_id": intake["intake_id"], "actor_subject_id": actor_id,
        "service_principal": service_principal, "action": action,
        "resource_type": resource_type, "resource_id": after[resource_key],
        "before_value": deepcopy(before), "after_value": deepcopy(after),
        "result": result, "correlation_id": correlation_id or intake["correlation_id"],
        "occurred_at": _iso(datetime.now(UTC)), "worm_state": "worm_pending",
    }
    store.workflow_audits[audit_id] = audit
    intake.setdefault("audit", []).append({k: audit[k] for k in ("audit_event_id", "action", "occurred_at", "result")})
    return audit_id


def enqueue_workflow_event(store: Any, intake: Mapping[str, Any], *, event_type: str,
                           producer: str, aggregate_type: str, aggregate_id: str,
                           aggregate_version: int, payload: Mapping[str, Any]) -> str:
    event_id = str(uuid4())
    tenant_id = intake["scope"]["tenant_id"]
    store.workflow_outbox[event_id] = {
        "event_id": event_id, "event_type": event_type, "event_version": 1,
        "occurred_at": _iso(datetime.now(UTC)), "producer": producer,
        "tenant_id": tenant_id, "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id, "aggregate_version": aggregate_version,
        "partition_key": f"{tenant_id}:{intake['intake_id']}",
        "correlation_id": intake["correlation_id"], "payload": deepcopy(dict(payload)),
        "sensitive_fields": ["payload.original_url"] if event_type == "intake.submitted" else [],
    }
    return event_id


def initialize_intake_workflow(
    store: Any, intake: dict[str, Any], policy: IntakeWorkflowPolicy | None,
    *, record_submission: bool = True,
) -> tuple[str, str]:
    """Create one unassigned task, SLA and evidence inside the caller's unit of work.

    The caller must enclose this function, the Intake/job writes and the replay
    receipt in ``store.transaction()``. Resolve everything before changing state.
    Policy inputs are copied so a provider cannot rewrite the authorized scope.
    The native SQL submission owner already records its Intake creation evidence
    in this transaction and passes ``record_submission=False``. Operator/v1
    callers retain this helper's existing submission evidence.
    """
    if policy is None:
        raise IntakeWorkflowUnavailable("Intake workflow policy/calendar is unavailable")
    scope = deepcopy(intake["scope"])
    intake_id, tenant_id = intake["intake_id"], scope["tenant_id"]
    UUID(intake_id)
    UUID(tenant_id)
    UUID(intake["submitted_by"])
    submitted_at = datetime.fromisoformat(intake["submitted_at"].replace("Z", "+00:00"))
    if submitted_at.tzinfo is None or submitted_at.utcoffset() is None:
        raise IntakeWorkflowUnavailable("Timezone-aware Intake submission is required")
    existing_assignments = [a for a in store.assignments.values() if a.get("intake_id") == intake_id]
    existing_slas = [s for s in store.slas.values() if s.get("intake_id") == intake_id]
    if existing_assignments or existing_slas:
        raise IntakeWorkflowUnavailable("Initial workflow already exists; initialization cannot reset it")
    try:
        plan = policy.resolve_initial(scope, submitted_at)
    except Exception as exc:
        raise IntakeWorkflowUnavailable("Intake workflow resolution failed") from exc
    if (
        not isinstance(plan, IntakeWorkflowPlan)
        or scope != intake["scope"]
        or plan.schedule.tenant_id != tenant_id
        or plan.schedule.started_at != submitted_at.astimezone(UTC)
    ):
        raise IntakeWorkflowUnavailable("Resolved workflow does not bind to this Intake")

    schedule = plan.schedule
    assignment_id, sla_id = str(uuid4()), str(uuid4())
    correlation_id = intake.get("correlation_id") or str(uuid4())
    UUID(correlation_id)
    ts, due = _iso(submitted_at), _iso(schedule.due_at)
    assignment_audit_id, sla_audit_id = str(uuid4()), str(uuid4())
    assignment = {
        "assignment_id": assignment_id,
        "tenant_id": tenant_id,
        "intake_id": intake_id,
        "status": "UNASSIGNED",
        "queue_key": plan.queue_key,
        "owner_subject_id": None,
        "owner_role": None,
        "due_at": due,
        "version": 1,
        "created_at": ts,
        "updated_at": ts,
        "correlation_id": correlation_id,
        "audit_event_id": assignment_audit_id,
    }
    sla = {
        "sla_instance_id": sla_id,
        "assignment_id": assignment_id,
        "tenant_id": tenant_id,
        "intake_id": intake_id,
        "state": "ON_TRACK",
        "policy_version": schedule.policy_version,
        "calendar_version": schedule.calendar_version,
        "started_at": ts,
        "due_soon_at": _iso(schedule.due_soon_at),
        "due_at": due,
        "breach_at": _iso(schedule.breach_at),
        "schedule": {k: _iso(v) if isinstance(v, datetime) else v for k, v in asdict(schedule).items()},
        "pause_intervals": [],
        "paused_duration_seconds": 0,
        "active_pause_interval_id": None,
        "version": 1,
        "audit_event_id": sla_audit_id,
        "correlation_id": correlation_id,
    }
    audits = []
    for audit_id, action, resource_type, resource_id, after in (
        (assignment_audit_id, "assignment.create", "assignment", assignment_id, assignment),
        (sla_audit_id, "sla.create", "sla_instance", sla_id, sla),
    ):
        audits.append({
            "audit_event_id": audit_id,
            "tenant_id": tenant_id,
            "intake_id": intake_id,
            "actor_subject_id": intake["submitted_by"],
            "service_principal": "intake_workflow",
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "after_value": deepcopy(after),
            "result": "SUCCEEDED",
            "correlation_id": correlation_id,
            "occurred_at": ts,
            "worm_state": "worm_pending",
        })
    # assignment.create is an audit action, not a new, uncatalogued event type.
    event_id = str(uuid4())
    event = {
        "event_id": event_id,
        "event_type": "sla.state_changed",
        "event_version": 1,
        "occurred_at": ts,
        "producer": "sla_service",
        "tenant_id": tenant_id,
        "aggregate_type": "sla_instance",
        "aggregate_id": sla_id,
        "aggregate_version": 1,
        "partition_key": f"{tenant_id}:{intake_id}",
        "correlation_id": correlation_id,
        "payload": {
            "sla_instance_id": sla_id,
            "intake_id": intake_id,
            "from_state": None,
            "to_state": "ON_TRACK",
            "due_at": due,
            "paused_duration_seconds": 0,
            "version": 1,
            "occurred_at": ts,
        },
    }
    store.assignments[assignment_id] = assignment
    store.slas[sla_id] = sla
    for audit in audits:
        store.workflow_audits[audit["audit_event_id"]] = audit
        intake.setdefault("audit", []).append({k: audit[k] for k in ("audit_event_id", "action", "occurred_at", "result")})
    store.workflow_outbox[event_id] = event
    intake.update({
        "correlation_id": correlation_id,
        "due_at": due,
        "assignment_id": assignment_id,
        "sla_instance_id": sla_id,
    })
    if record_submission:
        record_workflow_change(store, intake, resource_type="intake", before=None,
                               after=intake, action="intake.create", actor_id=intake["submitted_by"])
        enqueue_workflow_event(
            store, intake, event_type="intake.submitted", producer="listing_intake_service",
            aggregate_type="intake", aggregate_id=intake_id, aggregate_version=1,
            payload={
                "intake_id": intake_id, "intake_method": intake["intake_method"],
                "submitter_subject_id": intake["submitted_by"], "submitted_at": ts,
                "original_url": intake.get("original_url"), "source_id": intake.get("source_id"),
                "scope": deepcopy(intake["scope"]),
            },
        )
    return assignment_id, sla_id
