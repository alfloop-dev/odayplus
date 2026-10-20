"""Owned parser acceptance and fresh bounded SQL execution reads.

No SQL transaction spans external IO. These methods do not execute a child,
settle a result or grant production authority. Retried ownership needs the
complete actual previous-attempt proof, never a fabricated historical row.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from modules.listing.application.native_intake_assisted_entry_request_proof import _object, _time
from modules.listing.application.native_intake_parser_job_proof import parser_admission_provenance
from modules.listing.application.native_intake_parser_policy import JOB_TYPE
from modules.listing.application.native_intake_parser_prerequisites import (
    NativeParserCommandBudget,
)
from modules.listing.application.native_intake_retrieval_policy import (
    canonical_json,
    canonical_sha256,
)
from shared.jobs.queue import JobFenceRejectedError, JobStatus


@dataclass(frozen=True)
class NativeParserExecutionState:
    physical: object
    job: dict
    payload: dict
    run: dict
    before: NativeParserCommandBudget
    context: object


class NativeParserExecutionReads:
    def __init__(self, owner):
        self.owner, self.graph, self.engine, self.queue = owner, owner.graph, owner.engine, owner.queue

    def historical_locked(self, delivery, tenant, generation, *, business_fence=None):
        """Current ownership and immutable attempts, without positive grants."""
        before = NativeParserCommandBudget(self.owner.clock)
        before()
        self.graph._one("SELECT job_id FROM durable_jobs WHERE job_id=? FOR UPDATE", (delivery.job_id,))
        before()
        physical = self.queue.get(delivery.job_id)
        now = self.owner.clock._clock()
        if (physical is None or physical.job_type != JOB_TYPE or physical.status != JobStatus.RUNNING
                or not physical.locked_by or physical.locked_by != delivery.locked_by
                or physical.fence_token != delivery.fence_token or physical.payload != delivery.payload
                or physical.correlation_id != delivery.correlation_id
                or physical.lease_expires_at is None or physical.lease_expires_at <= now):
            raise JobFenceRejectedError("NATIVE_PARSER_PHYSICAL_NOT_OWNED")
        # claim_next/heartbeat own lease_expires_at. The legacy leased_until
        # field is NULL on a genuine PostgreSQL claim and is not a third lease.
        before.deadline = min(before.deadline, physical.lease_expires_at)
        before()
        job = self.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=? FOR UPDATE",
            (tenant, delivery.job_id))
        payload = _object(job["payload"])
        if (job["job_type"] != JOB_TYPE or job["status"] not in {"QUEUED", "RUNNING", "RETRYING"}
                or job["checkpoint"] != "PARSING" or job["result"] is not None
                or type(generation) is not int or payload["delivery_generation"] != generation
                or (business_fence is not None and job["fence_token"] != business_fence)
                or type(job["attempt"]) is not int or not 0 <= job["attempt"] <= job["max_attempts"]
                or job["max_attempts"] != payload["admission"]["policy"]["max_attempts"]
                or job["fence_token"] != job["attempt"] + 1
                or physical.max_retries != job["max_attempts"] or physical.attempts < max(1, job["attempt"])):
            raise JobFenceRejectedError("NATIVE_PARSER_BUSINESS_NOT_OWNED")
        run = self.owner.runs.get(tenant, job["aggregate_id"], before=before, for_update=True)
        if run is None or run["status"] != ("QUEUED" if job["status"] == "RETRYING" else job["status"]):
            raise JobFenceRejectedError("NATIVE_PARSER_RUN_NOT_OWNED")
        self.owner.jobs.admission(job, run, before=before)
        if job["status"] == "RUNNING":
            acceptance = self.owner.jobs.acceptance(job, run, before=before)
            if (job["lease_expires_at"] is None or _time(job["lease_expires_at"]) <= now
                    or acceptance["worker"] != physical.locked_by
                    or acceptance["physical_fence"] != physical.fence_token or job["attempt"] < 1):
                raise JobFenceRejectedError("NATIVE_PARSER_DUAL_LEASE_NOT_OWNED")
            before.deadline = min(before.deadline, _time(job["lease_expires_at"]))
        elif job["status"] == "RETRYING":
            from modules.listing.application.native_intake_parser_history import (
                NativeParserTerminalHistory,
            )
            from modules.listing.application.native_intake_parser_retry import (
                read_prepared_parser_retry,
            )

            read_prepared_parser_retry(NativeParserTerminalHistory(self.graph), job, run, physical, before=before)
        elif (job["attempt"] != 0 or generation != 1 or job["lease_expires_at"] is not None
                or job["next_attempt_at"] is not None or any(key in payload for key in (
                    "acceptance_receipt", "accepted_binding", "retry_receipt"))):
            raise JobFenceRejectedError("NATIVE_PARSER_INITIAL_ATTEMPT_REJECTED")
        before.check_in_memory()
        return physical, job, payload, run, before

    def fresh(self, delivery, tenant, generation, *, business_fence=None):
        physical, job, payload, run, before = self.historical_locked(
            delivery, tenant, generation, business_fence=business_fence)
        if job["cancellation_requested_at"] is not None or physical.attempts > physical.max_retries:
            raise JobFenceRejectedError("NATIVE_PARSER_EXECUTION_CANCELLED_OR_EXHAUSTED")
        before.deadline = min(before.deadline, _time(job["timeout_at"]))
        before()
        intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE",
            (tenant, run["intake_id"]))
        context = self.owner._prerequisites().verify(
            intake, run["parser_release_id"], generation=generation, before=before)
        if (context.parser_run_id != run["parser_run_id"] or context.job_id != job["job_id"]
                or canonical_sha256(context.binding()) != canonical_sha256(payload["admission"]["binding"])
                or self.owner.auth.decode(physical) != (tenant, generation)
                or context.attempt_token != physical.payload["attempt_token"]):
            raise JobFenceRejectedError("NATIVE_PARSER_CURRENT_BINDING_REJECTED")
        before.deadline = min(before.deadline, context.classified.valid_until)
        if job["status"] == "RUNNING":
            from modules.listing.application.native_intake_parser_attempts import (
                NativeParserAttemptHistory,
            )

            NativeParserAttemptHistory(self.graph).previous(job, run, physical, context.original, before=before)
        before.check_in_memory()
        return NativeParserExecutionState(physical, job, payload, run, before, context)

    def accept(self, delivery, tenant, generation):
        with self.graph.transaction(tenant):
            state = self.fresh(delivery, tenant, generation)
            physical, job, payload, run, before = state.physical, state.job, state.payload, state.run, state.before
            initial = job["status"] == "QUEUED" and job["attempt"] == 0
            retry = job["status"] == "RETRYING" and 1 <= job["attempt"] < job["max_attempts"]
            if (not (initial or retry) or generation != 1
                    or physical.attempts > physical.max_retries):
                raise JobFenceRejectedError("NATIVE_PARSER_ACCEPTANCE_NOT_CURRENT")
            at, audit_id = self.owner.clock._clock(), str(uuid4())
            if retry and at < _time(job["next_attempt_at"]):
                raise JobFenceRejectedError("NATIVE_PARSER_RETRY_NOT_DUE")
            accepted_run = self.owner.runs.accept(run, at=at, before=before)
            receipt = {"job_id": job["job_id"], "parser_run_id": run["parser_run_id"], "intake_id": run["intake_id"],
                "accepted_at": at.isoformat(), "business_fence": job["fence_token"] + 1,
                "physical_fence": physical.fence_token, "worker": physical.locked_by, "attempt": job["attempt"] + 1,
                "delivery_generation": generation, "attempt_token_sha256": payload["attempt_token_sha256"],
                "run_version_before": run["version"], "run_version_after": accepted_run["version"], "audit_event_id": audit_id}
            accepted = {**payload, "acceptance_receipt": receipt, "accepted_binding": state.context.binding()}
            if retry:
                accepted["attempt_history"] = [*payload.get("attempt_history", []), {
                    "acceptance_receipt": payload["acceptance_receipt"], "accepted_binding": payload["accepted_binding"],
                    "retry_receipt": payload["retry_receipt"]}]
                del accepted["retry_receipt"]
            before()
            if self.engine.execute("UPDATE workflow.jobs SET status='RUNNING',attempt=attempt+1,fence_token=fence_token+1,"
                    "payload=?::jsonb,version=version+1,heartbeat_at=?,lease_expires_at=?,updated_at=?,"
                    "next_attempt_at=NULL,last_error=NULL "
                    "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND status=? "
                    "AND result IS NULL AND attempt=? AND attempt<max_attempts AND cancellation_requested_at IS NULL "
                    "AND (next_attempt_at IS NULL OR next_attempt_at<=clock_timestamp()) "
                    "AND timeout_at>clock_timestamp() AND ?::timestamptz>clock_timestamp() "
                    "AND ?::timestamptz>clock_timestamp()",
                    (canonical_json(accepted), at.isoformat(), physical.lease_expires_at.isoformat(),
                        at.isoformat(), tenant, job["job_id"], job["version"], job["fence_token"], job["status"], job["attempt"],
                        physical.lease_expires_at.isoformat(),
                        state.context.classified.valid_until.isoformat())).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_PARSER_ACCEPTANCE_CAS_REJECTED")
            before()
            self.owner._audit(state.context.intake, "intake.parser_attempt_accepted.v1",
                {"job_id": job["job_id"], "status": job["status"], "parser_run_status": "QUEUED"},
                {**receipt, "status": "RUNNING", "parser_run_status": "RUNNING", "intake_mutated": False,
                    "accepted_binding": accepted["accepted_binding"],
                    "native_parser_provenance": parser_admission_provenance(payload)}, at=at, audit_id=audit_id,
                causation=state.context.original.parent_event_id)
            self.owner._confirm(state.context.intake, run["parser_release_id"], generation,
                state.context.binding(), state.context.attempt_token, before)
            before()
            return receipt["business_fence"], state.context.policy
