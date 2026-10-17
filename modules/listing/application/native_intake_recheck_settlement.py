"""Settle owned recheck failures without restoring retired execution grants.

Immutable admission evidence authenticates this one existing command. Only
the current physical owner may settle it; retries also need today's key,
configuration, requester and unchanged waiting Intake. This module never
advances an Intake or performs external IO.
"""

from __future__ import annotations

import re
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException

from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_pending_commands import NativeIntakeCommandBudget
from modules.listing.application.native_intake_recheck_terminal_proof import (
    NativeRecheckTerminalProof,
)
from modules.listing.application.native_intake_source_policy import _json, _object, _time
from modules.listing.application.native_intake_source_policy_recheck import (
    NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE as JOB_TYPE,
)
from modules.listing.application.native_intake_source_policy_recheck import (
    NativeIntakeSourcePolicyRecheckUnavailable,
    native_intake_source_policy_recheck_policy,
)
from modules.listing.application.native_intake_source_policy_recheck_history import (
    NativeHistoricalRecheckProof,
)
from shared.audit.errors import CanonicalAuditUnavailable
from shared.domain.events import DomainEvent
from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError, JobStatus


class NativeRecheckSettlement:
    def __init__(self, owner):
        self.owner, self.graph, self.engine = owner, owner.graph, owner.engine
        self.queue, self.source = owner.queue, owner.source

    @staticmethod
    def _identity(delivery):
        """Closed routing fields only, with no current execution grant."""
        try:
            data = delivery.payload
            if (delivery.job_type != JOB_TYPE or not isinstance(data, dict)
                    or set(data) != {"job_id", "tenant_id", "attempt_token"}
                    or data["job_id"] != delivery.job_id or not isinstance(data["attempt_token"], str)
                    or re.fullmatch(r"[1-9][0-9]{0,18}\.[0-9a-f]{64}", data["attempt_token"]) is None):
                raise ValueError
            job_id, tenant = native_intake_uuid(data["job_id"]), native_intake_uuid(data["tenant_id"])
            generation = int(data["attempt_token"].split(".", 1)[0])
            if generation > 2**63 - 1:
                raise ValueError
            return job_id, tenant, generation
        except (ValueError, TypeError, AttributeError, KeyError):
            raise JobFenceRejectedError("NATIVE_RECHECK_NEGATIVE_IDENTITY_REJECTED") from None

    def _replay(self, delivery, job, view, before):
        before()
        actual = self.queue.get(job["job_id"])
        if (actual is None or actual.payload != delivery.payload
                or actual.correlation_id != delivery.correlation_id):
            raise JobFenceRejectedError("NATIVE_RECHECK_NEGATIVE_REPLAY_DELIVERY_REJECTED")
        payload = _object(job["payload"])
        # The terminal reader verifies the full canonical admission/request
        # chain itself. This raw row is only its independently bound subject.
        row = view._one("SELECT payload FROM workflow.outbox_events WHERE tenant_id=? AND event_id=?",
            (job["tenant_id"], native_intake_uuid(payload["event_id"])))
        closed = NativeRecheckTerminalProof(view).read(_object(row["payload"]), job, before=before)
        if closed is None:
            raise JobFenceRejectedError("NATIVE_RECHECK_NEGATIVE_REPLAY_NOT_TERMINAL")
        return _object(job["result"]) if closed["status"] == "SUCCEEDED" else None

    def _owned(self, delivery, tenant, generation, before, history):
        before()
        self.graph._one("SELECT job_id FROM durable_jobs WHERE job_id=? FOR UPDATE", (delivery.job_id,))
        before()
        current = self.queue.get(delivery.job_id)
        now = self.source._clock()
        if (current is None or current.job_type != JOB_TYPE or current.status != JobStatus.RUNNING
                or not current.locked_by or current.locked_by != delivery.locked_by
                or current.fence_token != delivery.fence_token or current.payload != delivery.payload
                or current.correlation_id != delivery.correlation_id
                or current.lease_expires_at is None or current.lease_expires_at <= now):
            raise JobFenceRejectedError("NATIVE_RECHECK_DELIVERY_NOT_OWNED")
        # Tighten the existing root's deadline to its actual owned lease;
        # neither the root nor the lease gets a new time period here.
        before.deadline = min(before.deadline, current.lease_expires_at)
        before()
        job = self.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=? FOR UPDATE",
            (tenant, delivery.job_id))
        payload = _object(job["payload"])
        history._admission_receipt(job, payload, before)
        history._origin._admitted_request(job, payload, before)
        if (payload["delivery_generation"] != generation or job["status"] not in {"QUEUED", "RETRYING"}
                or job["result"] is not None or job["checkpoint"] != history._waiting_state(payload)
                or type(job["fence_token"]) is not int or job["fence_token"] != 1
                or job["lease_expires_at"] is not None
                or type(job["max_attempts"]) is not int or not 1 <= job["max_attempts"] <= 3
                or type(job["attempt"]) is not int or not 0 <= job["attempt"] < job["max_attempts"]
                or job["max_attempts"] != payload["policy"]["max_attempts"]
                or current.max_retries != job["max_attempts"] or current.attempts < job["attempt"] + 1
                or current.idempotency_key != JOB_TYPE + ":" + tenant + ":" + payload["request_id"]
                or any(key in payload for key in ("accepted_worker", "accepted_delivery_fence"))):
            raise JobFenceRejectedError("NATIVE_RECHECK_UNKNOWN_ATTEMPT_OUTCOME")
        return current, job, payload

    def _current_execution(self, current, tenant, generation):
        try:
            configured = native_intake_source_policy_recheck_policy()
            valid_key = self.owner.auth.decode(current) == (tenant, generation)
            return configured, valid_key
        except (NativeIntakeSourcePolicyRecheckUnavailable, JobFenceRejectedError):
            return None, False

    def _retry_due(self, intake, payload, job, attempt, now, before):
        # A negative origin proof cannot become a new requester grant.
        if (self.owner._waiting_context(payload) != (intake["processing_state"], intake["source_policy_state"])
                or intake["version"] != payload["expected_intake_version"]
                or any(intake[k] is not None for k in ("deleted_at", "resolved_listing_id", "completed_at"))
                or self.source._input_digest(intake) != payload["input_sha256"]):
            return None
        try:
            self.owner._authority(payload["requested_by"], intake, before)
        except PermissionError:
            return None
        except HTTPException as error:
            if error.status_code in {401, 403}:
                return None
            raise
        due = now + timedelta(seconds=payload["policy"]["backoff_seconds"][attempt - 1])
        return due if due < _time(job["timeout_at"]) else None

    def settle(self, delivery, error):
        job_id, tenant, generation = self._identity(delivery)
        with self.graph.transaction(tenant):
            before = NativeIntakeCommandBudget(self.source)
            view = self.source._producer_reads(before)
            preliminary = view._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?", (tenant, job_id))
            if preliminary["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                return self._replay(delivery, preliminary, view, before)
            history = NativeHistoricalRecheckProof(view)
            current, job, payload = self._owned(delivery, tenant, generation, before, history)
            before()
            intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE",
                (tenant, payload["intake_id"]))
            now = self.source._clock()
            cancelled = job["cancellation_requested_at"] is not None
            configured, valid_key = (None, False) if cancelled else self._current_execution(current, tenant, generation)
            if (isinstance(error, JobFenceRejectedError) and valid_key and configured == payload["policy"]
                    and _time(job["timeout_at"]) > now and not cancelled):
                raise error
            attempt = job["attempt"] + 1
            retryable = (getattr(error, "sqlstate", None) in {"40001", "40P01"}
                or isinstance(error, CanonicalAuditUnavailable))
            due = None
            if (not cancelled and retryable and valid_key and configured == payload["policy"]
                    and attempt < job["max_attempts"]):
                due = self._retry_due(intake, payload, job, attempt, now, before)
            status = "CANCELLED" if cancelled else "RETRYING" if due else "FAILED"
            code = ("NATIVE_RECHECK_CANCELLED" if cancelled else "NATIVE_RECHECK_RETRY_SCHEDULED"
                if due else "NATIVE_RECHECK_FAILED")
            before()
            if self.engine.execute("UPDATE workflow.jobs SET status=?,attempt=?,last_error=?::jsonb,next_attempt_at=?,"
                    "version=version+1,updated_at=? WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? "
                    "AND status IN ('QUEUED','RETRYING') AND result IS NULL AND lease_expires_at IS NULL",
                    (status, attempt, _json({"code": code, "error_class": type(error).__name__}),
                        due.isoformat() if due else None, now.isoformat(), tenant, job_id,
                        job["version"], job["fence_token"])).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_RECHECK_SETTLEMENT_FENCE_REJECTED")
            before()
            self.owner._audit(intake, "intake.source_policy_recheck_delivery_settled.v1",
                {"job_id": job_id, "status": job["status"]},
                {"job_id": job_id, "status": status, "attempt": attempt, "code": code},
                now, str(uuid4()), payload["event_id"])
            before()
            if not self.engine.query_one("SELECT job_id FROM durable_jobs WHERE job_id=? AND status=? "
                    "AND fence_token=? AND locked_by=? AND lease_expires_at>clock_timestamp()",
                    (job_id, JobStatus.RUNNING.value, current.fence_token, current.locked_by)):
                raise JobFenceRejectedError("NATIVE_RECHECK_NEGATIVE_FINAL_DELIVERY_REJECTED")
            before()
            self.queue.update_status(job_id,
                JobStatus.CANCELLED if cancelled else JobStatus.QUEUED if due else JobStatus.FAILED,
                delivery_state=None if cancelled else JobDeliveryState.RETRYING if due else JobDeliveryState.DEAD_LETTER,
                expected_version=current.version, fence_token=current.fence_token, error_message=code)
            before()
            if due:
                self.engine.execute("UPDATE durable_jobs SET available_at=? WHERE job_id=?", (due.isoformat(), job_id))
            elif not cancelled:
                self.graph._outbox(DomainEvent(event_type="job.dead_lettered", producer="job_platform", tenant_id=tenant,
                    aggregate_type="job", aggregate_id=job_id, aggregate_version=job["version"] + 1,
                    partition_key=tenant + ":" + job_id, correlation_id=job["correlation_id"],
                    causation_id=payload["event_id"], actor_ref=JOB_TYPE, occurred_at=now,
                    schema_ref="#/payloads/JobDeadLetteredV1", sensitive_fields=["payload.last_error_details"],
                    payload={"job_id": job_id, "job_type": JOB_TYPE, "checkpoint": job["checkpoint"],
                        "attempt": attempt, "error_code": code, "dead_lettered_at": now.isoformat(),
                        "last_error_details": {"error_class": type(error).__name__}}))
            before()
            return None
