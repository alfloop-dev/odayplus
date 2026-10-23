"""Prepare a fresh authorized retry without authorizing external IO."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from modules.listing.application.native_intake_assisted_entry_execution import (
    NativeAssistedExecutionReads,
)
from modules.listing.application.native_intake_assisted_entry_job_proof import (
    assisted_job_provenance,
)
from modules.listing.application.native_intake_assisted_entry_policy import (
    native_intake_assisted_entry_policy,
)
from modules.listing.application.native_intake_assisted_entry_request_proof import _time
from modules.listing.application.native_intake_retrieval_policy import canonical_json
from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError, JobStatus


def prepare_assisted_retry(owner, delivery, leases, *, error_class):
    tenant, generation = owner.delivery_identity(delivery)
    with leases.root():
        state = NativeAssistedExecutionReads(owner).retry_context(delivery, tenant, generation,
            business_fence=leases.business_fence)
        physical, job, payload, before = state.physical, state.job, state.payload, state.before
        if (job["status"] != "RUNNING" or job["attempt"] >= job["max_attempts"]
                or physical.attempts >= physical.max_retries):
            raise JobFenceRejectedError("NATIVE_ASSISTED_RETRY_EXHAUSTED")
        now = owner._clock()
        due = now + timedelta(seconds=payload["policy"]["backoff_seconds"][job["attempt"] - 1])
        # A reaped process whose finish root lost permission may still own an
        # unreleased anonymous slot. Preserve it until its real reservation
        # expiry instead of spending the next bounded attempt on that slot.
        retained = [_time(item["reservation"]["expires_at"]) for item in payload["attempt_intents"]
            if item["state"] == "ADMITTED"]
        due = max([due, *retained])
        if due >= _time(job["timeout_at"]):
            raise JobFenceRejectedError("NATIVE_ASSISTED_ORIGINAL_RETRY_DEADLINE_EXHAUSTED")
        receipt = {"job_id": job["job_id"], "request_id": payload["request_id"], "intake_id": payload["intake_id"],
            "prepared_at": now.isoformat(), "available_at": due.isoformat(), "business_fence": job["fence_token"],
            "physical_fence": physical.fence_token, "worker": physical.locked_by, "attempt": job["attempt"],
            "audit_event_id": str(uuid4()), "acceptance_audit_event_id": payload["acceptance_receipt"]["audit_event_id"],
            "source_sha256": state.classified.source_snapshot_sha256, "source_version": state.source["version"],
            "authority": owner._authority_snapshot(state.principal, now)}
        # Generation/token SHA remain the immutable admitted occurrence. Only
        # the next acceptance advances the business fence/attempt. Every new
        # capture uses its own random key and both then-current fences.
        updated = {**payload, "retry_receipt": receipt}
        code = "NATIVE_ASSISTED_RETRY_SCHEDULED"
        before()
        if owner.engine.execute("UPDATE workflow.jobs SET status='RETRYING',next_attempt_at=?,payload=?::jsonb,"
                "lease_expires_at=NULL,last_error=?::jsonb,version=version+1,updated_at=? "
                "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND status='RUNNING' "
                "AND result IS NULL AND cancellation_requested_at IS NULL AND lease_expires_at>clock_timestamp() "
                "AND timeout_at>?::timestamptz AND ?::timestamptz>clock_timestamp()",
                (due.isoformat(), canonical_json(updated), canonical_json({"code": code, "error_class": error_class}),
                    now.isoformat(), tenant, job["job_id"], job["version"], job["fence_token"], due.isoformat(),
                    state.classified.valid_until.isoformat())).rowcount != 1:
            raise JobFenceRejectedError("NATIVE_ASSISTED_RETRY_BUSINESS_CAS_REJECTED")
        before()
        owner._audit(state.intake, "intake.assisted_entry_delivery_settled.v1", {"job_id": job["job_id"], "status": "RUNNING"},
            {**receipt, "status": "RETRYING", "intake_mutated": False, "native_assisted_provenance": assisted_job_provenance(payload)},
            now, audit_id=receipt["audit_event_id"], causation=payload["request_id"])
        before()
        if (native_intake_assisted_entry_policy(tenant).sha256 != payload["policy_sha256"]
                or owner.auth.decode(owner.queue.get(job["job_id"])) != (tenant, generation)
                or not owner.engine.query_one("SELECT job_id FROM durable_jobs WHERE job_id=? AND status=? "
                    "AND fence_token=? AND version=? AND locked_by=? AND lease_expires_at>clock_timestamp()",
                    (job["job_id"], JobStatus.RUNNING.value, physical.fence_token, physical.version, physical.locked_by))):
            raise JobFenceRejectedError("NATIVE_ASSISTED_RETRY_FINAL_PHYSICAL_OR_CONFIG_REJECTED")
        before()
        owner.queue.update_status(job["job_id"], JobStatus.QUEUED, delivery_state=JobDeliveryState.RETRYING,
            expected_version=physical.version, fence_token=physical.fence_token, error_message=code)
        before()
        owner.engine.execute("UPDATE durable_jobs SET available_at=? WHERE job_id=?", (due.isoformat(), job["job_id"]))
        before()
        return None
