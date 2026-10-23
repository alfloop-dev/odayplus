"""Close owned initial/retry commands and expired accepted parser attempts.

This control path never accepts an attempt, changes Intake, restores a grant
or discards unresolved IO. Accepted recovery authenticates the original owner
and closed journals; an untyped human cancellation flag remains insufficient.
"""
from __future__ import annotations

import json
from datetime import timedelta
from uuid import uuid4

from modules.listing.application.native_intake_assisted_entry_request_proof import _object, _time
from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_parser_attempts import (
    RETRY_CODE,
    active_payload_fields,
)
from modules.listing.application.native_intake_parser_history import (
    NativeParserTerminalHistory,
    _audited_exact,
)
from modules.listing.application.native_intake_parser_job_proof import (
    parser_admission_provenance,
    parser_delivery_identity,
)
from modules.listing.application.native_intake_parser_journal import ClosedParserJournalFacts
from modules.listing.application.native_intake_parser_leases import NativeParserCompositeLeases
from modules.listing.application.native_intake_parser_negative import (
    NativeParserNegativeClosure,
    _cause,
    _publish,
)
from modules.listing.application.native_intake_parser_policy import (
    JOB_TYPE,
    LEASE_SECONDS,
    NativeParserConflict,
)
from modules.listing.application.native_intake_parser_prerequisites import NativeParserCommandBudget
from modules.listing.application.native_intake_parser_recovery import (
    RECOVERY_FIELDS,
    RECOVERY_REASON,
    accepted_parser_recovery_facts,
    require_terminal_recovery,
)
from modules.listing.application.native_intake_parser_retry import (
    prepared_parser_retry_facts,
    require_prepared_parser_retry_state,
)
from modules.listing.application.native_intake_retrieval_policy import (
    canonical_json,
    canonical_sha256,
)
from shared.audit.backlog import CanonicalAuditBacklog
from shared.domain.events import DomainEvent
from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError, JobStatus

CODE = "NATIVE_PARSER_COMMAND_CANCELLED"
REASON = "Cancel an owned unaccepted parser command; retain its original budget, start and complete operation history."
_LOCAL_CAUSES = {
    "CAPABILITY_UNAVAILABLE": {"NativeParserUnavailable", "NativeIORejected"},
    "ARTIFACT_UNAVAILABLE": {"NativeParserArtifactRejected", "NativeParserArtifactUnavailable"},
    "REQUESTER_DENIED": {"PermissionError", "HTTPException"},
    "SOURCE_PERMISSION_WITHDRAWN": {"NativeAssistedEntryConflict"},
    "FROZEN_CONFIGURATION_WITHDRAWN": {"NativeParserConflict", "JobFenceRejectedError"},
}
_FIELDS = {"proof_version", "job_id", "parser_run_id", "intake_id", "source_snapshot_id", "parser_release_id",
    "settled_at", "prior_status", "cause_code", "cause_error_class", "attempt", "delivery_generation", "worker",
    "business_fence_before", "business_fence_after", "physical_fence", "physical_attempts", "physical_lease_expires_at",
    "business_version_before", "business_version", "physical_version_before", "physical_version",
    "run_version_before", "run_version", "first_started_at", "timeout_at", "source_valid_until",
    "acceptance_audit_event_id", "retry_audit_event_id", "attempt_history_sha256", "audit_event_id",
    "parser_completed_event_id", "intent_ids", "journal_sha256", "quality_summary", "native_parser_provenance", "code"}


def _reject():
    raise NativeParserConflict("NATIVE_PARSER_CANCEL_PROOF_REJECTED") from None


def _facts(history, job, run, physical, *, terminal, before):
    """Authenticate initial admission or the real prior accepted retry record."""
    payload = _object(job["payload"])
    extra = {"cancellation_receipt"} if terminal else set()
    if job["attempt"] == 0:
        if (type(job["attempt"]) is not int or set(payload) != {
                "admission", "delivery_generation", "attempt_token_sha256", "attempt_intents"} | extra
                or payload["attempt_intents"] != [] or run["started_at"] is not None):
            _reject()
        history._parent_input(job["tenant_id"], payload["admission"]["binding"], before)
        # No acceptance and no operations exist: do not invent an accepted
        # attempt just to call the accepted-attempt journal verifier.
        return ClosedParserJournalFacts((), (), canonical_sha256([]), 0, 0), None
    if (type(job["attempt"]) is not int or not 1 <= job["attempt"] < job["max_attempts"]
            or set(payload) != active_payload_fields(job["attempt"]) | {"retry_receipt"} | extra):
        _reject()
    prepared = prepared_parser_retry_facts(history, job, run, physical, before=before)
    if not terminal:
        require_prepared_parser_retry_state(history, job, run, physical, prepared, before=before)
    return prepared.combined, prepared


def _derived_cause(job, payload, physical, at, business_lease=None):
    if at >= _time(job["timeout_at"]):
        return "ORIGINAL_COMMAND_EXPIRED"
    if physical.attempts > physical.max_retries:
        return "ORIGINAL_PHYSICAL_CAP_EXHAUSTED"
    if at >= _time(payload["admission"]["binding"]["source_valid_until"]):
        return "ORIGINAL_SOURCE_AUTHORITY_EXPIRED"
    if business_lease is not None and at >= _time(business_lease):
        return "EXPIRED_ACCEPTED_BUSINESS_LEASE"
    return None


class NativeParserUnacceptedCancellation:
    def __init__(self, owner):
        self.owner, self.graph, self.engine, self.queue = owner, owner.graph, owner.engine, owner.queue

    def cancel(self, delivery, *, leases=None):
        return self._settle(delivery, leases=leases, recovering=False)

    def recover_expired(self, delivery, *, leases=None):
        return self._settle(delivery, leases=leases, recovering=True)

    def _settle(self, delivery, *, leases, recovering):
        if leases is not None:
            if (not isinstance(leases, NativeParserCompositeLeases)
                    or leases.owner is not self.owner or leases.delivery != delivery):
                raise JobFenceRejectedError("NATIVE_PARSER_CANCEL_LEASE_BINDING_REJECTED")
            leases.pause()
        tenant, generation = parser_delivery_identity(delivery)
        try:
            return self._cancel(delivery, tenant, generation, recovering=recovering)
        except Exception:
            # Unknown commit response is resolved from the actual full terminal
            # proof, never by writing again or repeating an external operation.
            try:
                with self.graph.transaction(tenant):
                    before = NativeParserCommandBudget(self.owner.clock)
                    before()
                    job = self.engine.query_one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                        (tenant, delivery.job_id))
                    if job is not None and job["status"] == "CANCELLED":
                        NativeParserTerminalHistory(self.graph).read(delivery, tenant, generation, before=before)
                        return _object(job["payload"])["cancellation_receipt"]
            except Exception:
                pass
            raise

    def _cancel(self, delivery, tenant, generation, *, recovering=False):
        owner, history = self.owner, NativeParserTerminalHistory(self.graph)
        with self.graph.transaction(tenant):
            before = NativeParserCommandBudget(owner.clock)
            before()
            self.graph._one("SELECT job_id FROM durable_jobs WHERE job_id=? FOR UPDATE", (delivery.job_id,))
            before()
            physical = self.queue.get(delivery.job_id)
            before()
            job = self.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=? FOR UPDATE",
                (tenant, delivery.job_id))
            run = owner.runs.get(tenant, job["aggregate_id"], before=before, for_update=True)
            owner.jobs.admission(job, run, before=before)
            payload = _object(job["payload"])
            if job["status"] == "CANCELLED":
                history.read(delivery, tenant, generation, before=before)
                return payload["cancellation_receipt"]
            at = owner.clock._clock()
            if (physical is None or physical.status != JobStatus.RUNNING or physical.job_type != JOB_TYPE
                    or physical.payload != delivery.payload or physical.correlation_id != delivery.correlation_id
                    or not physical.locked_by or physical.locked_by != delivery.locked_by
                    or physical.fence_token != delivery.fence_token or physical.lease_expires_at is None
                    or not at < physical.lease_expires_at <= at+timedelta(seconds=LEASE_SECONDS)
                    or physical.leased_until is not None or physical.heartbeat_at is None
                    or physical.fence_token != physical.attempts or physical.attempts < max(1, job["attempt"])
                    or generation != 1 or payload["delivery_generation"] != generation):
                raise JobFenceRejectedError("NATIVE_PARSER_CANCEL_PHYSICAL_NOT_OWNED")
            before.deadline = min(before.deadline, physical.lease_expires_at)
            initial = job["status"] == "QUEUED" and job["attempt"] == 0
            accepted = recovering and job["status"] == "RUNNING" and job["attempt"] >= 1
            if (not (accepted if recovering else initial or job["status"] == "RETRYING")
                    or run["status"] != ("RUNNING" if accepted else "QUEUED")
                    or job["fence_token"] != job["attempt"]+1
                    or ((job["lease_expires_at"] is None) if accepted else (job["lease_expires_at"] is not None))
                    or job["result"] is not None or job["cancellation_requested_at"] is not None
                    or (accepted and (job["last_error"] is not None or job["next_attempt_at"] is not None
                        or physical.delivery_state != (JobDeliveryState.RETRYING if job["attempt"] > 1 else None)
                        or physical.error_message != (RETRY_CODE if job["attempt"] > 1 else None)))
                    or job["checkpoint"] != "PARSING" or (initial and (job["version"] != 1 or job["last_error"] is not None
                        or job["next_attempt_at"] is not None or physical.delivery_state is not None or physical.error_message is not None))):
                raise JobFenceRejectedError("NATIVE_PARSER_RECOVERY_ACCEPTED_COMMAND_REQUIRED" if recovering
                    else "NATIVE_PARSER_CANCEL_UNACCEPTED_COMMAND_REQUIRED")
            recovery = None
            if accepted:
                recovery = accepted_parser_recovery_facts(history, job, run, physical, at,
                    job["lease_expires_at"], terminal=False, before=before)
                facts, prepared = recovery.combined, None
                expected_version = recovery.acceptance["run_version_after"]
            else:
                facts, prepared = _facts(history, job, run, physical, terminal=False, before=before)
                expected_version = 1 if prepared is None else prepared.receipt["run_version_after"]
            if (run["version"] != expected_version or run["completed_at"] is not None or run["parsed_payload"] is not None
                    or run["normalized_payload"] is not None or _object(run["field_confidence"]) != {}
                    or (json.loads(run["validation_errors"]) if isinstance(run["validation_errors"], str)
                        else run["validation_errors"]) != []):
                _reject()
            before()
            intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE",
                (tenant, run["intake_id"]))
            at = owner.clock._clock()
            business_lease = job["lease_expires_at"] if accepted else None
            cause, error_class = _derived_cause(job, payload, physical, at, business_lease), None
            if cause is None:
                if accepted:
                    raise JobFenceRejectedError("NATIVE_PARSER_RECOVERY_CAUSE_REQUIRED")
                cause, error_class = _cause(owner, intake, run, payload, physical, before)
                if error_class not in _LOCAL_CAUSES.get(cause, set()):
                    _reject()
            before()
            CanonicalAuditBacklog(self.engine).require_available(tenant)
            at = owner.clock._clock()
            derived = _derived_cause(job, payload, physical, at, business_lease)
            if derived is not None:
                cause, error_class = derived, None
            updated = owner.runs.complete_negative(run, status="CANCELLED", at=at, before=before)
            receipt = {"proof_version": 1, "code": CODE, "job_id": job["job_id"],
                **{k: run[k] for k in ("parser_run_id", "intake_id", "source_snapshot_id", "parser_release_id")},
                "settled_at": at.isoformat(), "prior_status": job["status"], "cause_code": cause,
                "cause_error_class": error_class, "attempt": job["attempt"], "delivery_generation": generation,
                "worker": physical.locked_by, "business_fence_before": job["fence_token"],
                "business_fence_after": job["fence_token"]+1, "physical_fence": physical.fence_token,
                "physical_attempts": physical.attempts, "physical_lease_expires_at": physical.lease_expires_at.isoformat(),
                "business_version_before": job["version"], "business_version": job["version"]+1,
                "physical_version_before": physical.version, "physical_version": physical.version+1,
                "run_version_before": run["version"], "run_version": updated["version"],
                "first_started_at": None if run["started_at"] is None else _time(run["started_at"]).isoformat(),
                "timeout_at": _time(job["timeout_at"]).isoformat(),
                "source_valid_until": payload["admission"]["binding"]["source_valid_until"],
                "acceptance_audit_event_id": (recovery.acceptance["audit_event_id"] if recovery is not None
                    else None if prepared is None else prepared.acceptance["audit_event_id"]),
                "retry_audit_event_id": None if prepared is None else prepared.receipt["audit_event_id"],
                "attempt_history_sha256": canonical_sha256(payload.get("attempt_history", [])),
                "audit_event_id": str(uuid4()), "parser_completed_event_id": str(uuid4()),
                "intent_ids": list(facts.intent_ids), "journal_sha256": facts.journal_sha256,
                "quality_summary": facts.quality_summary(), "native_parser_provenance": parser_admission_provenance(payload)}
            if recovery is not None:
                receipt.update(accepted_physical_fence=recovery.acceptance["physical_fence"],
                    accepted_worker=recovery.acceptance["worker"], business_lease_expires_at=_time(business_lease).isoformat(),
                    ownership_mode=recovery.ownership_mode)
            if set(receipt) != _FIELDS | (RECOVERY_FIELDS if accepted else set()):
                _reject()
            completed = DomainEvent(event_id=receipt["parser_completed_event_id"], event_type="parser.run_completed",
                producer="parser_service", tenant_id=tenant, aggregate_type="parser_run", aggregate_id=run["parser_run_id"],
                aggregate_version=updated["version"], partition_key=tenant+":"+run["intake_id"],
                correlation_id=job["correlation_id"], causation_id=payload["admission"]["binding"]["original_input"]["parent_event_id"],
                actor_ref=JOB_TYPE, occurred_at=at, schema_ref="#/payloads/ParserRunCompletedV1",
                payload={"parser_run_id": run["parser_run_id"], "intake_id": run["intake_id"],
                    "parser_release_id": run["parser_release_id"], "status": "CANCELLED", "quality_summary": facts.quality_summary()})
            _publish(owner, completed, before)
            before()
            self.graph.audit_sink.persist({"audit_event_id": receipt["audit_event_id"], "tenant_id": tenant,
                "resource_type": "intake", "resource_id": run["intake_id"], "actor_subject_id": None,
                "service_principal": JOB_TYPE, "actor_role": "SVC_PARSER", "action": "parser.run_completed.v1",
                "result": "SUCCEEDED", "causation_id": completed.causation_id, "correlation_id": job["correlation_id"],
                "occurred_at": at.isoformat(), "reason": RECOVERY_REASON if accepted else REASON,
                "before_value": {"job_id": job["job_id"], "status": job["status"], "parser_run_status": run["status"]},
                "after_value": {**receipt, "status": "CANCELLED", "parser_run_status": "CANCELLED", "intake_mutated": False}},
                {k: intake[k] for k in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")})
            before()
            if self.engine.execute("UPDATE workflow.jobs SET status='CANCELLED',payload=?::jsonb,last_error=?::jsonb,"
                    "lease_expires_at=NULL,next_attempt_at=NULL,fence_token=fence_token+1,version=version+1,heartbeat_at=?,updated_at=? "
                    "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND attempt=? AND status=? "
                    "AND result IS NULL AND cancellation_requested_at IS NULL "
                    "AND ((status IN ('QUEUED','RETRYING') AND lease_expires_at IS NULL) "
                    "OR (status='RUNNING' AND lease_expires_at=?::timestamptz)) "
                    "AND (?::boolean IS FALSE OR lease_expires_at<=clock_timestamp())",
                    (canonical_json({**payload, "cancellation_receipt": receipt}), canonical_json({"code": CODE}),
                        at.isoformat(), at.isoformat(), tenant, job["job_id"], job["version"], job["fence_token"],
                        job["attempt"], job["status"], business_lease,
                        recovery is not None and recovery.ownership_mode == "RECLAIMED_PHYSICAL")).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_PARSER_CANCEL_BUSINESS_CAS_REJECTED")
            before()
            if self.engine.query_one("SELECT job_id FROM durable_jobs WHERE job_id=? AND status=? AND version=? "
                    "AND fence_token=? AND locked_by=? AND lease_expires_at>clock_timestamp()",
                    (job["job_id"], JobStatus.RUNNING.value, physical.version, physical.fence_token, physical.locked_by)) is None:
                raise JobFenceRejectedError("NATIVE_PARSER_CANCEL_PHYSICAL_CAS_REJECTED")
            before()
            self.queue.update_status(job["job_id"], JobStatus.CANCELLED, expected_version=physical.version,
                fence_token=physical.fence_token, error_message=CODE)
            before()
            return receipt


def read_cancelled_unaccepted_parser(history, job, run, physical, tenant, generation, *, before):
    """Authenticate the entire closure after settings and credentials retire."""
    try:
        payload = _object(job["payload"])
        receipt = _object(payload["cancellation_receipt"])
        accepted = receipt.get("prior_status") == "RUNNING"
        integer_fields = {"proof_version", "attempt", "delivery_generation", "business_fence_before", "business_fence_after",
            "physical_fence", "physical_attempts", "business_version_before", "business_version", "physical_version_before",
            "physical_version", "run_version_before", "run_version"}
        if (set(receipt) != _FIELDS | (RECOVERY_FIELDS if accepted else set())
                or any(type(receipt[k]) is not int or not 0 <= receipt[k] <= 2**63-1 for k in integer_fields)
                or receipt["proof_version"] != 1 or receipt["code"] != CODE or receipt["job_id"] != job["job_id"]
                or any(receipt[k] != run[k] for k in ("parser_run_id", "intake_id", "source_snapshot_id", "parser_release_id"))
                or generation != 1 or receipt["delivery_generation"] != generation or receipt["attempt"] != job["attempt"]
                or job["status"] != "CANCELLED" or run["status"] != "CANCELLED" or physical.status != JobStatus.CANCELLED
                or job["result"] is not None or job["lease_expires_at"] is not None or job["next_attempt_at"] is not None
                or job["cancellation_requested_at"] is not None or job["checkpoint"] != "PARSING"
                or canonical_json(_object(job["last_error"])) != canonical_json({"code": CODE})
                or receipt["business_fence_before"] != job["attempt"]+1
                or receipt["business_fence_after"] != receipt["business_fence_before"]+1
                or job["fence_token"] != receipt["business_fence_after"] or job["version"] != receipt["business_version"]
                or receipt["business_version"] != receipt["business_version_before"]+1
                or receipt["physical_version"] != receipt["physical_version_before"]+1
                or physical.version != receipt["physical_version"] or physical.fence_token != receipt["physical_fence"]
                or physical.attempts != receipt["physical_attempts"] or physical.fence_token != physical.attempts
                or physical.attempts < max(1, job["attempt"]) or not isinstance(receipt["worker"], str) or not receipt["worker"]
                or physical.delivery_state is not None or physical.error_message != CODE
                or any(getattr(physical, k) is not None for k in ("locked_by", "heartbeat_at", "lease_expires_at", "leased_until"))
                or run["version"] != receipt["run_version"] or receipt["run_version"] != receipt["run_version_before"]+1
                or run["parsed_payload"] is not None or run["normalized_payload"] is not None or _object(run["field_confidence"]) != {}
                or (json.loads(run["validation_errors"]) if isinstance(run["validation_errors"], str) else run["validation_errors"]) != []
                or receipt["native_parser_provenance"] != parser_admission_provenance(payload)
                or receipt["timeout_at"] != _time(job["timeout_at"]).isoformat()
                or receipt["source_valid_until"] != payload["admission"]["binding"]["source_valid_until"]
                or receipt["first_started_at"] != (None if run["started_at"] is None else _time(run["started_at"]).isoformat())
                or receipt["attempt_history_sha256"] != canonical_sha256(payload.get("attempt_history", []))):
            _reject()
        at = _time(receipt["settled_at"])
        if (not _time(payload["admission"]["admitted_at"]) <= at < _time(receipt["physical_lease_expires_at"])
                or _time(receipt["physical_lease_expires_at"])-at > timedelta(seconds=LEASE_SECONDS)
                or _time(run["completed_at"]) != at or _time(job["heartbeat_at"]) != at or _time(job["updated_at"]) != at):
            _reject()
        if accepted:
            proof = accepted_parser_recovery_facts(history, job, run, physical, at,
                receipt["business_lease_expires_at"], terminal=True, before=before)
            require_terminal_recovery(receipt, proof)
            facts, prepared = proof.combined, None
        else:
            facts, prepared = _facts(history, job, run, physical, terminal=True, before=before)
        if not accepted and prepared is None:
            if (receipt["prior_status"] != "QUEUED" or receipt["business_version_before"] != 1 or receipt["run_version_before"] != 1
                    or receipt["acceptance_audit_event_id"] is not None or receipt["retry_audit_event_id"] is not None):
                _reject()
        elif not accepted:
            retry = prepared.receipt
            claims = physical.fence_token-retry["physical_fence"]
            if (receipt["prior_status"] != "RETRYING" or receipt["business_version_before"] != retry["business_version"]
                    or receipt["run_version_before"] != retry["run_version_after"]
                    or receipt["acceptance_audit_event_id"] != prepared.acceptance["audit_event_id"]
                    or receipt["retry_audit_event_id"] != retry["audit_event_id"]
                    or not _time(retry["available_at"]) <= at or claims < 1
                    or physical.attempts-retry["physical_attempts"] != claims
                    or receipt["physical_version_before"]-retry["physical_version"] < claims):
                _reject()
            before()
            available = history.engine.query_one("SELECT available_at FROM durable_jobs WHERE job_id=?", (job["job_id"],))
            if available is None or _time(available["available_at"]) != _time(retry["available_at"]):
                _reject()
        derived = _derived_cause(job, payload, physical, at, receipt["business_lease_expires_at"] if accepted else None)
        if ((derived is not None and (receipt["cause_code"] != derived or receipt["cause_error_class"] is not None))
                or (derived is None and (accepted or receipt["cause_error_class"] not in _LOCAL_CAUSES.get(receipt["cause_code"], set())))
                or receipt["intent_ids"] != list(facts.intent_ids) or receipt["journal_sha256"] != facts.journal_sha256
                or canonical_json(receipt["quality_summary"]) != canonical_json(facts.quality_summary())):
            _reject()
        for key in ("audit_event_id", "parser_completed_event_id"):
            native_intake_uuid(receipt[key])
        before()
        audit = history.engine.query_one("SELECT * FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=? "
            "AND resource_type='intake' AND resource_id=? AND actor_subject_id IS NULL AND service_principal=? "
            "AND actor_role='SVC_PARSER' AND action='parser.run_completed.v1' AND result='SUCCEEDED' "
            "AND causation_id=? AND correlation_id=?", (tenant, receipt["audit_event_id"], run["intake_id"], JOB_TYPE,
                payload["admission"]["binding"]["original_input"]["parent_event_id"], job["correlation_id"]))
        if (audit is None or canonical_json(_object(audit["before_value"])) != canonical_json({
                "job_id": job["job_id"], "status": receipt["prior_status"], "parser_run_status": "RUNNING" if accepted else "QUEUED"})
                or not _audited_exact(_object(audit["after_value"]), {**receipt, "status": "CANCELLED", "parser_run_status": "CANCELLED",
                    "intake_mutated": False}) or audit["reason"] != (RECOVERY_REASON if accepted else REASON)
                or _time(audit["occurred_at"]) != at):
            _reject()
        history._event(job, receipt["parser_completed_event_id"], at, before, event_type="parser.run_completed",
            producer="parser_service", aggregate_type="parser_run", aggregate_id=run["parser_run_id"],
            aggregate_version=run["version"], partition_key=tenant+":"+run["intake_id"],
            causation_id=payload["admission"]["binding"]["original_input"]["parent_event_id"],
            schema_ref="#/payloads/ParserRunCompletedV1", payload={"parser_run_id": run["parser_run_id"],
                "intake_id": run["intake_id"], "parser_release_id": run["parser_release_id"], "status": "CANCELLED",
                "quality_summary": facts.quality_summary()})
        before.check_in_memory()
        return NativeParserNegativeClosure(run["parser_run_id"], canonical_sha256(receipt))
    except NativeParserConflict:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
        _reject()
