"""Commit a frozen human proposal request; never perform IO or advance Intake."""

from __future__ import annotations

import hashlib
import re
from datetime import timedelta
from uuid import UUID, uuid5

from modules.listing.application.intake_authorization import authorize_intake_action
from modules.listing.application.native_intake_assisted_entry_input import (
    IDENTITY_FIELDS,
    NativeAssistedEntryCommand,
)
from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryAuthenticator,
    NativeAssistedEntryConflict,
    native_intake_assisted_entry_policy,
)
from modules.listing.application.native_intake_assisted_entry_request_proof import (
    REQUEST_AGGREGATE,
    REQUEST_EVENT,
    REQUEST_SCHEMA,
    NativeAssistedEntryRequestProof,
    request_audit_id,
    request_receipt,
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
from modules.listing.application.native_intake_retrieval_policy import canonical_json
from modules.listing.domain.intake_states import (
    Actor,
    IntakeAggregate,
    IntakeStage,
    IntakeStateMachine,
    PrincipalRole,
    TransitionContext,
)
from shared.audit.backlog import CanonicalAuditBacklog
from shared.auth import Role
from shared.domain.events import DomainEvent

_KEY = re.compile(r"[A-Za-z0-9._:-]{16,128}\Z")


def human_transition_role(principal):
    if principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE):
        return PrincipalRole.EXPANSION_MANAGER
    if principal.has_role(Role.EXPANSION_USER):
        return PrincipalRole.EXPANSION_STAFF
    if principal.has_role(Role.DATA_OWNER):
        return PrincipalRole.DATA_STEWARD
    raise PermissionError("NATIVE_ASSISTED_HUMAN_TRANSITION_ROLE_REQUIRED")


class NativeAssistedEntryRequestOwner:
    def __init__(self, graph):
        self.graph, self.engine = graph, graph.engine
        self.source = NativeAssistedEntrySourceProof(graph)
        self.requests = NativeAssistedEntryRequestProof(graph)
        self.pending = NativeIntakePendingCommands(graph)

    def context(self, principal, intake_id, command, before):
        before()
        intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? "
            "AND deleted_at IS NULL FOR UPDATE", (principal.tenant_id, native_intake_uuid(intake_id)))
        authorize_intake_action(principal, "correct", resource={**intake, "submitter": intake["submitter_subject_id"]},
            is_identity_affecting=True, risk_summary=command.risk_summary, risk_acknowledged=command.risk_acknowledged)
        current = current_native_assisted_principal(self.engine, principal.subject_id, intake,
            risk_summary=command.risk_summary, risk_acknowledged=command.risk_acknowledged, before=before)
        human_transition_role(current)
        return intake, current

    def request(self, principal, intake_id, *, expected_version, command, idempotency_key):
        if (type(expected_version) is not int or expected_version < 1
                or not isinstance(command, NativeAssistedEntryCommand)
                or not isinstance(idempotency_key, str) or not _KEY.fullmatch(idempotency_key)):
            raise ValueError("NATIVE_ASSISTED_EXPLICIT_COMMAND_REQUIRED")
        tenant, actor = native_intake_uuid(principal.tenant_id), native_intake_uuid(principal.subject_id)
        request_id = str(uuid5(UUID(native_intake_uuid(intake_id)),
            f"native-assisted-entry-request-v1:{tenant}:{actor}:{idempotency_key}"))
        with self.graph.transaction(tenant):
            before = NativeIntakeCommandBudget(self.source.source)
            intake, current = self.context(principal, intake_id, command, before)
            policy = native_intake_assisted_entry_policy(tenant)
            # Validate availability without enqueueing a job or retaining an
            # execution token. Negative withdrawal never calls this path.
            NativeAssistedEntryAuthenticator.sign(request_id, tenant, 1)
            _, classified = self.source.current_manual_source(intake, before=before)
            before()
            prior = self.engine.query_one("SELECT event_id FROM workflow.outbox_events WHERE tenant_id=? AND event_id=?",
                                           (tenant, request_id))
            if prior:
                proof = self.requests.read(tenant, intake_id, request_id, before=before)
                self.pending.require_not_withdrawn(proof.event, "ASSISTED_ENTRY", before)
                data = proof.event["payload"]
                if (data["command_sha256"] != command.sha256 or data["expected_intake_version"] != expected_version
                        or data["requested_by"] != actor or data["input_sha256"] != self.source.source._input_digest(intake)):
                    raise NativeAssistedEntryConflict("NATIVE_ASSISTED_IDEMPOTENCY_CONFLICT")
                before()
                if self.source.source._clock() >= classified.valid_until:
                    raise NativeAssistedEntryConflict("NATIVE_ASSISTED_CURRENT_APPROVAL_EXPIRED")
                return proof.receipt, True
            if intake["version"] != expected_version:
                raise NativeAssistedEntryConflict("NATIVE_ASSISTED_INTAKE_VERSION_CONFLICT")
            parent = self.source.current_parent(intake, before=before)
            # The aggregate owner is always the immutable submitter, even
            # when a permitted manager/steward submits this human command.
            aggregate = IntakeAggregate(id=intake_id, tenant_id=tenant, stage=IntakeStage(intake["processing_state"]),
                version=intake["version"], created_by=intake["submitter_subject_id"], source_id=intake["source_id"],
                owner_id=native_intake_uuid(intake["submitter_subject_id"]))
            IntakeStateMachine.transition(aggregate, IntakeStage.PARSING, TransitionContext(
                actor=Actor(actor, human_transition_role(current), tenant), idempotency_key=idempotency_key,
                version_before=expected_version, reason=command.reason, risk_acknowledged=True,
                correlation_id=intake["correlation_id"], causation_id=parent.event_id))
            self.pending.require_clear(intake, before=before)
            before()
            CanonicalAuditBacklog(self.engine).require_available(tenant)
            at = self.source.source._clock()
            values = command.fields.values()
            corrections = {name: str(uuid5(UUID(request_id), "initial-proposal-v1:" + name)) for name in sorted(values)}
            data = {"request_id": request_id, "intake_id": intake_id, "expected_intake_version": expected_version,
                "requested_by": actor, "requested_at": at.isoformat(), "input_sha256": parent.input_sha256,
                "fields_sha256": command.fields.sha256, "command_sha256": command.sha256,
                "correction_ids": sorted(corrections.values()), "source_policy_event_id": parent.event_id,
                "source_policy_event_sha256": parent.event_sha256, "root_identity_event_id": parent.root_identity_event_id,
                "policy_sha256": policy.sha256, "timeout_at": (at + timedelta(seconds=policy.deadline_seconds)).isoformat()}
            event = DomainEvent(event_id=request_id, event_type=REQUEST_EVENT, producer="listing_intake_service",
                tenant_id=tenant, aggregate_type=REQUEST_AGGREGATE, aggregate_id=request_id, aggregate_version=1,
                partition_key=tenant + ":" + intake_id, correlation_id=intake["correlation_id"], actor_ref=actor,
                causation_id=parent.event_id, occurred_at=at, schema_ref=REQUEST_SCHEMA, payload=data)
            for name, value in values.items():
                before()
                encoded = canonical_json(value)
                self.engine.execute("INSERT INTO intake.human_corrections (correction_id,tenant_id,intake_id,field_path,"
                    "field_classification,corrected_value,after_effective_value,reason,proposed_by,status,identity_affecting,"
                    "version,created_at) VALUES (?,?,?,?,'INTERNAL',?::jsonb,?::jsonb,?,?,'PROPOSED',?,1,?)",
                    (corrections[name], tenant, intake_id, name, encoded, encoded, command.correction_reason(), actor,
                     name in IDENTITY_FIELDS, at.isoformat()))
            receipt = request_receipt(event.to_dict())
            before()
            self.graph.audit_sink.persist({"audit_event_id": request_audit_id(request_id), "tenant_id": tenant,
                "resource_type": "intake", "resource_id": intake_id, "action": "intake.assisted_entry_requested.v1",
                "result": "SUCCEEDED", "actor_subject_id": actor, "actor_role": human_transition_role(current).value,
                "correlation_id": intake["correlation_id"], "causation_id": parent.event_id, "occurred_at": at.isoformat(),
                "before_value": {"processing_state": intake["processing_state"], "version": expected_version},
                "after_value": {**receipt, "native_assisted_request_provenance": data}, "reason": command.reason},
                {k: intake[k] for k in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")})
            before()
            self.graph._outbox(event)
            before()
            if (self.source.source._clock() >= classified.valid_until
                    or native_intake_assisted_entry_policy(tenant).sha256 != policy.sha256):
                raise NativeAssistedEntryConflict("NATIVE_ASSISTED_APPROVAL_OR_CONFIGURATION_CHANGED")
            return receipt, False


def idempotency_key_sha256(value):
    if not isinstance(value, str) or not _KEY.fullmatch(value):
        raise ValueError("NATIVE_COMMAND_IDEMPOTENCY_KEY_REQUIRED")
    return hashlib.sha256(value.encode()).hexdigest()
