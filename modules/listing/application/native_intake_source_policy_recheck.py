"""Explicit requests and a separately fenced worker for AUTH_REQUIRED waits.

A request leaves the Intake waiting. The real recheck worker must establish
current requester authority before emitting a new policy-start state event.
Historical proofs root directly at identity and never walk prior rechecks.
"""

import hashlib
from datetime import timedelta
from uuid import UUID, uuid4, uuid5

from modules.external_data.security.assisted_listing_retrieval import (
    contains_sensitive_submission_material,
)
from modules.listing.application.intake_authorization import authorize_intake_action
from modules.listing.application.native_intake_job_runtime import (
    decode_native_intake_attempt,
    load_native_intake_policy,
    sign_native_intake_attempt,
)
from modules.listing.application.native_intake_requester_authority import (
    current_native_recheck_principal,
)
from modules.listing.application.native_intake_source_policy import (
    NativeIntakeSourcePolicyOwner,
    _json,
    _object,
    _time,
    _uuid,
)
from modules.listing.domain.identity_graph import IdentityConflictError
from modules.listing.domain.intake_states import (
    Actor,
    IntakeAggregate,
    IntakeStage,
    IntakeStateMachine,
    PrincipalRole,
    TransitionContext,
)
from shared.audit.backlog import CanonicalAuditBacklog
from shared.domain.events import DomainEvent, validate_event
from shared.jobs.queue import JobFenceRejectedError, JobRequest, JobStatus

NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE = "native-intake-source-policy-recheck"
POLICY_ENV = "ODP_NATIVE_INTAKE_SOURCE_POLICY_RECHECK_POLICY_JSON"
SIGNING_KEY_ENV = "ODP_NATIVE_INTAKE_SOURCE_POLICY_RECHECK_SIGNING_KEY"
REQUEST_EVENT = "intake.source_policy_recheck_requested"
REQUEST_SCHEMA = "#/payloads/IntakeSourcePolicyRecheckRequestedV1"


class NativeIntakeSourcePolicyRecheckUnavailable(RuntimeError):
    pass


class NativeIntakeSourcePolicyRecheckConflict(IdentityConflictError):
    pass


def native_intake_source_policy_recheck_policy() -> dict:
    return load_native_intake_policy(env_name=POLICY_ENV, max_seconds=15, max_attempts=3,
        unavailable=NativeIntakeSourcePolicyRecheckUnavailable, error_code="NATIVE_RECHECK_POLICY_UNAVAILABLE")


class NativeIntakeSourcePolicyRecheckAuthenticator:
    @staticmethod
    def sign(job_id: str, tenant: str, generation: int) -> str:
        return sign_native_intake_attempt(NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE, SIGNING_KEY_ENV,
            job_id, tenant, generation, unavailable=NativeIntakeSourcePolicyRecheckUnavailable,
            error_code="NATIVE_RECHECK_SIGNING_KEY_UNAVAILABLE")

    def decode(self, delivery) -> tuple[str, int]:
        return decode_native_intake_attempt(delivery, job_type=NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE,
            sign=self.sign, error_code="NATIVE_RECHECK_TOKEN_REJECTED")


class NativeIntakeSourcePolicyRecheckOwner:
    def __init__(self, graph):
        self.graph, self.engine, self.queue = graph, graph.engine, graph.queue
        self.source = NativeIntakeSourcePolicyOwner(graph)
        self.auth = NativeIntakeSourcePolicyRecheckAuthenticator()

    @staticmethod
    def _request_audit_id(request_id):
        return str(uuid5(UUID(request_id), "native-source-policy-recheck-request-audit-v1"))

    @staticmethod
    def _waiting_context(payload):
        keys = {"waiting_state", "waiting_source_policy"}
        present = keys & set(payload)
        if not present:
            # Existing v1 requests/admissions predate the assisted recheck.
            return "AWAITING_SOURCE_AUTHORIZATION", "AUTH_REQUIRED"
        if present != keys:
            raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_WAITING_CONTEXT_INCOMPLETE")
        state, policy = payload["waiting_state"], payload["waiting_source_policy"]
        if not (isinstance(state, str) and isinstance(policy, str)) or not (
                (state == "AWAITING_SOURCE_AUTHORIZATION" and policy == "AUTH_REQUIRED")
                or (state == "AWAITING_ASSISTED_ENTRY" and policy in {"AUTH_REQUIRED", "ASSISTED_ENTRY_ONLY"})):
            raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_WAITING_CONTEXT_REJECTED")
        return state, policy

    def _authority(self, subject, intake, before):
        return current_native_recheck_principal(self.engine, subject, intake, before=before)

    def command_context(self, principal, intake_id: str, *, before) -> dict:
        before()
        intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? "
            "AND deleted_at IS NULL FOR UPDATE", (principal.tenant_id, _uuid(intake_id)))
        authorize_intake_action(principal, "recheck_source_policy",
            resource={**intake, "submitter": intake["submitter_subject_id"]})
        self._authority(principal.subject_id, intake, before)
        return intake

    def _root_identity(self, tenant, event_id, before):
        view = self.source._producer_reads(before)
        row = view._one("SELECT payload FROM workflow.outbox_events WHERE tenant_id=? AND event_id=?",
                        (tenant, _uuid(event_id)))
        # Check the direct identity discriminator BEFORE invoking the policy
        # producer verifier. A recheck cannot smuggle another recheck as root.
        event = _object(row["payload"])
        if event.get("actor_ref") != "native-intake-identity":
            raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_ROOT_NOT_IDENTITY")
        _, event, _, producer = NativeIntakeSourcePolicyOwner(view)._event(tenant, event_id, before=before)
        return event, producer

    def _waiting(self, intake, version, before):
        state, policy = intake["processing_state"], intake["source_policy_state"]
        self._waiting_context({"waiting_state": state, "waiting_source_policy": policy})
        if (intake["version"] != version
                or any(intake[k] is not None for k in ("deleted_at", "resolved_listing_id", "completed_at"))):
            raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_NOT_CURRENT_WAIT")
        before()
        if (self.engine.query_one("SELECT 1 FROM identity.match_cases WHERE tenant_id=? AND intake_id=? LIMIT 1",
                                 (intake["tenant_id"], intake["intake_id"])) is not None
                or self.engine.query_one("SELECT 1 FROM expansion.promotion_decisions WHERE tenant_id=? AND intake_id=? LIMIT 1",
                                         (intake["tenant_id"], intake["intake_id"])) is not None):
            raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_DOWNSTREAM_WORK_EXISTS")
        before()
        jobs = self.engine.query("SELECT j.job_type,j.status,d.job_id AS delivery_id,d.status AS delivery_status,d.delivery_state "
            "FROM workflow.jobs j LEFT JOIN durable_jobs d ON d.job_id::text=j.job_id::text "
            "WHERE j.tenant_id=? AND j.aggregate_type='intake' AND j.aggregate_id=?",
            (intake["tenant_id"], intake["intake_id"]))
        if (not any(j["job_type"] == "native-intake-source-policy" for j in jobs)
                or any(j["status"] != "SUCCEEDED" or j["delivery_id"] is None
                       or j["delivery_status"] != JobStatus.SUCCEEDED.value or j["delivery_state"] is not None for j in jobs)):
            raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_NATIVE_JOBS_NOT_SETTLED")
        view = self.source._producer_reads(before)
        job = view._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND aggregate_type='intake' "
            "AND aggregate_id=? AND job_type='native-intake-source-policy' AND status='SUCCEEDED' "
            "AND result->>'version'=?::text", (intake["tenant_id"], intake["intake_id"], str(version)))
        delivery = view.queue.get(job["job_id"])
        if delivery is None:
            raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_WAIT_PROOF_MISSING")
        from modules.listing.application.native_intake_source_policy_recheck_history import (
            HistoricalSourcePolicyProof,
        )

        payload = _object(job["payload"])
        tenant = intake["tenant_id"]
        result, produced = HistoricalSourcePolicyProof(view).committed_with_producer(delivery, tenant,
            payload.get("delivery_generation"), producer_event_id=payload["event_id"])
        if (tenant != intake["tenant_id"] or result is None or result["version"] != version
                or result["intake_id"] != intake["intake_id"]
                or result["processing_state"] != state or result["policy_decision"]["effective_policy"] != policy
                or payload["input_sha256"] != self.source._input_digest(intake)):
            raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_WAIT_PROOF_CONFLICT")
        _, event, _, producer = produced
        if (("recheck_provenance" in payload) != (producer.get("recheck") is True)
                or (producer.get("recheck") is True and payload["recheck_provenance"] != producer["recheck_provenance"])):
            raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_WAIT_PRODUCER_BINDING_CONFLICT")
        self.source._input_binding(intake, event, payload["intake_version"], producer, pending=False, before=before)
        root = producer.get("root_identity_event_id", payload["event_id"])
        original, _ = self._root_identity(tenant, root, before)
        if original["aggregate_id"] != intake["intake_id"]:
            raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_ROOT_INTAKE_CONFLICT")
        return root

    def request(self, principal, intake_id, *, expected_version, reason, idempotency_key):
        if (type(expected_version) is not int or expected_version < 1 or not idempotency_key
                or not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 4000
                or contains_sensitive_submission_material({"reason": reason})):
            raise ValueError("Explicit safe source-policy recheck intent is required")
        with self.graph.transaction(principal.tenant_id):
            from modules.listing.application.native_intake_pending_commands import (
                NativeIntakeCommandBudget,
                NativeIntakePendingCommands,
            )

            before = NativeIntakeCommandBudget(self.source)
            intake = self.command_context(principal, intake_id, before=before)
            root = self._waiting(intake, expected_version, before)
            before()
            NativeIntakePendingCommands(self.graph).require_clear(intake, before=before)
            before()
            CanonicalAuditBacklog(self.engine).require_available(intake["tenant_id"])
            request_id, at = str(uuid4()), self.source._clock()
            audit_id = self._request_audit_id(request_id)
            event = DomainEvent(event_id=request_id, event_type=REQUEST_EVENT, producer="listing_intake_service",
                tenant_id=intake["tenant_id"], aggregate_type="intake_policy_recheck", aggregate_id=request_id,
                aggregate_version=1, partition_key=intake["tenant_id"] + ":" + intake_id,
                correlation_id=intake["correlation_id"], actor_ref=principal.subject_id, occurred_at=at,
                schema_ref=REQUEST_SCHEMA, payload={"request_id": request_id, "intake_id": intake_id,
                    "expected_intake_version": expected_version, "requested_by": principal.subject_id,
                    "requested_at": at.isoformat(), "input_sha256": self.source._input_digest(intake),
                    "root_identity_event_id": root, "waiting_state": intake["processing_state"],
                    "waiting_source_policy": intake["source_policy_state"]})
            receipt = {"request_id": request_id, "intake_id": intake_id, "status": "REQUESTED",
                "version": expected_version, "requested_at": at.isoformat(), "correlation_id": intake["correlation_id"],
                "audit_event_id": audit_id, "outbox_event_id": event.event_id}
            self.graph.audit_sink.persist({"audit_event_id": audit_id, "tenant_id": intake["tenant_id"],
                "resource_type": "intake", "resource_id": intake_id, "action": "intake.source_policy_recheck_requested.v1",
                "result": "SUCCEEDED", "actor_subject_id": principal.subject_id, "actor_role": "DATA_STEWARD",
                "correlation_id": intake["correlation_id"], "occurred_at": at.isoformat(),
                "before_value": {"processing_state": intake["processing_state"], "source_policy_state": intake["source_policy_state"],
                    "version": expected_version},
                "after_value": {**receipt, "native_recheck_request_provenance": event.payload}, "reason": reason},
                {k: intake[k] for k in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")})
            before()
            self.graph._outbox(event)
            before()
            return receipt

    def _request_event(self, tenant, event_id, before):
        view = self.source._producer_reads(before)
        row = view._one("SELECT * FROM workflow.outbox_events WHERE tenant_id=? AND event_id=?", (tenant, _uuid(event_id)))
        event = _object(row["payload"])
        data = _object(event.get("payload"))
        if (validate_event(event) or row["event_type"] != REQUEST_EVENT or event.get("event_type") != REQUEST_EVENT
                or row["aggregate_type"] != "intake_policy_recheck" or event.get("aggregate_type") != row["aggregate_type"]
                or row["event_id"] != event_id or event.get("event_id") != event_id
                or row["aggregate_id"] != event_id or event.get("aggregate_id") != event_id
                or type(row["aggregate_version"]) is not int or row["aggregate_version"] != 1
                or type(event.get("aggregate_version")) is not int or event["aggregate_version"] != 1
                or row["event_version"] != 1 or type(event.get("event_version")) is not int or event["event_version"] != 1
                or event.get("producer") != "listing_intake_service" or event.get("schema_ref") != REQUEST_SCHEMA
                or event.get("tenant_id") != tenant or event.get("correlation_id") != row["correlation_id"]
                or type(data.get("expected_intake_version")) is not int or data["expected_intake_version"] < 1
                or data.get("request_id") != event_id or data.get("requested_by") != event.get("actor_ref")
                or row["partition_key"] != tenant + ":" + data["intake_id"] or event.get("partition_key") != row["partition_key"]
                or _time(data["requested_at"]) != _time(event["occurred_at"])
                or _time(row["occurred_at"]) != _time(event["occurred_at"])):
            raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_REQUEST_EVENT_CONFLICT")
        _uuid(data["intake_id"])
        _uuid(data["requested_by"])
        waiting_state, waiting_policy = self._waiting_context(data)
        original, producer = self._root_identity(tenant, data["root_identity_event_id"], before)
        if original["aggregate_id"] != data["intake_id"] or original["correlation_id"] != row["correlation_id"]:
            raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_REQUEST_ROOT_CONFLICT")
        audit_id = self._request_audit_id(event_id)
        audit = view._one("SELECT after_value,before_value FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=? "
            "AND resource_type='intake' AND resource_id=? AND actor_subject_id=? AND actor_role='DATA_STEWARD' "
            "AND action='intake.source_policy_recheck_requested.v1' AND result='SUCCEEDED' AND correlation_id=?",
            (tenant, audit_id, data["intake_id"], data["requested_by"], row["correlation_id"]))
        receipt = {"request_id": event_id, "intake_id": data["intake_id"], "status": "REQUESTED",
            "version": data["expected_intake_version"], "requested_at": data["requested_at"],
            "correlation_id": row["correlation_id"], "audit_event_id": audit_id, "outbox_event_id": event_id}
        if any(_object(audit["after_value"]).get(k) != v for k, v in receipt.items()):
            raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_REQUEST_AUDIT_CONFLICT")
        if "waiting_state" in data or "native_recheck_request_provenance" in _object(audit["after_value"]):
            before_value = _object(audit["before_value"])
            if ("waiting_state" not in data
                    or _object(audit["after_value"]).get("native_recheck_request_provenance") != data
                    or before_value != {"processing_state": waiting_state, "source_policy_state": waiting_policy,
                        "version": data["expected_intake_version"]}):
                raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_REQUEST_WAITING_CONTEXT_NOT_AUDITED")
        return row, event, hashlib.sha256(_json(event).encode()).hexdigest(), producer

    def admit_event(self, tenant, event_id):
        policy = native_intake_source_policy_recheck_policy()
        self.source._partition(policy, tenant)
        with self.graph.transaction(tenant):
            from modules.listing.application.native_intake_pending_commands import (
                NativeIntakeCommandBudget,
                NativeIntakePendingCommands,
            )

            before = NativeIntakeCommandBudget(self.source)
            at = self.source._clock()
            row, event, digest, _ = self._request_event(tenant, event_id, before)
            data = event["payload"]
            job_id = str(uuid5(UUID(event_id), NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE))
            before()
            self.engine.query_one("SELECT job_id FROM durable_jobs WHERE job_id=? FOR UPDATE", (job_id,))
            before()
            prior = self.engine.query_one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=? FOR UPDATE", (tenant, job_id))
            before()
            intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE", (tenant, data["intake_id"]))
            pending = NativeIntakePendingCommands(self.graph)
            pending.require_not_withdrawn(event, "SOURCE_POLICY_RECHECK", before)
            pending.require_clear(intake, before=before, own_request_id=event_id)
            self._authority(data["requested_by"], intake, before)
            token = self.auth.sign(job_id, tenant, 1)
            if prior is not None:
                prior_payload = _object(prior["payload"])
                self._admitted_request(prior, prior_payload, before)
                return self._admission_receipt(prior, prior_payload, before)
            root = self._waiting(intake, data["expected_intake_version"], before)
            if (root != data["root_identity_event_id"] or self.source._input_digest(intake) != data["input_sha256"]
                    or self._waiting_context(data) != (intake["processing_state"], intake["source_policy_state"])):
                raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_REQUEST_INPUT_CHANGED")
            timeout = at + timedelta(seconds=policy["deadline_seconds"])
            audit_id = str(uuid4())
            receipt = {"job_id": job_id, "request_id": event_id, "intake_id": data["intake_id"],
                "admitted_intake_version": data["expected_intake_version"], "job_status_at_admission": "QUEUED",
                "timeout_at": timeout.isoformat(), "audit_event_id": audit_id}
            payload = {"event_id": event_id, "event_sha256": digest, **data, "policy": policy,
                "delivery_generation": 1, "attempt_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                "admission_receipt": receipt}
            before()
            self.engine.execute("INSERT INTO workflow.jobs (job_id,tenant_id,job_type,aggregate_type,aggregate_id,"
                "status,checkpoint,attempt,max_attempts,fence_token,timeout_at,payload,correlation_id,idempotency_key) "
                "VALUES (?,?,?,'intake_policy_recheck',?,'QUEUED',?,0,?,1,?,?::jsonb,?,?)",
                (job_id, tenant, NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE, event_id, intake["processing_state"], policy["max_attempts"],
                 timeout.isoformat(), _json(payload), row["correlation_id"], event_id))
            before()
            delivery, created = self.queue.enqueue(JobRequest(NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE,
                {"job_id": job_id, "tenant_id": tenant, "attempt_token": token}, job_id=job_id,
                idempotency_key=NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE + ":" + tenant + ":" + event_id),
                correlation_id=row["correlation_id"])
            if not created or delivery.job_id != job_id:
                raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_DELIVERY_RESERVATION_CONFLICT")
            before()
            self.engine.execute("UPDATE durable_jobs SET max_retries=? WHERE job_id=?", (policy["max_attempts"], job_id))
            self._audit(intake, "intake.source_policy_recheck_admitted.v1", None,
                {**receipt, "native_recheck_provenance": self._provenance(payload)}, at, audit_id, event_id)
            before()
            if native_intake_source_policy_recheck_policy() != policy:
                raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_ADMISSION_POLICY_CHANGED")
            return receipt

    @staticmethod
    def _provenance(payload):
        result = {**{k: payload[k] for k in ("event_id", "event_sha256", "request_id", "intake_id",
            "expected_intake_version", "requested_by", "requested_at", "input_sha256", "root_identity_event_id",
            "delivery_generation", "attempt_token_sha256")},
            "policy_sha256": hashlib.sha256(_json(payload["policy"]).encode()).hexdigest()}
        NativeIntakeSourcePolicyRecheckOwner._waiting_context(payload)
        if "waiting_state" in payload:
            result.update(waiting_state=payload["waiting_state"], waiting_source_policy=payload["waiting_source_policy"])
        return result

    def _audit(self, intake, action, before, after, at, audit_id, causation):
        self.graph.audit_sink.persist({"audit_event_id": audit_id, "tenant_id": intake["tenant_id"],
            "resource_type": "intake", "resource_id": intake["intake_id"], "action": action, "result": "SUCCEEDED",
            "actor_subject_id": None, "service_principal": NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE,
            "actor_role": "SVC_INTAKE", "correlation_id": intake["correlation_id"], "causation_id": causation,
            "occurred_at": at.isoformat(), "before_value": before, "after_value": after,
            "reason": "Recheck current authority before evaluating source policy."},
            {k: intake[k] for k in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")})

    def _admission_receipt(self, job, payload, before):
        receipt = _object(payload.get("admission_receipt"))
        before()
        delivery = self.queue.get(job["job_id"])
        if (job["job_type"] != NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE
                or job["aggregate_type"] != "intake_policy_recheck" or job["aggregate_id"] != payload["request_id"]
                or job["idempotency_key"] != payload["request_id"] or payload["event_id"] != payload["request_id"]
                or delivery is None or delivery.job_type != NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE
                or delivery.correlation_id != job["correlation_id"]
                or self.auth.decode(delivery) != (job["tenant_id"], payload["delivery_generation"])
                or delivery.payload != {"job_id": job["job_id"], "tenant_id": job["tenant_id"],
                    "attempt_token": self.auth.sign(job["job_id"], job["tenant_id"], payload["delivery_generation"])}
                or hashlib.sha256(delivery.payload["attempt_token"].encode()).hexdigest() != payload["attempt_token_sha256"]
                or set(receipt) != {"job_id", "request_id", "intake_id", "admitted_intake_version", "job_status_at_admission",
                                   "timeout_at", "audit_event_id"}
                or receipt["job_id"] != job["job_id"] or receipt["request_id"] != job["aggregate_id"]
                or receipt["intake_id"] != payload["intake_id"]
                or type(receipt["admitted_intake_version"]) is not int
                or receipt["admitted_intake_version"] != payload["expected_intake_version"]
                or receipt["job_status_at_admission"] != "QUEUED" or _time(receipt["timeout_at"]) != _time(job["timeout_at"])):
            raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_ADMISSION_RECEIPT_CONFLICT")
        before()
        audit = self.graph._one("SELECT after_value FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=? "
            "AND resource_type='intake' AND resource_id=? AND actor_subject_id IS NULL AND service_principal=? "
            "AND actor_role='SVC_INTAKE' AND action='intake.source_policy_recheck_admitted.v1' AND result='SUCCEEDED' "
            "AND causation_id=? AND correlation_id=?", (job["tenant_id"], receipt["audit_event_id"], payload["intake_id"],
                NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE, payload["event_id"], job["correlation_id"]))
        snapshot = _object(audit["after_value"])
        if (any(snapshot.get(k) != v for k, v in receipt.items())
                or snapshot.get("native_recheck_provenance") != self._provenance(payload)):
            raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_ADMISSION_AUDIT_CONFLICT")
        return receipt

    def admit_partition(self, tenant, *, limit, cursor=None):
        from modules.listing.application.native_intake_source_policy_recheck_partition import (
            NativeRecheckPartition,
        )

        self.source._partition(native_intake_source_policy_recheck_policy(), tenant)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("A bounded partition size from 1 to 100 is required")
        yield from NativeRecheckPartition(self).admit(tenant, limit=limit, cursor=cursor)

    def _admitted_request(self, job, payload, before):
        row, event, digest, producer = self._request_event(job["tenant_id"], payload["event_id"], before)
        if (digest != payload["event_sha256"] or row["correlation_id"] != job["correlation_id"]
                or any(payload.get(k) != v for k, v in event["payload"].items())):
            raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_ADMITTED_REQUEST_CHANGED")
        return event, producer

    def _locked(self, delivery, tenant, generation, *, settlement=False):
        preliminary = self.graph._one("SELECT timeout_at FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                                      (tenant, delivery.job_id))
        peek = self.queue.get(delivery.job_id)
        if peek is None or peek.lease_expires_at is None:
            raise JobFenceRejectedError("NATIVE_RECHECK_DELIVERY_NOT_OWNED")
        self.source._budget(peek.lease_expires_at if settlement else preliminary["timeout_at"], peek.lease_expires_at)
        self.graph._one("SELECT job_id FROM durable_jobs WHERE job_id=? FOR UPDATE", (delivery.job_id,))
        current = self.queue.get(delivery.job_id)
        now = self.source._clock()
        if (current is None or current.job_type != NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE
                or current.status != JobStatus.RUNNING or not current.locked_by
                or current.locked_by != delivery.locked_by or current.fence_token != delivery.fence_token
                or current.payload != delivery.payload or current.correlation_id != delivery.correlation_id
                or current.lease_expires_at is None or current.lease_expires_at <= now):
            raise JobFenceRejectedError("NATIVE_RECHECK_DELIVERY_NOT_OWNED")
        job = self.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=? FOR UPDATE", (tenant, delivery.job_id))
        payload = _object(job["payload"])
        if type(payload.get("delivery_generation")) is not int or payload["delivery_generation"] != generation:
            raise JobFenceRejectedError("NATIVE_RECHECK_GENERATION_REJECTED")
        self._admission_receipt(job, payload,
            lambda: self.source._budget(current.lease_expires_at if settlement else job["timeout_at"], current.lease_expires_at))
        return current, job, payload

    def _committed_result(self, delivery, tenant, generation, *, before=lambda: None):
        before()
        job = self.engine.query_one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?", (tenant, delivery.job_id))
        if job is None or job["status"] != "SUCCEEDED":
            return None
        payload, result = _object(job["payload"]), _object(job["result"])
        self._admission_receipt(job, payload, before)
        self._admitted_request(job, payload, before)
        before()
        current = self.queue.get(delivery.job_id)
        keys = {"job_id", "request_id", "intake_id", "processing_state", "version", "transition_id", "audit_event_id",
                "outbox_event_id", "correlation_id", "root_identity_event_id", "input_sha256", "requested_by",
                "accepted_delivery_fence", "accepted_business_fence", "accepted_attempt", "accepted_worker", "authority"}
        if (set(result) != keys or current.status != JobStatus.SUCCEEDED or current.delivery_state is not None
                or self.auth.decode(delivery) != (tenant, generation) or current.payload != delivery.payload
                or type(payload.get("delivery_generation")) is not int or payload["delivery_generation"] != generation
                or result["job_id"] != job["job_id"] or result["request_id"] != payload["request_id"]
                or result["intake_id"] != payload["intake_id"] or result["processing_state"] != "CHECKING_SOURCE_POLICY"
                or type(result["version"]) is not int or result["version"] != payload["expected_intake_version"] + 1
                or result["correlation_id"] != job["correlation_id"]
                or any(result[k] != payload[k] for k in ("root_identity_event_id", "input_sha256", "requested_by"))
                or type(result["accepted_delivery_fence"]) is not int or result["accepted_delivery_fence"] != current.fence_token
                or result["accepted_delivery_fence"] != payload.get("accepted_delivery_fence")
                or type(result["accepted_business_fence"]) is not int or result["accepted_business_fence"] != job["fence_token"]
                or type(result["accepted_attempt"]) is not int or result["accepted_attempt"] != job["attempt"]
                or not isinstance(result["accepted_worker"], str) or not result["accepted_worker"]
                or result["accepted_worker"] != payload.get("accepted_worker")
                or not isinstance(result["authority"], dict)
                or result["authority"].get("subject_id") != payload["requested_by"]
                or result["authority"].get("role") != "data_owner"):
            raise JobFenceRejectedError("NATIVE_RECHECK_TERMINAL_RECEIPT_CONFLICT")
        before()
        waiting_state, _ = self._waiting_context(payload)
        transition = self.engine.query_one("SELECT transition_id FROM intake.intake_stage_transitions "
            "WHERE tenant_id=? AND transition_id=? AND intake_id=? AND job_id=? "
            "AND from_state=? AND to_state='CHECKING_SOURCE_POLICY' "
            "AND expected_version=? AND resulting_version=? AND actor_subject_id IS NULL AND service_principal=? "
            "AND permission='intake.recheck_source_policy' AND reason_code='SOURCE_POLICY_RECHECK_VERIFIED' "
            "AND idempotency_key=? AND causation_id=? AND correlation_id=?",
            (tenant, result["transition_id"], payload["intake_id"], job["job_id"], waiting_state, payload["expected_intake_version"],
             result["version"], NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE, payload["event_id"], payload["event_id"], job["correlation_id"]))
        before()
        audit = self.engine.query_one("SELECT after_value FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=? "
            "AND resource_type='intake' AND resource_id=? AND actor_subject_id IS NULL AND service_principal=? "
            "AND actor_role='SVC_INTAKE' AND action='intake.source_policy_recheck_completed.v1' AND result='SUCCEEDED' "
            "AND causation_id=? AND correlation_id=?", (tenant, result["audit_event_id"], payload["intake_id"],
                NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE, payload["event_id"], job["correlation_id"]))
        before()
        row = self.engine.query_one("SELECT * FROM workflow.outbox_events WHERE tenant_id=? AND event_id=?",
                                    (tenant, result["outbox_event_id"]))
        if not transition or not audit or not row:
            raise JobFenceRejectedError("NATIVE_RECHECK_TERMINAL_EVIDENCE_MISSING")
        snapshot, event = _object(audit["after_value"]), _object(row["payload"])
        expected = {"intake_id": payload["intake_id"], "from_state": waiting_state,
            "to_state": "CHECKING_SOURCE_POLICY", "transition_id": result["transition_id"], "version": result["version"],
            "occurred_at": event.get("occurred_at")}
        if (any(snapshot.get(k) != v for k, v in result.items())
                or snapshot.get("native_recheck_provenance") != self._provenance(payload)
                or validate_event(event) or event.get("event_id") != result["outbox_event_id"]
                or row["event_id"] != event["event_id"] or row["event_type"] != "intake.state_changed"
                or event.get("event_type") != row["event_type"] or row["event_version"] != 1
                or type(event.get("event_version")) is not int or event["event_version"] != 1
                or row["aggregate_type"] != "intake" or event.get("aggregate_type") != "intake"
                or event.get("producer") != "listing_intake_service" or event.get("schema_ref") != "#/payloads/IntakeStateChangedV1"
                or event.get("tenant_id") != tenant or row["aggregate_id"] != payload["intake_id"]
                or event.get("aggregate_id") != row["aggregate_id"] or row["aggregate_version"] != result["version"]
                or type(event.get("aggregate_version")) is not int or event["aggregate_version"] != result["version"]
                or row["correlation_id"] != job["correlation_id"] or event.get("correlation_id") != job["correlation_id"]
                or row["partition_key"] != tenant + ":" + payload["intake_id"] or event.get("partition_key") != row["partition_key"]
                or event.get("actor_ref") != NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE
                or event.get("causation_id") != payload["event_id"] or event.get("payload") != expected
                or _time(row["occurred_at"]) != _time(event["occurred_at"])):
            raise JobFenceRejectedError("NATIVE_RECHECK_TERMINAL_EVIDENCE_CONFLICT")
        return result

    def _source_producer(self, tenant, event_id, before):
        """Constant-depth evidence for a child policy job, without live grants."""
        before()
        row = self.graph._one("SELECT * FROM workflow.outbox_events WHERE tenant_id=? AND event_id=?", (tenant, _uuid(event_id)))
        event = _object(row["payload"])
        data = _object(event.get("payload"))
        before()
        transition = self.graph._one("SELECT job_id FROM intake.intake_stage_transitions WHERE tenant_id=? "
            "AND transition_id=? AND intake_id=?", (tenant, _uuid(data.get("transition_id")), row["aggregate_id"]))
        before()
        delivery = self.queue.get(transition["job_id"])
        if delivery is None:
            raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_PRODUCER_DELIVERY_MISSING")
        bound_tenant, generation = self.auth.decode(delivery)
        result = self._committed_result(delivery, tenant, generation, before=before)
        if (bound_tenant != tenant or result is None or result["outbox_event_id"] != event_id
                or result["intake_id"] != row["aggregate_id"]):
            raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_PRODUCER_PROOF_CONFLICT")
        _, original = self._root_identity(tenant, result["root_identity_event_id"], before)
        before()
        recorded = self.graph._one("SELECT payload FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                                   (tenant, transition["job_id"]))
        admitted_payload = _object(recorded["payload"])
        waiting = ({k: admitted_payload[k] for k in ("waiting_state", "waiting_source_policy")}
                   if "waiting_state" in admitted_payload else {})
        return row, event, hashlib.sha256(_json(event).encode()).hexdigest(), {
            **original, "recheck": True, "root_identity_event_id": result["root_identity_event_id"],
            "recheck_provenance": {"request_id": result["request_id"], "job_id": result["job_id"],
                "outbox_event_id": event_id, "root_identity_event_id": result["root_identity_event_id"],
                "input_sha256": result["input_sha256"], **waiting}}

    def execute(self, delivery):
        tenant, generation = self.auth.decode(delivery)
        policy = native_intake_source_policy_recheck_policy()
        self.source._partition(policy, tenant)
        with self.graph.transaction(tenant):
            if result := self._committed_result(delivery, tenant, generation):
                return result
            current, job, payload = self._locked(delivery, tenant, generation)
            def before():
                self.source._budget(job["timeout_at"], current.lease_expires_at)
            before()
            if (job["status"] not in {"QUEUED", "RETRYING"} or job["result"] is not None
                    or payload["policy"] != policy or job["max_attempts"] != policy["max_attempts"]
                    or job["attempt"] >= job["max_attempts"] or job["cancellation_requested_at"] is not None
                    or (job["next_attempt_at"] is not None and _time(job["next_attempt_at"]) > self.source._clock())):
                raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_ATTEMPT_NOT_ADMITTED")
            event, _ = self._admitted_request(job, payload, before)
            before()
            intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE", (tenant, payload["intake_id"]))
            principal = self._authority(payload["requested_by"], intake, before)
            root = self._waiting(intake, payload["expected_intake_version"], before)
            if (root != payload["root_identity_event_id"] or self.source._input_digest(intake) != payload["input_sha256"]
                    or self._waiting_context(payload) != (intake["processing_state"], intake["source_policy_state"])):
                raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_INPUT_CHANGED")
            before()
            if self.engine.execute("UPDATE workflow.jobs SET status='RUNNING',attempt=attempt+1,fence_token=fence_token+1,"
                "version=version+1,heartbeat_at=clock_timestamp(),lease_expires_at=? "
                "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND status IN ('QUEUED','RETRYING') "
                "AND attempt<max_attempts AND cancellation_requested_at IS NULL AND timeout_at>clock_timestamp()",
                (current.lease_expires_at.isoformat(), tenant, job["job_id"], job["version"], job["fence_token"])).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_RECHECK_ACCEPTANCE_FENCE_REJECTED")
            business_version, business_fence = job["version"] + 1, job["fence_token"] + 1
            at, transition_id, audit_id = self.source._clock(), str(uuid4()), str(uuid4())
            aggregate = IntakeAggregate(id=intake["intake_id"], tenant_id=tenant, stage=IntakeStage(intake["processing_state"]),
                version=intake["version"], created_by=intake["submitter_subject_id"], source_id=intake["source_id"])
            IntakeStateMachine.transition(aggregate, IntakeStage.CHECKING_SOURCE_POLICY, TransitionContext(
                actor=Actor(NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE, PrincipalRole.SVC_INTAKE, tenant),
                version_before=intake["version"], idempotency_key=payload["event_id"], reason="Verify current source policy recheck authority.",
                correlation_id=intake["correlation_id"], causation_id=payload["event_id"], current_time=at,
                source_policy_recheck_verified=True))
            before()
            if self.engine.execute("UPDATE intake.intakes SET processing_state='CHECKING_SOURCE_POLICY',version=?,last_transition_at=? "
                "WHERE tenant_id=? AND intake_id=? AND version=? AND processing_state=? "
                "AND source_policy_state=? AND deleted_at IS NULL AND resolved_listing_id IS NULL AND completed_at IS NULL",
                (aggregate.version, at.isoformat(), tenant, intake["intake_id"], intake["version"],
                 intake["processing_state"], intake["source_policy_state"])).rowcount != 1:
                raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_INTAKE_CAS_REJECTED")
            before()
            sequence = self.engine.query_one("SELECT COALESCE(MAX(sequence_no),0)+1 AS next FROM intake.intake_stage_transitions "
                "WHERE tenant_id=? AND intake_id=?", (tenant, intake["intake_id"]))["next"]
            before()
            self.engine.execute("INSERT INTO intake.intake_stage_transitions (transition_id,tenant_id,intake_id,sequence_no,"
                "from_state,to_state,actor_subject_id,service_principal,permission,reason_code,reason_text,idempotency_key,"
                "expected_version,resulting_version,job_id,correlation_id,causation_id,occurred_at) "
                "VALUES (?,?,?,?,?,'CHECKING_SOURCE_POLICY',NULL,?,"
                "'intake.recheck_source_policy','SOURCE_POLICY_RECHECK_VERIFIED',?,?,?,?,?,?,?,?)",
                (transition_id, tenant, intake["intake_id"], sequence, intake["processing_state"], NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE,
                 "Verify current source policy recheck authority.", payload["event_id"], intake["version"], aggregate.version,
                 job["job_id"], intake["correlation_id"], payload["event_id"], at.isoformat()))
            state_event = DomainEvent(event_type="intake.state_changed", producer="listing_intake_service", tenant_id=tenant,
                aggregate_type="intake", aggregate_id=intake["intake_id"], aggregate_version=aggregate.version,
                partition_key=tenant + ":" + intake["intake_id"], correlation_id=intake["correlation_id"],
                causation_id=payload["event_id"], actor_ref=NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE, occurred_at=at,
                schema_ref="#/payloads/IntakeStateChangedV1", payload={"intake_id": intake["intake_id"],
                    "from_state": intake["processing_state"], "to_state": "CHECKING_SOURCE_POLICY",
                    "transition_id": transition_id, "version": aggregate.version, "occurred_at": at.isoformat()})
            scope = principal.scope
            authority = {"subject_id": principal.subject_id, "role": "data_owner", "checked_at": at.isoformat(),
                "scope_sha256": hashlib.sha256(_json({"tenant_id": scope.tenant_id, "clearance": scope.clearance.name,
                    **{k: sorted(getattr(scope, k)) for k in ("brand_ids", "region_ids", "store_ids", "assigned_area_ids", "heat_zone_ids", "modules")}}).encode()).hexdigest()}
            result = {"job_id": job["job_id"], "request_id": payload["request_id"], "intake_id": intake["intake_id"],
                "processing_state": "CHECKING_SOURCE_POLICY", "version": aggregate.version, "transition_id": transition_id,
                "audit_event_id": audit_id, "outbox_event_id": state_event.event_id, "correlation_id": intake["correlation_id"],
                **{k: payload[k] for k in ("root_identity_event_id", "input_sha256", "requested_by")},
                "accepted_delivery_fence": current.fence_token, "accepted_business_fence": business_fence,
                "accepted_attempt": job["attempt"] + 1, "accepted_worker": current.locked_by, "authority": authority}
            before()
            self._audit(intake, "intake.source_policy_recheck_completed.v1", {"processing_state": intake["processing_state"], "version": intake["version"]},
                {**result, "native_recheck_provenance": self._provenance(payload)}, at, audit_id, event["event_id"])
            before()
            self.graph._outbox(state_event)
            before()
            if (native_intake_source_policy_recheck_policy() != policy
                    or not self.engine.query_one("SELECT job_id FROM durable_jobs WHERE job_id=? AND status=? "
                        "AND fence_token=? AND locked_by=? AND lease_expires_at>clock_timestamp()",
                        (job["job_id"], JobStatus.RUNNING.value, current.fence_token, current.locked_by))):
                raise JobFenceRejectedError("NATIVE_RECHECK_FINAL_DELIVERY_OR_POLICY_REJECTED")
            before()
            accepted = {**payload, "accepted_delivery_fence": current.fence_token, "accepted_worker": current.locked_by}
            if self.engine.execute("UPDATE workflow.jobs SET status='SUCCEEDED',checkpoint='CHECKING_SOURCE_POLICY',result=?::jsonb,"
                "payload=?::jsonb,version=version+1,heartbeat_at=?,lease_expires_at=NULL,updated_at=? "
                "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND status='RUNNING' "
                "AND attempt=? AND cancellation_requested_at IS NULL AND timeout_at>clock_timestamp() AND lease_expires_at>clock_timestamp()",
                (_json(result), _json(accepted), at.isoformat(), at.isoformat(), tenant, job["job_id"], business_version,
                 business_fence, job["attempt"] + 1)).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_RECHECK_FINAL_BUSINESS_FENCE_REJECTED")
            before()
            self.queue.update_status(job["job_id"], JobStatus.SUCCEEDED, expected_version=current.version, fence_token=current.fence_token)
            return result

    def settle_failure(self, delivery, error):
        from modules.listing.application.native_intake_recheck_settlement import (
            NativeRecheckSettlement,
        )

        return NativeRecheckSettlement(self).settle(delivery, error)


def run_native_intake_source_policy_recheck_delivery(delivery, owner):
    try:
        return owner.execute(delivery)
    except Exception as error:
        return owner.settle_failure(delivery, error)
