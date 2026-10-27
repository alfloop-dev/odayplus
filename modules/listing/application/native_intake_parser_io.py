"""Journal fixed read-only snapshot operations and the actual parser child.

Fresh current authority gates each positive boundary. After a known reaped
operation, an owned control-plane root may close its journal/release its own
anonymous quota despite positive configuration loss. Such cleanup never
authorizes use of output or changes a canonical parser run or Intake.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from uuid import uuid4

from modules.external_data.application.native_assisted_snapshot_transport import (
    NativeAssistedSnapshotObjectMetadata,
    NativeVerifiedAssistedSnapshot,
)
from modules.external_data.application.native_intake_process import (
    NativeIORejected,
    NativeIOUnreaped,
)
from modules.external_data.application.native_manual_parser_process import NativeManualParserProcess
from modules.external_data.application.native_parser_output import (
    NativeParserOutput,
    validate_native_manual_parser_output,
)
from modules.external_data.application.native_parser_snapshot_transport import (
    NativeParserSnapshotTransport,
)
from modules.external_data.application.native_snapshot_transport import NativeBucketMetadata
from modules.listing.application.native_intake_assisted_entry_request_proof import _time
from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_parser_completion import _ParserOperationFinishes
from modules.listing.application.native_intake_parser_execution import NativeParserExecutionReads
from modules.listing.application.native_intake_parser_finish import _ParserFinishSQL
from modules.listing.application.native_intake_parser_job_proof import parser_delivery_identity
from modules.listing.application.native_intake_parser_journal import OPERATIONS
from modules.listing.application.native_intake_parser_leases import NativeParserCompositeLeases
from modules.listing.application.native_intake_parser_policy import (
    MAX_CHILD_SECONDS,
    MAX_IO_SECONDS,
)
from modules.listing.application.native_intake_retrieval_policy import (
    canonical_json,
    canonical_sha256,
)
from modules.listing.application.native_intake_retrieval_quota import (
    NativeIntakeRetrievalQuota,
)
from shared.jobs.queue import JobFenceRejectedError

MAX_INTENTS = 64


@dataclass(frozen=True)
class NativeParserObservedOutput:
    """Observed facts only; the final owner must freshly authorize a commit."""
    output: NativeParserOutput
    source_snapshot_id: str
    content_sha256: str
    object_generation: int
    metadata_sha256: str
    original_input_sha256: str
    artifact_sha256: str
    intent_ids: tuple[str, ...]


class NativeIntakeParserIO:
    def __init__(self, owner, delivery, leases, policy, *, storage_factory=None, parser_process_factory=None):
        self.owner, self.delivery, self.leases, self.policy = owner, delivery, leases, policy
        self.tenant, self.generation = parser_delivery_identity(delivery)
        if (not isinstance(leases, NativeParserCompositeLeases) or leases.owner is not owner
                or leases.delivery != delivery or leases.policy_sha256 != policy.sha256
                or policy.tenant_id != self.tenant):
            raise JobFenceRejectedError("NATIVE_PARSER_IO_OWNER_REQUIRED")
        # Only explicit constructor injection is a synthetic test seam.
        # Neither jobs nor settings choose an executable/provider entrypoint.
        storage = NativeParserSnapshotTransport if storage_factory is None else storage_factory
        parser = NativeManualParserProcess if parser_process_factory is None else parser_process_factory
        self.storage = storage(guard=leases.guard)
        self.parser = parser()
        self.quota = NativeIntakeRetrievalQuota(owner.engine)
        self.completed = {}
        self.metadata = None
        self._finishes = _ParserOperationFinishes(self)
        self._active_operation = None

    def _fresh(self):
        return NativeParserExecutionReads(self.owner).fresh(self.delivery, self.tenant, self.generation,
            business_fence=self.leases.business_fence)

    def _arguments(self, operation, context, data):
        snapshot = context.original.snapshot()
        if operation == "gcs_bucket":
            return {}
        if operation in {"gcs_head", "gcs_download"}:
            prefix = "gs://" + self.policy.storage.bucket + "/"
            if (not snapshot["raw_object_uri"].startswith(prefix)
                    or type(snapshot["object_generation"]) is not int or snapshot["object_generation"] < 1):
                raise JobFenceRejectedError("NATIVE_PARSER_PINNED_SNAPSHOT_REQUIRED")
            return {"key": snapshot["raw_object_uri"][len(prefix):], "generation": snapshot["object_generation"]}
        if operation == "parser_child":
            if (not isinstance(data, bytes) or len(data) != snapshot["byte_length"]
                    or hashlib.sha256(data).hexdigest() != context.original.lineage.content_sha256):
                raise JobFenceRejectedError("NATIVE_PARSER_CHILD_DOCUMENT_REJECTED")
            return {"document_sha256": context.original.lineage.content_sha256, "byte_length": len(data),
                "artifact_sha256": context.artifact.sha256,
                "output_schema_version": context.artifact.output_schema_version}
        raise JobFenceRejectedError("NATIVE_PARSER_OPERATION_REJECTED")

    def _intent(self, operation, *, data):
        if operation not in OPERATIONS:
            raise JobFenceRejectedError("NATIVE_PARSER_OPERATION_REJECTED")
        with self.leases.root():
            state = self._fresh()
            job, payload, before, context = state.job, state.payload, state.before, state.context
            if job["status"] != "RUNNING" or context.policy != self.policy:
                raise JobFenceRejectedError("NATIVE_PARSER_IO_NOT_CURRENT")
            arguments = self._arguments(operation, context, data)
            intents = payload["attempt_intents"]
            if not isinstance(intents, list) or len(intents) >= MAX_INTENTS:
                raise JobFenceRejectedError("NATIVE_PARSER_INTENT_BUDGET_EXHAUSTED")
            ids = set()
            for item in intents:
                before.check_in_memory()
                if (not isinstance(item, dict) or item.get("operation") not in OPERATIONS
                        or item.get("state") not in {"FINISHED", "ERROR"}):
                    raise JobFenceRejectedError("NATIVE_PARSER_PRIOR_OPERATION_UNRESOLVED")
                ident = native_intake_uuid(item["intent_id"])
                if ident in ids:
                    raise JobFenceRejectedError("NATIVE_PARSER_INTENT_NOT_UNIQUE")
                ids.add(ident)
            if operation == "gcs_bucket" and any(item.get("delivery_generation") == self.generation
                    and item.get("attempt") == job["attempt"] for item in intents):
                raise JobFenceRejectedError("NATIVE_PARSER_ATTEMPT_IO_ALREADY_STARTED")
            position = OPERATIONS.index(operation)
            if position:
                predecessor = OPERATIONS[position - 1]
                matched = [item for item in intents if item["intent_id"] == self.completed.get(predecessor)]
                if (len(matched) != 1 or matched[0]["state"] != "FINISHED" or matched[0]["operation"] != predecessor
                        or any(matched[0][key] != value for key, value in {
                            "delivery_generation": self.generation, "business_fence": job["fence_token"],
                            "physical_fence": state.physical.fence_token, "worker": state.physical.locked_by,
                            "parser_run_id": state.run["parser_run_id"], "attempt": job["attempt"],
                            "binding_sha256": canonical_sha256(context.binding())}.items())):
                    raise JobFenceRejectedError("NATIVE_PARSER_PREDECESSOR_PROOF_REQUIRED")
            reservation = self.quota.reserve_storage(context.source["source_id"],
                global_limit=self.policy.global_max_concurrency, source_limit=self.policy.per_source_max_concurrency,
                before=before)
            at = self.owner.clock._clock()
            binding = context.binding()
            intent = {"intent_id": str(uuid4()), "operation": operation, "arguments": arguments,
                "state": "ADMITTED", "delivery_generation": self.generation,
                "physical_fence": state.physical.fence_token, "business_fence": job["fence_token"],
                "attempt": job["attempt"], "worker": state.physical.locked_by,
                "parser_run_id": state.run["parser_run_id"],
                "source_snapshot_id": state.run["source_snapshot_id"],
                "original_input_sha256": context.original.sha256, "artifact_sha256": context.artifact.sha256,
                "binding_sha256": canonical_sha256(binding), "policy_sha256": self.policy.sha256,
                "source_version": context.source["version"], "source_sha256": context.classified.source_snapshot_sha256,
                "source_valid_until": context.classified.valid_until.isoformat(),
                "human_authority_sha256": binding["human_authority_sha256"],
                "reservation": asdict(reservation), "admitted_at": at.isoformat()}
            updated = {**payload, "attempt_intents": [*intents, intent]}
            before()
            if self.owner.engine.execute("UPDATE workflow.jobs SET payload=?::jsonb,version=version+1,"
                    "updated_at=clock_timestamp() WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? "
                    "AND status='RUNNING' AND result IS NULL AND cancellation_requested_at IS NULL "
                    "AND timeout_at>clock_timestamp() AND lease_expires_at>clock_timestamp() "
                    "AND ?::timestamptz>clock_timestamp()",
                    (canonical_json(updated), self.tenant, job["job_id"], job["version"], job["fence_token"],
                        context.classified.valid_until.isoformat())).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_PARSER_INTENT_COMMIT_REJECTED")
            self.owner._confirm(context.intake, state.run["parser_release_id"], self.generation,
                context.binding(), context.attempt_token, before)
            before()
        # Capture facts only after the actual intent root acknowledges exit.
        # A lost intent reply cannot start IO or reconstruct this capability.
        self._active_operation = self._finishes.open(intent, state, updated)
        return intent, context

    def _finish(self, intent, *, pending):
        _, captured, _ = self._finishes.facts(pending)
        if canonical_json(captured) != canonical_json(intent):
            raise JobFenceRejectedError("NATIVE_PARSER_INTENT_OWNERSHIP_REJECTED")
        return _ParserFinishSQL(self, self._finishes).finish(pending)

    def _completion_unproven(self, cause):
        try:
            self.leases.pause()
        except BaseException as pause_error:
            raise NativeIOUnreaped("NATIVE_IO_PARSER_CHILD_OR_HEARTBEAT_UNRESOLVED") from pause_error
        raise NativeIOUnreaped("NATIVE_IO_PARSER_COMPLETION_UNPROVEN") from cause

    def _invoke(self, operation, context, arguments, deadline, data):
        if getattr(getattr(self.owner.engine, "_local", None), "connection", None) is not None:
            raise JobFenceRejectedError("NATIVE_PARSER_IO_SQL_ROOT_FORBIDDEN")
        snapshot = context.original.snapshot()
        if operation == "gcs_bucket":
            result = self.storage.bucket_info(self.policy.storage, deadline=deadline)
            if (not isinstance(result, NativeBucketMetadata) or result.bucket_name != self.policy.storage.bucket
                    or result.location != "ASIA-EAST1" or result.location_type != "region"):
                raise NativeIORejected("NATIVE_IO_PARSER_BUCKET_PROOF_REJECTED")
            return result, {"sha256": canonical_sha256(asdict(result))}
        if operation == "gcs_head":
            result = self.storage.head_version(self.policy.storage, arguments["key"], arguments["generation"], deadline=deadline)
            if (not isinstance(result, NativeAssistedSnapshotObjectMetadata)
                    or result.uri != snapshot["raw_object_uri"] or result.generation != snapshot["object_generation"]
                    or result.byte_length != snapshot["byte_length"] or result.media_type != "application/json"
                    or result.kms_key_version_name != snapshot["encryption_key_ref"]):
                raise NativeIORejected("NATIVE_IO_PARSER_SNAPSHOT_METADATA_REJECTED")
            return result, {"sha256": canonical_sha256(asdict(result)), "generation": result.generation,
                "byte_length": result.byte_length}
        if operation == "gcs_download":
            result = self.storage.download_version_bounded(self.policy.storage, arguments["key"],
                arguments["generation"], deadline=deadline)
            verified = NativeVerifiedAssistedSnapshot.from_download(result, self.metadata,
                expected_sha256=context.original.lineage.content_sha256)
            return result, {"sha256": verified.content_sha256, "byte_length": len(result),
                "generation": verified.metadata.generation}
        if operation == "parser_child":
            result = self.parser.parse(data, lineage=context.original.lineage, artifact=context.artifact,
                original_fields=context.original.command().fields.values(), deadline=deadline, guard=self.leases.guard)
            if not isinstance(result, NativeParserOutput):
                raise NativeIORejected("NATIVE_IO_PARSER_OUTPUT_PROOF_REQUIRED")
            encoded = json.dumps(result.facts.payloads(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            checked = validate_native_manual_parser_output(encoded, artifact=context.artifact,
                lineage=context.original.lineage, original_fields=context.original.command().fields.values())
            if checked != result:
                raise NativeIORejected("NATIVE_IO_PARSER_OUTPUT_PROOF_REJECTED")
            return checked, {"sha256": checked.output_sha256, "artifact_sha256": checked.artifact_sha256,
                "status": checked.status}
        raise JobFenceRejectedError("NATIVE_PARSER_OPERATION_REJECTED")

    def _call(self, operation, *, data=None):
        from modules.external_data.application.native_intake_process_lifecycle import (
            _observe_native_process,
        )

        started = time.monotonic()
        intent, context = self._intent(operation, data=data)
        token, observation = self._active_operation, None
        maximum = MAX_CHILD_SECONDS if operation == "parser_child" else MAX_IO_SECONDS
        permission_bound = started + max(0, (_time(intent["source_valid_until"]) - _time(intent["admitted_at"])).total_seconds())
        try:
            self.leases.start()
            deadline = min(self.leases.io_deadline(maximum_seconds=maximum), started + maximum, permission_bound)
            if deadline <= time.monotonic():
                raise NativeIORejected("NATIVE_IO_PARSER_DEADLINE_EXPIRED")
            with _observe_native_process() as observation:
                self._finishes.attach(token, observation)
                result, observed = self._invoke(operation, context, intent["arguments"], deadline, data)
        except NativeIOUnreaped:
            try:
                self.leases.pause()
            except BaseException as pause_error:
                raise NativeIOUnreaped("NATIVE_IO_PARSER_CHILD_OR_HEARTBEAT_UNRESOLVED") from pause_error
            raise
        except Exception as error:
            try:
                pending = (self._finishes.not_invoked(token, error) if observation is None
                    else self._finishes.observed(token, observation, error=error))
            except Exception as completion_error:
                self._completion_unproven(completion_error)
            self.leases.pause()
            self._finish(intent, pending=pending)
            raise
        try:
            pending = self._finishes.observed(token, observation, observed=observed)
        except Exception as completion_error:
            self._completion_unproven(completion_error)
        self.leases.pause()
        finished = self._finish(intent, pending=pending)
        # Closing observed IO does not authorize consuming its returned facts.
        with self.leases.root():
            state = self._fresh()
            if (state.context.policy != self.policy
                    or canonical_sha256(state.context.binding()) != intent["binding_sha256"]):
                raise JobFenceRejectedError("NATIVE_PARSER_POST_IO_BINDING_REJECTED")
            state.before()
        self.completed[operation] = finished["intent_id"]
        return result, context

    def read_and_parse(self):
        self._call("gcs_bucket")
        self.metadata, _ = self._call("gcs_head")
        data, _ = self._call("gcs_download")
        output, context = self._call("parser_child", data=data)
        return NativeParserObservedOutput(output, context.original.snapshot()["source_snapshot_id"],
            context.original.lineage.content_sha256, self.metadata.generation,
            canonical_sha256(asdict(self.metadata)), context.original.sha256, context.artifact.sha256,
            tuple(self.completed[operation] for operation in OPERATIONS))
