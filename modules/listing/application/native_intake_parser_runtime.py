"""Orchestrate a trusted parser owner; all durable grants stay with that owner.

This module adds no admission, provider selection, SQL writes or worker wiring.
Terminal/prepared history is not an execution grant. IO starts only after an
actual acceptance or a fully authenticated reconciliation of its lost reply.
The return value is a committed output result, or None for a proved negative
closure/prepared retry; the caller must not mark the physical job successful.
"""
from __future__ import annotations

from fastapi import HTTPException

from modules.external_data.application.native_intake_process import (
    NativeIORejected,
    NativeIOUnreaped,
)
from modules.external_data.application.native_parser_artifacts import (
    NativeParserArtifactRejected,
    NativeParserArtifactUnavailable,
)
from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryConflict,
)
from modules.listing.application.native_intake_parser_execution import NativeParserExecutionReads
from modules.listing.application.native_intake_parser_failure_policy import (
    parser_failure_kind,
    parser_failure_observation,
)
from modules.listing.application.native_intake_parser_io import NativeIntakeParserIO
from modules.listing.application.native_intake_parser_job_proof import parser_delivery_identity
from modules.listing.application.native_intake_parser_leases import NativeParserCompositeLeases
from modules.listing.application.native_intake_parser_policy import (
    NativeParserConflict,
    NativeParserUnavailable,
)
from modules.listing.application.native_intake_parser_results import commit_parser_output
from shared.jobs.queue import JobFenceRejectedError, JobStatus

_UNRESOLVED = object()
_RETRY_LIMITS = {
    "NATIVE_PARSER_RETRY_EXHAUSTED",
    "NATIVE_PARSER_ORIGINAL_RETRY_DEADLINE_EXHAUSTED",
}


def _capability_candidate(error):
    """Routing hints only; settle_unavailable independently proves the cause."""
    if isinstance(error, (NativeParserArtifactRejected, NativeParserArtifactUnavailable, PermissionError)):
        return True
    if isinstance(error, HTTPException):
        return error.status_code in {401, 403}
    if isinstance(error, NativeParserUnavailable):
        return str(error) != "NATIVE_PARSER_CURRENT_SQL_ROOT_REQUIRED"
    if type(error) is NativeIORejected:
        return error.status in {401, 403} or error.code in {
            "NATIVE_GCS_PARSER_CREDENTIAL_UNAVAILABLE", "NATIVE_IO_PARSER_CURRENT_CONFIG_LOST"}
    if isinstance(error, NativeAssistedEntryConflict):
        return str(error) == "NATIVE_ASSISTED_CURRENT_MANUAL_APPROVAL_REQUIRED"
    if isinstance(error, NativeParserConflict):
        return str(error) in {
            "NATIVE_PARSER_ADMISSION_CONFIG_CHANGED", "NATIVE_PARSER_ENGINEERING_NAMESPACE_REJECTED"}
    return isinstance(error, JobFenceRejectedError) and str(error) == "NATIVE_PARSER_TOKEN_REJECTED"


def _closed_outcome(delivery, outcome):
    if outcome["terminal"]:
        return outcome["result"]
    if outcome.get("retry_prepared"):
        # History proves both a queued retry and a later physical claim. Only
        # the latter needs a new acceptance; replaying the old call does not.
        if (delivery.status != JobStatus.RUNNING
                or delivery.fence_token <= outcome["retry_receipt"]["physical_fence"]):
            return None
    return _UNRESOLVED


class NativeIntakeParserRuntime:
    def __init__(self, owner, *, storage_factory=None, parser_process_factory=None):
        self.owner = owner
        # Trusted composition-time seams only. No job, request, setting or
        # credential supplies a provider, import path or executable here.
        self.storage_factory = storage_factory
        self.parser_process_factory = parser_process_factory

    def execute(self, delivery):
        tenant, generation = parser_delivery_identity(delivery)
        try:
            outcome = self.owner.replay(delivery)
        except NativeParserConflict as error:
            if str(error) != "NATIVE_PARSER_TERMINAL_PROOF_REJECTED":
                raise
            # Active history intentionally rejects a reclaimed physical fence.
            # Only recovery may authenticate the old acceptance and require an
            # expired old business lease plus completely closed journals/slots.
            result = self._control(self.owner.recover_expired, delivery, error)
            if result is not _UNRESOLVED:
                return result
            raise
        result = _closed_outcome(delivery, outcome)
        if result is not _UNRESOLVED:
            return result
        try:
            fence, policy = self.owner._accept(delivery, tenant, generation)
        except NativeIOUnreaped:
            raise
        except JobFenceRejectedError as error:
            # A known rejection never enters acceptance reconciliation or IO.
            return self._unaccepted_failure(delivery, error)
        except Exception as error:
            try:
                fence = self._accepted_fence(delivery, tenant, generation)
            except NativeIOUnreaped:
                raise error from None
            except Exception:
                return self._unaccepted_failure(delivery, error)
            try:
                policy = self._accepted_policy(delivery, tenant, generation, fence)
            except Exception as cause:
                return self._accepted_failure(delivery, error, business_fence=fence, cause=cause)
        return self._run_accepted(delivery, fence, policy)

    def _accepted_fence(self, delivery, tenant, generation):
        # Return only after the read transaction exits successfully. This
        # authenticates admission, acceptance, delivery and both live owners;
        # syntax, a RUNNING flag or a local exception never substitutes for it.
        with self.owner.graph.transaction(tenant):
            _, job, _, _, before = NativeParserExecutionReads(self.owner).historical_locked(
                delivery, tenant, generation)
            if job["status"] != "RUNNING":
                raise JobFenceRejectedError("NATIVE_PARSER_ACCEPTANCE_OUTCOME_UNPROVEN")
            fence = job["fence_token"]
            before()
        return fence

    def _accepted_policy(self, delivery, tenant, generation, fence):
        with self.owner.graph.transaction(tenant):
            state = NativeParserExecutionReads(self.owner).fresh(
                delivery, tenant, generation, business_fence=fence)
            # fresh authenticates prior attempt history. Any current intent,
            # including a finished one, forbids restarting this attempt's IO.
            if (state.job["status"] != "RUNNING"
                    or any(item["attempt"] == state.job["attempt"]
                        for item in state.payload["attempt_intents"])):
                raise JobFenceRejectedError("NATIVE_PARSER_ACCEPTANCE_RESUME_UNPROVEN")
            policy = state.context.policy
            state.before()
        return policy

    def _replay_after_error(self, delivery, *, leases=None):
        try:
            return _closed_outcome(delivery, self.owner.replay(delivery, leases=leases))
        except Exception:
            # A second read/root failure supplies no closure and must not
            # replace the original failure with a misleading parser defect.
            return _UNRESOLVED

    def _control(self, method, delivery, original, *, leases=None, preserve_result=False, **kwargs):
        try:
            result = method(delivery, leases=leases, **kwargs)
        except NativeIOUnreaped:
            raise original from None
        except Exception:
            return self._replay_after_error(delivery, leases=leases)
        # A terminal setter can discover an already committed positive result
        # after our earlier replay. Preserve that authenticated owner result;
        # cancellation/recovery receipts still represent negative closures.
        return result if preserve_result else None

    def _unaccepted_failure(self, delivery, error):
        result = self._replay_after_error(delivery)
        if result is not _UNRESOLVED:
            return result
        # Each API re-reads the actual status, original caps and full proof.
        # Neither API can accept an attempt or turn a healthy root error into
        # a negative cause. No business fence is guessed after rejection.
        for method in (self.owner.cancel_unaccepted, self.owner.recover_expired):
            result = self._control(method, delivery, error)
            if result is not _UNRESOLVED:
                return result
        raise error

    def _run_accepted(self, delivery, fence, policy):
        try:
            leases = NativeParserCompositeLeases(self.owner, delivery,
                business_fence=fence, policy_sha256=policy.sha256)
        except NativeIOUnreaped:
            raise
        except Exception as error:
            return self._accepted_failure(delivery, error, business_fence=fence)
        pending_error = None
        commit_started = False
        try:
            io = NativeIntakeParserIO(self.owner, delivery, leases, policy,
                storage_factory=self.storage_factory, parser_process_factory=self.parser_process_factory)
            observed = io.read_and_parse()
            # Set before the SQL call: a lost response cannot justify another
            # read/child/commit or classifying a commit error as an IO defect.
            commit_started = True
            return commit_parser_output(self.owner, delivery, leases, observed)
        except NativeIOUnreaped as error:
            pending_error = error
            raise
        except Exception as error:
            try:
                return self._accepted_failure(delivery, error, business_fence=fence,
                    leases=leases, allow_failure=not commit_started)
            except BaseException as unresolved:
                pending_error = unresolved
                raise
        except BaseException as error:
            pending_error = error
            raise
        finally:
            try:
                leases.pause()
            except BaseException:
                # Failed finalization cannot report success; an already
                # escaping error (especially unreaped IO) keeps its identity.
                if pending_error is None:
                    raise

    def _accepted_failure(self, delivery, error, *, business_fence, leases=None,
            cause=None, allow_failure=False):
        cause = error if cause is None else cause
        if isinstance(cause, NativeIOUnreaped):
            raise error
        result = self._replay_after_error(delivery, leases=leases)
        if result is not _UNRESOLVED:
            return result
        if _capability_candidate(cause):
            result = self._control(self.owner.settle_unavailable, delivery, error,
                business_fence=business_fence, leases=leases, preserve_result=True)
        elif isinstance(cause, (JobFenceRejectedError, NativeParserConflict)):
            # Root/lease expiry alone is no cause. Recovery must independently
            # prove a genuinely expired original cap or accepted business lease.
            result = self._control(self.owner.recover_expired, delivery, error, leases=leases)
        elif allow_failure and leases is not None:
            observed = parser_failure_observation(cause)
            kind = parser_failure_kind(observed, type(cause).__name__)
            if kind == "PERMANENT":
                result = self._control(self.owner.settle_permanent, delivery, error, leases=leases, preserve_result=True)
            elif kind == "TRANSIENT":
                result = self._retry(delivery, leases, error)
        if result is not _UNRESOLVED:
            return result
        raise error

    def _retry(self, delivery, leases, original):
        # The typed exception is only a routing hint. prepare_retry proves the
        # actual closed journal, fresh authority and original caps itself.
        try:
            self.owner.prepare_retry(delivery, leases=leases)
        except NativeIOUnreaped:
            raise original from None
        except Exception as error:
            result = self._replay_after_error(delivery, leases=leases)
            if result is not _UNRESOLVED:
                return result
            if isinstance(error, JobFenceRejectedError) and str(error) in _RETRY_LIMITS:
                # A source deadline that blocks retry is not Intake failure:
                # settle_permanent independently proves original cap/time
                # exhaustion, plus fresh authority, or refuses this path.
                return self._control(self.owner.settle_permanent, delivery, original, leases=leases, preserve_result=True)
            if _capability_candidate(error):
                return self._control(self.owner.settle_unavailable, delivery, original,
                    business_fence=leases.business_fence, leases=leases, preserve_result=True)
            return _UNRESOLVED
        return None
