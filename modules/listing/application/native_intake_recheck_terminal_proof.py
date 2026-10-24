"""Historical recheck closure; no source, human or execution authority."""

from __future__ import annotations

from uuid import UUID, uuid5

from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryConflict,
)
from modules.listing.application.native_intake_retrieval import audited_value_matches
from modules.listing.application.native_intake_source_policy import _object, _time
from modules.listing.application.native_intake_source_policy_recheck_history import (
    NativeHistoricalRecheckProof,
)
from shared.domain.events import validate_event
from shared.jobs.queue import JobDeliveryState, JobStatus

JOB_TYPE = "native-intake-source-policy-recheck"


class NativeRecheckTerminalProof:
    def __init__(self, graph):
        self.graph, self.engine, self.queue = graph, graph.engine, graph.queue

    @staticmethod
    def reject():
        raise NativeAssistedEntryConflict("NATIVE_COMMAND_RECHECK_TERMINAL_PROOF_REJECTED")

    def read(self, original, job, *, before):
        """Return a proven terminal, or None for actual unfinished work.

        In particular, a status pair is a necessary discriminator, never a
        closure proof. Legacy negative writers recorded no accepted worker;
        their real admission, settlement audit and dead letter are required.
        """
        tenant, data = original["tenant_id"], original["payload"]
        if (job["tenant_id"] != tenant or job["job_type"] != JOB_TYPE
                or job["aggregate_type"] != "intake_policy_recheck"
                or job["aggregate_id"] != original["event_id"]
                or job["job_id"] != str(uuid5(UUID(original["event_id"]), JOB_TYPE))):
            self.reject()
        if job["status"] in {"QUEUED", "RUNNING", "RETRYING"}:
            return None
        before()
        delivery = self.queue.get(job["job_id"])
        payload = _object(job["payload"])
        if delivery is None:
            self.reject()
        history = NativeHistoricalRecheckProof(self.graph)
        if job["status"] == "SUCCEEDED":
            verified = history._committed_result(delivery, tenant, payload.get("delivery_generation"), before=before)
            if verified is None:
                self.reject()
            result, _ = verified
            if (result["request_id"] != original["event_id"]
                    or result["intake_id"] != data["intake_id"]):
                self.reject()
            return {"job_id": job["job_id"], "status": "SUCCEEDED", "audit_event_id": result["audit_event_id"],
                "outbox_event_id": result["outbox_event_id"]}
        if job["status"] not in {"FAILED", "CANCELLED"}:
            self.reject()
        history._admission_receipt(job, payload, before)
        history._origin._admitted_request(job, payload, before)
        cancelled = job["status"] == "CANCELLED"
        code = "NATIVE_RECHECK_CANCELLED" if cancelled else "NATIVE_RECHECK_FAILED"
        error = _object(job["last_error"])
        if (job["result"] is not None or job["checkpoint"] != history._waiting_state(payload)
                or job["idempotency_key"] != original["event_id"]
                or delivery.idempotency_key != JOB_TYPE + ":" + tenant + ":" + original["event_id"]
                or delivery.status != (JobStatus.CANCELLED if cancelled else JobStatus.FAILED)
                or delivery.delivery_state != (None if cancelled else JobDeliveryState.DEAD_LETTER)
                or delivery.error_message != code or delivery.locked_by is not None
                or delivery.lease_expires_at is not None or delivery.leased_until is not None
                or job["lease_expires_at"] is not None
                or type(job["attempt"]) is not int or not 1 <= job["attempt"] <= job["max_attempts"]
                or job["max_attempts"] != payload["policy"]["max_attempts"]
                or delivery.max_retries != job["max_attempts"] or delivery.attempts < job["attempt"]
                or type(job["fence_token"]) is not int or job["fence_token"] != 1
                or type(delivery.fence_token) is not int or delivery.fence_token < 1
                or any(k in payload for k in ("accepted_worker", "accepted_delivery_fence"))
                or cancelled != (job["cancellation_requested_at"] is not None)
                or set(error) != {"code", "error_class"} or error["code"] != code
                or not isinstance(error["error_class"], str) or not error["error_class"]):
            self.reject()
        before()
        rows = self.engine.query("SELECT * FROM audit.audit_events WHERE tenant_id=? "
            "AND resource_type='intake' AND resource_id=? AND actor_subject_id IS NULL "
            "AND service_principal=? AND actor_role='SVC_INTAKE' "
            "AND action='intake.source_policy_recheck_delivery_settled.v1' AND result='SUCCEEDED' "
            "AND causation_id=? AND correlation_id=? AND after_value->>'job_id'=? "
            "AND after_value->>'status'=? LIMIT 2", (tenant, data["intake_id"], JOB_TYPE,
                original["event_id"], job["correlation_id"], job["job_id"], job["status"]))
        if len(rows) != 1:
            self.reject()
        audit = rows[0]
        expected = {"job_id": job["job_id"], "status": job["status"], "attempt": job["attempt"], "code": code}
        prior = _object(audit["before_value"])
        if (not audited_value_matches(_object(audit["after_value"]), expected)
                or set(prior) != {"job_id", "status"} or prior["job_id"] != job["job_id"]
                or prior["status"] not in {"QUEUED", "RETRYING"}
                or (prior["status"] == "RETRYING" and job["attempt"] < 2)
                or _time(audit["occurred_at"]) != _time(job["updated_at"])):
            self.reject()
        before()
        if self.engine.query_one("SELECT transition_id FROM intake.intake_stage_transitions "
                "WHERE tenant_id=? AND job_id=? LIMIT 1", (tenant, job["job_id"])):
            self.reject()
        dead_letter_id = self._dead_letter(original, job, audit, error, cancelled, before)
        before()
        return {"job_id": job["job_id"], "status": job["status"], "audit_event_id": audit["audit_event_id"],
            "outbox_event_id": dead_letter_id}

    def _dead_letter(self, original, job, audit, error, cancelled, before):
        before()
        rows = self.engine.query("SELECT * FROM workflow.outbox_events WHERE tenant_id=? "
            "AND event_type='job.dead_lettered' AND aggregate_type='job' AND aggregate_id=? LIMIT 2",
            (job["tenant_id"], job["job_id"]))
        if cancelled:
            if rows:
                self.reject()
            return None
        if len(rows) != 1:
            self.reject()
        row = rows[0]
        event = _object(row["payload"])
        at = event.get("occurred_at")
        expected = {"job_id": job["job_id"], "job_type": JOB_TYPE, "checkpoint": job["checkpoint"],
            "attempt": job["attempt"], "error_code": "NATIVE_RECHECK_FAILED", "dead_lettered_at": at,
            "last_error_details": {"error_class": error["error_class"]}}
        if (validate_event(event) or event.get("event_id") != row["event_id"]
                or event.get("event_type") != row["event_type"] or event.get("producer") != "job_platform"
                or type(event.get("event_version")) is not int or event["event_version"] != 1 or row["event_version"] != 1
                or event.get("aggregate_type") != "job" or event.get("aggregate_id") != job["job_id"]
                or type(event.get("aggregate_version")) is not int or event["aggregate_version"] != job["version"]
                or row["aggregate_version"] != job["version"] or event.get("tenant_id") != job["tenant_id"]
                or row["partition_key"] != job["tenant_id"] + ":" + job["job_id"]
                or event.get("partition_key") != row["partition_key"]
                or row["correlation_id"] != job["correlation_id"] or event.get("correlation_id") != row["correlation_id"]
                or event.get("causation_id") != original["event_id"] or event.get("actor_ref") != JOB_TYPE
                or event.get("schema_ref") != "#/payloads/JobDeadLetteredV1"
                or event.get("sensitive_fields") != ["payload.last_error_details"]
                or event.get("payload") != expected or _time(at) != _time(row["occurred_at"])
                or _time(at) != _time(audit["occurred_at"])):
            self.reject()
        return row["event_id"]
