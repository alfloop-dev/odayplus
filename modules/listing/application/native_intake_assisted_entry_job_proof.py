"""Immutable manual job admission reads, without restoring execution keys."""

from __future__ import annotations

import hashlib
import re
from datetime import timedelta
from uuid import UUID, uuid5

from modules.listing.application.native_intake_assisted_entry_policy import (
    JOB_TYPE,
    NativeAssistedEntryConflict,
)
from modules.listing.application.native_intake_assisted_entry_request_proof import (
    NativeAssistedEntryRequestProof,
    _object,
    _time,
)
from modules.listing.application.native_intake_retrieval import audited_value_matches
from modules.listing.application.native_intake_retrieval_policy import canonical_sha256


def assisted_job_provenance(payload):
    keys = ("event_id", "event_sha256", "request_id", "intake_id", "expected_intake_version", "requested_by",
        "requested_at", "input_sha256", "fields_sha256", "command_sha256", "correction_ids",
        "source_policy_event_id", "source_policy_event_sha256", "root_identity_event_id", "policy_sha256",
        "timeout_at", "delivery_generation", "attempt_token_sha256")
    return {**{k: payload[k] for k in keys}, "execution_policy_sha256": canonical_sha256(payload["policy"])}


class NativeAssistedEntryJobProof:
    def __init__(self, graph):
        self.graph, self.engine, self.queue = graph, graph.engine, graph.queue

    def admission(self, job, *, before):
        receipt, _, _ = self._read(job, before=before, with_request=False)
        return receipt

    def admission_with_request(self, job, *, before):
        """Fresh positive input and its actual parent, with one origin proof.

        The caller must lock the physical/job rows and Intake before this
        method takes proposal share locks. No caller-supplied proof or cached
        human/source authority can substitute for the reads in this method.
        """
        return self._read(job, before=before, with_request=True)

    def _read(self, job, *, before, with_request):
        payload = _object(job["payload"])
        tenant, request_id = job["tenant_id"], payload["request_id"]
        reader = NativeAssistedEntryRequestProof(self.graph)
        request, parent = None, None
        if with_request:
            request, parent = reader.read_with_parent(tenant, payload["intake_id"], request_id, before=before)
            original = request.event
        else:
            original, _ = reader.envelope(tenant, payload["intake_id"], request_id, before=before)
        before()
        delivery = self.queue.get(job["job_id"])
        receipt = _object(payload.get("admission_receipt"))
        generation = payload.get("delivery_generation")
        physical = delivery.payload if delivery else None
        if (job["job_type"] != JOB_TYPE or job["aggregate_type"] != "intake_assisted_entry"
                or job["aggregate_id"] != request_id or job["idempotency_key"] != request_id
                or job["job_id"] != str(uuid5(UUID(request_id), JOB_TYPE)) or payload["event_id"] != request_id
                or payload["event_sha256"] != canonical_sha256(original)
                or any(payload.get(k) != v for k, v in original["payload"].items())
                or canonical_sha256(payload["policy"]) != payload["policy_sha256"]
                or job["correlation_id"] != original["correlation_id"]
                or delivery is None or delivery.job_type != JOB_TYPE or delivery.correlation_id != job["correlation_id"]
                or delivery.idempotency_key != JOB_TYPE + ":" + tenant + ":" + request_id
                or type(generation) is not int or not 1 <= generation <= 2**63 - 1
                or not isinstance(physical, dict) or set(physical) != {"job_id", "tenant_id", "attempt_token"}
                or physical["job_id"] != job["job_id"] or physical["tenant_id"] != tenant
                or not isinstance(physical["attempt_token"], str)
                or not re.fullmatch(str(generation) + r"\.[0-9a-f]{64}", physical["attempt_token"])
                or hashlib.sha256(physical["attempt_token"].encode()).hexdigest() != payload["attempt_token_sha256"]
                or _time(job["timeout_at"]) != _time(payload["timeout_at"])
                or set(receipt) != {"job_id", "request_id", "intake_id", "admitted_intake_version",
                    "job_status_at_admission", "timeout_at", "audit_event_id"}
                or any(receipt[k] != payload[k] for k in ("request_id", "intake_id", "timeout_at"))
                or receipt["job_id"] != job["job_id"] or receipt["job_status_at_admission"] != "QUEUED"
                or type(receipt["admitted_intake_version"]) is not int
                or receipt["admitted_intake_version"] != payload["expected_intake_version"]):
            raise NativeAssistedEntryConflict("NATIVE_ASSISTED_JOB_ADMISSION_CONFLICT")
        before()
        audit = self.engine.query_one("SELECT after_value FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=? "
            "AND resource_type='intake' AND resource_id=? AND actor_subject_id IS NULL AND service_principal=? "
            "AND actor_role='SVC_INTAKE' AND action='intake.assisted_entry_admitted.v1' AND result='SUCCEEDED' "
            "AND causation_id=? AND correlation_id=?", (tenant, receipt["audit_event_id"], payload["intake_id"],
                JOB_TYPE, request_id, job["correlation_id"]))
        if audit is None or not audited_value_matches(_object(audit["after_value"]),
                {**receipt, "native_assisted_provenance": assisted_job_provenance(payload)}):
            raise NativeAssistedEntryConflict("NATIVE_ASSISTED_JOB_ADMISSION_AUDIT_CONFLICT")
        before()
        return receipt, request, parent

    def acceptance(self, job, *, before):
        """Read the immutable accepted attempt; it grants no present access."""
        payload = _object(job["payload"])
        receipt = _object(payload.get("acceptance_receipt"))
        authority = _object(payload.get("accepted_authority"))
        if (set(receipt) != {"job_id", "request_id", "intake_id", "accepted_at", "business_fence",
                "physical_fence", "worker", "attempt", "audit_event_id"}
                or any(receipt[key] != payload[key] for key in ("request_id", "intake_id"))
                or receipt["job_id"] != job["job_id"]
                or type(receipt["business_fence"]) is not int or receipt["business_fence"] != job["fence_token"]
                or receipt["business_fence"] < 2
                or type(receipt["physical_fence"]) is not int or receipt["physical_fence"] < 1
                or receipt["physical_fence"] != payload.get("accepted_delivery_fence")
                or not isinstance(receipt["worker"], str) or not receipt["worker"]
                or receipt["worker"] != payload.get("accepted_worker")
                or type(receipt["attempt"]) is not int or receipt["attempt"] != job["attempt"]
                or not 1 <= receipt["attempt"] <= job["max_attempts"]
                or type(payload.get("accepted_source_version")) is not int or payload["accepted_source_version"] < 1
                or set(authority) != {"subject_id", "role", "checked_at", "scope_sha256"}
                or authority["subject_id"] != payload["requested_by"]
                or authority["role"] not in {"EXPANSION_STAFF", "EXPANSION_MANAGER", "DATA_STEWARD"}
                or _time(authority["checked_at"]) != _time(receipt["accepted_at"])
                or _time(receipt["accepted_at"]) < _time(payload["requested_at"])
                or _time(receipt["accepted_at"]) >= _time(job["timeout_at"])):
            raise NativeAssistedEntryConflict("NATIVE_ASSISTED_ACCEPTANCE_RECEIPT_REJECTED")
        for value in (payload.get("accepted_source_sha256"), payload.get("accepted_document_sha256"), authority["scope_sha256"]):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise NativeAssistedEntryConflict("NATIVE_ASSISTED_ACCEPTANCE_HASH_REJECTED")
        before()
        audit = self.engine.query_one("SELECT after_value,before_value,occurred_at FROM audit.audit_events "
            "WHERE tenant_id=? AND audit_event_id=? AND resource_type='intake' AND resource_id=? "
            "AND actor_subject_id IS NULL AND service_principal=? AND actor_role='SVC_INTAKE' "
            "AND action='intake.assisted_entry_attempt_accepted.v1' AND result='SUCCEEDED' "
            "AND causation_id=? AND correlation_id=?", (job["tenant_id"], receipt["audit_event_id"],
                payload["intake_id"], JOB_TYPE, payload["request_id"], job["correlation_id"]))
        expected = {**receipt, "status": "RUNNING", "intake_mutated": False,
            "native_assisted_provenance": assisted_job_provenance(payload),
            "acceptance": {key: payload[key] for key in ("accepted_source_sha256", "accepted_source_version",
                "accepted_authority", "accepted_document_sha256")}}
        if (audit is None or not audited_value_matches(_object(audit["after_value"]), expected)
                or receipt["business_fence"] != receipt["attempt"] + 1
                or _object(audit["before_value"]) != {
                    "job_id": job["job_id"], "status": "QUEUED" if receipt["attempt"] == 1 else "RETRYING"}
                or _time(audit["occurred_at"]) != _time(receipt["accepted_at"])):
            raise NativeAssistedEntryConflict("NATIVE_ASSISTED_ACCEPTANCE_AUDIT_REJECTED")
        before()
        return receipt

    def retry(self, job, *, before):
        """Read a previously authorized retry without granting new execution."""
        payload = _object(job["payload"])
        receipt = _object(payload.get("retry_receipt"))
        acceptance = self.acceptance(job, before=before)
        keys = {"job_id", "request_id", "intake_id", "prepared_at", "available_at", "business_fence",
            "physical_fence", "worker", "attempt", "audit_event_id", "acceptance_audit_event_id",
            "source_sha256", "source_version", "authority"}
        if (set(receipt) != keys or receipt["job_id"] != job["job_id"]
                or any(receipt[key] != payload[key] for key in ("request_id", "intake_id"))
                or receipt["acceptance_audit_event_id"] != acceptance["audit_event_id"]
                or any(receipt[key] != acceptance[key] for key in ("business_fence", "physical_fence", "worker", "attempt"))
                or not 1 <= receipt["attempt"] < job["max_attempts"]
                or not _time(acceptance["accepted_at"]) <= _time(receipt["prepared_at"]) < _time(receipt["available_at"])
                or _time(receipt["available_at"]) < _time(receipt["prepared_at"]) + timedelta(
                    seconds=payload["policy"]["backoff_seconds"][receipt["attempt"] - 1])
                or _time(receipt["available_at"]) >= _time(job["timeout_at"])
                or type(receipt["source_version"]) is not int or receipt["source_version"] < 1
                or not isinstance(receipt["source_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", receipt["source_sha256"])):
            raise NativeAssistedEntryConflict("NATIVE_ASSISTED_RETRY_RECEIPT_REJECTED")
        authority = _object(receipt["authority"])
        if (set(authority) != {"subject_id", "role", "checked_at", "scope_sha256"}
                or authority["subject_id"] != payload["requested_by"]
                or authority["role"] not in {"EXPANSION_STAFF", "EXPANSION_MANAGER", "DATA_STEWARD"}
                or _time(authority["checked_at"]) != _time(receipt["prepared_at"])
                or not isinstance(authority["scope_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", authority["scope_sha256"])):
            raise NativeAssistedEntryConflict("NATIVE_ASSISTED_RETRY_AUTHORITY_REJECTED")
        before()
        audit = self.engine.query_one("SELECT after_value,before_value,occurred_at FROM audit.audit_events "
            "WHERE tenant_id=? AND audit_event_id=? AND resource_type='intake' AND resource_id=? "
            "AND actor_subject_id IS NULL AND service_principal=? AND actor_role='SVC_INTAKE' "
            "AND action='intake.assisted_entry_delivery_settled.v1' AND result='SUCCEEDED' "
            "AND causation_id=? AND correlation_id=?", (job["tenant_id"], receipt["audit_event_id"],
                payload["intake_id"], JOB_TYPE, payload["request_id"], job["correlation_id"]))
        if (audit is None or not audited_value_matches(_object(audit["after_value"]),
                {**receipt, "status": "RETRYING", "intake_mutated": False,
                    "native_assisted_provenance": assisted_job_provenance(payload)})
                or _object(audit["before_value"]) != {"job_id": job["job_id"], "status": "RUNNING"}
                or _time(audit["occurred_at"]) != _time(receipt["prepared_at"])):
            raise NativeAssistedEntryConflict("NATIVE_ASSISTED_RETRY_AUDIT_REJECTED")
        before()
        return receipt
