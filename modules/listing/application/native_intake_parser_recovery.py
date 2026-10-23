"""Facts for closing an expired accepted parser attempt, never fresh grants.

Use the actual acceptance audit and original journals across a real physical
reclaim. A new physical claim cannot take over a still-live business lease.
No acceptance, attempt, old Job or old Intake is manufactured for recovery.
"""
from __future__ import annotations

from dataclasses import dataclass

from modules.listing.application.native_intake_assisted_entry_request_proof import _object, _time
from modules.listing.application.native_intake_parser_attempts import (
    NativeParserAttemptHistory,
    active_payload_fields,
)
from modules.listing.application.native_intake_parser_policy import NativeParserConflict
from modules.listing.application.native_intake_parser_retry import require_closed_slots
from shared.jobs.queue import JobFenceRejectedError

RECOVERY_FIELDS = {"accepted_physical_fence", "accepted_worker", "business_lease_expires_at", "ownership_mode"}
RECOVERY_REASON = "Close an owned expired accepted parser command; fence the former owner and retain all original attempts and evidence."


def reject():
    raise NativeParserConflict("NATIVE_PARSER_RECOVERY_PROOF_REJECTED") from None


@dataclass(frozen=True)
class NativeParserAcceptedRecoveryFacts:
    acceptance: dict
    prior: object
    combined: object
    ownership_mode: str


def accepted_parser_recovery_facts(history, job, run, physical, at, business_lease, *, terminal, before):
    """Prove a real accepted attempt and every closed operation, not its grants."""
    payload = _object(job["payload"])
    extra = {"cancellation_receipt"} if terminal else set()
    if (type(job["attempt"]) is not int or not 1 <= job["attempt"] <= job["max_attempts"]
            or set(payload) != active_payload_fields(job["attempt"]) | extra or business_lease is None):
        reject()
    attempts = NativeParserAttemptHistory(history.graph)
    acceptance = attempts.acceptance_record(job, run, _object(payload["acceptance_receipt"]),
        _object(payload["accepted_binding"]), attempt=job["attempt"], before=before)
    lease = _time(business_lease)
    if _time(acceptance["accepted_at"]) >= lease:
        reject()
    if physical.fence_token == acceptance["physical_fence"]:
        if physical.locked_by != acceptance["worker"] and not terminal:
            raise JobFenceRejectedError("NATIVE_PARSER_RECOVERY_ACCEPTED_OWNER_REJECTED")
        mode = "SAME_ACCEPTED_CLAIM"
    elif physical.fence_token > acceptance["physical_fence"]:
        if at < lease:
            if terminal:
                reject()
            raise JobFenceRejectedError("NATIVE_PARSER_RECOVERY_BUSINESS_LEASE_STILL_LIVE")
        mode = "RECLAIMED_PHYSICAL"
    else:
        if terminal:
            reject()
        raise JobFenceRejectedError("NATIVE_PARSER_RECOVERY_OLD_PHYSICAL_FENCE_REJECTED")
    # This is deliberately stage-independent: actual terminal rows are used
    # during replay, and acceptance_record authenticates the old owner itself.
    original = history._parent_input(job["tenant_id"], payload["admission"]["binding"], before)
    prior, _, combined = attempts.closed_current(job, run, physical, original, acceptance, at, before=before)
    try:
        require_closed_slots(history.engine, combined, before)
    except JobFenceRejectedError as error:
        if not terminal or str(error) != "NATIVE_PARSER_RETRY_UNRESOLVED_IO":
            raise
        reject()
    before.check_in_memory()
    return NativeParserAcceptedRecoveryFacts(acceptance, prior, combined, mode)


def require_terminal_recovery(receipt, proof):
    acceptance = proof.acceptance
    if (type(receipt["accepted_physical_fence"]) is not int
            or receipt["accepted_physical_fence"] != acceptance["physical_fence"]
            or receipt["accepted_worker"] != acceptance["worker"]
            or receipt["ownership_mode"] != proof.ownership_mode
            or receipt["acceptance_audit_event_id"] != acceptance["audit_event_id"]
            or receipt["retry_audit_event_id"] is not None
            or receipt["run_version_before"] != acceptance["run_version_after"]
            or receipt["business_version_before"] < 2
            or receipt["attempt_history_sha256"] != proof.prior.history_sha256
            or (proof.ownership_mode == "SAME_ACCEPTED_CLAIM" and receipt["worker"] != acceptance["worker"])):
        reject()
