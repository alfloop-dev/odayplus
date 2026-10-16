"""Admit each bounded IO operation under a durable, fenced tenant intent."""

import hashlib
import time
from dataclasses import asdict
from urllib.parse import urljoin
from uuid import uuid4

from modules.external_data.application.native_intake_process import (
    NativeIORejected,
    NativeIOUnreaped,
)
from modules.external_data.application.native_snapshot_process_transport import (
    NativeHttpProcessTransport,
    NativeSnapshotProcessTransport,
)
from modules.external_data.application.native_snapshot_transport import (
    NativeSnapshotProofRejected,
    NativeVerifiedSnapshot,
    native_snapshot_object_key,
)
from modules.listing.application.native_intake_retrieval import (
    NativeRetrievalQuarantineRequired,
    _time,
)
from modules.listing.application.native_intake_retrieval_policy import (
    MAX_REDIRECTS,
    canonical_json,
    native_intake_retrieval_policy,
)
from modules.listing.application.native_intake_retrieval_quota import NativeRetrievalReservation
from shared.jobs.queue import JobFenceRejectedError

_OPERATIONS = frozenset({"http_hop", "gcs_bucket", "gcs_upload", "gcs_head", "gcs_download", "gcs_delete"})
MAX_INTENTS = 64


class NativeIntakeRetrievalIO:
    def __init__(self, owner, delivery, leases, policy):
        self.owner, self.delivery, self.leases, self.policy = owner, delivery, leases, policy
        self.tenant, self.generation = owner.auth.decode(delivery)
        self.http = owner.http_transport or NativeHttpProcessTransport()
        factory = owner.snapshot_transport_factory or NativeSnapshotProcessTransport
        self.storage = factory(guard=leases.guard)
        self.object_metadata = None
        self.upload_intent = None

    def _fresh(self, url, *, settlement=False):
        return self.owner._fresh(self.delivery, self.tenant, self.generation, url=url,
            settlement=settlement, business_fence=self.leases.business_fence)

    def _intent(self, operation, url, arguments):
        if operation not in _OPERATIONS:
            raise JobFenceRejectedError("NATIVE_RETRIEVAL_OPERATION_REJECTED")
        with self.leases.root():
            physical, job, payload, before, intake, source, classified = self._fresh(url)
            self.owner._positive_source(payload, classified)
            if operation == "gcs_delete":
                metadata = self.object_metadata
                if (metadata is None or self.upload_intent is None
                        or arguments != {"key": self.upload_intent["arguments"]["key"], "generation": metadata.generation}
                        or metadata.uri != "gs://" + self.policy.storage.bucket + "/" + arguments["key"]):
                    raise JobFenceRejectedError("NATIVE_RETRIEVAL_CLEANUP_OWNERSHIP_UNPROVEN")
                uploads = [i for i in payload["attempt_intents"] if i["intent_id"] == self.upload_intent["intent_id"]]
                if (len(uploads) != 1 or uploads[0]["operation"] != "gcs_upload"
                        or uploads[0]["arguments"]["key"] != arguments["key"]
                        or uploads[0]["delivery_generation"] != self.generation
                        or uploads[0]["business_fence"] != job["fence_token"]
                        or uploads[0]["physical_fence"] != physical.fence_token
                        or uploads[0]["worker"] != physical.locked_by):
                    raise JobFenceRejectedError("NATIVE_RETRIEVAL_CLEANUP_JOURNAL_UNPROVEN")
                # No successful/unknown SQL outcome is inferred from an IO
                # error. Re-read current, owned SQL and prove no reference to
                # this tenant's exact attempt object/generation before delete.
                before()
                reference = self.owner.engine.query_one("SELECT source_snapshot_id FROM intake.source_snapshots "
                    "WHERE tenant_id=? AND raw_object_uri=? AND (object_generation=? OR object_generation IS NULL) LIMIT 1",
                    (self.tenant, metadata.uri, metadata.generation))
                if reference is not None:
                    raise JobFenceRejectedError("NATIVE_RETRIEVAL_CLEANUP_OBJECT_REFERENCED")
            intents = payload["attempt_intents"]
            if not isinstance(intents, list) or len(intents) >= MAX_INTENTS:
                raise JobFenceRejectedError("NATIVE_RETRIEVAL_INTENT_BUDGET_EXHAUSTED")
            reservation = self.owner.quota.reserve(source, global_limit=self.policy.global_max_concurrency,
                charge_rate=operation == "http_hop", before=before)
            at = self.owner._clock()
            intent = {"intent_id": str(uuid4()), "operation": operation, "arguments": arguments,
                "delivery_generation": self.generation, "physical_fence": physical.fence_token,
                "business_fence": job["fence_token"], "attempt": job["attempt"], "worker": physical.locked_by,
                "input_sha256": payload["input_sha256"], "source_snapshot_sha256": classified.source_snapshot_sha256,
                "source_version": source["version"], "url": url, "reservation": asdict(reservation),
                "admitted_at": at.isoformat(), "state": "ADMITTED"}
            updated = {**payload, "attempt_intents": [*intents, intent]}
            before()
            if self.owner.engine.execute("UPDATE workflow.jobs SET payload=?::jsonb,version=version+1,updated_at=clock_timestamp() "
                "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND status='RUNNING' "
                "AND cancellation_requested_at IS NULL AND timeout_at>clock_timestamp() "
                "AND lease_expires_at>clock_timestamp() AND ?::timestamptz>clock_timestamp()",
                (canonical_json(updated), self.tenant, job["job_id"], job["version"], job["fence_token"],
                 classified.valid_until.isoformat())).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_RETRIEVAL_INTENT_COMMIT_REJECTED")
            before()
            if native_intake_retrieval_policy(self.tenant) != self.policy:
                raise JobFenceRejectedError("NATIVE_RETRIEVAL_INTENT_CONFIG_CHANGED")
        return intent

    def _finish(self, intent, *, success):
        # Called only after a transport proves its owned child exited. No
        # un-reaped process may enter this early-release path. Lost actor,
        # input, fence or cancellation retains the conservative slot expiry.
        with self.leases.root():
            physical, job, payload, before, _, _, classified = self._fresh(intent["url"], settlement=True)
            matches = [i for i in payload["attempt_intents"] if i.get("intent_id") == intent["intent_id"]]
            if matches != [intent] or intent["state"] != "ADMITTED":
                raise JobFenceRejectedError("NATIVE_RETRIEVAL_INTENT_OWNERSHIP_REJECTED")
            self.owner.quota.release(NativeRetrievalReservation(**intent["reservation"]), process_reaped=True, before=before)
            finished = {**intent, "state": "FINISHED" if success else "ERROR", "finished_at": self.owner._clock().isoformat()}
            updated = {**payload, "attempt_intents": [finished if i == intent else i for i in payload["attempt_intents"]]}
            before()
            if self.owner.engine.execute("UPDATE workflow.jobs SET payload=?::jsonb,version=version+1,updated_at=clock_timestamp() "
                "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND status='RUNNING' "
                "AND cancellation_requested_at IS NULL AND lease_expires_at>clock_timestamp()",
                (canonical_json(updated), self.tenant, job["job_id"], job["version"], job["fence_token"])).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_RETRIEVAL_INTENT_FINISH_REJECTED")
            before()
        self.owner._positive_source(payload, classified)
        if _time(job["timeout_at"]) <= _time(finished["finished_at"]):
            raise NativeIORejected("NATIVE_IO_DEADLINE_EXPIRED")

    def call(self, operation, url, arguments, invoke):
        start = time.monotonic()
        intent = self._intent(operation, url, arguments)
        self.leases.start()
        try:
            # Include admission/renewal setup in this slot's hard IO cap.
            # Its 12s expiry therefore outlives the 10s IO plus 1s cleanup.
            result = invoke(min(self.leases.io_deadline(), start + 10))
        except NativeIOUnreaped:
            # No early release, negative terminal closure or orphan delete.
            # Recovery must reconcile this still-owned attempt separately.
            raise
        except Exception:
            self.leases.pause()
            self._finish(intent, success=False)
            raise
        self.leases.pause()
        self._finish(intent, success=True)
        return result, intent

    def capture(self):
        # Provider region validation precedes fetching any source content.
        # The dedicated GCS token is read only inside this admitted operation.
        with self.leases.root():
            _, _, _, _, intake, _, _ = self._fresh(None)
            original_url = intake["canonical_url"]
        bucket, _ = self.call("gcs_bucket", original_url, {}, lambda deadline: self.storage.bucket_info(self.policy.storage, deadline=deadline))
        current_url = original_url
        for hop in range(MAX_REDIRECTS + 1):
            response, _ = self.call("http_hop", current_url, {"redirect_index": hop},
                lambda deadline, url=current_url: self.http.fetch_hop(url, deadline=deadline, guard=self.leases.guard))
            if response.auth_wall or response.status in {401, 403}:
                raise NativeRetrievalQuarantineRequired("NATIVE_RETRIEVAL_AUTH_WALL")
            if response.status in {301, 302, 303, 307, 308}:
                if hop == MAX_REDIRECTS or not response.location:
                    raise NativeRetrievalQuarantineRequired("NATIVE_RETRIEVAL_REDIRECT_PROHIBITED")
                current_url = urljoin(current_url, response.location)
                # The NEXT intent independently proves source association and
                # all current authority before its child resolves this URL.
                continue
            if response.status != 200:
                raise NativeIORejected("NATIVE_HTTP_RESPONSE_REJECTED", status=response.status)
            break
        body = response.body
        digest = hashlib.sha256(body).hexdigest()
        capture_id = str(uuid4())
        key = native_snapshot_object_key(self.tenant, self.delivery.job_id, self.generation,
                                         self.leases.business_fence, capture_id)
        # Durable upload intent contains no raw body. Its key/fences/hash,
        # byte length/media and DB capture time are committed before upload.
        arguments = {"key": key, "content_sha256": digest, "byte_length": len(body),
                     "media_type": response.media_type, "capture_id": capture_id}
        try:
            metadata, upload = self.call("gcs_upload", current_url, arguments,
                lambda deadline: self.storage.upload_with_kms_if_absent(self.policy.storage, key, body,
                    response.media_type, deadline=deadline))
            generation = metadata.generation
            self.object_metadata, self.upload_intent = metadata, upload
        except NativeIOUnreaped:
            raise
        except (NativeIORejected, NativeSnapshotProofRejected):
            # Reconcile only this exact committed upload intent/key. Never
            # enumerate objects or adopt a late older attempt's object.
            with self.leases.root():
                _, _, payload, _, _, _, _ = self._fresh(current_url)
                matches = [i for i in payload["attempt_intents"] if i["operation"] == "gcs_upload"
                           and i["arguments"] == arguments and i["delivery_generation"] == self.generation
                           and i["business_fence"] == self.leases.business_fence]
                if len(matches) != 1:
                    raise JobFenceRejectedError("NATIVE_RETRIEVAL_UNKNOWN_UPLOAD_INTENT_MISSING") from None
                upload = matches[0]
            generation = None
        metadata, _ = self.call("gcs_head", current_url, {"key": key, "generation": generation},
            lambda deadline: self.storage.head_version(self.policy.storage, key, generation, deadline=deadline))
        self.object_metadata, self.upload_intent = metadata, upload
        downloaded, _ = self.call("gcs_download", current_url, {"key": key, "generation": metadata.generation},
            lambda deadline: self.storage.download_version_bounded(self.policy.storage, key, metadata.generation, deadline=deadline))
        verified = NativeVerifiedSnapshot.from_download(downloaded, metadata, expected_sha256=digest)
        if metadata.media_type != response.media_type or metadata.byte_length != len(body):
            raise NativeSnapshotProofRejected("NATIVE_RETRIEVAL_SNAPSHOT_PROOF_REJECTED")
        return verified, upload, bucket

    def cleanup_known_unreferenced(self):
        if self.object_metadata is None or self.upload_intent is None:
            return
        key = self.upload_intent["arguments"]["key"]
        generation = self.object_metadata.generation
        self.call("gcs_delete", self.upload_intent["url"], {"key": key, "generation": generation},
            lambda deadline: self.storage.delete_exact_generation(self.policy.storage, key, generation, deadline=deadline))
