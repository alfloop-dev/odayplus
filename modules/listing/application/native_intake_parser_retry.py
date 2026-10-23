"""Prepare/prove a bounded same-run retry after an actual reaped transient IO.

No operation is retried in this SQL root. The next physical claim must pass a
new acceptance and fresh authority; original command budgets never restart.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from modules.listing.application.native_intake_assisted_entry_request_proof import _object, _time
from modules.listing.application.native_intake_parser_attempts import (
    RETRY_CODE,
    RETRY_FIELDS,
    RETRY_REASON,
    NativeParserAttemptHistory,
    NativeParserPreparedRetry,
    active_payload_fields,
    reject,
    transient_failure,
)
from modules.listing.application.native_intake_parser_execution import NativeParserExecutionReads
from modules.listing.application.native_intake_parser_job_proof import parser_admission_provenance
from modules.listing.application.native_intake_parser_journal import (
    closed_parser_attempt_journal,
    combine_closed_parser_journals,
)
from modules.listing.application.native_intake_parser_leases import NativeParserCompositeLeases
from modules.listing.application.native_intake_parser_policy import JOB_TYPE, LEASE_SECONDS
from modules.listing.application.native_intake_retrieval_policy import (
    canonical_json,
    canonical_sha256,
)
from shared.audit.backlog import CanonicalAuditBacklog
from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError, JobStatus


def require_closed_slots(engine, facts, before):
    if facts.reservation_ids:
        before()
        if engine.query_one("SELECT reservation_id FROM intake.native_retrieval_inflight_slots "
                "WHERE reservation_id=ANY(?::uuid[]) LIMIT 1", (list(facts.reservation_ids),)) is not None:
            raise JobFenceRejectedError("NATIVE_PARSER_RETRY_UNRESOLVED_IO")


class NativeParserRetry:
    def __init__(self, owner):
        self.owner, self.graph, self.engine, self.queue = owner, owner.graph, owner.engine, owner.queue

    def prepare(self, delivery, *, leases):
        if (not isinstance(leases, NativeParserCompositeLeases)
                or leases.owner is not self.owner or leases.delivery != delivery):
            raise JobFenceRejectedError("NATIVE_PARSER_RETRY_LEASE_BINDING_REJECTED")
        outcome = self.owner.replay(delivery, leases=leases)
        if outcome.get("retry_prepared"):
            return outcome["retry_receipt"]
        if outcome["terminal"]:
            raise JobFenceRejectedError("NATIVE_PARSER_RETRY_TERMINAL_COMMAND_REJECTED")
        try:
            return self._prepare(delivery, leases)
        except Exception:
            outcome = self.owner.replay(delivery, leases=leases)
            if outcome.get("retry_prepared"):
                return outcome["retry_receipt"]
            raise

    def _prepare(self, delivery, leases):
        owner, tenant = self.owner, leases.tenant
        with leases.root():
            state = NativeParserExecutionReads(owner).fresh(delivery, tenant, leases.generation,
                business_fence=leases.business_fence)
            job, payload, run, before, context, physical = (
                state.job, state.payload, state.run, state.before, state.context, state.physical)
            if (job["status"] != "RUNNING" or set(payload) != active_payload_fields(job["attempt"])
                    or job["last_error"] is not None or context.policy.sha256 != leases.policy_sha256):
                raise JobFenceRejectedError("NATIVE_PARSER_RETRY_ACTIVE_ATTEMPT_REQUIRED")
            if job["attempt"] >= job["max_attempts"] or physical.attempts >= physical.max_retries:
                raise JobFenceRejectedError("NATIVE_PARSER_RETRY_EXHAUSTED")
            history = NativeParserAttemptHistory(self.graph)
            acceptance = owner.jobs.acceptance(job, run, before=before)
            prior = history.previous(job, run, physical, context.original, before=before)
            at = owner.clock._clock()
            current = closed_parser_attempt_journal(run, payload["admission"], context.original, acceptance,
                prior.current_items, at, timeout_at=job["timeout_at"], before=before)
            last = transient_failure(prior.current_items, current)
            combined = combine_closed_parser_journals((*prior.journals, current), payload["attempt_intents"], before=before)
            require_closed_slots(self.engine, combined, before)
            before()
            CanonicalAuditBacklog(self.engine).require_available(tenant)
            at = owner.clock._clock()
            due = at+timedelta(seconds=context.policy.backoff_seconds[job["attempt"]-1])
            if due >= min(_time(job["timeout_at"]), context.classified.valid_until):
                raise JobFenceRejectedError("NATIVE_PARSER_ORIGINAL_RETRY_DEADLINE_EXHAUSTED")
            if (not at < _time(job["lease_expires_at"]) <= physical.lease_expires_at
                    or physical.lease_expires_at-at > timedelta(seconds=LEASE_SECONDS)):
                raise JobFenceRejectedError("NATIVE_PARSER_RETRY_LEASE_WITNESS_REJECTED")
            queued = owner.runs.retry_same_run(run, before=before)
            receipt = {"proof_version": 1, "job_id": job["job_id"], "parser_run_id": run["parser_run_id"],
                "intake_id": run["intake_id"], "prepared_at": at.isoformat(), "available_at": due.isoformat(),
                "attempt": job["attempt"], "delivery_generation": leases.generation, "business_fence": job["fence_token"],
                "physical_fence": physical.fence_token, "worker": physical.locked_by, "business_version": job["version"]+1,
                "physical_version": physical.version+1, "physical_attempts": physical.attempts,
                "run_version_before": run["version"], "run_version_after": queued["version"], "timeout_at": _time(job["timeout_at"]).isoformat(),
                "first_started_at": _time(run["started_at"]).isoformat(), "acceptance_audit_event_id": acceptance["audit_event_id"],
                "audit_event_id": str(uuid4()), "code": RETRY_CODE, "error_class": last["error_class"],
                "failure_intent_id": last["intent_id"], "failure_observation": last["failure_observation"],
                "intent_ids": list(current.intent_ids), "journal_sha256": current.journal_sha256,
                "quality_summary": current.quality_summary(), "physical_lease_expires_at": physical.lease_expires_at.isoformat(),
                "business_lease_expires_at": _time(job["lease_expires_at"]).isoformat(),
                "native_parser_provenance": parser_admission_provenance(payload)}
            if set(receipt) != RETRY_FIELDS:
                reject()
            before()
            self.graph.audit_sink.persist({"audit_event_id": receipt["audit_event_id"], "tenant_id": tenant,
                "resource_type": "intake", "resource_id": run["intake_id"], "actor_subject_id": None,
                "service_principal": JOB_TYPE, "actor_role": "SVC_PARSER", "action": "intake.parser_retry_prepared.v1",
                "result": "SUCCEEDED", "causation_id": context.original.parent_event_id, "correlation_id": job["correlation_id"],
                "occurred_at": at.isoformat(), "reason": RETRY_REASON,
                "before_value": {"job_id": job["job_id"], "status": "RUNNING", "parser_run_status": "RUNNING", "attempt": job["attempt"]},
                "after_value": {**receipt, "status": "RETRYING", "parser_run_status": "QUEUED", "intake_mutated": False}},
                {k: context.intake[k] for k in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")})
            owner._confirm(context.intake, run["parser_release_id"], leases.generation,
                context.binding(), context.attempt_token, before)
            before()
            if self.engine.execute("UPDATE workflow.jobs SET status='RETRYING',next_attempt_at=?,payload=?::jsonb,"
                    "lease_expires_at=NULL,last_error=?::jsonb,version=version+1,heartbeat_at=?,updated_at=? "
                    "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND attempt=? AND status='RUNNING' "
                    "AND result IS NULL AND cancellation_requested_at IS NULL AND lease_expires_at>clock_timestamp() "
                    "AND timeout_at>?::timestamptz AND ?::timestamptz>clock_timestamp()",
                    (due.isoformat(), canonical_json({**payload, "retry_receipt": receipt}),
                        canonical_json({"code": RETRY_CODE, "error_class": last["error_class"]}), at.isoformat(), at.isoformat(),
                        tenant, job["job_id"], job["version"], job["fence_token"], job["attempt"], due.isoformat(),
                        context.classified.valid_until.isoformat())).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_PARSER_RETRY_BUSINESS_CAS_REJECTED")
            before()
            if self.engine.query_one("SELECT job_id FROM durable_jobs WHERE job_id=? AND status=? AND version=? "
                    "AND fence_token=? AND locked_by=? AND lease_expires_at>clock_timestamp()",
                    (job["job_id"], JobStatus.RUNNING.value, physical.version, physical.fence_token, physical.locked_by)) is None:
                raise JobFenceRejectedError("NATIVE_PARSER_RETRY_PHYSICAL_CAS_REJECTED")
            before()
            self.queue.update_status(job["job_id"], JobStatus.QUEUED, delivery_state=JobDeliveryState.RETRYING,
                expected_version=physical.version, fence_token=physical.fence_token, error_message=RETRY_CODE)
            before()
            if self.engine.execute("UPDATE durable_jobs SET available_at=? WHERE job_id=? AND version=? AND fence_token=? "
                    "AND attempts=? AND status=? AND delivery_state=? AND locked_by IS NULL AND lease_expires_at IS NULL",
                    (due.isoformat(), job["job_id"], physical.version+1, physical.fence_token, physical.attempts,
                        JobStatus.QUEUED.value, JobDeliveryState.RETRYING.value)).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_PARSER_RETRY_AVAILABILITY_CAS_REJECTED")
            before()
            return receipt


@dataclass(frozen=True)
class NativeParserPreparedRetryFacts:
    """Audited prior facts only, usable after a later terminal cancellation."""
    receipt: dict
    acceptance: dict
    prior: object
    combined: object


def prepared_parser_retry_facts(history, job, run, physical, *, before):
    """Prove the actual preparation, independently of today's job status."""
    payload, tenant = _object(job["payload"]), job["tenant_id"]
    receipt = _object(payload["retry_receipt"])
    attempts = NativeParserAttemptHistory(history.graph)
    acceptance = attempts.acceptance_record(job, run, _object(payload["acceptance_receipt"]),
        _object(payload["accepted_binding"]), attempt=job["attempt"], before=before)
    original = history._parent_input(tenant, payload["admission"]["binding"], before)
    prior = attempts.previous(job, run, physical, original, before=before)
    current = closed_parser_attempt_journal(run, payload["admission"], original, acceptance, prior.current_items,
        _time(receipt["prepared_at"]), timeout_at=job["timeout_at"], before=before)
    attempts.retry_record(job, run, receipt, acceptance, prior.current_items, current,
        max_physical_attempts=physical.max_retries, before=before)
    combined = combine_closed_parser_journals((*prior.journals, current), payload["attempt_intents"], before=before)
    require_closed_slots(history.engine, combined, before)
    before.check_in_memory()
    return NativeParserPreparedRetryFacts(receipt, acceptance, prior, combined)


def require_prepared_parser_retry_state(history, job, run, physical, facts, *, before):
    """Bind those historical facts to a genuine currently queued/claimed retry."""
    payload, receipt = _object(job["payload"]), facts.receipt
    if (set(payload) != active_payload_fields(job["attempt"]) | {"retry_receipt"}
            or job["status"] != "RETRYING" or run["status"] != "QUEUED" or job["result"] is not None
            or job["lease_expires_at"] is not None or job["cancellation_requested_at"] is not None or job["checkpoint"] != "PARSING"
            or job["version"] != receipt["business_version"] or job["fence_token"] != receipt["business_fence"]
            or run["version"] != receipt["run_version_after"] or run["completed_at"] is not None
            or run["parsed_payload"] is not None or run["normalized_payload"] is not None
            or _object(run["field_confidence"]) != {}
            or (json.loads(run["validation_errors"]) if isinstance(run["validation_errors"], str) else run["validation_errors"]) != []
            or canonical_json(_object(job["last_error"])) != canonical_json({"code": RETRY_CODE, "error_class": receipt["error_class"]})
            or _time(job["updated_at"]) != _time(receipt["prepared_at"]) or _time(job["heartbeat_at"]) != _time(receipt["prepared_at"])
            or _time(job["next_attempt_at"]) != _time(receipt["available_at"])
            or physical.delivery_state != JobDeliveryState.RETRYING or physical.error_message != RETRY_CODE
            or physical.leased_until is not None):
        reject()
    if physical.status == JobStatus.QUEUED:
        if (physical.version != receipt["physical_version"] or physical.fence_token != receipt["physical_fence"]
                or physical.attempts != receipt["physical_attempts"]
                or any(getattr(physical, k) is not None for k in ("locked_by", "heartbeat_at", "lease_expires_at"))):
            reject()
    elif physical.status == JobStatus.RUNNING:
        claims = physical.fence_token-receipt["physical_fence"]
        if (claims < 1 or physical.attempts-receipt["physical_attempts"] != claims
                or physical.version-receipt["physical_version"] < claims
                or not physical.locked_by or physical.heartbeat_at is None or physical.lease_expires_at is None
                or physical.heartbeat_at < _time(receipt["available_at"])):
            reject()
    else:
        reject()
    before()
    row = history.engine.query_one("SELECT available_at FROM durable_jobs WHERE job_id=?", (job["job_id"],))
    if row is None or _time(row["available_at"]) != _time(receipt["available_at"]):
        reject()
    before.check_in_memory()
    return NativeParserPreparedRetry(run["parser_run_id"], canonical_sha256(receipt), canonical_json(receipt))


def read_prepared_parser_retry(history, job, run, physical, *, before):
    """Prove a queued/claimed prepared retry without restoring positive grants."""
    facts = prepared_parser_retry_facts(history, job, run, physical, before=before)
    return require_prepared_parser_retry_state(history, job, run, physical, facts, before=before)
