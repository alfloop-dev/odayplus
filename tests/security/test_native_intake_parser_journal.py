"""Adversarial closed-operation facts only; these fixtures confer no SQL grant."""
import copy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from modules.external_data.application.native_intake_process import NativeIORejected
from modules.listing.application.native_intake_parser_failure_policy import (
    parser_failure_observation,
)
from modules.listing.application.native_intake_parser_journal import (
    closed_parser_attempt_journal,
    combine_closed_parser_journals,
)
from modules.listing.application.native_intake_parser_policy import NativeParserConflict
from modules.listing.application.native_intake_retrieval_policy import canonical_sha256


@pytest.fixture
def journal_case():
    at = datetime(2026, 10, 18, tzinfo=UTC)
    run = {k: str(uuid4()) for k in ("parser_run_id", "source_snapshot_id", "intake_id")}
    snapshot = {"source_id": "synthetic-parser-journal", "raw_object_uri": "gs://synthetic-parser-journal/original",
        "object_generation": 1, "byte_length": 137, "content_sha256": "a"*64}
    original = SimpleNamespace(snapshot=lambda: snapshot, sha256="b"*64,
        lineage=SimpleNamespace(content_sha256="a"*64))
    binding = {"policy_sha256": "c"*64, "source_version": 7, "source_sha256": "d"*64,
        "source_valid_until": (at+timedelta(seconds=120)).isoformat(), "human_authority_sha256": "e"*64}
    artifact = {"output_schema_version": "synthetic-output-v1"}
    job_id = str(uuid4())
    admission = {"binding": binding, "artifact": artifact,
        "policy": {"storage": {"bucket": "synthetic-parser-journal"}}, "receipt": {"job_id": job_id}}
    def attempt(number):
        accepted = at+timedelta(seconds=number*10)
        acceptance = {"job_id": job_id, "parser_run_id": run["parser_run_id"], "intake_id": run["intake_id"],
            "attempt": number, "delivery_generation": 1, "business_fence": number+1, "physical_fence": number*2,
            "worker": "synthetic-parser-owner", "accepted_at": accepted.isoformat()}
        items = []
        for position, operation in enumerate(("gcs_bucket", "gcs_head")):
            stamp = accepted+timedelta(seconds=position+1)
            items.append({"intent_id": str(uuid4()), "operation": operation,
                "arguments": {} if position==0 else {"key": "original", "generation": 1},
                "state": "FINISHED" if position==0 else "ERROR", "delivery_generation": 1,
                "physical_fence": acceptance["physical_fence"], "business_fence": acceptance["business_fence"],
                "attempt": number, "worker": acceptance["worker"], "parser_run_id": run["parser_run_id"],
                "source_snapshot_id": run["source_snapshot_id"], "original_input_sha256": original.sha256,
                "artifact_sha256": canonical_sha256(artifact), "binding_sha256": canonical_sha256(binding),
                **binding, "reservation": {"reservation_id": str(uuid4()), "rate_reservation_id": None,
                    "source_id": snapshot["source_id"], "expires_at": (stamp+timedelta(seconds=45)).isoformat()},
                "admitted_at": stamp.isoformat(), "finished_at": stamp.isoformat(), "process_state": "NO_CHILD_REMAINS",
                "observed": {"sha256": "f"*64} if position==0 else None,
                "error_class": None if position==0 else "NativeIORejected"})
        items[-1]["failure_observation"] = parser_failure_observation(NativeIORejected("NATIVE_IO_CONNECTION_FAILED"))
        return acceptance, items
    before = SimpleNamespace(check_in_memory=lambda: None)
    return run, admission, original, attempt, at+timedelta(seconds=119), before


def read(case, acceptance, items):
    run, admission, original, _, at, before = case
    return closed_parser_attempt_journal(run, admission, original, acceptance, items, at,
        timeout_at=at+timedelta(seconds=1), before=before)


def test_each_explicit_accepted_attempt_binds_all_of_its_operations_without_fake_job_views(journal_case):
    first, items1 = journal_case[3](1)
    second, items2 = journal_case[3](2)
    one, two = read(journal_case, first, items1), read(journal_case, second, items2)
    facts = combine_closed_parser_journals((one, two), items1+items2, before=journal_case[-1])
    assert facts.intent_ids == tuple(i["intent_id"] for i in items1+items2)
    assert facts.journal_sha256 == canonical_sha256(items1+items2)
    assert facts.quality_summary() == {"recorded_operations": 4, "finished_operations": 2,
        "error_operations": 2, "reaped_operations": 4, "unresolved_operations": 0, "output_committed": False}


@pytest.mark.parametrize("damage", ["unresolved", "unreaped", "old_fence", "wrong_attempt", "unknown_field",
    "error_midstream", "wrong_job", "wrong_intake", "boolean_fence", "before_acceptance", "late_finish",
    "wrong_error_family", "extra_error_field"])
def test_closed_labels_do_not_replace_exact_accepted_operation_facts(journal_case, damage):
    acceptance, items = journal_case[3](2)
    if damage == "unresolved":
        items[-1]["state"] = "ADMITTED"
    elif damage == "unreaped":
        items[-1]["process_state"] = "UNKNOWN"
    elif damage == "old_fence":
        items[0]["physical_fence"] -= 1
    elif damage == "wrong_attempt":
        items[0]["attempt"] -= 1
    elif damage == "unknown_field":
        items[-1]["legacy_retry"] = True
    elif damage == "error_midstream":
        items[0]["state"], items[0]["observed"], items[0]["error_class"] = "ERROR", None, "NativeIORejected"
    elif damage == "wrong_job":
        acceptance["job_id"] = str(uuid4())
    elif damage == "wrong_intake":
        acceptance["intake_id"] = str(uuid4())
    elif damage == "boolean_fence":
        acceptance["physical_fence"] = True
        for item in items:
            item["physical_fence"] = True
    elif damage == "before_acceptance":
        items[0]["admitted_at"] = (datetime.fromisoformat(acceptance["accepted_at"])-timedelta(seconds=1)).isoformat()
    elif damage == "late_finish":
        items[-1]["finished_at"] = (journal_case[-2]+timedelta(seconds=1)).isoformat()
    elif damage == "wrong_error_family":
        items[-1]["failure_observation"]["family"] = "NativeIOUnreaped"
    else:
        items[-1]["failure_observation"]["retryable"] = True
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_ATTEMPT_JOURNAL_REJECTED$"):
        read(journal_case, acceptance, items)


@pytest.mark.parametrize("damage", ["missing_attempt", "extra_operation", "order", "reused_intent", "reused_slot"])
def test_combined_history_cannot_drop_or_reuse_a_recorded_operation(journal_case, damage):
    first, items1 = journal_case[3](1)
    second, items2 = journal_case[3](2)
    if damage == "reused_intent":
        items2[0]["intent_id"] = items1[0]["intent_id"]
    elif damage == "reused_slot":
        items2[0]["reservation"]["reservation_id"] = items1[0]["reservation"]["reservation_id"]
    one, two = read(journal_case, first, items1), read(journal_case, second, items2)
    records, items = (one, two), items1+items2
    if damage == "missing_attempt":
        records = (two,)
    elif damage == "extra_operation":
        items.append(copy.deepcopy(items[-1]))
    elif damage == "order":
        items.reverse()
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_ATTEMPT_JOURNAL_REJECTED$"):
        combine_closed_parser_journals(records, items, before=journal_case[-1])
