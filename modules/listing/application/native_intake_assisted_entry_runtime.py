"""Run the committed human command; every durable outcome belongs to its owner."""

from __future__ import annotations

from fastapi import HTTPException

from modules.external_data.application.native_assisted_snapshot_transport import (
    NativeAssistedSnapshotProofRejected,
)
from modules.external_data.application.native_intake_process import (
    NativeIORejected,
    NativeIOUnreaped,
)
from modules.listing.application.native_intake_assisted_entry import NativeAssistedSourceChanged
from modules.listing.application.native_intake_assisted_entry_execution import (
    NativeAssistedExecutionReads,
)
from modules.listing.application.native_intake_assisted_entry_io import NativeIntakeAssistedEntryIO
from modules.listing.application.native_intake_assisted_entry_leases import (
    NativeAssistedCompositeLeases,
)
from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryConflict,
    NativeAssistedEntryUnavailable,
    native_intake_assisted_entry_policy,
)
from modules.listing.application.native_intake_assisted_entry_request_proof import _object, _time
from modules.listing.application.native_intake_assisted_entry_results import commit_assisted_result
from modules.listing.application.native_intake_assisted_entry_retry import prepare_assisted_retry
from modules.listing.application.native_intake_assisted_entry_settlement import (
    NativeAssistedEntrySettlement,
)
from modules.listing.application.native_intake_pending_commands import NativeIntakeCommandBudget
from modules.listing.application.native_intake_retrieval_quota import NativeRetrievalQuotaExceeded
from shared.audit.errors import CanonicalAuditUnavailable
from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError, JobStatus


def retryable(error):
    if isinstance(error, (NativeAssistedSourceChanged, CanonicalAuditUnavailable, NativeRetrievalQuotaExceeded)):
        return True
    if isinstance(error, NativeIORejected) and (
            error.status in {408, 429} or (error.status or 0) >= 500 or error.code in {
                "NATIVE_IO_DEADLINE_EXPIRED", "NATIVE_IO_CONNECTION_FAILED", "NATIVE_IO_CHILD_FAILED"}):
        return True
    seen, pending = set(), [error]
    while pending and len(seen) < 8:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if getattr(current, "sqlstate", None) in {"40001", "40P01", "57014", "55P03"}:
            return True
        pending.extend(x for x in (current.__cause__, current.__context__) if isinstance(x, BaseException))
    return False


class NativeIntakeAssistedEntryRuntime:
    def __init__(self, owner):
        self.owner, self.settlement = owner, NativeAssistedEntrySettlement(owner)

    def execute(self, delivery):
        owner = self.owner
        tenant, generation = owner.delivery_identity(delivery)
        outcome = self.settlement.replay(delivery)
        if outcome["terminal"]:
            return outcome["result"]
        try:
            fence, policy = owner._accept(delivery, tenant, generation)
        except Exception as error:
            if isinstance(error, JobFenceRejectedError):
                # A known rejection, including duplicate execution, never
                # becomes evidence that this caller accepted the command.
                return self.settlement.negative(delivery, error)
            accepted_fence = None
            try:
                with owner.graph.transaction(tenant):
                    _, historical, _, _ = owner._historical_locked(delivery, tenant, generation)
                    if historical["status"] != "RUNNING":
                        raise JobFenceRejectedError("NATIVE_ASSISTED_ACCEPTANCE_OUTCOME_UNPROVEN")
                    accepted_fence = historical["fence_token"]
                with owner.graph.transaction(tenant):
                    state = owner._fresh(delivery, tenant, generation, business_fence=accepted_fence)
                    if state.job["status"] != "RUNNING":
                        raise JobFenceRejectedError("NATIVE_ASSISTED_ACCEPTANCE_OUTCOME_UNPROVEN")
                    fence = state.job["fence_token"]
                    policy = native_intake_assisted_entry_policy(tenant)
            except Exception:
                return self.settlement.negative(delivery, error, business_fence=accepted_fence)
        try:
            leases = NativeAssistedCompositeLeases(owner.graph, delivery, business_fence=fence, policy_sha256=policy.sha256)
        except Exception as error:
            return self.settlement.negative(delivery, error, business_fence=fence)
        io = None
        try:
            io = NativeIntakeAssistedEntryIO(owner, delivery, leases, policy)
            verified, upload, bucket = io.capture()
            # This is set before entering SQL, including a commit whose
            # response is lost. Cleanup cannot infer that such SQL rolled back.
            io.terminal_attempted = True
            return commit_assisted_result(owner, delivery, leases, verified=verified, upload=upload, bucket=bucket)
        except NativeIOUnreaped:
            raise
        except Exception as error:
            return self._settle(delivery, leases, error, io=io)
        finally:
            leases.pause()

    def _retry_prepared(self, delivery):
        """Prove an unknown retry commit; this historical proof permits no IO."""
        owner = self.owner
        tenant, generation = owner.delivery_identity(delivery)
        with owner.graph.transaction(tenant):
            before = NativeIntakeCommandBudget(owner.source.source)
            before()
            job = owner.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?", (tenant, delivery.job_id))
            if job["status"] != "RETRYING":
                return False
            payload = _object(job["payload"])
            owner.jobs.admission(job, before=before)
            receipt = owner.jobs.retry(job, before=before)
            before()
            physical = owner.queue.get(job["job_id"])
            before()
            row = owner.graph._one("SELECT available_at FROM durable_jobs WHERE job_id=?", (job["job_id"],))
            if (physical is None or physical.status != JobStatus.QUEUED or physical.delivery_state != JobDeliveryState.RETRYING
                    or physical.payload != delivery.payload or payload["delivery_generation"] != generation
                    or physical.fence_token != receipt["physical_fence"] or physical.locked_by is not None
                    or physical.lease_expires_at is not None or physical.leased_until is not None
                    or job["lease_expires_at"] is not None or job["result"] is not None
                    or job["checkpoint"] != "AWAITING_ASSISTED_ENTRY"
                    or physical.error_message != "NATIVE_ASSISTED_RETRY_SCHEDULED"
                    or _time(job["updated_at"]) != _time(receipt["prepared_at"])
                    or _time(job["next_attempt_at"]) != _time(receipt["available_at"])
                    or _time(row["available_at"]) != _time(receipt["available_at"])
                    or physical.max_retries != job["max_attempts"] or physical.attempts >= physical.max_retries):
                raise JobFenceRejectedError("NATIVE_ASSISTED_UNKNOWN_RETRY_COMMIT_UNPROVEN")
            before()
            return True

    def _settle(self, delivery, leases, error, *, io):
        owner = self.owner
        outcome = self.settlement.replay(delivery, leases=leases)
        if outcome["terminal"]:
            return outcome["result"]
        tenant, generation = owner.delivery_identity(delivery)
        try:
            with leases.root():
                state = NativeAssistedExecutionReads(owner).retry_context(delivery, tenant, generation,
                    business_fence=leases.business_fence)
                can_retry = (state.job["status"] == "RUNNING" and state.job["attempt"] < state.job["max_attempts"]
                    and state.physical.attempts < state.physical.max_retries)
        except (PermissionError, JobFenceRejectedError, HTTPException, NativeAssistedEntryConflict, NativeAssistedEntryUnavailable):
            return self.settlement.negative(delivery, error, business_fence=leases.business_fence, leases=leases)
        if io is not None and not io.terminal_attempted:
            try:
                io.cleanup_known_unreferenced()
            except NativeIOUnreaped:
                raise
            except HTTPException as cleanup_error:
                if cleanup_error.status_code not in {401, 403}:
                    raise
                return self.settlement.negative(delivery, cleanup_error,
                    business_fence=leases.business_fence, leases=leases)
            except (PermissionError, NativeAssistedEntryUnavailable) as cleanup_error:
                return self.settlement.negative(delivery, cleanup_error,
                    business_fence=leases.business_fence, leases=leases)
            except (NativeIORejected, NativeAssistedSnapshotProofRejected, JobFenceRejectedError,
                    NativeAssistedEntryConflict, NativeRetrievalQuotaExceeded):
                # The durable journal retains the actual unresolved cleanup.
                # A refused cleanup admission must not replace the original
                # business error. No deletion or slot release is inferred.
                pass
        if can_retry and retryable(error):
            try:
                return prepare_assisted_retry(owner, delivery, leases, error_class=type(error).__name__[:96])
            except Exception as retry_error:
                if self._retry_prepared(delivery):
                    return None
                return self.settlement.negative(delivery, retry_error, business_fence=leases.business_fence, leases=leases)
        return self.settlement.negative(delivery, error, business_fence=leases.business_fence, leases=leases)
