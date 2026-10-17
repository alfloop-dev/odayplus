"""Execute native exact URL identity checks from a proven native-start result.

Global registry recognition is separate from source approval. This SQL-only
phase never retrieves content, creates a Listing or invents a match case.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5

from modules.listing.application.native_intake_job_runtime import (
    decode_native_intake_attempt,
    load_native_intake_policy,
    set_native_intake_sql_budget,
    sign_native_intake_attempt,
)
from modules.listing.application.native_intake_start import NativeIntakeStartOwner
from modules.listing.application.native_intake_url_identity import (
    NATIVE_URL_RULE_SOURCE_SHA256,
    NATIVE_URL_RULE_VERSION,
    NativeURLIdentityUnsupported,
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

NATIVE_INTAKE_IDENTITY_JOB_TYPE = "native-intake-identity"
POLICY_ENV = "ODP_NATIVE_INTAKE_IDENTITY_POLICY_JSON"
SIGNING_KEY_ENV = "ODP_NATIVE_INTAKE_IDENTITY_SIGNING_KEY"
# Both durable claim/heartbeat currently grant 45 seconds. Leave five seconds
# of minimum headroom; identity uses 30s and execution uses the current lease, not this
# assumption, to bound its SQL waits and final acceptance.
MAX_COMMAND_SECONDS = 30


class NativeIntakeIdentityUnavailable(RuntimeError):
    pass


class NativeIntakeIdentityConflict(ValueError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _object(value: Any) -> dict:
    value = json.loads(value) if isinstance(value, str) else value
    if not isinstance(value, dict):
        raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_INVALID_OBJECT")
    return value


def _uuid(value: Any) -> str:
    if not isinstance(value, str) or not UUID(value).int or str(UUID(value)) != value:
        raise ValueError("A nonzero canonical UUID is required")
    return value


def _time(value: Any) -> datetime:
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_TIME_UNAVAILABLE")
    return result


def native_intake_identity_policy() -> dict:
    return load_native_intake_policy(env_name=POLICY_ENV, max_seconds=MAX_COMMAND_SECONDS,
        unavailable=NativeIntakeIdentityUnavailable, error_code="NATIVE_IDENTITY_POLICY_UNAVAILABLE", max_attempts=3)


class NativeIntakeIdentityAuthenticator:
    """Authenticate the opaque capability before binding or reading a tenant."""

    @staticmethod
    def sign(job_id: str, tenant: str, generation: int) -> str:
        return sign_native_intake_attempt(NATIVE_INTAKE_IDENTITY_JOB_TYPE, SIGNING_KEY_ENV,
            job_id, tenant, generation, unavailable=NativeIntakeIdentityUnavailable,
            error_code="NATIVE_IDENTITY_SIGNING_KEY_UNAVAILABLE")

    def decode(self, delivery) -> tuple[str, int]:
        return decode_native_intake_attempt(delivery, job_type=NATIVE_INTAKE_IDENTITY_JOB_TYPE,
            sign=self.sign, error_code="NATIVE_IDENTITY_TOKEN_REJECTED")


class NativeIntakeIdentityOwner:
    def __init__(self, graph: Any) -> None:
        self.graph, self.engine, self.queue = graph, graph.engine, graph.queue
        if getattr(self.engine, "dialect", None) != "postgresql":
            raise NativeIntakeIdentityUnavailable("NATIVE_IDENTITY_POSTGRESQL_REQUIRED")
        self.auth = NativeIntakeIdentityAuthenticator()

    def _clock(self) -> datetime:
        return _time(self.engine.query_one("SELECT clock_timestamp() AS now")["now"])

    @staticmethod
    def _partition(policy: dict, tenant: str) -> None:
        _uuid(tenant)
        if tenant not in policy["tenant_ids"]:
            raise PermissionError("NATIVE_IDENTITY_TENANT_NOT_CONFIGURED")

    def _audit(self, intake: dict, action: str, before: Any, after: dict, at: datetime, *,
               audit_id: str, causation: str) -> None:
        self.graph.audit_sink.persist({
            "audit_event_id": audit_id, "tenant_id": intake["tenant_id"],
            "resource_type": "intake", "resource_id": intake["intake_id"],
            "action": action, "result": "SUCCEEDED", "actor_subject_id": None,
            "service_principal": NATIVE_INTAKE_IDENTITY_JOB_TYPE, "actor_role": "SVC_INTAKE",
            "correlation_id": intake["correlation_id"], "causation_id": causation,
            "occurred_at": at.isoformat(), "before_value": before, "after_value": after,
            "reason": "Check an existing native canonical URL identity.",
        }, {key: intake[key] for key in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")})

    def _event(self, tenant: str, event_id: str) -> tuple[dict, dict, str]:
        row = self.graph._one("SELECT * FROM workflow.outbox_events WHERE tenant_id=? AND event_id=?",
                              (tenant, _uuid(event_id)))
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
                or event.get("actor_ref") != "native-intake-start"
                or event["payload"].get("intake_id") != row["aggregate_id"]
                or event["payload"].get("from_state") != "SUBMITTED"
                or event["payload"].get("to_state") != "CHECKING_IDENTITY"
                or type(event["payload"].get("version")) is not int
                or event["payload"]["version"] != event["aggregate_version"]):
            raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_START_EVENT_CONFLICT")
        transition = self.graph._one("SELECT job_id FROM intake.intake_stage_transitions "
            "WHERE tenant_id=? AND transition_id=? AND intake_id=?", (tenant,
                _uuid(event["payload"]["transition_id"]), row["aggregate_id"]))
        start_job = self.graph._one("SELECT payload FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                                    (tenant, transition["job_id"]))
        generation = _object(start_job["payload"]).get("delivery_generation")
        delivery = self.queue.get(transition["job_id"])
        # Verify the producer's immutable terminal proof. This is historical
        # evidence validation, not a new native-start capability or key grant.
        prior = NativeIntakeStartOwner(self.graph)._committed_result(delivery, tenant, generation) if delivery else None
        if prior is None or prior["outbox_event_id"] != event_id:
            raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_START_PROOF_MISSING")
        return row, event, hashlib.sha256(_json(event).encode()).hexdigest()

    def _input_binding(self, intake: dict, event: dict, expected_version: int, *, pending: bool) -> None:
        if (type(expected_version) is not int or expected_version < 2
                or intake["deleted_at"] is not None or event["aggregate_version"] != expected_version
                or intake["tenant_id"] != event["tenant_id"] or intake["intake_id"] != event["aggregate_id"]
                or intake["correlation_id"] != event["correlation_id"]
                or (pending and (intake["version"] != expected_version
                                  or intake["processing_state"] != "CHECKING_IDENTITY"
                                  or any(intake[key] is not None for key in (
                                      "source_id", "source_policy_state", "canonical_url",
                                      "canonical_url_sha256", "resolved_listing_id"))))):
            raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_INTAKE_BINDING_CONFLICT")
        producer = NativeIntakeStartOwner(self.graph)
        _, submitted, submitted_digest = producer._event(intake["tenant_id"], event["causation_id"])
        original_job = self.graph._one("SELECT j.payload FROM workflow.jobs j "
            "JOIN intake.intake_stage_transitions t ON t.tenant_id=j.tenant_id AND t.job_id=j.job_id "
            "WHERE t.tenant_id=? AND t.transition_id=? AND t.intake_id=? AND j.job_type='native-intake-start'",
            (intake["tenant_id"], event["payload"]["transition_id"], intake["intake_id"]))
        original_payload = _object(original_job["payload"])
        # The producer's terminal audit already binds this original hash.
        # Mutable actor/scope claims cannot substitute a new submission intent.
        if (original_payload.get("event_id") != submitted["event_id"]
                or original_payload.get("event_sha256") != submitted_digest):
            raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_ORIGINAL_SUBMISSION_PROOF_CONFLICT")
        producer._input_binding(intake, submitted, submitted["aggregate_version"], pending=False)

    @staticmethod
    def _input_digest(intake: dict) -> str:
        keys = ("intake_id", "tenant_id", "intake_method", "original_url", "submitter_subject_id",
                "correlation_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")
        return hashlib.sha256(_json({key: intake[key] for key in keys}).encode()).hexdigest()

    def _schema(self) -> None:
        indexes = self.engine.query("SELECT c.relname,i.indisunique FROM pg_catalog.pg_index i "
            "JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='intake' AND c.relname IN "
            "('ux_intakes_exact_url_active','ix_intakes_canonical_url_history')")
        if {row["relname"]: row["indisunique"] for row in indexes} != {"ix_intakes_canonical_url_history": False}:
            raise NativeIntakeIdentityUnavailable("NATIVE_IDENTITY_CURRENT_SCHEMA_REQUIRED")


    def admit_event(self, tenant: str, event_id: str) -> dict:
        policy = native_intake_identity_policy()
        self._partition(policy, tenant)
        with self.graph.transaction(tenant):
            self._schema()
            _, event, event_hash = self._event(tenant, event_id)
            intake_id, version = event["aggregate_id"], event["aggregate_version"]
            job_id = str(uuid5(UUID(intake_id), NATIVE_INTAKE_IDENTITY_JOB_TYPE))
            # Same transport -> business -> Intake order as execution. An
            # absent reservation is protected by this SERIALIZABLE unit.
            self.engine.query_one("SELECT job_id FROM durable_jobs WHERE job_id=? FOR UPDATE", (job_id,))
            prior = self.engine.query_one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=? FOR UPDATE",
                                          (tenant, job_id))
            intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE",
                                      (tenant, intake_id))
            self._input_binding(intake, event, version, pending=prior is None)
            token = self.auth.sign(job_id, tenant, 1)
            if prior is not None:
                payload = _object(prior["payload"])
                if payload.get("input_sha256") != self._input_digest(intake):
                    raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_INPUT_CHANGED")
                current = self.queue.get(job_id)
                if (prior["job_type"] != NATIVE_INTAKE_IDENTITY_JOB_TYPE or prior["aggregate_type"] != "intake"
                        or prior["aggregate_id"] != intake_id or prior["idempotency_key"] != intake_id
                        or payload.get("event_id") != event_id or payload.get("event_sha256") != event_hash
                        or payload.get("intake_version") != version or payload.get("policy") != policy
                        or current is None or current.payload != {"job_id": job_id, "tenant_id": tenant,
                                                                 "attempt_token": token}
                        or current.job_type != NATIVE_INTAKE_IDENTITY_JOB_TYPE):
                    raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_RESERVATION_CONFLICT")
                return self._admission_receipt(prior, payload)
            at, audit_id = self._clock(), str(uuid4())
            deadline = at + timedelta(seconds=policy["deadline_seconds"])
            receipt = {"job_id": job_id, "intake_id": intake_id, "admitted_intake_version": version,
                       "job_status_at_admission": "QUEUED", "timeout_at": deadline.isoformat(),
                       "identity_started_event_id": event_id, "audit_event_id": audit_id}
            payload = {"event_id": event_id, "event_sha256": event_hash, "intake_version": version,
                       "policy": policy, "delivery_generation": 1,
                       "attempt_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                       "submitter_subject_id": intake["submitter_subject_id"],
                       "resolution_mode": NATIVE_URL_RULE_VERSION, "input_sha256": self._input_digest(intake),
                       "rule_source_sha256": NATIVE_URL_RULE_SOURCE_SHA256, "admission_receipt": receipt}
            self.engine.execute("INSERT INTO workflow.jobs (job_id,tenant_id,job_type,aggregate_type,aggregate_id,"
                "status,checkpoint,attempt,max_attempts,fence_token,timeout_at,payload,correlation_id,idempotency_key) "
                "VALUES (?,?,?,'intake',?,'QUEUED','CHECKING_IDENTITY',0,?,1,?,?::jsonb,?,?)",
                (job_id, tenant, NATIVE_INTAKE_IDENTITY_JOB_TYPE, intake_id, policy["max_attempts"],
                 deadline.isoformat(), _json(payload), intake["correlation_id"], intake_id))
            delivery, created = self.queue.enqueue(JobRequest(NATIVE_INTAKE_IDENTITY_JOB_TYPE,
                {"job_id": job_id, "tenant_id": tenant, "attempt_token": token},
                idempotency_key=NATIVE_INTAKE_IDENTITY_JOB_TYPE + ":" + tenant + ":" + intake_id,
                job_id=job_id), correlation_id=intake["correlation_id"])
            if not created or delivery.job_id != job_id:
                raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_DELIVERY_RESERVATION_CONFLICT")
            self.engine.execute("UPDATE durable_jobs SET max_retries=? WHERE job_id=?",
                                (policy["max_attempts"], job_id))
            self._audit(intake, "intake.identity_resolution_requested.v1", None,
                        {**receipt, "native_identity_provenance": self._provenance(payload)}, at,
                        audit_id=audit_id, causation=event_id)
            return receipt

    def _admission_receipt(self, job: dict, payload: dict) -> dict:
        receipt = _object(payload.get("admission_receipt"))
        if (set(receipt) != {"job_id", "intake_id", "admitted_intake_version", "job_status_at_admission",
                             "timeout_at", "identity_started_event_id", "audit_event_id"}
                or receipt.get("job_id") != job["job_id"] or receipt.get("intake_id") != job["aggregate_id"]
                or type(receipt.get("admitted_intake_version")) is not int
                or receipt["admitted_intake_version"] != payload["intake_version"]
                or receipt.get("job_status_at_admission") != "QUEUED"
                or receipt.get("identity_started_event_id") != payload["event_id"]
                or _time(receipt["timeout_at"]) != _time(job["timeout_at"])):
            raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_ADMISSION_RECEIPT_CONFLICT")
        audit = self.engine.query_one("SELECT after_value FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=? "
            "AND resource_type='intake' AND resource_id=? AND actor_subject_id IS NULL AND service_principal=? "
            "AND actor_role='SVC_INTAKE' AND action='intake.identity_resolution_requested.v1' AND result='SUCCEEDED' "
            "AND causation_id=? AND correlation_id=?", (job["tenant_id"], receipt["audit_event_id"], job["aggregate_id"],
                NATIVE_INTAKE_IDENTITY_JOB_TYPE, payload["event_id"], job["correlation_id"]))
        if audit is None:
            raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_ADMISSION_AUDIT_MISSING")
        snapshot = _object(audit["after_value"])
        if (any(snapshot.get(k) != v for k, v in receipt.items())
                or snapshot.get("native_identity_provenance") != self._provenance(payload)):
            raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_ADMISSION_AUDIT_CONFLICT")
        return receipt

    def admit_partition(self, tenant: str, *, limit: int):
        """Yield one committed receipt at a time; a batch is not one transaction."""
        self._partition(native_intake_identity_policy(), tenant)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("A bounded partition size from 1 to 100 is required")
        with self.graph.transaction(tenant):
            rows = self.engine.query("SELECT e.event_id FROM workflow.outbox_events e "
                "JOIN intake.intakes i ON i.tenant_id=e.tenant_id AND i.intake_id=e.aggregate_id "
                "WHERE e.tenant_id=? AND e.event_type='intake.state_changed' AND e.aggregate_type='intake' "
                "AND i.processing_state='CHECKING_IDENTITY' AND i.deleted_at IS NULL "
                "AND e.payload->>'actor_ref'='native-intake-start' "
                "AND NOT EXISTS (SELECT 1 FROM workflow.jobs j WHERE j.tenant_id=e.tenant_id "
                "AND j.job_type=? AND j.idempotency_key=i.intake_id::text) "
                "ORDER BY e.occurred_at,e.event_id LIMIT ?", (tenant, NATIVE_INTAKE_IDENTITY_JOB_TYPE, limit))
        for row in rows:
            yield self.admit_event(tenant, row["event_id"])

    def _budget(self, timeout: Any, lease: Any) -> None:
        # Recompute at each lock/write boundary. The final conditional writes
        # below independently reject a deadline/lease that elapsed meanwhile.
        set_native_intake_sql_budget(self.engine, _time(timeout), _time(lease), self._clock(),
                                     error_code="NATIVE_IDENTITY_DEADLINE_OR_LEASE_EXPIRED")

    def _locked(self, delivery, tenant: str, generation: int, *, settlement: bool = False) -> tuple[Any, dict, dict]:
        # Deliberately retain the existing identity coordinator's small
        # transport predicate; its decision-specific business fence is not
        # applicable to this Intake command. Both paths get regression coverage.
        preliminary = self.graph._one("SELECT timeout_at FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                                      (tenant, delivery.job_id))
        peek = self.queue.get(delivery.job_id)
        if peek is None or peek.lease_expires_at is None:
            raise JobFenceRejectedError("NATIVE_IDENTITY_DELIVERY_NOT_OWNED")
        # Expired business work may still be protectively settled by its live
        # delivery owner; it cannot receive another Intake mutation grant.
        budget = peek.lease_expires_at if settlement else preliminary["timeout_at"]
        self._budget(budget, peek.lease_expires_at)
        self.graph._one("SELECT job_id FROM durable_jobs WHERE job_id=? FOR UPDATE", (delivery.job_id,))
        current = self.queue.get(delivery.job_id)
        now = self._clock()
        if (current is None or current.job_type != NATIVE_INTAKE_IDENTITY_JOB_TYPE
                or current.status != JobStatus.RUNNING or not current.locked_by
                or current.locked_by != delivery.locked_by or current.fence_token != delivery.fence_token
                or current.payload != delivery.payload or current.correlation_id != delivery.correlation_id
                or current.lease_expires_at is None or current.lease_expires_at <= now):
            raise JobFenceRejectedError("NATIVE_IDENTITY_DELIVERY_NOT_OWNED")
        job = self.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=? FOR UPDATE",
                              (tenant, delivery.job_id))
        payload = _object(job["payload"])
        if (job["job_type"] != NATIVE_INTAKE_IDENTITY_JOB_TYPE or job["aggregate_type"] != "intake"
                or job["idempotency_key"] != job["aggregate_id"]
                or job["correlation_id"] != current.correlation_id
                or type(payload.get("delivery_generation")) is not int
                or payload.get("delivery_generation") != generation
                or payload.get("resolution_mode") != NATIVE_URL_RULE_VERSION
                or payload.get("attempt_token_sha256") != hashlib.sha256(delivery.payload["attempt_token"].encode()).hexdigest()):
            raise JobFenceRejectedError("NATIVE_IDENTITY_ADMISSION_NOT_OWNED")
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
        if target not in {"READY", "CHECKING_SOURCE_POLICY"}:
            raise JobFenceRejectedError("NATIVE_IDENTITY_TERMINAL_STATE_CONFLICT")
        permission = "identity.resolve" if target == "READY" else "source_policy.evaluate"
        action = "intake.resolved.v1" if target == "READY" else "intake.source_policy_started.v1"
        proof = _object(result.get("identity_evidence"))
        if (current is None or current.status != JobStatus.SUCCEEDED or current.payload != delivery.payload
                or current.job_type != NATIVE_INTAKE_IDENTITY_JOB_TYPE
                or job["job_type"] != NATIVE_INTAKE_IDENTITY_JOB_TYPE or job["aggregate_type"] != "intake"
                or job["idempotency_key"] != job["aggregate_id"] or current.correlation_id != job["correlation_id"]
                or type(payload.get("delivery_generation")) is not int
                or payload["delivery_generation"] != generation
                or payload.get("attempt_token_sha256") != hashlib.sha256(delivery.payload["attempt_token"].encode()).hexdigest()
                or set(result) != {"job_id", "intake_id", "processing_state", "version", "transition_id",
                                   "audit_event_id", "outbox_event_id", "correlation_id", "identity_evidence"}
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
            raise JobFenceRejectedError("NATIVE_IDENTITY_TERMINAL_RECEIPT_CONFLICT")
        self._admission_receipt(job, payload)
        if not self.engine.query_one("SELECT transition_id FROM intake.intake_stage_transitions "
            "WHERE tenant_id=? AND transition_id=? AND intake_id=? AND job_id=? "
            "AND from_state='CHECKING_IDENTITY' AND to_state=? AND resulting_version=? "
            "AND expected_version=? AND actor_subject_id IS NULL AND service_principal=? "
            "AND permission=? AND source_policy_version=? AND causation_id=? AND correlation_id=?",
            (tenant, result["transition_id"], job["aggregate_id"], delivery.job_id, target,
             result["version"], payload["intake_version"], NATIVE_INTAKE_IDENTITY_JOB_TYPE,
             permission, proof["source_version"], payload["event_id"], job["correlation_id"])):
            raise JobFenceRejectedError("NATIVE_IDENTITY_TERMINAL_TRANSITION_CONFLICT")
        audit = self.engine.query_one("SELECT after_value FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=? "
            "AND resource_type='intake' AND resource_id=? AND actor_subject_id IS NULL AND service_principal=? "
            "AND actor_role='SVC_INTAKE' AND action=? AND result='SUCCEEDED' AND causation_id=? AND correlation_id=?",
            (tenant, result["audit_event_id"], job["aggregate_id"], NATIVE_INTAKE_IDENTITY_JOB_TYPE,
             action, payload["event_id"], job["correlation_id"]))
        outbox = self.engine.query_one("SELECT payload FROM workflow.outbox_events WHERE tenant_id=? AND event_id=? "
            "AND event_type='intake.state_changed' AND aggregate_type='intake' AND aggregate_id=? "
            "AND aggregate_version=? AND correlation_id=?", (tenant, result["outbox_event_id"],
                job["aggregate_id"], result["version"], job["correlation_id"]))
        if audit is None or outbox is None:
            raise JobFenceRejectedError("NATIVE_IDENTITY_TERMINAL_EVIDENCE_MISSING")
        snapshot, event = _object(audit["after_value"]), _object(outbox["payload"])
        if (any(snapshot.get(key) != value for key, value in result.items())
                or snapshot.get("native_identity_provenance") != self._provenance(payload)
                or validate_event(event) or event.get("event_id") != result["outbox_event_id"]
                or event.get("event_type") != "intake.state_changed"
                or event.get("producer") != "listing_intake_service"
                or event.get("schema_ref") != "#/payloads/IntakeStateChangedV1"
                or type(event.get("event_version")) is not int or event["event_version"] != 1
                or type(event.get("aggregate_version")) is not int
                or event["tenant_id"] != tenant or event["aggregate_id"] != job["aggregate_id"]
                or event["aggregate_version"] != result["version"]
                or event.get("actor_ref") != NATIVE_INTAKE_IDENTITY_JOB_TYPE
                or event.get("causation_id") != payload["event_id"]
                or event["correlation_id"] != job["correlation_id"]
                or event["payload"].get("transition_id") != result["transition_id"]
                or event["payload"].get("intake_id") != job["aggregate_id"]
                or event["payload"].get("from_state") != "CHECKING_IDENTITY"
                or event["payload"].get("to_state") != target
                or type(event["payload"].get("version")) is not int
                or event["payload"]["version"] != result["version"]):
            raise JobFenceRejectedError("NATIVE_IDENTITY_TERMINAL_EVIDENCE_CONFLICT")
        _, _, digest = self._event(tenant, payload["event_id"])
        if digest != payload["event_sha256"]:
            raise JobFenceRejectedError("NATIVE_IDENTITY_ORIGINAL_EVENT_CHANGED")
        return result

    @staticmethod
    def _provenance(payload: dict) -> dict:
        return {**{key: payload[key] for key in ("event_id", "event_sha256", "intake_version",
                    "delivery_generation", "input_sha256", "rule_source_sha256", "resolution_mode")},
                "policy_sha256": hashlib.sha256(_json(payload["policy"]).encode()).hexdigest()}


    @staticmethod
    def _source_reusable(source: dict, now: datetime) -> bool:
        try:
            owner = _uuid(source["policy_owner_subject_id"])
            return (owner != str(uuid5(NAMESPACE_DNS, "system"))
                and source["retrieval_mode"] == "APPROVED_RETRIEVAL"
                and source["production_enabled"] is True and source["kill_switch"] is False
                and all(isinstance(source.get(key), str) and source[key].strip()
                        for key in ("legal_approval_ref", "license_approval_ref"))
                and source.get("review_expires_at") is not None
                and _time(source["review_expires_at"]) > now)
        except (ValueError, TypeError, KeyError, AttributeError):
            return False

    def _resolve(self, intake: dict, job: dict, current) -> tuple[dict, Any, str, str | None, dict]:
        if intake["intake_method"] != "URL":
            raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_METHOD_UNSUPPORTED")
        identity = normalize_native_intake_url(intake["original_url"])
        tenant = intake["tenant_id"]

        def query(sql, args=()):
            self._budget(job["timeout_at"], current.lease_expires_at)
            return self.engine.query(sql, args)

        # Registry rows are GLOBAL. Count all host matches before checking
        # their rule version; an unsupported second source is still ambiguous.
        sources = query("SELECT s.* FROM intake.source_registry s WHERE EXISTS "
            "(SELECT 1 FROM unnest(s.allowed_hosts) AS h(host) WHERE lower(h.host)=?) "
            "ORDER BY s.source_id LIMIT 2 FOR SHARE", (identity.source_host,))
        if len(sources) != 1 or sources[0]["canonicalization_rule_version"] != NATIVE_URL_RULE_VERSION:
            raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_SOURCE_UNSUPPORTED_OR_AMBIGUOUS")
        source = sources[0]
        hosts = source["allowed_hosts"]
        if isinstance(hosts, str):
            hosts = json.loads(hosts)
        if not isinstance(hosts, list) or not 1 <= len(hosts) <= 64:
            raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_REGISTRY_HOST_BOUND_UNSUPPORTED")
        for host in hosts:
            if not isinstance(host, str) or normalize_native_intake_url("https://" + host).source_host != host.lower():
                raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_REGISTRY_HOST_UNSUPPORTED")
        at = self._clock()
        proof = {"source_id": source["source_id"], "source_version": source["version"],
                 "source_host": identity.source_host, "canonical_url_sha256": identity.canonical_url_sha256,
                 "normalization_rule_version": identity.rule_version,
                 "normalization_rule_source_sha256": identity.rule_source_sha256,
                 "checked_at": at.isoformat(), "outcome": "SOURCE_POLICY_REQUIRED",
                 "reason": "REUSE_POLICY_DENIED", "valid_until": None}
        if not self._source_reusable(source, at):
            return source, identity, "CHECKING_SOURCE_POLICY", None, proof
        # Native properties represent region, but not brand/area/heat-zone.
        # A service tenant partition is not a substitute for that resource proof.
        if any(intake[key] is not None for key in ("brand_id", "assigned_area_id", "heat_zone_id")):
            raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_SCOPE_UNREPRESENTABLE")
        listings = query("SELECT * FROM expansion.listings WHERE tenant_id=? AND source_id=? "
            "AND canonical_url_sha256=? ORDER BY listing_id LIMIT 2",
            (tenant, source["source_id"], identity.canonical_url_sha256))
        if not listings:
            return source, identity, "CHECKING_SOURCE_POLICY", None, {**proof, "reason": "EXACT_URL_ABSENT"}
        if len(listings) != 1 or listings[0]["lifecycle_state"] != "ACTIVE" or listings[0]["archived_at"] is not None:
            raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_EXISTING_LISTING_CONTRADICTORY")
        peek = listings[0]
        edge_sql = ("SELECT * FROM identity.source_identity_edges WHERE tenant_id=? "
            "AND (listing_id=? OR (source_id=? AND source_entity_id=?)) "
            "AND (effective_to IS NULL OR effective_to>?::timestamptz) ORDER BY edge_id LIMIT 17")
        edge_args = (tenant, peek["listing_id"], source["source_id"], peek["source_listing_id"], at.isoformat())
        edge_peek = query(edge_sql, edge_args)
        if len(edge_peek) > 16:
            raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_GRAPH_BOUND_EXCEEDED")
        active_edges = [row for row in edge_peek if _time(row["effective_from"]) <= at]
        roots = sorted({_uuid(row["property_id"]) for row in active_edges}
                       | ({_uuid(peek["property_id"])} if peek["property_id"] else set()))
        if not roots:
            raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_PROPERTY_MISSING")
        reach = query("WITH RECURSIVE reach(property_id,path,depth,is_cycle) AS ("
            "SELECT property_id,ARRAY[property_id],0,false FROM identity.properties "
            "WHERE tenant_id=? AND property_id=ANY(?::uuid[]) UNION ALL "
            "SELECT r.to_property_id,p.path||r.to_property_id,p.depth+1,r.to_property_id=ANY(p.path) "
            "FROM reach p JOIN identity.property_redirects r ON r.tenant_id=? AND r.from_property_id=p.property_id "
            "WHERE r.reversed_at IS NULL AND r.effective_from<=?::timestamptz AND p.depth<8 AND NOT p.is_cycle) "
            "SELECT property_id,depth,is_cycle FROM reach", (tenant, roots, tenant, at.isoformat()))
        if any(row["is_cycle"] for row in reach):
            raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_REDIRECT_CYCLE")
        property_ids = sorted({row["property_id"] for row in reach})
        properties = query("SELECT * FROM identity.properties WHERE tenant_id=? AND property_id=ANY(?::uuid[]) "
                           "ORDER BY property_id FOR SHARE", (tenant, property_ids))
        props = {row["property_id"]: row for row in properties}
        locked = query("SELECT * FROM expansion.listings WHERE tenant_id=? AND listing_id=? FOR SHARE",
                       (tenant, peek["listing_id"]))
        if not locked or locked[0] != peek:
            raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_LISTING_CHANGED")
        edges = query(edge_sql + " FOR SHARE", edge_args)
        redirects = query("SELECT * FROM identity.property_redirects WHERE tenant_id=? "
            "AND from_property_id=ANY(?::uuid[]) AND reversed_at IS NULL "
            "ORDER BY from_property_id,redirect_id FOR SHARE", (tenant, property_ids))
        if edges != edge_peek or set(props) != set(property_ids) or not set(roots).issubset(props):
            raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_GRAPH_CHANGED")
        if any(row["source_id"] != source["source_id"]
               or row["listing_id"] not in {None, peek["listing_id"]}
               or (peek["source_listing_id"] is not None and row["source_entity_id"] != peek["source_listing_id"])
               for row in active_edges):
            raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_EDGE_CONTRADICTORY")
        current_redirects = {row["from_property_id"]: row for row in redirects if _time(row["effective_from"]) <= at}
        if len(current_redirects) != sum(_time(row["effective_from"]) <= at for row in redirects):
            raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_REDIRECT_AMBIGUOUS")
        effective = set()
        for root in roots:
            cursor, seen = root, set()
            for _ in range(9):
                if cursor in seen or cursor not in props:
                    raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_REDIRECT_UNPROVEN")
                seen.add(cursor)
                prop, redirect = props[cursor], current_redirects.get(cursor)
                if redirect is None:
                    if prop["status"] != "ACTIVE" or prop["current_redirect_property_id"] is not None:
                        raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_PROPERTY_NOT_ACTIVE")
                    effective.add(cursor)
                    break
                if prop["status"] != "MERGED" or prop["current_redirect_property_id"] != redirect["to_property_id"]:
                    raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_REDIRECT_CONTRADICTORY")
                cursor = redirect["to_property_id"]
            else:
                raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_REDIRECT_BOUND_EXCEEDED")
        if len(effective) != 1:
            raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_CONTRADICTORY_OPEN_CONTRACT_GAP")
        effective_id = effective.pop()
        if intake["region_id"] is not None and props[effective_id]["region_id"] != intake["region_id"]:
            raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_REGION_DENIED")
        # A future-dated edge/redirect or an expiring current edge must not
        # become a different graph while this short command is being accepted.
        bounds = [_time(source["review_expires_at"])]
        bounds += [_time(row["effective_from"]) for row in edges + redirects if _time(row["effective_from"]) > at]
        bounds += [_time(row["effective_to"]) for row in active_edges if row["effective_to"] is not None]
        proof.update(outcome="EXACT_EXISTING", reason="EXACT_CANONICAL_URL", valid_until=min(bounds).isoformat(),
            listing_id=peek["listing_id"], listing_version=peek["version"], effective_property_id=effective_id,
            property_versions={key: props[key]["version"] for key in sorted(props)},
            edge_versions={row["edge_id"]: row["edge_version"] for row in edges},
            redirect_versions={row["redirect_id"]: row["version"] for row in redirects})
        return source, identity, "READY", peek["listing_id"], proof

    def execute(self, delivery) -> dict:
        tenant, generation = self.auth.decode(delivery)
        policy = native_intake_identity_policy()
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
                    or _time(job["timeout_at"]) <= now
                    or (job["next_attempt_at"] is not None and _time(job["next_attempt_at"]) > now)):
                raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_ATTEMPT_NOT_ADMITTED")
            self._budget(job["timeout_at"], current.lease_expires_at)
            intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE",
                                      (tenant, job["aggregate_id"]))
            _, event, digest = self._event(tenant, payload["event_id"])
            if (digest != payload["event_sha256"] or payload["input_sha256"] != self._input_digest(intake)
                    or payload["submitter_subject_id"] != intake["submitter_subject_id"]):
                raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_INPUT_CHANGED")
            version = payload["intake_version"]
            self._input_binding(intake, event, version, pending=True)
            self._budget(job["timeout_at"], current.lease_expires_at)
            if self.engine.execute("UPDATE workflow.jobs SET status='RUNNING',attempt=attempt+1,"
                "fence_token=fence_token+1,version=version+1,heartbeat_at=clock_timestamp(),lease_expires_at=? "
                "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? "
                "AND status IN ('QUEUED','RETRYING') AND attempt<max_attempts "
                "AND cancellation_requested_at IS NULL AND timeout_at>clock_timestamp()",
                (current.lease_expires_at.isoformat(), tenant, delivery.job_id,
                 job["version"], job["fence_token"])).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_IDENTITY_ACCEPTANCE_FENCE_REJECTED")
            job = {**job, "version": job["version"] + 1, "fence_token": job["fence_token"] + 1}
            source, identity, target, listing_id, proof = self._resolve(intake, job, current)
            proof.update(accepted_delivery_fence=current.fence_token, accepted_worker=current.locked_by,
                         accepted_business_fence=job["fence_token"], accepted_attempt=job["attempt"] + 1)
            permission = "identity.resolve" if target == "READY" else "source_policy.evaluate"
            action = "intake.resolved.v1" if target == "READY" else "intake.source_policy_started.v1"
            valid_until = proof["valid_until"]
            at, transition_id, audit_id = self._clock(), str(uuid4()), str(uuid4())
            aggregate = IntakeAggregate(id=intake["intake_id"], tenant_id=tenant,
                stage=IntakeStage(intake["processing_state"]), version=intake["version"],
                created_by=intake["submitter_subject_id"], source_id=source["source_id"],
                canonical_url=identity.canonical_url, owner_id=intake["submitter_subject_id"])
            IntakeStateMachine.transition(aggregate, IntakeStage(target), TransitionContext(
                actor=Actor(NATIVE_INTAKE_IDENTITY_JOB_TYPE, PrincipalRole.SVC_INTAKE, tenant),
                version_before=version, idempotency_key=payload["event_id"],
                correlation_id=intake["correlation_id"], causation_id=payload["event_id"], current_time=at))
            self._budget(job["timeout_at"], current.lease_expires_at)
            if self.engine.execute("UPDATE intake.intakes SET processing_state=?,version=?,last_transition_at=?,"
                "source_id=?,canonical_url=?,canonical_url_sha256=?,source_policy_state=NULL,resolved_listing_id=?,completed_at=? "
                "WHERE tenant_id=? AND intake_id=? AND processing_state='CHECKING_IDENTITY' AND version=? AND deleted_at IS NULL "
                "AND (?::timestamptz IS NULL OR ?::timestamptz>clock_timestamp())",
                (target, aggregate.version, at.isoformat(), source["source_id"], identity.canonical_url,
                 identity.canonical_url_sha256, listing_id, at.isoformat() if target == "READY" else None,
                 tenant, intake["intake_id"], version, valid_until, valid_until)).rowcount != 1:
                raise NativeIntakeIdentityConflict("NATIVE_IDENTITY_VERSION_OR_PROOF_EXPIRED")
            sequence = self.engine.query_one("SELECT COALESCE(MAX(sequence_no),0)+1 AS next "
                "FROM intake.intake_stage_transitions WHERE tenant_id=? AND intake_id=?",
                (tenant, intake["intake_id"]))["next"]
            self._budget(job["timeout_at"], current.lease_expires_at)
            self.engine.execute("INSERT INTO intake.intake_stage_transitions (transition_id,tenant_id,intake_id,sequence_no,"
                "from_state,to_state,actor_subject_id,service_principal,permission,reason_text,idempotency_key,"
                "expected_version,resulting_version,job_id,source_policy_version,correlation_id,causation_id,occurred_at) "
                "VALUES (?,?,?,?,'CHECKING_IDENTITY',?,NULL,?,?,?,?,?,?,?,?,?,?,?)",
                (transition_id, tenant, intake["intake_id"], sequence, target, NATIVE_INTAKE_IDENTITY_JOB_TYPE,
                 permission, "Check an existing native canonical URL identity.", payload["event_id"], version,
                 aggregate.version, delivery.job_id, source["version"], intake["correlation_id"],
                 payload["event_id"], at.isoformat()))
            state_event = DomainEvent(event_type="intake.state_changed", producer="listing_intake_service",
                tenant_id=tenant, aggregate_type="intake", aggregate_id=intake["intake_id"],
                aggregate_version=aggregate.version, partition_key=tenant + ":" + intake["intake_id"],
                correlation_id=intake["correlation_id"], causation_id=payload["event_id"],
                actor_ref=NATIVE_INTAKE_IDENTITY_JOB_TYPE, occurred_at=at,
                schema_ref="#/payloads/IntakeStateChangedV1", payload={"intake_id": intake["intake_id"],
                    "from_state": "CHECKING_IDENTITY", "to_state": target, "transition_id": transition_id,
                    "version": aggregate.version, "occurred_at": at.isoformat()})
            result = {"job_id": delivery.job_id, "intake_id": intake["intake_id"],
                      "processing_state": target, "version": aggregate.version,
                      "transition_id": transition_id, "audit_event_id": audit_id,
                      "outbox_event_id": state_event.event_id, "correlation_id": intake["correlation_id"],
                      "identity_evidence": proof}
            self._budget(job["timeout_at"], current.lease_expires_at)
            self._audit(intake, action, {"processing_state": "CHECKING_IDENTITY", "version": version},
                        {**result, "native_identity_provenance": self._provenance(payload)}, at,
                        audit_id=audit_id, causation=payload["event_id"])
            self._budget(job["timeout_at"], current.lease_expires_at)
            self.graph._outbox(state_event)
            self._budget(job["timeout_at"], current.lease_expires_at)
            if (native_intake_identity_policy() != policy
                    or not self.engine.query_one("SELECT job_id FROM durable_jobs WHERE job_id=? AND status=? "
                        "AND fence_token=? AND locked_by=? AND lease_expires_at>clock_timestamp()",
                        (delivery.job_id, JobStatus.RUNNING.value, current.fence_token, current.locked_by))):
                raise JobFenceRejectedError("NATIVE_IDENTITY_FINAL_DELIVERY_OR_POLICY_REJECTED")
            accepted = {**payload, "accepted_delivery_fence": current.fence_token,
                        "accepted_worker": current.locked_by}
            if self.engine.execute("UPDATE workflow.jobs SET status='SUCCEEDED',checkpoint=?,"
                "result=?::jsonb,payload=?::jsonb,version=version+1,heartbeat_at=?,lease_expires_at=NULL,updated_at=? "
                "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND status='RUNNING' "
                "AND cancellation_requested_at IS NULL AND timeout_at>clock_timestamp() "
                "AND (?::timestamptz IS NULL OR ?::timestamptz>clock_timestamp())",
                (target, _json(result), _json(accepted), at.isoformat(), at.isoformat(), tenant, delivery.job_id,
                 job["version"], job["fence_token"], valid_until, valid_until)).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_IDENTITY_FINAL_BUSINESS_OR_PROOF_REJECTED")
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
                raise JobFenceRejectedError("NATIVE_IDENTITY_UNKNOWN_ATTEMPT_OUTCOME")
            policy, now = payload["policy"], self._clock()
            try:
                current_policy = native_intake_identity_policy()
            except NativeIntakeIdentityUnavailable:
                current_policy = None
            cancelled = job["cancellation_requested_at"] is not None
            if isinstance(error, JobFenceRejectedError) and not cancelled and _time(job["timeout_at"]) > now:
                raise error
            attempt = min(job["attempt"] + 1, job["max_attempts"])
            retryable = (getattr(error, "sqlstate", None) in {"40001", "40P01"}
                         or isinstance(error, CanonicalAuditUnavailable))
            due = None
            if (not cancelled and retryable and policy == current_policy
                    and attempt < job["max_attempts"] and _time(job["timeout_at"]) > now):
                due = now + timedelta(seconds=policy["backoff_seconds"][attempt - 1])
                if due >= _time(job["timeout_at"]):
                    due = None
            status = "CANCELLED" if cancelled else "RETRYING" if due else "FAILED"
            code = "NATIVE_IDENTITY_CANCELLED" if cancelled else "NATIVE_IDENTITY_RETRY_SCHEDULED" if due else "NATIVE_IDENTITY_FAILED"
            # Preserve an operator-visible, compiled reason for deliberate
            # unsupported/contradictory outcomes. Never echo exception text
            # unless it is exactly one of these static non-sensitive codes.
            safe_reasons = {"NATIVE_IDENTITY_" + suffix for suffix in (
                "METHOD_UNSUPPORTED", "SOURCE_UNSUPPORTED_OR_AMBIGUOUS", "SCOPE_UNREPRESENTABLE",
                "EXISTING_LISTING_CONTRADICTORY", "EDGE_CONTRADICTORY", "GRAPH_BOUND_EXCEEDED",
                "REDIRECT_CYCLE", "REDIRECT_AMBIGUOUS", "REDIRECT_UNPROVEN", "PROPERTY_NOT_ACTIVE",
                "REDIRECT_CONTRADICTORY", "REDIRECT_BOUND_EXCEEDED", "CONTRADICTORY_OPEN_CONTRACT_GAP",
                "REGION_DENIED", "REGISTRY_HOST_UNSUPPORTED", "REGISTRY_HOST_BOUND_UNSUPPORTED",
                "URL_UNSUPPORTED", "HOST_UNSUPPORTED", "AUTHORITY_UNSUPPORTED", "PORT_UNSUPPORTED",
                "INPUT_CHANGED", "INTAKE_BINDING_CONFLICT", "ATTEMPT_NOT_ADMITTED",
                "ORIGINAL_SUBMISSION_PROOF_CONFLICT",
            )}
            if (status == "FAILED" and isinstance(error, (NativeIntakeIdentityConflict, NativeURLIdentityUnsupported))
                    and str(error) in safe_reasons):
                code = str(error)
            intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE",
                                      (tenant, job["aggregate_id"]))
            if self.engine.execute("UPDATE workflow.jobs SET status=?,attempt=?,last_error=?::jsonb,next_attempt_at=?,"
                "version=version+1,updated_at=? WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=?",
                (status, attempt, _json({"code": code, "error_class": type(error).__name__}),
                 due.isoformat() if due else None, now.isoformat(), tenant, delivery.job_id,
                 job["version"], job["fence_token"])).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_IDENTITY_SETTLEMENT_FENCE_REJECTED")
            self._audit(intake, "intake.identity_resolution_delivery_settled.v1",
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
                    actor_ref=NATIVE_INTAKE_IDENTITY_JOB_TYPE, occurred_at=now,
                    schema_ref="#/payloads/JobDeadLetteredV1", sensitive_fields=["payload.last_error_details"],
                    payload={"job_id": delivery.job_id, "job_type": NATIVE_INTAKE_IDENTITY_JOB_TYPE,
                        "checkpoint": job["checkpoint"], "attempt": attempt, "error_code": code,
                        "dead_lettered_at": now.isoformat(), "last_error_details": {"error_class": type(error).__name__}}))
            return None


def run_native_intake_identity_delivery(delivery, owner: NativeIntakeIdentityOwner) -> dict | None:
    try:
        return owner.execute(delivery)
    except Exception as error:
        return owner.settle_failure(delivery, error)
