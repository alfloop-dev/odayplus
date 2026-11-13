"""Journal each purpose-bound JSON storage operation under fresh authority.

The paired owner supplies a freshly verified execution state in every root.
Each operation uses the fixed assisted JSON-only process capability.
"""

import hashlib
import time
from dataclasses import asdict
from uuid import uuid4

from modules.external_data.application.native_assisted_snapshot_process_transport import (
    NativeAssistedSnapshotProcessTransport,
)
from modules.external_data.application.native_assisted_snapshot_transport import (
    NativeAssistedSnapshotObjectMetadata,
    NativeAssistedSnapshotProofRejected,
    NativeVerifiedAssistedSnapshot,
    native_assisted_snapshot_object_key,
)
from modules.external_data.application.native_intake_process import (
    NativeIORejected,
    NativeIOUnreaped,
)
from modules.external_data.application.native_snapshot_transport import NativeBucketMetadata
from modules.listing.application.native_intake_assisted_entry_policy import (
    native_intake_assisted_entry_policy,
)
from modules.listing.application.native_intake_retrieval_policy import canonical_json
from modules.listing.application.native_intake_retrieval_quota import NativeRetrievalReservation
from modules.listing.application.native_intake_source_policy import _time
from shared.infrastructure.persistence.assisted_listing_intake import (
    NativeIntakeSnapshotMetadataRepository,
)
from shared.jobs.queue import JobFenceRejectedError

OPERATIONS = frozenset({"gcs_bucket", "gcs_upload", "gcs_head", "gcs_download", "gcs_delete"})
MAX_INTENTS = 64


class NativeIntakeAssistedEntryIO:
    def __init__(self, owner, delivery, leases, policy):
        self.owner, self.delivery, self.leases, self.policy = owner, delivery, leases, policy
        self.tenant, self.generation = owner.delivery_identity(delivery)
        self.snapshots = NativeIntakeSnapshotMetadataRepository(owner.graph)
        factory = owner.snapshot_transport_factory or NativeAssistedSnapshotProcessTransport
        self.storage = factory(guard=leases.guard)
        self.object_metadata, self.upload_intent = None, None
        self.terminal_attempted = False

    def _fresh(self, *, settlement=False):
        return self.owner._fresh(self.delivery, self.tenant, self.generation,
            settlement=settlement, business_fence=self.leases.business_fence)

    def _intent(self, operation, arguments):
        if operation not in OPERATIONS:
            raise JobFenceRejectedError("NATIVE_ASSISTED_OPERATION_REJECTED")
        with self.leases.root():
            state = self._fresh()
            physical, job, payload, before = state.physical, state.job, state.payload, state.before
            source, classified = state.source, state.classified
            if native_intake_assisted_entry_policy(self.tenant) != self.policy:
                raise JobFenceRejectedError("NATIVE_ASSISTED_IO_CONFIG_CHANGED")
            self.owner._positive_source(payload, classified)
            body = self.owner._document(state.request, state.intake)
            document_sha = hashlib.sha256(body).hexdigest()
            if operation == "gcs_upload" and (
                    arguments.get("content_sha256") != document_sha or arguments.get("byte_length") != len(body)
                    or arguments.get("media_type") != "application/json"):
                raise JobFenceRejectedError("NATIVE_ASSISTED_UPLOAD_FIELDS_CHANGED")
            if operation in {"gcs_head", "gcs_download", "gcs_delete"}:
                if self.upload_intent is None or arguments.get("key") != self.upload_intent["arguments"]["key"]:
                    raise JobFenceRejectedError("NATIVE_ASSISTED_OBJECT_JOURNAL_REQUIRED")
                uploads = [x for x in payload["attempt_intents"] if x.get("intent_id") == self.upload_intent["intent_id"]]
                if (len(uploads) != 1 or uploads[0]["operation"] != "gcs_upload"
                        or uploads[0]["arguments"]["content_sha256"] != document_sha
                        or uploads[0]["delivery_generation"] != self.generation
                        or uploads[0]["business_fence"] != job["fence_token"]
                        or uploads[0]["physical_fence"] != physical.fence_token
                        or uploads[0]["worker"] != physical.locked_by):
                    raise JobFenceRejectedError("NATIVE_ASSISTED_OBJECT_JOURNAL_REJECTED")
            if operation == "gcs_delete":
                metadata = self.object_metadata
                if (self.terminal_attempted or metadata is None
                        or arguments != {"key": self.upload_intent["arguments"]["key"], "generation": metadata.generation}
                        or metadata.uri != "gs://" + self.policy.storage.bucket + "/" + arguments["key"]):
                    raise JobFenceRejectedError("NATIVE_ASSISTED_CLEANUP_OWNERSHIP_UNPROVEN")
                before()
                if self.snapshots.referenced_object(self.tenant, metadata.uri, metadata.generation) is not None:
                    raise JobFenceRejectedError("NATIVE_ASSISTED_CLEANUP_OBJECT_REFERENCED")
            intents = payload["attempt_intents"]
            if not isinstance(intents, list) or len(intents) >= MAX_INTENTS:
                raise JobFenceRejectedError("NATIVE_ASSISTED_INTENT_BUDGET_EXHAUSTED")
            reservation = self.owner.quota.reserve_storage(source["source_id"],
                global_limit=self.policy.global_max_concurrency, source_limit=self.policy.per_source_max_concurrency,
                before=before)
            at = self.owner._clock()
            intent = {"intent_id": str(uuid4()), "operation": operation, "arguments": arguments,
                "delivery_generation": self.generation, "physical_fence": physical.fence_token,
                "business_fence": job["fence_token"], "attempt": job["attempt"], "worker": physical.locked_by,
                "request_id": payload["request_id"], "input_sha256": payload["input_sha256"],
                "fields_sha256": payload["fields_sha256"], "command_sha256": payload["command_sha256"],
                "document_sha256": document_sha, "source_snapshot_sha256": classified.source_snapshot_sha256,
                "source_version": source["version"], "source_valid_until": classified.valid_until.isoformat(),
                "authority": self.owner._authority_snapshot(state.principal, at),
                "reservation": asdict(reservation), "admitted_at": at.isoformat(), "state": "ADMITTED"}
            updated = {**payload, "attempt_intents": [*intents, intent]}
            before()
            if self.owner.engine.execute("UPDATE workflow.jobs SET payload=?::jsonb,version=version+1,updated_at=clock_timestamp() "
                "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND status='RUNNING' "
                "AND cancellation_requested_at IS NULL AND timeout_at>clock_timestamp() "
                "AND lease_expires_at>clock_timestamp() AND ?::timestamptz>clock_timestamp()",
                (canonical_json(updated), self.tenant, job["job_id"], job["version"], job["fence_token"],
                 classified.valid_until.isoformat())).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_ASSISTED_INTENT_COMMIT_REJECTED")
            before()
            if native_intake_assisted_entry_policy(self.tenant) != self.policy:
                raise JobFenceRejectedError("NATIVE_ASSISTED_INTENT_CONFIG_CHANGED")
        return intent

    def _finish(self, intent, *, success):
        # The fixed process transport returns/raises only after its child exits,
        # except for NativeIOUnreaped, which never enters this release path.
        with self.leases.root():
            state = self._fresh(settlement=True)
            job, payload, before = state.job, state.payload, state.before
            self.owner._positive_source(payload, state.classified)
            matches = [x for x in payload["attempt_intents"] if x.get("intent_id") == intent["intent_id"]]
            if matches != [intent] or intent["state"] != "ADMITTED":
                raise JobFenceRejectedError("NATIVE_ASSISTED_INTENT_OWNERSHIP_REJECTED")
            self.owner.quota.release(NativeRetrievalReservation(**intent["reservation"]), process_reaped=True, before=before)
            finished = {**intent, "state": "FINISHED" if success else "ERROR", "finished_at": self.owner._clock().isoformat()}
            updated = {**payload, "attempt_intents": [finished if x == intent else x for x in payload["attempt_intents"]]}
            before()
            if self.owner.engine.execute("UPDATE workflow.jobs SET payload=?::jsonb,version=version+1,updated_at=clock_timestamp() "
                "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND status='RUNNING' "
                "AND cancellation_requested_at IS NULL AND lease_expires_at>clock_timestamp() "
                "AND ?::timestamptz>clock_timestamp()",
                (canonical_json(updated), self.tenant, job["job_id"], job["version"], job["fence_token"],
                 state.classified.valid_until.isoformat())).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_ASSISTED_INTENT_FINISH_REJECTED")
            before()
        if _time(job["timeout_at"]) <= _time(finished["finished_at"]):
            raise NativeIORejected("NATIVE_IO_DEADLINE_EXPIRED")

    def call(self, operation, arguments, invoke):
        started = time.monotonic()
        intent = self._intent(operation, arguments)
        if operation == "gcs_upload":
            self.upload_intent = intent
        permission_bound = started + max(0, (_time(intent["source_valid_until"]) - _time(intent["admitted_at"])).total_seconds())
        self.leases.start()
        try:
            result = invoke(min(self.leases.io_deadline(), started + 10, permission_bound))
        except NativeIOUnreaped:
            raise
        except Exception:
            self.leases.pause()
            self._finish(intent, success=False)
            raise
        self.leases.pause()
        self._finish(intent, success=True)
        return result, intent

    def capture(self):
        with self.leases.root():
            state = self._fresh()
            body = self.owner._document(state.request, state.intake)
        bucket, _ = self.call("gcs_bucket", {}, lambda deadline: self.storage.bucket_info(self.policy.storage, deadline=deadline))
        if (not isinstance(bucket, NativeBucketMetadata) or bucket.bucket_name != self.policy.storage.bucket
                or bucket.location != "ASIA-EAST1" or bucket.location_type != "region"):
            raise NativeAssistedSnapshotProofRejected("NATIVE_ASSISTED_BUCKET_PROOF_REJECTED")
        capture_id = str(uuid4())
        key = native_assisted_snapshot_object_key(self.tenant, self.delivery.job_id,
            self.generation, self.leases.business_fence, capture_id)
        digest = hashlib.sha256(body).hexdigest()
        arguments = {"key": key, "content_sha256": digest, "byte_length": len(body),
            "media_type": "application/json", "capture_id": capture_id}
        try:
            metadata, upload = self.call("gcs_upload", arguments,
                lambda deadline: self.storage.upload_with_kms_if_absent(self.policy.storage, key, body,
                    "application/json", deadline=deadline))
            if not isinstance(metadata, NativeAssistedSnapshotObjectMetadata):
                raise NativeAssistedSnapshotProofRejected("NATIVE_ASSISTED_UPLOAD_PROOF_REJECTED")
            generation = metadata.generation
            self.object_metadata, self.upload_intent = metadata, upload
        except NativeIOUnreaped:
            raise
        except (NativeIORejected, NativeAssistedSnapshotProofRejected):
            with self.leases.root():
                state = self._fresh()
                matches = [x for x in state.payload["attempt_intents"] if x["operation"] == "gcs_upload"
                    and x["arguments"] == arguments and x["delivery_generation"] == self.generation
                    and x["business_fence"] == self.leases.business_fence]
                if len(matches) != 1:
                    raise JobFenceRejectedError("NATIVE_ASSISTED_UNKNOWN_UPLOAD_INTENT_MISSING") from None
                upload = matches[0]
                self.upload_intent = upload
            generation = None
        metadata, _ = self.call("gcs_head", {"key": key, "generation": generation},
            lambda deadline: self.storage.head_version(self.policy.storage, key, generation, deadline=deadline))
        if (not isinstance(metadata, NativeAssistedSnapshotObjectMetadata)
                or metadata.uri != "gs://" + self.policy.storage.bucket + "/" + key
                or type(metadata.generation) is not int or not 1 <= metadata.generation <= 2**63 - 1
                or (generation is not None and metadata.generation != generation)
                or not self.policy.storage.accepts_actual_key_version(metadata.kms_key_version_name)):
            raise NativeAssistedSnapshotProofRejected("NATIVE_ASSISTED_HEAD_PROOF_REJECTED")
        self.object_metadata, self.upload_intent = metadata, upload
        data, _ = self.call("gcs_download", {"key": key, "generation": metadata.generation},
            lambda deadline: self.storage.download_version_bounded(self.policy.storage, key, metadata.generation, deadline=deadline))
        verified = NativeVerifiedAssistedSnapshot.from_download(data, metadata, expected_sha256=digest)
        if metadata.media_type != "application/json" or metadata.byte_length != len(body):
            raise NativeAssistedSnapshotProofRejected("NATIVE_ASSISTED_SNAPSHOT_DOCUMENT_REJECTED")
        return verified, upload, bucket

    def cleanup_known_unreferenced(self):
        if self.object_metadata is None or self.upload_intent is None or self.terminal_attempted:
            return
        key = self.upload_intent["arguments"]["key"]
        generation = self.object_metadata.generation
        self.call("gcs_delete", {"key": key, "generation": generation},
            lambda deadline: self.storage.delete_exact_generation(self.policy.storage, key, generation, deadline=deadline))
