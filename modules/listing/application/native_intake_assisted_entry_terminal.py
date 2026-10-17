"""Full manual terminal proofs, never a present execution capability."""

from __future__ import annotations

from datetime import timedelta

from modules.listing.application.native_intake_assisted_entry_history import (
    NativeAssistedEntryHistory,
)
from modules.listing.application.native_intake_assisted_entry_job_proof import (
    NativeAssistedEntryJobProof,
    assisted_job_provenance,
)
from modules.listing.application.native_intake_assisted_entry_policy import JOB_TYPE
from modules.listing.application.native_intake_assisted_entry_request_proof import _object, _time
from modules.listing.application.native_intake_pending_commands import NativeIntakePendingCommands
from modules.listing.application.native_intake_retrieval import audited_value_matches
from modules.listing.application.native_intake_retrieval_policy import canonical_sha256
from shared.domain.events import validate_event
from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError, JobStatus

NEGATIVE_FIELDS = frozenset({"job_id", "request_id", "intake_id", "status", "settled_at", "prior_status",
    "business_fence", "physical_fence", "worker", "business_attempt", "physical_attempt", "business_version",
    "physical_version", "accepted", "audit_event_id", "code", "error_class", "dead_letter_event_id"})


def reject():
    raise JobFenceRejectedError("NATIVE_ASSISTED_NEGATIVE_TERMINAL_PROOF_REJECTED")


class NativeAssistedEntryTerminal:
    def __init__(self, graph):
        self.graph, self.engine, self.queue = graph, graph.engine, graph.queue

    def replay(self, delivery, tenant, generation, *, before):
        """An explicit discriminator keeps negative success distinct from active."""
        before()
        job = self.engine.query_one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?", (tenant, delivery.job_id))
        if job is None:
            raise JobFenceRejectedError("NATIVE_ASSISTED_REPLAY_JOB_MISSING")
        payload = _object(job["payload"])
        before()
        physical = self.queue.get(job["job_id"])
        if (physical is None or physical.payload != delivery.payload or physical.correlation_id != delivery.correlation_id
                or payload.get("delivery_generation") != generation):
            reject()
        if job["status"] in {"QUEUED", "RUNNING", "RETRYING"}:
            return {"terminal": False, "result": None}
        before()
        original = self.graph._one("SELECT payload FROM workflow.outbox_events WHERE tenant_id=? AND event_id=?",
            (tenant, payload["request_id"]))
        closure = self.read(_object(original["payload"]), job, before=before)
        if closure is None:
            reject()
        return {"terminal": True, "result": _object(job["result"]) if closure["status"] == "SUCCEEDED" else None}

    def read(self, original, job, *, before):
        payload = _object(job["payload"])
        if (not {"request_id", "intake_id", "event_sha256", "delivery_generation"} <= payload.keys()
                or job["tenant_id"] != original["tenant_id"] or payload["request_id"] != original["event_id"]
                or payload["event_sha256"] != canonical_sha256(original)
                or payload["intake_id"] != original["payload"]["intake_id"]):
            reject()
        if job["status"] in {"QUEUED", "RUNNING", "RETRYING"}:
            return None
        before()
        delivery = self.queue.get(job["job_id"])
        if delivery is None:
            reject()
        if job["status"] == "SUCCEEDED":
            result = NativeAssistedEntryHistory(self.graph).committed_result(delivery, job["tenant_id"],
                payload["delivery_generation"], before=before)
            if result is None:
                reject()
            return {"job_id": job["job_id"], "status": "SUCCEEDED", "audit_event_id": result["audit_event_id"],
                "outbox_event_id": result["outbox_event_id"]}
        if job["status"] not in {"FAILED", "CANCELLED"}:
            reject()
        proof = NativeAssistedEntryJobProof(self.graph)
        proof.admission(job, before=before)
        receipt = _object(payload.get("settlement_receipt"))
        cancelled = job["status"] == "CANCELLED"
        code = "NATIVE_ASSISTED_CANCELLED" if cancelled else "NATIVE_ASSISTED_FAILED"
        integers = ("business_fence", "physical_fence", "physical_attempt", "business_version", "physical_version")
        if (set(receipt) != NEGATIVE_FIELDS or any(type(receipt[k]) is not int or receipt[k] < 1 for k in integers)
                or type(receipt["business_attempt"]) is not int or not 0 <= receipt["business_attempt"] <= job["max_attempts"]
                or type(receipt["accepted"]) is not bool or receipt["accepted"] != (receipt["prior_status"] == "RUNNING")
                or receipt["prior_status"] not in {"QUEUED", "RETRYING", "RUNNING"}
                or receipt["job_id"] != job["job_id"] or receipt["status"] != job["status"] or receipt["code"] != code
                or any(receipt[k] != payload[k] for k in ("request_id", "intake_id"))
                or receipt["business_fence"] != job["fence_token"] or receipt["business_attempt"] != job["attempt"]
                or receipt["business_fence"] != receipt["business_attempt"] + 1
                or receipt["business_version"] != job["version"] or receipt["physical_version"] != delivery.version
                or receipt["physical_fence"] != delivery.fence_token or receipt["physical_attempt"] != delivery.attempts
                or not isinstance(receipt["worker"], str) or not receipt["worker"]
                or not isinstance(receipt["error_class"], str) or not receipt["error_class"]
                or job["result"] is not None or job["checkpoint"] != "AWAITING_ASSISTED_ENTRY"
                or job["lease_expires_at"] is not None or job["next_attempt_at"] is not None
                or delivery.status != (JobStatus.CANCELLED if cancelled else JobStatus.FAILED)
                or delivery.delivery_state != (None if cancelled else JobDeliveryState.DEAD_LETTER)
                or delivery.error_message != code or delivery.locked_by is not None
                or delivery.lease_expires_at is not None or delivery.leased_until is not None
                or delivery.max_retries != job["max_attempts"] or job["max_attempts"] != payload["policy"]["max_attempts"]
                or _object(job["last_error"]) != {"code": code, "error_class": receipt["error_class"]}
                or _time(receipt["settled_at"]) != _time(job["updated_at"])
                or _time(receipt["settled_at"]) < _time(payload["requested_at"])
                or cancelled != (job["cancellation_requested_at"] is not None)):
            reject()
        if receipt["prior_status"] == "QUEUED":
            if receipt["business_attempt"] != 0 or any(k in payload for k in (
                    "acceptance_receipt", "accepted_worker", "accepted_delivery_fence", "retry_receipt")):
                reject()
        else:
            accepted = proof.acceptance(job, before=before)
            if receipt["accepted"] and any(receipt[k] != accepted[a] for k, a in (
                    ("physical_fence", "physical_fence"), ("worker", "worker"), ("business_attempt", "attempt"))):
                reject()
            if receipt["prior_status"] == "RETRYING":
                retry = proof.retry(job, before=before)
                if (receipt["physical_attempt"] < receipt["business_attempt"] + 1
                        or _time(retry["prepared_at"]) > _time(receipt["settled_at"])):
                    reject()
        if receipt["physical_attempt"] < max(1, receipt["business_attempt"]):
            reject()
        if cancelled:
            withdrawal = NativeIntakePendingCommands(self.graph).withdrawal(original, "ASSISTED_ENTRY", before)
            if (withdrawal is None or withdrawal[0]["payload"]["job_id"] != job["job_id"]
                    or _time(withdrawal[0]["payload"]["requested_at"]) != _time(job["cancellation_requested_at"])
                    or _time(job["cancellation_requested_at"]) > _time(receipt["settled_at"])):
                reject()
        before()
        audit = self.engine.query_one("SELECT after_value,before_value,occurred_at FROM audit.audit_events "
            "WHERE tenant_id=? AND audit_event_id=? AND resource_type='intake' AND resource_id=? "
            "AND actor_subject_id IS NULL AND service_principal=? AND actor_role='SVC_INTAKE' "
            "AND action='intake.assisted_entry_delivery_settled.v1' AND result='SUCCEEDED' "
            "AND causation_id=? AND correlation_id=?", (job["tenant_id"], receipt["audit_event_id"],
                payload["intake_id"], JOB_TYPE, payload["request_id"], job["correlation_id"]))
        if (audit is None or _time(audit["occurred_at"]) != _time(receipt["settled_at"])
                or _object(audit["before_value"]) != {"job_id": job["job_id"], "status": receipt["prior_status"]}
                or not audited_value_matches(_object(audit["after_value"]),
                    {**receipt, "intake_mutated": False, "native_assisted_provenance": assisted_job_provenance(payload)})):
            reject()
        before()
        if self.engine.query_one("SELECT transition_id FROM intake.intake_stage_transitions WHERE tenant_id=? AND job_id=? LIMIT 1",
                (job["tenant_id"], job["job_id"])):
            reject()
        self._dead_letter(job, receipt, before)
        before()
        return {"job_id": job["job_id"], "status": job["status"], "audit_event_id": receipt["audit_event_id"],
            "outbox_event_id": receipt["dead_letter_event_id"]}

    def _dead_letter(self, job, receipt, before):
        before()
        rows = self.engine.query("SELECT * FROM workflow.outbox_events WHERE tenant_id=? AND event_type='job.dead_lettered' "
            "AND aggregate_type='job' AND aggregate_id=? LIMIT 2", (job["tenant_id"], job["job_id"]))
        if receipt["status"] == "CANCELLED":
            if rows or receipt["dead_letter_event_id"] is not None:
                reject()
            return
        if len(rows) != 1:
            reject()
        row, payload = rows[0], _object(job["payload"])
        event = _object(row["payload"])
        at = _time(receipt["settled_at"])
        expected = {"event_id": receipt["dead_letter_event_id"], "event_type": "job.dead_lettered", "event_version": 1,
            "producer": "job_platform", "tenant_id": job["tenant_id"], "aggregate_type": "job", "aggregate_id": job["job_id"],
            "aggregate_version": job["version"], "partition_key": job["tenant_id"] + ":" + job["job_id"],
            "correlation_id": job["correlation_id"], "causation_id": payload["request_id"], "actor_ref": JOB_TYPE,
            "schema_ref": "#/payloads/JobDeadLetteredV1", "sensitive_fields": ["payload.last_error_details"],
            "payload": {"job_id": job["job_id"], "job_type": JOB_TYPE, "checkpoint": job["checkpoint"],
                "attempt": receipt["business_attempt"] if receipt["accepted"] else receipt["physical_attempt"],
                "error_code": receipt["code"], "dead_lettered_at": receipt["settled_at"],
                "last_error_details": {"error_class": receipt["error_class"]}}}
        if (validate_event(event) or any(event.get(k) != v for k, v in expected.items())
                or any(row[k] != expected[k] for k in ("event_id", "event_type", "event_version", "tenant_id",
                    "aggregate_type", "aggregate_id", "aggregate_version", "partition_key", "correlation_id"))
                or type(event.get("event_version")) is not int or type(event.get("aggregate_version")) is not int
                or _time(event.get("occurred_at")) != at or _time(row["occurred_at"]) != at
                or _time(row["retention_until"]) != at + timedelta(days=30)):
            reject()
