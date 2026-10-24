"""Real physical cells acquired once per complete successful manual proof.

Both comparison paths use genuine API/manual history and original deadlines.
Direct row-view tests below are only factual transport/lifetime observations,
never a substitute for the full history verifier or a current authority grant.
"""
from dataclasses import replace
from uuid import uuid4

import pytest

from modules.listing.application.native_intake_assisted_entry_history import (
    NativeAssistedEntryHistory,
)
from modules.listing.application.native_intake_assisted_entry_history_rows import (
    _SETTINGS_PHYSICAL_SQL,
    history_physical,
    read_success_history_rows,
)
from modules.listing.application.native_intake_assisted_entry_request_proof import _object
from modules.listing.application.native_intake_parser_prerequisites import NativeParserCommandBudget
from modules.listing.application.native_intake_pending_commands import NativeIntakeCommandBudget
from shared.infrastructure.persistence.postgresql import PostgresEngine
from shared.jobs.queue import JobFenceRejectedError
from tests.integration.test_native_intake_producer_discovery_postgresql import (
    authority as authority,
)
from tests.integration.test_native_intake_producer_discovery_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_producer_discovery_postgresql import (
    identity_case as identity_case,
)
from tests.integration.test_native_intake_producer_discovery_postgresql import (
    intake_blank_db as intake_blank_db,
)
from tests.integration.test_native_intake_producer_discovery_postgresql import (
    native_api as native_api,
)
from tests.integration.test_native_intake_producer_discovery_postgresql import (
    parser_admission_case as parser_admission_case,
)
from tests.integration.test_native_intake_producer_discovery_postgresql import (
    parser_realm as parser_realm,
)
from tests.integration.test_native_intake_producer_discovery_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_producer_discovery_postgresql import (
    start_case as start_case,
)
from tests.integration.test_native_intake_terminal_evidence_postgresql import _persisted

pytestmark = pytest.mark.requires_live_env


def _manual(case):
    owner, ids = case[:2]
    with owner.graph.transaction(ids["tenant"]):
        job = owner.engine.query_one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
            (ids["tenant"], case[3]["job_id"]))
        delivery = owner.queue.get(job["job_id"])
    assert delivery is not None and job["status"] == "SUCCEEDED"
    return job, delivery


def _observe(case, patch, corrupt=None):
    calls = []
    original = PostgresEngine.query_one

    def query(engine, statement, params=()):
        result = original(engine, statement, params)
        if engine is case[0].engine:
            calls.append((statement, tuple(params)))
            if statement == _SETTINGS_PHYSICAL_SQL and corrupt is not None:
                return corrupt(result)
        return result

    patch.setattr(PostgresEngine, "query_one", query)
    return calls


def test_complete_legacy_and_native_history_match_with_two_fewer_physical_reads(parser_admission_case, monkeypatch):
    case = parser_admission_case
    owner, ids = case[:2]
    job, delivery = _manual(case)
    original = _persisted(case)
    results, traces = [], []
    for budget_type, lock_job in ((NativeIntakeCommandBudget, False),
            (NativeParserCommandBudget, False), (NativeParserCommandBudget, True)):
        with owner.graph.transaction(ids["tenant"]):
            budget = budget_type(owner.clock)
            bounds = budget.deadline, budget._monotonic_deadline
            root = owner.engine._local.root_transaction
            with monkeypatch.context() as patch:
                calls = _observe(case, patch)
                results.append(NativeAssistedEntryHistory(owner.graph).committed_result_with_command(
                    delivery, ids["tenant"], 1, before=budget, lock_job=lock_job))
            assert owner.engine._local.root_transaction is root
            assert (budget.deadline, budget._monotonic_deadline) == bounds
            traces.append(calls)
    assert results[0] is not None and results[0] == results[1] == results[2]
    assert results[0][2] == job
    job_sql = "SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?"
    job_params = ids["tenant"], job["job_id"]
    for calls in traces[:2]:
        assert [(s, p) for s, p in calls if s in {job_sql, job_sql + " FOR SHARE"}
            and p == job_params] == [(job_sql, job_params)]
    assert [(s, p) for s, p in traces[2] if s in {job_sql, job_sql + " FOR SHARE"}
        and p == job_params] == [(job_sql + " FOR SHARE", job_params)]
    # Independent actual queue SQL and exact genuine manual job parameters.
    get_sql = "SELECT * FROM durable_jobs WHERE job_id = ?"
    assert sum(s == get_sql and p == (job["job_id"],) for s, p in traces[0]) == 2
    assert not any(s == get_sql and p == (job["job_id"],) for s, p in traces[1])
    assert not any(s == _SETTINGS_PHYSICAL_SQL for s, _ in traces[0])
    assert [p for s, p in traces[1] if s == _SETTINGS_PHYSICAL_SQL] == [(job["job_id"], ids["tenant"])]
    # A reference to a missing business row never becomes a successful proof,
    # including after a genuine result and when the parser requests its lock.
    missing = replace(delivery, job_id=str(uuid4()))
    for lock_job in (False, True):
        with owner.graph.transaction(ids["tenant"]):
            assert NativeAssistedEntryHistory(owner.graph).committed_result_with_command(
                missing, ids["tenant"], 1, before=NativeParserCommandBudget(owner.clock),
                lock_job=lock_job) is None
    assert _persisted(case) == original


def test_changed_queue_reader_keeps_its_original_refusal_and_live_read(parser_admission_case, monkeypatch):
    case = parser_admission_case
    owner, ids = case[:2]
    job, delivery = _manual(case)
    original = _persisted(case)
    get, invoked, sentinel = owner.queue.get, [], RuntimeError("original physical read refusal")

    def changed(job_id):
        if job_id == job["job_id"]:
            invoked.append(job_id)
            raise sentinel
        return get(job_id)

    monkeypatch.setattr(owner.queue, "get", changed)
    calls = _observe(case, monkeypatch)
    with pytest.raises(RuntimeError) as error:
        with owner.graph.transaction(ids["tenant"]):
            NativeAssistedEntryHistory(owner.graph).committed_result_with_command(
                delivery, ids["tenant"], 1, before=NativeParserCommandBudget(owner.clock))
    assert error.value is sentinel and invoked == [job["job_id"]]
    assert not any(s == _SETTINGS_PHYSICAL_SQL for s, _ in calls)
    assert _persisted(case) == original


@pytest.mark.parametrize("damage", ["missing_column", "foreign_job"])
def test_corrupt_physical_transport_refuses_without_get_or_fallback(parser_admission_case, monkeypatch, damage):
    case = parser_admission_case
    owner, ids = case[:2]
    job, delivery = _manual(case)
    original = _persisted(case)

    def corrupt(value):
        row = dict(value)
        if damage == "missing_column":
            del row["history_physical_max_retries"]
        else:
            row["history_physical_job_id"] = str(uuid4())
        return row

    calls = _observe(case, monkeypatch, corrupt)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_ASSISTED_TERMINAL_ROWS_REJECTED$"):
        with owner.graph.transaction(ids["tenant"]):
            NativeAssistedEntryHistory(owner.graph).committed_result_with_command(
                delivery, ids["tenant"], 1, before=NativeParserCommandBudget(owner.clock))
    assert [p for s, p in calls if s == _SETTINGS_PHYSICAL_SQL] == [(job["job_id"], ids["tenant"])]
    assert not any(s == "SELECT * FROM durable_jobs WHERE job_id = ?" and p == (job["job_id"],)
        for s, p in calls)
    assert _persisted(case) == original


def _rows(case, job, budget):
    return read_success_history_rows(case[0].graph, job, _object(job["payload"]), _object(job["result"]), budget)


def test_fact_view_refuses_after_the_actual_original_root_is_released(parser_admission_case):
    case = parser_admission_case
    owner, ids = case[:2]
    job, actual = _manual(case)
    original = _persisted(case)
    with owner.graph.transaction(ids["tenant"]):
        root = owner.engine._local.root_transaction
        rows = _rows(case, job, NativeParserCommandBudget(owner.clock))
        closed, observed = history_physical(rows, job)
        assert closed and observed == actual
    with owner.graph.transaction(ids["tenant"]):
        assert owner.engine._local.root_transaction is not root
        with pytest.raises(JobFenceRejectedError, match="^NATIVE_ASSISTED_TERMINAL_ROWS_REJECTED$"):
            history_physical(rows, job)
    assert _persisted(case) == original


def test_each_invocation_reloads_real_same_root_physical_changes_and_rollback(parser_admission_case):
    case = parser_admission_case
    owner, ids = case[:2]
    job, actual = _manual(case)
    original = _persisted(case)
    with owner.graph.transaction(ids["tenant"]):
        budget = NativeParserCommandBudget(owner.clock)
        closed, first = history_physical(_rows(case, job, budget), job)
        assert closed and first == actual
        first.payload["local_only"] = "not shared"
        budget()
        owner.engine.execute("SAVEPOINT history_physical_negative")
        budget()
        assert owner.engine.execute("UPDATE durable_jobs SET version=version+1 WHERE job_id=?",
            (job["job_id"],)).rowcount == 1
        closed, changed = history_physical(_rows(case, job, budget), job)
        assert closed and changed == replace(actual, version=actual.version + 1)
        assert "local_only" not in changed.payload
        budget()
        owner.engine.execute("ROLLBACK TO SAVEPOINT history_physical_negative")
        closed, restored = history_physical(_rows(case, job, budget), job)
        assert closed and restored == actual
    assert _persisted(case) == original
