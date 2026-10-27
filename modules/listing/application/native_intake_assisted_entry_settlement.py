"""Job-only negative settlement under actual owner fences, without old grants."""

from __future__ import annotations

import re
from uuid import uuid4

from modules.listing.application.native_intake_assisted_entry_job_proof import (
    assisted_job_provenance,
)
from modules.listing.application.native_intake_assisted_entry_policy import JOB_TYPE
from modules.listing.application.native_intake_assisted_entry_request_proof import _object, _time
from modules.listing.application.native_intake_assisted_entry_terminal import (
    NativeAssistedEntryTerminal,
)
from modules.listing.application.native_intake_pending_commands import NativeIntakeCommandBudget
from modules.listing.application.native_intake_retrieval_policy import canonical_json
from shared.domain.events import DomainEvent
from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError, JobStatus


class NativeAssistedEntrySettlement:
    def __init__(self, owner):
        self.owner, self.graph, self.engine, self.queue = owner, owner.graph, owner.engine, owner.queue

    def replay(self, delivery, *, leases=None):
        tenant, generation = self.owner.delivery_identity(delivery)
        if leases is not None:
            leases.pause()
        with self.graph.transaction(tenant):
            return NativeAssistedEntryTerminal(self.graph).replay(delivery, tenant, generation,
                before=NativeIntakeCommandBudget(self.owner.source.source))

    def negative(self, delivery, error, *, business_fence=None, leases=None):
        # Unknown successful SQL is proved before another write. The second
        # root independently locks the actual physical/business rows: a commit
        # that races this read causes a fence rejection, never an overwrite.
        outcome = self.replay(delivery, leases=leases)
        if outcome["terminal"]:
            return outcome["result"]
        tenant, generation = self.owner.delivery_identity(delivery)
        try:
            return self._negative(delivery, tenant, generation, error, business_fence=business_fence)
        except Exception:
            outcome = self.replay(delivery, leases=leases)
            if outcome["terminal"]:
                return outcome["result"]
            raise

    def _negative(self, delivery, tenant, generation, error, *, business_fence):
        with self.graph.transaction(tenant):
            physical, job, payload, before = self.owner._historical_locked(delivery, tenant, generation,
                business_fence=business_fence)
            accepted = business_fence is not None
            if job["status"] not in ({"RUNNING"} if accepted else {"QUEUED", "RETRYING"}):
                raise JobFenceRejectedError("NATIVE_ASSISTED_NEGATIVE_ACCEPTANCE_CONTEXT_REJECTED")
            before()
            intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE",
                (tenant, payload["intake_id"]))
            cancelled = job["cancellation_requested_at"] is not None
            at = self.owner._clock()
            status, code = ("CANCELLED", "NATIVE_ASSISTED_CANCELLED") if cancelled else ("FAILED", "NATIVE_ASSISTED_FAILED")
            if cancelled:
                before()
                original = self.graph._one("SELECT payload FROM workflow.outbox_events WHERE tenant_id=? AND event_id=?",
                    (tenant, payload["request_id"]))
                withdrawal = self.owner.pending.withdrawal(_object(original["payload"]), "ASSISTED_ENTRY", before)
                if (withdrawal is None or withdrawal[0]["payload"]["job_id"] != job["job_id"]
                        or _time(withdrawal[0]["payload"]["requested_at"]) != _time(job["cancellation_requested_at"])
                        or _time(job["cancellation_requested_at"]) > at):
                    raise JobFenceRejectedError("NATIVE_ASSISTED_NEGATIVE_CANCELLATION_JOURNAL_REJECTED")
            error_class = type(error).__name__[:96]
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,95}", error_class):
                error_class = "NativeAssistedExecutionError"
            receipt = {"job_id": job["job_id"], "request_id": payload["request_id"], "intake_id": payload["intake_id"],
                "status": status, "settled_at": at.isoformat(), "prior_status": job["status"],
                "business_fence": job["fence_token"], "physical_fence": physical.fence_token,
                "worker": physical.locked_by, "business_attempt": job["attempt"], "physical_attempt": physical.attempts,
                "business_version": job["version"] + 1, "physical_version": physical.version + 1,
                "accepted": accepted, "audit_event_id": str(uuid4()), "code": code, "error_class": error_class,
                "dead_letter_event_id": None if cancelled else str(uuid4())}
            updated = {**payload, "settlement_receipt": receipt}
            before()
            if self.engine.execute("UPDATE workflow.jobs SET status=?,payload=?::jsonb,last_error=?::jsonb,"
                    "lease_expires_at=NULL,next_attempt_at=NULL,heartbeat_at=?,version=version+1,updated_at=? "
                    "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND status=? AND result IS NULL "
                    "AND (cancellation_requested_at IS NOT NULL)=?::boolean "
                    "AND ((status='RUNNING' AND lease_expires_at>clock_timestamp()) "
                    "OR (status IN ('QUEUED','RETRYING') AND lease_expires_at IS NULL))",
                    (status, canonical_json(updated), canonical_json({"code": code, "error_class": error_class}),
                        at.isoformat(), at.isoformat(), tenant, job["job_id"], job["version"], job["fence_token"],
                        job["status"], cancelled)).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_ASSISTED_NEGATIVE_BUSINESS_CAS_REJECTED")
            before()
            self.owner._audit(intake, "intake.assisted_entry_delivery_settled.v1",
                {"job_id": job["job_id"], "status": job["status"]},
                {**receipt, "intake_mutated": False, "native_assisted_provenance": assisted_job_provenance(payload)},
                at, audit_id=receipt["audit_event_id"], causation=payload["request_id"],
                reason="Settle this admitted job under its actual owner fences without changing Intake or human evidence.")
            before()
            if not self.engine.query_one("SELECT job_id FROM durable_jobs WHERE job_id=? AND status=? "
                    "AND fence_token=? AND version=? AND locked_by=? AND lease_expires_at>clock_timestamp()",
                    (job["job_id"], JobStatus.RUNNING.value, physical.fence_token, physical.version, physical.locked_by)):
                raise JobFenceRejectedError("NATIVE_ASSISTED_NEGATIVE_PHYSICAL_FENCE_REJECTED")
            before()
            self.queue.update_status(job["job_id"], JobStatus.CANCELLED if cancelled else JobStatus.FAILED,
                delivery_state=None if cancelled else JobDeliveryState.DEAD_LETTER,
                expected_version=physical.version, fence_token=physical.fence_token, error_message=code)
            if not cancelled:
                before()
                self.graph._outbox(DomainEvent(event_id=receipt["dead_letter_event_id"], event_type="job.dead_lettered",
                    producer="job_platform", tenant_id=tenant, aggregate_type="job", aggregate_id=job["job_id"],
                    aggregate_version=job["version"] + 1, partition_key=tenant + ":" + job["job_id"],
                    correlation_id=job["correlation_id"], causation_id=payload["request_id"], actor_ref=JOB_TYPE, occurred_at=at,
                    schema_ref="#/payloads/JobDeadLetteredV1", sensitive_fields=["payload.last_error_details"],
                    payload={"job_id": job["job_id"], "job_type": JOB_TYPE, "checkpoint": job["checkpoint"],
                        "attempt": job["attempt"] if accepted else physical.attempts, "error_code": code,
                        "dead_lettered_at": at.isoformat(), "last_error_details": {"error_class": error_class}}))
            before()
            return None
