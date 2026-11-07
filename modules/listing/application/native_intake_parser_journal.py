"""Closed actual parser-operation facts for an explicit accepted attempt.

This verifier has no SQL grant, does not synthesize a Job/Intake, and never
proves acceptance by itself. Its owner must authenticate the actual accepted
attempt, original immutable parent, current fences, quota and audit records.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from modules.listing.application.native_intake_assisted_entry_request_proof import _time
from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_parser_failure_policy import (
    valid_parser_failure_observation,
)
from modules.listing.application.native_intake_parser_policy import (
    MAX_ATTEMPTS,
    NativeParserConflict,
)
from modules.listing.application.native_intake_retrieval_policy import (
    canonical_json,
    canonical_sha256,
)

OPERATIONS = ("gcs_bucket", "gcs_head", "gcs_download", "parser_child")
INTENT_FIELDS = {
    "intent_id", "operation", "arguments", "state", "delivery_generation", "physical_fence", "business_fence",
    "attempt", "worker", "parser_run_id", "source_snapshot_id", "original_input_sha256", "artifact_sha256",
    "binding_sha256", "policy_sha256", "source_version", "source_sha256", "source_valid_until",
    "human_authority_sha256", "reservation", "admitted_at", "finished_at", "process_state", "observed", "error_class",
}


def reject():
    raise NativeParserConflict("NATIVE_PARSER_ATTEMPT_JOURNAL_REJECTED") from None


def digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


@dataclass(frozen=True)
class ClosedParserJournalFacts:
    intent_ids: tuple[str, ...]
    reservation_ids: tuple[str, ...]
    journal_sha256: str
    finished_count: int
    error_count: int

    def quality_summary(self):
        return {"recorded_operations": len(self.intent_ids), "finished_operations": self.finished_count,
            "error_operations": self.error_count, "reaped_operations": len(self.intent_ids),
            "unresolved_operations": 0, "output_committed": False}


def closed_parser_attempt_journal(run, admission, original, acceptance, items, at, *, timeout_at, before):
    """Read every operation for the given genuine acceptance; ERROR is not reaping."""
    binding, artifact = admission["binding"], admission["artifact"]
    snapshot = original.snapshot()
    if (type(acceptance["attempt"]) is not int or not 1 <= acceptance["attempt"] <= MAX_ATTEMPTS
            or type(acceptance["delivery_generation"]) is not int or acceptance["delivery_generation"] != 1
            or any(type(acceptance[k]) is not int or not 1 <= acceptance[k] <= 2**63-1
                for k in ("business_fence", "physical_fence"))
            or acceptance["business_fence"] != acceptance["attempt"]+1
            or not isinstance(acceptance["worker"], str) or not acceptance["worker"]
            or acceptance["job_id"] != admission["receipt"]["job_id"]
            or acceptance["parser_run_id"] != run["parser_run_id"] or acceptance["intake_id"] != run["intake_id"]
            or not isinstance(items, list) or len(items) > len(OPERATIONS)):
        reject()
    prefix = "gs://" + admission["policy"]["storage"]["bucket"] + "/"
    if not snapshot["raw_object_uri"].startswith(prefix):
        reject()
    key = snapshot["raw_object_uri"][len(prefix):]
    arguments = ({}, {"key": key, "generation": snapshot["object_generation"]},
        {"key": key, "generation": snapshot["object_generation"]},
        {"document_sha256": original.lineage.content_sha256, "byte_length": snapshot["byte_length"],
            "artifact_sha256": canonical_sha256(artifact), "output_schema_version": artifact["output_schema_version"]})
    ids, reservations, finished, errors = [], [], 0, 0
    for position, item in enumerate(items):
        before.check_in_memory()
        if (not isinstance(item, dict) or set(item) not in (INTENT_FIELDS, INTENT_FIELDS | {"failure_observation"})
                or item["state"] not in {"FINISHED", "ERROR"}
                or item["process_state"] != "NO_CHILD_REMAINS"):
            reject()
        if "failure_observation" in item and (item["state"] != "ERROR"
                or not valid_parser_failure_observation(item["failure_observation"], item["error_class"])):
            reject()
        expected = {"operation": OPERATIONS[position], "arguments": arguments[position], "delivery_generation": acceptance["delivery_generation"],
            "physical_fence": acceptance["physical_fence"], "business_fence": acceptance["business_fence"],
            "attempt": acceptance["attempt"], "worker": acceptance["worker"],
            "parser_run_id": run["parser_run_id"], "source_snapshot_id": run["source_snapshot_id"],
            "original_input_sha256": original.sha256, "artifact_sha256": canonical_sha256(artifact),
            "binding_sha256": canonical_sha256(binding), "policy_sha256": binding["policy_sha256"],
            "source_version": binding["source_version"], "source_sha256": binding["source_sha256"],
            "source_valid_until": binding["source_valid_until"], "human_authority_sha256": binding["human_authority_sha256"]}
        admitted, completed = _time(item["admitted_at"]), _time(item["finished_at"])
        if (canonical_json({k: item[k] for k in expected}) != canonical_json(expected)
                or not _time(acceptance["accepted_at"]) <= admitted <= completed <= at
                or admitted >= min(_time(timeout_at), _time(binding["source_valid_until"]))
                or (position and _time(items[position-1]["finished_at"]) > admitted)):
            reject()
        observed = item["observed"]
        if item["state"] == "ERROR":
            if (position != len(items)-1 or observed is not None or not isinstance(item["error_class"], str)
                    or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,95}", item["error_class"]) is None):
                reject()
            errors += 1
        else:
            if item["error_class"] is not None or not isinstance(observed, dict) or not digest(observed.get("sha256")):
                reject()
            if position == 0 and set(observed) != {"sha256"}:
                reject()
            if position in {1, 2} and (set(observed) != {"sha256", "generation", "byte_length"}
                    or type(observed["generation"]) is not int or type(observed["byte_length"]) is not int
                    or observed["generation"] != snapshot["object_generation"]
                    or observed["byte_length"] != snapshot["byte_length"]
                    or (position == 2 and observed["sha256"] != snapshot["content_sha256"])):
                reject()
            if position == 3 and (set(observed) != {"sha256", "artifact_sha256", "status"}
                    or observed["artifact_sha256"] != canonical_sha256(artifact)
                    or observed["status"] not in {"SUCCEEDED", "PARTIAL"}):
                reject()
            finished += 1
        reservation = item["reservation"]
        if (not isinstance(reservation, dict)
                or set(reservation) != {"reservation_id", "rate_reservation_id", "source_id", "expires_at"}
                or reservation["rate_reservation_id"] is not None or reservation["source_id"] != snapshot["source_id"]
                or _time(reservation["expires_at"]) <= admitted):
            reject()
        ids.append(native_intake_uuid(item["intent_id"]))
        reservations.append(native_intake_uuid(reservation["reservation_id"]))
    if len(ids) != len(set(ids)) or len(reservations) != len(set(reservations)):
        reject()
    before.check_in_memory()
    return ClosedParserJournalFacts(tuple(ids), tuple(reservations), canonical_sha256(items), finished, errors)


def combine_closed_parser_journals(journals, items, *, before):
    """Bind the complete flat journal; no extra or reused operation is ignored."""
    before.check_in_memory()
    if (not isinstance(journals, tuple) or not 1 <= len(journals) <= MAX_ATTEMPTS
            or any(not isinstance(facts, ClosedParserJournalFacts) for facts in journals)
            or not isinstance(items, list) or len(items) > MAX_ATTEMPTS*len(OPERATIONS)):
        reject()
    ids = tuple(ident for facts in journals for ident in facts.intent_ids)
    reservations = tuple(ident for facts in journals for ident in facts.reservation_ids)
    if (len(ids) != len(items) or len(ids) != len(set(ids)) or len(reservations) != len(set(reservations))
            or any(not isinstance(item, dict) for item in items)
            or [item["intent_id"] for item in items] != list(ids)):
        reject()
    before.check_in_memory()
    return ClosedParserJournalFacts(ids, reservations, canonical_sha256(items),
        sum(facts.finished_count for facts in journals), sum(facts.error_count for facts in journals))
