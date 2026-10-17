"""Prove manual command origin independently of current source permission.

No source HTTP, storage IO or SQL-root ownership lives here. A historical
source decision authenticates the command only; each positive operation also
needs fresh current-human authority, current source approval and owner fences.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryConflict,
)
from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_retrieval_policy import canonical_sha256
from modules.listing.application.native_intake_source_policy import NativeIntakeSourcePolicyOwner
from modules.listing.application.native_intake_source_policy_recheck_history import (
    HistoricalSourcePolicyProof,
)
from modules.listing.application.native_intake_source_policy_rules import (
    evaluate_native_source_policy,
)
from modules.listing.application.native_intake_url_identity import normalize_native_intake_url
from shared.domain.events import validate_event


def _object(value):
    try:
        result = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        raise NativeAssistedEntryConflict("NATIVE_ASSISTED_SOURCE_OBJECT_REJECTED") from None
    if not isinstance(result, dict):
        raise NativeAssistedEntryConflict("NATIVE_ASSISTED_SOURCE_OBJECT_REJECTED")
    return result


@dataclass(frozen=True)
class NativeAssistedSourceParent:
    tenant_id: str
    event_id: str
    event_sha256: str
    intake_id: str
    intake_version: int
    input_sha256: str
    source_id: str
    effective_policy: str
    root_identity_event_id: str
    correlation_id: str


class NativeAssistedEntrySourceProof:
    def __init__(self, graph):
        self.graph, self.engine = graph, graph.engine
        self.source = NativeIntakeSourcePolicyOwner(graph)

    def parent(self, tenant, event_id, before):
        native_intake_uuid(tenant)
        native_intake_uuid(event_id)
        # The bounded read-only view covers recursive original identity OR
        # bounded recheck proof, including every SQL/queue read it performs.
        parent = HistoricalSourcePolicyProof(self.source._producer_reads(before))
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
                or event["payload"].get("to_state") != "AWAITING_ASSISTED_ENTRY"
                or event["payload"].get("version") != event["aggregate_version"]):
            raise NativeAssistedEntryConflict("NATIVE_ASSISTED_SOURCE_PARENT_EVENT_REJECTED")
        transition = parent.graph._one("SELECT job_id FROM intake.intake_stage_transitions "
            "WHERE tenant_id=? AND transition_id=? AND intake_id=?",
            (tenant, event["payload"]["transition_id"], row["aggregate_id"]))
        business = parent.graph._one("SELECT payload FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                                     (tenant, transition["job_id"]))
        delivery = parent.queue.get(transition["job_id"])
        payload = _object(business["payload"])
        proof, produced = parent.committed_with_producer(delivery, tenant, payload.get("delivery_generation"),
            producer_event_id=payload["event_id"]) if delivery else (None, None)
        if (proof is None or proof["outbox_event_id"] != event_id or proof["processing_state"] != "AWAITING_ASSISTED_ENTRY"
                or proof["intake_id"] != row["aggregate_id"] or proof["version"] != row["aggregate_version"]
                or proof["policy_decision"]["effective_policy"] not in {"AUTH_REQUIRED", "ASSISTED_ENTRY_ONLY"}):
            raise NativeAssistedEntryConflict("NATIVE_ASSISTED_SOURCE_PARENT_PROOF_MISSING")
        _, _, _, producer = produced
        if (("recheck_provenance" in payload) != (producer.get("recheck") is True)
                or (producer.get("recheck") is True and payload["recheck_provenance"] != producer["recheck_provenance"])):
            raise NativeAssistedEntryConflict("NATIVE_ASSISTED_SOURCE_RECHECK_BINDING_REJECTED")
        root = producer.get("root_identity_event_id", payload["event_id"])
        native_intake_uuid(root)
        return NativeAssistedSourceParent(tenant, event_id, canonical_sha256(event), row["aggregate_id"],
            row["aggregate_version"], payload["input_sha256"], proof["policy_decision"]["source_id"],
            proof["policy_decision"]["effective_policy"], root, row["correlation_id"])

    def current_parent(self, intake, *, before) -> NativeAssistedSourceParent:
        before()
        rows = self.engine.query("SELECT event_id FROM workflow.outbox_events "
            "WHERE tenant_id=? AND event_type='intake.state_changed' AND aggregate_type='intake' "
            "AND aggregate_id=? AND aggregate_version=? "
            "AND payload->'payload'->>'to_state'='AWAITING_ASSISTED_ENTRY' LIMIT 2",
            (intake["tenant_id"], intake["intake_id"], intake["version"]))
        if len(rows) != 1:
            raise NativeAssistedEntryConflict("NATIVE_ASSISTED_SOURCE_PARENT_NOT_UNIQUE")
        proof = self.parent(intake["tenant_id"], rows[0]["event_id"], before)
        self.bind_waiting(intake, proof)
        return proof

    def bind_waiting(self, intake, parent: NativeAssistedSourceParent):
        if (intake["processing_state"] != "AWAITING_ASSISTED_ENTRY"
                or intake["intake_method"] != "URL" or intake["version"] != parent.intake_version
                or intake["tenant_id"] != parent.tenant_id or intake["intake_id"] != parent.intake_id
                or intake["correlation_id"] != parent.correlation_id
                or intake["source_id"] != parent.source_id or intake["source_policy_state"] != parent.effective_policy
                or self.source._input_digest(intake) != parent.input_sha256
                or any(intake[k] is not None for k in ("deleted_at", "resolved_listing_id", "completed_at"))):
            raise NativeAssistedEntryConflict("NATIVE_ASSISTED_CURRENT_INTAKE_BINDING_REJECTED")

    def current_manual_source(self, intake, *, before):
        # This method does not infer permission from the historical waiting
        # label, and must never be required for negative request withdrawal.
        try:
            identity = normalize_native_intake_url(intake["original_url"])
        except (ValueError, TypeError):
            raise NativeAssistedEntryConflict("NATIVE_ASSISTED_CURRENT_INPUT_REJECTED") from None
        if (intake["intake_method"] != "URL" or intake["canonical_url"] != identity.canonical_url
                or intake["canonical_url_sha256"] != identity.canonical_url_sha256):
            raise NativeAssistedEntryConflict("NATIVE_ASSISTED_CURRENT_INPUT_REJECTED")
        before()
        matches = self.engine.query("SELECT source_id FROM intake.source_registry s WHERE EXISTS "
            "(SELECT 1 FROM unnest(s.allowed_hosts) AS h(host) WHERE lower(h.host)=?) "
            "ORDER BY source_id LIMIT 2 FOR SHARE", (identity.source_host,))
        before()
        source = self.graph._one("SELECT * FROM intake.source_registry WHERE source_id=? FOR SHARE", (intake["source_id"],))
        before()
        classified = evaluate_native_source_policy(source, source_host=identity.source_host,
            expected_source_id=intake["source_id"], host_match_count=len(matches), now=self.source._clock())
        if (classified.target_state != "AWAITING_ASSISTED_ENTRY"
                or classified.effective_policy not in {"AUTH_REQUIRED", "ASSISTED_ENTRY_ONLY"}
                or classified.valid_until is None):
            raise NativeAssistedEntryConflict("NATIVE_ASSISTED_CURRENT_MANUAL_APPROVAL_REQUIRED")
        before()
        return source, classified
