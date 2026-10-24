"""Atomic validated parser output under fresh authority and both owner fences.

Only real completed read/child journals may produce facts. This component
provides no retry, historical replay or default-worker settlement policy.
"""
from __future__ import annotations

import json
import re
from uuid import uuid4

from modules.external_data.application.native_parser_output import (
    NativeParserOutput,
    validate_native_manual_parser_output,
)
from modules.listing.application.native_intake_assisted_entry_request_proof import _time
from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_parser_attempts import (
    NativeParserAttemptHistory,
    active_payload_fields,
)
from modules.listing.application.native_intake_parser_execution import NativeParserExecutionReads
from modules.listing.application.native_intake_parser_io import (
    OPERATIONS,
    NativeParserObservedOutput,
)
from modules.listing.application.native_intake_parser_job_proof import parser_admission_provenance
from modules.listing.application.native_intake_parser_journal import INTENT_FIELDS as _INTENT_FIELDS
from modules.listing.application.native_intake_parser_journal import (
    closed_parser_attempt_journal,
    combine_closed_parser_journals,
)
from modules.listing.application.native_intake_parser_leases import NativeParserCompositeLeases
from modules.listing.application.native_intake_parser_policy import JOB_TYPE, NativeParserConflict
from modules.listing.application.native_intake_retrieval_policy import (
    canonical_json,
    canonical_sha256,
)
from modules.listing.domain.intake_states import (
    Actor,
    IntakeAggregate,
    IntakeStage,
    IntakeStateMachine,
    PrincipalRole,
    TransitionContext,
)
from shared.audit.backlog import CanonicalAuditBacklog
from shared.domain.events import DomainEvent
from shared.jobs.queue import JobFenceRejectedError, JobStatus


def _reject():
    raise JobFenceRejectedError("NATIVE_PARSER_OUTPUT_JOURNAL_REJECTED") from None


def _observed_output(state, observed, at, *, prior):
    """Facts and a closed journal only; the caller separately owns SQL grants."""
    context, job, run = state.context, state.job, state.run
    snapshot, binding = context.original.snapshot(), context.binding()
    if (not isinstance(observed, NativeParserObservedOutput)
            or not isinstance(observed.output, NativeParserOutput)
            or observed.source_snapshot_id != run["source_snapshot_id"]
            or observed.content_sha256 != context.original.lineage.content_sha256
            or observed.object_generation != snapshot["object_generation"]
            or observed.original_input_sha256 != context.original.sha256
            or observed.artifact_sha256 != context.artifact.sha256
            or not isinstance(observed.metadata_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", observed.metadata_sha256) is None
            or not isinstance(observed.intent_ids, tuple) or len(observed.intent_ids) != len(OPERATIONS)
            or len(set(observed.intent_ids)) != len(OPERATIONS)):
        _reject()
    checked = validate_native_manual_parser_output(
        json.dumps(observed.output.facts.payloads(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode(),
        artifact=context.artifact, lineage=context.original.lineage,
        original_fields=context.original.command().fields.values())
    if checked != observed.output:
        _reject()
    intents = prior.current_items
    # Every prior accepted/retry record and its closed IO has been proved.
    # Only the actual four operations in this accepted attempt produce facts.
    if (set(state.payload) != active_payload_fields(job["attempt"])
            or state.payload["delivery_generation"] != 1 or job["attempt"] < 1 or job["last_error"] is not None
            or not isinstance(intents, list) or len(intents) != len(OPERATIONS)
            or any(not isinstance(item, dict) or item.get("state") != "FINISHED" for item in intents)):
        _reject()
    prefix = "gs://" + context.policy.storage.bucket + "/"
    key = snapshot["raw_object_uri"][len(prefix):]
    expected_arguments = ({}, {"key": key, "generation": snapshot["object_generation"]},
        {"key": key, "generation": snapshot["object_generation"]},
        {"document_sha256": context.original.lineage.content_sha256, "byte_length": snapshot["byte_length"],
            "artifact_sha256": context.artifact.sha256, "output_schema_version": context.artifact.output_schema_version})
    expected_observed = (None,
        {"sha256": observed.metadata_sha256, "generation": snapshot["object_generation"],
            "byte_length": snapshot["byte_length"]},
        {"sha256": observed.content_sha256, "byte_length": snapshot["byte_length"],
            "generation": snapshot["object_generation"]},
        {"sha256": checked.output_sha256, "artifact_sha256": checked.artifact_sha256, "status": checked.status})
    selected, reservations = [], []
    for position, ident in enumerate(observed.intent_ids):
        native_intake_uuid(ident)
        matches = [item for item in intents if item.get("intent_id") == ident]
        if len(matches) != 1:
            _reject()
        item = matches[0]
        expected = {"operation": OPERATIONS[position], "arguments": expected_arguments[position],
            "state": "FINISHED", "delivery_generation": state.payload["delivery_generation"],
            "physical_fence": state.physical.fence_token, "business_fence": job["fence_token"],
            "attempt": job["attempt"], "worker": state.physical.locked_by,
            "parser_run_id": run["parser_run_id"], "source_snapshot_id": run["source_snapshot_id"],
            "original_input_sha256": context.original.sha256, "artifact_sha256": context.artifact.sha256,
            "binding_sha256": canonical_sha256(binding), "policy_sha256": context.policy.sha256,
            "source_version": context.source["version"], "source_sha256": context.classified.source_snapshot_sha256,
            "source_valid_until": context.classified.valid_until.isoformat(),
            "human_authority_sha256": binding["human_authority_sha256"],
            "process_state": "NO_CHILD_REMAINS", "error_class": None}
        if (set(item) != _INTENT_FIELDS
                or canonical_json({k: item[k] for k in expected}) != canonical_json(expected)
                or not _time(item["admitted_at"]) <= _time(item["finished_at"]) <= at
                or (selected and _time(selected[-1]["finished_at"]) > _time(item["admitted_at"]))):
            _reject()
        value = item["observed"]
        if position == 0:
            if (not isinstance(value, dict) or set(value) != {"sha256"}
                    or not isinstance(value["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is None):
                _reject()
        elif canonical_json(value) != canonical_json(expected_observed[position]):
            _reject()
        reservation = item["reservation"]
        if (not isinstance(reservation, dict)
                or set(reservation) != {"reservation_id", "rate_reservation_id", "source_id", "expires_at"}
                or reservation["rate_reservation_id"] is not None
                or reservation["source_id"] != context.source["source_id"]
                or _time(reservation["expires_at"]) <= _time(item["admitted_at"])):
            _reject()
        reservations.append(native_intake_uuid(reservation["reservation_id"]))
        selected.append(item)
    if (len(set(reservations)) != len(OPERATIONS)
            or [item for item in intents if item.get("delivery_generation") == state.payload["delivery_generation"]
                and item.get("attempt") == job["attempt"]] != selected):
        _reject()
    return checked, selected, reservations


def commit_parser_output(owner, delivery, leases, observed):
    """Settle a genuine output and its delivery; PARTIAL is a run outcome."""
    if (not isinstance(leases, NativeParserCompositeLeases)
            or leases.owner is not owner or leases.delivery != delivery):
        _reject()
    with leases.root():
        try:
            state = NativeParserExecutionReads(owner).fresh(delivery, leases.tenant, leases.generation,
                business_fence=leases.business_fence)
            prior = NativeParserAttemptHistory(owner.graph).previous(state.job, state.run, state.physical,
                state.context.original, before=state.before)
            current = closed_parser_attempt_journal(state.run, state.payload["admission"], state.context.original,
                state.payload["acceptance_receipt"], prior.current_items, owner.clock._clock(),
                timeout_at=state.job["timeout_at"], before=state.before)
            combine_closed_parser_journals((*prior.journals, current), state.payload["attempt_intents"], before=state.before)
        except NativeParserConflict as error:
            if str(error) not in {"NATIVE_PARSER_ATTEMPT_HISTORY_REJECTED", "NATIVE_PARSER_ATTEMPT_JOURNAL_REJECTED"}:
                raise
            _reject()
        job, run, before, context = state.job, state.run, state.before, state.context
        if job["status"] != "RUNNING" or context.policy.sha256 != leases.policy_sha256:
            _reject()
        at = owner.clock._clock()
        checked, journal, reservations = _observed_output(state, observed, at, prior=prior)
        before()
        if owner.engine.query_one("SELECT reservation_id FROM intake.native_retrieval_inflight_slots "
                "WHERE reservation_id=ANY(?::uuid[]) LIMIT 1", (reservations,)) is not None:
            _reject()
        before()
        CanonicalAuditBacklog(owner.engine).require_available(leases.tenant)
        intake = context.intake
        target = IntakeStage.MATCHING if checked.status == "SUCCEEDED" else IntakeStage.NEEDS_REVIEW
        action = "intake.matching_started.v1" if checked.status == "SUCCEEDED" else "intake.review_required.v1"
        permission = "parser.execute" if checked.status == "SUCCEEDED" else "intake.route_review"
        aggregate = IntakeAggregate(id=intake["intake_id"], tenant_id=leases.tenant, stage=IntakeStage.PARSING,
            version=intake["version"], created_by=intake["submitter_subject_id"], source_id=intake["source_id"],
            canonical_url=intake["canonical_url"], policy=intake["source_policy_state"], owner_id=intake["submitter_subject_id"])
        IntakeStateMachine.transition(aggregate, target, TransitionContext(
            actor=Actor(JOB_TYPE, PrincipalRole.SVC_PARSER, leases.tenant), idempotency_key=run["parser_run_id"],
            correlation_id=job["correlation_id"], causation_id=context.original.parent_event_id,
            version_before=intake["version"], reason="Route the attested parser's actual validated output.", current_time=at))
        updated_run = owner.runs.complete_output(run, checked, at=at, before=before)
        transition_id, audit_id, parser_audit_id = (str(uuid4()) for _ in range(3))
        completed = DomainEvent(event_type="parser.run_completed", producer="parser_service", tenant_id=leases.tenant,
            aggregate_type="parser_run", aggregate_id=run["parser_run_id"], aggregate_version=updated_run["version"],
            partition_key=leases.tenant + ":" + intake["intake_id"], correlation_id=job["correlation_id"],
            causation_id=context.original.parent_event_id, actor_ref=JOB_TYPE, occurred_at=at,
            schema_ref="#/payloads/ParserRunCompletedV1", payload={"parser_run_id": run["parser_run_id"],
                "intake_id": intake["intake_id"], "parser_release_id": run["parser_release_id"],
                "status": checked.status, "quality_summary": checked.quality_summary(),
                "output_schema_version": context.artifact.output_schema_version})
        changed = DomainEvent(event_type="intake.state_changed", producer="listing_intake_service", tenant_id=leases.tenant,
            aggregate_type="intake", aggregate_id=intake["intake_id"], aggregate_version=aggregate.version,
            partition_key=leases.tenant + ":" + intake["intake_id"], correlation_id=job["correlation_id"],
            causation_id=completed.event_id, actor_ref=JOB_TYPE, occurred_at=at,
            schema_ref="#/payloads/IntakeStateChangedV1", payload={"intake_id": intake["intake_id"],
                "from_state": "PARSING", "to_state": target.value, "transition_id": transition_id,
                "version": aggregate.version, "occurred_at": at.isoformat()})
        result = {"job_id": job["job_id"], "parser_run_id": run["parser_run_id"], "intake_id": intake["intake_id"],
            "source_snapshot_id": run["source_snapshot_id"], "parser_release_id": run["parser_release_id"],
            "parser_status": checked.status, "job_status": "SUCCEEDED", "processing_state": target.value,
            "version": aggregate.version, "intake_version_before": intake["version"], "run_version": updated_run["version"],
            "settled_at": at.isoformat(), "transition_id": transition_id, "audit_event_id": audit_id,
            "parser_audit_event_id": parser_audit_id, "parser_completed_event_id": completed.event_id,
            "outbox_event_id": changed.event_id, "business_fence": job["fence_token"],
            "physical_fence": state.physical.fence_token, "worker": state.physical.locked_by,
            "business_version": job["version"] + 1, "physical_version": state.physical.version + 1,
            "physical_attempts": state.physical.attempts,
            "attempt": job["attempt"], "delivery_generation": leases.generation,
            "acceptance_audit_event_id": state.payload["acceptance_receipt"]["audit_event_id"],
            "output_sha256": checked.output_sha256, "quality_summary": checked.quality_summary(),
            "journal_sha256": canonical_sha256(journal), "intent_ids": list(observed.intent_ids),
            "native_parser_provenance": parser_admission_provenance(state.payload)}
        if job["attempt"] > 1:
            result["attempt_history_sha256"] = prior.history_sha256
        before()
        sequence = owner.engine.query_one("SELECT COALESCE(MAX(sequence_no),0)+1 AS next "
            "FROM intake.intake_stage_transitions WHERE tenant_id=? AND intake_id=?",
            (leases.tenant, intake["intake_id"]))["next"]
        result["transition_sequence"] = sequence
        before()
        owner.engine.execute("INSERT INTO intake.intake_stage_transitions (transition_id,tenant_id,intake_id,sequence_no,"
            "from_state,to_state,actor_subject_id,service_principal,permission,reason_code,reason_text,idempotency_key,"
            "expected_version,resulting_version,job_id,source_policy_version,source_snapshot_id,correlation_id,causation_id,occurred_at) "
            "VALUES (?,?,?,?,'PARSING',?,NULL,?,?,?,'Route the actual validated parser output.',?,?,?,?,?,?,?,?,?)",
            (transition_id, leases.tenant, intake["intake_id"], sequence, target.value, JOB_TYPE, permission,
                "PARSER_VALIDATED_OUTPUT" if checked.status == "SUCCEEDED" else "PARSER_PARTIAL_OUTPUT",
                run["parser_run_id"], intake["version"], aggregate.version, job["job_id"], context.source["version"],
                run["source_snapshot_id"], job["correlation_id"], completed.event_id, at.isoformat()))
        for event in (completed, changed):
            before()
            owner.graph._outbox(event)
            # Check actual persistence after the writer. A skipped or changed
            # insert must still roll back this owned output transaction.
            before()
            if not owner.engine.query_one("SELECT event_id FROM workflow.outbox_events "
                    "WHERE tenant_id=? AND event_id=? AND causation_id IS NOT DISTINCT FROM ? "
                    "AND sensitive_fields=?::text[]",
                    (leases.tenant, event.event_id, event.causation_id, list(event.sensitive_fields))):
                raise JobFenceRejectedError("NATIVE_PARSER_OUTPUT_OUTBOX_METADATA_REJECTED")
        for current_action, current_id, previous in (
                ("parser.run_completed.v1", parser_audit_id, {"parser_run_id": run["parser_run_id"], "status": "RUNNING"}),
                (action, audit_id, {"processing_state": "PARSING", "version": intake["version"]})):
            before()
            owner.graph.audit_sink.persist({"audit_event_id": current_id, "tenant_id": leases.tenant,
                "resource_type": "intake", "resource_id": intake["intake_id"], "action": current_action,
                "result": "SUCCEEDED", "actor_subject_id": None, "service_principal": JOB_TYPE, "actor_role": "SVC_PARSER",
                "correlation_id": job["correlation_id"], "causation_id": context.original.parent_event_id,
                "occurred_at": at.isoformat(), "before_value": previous, "after_value": result,
                "reason": "Commit the actual parser output and route its measured outcome."},
                {key: intake[key] for key in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")})
        owner._confirm(intake, run["parser_release_id"], leases.generation,
            context.binding(), context.attempt_token, before)
        before()
        if owner.engine.execute("UPDATE intake.intakes SET processing_state=?,version=?,last_transition_at=? "
                "WHERE tenant_id=? AND intake_id=? AND version=? AND processing_state='PARSING' "
                "AND source_policy_state=? AND deleted_at IS NULL AND resolved_listing_id IS NULL AND completed_at IS NULL "
                "AND ?::timestamptz>clock_timestamp()",
                (target.value, aggregate.version, at.isoformat(), leases.tenant, intake["intake_id"], intake["version"],
                    intake["source_policy_state"], context.classified.valid_until.isoformat())).rowcount != 1:
            _reject()
        before()
        if owner.engine.execute("UPDATE workflow.jobs SET status='SUCCEEDED',result=?::jsonb,"
                "payload=?::jsonb,lease_expires_at=NULL,next_attempt_at=NULL,heartbeat_at=?,updated_at=?,version=version+1 "
                "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND attempt=? AND status='RUNNING' "
                "AND result IS NULL AND cancellation_requested_at IS NULL AND lease_expires_at>clock_timestamp() "
                "AND timeout_at>clock_timestamp() AND ?::timestamptz>clock_timestamp()",
                (canonical_json(result), canonical_json({**state.payload, "terminal_receipt": result}),
                    at.isoformat(), at.isoformat(), leases.tenant, job["job_id"], job["version"], job["fence_token"],
                    job["attempt"], context.classified.valid_until.isoformat())).rowcount != 1:
            _reject()
        before()
        if owner.engine.query_one("SELECT job_id FROM durable_jobs WHERE job_id=? AND status=? AND version=? "
                "AND fence_token=? AND locked_by=? AND lease_expires_at>clock_timestamp()",
                (job["job_id"], JobStatus.RUNNING.value, state.physical.version,
                    state.physical.fence_token, state.physical.locked_by)) is None:
            _reject()
        before()
        owner.queue.update_status(job["job_id"], JobStatus.SUCCEEDED,
            expected_version=state.physical.version, fence_token=state.physical.fence_token)
        before()
        return result
