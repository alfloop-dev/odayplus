"""Withdraw an authenticated human request; an admitted worker settles itself."""

from __future__ import annotations

from datetime import timedelta

from modules.listing.application.intake_authorization import authorize_intake_action
from modules.listing.application.native_intake_assisted_entry_input import _safe_text
from modules.listing.application.native_intake_assisted_entry_job_proof import (
    NativeAssistedEntryJobProof,
)
from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryConflict,
)
from modules.listing.application.native_intake_assisted_entry_request import (
    human_transition_role,
    idempotency_key_sha256,
)
from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_pending_commands import (
    LANES,
    NativeIntakePendingCommands,
    cancellation_audit_id,
    cancellation_command_sha256,
    cancellation_id,
    cancellation_receipt,
)
from modules.listing.application.native_intake_requester_authority import (
    current_native_command_withdrawal_principal,
)
from modules.listing.application.native_intake_retrieval_policy import canonical_sha256
from modules.listing.application.native_intake_source_policy import (
    NativeIntakeSourcePolicyOwner,
    _object,
)
from modules.listing.application.native_intake_source_policy_recheck_history import (
    NativeHistoricalRecheckProof,
)
from shared.audit.backlog import CanonicalAuditBacklog
from shared.domain.events import DomainEvent


class NativeIntakeCommandWithdrawalOwner:
    def __init__(self, graph):
        self.graph, self.engine, self.queue = graph, graph.engine, graph.queue
        self.source = NativeIntakeSourcePolicyOwner(graph)
        self.pending = NativeIntakePendingCommands(graph)

    def cancel(self, principal, intake_id, request_id, *, request_kind, expected_version, reason, idempotency_key):
        if (request_kind not in LANES or type(expected_version) is not int or expected_version < 1
                or not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 4000):
            raise ValueError("NATIVE_COMMAND_WITHDRAWAL_INTENT_REQUIRED")
        _safe_text(reason)
        key_sha = idempotency_key_sha256(idempotency_key)
        tenant, actor = native_intake_uuid(principal.tenant_id), native_intake_uuid(principal.subject_id)
        intake_id, request_id = native_intake_uuid(intake_id), native_intake_uuid(request_id)
        event_id = cancellation_id(request_id, tenant, actor, request_kind, key_sha)
        with self.graph.transaction(tenant):
            deadline = self.source._clock() + timedelta(milliseconds=750)

            def before():
                self.source._admission_budget(deadline)

            before()
            intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? "
                "AND deleted_at IS NULL FOR UPDATE", (tenant, intake_id))
            action = "cancel" if request_kind == "ASSISTED_ENTRY" else "recheck_source_policy"
            authorize_intake_action(principal, action, resource={**intake, "submitter": intake["submitter_subject_id"]})
            current = current_native_command_withdrawal_principal(self.engine, actor, intake,
                request_kind=request_kind, before=before)
            original = self.pending.original(tenant, intake_id, request_id, request_kind, before)
            data = {"cancellation_id": event_id, "request_id": request_id, "intake_id": intake_id,
                "expected_intake_version": expected_version, "requested_by": actor,
                "original_request_sha256": canonical_sha256(original), "request_kind": request_kind}
            prior = self.pending.withdrawal(original, request_kind, before)
            if prior is not None:
                event, audit = prior
                if (event["event_id"] != event_id or any(event["payload"].get(k) != v for k, v in data.items())
                        or _object(audit["after_value"])["command_sha256"] != cancellation_command_sha256(data, reason)):
                    raise NativeAssistedEntryConflict("NATIVE_COMMAND_WITHDRAWAL_IDEMPOTENCY_CONFLICT")
                return cancellation_receipt(event), True
            if intake["version"] != expected_version:
                raise NativeAssistedEntryConflict("NATIVE_COMMAND_WITHDRAWAL_VERSION_CONFLICT")
            _, aggregate, job_type, event_type, audit_action = LANES[request_kind]
            before()
            # Workers lock physical/business before Intake. Never wait in the
            # opposite order while holding the current Intake exclusion lock.
            jobs = self.engine.query("SELECT * FROM workflow.jobs WHERE tenant_id=? AND aggregate_type=? "
                "AND aggregate_id=? AND job_type=? LIMIT 2 FOR UPDATE NOWAIT", (tenant, aggregate, request_id, job_type))
            if len(jobs) > 1:
                raise NativeAssistedEntryConflict("NATIVE_COMMAND_WITHDRAWAL_JOB_NOT_UNIQUE")
            job = jobs[0] if jobs else None
            if job:
                payload = _object(job["payload"])
                if request_kind == "ASSISTED_ENTRY":
                    NativeAssistedEntryJobProof(self.graph).admission(job, before=before)
                else:
                    proof = NativeHistoricalRecheckProof(self.source._producer_reads(before))
                    proof._admission_receipt(job, payload, before)
                    proof._origin._admitted_request(job, payload, before)
                before()
                delivery = self.queue.get(job["job_id"])
                if self.pending.settled_pair(job, delivery) or job["status"] not in {"QUEUED", "RUNNING", "RETRYING"}:
                    if self.pending.terminal_closure(original, job, request_kind, before) is None:
                        raise NativeAssistedEntryConflict("NATIVE_COMMAND_TERMINAL_CLOSURE_NOT_PROVEN")
                    raise NativeAssistedEntryConflict("NATIVE_COMMAND_ALREADY_TERMINAL")
                if job["cancellation_requested_at"] is not None:
                    raise NativeAssistedEntryConflict("NATIVE_COMMAND_CANCELLATION_WITHOUT_JOURNAL")
            before()
            CanonicalAuditBacklog(self.engine).require_available(tenant)
            at = self.source._clock()
            data.update(requested_at=at.isoformat(), job_id=job["job_id"] if job else None)
            if job:
                before()
                if self.engine.execute("UPDATE workflow.jobs SET cancellation_requested_at=?,version=version+1 "
                    "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? "
                    "AND status IN ('QUEUED','RUNNING','RETRYING') AND cancellation_requested_at IS NULL",
                    (at.isoformat(), tenant, job["job_id"], job["version"], job["fence_token"])).rowcount != 1:
                    raise NativeAssistedEntryConflict("NATIVE_COMMAND_CANCELLATION_CAS_REJECTED")
            event = DomainEvent(event_id=event_id, event_type=event_type, producer="listing_intake_service",
                tenant_id=tenant, aggregate_type=aggregate, aggregate_id=request_id, aggregate_version=2,
                partition_key=tenant + ":" + intake_id, correlation_id=intake["correlation_id"],
                actor_ref=actor, causation_id=request_id, occurred_at=at,
                schema_ref="#/payloads/IntakeCommandCancellationV1", payload=data)
            receipt = cancellation_receipt(event.to_dict())
            before()
            self.graph.audit_sink.persist({"audit_event_id": cancellation_audit_id(event_id), "tenant_id": tenant,
                "resource_type": "intake", "resource_id": intake_id, "action": audit_action, "result": "SUCCEEDED",
                "actor_subject_id": actor,
                "actor_role": "DATA_STEWARD" if request_kind == "SOURCE_POLICY_RECHECK" else human_transition_role(current).value,
                "correlation_id": intake["correlation_id"], "causation_id": request_id, "occurred_at": at.isoformat(),
                "before_value": {"request_id": request_id, "version": expected_version},
                "after_value": {**receipt, "native_command_withdrawal_provenance": data,
                    "command_sha256": cancellation_command_sha256(data, reason), "idempotency_key_sha256": key_sha},
                "reason": reason}, {k: intake[k] for k in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")})
            before()
            self.graph._outbox(event)
            before()
            return receipt, False
