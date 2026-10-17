"""Create real native Intake anchors without inventing processed evidence."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from modules.listing.application.intake_authorization import authorize_intake_action
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
    def __init__(self, graph):
        self.graph, self.engine = graph, graph.engine

    def get(self, tenant: str, intake_id: str) -> dict:
        UUID(intake_id)
        with self.graph.transaction(tenant):
            return self.graph._one(
                "SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? AND deleted_at IS NULL",
                (tenant, intake_id),
            )

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
            return {**receipt, "outbox_event_id": event.event_id}
