"""Authenticate actual prior accepted attempts and their closed retry journals.

Historical facts confer no current authority. Every positive caller must also
verify current grants and both actual owner fences in the same bounded root.
No historical Job/Intake is fabricated and no prior operation is discarded.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta

from modules.listing.application.native_intake_assisted_entry_request_proof import _object, _time
from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_parser_failure_policy import parser_failure_kind
from modules.listing.application.native_intake_parser_job_proof import (
    NativeParserJobProof,
    parser_admission_provenance,
)
from modules.listing.application.native_intake_parser_journal import (
    ClosedParserJournalFacts,
    closed_parser_attempt_journal,
    combine_closed_parser_journals,
)
from modules.listing.application.native_intake_parser_policy import (
    JOB_TYPE,
    LEASE_SECONDS,
    NativeParserConflict,
)
from modules.listing.application.native_intake_retrieval import audited_value_matches
from modules.listing.application.native_intake_retrieval_policy import (
    canonical_json,
    canonical_sha256,
)

RETRY_CODE = "NATIVE_PARSER_RETRY_SCHEDULED"
RETRY_REASON = "Prepare one authorized retry of the same parser run; retain the original command and every attempt."
ACCEPTANCE_FIELDS = {"job_id", "parser_run_id", "intake_id", "accepted_at", "business_fence", "physical_fence",
    "worker", "attempt", "delivery_generation", "attempt_token_sha256", "run_version_before", "run_version_after", "audit_event_id"}
RETRY_FIELDS = {"proof_version", "job_id", "parser_run_id", "intake_id", "prepared_at", "available_at", "attempt",
    "delivery_generation", "business_fence", "physical_fence", "worker", "business_version", "physical_version",
    "physical_attempts", "run_version_before", "run_version_after", "timeout_at", "first_started_at",
    "acceptance_audit_event_id", "audit_event_id", "code", "error_class", "failure_intent_id", "failure_observation",
    "intent_ids", "journal_sha256", "quality_summary", "physical_lease_expires_at", "business_lease_expires_at",
    "native_parser_provenance"}
ACTIVE_FIELDS = {"admission", "delivery_generation", "attempt_token_sha256", "attempt_intents",
    "acceptance_receipt", "accepted_binding"}


def active_payload_fields(attempt):
    return ACTIVE_FIELDS | ({"attempt_history"} if attempt > 1 else set())


def reject():
    raise NativeParserConflict("NATIVE_PARSER_ATTEMPT_HISTORY_REJECTED") from None


def exact(actual, expected):
    return (audited_value_matches(actual, expected)
        and all(canonical_json(actual[k]) == canonical_json(v) for k, v in expected.items()))


def transient_failure(items, facts):
    if (facts.error_count != 1 or not items or items[-1]["state"] != "ERROR"
            or items[-1]["intent_id"] != facts.intent_ids[-1]
            or parser_failure_kind(items[-1].get("failure_observation"), items[-1]["error_class"]) != "TRANSIENT"):
        reject()
    return items[-1]


@dataclass(frozen=True)
class NativeParserPreviousAttempts:
    journals: tuple[ClosedParserJournalFacts, ...]
    current_items: list
    history_sha256: str


@dataclass(frozen=True)
class NativeParserPreparedRetry:
    """An authenticated queued retry, never terminal or an execution grant."""
    parser_run_id: str
    receipt_sha256: str
    receipt_json: str


class NativeParserAttemptHistory:
    def __init__(self, graph):
        self.graph, self.engine, self.queue = graph, graph.engine, graph.queue
        self.jobs = NativeParserJobProof(graph)

    def acceptance_record(self, job, run, receipt, binding, *, attempt, before):
        self.jobs._root(job["tenant_id"], before)
        admission = _object(job["payload"])["admission"]
        integers = ("business_fence", "physical_fence", "attempt", "delivery_generation", "run_version_before", "run_version_after")
        if (set(receipt) != ACCEPTANCE_FIELDS
                or any(type(receipt[k]) is not int or not 1 <= receipt[k] <= 2**63-1 for k in integers)
                or type(attempt) is not int or not 1 <= attempt <= job["max_attempts"]
                or receipt["attempt"] != attempt or receipt["business_fence"] != attempt+1
                or receipt["job_id"] != job["job_id"] or receipt["parser_run_id"] != run["parser_run_id"]
                or receipt["intake_id"] != run["intake_id"] or receipt["delivery_generation"] != 1
                or receipt["delivery_generation"] != _object(job["payload"])["delivery_generation"]
                or receipt["attempt_token_sha256"] != _object(job["payload"])["attempt_token_sha256"]
                or not isinstance(receipt["worker"], str) or not receipt["worker"]
                or receipt["run_version_before"] != 2*attempt-1 or receipt["run_version_after"] != 2*attempt
                or run["version"] < receipt["run_version_after"]
                or canonical_json(binding) != canonical_json(admission["binding"])
                or not _time(admission["admitted_at"]) <= _time(receipt["accepted_at"]) < _time(job["timeout_at"])
                or run["started_at"] is None or _time(run["started_at"]) > _time(receipt["accepted_at"])):
            reject()
        native_intake_uuid(receipt["audit_event_id"])
        before()
        audit = self.engine.query_one("SELECT after_value,before_value,occurred_at FROM audit.audit_events "
            "WHERE tenant_id=? AND audit_event_id=? AND resource_type='intake' AND resource_id=? "
            "AND actor_subject_id IS NULL AND service_principal=? AND actor_role='SVC_PARSER' "
            "AND action='intake.parser_attempt_accepted.v1' AND result='SUCCEEDED' AND causation_id=? AND correlation_id=?",
            (job["tenant_id"], receipt["audit_event_id"], run["intake_id"], JOB_TYPE,
                admission["binding"]["original_input"]["parent_event_id"], job["correlation_id"]))
        previous = {"job_id": job["job_id"], "status": "QUEUED" if attempt == 1 else "RETRYING", "parser_run_status": "QUEUED"}
        after = {**receipt, "status": "RUNNING", "parser_run_status": "RUNNING", "intake_mutated": False,
            "accepted_binding": binding, "native_parser_provenance": parser_admission_provenance(_object(job["payload"]))}
        if (audit is None or canonical_json(_object(audit["before_value"])) != canonical_json(previous)
                or not exact(_object(audit["after_value"]), after)
                or _time(audit["occurred_at"]) != _time(receipt["accepted_at"])):
            reject()
        before.check_in_memory()
        return receipt

    def current_acceptance(self, job, run, *, before):
        payload = _object(job["payload"])
        receipt = self.acceptance_record(job, run, _object(payload["acceptance_receipt"]),
            _object(payload["accepted_binding"]), attempt=job["attempt"], before=before)
        if receipt["business_fence"] != job["fence_token"]:
            reject()
        return receipt

    def closed_current(self, job, run, physical, original, acceptance, at, *, before):
        """Closed facts only; the owner separately authenticates current acceptance."""
        payload = _object(job["payload"])
        prior = self.previous(job, run, physical, original, before=before)
        current = closed_parser_attempt_journal(run, payload["admission"], original, acceptance,
            prior.current_items, at, timeout_at=job["timeout_at"], before=before)
        combined = combine_closed_parser_journals((*prior.journals, current), payload["attempt_intents"], before=before)
        return prior, current, combined

    def retry_record(self, job, run, receipt, acceptance, items, facts, *, max_physical_attempts, before):
        admission = _object(job["payload"])["admission"]
        last = transient_failure(items, facts)
        integers = ("proof_version", "attempt", "delivery_generation", "business_fence", "physical_fence",
            "business_version", "physical_version", "physical_attempts", "run_version_before", "run_version_after")
        if (set(receipt) != RETRY_FIELDS or any(type(receipt[k]) is not int or not 1 <= receipt[k] <= 2**63-1 for k in integers)
                or receipt["proof_version"] != 1 or receipt["code"] != RETRY_CODE
                or receipt["job_id"] != job["job_id"] or receipt["parser_run_id"] != run["parser_run_id"]
                or receipt["intake_id"] != run["intake_id"]
                or any(receipt[k] != acceptance[k] for k in ("attempt", "delivery_generation", "business_fence", "physical_fence", "worker"))
                or receipt["acceptance_audit_event_id"] != acceptance["audit_event_id"]
                or not receipt["attempt"] <= receipt["physical_attempts"] < max_physical_attempts
                or not receipt["attempt"] < job["max_attempts"]
                or receipt["run_version_before"] != acceptance["run_version_after"]
                or receipt["run_version_after"] != receipt["run_version_before"]+1
                or _time(receipt["timeout_at"]) != _time(job["timeout_at"])
                or _time(receipt["first_started_at"]) != _time(run["started_at"])
                or receipt["intent_ids"] != list(facts.intent_ids) or receipt["journal_sha256"] != facts.journal_sha256
                or canonical_json(receipt["quality_summary"]) != canonical_json(facts.quality_summary())
                or receipt["failure_intent_id"] != last["intent_id"] or receipt["error_class"] != last["error_class"]
                or canonical_json(receipt["failure_observation"]) != canonical_json(last["failure_observation"])
                or receipt["native_parser_provenance"] != parser_admission_provenance(_object(job["payload"]))):
            reject()
        at, due = _time(receipt["prepared_at"]), _time(receipt["available_at"])
        backoff = admission["policy"]["backoff_seconds"][receipt["attempt"]-1]
        if (type(backoff) not in {int, float} or not math.isfinite(backoff) or backoff <= 0
                or due != at+timedelta(seconds=backoff)
                or not _time(acceptance["accepted_at"]) <= at < due < min(_time(job["timeout_at"]), _time(admission["binding"]["source_valid_until"]))
                or not at < _time(receipt["business_lease_expires_at"]) <= _time(receipt["physical_lease_expires_at"])
                or _time(receipt["physical_lease_expires_at"])-at > timedelta(seconds=LEASE_SECONDS)):
            reject()
        native_intake_uuid(receipt["audit_event_id"])
        before()
        audit = self.engine.query_one("SELECT before_value,after_value,occurred_at,reason FROM audit.audit_events "
            "WHERE tenant_id=? AND audit_event_id=? AND resource_type='intake' AND resource_id=? "
            "AND actor_subject_id IS NULL AND service_principal=? AND actor_role='SVC_PARSER' "
            "AND action='intake.parser_retry_prepared.v1' AND result='SUCCEEDED' AND causation_id=? AND correlation_id=?",
            (job["tenant_id"], receipt["audit_event_id"], run["intake_id"], JOB_TYPE,
                admission["binding"]["original_input"]["parent_event_id"], job["correlation_id"]))
        previous = {"job_id": job["job_id"], "status": "RUNNING", "parser_run_status": "RUNNING", "attempt": receipt["attempt"]}
        after = {**receipt, "status": "RETRYING", "parser_run_status": "QUEUED", "intake_mutated": False}
        if (audit is None or canonical_json(_object(audit["before_value"])) != canonical_json(previous)
                or not exact(_object(audit["after_value"]), after) or audit["reason"] != RETRY_REASON
                or _time(audit["occurred_at"]) != at):
            reject()
        before.check_in_memory()
        return receipt

    def previous(self, job, run, physical, original, *, before):
        """Prove every previous attempt; caller separately proves the current one."""
        self.jobs._root(job["tenant_id"], before)
        payload = _object(job["payload"])
        attempt, entries, items = job["attempt"], payload.get("attempt_history", []), payload["attempt_intents"]
        if (type(attempt) is not int or not 0 <= attempt <= job["max_attempts"]
                or not isinstance(entries, list) or len(entries) != max(0, attempt-1)
                or (attempt <= 1 and "attempt_history" in payload) or (attempt > 1 and "attempt_history" not in payload)
                or not isinstance(items, list) or len(items) > max(1, attempt)*4
                or any(not isinstance(item, dict) or type(item.get("attempt")) is not int for item in items)):
            reject()
        if attempt == 0:
            if items or entries:
                reject()
            return NativeParserPreviousAttempts((), [], canonical_sha256([]))
        current = _object(payload["acceptance_receipt"])
        cursor, journals, seen_ids, seen_slots = 0, [], set(), set()
        previous_physical_attempts = 0
        for position, entry in enumerate(entries, start=1):
            before.check_in_memory()
            if not isinstance(entry, dict) or set(entry) != {"acceptance_receipt", "accepted_binding", "retry_receipt"}:
                reject()
            acceptance = self.acceptance_record(job, run, _object(entry["acceptance_receipt"]),
                _object(entry["accepted_binding"]), attempt=position, before=before)
            subset = []
            while cursor < len(items) and items[cursor]["attempt"] == position:
                subset.append(items[cursor])
                cursor += 1
            prepared = _object(entry["retry_receipt"])
            facts = closed_parser_attempt_journal(run, payload["admission"], original, acceptance, subset,
                _time(prepared["prepared_at"]), timeout_at=job["timeout_at"], before=before)
            retry = self.retry_record(job, run, prepared, acceptance, subset, facts,
                max_physical_attempts=physical.max_retries, before=before)
            following = _object(entries[position]["acceptance_receipt"]) if position < len(entries) else current
            if (following["attempt"] != position+1 or following["business_fence"] != retry["business_fence"]+1
                    or following["physical_fence"] <= retry["physical_fence"]
                    or _time(following["accepted_at"]) < _time(retry["available_at"])
                    or not previous_physical_attempts < retry["physical_attempts"] < physical.attempts
                    or not retry["business_version"] < job["version"] or not retry["physical_version"] < physical.version
                    or seen_ids.intersection(facts.intent_ids) or seen_slots.intersection(facts.reservation_ids)):
                reject()
            if position == 1 and _time(run["started_at"]) != _time(acceptance["accepted_at"]):
                reject()
            previous_physical_attempts = retry["physical_attempts"]
            seen_ids.update(facts.intent_ids)
            seen_slots.update(facts.reservation_ids)
            journals.append(facts)
        current_items = items[cursor:]
        if (any(item["attempt"] != attempt or type(item.get("delivery_generation")) is not int
                or item.get("delivery_generation") != payload["delivery_generation"]
                or item.get("intent_id") in seen_ids
                or (isinstance(item.get("reservation"), dict) and item["reservation"].get("reservation_id") in seen_slots)
                for item in current_items)
                or (attempt == 1 and _time(run["started_at"]) != _time(current["accepted_at"]))):
            reject()
        if seen_slots:
            before()
            if self.engine.query_one("SELECT reservation_id FROM intake.native_retrieval_inflight_slots "
                    "WHERE reservation_id=ANY(?::uuid[]) LIMIT 1", (list(seen_slots),)) is not None:
                reject()
        before.check_in_memory()
        return NativeParserPreviousAttempts(tuple(journals), current_items, canonical_sha256(entries))
