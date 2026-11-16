"""Create real native Intake anchors without inventing processed evidence."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

from modules.listing.application.intake_authorization import authorize_intake_action
from modules.listing.application.intake_workflow_bootstrap import (
    IntakeWorkflowUnavailable,
    initialize_intake_workflow,
    schedule_from_record,
)
from modules.listing.domain.identity_graph import IdentityConflictError, IdentityNotFoundError
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

# Catalogued payload contracts for the events the shared workflow commands emit.
_WORKFLOW_EVENT_SCHEMAS = {
    "assignment.assigned": "#/payloads/AssignmentChangedV1",
    "assignment.transferred": "#/payloads/AssignmentChangedV1",
    "assignment.claimed": "#/payloads/AssignmentStateChangedV1",
    "assignment.completed": "#/payloads/AssignmentStateChangedV1",
    "sla.state_changed": "#/payloads/SlaStateChangedV1",
}


def _instant(value) -> str | None:
    """Normalize one recorded aware instant to the shared command text form."""
    if value is None:
        return None
    at = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if at.utcoffset() is None:
        raise IntakeWorkflowUnavailable("Recorded workflow time is not timezone-aware")
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json_value(value):
    from json import loads

    return loads(value) if isinstance(value, str) else value


class _NativeWorkflowState(SimpleNamespace):
    """Request-local command surface for one locked native workflow.

    It mirrors the shared command store interface for exactly the recorded
    rows loaded in this SQL root. It is never flushed to durable_documents.
    """

    def canonical_audit_is_committed(self, audit) -> bool:
        return any(audit is loaded for loaded in self.committed_audits)


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

    def processing_history_span(self, tenant: str, intake_id: str, through: int | None = None) -> dict:
        """Bound the original transition rows, after the caller authorizes Intake.

        The HTTP owner retains the same SERIALIZABLE root and current-version
        fence. These are recorded sequences, never a reconstructed stage path.
        """
        with self.graph.transaction(tenant):
            return self.engine.query_one(
                "SELECT MIN(sequence_no) AS first_sequence,MAX(sequence_no) AS last_sequence "
                "FROM intake.intake_stage_transitions WHERE tenant_id=? AND intake_id=? "
                "AND (?::bigint IS NULL OR sequence_no<=?::bigint)", (tenant, intake_id, through, through))

    def processing_history(self, tenant: str, intake_id: str, *, through: int, position: int,
                           backward: bool, limit: int) -> list[dict]:
        """Project bounded metadata only, without raw reasons or command keys."""
        if type(limit) is not int or not 1 <= limit <= 26:
            raise ValueError("Native history read bound is required")
        comparison, order = ("<", "DESC") if backward else (">", "ASC")
        with self.graph.transaction(tenant):
            return self.engine.query(
                "SELECT transition_id,sequence_no,from_state,to_state,actor_subject_id,service_principal,"  # nosec B608 -- operator/order picked from two literals by bool; values bound as ?.
                "permission,reason_code,expected_version,resulting_version,job_id,source_policy_version,"
                "parser_release_id,source_snapshot_id,match_case_id,correlation_id,causation_id,occurred_at "
                "FROM intake.intake_stage_transitions WHERE tenant_id=? AND intake_id=? "
                f"AND sequence_no{comparison}? AND sequence_no<=? ORDER BY sequence_no {order} LIMIT ?",
                (tenant, intake_id, position, through, limit))

    def processing_evidence_position(self, tenant: str, intake_id: str, position, *, source_id, before) -> bool:
        """Validate a cursor's exact saved parent/reference, never a derived ID."""
        from modules.listing.application.native_intake_assisted_entry_request_proof import _time
        from modules.listing.application.native_intake_parser_runs import (
            CanonicalNativeParserRunRepository,
        )
        from shared.infrastructure.persistence.assisted_listing_intake import (
            NativeIntakeSnapshotMetadataRepository,
        )

        at, kind, reference = position
        with self.graph.transaction(tenant):
            if kind == "SNAPSHOT":
                row = NativeIntakeSnapshotMetadataRepository(self.graph).metadata_for_intake(
                    tenant, intake_id, reference, before=before)
                column = "stored_at"
            else:
                row = CanonicalNativeParserRunRepository(self.engine).metadata_for_intake(
                    tenant, intake_id, reference, before=before)
                column = "created_at"
            return row is not None and row["source_id"] == source_id and _time(row[column]) == at

    def processing_evidence(self, tenant: str, intake_id: str, *, through, position, backward, limit, before) -> list[dict]:
        """Merge bounded existing lifecycle records; no bodies or execution IO."""
        from modules.listing.application.native_intake_assisted_entry_request_proof import _time
        from modules.listing.application.native_intake_parser_runs import (
            CanonicalNativeParserRunRepository,
        )
        from shared.infrastructure.persistence.assisted_listing_intake import (
            NativeIntakeSnapshotMetadataRepository,
        )

        marker = None if position is None else (position[0].isoformat(), 0 if position[1] == "SNAPSHOT" else 1, position[2])
        with self.graph.transaction(tenant):
            runs = CanonicalNativeParserRunRepository(self.engine).metadata_page_for_intake(
                tenant, intake_id, through=through.isoformat(), position=marker, backward=backward, limit=limit, before=before)
            snapshots = NativeIntakeSnapshotMetadataRepository(self.graph).metadata_page_for_intake(
                tenant, intake_id, through=through.isoformat(), position=marker, backward=backward, limit=limit, before=before)
            records = [{"kind": "SNAPSHOT", "recorded_at": item["stored_at"], "metadata": item} for item in snapshots]
            records += [{"kind": "PARSER_RUN", "recorded_at": item["created_at"], "metadata": item} for item in runs]
            before.check_in_memory()
            records.sort(key=lambda item: (_time(item["recorded_at"]), 0 if item["kind"] == "SNAPSHOT" else 1,
                item["metadata"]["source_snapshot_id" if item["kind"] == "SNAPSHOT" else "parser_run_id"]), reverse=backward)
            return records[:limit]

    def parser_fields(self, intake, run_id, *, intake_version, run_version, before):
        """Read the selected actual run through its original complete proof.

        The caller first authorizes current HTTP view access and retains this
        same tenant SQL root. Recorded history never authorizes an action or
        supplies a saved Listing value.
        """
        from modules.listing.application.native_intake_parser_history import (
            NativeParserTerminalHistory,
        )
        from modules.listing.application.native_intake_parser_input import (
            NativeParserManualInputReader,
        )
        from modules.listing.application.native_intake_parser_job_proof import (
            parser_delivery_identity,
        )
        from modules.listing.application.native_intake_parser_policy import (
            JOB_TYPE,
            NativeParserConflict,
        )
        from modules.listing.application.native_intake_parser_runs import (
            CanonicalNativeParserRunRepository,
        )

        tenant, intake_id = intake["tenant_id"], intake["intake_id"]
        if intake["version"] != intake_version:
            raise IdentityConflictError("CANONICAL_INTAKE_VERSION_CONFLICT")
        metadata = CanonicalNativeParserRunRepository(self.engine).metadata_for_intake(
            tenant, intake_id, run_id, before=before)
        if metadata is None:
            raise IdentityNotFoundError("Canonical parser run not found")
        if metadata["source_id"] != intake["source_id"]:
            raise NativeParserConflict("NATIVE_PARSER_FIELD_LINEAGE_UNAVAILABLE")
        if metadata["version"] != run_version:
            raise IdentityConflictError("CANONICAL_PARSER_RUN_VERSION_CONFLICT")
        before()
        jobs = self.engine.query("SELECT job_id,status,version FROM workflow.jobs WHERE tenant_id=? "
            "AND job_type=? AND aggregate_type='parser_run' AND aggregate_id=? LIMIT 2", (tenant, JOB_TYPE, run_id))
        if len(jobs) != 1:
            raise NativeParserConflict("NATIVE_PARSER_FIELD_LINEAGE_UNAVAILABLE")
        before()
        delivery = self.graph.queue.get(jobs[0]["job_id"])
        if delivery is None:
            raise NativeParserConflict("NATIVE_PARSER_FIELD_LINEAGE_UNAVAILABLE")
        actual_tenant, generation = parser_delivery_identity(delivery)
        if actual_tenant != tenant:
            raise NativeParserConflict("NATIVE_PARSER_FIELD_LINEAGE_UNAVAILABLE")
        _, original, run = NativeParserTerminalHistory(self.graph).read_with_parent_input(
            delivery, tenant, generation, before=before)
        if (run["parser_run_id"] != run_id or run["version"] != run_version
                or run["intake_id"] != intake_id or run["source_snapshot_id"] != metadata["source_snapshot_id"]
                or original.lineage.source_id != intake["source_id"]):
            raise NativeParserConflict("NATIVE_PARSER_FIELD_LINEAGE_UNAVAILABLE")
        _, fields = NativeParserManualInputReader(self.graph).field_projection(
            original, run, before=before, include_observations=True)
        before()
        observed = self.engine.query_one("SELECT clock_timestamp() AS now")["now"]
        before.check_in_memory()
        return {"intake_id": intake_id, "intake_version": intake_version, "source_id": intake["source_id"],
            "parser_run_id": run_id, "parser_run_version": run_version, "status": run["status"],
            "parser_job_id": jobs[0]["job_id"], "parser_job_version": jobs[0]["version"],
            "job_status": jobs[0]["status"], "source_snapshot_id": run["source_snapshot_id"],
            "parser_release_id": run["parser_release_id"], "observed_at": observed, "fields": fields}

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

    def workflow_owner(self, tenant: str, *, intake_id: str | None = None, assignment_id: str | None = None,
                       sla_instance_id: str | None = None) -> str | None:
        """Return the owning native Intake only for an actual recorded native row.

        A UUID alone is not provenance. Absence means this resource is not a
        native workflow resource in the verified tenant; the caller keeps the
        retained legacy behaviour for it rather than guessing.
        """
        selected = [value for value in (intake_id, assignment_id, sla_instance_id) if value is not None]
        if len(selected) != 1:
            raise ValueError("Exactly one native workflow resource is required")
        UUID(selected[0])
        with self.graph.transaction(tenant):
            if intake_id is not None:
                row = self.engine.query_one("SELECT intake_id FROM intake.intakes WHERE tenant_id=? AND intake_id=? "
                    "AND deleted_at IS NULL", (tenant, intake_id))
            elif assignment_id is not None:
                row = self.engine.query_one("SELECT a.intake_id FROM workflow.assignments a JOIN intake.intakes i "
                    "ON i.tenant_id=a.tenant_id AND i.intake_id=a.intake_id AND i.deleted_at IS NULL "
                    "WHERE a.tenant_id=? AND a.assignment_id=?", (tenant, assignment_id))
            else:
                row = self.engine.query_one("SELECT s.intake_id FROM workflow.sla_instances s JOIN intake.intakes i "
                    "ON i.tenant_id=s.tenant_id AND i.intake_id=s.intake_id AND i.deleted_at IS NULL "
                    "WHERE s.tenant_id=? AND s.sla_instance_id=?", (tenant, sla_instance_id))
            return None if row is None else row["intake_id"]

    def workflow_clock(self, tenant: str) -> datetime:
        """Use the same database clock as the native SQL owners."""
        with self.graph.transaction(tenant):
            at = self.engine.query_one("SELECT clock_timestamp() AS at")["at"]
            return datetime.fromisoformat(at).astimezone(UTC)

    def workflow_command_state(self, tenant: str, intake_id: str) -> _NativeWorkflowState:
        """Lock one complete recorded native workflow into the shared command shape.

        The caller holds this graph root for the whole command, replay receipt
        and flush. Incomplete or historical rows without recorded facts stay
        unavailable; nothing is defaulted from current policy or documents.
        """
        UUID(intake_id)
        with self.graph.transaction(tenant):
            intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? "
                "AND deleted_at IS NULL", (tenant, intake_id))
            assignments = self.engine.query("SELECT * FROM workflow.assignments WHERE tenant_id=? AND intake_id=? "
                "ORDER BY assignment_id FOR UPDATE", (tenant, intake_id))
            slas = self.engine.query("SELECT * FROM workflow.sla_instances WHERE tenant_id=? AND intake_id=? "
                "FOR UPDATE", (tenant, intake_id))
            if len(assignments) != 1 or len(slas) != 1:
                raise IntakeWorkflowUnavailable("Native Assignment/SLA workflow is not recorded")
            assignment, sla = assignments[0], slas[0]
            if (sla["assignment_id"] != assignment["assignment_id"]
                    or any(sla[key] is None for key in ("calendar_version", "due_soon_at", "breach_at"))):
                raise IntakeWorkflowUnavailable("Historical native workflow binding is unavailable")
            intervals = self.engine.query("SELECT * FROM workflow.sla_pause_intervals WHERE tenant_id=? "
                "AND sla_instance_id=? ORDER BY paused_at,sla_pause_interval_id FOR UPDATE",
                (tenant, sla["sla_instance_id"]))
            if any(item["expected_resume_at"] is None for item in intervals):
                # Never infer an old interval's expectation from its actual
                # resume, the current SLA resume_at or today's calendar.
                raise IntakeWorkflowUnavailable("Historical pause expectation is unavailable")
            scope = {"tenant_id": tenant, **{key: intake[key] for key in (
                "brand_id", "region_id", "assigned_area_id", "heat_zone_id")}}
            pauses = [{"pause_interval_id": item["sla_pause_interval_id"], "sla_instance_id": sla["sla_instance_id"],
                "tenant_id": tenant, "paused_at": _instant(item["paused_at"]),
                "expected_resume_at": _instant(item["expected_resume_at"]),
                "resumed_at": _instant(item["resumed_at"]), "reason": item["reason"],
                "approved_by": item["approved_by"], "created_by": item["created_by"],
                "version": item["version"]} for item in intervals]
            active = [item for item in pauses if item["resumed_at"] is None]
            if active and (_instant(sla["paused_at"]) != active[0]["paused_at"]
                           or _instant(sla["resume_at"]) != active[0]["expected_resume_at"]):
                raise IntakeWorkflowUnavailable("Recorded active pause differs from its SLA")
            if not active and (sla["paused_at"] is not None or sla["resume_at"] is not None):
                raise IntakeWorkflowUnavailable("Recorded SLA pause has no interval")
            schedule = {"tenant_id": tenant, "policy_version": sla["policy_version"],
                "calendar_version": sla["calendar_version"], **{key: _instant(sla[key]) for key in (
                    "started_at", "due_soon_at", "due_at", "breach_at")}}
            sla_shape = {"sla_instance_id": sla["sla_instance_id"], "assignment_id": sla["assignment_id"],
                "tenant_id": tenant, "intake_id": intake_id, "state": sla["state"],
                "policy_version": sla["policy_version"], "calendar_version": sla["calendar_version"],
                **{key: schedule[key] for key in ("started_at", "due_soon_at", "due_at", "breach_at")},
                "completed_at": _instant(sla["completed_at"]), "schedule": schedule, "pause_intervals": pauses,
                "paused_duration_seconds": int(sum(
                    (datetime.fromisoformat(item["resumed_at"].replace("Z", "+00:00"))
                     - datetime.fromisoformat(item["paused_at"].replace("Z", "+00:00"))).total_seconds()
                    for item in pauses if item["resumed_at"] is not None)),
                "active_pause_interval_id": active[0]["pause_interval_id"] if active else None,
                "version": sla["version"], "correlation_id": intake["correlation_id"]}
            assignment_shape = {key: assignment[key] for key in (
                "assignment_id", "tenant_id", "intake_id", "status", "queue_key", "owner_subject_id",
                "owner_role", "previous_owner_subject_id", "assigned_by", "handoff_note", "version")}
            assignment_shape.update({"due_at": _instant(assignment["due_at"]),
                "created_at": _instant(assignment["created_at"]), "updated_at": _instant(assignment["updated_at"]),
                "correlation_id": intake["correlation_id"]})
            intake_shape = {"intake_id": intake_id, "tenant_id": tenant, "scope": scope,
                "correlation_id": intake["correlation_id"], "submitted_by": intake["submitter_subject_id"],
                "state": intake["processing_state"], "version": intake["version"],
                "assigned_to": assignment["owner_subject_id"], "due_at": assignment_shape["due_at"],
                "audit": [], "processing_history": []}
            committed = []
            # The original completion evaluator reads actual committed work.
            # Only the canonical audit that recorded this current Intake
            # version can prove its state; none is synthesized here.
            audits = self.engine.query("SELECT audit_event_id,tenant_id,action,resource_type,resource_id,result,"
                "after_value FROM audit.audit_events WHERE tenant_id=? AND resource_type='intake' AND resource_id=? "
                "AND result='SUCCEEDED' AND after_value->>'version'=? ORDER BY sequence_no DESC LIMIT 1",
                (tenant, intake_id, str(intake["version"])))
            for audit in audits:
                committed.append({**audit, "intake_id": audit["resource_id"],
                                  "after_value": _json_value(audit["after_value"])})
            if committed:
                intake_shape["audit_event_id"] = committed[0]["audit_event_id"]
            decisions = self.engine.query("SELECT d.match_decision_id AS decision_id,c.intake_id,d.tenant_id,"
                "d.status,d.version,d.decision_type AS action FROM identity.match_decisions d "
                "JOIN identity.match_cases c ON c.tenant_id=d.tenant_id AND c.match_case_id=d.match_case_id "
                "WHERE d.tenant_id=? AND c.intake_id=?", (tenant, intake_id))
            corrections = self.engine.query("SELECT correction_id,intake_id,tenant_id,status,version "
                "FROM intake.human_corrections WHERE tenant_id=? AND intake_id=?", (tenant, intake_id))
            promotions = self.engine.query("SELECT promotion_decision_id,intake_id,tenant_id,status,version "
                "FROM expansion.promotion_decisions WHERE tenant_id=? AND intake_id=?", (tenant, intake_id))
            jobs = self.engine.query("SELECT job_id,aggregate_id AS intake_id,tenant_id,status,version,job_type "
                "FROM workflow.jobs WHERE tenant_id=? AND aggregate_type='intake' AND aggregate_id=?",
                (tenant, intake_id))
            return _NativeWorkflowState(
                intake=intake, intakes={intake_id: intake_shape},
                assignments={assignment["assignment_id"]: assignment_shape},
                slas={sla["sla_instance_id"]: sla_shape},
                decisions={row["decision_id"]: row for row in decisions},
                corrections={row["correction_id"]: row for row in corrections},
                promotions={row["promotion_decision_id"]: row for row in promotions},
                jobs={row["job_id"]: row for row in jobs},
                workflow_audits={audit["audit_event_id"]: audit for audit in committed},
                committed_audits=tuple(committed), workflow_outbox={},
                baseline={"assignments": deepcopy({assignment["assignment_id"]: assignment_shape}),
                          "slas": deepcopy({sla["sla_instance_id"]: sla_shape})})

    def persist_workflow_command(self, state: _NativeWorkflowState, *, actor: str, reason: str,
                                 idempotency_key: str, at: datetime) -> None:
        """Write one shared command's result to the original normalized rows.

        Assignment/SLA rows, transitions, pause intervals, canonical audit and
        outbox commit with the caller's replay receipt in this same SQL root.
        The native Intake row is the processing fence for its pipeline workers
        and is not changed by workflow commands; its Intake-resource evidence
        from the shared command therefore has no native write to describe.
        """
        tenant = state.intake["tenant_id"]
        intake_id = state.intake["intake_id"]
        scope = state.intakes[intake_id]["scope"]
        when = _instant(at)
        with self.graph.transaction(tenant):
            CanonicalAuditBacklog(self.engine).require_available(tenant)
            for assignment_id, after in state.assignments.items():
                before = state.baseline["assignments"][assignment_id]
                if after["version"] == before["version"]:
                    continue
                if after["version"] <= before["version"] or after["intake_id"] != intake_id:
                    raise IntakeWorkflowUnavailable("Assignment command did not advance its version")
                if self.engine.execute(
                    "UPDATE workflow.assignments SET status=?,owner_subject_id=?,owner_role=?,"
                    "previous_owner_subject_id=?,assigned_by=?,handoff_note=?,queue_key=?,due_at=?,version=?,updated_at=? "
                    "WHERE tenant_id=? AND intake_id=? AND assignment_id=? AND version=?",
                    (after["status"], after["owner_subject_id"], after["owner_role"],
                     before["owner_subject_id"] if after["owner_subject_id"] != before["owner_subject_id"]
                     and before["owner_subject_id"] is not None else before["previous_owner_subject_id"],
                     actor if before["status"] == "UNASSIGNED" and after["status"] != "UNASSIGNED"
                     else before["assigned_by"], after.get("handoff_note"), after["queue_key"], after["due_at"],
                     after["version"], when, tenant, intake_id, assignment_id, before["version"]),
                ).rowcount != 1:
                    raise IdentityConflictError("NATIVE_ASSIGNMENT_VERSION_CONFLICT")
            for sla_id, after in state.slas.items():
                before = state.baseline["slas"][sla_id]
                if after["version"] == before["version"]:
                    continue
                if after["version"] <= before["version"]:
                    raise IntakeWorkflowUnavailable("SLA command did not advance its version")
                known = {item["pause_interval_id"]: item for item in before["pause_intervals"]}
                for item in after["pause_intervals"]:
                    prior = known.get(item["pause_interval_id"])
                    if prior is None:
                        UUID(item["pause_interval_id"])
                        self.engine.execute(
                            "INSERT INTO workflow.sla_pause_intervals (sla_pause_interval_id,tenant_id,sla_instance_id,"
                            "paused_at,expected_resume_at,resumed_at,reason,approved_by,created_by,version) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (item["pause_interval_id"], tenant, sla_id, item["paused_at"], item["expected_resume_at"],
                             item["resumed_at"], item["reason"], item["approved_by"], item["created_by"],
                             item["version"]))
                    elif item["resumed_at"] != prior["resumed_at"]:
                        if (prior["resumed_at"] is not None or item["version"] != prior["version"] + 1
                                or any(item[key] != prior[key] for key in (
                                    "paused_at", "expected_resume_at", "reason", "approved_by", "created_by"))):
                            raise IntakeWorkflowUnavailable("Recorded pause interval cannot be rewritten")
                        if self.engine.execute(
                            "UPDATE workflow.sla_pause_intervals SET resumed_at=?,version=? WHERE tenant_id=? "
                            "AND sla_instance_id=? AND sla_pause_interval_id=? AND version=? AND resumed_at IS NULL",
                            (item["resumed_at"], item["version"], tenant, sla_id, item["pause_interval_id"],
                             prior["version"]),
                        ).rowcount != 1:
                            raise IdentityConflictError("NATIVE_SLA_PAUSE_VERSION_CONFLICT")
                if {item["pause_interval_id"] for item in after["pause_intervals"]} < set(known):
                    raise IntakeWorkflowUnavailable("Recorded pause history cannot be removed")
                active = [item for item in after["pause_intervals"] if item["resumed_at"] is None]
                if self.engine.execute(
                    "UPDATE workflow.sla_instances SET state=?,due_soon_at=?,due_at=?,breach_at=?,paused_at=?,"
                    "resume_at=?,completed_at=?,version=? WHERE tenant_id=? AND intake_id=? AND sla_instance_id=? "
                    "AND version=?",
                    (after["state"], after["due_soon_at"], after["due_at"], after["breach_at"],
                     active[0]["paused_at"] if active else None, active[0]["expected_resume_at"] if active else None,
                     after.get("completed_at"), after["version"], tenant, intake_id, sla_id, before["version"]),
                ).rowcount != 1:
                    raise IdentityConflictError("NATIVE_SLA_VERSION_CONFLICT")
            for audit in state.workflow_audits.values():
                if any(audit is loaded for loaded in state.committed_audits):
                    continue
                kind, before, after = audit["resource_type"], audit["before_value"], audit["after_value"]
                if kind == "intake":
                    continue
                if kind == "assignment":
                    sequence = self.engine.query_one("SELECT COALESCE(MAX(sequence_no),0)+1 AS next FROM "
                        "workflow.assignment_transitions WHERE tenant_id=? AND assignment_id=?",
                        (tenant, audit["resource_id"]))["next"]
                    self.engine.execute(
                        "INSERT INTO workflow.assignment_transitions (assignment_transition_id,tenant_id,assignment_id,"
                        "sequence_no,from_status,to_status,actor_subject_id,permission,reason,owner_before,owner_after,"
                        "expected_version,resulting_version,idempotency_key,correlation_id,occurred_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (str(uuid4()), tenant, audit["resource_id"], sequence, before["status"], after["status"],
                         audit["actor_subject_id"], audit["action"], reason,
                         before["owner_subject_id"], after["owner_subject_id"], before["version"], after["version"],
                         idempotency_key, audit["correlation_id"], when))
                elif kind == "sla_instance":
                    sequence = self.engine.query_one("SELECT COALESCE(MAX(sequence_no),0)+1 AS next FROM "
                        "workflow.sla_transitions WHERE tenant_id=? AND sla_instance_id=?",
                        (tenant, audit["resource_id"]))["next"]
                    self.engine.execute(
                        "INSERT INTO workflow.sla_transitions (sla_transition_id,tenant_id,sla_instance_id,sequence_no,"
                        "from_state,to_state,actor_subject_id,permission,reason,due_at_before,due_at_after,"
                        "expected_version,resulting_version,idempotency_key,correlation_id,occurred_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (str(uuid4()), tenant, audit["resource_id"], sequence, before["state"], after["state"],
                         audit["actor_subject_id"], audit["action"], reason,
                         before["due_at"], after["due_at"], before["version"], after["version"],
                         idempotency_key, audit["correlation_id"], when))
                else:
                    raise IntakeWorkflowUnavailable("Workflow command produced unsupported evidence")
                self.graph.audit_sink.persist(audit, scope)
            for value in state.workflow_outbox.values():
                self.graph._outbox(DomainEvent(
                    **{**value, "occurred_at": datetime.fromisoformat(value["occurred_at"].replace("Z", "+00:00"))},
                    actor_ref=actor, policy_version=state.slas[next(iter(state.slas))]["policy_version"],
                    schema_ref=_WORKFLOW_EVENT_SCHEMAS[value["event_type"]],
                ))

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
