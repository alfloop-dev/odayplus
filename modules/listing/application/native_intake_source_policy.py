"""Evaluate current native source policy from a proven identity-stage result.

Three declared exits commit with their audit, state event and both jobs.
A decision receipt is historical evidence, not a later retrieval capability.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4, uuid5

from modules.listing.application.native_intake_identity import NativeIntakeIdentityOwner
from modules.listing.application.native_intake_job_runtime import (
    decode_native_intake_attempt,
    load_native_intake_policy,
    set_native_intake_sql_budget,
    sign_native_intake_attempt,
)
from modules.listing.application.native_intake_source_policy_rules import (
    POLICY_RULE_SOURCE_SHA256,
    POLICY_RULE_VERSION,
    NativeSourcePolicyRuleUnavailable,
    evaluate_native_source_policy,
    source_policy_snapshot_json,
)
from modules.listing.application.native_intake_url_identity import (
    NATIVE_URL_RULE_SOURCE_SHA256,
    NATIVE_URL_RULE_VERSION,
    normalize_native_intake_url,
)
from modules.listing.domain.intake_states import (
    Actor,
    IntakeAggregate,
    IntakeStage,
    IntakeStateMachine,
    PrincipalRole,
    TransitionContext,
)
from shared.audit.errors import CanonicalAuditUnavailable
from shared.domain.events import DomainEvent, validate_event
from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError, JobRequest, JobStatus

NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE = "native-intake-source-policy"
POLICY_ENV = "ODP_NATIVE_INTAKE_SOURCE_POLICY_POLICY_JSON"
SIGNING_KEY_ENV = "ODP_NATIVE_INTAKE_SOURCE_POLICY_SIGNING_KEY"
# Conservatively use the reliability contract's 15s policy-stage limit for
# the whole admitted job, including retries. The actual physical lease still
# bounds every command and protective settlement independently.
# With the contract's 5/30/120s backoff, this total budget permits at most
# two attempts, not all three. Reconciling that contract row remains OPEN
# for the named SLO owner; this conservative cap does not approve rollout.
MAX_COMMAND_SECONDS = 15
ADMISSION_BUDGET_MILLISECONDS = 750


class NativeIntakeSourcePolicyUnavailable(RuntimeError):
    pass


class NativeIntakeSourcePolicyConflict(ValueError):
    pass


class NativeSourcePolicyReevaluationRequired(NativeIntakeSourcePolicyConflict):
    """Only source proof changed while every other acceptance predicate held."""


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _object(value: Any) -> dict:
    value = json.loads(value) if isinstance(value, str) else value
    if not isinstance(value, dict):
        raise NativeIntakeSourcePolicyConflict("NATIVE_SOURCE_POLICY_INVALID_OBJECT")
    return value


def _uuid(value: Any) -> str:
    if not isinstance(value, str) or not UUID(value).int or str(UUID(value)) != value:
        raise ValueError("A nonzero canonical UUID is required")
    return value


def _time(value: Any) -> datetime:
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise NativeIntakeSourcePolicyConflict("NATIVE_SOURCE_POLICY_TIME_UNAVAILABLE")
    return result


def native_intake_source_policy_policy() -> dict:
    return load_native_intake_policy(env_name=POLICY_ENV, max_seconds=MAX_COMMAND_SECONDS,
        unavailable=NativeIntakeSourcePolicyUnavailable, error_code="NATIVE_SOURCE_POLICY_POLICY_UNAVAILABLE", max_attempts=3)


class NativeIntakeSourcePolicyAuthenticator:
    """Authenticate the opaque capability before binding or reading a tenant."""

    @staticmethod
    def sign(job_id: str, tenant: str, generation: int) -> str:
        return sign_native_intake_attempt(NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE, SIGNING_KEY_ENV,
            job_id, tenant, generation, unavailable=NativeIntakeSourcePolicyUnavailable,
            error_code="NATIVE_SOURCE_POLICY_SIGNING_KEY_UNAVAILABLE")

    def decode(self, delivery) -> tuple[str, int]:
        return decode_native_intake_attempt(delivery, job_type=NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE,
            sign=self.sign, error_code="NATIVE_SOURCE_POLICY_TOKEN_REJECTED")


class NativeIntakeSourcePolicyOwner:
    def __init__(self, graph: Any) -> None:
        self.graph, self.engine, self.queue = graph, graph.engine, graph.queue
        if getattr(self.engine, "dialect", None) != "postgresql":
            raise NativeIntakeSourcePolicyUnavailable("NATIVE_SOURCE_POLICY_POSTGRESQL_REQUIRED")
        self.auth = NativeIntakeSourcePolicyAuthenticator()

    def _clock(self) -> datetime:
        return _time(self.engine.query_one("SELECT clock_timestamp() AS now")["now"])

    @staticmethod
    def _partition(policy: dict, tenant: str) -> None:
        _uuid(tenant)
        if tenant not in policy["tenant_ids"]:
            raise PermissionError("NATIVE_SOURCE_POLICY_TENANT_NOT_CONFIGURED")

    def _audit(self, intake: dict, action: str, before: Any, after: dict, at: datetime, *,
               audit_id: str, causation: str) -> None:
        self.graph.audit_sink.persist({
            "audit_event_id": audit_id, "tenant_id": intake["tenant_id"],
            "resource_type": "intake", "resource_id": intake["intake_id"],
            "action": action, "result": "SUCCEEDED", "actor_subject_id": None,
            "service_principal": NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE, "actor_role": "SVC_INTAKE",
            "correlation_id": intake["correlation_id"], "causation_id": causation,
            "occurred_at": at.isoformat(), "before_value": before, "after_value": after,
            "reason": "Evaluate the current native source policy.",
        }, {key: intake[key] for key in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")})

    def _producer_reads(self, before: Callable[[], None]):
        # Narrow, per-call read views retain this thread's real SQL transaction.
        # They never replace shared engine/queue methods or expose mutations.
        def read(method, sql, params=()):
            if not sql.lstrip().upper().startswith("SELECT ") or "FOR UPDATE" in sql.upper():
                raise NativeIntakeSourcePolicyConflict("NATIVE_SOURCE_POLICY_PRODUCER_READ_ONLY_REQUIRED")
            before()
            return method(sql, params)

        def get(job_id):
            before()
            return self.queue.get(job_id)

        return SimpleNamespace(
            engine=SimpleNamespace(dialect="postgresql",
                query=lambda sql, params=(): read(self.engine.query, sql, params),
                query_one=lambda sql, params=(): read(self.engine.query_one, sql, params)),
            queue=SimpleNamespace(get=get),
            _one=lambda sql, params: read(self.graph._one, sql, params),
        )

    def _admission_budget(self, deadline: datetime) -> None:
        remaining = math.floor((deadline - self._clock()).total_seconds() * 1000) - 1
        if remaining < 1:
            raise NativeIntakeSourcePolicyConflict("NATIVE_SOURCE_POLICY_ADMISSION_DEADLINE_EXPIRED")
        value = str(remaining) + "ms"
        self.engine.query_one("SELECT set_config('statement_timeout',?,true)", (value,))
        self.engine.query_one("SELECT set_config('lock_timeout',?,true)", (value,))

    @staticmethod
    def _occurrence(intake_id: str, event_id: str) -> str:
        return intake_id + ":" + event_id

    @staticmethod
    def _decision_id(job_id: str) -> str:
        return str(uuid5(UUID(job_id), "native-source-policy-decision-v1"))

    @staticmethod
    def _branch(target: str) -> tuple[str, str]:
        try:
            return {
                "RETRIEVING": ("source.retrieve", "intake.retrieval_approved.v1"),
                "AWAITING_ASSISTED_ENTRY": ("intake.assisted_entry", "intake.assisted_entry_required.v1"),
                "QUARANTINED": ("intake.quarantine", "intake.quarantined.v1"),
            }[target]
        except KeyError as error:
            raise NativeIntakeSourcePolicyConflict("NATIVE_SOURCE_POLICY_TARGET_UNSUPPORTED") from error

    def _event(self, tenant: str, event_id: str, *, before: Callable[[], None] | None = None) -> tuple[dict, dict, str, dict]:
        view = self._producer_reads(before or (lambda: None))
        row = view._one("SELECT * FROM workflow.outbox_events WHERE tenant_id=? AND event_id=?", (tenant, _uuid(event_id)))
        event = _object(row["payload"])
        if (validate_event(event) or row["event_type"] != "intake.state_changed"
                or event.get("event_type") != row["event_type"]
                or event.get("producer") != "listing_intake_service"
                or event.get("schema_ref") != "#/payloads/IntakeStateChangedV1"
                or type(event.get("event_version")) is not int or event["event_version"] != 1
                or type(event.get("aggregate_version")) is not int
                or event["tenant_id"] != tenant or row["aggregate_type"] != "intake"
                or event["aggregate_type"] != "intake" or row["event_id"] != event["event_id"]
                or row["aggregate_id"] != event["aggregate_id"]
                or row["aggregate_version"] != event["aggregate_version"]
                or row["event_version"] != event["event_version"]
                or row["correlation_id"] != event["correlation_id"]
                or row["partition_key"] != tenant + ":" + row["aggregate_id"]
                or event["partition_key"] != row["partition_key"]
                or event.get("actor_ref") != "native-intake-identity"
                or event["payload"].get("intake_id") != row["aggregate_id"]
                or event["payload"].get("from_state") != "CHECKING_IDENTITY"
                or event["payload"].get("to_state") != "CHECKING_SOURCE_POLICY"
                or type(event["payload"].get("version")) is not int
                or event["payload"]["version"] != event["aggregate_version"]):
            raise NativeIntakeSourcePolicyConflict("NATIVE_SOURCE_POLICY_PRODUCER_EVENT_CONFLICT")
        transition = view._one("SELECT job_id FROM intake.intake_stage_transitions "
            "WHERE tenant_id=? AND transition_id=? AND intake_id=?", (tenant,
                _uuid(event["payload"]["transition_id"]), row["aggregate_id"]))
        producer_job = view._one("SELECT payload FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                                 (tenant, transition["job_id"]))
        payload = _object(producer_job["payload"])
        delivery = view.queue.get(transition["job_id"])
        prior = NativeIntakeIdentityOwner(view)._committed_result(delivery, tenant, payload.get("delivery_generation")) if delivery else None
        if prior is None or prior["outbox_event_id"] != event_id or prior["processing_state"] != "CHECKING_SOURCE_POLICY":
            raise NativeIntakeSourcePolicyConflict("NATIVE_SOURCE_POLICY_PRODUCER_PROOF_MISSING")
        return row, event, hashlib.sha256(_json(event).encode()).hexdigest(), {"result": prior, "payload": payload}


    def _input_binding(self, intake: dict, event: dict, expected_version: int, producer: dict, *,
                       pending: bool, before: Callable[[], None]) -> None:
        prior, admitted = producer["result"], producer["payload"]
        evidence = _object(prior["identity_evidence"])
        identity = normalize_native_intake_url(intake["original_url"])
        if (type(expected_version) is not int or expected_version < 3
                or intake["deleted_at"] is not None or event["aggregate_version"] != expected_version
                or intake["tenant_id"] != event["tenant_id"] or intake["intake_id"] != event["aggregate_id"]
                or intake["correlation_id"] != event["correlation_id"] or intake["intake_method"] != "URL"
                or admitted["input_sha256"] != NativeIntakeIdentityOwner._input_digest(intake)
                or intake["source_id"] != evidence["source_id"]
                or intake["canonical_url"] != identity.canonical_url
                or intake["canonical_url_sha256"] != identity.canonical_url_sha256
                or evidence["canonical_url_sha256"] != identity.canonical_url_sha256
                or evidence["normalization_rule_version"] != identity.rule_version
                or evidence["normalization_rule_source_sha256"] != identity.rule_source_sha256
                or (pending and (intake["version"] != expected_version
                    or intake["processing_state"] != "CHECKING_SOURCE_POLICY"
                    or any(intake[key] is not None for key in ("source_policy_state", "resolved_listing_id", "completed_at"))))):
            raise NativeIntakeSourcePolicyConflict("NATIVE_SOURCE_POLICY_INTAKE_BINDING_CONFLICT")
        # Admission/execution recheck original actor and resource scope, bound
        # to the producer's audited submitted digest, before any new mutation.
        owner = NativeIntakeIdentityOwner(self._producer_reads(before))
        _, started, digest = owner._event(intake["tenant_id"], admitted["event_id"])
        if digest != admitted["event_sha256"]:
            raise NativeIntakeSourcePolicyConflict("NATIVE_SOURCE_POLICY_ORIGINAL_INPUT_CONFLICT")
        owner._input_binding(intake, started, admitted["intake_version"], pending=False)


    @staticmethod
    def _input_digest(intake: dict) -> str:
        keys = ("intake_id", "tenant_id", "intake_method", "original_url", "submitter_subject_id",
                "correlation_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id",
                "source_id", "canonical_url", "canonical_url_sha256")
        return hashlib.sha256(_json({key: intake[key] for key in keys}).encode()).hexdigest()


    def _schema(self) -> None:
        indexes = self.engine.query("SELECT c.relname,i.indisunique FROM pg_catalog.pg_index i "
            "JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='intake' AND c.relname IN "
            "('ux_intakes_exact_url_active','ix_intakes_canonical_url_history')")
        if {row["relname"]: row["indisunique"] for row in indexes} != {"ix_intakes_canonical_url_history": False}:
            raise NativeIntakeSourcePolicyUnavailable("NATIVE_SOURCE_POLICY_CURRENT_SCHEMA_REQUIRED")


    def admit_event(self, tenant: str, event_id: str) -> dict:
        policy = native_intake_source_policy_policy()
        self._partition(policy, tenant)
        with self.graph.transaction(tenant):
            entry = self._clock()
            bound = entry + timedelta(milliseconds=ADMISSION_BUDGET_MILLISECONDS)
            def before():
                self._admission_budget(bound)
            before()
            self._schema()
            _, event, event_hash, producer = self._event(tenant, event_id, before=before)
            intake_id, version = event["aggregate_id"], event["aggregate_version"]
            occurrence = self._occurrence(intake_id, event_id)
            job_id = str(uuid5(UUID(intake_id), NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE + ":" + event_id))
            before()
            self.engine.query_one("SELECT job_id FROM durable_jobs WHERE job_id=? FOR UPDATE", (job_id,))
            before()
            prior = self.engine.query_one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=? FOR UPDATE", (tenant, job_id))
            before()
            intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE", (tenant, intake_id))
            self._input_binding(intake, event, version, producer, pending=prior is None, before=before)
            token = self.auth.sign(job_id, tenant, 1)
            if prior is not None:
                payload = _object(prior["payload"])
                before()
                current = self.queue.get(job_id)
                if (prior["job_type"] != NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE or prior["aggregate_type"] != "intake"
                        or prior["aggregate_id"] != intake_id or prior["idempotency_key"] != occurrence
                        or payload.get("event_id") != event_id or payload.get("event_sha256") != event_hash
                        or payload.get("input_sha256") != self._input_digest(intake)
                        or payload.get("intake_version") != version or payload.get("policy") != policy
                        or current is None or current.payload != {"job_id": job_id, "tenant_id": tenant, "attempt_token": token}
                        or current.job_type != NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE):
                    raise NativeIntakeSourcePolicyConflict("NATIVE_SOURCE_POLICY_RESERVATION_CONFLICT")
                before()
                receipt = self._admission_receipt(prior, payload)
                before()
                return receipt
            at, audit_id = self._clock(), str(uuid4())
            deadline = entry + timedelta(seconds=policy["deadline_seconds"])
            receipt = {"job_id": job_id, "intake_id": intake_id, "admitted_intake_version": version,
                       "job_status_at_admission": "QUEUED", "timeout_at": deadline.isoformat(),
                       "policy_started_event_id": event_id, "audit_event_id": audit_id}
            payload = {"event_id": event_id, "event_sha256": event_hash, "intake_version": version,
                       "policy": policy, "delivery_generation": 1,
                       "attempt_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                       "submitter_subject_id": intake["submitter_subject_id"],
                       "source_id": intake["source_id"], "canonical_url_sha256": intake["canonical_url_sha256"],
                       "resolution_mode": NATIVE_URL_RULE_VERSION, "input_sha256": self._input_digest(intake),
                       "rule_source_sha256": NATIVE_URL_RULE_SOURCE_SHA256,
                       "policy_rule_version": POLICY_RULE_VERSION, "policy_rule_sha256": POLICY_RULE_SOURCE_SHA256,
                       "admission_receipt": receipt}
            before()
            self.engine.execute("INSERT INTO workflow.jobs (job_id,tenant_id,job_type,aggregate_type,aggregate_id,"
                "status,checkpoint,attempt,max_attempts,fence_token,timeout_at,payload,correlation_id,idempotency_key) "
                "VALUES (?,?,?,'intake',?,'QUEUED','CHECKING_SOURCE_POLICY',0,?,1,?,?::jsonb,?,?)",
                (job_id, tenant, NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE, intake_id, policy["max_attempts"],
                 deadline.isoformat(), _json(payload), intake["correlation_id"], occurrence))
            before()
            delivery, created = self.queue.enqueue(JobRequest(NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE,
                {"job_id": job_id, "tenant_id": tenant, "attempt_token": token},
                idempotency_key=NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE + ":" + tenant + ":" + occurrence,
                job_id=job_id), correlation_id=intake["correlation_id"])
            if not created or delivery.job_id != job_id:
                raise NativeIntakeSourcePolicyConflict("NATIVE_SOURCE_POLICY_DELIVERY_RESERVATION_CONFLICT")
            before()
            self.engine.execute("UPDATE durable_jobs SET max_retries=? WHERE job_id=?", (policy["max_attempts"], job_id))
            before()
            self._audit(intake, "intake.source_policy_evaluation_requested.v1", None,
                        {**receipt, "native_source_policy_provenance": self._provenance(payload)}, at,
                        audit_id=audit_id, causation=event_id)
            # Includes nested audit work: late admission is never accepted.
            before()
            if native_intake_source_policy_policy() != policy:
                raise NativeIntakeSourcePolicyConflict("NATIVE_SOURCE_POLICY_ADMISSION_POLICY_CHANGED")
            return receipt


    def _admission_receipt(self, job: dict, payload: dict) -> dict:
        receipt = _object(payload.get("admission_receipt"))
        if (set(receipt) != {"job_id", "intake_id", "admitted_intake_version", "job_status_at_admission",
                             "timeout_at", "policy_started_event_id", "audit_event_id"}
                or receipt.get("job_id") != job["job_id"] or receipt.get("intake_id") != job["aggregate_id"]
                or type(receipt.get("admitted_intake_version")) is not int
                or receipt["admitted_intake_version"] != payload["intake_version"]
                or receipt.get("job_status_at_admission") != "QUEUED"
                or receipt.get("policy_started_event_id") != payload["event_id"]
                or _time(receipt["timeout_at"]) != _time(job["timeout_at"])):
            raise NativeIntakeSourcePolicyConflict("NATIVE_SOURCE_POLICY_ADMISSION_RECEIPT_CONFLICT")
        audit = self.engine.query_one("SELECT after_value FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=? "
            "AND resource_type='intake' AND resource_id=? AND actor_subject_id IS NULL AND service_principal=? "
            "AND actor_role='SVC_INTAKE' AND action='intake.source_policy_evaluation_requested.v1' AND result='SUCCEEDED' "
            "AND causation_id=? AND correlation_id=?", (job["tenant_id"], receipt["audit_event_id"], job["aggregate_id"],
                NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE, payload["event_id"], job["correlation_id"]))
        if audit is None:
            raise NativeIntakeSourcePolicyConflict("NATIVE_SOURCE_POLICY_ADMISSION_AUDIT_MISSING")
        snapshot = _object(audit["after_value"])
        if (any(snapshot.get(k) != v for k, v in receipt.items())
                or snapshot.get("native_source_policy_provenance") != self._provenance(payload)):
            raise NativeIntakeSourcePolicyConflict("NATIVE_SOURCE_POLICY_ADMISSION_AUDIT_CONFLICT")
        return receipt

    def admit_partition(self, tenant: str, *, limit: int):
        self._partition(native_intake_source_policy_policy(), tenant)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("A bounded partition size from 1 to 100 is required")
        with self.graph.transaction(tenant):
            bound = self._clock() + timedelta(milliseconds=ADMISSION_BUDGET_MILLISECONDS)
            self._admission_budget(bound)
            rows = self.engine.query("SELECT e.event_id FROM workflow.outbox_events e "
                "JOIN intake.intakes i ON i.tenant_id=e.tenant_id AND i.intake_id=e.aggregate_id "
                "WHERE e.tenant_id=? AND e.event_type='intake.state_changed' AND e.aggregate_type='intake' "
                "AND i.processing_state='CHECKING_SOURCE_POLICY' AND i.deleted_at IS NULL "
                "AND e.aggregate_version=i.version AND e.payload->>'actor_ref'='native-intake-identity' "
                "AND e.payload->'payload'->>'to_state'='CHECKING_SOURCE_POLICY' "
                "AND NOT EXISTS (SELECT 1 FROM workflow.jobs j WHERE j.tenant_id=e.tenant_id AND j.job_type=? "
                "AND j.idempotency_key=i.intake_id::text||':'||e.event_id::text) "
                "ORDER BY e.occurred_at,e.event_id LIMIT ?", (tenant, NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE, limit))
            self._admission_budget(bound)
        for row in rows:
            yield self.admit_event(tenant, row["event_id"])


    def _budget(self, timeout: Any, lease: Any) -> None:
        # Recompute at each lock/write boundary. The final conditional writes
        # below independently reject a deadline/lease that elapsed meanwhile.
        set_native_intake_sql_budget(self.engine, _time(timeout), _time(lease), self._clock(),
                                     error_code="NATIVE_SOURCE_POLICY_DEADLINE_OR_LEASE_EXPIRED")

    def _locked(self, delivery, tenant: str, generation: int, *, settlement: bool = False) -> tuple[Any, dict, dict]:
        # Deliberately retain the existing identity coordinator's small
        # transport predicate; its decision-specific business fence is not
        # applicable to this Intake command. Both paths get regression coverage.
        preliminary = self.graph._one("SELECT timeout_at FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                                      (tenant, delivery.job_id))
        peek = self.queue.get(delivery.job_id)
        if peek is None or peek.lease_expires_at is None:
            raise JobFenceRejectedError("NATIVE_SOURCE_POLICY_DELIVERY_NOT_OWNED")
        # Expired business work may still be protectively settled by its live
        # delivery owner; it cannot receive another Intake mutation grant.
        budget = peek.lease_expires_at if settlement else preliminary["timeout_at"]
        self._budget(budget, peek.lease_expires_at)
        self.graph._one("SELECT job_id FROM durable_jobs WHERE job_id=? FOR UPDATE", (delivery.job_id,))
        current = self.queue.get(delivery.job_id)
        now = self._clock()
        if (current is None or current.job_type != NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE
                or current.status != JobStatus.RUNNING or not current.locked_by
                or current.locked_by != delivery.locked_by or current.fence_token != delivery.fence_token
                or current.payload != delivery.payload or current.correlation_id != delivery.correlation_id
                or current.lease_expires_at is None or current.lease_expires_at <= now):
            raise JobFenceRejectedError("NATIVE_SOURCE_POLICY_DELIVERY_NOT_OWNED")
        job = self.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=? FOR UPDATE",
                              (tenant, delivery.job_id))
        payload = _object(job["payload"])
        if (job["job_type"] != NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE or job["aggregate_type"] != "intake"
                or job["idempotency_key"] != self._occurrence(job["aggregate_id"], payload["event_id"])
                or job["correlation_id"] != current.correlation_id
                or type(payload.get("delivery_generation")) is not int
                or payload.get("delivery_generation") != generation
                or payload.get("resolution_mode") != NATIVE_URL_RULE_VERSION
                or payload.get("attempt_token_sha256") != hashlib.sha256(delivery.payload["attempt_token"].encode()).hexdigest()):
            raise JobFenceRejectedError("NATIVE_SOURCE_POLICY_ADMISSION_NOT_OWNED")
        self._admission_receipt(job, payload)
        return current, job, payload

    def _committed_result(self, delivery, tenant: str, generation: int) -> dict | None:
        current = self.queue.get(delivery.job_id)
        job = self.engine.query_one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                                    (tenant, delivery.job_id))
        if job is None or job["status"] != "SUCCEEDED":
            return None
        payload, result = _object(job["payload"]), _object(job["result"])
        target = result.get("processing_state")
        if target not in {"RETRIEVING", "AWAITING_ASSISTED_ENTRY", "QUARANTINED"}:
            raise JobFenceRejectedError("NATIVE_SOURCE_POLICY_TERMINAL_STATE_CONFLICT")
        permission, action = self._branch(target)
        proof = _object(result.get("policy_decision"))
        if (current is None or current.status != JobStatus.SUCCEEDED or current.payload != delivery.payload
                or current.job_type != NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE
                or job["job_type"] != NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE or job["aggregate_type"] != "intake"
                or job["idempotency_key"] != self._occurrence(job["aggregate_id"], payload["event_id"]) or current.correlation_id != job["correlation_id"]
                or type(payload.get("delivery_generation")) is not int
                or payload["delivery_generation"] != generation
                or payload.get("attempt_token_sha256") != hashlib.sha256(delivery.payload["attempt_token"].encode()).hexdigest()
                or set(result) != {"job_id", "intake_id", "processing_state", "version", "transition_id",
                                   "audit_event_id", "outbox_event_id", "correlation_id", "policy_decision"}
                or not self._decision_valid(proof, payload, delivery.job_id, target)
                or result["job_id"] != delivery.job_id or result["intake_id"] != job["aggregate_id"]
                or result["correlation_id"] != job["correlation_id"]
                or type(payload.get("intake_version")) is not int or type(result["version"]) is not int
                or result["version"] != payload["intake_version"] + 1
                or proof.get("normalization_rule_source_sha256") != payload.get("rule_source_sha256")
                or proof.get("normalization_rule_version") != payload.get("resolution_mode")
                or type(proof.get("source_version")) is not int
                or type(proof.get("accepted_delivery_fence")) is not int
                or proof["accepted_delivery_fence"] != current.fence_token
                or payload.get("accepted_delivery_fence") != proof["accepted_delivery_fence"]
                or not isinstance(proof.get("accepted_worker"), str) or not proof["accepted_worker"]
                or payload.get("accepted_worker") != proof["accepted_worker"]
                or type(proof.get("accepted_business_fence")) is not int
                or proof["accepted_business_fence"] != job["fence_token"]
                or type(proof.get("accepted_attempt")) is not int or proof["accepted_attempt"] != job["attempt"]):
            raise JobFenceRejectedError("NATIVE_SOURCE_POLICY_TERMINAL_RECEIPT_CONFLICT")
        self._admission_receipt(job, payload)
        if not self.engine.query_one("SELECT transition_id FROM intake.intake_stage_transitions "
            "WHERE tenant_id=? AND transition_id=? AND intake_id=? AND job_id=? "
            "AND from_state='CHECKING_SOURCE_POLICY' AND to_state=? AND resulting_version=? "
            "AND expected_version=? AND actor_subject_id IS NULL AND service_principal=? "
            "AND permission=? AND source_policy_version=? AND reason_code=? AND causation_id=? AND correlation_id=?",
            (tenant, result["transition_id"], job["aggregate_id"], delivery.job_id, target,
             result["version"], payload["intake_version"], NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE,
             permission, proof["source_version"], proof["reason_code"], payload["event_id"], job["correlation_id"])):
            raise JobFenceRejectedError("NATIVE_SOURCE_POLICY_TERMINAL_TRANSITION_CONFLICT")
        audit = self.engine.query_one("SELECT after_value FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=? "
            "AND resource_type='intake' AND resource_id=? AND actor_subject_id IS NULL AND service_principal=? "
            "AND actor_role='SVC_INTAKE' AND action=? AND result='SUCCEEDED' AND causation_id=? AND correlation_id=?",
            (tenant, result["audit_event_id"], job["aggregate_id"], NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE,
             action, payload["event_id"], job["correlation_id"]))
        outbox = self.engine.query_one("SELECT payload FROM workflow.outbox_events WHERE tenant_id=? AND event_id=? "
            "AND event_type='intake.state_changed' AND aggregate_type='intake' AND aggregate_id=? "
            "AND aggregate_version=? AND correlation_id=?", (tenant, result["outbox_event_id"],
                job["aggregate_id"], result["version"], job["correlation_id"]))
        if audit is None or outbox is None:
            raise JobFenceRejectedError("NATIVE_SOURCE_POLICY_TERMINAL_EVIDENCE_MISSING")
        snapshot, event = _object(audit["after_value"]), _object(outbox["payload"])
        if (any(snapshot.get(key) != value for key, value in result.items())
                or snapshot.get("native_source_policy_provenance") != self._provenance(payload)
                or validate_event(event) or event.get("event_id") != result["outbox_event_id"]
                or event.get("event_type") != "intake.state_changed"
                or event.get("producer") != "listing_intake_service"
                or event.get("schema_ref") != "#/payloads/IntakeStateChangedV1"
                or type(event.get("event_version")) is not int or event["event_version"] != 1
                or type(event.get("aggregate_version")) is not int
                or event["tenant_id"] != tenant or event["aggregate_id"] != job["aggregate_id"]
                or event["aggregate_version"] != result["version"]
                or event.get("actor_ref") != NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE
                or event.get("causation_id") != payload["event_id"]
                or event["correlation_id"] != job["correlation_id"]
                or event["payload"].get("transition_id") != result["transition_id"]
                or event["payload"].get("intake_id") != job["aggregate_id"]
                or event["payload"].get("from_state") != "CHECKING_SOURCE_POLICY"
                or event["payload"].get("to_state") != target
                or type(event["payload"].get("version")) is not int
                or event["payload"]["version"] != result["version"]):
            raise JobFenceRejectedError("NATIVE_SOURCE_POLICY_TERMINAL_EVIDENCE_CONFLICT")
        _, _, digest, _ = self._event(tenant, payload["event_id"])
        if digest != payload["event_sha256"]:
            raise JobFenceRejectedError("NATIVE_SOURCE_POLICY_ORIGINAL_EVENT_CHANGED")
        return result

    @staticmethod
    def _provenance(payload: dict) -> dict:
        return {**{key: payload[key] for key in ("event_id", "event_sha256", "intake_version",
                    "delivery_generation", "input_sha256", "rule_source_sha256", "resolution_mode",
                    "source_id", "canonical_url_sha256", "policy_rule_version", "policy_rule_sha256")},
                "policy_sha256": hashlib.sha256(_json(payload["policy"]).encode()).hexdigest()}



    @staticmethod
    def _decision_valid(proof: dict, payload: dict, job_id: str, target: str) -> bool:
        keys = {"policy_decision_id", "policy_rule_version", "policy_rule_sha256", "source_id", "source_version",
                "source_host", "host_match_count", "canonical_url_sha256", "normalization_rule_version",
                "normalization_rule_source_sha256", "source_snapshot", "source_snapshot_sha256", "evaluated_at",
                "valid_until", "effective_policy", "target_state", "reason_code", "accepted_delivery_fence",
                "accepted_worker", "accepted_business_fence", "accepted_attempt"}
        return (set(proof) == keys
            and proof["policy_decision_id"] == NativeIntakeSourcePolicyOwner._decision_id(job_id)
            and proof["policy_rule_version"] == payload["policy_rule_version"]
            and proof["policy_rule_sha256"] == payload["policy_rule_sha256"]
            and proof["source_id"] == payload["source_id"]
            and proof["canonical_url_sha256"] == payload["canonical_url_sha256"]
            and proof["target_state"] == target and type(proof["host_match_count"]) is int
            and proof["host_match_count"] in {0, 1, 2} and isinstance(proof["source_snapshot"], dict)
            and proof["source_snapshot"].get("source_id") == proof["source_id"]
            and proof["source_snapshot"].get("version") == proof["source_version"]
            and hashlib.sha256(_json(proof["source_snapshot"]).encode()).hexdigest() == proof["source_snapshot_sha256"]
            and (target != "RETRIEVING" or proof["effective_policy"] == "APPROVED_RETRIEVAL")
            and (target != "AWAITING_ASSISTED_ENTRY" or proof["effective_policy"] == "ASSISTED_ENTRY_ONLY")
            and (target != "QUARANTINED" or proof["effective_policy"] in {"SOURCE_BLOCKED", "POLICY_UNKNOWN"}))


    def _resolve(self, intake: dict, job: dict, current) -> tuple[dict, Any, dict]:
        identity = normalize_native_intake_url(intake["original_url"])
        self._budget(job["timeout_at"], current.lease_expires_at)
        matches = self.engine.query("SELECT source_id FROM intake.source_registry s WHERE EXISTS "
            "(SELECT 1 FROM unnest(s.allowed_hosts) AS h(host) WHERE lower(h.host)=?) "
            "ORDER BY source_id LIMIT 2 FOR SHARE", (identity.source_host,))
        self._budget(job["timeout_at"], current.lease_expires_at)
        source = self.graph._one("SELECT * FROM intake.source_registry WHERE source_id=? FOR SHARE", (intake["source_id"],))
        at = self._clock()
        classified = evaluate_native_source_policy(source, source_host=identity.source_host,
            expected_source_id=intake["source_id"], host_match_count=len(matches), now=at)
        proof = {"policy_decision_id": self._decision_id(job["job_id"]),
                 "policy_rule_version": POLICY_RULE_VERSION, "policy_rule_sha256": POLICY_RULE_SOURCE_SHA256,
                 "source_id": source["source_id"], "source_version": source["version"],
                 "source_host": identity.source_host, "host_match_count": len(matches),
                 "canonical_url_sha256": identity.canonical_url_sha256,
                 "normalization_rule_version": identity.rule_version,
                 "normalization_rule_source_sha256": identity.rule_source_sha256,
                 "source_snapshot": json.loads(classified.source_snapshot_json),
                 "source_snapshot_sha256": classified.source_snapshot_sha256,
                 "evaluated_at": at.isoformat(), "target_state": classified.target_state,
                 "effective_policy": classified.effective_policy, "reason_code": classified.reason_code,
                 "valid_until": classified.valid_until.isoformat() if classified.valid_until else None}
        return source, classified, proof

    def _cas_rejected(self, delivery, job: dict, payload: dict, intake: dict, proof: dict, *,
                      expected_stage: str, expected_version: int) -> None:
        # Diagnose a rejected CAS while this root still holds all original
        # locks. Source-only expiry may retry; any other failed/unknown axis
        # must never be relabelled as source-policy reevaluation.
        now = self._clock()
        physical = self.queue.get(delivery.job_id)
        business = self.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                                   (intake["tenant_id"], delivery.job_id))
        actual = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=?",
                                 (intake["tenant_id"], intake["intake_id"]))
        if (physical is None or physical.status != JobStatus.RUNNING
                or physical.job_type != NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE
                or physical.fence_token != delivery.fence_token or physical.locked_by != delivery.locked_by
                or physical.version != delivery.version or physical.payload != delivery.payload
                or physical.correlation_id != delivery.correlation_id or physical.lease_expires_at is None
                or physical.lease_expires_at <= now):
            raise JobFenceRejectedError("NATIVE_SOURCE_POLICY_FINAL_DELIVERY_REJECTED")
        if (business["version"] != job["version"] or business["fence_token"] != job["fence_token"]
                or business["status"] != "RUNNING" or business["attempt"] != job["attempt"] + 1
                or business["max_attempts"] != payload["policy"]["max_attempts"]
                or business["result"] is not None or business["cancellation_requested_at"] is not None
                or _time(business["timeout_at"]) <= now or _object(business["payload"]) != payload
                or native_intake_source_policy_policy() != payload["policy"]
                or actual["processing_state"] != expected_stage or actual["version"] != expected_version
                or actual["source_policy_state"] != (None if expected_stage == "CHECKING_SOURCE_POLICY" else proof["effective_policy"])
                or actual["deleted_at"] is not None or self._input_digest(actual) != payload["input_sha256"]
                or actual["resolved_listing_id"] is not None or actual["completed_at"] is not None):
            raise NativeIntakeSourcePolicyConflict("NATIVE_SOURCE_POLICY_FINAL_NON_SOURCE_CONFLICT")
        source = self.graph._one("SELECT * FROM intake.source_registry WHERE source_id=? FOR SHARE", (intake["source_id"],))
        changed = hashlib.sha256(source_policy_snapshot_json(source).encode()).hexdigest() != proof["source_snapshot_sha256"]
        expired = proof["valid_until"] is not None and _time(proof["valid_until"]) <= now
        if changed or expired:
            raise NativeSourcePolicyReevaluationRequired("NATIVE_SOURCE_POLICY_PROOF_CHANGED")
        raise NativeIntakeSourcePolicyConflict("NATIVE_SOURCE_POLICY_FINAL_CAS_REJECTED")

    def _ensure_source_proof(self, current, job, payload, intake, proof, *, stage, version):
        self._budget(job["timeout_at"], current.lease_expires_at)
        row = self.graph._one("SELECT * FROM intake.source_registry WHERE source_id=? FOR SHARE", (intake["source_id"],))
        if (hashlib.sha256(source_policy_snapshot_json(row).encode()).hexdigest() != proof["source_snapshot_sha256"]
                or (proof["valid_until"] is not None and _time(proof["valid_until"]) <= self._clock())):
            self._cas_rejected(current, job, payload, intake, proof, expected_stage=stage, expected_version=version)


    def execute(self, delivery) -> dict:
        tenant, generation = self.auth.decode(delivery)
        policy = native_intake_source_policy_policy()
        self._partition(policy, tenant)
        with self.graph.transaction(tenant):
            if result := self._committed_result(delivery, tenant, generation):
                return result
            current, job, payload = self._locked(delivery, tenant, generation)
            self._schema()
            now = self._clock()
            if (job["status"] not in {"QUEUED", "RETRYING"} or job["result"] is not None
                    or payload.get("policy") != policy or job["max_attempts"] != policy["max_attempts"]
                    or job["attempt"] >= job["max_attempts"] or job["cancellation_requested_at"] is not None
                    or payload.get("rule_source_sha256") != NATIVE_URL_RULE_SOURCE_SHA256
                    or payload.get("policy_rule_version") != POLICY_RULE_VERSION
                    or payload.get("policy_rule_sha256") != POLICY_RULE_SOURCE_SHA256
                    or _time(job["timeout_at"]) <= now
                    or (job["next_attempt_at"] is not None and _time(job["next_attempt_at"]) > now)):
                raise NativeIntakeSourcePolicyConflict("NATIVE_SOURCE_POLICY_ATTEMPT_NOT_ADMITTED")
            self._budget(job["timeout_at"], current.lease_expires_at)
            intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE",
                                      (tenant, job["aggregate_id"]))
            def before():
                self._budget(job["timeout_at"], current.lease_expires_at)
            _, event, digest, producer = self._event(tenant, payload["event_id"], before=before)
            if (digest != payload["event_sha256"] or payload["input_sha256"] != self._input_digest(intake)
                    or payload["submitter_subject_id"] != intake["submitter_subject_id"]):
                raise NativeIntakeSourcePolicyConflict("NATIVE_SOURCE_POLICY_INPUT_CHANGED")
            version = payload["intake_version"]
            self._input_binding(intake, event, version, producer, pending=True, before=before)
            self._budget(job["timeout_at"], current.lease_expires_at)
            if self.engine.execute("UPDATE workflow.jobs SET status='RUNNING',attempt=attempt+1,"
                "fence_token=fence_token+1,version=version+1,heartbeat_at=clock_timestamp(),lease_expires_at=? "
                "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? "
                "AND status IN ('QUEUED','RETRYING') AND attempt<max_attempts "
                "AND cancellation_requested_at IS NULL AND timeout_at>clock_timestamp()",
                (current.lease_expires_at.isoformat(), tenant, delivery.job_id,
                 job["version"], job["fence_token"])).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_SOURCE_POLICY_ACCEPTANCE_FENCE_REJECTED")
            job = {**job, "version": job["version"] + 1, "fence_token": job["fence_token"] + 1}
            source, classified, proof = self._resolve(intake, job, current)
            target = classified.target_state
            proof.update(accepted_delivery_fence=current.fence_token, accepted_worker=current.locked_by,
                         accepted_business_fence=job["fence_token"], accepted_attempt=job["attempt"] + 1)
            permission, action = self._branch(target)
            valid_until = proof["valid_until"]
            at, transition_id, audit_id = self._clock(), str(uuid4()), str(uuid4())
            aggregate = IntakeAggregate(id=intake["intake_id"], tenant_id=tenant,
                stage=IntakeStage(intake["processing_state"]), version=intake["version"],
                created_by=intake["submitter_subject_id"], source_id=source["source_id"],
                canonical_url=intake["canonical_url"], owner_id=intake["submitter_subject_id"])
            IntakeStateMachine.transition(aggregate, IntakeStage(target), TransitionContext(
                actor=Actor(NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE, PrincipalRole.SVC_INTAKE, tenant),
                version_before=version, idempotency_key=payload["event_id"],
                correlation_id=intake["correlation_id"], causation_id=payload["event_id"], current_time=at))
            self._budget(job["timeout_at"], current.lease_expires_at)
            self._ensure_source_proof(current, job, payload, intake, proof, stage="CHECKING_SOURCE_POLICY", version=version)
            if self.engine.execute("UPDATE intake.intakes SET processing_state=?,version=?,last_transition_at=?,source_policy_state=? "
                "WHERE tenant_id=? AND intake_id=? AND processing_state='CHECKING_SOURCE_POLICY' AND version=? AND deleted_at IS NULL "
                "AND source_policy_state IS NULL "
                "AND (?::timestamptz IS NULL OR ?::timestamptz>clock_timestamp())",
                (target, aggregate.version, at.isoformat(), classified.effective_policy,
                 tenant, intake["intake_id"], version, valid_until, valid_until)).rowcount != 1:
                self._cas_rejected(current, job, payload, intake, proof,
                                   expected_stage="CHECKING_SOURCE_POLICY", expected_version=version)
            sequence = self.engine.query_one("SELECT COALESCE(MAX(sequence_no),0)+1 AS next "
                "FROM intake.intake_stage_transitions WHERE tenant_id=? AND intake_id=?",
                (tenant, intake["intake_id"]))["next"]
            self._budget(job["timeout_at"], current.lease_expires_at)
            self.engine.execute("INSERT INTO intake.intake_stage_transitions (transition_id,tenant_id,intake_id,sequence_no,"
                "from_state,to_state,actor_subject_id,service_principal,permission,reason_code,reason_text,idempotency_key,"
                "expected_version,resulting_version,job_id,source_policy_version,correlation_id,causation_id,occurred_at) "
                "VALUES (?,?,?,?,'CHECKING_SOURCE_POLICY',?,NULL,?,?,?,?,?,?,?,?,?,?,?,?)",
                (transition_id, tenant, intake["intake_id"], sequence, target, NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE,
                 permission, proof["reason_code"], "Evaluate the current native source policy.", payload["event_id"], version,
                 aggregate.version, delivery.job_id, source["version"], intake["correlation_id"],
                 payload["event_id"], at.isoformat()))
            state_event = DomainEvent(event_type="intake.state_changed", producer="listing_intake_service",
                tenant_id=tenant, aggregate_type="intake", aggregate_id=intake["intake_id"],
                aggregate_version=aggregate.version, partition_key=tenant + ":" + intake["intake_id"],
                correlation_id=intake["correlation_id"], causation_id=payload["event_id"],
                actor_ref=NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE, occurred_at=at,
                schema_ref="#/payloads/IntakeStateChangedV1", payload={"intake_id": intake["intake_id"],
                    "from_state": "CHECKING_SOURCE_POLICY", "to_state": target, "transition_id": transition_id,
                    "version": aggregate.version, "occurred_at": at.isoformat()})
            result = {"job_id": delivery.job_id, "intake_id": intake["intake_id"],
                      "processing_state": target, "version": aggregate.version,
                      "transition_id": transition_id, "audit_event_id": audit_id,
                      "outbox_event_id": state_event.event_id, "correlation_id": intake["correlation_id"],
                      "policy_decision": proof}
            self._budget(job["timeout_at"], current.lease_expires_at)
            self._audit(intake, action, {"processing_state": "CHECKING_SOURCE_POLICY", "version": version},
                        {**result, "native_source_policy_provenance": self._provenance(payload)}, at,
                        audit_id=audit_id, causation=payload["event_id"])
            self._budget(job["timeout_at"], current.lease_expires_at)
            self.graph._outbox(state_event)
            self._budget(job["timeout_at"], current.lease_expires_at)
            if (native_intake_source_policy_policy() != policy
                    or not self.engine.query_one("SELECT job_id FROM durable_jobs WHERE job_id=? AND status=? "
                        "AND fence_token=? AND locked_by=? AND lease_expires_at>clock_timestamp()",
                        (delivery.job_id, JobStatus.RUNNING.value, current.fence_token, current.locked_by))):
                raise JobFenceRejectedError("NATIVE_SOURCE_POLICY_FINAL_DELIVERY_OR_POLICY_REJECTED")
            self._ensure_source_proof(current, job, payload, intake, proof, stage=target, version=aggregate.version)
            accepted = {**payload, "accepted_delivery_fence": current.fence_token,
                        "accepted_worker": current.locked_by}
            if self.engine.execute("UPDATE workflow.jobs SET status='SUCCEEDED',checkpoint=?,"
                "result=?::jsonb,payload=?::jsonb,version=version+1,heartbeat_at=?,lease_expires_at=NULL,updated_at=? "
                "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND status='RUNNING' "
                "AND cancellation_requested_at IS NULL AND timeout_at>clock_timestamp() "
                "AND (?::timestamptz IS NULL OR ?::timestamptz>clock_timestamp())",
                (target, _json(result), _json(accepted), at.isoformat(), at.isoformat(), tenant, delivery.job_id,
                 job["version"], job["fence_token"], valid_until, valid_until)).rowcount != 1:
                self._cas_rejected(current, job, payload, intake, proof,
                                   expected_stage=target, expected_version=aggregate.version)
            self.queue.update_status(delivery.job_id, JobStatus.SUCCEEDED,
                expected_version=current.version, fence_token=current.fence_token)
            return result

    def settle_failure(self, delivery, error: Exception) -> dict | None:
        """Re-read an unknown commit before a still-owned protective settlement."""
        tenant, generation = self.auth.decode(delivery)
        with self.graph.transaction(tenant):
            if result := self._committed_result(delivery, tenant, generation):
                return result
            current, job, payload = self._locked(delivery, tenant, generation, settlement=True)
            if job["status"] not in {"QUEUED", "RETRYING"} or job["result"] is not None:
                raise JobFenceRejectedError("NATIVE_SOURCE_POLICY_UNKNOWN_ATTEMPT_OUTCOME")
            policy, now = payload["policy"], self._clock()
            try:
                current_policy = native_intake_source_policy_policy()
            except NativeIntakeSourcePolicyUnavailable:
                current_policy = None
            cancelled = job["cancellation_requested_at"] is not None
            if isinstance(error, JobFenceRejectedError) and not cancelled and _time(job["timeout_at"]) > now:
                raise error
            attempt = min(job["attempt"] + 1, job["max_attempts"])
            retryable = (getattr(error, "sqlstate", None) in {"40001", "40P01"}
                         or isinstance(error, (CanonicalAuditUnavailable, NativeSourcePolicyReevaluationRequired)))
            due = None
            if (not cancelled and retryable and policy == current_policy
                    and attempt < job["max_attempts"] and _time(job["timeout_at"]) > now):
                due = now + timedelta(seconds=policy["backoff_seconds"][attempt - 1])
                if due >= _time(job["timeout_at"]):
                    due = None
            status = "CANCELLED" if cancelled else "RETRYING" if due else "FAILED"
            code = "NATIVE_SOURCE_POLICY_CANCELLED" if cancelled else "NATIVE_SOURCE_POLICY_RETRY_SCHEDULED" if due else "NATIVE_SOURCE_POLICY_FAILED"
            # Preserve an operator-visible, compiled reason for deliberate
            # unsupported/contradictory outcomes. Never echo exception text
            # unless it is exactly one of these static non-sensitive codes.
            safe_reasons = {"NATIVE_SOURCE_POLICY_" + suffix for suffix in (
                "AUTH_REQUIRED_CONTRACT_OPEN", "SNAPSHOT_UNAVAILABLE", "DB_CLOCK_UNAVAILABLE",
                "INPUT_CHANGED", "INTAKE_BINDING_CONFLICT", "ATTEMPT_NOT_ADMITTED", "ORIGINAL_INPUT_CONFLICT",
                "FINAL_NON_SOURCE_CONFLICT", "FINAL_CAS_REJECTED",
            )}
            if status == "FAILED" and isinstance(error, (NativeIntakeSourcePolicyConflict, NativeSourcePolicyRuleUnavailable)) and str(error) in safe_reasons:
                code = str(error)
            intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE",
                                      (tenant, job["aggregate_id"]))
            if self.engine.execute("UPDATE workflow.jobs SET status=?,attempt=?,last_error=?::jsonb,next_attempt_at=?,"
                "version=version+1,updated_at=? WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=?",
                (status, attempt, _json({"code": code, "error_class": type(error).__name__}),
                 due.isoformat() if due else None, now.isoformat(), tenant, delivery.job_id,
                 job["version"], job["fence_token"])).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_SOURCE_POLICY_SETTLEMENT_FENCE_REJECTED")
            self._audit(intake, "intake.source_policy_evaluation_delivery_settled.v1",
                        {"job_id": delivery.job_id, "status": job["status"], "attempt": job["attempt"]},
                        {"job_id": delivery.job_id, "status": status, "attempt": attempt, "code": code}, now,
                        audit_id=str(uuid4()), causation=payload["event_id"])
            self.queue.update_status(delivery.job_id,
                JobStatus.CANCELLED if cancelled else JobStatus.QUEUED if due else JobStatus.FAILED,
                delivery_state=None if cancelled else JobDeliveryState.RETRYING if due else JobDeliveryState.DEAD_LETTER,
                expected_version=current.version, fence_token=current.fence_token, error_message=code)
            if due:
                self.engine.execute("UPDATE durable_jobs SET available_at=? WHERE job_id=?", (due.isoformat(), delivery.job_id))
            elif not cancelled:
                self.graph._outbox(DomainEvent(event_type="job.dead_lettered", producer="job_platform",
                    tenant_id=tenant, aggregate_type="job", aggregate_id=delivery.job_id,
                    aggregate_version=job["version"] + 1, partition_key=tenant + ":" + delivery.job_id,
                    correlation_id=job["correlation_id"], causation_id=payload["event_id"],
                    actor_ref=NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE, occurred_at=now,
                    schema_ref="#/payloads/JobDeadLetteredV1", sensitive_fields=["payload.last_error_details"],
                    payload={"job_id": delivery.job_id, "job_type": NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE,
                        "checkpoint": job["checkpoint"], "attempt": attempt, "error_code": code,
                        "dead_lettered_at": now.isoformat(), "last_error_details": {"error_class": type(error).__name__}}))
            return None


def run_native_intake_source_policy_delivery(delivery, owner: NativeIntakeSourcePolicyOwner) -> dict | None:
    try:
        return owner.execute(delivery)
    except Exception as error:
        return owner.settle_failure(delivery, error)
