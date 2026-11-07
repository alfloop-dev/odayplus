"""Actual witnessed metadata closure and bounded unknown-commit readback.

Synthetic GCS data runs through one actual owned test child per operation.
No typed historical Job/Intake, bool reaping proof or positive grant is forged.
"""
from contextlib import contextmanager
from uuid import uuid4

import pytest

from modules.listing.application.native_intake_parser_policy import NativeParserConflict
from shared.jobs.queue import JobFenceRejectedError
from tests.integration.test_native_intake_parser_admission_postgresql import persisted
from tests.integration.test_native_intake_parser_io_postgresql import (
    assert_input_preserved,
    read_journal,
)
from tests.integration.test_native_intake_parser_io_postgresql import authority as authority
from tests.integration.test_native_intake_parser_io_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_parser_io_postgresql import identity_case as identity_case
from tests.integration.test_native_intake_parser_io_postgresql import (
    intake_blank_db as intake_blank_db,
)
from tests.integration.test_native_intake_parser_io_postgresql import native_api as native_api
from tests.integration.test_native_intake_parser_io_postgresql import (
    parser_admission_case as parser_admission_case,
)
from tests.integration.test_native_intake_parser_io_postgresql import (
    parser_evidence_fields as parser_evidence_fields,
)
from tests.integration.test_native_intake_parser_io_postgresql import (
    parser_io_case as parser_io_case,
)
from tests.integration.test_native_intake_parser_io_postgresql import parser_realm as parser_realm
from tests.integration.test_native_intake_parser_io_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_parser_io_postgresql import start_case as start_case

pytestmark = pytest.mark.requires_live_env


class _UnknownFinishReply(OSError):
    pass


def finish_fault(case, io, monkeypatch, mode):
    """Raise at the real SQL root exit, before capability confirmation."""
    transaction, finish = io.owner.graph.transaction, io._finish
    state = {"active": False, "roots": 0, "entries": [], "pending": None}
    original = _UnknownFinishReply("Synthetic lost finish response")

    @contextmanager
    def root(tenant):
        targeted = state["active"] and getattr(io.owner.engine._local, "connection", None) is None
        if targeted:
            state["roots"] += 1
        with transaction(tenant):
            yield
            if targeted:
                state["entries"].append(io._finishes.expected(state["pending"]))
                if mode == "rollback_always" or mode == "rollback_first" and state["roots"] == 1:
                    raise original
        if targeted and (mode == "unknown_always" or mode in {"unknown_first", "changed_readback"} and state["roots"] == 1):
            if mode == "changed_readback":
                with case[2].connect() as connection:
                    connection.execute("UPDATE workflow.jobs SET payload=jsonb_set(payload, "
                        "'{attempt_intents,0,observed,sha256}',to_jsonb(%s::text)) WHERE job_id=%s",
                        ("f" * 64, io.delivery.job_id))
            raise original

    def finishing(intent, *, pending):
        state.update(active=True, pending=pending)
        try:
            return finish(intent, pending=pending)
        finally:
            state["active"] = False

    monkeypatch.setattr(io.owner.graph, "transaction", root)
    monkeypatch.setattr(io, "_finish", finishing)
    return state, original


@pytest.mark.parametrize("mode", ["unknown_first", "rollback_first"])
def test_one_fresh_finish_root_reconciles_actual_commit_or_known_rollback_without_io_repetition(
        parser_io_case, monkeypatch, mode):
    case, io, leases, _ = parser_io_case
    original = persisted(case)
    calls = len(case[5].calls)
    state, _ = finish_fault(case, io, monkeypatch, mode)
    result, _ = io._call("gcs_bucket")
    assert result is not None and state["roots"] == 2
    assert len(state["entries"]) == 2 and state["entries"][0] == state["entries"][1]
    journal, slots, rates = read_journal(case, io)
    assert journal == [state["entries"][0]] and slots == rates == []
    assert journal[0]["state"] == "FINISHED" and journal[0]["process_state"] == "NO_CHILD_REMAINS"
    assert io.completed == {"gcs_bucket": journal[0]["intent_id"]}
    assert case[5].calls[calls:] == [("bucket", None, None)]
    assert leases._thread is None and leases._proof is None
    assert_input_preserved(case, original)


@pytest.mark.parametrize("mode,closed", [("unknown_always", True), ("rollback_always", False), ("changed_readback", True)])
def test_two_unconfirmed_finish_roots_withhold_output_and_preserve_the_first_error(
        parser_io_case, monkeypatch, mode, closed):
    case, io, leases, _ = parser_io_case
    original = persisted(case)
    calls = len(case[5].calls)
    state, error = finish_fault(case, io, monkeypatch, mode)
    with pytest.raises(_UnknownFinishReply) as caught:
        io._call("gcs_bucket")
    assert caught.value is error and state["roots"] == 2 and io.completed == {}
    journal, slots, rates = read_journal(case, io)
    assert len(journal) == 1 and journal[0]["state"] == ("FINISHED" if closed else "ADMITTED")
    assert len(slots) == (0 if closed else 1) and rates == []
    assert case[5].calls[calls:] == [("bucket", None, None)]
    assert leases._thread is None and leases._proof is None
    pending = state["pending"]
    assert io._finishes._pending[pending].phase == "UNRESOLVED"
    with pytest.raises(NativeParserConflict):
        io._finishes.facts(pending)
    assert_input_preserved(case, original)


def after_bucket(case, io, leases, monkeypatch, action):
    original = case[5].bucket_info

    def invoke(*args, **kwargs):
        result = original(*args, **kwargs)
        # Join this operation's heartbeat before our explicit SQL fixture fault.
        leases.pause()
        action()
        return result

    monkeypatch.setattr(case[5], "bucket_info", invoke)


def test_expired_business_lease_allows_only_witnessed_metadata_close_and_no_positive_continuation(
        parser_io_case, monkeypatch):
    case, io, leases, delivery = parser_io_case
    original = persisted(case)

    def expire():
        with case[2].connect() as connection:
            assert connection.execute("UPDATE workflow.jobs SET lease_expires_at=clock_timestamp()-interval '1 second' "
                "WHERE job_id=%s AND status='RUNNING'", (delivery.job_id,)).rowcount == 1

    after_bucket(case, io, leases, monkeypatch, expire)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_DUAL_LEASE_NOT_OWNED$"):
        io._call("gcs_bucket")
    journal, slots, rates = read_journal(case, io)
    assert len(journal) == 1 and journal[0]["state"] == "FINISHED"
    assert slots == rates == [] and io.completed == {}
    assert_input_preserved(case, original)


@pytest.mark.parametrize("fault", ["physical_fence", "own_slot_missing", "own_slot_changed"])
def test_changed_current_claim_or_exact_own_slot_cannot_be_closed_by_a_reaped_operation(
        parser_io_case, monkeypatch, fault):
    case, io, leases, delivery = parser_io_case
    original = persisted(case)
    captured = {}

    def damage():
        journal, slots, _ = read_journal(case, io)
        captured["intent"] = journal[0]
        assert len(slots) == 1
        with case[2].connect() as connection:
            if fault == "physical_fence":
                assert connection.execute("UPDATE odp_runtime.durable_jobs SET fence_token=fence_token+1 "
                    "WHERE job_id=%s", (delivery.job_id,)).rowcount == 1
            elif fault == "own_slot_missing":
                assert connection.execute("DELETE FROM intake.native_retrieval_inflight_slots WHERE reservation_id=%s",
                    (slots[0]["reservation_id"],)).rowcount == 1
            else:
                assert connection.execute("UPDATE intake.native_retrieval_inflight_slots SET expires_at=expires_at+interval '1 second' "
                    "WHERE reservation_id=%s", (slots[0]["reservation_id"],)).rowcount == 1

    after_bucket(case, io, leases, monkeypatch, damage)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_WITNESSED_FINISH_REJECTED$"):
        io._call("gcs_bucket")
    journal, slots, rates = read_journal(case, io)
    assert journal == [captured["intent"]] and journal[0]["state"] == "ADMITTED"
    assert len(slots) == (0 if fault == "own_slot_missing" else 1)
    assert rates == [] and io.completed == {}
    assert_input_preserved(case, original)


def test_closing_own_witnessed_slot_never_cleans_an_unrelated_expired_slot(parser_io_case, monkeypatch):
    case, io, leases, _ = parser_io_case
    unrelated = str(uuid4())

    def insert():
        with case[2].connect() as connection:
            connection.execute("INSERT INTO intake.native_retrieval_inflight_slots "
                "(reservation_id,source_id,admitted_at,expires_at) VALUES (%s,%s,clock_timestamp()-interval '2 minutes',"
                "clock_timestamp()-interval '1 minute')", (unrelated, "synthetic-unrelated-source"))

    after_bucket(case, io, leases, monkeypatch, insert)
    io._call("gcs_bucket")
    journal, slots, rates = read_journal(case, io)
    assert len(journal) == 1 and journal[0]["state"] == "FINISHED"
    assert len(slots) == 1 and str(slots[0]["reservation_id"]) == unrelated
    assert slots[0]["source_id"] == "synthetic-unrelated-source" and rates == []
