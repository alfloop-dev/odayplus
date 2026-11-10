"""Commit truthful manual evidence, human transition and both jobs atomically."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from uuid import uuid4

from modules.external_data.application.native_assisted_snapshot_transport import (
    NativeAssistedSnapshotObjectMetadata,
    NativeVerifiedAssistedSnapshot,
    native_assisted_snapshot_object_key,
)
from modules.external_data.application.native_snapshot_transport import NativeBucketMetadata
from modules.listing.application.native_intake_assisted_entry_job_proof import (
    assisted_job_provenance,
)
from modules.listing.application.native_intake_assisted_entry_policy import (
    JOB_TYPE,
    native_intake_assisted_entry_policy,
)
from modules.listing.application.native_intake_assisted_entry_request import human_transition_role
from modules.listing.application.native_intake_assisted_entry_request_proof import _time
from modules.listing.application.native_intake_retrieval_policy import (
    canonical_json,
    canonical_sha256,
)
from shared.domain.events import DomainEvent
from shared.infrastructure.persistence.assisted_listing_intake import (
    NativeIntakeSnapshotMetadataRepository,
)
from shared.jobs.queue import JobFenceRejectedError, JobStatus

SNAPSHOT_FIELDS = (
    "source_snapshot_id", "tenant_id", "intake_id", "source_id", "original_url", "canonical_url",
    "raw_object_uri", "content_sha256", "media_type", "byte_length", "captured_at", "observed_at",
    "capture_method", "retention_class", "encryption_key_ref", "object_generation", "residency_mode", "version",
)


def snapshot_payload(snapshot):
    return {key: snapshot[key] for key in ("source_snapshot_id", "intake_id", "source_id", "content_sha256",
        "captured_at", "retention_class", "residency_mode", "object_generation")}


def _manual_snapshot(owner, state, verified, upload, bucket):
    job, payload, before, intake = state.job, state.payload, state.before, state.intake
    if not isinstance(verified, NativeVerifiedAssistedSnapshot) or not isinstance(bucket, NativeBucketMetadata):
        raise JobFenceRejectedError("NATIVE_ASSISTED_ACTUAL_PROVIDER_PROOF_REQUIRED")
    matches = [item for item in payload["attempt_intents"] if item.get("intent_id") == upload.get("intent_id")]
    if len(matches) != 1:
        raise JobFenceRejectedError("NATIVE_ASSISTED_UPLOAD_JOURNAL_MISSING")
    intent = matches[0]
    arguments, metadata = intent["arguments"], verified.metadata
    binding = native_intake_assisted_entry_policy(intake["tenant_id"]).storage
    body = owner._document(state.request, intake)
    expected_key = native_assisted_snapshot_object_key(intake["tenant_id"], job["job_id"],
        payload["delivery_generation"], job["fence_token"], arguments["capture_id"])
    if (intent["operation"] != "gcs_upload" or intent["state"] not in {"FINISHED", "ERROR"}
            or intent["delivery_generation"] != payload["delivery_generation"]
            or intent["business_fence"] != job["fence_token"] or intent["attempt"] != job["attempt"]
            or intent["physical_fence"] != state.physical.fence_token or intent["worker"] != state.physical.locked_by
            or intent["request_id"] != payload["request_id"] or intent["input_sha256"] != payload["input_sha256"]
            or intent["fields_sha256"] != payload["fields_sha256"] or intent["command_sha256"] != payload["command_sha256"]
            or intent["document_sha256"] != hashlib.sha256(body).hexdigest()
            or intent["source_snapshot_sha256"] != state.classified.source_snapshot_sha256
            or intent["source_version"] != state.source["version"] or arguments["key"] != expected_key
            or verified.content_sha256 != intent["document_sha256"]
            or arguments["content_sha256"] != verified.content_sha256 or arguments["byte_length"] != len(body)
            or arguments["media_type"] != "application/json"
            or not isinstance(metadata, NativeAssistedSnapshotObjectMetadata)
            or metadata.uri != "gs://" + binding.bucket + "/" + expected_key
            or type(metadata.generation) is not int or not 1 <= metadata.generation <= 2**63 - 1
            or metadata.byte_length != len(body) or metadata.media_type != "application/json"
            or not binding.accepts_actual_key_version(metadata.kms_key_version_name)
            or bucket.bucket_name != binding.bucket or bucket.location != "ASIA-EAST1" or bucket.location_type != "region"):
        raise JobFenceRejectedError("NATIVE_ASSISTED_UPLOAD_DOCUMENT_OR_PROVIDER_BINDING_REJECTED")
    snapshot = {"source_snapshot_id": str(uuid4()), "tenant_id": intake["tenant_id"], "intake_id": intake["intake_id"],
        "source_id": intake["source_id"], "original_url": intake["original_url"], "canonical_url": intake["canonical_url"],
        "raw_object_uri": metadata.uri, "content_sha256": verified.content_sha256, "media_type": "application/json",
        # This is the owner's actual observation of the manual document;
        # it makes no claim about when an external source observed a listing.
        "byte_length": len(body), "captured_at": intent["admitted_at"], "observed_at": intent["admitted_at"],
        "capture_method": "MANUAL_ENTRY", "retention_class": binding.retention_class,
        "encryption_key_ref": metadata.kms_key_version_name, "object_generation": metadata.generation,
        "residency_mode": binding.residency_mode, "version": 1}
    snapshots = NativeIntakeSnapshotMetadataRepository(owner.graph)
    before()
    if snapshots.find_content(intake["tenant_id"], intake["intake_id"], intake["source_id"], verified.content_sha256) is not None:
        # A complete prior commit is handled by the full terminal reader before
        # retry/negative work. An isolated row is never an adoption capability.
        raise JobFenceRejectedError("NATIVE_ASSISTED_EXISTING_SNAPSHOT_OUTCOME_UNPROVEN")
    before()
    snapshots.insert(tuple(snapshot[key] for key in SNAPSHOT_FIELDS))
    return snapshot, intent


def _link_original_proposals(owner, state, snapshot_id):
    data, result = state.request.event["payload"], {}
    values = state.request.command.fields.values()
    for row in state.request.rows:
        state.before()
        if owner.engine.execute("UPDATE intake.human_corrections SET source_snapshot_id=?,version=version+1 "
                "WHERE tenant_id=? AND intake_id=? AND correction_id=? AND version=? AND status='PROPOSED' "
                "AND source_snapshot_id IS NULL AND reviewed_by IS NULL",
                (snapshot_id, state.intake["tenant_id"],
                    data["intake_id"], row["correction_id"], row["version"])).rowcount != 1:
            raise JobFenceRejectedError("NATIVE_ASSISTED_PROPOSAL_LINK_CAS_REJECTED")
        result[row["correction_id"]] = {"field_path": row["field_path"],
            "corrected_value_sha256": canonical_sha256(values[row["field_path"]]),
            "reason_sha256": hashlib.sha256(row["reason"].encode()).hexdigest(),
            "created_at": _time(row["created_at"]).isoformat(), "proposed_by": row["proposed_by"]}
    return result


def commit_assisted_result(owner, delivery, leases, *, verified, upload, bucket):
    tenant, generation = owner.delivery_identity(delivery)
    with leases.root():
        state = owner._fresh(delivery, tenant, generation, business_fence=leases.business_fence)
        physical, job, payload, intake, before = state.physical, state.job, state.payload, state.intake, state.before
        if job["status"] != "RUNNING":
            raise JobFenceRejectedError("NATIVE_ASSISTED_FINAL_ACCEPTANCE_REQUIRED")
        snapshot, intent = _manual_snapshot(owner, state, verified, upload, bucket)
        correction_binding = _link_original_proposals(owner, state, snapshot["source_snapshot_id"])
        at, transition_id, audit_id = owner._clock(), str(uuid4()), str(uuid4())
        aggregate = owner._preview(intake, state.request, state.principal, at)
        validity = state.classified.valid_until.isoformat()
        before()
        if owner.engine.execute("UPDATE intake.intakes SET processing_state='PARSING',version=?,last_transition_at=? "
                "WHERE tenant_id=? AND intake_id=? AND version=? AND processing_state='AWAITING_ASSISTED_ENTRY' "
                "AND source_policy_state=? AND deleted_at IS NULL AND completed_at IS NULL AND resolved_listing_id IS NULL "
                "AND ?::timestamptz>clock_timestamp()",
                (aggregate.version, at.isoformat(), tenant, intake["intake_id"], intake["version"],
                    intake["source_policy_state"], validity)).rowcount != 1:
            raise JobFenceRejectedError("NATIVE_ASSISTED_INTAKE_FINAL_CAS_REJECTED")
        before()
        sequence = owner.engine.query_one("SELECT COALESCE(MAX(sequence_no),0)+1 AS next FROM intake.intake_stage_transitions "
            "WHERE tenant_id=? AND intake_id=?", (tenant, intake["intake_id"]))["next"]
        before()
        owner.engine.execute("INSERT INTO intake.intake_stage_transitions (transition_id,tenant_id,intake_id,sequence_no,"
            "from_state,to_state,actor_subject_id,service_principal,permission,reason_code,reason_text,idempotency_key,"
            "expected_version,resulting_version,job_id,source_policy_version,source_snapshot_id,correlation_id,causation_id,occurred_at) "
            "VALUES (?,?,?,?,'AWAITING_ASSISTED_ENTRY','PARSING',?,NULL,'intake.assisted_entry',"
            "'ASSISTED_ENTRY_HUMAN_COMMAND_VERIFIED',?,?,?,?,?,?,?,?,?,?)",
            (transition_id, tenant, intake["intake_id"], sequence, state.principal.subject_id, state.request.command.reason,
                payload["request_id"], intake["version"], aggregate.version, job["job_id"], state.source["version"],
                snapshot["source_snapshot_id"], intake["correlation_id"], payload["request_id"], at.isoformat()))
        changed = DomainEvent(event_type="intake.state_changed", producer="listing_intake_service", tenant_id=tenant,
            aggregate_type="intake", aggregate_id=intake["intake_id"], aggregate_version=aggregate.version,
            partition_key=tenant + ":" + intake["intake_id"], correlation_id=intake["correlation_id"],
            causation_id=payload["request_id"], actor_ref=state.principal.subject_id, occurred_at=at,
            schema_ref="#/payloads/IntakeStateChangedV1", payload={"intake_id": intake["intake_id"],
                "from_state": "AWAITING_ASSISTED_ENTRY", "to_state": "PARSING", "transition_id": transition_id,
                "version": aggregate.version, "occurred_at": at.isoformat()})
        captured = DomainEvent(event_type="snapshot.manual_entry_created", producer="assisted_entry_service", tenant_id=tenant,
            aggregate_type="source_snapshot", aggregate_id=snapshot["source_snapshot_id"], aggregate_version=1,
            partition_key=tenant + ":" + intake["intake_id"], correlation_id=intake["correlation_id"],
            causation_id=payload["request_id"], actor_ref=JOB_TYPE, occurred_at=at,
            schema_ref="#/payloads/SnapshotCreatedV1", sensitive_fields=["payload.object_uri"], payload=snapshot_payload(snapshot))
        result = {"job_id": job["job_id"], "request_id": payload["request_id"], "intake_id": intake["intake_id"],
            "processing_state": "PARSING", "version": aggregate.version, "transition_id": transition_id,
            "audit_event_id": audit_id, "outbox_event_id": changed.event_id, "correlation_id": intake["correlation_id"],
            "source_snapshot_id": snapshot["source_snapshot_id"], "snapshot_created_event_id": captured.event_id,
            "requested_by": payload["requested_by"], "human_role": human_transition_role(state.principal).value,
            "accepted_delivery_fence": physical.fence_token, "accepted_business_fence": job["fence_token"],
            "accepted_attempt": job["attempt"], "accepted_worker": physical.locked_by,
            "acceptance_audit_event_id": payload["acceptance_receipt"]["audit_event_id"],
            "native_snapshot_proof": {"snapshot_fields": snapshot, "bucket_metadata": asdict(bucket),
                "upload_intent_id": intent["intent_id"], "correction_bindings": correction_binding}}
        authority = owner._authority_snapshot(state.principal, at)
        before()
        owner.graph.audit_sink.persist({"audit_event_id": audit_id, "tenant_id": tenant,
            "resource_type": "intake", "resource_id": intake["intake_id"], "action": "intake.assisted_entry_completed.v1",
            "result": "SUCCEEDED", "actor_subject_id": state.principal.subject_id, "service_principal": None,
            "actor_role": result["human_role"], "correlation_id": intake["correlation_id"], "causation_id": payload["request_id"],
            "occurred_at": at.isoformat(), "before_value": {"processing_state": "AWAITING_ASSISTED_ENTRY", "version": intake["version"]},
            "after_value": {**result, "native_assisted_provenance": assisted_job_provenance(payload), "authority": authority,
                "executor": {"service_principal": JOB_TYPE, "worker": physical.locked_by,
                    "physical_fence": physical.fence_token, "business_fence": job["fence_token"]}},
            "reason": state.request.command.reason},
            {key: intake[key] for key in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")})
        before()
        owner.graph._outbox(changed)
        before()
        owner.graph._outbox(captured)
        before()
        if (native_intake_assisted_entry_policy(tenant).sha256 != payload["policy_sha256"]
                or owner.auth.decode(owner.queue.get(job["job_id"])) != (tenant, generation)
                or not owner.engine.query_one("SELECT job_id FROM durable_jobs WHERE job_id=? AND status=? "
                    "AND fence_token=? AND locked_by=? AND lease_expires_at>clock_timestamp()",
                    (job["job_id"], JobStatus.RUNNING.value, physical.fence_token, physical.locked_by))):
            raise JobFenceRejectedError("NATIVE_ASSISTED_FINAL_PHYSICAL_OR_CONFIG_REJECTED")
        before()
        if owner.engine.execute("UPDATE workflow.jobs SET status='SUCCEEDED',checkpoint='PARSING',result=?::jsonb,"
                "version=version+1,heartbeat_at=?,lease_expires_at=NULL,updated_at=? "
                "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND attempt=? AND status='RUNNING' "
                "AND result IS NULL AND cancellation_requested_at IS NULL AND lease_expires_at>clock_timestamp() "
                "AND timeout_at>clock_timestamp() AND ?::timestamptz>clock_timestamp()",
                (canonical_json(result), at.isoformat(), at.isoformat(), tenant, job["job_id"], job["version"],
                    job["fence_token"], job["attempt"], validity)).rowcount != 1:
            raise JobFenceRejectedError("NATIVE_ASSISTED_FINAL_BUSINESS_CAS_REJECTED")
        before()
        owner.queue.update_status(job["job_id"], JobStatus.SUCCEEDED,
            expected_version=physical.version, fence_token=physical.fence_token)
        before()
        return result
