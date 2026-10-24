"""Prove committed fixed-provider parser results without restoring grants.

Initial positive attempts, owned unavailable and permanent failures. Retries
and cancellation require their own complete proofs.
"""
from __future__ import annotations

import json
import re
from datetime import timedelta

from modules.external_data.application.native_manual_json_parser import ManualParserLineage
from modules.external_data.application.native_parser_artifacts import VerifiedNativeParserArtifact
from modules.external_data.application.native_parser_output import (
    validate_native_manual_parser_output,
)
from modules.listing.application.native_intake_assisted_entry_history import (
    NativeAssistedEntryHistory,
)
from modules.listing.application.native_intake_assisted_entry_identity import (
    assisted_delivery_identity,
)
from modules.listing.application.native_intake_assisted_entry_request_proof import _object, _time
from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_parser_attempts import (
    NativeParserAttemptHistory,
    NativeParserPreparedRetry,
    active_payload_fields,
)
from modules.listing.application.native_intake_parser_input import NativeParserManualInput
from modules.listing.application.native_intake_parser_job_proof import (
    NativeParserJobProof,
    parser_admission_provenance,
    parser_delivery_identity,
)
from modules.listing.application.native_intake_parser_journal import INTENT_FIELDS as _INTENT_FIELDS
from modules.listing.application.native_intake_parser_journal import (
    closed_parser_attempt_journal,
    combine_closed_parser_journals,
)
from modules.listing.application.native_intake_parser_policy import JOB_TYPE, NativeParserConflict
from modules.listing.application.native_intake_parser_prerequisites import (
    NativeParserCommandBudget,
    NativeParserSQLClock,
)
from modules.listing.application.native_intake_parser_runs import CanonicalNativeParserRunRepository
from modules.listing.application.native_intake_retrieval import audited_value_matches
from modules.listing.application.native_intake_retrieval_policy import (
    canonical_json,
    canonical_sha256,
)
from shared.domain.events import validate_event
from shared.jobs.queue import JobStatus

_RESULT_FIELDS = {
    "job_id", "parser_run_id", "intake_id", "source_snapshot_id", "parser_release_id", "parser_status", "job_status",
    "processing_state", "version", "intake_version_before", "run_version", "settled_at", "transition_id",
    "audit_event_id", "parser_audit_event_id", "parser_completed_event_id", "outbox_event_id", "business_fence",
    "physical_fence", "worker", "attempt", "delivery_generation", "acceptance_audit_event_id", "output_sha256",
    "quality_summary", "journal_sha256", "intent_ids", "native_parser_provenance",
    "business_version", "physical_version", "physical_attempts", "transition_sequence",
}


def _reject():
    raise NativeParserConflict("NATIVE_PARSER_TERMINAL_PROOF_REJECTED") from None


def _audited_exact(actual, expected):
    # The shared retention verifier accepts Python equality. Canonical JSON
    # also binds the value types, so false cannot substitute for numeric zero.
    return (audited_value_matches(actual, expected)
        and all(canonical_json(actual[k]) == canonical_json(v) for k, v in expected.items()))


def _artifact(snapshot):
    """Rehydrate audited byte evidence; this is not a deployment capability."""
    value = VerifiedNativeParserArtifact(parser_release_id=snapshot["parser_release_id"],
        source_id=snapshot["source_id"], release_version=snapshot["release_version"],
        semantic_version=snapshot["semantic_version"], release_metadata_sha256=snapshot["release_metadata_sha256"],
        archive_uri=snapshot["artifact_uri"], archive_sha256=snapshot["artifact_sha256"],
        package_name=snapshot["package_name"], input_schema_version=snapshot["input_schema_version"],
        output_schema_version=snapshot["output_schema_version"], test_corpus_version=snapshot["test_corpus_version"],
        installed_sha256=tuple(sorted(snapshot["installed_sha256"].items())),
        actual_corpus_cases=snapshot["actual_corpus_cases"], corpus_result_sha256=snapshot["corpus_result_sha256"])
    if canonical_json(value.snapshot()) != canonical_json(snapshot):
        _reject()
    return value


class NativeParserTerminalHistory:
    def __init__(self, graph):
        self.graph, self.engine, self.queue = graph, graph.engine, graph.queue
        self.clock, self.jobs = NativeParserSQLClock(self.engine), NativeParserJobProof(graph)
        self.runs = CanonicalNativeParserRunRepository(self.engine)
        self.parent = NativeAssistedEntryHistory(graph)

    def _parent_input(self, tenant, binding, before):
        """Full genuine parent proof, with no fabricated historical Intake."""
        original = binding["original_input"]
        parent_job_id = native_intake_uuid(original["parent_job_id"])
        before()
        physical = self.queue.get(parent_job_id)
        if physical is None or physical.job_id != parent_job_id:
            _reject()
        actual_tenant, generation = assisted_delivery_identity(physical)
        if actual_tenant != tenant:
            _reject()
        proven = self.parent.committed_result_with_command(physical, tenant, generation, before=before)
        if proven is None:
            _reject()
        result, command, job = proven
        if job["job_id"] != parent_job_id or job["tenant_id"] != tenant:
            _reject()
        payload = _object(job["payload"])
        snapshot = _object(result["native_snapshot_proof"]["snapshot_fields"])
        ids = tuple(sorted((row["field_path"], ident)
            for ident, row in result["native_snapshot_proof"]["correction_bindings"].items()))
        lineage = ManualParserLineage(tenant_id=tenant, intake_id=result["intake_id"],
            request_id=payload["request_id"], requested_by=payload["requested_by"], source_id=snapshot["source_id"],
            content_sha256=snapshot["content_sha256"], fields_sha256=command.fields.sha256, correction_ids=ids)
        value = NativeParserManualInput(job["job_id"], result["outbox_event_id"], canonical_sha256(result),
            canonical_json(snapshot), lineage, command.model_dump_json())
        if (result["intake_id"] != binding["intake_id"] or result["version"] != binding["intake_version"]
                or canonical_json(value.binding()) != canonical_json(original)):
            _reject()
        before()
        return value

    def _journal(self, job, run, physical, payload, result, original, checked, at, before):
        """Verify committed execution facts, without pretending a lease is live."""
        binding, artifact = payload["admission"]["binding"], payload["admission"]["artifact"]
        prior = NativeParserAttemptHistory(self.graph).previous(job, run, physical, original, before=before)
        snapshot, intents = original.snapshot(), prior.current_items
        current = closed_parser_attempt_journal(run, payload["admission"], original, payload["acceptance_receipt"],
            intents, at, timeout_at=job["timeout_at"], before=before)
        combine_closed_parser_journals((*prior.journals, current), payload["attempt_intents"], before=before)
        if job["attempt"] > 1 and result.get("attempt_history_sha256") != prior.history_sha256:
            _reject()
        operations = ("gcs_bucket", "gcs_head", "gcs_download", "parser_child")
        ids = result["intent_ids"]
        # Every earlier accepted/retry record has just been proved in full.
        if (payload["delivery_generation"] != 1 or job["attempt"] < 1
                or not isinstance(intents, list) or len(intents) != 4
                or any(not isinstance(item, dict) or set(item) != _INTENT_FIELDS for item in intents)
                or not isinstance(ids, list) or len(ids) != len(set(ids)) or len(ids) != 4):
            _reject()
        if canonical_sha256(intents) != result["journal_sha256"] or [i["intent_id"] for i in intents] != ids:
            _reject()
        prefix = "gs://" + payload["admission"]["policy"]["storage"]["bucket"] + "/"
        if not snapshot["raw_object_uri"].startswith(prefix):
            _reject()
        key, reservations = snapshot["raw_object_uri"][len(prefix):], []
        for index, item in enumerate(intents):
            before.check_in_memory()
            native_intake_uuid(item["intent_id"])
            expected = {"operation": operations[index], "state": "FINISHED", "delivery_generation": 1,
                "physical_fence": result["physical_fence"], "business_fence": result["business_fence"],
                "attempt": result["attempt"], "worker": result["worker"], "parser_run_id": run["parser_run_id"],
                "source_snapshot_id": run["source_snapshot_id"], "original_input_sha256": original.sha256,
                "artifact_sha256": canonical_sha256(artifact), "binding_sha256": canonical_sha256(binding),
                "policy_sha256": binding["policy_sha256"], "source_version": binding["source_version"],
                "source_sha256": binding["source_sha256"], "source_valid_until": binding["source_valid_until"],
                "human_authority_sha256": binding["human_authority_sha256"],
                "process_state": "NO_CHILD_REMAINS", "error_class": None}
            if (canonical_json({k: item[k] for k in expected}) != canonical_json(expected)
                    or not _time(item["admitted_at"]) <= _time(item["finished_at"]) <= at
                    or (index and _time(intents[index-1]["finished_at"]) > _time(item["admitted_at"]))):
                _reject()
            arguments = {} if index == 0 else ({"key": key, "generation": snapshot["object_generation"]} if index < 3
                else {"document_sha256": original.lineage.content_sha256, "byte_length": snapshot["byte_length"],
                    "artifact_sha256": checked.artifact_sha256, "output_schema_version": artifact["output_schema_version"]})
            if canonical_json(item["arguments"]) != canonical_json(arguments):
                _reject()
            observed = item["observed"]
            if index in {0, 1}:
                expected_keys = {"sha256"} if index == 0 else {"sha256", "generation", "byte_length"}
                if (not isinstance(observed, dict) or set(observed) != expected_keys
                        or not isinstance(observed["sha256"], str)
                        or re.fullmatch(r"[0-9a-f]{64}", observed["sha256"]) is None
                        or (index == 1 and (observed["generation"] != snapshot["object_generation"]
                            or observed["byte_length"] != snapshot["byte_length"]))):
                    _reject()
            if index == 2 and observed != {"sha256": snapshot["content_sha256"],
                    "byte_length": snapshot["byte_length"], "generation": snapshot["object_generation"]}:
                _reject()
            if index == 3 and observed != {"sha256": checked.output_sha256,
                    "artifact_sha256": checked.artifact_sha256, "status": checked.status}:
                _reject()
            reservation = item["reservation"]
            if (not isinstance(reservation, dict)
                    or set(reservation) != {"reservation_id", "rate_reservation_id", "source_id", "expires_at"}
                    or reservation["rate_reservation_id"] is not None
                    or reservation["source_id"] != snapshot["source_id"]
                    or _time(reservation["expires_at"]) <= _time(item["admitted_at"])):
                _reject()
            reservations.append(native_intake_uuid(reservation["reservation_id"]))
        if len(set(reservations)) != 4:
            _reject()
        before()
        if self.engine.query_one("SELECT reservation_id FROM intake.native_retrieval_inflight_slots "
                "WHERE reservation_id=ANY(?::uuid[]) LIMIT 1", (reservations,)) is not None:
            _reject()

    def _event(self, job, ident, at, before, **expected):
        before()
        row = self.graph._one("SELECT * FROM workflow.outbox_events WHERE tenant_id=? AND event_id=?",
            (job["tenant_id"], ident))
        event = _object(row["payload"])
        sensitive = row["sensitive_fields"]
        # PostgresEngine serializes both JSON values and SQL arrays. Compare
        # the decoded array with the exact envelope declaration.
        sensitive = json.loads(sensitive) if isinstance(sensitive, str) else sensitive
        common = {"event_id": ident, "event_version": 1, "tenant_id": job["tenant_id"],
            "correlation_id": job["correlation_id"], "actor_ref": JOB_TYPE, "sensitive_fields": []}
        expected = {**common, **expected}
        if (validate_event(event) or any(canonical_json(event.get(k)) != canonical_json(v) for k, v in expected.items())
                or any(row[k] != expected[k] for k in ("event_id", "event_type", "event_version", "tenant_id",
                    "aggregate_type", "aggregate_id", "aggregate_version", "partition_key", "correlation_id", "causation_id"))
                or sensitive != expected["sensitive_fields"]
                or type(event.get("aggregate_version")) is not int or type(event.get("event_version")) is not int
                or _time(event["occurred_at"]) != at or _time(row["occurred_at"]) != at
                or _time(row["retention_until"]) != at + timedelta(days=30)):
            _reject()

    def read(self, delivery, tenant, generation, *, before):
        """Prove a positive/negative closure or return a genuine open state."""
        # A caller's missing/foreign root remains a root contract error.
        # Only failures inside the canonical data proof are normalized.
        self.jobs._root(tenant, before)
        return self._read_proof(delivery, tenant, generation, before=before)

    def read_positive_with_parent_input(self, delivery, tenant, generation, *, before):
        """Return the actual parent input proved in this same positive read.

        No proof, source/human authority or original budget is reused by a
        later invocation. Negative/open closures retain their existing read
        API and cannot supply a positive matching input through this method.
        """
        self.jobs._root(tenant, before)
        proven = self._read_proof(delivery, tenant, generation, before=before, with_parent_input=True)
        if (type(proven) is not tuple or len(proven) != 2
                or type(proven[0]) is not dict or type(proven[1]) is not NativeParserManualInput):
            _reject()
        before.check_in_memory()
        return proven

    def _read_proof(self, delivery, tenant, generation, *, before, with_parent_input=False):
        try:
            before()
            job = self.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                (tenant, delivery.job_id))
            payload = _object(job["payload"])
            run = self.runs.get(tenant, job["aggregate_id"], before=before)
            self.jobs.admission(job, run, before=before)
            before()
            physical = self.queue.get(job["job_id"])
            if (physical is None or physical.payload != delivery.payload
                    or parser_delivery_identity(physical) != (tenant, generation)):
                _reject()
            if job["status"] == "RETRYING":
                from modules.listing.application.native_intake_parser_retry import (
                    read_prepared_parser_retry,
                )

                return read_prepared_parser_retry(self, job, run, physical, before=before)
            if job["status"] == "CANCELLED":
                from modules.listing.application.native_intake_parser_cancellation import (
                    read_cancelled_unaccepted_parser,
                )

                return read_cancelled_unaccepted_parser(self, job, run, physical, tenant, generation, before=before)
            if job["status"] in {"QUEUED", "RUNNING"}:
                # Report only a genuine unfinished command. Expired
                # leases are history, not a present ownership claim.
                if (run["status"] != job["status"] or job["result"] is not None
                        or any(k in payload for k in ("terminal_receipt", "negative_receipt", "permanent_failure_receipt")) or generation != 1
                        or job["checkpoint"] != "PARSING" or run["completed_at"] is not None
                        or run["parsed_payload"] is not None or run["normalized_payload"] is not None
                        or _object(run["field_confidence"]) != {}
                        or (json.loads(run["validation_errors"]) if isinstance(run["validation_errors"], str)
                            else run["validation_errors"]) != []):
                    _reject()
                if job["status"] == "RUNNING":
                    accepted = self.jobs.acceptance(job, run, before=before)
                    if (set(payload) != active_payload_fields(job["attempt"])
                            or job["attempt"] < 1 or physical.status != JobStatus.RUNNING
                            or physical.locked_by != accepted["worker"]
                            or physical.fence_token != accepted["physical_fence"]
                            or run["version"] != accepted["run_version_after"]):
                        _reject()
                    if job["attempt"] > 1:
                        original = self._parent_input(tenant, payload["admission"]["binding"], before)
                        NativeParserAttemptHistory(self.graph).previous(job, run, physical, original, before=before)
                elif (job["attempt"] != 0 or job["fence_token"] != 1 or run["version"] != 1
                        or set(payload) != {"admission", "delivery_generation", "attempt_token_sha256", "attempt_intents"}
                        or run["started_at"] is not None or job["lease_expires_at"] is not None
                        or job["next_attempt_at"] is not None
                        or any(key in payload for key in ("acceptance_receipt", "accepted_binding", "retry_receipt"))
                        or physical.status not in {JobStatus.QUEUED, JobStatus.RUNNING}):
                    _reject()
                before.check_in_memory()
                return None
            if job["status"] == "FAILED":
                if "permanent_failure_receipt" in payload:
                    from modules.listing.application.native_intake_parser_permanent import (
                        read_permanent_terminal,
                    )

                    return read_permanent_terminal(self, job, run, physical, tenant, generation, before=before)
                from modules.listing.application.native_intake_parser_negative import (
                    read_unavailable_terminal,
                )

                return read_unavailable_terminal(self, job, run, physical, tenant, generation, before=before)
            acceptance = self.jobs.acceptance(job, run, before=before)
            result = _object(job["result"])
            binding = payload["admission"]["binding"]
            expected_result_fields = _RESULT_FIELDS | ({"attempt_history_sha256"} if job["attempt"] > 1 else set())
            if (set(result) != expected_result_fields or canonical_json(payload.get("terminal_receipt")) != canonical_json(result)
                    or set(payload) != active_payload_fields(job["attempt"]) | {"terminal_receipt"}
                    or job["last_error"] is not None
                    or job["status"] != "SUCCEEDED" or run["status"] not in {"SUCCEEDED", "PARTIAL"}
                    or physical.status != JobStatus.SUCCEEDED or physical.delivery_state is not None
                    or physical.error_message is not None
                    or any(getattr(physical, k) is not None for k in ("locked_by", "heartbeat_at", "lease_expires_at", "leased_until"))
                    or job["lease_expires_at"] is not None or job["next_attempt_at"] is not None
                    or job["cancellation_requested_at"] is not None or job["checkpoint"] != "PARSING"
                    or physical.fence_token != acceptance["physical_fence"]
                    or not job["attempt"] <= physical.attempts <= physical.max_retries
                    or result["job_status"] != "SUCCEEDED" or result["parser_status"] != run["status"]
                    or result["job_id"] != job["job_id"] or result["parser_run_id"] != run["parser_run_id"]
                    or any(result[k] != run[k] for k in ("intake_id", "source_snapshot_id", "parser_release_id"))
                    or result["intake_version_before"] != binding["intake_version"]
                    or result["version"] != binding["intake_version"] + 1
                    or any(type(result[k]) is not int or result[k] < 1 for k in (
                        "version", "intake_version_before", "run_version", "business_version",
                        "physical_version", "physical_attempts", "transition_sequence", "physical_fence", "business_fence",
                        "attempt", "delivery_generation"))
                    or result["business_version"] != job["version"]
                    or result["physical_version"] != physical.version
                    or result["physical_attempts"] != physical.attempts
                    or result["run_version"] != run["version"] or run["version"] != acceptance["run_version_after"] + 1
                    or result["acceptance_audit_event_id"] != acceptance["audit_event_id"]
                    or any(result[k] != acceptance[k] for k in ("physical_fence", "business_fence", "worker", "attempt", "delivery_generation"))
                    or result["delivery_generation"] != generation
                    or result["native_parser_provenance"] != parser_admission_provenance(payload)):
                _reject()
            at = _time(result["settled_at"])
            if (not _time(acceptance["accepted_at"]) <= at < _time(job["timeout_at"])
                    or at >= _time(binding["source_valid_until"])
                    or _time(run["started_at"]) > _time(acceptance["accepted_at"])
                    or _time(run["completed_at"]) != at or _time(job["updated_at"]) != at
                    or _time(job["heartbeat_at"]) != at):
                _reject()
            original = self._parent_input(tenant, binding, before)
            artifact = _artifact(payload["admission"]["artifact"])
            facts = {k: json.loads(run[k]) if isinstance(run[k], str) else run[k]
                for k in ("parsed_payload", "normalized_payload", "field_confidence", "validation_errors")}
            checked = validate_native_manual_parser_output(canonical_json(facts).encode(), artifact=artifact,
                lineage=original.lineage, original_fields=original.command().fields.values())
            if (checked.status != run["status"] or checked.output_sha256 != result["output_sha256"]
                    or canonical_json(checked.quality_summary()) != canonical_json(result["quality_summary"])):
                _reject()
            self._journal(job, run, physical, payload, result, original, checked, at, before)
            target = "MATCHING" if run["status"] == "SUCCEEDED" else "NEEDS_REVIEW"
            action = "intake.matching_started.v1" if target == "MATCHING" else "intake.review_required.v1"
            permission = "parser.execute" if target == "MATCHING" else "intake.route_review"
            if result["processing_state"] != target:
                _reject()
            before()
            transition = self.engine.query_one("SELECT * FROM intake.intake_stage_transitions WHERE tenant_id=? "
                "AND transition_id=? AND intake_id=? AND job_id=? AND from_state='PARSING' AND to_state=? "
                "AND actor_subject_id IS NULL AND service_principal=? AND permission=? AND expected_version=? "
                "AND resulting_version=? AND idempotency_key=? AND source_snapshot_id=? AND source_policy_version=? "
                "AND correlation_id=? AND causation_id=?", (tenant, result["transition_id"], run["intake_id"],
                    job["job_id"], target, JOB_TYPE, permission, result["intake_version_before"], result["version"],
                    run["parser_run_id"], run["source_snapshot_id"], binding["source_version"], job["correlation_id"],
                    result["parser_completed_event_id"]))
            if (transition is None or _time(transition["occurred_at"]) != at
                    or transition["sequence_no"] != result["transition_sequence"]
                    or transition["reason_code"] != ("PARSER_VALIDATED_OUTPUT" if target == "MATCHING" else "PARSER_PARTIAL_OUTPUT")
                    or transition["reason_text"] != "Route the actual validated parser output."):
                _reject()
            for audit_id, audit_action, previous in (
                    (result["parser_audit_event_id"], "parser.run_completed.v1", {"parser_run_id": run["parser_run_id"], "status": "RUNNING"}),
                    (result["audit_event_id"], action, {"processing_state": "PARSING", "version": result["intake_version_before"]})):
                before()
                audit = self.engine.query_one("SELECT * FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=? "
                    "AND resource_type='intake' AND resource_id=? AND actor_subject_id IS NULL AND service_principal=? "
                    "AND actor_role='SVC_PARSER' AND action=? AND result='SUCCEEDED' AND causation_id=? AND correlation_id=?",
                    (tenant, audit_id, run["intake_id"], JOB_TYPE, audit_action, original.parent_event_id, job["correlation_id"]))
                if (audit is None or _object(audit["before_value"]) != previous
                        or not _audited_exact(_object(audit["after_value"]), result)
                        or _time(audit["occurred_at"]) != at
                        or audit["reason"] != "Commit the actual parser output and route its measured outcome."):
                    _reject()
            partition = tenant + ":" + run["intake_id"]
            self._event(job, result["parser_completed_event_id"], at, before, event_type="parser.run_completed",
                producer="parser_service", aggregate_type="parser_run", aggregate_id=run["parser_run_id"],
                aggregate_version=run["version"], partition_key=partition, causation_id=original.parent_event_id,
                schema_ref="#/payloads/ParserRunCompletedV1", payload={"parser_run_id": run["parser_run_id"],
                    "intake_id": run["intake_id"], "parser_release_id": run["parser_release_id"], "status": checked.status,
                    "quality_summary": checked.quality_summary(), "output_schema_version": artifact.output_schema_version})
            self._event(job, result["outbox_event_id"], at, before, event_type="intake.state_changed",
                producer="listing_intake_service", aggregate_type="intake", aggregate_id=run["intake_id"],
                aggregate_version=result["version"], partition_key=partition,
                causation_id=result["parser_completed_event_id"], schema_ref="#/payloads/IntakeStateChangedV1",
                payload={"intake_id": run["intake_id"], "from_state": "PARSING", "to_state": target,
                    "transition_id": result["transition_id"], "version": result["version"], "occurred_at": at.isoformat()})
            before.check_in_memory()
            return (result, original) if with_parent_input else result
        except NativeParserConflict as error:
            if str(error) == "NATIVE_PARSER_ROOT_DEADLINE_EXPIRED":
                raise
            _reject()
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
            _reject()

    def replay(self, delivery):
        from modules.listing.application.native_intake_parser_negative import (
            NativeParserNegativeClosure,
        )

        tenant, generation = parser_delivery_identity(delivery)
        with self.graph.transaction(tenant):
            result = self.read(delivery, tenant, generation, before=NativeParserCommandBudget(self.clock))
            if isinstance(result, NativeParserPreparedRetry):
                return {"terminal": False, "result": None, "retry_prepared": True,
                    "retry_receipt": json.loads(result.receipt_json)}
            if isinstance(result, NativeParserNegativeClosure):
                return {"terminal": True, "result": None}
            return {"terminal": result is not None, "result": result}
