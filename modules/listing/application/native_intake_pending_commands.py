"""Shared exclusion and authenticated withdrawal journal for native commands.

Callers own the same locked Intake and bounded SERIALIZABLE transaction.
A cancellation event alone never hides a pending request or an active job.
No current source/key/account grant is inferred from these historical reads.
"""

from __future__ import annotations

import re
import time
from datetime import timedelta
from uuid import UUID, uuid5

from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryConflict,
)
from modules.listing.application.native_intake_assisted_entry_request_proof import (
    NativeAssistedEntryRequestProof,
    _object,
    _time,
)
from modules.listing.application.native_intake_retrieval import audited_value_matches
from modules.listing.application.native_intake_retrieval_policy import canonical_sha256
from shared.domain.events import validate_event

LANES = {
    "ASSISTED_ENTRY": ("intake.assisted_entry_requested", "intake_assisted_entry", "native-intake-assisted-entry",
        "intake.assisted_entry_request_cancelled", "intake.assisted_entry_request_cancelled.v1"),
    "SOURCE_POLICY_RECHECK": ("intake.source_policy_recheck_requested", "intake_policy_recheck",
        "native-intake-source-policy-recheck", "intake.source_policy_recheck_cancelled",
        "intake.source_policy_recheck_request_cancelled.v1"),
}
_HUMAN_ROLES = {"EXPANSION_STAFF", "EXPANSION_MANAGER", "DATA_STEWARD"}
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_TERMINAL = {("SUCCEEDED", "succeeded", None), ("FAILED", "failed", "dead_letter"),
    ("DEAD_LETTER", "failed", "dead_letter"), ("CANCELLED", "cancelled", None)}
PROOF_BATCH_SIZE = 64
_WITHDRAWAL_ROWS = ("SELECT * FROM workflow.outbox_events WHERE tenant_id=? "
    "AND aggregate_type=? AND aggregate_id=? AND event_type=? LIMIT 2")
_WITHDRAWAL_AUDIT = ("SELECT after_value,reason,actor_role FROM audit.audit_events "
    "WHERE tenant_id=? AND audit_event_id=? AND resource_type='intake' AND resource_id=? "
    "AND actor_subject_id=? AND service_principal IS NULL AND action=? AND result='SUCCEEDED' "
    "AND causation_id=? AND correlation_id=?")


class NativeIntakeCommandBudget:
    """One 750 ms root budget, including its in-memory proof checks.

    SQL boundaries still use the current database clock and actual statement
    and lock timeouts. Checking a closed batch row needs no SQL of its own;
    its monotonic check shares this same original deadline. Neither clock
    grants source, human, job or historical-closure authority.
    """

    def __init__(self, source):
        self.source = source
        self._monotonic_deadline = time.monotonic() + 0.750
        observed = source._clock()
        remaining = self._monotonic_deadline - time.monotonic()
        self.check_in_memory()
        # Counting the clock query's elapsed time makes the SQL deadline
        # conservative. A later callback never starts another 750 ms period.
        self.deadline = observed + timedelta(seconds=remaining)

    def check_in_memory(self):
        if time.monotonic() >= self._monotonic_deadline:
            raise NativeAssistedEntryConflict("NATIVE_COMMAND_ROOT_DEADLINE_EXPIRED")

    def __call__(self):
        self.check_in_memory()
        self.source._admission_budget(self.deadline)
        self.check_in_memory()


class _WithdrawalBatch:
    """The original verifier's two exact reads over the caller's SQL snapshot.

    No origin/grant proof is cached, and no query can fall through to SQL.
    """

    def __init__(self, cancellations, audits):
        self.engine = self
        self.cancellations = {}
        self.audits = {}
        for row in cancellations:
            key = tuple(row[k] for k in ("tenant_id", "aggregate_type", "aggregate_id", "event_type"))
            self.cancellations.setdefault(key, []).append(row)
        for row in audits:
            key = row["tenant_id"], row["audit_event_id"]
            if key in self.audits:
                raise NativeAssistedEntryConflict("NATIVE_COMMAND_WITHDRAWAL_AUDIT_NOT_UNIQUE")
            self.audits[key] = row

    def query(self, statement, parameters):
        if statement != _WITHDRAWAL_ROWS:
            raise RuntimeError("NATIVE_COMMAND_BATCH_QUERY_NOT_DECLARED")
        return self.cancellations.get(tuple(parameters), [])[:2]

    def query_one(self, statement, parameters):
        if statement != _WITHDRAWAL_AUDIT:
            raise RuntimeError("NATIVE_COMMAND_BATCH_QUERY_NOT_DECLARED")
        tenant, audit_id, intake, actor, action, causation, correlation = parameters
        row = self.audits.get((tenant, audit_id))
        if row is None:
            return None
        expected = {"resource_type": "intake", "resource_id": intake, "actor_subject_id": actor,
            "service_principal": None, "action": action, "result": "SUCCEEDED",
            "causation_id": causation, "correlation_id": correlation}
        if any(row.get(k) != v for k, v in expected.items()):
            return None
        return row


def cancellation_id(request_id, tenant, actor, kind, key_sha256):
    return str(uuid5(UUID(request_id), f"native-command-withdrawal-v1:{tenant}:{actor}:{kind}:{key_sha256}"))


def cancellation_audit_id(event_id):
    return str(uuid5(UUID(event_id), "native-command-withdrawal-audit-v1"))


def cancellation_command_sha256(data, reason):
    return canonical_sha256({"request_id": data["request_id"], "intake_id": data["intake_id"],
        "expected_intake_version": data["expected_intake_version"], "request_kind": data["request_kind"],
        "original_request_sha256": data["original_request_sha256"], "reason": reason})


def cancellation_receipt(event):
    data = event["payload"]
    return {"cancellation_id": event["event_id"], "request_id": data["request_id"],
        "intake_id": data["intake_id"], "status": "WITHDRAWN" if data["job_id"] is None else "CANCELLATION_REQUESTED",
        "version": data["expected_intake_version"], "requested_at": data["requested_at"],
        "requested_by": data["requested_by"], "job_id": data["job_id"], "correlation_id": event["correlation_id"],
        "audit_event_id": cancellation_audit_id(event["event_id"]), "outbox_event_id": event["event_id"]}


class NativeIntakePendingCommands:
    def __init__(self, graph):
        self.graph, self.engine = graph, graph.engine

    def original(self, tenant, intake_id, request_id, kind, before):
        if kind == "ASSISTED_ENTRY":
            event, _ = NativeAssistedEntryRequestProof(self.graph).envelope(tenant, intake_id, request_id, before=before)
        elif kind == "SOURCE_POLICY_RECHECK":
            from modules.listing.application.native_intake_source_policy_recheck import (
                NativeIntakeSourcePolicyRecheckOwner,
            )

            _, event, _, _ = NativeIntakeSourcePolicyRecheckOwner(self.graph)._request_event(tenant, request_id, before)
        else:
            raise ValueError("NATIVE_COMMAND_KIND_REQUIRED")
        if event["payload"]["intake_id"] != intake_id:
            raise NativeAssistedEntryConflict("NATIVE_COMMAND_FOREIGN_REQUEST")
        return event

    def withdrawal(self, original, kind, before):
        """Require the exact negative event AND its canonical audit, or none.

        This does not settle an admitted job. The caller must continue to
        exclude that job until both its actual rows reach a terminal pair.
        """
        _, aggregate, _, event_type, action = LANES[kind]
        tenant, intake = original["tenant_id"], original["payload"]["intake_id"]
        request_id = original["event_id"]
        before()
        rows = self.engine.query(_WITHDRAWAL_ROWS,
            (tenant, aggregate, request_id, event_type))
        if not rows:
            return None
        if len(rows) != 1:
            raise NativeAssistedEntryConflict("NATIVE_COMMAND_WITHDRAWAL_NOT_UNIQUE")
        row = rows[0]
        event = _object(row["payload"])
        data = _object(event.get("payload"))
        if (validate_event(event) or event.get("event_type") != event_type or row["event_type"] != event_type
                or type(row["event_version"]) is not int or row["event_version"] != 1
                or type(event.get("event_version")) is not int or event["event_version"] != 1
                or event.get("event_id") != row["event_id"] or data.get("cancellation_id") != row["event_id"]
                or event.get("tenant_id") != tenant or event.get("producer") != "listing_intake_service"
                or event.get("schema_ref") != "#/payloads/IntakeCommandCancellationV1"
                or event.get("aggregate_type") != aggregate or row["aggregate_type"] != aggregate
                or event.get("aggregate_id") != request_id or row["aggregate_id"] != request_id
                or type(row["aggregate_version"]) is not int or row["aggregate_version"] != 2
                or type(event.get("aggregate_version")) is not int or event["aggregate_version"] != 2
                or row["partition_key"] != tenant + ":" + intake or event.get("partition_key") != row["partition_key"]
                or row["correlation_id"] != original["correlation_id"] or event.get("correlation_id") != row["correlation_id"]
                or event.get("causation_id") != request_id or data.get("request_id") != request_id
                or data.get("intake_id") != intake or data.get("request_kind") != kind
                or data.get("original_request_sha256") != canonical_sha256(original)
                or event.get("actor_ref") != data.get("requested_by")
                or _time(row["occurred_at"]) != _time(event.get("occurred_at"))
                or _time(row["occurred_at"]) != _time(data.get("requested_at"))
                or _time(row["retention_until"]) != _time(row["occurred_at"]) + timedelta(days=30)):
            raise NativeAssistedEntryConflict("NATIVE_COMMAND_WITHDRAWAL_EVENT_CONFLICT")
        before()
        audit = self.engine.query_one(_WITHDRAWAL_AUDIT,
            (tenant, cancellation_audit_id(event["event_id"]), intake, data["requested_by"], action,
             request_id, event["correlation_id"]))
        if audit is None or audit["actor_role"] not in ({"DATA_STEWARD"} if kind == "SOURCE_POLICY_RECHECK" else _HUMAN_ROLES):
            raise NativeAssistedEntryConflict("NATIVE_COMMAND_WITHDRAWAL_AUDIT_MISSING")
        snapshot = _object(audit["after_value"])
        key_sha = snapshot.get("idempotency_key_sha256")
        if (not isinstance(key_sha, str) or not _SHA.fullmatch(key_sha)
                or cancellation_id(request_id, tenant, data["requested_by"], kind, key_sha) != event["event_id"]
                or not audited_value_matches(snapshot, {**cancellation_receipt(event),
                    "native_command_withdrawal_provenance": data,
                    "command_sha256": cancellation_command_sha256(data, audit["reason"]),
                    "idempotency_key_sha256": key_sha})):
            raise NativeAssistedEntryConflict("NATIVE_COMMAND_WITHDRAWAL_AUDIT_CONFLICT")
        before()
        return event, audit

    def require_not_withdrawn(self, original, kind, before):
        if self.withdrawal(original, kind, before) is not None:
            raise NativeAssistedEntryConflict("NATIVE_COMMAND_ALREADY_WITHDRAWN")

    @staticmethod
    def closed_candidate_envelope(row, intake, kind):
        """Bind the actual request bytes before checking its negative journal.

        Withdrawal authenticated the full origin before committing its audit.
        Rechecking a closure needs that exact original's hash and row/envelope
        binding, not a second positive source-history traversal. This method
        grants no request/admission authority and never replaces their proofs.
        """
        event = _object(row["payload"])
        data = _object(event.get("payload"))
        request_type, aggregate, _, _, _ = LANES[kind]
        schema = ("#/payloads/IntakeAssistedEntryRequestedV1" if kind == "ASSISTED_ENTRY"
                  else "#/payloads/IntakeSourcePolicyRecheckRequestedV1")
        if (validate_event(event) or event.get("event_type") != request_type or row["event_type"] != request_type
                or event.get("event_id") != row["event_id"] or data.get("request_id") != row["event_id"]
                or event.get("tenant_id") != intake["tenant_id"] or row["tenant_id"] != intake["tenant_id"]
                or event.get("aggregate_type") != aggregate or row["aggregate_type"] != aggregate
                or event.get("aggregate_id") != row["event_id"] or row["aggregate_id"] != row["event_id"]
                or type(event.get("event_version")) is not int or event["event_version"] != 1
                or type(row["event_version"]) is not int or row["event_version"] != 1
                or type(event.get("aggregate_version")) is not int or event["aggregate_version"] != 1
                or type(row["aggregate_version"]) is not int or row["aggregate_version"] != 1
                or event.get("producer") != "listing_intake_service" or event.get("schema_ref") != schema
                or data.get("intake_id") != intake["intake_id"] or event.get("actor_ref") != data.get("requested_by")
                or event.get("partition_key") != intake["tenant_id"] + ":" + intake["intake_id"]
                or row["partition_key"] != event.get("partition_key")
                or event.get("correlation_id") != intake["correlation_id"] or row["correlation_id"] != event.get("correlation_id")
                or _time(event.get("occurred_at")) != _time(row["occurred_at"])
                or _time(data.get("requested_at")) != _time(row["occurred_at"])
                or _time(row["retention_until"]) != _time(row["occurred_at"]) + timedelta(days=30)):
            raise NativeAssistedEntryConflict("NATIVE_COMMAND_CLOSURE_ORIGINAL_BINDING_REJECTED")
        return event

    def _withdrawal_batch(self, tenant, rows, before):
        if not rows:
            return NativeIntakePendingCommands(_WithdrawalBatch([], []))
        before()
        cancellations = self.engine.query("SELECT * FROM workflow.outbox_events WHERE tenant_id=? "
            "AND aggregate_type=ANY(?::text[]) AND aggregate_id=ANY(?::uuid[]) "
            "AND event_type=ANY(?::text[]) LIMIT ?", (tenant,
                [v[1] for v in LANES.values()], [r["event_id"] for r in rows],
                [v[3] for v in LANES.values()], 2 * len(rows) + 1))
        if len(cancellations) > 2 * len(rows):
            raise NativeAssistedEntryConflict("NATIVE_COMMAND_WITHDRAWAL_NOT_UNIQUE")
        ids = [cancellation_audit_id(r["event_id"]) for r in cancellations]
        before()
        audits = self.engine.query("SELECT * FROM audit.audit_events WHERE tenant_id=? "
            "AND audit_event_id=ANY(?::uuid[])", (tenant, ids)) if ids else []
        return NativeIntakePendingCommands(_WithdrawalBatch(cancellations, audits))

    def terminal_closure(self, original, job, kind, before):
        if kind == "SOURCE_POLICY_RECHECK":
            from modules.listing.application.native_intake_recheck_terminal_proof import (
                NativeRecheckTerminalProof,
            )

            return NativeRecheckTerminalProof(self.graph).read(original, job, before=before)
        from modules.listing.application.native_intake_assisted_entry_terminal import (
            NativeAssistedEntryTerminal,
        )

        return NativeAssistedEntryTerminal(self.graph).read(original, job, before=before)

    def require_clear(self, intake, *, before, own_request_id=None):
        """Prove both lanes clear, in bounded batches, at ANY Intake version.

        There is no historical request count limit. Each batch reads the two
        withdrawal evidence families once, then runs the SAME Python verifier
        over those actual rows. Only those in-memory checks use a no-op read
        callback; the outer loop checks the caller's same root deadline for
        every row. A NativeIntakeCommandBudget avoids SQL for that pure check;
        legacy callable budgets retain their original per-row callback.
        Admitted terminal history still needs full proofs and may exhaust the
        fixed root budget; this does not claim unbounded terminal liveness.
        """
        tenant, intake_id = intake["tenant_id"], intake["intake_id"]
        check_in_memory = getattr(before, "check_in_memory", before)
        cursor_time, cursor_id = None, None
        while True:
            before()
            rows = self.engine.query("WITH candidates AS ("
                "SELECT e.event_id::text AS request_id,e.occurred_at FROM workflow.outbox_events e WHERE e.tenant_id=? "
                "AND ((e.event_type='intake.assisted_entry_requested' AND e.aggregate_type='intake_assisted_entry') "
                "OR (e.event_type='intake.source_policy_recheck_requested' AND e.aggregate_type='intake_policy_recheck')) "
                "AND (e.payload->'payload'->>'intake_id'=? OR e.partition_key=?) UNION "
                "SELECT COALESCE(a.after_value->>'request_id','invalid-audit:'||a.audit_event_id::text),a.occurred_at "
                "FROM audit.audit_events a WHERE a.tenant_id=? AND a.resource_type='intake' AND a.resource_id=? "
                "AND a.action IN ('intake.assisted_entry_requested.v1','intake.source_policy_recheck_requested.v1') "
                "AND a.result='SUCCEEDED'), identities AS ("
                "SELECT request_id,min(occurred_at) AS occurred_at FROM candidates GROUP BY request_id) "
                "SELECT e.*,c.request_id AS journal_request_id,c.occurred_at AS journal_occurred_at FROM identities c "
                "LEFT JOIN workflow.outbox_events e ON e.tenant_id=? AND e.event_id::text=c.request_id "
                "WHERE (?::text IS NULL OR c.request_id<>?::text) "
                "AND (?::timestamptz IS NULL OR (c.occurred_at,c.request_id)>(?::timestamptz,?::text)) "
                "ORDER BY c.occurred_at,c.request_id LIMIT ?",
                (tenant, intake_id, tenant + ':' + intake_id, tenant, intake_id, tenant,
                 own_request_id, own_request_id, cursor_time, cursor_time, cursor_id, PROOF_BATCH_SIZE))
            if not rows:
                break
            if any(r["event_id"] is None or r["event_id"] != r["journal_request_id"] for r in rows):
                raise NativeAssistedEntryConflict("NATIVE_COMMAND_REQUEST_JOURNAL_INCOMPLETE")
            before()
            jobs = self.engine.query("SELECT * FROM workflow.jobs WHERE tenant_id=? "
                "AND aggregate_type=ANY(?::text[]) AND aggregate_id=ANY(?::uuid[]) LIMIT ?",
                (tenant, [v[1] for v in LANES.values()], [r["event_id"] for r in rows], 2 * len(rows) + 1))
            if len(jobs) > 2 * len(rows):
                raise NativeAssistedEntryConflict("NATIVE_COMMAND_JOB_NOT_UNIQUE")
            by_request = {}
            for job in jobs:
                by_request.setdefault(job["aggregate_id"], []).append(job)
            expected_ids = [str(uuid5(UUID(r["event_id"]), LANES[
                "ASSISTED_ENTRY" if r["event_type"] == LANES["ASSISTED_ENTRY"][0] else "SOURCE_POLICY_RECHECK"][2])) for r in rows]
            before()
            physical_ids = {r["job_id"] for r in self.engine.query("SELECT job_id FROM durable_jobs "
                "WHERE job_id=ANY(?::text[])", (expected_ids,))}
            jobless = [r for r in rows if r["event_id"] not in by_request]
            evidence = self._withdrawal_batch(tenant, jobless, before)
            for row, expected_id in zip(rows, expected_ids, strict=True):
                check_in_memory()
                kind = "ASSISTED_ENTRY" if row["event_type"] == LANES["ASSISTED_ENTRY"][0] else "SOURCE_POLICY_RECHECK"
                original = self.closed_candidate_envelope(row, intake, kind)
                candidates = by_request.get(row["event_id"], [])
                if len(candidates) > 1:
                    raise NativeAssistedEntryConflict("NATIVE_COMMAND_JOB_NOT_UNIQUE")
                if candidates:
                    job = candidates[0]
                    if job["status"] in {"QUEUED", "RUNNING", "RETRYING"}:
                        raise NativeAssistedEntryConflict("NATIVE_COMMAND_COMPETING_ACTIVE_JOB")
                    if self.terminal_closure(original, job, kind, before) is None:
                        raise NativeAssistedEntryConflict("NATIVE_COMMAND_COMPETING_ACTIVE_JOB")
                else:
                    if expected_id in physical_ids:
                        raise NativeAssistedEntryConflict("NATIVE_COMMAND_ORPHAN_PHYSICAL_JOB")
                    withdrawn = evidence.withdrawal(original, kind, lambda: None)
                    if withdrawn is None or withdrawn[0]["payload"]["job_id"] is not None:
                        raise NativeAssistedEntryConflict("NATIVE_COMMAND_COMPETING_PENDING_REQUEST")
            cursor_time, cursor_id = rows[-1]["journal_occurred_at"], rows[-1]["journal_request_id"]
        before()

    @staticmethod
    def settled_pair(job, delivery):
        return delivery is not None and (job["status"], delivery.status.value,
            delivery.delivery_state.value if delivery.delivery_state else None) in _TERMINAL
