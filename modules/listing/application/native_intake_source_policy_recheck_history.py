"""Read immutable recheck provenance without reviving an execution key.

Current execution/admission retains the original HMAC verification. This
reader has no sign, decode, transaction or job-control API. A recorded token
is accepted here only as bytes bound to the immutable canonical admission
AND terminal audit chain. It grants no new human, source or worker authority.
"""

from __future__ import annotations

import hashlib
import re

from modules.listing.application.native_intake_producer_reads import _native_producer_noop
from modules.listing.application.native_intake_source_policy import (
    NativeIntakeSourcePolicyOwner,
    _json,
    _object,
    _time,
    _uuid,
)
from modules.listing.domain.identity_graph import IdentityConflictError
from shared.domain.events import validate_event
from shared.jobs.queue import JobFenceRejectedError, JobStatus

NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE = "native-intake-source-policy-recheck"


class NativeIntakeSourcePolicyRecheckConflict(IdentityConflictError):
    pass


class NativeHistoricalRecheckProof:
    def __init__(self, graph):
        # Callers supply the existing bounded read-only producer view. The
        # original owner is borrowed solely for its request/root read proofs.
        from modules.listing.application.native_intake_source_policy_recheck import (
            NativeIntakeSourcePolicyRecheckOwner,
        )

        self.graph, self.engine, self.queue = graph, graph.engine, graph.queue
        self._origin = NativeIntakeSourcePolicyRecheckOwner(graph)

    @staticmethod
    def _delivery_bound(delivery, job, payload):
        generation = payload.get("delivery_generation")
        data = delivery.payload
        if (type(generation) is not int or not 1 <= generation <= 2**63 - 1
                or delivery.job_type != NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE
                or not isinstance(data, dict) or set(data) != {"job_id", "tenant_id", "attempt_token"}
                or data.get("job_id") != job["job_id"] or data.get("tenant_id") != job["tenant_id"]
                or not isinstance(data.get("attempt_token"), str)
                or not re.fullmatch(str(generation) + r"\.[0-9a-f]{64}", data["attempt_token"])):
            return False
        # _admission_receipt below MUST additionally verify the exact canonical
        # audit provenance containing this same token SHA, not just this row.
        return hashlib.sha256(data["attempt_token"].encode()).hexdigest() == payload.get("attempt_token_sha256")

    def _waiting_state(self, payload):
        state, policy = self._origin._waiting_context(payload)
        if "waiting_state" in payload and (
                self._origin._provenance(payload).get("waiting_state") != state
                or self._origin._provenance(payload).get("waiting_source_policy") != policy):
            raise JobFenceRejectedError("NATIVE_RECHECK_HISTORY_WAITING_CONTEXT_NOT_AUDITED")
        return state

    def _admission_receipt(self, job, payload, before):
        receipt = _object(payload.get("admission_receipt"))
        before()
        delivery = self.queue.get(job["job_id"])
        if (job["job_type"] != NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE
                or job["aggregate_type"] != "intake_policy_recheck" or job["aggregate_id"] != payload["request_id"]
                or job["idempotency_key"] != payload["request_id"] or payload["event_id"] != payload["request_id"]
                or delivery is None or delivery.job_type != NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE
                or delivery.correlation_id != job["correlation_id"]
                or not self._delivery_bound(delivery, job, payload)
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
                or snapshot.get("native_recheck_provenance") != self._origin._provenance(payload)):
            raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_ADMISSION_AUDIT_CONFLICT")
        return receipt

    def _committed_result(self, delivery, tenant, generation, *, before=_native_producer_noop):
        """Return the receipt and its origin producer after the full proof."""
        before()
        job = self.engine.query_one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?", (tenant, delivery.job_id))
        if job is None or job["status"] != "SUCCEEDED":
            return None
        payload, result = _object(job["payload"]), _object(job["result"])
        self._admission_receipt(job, payload, before)
        _, original = self._origin._admitted_request(job, payload, before)
        before()
        current = self.queue.get(delivery.job_id)
        keys = {"job_id", "request_id", "intake_id", "processing_state", "version", "transition_id", "audit_event_id",
                "outbox_event_id", "correlation_id", "root_identity_event_id", "input_sha256", "requested_by",
                "accepted_delivery_fence", "accepted_business_fence", "accepted_attempt", "accepted_worker", "authority"}
        if (set(result) != keys or current is None or current.status != JobStatus.SUCCEEDED or current.delivery_state is not None
                or not self._delivery_bound(delivery, job, payload) or current.payload != delivery.payload
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
        waiting_state = self._waiting_state(payload)
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
                or snapshot.get("native_recheck_provenance") != self._origin._provenance(payload)
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
        return result, original

    def source_producer(self, tenant, event_id, before):
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
        before()
        job = self.graph._one("SELECT payload FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                              (tenant, transition["job_id"]))
        payload = _object(job["payload"])
        generation = payload.get("delivery_generation")
        if type(generation) is not int or generation < 1:
            raise JobFenceRejectedError("NATIVE_RECHECK_HISTORY_GENERATION_REJECTED")
        bound_tenant = delivery.payload.get("tenant_id") if isinstance(delivery.payload, dict) else None
        verified = self._committed_result(delivery, tenant, generation, before=before)
        if bound_tenant != tenant or verified is None:
            raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_PRODUCER_PROOF_CONFLICT")
        result, original = verified
        if (result["outbox_event_id"] != event_id
                or result["intake_id"] != row["aggregate_id"]):
            raise NativeIntakeSourcePolicyRecheckConflict("NATIVE_RECHECK_PRODUCER_PROOF_CONFLICT")
        # This invocation already authenticated the exact origin as part of
        # the full receipt proof. Keep the final budget boundary without
        # repeating that same historical read or borrowing present authority.
        before()
        waiting = ({k: payload[k] for k in ("waiting_state", "waiting_source_policy")}
                   if "waiting_state" in payload else {})
        return row, event, hashlib.sha256(_json(event).encode()).hexdigest(), {
            **original, "recheck": True, "root_identity_event_id": result["root_identity_event_id"],
            "recheck_provenance": {"request_id": result["request_id"], "job_id": result["job_id"],
                "outbox_event_id": event_id, "root_identity_event_id": result["root_identity_event_id"],
                "input_sha256": result["input_sha256"], **waiting}}

class _HistoricalSourcePolicyOwner(NativeIntakeSourcePolicyOwner):
    """Reuse the complete source receipt verifier over a read-only graph.

    Only the recheck producer read changes: its historic signature bytes are
    proved by canonical admission/terminal audits, rather than today's key.
    All original source execution classes and their HMAC checks are unchanged.
    """

    def _recheck_producer(self, view, tenant, event_id, before):
        return NativeHistoricalRecheckProof(view).source_producer(tenant, event_id, before)

    def _event(self, tenant, event_id, *, before=None):
        before = before if before is not None else _native_producer_noop
        proven = super()._event(tenant, event_id, before=before)
        # Capture the actual verified return value, never a substitute from
        # caller-supplied payload metadata. This is not a read cache: _event
        # always executes its complete proof before assigning this value.
        self._verified_producer = (tenant, event_id, proven)
        return proven


class HistoricalSourcePolicyProof:
    """Expose only the two historical read methods over a bounded read view."""

    def __init__(self, graph):
        self.graph, self.engine, self.queue = graph, graph.engine, graph.queue
        self._reader = _HistoricalSourcePolicyOwner(graph)

    def _committed_result(self, delivery, tenant, generation):
        return self._reader._committed_result(delivery, tenant, generation)

    def committed_with_producer(self, delivery, tenant, generation, *, producer_event_id):
        """Return the producer just verified inside this full receipt proof.

        A fresh reader per invocation prevents reuse across calls or SQL
        roots. Its supplied graph remains the caller's budgeted read-only
        view, including every nested SQL and queue read. No current human,
        source, execution key or lease authority is cached here.
        """
        reader = _HistoricalSourcePolicyOwner(self.graph)
        reader._verified_producer = None
        result = reader._committed_result(delivery, tenant, generation)
        if result is None:
            return None, None
        captured = reader._verified_producer
        if captured is None or captured[:2] != (tenant, producer_event_id):
            raise JobFenceRejectedError("NATIVE_SOURCE_HISTORY_PRODUCER_NOT_VERIFIED")
        return result, captured[2]

    def _event(self, tenant, event_id, *, before):
        return self._reader._event(tenant, event_id, before=before)
