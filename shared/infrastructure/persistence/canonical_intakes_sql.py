"""Create real native Intake anchors without inventing processed evidence."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

from modules.listing.application.intake_authorization import authorize_intake_action
from modules.listing.application.intake_workflow_bootstrap import (
    IntakeWorkflowUnavailable,
    initialize_intake_workflow,
    schedule_from_record,
)
from modules.listing.domain.identity_graph import IdentityConflictError
from modules.listing.domain.intake_states import (
    Actor,
    IntakeAggregate,
    IntakeStage,
    IntakeStateMachine,
    PrincipalRole,
    TransitionContext,
)
from shared.audit.backlog import CanonicalAuditBacklog
from shared.auth import Principal, Role
from shared.domain.events import DomainEvent
from shared.jobs.queue import JobStatus


class CanonicalIntakeRepository:
    def __init__(self, graph, workflow_policy=None):
        self.graph, self.engine = graph, graph.engine
        self.workflow_policy = workflow_policy

    def get(self, tenant: str, intake_id: str) -> dict:
        UUID(intake_id)
        with self.graph.transaction(tenant):
            return self.graph._one(
                "SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? AND deleted_at IS NULL",
                (tenant, intake_id),
            )

    def workflow(self, tenant: str, intake_id: str) -> dict | None:
        """Read recorded workflow facts after the caller authorizes its Intake.

        Older native rows without workflow remain unknown. A partial binding is
        unavailable; neither current policy nor a legacy document fills it in.
        """
        with self.graph.transaction(tenant):
            row = self.engine.query_one(
                "SELECT a.assignment_id,a.version AS assignment_version,a.status AS assignment_status,"
                "a.owner_subject_id,a.owner_role,a.queue_key,a.due_at AS assignment_due_at,"
                "s.tenant_id,s.assignment_id AS bound_assignment_id,s.sla_instance_id,s.version AS sla_version,s.state AS sla_state,"
                "s.policy_version,s.calendar_version,s.started_at,s.due_soon_at,s.due_at,s.breach_at "
                "FROM workflow.sla_instances s LEFT JOIN workflow.assignments a "
                "ON a.tenant_id=s.tenant_id AND a.intake_id=s.intake_id AND a.assignment_id=s.assignment_id "
                "WHERE s.tenant_id=? AND s.intake_id=?", (tenant, intake_id),
            )
            if row is None:
                if self.engine.query_one("SELECT 1 FROM workflow.assignments WHERE tenant_id=? AND intake_id=? LIMIT 1",
                                         (tenant, intake_id)) is not None:
                    raise IntakeWorkflowUnavailable("Native workflow binding is incomplete")
                return None
            if row["bound_assignment_id"] is None and all(row[key] is None for key in (
                "calendar_version", "due_soon_at", "breach_at",
            )):
                # The additive migration deliberately leaves this historical
                # binding unknown. Keep the authorized Intake detail readable
                # without substituting today's policy or an old document.
                return None
            schedule = schedule_from_record({"schedule": {key: row[key] for key in (
                "tenant_id", "policy_version", "calendar_version", "started_at", "due_soon_at", "due_at", "breach_at",
            )}})
            if (row["assignment_id"] is None or row["assignment_due_at"] is None
                    or datetime.fromisoformat(row["assignment_due_at"]) != schedule.due_at):
                raise IntakeWorkflowUnavailable("Native Assignment/SLA binding differs")
            return {key: value for key, value in row.items()
                    if key not in {"tenant_id", "assignment_due_at", "bound_assignment_id"}}

    def _initialize_workflow(self, row: dict, *, reason: str, idempotency_key: str) -> None:
        """Persist the shared bootstrap in this native submission's SQL unit.

        This request-local assembly is never flushed to durable_documents. Its
        policy, initial facts and evidence come from the existing bootstrap.
        """
        state = SimpleNamespace(assignments={}, slas={}, workflow_audits={}, workflow_outbox={})
        tenant, intake_id, actor = row["tenant_id"], row["intake_id"], row["submitter_subject_id"]
        scope = {"tenant_id": tenant, **{key: row[key] for key in (
            "brand_id", "region_id", "assigned_area_id", "heat_zone_id",
        )}}
        context = {"intake_id": intake_id, "scope": scope, "submitted_by": actor,
                   "submitted_at": row["submitted_at"], "correlation_id": row["correlation_id"]}
        assignment_id, sla_id = initialize_intake_workflow(
            state, context, self.workflow_policy, record_submission=False,
        )
        assignment, sla = state.assignments[assignment_id], state.slas[sla_id]
        self.engine.execute(
            "INSERT INTO workflow.assignments (assignment_id,tenant_id,intake_id,status,queue_key,"
            "owner_subject_id,owner_role,due_at,version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            tuple(assignment[key] for key in ("assignment_id", "tenant_id", "intake_id", "status", "queue_key",
                "owner_subject_id", "owner_role", "due_at", "version", "created_at", "updated_at")),
        )
        self.engine.execute(
            "INSERT INTO workflow.sla_instances (sla_instance_id,tenant_id,intake_id,assignment_id,policy_version,"
            "calendar_version,state,started_at,due_soon_at,due_at,breach_at,version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            tuple(sla[key] for key in ("sla_instance_id", "tenant_id", "intake_id", "assignment_id", "policy_version",
                "calendar_version", "state", "started_at", "due_soon_at", "due_at", "breach_at", "version")),
        )
        at, correlation = row["submitted_at"], row["correlation_id"]
        self.engine.execute(
            "INSERT INTO workflow.assignment_transitions (assignment_transition_id,tenant_id,assignment_id,"
            "sequence_no,from_status,to_status,actor_subject_id,service_principal,permission,reason,"
            "resulting_version,idempotency_key,correlation_id,occurred_at) "
            "VALUES (?,?,?,1,NULL,?,?,'intake_workflow','assignment.create',?,1,?,?,?)",
            (str(uuid4()), tenant, assignment_id, assignment["status"], actor, reason, idempotency_key, correlation, at),
        )
        self.engine.execute(
            "INSERT INTO workflow.sla_transitions (sla_transition_id,tenant_id,sla_instance_id,sequence_no,"
            "from_state,to_state,actor_subject_id,service_principal,permission,reason,due_at_after,"
            "resulting_version,idempotency_key,correlation_id,occurred_at) "
            "VALUES (?,?,?,1,NULL,?,?,'intake_workflow','sla.create',?,?,1,?,?,?)",
            (str(uuid4()), tenant, sla_id, sla["state"], actor, reason, sla["due_at"], idempotency_key, correlation, at),
        )
        for audit in state.workflow_audits.values():
            self.graph.audit_sink.persist(audit, scope)
        for value in state.workflow_outbox.values():
            self.graph._outbox(DomainEvent(
                **{**value, "occurred_at": datetime.fromisoformat(value["occurred_at"])},
                actor_ref=actor, policy_version=sla["policy_version"], schema_ref="#/payloads/SlaStateChangedV1",
            ))

    def page(self, tenant: str, *, scope, owner: str | None, state: str | None,
             before: tuple[str, str] | None, limit: int) -> list[dict]:
        """Project current native scope/ownership before applying a page limit.

        The route still authorizes every returned row, including the lookahead.
        A cursor is an untrusted position, never a tenant or permission grant.
        Native Intakes have no store axis, so a store-restricted principal has
        no representable row rather than receiving unscoped metadata.
        """
        from json import dumps

        with self.graph.transaction(tenant):
            return self.engine.query(
                "SELECT intake_id,tenant_id,submitter_subject_id,intake_method,processing_state,version,"
                "submitted_at,correlation_id,brand_id,region_id,assigned_area_id,heat_zone_id "
                "FROM intake.intakes WHERE tenant_id=? AND deleted_at IS NULL "
                "AND ?::boolean AND (?::uuid IS NULL OR submitter_subject_id=?::uuid) "
                "AND (?::boolean OR jsonb_exists(?::jsonb,brand_id::text)) "
                "AND (?::boolean OR jsonb_exists(?::jsonb,region_id::text)) "
                "AND (?::boolean OR jsonb_exists(?::jsonb,assigned_area_id::text)) "
                "AND (?::boolean OR jsonb_exists(?::jsonb,heat_zone_id::text)) "
                "AND (?::text IS NULL OR processing_state=?::text) "
                "AND (?::timestamptz IS NULL OR (submitted_at,intake_id)<(?::timestamptz,?::uuid)) "
                "ORDER BY submitted_at DESC,intake_id DESC LIMIT ?",
                (tenant, not scope.store_ids, owner, owner,
                 not scope.brand_ids, dumps(sorted(scope.brand_ids)),
                 not scope.region_ids, dumps(sorted(scope.region_ids)),
                 not scope.assigned_area_ids, dumps(sorted(scope.assigned_area_ids)),
                 not scope.heat_zone_ids, dumps(sorted(scope.heat_zone_ids)), state, state,
                 before[0] if before else None, before[0] if before else None, before[1] if before else None, limit),
            )

    def cancellation_context(self, principal: Principal, intake_id: str) -> dict:
        """Lock and authorize the current native row, including for replay."""
        UUID(intake_id)
        with self.graph.transaction(principal.tenant_id):
            row = self.graph._one(
                "SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? "
                "AND deleted_at IS NULL FOR UPDATE", (principal.tenant_id, intake_id),
            )
            authorize_intake_action(principal, "cancel",
                resource={**row, "submitter": row["submitter_subject_id"]})
            return row

    def cancel_authorization_wait(self, principal: Principal, intake_id: str, *,
                                  expected_version: int, reason: str, idempotency_key: str) -> dict:
        """Cancel a settled authorization wait without granting source access.

        The HTTP owner persists its idempotency receipt in this same SQL root.
        Job rows are read under SERIALIZABLE, never locked after the Intake.
        """
        if not reason.strip() or not idempotency_key:
            raise ValueError("Explicit cancellation intent is required")
        tenant, actor = principal.tenant_id, principal.subject_id
        UUID(actor)
        with self.graph.transaction(tenant):
            row = self.cancellation_context(principal, intake_id)
            if (row["processing_state"] != "AWAITING_SOURCE_AUTHORIZATION"
                    or row["source_policy_state"] != "AUTH_REQUIRED"
                    or row["version"] != expected_version):
                raise IdentityConflictError("CANNOT_CANCEL_AUTHORIZATION_WAIT")
            if (self.engine.query_one("SELECT 1 FROM identity.match_cases WHERE tenant_id=? AND intake_id=? LIMIT 1",
                                      (tenant, intake_id)) is not None
                    or self.engine.query_one("SELECT 1 FROM expansion.promotion_decisions WHERE tenant_id=? AND intake_id=? LIMIT 1",
                                             (tenant, intake_id)) is not None):
                raise IdentityConflictError("CANNOT_CANCEL_AUTHORIZATION_WAIT")
            jobs = self.engine.query(
                "SELECT j.job_type,j.status,d.job_id AS delivery_id,d.status AS delivery_status,d.delivery_state "
                "FROM workflow.jobs j LEFT JOIN durable_jobs d ON d.job_id::text=j.job_id::text "
                "WHERE j.tenant_id=? AND j.aggregate_type='intake' AND j.aggregate_id=?", (tenant, intake_id),
            )
            if (not any(j["job_type"] == "native-intake-source-policy" for j in jobs)
                    or any(j["status"] != "SUCCEEDED" or j["delivery_id"] is None
                           or j["delivery_status"] != JobStatus.SUCCEEDED.value or j["delivery_state"] is not None for j in jobs)):
                raise IdentityConflictError("AUTHORIZATION_WAIT_JOBS_NOT_SETTLED")
            if principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE):
                role = PrincipalRole.EXPANSION_MANAGER
            elif principal.has_role(Role.DATA_OWNER):
                role = PrincipalRole.DATA_STEWARD
            else:
                role = PrincipalRole.EXPANSION_STAFF
            aggregate = IntakeAggregate(id=intake_id, tenant_id=tenant, stage=IntakeStage(row["processing_state"]),
                version=row["version"], created_by=row["submitter_subject_id"], source_id=row["source_id"],
                owner_id=row["submitter_subject_id"])
            at = datetime.fromisoformat(self.engine.query_one("SELECT clock_timestamp() AS at")["at"]).astimezone(UTC)
            at = at.isoformat().replace("+00:00", "Z")
            IntakeStateMachine.transition(aggregate, IntakeStage.CANCELLED, TransitionContext(
                actor=Actor(actor, role, tenant), idempotency_key=idempotency_key,
                version_before=expected_version, reason=reason, correlation_id=row["correlation_id"],
                current_time=datetime.fromisoformat(at)))
            CanonicalAuditBacklog(self.engine).require_available(tenant)
            transition_id, audit_id = str(uuid4()), str(uuid4())
            if self.engine.execute(
                "UPDATE intake.intakes SET processing_state='CANCELLED',version=?,last_transition_at=? "
                "WHERE tenant_id=? AND intake_id=? AND version=? AND processing_state='AWAITING_SOURCE_AUTHORIZATION' "
                "AND source_policy_state='AUTH_REQUIRED' AND deleted_at IS NULL",
                (aggregate.version, at, tenant, intake_id, expected_version),
            ).rowcount != 1:
                raise IdentityConflictError("CANNOT_CANCEL_AUTHORIZATION_WAIT")
            sequence = self.engine.query_one("SELECT COALESCE(MAX(sequence_no),0)+1 AS next "
                "FROM intake.intake_stage_transitions WHERE tenant_id=? AND intake_id=?", (tenant, intake_id))["next"]
            self.engine.execute(
                "INSERT INTO intake.intake_stage_transitions (transition_id,tenant_id,intake_id,sequence_no,"
                "from_state,to_state,actor_subject_id,permission,reason_text,idempotency_key,expected_version,"
                "resulting_version,correlation_id,occurred_at) "
                "VALUES (?,?,?,?,'AWAITING_SOURCE_AUTHORIZATION','CANCELLED',?,'intake.cancel',?,?,?,?,?,?)",
                (transition_id, tenant, intake_id, sequence, actor, reason, idempotency_key,
                 expected_version, aggregate.version, row["correlation_id"], at),
            )
            event = DomainEvent(event_type="intake.state_changed", producer="listing_intake_service",
                tenant_id=tenant, aggregate_type="intake", aggregate_id=intake_id, aggregate_version=aggregate.version,
                partition_key=tenant + ":" + intake_id, correlation_id=row["correlation_id"], actor_ref=actor,
                occurred_at=datetime.fromisoformat(at), schema_ref="#/payloads/IntakeStateChangedV1",
                payload={"intake_id": intake_id, "from_state": "AWAITING_SOURCE_AUTHORIZATION", "to_state": "CANCELLED",
                         "transition_id": transition_id, "version": aggregate.version, "occurred_at": at})
            receipt = {"intake_id": intake_id, "processing_state": "CANCELLED", "version": aggregate.version,
                "cancelled_at": at, "correlation_id": row["correlation_id"], "transition_id": transition_id,
                "audit_event_id": audit_id, "outbox_event_id": event.event_id}
            self.graph.audit_sink.persist({"audit_event_id": audit_id, "tenant_id": tenant, "resource_type": "intake",
                "resource_id": intake_id, "action": "intake.cancelled.v1", "result": "SUCCEEDED",
                "actor_subject_id": actor, "correlation_id": row["correlation_id"], "occurred_at": at,
                "before_value": {"intake_id": intake_id, "processing_state": row["processing_state"], "version": expected_version},
                "after_value": receipt, "reason": reason},
                {"tenant_id": tenant, **{key: row[key] for key in ("brand_id", "region_id", "assigned_area_id", "heat_zone_id")}})
            self.graph._outbox(event)
            return receipt

    def submit(self, tenant: str, actor: str, *, method: str, original_url: str | None,
               scope: dict, reason: str, idempotency_key: str) -> dict:
        """Commit SUBMITTED, its transition, audit and event in one SQL unit.

        Admission is not retrieval, parsing, matching or execution. In particular
        the submitted URL is not asserted to be canonical, no source/policy is
        guessed, and no match case, Listing or worker-success receipt is made.
        The HTTP owner commits its idempotency receipt in this same root unit.
        """
        UUID(actor)
        if (method not in {"URL", "MANUAL"} or not reason.strip() or not idempotency_key
                or (method == "URL") != (original_url is not None)):
            raise ValueError("Explicit Intake method and submission intent are required")
        scope = {key: scope.get(key) for key in ("brand_id", "region_id", "assigned_area_id", "heat_zone_id")}
        for value in scope.values():
            if value is not None:
                UUID(value)
        with self.graph.transaction(tenant):
            intake_id, correlation, transition_id, audit_id = (str(uuid4()) for _ in range(4))
            row = self.engine.query_one(
                "INSERT INTO intake.intakes (intake_id,tenant_id,submitter_subject_id,intake_method,original_url,"
                "brand_id,region_id,assigned_area_id,heat_zone_id,processing_state,correlation_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,'SUBMITTED',?) RETURNING *",
                (intake_id, tenant, actor, method, original_url, scope["brand_id"], scope["region_id"],
                 scope["assigned_area_id"], scope["heat_zone_id"], correlation),
            )
            at, version = row["submitted_at"], row["version"]
            self.engine.execute(
                "INSERT INTO intake.intake_stage_transitions (transition_id,tenant_id,intake_id,sequence_no,"
                "from_state,to_state,actor_subject_id,permission,reason_text,idempotency_key,resulting_version,"
                "correlation_id,occurred_at) VALUES (?,?,?,1,NULL,'SUBMITTED',?,'intake.create',?,?,?,?,?)",
                (transition_id, tenant, intake_id, actor, reason, idempotency_key, version, correlation, at),
            )
            receipt = {"intake_id": intake_id, "processing_state": row["processing_state"], "version": version,
                       "intake_method": method, "submitted_at": at, "correlation_id": correlation,
                       "transition_id": transition_id, "audit_event_id": audit_id}
            native_scope = {"tenant_id": tenant, **scope}
            self.graph.audit_sink.persist({
                "audit_event_id": audit_id, "tenant_id": tenant, "resource_type": "intake", "resource_id": intake_id,
                "action": "intake.create", "result": "SUCCEEDED", "actor_subject_id": actor,
                "correlation_id": correlation, "occurred_at": at, "before_value": None,
                "after_value": receipt, "reason": reason,
            }, native_scope)
            event = DomainEvent(
                event_type="intake.submitted", tenant_id=tenant, aggregate_type="intake", aggregate_id=intake_id,
                aggregate_version=version, partition_key=tenant + ":" + intake_id, correlation_id=correlation,
                producer="listing_intake_service", occurred_at=datetime.fromisoformat(at), actor_ref=actor,
                schema_ref="#/payloads/IntakeSubmittedV1",
                sensitive_fields=["payload.original_url"],
                payload={"intake_id": intake_id, "intake_method": method, "submitter_subject_id": actor,
                         "submitted_at": at, "scope": native_scope},
            )
            self.graph._outbox(event)
            self._initialize_workflow(row, reason=reason, idempotency_key=idempotency_key)
            return {**receipt, "outbox_event_id": event.event_id}
