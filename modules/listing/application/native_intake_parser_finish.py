"""Close one witnessed operation, with one bounded unknown-reply readback.

This private metadata owner never launches IO, renews a lease, changes a run
or Intake, or restores a positive grant. It requires the same live physical
claim and the IO issuer's one-use pending action. Expired business time alone
may not prevent recording a real completion. Output still requires the normal
separate fresh positive root after this method returns.
"""
from __future__ import annotations

from datetime import timedelta

from modules.listing.application.native_intake_assisted_entry_request_proof import _object, _time
from modules.listing.application.native_intake_parser_attempts import (
    NativeParserAttemptHistory,
    active_payload_fields,
)
from modules.listing.application.native_intake_parser_completion import (
    _job_identity,
    _ParserOperationFinishes,
    _physical_identity,
    _run_identity,
)
from modules.listing.application.native_intake_parser_history import NativeParserTerminalHistory
from modules.listing.application.native_intake_parser_journal import (
    INTENT_FIELDS,
    closed_parser_attempt_journal,
)
from modules.listing.application.native_intake_parser_policy import NativeParserConflict
from modules.listing.application.native_intake_parser_prerequisites import NativeParserCommandBudget
from modules.listing.application.native_intake_retrieval_policy import canonical_json
from modules.listing.application.native_intake_retrieval_quota import SLOT_SECONDS
from shared.jobs.queue import JobStatus

_ADMITTED_FIELDS = INTENT_FIELDS - {"finished_at", "process_state", "observed", "error_class"}


def _reject():
    raise NativeParserConflict("NATIVE_PARSER_WITNESSED_FINISH_REJECTED") from None


class _ParserFinishSQL:
    def __init__(self, io, records):
        if type(records) is not _ParserOperationFinishes or records.io is not io:
            _reject()
        records._scope()
        self.io, self.records = io, records
        self.owner, self.graph, self.engine, self.queue = io.owner, io.owner.graph, io.owner.engine, io.owner.queue
        self.tenant, self.generation = io.tenant, io.generation

    def _physical_now(self, physical, before):
        # A final DB-clock predicate is required even in the read-only branch.
        before()
        row = self.engine.query_one("SELECT job_id FROM durable_jobs WHERE job_id=? AND status=? "
            "AND fence_token=? AND locked_by=? AND job_type=? AND correlation_id=? AND idempotency_key=? "
            "AND attempts=? AND max_retries=? AND payload_json::text=(?::jsonb)::text "
            "AND lease_expires_at>clock_timestamp()",
            (physical.job_id, JobStatus.RUNNING.value, physical.fence_token, physical.locked_by,
                physical.job_type, physical.correlation_id, physical.idempotency_key, physical.attempts,
                physical.max_retries, canonical_json(physical.payload)))
        if row is None:
            _reject()
        before()

    def _owned(self, pending):
        facts, intent, captured = self.records.facts(pending)
        before = NativeParserCommandBudget(self.owner.clock)
        before()
        self.graph._one("SELECT job_id FROM durable_jobs WHERE job_id=? FOR UPDATE", (self.io.delivery.job_id,))
        before()
        physical = self.queue.get(self.io.delivery.job_id)
        now = self.owner.clock._clock()
        if (physical is None or physical.status != JobStatus.RUNNING
                or physical.lease_expires_at is None or physical.lease_expires_at <= now
                or canonical_json(_physical_identity(physical)) != canonical_json(facts["physical"])
                or type(physical.fence_token) is not int or type(physical.attempts) is not int):
            _reject()
        before.deadline = min(before.deadline, physical.lease_expires_at)
        before()
        job = self.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=? FOR UPDATE",
            (self.tenant, physical.job_id))
        payload = _object(job["payload"])
        if (job["status"] != "RUNNING" or job["result"] is not None
                or canonical_json(_job_identity(job)) != canonical_json(facts["job"])
                or type(job["version"]) is not int or job["version"] < 1
                or type(job["attempt"]) is not int or type(job["fence_token"]) is not int
                or job["fence_token"] != self.io.leases.business_fence
                or type(payload.get("delivery_generation")) is not int
                or payload["delivery_generation"] != self.generation
                or set(payload) != active_payload_fields(job["attempt"])):
            _reject()
        run = self.owner.runs.get(self.tenant, job["aggregate_id"], before=before, for_update=True)
        if (run is None or canonical_json(_run_identity(run)) != canonical_json(facts["run"])
                or run["status"] != "RUNNING" or run["version"] != 2 * job["attempt"]
                or run["parsed_payload"] is not None or run["normalized_payload"] is not None
                or run["completed_at"] is not None or _object(run["field_confidence"]) != {}
                or run["validation_errors"] not in ([], "[]")):
            _reject()
        # No edited historical rows or cached validators. Reopen actual
        # admission, accepted attempt, original parent and every prior retry.
        self.owner.jobs.admission(job, run, before=before)
        acceptance = self.owner.jobs.acceptance(job, run, before=before)
        original = NativeParserTerminalHistory(self.graph)._parent_input(
            self.tenant, payload["admission"]["binding"], before)
        if canonical_json(original.binding()) != canonical_json(facts["original_input"]):
            _reject()
        prior = NativeParserAttemptHistory(self.graph).previous(job, run, physical, original, before=before)
        current = prior.current_items
        items, captured_items = payload["attempt_intents"], captured["attempt_intents"]
        if (not current or not items or len(items) != len(captured_items)
                or canonical_json(items[:-1]) != canonical_json(captured_items[:-1])
                or canonical_json(captured_items[-1]) != canonical_json(intent)
                or canonical_json({k: v for k, v in payload.items() if k != "attempt_intents"})
                    != canonical_json({k: v for k, v in captured.items() if k != "attempt_intents"})
                or current[-1]["intent_id"] != intent["intent_id"]
                or acceptance["worker"] != physical.locked_by
                or acceptance["physical_fence"] != physical.fence_token):
            _reject()
        prefix = current[:-1]
        historical = closed_parser_attempt_journal(run, payload["admission"], original, acceptance,
            prefix, now, timeout_at=job["timeout_at"], before=before)
        if historical.error_count:
            _reject()
        if historical.reservation_ids:
            before()
            if self.engine.query_one("SELECT reservation_id FROM intake.native_retrieval_inflight_slots "
                    "WHERE reservation_id=ANY(?::uuid[]) LIMIT 1", (list(historical.reservation_ids),)) is not None:
                _reject()
        return physical, job, run, payload, original, acceptance, intent, prefix, now, before

    def _transition(self, pending):
        physical, job, run, payload, original, acceptance, intent, prefix, now, before = self._owned(pending)
        actual = payload["attempt_intents"][-1]
        expected = self.records.expected(pending)
        admitted = canonical_json(actual) == canonical_json(intent)
        if admitted:
            if set(actual) != _ADMITTED_FIELDS or actual["state"] != "ADMITTED":
                _reject()
            expected = self.records.seal(pending, now)
        elif expected is None or canonical_json(actual) != canonical_json(expected):
            _reject()
        # The prefix above is actual durable history. This second pure check
        # validates the proposed entry's closed fields against the real parent
        # and acceptance. In the ADMITTED branch its reality comes only from
        # the retained runner witness; this check does not establish durability.
        closed_parser_attempt_journal(run, payload["admission"], original, acceptance,
            [*prefix, expected], now, timeout_at=job["timeout_at"], before=before)
        reservation = intent["reservation"]
        quota = self.io.quota
        quota.schema(before=before)
        quota._locks(reservation["source_id"], before)
        before()
        slot = self.engine.query_one("SELECT reservation_id,source_id,admitted_at,expires_at "
            "FROM intake.native_retrieval_inflight_slots WHERE reservation_id=? FOR UPDATE",
            (reservation["reservation_id"],))
        if not admitted:
            if slot is not None:
                _reject()
            self._physical_now(physical, before)
            return
        if (slot is None or reservation["rate_reservation_id"] is not None
                or slot["source_id"] != reservation["source_id"]
                or _time(slot["expires_at"]) != _time(reservation["expires_at"])
                or not _time(slot["admitted_at"]) <= _time(intent["admitted_at"]) < _time(slot["expires_at"])
                or not timedelta(0) < _time(slot["expires_at"])-_time(slot["admitted_at"])
                    <= timedelta(seconds=SLOT_SECONDS+1)):
            _reject()
        # Never call reserve_storage: it can remove unrelated expired slots.
        before()
        if self.engine.execute("DELETE FROM intake.native_retrieval_inflight_slots WHERE reservation_id=? "
                "AND source_id=? AND expires_at=? AND admitted_at=?",
                (reservation["reservation_id"], reservation["source_id"], _time(slot["expires_at"]).isoformat(),
                    _time(slot["admitted_at"]).isoformat())).rowcount != 1:
            _reject()
        updated = {**payload, "attempt_intents": [*payload["attempt_intents"][:-1], expected]}
        before()
        if self.engine.execute("UPDATE workflow.jobs SET payload=?::jsonb,version=version+1,"
                "updated_at=clock_timestamp() WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? "
                "AND attempt=? AND status='RUNNING' AND result IS NULL AND payload::text=(?::jsonb)::text "
                "AND EXISTS (SELECT 1 FROM durable_jobs d WHERE d.job_id=workflow.jobs.job_id::text "
                "AND d.status=? AND d.fence_token=? AND d.locked_by=? AND d.lease_expires_at>clock_timestamp())",
                (canonical_json(updated), self.tenant, job["job_id"], job["version"], job["fence_token"], job["attempt"],
                    canonical_json(payload), JobStatus.RUNNING.value, physical.fence_token, physical.locked_by)).rowcount != 1:
            _reject()
        self._physical_now(physical, before)

    def finish(self, pending):
        self.records.facts(pending)
        if getattr(getattr(self.engine, "_local", None), "connection", None) is not None:
            _reject()
        first_error = None
        # At most one initial root plus one fresh outcome reconciliation.
        # A journal close never starts another provider call or heartbeat.
        for _ in range(2):
            try:
                self.io.leases.pause()
                with self.graph.transaction(self.tenant):
                    self._transition(pending)
                return self.records.confirm(pending)
            except Exception as error:
                if first_error is None:
                    first_error = error
        try:
            self.records.unresolved(pending)
        except Exception:
            pass
        # Outside both handlers: preserve the original finish error rather
        # than replacing its identity/cause with an inconclusive second read.
        raise first_error
