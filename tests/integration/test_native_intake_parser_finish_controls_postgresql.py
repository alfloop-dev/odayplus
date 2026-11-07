"""Finish invocation controls through genuine canonical parser SQL fixtures.

GCS is the existing explicit synthetic provider. Where invoked, its fixture
uses an actual owned fixed storage child and the real lifecycle kernel. Spies
only forward calls; they never issue completion facts or rewrite provenance.
The heartbeat join fault is simulated routing, not proof of a stuck OS thread.
"""
from copy import deepcopy

import pytest

from modules.external_data.application import native_intake_process_lifecycle as lifecycle
from modules.external_data.application.native_intake_process import (
    NativeIntakeProcessRunner,
    NativeIORejected,
    NativeIOUnreaped,
)
from modules.listing.application.native_intake_parser_policy import NativeParserConflict
from shared.jobs.queue import JobStatus
from tests.integration.test_native_intake_parser_admission_postgresql import persisted
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
from tests.integration.test_native_intake_parser_io_postgresql import read_journal
from tests.integration.test_native_intake_parser_io_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_parser_io_postgresql import start_case as start_case

pytestmark = pytest.mark.requires_live_env


def _forwarding_spy(monkeypatch, target, name):
    original = getattr(target, name)
    calls = {"entered": [], "returned": [], "errors": []}

    def forward(*args, **kwargs):
        calls["entered"].append((args, kwargs))
        try:
            result = original(*args, **kwargs)
        except BaseException as error:
            calls["errors"].append(error)
            raise
        calls["returned"].append(result)
        return result

    monkeypatch.setattr(target, name, forward)
    return calls


def _watch_controls(case, io, monkeypatch):
    """Observe actual control flow, including a separately read committed intent."""
    trace = {name: _forwarding_spy(monkeypatch, target, method) for name, target, method in (
        ("capture", lifecycle, "_observe_native_process"),
        ("consume", lifecycle, "_consume_native_process_completion"),
        ("runner", NativeIntakeProcessRunner, "run"),
        ("attach", io._finishes, "attach"),
        ("not_invoked", io._finishes, "not_invoked"),
        ("observed", io._finishes, "observed"),
        ("finish", io, "_finish"),
    )}
    intent = io._intent
    trace["admissions"] = []

    def acknowledged(*args, **kwargs):
        result = intent(*args, **kwargs)
        assert getattr(io.owner.engine._local, "connection", None) is None
        record = io._finishes._live[io._active_operation]
        assert record.phase == "OPEN" and record.observation is None
        journal, slots, rates = read_journal(case, io)
        assert journal == [result[0]] and journal[0]["state"] == "ADMITTED"
        assert journal[0]["operation"] == "gcs_bucket"
        assert len(slots) == 1
        assert slots[0]["reservation_id"] == journal[0]["reservation"]["reservation_id"]
        assert journal[0]["reservation"]["rate_reservation_id"] is None and rates == []
        trace["admissions"].append(deepcopy((journal, slots, rates)))
        return result

    monkeypatch.setattr(io, "_intent", acknowledged)
    return trace


def _assert_retained(case, io, trace):
    assert len(trace["admissions"]) == 1
    journal, slots, rates = read_journal(case, io)
    assert (journal, slots, rates) == trace["admissions"][0]
    assert len(journal) == len(slots) == 1 and journal[0]["state"] == "ADMITTED"
    assert slots[0]["reservation_id"] == journal[0]["reservation"]["reservation_id"]
    assert rates == [] and io.completed == {} and io.metadata is None


def _assert_canonical_preserved(case, io, original, objects):
    current = persisted(case)
    for table in ("intake.intakes", "intake.source_snapshots", "intake.human_corrections",
            "intake.parser_runs", "identity.match_cases", "expansion.listings",
            "workflow.outbox_events", "audit.audit_events"):
        assert current[table] == original[table], table
    assert io.completed == {} and io.metadata is None
    assert case[5].objects == objects


def _assert_no_adapter_capture(trace, invocation):
    assert invocation["entered"] == []
    for name in ("capture", "attach", "runner", "consume", "observed"):
        assert trace[name]["entered"] == [], name


def test_owned_pre_adapter_start_error_closes_only_its_acknowledged_intent(
        parser_io_case, monkeypatch):
    case, io, leases, _ = parser_io_case
    original, objects = persisted(case), deepcopy(case[5].objects)
    trace = _watch_controls(case, io, monkeypatch)
    invocation = _forwarding_spy(monkeypatch, io, "_invoke")
    error = RuntimeError("Synthetic owned pre-adapter lease start failure")
    starts = []

    def fail_start():
        assert len(trace["admissions"]) == 1
        assert io._finishes._live[io._active_operation].phase == "OPEN"
        starts.append(io._active_operation)
        raise error

    monkeypatch.setattr(leases, "start", fail_start)
    with pytest.raises(RuntimeError) as caught:
        io.read_and_parse()
    assert caught.value is error and starts == [io._active_operation]
    _assert_no_adapter_capture(trace, invocation)
    assert trace["not_invoked"]["entered"] == [((io._active_operation, error), {})]
    assert len(trace["not_invoked"]["returned"]) == 1
    assert len(trace["finish"]["entered"]) == len(trace["finish"]["returned"]) == 1
    assert trace["finish"]["errors"] == []
    journal, slots, rates = read_journal(case, io)
    assert journal == trace["finish"]["returned"] and len(journal) == 1
    assert journal[0]["state"] == "ERROR" and journal[0]["process_state"] == "NO_CHILD_REMAINS"
    assert journal[0]["error_class"] == "RuntimeError" and journal[0]["observed"] is None
    assert "failure_observation" not in journal[0]
    admitted = trace["admissions"][0][0][0]
    assert {key: journal[0][key] for key in admitted if key != "state"} == {
        key: value for key, value in admitted.items() if key != "state"}
    assert slots == rates == [] and case[5].calls == original["storage_calls"]
    assert leases._thread is None and leases._proof is None
    _assert_canonical_preserved(case, io, original, objects)


@pytest.mark.parametrize("status", [401, 403])
def test_adapter_auth_rejection_with_zero_actual_runs_cannot_mint_completion(
        parser_io_case, monkeypatch, status):
    case, io, leases, _ = parser_io_case
    original, objects = persisted(case), deepcopy(case[5].objects)
    trace = _watch_controls(case, io, monkeypatch)
    error = NativeIORejected("NATIVE_GCS_HTTP_REJECTED", status=status)
    invocations = []

    def reject_before_runner(operation, context, arguments, deadline, data):
        assert getattr(io.owner.engine._local, "connection", None) is None
        assert operation == "gcs_bucket" and arguments == {} and data is None
        record = io._finishes._live[io._active_operation]
        assert record.phase == "INVOKING" and record.observation is not None
        assert trace["attach"]["entered"] == [((io._active_operation, record.observation), {})]
        invocations.append(error)
        raise error

    # Override only this IO's adapter entry, before the fixture's fixed runner.
    # Neither the synthetic provider nor the lifecycle kernel is made to deny.
    monkeypatch.setattr(io, "_invoke", reject_before_runner)
    with pytest.raises(NativeIOUnreaped, match="^NATIVE_IO_PARSER_COMPLETION_UNPROVEN$") as caught:
        io.read_and_parse()
    assert invocations == [error] and caught.value is not error
    assert len(trace["capture"]["entered"]) == len(trace["attach"]["entered"]) == 1
    assert trace["runner"]["entered"] == [] and trace["not_invoked"]["entered"] == []
    assert len(trace["observed"]["entered"]) == 1
    assert trace["observed"]["entered"][0][1] == {"error": error}
    assert len(trace["consume"]["entered"]) == len(trace["consume"]["errors"]) == 1
    refusal = trace["consume"]["errors"][0]
    assert type(refusal) is lifecycle.NativeProcessCompletionUnavailable
    assert str(refusal) == "NATIVE_PROCESS_COMPLETION_UNAVAILABLE"
    assert caught.value.__cause__ is refusal and refusal.__context__ is error
    assert trace["consume"]["returned"] == trace["observed"]["returned"] == []
    assert trace["finish"]["entered"] == [] and io._finishes._pending == {}
    _assert_retained(case, io, trace)
    assert case[5].calls == original["storage_calls"]
    assert leases._thread is None and leases._proof is None
    _assert_canonical_preserved(case, io, original, objects)


def test_simulated_join_failure_after_real_bucket_child_withholds_sql_finish(
        parser_io_case, monkeypatch):
    case, io, leases, _ = parser_io_case
    original, objects = persisted(case), deepcopy(case[5].objects)
    trace = _watch_controls(case, io, monkeypatch)
    bucket, pause = case[5].bucket_info, leases.pause
    error = NativeParserConflict("NATIVE_PARSER_HEARTBEAT_NOT_JOINED")
    owned, failures = {}, []

    def simulated_join_failure():
        assert leases._thread is owned["thread"]
        failures.append(error)
        # Deliberately simulate the refusal; do not pretend an OS join failed.
        raise error

    def bucket_then_arm_join_failure(*args, **kwargs):
        result = bucket(*args, **kwargs)
        assert len(trace["runner"]["entered"]) == len(trace["runner"]["returned"]) == 1
        assert trace["runner"]["errors"] == []
        owned["thread"] = leases._thread
        assert owned["thread"] is not None and owned["thread"].ident is not None
        assert case[5].calls == [*original["storage_calls"], ("bucket", None, None)]
        monkeypatch.setattr(leases, "pause", simulated_join_failure)
        return result

    monkeypatch.setattr(case[5], "bucket_info", bucket_then_arm_join_failure)
    try:
        with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_HEARTBEAT_NOT_JOINED$") as caught:
            io.read_and_parse()
        assert caught.value is error and failures == [error]
        assert trace["consume"]["returned"] == ["REAPED"]
        assert trace["consume"]["errors"] == [] and trace["not_invoked"]["entered"] == []
        assert len(trace["observed"]["returned"]) == 1 and trace["finish"]["entered"] == []
        pending = trace["observed"]["returned"][0]
        assert io._finishes._pending[pending].phase == "PENDING"
        assert leases._thread is owned["thread"]
        _assert_retained(case, io, trace)
        assert case[5].calls == [*original["storage_calls"], ("bucket", None, None)]
        _assert_canonical_preserved(case, io, original, objects)
    finally:
        # Test-owned cleanup only, after the failure assertions. This cannot
        # supply evidence that the product joined before refusing its finish.
        monkeypatch.setattr(leases, "pause", pause)
        pause()
        if "thread" in owned:
            assert not owned["thread"].is_alive()
        assert leases._thread is None and leases._proof is None


def test_actual_expired_physical_lease_refuses_finish_with_same_worker_and_fence(
        parser_io_case, monkeypatch):
    case, io, leases, delivery = parser_io_case
    original, objects = persisted(case), deepcopy(case[5].objects)
    trace = _watch_controls(case, io, monkeypatch)
    bucket = case[5].bucket_info
    expired = {}

    def bucket_then_expire_own_physical_lease(*args, **kwargs):
        result = bucket(*args, **kwargs)
        assert len(trace["runner"]["entered"]) == len(trace["runner"]["returned"]) == 1
        assert trace["runner"]["errors"] == []
        thread = leases._thread
        assert thread is not None and thread.ident is not None
        leases.pause()
        assert not thread.is_alive() and leases._thread is None and leases._proof is None
        assert getattr(io.owner.engine._local, "connection", None) is None
        before = persisted(case)
        with case[2].connect() as connection:
            changed = connection.execute(
                "UPDATE odp_runtime.durable_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' "
                "WHERE job_id=%s AND status=%s AND locked_by=%s AND fence_token=%s "
                "AND lease_expires_at>clock_timestamp() "
                "RETURNING job_id,locked_by,fence_token,lease_expires_at<clock_timestamp()",
                (delivery.job_id, JobStatus.RUNNING.value, delivery.locked_by, delivery.fence_token))
            assert changed.rowcount == 1
            assert changed.fetchone() == (delivery.job_id, delivery.locked_by, delivery.fence_token, True)
        after = persisted(case)
        for table in before:
            if table != "delivery":
                assert after[table] == before[table], table
        before_jobs = {row["job_id"]: row for row in before["delivery"]}
        after_jobs = {row["job_id"]: row for row in after["delivery"]}
        assert after_jobs.keys() == before_jobs.keys()
        for job_id, row in before_jobs.items():
            if job_id == delivery.job_id:
                assert row["lease_expires_at"] != after_jobs[job_id]["lease_expires_at"]
                assert {key: value for key, value in after_jobs[job_id].items() if key != "lease_expires_at"} == {
                    key: value for key, value in row.items() if key != "lease_expires_at"}
            else:
                assert after_jobs[job_id] == row
        expired["snapshot"] = after
        return result

    monkeypatch.setattr(case[5], "bucket_info", bucket_then_expire_own_physical_lease)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_WITNESSED_FINISH_REJECTED$") as caught:
        io.read_and_parse()
    assert trace["consume"]["returned"] == ["REAPED"] and trace["consume"]["errors"] == []
    assert trace["not_invoked"]["entered"] == []
    assert len(trace["finish"]["entered"]) == 1 and trace["finish"]["returned"] == []
    assert trace["finish"]["errors"] == [caught.value]
    pending = trace["observed"]["returned"][0]
    assert io._finishes._pending[pending].phase == "UNRESOLVED"
    _assert_retained(case, io, trace)
    assert persisted(case) == expired["snapshot"]
    assert case[5].calls == [*original["storage_calls"], ("bucket", None, None)]
    assert leases._thread is None and leases._proof is None
    _assert_canonical_preserved(case, io, original, objects)


def test_pre_adapter_unreaped_error_keeps_priority_over_owned_control_cleanup(
        parser_io_case, monkeypatch):
    case, io, leases, _ = parser_io_case
    original, objects = persisted(case), deepcopy(case[5].objects)
    trace = _watch_controls(case, io, monkeypatch)
    invocation = _forwarding_spy(monkeypatch, io, "_invoke")
    error = NativeIOUnreaped("NATIVE_IO_CHILD_UNREAPED")
    starts = []

    def unresolved_start():
        # Simulated unresolved outcome, not an assertion that a child exists.
        assert len(trace["admissions"]) == 1
        assert io._finishes._live[io._active_operation].phase == "OPEN"
        starts.append(io._active_operation)
        raise error

    monkeypatch.setattr(leases, "start", unresolved_start)
    with pytest.raises(NativeIOUnreaped) as caught:
        io.read_and_parse()
    assert caught.value is error and starts == [io._active_operation]
    _assert_no_adapter_capture(trace, invocation)
    assert trace["not_invoked"]["entered"] == trace["finish"]["entered"] == []
    assert io._finishes._pending == {}
    _assert_retained(case, io, trace)
    assert case[5].calls == original["storage_calls"]
    assert leases._thread is None and leases._proof is None
    _assert_canonical_preserved(case, io, original, objects)
