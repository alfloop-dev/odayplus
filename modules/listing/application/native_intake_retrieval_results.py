"""Commit native snapshot/state/audit/outbox/jobs together, then prove replay."""

import hashlib
from dataclasses import asdict
from datetime import timedelta
from uuid import uuid4

from modules.listing.application.native_intake_retrieval import _object, audited_value_matches
from modules.listing.application.native_intake_retrieval_policy import (
    JOB_TYPE,
    NativeSnapshotBinding,
    canonical_json,
    native_intake_retrieval_policy,
)
from modules.listing.domain.intake_states import (
    Actor,
    IntakeAggregate,
    IntakeStage,
    IntakeStateMachine,
    PrincipalRole,
    TransitionContext,
)
from shared.domain.events import DomainEvent, validate_event
from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError, JobStatus

_BRANCHES = {
    "PARSING": ("snapshot.create", "intake.parsing_started.v1"),
    "FAILED": ("job.fail", "intake.processing_failed.v1"),
    "QUARANTINED": ("intake.quarantine", "intake.quarantined.v1"),
}
_SNAPSHOT_FIELDS = (
    "source_snapshot_id", "tenant_id", "intake_id", "source_id", "original_url", "canonical_url",
    "raw_object_uri", "content_sha256", "media_type", "byte_length", "captured_at", "observed_at",
    "capture_method", "retention_class", "encryption_key_ref", "object_generation", "residency_mode", "version",
)


def dead_letter(owner, job, payload, code, at, *, checkpoint, version, attempt=None):
    owner.graph._outbox(DomainEvent(event_type="job.dead_lettered", producer="job_platform",
        tenant_id=job["tenant_id"], aggregate_type="job", aggregate_id=job["job_id"], aggregate_version=version,
        partition_key=job["tenant_id"] + ":" + job["job_id"], correlation_id=job["correlation_id"],
        causation_id=payload["event_id"], actor_ref=JOB_TYPE, occurred_at=at, schema_ref="#/payloads/JobDeadLetteredV1",
        sensitive_fields=["payload.last_error_details"], payload={"job_id": job["job_id"], "job_type": JOB_TYPE,
            "checkpoint": checkpoint, "attempt": job["attempt"] if attempt is None else attempt, "error_code": code,
            "dead_lettered_at": at.isoformat(), "last_error_details": {"error_class": "NativeRetrievalFailure"}}))


def _snapshot_payload(snapshot):
    return {"source_snapshot_id": snapshot["source_snapshot_id"], "intake_id": snapshot["intake_id"],
        "source_id": snapshot["source_id"], "content_sha256": snapshot["content_sha256"],
        "captured_at": snapshot["captured_at"], "retention_class": snapshot["retention_class"],
        "residency_mode": snapshot["residency_mode"], "object_generation": snapshot["object_generation"]}


def _read_event(owner, tenant, event_id, *, event_type, aggregate_type, aggregate_id, aggregate_version,
                partition, correlation, causation, actor, before):
    before()
    row = owner.graph._one("SELECT * FROM workflow.outbox_events WHERE tenant_id=? AND event_id=?", (tenant, event_id))
    event = _object(row["payload"])
    if (validate_event(event) or row["event_id"] != event_id or event.get("event_id") != event_id
            or row["event_type"] != event_type or event.get("event_type") != event_type
            or row["aggregate_type"] != aggregate_type or event.get("aggregate_type") != aggregate_type
            or row["aggregate_id"] != aggregate_id or event.get("aggregate_id") != aggregate_id
            or row["aggregate_version"] != aggregate_version or event.get("aggregate_version") != aggregate_version
            or type(event.get("aggregate_version")) is not int
            or row["event_version"] != 1 or type(event.get("event_version")) is not int or event["event_version"] != 1
            or event.get("tenant_id") != tenant or row["partition_key"] != partition or event.get("partition_key") != partition
            or row["correlation_id"] != correlation or event.get("correlation_id") != correlation
            or event.get("causation_id") != causation or event.get("actor_ref") != actor):
        raise JobFenceRejectedError("NATIVE_RETRIEVAL_TERMINAL_EVENT_REJECTED")
    return event


def _snapshot(owner, intake, job, payload, verified, upload, bucket, before):
    matches = [i for i in payload["attempt_intents"] if i["intent_id"] == upload["intent_id"]]
    if len(matches) != 1:
        raise JobFenceRejectedError("NATIVE_RETRIEVAL_UPLOAD_JOURNAL_MISSING")
    intent = matches[0]
    arguments, metadata = intent["arguments"], verified.metadata
    binding = payload["policy"]["storage_binding"]
    if (intent["operation"] != "gcs_upload" or intent["state"] not in {"FINISHED", "ERROR"}
            or intent["delivery_generation"] != payload["delivery_generation"]
            or intent["business_fence"] != job["fence_token"] or intent["attempt"] != job["attempt"]
            or intent["physical_fence"] != payload["accepted_delivery_fence"]
            or intent["worker"] != payload["accepted_worker"] or intent["input_sha256"] != payload["input_sha256"]
            or intent["source_snapshot_sha256"] != payload["accepted_source_sha256"]
            or verified.content_sha256 != arguments["content_sha256"] or metadata.byte_length != arguments["byte_length"]
            or metadata.media_type != arguments["media_type"]
            or metadata.uri != "gs://" + binding["bucket"] + "/" + arguments["key"]
            or bucket.bucket_name != binding["bucket"] or bucket.location != "ASIA-EAST1" or bucket.location_type != "region"):
        raise JobFenceRejectedError("NATIVE_RETRIEVAL_UPLOAD_JOURNAL_REJECTED")
    snapshot = {"source_snapshot_id": str(uuid4()), "tenant_id": intake["tenant_id"], "intake_id": intake["intake_id"],
        "source_id": intake["source_id"], "original_url": intake["original_url"], "canonical_url": intake["canonical_url"],
        "raw_object_uri": metadata.uri, "content_sha256": verified.content_sha256, "media_type": metadata.media_type,
        "byte_length": metadata.byte_length, "captured_at": intent["admitted_at"], "observed_at": intent["admitted_at"],
        "capture_method": "SERVER_RETRIEVAL", "retention_class": binding["retention_class"],
        "encryption_key_ref": metadata.kms_key_version_name, "object_generation": metadata.generation,
        "residency_mode": binding["residency_mode"], "version": 1}
    before()
    existing = owner.engine.query_one("SELECT * FROM intake.source_snapshots WHERE tenant_id=? AND intake_id=? "
        "AND source_id=? AND content_sha256=? FOR UPDATE",
        (intake["tenant_id"], intake["intake_id"], intake["source_id"], verified.content_sha256))
    if existing is not None:
        # The INSTALLED key includes intake_id. Equal bytes from another
        # Intake cannot select/adopt its snapshot or storage object here.
        snapshot["source_snapshot_id"] = existing["source_snapshot_id"]
        if any(existing[k] != snapshot[k] for k in _SNAPSHOT_FIELDS):
            raise JobFenceRejectedError("NATIVE_RETRIEVAL_OWN_SNAPSHOT_COLLISION_UNPROVEN")
        before()
        prior = owner.graph._one("SELECT event_id FROM workflow.outbox_events WHERE tenant_id=? AND event_type='snapshot.created' "
            "AND aggregate_type='source_snapshot' AND aggregate_id=? AND aggregate_version=?",
            (intake["tenant_id"], existing["source_snapshot_id"], existing["version"]))
        event = _read_event(owner, intake["tenant_id"], prior["event_id"], event_type="snapshot.created",
            aggregate_type="source_snapshot", aggregate_id=existing["source_snapshot_id"], aggregate_version=existing["version"],
            partition=intake["tenant_id"] + ":" + intake["intake_id"], correlation=job["correlation_id"],
            causation=payload["event_id"], actor=JOB_TYPE, before=before)
        if event["payload"] != _snapshot_payload(existing) or event["producer"] != "retrieval_service":
            raise JobFenceRejectedError("NATIVE_RETRIEVAL_OWN_SNAPSHOT_EVENT_UNPROVEN")
        return snapshot, prior["event_id"]
    before()
    owner.engine.execute("INSERT INTO intake.source_snapshots (source_snapshot_id,tenant_id,intake_id,source_id,"
        "original_url,canonical_url,raw_object_uri,content_sha256,media_type,byte_length,captured_at,observed_at,"
        "capture_method,retention_class,encryption_key_ref,object_generation,residency_mode,version) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(snapshot[k] for k in _SNAPSHOT_FIELDS))
    event = DomainEvent(event_type="snapshot.created", producer="retrieval_service", tenant_id=intake["tenant_id"],
        aggregate_type="source_snapshot", aggregate_id=snapshot["source_snapshot_id"], aggregate_version=snapshot["version"],
        partition_key=intake["tenant_id"] + ":" + intake["intake_id"], correlation_id=job["correlation_id"],
        causation_id=payload["event_id"], actor_ref=JOB_TYPE, schema_ref="#/payloads/SnapshotCreatedV1",
        sensitive_fields=["payload.object_uri"], payload=_snapshot_payload(snapshot))
    # Catalog requires the sensitive-field declaration although its closed
    # payload excludes object_uri. Do not widen either committed contract.
    before()
    owner.graph._outbox(event)
    return snapshot, event.event_id


def commit_result(owner, delivery, leases, *, target, code, verified=None, upload=None, bucket=None):
    if target not in _BRANCHES:
        raise JobFenceRejectedError("NATIVE_RETRIEVAL_EXIT_REJECTED")
    tenant, generation = owner.auth.decode(delivery)
    with leases.root():
        physical, job, payload, before, intake, source, classified = owner._fresh(delivery, tenant, generation,
            settlement=target != "PARSING", business_fence=leases.business_fence)
        snapshot, snapshot_event_id = None, None
        if target == "PARSING":
            owner._positive_source(payload, classified)
            snapshot, snapshot_event_id = _snapshot(owner, intake, job, payload, verified, upload, bucket, before)
        elif classified.target_state != "RETRIEVING" or classified.effective_policy != "APPROVED_RETRIEVAL":
            target, code = "QUARANTINED", "NATIVE_RETRIEVAL_CURRENT_SOURCE_DENIED"
        permission, action = _BRANCHES[target]
        before()
        at, transition_id, audit_id = owner._clock(), str(uuid4()), str(uuid4())
        aggregate = IntakeAggregate(id=intake["intake_id"], tenant_id=tenant, stage=IntakeStage.RETRIEVING,
            version=intake["version"], created_by=intake["submitter_subject_id"], source_id=intake["source_id"],
            canonical_url=intake["canonical_url"], owner_id=intake["submitter_subject_id"])
        IntakeStateMachine.transition(aggregate, IntakeStage(target), TransitionContext(
            actor=Actor(JOB_TYPE, PrincipalRole.SVC_RETRIEVAL, tenant), version_before=intake["version"],
            idempotency_key=payload["event_id"], correlation_id=intake["correlation_id"],
            causation_id=payload["event_id"], current_time=at, reason=code))
        source_policy = classified.effective_policy if classified.target_state != "RETRIEVING" else intake["source_policy_state"]
        validity = classified.valid_until.isoformat() if target == "PARSING" else None
        before()
        if owner.engine.execute("UPDATE intake.intakes SET processing_state=?,version=?,last_transition_at=?,"
            "source_policy_state=? WHERE tenant_id=? AND intake_id=? AND version=? AND processing_state='RETRIEVING' "
            "AND deleted_at IS NULL AND source_policy_state='APPROVED_RETRIEVAL' "
            "AND (?::timestamptz IS NULL OR ?::timestamptz>clock_timestamp())",
            (target, aggregate.version, at.isoformat(), source_policy,
             tenant, intake["intake_id"], intake["version"], validity, validity)).rowcount != 1:
            raise JobFenceRejectedError("NATIVE_RETRIEVAL_INTAKE_TERMINAL_CAS_REJECTED")
        before()
        sequence = owner.engine.query_one("SELECT COALESCE(MAX(sequence_no),0)+1 AS next FROM intake.intake_stage_transitions "
            "WHERE tenant_id=? AND intake_id=?", (tenant, intake["intake_id"]))["next"]
        before()
        owner.engine.execute("INSERT INTO intake.intake_stage_transitions (transition_id,tenant_id,intake_id,sequence_no,"
            "from_state,to_state,actor_subject_id,service_principal,permission,reason_code,reason_text,idempotency_key,"
            "expected_version,resulting_version,job_id,source_policy_version,source_snapshot_id,correlation_id,causation_id,occurred_at) "
            "VALUES (?,?,?,?,'RETRIEVING',?,NULL,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (transition_id, tenant, intake["intake_id"], sequence, target, JOB_TYPE, permission, code,
             "Execute the currently authorized native retrieval command.", payload["event_id"], intake["version"], aggregate.version,
             job["job_id"], source["version"], snapshot["source_snapshot_id"] if snapshot else None,
             intake["correlation_id"], payload["event_id"], at.isoformat()))
        state = DomainEvent(event_type="intake.state_changed", producer="listing_intake_service", tenant_id=tenant,
            aggregate_type="intake", aggregate_id=intake["intake_id"], aggregate_version=aggregate.version,
            partition_key=tenant + ":" + intake["intake_id"], correlation_id=intake["correlation_id"],
            causation_id=payload["event_id"], actor_ref=JOB_TYPE, occurred_at=at, schema_ref="#/payloads/IntakeStateChangedV1",
            payload={"intake_id": intake["intake_id"], "from_state": "RETRIEVING", "to_state": target,
                "transition_id": transition_id, "version": aggregate.version, "occurred_at": at.isoformat()})
        result = {"job_id": job["job_id"], "intake_id": intake["intake_id"], "processing_state": target,
            "version": aggregate.version, "transition_id": transition_id, "audit_event_id": audit_id,
            "outbox_event_id": state.event_id, "correlation_id": intake["correlation_id"], "error_code": code,
            "accepted_delivery_fence": physical.fence_token, "accepted_worker": physical.locked_by,
            "accepted_business_fence": job["fence_token"], "accepted_attempt": job["attempt"]}
        if snapshot:
            result.update(source_snapshot_id=snapshot["source_snapshot_id"], snapshot_created_event_id=snapshot_event_id,
                native_snapshot_proof={"snapshot_fields": snapshot, "bucket_metadata": asdict(bucket), "upload_intent_id": upload["intent_id"]})
        before()
        owner._audit(intake, action, {"processing_state": "RETRIEVING", "version": intake["version"]},
            {**result, "native_retrieval_provenance": owner._provenance(payload)}, at,
            audit_id=audit_id, causation=payload["event_id"])
        before()
        owner.graph._outbox(state)
        before()
        if (native_intake_retrieval_policy(tenant).sha256 != payload["policy_sha256"]
                or not owner.engine.query_one("SELECT job_id FROM durable_jobs WHERE job_id=? AND status=? AND fence_token=? "
            "AND locked_by=? AND lease_expires_at>clock_timestamp()", (job["job_id"], JobStatus.RUNNING.value,
                physical.fence_token, physical.locked_by))):
            raise JobFenceRejectedError("NATIVE_RETRIEVAL_FINAL_PHYSICAL_FENCE_REJECTED")
        status = "FAILED" if target == "FAILED" else "SUCCEEDED"
        before()
        if owner.engine.execute("UPDATE workflow.jobs SET status=?,checkpoint=?,result=?::jsonb,version=version+1,"
            "heartbeat_at=clock_timestamp(),lease_expires_at=NULL,updated_at=clock_timestamp() "
            "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND status='RUNNING' "
            "AND cancellation_requested_at IS NULL AND lease_expires_at>clock_timestamp() "
            "AND (?::boolean OR timeout_at>clock_timestamp()) "
            "AND (?::timestamptz IS NULL OR ?::timestamptz>clock_timestamp())",
            (status, target, canonical_json(result), tenant, job["job_id"], job["version"], job["fence_token"],
             target != "PARSING", validity, validity)).rowcount != 1:
            raise JobFenceRejectedError("NATIVE_RETRIEVAL_BUSINESS_TERMINAL_CAS_REJECTED")
        before()
        owner.queue.update_status(job["job_id"], JobStatus.FAILED if target == "FAILED" else JobStatus.SUCCEEDED,
            delivery_state=JobDeliveryState.DEAD_LETTER if target == "FAILED" else None,
            expected_version=physical.version, fence_token=physical.fence_token,
            error_message=code if target == "FAILED" else None)
        if target == "FAILED":
            before()
            dead_letter(owner, job, payload, code, at, checkpoint=target, version=job["version"] + 1)
        before()
        return result


def committed_result(owner, delivery, tenant, generation):
    """Historical result proof only. Never used as an external-IO capability."""
    owner.engine.query_one("SELECT set_config('statement_timeout',?,true)", ("750ms",))
    bound = owner._clock()
    bound += timedelta(milliseconds=750)

    def before():
        owner._admission_budget(bound)

    before()
    job = owner.engine.query_one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?", (tenant, delivery.job_id))
    if job is None or job["result"] is None:
        return None
    payload, result = _object(job["payload"]), _object(job["result"])
    target = result.get("processing_state")
    if target not in _BRANCHES:
        raise JobFenceRejectedError("NATIVE_RETRIEVAL_TERMINAL_STATE_REJECTED")
    permission, action = _BRANCHES[target]
    expected_status = "FAILED" if target == "FAILED" else "SUCCEEDED"
    common = {"job_id", "intake_id", "processing_state", "version", "transition_id", "audit_event_id",
              "outbox_event_id", "correlation_id", "error_code", "accepted_delivery_fence", "accepted_worker",
              "accepted_business_fence", "accepted_attempt"}
    fields = common | ({"source_snapshot_id", "snapshot_created_event_id", "native_snapshot_proof"} if target == "PARSING" else set())
    before()
    physical = owner.queue.get(delivery.job_id)
    if (set(result) != fields or physical is None
            or physical.job_type != JOB_TYPE or physical.status != (JobStatus.FAILED if target == "FAILED" else JobStatus.SUCCEEDED)
            or physical.payload != delivery.payload or job["status"] != expected_status or job["checkpoint"] != target
            or job["job_type"] != JOB_TYPE or job["aggregate_type"] != "intake"
            or job["idempotency_key"] != job["aggregate_id"] + ":" + payload["event_id"]
            or type(payload.get("delivery_generation")) is not int or payload["delivery_generation"] != generation
            or payload.get("attempt_token_sha256") != hashlib.sha256(delivery.payload["attempt_token"].encode()).hexdigest()
            or result["job_id"] != delivery.job_id or result["intake_id"] != job["aggregate_id"]
            or result["correlation_id"] != job["correlation_id"] or physical.correlation_id != job["correlation_id"]
            or type(result["version"]) is not int or result["version"] != payload["intake_version"] + 1
            or any(type(result[k]) is not int or result[k] < 1 for k in
                   ("accepted_delivery_fence", "accepted_business_fence", "accepted_attempt"))
            or result["accepted_delivery_fence"] != physical.fence_token
            or result["accepted_delivery_fence"] != payload.get("accepted_delivery_fence")
            or not isinstance(result["accepted_worker"], str) or not result["accepted_worker"]
            or result["accepted_worker"] != payload.get("accepted_worker")
            or result["accepted_business_fence"] != job["fence_token"]
            or result["accepted_business_fence"] != payload.get("accepted_business_fence")
            or result["accepted_attempt"] != job["attempt"] or result["accepted_attempt"] != payload.get("accepted_attempt")):
        raise JobFenceRejectedError("NATIVE_RETRIEVAL_TERMINAL_RECEIPT_REJECTED")
    owner._receipt(job, payload, before)
    before()
    transition = owner.engine.query_one("SELECT * FROM intake.intake_stage_transitions WHERE tenant_id=? AND transition_id=? "
        "AND intake_id=? AND job_id=? AND from_state='RETRIEVING' AND to_state=? AND expected_version=? AND resulting_version=? "
        "AND actor_subject_id IS NULL AND service_principal=? AND permission=? AND reason_code=? AND correlation_id=? AND causation_id=?",
        (tenant, result["transition_id"], job["aggregate_id"], job["job_id"], target, payload["intake_version"], result["version"],
         JOB_TYPE, permission, result["error_code"], job["correlation_id"], payload["event_id"]))
    if transition is None:
        raise JobFenceRejectedError("NATIVE_RETRIEVAL_TERMINAL_TRANSITION_MISSING")
    before()
    audit = owner.engine.query_one("SELECT after_value FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=? "
        "AND resource_type='intake' AND resource_id=? AND actor_subject_id IS NULL AND service_principal=? "
        "AND actor_role='SVC_RETRIEVAL' AND action=? AND result='SUCCEEDED' AND correlation_id=? AND causation_id=?",
        (tenant, result["audit_event_id"], job["aggregate_id"], JOB_TYPE, action, job["correlation_id"], payload["event_id"]))
    if audit is None or not audited_value_matches(_object(audit["after_value"]),
            {**result, "native_retrieval_provenance": owner._provenance(payload)}):
        raise JobFenceRejectedError("NATIVE_RETRIEVAL_TERMINAL_AUDIT_REJECTED")
    state = _read_event(owner, tenant, result["outbox_event_id"], event_type="intake.state_changed", aggregate_type="intake",
        aggregate_id=job["aggregate_id"], aggregate_version=result["version"], partition=tenant + ":" + job["aggregate_id"],
        correlation=job["correlation_id"], causation=payload["event_id"], actor=JOB_TYPE, before=before)
    if (state["producer"] != "listing_intake_service" or state["schema_ref"] != "#/payloads/IntakeStateChangedV1"
            or set(state["payload"]) != {"intake_id", "from_state", "to_state", "transition_id", "version", "occurred_at"}
            or state["payload"]["intake_id"] != job["aggregate_id"] or state["payload"]["from_state"] != "RETRIEVING"
            or state["payload"]["to_state"] != target or state["payload"]["version"] != result["version"]
            or state["payload"]["transition_id"] != result["transition_id"]):
        raise JobFenceRejectedError("NATIVE_RETRIEVAL_TERMINAL_STATE_EVENT_REJECTED")
    if target == "PARSING":
        proof = _object(result["native_snapshot_proof"])
        if set(proof) != {"snapshot_fields", "bucket_metadata", "upload_intent_id"}:
            raise JobFenceRejectedError("NATIVE_RETRIEVAL_TERMINAL_SNAPSHOT_PROOF_REJECTED")
        snapshot = _object(proof["snapshot_fields"])
        storage = payload["policy"]["storage_binding"]
        if (set(snapshot) != set(_SNAPSHOT_FIELDS) or snapshot["tenant_id"] != tenant
                or snapshot["intake_id"] != job["aggregate_id"] or snapshot["source_snapshot_id"] != result["source_snapshot_id"]
                or snapshot["source_id"] != payload["source_id"] or type(snapshot["object_generation"]) is not int
                or not 1 <= snapshot["object_generation"] <= 2**63 - 1 or snapshot["residency_mode"] != "TW_ONLY"
                or type(snapshot["version"]) is not int or snapshot["version"] < 1
                or type(snapshot["byte_length"]) is not int or not 0 <= snapshot["byte_length"] <= 512_000
                or not NativeSnapshotBinding(**storage).accepts_actual_key_version(snapshot["encryption_key_ref"])
                or proof["bucket_metadata"] != {"bucket_name": storage["bucket"], "location": "ASIA-EAST1", "location_type": "region"}
                or transition["source_snapshot_id"] != result["source_snapshot_id"]):
            raise JobFenceRejectedError("NATIVE_RETRIEVAL_TERMINAL_SNAPSHOT_BINDING_REJECTED")
        before()
        actual = owner.graph._one("SELECT * FROM intake.source_snapshots WHERE tenant_id=? AND source_snapshot_id=? "
            "AND intake_id=?", (tenant, result["source_snapshot_id"], job["aggregate_id"]))
        if any(actual[k] != v for k, v in snapshot.items()):
            raise JobFenceRejectedError("NATIVE_RETRIEVAL_TERMINAL_SNAPSHOT_CHANGED")
        event = _read_event(owner, tenant, result["snapshot_created_event_id"], event_type="snapshot.created",
            aggregate_type="source_snapshot", aggregate_id=result["source_snapshot_id"], aggregate_version=snapshot["version"],
            partition=tenant + ":" + job["aggregate_id"], correlation=job["correlation_id"], causation=payload["event_id"],
            actor=JOB_TYPE, before=before)
        if (event["producer"] != "retrieval_service" or event["schema_ref"] != "#/payloads/SnapshotCreatedV1"
                or event["payload"] != _snapshot_payload(snapshot) or event["sensitive_fields"] != ["payload.object_uri"]):
            raise JobFenceRejectedError("NATIVE_RETRIEVAL_TERMINAL_SNAPSHOT_EVENT_REJECTED")
        # Bind actual immutable bytes/provider proof to the same durable upload
        # journal; replay cannot accept a lookalike snapshot from another job.
        matches = [i for i in payload["attempt_intents"] if i["intent_id"] == proof["upload_intent_id"]]
        if (len(matches) != 1 or matches[0]["operation"] != "gcs_upload"
                or matches[0]["delivery_generation"] != generation
                or matches[0]["business_fence"] != job["fence_token"]
                or matches[0]["arguments"]["content_sha256"] != snapshot["content_sha256"]
                or matches[0]["arguments"]["byte_length"] != snapshot["byte_length"]
                or matches[0]["arguments"]["media_type"] != snapshot["media_type"]
                or snapshot["raw_object_uri"] != "gs://" + payload["policy"]["storage_binding"]["bucket"] + "/" + matches[0]["arguments"]["key"]):
            raise JobFenceRejectedError("NATIVE_RETRIEVAL_TERMINAL_UPLOAD_JOURNAL_REJECTED")
    elif transition["source_snapshot_id"] is not None:
        raise JobFenceRejectedError("NATIVE_RETRIEVAL_NEGATIVE_SNAPSHOT_FORBIDDEN")
    _, digest, _ = owner._parent(tenant, payload["event_id"], before)
    if digest != payload["event_sha256"]:
        raise JobFenceRejectedError("NATIVE_RETRIEVAL_TERMINAL_PARENT_CHANGED")
    before()
    return result
