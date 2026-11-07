"""Private facts for one live parser operation and one pending finish.

No serialized journal, digest, exception or caller boolean issues a completion
capability. The IO instance captures its actual acknowledged intent, binds the
same live runner observation before invoking IO, and consumes that observation
once after the invocation unwinds. These facts grant neither SQL ownership nor
permission to use output. The finish owner must independently re-read all
durable provenance and ownership; a positive continuation needs fresh grants.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass

from modules.external_data.application.native_intake_process import NativeIOUnreaped
from modules.listing.application.native_intake_assisted_entry_request_proof import _object, _time
from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_parser_failure_policy import (
    parser_failure_observation,
)
from modules.listing.application.native_intake_parser_journal import OPERATIONS
from modules.listing.application.native_intake_parser_policy import NativeParserConflict
from modules.listing.application.native_intake_retrieval_policy import (
    canonical_json,
    canonical_sha256,
)
from shared.jobs.queue import JobStatus

_ISSUER = object()


def _reject():
    raise NativeParserConflict("NATIVE_PARSER_LIVE_COMPLETION_REQUIRED") from None


class _PrivateCompletionHandle:
    __slots__ = ()

    def __new__(cls, issuer=None):
        if issuer is not _ISSUER:
            _reject()
        return super().__new__(cls)

    def __reduce_ex__(self, protocol):
        raise TypeError("NATIVE_PARSER_COMPLETION_NOT_SERIALIZABLE")


class _LiveParserOperation(_PrivateCompletionHandle):
    __slots__ = ()


class _PendingParserFinish(_PrivateCompletionHandle):
    __slots__ = ()


def _run_identity(run):
    value = {key: run[key] for key in ("tenant_id", "parser_run_id", "intake_id", "source_snapshot_id",
        "parser_release_id", "correlation_id", "status", "version", "parsed_payload", "normalized_payload")}
    for key in ("created_at", "started_at", "completed_at"):
        value[key] = None if run[key] is None else _time(run[key]).isoformat()
    value["field_confidence"] = _object(run["field_confidence"])
    errors = run["validation_errors"]
    value["validation_errors"] = json.loads(errors) if isinstance(errors, str) else errors
    return value


def _job_identity(job):
    value = {key: job[key] for key in ("tenant_id", "job_id", "job_type", "aggregate_type", "aggregate_id",
        "checkpoint", "attempt", "max_attempts", "fence_token", "correlation_id", "idempotency_key")}
    value["timeout_at"] = _time(job["timeout_at"]).isoformat()
    return value


def _physical_identity(physical):
    return {"job_id": physical.job_id, "job_type": physical.job_type, "worker": physical.locked_by,
        "fence_token": physical.fence_token, "attempts": physical.attempts, "max_retries": physical.max_retries,
        "correlation_id": physical.correlation_id, "idempotency_key": physical.idempotency_key,
        "payload_sha256": canonical_sha256(physical.payload)}


@dataclass
class _FinishRecord:
    facts_json: str
    intent_json: str
    payload_json: str
    phase: str = "OPEN"
    observation: object = None
    outcome_json: str | None = None
    outcome_sha256: str | None = None
    expected_json: str | None = None


class _ParserOperationFinishes:
    """Issuer owned by one IO instance, bounded to its four operations.

    Registry membership and object identity prevent reconstruction through an
    application API. This is not a sandbox against arbitrary Python executing
    inside the trusted process. No authority context is retained in records.
    """

    def __init__(self, io):
        self.io, self.owner, self.engine, self.leases = io, io.owner, io.owner.engine, io.leases
        self._issuing_io = io
        self.delivery = io.delivery
        self.thread = threading.get_ident()
        self._live = {}
        self._pending = {}
        self._seen_intents = set()

    def _scope(self, *, outside_root=False):
        if (threading.get_ident() != self.thread or self.io is not self._issuing_io
                or self.io.owner is not self.owner
                or self.io.owner.engine is not self.engine or self.io.leases is not self.leases
                or self.io.delivery is not self.delivery
                or (outside_root and getattr(getattr(self.engine, "_local", None), "connection", None) is not None)):
            _reject()

    def open(self, intent, state, payload):
        """Called only after the actual intent transaction acknowledged exit."""
        from modules.listing.application.native_intake_parser_execution import (
            NativeParserExecutionState,
        )

        self._scope(outside_root=True)
        if type(state) is not NativeParserExecutionState:
            _reject()
        if type(intent) is not dict:
            _reject()
        integers = (state.job["attempt"], state.job["max_attempts"], state.job["fence_token"], state.run["version"],
            state.physical.fence_token, state.physical.attempts, state.physical.max_retries,
            self.delivery.fence_token, self.leases.business_fence, self.io.generation,
            intent.get("delivery_generation"), intent.get("source_version"), payload.get("delivery_generation"))
        if (len(self._live) >= len(OPERATIONS)
                or any(type(value) is not int or not 1 <= value <= 2**63 - 1 for value in integers)
                or intent.get("operation") not in OPERATIONS
                or intent.get("state") != "ADMITTED" or state.job["status"] != "RUNNING"
                or state.run["status"] != "RUNNING" or state.physical.status != JobStatus.RUNNING
                or not payload["attempt_intents"]
                or canonical_json(payload["attempt_intents"][-1]) != canonical_json(intent)
                or canonical_json(payload["admission"]) != canonical_json(state.payload["admission"])
                or type(intent.get("attempt")) is not int or intent["attempt"] != state.job["attempt"]
                or type(intent.get("business_fence")) is not int
                or intent["business_fence"] != self.leases.business_fence
                or intent["business_fence"] != state.job["fence_token"]
                or type(intent.get("physical_fence")) is not int
                or intent["physical_fence"] != self.delivery.fence_token
                or intent["worker"] != self.delivery.locked_by
                or canonical_json(_physical_identity(state.physical)) != canonical_json(_physical_identity(self.delivery))
                or state.job["job_id"] != self.delivery.job_id
                or state.job["job_type"] != self.delivery.job_type
                or state.job["correlation_id"] != self.delivery.correlation_id
                or state.job["tenant_id"] != self.io.tenant or state.run["tenant_id"] != self.io.tenant
                or state.job["aggregate_id"] != state.run["parser_run_id"]
                or intent["parser_run_id"] != state.run["parser_run_id"]
                or intent["source_snapshot_id"] != state.run["source_snapshot_id"]
                or intent["delivery_generation"] != self.io.generation
                or payload["delivery_generation"] != self.io.generation):
            _reject()
        ident = native_intake_uuid(intent["intent_id"])
        if ident in self._seen_intents:
            _reject()
        facts = {"job": _job_identity(state.job), "run": _run_identity(state.run),
            "physical": _physical_identity(state.physical),
            "original_input": state.context.original.binding()}
        token = _LiveParserOperation(_ISSUER)
        self._live[token] = _FinishRecord(canonical_json(facts), canonical_json(intent), canonical_json(payload))
        self._seen_intents.add(ident)
        return token

    def attach(self, token, observation):
        """Bind the exact observation before this invocation enters its adapter."""
        self._scope(outside_root=True)
        if type(token) is not _LiveParserOperation or token not in self._live or observation is None:
            _reject()
        record = self._live[token]
        if record.phase != "OPEN" or record.observation is not None:
            _reject()
        record.observation = observation
        record.phase = "INVOKING"

    def observed(self, token, observation, *, observed=None, error=None):
        """Consume a real completed runner fact, never infer one from a result."""
        from modules.external_data.application.native_intake_process_lifecycle import (
            _consume_native_process_completion,
        )

        self._scope(outside_root=True)
        if type(token) is not _LiveParserOperation or token not in self._live:
            _reject()
        record = self._live[token]
        if (record.phase != "INVOKING" or record.observation is not observation
                or isinstance(error, NativeIOUnreaped)
                or ((observed is None) == (error is None))
                or (observed is not None and type(observed) is not dict)
                or (error is not None and not isinstance(error, BaseException))):
            _reject()
        # Consume before any outcome encoding. A failed encoding cannot reuse
        # the same runner receipt to create a second pending action.
        record.phase = "CONSUMING"
        try:
            fact = _consume_native_process_completion(observation)
        finally:
            record.observation = None
        return self._pending_outcome(record, fact, observed=observed, error=error)

    def not_invoked(self, token, error):
        """Owned control flow failed before attach/entry into any adapter.

        Once attach has entered INVOKING, even zero observed kernel runs may
        not use this path. There is no caller-selected reaping boolean.
        """
        self._scope(outside_root=True)
        if type(token) is not _LiveParserOperation or token not in self._live:
            _reject()
        record = self._live[token]
        if (record.phase != "OPEN" or record.observation is not None
                or not isinstance(error, BaseException) or isinstance(error, NativeIOUnreaped)):
            _reject()
        record.phase = "CONSUMING"
        return self._pending_outcome(record, "NEVER_LAUNCHED", error=error)

    def _pending_outcome(self, record, fact, *, observed=None, error=None):
        if record.phase != "CONSUMING":
            _reject()
        if type(fact) is not str or fact not in {"REAPED", "NEVER_LAUNCHED"} or (error is None and fact != "REAPED"):
            _reject()
        name = None
        failure = None
        if error is not None:
            try:
                name = type(error).__name__
            except BaseException:
                name = "NativeParserOperationError"
            if type(name) is not str or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,95}", name) is None:
                name = "NativeParserOperationError"
            failure = parser_failure_observation(error)
        outcome = {"state": "FINISHED" if error is None else "ERROR", "observed": observed,
            "error_class": name, "process_state": "NO_CHILD_REMAINS"}
        if failure is not None:
            outcome["failure_observation"] = failure
        encoded = canonical_json(outcome)
        record.outcome_json = encoded
        record.outcome_sha256 = canonical_sha256({"domain": "native-parser-live-completion-v1",
            "intent_sha256": canonical_sha256(json.loads(record.intent_json)), "lifecycle_fact": fact,
            "outcome": json.loads(encoded)})
        record.phase = "PENDING"
        pending = _PendingParserFinish(_ISSUER)
        self._pending[pending] = record
        return pending

    def _record(self, pending):
        self._scope()
        if type(pending) is not _PendingParserFinish or pending not in self._pending:
            _reject()
        record = self._pending[pending]
        if record.phase != "PENDING" or record.outcome_json is None or record.outcome_sha256 is None:
            _reject()
        return record

    def facts(self, pending):
        """Fresh copies of immutable facts; none is a current authority grant."""
        record = self._record(pending)
        return json.loads(record.facts_json), json.loads(record.intent_json), json.loads(record.payload_json)

    def seal(self, pending, at):
        """Sample once in a real finish root, then reuse the exact entry."""
        record = self._record(pending)
        if getattr(getattr(self.engine, "_local", None), "connection", None) is None:
            _reject()
        if record.expected_json is None:
            intent = json.loads(record.intent_json)
            stamp = _time(at)
            if stamp < _time(intent["admitted_at"]):
                _reject()
            record.expected_json = canonical_json({**intent, **json.loads(record.outcome_json),
                "finished_at": stamp.isoformat()})
        return json.loads(record.expected_json)

    def expected(self, pending):
        record = self._record(pending)
        return None if record.expected_json is None else json.loads(record.expected_json)

    def confirm(self, pending):
        """A caller may confirm only after the separate finish root exits."""
        self._scope(outside_root=True)
        record = self._record(pending)
        if record.expected_json is None:
            _reject()
        record.phase = "CONFIRMED"
        return json.loads(record.expected_json)

    def unresolved(self, pending):
        self._scope(outside_root=True)
        record = self._record(pending)
        record.phase = "UNRESOLVED"
