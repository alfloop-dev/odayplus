"""Owned capability-loss settlement and configuration-independent failure proof.

Each accepted attempt requires its complete prior retry history. Unknown
reaping and lost owner fences remain unresolved. Current capability loss
never changes Intake or publishes parsed facts; cancellation and healthy
authority defects have separate settlement paths.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException

from modules.external_data.application.native_intake_process import NativeIORejected
from modules.external_data.application.native_parser_artifacts import (
    NativeParserArtifactRejected,
    NativeParserArtifactUnavailable,
)
from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryConflict,
)
from modules.listing.application.native_intake_assisted_entry_request_proof import _object, _time
from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_parser_attempts import (
    NativeParserAttemptHistory,
    active_payload_fields,
)
from modules.listing.application.native_intake_parser_execution import NativeParserExecutionReads
from modules.listing.application.native_intake_parser_failure_policy import (
    valid_parser_failure_observation,
)
from modules.listing.application.native_intake_parser_history import (
    NativeParserTerminalHistory,
    _audited_exact,
)
from modules.listing.application.native_intake_parser_job_proof import (
    parser_admission_provenance,
    parser_delivery_identity,
)
from modules.listing.application.native_intake_parser_journal import (
    ClosedParserJournalFacts as ClosedParserJournalFacts,
)
from modules.listing.application.native_intake_parser_journal import (
    closed_parser_attempt_journal,
)
from modules.listing.application.native_intake_parser_policy import (
    JOB_TYPE,
    LEASE_SECONDS,
    NativeParserConflict,
    NativeParserUnavailable,
)
from modules.listing.application.native_intake_retrieval_policy import (
    canonical_json,
    canonical_sha256,
)
from shared.audit.backlog import CanonicalAuditBacklog
from shared.domain.events import DomainEvent
from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError, JobStatus

_CODE = "NATIVE_PARSER_CAPABILITY_UNAVAILABLE"
_OBSERVED_STORAGE_CAUSE = "OBSERVED_GCS_AUTHORIZATION_UNAVAILABLE"
_REASON = "Settle the owned parser run after freshly observed capability loss; preserve Intake and original evidence."
_ACTIVE_FIELDS = {"admission", "delivery_generation", "attempt_token_sha256", "attempt_intents",
    "acceptance_receipt", "accepted_binding"}
_NEGATIVE_FIELDS = {"job_id", "parser_run_id", "intake_id", "source_snapshot_id", "parser_release_id",
    "parser_status", "job_status", "settled_at", "prior_status", "business_fence", "physical_fence", "worker",
    "attempt", "delivery_generation", "business_version", "physical_version", "physical_attempts", "run_version",
    "physical_lease_expires_at", "business_lease_expires_at", "acceptance_audit_event_id", "audit_event_id",
    "parser_completed_event_id", "dead_letter_event_id", "code", "cause_code", "error_class", "intent_ids",
    "journal_sha256", "quality_summary", "native_parser_provenance"}


@dataclass(frozen=True)
class NativeParserNegativeClosure:
    """An authenticated closed outcome, never an execution or output grant."""
    parser_run_id: str
    receipt_sha256: str


def _failure_reject():
    raise NativeParserConflict("NATIVE_PARSER_NEGATIVE_TERMINAL_PROOF_REJECTED") from None


def _cause(owner, intake, run, payload, physical, before):
    """Observe current unavailability; a budget or structural error is not it."""
    try:
        owner._confirm(intake, run["parser_release_id"], payload["delivery_generation"],
            payload["admission"]["binding"], physical.payload["attempt_token"], before)
        owner.auth.decode(physical)
    except NativeParserUnavailable as error:
        result = "CAPABILITY_UNAVAILABLE", type(error).__name__
    except NativeIORejected as error:
        if error.code != "NATIVE_GCS_PARSER_CREDENTIAL_UNAVAILABLE":
            raise
        result = "CAPABILITY_UNAVAILABLE", type(error).__name__
    except (NativeParserArtifactRejected, NativeParserArtifactUnavailable) as error:
        result = "ARTIFACT_UNAVAILABLE", type(error).__name__
    except PermissionError as error:
        result = "REQUESTER_DENIED", type(error).__name__
    except HTTPException as error:
        if error.status_code not in {401, 403}:
            raise
        result = "REQUESTER_DENIED", type(error).__name__
    except NativeAssistedEntryConflict as error:
        if str(error) != "NATIVE_ASSISTED_CURRENT_MANUAL_APPROVAL_REQUIRED":
            raise
        result = "SOURCE_PERMISSION_WITHDRAWN", type(error).__name__
    except NativeParserConflict as error:
        if str(error) not in {"NATIVE_PARSER_ADMISSION_CONFIG_CHANGED", "NATIVE_PARSER_ENGINEERING_NAMESPACE_REJECTED"}:
            raise
        result = "FROZEN_CONFIGURATION_WITHDRAWN", type(error).__name__
    except JobFenceRejectedError as error:
        if str(error) != "NATIVE_PARSER_TOKEN_REJECTED":
            raise
        result = "FROZEN_CONFIGURATION_WITHDRAWN", type(error).__name__
    else:
        before()
        raise JobFenceRejectedError("NATIVE_PARSER_CAPABILITY_STILL_CURRENT")
    # The same original root must still be live after observing a negative.
    before()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,95}", result[1]) is None:
        raise JobFenceRejectedError("NATIVE_PARSER_NEGATIVE_CAUSE_REQUIRED")
    return result


def _closed_slots(engine, facts, before):
    if facts.reservation_ids:
        before()
        if engine.query_one("SELECT reservation_id FROM intake.native_retrieval_inflight_slots "
                "WHERE reservation_id=ANY(?::uuid[]) LIMIT 1", (list(facts.reservation_ids),)) is not None:
            reject()


def _storage_authorization_failure(items, facts):
    """A fact from the authenticated current journal, never a fresh grant."""
    if facts.error_count != 1 or not facts.intent_ids or not items:
        return None
    last = items[-1]
    observed = last.get("failure_observation")
    if (last["state"] != "ERROR" or last["intent_id"] != facts.intent_ids[-1]
            or last["process_state"] != "NO_CHILD_REMAINS"
            or last["operation"] not in {"gcs_bucket", "gcs_head", "gcs_download"}
            or last["error_class"] != "NativeIORejected"
            or not valid_parser_failure_observation(observed, last["error_class"])
            or observed["code"] != "NATIVE_GCS_HTTP_REJECTED" or observed["status"] not in {401, 403}):
        return None
    return last


def _publish(owner, event, before):
    before()
    owner.graph._outbox(event)
    before()
    if owner.engine.execute("UPDATE workflow.outbox_events SET causation_id=?,sensitive_fields=?::text[] "
            "WHERE tenant_id=? AND event_id=? AND causation_id IS NULL AND sensitive_fields='{}'::text[]",
            (event.causation_id, event.sensitive_fields, event.tenant_id, event.event_id)).rowcount != 1:
        raise JobFenceRejectedError("NATIVE_PARSER_NEGATIVE_OUTBOX_METADATA_REJECTED")


def reject():
    raise NativeParserConflict("NATIVE_PARSER_NEGATIVE_JOURNAL_REJECTED") from None






def closed_initial_parser_journal(job, run, payload, original, acceptance, at, *, before):
    """Keep the initial-only contract over the explicit accepted journal proof."""
    if (type(job["attempt"]) is not int or job["attempt"] != 1
            or type(payload["delivery_generation"]) is not int or payload["delivery_generation"] != 1):
        reject()
    try:
        return closed_parser_attempt_journal(run, payload["admission"], original, acceptance,
            payload["attempt_intents"], at, timeout_at=job["timeout_at"], before=before)
    except NativeParserConflict as error:
        if str(error) != "NATIVE_PARSER_ATTEMPT_JOURNAL_REJECTED":
            raise
        reject()


class NativeParserUnavailableSettlement:
    def __init__(self, owner):
        self.owner, self.graph, self.engine, self.queue = owner, owner.graph, owner.engine, owner.queue

    def settle(self, delivery, *, business_fence, leases=None):
        if (type(business_fence) is not int or business_fence < 2
                or (leases is not None and getattr(leases, "business_fence", None) != business_fence)):
            raise JobFenceRejectedError("NATIVE_PARSER_NEGATIVE_ACCEPTED_OWNER_REQUIRED")
        # A lost successful SQL response is reconciled before any new write.
        outcome = self.owner.replay(delivery, leases=leases)
        if outcome["terminal"]:
            return outcome["result"]
        tenant, generation = parser_delivery_identity(delivery)
        try:
            return self._settle(delivery, tenant, generation, business_fence)
        except Exception:
            outcome = self.owner.replay(delivery, leases=leases)
            if outcome["terminal"]:
                return outcome["result"]
            raise

    def _settle(self, delivery, tenant, generation, business_fence):
        with self.graph.transaction(tenant):
            physical, job, payload, run, before = NativeParserExecutionReads(self.owner).historical_locked(
                delivery, tenant, generation, business_fence=business_fence)
            if (job["status"] != "RUNNING" or job["attempt"] < 1 or generation != 1
                    or set(payload) != active_payload_fields(job["attempt"]) or job["cancellation_requested_at"] is not None
                    or physical.attempts > physical.max_retries or job["last_error"] is not None):
                raise JobFenceRejectedError("NATIVE_PARSER_NEGATIVE_ACCEPTED_ATTEMPT_REQUIRED")
            acceptance = self.owner.jobs.acceptance(job, run, before=before)
            history = NativeParserTerminalHistory(self.graph)
            original = history._parent_input(tenant, payload["admission"]["binding"], before)
            inspected_at = self.owner.clock._clock()
            if job["attempt"] == 1:
                facts = closed_initial_parser_journal(job, run, payload, original, acceptance, inspected_at, before=before)
                prior, current, current_items = None, facts, payload["attempt_intents"]
            else:
                prior, current, facts = NativeParserAttemptHistory(self.graph).closed_current(
                    job, run, physical, original, acceptance, inspected_at, before=before)
                current_items = prior.current_items
            _closed_slots(self.engine, facts, before)
            before()
            intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE",
                (tenant, run["intake_id"]))
            try:
                cause_code, error_class = _cause(self.owner, intake, run, payload, physical, before)
            except JobFenceRejectedError as error:
                if (str(error) != "NATIVE_PARSER_CAPABILITY_STILL_CURRENT"
                        or _storage_authorization_failure(current_items, current) is None):
                    raise
                # Configured local credentials do not establish GCS access.
                # The actual current operation already failed401/403, was
                # reaped/closed and released all its quota. Keep Intake intact.
                cause_code, error_class = _OBSERVED_STORAGE_CAUSE, "NativeIORejected"
            before()
            CanonicalAuditBacklog(self.engine).require_available(tenant)
            at = self.owner.clock._clock()
            if (not at < _time(job["lease_expires_at"]) <= physical.lease_expires_at
                    or physical.lease_expires_at - at > timedelta(seconds=LEASE_SECONDS)
                    or not at < _time(job["timeout_at"])
                    or not at < _time(payload["admission"]["binding"]["source_valid_until"])):
                raise JobFenceRejectedError("NATIVE_PARSER_NEGATIVE_LEASE_WITNESS_REJECTED")
            updated_run = self.owner.runs.complete_negative(run, status="FAILED", at=at, before=before)
            quality = facts.quality_summary()
            audit_id, completed_id, dead_letter_id = (str(uuid4()) for _ in range(3))
            receipt = {"job_id": job["job_id"], "parser_run_id": run["parser_run_id"],
                "intake_id": run["intake_id"], "source_snapshot_id": run["source_snapshot_id"],
                "parser_release_id": run["parser_release_id"], "parser_status": "FAILED", "job_status": "FAILED",
                "settled_at": at.isoformat(), "prior_status": "RUNNING", "business_fence": job["fence_token"],
                "physical_fence": physical.fence_token, "worker": physical.locked_by, "attempt": job["attempt"],
                "delivery_generation": generation, "business_version": job["version"] + 1,
                "physical_version": physical.version + 1, "physical_attempts": physical.attempts,
                "run_version": updated_run["version"], "physical_lease_expires_at": physical.lease_expires_at.isoformat(),
                "business_lease_expires_at": _time(job["lease_expires_at"]).isoformat(),
                "acceptance_audit_event_id": acceptance["audit_event_id"], "audit_event_id": audit_id,
                "parser_completed_event_id": completed_id, "dead_letter_event_id": dead_letter_id,
                "code": _CODE, "cause_code": cause_code, "error_class": error_class,
                "intent_ids": list(facts.intent_ids), "journal_sha256": facts.journal_sha256,
                "quality_summary": quality, "native_parser_provenance": parser_admission_provenance(payload)}
            if prior is not None:
                receipt["attempt_history_sha256"] = prior.history_sha256
            if set(receipt) != _NEGATIVE_FIELDS | ({"attempt_history_sha256"} if job["attempt"] > 1 else set()):
                _failure_reject()
            completed = DomainEvent(event_id=completed_id, event_type="parser.run_completed", producer="parser_service",
                tenant_id=tenant, aggregate_type="parser_run", aggregate_id=run["parser_run_id"],
                aggregate_version=updated_run["version"], partition_key=tenant + ":" + run["intake_id"],
                correlation_id=job["correlation_id"], causation_id=original.parent_event_id, actor_ref=JOB_TYPE,
                occurred_at=at, schema_ref="#/payloads/ParserRunCompletedV1", payload={
                    "parser_run_id": run["parser_run_id"], "intake_id": run["intake_id"],
                    "parser_release_id": run["parser_release_id"], "status": "FAILED", "quality_summary": quality})
            dead_letter = DomainEvent(event_id=dead_letter_id, event_type="job.dead_lettered", producer="job_platform",
                tenant_id=tenant, aggregate_type="job", aggregate_id=job["job_id"],
                aggregate_version=receipt["business_version"], partition_key=tenant + ":" + job["job_id"],
                correlation_id=job["correlation_id"], causation_id=original.parent_event_id, actor_ref=JOB_TYPE,
                occurred_at=at, schema_ref="#/payloads/JobDeadLetteredV1", sensitive_fields=["payload.last_error_details"],
                payload={"job_id": job["job_id"], "job_type": JOB_TYPE, "checkpoint": "PARSING", "attempt": job["attempt"],
                    "error_code": _CODE, "dead_lettered_at": at.isoformat(), "last_error_details": {"error_class": error_class}})
            for event in (completed, dead_letter):
                _publish(self.owner, event, before)
            before()
            self.graph.audit_sink.persist({"audit_event_id": audit_id, "tenant_id": tenant,
                "resource_type": "intake", "resource_id": run["intake_id"], "actor_subject_id": None,
                "service_principal": JOB_TYPE, "actor_role": "SVC_PARSER", "action": "parser.run_completed.v1",
                "result": "SUCCEEDED", "causation_id": original.parent_event_id, "correlation_id": job["correlation_id"],
                "occurred_at": at.isoformat(), "reason": _REASON,
                "before_value": {"parser_run_id": run["parser_run_id"], "status": "RUNNING"},
                "after_value": {**receipt, "intake_mutated": False}},
                {k: intake[k] for k in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")})
            before()
            if self.engine.execute("UPDATE workflow.jobs SET status='FAILED',payload=?::jsonb,last_error=?::jsonb,"
                    "lease_expires_at=NULL,next_attempt_at=NULL,heartbeat_at=?,updated_at=?,version=version+1 "
                    "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND attempt=? AND status='RUNNING' "
                    "AND result IS NULL AND cancellation_requested_at IS NULL AND lease_expires_at>clock_timestamp() "
                    "AND timeout_at>clock_timestamp() AND ?::timestamptz>clock_timestamp()",
                    (canonical_json({**payload, "negative_receipt": receipt}),
                        canonical_json({"code": _CODE, "error_class": error_class}), at.isoformat(), at.isoformat(),
                        tenant, job["job_id"], job["version"], job["fence_token"], job["attempt"],
                        payload["admission"]["binding"]["source_valid_until"])).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_PARSER_NEGATIVE_BUSINESS_CAS_REJECTED")
            before()
            if self.engine.query_one("SELECT job_id FROM durable_jobs WHERE job_id=? AND status=? AND version=? "
                    "AND fence_token=? AND locked_by=? AND lease_expires_at>clock_timestamp()",
                    (job["job_id"], JobStatus.RUNNING.value, physical.version, physical.fence_token, physical.locked_by)) is None:
                raise JobFenceRejectedError("NATIVE_PARSER_NEGATIVE_PHYSICAL_CAS_REJECTED")
            before()
            self.queue.update_status(job["job_id"], JobStatus.FAILED, delivery_state=JobDeliveryState.DEAD_LETTER,
                expected_version=physical.version, fence_token=physical.fence_token, error_message=_CODE)
            before()
            return None


def read_unavailable_terminal(history, job, run, physical, tenant, generation, *, before):
    """Authenticate the whole negative closure without current capability."""
    try:
        payload = _object(job["payload"])
        receipt = _object(payload["negative_receipt"])
        acceptance = history.jobs.acceptance(job, run, before=before)
        integers = ("business_fence", "physical_fence", "attempt", "delivery_generation", "business_version",
            "physical_version", "physical_attempts", "run_version")
        if (set(payload) != active_payload_fields(job["attempt"]) | {"negative_receipt"}
                or set(receipt) != _NEGATIVE_FIELDS | ({"attempt_history_sha256"} if job["attempt"] > 1 else set())
                or any(type(receipt[k]) is not int or receipt[k] < 1 for k in integers)
                or job["status"] != "FAILED" or run["status"] != "FAILED" or physical.status != JobStatus.FAILED
                or receipt["parser_status"] != "FAILED" or receipt["job_status"] != "FAILED"
                or receipt["prior_status"] != "RUNNING" or receipt["code"] != _CODE
                or receipt["cause_code"] not in {"CAPABILITY_UNAVAILABLE", "ARTIFACT_UNAVAILABLE", "REQUESTER_DENIED",
                    "SOURCE_PERMISSION_WITHDRAWN", "FROZEN_CONFIGURATION_WITHDRAWN", _OBSERVED_STORAGE_CAUSE}
                or not isinstance(receipt["error_class"], str)
                or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,95}", receipt["error_class"]) is None
                or receipt["job_id"] != job["job_id"] or receipt["parser_run_id"] != run["parser_run_id"]
                or any(receipt[k] != run[k] for k in ("intake_id", "source_snapshot_id", "parser_release_id"))
                or generation != 1 or receipt["delivery_generation"] != generation or job["attempt"] < 1
                or any(receipt[k] != acceptance[k] for k in ("physical_fence", "business_fence", "worker", "attempt", "delivery_generation"))
                or receipt["acceptance_audit_event_id"] != acceptance["audit_event_id"]
                or receipt["business_version"] != job["version"] or receipt["physical_version"] != physical.version
                or receipt["physical_attempts"] != physical.attempts
                or not 1 <= physical.attempts <= physical.max_retries
                or physical.fence_token != receipt["physical_fence"]
                or physical.delivery_state != JobDeliveryState.DEAD_LETTER or physical.error_message != _CODE
                or any(getattr(physical, k) is not None for k in ("locked_by", "heartbeat_at", "lease_expires_at", "leased_until"))
                or job["result"] is not None or job["lease_expires_at"] is not None or job["next_attempt_at"] is not None
                or job["cancellation_requested_at"] is not None or job["checkpoint"] != "PARSING"
                or _object(job["last_error"]) != {"code": _CODE, "error_class": receipt["error_class"]}
                or receipt["run_version"] != run["version"] or run["version"] != acceptance["run_version_after"] + 1
                or run["parsed_payload"] is not None or run["normalized_payload"] is not None
                or _object(run["field_confidence"]) != {}
                or (json.loads(run["validation_errors"]) if isinstance(run["validation_errors"], str)
                    else run["validation_errors"]) != []
                or receipt["native_parser_provenance"] != parser_admission_provenance(payload)):
            _failure_reject()
        at = _time(receipt["settled_at"])
        if (not _time(acceptance["accepted_at"]) <= at < _time(receipt["business_lease_expires_at"])
                <= _time(receipt["physical_lease_expires_at"])
                or _time(receipt["physical_lease_expires_at"]) - at > timedelta(seconds=LEASE_SECONDS)
                or not at < _time(job["timeout_at"])
                or not at < _time(payload["admission"]["binding"]["source_valid_until"])
                or _time(run["started_at"]) > _time(acceptance["accepted_at"])
                or _time(run["completed_at"]) != at or _time(job["heartbeat_at"]) != at or _time(job["updated_at"]) != at):
            _failure_reject()
        original = history._parent_input(tenant, payload["admission"]["binding"], before)
        if job["attempt"] == 1:
            facts = closed_initial_parser_journal(job, run, payload, original, acceptance, at, before=before)
            current, current_items = facts, payload["attempt_intents"]
            if _time(run["started_at"]) != _time(acceptance["accepted_at"]):
                _failure_reject()
        else:
            prior, current, facts = NativeParserAttemptHistory(history.graph).closed_current(
                job, run, physical, original, acceptance, at, before=before)
            current_items = prior.current_items
            if receipt["attempt_history_sha256"] != prior.history_sha256:
                _failure_reject()
        if (receipt["cause_code"] == _OBSERVED_STORAGE_CAUSE
                and (_storage_authorization_failure(current_items, current) is None
                    or receipt["error_class"] != "NativeIORejected")):
            _failure_reject()
        if (receipt["intent_ids"] != list(facts.intent_ids) or receipt["journal_sha256"] != facts.journal_sha256
                or canonical_json(receipt["quality_summary"]) != canonical_json(facts.quality_summary())):
            _failure_reject()
        _closed_slots(history.engine, facts, before)
        before()
        audit = history.engine.query_one("SELECT * FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=? "
            "AND resource_type='intake' AND resource_id=? AND actor_subject_id IS NULL AND service_principal=? "
            "AND actor_role='SVC_PARSER' AND action='parser.run_completed.v1' AND result='SUCCEEDED' "
            "AND causation_id=? AND correlation_id=?", (tenant, receipt["audit_event_id"], run["intake_id"], JOB_TYPE,
                original.parent_event_id, job["correlation_id"]))
        if (audit is None or _object(audit["before_value"]) != {"parser_run_id": run["parser_run_id"], "status": "RUNNING"}
                or not _audited_exact(_object(audit["after_value"]), {**receipt, "intake_mutated": False})
                or audit["reason"] != _REASON or _time(audit["occurred_at"]) != at):
            _failure_reject()
        for key in ("audit_event_id", "parser_completed_event_id", "dead_letter_event_id"):
            native_intake_uuid(receipt[key])
        history._event(job, receipt["parser_completed_event_id"], at, before, event_type="parser.run_completed",
            producer="parser_service", aggregate_type="parser_run", aggregate_id=run["parser_run_id"],
            aggregate_version=run["version"], partition_key=tenant + ":" + run["intake_id"],
            causation_id=original.parent_event_id, schema_ref="#/payloads/ParserRunCompletedV1",
            payload={"parser_run_id": run["parser_run_id"], "intake_id": run["intake_id"],
                "parser_release_id": run["parser_release_id"], "status": "FAILED", "quality_summary": facts.quality_summary()})
        history._event(job, receipt["dead_letter_event_id"], at, before, event_type="job.dead_lettered",
            producer="job_platform", aggregate_type="job", aggregate_id=job["job_id"],
            aggregate_version=job["version"], partition_key=tenant + ":" + job["job_id"],
            causation_id=original.parent_event_id, schema_ref="#/payloads/JobDeadLetteredV1",
            sensitive_fields=["payload.last_error_details"], payload={"job_id": job["job_id"], "job_type": JOB_TYPE,
                "checkpoint": "PARSING", "attempt": job["attempt"], "error_code": _CODE,
                "dead_lettered_at": at.isoformat(), "last_error_details": {"error_class": receipt["error_class"]}})
        before.check_in_memory()
        return NativeParserNegativeClosure(run["parser_run_id"], canonical_sha256(receipt))
    except NativeParserConflict:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
        _failure_reject()
