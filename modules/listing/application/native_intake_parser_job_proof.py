"""Parser admission provenance and routing, without restoring current grants.

These reads establish the original reservation only. Execution, accepted
attempts, retries and terminal results need their own current ownership proof.
"""
from __future__ import annotations

import hashlib
import re
from datetime import timedelta
from uuid import UUID, uuid5

from modules.listing.application.native_intake_assisted_entry_request_proof import _object, _time
from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_parser_policy import (
    JOB_TYPE,
    MAX_ATTEMPTS,
    MAX_COMMAND_SECONDS,
    NativeParserConflict,
)
from modules.listing.application.native_intake_parser_prerequisites import (
    NativeParserCommandBudget,
    native_parser_run_id,
)
from modules.listing.application.native_intake_retrieval import audited_value_matches
from modules.listing.application.native_intake_retrieval_policy import canonical_sha256
from shared.jobs.queue import JobFenceRejectedError

_SHA = re.compile(r"[0-9a-f]{64}\Z")


def parser_delivery_identity(delivery):
    """Closed routing data only; a token's syntax is never authorization."""
    try:
        data = delivery.payload
        if (delivery.job_type != JOB_TYPE or not isinstance(data, dict)
                or set(data) != {"job_id", "tenant_id", "attempt_token"}
                or data["job_id"] != delivery.job_id or not isinstance(data["attempt_token"], str)
                or re.fullmatch(r"[1-9][0-9]{0,18}\.[0-9a-f]{64}", data["attempt_token"]) is None):
            raise ValueError
        native_intake_uuid(delivery.job_id)
        tenant = native_intake_uuid(data["tenant_id"])
        generation = int(data["attempt_token"].split(".", 1)[0])
        if generation > 2**63 - 1:
            raise ValueError
        return tenant, generation
    except (ValueError, TypeError, AttributeError, KeyError):
        raise JobFenceRejectedError("NATIVE_PARSER_ROUTING_IDENTITY_REJECTED") from None


def parser_admission_provenance(payload):
    return {"admission_sha256": canonical_sha256(payload["admission"])}


def _reject():
    raise NativeParserConflict("NATIVE_PARSER_ADMISSION_PROOF_REJECTED") from None


class NativeParserJobProof:
    def __init__(self, graph):
        self.graph, self.engine, self.queue = graph, graph.engine, graph.queue

    def _root(self, tenant, before):
        tenant = native_intake_uuid(tenant)
        if (not isinstance(before, NativeParserCommandBudget) or before.source.engine is not self.engine
                or getattr(self.engine, "dialect", None) != "postgresql"
                or getattr(getattr(self.engine, "_local", None), "connection", None) is None):
            _reject()
        before()
        root = self.engine.query_one("SELECT current_setting('transaction_isolation') AS isolation, "
            "current_setting('app.tenant_id',true) AS tenant")
        if root["isolation"] != "serializable" or root["tenant"] != tenant:
            _reject()
        before.check_in_memory()
        return tenant

    def admission(self, job, run, *, before):
        """Check actual canonical rows, delivery and original scoped audit.

        No environment, current key, role or source approval is consulted.
        The caller owns a bounded tenant SQL root and locks rows in order.
        """
        try:
            tenant = self._root(job["tenant_id"], before)
            payload = _object(job["payload"])
            original = _object(payload["admission"])
            receipt = _object(original["receipt"])
            binding = _object(original["binding"])
            artifact = _object(original["artifact"])
            policy = _object(original["policy"])
            run_id = native_parser_run_id(tenant, run["source_snapshot_id"], run["parser_release_id"])
            job_id = str(uuid5(UUID(run_id), JOB_TYPE))
            for key in ("intake_id", "source_snapshot_id", "parser_release_id", "correlation_id"):
                native_intake_uuid(run[key])
            if (set(original) != {"binding", "artifact", "policy", "admitted_at", "timeout_at",
                    "initial_attempt_token_sha256", "receipt"}
                    or set(receipt) != {"job_id", "parser_run_id", "intake_id", "source_snapshot_id",
                        "parser_release_id", "admitted_intake_version", "job_status_at_admission",
                        "timeout_at", "audit_event_id"}
                    or job["job_id"] != job_id or run["parser_run_id"] != run_id
                    or run["tenant_id"] != tenant or job["job_type"] != JOB_TYPE
                    or job["aggregate_type"] != "parser_run" or job["aggregate_id"] != run_id
                    or job["idempotency_key"] != run_id or job["correlation_id"] != run["correlation_id"]
                    or receipt["job_id"] != job_id or receipt["parser_run_id"] != run_id
                    or any(receipt[k] != run[k] for k in ("intake_id", "source_snapshot_id", "parser_release_id"))
                    or receipt["job_status_at_admission"] != "QUEUED"
                    or type(receipt["admitted_intake_version"]) is not int or receipt["admitted_intake_version"] < 2
                    or binding["intake_version"] != receipt["admitted_intake_version"]
                    or any(binding[k] != v for k, v in {
                        "tenant_id": tenant, "intake_id": run["intake_id"],
                        "parser_run_id": run_id, "job_id": job_id}.items())
                    or binding["original_input"]["source_snapshot_id"] != run["source_snapshot_id"]
                    or artifact["parser_release_id"] != run["parser_release_id"]
                    or artifact["source_id"] != binding["original_input"]["lineage"]["source_id"]
                    or binding["original_input"]["lineage"]["tenant_id"] != tenant
                    or binding["original_input"]["lineage"]["intake_id"] != run["intake_id"]
                    or canonical_sha256(policy) != binding["policy_sha256"]
                    or canonical_sha256(artifact) != binding["artifact_sha256"]
                    or policy["tenant_id"] != tenant or type(policy["max_attempts"]) is not int
                    or not 1 <= policy["max_attempts"] <= MAX_ATTEMPTS
                    or job["max_attempts"] != policy["max_attempts"]
                    or type(policy["deadline_seconds"]) is not int
                    or not 1 <= policy["deadline_seconds"] <= MAX_COMMAND_SECONDS
                    or _time(original["timeout_at"]) != _time(receipt["timeout_at"])
                    or _time(original["timeout_at"]) != _time(job["timeout_at"])
                    or _time(original["timeout_at"]) - _time(original["admitted_at"]) != timedelta(
                        seconds=policy["deadline_seconds"])
                    or _time(original["admitted_at"]) != _time(run["created_at"])
                    or not isinstance(original["initial_attempt_token_sha256"], str)
                    or not _SHA.fullmatch(original["initial_attempt_token_sha256"])):
                _reject()
            native_intake_uuid(receipt["audit_event_id"])
            before()
            physical = self.queue.get(job_id)
            if physical is None:
                _reject()
            actual_tenant, generation = parser_delivery_identity(physical)
            token_hash = hashlib.sha256(physical.payload["attempt_token"].encode()).hexdigest()
            if (actual_tenant != tenant or type(payload["delivery_generation"]) is not int
                    or generation != payload["delivery_generation"]
                    or token_hash != payload["attempt_token_sha256"]
                    or (generation == 1 and token_hash != original["initial_attempt_token_sha256"])
                    or physical.correlation_id != job["correlation_id"]
                    or physical.idempotency_key != JOB_TYPE + ":" + tenant + ":" + run_id
                    or physical.max_retries != job["max_attempts"]):
                _reject()
            before()
            audit = self.engine.query_one("SELECT after_value,before_value,occurred_at FROM audit.audit_events "
                "WHERE tenant_id=? AND audit_event_id=? AND resource_type='intake' AND resource_id=? "
                "AND actor_subject_id IS NULL AND service_principal=? AND actor_role='SVC_PARSER' "
                "AND action='intake.parser_admitted.v1' AND result='SUCCEEDED' "
                "AND causation_id=? AND correlation_id=?", (tenant, receipt["audit_event_id"],
                    run["intake_id"], JOB_TYPE, binding["original_input"]["parent_event_id"], job["correlation_id"]))
            if (audit is None or _object(audit["before_value"]) != {
                    "processing_state": "PARSING", "version": receipt["admitted_intake_version"]}
                    or not audited_value_matches(_object(audit["after_value"]),
                        {**receipt, "native_parser_admission": original,
                            "native_parser_provenance": parser_admission_provenance(payload)})
                    or _time(audit["occurred_at"]) != _time(original["admitted_at"])):
                _reject()
            before.check_in_memory()
            return receipt
        except NativeParserConflict:
            raise
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
            _reject()

    def acceptance(self, job, run, *, before):
        """Authenticate this actual accepted attempt, without a current IO grant."""
        from modules.listing.application.native_intake_parser_attempts import (
            NativeParserAttemptHistory,
        )

        try:
            self._root(job["tenant_id"], before)
            return NativeParserAttemptHistory(self.graph).current_acceptance(job, run, before=before)
        except NativeParserConflict:
            raise
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
            _reject()
