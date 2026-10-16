"""Native retrieval: durable admission and per-operation current authority.

Historical source decisions authenticate the command's origin. They never
grant a later HTTP or storage operation. No external IO belongs in an SQL root.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4, uuid5

from modules.listing.application.intake_audit import POLICY_EVIDENCE_KEY
from modules.listing.application.native_intake_job_runtime import set_native_intake_sql_budget
from modules.listing.application.native_intake_requester_authority import (
    current_native_retrieval_submitter,
)
from modules.listing.application.native_intake_retrieval_policy import (
    JOB_TYPE,
    NativeIntakeRetrievalAuthenticator,
    NativeIntakeRetrievalConflict,
    NativeIntakeRetrievalUnavailable,
    canonical_json,
    canonical_sha256,
    native_intake_retrieval_policy,
)
from modules.listing.application.native_intake_retrieval_quota import NativeIntakeRetrievalQuota
from modules.listing.application.native_intake_source_policy import NativeIntakeSourcePolicyOwner
from modules.listing.application.native_intake_source_policy_rules import (
    evaluate_native_source_policy,
    source_policy_snapshot_json,
)
from modules.listing.application.native_intake_url_identity import normalize_native_intake_url
from shared.domain.events import validate_event
from shared.jobs.queue import JobFenceRejectedError, JobRequest, JobStatus

ADMISSION_MILLISECONDS = 750


class NativeRetrievalSourceChanged(NativeIntakeRetrievalConflict):
    pass


class NativeRetrievalQuarantineRequired(NativeIntakeRetrievalConflict):
    pass


def _object(value):
    result = json.loads(value) if isinstance(value, str) else value
    if not isinstance(result, dict):
        raise NativeIntakeRetrievalConflict("NATIVE_RETRIEVAL_OBJECT_REJECTED")
    return result


def _time(value):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise NativeIntakeRetrievalConflict("NATIVE_RETRIEVAL_TIME_UNAVAILABLE")
    return result


def audited_value_matches(actual, expected):
    """Require exact business evidence plus the sink-owned retention binding."""
    if not isinstance(actual, dict) or set(actual) != set(expected) | {POLICY_EVIDENCE_KEY}:
        return False
    policy = actual[POLICY_EVIDENCE_KEY]
    return (all(actual[k] == v for k, v in expected.items()) and isinstance(policy, dict)
        and set(policy) == {"policy_version_id", "policy_sha256", "approved_by", "approved_at", "retained_until"}
        and all(isinstance(value, str) and value for value in policy.values())
        and len(policy["policy_sha256"]) == 64
        and all(c in "0123456789abcdef" for c in policy["policy_sha256"]))


class NativeIntakeRetrievalOwner:
    def __init__(self, graph: Any, *, http_transport=None, snapshot_transport_factory=None):
        self.graph, self.engine, self.queue = graph, graph.engine, graph.queue
        if getattr(self.engine, "dialect", None) != "postgresql":
            raise NativeIntakeRetrievalUnavailable("NATIVE_RETRIEVAL_POSTGRESQL_REQUIRED")
        self.auth = NativeIntakeRetrievalAuthenticator()
        self.source = NativeIntakeSourcePolicyOwner(graph)
        self.quota = NativeIntakeRetrievalQuota(self.engine)
        # Component tests may construct owned synthetic transports. Runtime
        # never selects a child, address or transport implementation by config.
        self.http_transport = http_transport
        self.snapshot_transport_factory = snapshot_transport_factory

    def _clock(self):
        return _time(self.engine.query_one("SELECT clock_timestamp() AS now")["now"])

    def _admission_budget(self, bound):
        milliseconds = math.floor((bound - self._clock()).total_seconds() * 1000) - 1
        if milliseconds < 1:
            raise NativeIntakeRetrievalConflict("NATIVE_RETRIEVAL_ADMISSION_DEADLINE_EXPIRED")
        value = str(milliseconds) + "ms"
        self.engine.query_one("SELECT set_config('lock_timeout',?,true)", (value,))
        self.engine.query_one("SELECT set_config('statement_timeout',?,true)", (value,))

    def _budget(self, job, physical, *, settlement=False):
        lease = physical.lease_expires_at
        if job["status"] == "RUNNING":
            if job["lease_expires_at"] is None:
                raise JobFenceRejectedError("NATIVE_RETRIEVAL_BUSINESS_LEASE_MISSING")
            lease = min(lease, _time(job["lease_expires_at"]))
        set_native_intake_sql_budget(self.engine, lease if settlement else _time(job["timeout_at"]),
            lease, self._clock(), error_code="NATIVE_RETRIEVAL_DEADLINE_OR_DUAL_LEASE_EXPIRED")

    def _audit(self, intake, action, before, after, at, *, audit_id, causation):
        self.graph.audit_sink.persist({
            "audit_event_id": audit_id, "tenant_id": intake["tenant_id"], "resource_type": "intake",
            "resource_id": intake["intake_id"], "action": action, "result": "SUCCEEDED",
            "actor_subject_id": None, "service_principal": JOB_TYPE, "actor_role": "SVC_RETRIEVAL",
            "correlation_id": intake["correlation_id"], "causation_id": causation,
            "occurred_at": at.isoformat(), "before_value": before, "after_value": after,
            "reason": "Execute the currently authorized native retrieval command.",
        }, {k: intake[k] for k in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")})

    def _parent(self, tenant, event_id, before):
        # The bounded read-only view covers recursive original identity OR
        # bounded recheck proof, including every SQL/queue read it performs.
        parent = NativeIntakeSourcePolicyOwner(self.source._producer_reads(before))
        row = parent.graph._one("SELECT * FROM workflow.outbox_events WHERE tenant_id=? AND event_id=?", (tenant, event_id))
        event = _object(row["payload"])
        if (validate_event(event) or row["event_type"] != "intake.state_changed"
                or row["event_version"] != 1 or event.get("event_type") != row["event_type"]
                or type(event.get("event_version")) is not int or event["event_version"] != 1
                or row["aggregate_type"] != "intake" or event.get("aggregate_type") != "intake"
                or event.get("producer") != "listing_intake_service"
                or event.get("schema_ref") != "#/payloads/IntakeStateChangedV1"
                or event.get("actor_ref") != "native-intake-source-policy"
                or event["event_id"] != row["event_id"] or event["tenant_id"] != tenant
                or event["aggregate_id"] != row["aggregate_id"]
                or type(event.get("aggregate_version")) is not int
                or event["aggregate_version"] != row["aggregate_version"]
                or event["correlation_id"] != row["correlation_id"]
                or row["partition_key"] != tenant + ":" + row["aggregate_id"]
                or event["partition_key"] != row["partition_key"]
                or event["payload"].get("intake_id") != row["aggregate_id"]
                or event["payload"].get("from_state") != "CHECKING_SOURCE_POLICY"
                or event["payload"].get("to_state") != "RETRIEVING"
                or event["payload"].get("version") != event["aggregate_version"]):
            raise NativeIntakeRetrievalConflict("NATIVE_RETRIEVAL_PARENT_EVENT_REJECTED")
        transition = parent.graph._one("SELECT job_id FROM intake.intake_stage_transitions "
            "WHERE tenant_id=? AND transition_id=? AND intake_id=?",
            (tenant, event["payload"]["transition_id"], row["aggregate_id"]))
        business = parent.graph._one("SELECT payload FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                                     (tenant, transition["job_id"]))
        delivery = parent.queue.get(transition["job_id"])
        proof = parent._committed_result(delivery, tenant, _object(business["payload"]).get("delivery_generation")) if delivery else None
        if (proof is None or proof["outbox_event_id"] != event_id or proof["processing_state"] != "RETRIEVING"
                or proof["intake_id"] != row["aggregate_id"] or proof["version"] != row["aggregate_version"]
                or proof["policy_decision"]["effective_policy"] != "APPROVED_RETRIEVAL"):
            raise NativeIntakeRetrievalConflict("NATIVE_RETRIEVAL_PARENT_PROOF_MISSING")
        return event, canonical_sha256(event), proof

    def _binding(self, intake, payload, *, before):
        identity = normalize_native_intake_url(intake["original_url"])
        if (intake["processing_state"] != "RETRIEVING" or intake["version"] != payload["intake_version"]
                or intake["source_policy_state"] != "APPROVED_RETRIEVAL" or intake["intake_method"] != "URL"
                or intake["deleted_at"] is not None or intake["completed_at"] is not None
                or intake["resolved_listing_id"] is not None
                or intake["submitter_subject_id"] != payload["submitter_subject_id"]
                or self.source._input_digest(intake) != payload["input_sha256"]
                or intake["source_id"] != payload["source_id"]
                or intake["canonical_url"] != identity.canonical_url
                or intake["canonical_url_sha256"] != identity.canonical_url_sha256):
            raise JobFenceRejectedError("NATIVE_RETRIEVAL_CURRENT_INPUT_REJECTED")
        before()
        current_native_retrieval_submitter(self.engine, intake, before=before)
        before()

    def _current_source(self, intake, url, before):
        # Only pure URL validation may mean a prohibited destination. SQL
        # budget/fence errors also inherit ValueError and must retain their
        # ownership/retry semantics outside this narrow block.
        try:
            identity = normalize_native_intake_url(url)
        except ValueError as error:
            raise NativeRetrievalQuarantineRequired("NATIVE_RETRIEVAL_DESTINATION_PROHIBITED") from error
        before()
        matches = self.engine.query("SELECT source_id FROM intake.source_registry s WHERE EXISTS "
            "(SELECT 1 FROM unnest(s.allowed_hosts) AS h(host) WHERE lower(h.host)=?) "
            "ORDER BY source_id LIMIT 2 FOR SHARE", (identity.source_host,))
        before()
        source = self.graph._one("SELECT * FROM intake.source_registry WHERE source_id=? FOR SHARE", (intake["source_id"],))
        before()
        classification = evaluate_native_source_policy(source, source_host=identity.source_host,
            expected_source_id=intake["source_id"], host_match_count=len(matches), now=self._clock())
        return source, classification

    def _receipt(self, job, payload, before):
        receipt = _object(payload["admission_receipt"])
        if (set(receipt) != {"job_id", "intake_id", "admitted_intake_version", "job_status_at_admission",
                             "timeout_at", "source_policy_event_id", "audit_event_id"}
                or receipt["job_id"] != job["job_id"] or receipt["intake_id"] != job["aggregate_id"]
                or receipt["admitted_intake_version"] != payload["intake_version"]
                or receipt["job_status_at_admission"] != "QUEUED" or receipt["source_policy_event_id"] != payload["event_id"]
                or _time(receipt["timeout_at"]) != _time(job["timeout_at"])):
            raise JobFenceRejectedError("NATIVE_RETRIEVAL_ADMISSION_RECEIPT_REJECTED")
        before()
        audit = self.engine.query_one("SELECT after_value FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=? "
            "AND resource_type='intake' AND resource_id=? AND actor_subject_id IS NULL AND service_principal=? "
            "AND actor_role='SVC_RETRIEVAL' AND action='intake.retrieval_requested.v1' AND result='SUCCEEDED' "
            "AND causation_id=? AND correlation_id=?", (job["tenant_id"], receipt["audit_event_id"],
                job["aggregate_id"], JOB_TYPE, payload["event_id"], job["correlation_id"]))
        if audit is None or not audited_value_matches(_object(audit["after_value"]),
                {**receipt, "native_retrieval_provenance": self._provenance(payload)}):
            raise JobFenceRejectedError("NATIVE_RETRIEVAL_ADMISSION_AUDIT_REJECTED")
        before()
        return receipt

    @staticmethod
    def _provenance(payload):
        return {k: payload[k] for k in ("event_id", "event_sha256", "intake_version", "input_sha256",
            "submitter_subject_id", "source_id", "policy_sha256")}

    def admit_event(self, tenant: str, event_id: str):
        policy = native_intake_retrieval_policy(tenant)
        with self.graph.transaction(tenant):
            self.engine.query_one("SELECT set_config('statement_timeout',?,true)", ("750ms",))
            entry = self._clock()
            bound = entry + timedelta(milliseconds=ADMISSION_MILLISECONDS)

            def before():
                self._admission_budget(bound)

            event, digest, proof = self._parent(tenant, event_id, before)
            intake_id = event["aggregate_id"]
            job_id = str(uuid5(UUID(intake_id), JOB_TYPE + ":" + event_id))
            occurrence = intake_id + ":" + event_id
            before()
            self.engine.query_one("SELECT job_id FROM durable_jobs WHERE job_id=? FOR UPDATE", (job_id,))
            before()
            prior = self.engine.query_one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=? FOR UPDATE", (tenant, job_id))
            before()
            intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE", (tenant, intake_id))
            token = self.auth.sign(job_id, tenant, 1)
            immutable = {"event_id": event_id, "event_sha256": digest, "intake_version": proof["version"],
                "input_sha256": self.source._input_digest(intake), "submitter_subject_id": intake["submitter_subject_id"],
                "source_id": intake["source_id"], "policy": policy.snapshot(), "policy_sha256": policy.sha256}
            if prior is not None:
                payload = _object(prior["payload"])
                if (prior["job_type"] != JOB_TYPE or prior["aggregate_type"] != "intake" or prior["aggregate_id"] != intake_id
                        or prior["idempotency_key"] != occurrence or any(payload.get(k) != v for k, v in immutable.items())):
                    raise NativeIntakeRetrievalConflict("NATIVE_RETRIEVAL_RESERVATION_CONFLICT")
                # A historical admission receipt does not authorize IO. Current
                # execution rechecks input, submitter, source and both leases.
                return self._receipt(prior, payload, before)
            self._binding(intake, immutable, before=before)
            if intake["source_id"] != proof["policy_decision"]["source_id"]:
                raise NativeIntakeRetrievalConflict("NATIVE_RETRIEVAL_PARENT_SOURCE_CONFLICT")
            self.quota.schema(before=before)
            audit_id, at = str(uuid4()), self._clock()
            deadline = entry + timedelta(seconds=policy.deadline_seconds)
            receipt = {"job_id": job_id, "intake_id": intake_id, "admitted_intake_version": intake["version"],
                "job_status_at_admission": "QUEUED", "timeout_at": deadline.isoformat(),
                "source_policy_event_id": event_id, "audit_event_id": audit_id}
            payload = {**immutable, "delivery_generation": 1, "attempt_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                       "admission_receipt": receipt, "attempt_intents": []}
            before()
            self.engine.execute("INSERT INTO workflow.jobs (job_id,tenant_id,job_type,aggregate_type,aggregate_id,"
                "status,checkpoint,attempt,max_attempts,fence_token,timeout_at,payload,correlation_id,idempotency_key) "
                "VALUES (?,?,?,'intake',?,'QUEUED','RETRIEVING',0,?,1,?,?::jsonb,?,?)",
                (job_id, tenant, JOB_TYPE, intake_id, policy.max_attempts, deadline.isoformat(), canonical_json(payload),
                 intake["correlation_id"], occurrence))
            before()
            physical, created = self.queue.enqueue(JobRequest(JOB_TYPE, {"job_id": job_id, "tenant_id": tenant, "attempt_token": token},
                idempotency_key=JOB_TYPE + ":" + tenant + ":" + occurrence, job_id=job_id), correlation_id=intake["correlation_id"])
            if not created or physical.job_id != job_id:
                raise NativeIntakeRetrievalConflict("NATIVE_RETRIEVAL_DELIVERY_RESERVATION_CONFLICT")
            before()
            self.engine.execute("UPDATE durable_jobs SET max_retries=? WHERE job_id=?", (policy.max_attempts, job_id))
            before()
            self._audit(intake, "intake.retrieval_requested.v1", None,
                {**receipt, "native_retrieval_provenance": self._provenance(payload)}, at, audit_id=audit_id, causation=event_id)
            before()
            if native_intake_retrieval_policy(tenant) != policy:
                raise NativeIntakeRetrievalConflict("NATIVE_RETRIEVAL_ADMISSION_CONFIG_CHANGED")
            return receipt

    def admit_partition(self, tenant, *, limit):
        native_intake_retrieval_policy(tenant)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("A bounded partition size from 1 to 100 is required")
        with self.graph.transaction(tenant):
            self.engine.query_one("SELECT set_config('statement_timeout',?,true)", ("750ms",))
            rows = self.engine.query("SELECT e.event_id FROM workflow.outbox_events e "
                "JOIN intake.intakes i ON i.tenant_id=e.tenant_id AND i.intake_id=e.aggregate_id "
                "WHERE e.tenant_id=? AND e.event_type='intake.state_changed' AND e.aggregate_type='intake' "
                "AND e.aggregate_version=i.version AND i.processing_state='RETRIEVING' AND i.deleted_at IS NULL "
                "AND e.payload->>'actor_ref'='native-intake-source-policy' "
                "AND e.payload->'payload'->>'from_state'='CHECKING_SOURCE_POLICY' "
                "AND e.payload->'payload'->>'to_state'='RETRIEVING' "
                "AND NOT EXISTS (SELECT 1 FROM workflow.jobs j WHERE j.tenant_id=e.tenant_id AND j.job_type=? "
                "AND j.idempotency_key=i.intake_id::text||':'||e.event_id::text) "
                "ORDER BY e.occurred_at,e.event_id LIMIT ?", (tenant, JOB_TYPE, limit))
        for row in rows:
            yield self.admit_event(tenant, row["event_id"])

    def _locked(self, delivery, tenant, generation, *, settlement=False, business_fence=None):
        if self.auth.decode(delivery) != (tenant, generation):
            raise JobFenceRejectedError("NATIVE_RETRIEVAL_CAPABILITY_BINDING_REJECTED")
        self.engine.query_one("SELECT set_config('statement_timeout',?,true)", ("750ms",))
        self.engine.query_one("SELECT set_config('lock_timeout',?,true)", ("750ms",))
        entry = self._clock()
        preliminary = self.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?", (tenant, delivery.job_id))
        peek = self.queue.get(delivery.job_id)
        if peek is None or peek.lease_expires_at is None:
            raise JobFenceRejectedError("NATIVE_RETRIEVAL_PHYSICAL_LEASE_MISSING")
        # Fixed SQL-root budget never resets. Protective roots can outlive the
        # total work timeout, never either actual lease. Positive roots retain
        # the original total timeout across queue delay, redirects and retries.
        bound = min(entry + timedelta(milliseconds=750), peek.lease_expires_at)
        if preliminary["status"] == "RUNNING":
            if preliminary["lease_expires_at"] is None:
                raise JobFenceRejectedError("NATIVE_RETRIEVAL_BUSINESS_LEASE_MISSING")
            bound = min(bound, _time(preliminary["lease_expires_at"]))
        if not settlement:
            bound = min(bound, _time(preliminary["timeout_at"]))

        def before():
            set_native_intake_sql_budget(self.engine, bound, bound, self._clock(),
                                         error_code="NATIVE_RETRIEVAL_ROOT_BUDGET_EXPIRED")

        before()
        self.graph._one("SELECT job_id FROM durable_jobs WHERE job_id=? FOR UPDATE", (delivery.job_id,))
        current = self.queue.get(delivery.job_id)
        before()
        job = self.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=? FOR UPDATE", (tenant, delivery.job_id))
        payload = _object(job["payload"])
        now = self._clock()
        if (current is None or current.status != JobStatus.RUNNING or current.job_type != JOB_TYPE
                or not current.locked_by or current.locked_by != delivery.locked_by
                or current.fence_token != delivery.fence_token or current.payload != delivery.payload
                or current.correlation_id != delivery.correlation_id
                or current.lease_expires_at is None or current.lease_expires_at <= now
                or job["job_type"] != JOB_TYPE or job["aggregate_type"] != "intake"
                or job["idempotency_key"] != job["aggregate_id"] + ":" + payload["event_id"]
                or job["correlation_id"] != current.correlation_id
                or type(payload.get("delivery_generation")) is not int or payload["delivery_generation"] != generation
                or payload.get("attempt_token_sha256") != hashlib.sha256(delivery.payload["attempt_token"].encode()).hexdigest()
                or payload.get("policy_sha256") != canonical_sha256(payload["policy"])
                or payload["policy_sha256"] != native_intake_retrieval_policy(tenant).sha256):
            raise JobFenceRejectedError("NATIVE_RETRIEVAL_COMMAND_NOT_OWNED")
        if job["status"] == "RUNNING" and (
                job["lease_expires_at"] is None or _time(job["lease_expires_at"]) <= now
                or job["fence_token"] != payload.get("accepted_business_fence")
                or job["attempt"] != payload.get("accepted_attempt")
                or payload.get("accepted_delivery_fence") != current.fence_token
                or payload.get("accepted_worker") != current.locked_by
                or (business_fence is not None and job["fence_token"] != business_fence)):
            raise JobFenceRejectedError("NATIVE_RETRIEVAL_ACCEPTED_ATTEMPT_NOT_OWNED")
        self._receipt(job, payload, before)
        before()
        return current, job, payload, before

    def _fresh(self, delivery, tenant, generation, *, url=None, settlement=False, business_fence=None):
        physical, job, payload, before = self._locked(delivery, tenant, generation,
            settlement=settlement, business_fence=business_fence)
        if (job["status"] != "RUNNING" or job["result"] is not None
                or job["cancellation_requested_at"] is not None):
            raise JobFenceRejectedError("NATIVE_RETRIEVAL_RUNNING_CHECKPOINT_REQUIRED")
        before()
        intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE", (tenant, job["aggregate_id"]))
        self._binding(intake, payload, before=before)
        event, digest, proof = self._parent(tenant, payload["event_id"], before)
        if (digest != payload["event_sha256"] or event["aggregate_id"] != intake["intake_id"]
                or proof["version"] != payload["intake_version"]):
            raise JobFenceRejectedError("NATIVE_RETRIEVAL_ORIGINAL_PARENT_CHANGED")
        source, classified = self._current_source(intake, url or intake["canonical_url"], before)
        return physical, job, payload, before, intake, source, classified

    @staticmethod
    def _positive_source(payload, classified):
        if classified.target_state != "RETRIEVING" or classified.effective_policy != "APPROVED_RETRIEVAL":
            raise NativeRetrievalQuarantineRequired("NATIVE_RETRIEVAL_CURRENT_SOURCE_DENIED")
        if payload["accepted_source_sha256"] != classified.source_snapshot_sha256:
            raise NativeRetrievalSourceChanged("NATIVE_RETRIEVAL_SOURCE_CHANGED_DURING_ATTEMPT")

    def _accept(self, delivery, tenant, generation):
        with self.graph.transaction(tenant):
            physical, job, payload, before = self._locked(delivery, tenant, generation)
            policy = native_intake_retrieval_policy(tenant)
            now = self._clock()
            if (job["status"] not in {"QUEUED", "RETRYING"} or job["result"] is not None
                    or job["attempt"] >= job["max_attempts"] or job["max_attempts"] != policy.max_attempts
                    or job["cancellation_requested_at"] is not None
                    or _time(job["timeout_at"]) <= now
                    or (job["next_attempt_at"] is not None and _time(job["next_attempt_at"]) > now)):
                raise JobFenceRejectedError("NATIVE_RETRIEVAL_ATTEMPT_NOT_ADMITTED")
            before()
            intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE", (tenant, job["aggregate_id"]))
            self._binding(intake, payload, before=before)
            _, digest, _ = self._parent(tenant, payload["event_id"], before)
            if digest != payload["event_sha256"]:
                raise JobFenceRejectedError("NATIVE_RETRIEVAL_ORIGINAL_PARENT_CHANGED")
            source, classified = self._current_source(intake, intake["canonical_url"], before)
            accepted = {**payload, "accepted_worker": physical.locked_by,
                "accepted_delivery_fence": physical.fence_token, "accepted_business_fence": job["fence_token"] + 1,
                "accepted_attempt": job["attempt"] + 1, "accepted_source_sha256": classified.source_snapshot_sha256,
                "accepted_source_snapshot": json.loads(source_policy_snapshot_json(source))}
            before()
            if self.engine.execute("UPDATE workflow.jobs SET status='RUNNING',attempt=attempt+1,fence_token=fence_token+1,"
                "version=version+1,heartbeat_at=clock_timestamp(),lease_expires_at=?,payload=?::jsonb,updated_at=clock_timestamp() "
                "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND status IN ('QUEUED','RETRYING') "
                "AND result IS NULL AND cancellation_requested_at IS NULL AND timeout_at>clock_timestamp() AND attempt<max_attempts",
                (physical.lease_expires_at.isoformat(), canonical_json(accepted), tenant, job["job_id"],
                 job["version"], job["fence_token"])).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_RETRIEVAL_ACCEPTANCE_CAS_REJECTED")
            before()
            return accepted["accepted_business_fence"], policy

    def execute(self, delivery):
        from modules.listing.application.native_intake_retrieval_runtime import (
            NativeIntakeRetrievalRuntime,
        )

        return NativeIntakeRetrievalRuntime(self).execute(delivery)


def run_native_intake_retrieval_delivery(delivery, owner: NativeIntakeRetrievalOwner):
    return owner.execute(delivery)
