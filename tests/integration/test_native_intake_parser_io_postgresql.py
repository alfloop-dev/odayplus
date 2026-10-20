"""Actual parser leases/journal/quota and fixed child, synthetic GCS only.

These cases stop before canonical terminal job/run/Intake settlement. They do
not claim a default-worker or production acceptance result.
"""
import hashlib
from dataclasses import replace

import pytest
from fastapi import HTTPException

from modules.external_data.application.native_assisted_snapshot_transport import (
    NativeAssistedSnapshotProofRejected,
)
from modules.external_data.application.native_intake_process import (
    NativeIORejected,
    NativeIOUnreaped,
)
from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryConflict,
)
from modules.listing.application.native_intake_parser_io import NativeIntakeParserIO
from modules.listing.application.native_intake_parser_leases import NativeParserCompositeLeases
from modules.listing.application.native_intake_parser_policy import (
    SIGNING_KEY_ENV,
    NativeParserUnavailable,
)
from modules.listing.application.native_intake_parser_prerequisites import NativeParserCommandBudget
from modules.listing.application.native_intake_retrieval_quota import (
    NativeIntakeRetrievalQuota,
    NativeRetrievalQuotaExceeded,
)
from shared.jobs.queue import JobFenceRejectedError
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import BODY
from tests.integration.test_native_intake_parser_admission_postgresql import authority as authority
from tests.integration.test_native_intake_parser_admission_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_parser_admission_postgresql import claimed, persisted
from tests.integration.test_native_intake_parser_admission_postgresql import (
    identity_case as identity_case,
)
from tests.integration.test_native_intake_parser_admission_postgresql import (
    intake_blank_db as intake_blank_db,
)
from tests.integration.test_native_intake_parser_admission_postgresql import (
    native_api as native_api,
)
from tests.integration.test_native_intake_parser_admission_postgresql import (
    parser_admission_case as parser_admission_case,
)
from tests.integration.test_native_intake_parser_admission_postgresql import (
    parser_realm as parser_realm,
)
from tests.integration.test_native_intake_parser_admission_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_parser_admission_postgresql import (
    start_case as start_case,
)

pytestmark = pytest.mark.requires_live_env


@pytest.fixture(autouse=True)
def parser_evidence_fields(request, monkeypatch):
    if getattr(request, "param", None) == "partial":
        monkeypatch.setitem(BODY["fields"], "floor", "多樓層")


@pytest.fixture
def parser_io_case(parser_admission_case):
    case = parser_admission_case
    owner, ids, _, _, _, storage, _ = case
    _, delivery = claimed(case)
    fence, policy = owner._accept(delivery, ids["tenant"], 1)
    leases = NativeParserCompositeLeases(owner, delivery, business_fence=fence, policy_sha256=policy.sha256)
    io = NativeIntakeParserIO(owner, delivery, leases, policy, storage_factory=storage.factory)
    try:
        yield case, io, leases, delivery
    finally:
        leases.pause()


def read_journal(case, io):
    owner, ids, _, _, _, _, _ = case
    with owner.graph.transaction(ids["tenant"]):
        before = NativeParserCommandBudget(owner.clock)
        before()
        row = owner.graph._one("SELECT payload FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
            (ids["tenant"], io.delivery.job_id))
        before()
        slots = owner.engine.query("SELECT * FROM intake.native_retrieval_inflight_slots ORDER BY reservation_id")
        before()
        rates = owner.engine.query("SELECT * FROM intake.native_retrieval_rate_reservations ORDER BY reservation_id")
        return row["payload"]["attempt_intents"], slots, rates


def assert_input_preserved(case, original):
    current = persisted(case)
    for key in ("intake.intakes", "intake.source_snapshots", "intake.human_corrections",
            "identity.match_cases", "expansion.listings", "workflow.outbox_events"):
        assert current[key] == original[key], key
    run = current["intake.parser_runs"][0]
    assert run["status"] == "RUNNING" and run["parsed_payload"] is None and run["normalized_payload"] is None


@pytest.mark.parametrize("parser_evidence_fields,status", [(None, "SUCCEEDED"), ("partial", "PARTIAL")],
    indirect=["parser_evidence_fields"])
def test_real_paired_renewal_read_journal_and_fixed_child_with_zero_http_budget(parser_io_case, status):
    case, io, leases, _ = parser_io_case
    owner, ids, _, _, _, storage, _ = case
    original = persisted(case)
    calls = len(storage.calls)
    objects = {key: hashlib.sha256(body).hexdigest() for key, (body, _) in storage.objects.items()}
    output = io.read_and_parse()
    assert output.output.status == status and output.output.facts.payloads()["field_confidence"] == {}
    assert output.object_generation == 9 and len(output.intent_ids) == len(set(output.intent_ids)) == 4
    journal, slots, rates = read_journal(case, io)
    assert [item["operation"] for item in journal] == ["gcs_bucket", "gcs_head", "gcs_download", "parser_child"]
    assert all(item["state"] == "FINISHED" and item["process_state"] == "NO_CHILD_REMAINS" for item in journal)
    assert all(item["reservation"]["rate_reservation_id"] is None for item in journal)
    assert journal[-1]["observed"]["sha256"] == output.output.output_sha256
    assert slots == rates == []
    assert [call[0] for call in storage.calls[calls:]] == ["bucket", "head", "download"]
    assert {key: hashlib.sha256(body).hexdigest() for key, (body, _) in storage.objects.items()} == objects
    assert_input_preserved(case, original)
    assert leases._thread is None and leases._proof is None
    completed_journal = journal
    completed_calls = list(storage.calls)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_ATTEMPT_IO_ALREADY_STARTED$"):
        io.read_and_parse()
    journal, slots, rates = read_journal(case, io)
    assert journal == completed_journal and slots == rates == [] and storage.calls == completed_calls
    with owner.graph.transaction(ids["tenant"]):
        state = io._fresh()
        assert state.context.source["requests_per_minute"] == 0


@pytest.mark.parametrize("loss", ["key", "human_role", "source", "registry"])
def test_loss_after_observed_bucket_closes_owned_quota_but_does_not_authorize_using_result(parser_io_case, monkeypatch, loss):
    case, io, _, _ = parser_io_case
    owner, ids, database, _, _, storage, _ = case
    original = persisted(case)
    bucket = storage.bucket_info

    def lose(*args, **kwargs):
        result = bucket(*args, **kwargs)
        if loss == "key":
            monkeypatch.delenv(SIGNING_KEY_ENV)
        elif loss == "registry":
            owner.artifact_registry = None
        else:
            with database.connect() as connection:
                if loss == "human_role":
                    connection.execute("DELETE FROM identity.account_roles WHERE account_id=%s", (ids["actor"],))
                else:
                    connection.execute("UPDATE intake.source_registry SET kill_switch=true WHERE source_id LIKE 'native-parser-%'")
        return result

    monkeypatch.setattr(storage, "bucket_info", lose)
    with pytest.raises((NativeParserUnavailable, NativeIORejected, NativeAssistedEntryConflict, HTTPException, PermissionError)):
        io.read_and_parse()
    journal, slots, rates = read_journal(case, io)
    assert len(journal) == 1 and journal[0]["state"] in {"FINISHED", "ERROR"}
    assert slots == rates == [] and io.completed == {}
    assert_input_preserved(case, original)


def test_injected_unknown_reaping_outcome_retains_admitted_journal_and_quota(parser_io_case, monkeypatch):
    case, io, _, _ = parser_io_case
    storage = case[5]
    original = persisted(case)

    def unknown(*args, **kwargs):
        # A simulated capability failure, not a claim that this test leaks or
        # observes a real unreaped child. The real runner has separate tests.
        raise NativeIOUnreaped("SYNTHETIC_UNREAPED_OUTCOME")

    monkeypatch.setattr(storage, "bucket_info", unknown)
    with pytest.raises(NativeIOUnreaped):
        io.read_and_parse()
    journal, slots, rates = read_journal(case, io)
    assert len(journal) == 1 and journal[0]["state"] == "ADMITTED"
    assert len(slots) == 1 and slots[0]["reservation_id"] == journal[0]["reservation"]["reservation_id"]
    assert rates == []
    assert_input_preserved(case, original)


def test_current_anonymous_storage_slot_blocks_io_without_charging_http_or_deleting_the_slot(parser_io_case):
    case, io, _, _ = parser_io_case
    owner, ids, _, _, _, storage, _ = case
    with owner.graph.transaction(ids["tenant"]):
        state = io._fresh()
        quota = NativeIntakeRetrievalQuota(owner.engine)
        reservation = quota.reserve_storage(state.context.source["source_id"], global_limit=5,
            source_limit=1, before=state.before)
    calls = list(storage.calls)
    try:
        with pytest.raises(NativeRetrievalQuotaExceeded):
            io.read_and_parse()
        journal, slots, rates = read_journal(case, io)
        assert journal == [] and rates == [] and len(slots) == 1
        assert slots[0]["reservation_id"] == reservation.reservation_id and storage.calls == calls
    finally:
        with owner.graph.transaction(ids["tenant"]):
            before = NativeParserCommandBudget(owner.clock)
            quota.release(reservation, process_reaped=True, before=before)


def test_no_provider_operation_can_be_invoked_inside_a_business_sql_root(parser_io_case):
    case, io, _, _ = parser_io_case
    owner, ids, _, _, _, storage, _ = case
    calls = list(storage.calls)
    with owner.graph.transaction(ids["tenant"]):
        state = io._fresh()
        with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_IO_SQL_ROOT_FORBIDDEN$"):
            io._invoke("gcs_bucket", state.context, {}, 0, None)
    assert storage.calls == calls


@pytest.mark.parametrize("bad", ["generation", "kms", "media", "digest"])
def test_generation_metadata_and_content_substitution_cannot_reach_the_parser_child(parser_io_case, monkeypatch, bad):
    case, io, _, _ = parser_io_case
    storage = case[5]
    original = persisted(case)
    if bad == "digest":
        download = storage.download_version_bounded

        def changed(*args, **kwargs):
            body = download(*args, **kwargs)
            return b"x" * len(body)
        monkeypatch.setattr(storage, "download_version_bounded", changed)
    else:
        head = storage.head_version

        def changed(*args, **kwargs):
            metadata = head(*args, **kwargs)
            delta = {"generation": metadata.generation + 1} if bad == "generation" else (
                {"kms_key_version_name": "another-key"} if bad == "kms" else {"media_type": "text/html"})
            return replace(metadata, **delta)
        monkeypatch.setattr(storage, "head_version", changed)
    with pytest.raises((NativeIORejected, NativeAssistedSnapshotProofRejected)):
        io.read_and_parse()
    journal, slots, rates = read_journal(case, io)
    assert journal[-1]["state"] == "ERROR" and all(item["operation"] != "parser_child" for item in journal)
    assert slots == rates == []
    assert_input_preserved(case, original)


def test_unobserved_synthetic_parser_output_cannot_claim_reaping_or_release_its_slot(parser_io_case):
    case, io, _, _ = parser_io_case
    original = persisted(case)
    io.parser = type("MalformedParser", (), {"parse": lambda *args, **kwargs: {"status": "SUCCEEDED"}})()
    with pytest.raises(NativeIOUnreaped, match="^NATIVE_IO_PARSER_COMPLETION_UNPROVEN$"):
        io.read_and_parse()
    journal, slots, rates = read_journal(case, io)
    assert journal[-1]["operation"] == "parser_child" and journal[-1]["state"] == "ADMITTED"
    assert len(slots) == 1 and slots[0]["reservation_id"] == journal[-1]["reservation"]["reservation_id"]
    assert rates == []
    assert_input_preserved(case, original)
