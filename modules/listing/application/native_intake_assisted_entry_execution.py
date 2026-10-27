"""Fresh manual execution reads and durable acceptance before external IO."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from modules.listing.application.native_intake_assisted_entry_job_proof import (
    assisted_job_provenance,
)
from modules.listing.application.native_intake_assisted_entry_policy import (
    JOB_TYPE,
    native_intake_assisted_entry_policy,
)
from modules.listing.application.native_intake_assisted_entry_request_proof import _object, _time
from modules.listing.application.native_intake_pending_commands import NativeIntakeCommandBudget
from modules.listing.application.native_intake_retrieval_policy import canonical_json
from shared.jobs.queue import JobFenceRejectedError, JobStatus


@dataclass(frozen=True)
class NativeAssistedExecutionState:
    physical: Any
    job: dict
    payload: dict
    before: NativeIntakeCommandBudget
    intake: dict
    source: dict
    classified: Any
    principal: Any
    request: Any


class NativeAssistedExecutionReads:
    def __init__(self, owner):
        self.owner, self.graph, self.engine, self.queue = owner, owner.graph, owner.engine, owner.queue

    def _rows(self, delivery, tenant, generation, before, *, settlement, business_fence=None):
        before()
        self.graph._one("SELECT job_id FROM durable_jobs WHERE job_id=? FOR UPDATE", (delivery.job_id,))
        before()
        physical = self.queue.get(delivery.job_id)
        now = self.owner._clock()
        if (physical is None or physical.job_type != JOB_TYPE or physical.status != JobStatus.RUNNING
                or not physical.locked_by or physical.locked_by != delivery.locked_by
                or physical.fence_token != delivery.fence_token or physical.payload != delivery.payload
                or physical.correlation_id != delivery.correlation_id
                or physical.lease_expires_at is None or physical.lease_expires_at <= now):
            raise JobFenceRejectedError("NATIVE_ASSISTED_PHYSICAL_NOT_OWNED")
        before.deadline = min(before.deadline, physical.lease_expires_at)
        before()
        job = self.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=? FOR UPDATE",
            (tenant, delivery.job_id))
        payload = _object(job["payload"])
        if (job["job_type"] != JOB_TYPE or job["status"] not in {"QUEUED", "RETRYING", "RUNNING"}
                or job["result"] is not None or job["checkpoint"] != "AWAITING_ASSISTED_ENTRY"
                or type(payload.get("delivery_generation")) is not int or payload["delivery_generation"] != generation
                or (business_fence is not None and job["fence_token"] != business_fence)
                or type(job["fence_token"]) is not int or job["fence_token"] < 1
                or type(job["attempt"]) is not int or not 0 <= job["attempt"] <= job["max_attempts"]
                or job["max_attempts"] != payload["policy"]["max_attempts"]
                or physical.max_retries != job["max_attempts"] or physical.attempts < max(1, job["attempt"])):
            raise JobFenceRejectedError("NATIVE_ASSISTED_BUSINESS_NOT_OWNED")
        if job["fence_token"] != job["attempt"] + 1:
            raise JobFenceRejectedError("NATIVE_ASSISTED_ATTEMPT_FENCE_REJECTED")
        if job["status"] == "QUEUED" and (job["attempt"] != 0 or any(key in payload for key in (
                "acceptance_receipt", "accepted_worker", "accepted_delivery_fence", "retry_receipt"))):
            raise JobFenceRejectedError("NATIVE_ASSISTED_INITIAL_JOB_REJECTED")
        if job["status"] == "RUNNING":
            if (job["lease_expires_at"] is None or _time(job["lease_expires_at"]) <= now
                    or payload.get("accepted_worker") != physical.locked_by
                    or payload.get("accepted_delivery_fence") != physical.fence_token
                    or job["attempt"] < 1):
                raise JobFenceRejectedError("NATIVE_ASSISTED_DUAL_LEASE_NOT_OWNED")
            before.deadline = min(before.deadline, _time(job["lease_expires_at"]))
        elif job["lease_expires_at"] is not None or job["attempt"] >= job["max_attempts"]:
            raise JobFenceRejectedError("NATIVE_ASSISTED_UNACCEPTED_JOB_REJECTED")
        if not settlement:
            if physical.attempts > physical.max_retries:
                raise JobFenceRejectedError("NATIVE_ASSISTED_PHYSICAL_ATTEMPTS_EXHAUSTED")
            if job["status"] == "RETRYING" and (job["next_attempt_at"] is None or _time(job["next_attempt_at"]) > now):
                raise JobFenceRejectedError("NATIVE_ASSISTED_RETRY_NOT_DUE")
            if job["cancellation_requested_at"] is not None:
                raise JobFenceRejectedError("NATIVE_ASSISTED_CANCELLATION_REQUESTED")
            before.deadline = min(before.deadline, _time(job["timeout_at"]))
        before()
        return physical, job, payload

    def historical_locked(self, delivery, tenant, generation, *, business_fence=None):
        """Negative ownership only; no current key/config/human/source grant."""
        before = NativeIntakeCommandBudget(self.owner.source.source)
        physical, job, payload = self._rows(delivery, tenant, generation, before,
            settlement=True, business_fence=business_fence)
        self.owner.jobs.admission(job, before=before)
        if job["status"] == "RUNNING":
            self.owner.jobs.acceptance(job, before=before)
        elif job["status"] == "RETRYING":
            retry = self.owner.jobs.retry(job, before=before)
            if _time(job["next_attempt_at"]) != _time(retry["available_at"]):
                raise JobFenceRejectedError("NATIVE_ASSISTED_RETRY_SCHEDULE_REJECTED")
        return physical, job, payload, before

    def fresh(self, delivery, tenant, generation, *, settlement=False, business_fence=None):
        return self._positive_read(delivery, tenant, generation, settlement=settlement,
            business_fence=business_fence, allow_source_change=False)

    def retry_context(self, delivery, tenant, generation, *, business_fence):
        # Retry preparation performs no IO and may observe a changed source
        # that still has an explicit current positive manual grant.
        return self._positive_read(delivery, tenant, generation, settlement=False,
            business_fence=business_fence, allow_source_change=True)

    def _positive_read(self, delivery, tenant, generation, *, settlement, business_fence, allow_source_change):
        before = NativeIntakeCommandBudget(self.owner.source.source)
        physical, job, payload = self._rows(delivery, tenant, generation, before,
            settlement=settlement, business_fence=business_fence)
        before()
        intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE",
            (tenant, payload["intake_id"]))
        _, request, parent = self.owner.jobs.admission_with_request(job, before=before)
        if job["status"] == "RUNNING":
            self.owner.jobs.acceptance(job, before=before)
        elif job["status"] == "RETRYING":
            retry = self.owner.jobs.retry(job, before=before)
            if _time(job["next_attempt_at"]) != _time(retry["available_at"]):
                raise JobFenceRejectedError("NATIVE_ASSISTED_RETRY_SCHEDULE_REJECTED")
        if self.owner.auth.decode(physical) != (tenant, generation):
            raise JobFenceRejectedError("NATIVE_ASSISTED_CURRENT_KEY_REJECTED")
        policy = native_intake_assisted_entry_policy(tenant)
        self.owner._binding(intake, request, parent, policy)
        principal = self.owner._human(request, intake, before)
        source, classified = self.owner.source.current_manual_source(intake, before=before)
        before.deadline = min(before.deadline, classified.valid_until)
        if job["status"] == "RUNNING":
            if hashlib.sha256(self.owner._document(request, intake)).hexdigest() != payload["accepted_document_sha256"]:
                raise JobFenceRejectedError("NATIVE_ASSISTED_ACCEPTED_DOCUMENT_CHANGED")
            if not allow_source_change:
                self.owner._positive_source(payload, classified)
        before()
        return NativeAssistedExecutionState(physical, job, payload, before, intake, source, classified, principal, request)

    def accept(self, delivery, tenant, generation):
        with self.graph.transaction(tenant):
            state = self.fresh(delivery, tenant, generation)
            physical, job, payload, before = state.physical, state.job, state.payload, state.before
            if job["status"] not in {"QUEUED", "RETRYING"} or job["attempt"] >= job["max_attempts"]:
                raise JobFenceRejectedError("NATIVE_ASSISTED_ACCEPTANCE_NOT_CURRENT")
            now, policy = self.owner._clock(), native_intake_assisted_entry_policy(tenant)
            if policy.sha256 != payload["policy_sha256"]:
                raise JobFenceRejectedError("NATIVE_ASSISTED_ACCEPTANCE_POLICY_CHANGED")
            document = self.owner._document(state.request, state.intake)
            acceptance = {"job_id": job["job_id"], "request_id": payload["request_id"], "intake_id": payload["intake_id"],
                "accepted_at": now.isoformat(), "business_fence": job["fence_token"] + 1,
                "physical_fence": physical.fence_token, "worker": physical.locked_by,
                "attempt": job["attempt"] + 1, "audit_event_id": str(uuid4())}
            accepted = {**payload, "accepted_worker": physical.locked_by,
                "accepted_delivery_fence": physical.fence_token,
                "acceptance_receipt": acceptance,
                "accepted_source_sha256": state.classified.source_snapshot_sha256,
                "accepted_source_version": state.source["version"],
                "accepted_authority": self.owner._authority_snapshot(state.principal, now),
                "accepted_document_sha256": hashlib.sha256(document).hexdigest()}
            before()
            if self.engine.execute("UPDATE workflow.jobs SET status='RUNNING',attempt=attempt+1,fence_token=fence_token+1,"
                    "payload=?::jsonb,version=version+1,heartbeat_at=?,lease_expires_at=?,updated_at=? "
                    "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND status IN ('QUEUED','RETRYING') "
                    "AND result IS NULL AND attempt<max_attempts AND cancellation_requested_at IS NULL "
                    "AND timeout_at>clock_timestamp() AND ?::timestamptz>clock_timestamp() "
                    "AND ?::timestamptz>clock_timestamp()",
                    (canonical_json(accepted), now.isoformat(), physical.lease_expires_at.isoformat(), now.isoformat(),
                        tenant, job["job_id"], job["version"], job["fence_token"],
                        physical.lease_expires_at.isoformat(), state.classified.valid_until.isoformat())).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_ASSISTED_ACCEPTANCE_CAS_REJECTED")
            before()
            self.owner._audit(state.intake, "intake.assisted_entry_attempt_accepted.v1",
                {"job_id": job["job_id"], "status": job["status"]},
                {**acceptance, "status": "RUNNING", "intake_mutated": False,
                    "native_assisted_provenance": assisted_job_provenance(payload),
                    "acceptance": {key: accepted[key] for key in ("accepted_source_sha256", "accepted_source_version",
                        "accepted_authority", "accepted_document_sha256")}},
                now, audit_id=acceptance["audit_event_id"], causation=payload["event_id"])
            before()
            if (native_intake_assisted_entry_policy(tenant) != policy
                    or self.owner.auth.decode(self.queue.get(job["job_id"])) != (tenant, generation)):
                raise JobFenceRejectedError("NATIVE_ASSISTED_ACCEPTANCE_CONFIG_CHANGED")
            before()
            return job["fence_token"] + 1, policy
