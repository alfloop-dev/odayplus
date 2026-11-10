"""Own the retrieval lifecycle; generic worker code never infers success."""

import hashlib
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException

from modules.external_data.application.native_intake_process import (
    NativeIORejected,
    NativeIOUnreaped,
)
from modules.listing.application.native_intake_retrieval import (
    NativeRetrievalQuarantineRequired,
    NativeRetrievalSourceChanged,
    _object,
    _time,
)
from modules.listing.application.native_intake_retrieval_io import NativeIntakeRetrievalIO
from modules.listing.application.native_intake_retrieval_leases import (
    NativeRetrievalCompositeLeases,
)
from modules.listing.application.native_intake_retrieval_policy import (
    canonical_json,
    native_intake_retrieval_policy,
)
from modules.listing.application.native_intake_retrieval_quota import NativeRetrievalQuotaExceeded
from modules.listing.application.native_intake_retrieval_results import (
    commit_result,
    committed_result,
    dead_letter,
)
from shared.audit.errors import CanonicalAuditUnavailable
from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError, JobStatus

HTTP_QUARANTINE_CODES = {
    "NATIVE_HTTP_URL_REJECTED": "NATIVE_RETRIEVAL_DESTINATION_PROHIBITED",
    "NATIVE_HTTP_ADDRESS_REJECTED": "NATIVE_RETRIEVAL_DESTINATION_PROHIBITED",
    "NATIVE_HTTP_REDIRECT_REJECTED": "NATIVE_RETRIEVAL_REDIRECT_PROHIBITED",
    "NATIVE_HTTP_MEDIA_TYPE_REJECTED": "NATIVE_RETRIEVAL_CONTENT_PROHIBITED",
}


def _retryable(error):
    return (getattr(error, "sqlstate", None) in {"40001", "40P01", "57014"}
        or (isinstance(error, JobFenceRejectedError) and str(error) == "NATIVE_RETRIEVAL_ROOT_BUDGET_EXPIRED")
        or isinstance(error, (CanonicalAuditUnavailable, NativeRetrievalQuotaExceeded, NativeRetrievalSourceChanged))
        or (isinstance(error, NativeIORejected) and (error.status in {408, 429} or (error.status or 0) >= 500
            or error.code in {"NATIVE_IO_DEADLINE_EXPIRED", "NATIVE_IO_CONNECTION_FAILED", "NATIVE_IO_CHILD_FAILED"})))


def _safe_failure(error):
    """Operational cause evidence without exception/driver/source-page text."""
    causes, seen, current = [], set(), error
    while current is not None and id(current) not in seen and len(causes) < 8:
        seen.add(id(current))
        sqlstate = getattr(current, "sqlstate", None)
        if (not isinstance(sqlstate, str) or len(sqlstate) != 5
                or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" for c in sqlstate)):
            sqlstate = None
        code = getattr(current, "code", None)
        if code is None:
            candidate = str(current)
            if (candidate.startswith("NATIVE_") and len(candidate) <= 96
                    and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for c in candidate)):
                code = candidate
        if (not isinstance(code, str) or not code.startswith("NATIVE_") or len(code) > 96
                or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for c in code)):
            code = None
        causes.append({"error_class": type(current).__name__[:96], "sqlstate": sqlstate, "code": code})
        current = current.__cause__
    return {"code": "NATIVE_RETRIEVAL_EXECUTION_ERROR", "error_class": type(error).__name__[:96], "causes": causes}


class NativeIntakeRetrievalRuntime:
    def __init__(self, owner):
        self.owner = owner

    def _replay(self, delivery, tenant, generation, leases=None):
        if leases is not None:
            leases.pause()
        with self.owner.graph.transaction(tenant):
            return committed_result(self.owner, delivery, tenant, generation)

    def execute(self, delivery):
        owner = self.owner
        tenant, generation = owner.auth.decode(delivery)
        policy = native_intake_retrieval_policy(tenant)
        if result := self._replay(delivery, tenant, generation):
            return result
        try:
            fence, policy = owner._accept(delivery, tenant, generation)
        except Exception as error:
            # A known rejected fence cannot be interpreted as an unknown
            # successful acceptance (including a concurrent duplicate call).
            if not isinstance(error, JobFenceRejectedError):
                try:
                    with owner.graph.transaction(tenant):
                        _, job, _, _, _, _, _ = owner._fresh(delivery, tenant, generation)
                        fence = job["fence_token"]
                except Exception:
                    return self._unaccepted(delivery, tenant, generation, error)
            else:
                return self._unaccepted(delivery, tenant, generation, error)
        leases = NativeRetrievalCompositeLeases(owner.graph, delivery, business_fence=fence, policy_sha256=policy.sha256)
        io, terminal_attempted = None, False
        try:
            io = NativeIntakeRetrievalIO(owner, delivery, leases, policy)
            verified, upload, bucket = io.capture()
            terminal_attempted = True
            return commit_result(owner, delivery, leases, target="PARSING", code="NATIVE_RETRIEVAL_SNAPSHOT_VERIFIED",
                                 verified=verified, upload=upload, bucket=bucket)
        except NativeIOUnreaped:
            # This is unresolved execution, not a safely closed failure. Retain
            # its slot until conservative expiry and leave recovery visible.
            raise
        except Exception as error:
            return self._settle(delivery, tenant, generation, leases, error, io=io, terminal_attempted=terminal_attempted)
        finally:
            leases.pause()

    def _job_only(self, delivery, tenant, generation, *, cancelled, code, accepted):
        owner = self.owner
        with owner.graph.transaction(tenant):
            physical, job, payload, before = owner._locked(delivery, tenant, generation, settlement=True)
            statuses = {"RUNNING"} if accepted else {"QUEUED", "RETRYING"}
            if job["status"] not in statuses or job["result"] is not None:
                raise JobFenceRejectedError("NATIVE_RETRIEVAL_JOB_ONLY_SETTLEMENT_NOT_OWNED")
            if cancelled != (job["cancellation_requested_at"] is not None):
                raise JobFenceRejectedError("NATIVE_RETRIEVAL_CANCELLATION_CHANGED")
            before()
            intake = owner.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE",
                                      (tenant, job["aggregate_id"]))
            status, at = "CANCELLED" if cancelled else "FAILED", owner._clock()
            before()
            if owner.engine.execute("UPDATE workflow.jobs SET status=?,last_error=?::jsonb,lease_expires_at=NULL,"
                "heartbeat_at=clock_timestamp(),version=version+1,updated_at=clock_timestamp() "
                "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND result IS NULL "
                "AND (?::boolean OR cancellation_requested_at IS NULL)",
                (status, canonical_json({"code": code, "error_class": "NativeRetrievalFailure"}), tenant,
                 job["job_id"], job["version"], job["fence_token"], cancelled)).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_RETRIEVAL_JOB_ONLY_CAS_REJECTED")
            before()
            owner._audit(intake, "intake.retrieval_delivery_settled.v1", {"job_id": job["job_id"], "status": job["status"]},
                {"job_id": job["job_id"], "status": status, "code": code, "intake_mutated": False}, at,
                audit_id=str(uuid4()), causation=payload["event_id"])
            before()
            owner.queue.update_status(job["job_id"], JobStatus.CANCELLED if cancelled else JobStatus.FAILED,
                delivery_state=None if cancelled else JobDeliveryState.DEAD_LETTER,
                expected_version=physical.version, fence_token=physical.fence_token, error_message=code)
            if not cancelled:
                before()
                # Unaccepted work has no invented business attempt. The
                # dead-letter event records the actual claimed delivery count.
                dead_letter(owner, job, payload, code, at, checkpoint=job["checkpoint"], version=job["version"] + 1,
                            attempt=job["attempt"] if accepted else physical.attempts)
            before()
        return None

    def _unaccepted(self, delivery, tenant, generation, error):
        if result := self._replay(delivery, tenant, generation):
            return result
        with self.owner.graph.transaction(tenant):
            _, job, _, _ = self.owner._locked(delivery, tenant, generation, settlement=True)
            if job["status"] not in {"QUEUED", "RETRYING"}:
                raise error
            cancelled = job["cancellation_requested_at"] is not None
        # No durable accepted business lease exists, so even total timeout
        # cannot authorize an Intake FAILED transition in this path.
        return self._job_only(delivery, tenant, generation, cancelled=cancelled,
            code="NATIVE_RETRIEVAL_CANCELLED" if cancelled else "NATIVE_RETRIEVAL_ACCEPTANCE_REJECTED", accepted=False)

    def _retry(self, delivery, tenant, generation, leases, due):
        owner = self.owner
        with leases.root():
            physical, job, payload, before, intake, _, classified = owner._fresh(delivery, tenant, generation,
                business_fence=leases.business_fence)
            if classified.target_state != "RETRIEVING" or classified.effective_policy != "APPROVED_RETRIEVAL":
                raise NativeRetrievalQuarantineRequired("NATIVE_RETRIEVAL_CURRENT_SOURCE_DENIED")
            if not self._due(job, payload, owner._clock()) or _time(job["timeout_at"]) <= due:
                raise JobFenceRejectedError("NATIVE_RETRIEVAL_RETRY_BUDGET_CHANGED")
            token = owner.auth.sign(job["job_id"], tenant, generation + 1)
            updated = {**payload, "delivery_generation": generation + 1,
                       "attempt_token_sha256": hashlib.sha256(token.encode()).hexdigest()}
            at, code = owner._clock(), "NATIVE_RETRIEVAL_RETRY_SCHEDULED"
            before()
            if owner.engine.execute("UPDATE workflow.jobs SET status='RETRYING',next_attempt_at=?,payload=?::jsonb,"
                "lease_expires_at=NULL,last_error=?::jsonb,version=version+1,updated_at=clock_timestamp() "
                "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND status='RUNNING' "
                "AND cancellation_requested_at IS NULL AND lease_expires_at>clock_timestamp() AND timeout_at>?::timestamptz",
                (due.isoformat(), canonical_json(updated), canonical_json({**_object(job.get("last_error") or {}), "code": code}), tenant, job["job_id"],
                 job["version"], job["fence_token"], due.isoformat())).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_RETRIEVAL_RETRY_CAS_REJECTED")
            before()
            owner._audit(intake, "intake.retrieval_delivery_settled.v1", {"job_id": job["job_id"], "status": "RUNNING"},
                {"job_id": job["job_id"], "status": "RETRYING", "next_attempt_at": due.isoformat(), "code": code},
                at, audit_id=str(uuid4()), causation=payload["event_id"])
            before()
            owner.queue.update_status(job["job_id"], JobStatus.QUEUED,
                payload={"job_id": job["job_id"], "tenant_id": tenant, "attempt_token": token},
                delivery_state=JobDeliveryState.RETRYING, expected_version=physical.version, fence_token=physical.fence_token,
                error_message=code)
            before()
            owner.engine.execute("UPDATE durable_jobs SET available_at=? WHERE job_id=?", (due.isoformat(), job["job_id"]))
            before()
        return None

    @staticmethod
    def _due(job, payload, now):
        if job["attempt"] >= job["max_attempts"] or _time(job["timeout_at"]) <= now:
            return None
        due = now + timedelta(seconds=payload["policy"]["backoff_seconds"][job["attempt"] - 1])
        return due if due < _time(job["timeout_at"]) else None

    def _settle(self, delivery, tenant, generation, leases, error, *, io=None, terminal_attempted=False):
        owner = self.owner
        if result := self._replay(delivery, tenant, generation, leases):
            return result
        with leases.root():
            _, job, payload, before = owner._locked(delivery, tenant, generation,
                settlement=True, business_fence=leases.business_fence)
            if job["status"] != "RUNNING":
                raise JobFenceRejectedError("NATIVE_RETRIEVAL_ACCEPTED_SETTLEMENT_REQUIRED")
            cancelled = job["cancellation_requested_at"] is not None
            before()
            if owner.engine.execute("UPDATE workflow.jobs SET last_error=?::jsonb,version=version+1,updated_at=clock_timestamp() "
                "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND status='RUNNING' "
                "AND result IS NULL AND lease_expires_at>clock_timestamp()",
                (canonical_json(_safe_failure(error)), tenant, job["job_id"], job["version"], job["fence_token"])).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_RETRIEVAL_ERROR_EVIDENCE_FENCE_REJECTED")
            before()
        if cancelled:
            return self._job_only(delivery, tenant, generation, cancelled=True, code="NATIVE_RETRIEVAL_CANCELLED", accepted=True)
        try:
            with leases.root():
                _, job, payload, _, _, _, classified = owner._fresh(delivery, tenant, generation,
                    settlement=True, business_fence=leases.business_fence)
                now = owner._clock()
        except (PermissionError, JobFenceRejectedError, HTTPException):
            # Still-owned service settlement may close this job; it cannot
            # mutate an Intake using revoked actor/input authority.
            return self._job_only(delivery, tenant, generation, cancelled=False,
                code="NATIVE_RETRIEVAL_CURRENT_AUTHORITY_LOST", accepted=True)
        if classified.target_state != "RETRIEVING" or isinstance(error, NativeRetrievalQuarantineRequired):
            code = str(error) if isinstance(error, NativeRetrievalQuarantineRequired) else "NATIVE_RETRIEVAL_CURRENT_SOURCE_DENIED"
            return commit_result(owner, delivery, leases, target="QUARANTINED", code=code)
        quarantine = (HTTP_QUARANTINE_CODES.get(error.code)
            if isinstance(error, NativeIORejected) and isinstance(error.code, str) else None)
        if quarantine:
            return commit_result(owner, delivery, leases, target="QUARANTINED", code=quarantine)
        if io is not None and not terminal_attempted:
            try:
                io.cleanup_known_unreferenced()
            except NativeIOUnreaped:
                raise
            except (NativeIORejected, JobFenceRejectedError, NativeRetrievalSourceChanged):
                # An unresolved cleanup stays in its durable intent. Never
                # claim deletion or weaken the terminal root's fresh guards.
                pass
        if _retryable(error) and (due := self._due(job, payload, now)):
            return self._retry(delivery, tenant, generation, leases, due)
        return commit_result(owner, delivery, leases, target="FAILED", code="NATIVE_RETRIEVAL_FAILED")
