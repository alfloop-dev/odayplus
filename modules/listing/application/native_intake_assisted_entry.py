"""Native admission and fresh human/source binding for manual evidence jobs.

Admission reserves an existing committed human command. It creates neither
a snapshot nor an Intake transition and performs no external IO.
"""

from __future__ import annotations

import hashlib
from uuid import UUID, uuid4, uuid5

from modules.listing.application.native_intake_assisted_entry_input import manual_evidence_document
from modules.listing.application.native_intake_assisted_entry_job_proof import (
    NativeAssistedEntryJobProof,
    assisted_job_provenance,
)
from modules.listing.application.native_intake_assisted_entry_policy import (
    JOB_TYPE,
    NativeAssistedEntryAuthenticator,
    NativeAssistedEntryConflict,
    native_intake_assisted_entry_policy,
)
from modules.listing.application.native_intake_assisted_entry_request import human_transition_role
from modules.listing.application.native_intake_assisted_entry_request_proof import (
    NativeAssistedEntryRequestProof,
    _object,
    _time,
)
from modules.listing.application.native_intake_assisted_entry_source import (
    NativeAssistedEntrySourceProof,
)
from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_pending_commands import (
    NativeIntakeCommandBudget,
    NativeIntakePendingCommands,
)
from modules.listing.application.native_intake_requester_authority import (
    current_native_assisted_principal,
)
from modules.listing.application.native_intake_retrieval_policy import (
    canonical_json,
    canonical_sha256,
)
from modules.listing.application.native_intake_retrieval_quota import NativeIntakeRetrievalQuota
from modules.listing.domain.intake_states import (
    Actor,
    IntakeAggregate,
    IntakeStage,
    IntakeStateMachine,
    TransitionContext,
)
from shared.audit.backlog import CanonicalAuditBacklog
from shared.jobs.queue import JobFenceRejectedError, JobRequest


class NativeAssistedSourceChanged(NativeAssistedEntryConflict):
    pass


class NativeIntakeAssistedEntryOwner:
    def __init__(self, graph, *, snapshot_transport_factory=None):
        self.graph, self.engine, self.queue = graph, graph.engine, graph.queue
        self.source = NativeAssistedEntrySourceProof(graph)
        self.requests = NativeAssistedEntryRequestProof(graph)
        self.jobs = NativeAssistedEntryJobProof(graph)
        self.pending = NativeIntakePendingCommands(graph)
        self.auth = NativeAssistedEntryAuthenticator()
        self.quota = NativeIntakeRetrievalQuota(self.engine)
        # Explicit constructor injection is an owned synthetic test seam.
        # Runtime never chooses a process/transport implementation by config.
        self.snapshot_transport_factory = snapshot_transport_factory

    def _clock(self):
        return self.source.source._clock()

    def _fresh(self, delivery, tenant, generation, *, settlement=False, business_fence=None):
        from modules.listing.application.native_intake_assisted_entry_execution import (
            NativeAssistedExecutionReads,
        )

        return NativeAssistedExecutionReads(self).fresh(delivery, tenant, generation,
            settlement=settlement, business_fence=business_fence)

    def _historical_locked(self, delivery, tenant, generation, *, business_fence=None):
        from modules.listing.application.native_intake_assisted_entry_execution import (
            NativeAssistedExecutionReads,
        )

        return NativeAssistedExecutionReads(self).historical_locked(delivery, tenant, generation,
            business_fence=business_fence)

    def _accept(self, delivery, tenant, generation):
        from modules.listing.application.native_intake_assisted_entry_execution import (
            NativeAssistedExecutionReads,
        )

        return NativeAssistedExecutionReads(self).accept(delivery, tenant, generation)

    @staticmethod
    def delivery_identity(delivery):
        from modules.listing.application.native_intake_assisted_entry_identity import (
            assisted_delivery_identity,
        )

        return assisted_delivery_identity(delivery)

    @staticmethod
    def _authority_snapshot(principal, at):
        scope = principal.scope
        fields = {"tenant_id": scope.tenant_id, "clearance": scope.clearance.name,
            **{key: sorted(getattr(scope, key)) for key in (
                "brand_ids", "region_ids", "store_ids", "assigned_area_ids", "heat_zone_ids", "modules")}}
        return {"subject_id": principal.subject_id, "role": human_transition_role(principal).value,
            "checked_at": at.isoformat(), "scope_sha256": canonical_sha256(fields)}

    @staticmethod
    def _document(request, intake):
        return manual_evidence_document(tenant_id=intake["tenant_id"], intake_id=intake["intake_id"],
            request_id=request.event["event_id"], requested_by=request.event["payload"]["requested_by"],
            source_id=intake["source_id"], fields=request.command.fields,
            correction_ids={row["field_path"]: row["correction_id"] for row in request.rows})

    @staticmethod
    def _preview(intake, request, principal, at):
        aggregate = IntakeAggregate(id=intake["intake_id"], tenant_id=intake["tenant_id"],
            stage=IntakeStage.AWAITING_ASSISTED_ENTRY, version=intake["version"],
            created_by=intake["submitter_subject_id"], source_id=intake["source_id"],
            canonical_url=intake["canonical_url"], owner_id=native_intake_uuid(intake["submitter_subject_id"]))
        IntakeStateMachine.transition(aggregate, IntakeStage.PARSING, TransitionContext(
            actor=Actor(principal.subject_id, human_transition_role(principal), intake["tenant_id"]),
            version_before=intake["version"], idempotency_key=request.event["event_id"],
            reason=request.command.reason, risk_acknowledged=request.command.risk_acknowledged,
            correlation_id=intake["correlation_id"], causation_id=request.event["event_id"], current_time=at))
        return aggregate

    def _human(self, request, intake, before):
        principal = current_native_assisted_principal(self.engine, request.event["payload"]["requested_by"], intake,
            risk_summary=request.command.risk_summary, risk_acknowledged=request.command.risk_acknowledged, before=before)
        self._preview(intake, request, principal, self._clock())
        return principal

    def _binding(self, intake, request, parent, policy):
        self.source.bind_waiting(intake, parent)
        data = request.event["payload"]
        if (data["policy_sha256"] != policy.sha256 or intake["version"] != data["expected_intake_version"]
                or data["input_sha256"] != self.source.source._input_digest(intake)
                or (_time(data["timeout_at"]) - _time(data["requested_at"])).total_seconds() != policy.deadline_seconds):
            raise NativeAssistedEntryConflict("NATIVE_ASSISTED_CURRENT_COMMAND_OR_CONFIG_REJECTED")

    @staticmethod
    def _positive_source(payload, classified):
        if (classified.target_state != "AWAITING_ASSISTED_ENTRY"
                or classified.effective_policy not in {"AUTH_REQUIRED", "ASSISTED_ENTRY_ONLY"}
                or classified.valid_until is None):
            raise NativeAssistedEntryConflict("NATIVE_ASSISTED_CURRENT_MANUAL_APPROVAL_REQUIRED")
        if payload.get("accepted_source_sha256") != classified.source_snapshot_sha256:
            raise NativeAssistedSourceChanged("NATIVE_ASSISTED_ACCEPTED_SOURCE_CHANGED")

    def _audit(self, intake, action, before_value, after_value, at, *, audit_id, causation,
               reason="Execute the currently authorized frozen human assisted-entry command."):
        self.graph.audit_sink.persist({"audit_event_id": audit_id, "tenant_id": intake["tenant_id"],
            "resource_type": "intake", "resource_id": intake["intake_id"], "action": action, "result": "SUCCEEDED",
            "actor_subject_id": None, "service_principal": JOB_TYPE, "actor_role": "SVC_INTAKE",
            "correlation_id": intake["correlation_id"], "causation_id": causation, "occurred_at": at.isoformat(),
            "before_value": before_value, "after_value": after_value,
            "reason": reason},
            {key: intake[key] for key in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")})

    def admit_event(self, tenant, event_id):
        tenant, event_id = native_intake_uuid(tenant), native_intake_uuid(event_id)
        policy = native_intake_assisted_entry_policy(tenant)
        job_id = str(uuid5(UUID(event_id), JOB_TYPE))
        with self.graph.transaction(tenant):
            before = NativeIntakeCommandBudget(self.source.source)
            before()
            self.engine.query_one("SELECT job_id FROM durable_jobs WHERE job_id=? FOR UPDATE", (job_id,))
            before()
            prior = self.engine.query_one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=? FOR UPDATE",
                (tenant, job_id))
            before()
            row = self.graph._one("SELECT payload FROM workflow.outbox_events WHERE tenant_id=? AND event_id=? "
                "AND aggregate_type='intake_assisted_entry'", (tenant, event_id))
            intake_id = native_intake_uuid(_object(row["payload"])["payload"]["intake_id"])
            before()
            intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE",
                (tenant, intake_id))
            if prior is not None:
                receipt, request, parent = self.jobs.admission_with_request(prior, before=before)
            else:
                request, parent = self.requests.read_with_parent(tenant, intake_id, event_id, before=before)
            self._binding(intake, request, parent, policy)
            self._human(request, intake, before)
            _, classified = self.source.current_manual_source(intake, before=before)
            before.deadline = min(before.deadline, classified.valid_until, _time(request.event["payload"]["timeout_at"]))
            self.pending.require_not_withdrawn(request.event, "ASSISTED_ENTRY", before)
            self.pending.require_clear(intake, before=before, own_request_id=event_id)
            token = self.auth.sign(job_id, tenant, 1)
            if prior is not None:
                before()
                if self.auth.decode(self.queue.get(job_id)) != (tenant, _object(prior["payload"])["delivery_generation"]):
                    raise JobFenceRejectedError("NATIVE_ASSISTED_ADMISSION_REPLAY_KEY_REJECTED")
                return receipt
            before()
            CanonicalAuditBacklog(self.engine).require_available(tenant)
            at, audit_id = self._clock(), str(uuid4())
            data = request.event["payload"]
            receipt = {"job_id": job_id, "request_id": event_id, "intake_id": intake_id,
                "admitted_intake_version": data["expected_intake_version"], "job_status_at_admission": "QUEUED",
                "timeout_at": data["timeout_at"], "audit_event_id": audit_id}
            payload = {**data, "event_id": event_id, "event_sha256": request.event_sha256,
                "policy": policy.snapshot(), "delivery_generation": 1,
                "attempt_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                "admission_receipt": receipt, "attempt_intents": []}
            before()
            if self.engine.execute("INSERT INTO workflow.jobs (job_id,tenant_id,job_type,aggregate_type,aggregate_id,"
                    "status,checkpoint,attempt,max_attempts,fence_token,timeout_at,payload,correlation_id,idempotency_key) "
                    "SELECT ?,?,?,'intake_assisted_entry',?,'QUEUED','AWAITING_ASSISTED_ENTRY',0,?,1,?,?::jsonb,?,? "
                    "WHERE ?::timestamptz>clock_timestamp() AND ?::timestamptz>clock_timestamp()",
                    (job_id, tenant, JOB_TYPE, event_id, policy.max_attempts, data["timeout_at"], canonical_json(payload),
                        intake["correlation_id"], event_id, data["timeout_at"], classified.valid_until.isoformat())).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_ASSISTED_ADMISSION_EXPIRED")
            before()
            physical, created = self.queue.enqueue(JobRequest(JOB_TYPE,
                {"job_id": job_id, "tenant_id": tenant, "attempt_token": token}, job_id=job_id,
                idempotency_key=JOB_TYPE + ":" + tenant + ":" + event_id), correlation_id=intake["correlation_id"])
            if not created or physical.job_id != job_id:
                raise NativeAssistedEntryConflict("NATIVE_ASSISTED_PHYSICAL_RESERVATION_CONFLICT")
            before()
            self.engine.execute("UPDATE durable_jobs SET max_retries=? WHERE job_id=?", (policy.max_attempts, job_id))
            before()
            self._audit(intake, "intake.assisted_entry_admitted.v1", None,
                {**receipt, "native_assisted_provenance": assisted_job_provenance(payload)}, at,
                audit_id=audit_id, causation=event_id)
            before()
            if (native_intake_assisted_entry_policy(tenant) != policy
                    or self.auth.decode(self.queue.get(job_id)) != (tenant, 1)):
                raise JobFenceRejectedError("NATIVE_ASSISTED_ADMISSION_CONFIG_CHANGED")
            before()
            return receipt

    def admit_partition(self, tenant, *, limit, cursor=None):
        from modules.listing.application.native_intake_assisted_entry_partition import (
            NativeAssistedEntryPartition,
        )

        tenant = native_intake_uuid(tenant)
        native_intake_assisted_entry_policy(tenant)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("A bounded partition size from 1 to 100 is required")
        yield from NativeAssistedEntryPartition(self).admit(tenant, limit=limit, cursor=cursor)

    def execute(self, delivery):
        from modules.listing.application.native_intake_assisted_entry_runtime import (
            NativeIntakeAssistedEntryRuntime,
        )

        return NativeIntakeAssistedEntryRuntime(self).execute(delivery)


def run_native_intake_assisted_entry_delivery(delivery, owner: NativeIntakeAssistedEntryOwner):
    return owner.execute(delivery)
