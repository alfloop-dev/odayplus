"""Start native identity checking from a committed submission, without resolving it.

This short, SQL-only command owns admission, its transport and one transition.
Source resolution, retrieval, parsing and matching are subsequent work. Runtime
policy and the separate signing key are required inputs, never fixture defaults.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4, uuid5

from modules.listing.application.native_intake_job_runtime import (
    decode_native_intake_attempt,
    load_native_intake_policy,
    set_native_intake_sql_budget,
    sign_native_intake_attempt,
)
from modules.listing.application.native_intake_producer_reads import native_terminal_pair
from modules.listing.application.native_intake_terminal_evidence import native_terminal_evidence
from modules.listing.application.native_intake_terminal_ticket import (
    NativeIntakeTerminalTicketConflict,
    verify_native_intake_terminal_ticket,
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

NATIVE_INTAKE_START_JOB_TYPE = "native-intake-start"
POLICY_ENV = "ODP_NATIVE_INTAKE_START_POLICY_JSON"
SIGNING_KEY_ENV = "ODP_NATIVE_INTAKE_START_SIGNING_KEY"
# Both durable claim/heartbeat currently grant 45 seconds. Leave five seconds
# of headroom; execution additionally uses the actual current lease, not this
# assumption, to bound its SQL waits and final acceptance.
MAX_COMMAND_SECONDS = 40


class NativeIntakeStartUnavailable(RuntimeError):
    pass


class NativeIntakeStartConflict(ValueError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _object(value: Any) -> dict:
    value = json.loads(value) if isinstance(value, str) else value
    if not isinstance(value, dict):
        raise NativeIntakeStartConflict("NATIVE_START_INVALID_OBJECT")
    return value


def _uuid(value: Any) -> str:
    if not isinstance(value, str) or not UUID(value).int or str(UUID(value)) != value:
        raise ValueError("A nonzero canonical UUID is required")
    return value


def _time(value: Any) -> datetime:
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise NativeIntakeStartConflict("NATIVE_START_TIME_UNAVAILABLE")
    return result


def native_intake_start_policy() -> dict:
    return load_native_intake_policy(env_name=POLICY_ENV, max_seconds=MAX_COMMAND_SECONDS,
        unavailable=NativeIntakeStartUnavailable, error_code="NATIVE_START_POLICY_UNAVAILABLE")


class NativeIntakeStartAuthenticator:
    """Authenticate the opaque capability before binding or reading a tenant."""

    @staticmethod
    def sign(job_id: str, tenant: str, generation: int) -> str:
        return sign_native_intake_attempt(NATIVE_INTAKE_START_JOB_TYPE, SIGNING_KEY_ENV,
            job_id, tenant, generation, unavailable=NativeIntakeStartUnavailable,
            error_code="NATIVE_START_SIGNING_KEY_UNAVAILABLE")

    def decode(self, delivery) -> tuple[str, int]:
        return decode_native_intake_attempt(delivery, job_type=NATIVE_INTAKE_START_JOB_TYPE,
            sign=self.sign, error_code="NATIVE_START_TOKEN_REJECTED")


class NativeIntakeStartOwner:
    def __init__(self, graph: Any) -> None:
        self.graph, self.engine, self.queue = graph, graph.engine, graph.queue
        if getattr(self.engine, "dialect", None) != "postgresql":
            raise NativeIntakeStartUnavailable("NATIVE_START_POSTGRESQL_REQUIRED")
        self.auth = NativeIntakeStartAuthenticator()

    def _clock(self) -> datetime:
        return _time(self.engine.query_one("SELECT clock_timestamp() AS now")["now"])

    @staticmethod
    def _partition(policy: dict, tenant: str) -> None:
        _uuid(tenant)
        if tenant not in policy["tenant_ids"]:
            raise PermissionError("NATIVE_START_TENANT_NOT_CONFIGURED")

    def _audit(self, intake: dict, action: str, before: Any, after: dict, at: datetime, *,
               audit_id: str, causation: str) -> None:
        self.graph.audit_sink.persist({
            "audit_event_id": audit_id, "tenant_id": intake["tenant_id"],
            "resource_type": "intake", "resource_id": intake["intake_id"],
            "action": action, "result": "SUCCEEDED", "actor_subject_id": None,
            "service_principal": NATIVE_INTAKE_START_JOB_TYPE, "actor_role": "SVC_INTAKE",
            "correlation_id": intake["correlation_id"], "causation_id": causation,
            "occurred_at": at.isoformat(), "before_value": before, "after_value": after,
            "reason": "Start identity checking for a committed native submission.",
        }, {key: intake[key] for key in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")})

    def _event(self, tenant: str, event_id: str) -> tuple[dict, dict, str]:
        row = self.graph._one("SELECT * FROM workflow.outbox_events WHERE tenant_id=? AND event_id=?",
                              (tenant, _uuid(event_id)))
        event = _object(row["payload"])
        if (validate_event(event) or row["event_type"] != "intake.submitted"
                or event.get("producer") != "listing_intake_service"
                or event.get("schema_ref") != "#/payloads/IntakeSubmittedV1"
                or type(event.get("event_version")) is not int or event["event_version"] != 1
                or type(event.get("aggregate_version")) is not int
                or event["event_type"] != "intake.submitted" or event["tenant_id"] != tenant
                or row["aggregate_type"] != "intake" or event["aggregate_type"] != "intake"
                or row["event_id"] != event["event_id"] or row["aggregate_id"] != event["aggregate_id"]
                or row["aggregate_version"] != event["aggregate_version"]
                or row["event_version"] != event["event_version"]
                or row["correlation_id"] != event["correlation_id"]
                or row["partition_key"] != tenant + ":" + row["aggregate_id"]
                or event["partition_key"] != row["partition_key"]
                or event["payload"]["intake_id"] != row["aggregate_id"]):
            raise NativeIntakeStartConflict("NATIVE_START_SUBMISSION_BINDING_CONFLICT")
        return row, event, hashlib.sha256(_json(event).encode()).hexdigest()

    @staticmethod
    def _input_binding(intake: dict, event: dict, expected_version: int, *, pending: bool) -> None:
        payload = event["payload"]
        if (type(expected_version) is not int or expected_version < 1 or intake["deleted_at"] is not None
                or expected_version != event["aggregate_version"]
                or intake["tenant_id"] != event["tenant_id"] or intake["intake_id"] != event["aggregate_id"]
                or intake["submitter_subject_id"] != payload["submitter_subject_id"]
                or event.get("actor_ref") != intake["submitter_subject_id"]
                or intake["intake_method"] != payload["intake_method"]
                or intake["correlation_id"] != event["correlation_id"]
                or any(payload["scope"].get(key) != intake[key]
                       for key in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id"))
                or ("original_url" in payload and payload["original_url"] != intake["original_url"])
                or (pending and (intake["version"] != expected_version
                                  or intake["processing_state"] != "SUBMITTED"))):
            raise NativeIntakeStartConflict("NATIVE_START_INTAKE_BINDING_CONFLICT")

    def admit_event(self, tenant: str, event_id: str) -> dict:
        policy = native_intake_start_policy()
        self._partition(policy, tenant)
        with self.graph.transaction(tenant):
            _, event, event_hash = self._event(tenant, event_id)
            intake_id, version = event["aggregate_id"], event["aggregate_version"]
            job_id = str(uuid5(UUID(intake_id), NATIVE_INTAKE_START_JOB_TYPE))
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
                current = self.queue.get(job_id)
                if (prior["job_type"] != NATIVE_INTAKE_START_JOB_TYPE or prior["aggregate_type"] != "intake"
                        or prior["aggregate_id"] != intake_id or prior["idempotency_key"] != intake_id
                        or payload.get("event_id") != event_id or payload.get("event_sha256") != event_hash
                        or payload.get("intake_version") != version or payload.get("policy") != policy
                        or current is None or current.payload != {"job_id": job_id, "tenant_id": tenant,
                                                                 "attempt_token": token}
                        or current.job_type != NATIVE_INTAKE_START_JOB_TYPE):
                    raise NativeIntakeStartConflict("NATIVE_START_RESERVATION_CONFLICT")
                return self._admission_receipt(prior, payload)
            at, audit_id = self._clock(), str(uuid4())
            deadline = at + timedelta(seconds=policy["deadline_seconds"])
            receipt = {"job_id": job_id, "intake_id": intake_id, "admitted_intake_version": version,
                       "job_status_at_admission": "QUEUED", "timeout_at": deadline.isoformat(),
                       "submitted_event_id": event_id, "audit_event_id": audit_id}
            payload = {"event_id": event_id, "event_sha256": event_hash, "intake_version": version,
                       "policy": policy, "delivery_generation": 1,
                       "attempt_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                       "submitter_subject_id": intake["submitter_subject_id"],
                       "target_state": "CHECKING_IDENTITY", "admission_receipt": receipt}
            self.engine.execute("INSERT INTO workflow.jobs (job_id,tenant_id,job_type,aggregate_type,aggregate_id,"
                "status,checkpoint,attempt,max_attempts,fence_token,timeout_at,payload,correlation_id,idempotency_key) "
                "VALUES (?,?,?,'intake',?,'QUEUED','SUBMITTED',0,?,1,?,?::jsonb,?,?)",
                (job_id, tenant, NATIVE_INTAKE_START_JOB_TYPE, intake_id, policy["max_attempts"],
                 deadline.isoformat(), _json(payload), intake["correlation_id"], intake_id))
            delivery, created = self.queue.enqueue(JobRequest(NATIVE_INTAKE_START_JOB_TYPE,
                {"job_id": job_id, "tenant_id": tenant, "attempt_token": token},
                idempotency_key=NATIVE_INTAKE_START_JOB_TYPE + ":" + tenant + ":" + intake_id,
                job_id=job_id), correlation_id=intake["correlation_id"])
            if not created or delivery.job_id != job_id:
                raise NativeIntakeStartConflict("NATIVE_START_DELIVERY_RESERVATION_CONFLICT")
            self.engine.execute("UPDATE durable_jobs SET max_retries=? WHERE job_id=?",
                                (policy["max_attempts"], job_id))
            self._audit(intake, "intake.start_requested.v1", None,
                        {**receipt, "native_start_provenance": self._provenance(payload)}, at,
                        audit_id=audit_id, causation=event_id)
            return receipt

    def _admission_receipt(self, job: dict, payload: dict) -> dict:
        receipt = _object(payload.get("admission_receipt"))
        if (set(receipt) != {"job_id", "intake_id", "admitted_intake_version", "job_status_at_admission",
                             "timeout_at", "submitted_event_id", "audit_event_id"}
                or receipt.get("job_id") != job["job_id"] or receipt.get("intake_id") != job["aggregate_id"]
                or type(receipt.get("admitted_intake_version")) is not int
                or receipt["admitted_intake_version"] != payload["intake_version"]
                or receipt.get("job_status_at_admission") != "QUEUED"
                or receipt.get("submitted_event_id") != payload["event_id"]
                or _time(receipt["timeout_at"]) != _time(job["timeout_at"])):
            raise NativeIntakeStartConflict("NATIVE_START_ADMISSION_RECEIPT_CONFLICT")
        audit = self.engine.query_one("SELECT after_value FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=? "
            "AND resource_type='intake' AND resource_id=? AND actor_subject_id IS NULL AND service_principal=? "
            "AND actor_role='SVC_INTAKE' AND action='intake.start_requested.v1' AND result='SUCCEEDED' "
            "AND causation_id=? AND correlation_id=?", (job["tenant_id"], receipt["audit_event_id"], job["aggregate_id"],
                NATIVE_INTAKE_START_JOB_TYPE, payload["event_id"], job["correlation_id"]))
        if audit is None:
            raise NativeIntakeStartConflict("NATIVE_START_ADMISSION_AUDIT_MISSING")
        snapshot = _object(audit["after_value"])
        if (any(snapshot.get(k) != v for k, v in receipt.items())
                or snapshot.get("native_start_provenance") != self._provenance(payload)):
            raise NativeIntakeStartConflict("NATIVE_START_ADMISSION_AUDIT_CONFLICT")
        return receipt

    def admit_partition(self, tenant: str, *, limit: int):
        """Yield one committed receipt at a time; a batch is not one transaction."""
        self._partition(native_intake_start_policy(), tenant)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("A bounded partition size from 1 to 100 is required")
        with self.graph.transaction(tenant):
            rows = self.engine.query("SELECT e.event_id FROM workflow.outbox_events e "
                "JOIN intake.intakes i ON i.tenant_id=e.tenant_id AND i.intake_id=e.aggregate_id "
                "WHERE e.tenant_id=? AND e.event_type='intake.submitted' AND e.aggregate_type='intake' "
                "AND i.processing_state='SUBMITTED' AND i.deleted_at IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM workflow.jobs j WHERE j.tenant_id=e.tenant_id "
                "AND j.job_type=? AND j.idempotency_key=i.intake_id::text) "
                "ORDER BY e.occurred_at,e.event_id LIMIT ?", (tenant, NATIVE_INTAKE_START_JOB_TYPE, limit))
        for row in rows:
            yield self.admit_event(tenant, row["event_id"])

    def _budget(self, timeout: Any, lease: Any) -> None:
        # Recompute at each lock/write boundary. The final conditional writes
        # below independently reject a deadline/lease that elapsed meanwhile.
        set_native_intake_sql_budget(self.engine, _time(timeout), _time(lease), self._clock(),
                                     error_code="NATIVE_START_DEADLINE_OR_LEASE_EXPIRED")

    def _locked(self, delivery, tenant: str, generation: int, *, settlement: bool = False) -> tuple[Any, dict, dict]:
        # Deliberately retain the existing identity coordinator's small
        # transport predicate; its decision-specific business fence is not
        # applicable to this Intake command. Both paths get regression coverage.
        preliminary = self.graph._one("SELECT timeout_at FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                                      (tenant, delivery.job_id))
        peek = self.queue.get(delivery.job_id)
        if peek is None or peek.lease_expires_at is None:
            raise JobFenceRejectedError("NATIVE_START_DELIVERY_NOT_OWNED")
        # Expired business work may still be protectively settled by its live
        # delivery owner; it cannot receive another Intake mutation grant.
        budget = peek.lease_expires_at if settlement else preliminary["timeout_at"]
        self._budget(budget, peek.lease_expires_at)
        self.graph._one("SELECT job_id FROM durable_jobs WHERE job_id=? FOR UPDATE", (delivery.job_id,))
        current = self.queue.get(delivery.job_id)
        now = self._clock()
        if (current is None or current.job_type != NATIVE_INTAKE_START_JOB_TYPE
                or current.status != JobStatus.RUNNING or not current.locked_by
                or current.locked_by != delivery.locked_by or current.fence_token != delivery.fence_token
                or current.payload != delivery.payload or current.correlation_id != delivery.correlation_id
                or current.lease_expires_at is None or current.lease_expires_at <= now):
            raise JobFenceRejectedError("NATIVE_START_DELIVERY_NOT_OWNED")
        job = self.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=? FOR UPDATE",
                              (tenant, delivery.job_id))
        payload = _object(job["payload"])
        if (job["job_type"] != NATIVE_INTAKE_START_JOB_TYPE or job["aggregate_type"] != "intake"
                or job["idempotency_key"] != job["aggregate_id"]
                or job["correlation_id"] != current.correlation_id
                or type(payload.get("delivery_generation")) is not int
                or payload.get("delivery_generation") != generation
                or payload.get("target_state") != "CHECKING_IDENTITY"
                or payload.get("attempt_token_sha256") != hashlib.sha256(delivery.payload["attempt_token"].encode()).hexdigest()):
            raise JobFenceRejectedError("NATIVE_START_ADMISSION_NOT_OWNED")
        self._admission_receipt(job, payload)
        return current, job, payload

    def _committed_result(self, delivery, tenant: str, generation: int) -> dict | None:
        """A terminal receipt is evidence of the old command, never a new grant."""
        terminal_rows = native_terminal_evidence(self.graph, tenant, delivery.job_id)
        pair = (terminal_rows.pair() if terminal_rows is not None
                else native_terminal_pair(self.graph, tenant, delivery.job_id))
        if pair is None:
            current = self.queue.get(delivery.job_id)
            job = self.engine.query_one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                                        (tenant, delivery.job_id))
        else:
            current, job = pair
        if job is None or job["status"] != "SUCCEEDED":
            return None
        payload, result = _object(job["payload"]), _object(job["result"])
        try:
            verify_native_intake_terminal_ticket(delivery, tenant=tenant, generation=generation,
                job_type=NATIVE_INTAKE_START_JOB_TYPE,
                attempt_token_sha256=payload.get("attempt_token_sha256"))
        except NativeIntakeTerminalTicketConflict:
            raise JobFenceRejectedError("NATIVE_START_TERMINAL_RECEIPT_CONFLICT") from None
        if (current is None or current.status != JobStatus.SUCCEEDED
                or current.job_type != NATIVE_INTAKE_START_JOB_TYPE or current.payload != delivery.payload
                or job["job_type"] != NATIVE_INTAKE_START_JOB_TYPE or job["aggregate_type"] != "intake"
                or job["idempotency_key"] != job["aggregate_id"]
                or current.correlation_id != job["correlation_id"]
                or type(payload.get("delivery_generation")) is not int
                or payload.get("delivery_generation") != generation
                or payload.get("attempt_token_sha256") != hashlib.sha256(delivery.payload["attempt_token"].encode()).hexdigest()
                or result.get("job_id") != delivery.job_id or result.get("intake_id") != job["aggregate_id"]
                or set(result) != {"job_id", "intake_id", "processing_state", "version", "transition_id",
                                   "audit_event_id", "outbox_event_id", "correlation_id"}
                or result.get("correlation_id") != job["correlation_id"]
                or result.get("processing_state") != "CHECKING_IDENTITY"
                or type(payload.get("intake_version")) is not int
                or result.get("version") != payload["intake_version"] + 1
                or type(result.get("version")) is not int
                or not (terminal_rows.transition({
                           'tenant_id': tenant,
                           'transition_id': result.get("transition_id"),
                           'intake_id': job["aggregate_id"],
                           'job_id': delivery.job_id,
                           'from_state': 'SUBMITTED',
                           'to_state': 'CHECKING_IDENTITY',
                           'resulting_version': result.get("version"),
                           'expected_version': payload["intake_version"],
                           'actor_subject_id': None,
                           'service_principal': NATIVE_INTAKE_START_JOB_TYPE,
                           'permission': 'intake.process',
                           'causation_id': payload.get("event_id"),
                           'correlation_id': job["correlation_id"],
                       }) if terminal_rows is not None else self.engine.query_one("SELECT transition_id FROM intake.intake_stage_transitions "
                    "WHERE tenant_id=? AND transition_id=? AND intake_id=? AND job_id=? "
                    "AND from_state='SUBMITTED' AND to_state='CHECKING_IDENTITY' AND resulting_version=? "
                    "AND expected_version=? AND actor_subject_id IS NULL AND service_principal=? "
                    "AND permission='intake.process' AND causation_id=? AND correlation_id=?",
                    (tenant, result.get("transition_id"), job["aggregate_id"], delivery.job_id, result.get("version"),
                     payload["intake_version"], NATIVE_INTAKE_START_JOB_TYPE, payload.get("event_id"), job["correlation_id"])))):
            raise JobFenceRejectedError("NATIVE_START_TERMINAL_RECEIPT_CONFLICT")
        audit = (terminal_rows.result_audit({
                    'tenant_id': tenant,
                    'audit_event_id': result["audit_event_id"],
                    'resource_type': 'intake',
                    'resource_id': job["aggregate_id"],
                    'actor_subject_id': None,
                    'service_principal': NATIVE_INTAKE_START_JOB_TYPE,
                    'actor_role': 'SVC_INTAKE',
                    'action': 'intake.identity_check_started.v1',
                    'result': 'SUCCEEDED',
                    'causation_id': payload["event_id"],
                    'correlation_id': job["correlation_id"],
                }) if terminal_rows is not None else self.engine.query_one("SELECT after_value FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=? "
            "AND resource_type='intake' AND resource_id=? AND actor_subject_id IS NULL AND service_principal=? "
            "AND actor_role='SVC_INTAKE' AND action='intake.identity_check_started.v1' AND result='SUCCEEDED' "
            "AND causation_id=? AND correlation_id=?", (tenant, result["audit_event_id"], job["aggregate_id"],
                NATIVE_INTAKE_START_JOB_TYPE, payload["event_id"], job["correlation_id"])))
        outbox = (terminal_rows.outbox({
                     'tenant_id': tenant,
                     'event_id': result["outbox_event_id"],
                     'event_type': 'intake.state_changed',
                     'aggregate_type': 'intake',
                     'aggregate_id': job["aggregate_id"],
                     'aggregate_version': result["version"],
                     'correlation_id': job["correlation_id"],
                 }) if terminal_rows is not None else self.engine.query_one("SELECT payload FROM workflow.outbox_events WHERE tenant_id=? AND event_id=? "
            "AND event_type='intake.state_changed' AND aggregate_type='intake' AND aggregate_id=? "
            "AND aggregate_version=? AND correlation_id=?", (tenant, result["outbox_event_id"],
                job["aggregate_id"], result["version"], job["correlation_id"])))
        if audit is None or outbox is None:
            raise JobFenceRejectedError("NATIVE_START_TERMINAL_EVIDENCE_MISSING")
        snapshot, event = _object(audit["after_value"]), _object(outbox["payload"])
        if (any(snapshot.get(k) != v for k, v in result.items())
                or snapshot.get("native_start_provenance") != self._provenance(payload)
                or validate_event(event) or event.get("event_id") != result["outbox_event_id"]
                or event.get("producer") != "listing_intake_service"
                or event.get("schema_ref") != "#/payloads/IntakeStateChangedV1"
                or event.get("tenant_id") != tenant or event.get("aggregate_id") != job["aggregate_id"]
                or event.get("aggregate_version") != result["version"]
                or event.get("actor_ref") != NATIVE_INTAKE_START_JOB_TYPE
                or event.get("causation_id") != payload["event_id"]
                or event.get("correlation_id") != job["correlation_id"]
                or event["payload"].get("transition_id") != result["transition_id"]
                or event["payload"].get("intake_id") != job["aggregate_id"]
                or event["payload"].get("from_state") != "SUBMITTED"
                or event["payload"].get("to_state") != "CHECKING_IDENTITY"
                or event["payload"].get("version") != result["version"]):
            raise JobFenceRejectedError("NATIVE_START_TERMINAL_EVIDENCE_CONFLICT")
        if terminal_rows is not None:
            terminal_rows.finish()
        return result

    @staticmethod
    def _provenance(payload: dict) -> dict:
        return {**{key: payload[key] for key in ("event_id", "event_sha256", "intake_version", "delivery_generation")},
                "policy_sha256": hashlib.sha256(_json(payload["policy"]).encode()).hexdigest()}

    def execute(self, delivery) -> dict:
        tenant, generation = self.auth.decode(delivery)
        policy = native_intake_start_policy()
        self._partition(policy, tenant)
        with self.graph.transaction(tenant):
            if result := self._committed_result(delivery, tenant, generation):
                return result
            current, job, payload = self._locked(delivery, tenant, generation)
            now = self._clock()
            if (job["status"] not in {"QUEUED", "RETRYING"} or job["result"] is not None
                    or payload.get("policy") != policy or job["max_attempts"] != policy["max_attempts"]
                    or job["attempt"] >= job["max_attempts"] or job["cancellation_requested_at"] is not None
                    or _time(job["timeout_at"]) <= now
                    or (job["next_attempt_at"] is not None and _time(job["next_attempt_at"]) > now)):
                raise NativeIntakeStartConflict("NATIVE_START_ATTEMPT_NOT_ADMITTED")
            self._budget(job["timeout_at"], current.lease_expires_at)
            intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE",
                                      (tenant, job["aggregate_id"]))
            _, event, digest = self._event(tenant, payload["event_id"])
            if digest != payload["event_sha256"] or payload["submitter_subject_id"] != intake["submitter_subject_id"]:
                raise NativeIntakeStartConflict("NATIVE_START_EVENT_CHANGED")
            version = payload["intake_version"]
            self._input_binding(intake, event, version, pending=True)
            _uuid(intake["submitter_subject_id"])
            self._budget(job["timeout_at"], current.lease_expires_at)
            if self.engine.execute("UPDATE workflow.jobs SET status='RUNNING',attempt=attempt+1,"
                "fence_token=fence_token+1,version=version+1,heartbeat_at=clock_timestamp(),lease_expires_at=? "
                "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? "
                "AND status IN ('QUEUED','RETRYING') AND attempt<max_attempts "
                "AND cancellation_requested_at IS NULL AND timeout_at>clock_timestamp()",
                (current.lease_expires_at.isoformat(), tenant, delivery.job_id,
                 job["version"], job["fence_token"])).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_START_ACCEPTANCE_FENCE_REJECTED")
            job = {**job, "version": job["version"] + 1, "fence_token": job["fence_token"] + 1}
            at, transition_id, audit_id = self._clock(), str(uuid4()), str(uuid4())
            aggregate = IntakeAggregate(id=intake["intake_id"], tenant_id=tenant,
                stage=IntakeStage(intake["processing_state"]), version=intake["version"],
                created_by=intake["submitter_subject_id"], source_id=intake["source_id"],
                canonical_url=intake["canonical_url"], owner_id=intake["submitter_subject_id"])
            IntakeStateMachine.transition(aggregate, IntakeStage.CHECKING_IDENTITY, TransitionContext(
                actor=Actor(NATIVE_INTAKE_START_JOB_TYPE, PrincipalRole.SVC_INTAKE, tenant),
                version_before=version, idempotency_key=payload["event_id"],
                correlation_id=intake["correlation_id"], causation_id=payload["event_id"], current_time=at))
            if self.engine.execute("UPDATE intake.intakes SET processing_state=?,version=?,last_transition_at=? "
                "WHERE tenant_id=? AND intake_id=? AND processing_state='SUBMITTED' AND version=? AND deleted_at IS NULL",
                (aggregate.stage.value, aggregate.version, at.isoformat(), tenant, intake["intake_id"], version)).rowcount != 1:
                raise NativeIntakeStartConflict("NATIVE_START_VERSION_CONFLICT")
            sequence = self.engine.query_one("SELECT COALESCE(MAX(sequence_no),0)+1 AS next "
                "FROM intake.intake_stage_transitions WHERE tenant_id=? AND intake_id=?",
                (tenant, intake["intake_id"]))["next"]
            self.engine.execute("INSERT INTO intake.intake_stage_transitions (transition_id,tenant_id,intake_id,sequence_no,"
                "from_state,to_state,actor_subject_id,service_principal,permission,reason_text,idempotency_key,"
                "expected_version,resulting_version,job_id,correlation_id,causation_id,occurred_at) "
                "VALUES (?,?,?,?,'SUBMITTED','CHECKING_IDENTITY',NULL,?,'intake.process',?,?,?,?,?,?,?,?)",
                (transition_id, tenant, intake["intake_id"], sequence, NATIVE_INTAKE_START_JOB_TYPE,
                 "Start identity checking for a committed native submission.", payload["event_id"], version,
                 aggregate.version, delivery.job_id, intake["correlation_id"], payload["event_id"], at.isoformat()))
            state_event = DomainEvent(event_type="intake.state_changed", producer="listing_intake_service",
                tenant_id=tenant, aggregate_type="intake", aggregate_id=intake["intake_id"],
                aggregate_version=aggregate.version, partition_key=tenant + ":" + intake["intake_id"],
                correlation_id=intake["correlation_id"], causation_id=payload["event_id"],
                actor_ref=NATIVE_INTAKE_START_JOB_TYPE, occurred_at=at,
                schema_ref="#/payloads/IntakeStateChangedV1", payload={"intake_id": intake["intake_id"],
                    "from_state": "SUBMITTED", "to_state": "CHECKING_IDENTITY", "transition_id": transition_id,
                    "version": aggregate.version, "occurred_at": at.isoformat()})
            result = {"job_id": delivery.job_id, "intake_id": intake["intake_id"],
                      "processing_state": "CHECKING_IDENTITY", "version": aggregate.version,
                      "transition_id": transition_id, "audit_event_id": audit_id,
                      "outbox_event_id": state_event.event_id, "correlation_id": intake["correlation_id"]}
            self._budget(job["timeout_at"], current.lease_expires_at)
            # The documented .v1 audit label is intentional. Existing intake.create
            # audit records use another label and are not normalized here.
            self._audit(intake, "intake.identity_check_started.v1",
                        {"processing_state": "SUBMITTED", "version": version},
                        {**result, "native_start_provenance": self._provenance(payload)}, at,
                        audit_id=audit_id, causation=payload["event_id"])
            self._budget(job["timeout_at"], current.lease_expires_at)
            self.graph._outbox(state_event)
            self._budget(job["timeout_at"], current.lease_expires_at)
            if not self.engine.query_one("SELECT job_id FROM durable_jobs WHERE job_id=? AND status=? "
                "AND fence_token=? AND locked_by=? AND lease_expires_at>clock_timestamp()",
                (delivery.job_id, JobStatus.RUNNING.value, current.fence_token, current.locked_by)):
                raise JobFenceRejectedError("NATIVE_START_FINAL_DELIVERY_FENCE_REJECTED")
            accepted = {**payload, "accepted_delivery_fence": current.fence_token,
                        "accepted_worker": current.locked_by}
            if self.engine.execute("UPDATE workflow.jobs SET status='SUCCEEDED',checkpoint='CHECKING_IDENTITY',"
                "result=?::jsonb,payload=?::jsonb,version=version+1,"
                "heartbeat_at=?,lease_expires_at=NULL,updated_at=? WHERE tenant_id=? AND job_id=? AND version=? "
                "AND fence_token=? AND status='RUNNING' AND cancellation_requested_at IS NULL "
                "AND timeout_at>clock_timestamp()", (_json(result), _json(accepted), at.isoformat(), at.isoformat(),
                    tenant, delivery.job_id, job["version"], job["fence_token"])).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_START_FINAL_BUSINESS_FENCE_REJECTED")
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
                raise JobFenceRejectedError("NATIVE_START_UNKNOWN_ATTEMPT_OUTCOME")
            policy, now = payload["policy"], self._clock()
            try:
                current_policy = native_intake_start_policy()
            except NativeIntakeStartUnavailable:
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
            code = "NATIVE_START_CANCELLED" if cancelled else "NATIVE_START_RETRY_SCHEDULED" if due else "NATIVE_START_FAILED"
            intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE",
                                      (tenant, job["aggregate_id"]))
            if self.engine.execute("UPDATE workflow.jobs SET status=?,attempt=?,last_error=?::jsonb,next_attempt_at=?,"
                "version=version+1,updated_at=? WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=?",
                (status, attempt, _json({"code": code, "error_class": type(error).__name__}),
                 due.isoformat() if due else None, now.isoformat(), tenant, delivery.job_id,
                 job["version"], job["fence_token"])).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_START_SETTLEMENT_FENCE_REJECTED")
            self._audit(intake, "intake.start_delivery_settled.v1",
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
                    actor_ref=NATIVE_INTAKE_START_JOB_TYPE, occurred_at=now,
                    schema_ref="#/payloads/JobDeadLetteredV1", sensitive_fields=["payload.last_error_details"],
                    payload={"job_id": delivery.job_id, "job_type": NATIVE_INTAKE_START_JOB_TYPE,
                        "checkpoint": job["checkpoint"], "attempt": attempt, "error_code": code,
                        "dead_lettered_at": now.isoformat(), "last_error_details": {"error_class": type(error).__name__}}))
            return None


def run_native_intake_start_delivery(delivery, owner: NativeIntakeStartOwner) -> dict | None:
    try:
        return owner.execute(delivery)
    except Exception as error:
        return owner.settle_failure(delivery, error)
