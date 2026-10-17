"""Prove a committed human completion without restoring execution authority."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta

from modules.external_data.application.native_assisted_snapshot_transport import (
    native_assisted_snapshot_object_key,
)
from modules.listing.application.native_intake_assisted_entry_input import (
    IDENTITY_FIELDS,
    NativeAssistedEntryCommand,
    manual_evidence_document,
)
from modules.listing.application.native_intake_assisted_entry_job_proof import (
    NativeAssistedEntryJobProof,
    assisted_job_provenance,
)
from modules.listing.application.native_intake_assisted_entry_policy import JOB_TYPE
from modules.listing.application.native_intake_assisted_entry_request_proof import (
    _object,
    _time,
    request_audit_id,
)
from modules.listing.application.native_intake_assisted_entry_results import (
    SNAPSHOT_FIELDS,
    snapshot_payload,
)
from modules.listing.application.native_intake_retrieval import audited_value_matches
from modules.listing.application.native_intake_retrieval_policy import (
    NativeSnapshotBinding,
    canonical_sha256,
)
from shared.domain.events import validate_event
from shared.infrastructure.persistence.assisted_listing_intake import (
    NativeIntakeSnapshotMetadataRepository,
)
from shared.jobs.queue import JobFenceRejectedError, JobStatus


def _reject():
    raise JobFenceRejectedError("NATIVE_ASSISTED_TERMINAL_PROOF_REJECTED")


class NativeAssistedEntryHistory:
    def __init__(self, graph):
        self.graph, self.engine, self.queue = graph, graph.engine, graph.queue

    def committed_result(self, delivery, tenant, generation, *, before):
        """Authenticate success, including after later independent review.

        No current configuration, key, source permission or human role is
        consulted. This result never permits storage IO or an Intake write.
        The caller owns its bounded SERIALIZABLE tenant root.
        """
        before()
        job = self.engine.query_one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
            (tenant, delivery.job_id))
        if job is None or job["status"] != "SUCCEEDED":
            return None
        payload, result = _object(job["payload"]), _object(job["result"])
        proof = NativeAssistedEntryJobProof(self.graph)
        proof.admission(job, before=before)
        acceptance = proof.acceptance(job, before=before)
        before()
        physical = self.queue.get(job["job_id"])
        keys = {"job_id", "request_id", "intake_id", "processing_state", "version", "transition_id", "audit_event_id",
            "outbox_event_id", "correlation_id", "source_snapshot_id", "snapshot_created_event_id", "requested_by",
            "human_role", "accepted_delivery_fence", "accepted_business_fence", "accepted_attempt", "accepted_worker",
            "acceptance_audit_event_id", "native_snapshot_proof"}
        if (set(result) != keys or physical is None or physical.status != JobStatus.SUCCEEDED
                or physical.delivery_state is not None or physical.error_message is not None
                or any(getattr(physical, key) is not None for key in ("locked_by", "lease_expires_at", "leased_until"))
                or job["lease_expires_at"] is not None or job["checkpoint"] != "PARSING"
                or job["cancellation_requested_at"] is not None
                or physical.payload != delivery.payload or payload["delivery_generation"] != generation
                or any(result[key] != payload[key] for key in ("request_id", "intake_id", "requested_by"))
                or result["job_id"] != job["job_id"] or result["correlation_id"] != job["correlation_id"]
                or result["processing_state"] != "PARSING" or type(result["version"]) is not int
                or result["version"] != payload["expected_intake_version"] + 1
                or result["human_role"] not in {"EXPANSION_STAFF", "EXPANSION_MANAGER", "DATA_STEWARD"}
                or any(type(result[key]) is not int or result[key] < 1 for key in (
                    "accepted_delivery_fence", "accepted_business_fence", "accepted_attempt"))
                or result["acceptance_audit_event_id"] != acceptance["audit_event_id"]
                or any(result[r] != acceptance[a] for r, a in (("accepted_delivery_fence", "physical_fence"),
                    ("accepted_business_fence", "business_fence"), ("accepted_attempt", "attempt"), ("accepted_worker", "worker")))
                or physical.fence_token != acceptance["physical_fence"]
                or physical.max_retries != job["max_attempts"] or not job["attempt"] <= physical.attempts <= physical.max_retries):
            _reject()
        before()
        transition = self.engine.query_one("SELECT * FROM intake.intake_stage_transitions WHERE tenant_id=? "
            "AND transition_id=? AND intake_id=? AND job_id=? AND from_state='AWAITING_ASSISTED_ENTRY' AND to_state='PARSING' "
            "AND actor_subject_id=? AND service_principal IS NULL AND permission='intake.assisted_entry' "
            "AND reason_code='ASSISTED_ENTRY_HUMAN_COMMAND_VERIFIED' AND expected_version=? AND resulting_version=? "
            "AND idempotency_key=? AND causation_id=? AND correlation_id=? AND source_snapshot_id=?",
            (tenant, result["transition_id"], payload["intake_id"], job["job_id"], payload["requested_by"],
                payload["expected_intake_version"], result["version"], payload["request_id"], payload["request_id"],
                job["correlation_id"], result["source_snapshot_id"]))
        before()
        audit = self.engine.query_one("SELECT * FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=? "
            "AND resource_type='intake' AND resource_id=? AND actor_subject_id=? AND service_principal IS NULL "
            "AND actor_role=? AND action='intake.assisted_entry_completed.v1' AND result='SUCCEEDED' "
            "AND causation_id=? AND correlation_id=?", (tenant, result["audit_event_id"], payload["intake_id"],
                payload["requested_by"], result["human_role"], payload["request_id"], job["correlation_id"]))
        if transition is None or audit is None:
            _reject()
        at = _time(audit["occurred_at"])
        authority = _object(_object(audit["after_value"]).get("authority"))
        if (set(authority) != {"subject_id", "role", "checked_at", "scope_sha256"}
                or authority["subject_id"] != payload["requested_by"] or authority["role"] != result["human_role"]
                or _time(authority["checked_at"]) != at or not isinstance(authority["scope_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", authority["scope_sha256"])
                or _time(transition["occurred_at"]) != at or _time(job["updated_at"]) != at
                or not _time(acceptance["accepted_at"]) <= at < _time(payload["timeout_at"])
                or transition["source_policy_version"] != payload["accepted_source_version"]
                or _object(audit["before_value"]) != {
                    "processing_state": "AWAITING_ASSISTED_ENTRY", "version": payload["expected_intake_version"]}
                or not audited_value_matches(_object(audit["after_value"]),
                    {**result, "native_assisted_provenance": assisted_job_provenance(payload), "authority": authority,
                        "executor": {"service_principal": JOB_TYPE, "worker": acceptance["worker"],
                            "physical_fence": acceptance["physical_fence"], "business_fence": acceptance["business_fence"]}})):
            _reject()
        snapshot = self._snapshot(job, payload, result, at, before)
        command = self._corrections(tenant, payload, result, snapshot, before)
        before()
        request_audit = self.engine.query_one("SELECT reason FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=?",
            (tenant, request_audit_id(payload["request_id"])))
        if (request_audit is None or request_audit["reason"] != command.reason
                or audit["reason"] != command.reason or transition["reason_text"] != command.reason):
            _reject()
        changed = {"intake_id": payload["intake_id"], "from_state": "AWAITING_ASSISTED_ENTRY", "to_state": "PARSING",
            "transition_id": result["transition_id"], "version": result["version"], "occurred_at": at.isoformat()}
        self._event(job, result["outbox_event_id"], at, before, event_type="intake.state_changed",
            producer="listing_intake_service", aggregate_type="intake", aggregate_id=payload["intake_id"],
            version=result["version"], actor=payload["requested_by"], schema="IntakeStateChangedV1", data=changed,
            sensitive=[], retention_days=14)
        self._event(job, result["snapshot_created_event_id"], at, before, event_type="snapshot.manual_entry_created",
            producer="assisted_entry_service", aggregate_type="source_snapshot", aggregate_id=result["source_snapshot_id"],
            version=1, actor=JOB_TYPE, schema="SnapshotCreatedV1", data=snapshot_payload(snapshot),
            sensitive=["payload.object_uri"], retention_days=14)
        before()
        return result

    def _snapshot(self, job, payload, result, at, before):
        proof = _object(result["native_snapshot_proof"])
        if set(proof) != {"snapshot_fields", "bucket_metadata", "upload_intent_id", "correction_bindings"}:
            _reject()
        snapshot = _object(proof["snapshot_fields"])
        binding = NativeSnapshotBinding(**payload["policy"]["storage_binding"])
        if (set(snapshot) != set(SNAPSHOT_FIELDS) or snapshot["tenant_id"] != job["tenant_id"]
                or snapshot["intake_id"] != payload["intake_id"] or snapshot["source_snapshot_id"] != result["source_snapshot_id"]
                or snapshot["capture_method"] != "MANUAL_ENTRY" or snapshot["media_type"] != "application/json"
                or snapshot["observed_at"] != snapshot["captured_at"]
                or type(snapshot["version"]) is not int or snapshot["version"] != 1
                or type(snapshot["object_generation"]) is not int or not 1 <= snapshot["object_generation"] <= 2**63 - 1
                or type(snapshot["byte_length"]) is not int or not 1 <= snapshot["byte_length"] <= 512_000
                or snapshot["residency_mode"] != binding.residency_mode or snapshot["retention_class"] != binding.retention_class
                or not binding.accepts_actual_key_version(snapshot["encryption_key_ref"])
                or proof["bucket_metadata"] != {"bucket_name": binding.bucket, "location": "ASIA-EAST1", "location_type": "region"}):
            _reject()
        before()
        actual = NativeIntakeSnapshotMetadataRepository(self.graph).required_for_intake(
            job["tenant_id"], result["source_snapshot_id"], payload["intake_id"])
        if any(actual[key] != value for key, value in snapshot.items()):
            _reject()
        intents = [i for i in payload["attempt_intents"] if i.get("intent_id") == proof["upload_intent_id"]]
        if len(intents) != 1:
            _reject()
        intent, acceptance = intents[0], payload["acceptance_receipt"]
        args = intent["arguments"]
        key = native_assisted_snapshot_object_key(job["tenant_id"], job["job_id"], payload["delivery_generation"],
            job["fence_token"], args["capture_id"])
        if (intent["operation"] != "gcs_upload" or intent["state"] not in {"FINISHED", "ERROR"}
                or intent["delivery_generation"] != payload["delivery_generation"] or intent["business_fence"] != job["fence_token"]
                or any(intent[i] != acceptance[a] for i, a in (("physical_fence", "physical_fence"),
                    ("worker", "worker"), ("attempt", "attempt")))
                or any(intent[k] != payload[k] for k in ("request_id", "input_sha256", "fields_sha256", "command_sha256"))
                or intent["source_snapshot_sha256"] != payload["accepted_source_sha256"]
                or intent["source_version"] != payload["accepted_source_version"]
                or intent["document_sha256"] != payload["accepted_document_sha256"]
                or args != {"key": key, "content_sha256": snapshot["content_sha256"], "byte_length": snapshot["byte_length"],
                    "media_type": "application/json", "capture_id": args["capture_id"]}
                or snapshot["content_sha256"] != payload["accepted_document_sha256"]
                or snapshot["raw_object_uri"] != "gs://" + binding.bucket + "/" + key
                or _time(snapshot["captured_at"]) != _time(intent["admitted_at"])
                or not _time(acceptance["accepted_at"]) <= _time(intent["admitted_at"]) <= _time(intent["finished_at"]) <= at
                or _time(intent["source_valid_until"]) <= at):
            _reject()
        return snapshot

    def _corrections(self, tenant, payload, result, snapshot, before):
        bindings = _object(result["native_snapshot_proof"]["correction_bindings"])
        if set(bindings) != set(payload["correction_ids"]):
            _reject()
        before()
        rows = self.engine.query("SELECT *,corrected_value::text AS corrected_json FROM intake.human_corrections WHERE tenant_id=? AND intake_id=? "
            "AND correction_id=ANY(?::uuid[]) ORDER BY correction_id", (tenant, payload["intake_id"], payload["correction_ids"]))
        if len(rows) != len(bindings) or len({r["field_path"] for r in rows}) != len(rows):
            _reject()
        fields, ids, reasons = {}, {}, set()
        for row in rows:
            binding = bindings[row["correction_id"]]
            try:
                value = json.loads(row["corrected_json"])
            except (ValueError, TypeError):
                _reject()
            if (binding != {"field_path": row["field_path"], "corrected_value_sha256": canonical_sha256(value),
                    "reason_sha256": hashlib.sha256(row["reason"].encode()).hexdigest(),
                    "created_at": _time(row["created_at"]).isoformat(), "proposed_by": row["proposed_by"]}
                    or row["proposed_by"] != payload["requested_by"] or row["field_classification"] != "INTERNAL"
                    or row["source_snapshot_id"] != snapshot["source_snapshot_id"]
                    or row["identity_affecting"] is not (row["field_path"] in IDENTITY_FIELDS)
                    or row["status"] not in {"PROPOSED", "APPLIED", "REJECTED", "SUPERSEDED", "REVERSED"}
                    or type(row["version"]) is not int or row["version"] < 2
                    or _time(row["created_at"]) != _time(payload["requested_at"])):
                _reject()
            fields[row["field_path"]], ids[row["field_path"]] = value, row["correction_id"]
            reasons.add(row["reason"])
        if len(reasons) != 1:
            _reject()
        parts = next(iter(reasons)).split("\n")
        if (len(parts) != 4 or parts[0] != "Native assisted-entry proposal v1" or not parts[1].startswith("Reason: ")
                or parts[2] != "Risk acknowledged: yes" or not parts[3].startswith("Risk summary: ")):
            _reject()
        command = NativeAssistedEntryCommand.model_validate({"fields": fields, "reason": parts[1][8:],
            "risk_summary": parts[3][14:], "risk_acknowledged": True})
        body = manual_evidence_document(tenant_id=tenant, intake_id=payload["intake_id"], request_id=payload["request_id"],
            requested_by=payload["requested_by"], source_id=snapshot["source_id"], fields=command.fields, correction_ids=ids)
        if (command.sha256 != payload["command_sha256"] or command.fields.sha256 != payload["fields_sha256"]
                or command.correction_reason() != next(iter(reasons)) or len(body) != snapshot["byte_length"]
                or hashlib.sha256(body).hexdigest() != snapshot["content_sha256"]):
            _reject()
        return command

    def _event(self, job, event_id, at, before, *, event_type, producer, aggregate_type, aggregate_id, version,
               actor, schema, data, sensitive, retention_days):
        before()
        row = self.graph._one("SELECT * FROM workflow.outbox_events WHERE tenant_id=? AND event_id=?", (job["tenant_id"], event_id))
        event = _object(row["payload"])
        expected = {"event_id": event_id, "event_type": event_type, "event_version": 1, "producer": producer,
            "tenant_id": job["tenant_id"], "aggregate_type": aggregate_type, "aggregate_id": aggregate_id,
            "aggregate_version": version, "partition_key": job["tenant_id"] + ":" + _object(job["payload"])["intake_id"],
            "correlation_id": job["correlation_id"], "causation_id": _object(job["payload"])["request_id"],
            "actor_ref": actor, "schema_ref": "#/payloads/" + schema, "sensitive_fields": sensitive, "payload": data}
        if (validate_event(event) or any(event.get(k) != v for k, v in expected.items())
                or any(row[k] != expected[k] for k in ("event_id", "event_type", "event_version", "tenant_id",
                    "aggregate_type", "aggregate_id", "aggregate_version", "partition_key", "correlation_id"))
                or type(event.get("aggregate_version")) is not int or type(event.get("event_version")) is not int
                or _time(event.get("occurred_at")) != at or _time(row["occurred_at"]) != at
                or _time(row["retention_until"]) != at + timedelta(days=retention_days)):
            _reject()
