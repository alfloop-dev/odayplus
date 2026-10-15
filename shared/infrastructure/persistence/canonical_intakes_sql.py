"""Create real native Intake anchors without inventing processed evidence."""

from datetime import datetime
from uuid import UUID, uuid4

from shared.domain.events import DomainEvent


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
