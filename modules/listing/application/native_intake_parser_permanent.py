"""Owned permanent failure or original retry exhaustion under fresh authority.

Requires an actual typed, closed/reaped operation. No output is invented;
Intake failure, both jobs, run, audits and events commit in the same root.
Every prior retry remains authenticated; cancellation has a separate contract.
"""
from __future__ import annotations

import json
import math
from datetime import timedelta
from uuid import uuid4

from modules.listing.application.native_intake_assisted_entry_request_proof import _object, _time
from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_parser_attempts import (
    NativeParserAttemptHistory,
    active_payload_fields,
)
from modules.listing.application.native_intake_parser_execution import NativeParserExecutionReads
from modules.listing.application.native_intake_parser_failure_policy import parser_failure_kind
from modules.listing.application.native_intake_parser_history import (
    _audited_exact,
)
from modules.listing.application.native_intake_parser_job_proof import parser_admission_provenance
from modules.listing.application.native_intake_parser_leases import NativeParserCompositeLeases
from modules.listing.application.native_intake_parser_negative import (
    _NEGATIVE_FIELDS,
    NativeParserNegativeClosure,
    _closed_slots,
    _publish,
    closed_initial_parser_journal,
)
from modules.listing.application.native_intake_parser_policy import (
    JOB_TYPE,
    LEASE_SECONDS,
    NativeParserConflict,
)
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
from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError, JobStatus

_CODE = "NATIVE_PARSER_PERMANENT_FAILURE"
_CAUSE = "OBSERVED_PERMANENT_PARSER_FAILURE"
_REASON = "Commit the actual reaped permanent parser failure and fail the authorized Intake."
_TRANSITION_REASON = "The authorized parser observed a permanent defect; no parsed output was committed."
_EXHAUSTION_CODE = "NATIVE_PARSER_RETRY_EXHAUSTED"
_EXHAUSTION_CAUSE = "ORIGINAL_PARSER_RETRY_CAP_EXHAUSTED"
_TIME_EXHAUSTION_CAUSE = "ORIGINAL_PARSER_RETRY_TIME_EXHAUSTED"
_POLICIES = {
    _CAUSE: {"code": _CODE, "reason": _REASON, "transition_reason": _TRANSITION_REASON,
        "reason_code": "PARSER_PERMANENT_FAILURE"},
    _EXHAUSTION_CAUSE: {"code": _EXHAUSTION_CODE,
        "reason": "Commit the actual reaped transient parser failure after its original retry cap was exhausted.",
        "transition_reason": "The authorized parser exhausted its original retry cap; no parsed output was committed.",
        "reason_code": "PARSER_RETRY_EXHAUSTED"},
    _TIME_EXHAUSTION_CAUSE: {"code": _EXHAUSTION_CODE,
        "reason": "Commit the actual reaped transient parser failure when its original timeout cannot accommodate the next backoff.",
        "transition_reason": "The original parser timeout cannot accommodate its next backoff; no parsed output was committed.",
        "reason_code": "PARSER_RETRY_EXHAUSTED"},
}
_FIELDS = _NEGATIVE_FIELDS | {"processing_state", "intake_version_before", "version", "transition_id",
    "transition_sequence", "intake_audit_event_id", "outbox_event_id", "failure_intent_id", "failure_observation"}


def _reject():
    raise NativeParserConflict("NATIVE_PARSER_PERMANENT_TERMINAL_PROOF_REJECTED") from None


def _failure_fact(items, facts, job, physical, at):
    if facts.error_count != 1 or not facts.intent_ids:
        raise JobFenceRejectedError("NATIVE_PARSER_OBSERVED_PERMANENT_FAILURE_REQUIRED")
    last = items[-1]
    if last["state"] != "ERROR" or last["intent_id"] != facts.intent_ids[-1]:
        raise JobFenceRejectedError("NATIVE_PARSER_OBSERVED_PERMANENT_FAILURE_REQUIRED")
    kind = parser_failure_kind(last.get("failure_observation"), last["error_class"])
    if kind == "PERMANENT":
        return last, _CAUSE
    if kind == "TRANSIENT" and (job["attempt"] == job["max_attempts"] or physical.attempts == physical.max_retries):
        return last, _EXHAUSTION_CAUSE
    if kind == "TRANSIENT":
        backoff = _object(job["payload"])["admission"]["policy"]["backoff_seconds"][job["attempt"]-1]
        if type(backoff) not in {int, float} or not math.isfinite(backoff) or backoff <= 0:
            _reject()
        timeout = _time(job["timeout_at"])
        # This is an original admitted command budget, never the future expiry
        # of a source/account grant. The owner still requires fresh authority,
        # live fences and a live original root before changing Intake.
        if at < timeout <= at+timedelta(seconds=backoff):
            return last, _TIME_EXHAUSTION_CAUSE
    raise JobFenceRejectedError("NATIVE_PARSER_OBSERVED_PERMANENT_FAILURE_REQUIRED")


def _failure_journal(graph, job, run, physical, original, acceptance, at, *, before):
    payload = _object(job["payload"])
    if job["attempt"] == 1:
        facts = closed_initial_parser_journal(job, run, payload, original, acceptance, at, before=before)
        prior, current, items = None, facts, payload["attempt_intents"]
    else:
        prior, current, facts = NativeParserAttemptHistory(graph).closed_current(
            job, run, physical, original, acceptance, at, before=before)
        items = prior.current_items
    last, cause = _failure_fact(items, current, job, physical, at)
    return prior, facts, last, cause


class NativeParserPermanentSettlement:
    def __init__(self, owner):
        self.owner, self.graph, self.engine, self.queue = owner, owner.graph, owner.engine, owner.queue

    def settle(self, delivery, *, leases):
        if (not isinstance(leases, NativeParserCompositeLeases)
                or leases.owner is not self.owner or leases.delivery != delivery):
            raise JobFenceRejectedError("NATIVE_PARSER_PERMANENT_LEASE_BINDING_REJECTED")
        outcome = self.owner.replay(delivery, leases=leases)
        if outcome["terminal"]:
            return outcome["result"]
        try:
            return self._settle(delivery, leases)
        except Exception:
            outcome = self.owner.replay(delivery, leases=leases)
            if outcome["terminal"]:
                return outcome["result"]
            raise

    def _settle(self, delivery, leases):
        owner, tenant = self.owner, leases.tenant
        with leases.root():
            state = NativeParserExecutionReads(owner).fresh(delivery, tenant, leases.generation,
                business_fence=leases.business_fence)
            job, payload, run, before, context, physical = (
                state.job, state.payload, state.run, state.before, state.context, state.physical)
            if (job["status"] != "RUNNING" or job["attempt"] < 1 or leases.generation != 1
                    or set(payload) != active_payload_fields(job["attempt"]) or job["last_error"] is not None
                    or context.policy.sha256 != leases.policy_sha256):
                raise JobFenceRejectedError("NATIVE_PARSER_PERMANENT_INITIAL_ATTEMPT_REQUIRED")
            acceptance = owner.jobs.acceptance(job, run, before=before)
            at = owner.clock._clock()
            prior, facts, last, cause = _failure_journal(self.graph, job, run, physical,
                context.original, acceptance, at, before=before)
            policy = _POLICIES[cause]
            _closed_slots(self.engine, facts, before)
            before()
            CanonicalAuditBacklog(self.engine).require_available(tenant)
            intake = context.intake
            at = owner.clock._clock()
            if (not at < _time(job["lease_expires_at"]) <= physical.lease_expires_at
                    or physical.lease_expires_at - at > timedelta(seconds=LEASE_SECONDS)):
                raise JobFenceRejectedError("NATIVE_PARSER_PERMANENT_LEASE_WITNESS_REJECTED")
            aggregate = IntakeAggregate(id=intake["intake_id"], tenant_id=tenant, stage=IntakeStage.PARSING,
                version=intake["version"], created_by=intake["submitter_subject_id"], source_id=intake["source_id"],
                canonical_url=intake["canonical_url"], policy=intake["source_policy_state"], owner_id=intake["submitter_subject_id"])
            IntakeStateMachine.transition(aggregate, IntakeStage.FAILED, TransitionContext(
                actor=Actor(JOB_TYPE, PrincipalRole.SVC_PARSER, tenant), idempotency_key=run["parser_run_id"],
                correlation_id=job["correlation_id"], causation_id=context.original.parent_event_id,
                version_before=intake["version"], reason=policy["transition_reason"], current_time=at))
            updated_run = owner.runs.complete_negative(run, status="FAILED", at=at, before=before)
            audit_id, intake_audit_id, completed_id, dead_letter_id, transition_id, state_event_id = (
                str(uuid4()) for _ in range(6))
            before()
            sequence = self.engine.query_one("SELECT COALESCE(MAX(sequence_no),0)+1 AS next "
                "FROM intake.intake_stage_transitions WHERE tenant_id=? AND intake_id=?",
                (tenant, intake["intake_id"]))["next"]
            receipt = {"job_id": job["job_id"], "parser_run_id": run["parser_run_id"], "intake_id": run["intake_id"],
                "source_snapshot_id": run["source_snapshot_id"], "parser_release_id": run["parser_release_id"],
                "parser_status": "FAILED", "job_status": "FAILED", "settled_at": at.isoformat(), "prior_status": "RUNNING",
                "business_fence": job["fence_token"], "physical_fence": physical.fence_token, "worker": physical.locked_by,
                "attempt": job["attempt"], "delivery_generation": leases.generation, "business_version": job["version"]+1,
                "physical_version": physical.version+1, "physical_attempts": physical.attempts,
                "run_version": updated_run["version"], "physical_lease_expires_at": physical.lease_expires_at.isoformat(),
                "business_lease_expires_at": _time(job["lease_expires_at"]).isoformat(),
                "acceptance_audit_event_id": acceptance["audit_event_id"], "audit_event_id": audit_id,
                "parser_completed_event_id": completed_id, "dead_letter_event_id": dead_letter_id,
                "code": policy["code"], "cause_code": cause, "error_class": last["error_class"], "intent_ids": list(facts.intent_ids),
                "journal_sha256": facts.journal_sha256, "quality_summary": facts.quality_summary(),
                "native_parser_provenance": parser_admission_provenance(payload), "processing_state": "FAILED",
                "intake_version_before": intake["version"], "version": aggregate.version, "transition_id": transition_id,
                "transition_sequence": sequence, "intake_audit_event_id": intake_audit_id, "outbox_event_id": state_event_id,
                "failure_intent_id": last["intent_id"], "failure_observation": last["failure_observation"]}
            if prior is not None:
                receipt["attempt_history_sha256"] = prior.history_sha256
            if set(receipt) != _FIELDS | ({"attempt_history_sha256"} if job["attempt"] > 1 else set()):
                _reject()
            completed = DomainEvent(event_id=completed_id, event_type="parser.run_completed", producer="parser_service",
                tenant_id=tenant, aggregate_type="parser_run", aggregate_id=run["parser_run_id"],
                aggregate_version=updated_run["version"], partition_key=tenant+":"+run["intake_id"],
                correlation_id=job["correlation_id"], causation_id=context.original.parent_event_id, actor_ref=JOB_TYPE,
                occurred_at=at, schema_ref="#/payloads/ParserRunCompletedV1", payload={"parser_run_id": run["parser_run_id"],
                    "intake_id": run["intake_id"], "parser_release_id": run["parser_release_id"], "status": "FAILED",
                    "quality_summary": facts.quality_summary()})
            changed = DomainEvent(event_id=state_event_id, event_type="intake.state_changed", producer="listing_intake_service",
                tenant_id=tenant, aggregate_type="intake", aggregate_id=intake["intake_id"], aggregate_version=aggregate.version,
                partition_key=tenant+":"+intake["intake_id"], correlation_id=job["correlation_id"], causation_id=completed_id,
                actor_ref=JOB_TYPE, occurred_at=at, schema_ref="#/payloads/IntakeStateChangedV1", payload={
                    "intake_id": intake["intake_id"], "from_state": "PARSING", "to_state": "FAILED",
                    "transition_id": transition_id, "version": aggregate.version, "occurred_at": at.isoformat()})
            dead_letter = DomainEvent(event_id=dead_letter_id, event_type="job.dead_lettered", producer="job_platform",
                tenant_id=tenant, aggregate_type="job", aggregate_id=job["job_id"], aggregate_version=receipt["business_version"],
                partition_key=tenant+":"+job["job_id"], correlation_id=job["correlation_id"],
                causation_id=context.original.parent_event_id, actor_ref=JOB_TYPE, occurred_at=at,
                schema_ref="#/payloads/JobDeadLetteredV1", sensitive_fields=["payload.last_error_details"], payload={
                    "job_id": job["job_id"], "job_type": JOB_TYPE, "checkpoint": "PARSING", "attempt": job["attempt"],
                    "error_code": policy["code"], "dead_lettered_at": at.isoformat(), "last_error_details": {"error_class": last["error_class"]}})
            before()
            self.engine.execute("INSERT INTO intake.intake_stage_transitions (transition_id,tenant_id,intake_id,sequence_no,"
                "from_state,to_state,actor_subject_id,service_principal,permission,reason_code,reason_text,idempotency_key,"
                "expected_version,resulting_version,job_id,source_policy_version,source_snapshot_id,correlation_id,causation_id,occurred_at) "
                "VALUES (?,?,?,?,'PARSING','FAILED',NULL,?,'parser.execute',?,?,?,?,?,?,?,?,?,?,?)",
                (transition_id, tenant, intake["intake_id"], sequence, JOB_TYPE, policy["reason_code"], policy["transition_reason"], run["parser_run_id"],
                    intake["version"], aggregate.version, job["job_id"], context.source["version"], run["source_snapshot_id"],
                    job["correlation_id"], completed_id, at.isoformat()))
            for event in (completed, changed, dead_letter):
                _publish(owner, event, before)
            for ident, action, previous in ((audit_id, "parser.run_completed.v1", {"parser_run_id": run["parser_run_id"], "status": "RUNNING"}),
                    (intake_audit_id, "intake.processing_failed.v1", {"processing_state": "PARSING", "version": intake["version"]})):
                before()
                self.graph.audit_sink.persist({"audit_event_id": ident, "tenant_id": tenant, "resource_type": "intake",
                    "resource_id": intake["intake_id"], "actor_subject_id": None, "service_principal": JOB_TYPE,
                    "actor_role": "SVC_PARSER", "action": action, "result": "SUCCEEDED", "causation_id": context.original.parent_event_id,
                    "correlation_id": job["correlation_id"], "occurred_at": at.isoformat(), "reason": policy["reason"],
                    "before_value": previous, "after_value": {**receipt, "intake_mutated": True}},
                    {k: intake[k] for k in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")})
            owner._confirm(intake, run["parser_release_id"], leases.generation,
                context.binding(), context.attempt_token, before)
            before()
            if self.engine.execute("UPDATE intake.intakes SET processing_state='FAILED',version=?,last_transition_at=? "
                    "WHERE tenant_id=? AND intake_id=? AND version=? AND processing_state='PARSING' AND source_policy_state=? "
                    "AND deleted_at IS NULL AND resolved_listing_id IS NULL AND completed_at IS NULL "
                    "AND ?::timestamptz>clock_timestamp()", (aggregate.version, at.isoformat(), tenant, intake["intake_id"],
                        intake["version"], intake["source_policy_state"], context.classified.valid_until.isoformat())).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_PARSER_PERMANENT_INTAKE_CAS_REJECTED")
            before()
            if self.engine.execute("UPDATE workflow.jobs SET status='FAILED',payload=?::jsonb,last_error=?::jsonb,"
                    "lease_expires_at=NULL,next_attempt_at=NULL,heartbeat_at=?,updated_at=?,version=version+1 "
                    "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND attempt=? AND status='RUNNING' "
                    "AND result IS NULL AND cancellation_requested_at IS NULL AND lease_expires_at>clock_timestamp() "
                    "AND timeout_at>clock_timestamp() AND ?::timestamptz>clock_timestamp()",
                    (canonical_json({**payload, "permanent_failure_receipt": receipt}),
                        canonical_json({"code": policy["code"], "error_class": last["error_class"]}), at.isoformat(), at.isoformat(),
                        tenant, job["job_id"], job["version"], job["fence_token"], job["attempt"],
                        context.classified.valid_until.isoformat())).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_PARSER_PERMANENT_BUSINESS_CAS_REJECTED")
            before()
            if self.engine.query_one("SELECT job_id FROM durable_jobs WHERE job_id=? AND status=? AND version=? "
                    "AND fence_token=? AND locked_by=? AND lease_expires_at>clock_timestamp()",
                    (job["job_id"], JobStatus.RUNNING.value, physical.version, physical.fence_token, physical.locked_by)) is None:
                raise JobFenceRejectedError("NATIVE_PARSER_PERMANENT_PHYSICAL_CAS_REJECTED")
            before()
            self.queue.update_status(job["job_id"], JobStatus.FAILED, delivery_state=JobDeliveryState.DEAD_LETTER,
                expected_version=physical.version, fence_token=physical.fence_token, error_message=policy["code"])
            before()
            return None


def read_permanent_terminal(history, job, run, physical, tenant, generation, *, before):
    """Historical proof only; retired capabilities never become fresh grants."""
    try:
        payload = _object(job["payload"])
        receipt = _object(payload["permanent_failure_receipt"])
        policy = _POLICIES[receipt["cause_code"]]
        acceptance = history.jobs.acceptance(job, run, before=before)
        integers = ("business_fence", "physical_fence", "attempt", "delivery_generation", "business_version",
            "physical_version", "physical_attempts", "run_version", "intake_version_before", "version", "transition_sequence")
        if (set(payload) != active_payload_fields(job["attempt"]) | {"permanent_failure_receipt"}
                or set(receipt) != _FIELDS | ({"attempt_history_sha256"} if job["attempt"] > 1 else set())
                or any(type(receipt[k]) is not int or receipt[k] < 1 for k in integers)
                or job["status"] != "FAILED" or run["status"] != "FAILED" or physical.status != JobStatus.FAILED
                or receipt["parser_status"] != "FAILED" or receipt["job_status"] != "FAILED" or receipt["processing_state"] != "FAILED"
                or receipt["prior_status"] != "RUNNING" or receipt["code"] != policy["code"]
                or receipt["job_id"] != job["job_id"] or receipt["parser_run_id"] != run["parser_run_id"]
                or any(receipt[k] != run[k] for k in ("intake_id", "source_snapshot_id", "parser_release_id"))
                or generation != 1 or receipt["delivery_generation"] != generation or job["attempt"] < 1
                or any(receipt[k] != acceptance[k] for k in ("physical_fence", "business_fence", "worker", "attempt", "delivery_generation"))
                or receipt["acceptance_audit_event_id"] != acceptance["audit_event_id"]
                or receipt["business_version"] != job["version"] or receipt["physical_version"] != physical.version
                or receipt["physical_attempts"] != physical.attempts or not 1 <= physical.attempts <= physical.max_retries
                or physical.fence_token != receipt["physical_fence"] or physical.delivery_state != JobDeliveryState.DEAD_LETTER
                or physical.error_message != policy["code"]
                or any(getattr(physical, k) is not None for k in ("locked_by", "heartbeat_at", "lease_expires_at", "leased_until"))
                or job["result"] is not None or job["lease_expires_at"] is not None or job["next_attempt_at"] is not None
                or job["cancellation_requested_at"] is not None or job["checkpoint"] != "PARSING"
                or canonical_json(_object(job["last_error"])) != canonical_json({"code": policy["code"], "error_class": receipt["error_class"]})
                or receipt["run_version"] != run["version"] or run["version"] != acceptance["run_version_after"]+1
                or run["parsed_payload"] is not None or run["normalized_payload"] is not None
                or _object(run["field_confidence"]) != {}
                or (json.loads(run["validation_errors"]) if isinstance(run["validation_errors"], str) else run["validation_errors"]) != []
                or receipt["native_parser_provenance"] != parser_admission_provenance(payload)
                or receipt["intake_version_before"] != payload["admission"]["binding"]["intake_version"]
                or receipt["version"] != receipt["intake_version_before"]+1):
            _reject()
        at = _time(receipt["settled_at"])
        if (not _time(acceptance["accepted_at"]) <= at < _time(receipt["business_lease_expires_at"])
                <= _time(receipt["physical_lease_expires_at"])
                or _time(receipt["physical_lease_expires_at"])-at > timedelta(seconds=LEASE_SECONDS)
                or not at < _time(job["timeout_at"]) or not at < _time(payload["admission"]["binding"]["source_valid_until"])
                or _time(run["started_at"]) > _time(acceptance["accepted_at"])
                or _time(run["completed_at"]) != at or _time(job["heartbeat_at"]) != at or _time(job["updated_at"]) != at):
            _reject()
        original = history._parent_input(tenant, payload["admission"]["binding"], before)
        prior, facts, last, cause = _failure_journal(history.graph, job, run, physical,
            original, acceptance, at, before=before)
        if (receipt["cause_code"] != cause
                or (prior is not None and receipt["attempt_history_sha256"] != prior.history_sha256)
                or (prior is None and _time(run["started_at"]) != _time(acceptance["accepted_at"]))):
            _reject()
        if (receipt["intent_ids"] != list(facts.intent_ids) or receipt["journal_sha256"] != facts.journal_sha256
                or canonical_json(receipt["quality_summary"]) != canonical_json(facts.quality_summary())
                or receipt["failure_intent_id"] != last["intent_id"] or receipt["error_class"] != last["error_class"]
                or canonical_json(receipt["failure_observation"]) != canonical_json(last["failure_observation"])):
            _reject()
        _closed_slots(history.engine, facts, before)
        for k in ("audit_event_id", "parser_completed_event_id", "dead_letter_event_id", "transition_id", "intake_audit_event_id", "outbox_event_id"):
            native_intake_uuid(receipt[k])
        before()
        transition = history.engine.query_one("SELECT * FROM intake.intake_stage_transitions WHERE tenant_id=? "
            "AND transition_id=? AND intake_id=? AND job_id=? AND from_state='PARSING' AND to_state='FAILED' "
            "AND actor_subject_id IS NULL AND service_principal=? AND permission='parser.execute' AND expected_version=? "
            "AND resulting_version=? AND idempotency_key=? AND source_snapshot_id=? AND source_policy_version=? "
            "AND correlation_id=? AND causation_id=?", (tenant, receipt["transition_id"], run["intake_id"], job["job_id"],
                JOB_TYPE, receipt["intake_version_before"], receipt["version"], run["parser_run_id"], run["source_snapshot_id"],
                payload["admission"]["binding"]["source_version"], job["correlation_id"], receipt["parser_completed_event_id"]))
        if (transition is None or transition["sequence_no"] != receipt["transition_sequence"]
                or transition["reason_code"] != policy["reason_code"] or transition["reason_text"] != policy["transition_reason"]
                or _time(transition["occurred_at"]) != at):
            _reject()
        for key, action, previous in (("audit_event_id", "parser.run_completed.v1", {"parser_run_id": run["parser_run_id"], "status": "RUNNING"}),
                ("intake_audit_event_id", "intake.processing_failed.v1", {"processing_state": "PARSING", "version": receipt["intake_version_before"]})):
            before()
            audit = history.engine.query_one("SELECT * FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=? "
                "AND resource_type='intake' AND resource_id=? AND actor_subject_id IS NULL AND service_principal=? "
                "AND actor_role='SVC_PARSER' AND action=? AND result='SUCCEEDED' AND causation_id=? AND correlation_id=?",
                (tenant, receipt[key], run["intake_id"], JOB_TYPE, action, original.parent_event_id, job["correlation_id"]))
            if (audit is None or canonical_json(_object(audit["before_value"])) != canonical_json(previous)
                    or not _audited_exact(_object(audit["after_value"]), {**receipt, "intake_mutated": True})
                    or audit["reason"] != policy["reason"] or _time(audit["occurred_at"]) != at):
                _reject()
        history._event(job, receipt["parser_completed_event_id"], at, before, event_type="parser.run_completed",
            producer="parser_service", aggregate_type="parser_run", aggregate_id=run["parser_run_id"], aggregate_version=run["version"],
            partition_key=tenant+":"+run["intake_id"], causation_id=original.parent_event_id, schema_ref="#/payloads/ParserRunCompletedV1",
            payload={"parser_run_id": run["parser_run_id"], "intake_id": run["intake_id"], "parser_release_id": run["parser_release_id"],
                "status": "FAILED", "quality_summary": facts.quality_summary()})
        history._event(job, receipt["outbox_event_id"], at, before, event_type="intake.state_changed",
            producer="listing_intake_service", aggregate_type="intake", aggregate_id=run["intake_id"], aggregate_version=receipt["version"],
            partition_key=tenant+":"+run["intake_id"], causation_id=receipt["parser_completed_event_id"],
            schema_ref="#/payloads/IntakeStateChangedV1", payload={"intake_id": run["intake_id"], "from_state": "PARSING", "to_state": "FAILED",
                "transition_id": receipt["transition_id"], "version": receipt["version"], "occurred_at": at.isoformat()})
        history._event(job, receipt["dead_letter_event_id"], at, before, event_type="job.dead_lettered", producer="job_platform",
            aggregate_type="job", aggregate_id=job["job_id"], aggregate_version=job["version"], partition_key=tenant+":"+job["job_id"],
            causation_id=original.parent_event_id, schema_ref="#/payloads/JobDeadLetteredV1", sensitive_fields=["payload.last_error_details"],
            payload={"job_id": job["job_id"], "job_type": JOB_TYPE, "checkpoint": "PARSING", "attempt": job["attempt"], "error_code": policy["code"],
                "dead_lettered_at": at.isoformat(), "last_error_details": {"error_class": receipt["error_class"]}})
        before.check_in_memory()
        return NativeParserNegativeClosure(run["parser_run_id"], canonical_sha256(receipt))
    except NativeParserConflict:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, JobFenceRejectedError):
        _reject()
