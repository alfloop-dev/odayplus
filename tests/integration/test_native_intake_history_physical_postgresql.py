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
            if _SETTINGS_PHYSICAL_SQL in statement and corrupt is not None:
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
    job_params = ids["tenant"], job["job_id"]
    for calls, shared in zip(traces, (False, False, True), strict=True):
        job_reads = [s for s, p in calls if "FROM workflow.jobs" in s and p[:2] == job_params]
        assert len(job_reads) == 1
        # The same full proof now opts into the original graph owner's fixed
        # bounded acquisition; neither a second read nor a missing lock passes.
        assert job_reads[0].startswith("WITH facts AS MATERIALIZED (")
        assert "octet_length(row_to_json(p)::text)" in job_reads[0]
        assert "_odp_native_allowed" in job_reads[0]
        assert (" FOR SHARE" in job_reads[0]) is shared
    # Independent actual queue SQL and exact genuine manual job parameters.
    get_sql = "SELECT * FROM durable_jobs WHERE job_id = ?"
    assert sum(get_sql in s and p[:1] == (job["job_id"],) for s, p in traces[0]) == 2
    assert not any(get_sql in s and p[:1] == (job["job_id"],) for s, p in traces[1])
    assert not any(_SETTINGS_PHYSICAL_SQL in s for s, _ in traces[0])
    assert [p[:2] for s, p in traces[1] if _SETTINGS_PHYSICAL_SQL in s] == [(job["job_id"], ids["tenant"])]
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
    assert not any(_SETTINGS_PHYSICAL_SQL in s for s, _ in calls)
    assert _persisted(case) == original


@pytest.mark.parametrize("budget_type", [NativeIntakeCommandBudget, NativeParserCommandBudget])
@pytest.mark.parametrize("column", ["payload_json", "error_message"])
def test_original_manual_proof_bounds_physical_cells_before_driver_acquisition(
        parser_admission_case, monkeypatch, budget_type, column):
    from modules.listing.application.native_intake_assisted_entry_policy import (
        NativeAssistedEntryConflict,
    )
    from modules.listing.application.native_intake_parser_policy import NativeParserConflict

    case = parser_admission_case
    owner, ids = case[:2]
    job, delivery = _manual(case)
    with owner.graph.transaction(ids["tenant"]):
        expression = ("payload_json || jsonb_build_object('oversized',repeat('店',?))"
            if column == "payload_json" else "repeat('店',?)")
        owner.engine.execute(f"UPDATE durable_jobs SET {column}={expression} WHERE job_id=?",
            (4 * 1024 * 1024 // 3 + 1, job["job_id"]))
    previous = _persisted(case)
    query_one, acquired = PostgresEngine.query_one, []

    def observe_actual_driver_result(engine, statement, params=()):
        row = query_one(engine, statement, params)
        if engine is owner.engine and "durable_jobs" in statement and job["job_id"] in params and row is not None:
            keys = [key for key in (column, "history_physical_" + column) if key in row]
            if keys:
                acquired.append(all(row[key] is None for key in keys))
        return row

    monkeypatch.setattr(PostgresEngine, "query_one", observe_actual_driver_result)
    with owner.graph.transaction(ids["tenant"]):
        with pytest.raises((JobFenceRejectedError, NativeAssistedEntryConflict, NativeParserConflict)):
            NativeAssistedEntryHistory(owner.graph).committed_result_with_command(
                delivery, ids["tenant"], 1, before=budget_type(owner.clock))
    assert acquired == [True]
    assert _persisted(case) == previous


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
    code = "NATIVE_ACQUISITION_TRANSPORT_REJECTED" if damage == "missing_column" else "NATIVE_ASSISTED_TERMINAL_ROWS_REJECTED"
    with pytest.raises(JobFenceRejectedError, match="^" + code + "$"):
        with owner.graph.transaction(ids["tenant"]):
            NativeAssistedEntryHistory(owner.graph).committed_result_with_command(
                delivery, ids["tenant"], 1, before=NativeParserCommandBudget(owner.clock))
    assert [p[:2] for s, p in calls if _SETTINGS_PHYSICAL_SQL in s] == [(job["job_id"], ids["tenant"])]
    assert not any("SELECT * FROM durable_jobs WHERE job_id = ?" in s and p[:1] == (job["job_id"],)
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



def test_one_actual_root_charges_whole_batches_and_cannot_reset_when_exhausted(parser_admission_case, monkeypatch):
    """Transport-only: two legal groups per row, cumulative actual SQL bytes."""
    owner, ids = parser_admission_case[:2]
    job, _ = _manual(parser_admission_case)
    from shared.infrastructure.persistence.postgresql import NATIVE_ACQUISITION_ROOT_MAX_BYTES

    columns, groups = ("n", "a", "b"), (("n", "a"), ("b",))
    statement = ("SELECT n,repeat('店',800000) AS a,repeat('T',2400000) AS b "
        "FROM generate_series(1,2) AS n ORDER BY n DESC")
    original_query, observed = PostgresEngine.query, []

    def observe(engine, sql, params=()):
        rows = original_query(engine, sql, params)
        if engine is owner.engine and statement in sql:
            observed.append((len(rows), rows[0]["_odp_native_bytes"],
                rows[0]["_odp_native_allowed"], all(row[k] is None for row in rows for k in columns)))
        return rows

    monkeypatch.setattr(PostgresEngine, "query", observe)
    with owner.graph.transaction(ids["tenant"]):
        root = owner.engine._local.root_transaction
        NativeIntakeCommandBudget(owner.clock)
        assert owner.engine.enable_native_acquisition()[2] == NATIVE_ACQUISITION_ROOT_MAX_BYTES
        for index in range(3):
            rows = owner.engine.native_query(statement, columns=columns, groups=groups,
                order_by=(("n", True),))
            assert [r["n"] for r in rows] == [2, 1]
            assert all(len(r["a"]) == 800000 and len(r["b"]) == 2400000 for r in rows)
            assert all(set(r) == set(columns) for r in rows)
            used = observed[-1][1]
            assert 8 * 1024 * 1024 < used < 10 * 1024 * 1024
            assert owner.engine.enable_native_acquisition()[2] == NATIVE_ACQUISITION_ROOT_MAX_BYTES - used * (index + 1)
            del rows
        with pytest.raises(JobFenceRejectedError, match="^NATIVE_ACQUISITION_LIMIT_OR_TRANSPORT_REJECTED$"):
            owner.engine.native_query(statement, columns=columns, groups=groups)
        assert len(observed) == 4 and observed[-1][0] == 2
        assert observed[-1][2:] == (False, True)  # Entire batch suppressed, never truncated.
        assert owner.engine.enable_native_acquisition()[2] == 0
        with owner.engine.transaction():
            NativeParserCommandBudget(owner.clock)
            assert owner.engine._local.root_transaction is root
            assert owner.engine.native_acquisition_active()
            assert owner.engine.enable_native_acquisition()[2] == 0
            with pytest.raises(JobFenceRejectedError, match="^NATIVE_ACQUISITION_ROOT_EXHAUSTED$"):
                owner.queue.get(job["job_id"])
        assert len(observed) == 4
    with owner.graph.transaction(ids["tenant"]):
        assert owner.engine._local.root_transaction is not root
        assert not owner.engine.native_acquisition_active()
        assert owner.queue.get(job["job_id"]) is not None
        NativeParserCommandBudget(owner.clock)
        assert owner.engine.enable_native_acquisition()[2] == NATIVE_ACQUISITION_ROOT_MAX_BYTES


def test_native_acquisition_preserves_null_missing_order_and_savepoint_accounting(parser_admission_case):
    owner, ids = parser_admission_case[:2]
    from shared.infrastructure.persistence.postgresql import NATIVE_ACQUISITION_ROOT_MAX_BYTES

    statement = ("SELECT n,NULL::jsonb AS absent,'null'::jsonb AS json_null,"
        "jsonb_build_object('n',n) AS payload FROM generate_series(1,3) AS n ORDER BY n DESC")
    columns = ("n", "absent", "json_null", "payload")
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_ACQUISITION_CURRENT_ROOT_REQUIRED$"):
        owner.engine.native_query("SELECT 1 AS n", columns=("n",))
    with owner.graph.transaction(ids["tenant"]):
        expected = owner.engine.query(statement)
        NativeParserCommandBudget(owner.clock)
        root = owner.engine._local.root_transaction
        owner.engine.execute("SAVEPOINT native_transport_accounting")
        rows = owner.engine.native_query(statement, columns=columns, order_by=(("n", True),))
        assert rows == expected and [row["n"] for row in rows] == [3, 2, 1]
        remaining = owner.engine.enable_native_acquisition()[2]
        charged = NATIVE_ACQUISITION_ROOT_MAX_BYTES - remaining
        assert charged > 0
        owner.engine.execute("ROLLBACK TO SAVEPOINT native_transport_accounting")
        with owner.engine.transaction():
            NativeIntakeCommandBudget(owner.clock)
            assert owner.engine._local.root_transaction is root
            assert owner.engine.enable_native_acquisition()[2] == remaining
            assert owner.engine.native_query(statement, columns=columns, order_by=(("n", True),)) == expected
        assert owner.engine.enable_native_acquisition()[2] == remaining - charged
        for one in (False, True):
            assert owner.engine.native_query("SELECT NULL::text AS value WHERE false",
                columns=("value",), one=one) == (None if one else [])
            assert owner.engine.enable_native_acquisition()[2] == remaining - charged
        assert owner.engine.native_query("SELECT NULL::text AS value", columns=("value",), one=True) == {"value": None}
        assert owner.engine.enable_native_acquisition()[2] < remaining - charged


@pytest.mark.parametrize("failure", ["original_refusal", "normalization", "missing_cell", "extra_cell", "zero_bytes"])
def test_failed_native_acquisition_never_refunds_its_reservation(parser_admission_case, monkeypatch, failure):
    owner, ids = parser_admission_case[:2]
    from shared.infrastructure.persistence import postgresql
    from shared.infrastructure.persistence.postgresql import (
        NATIVE_ACQUISITION_ROOT_MAX_BYTES,
        NATIVE_ACQUISITION_ROW_MAX_BYTES,
    )

    statement = "SELECT 'original'::text AS value"
    original, sentinel, calls = PostgresEngine.query_one, RuntimeError("original transport refusal"), []

    def altered(engine, sql, params=()):
        if engine is not owner.engine or statement not in sql:
            return original(engine, sql, params)
        calls.append(True)
        if failure == "original_refusal":
            raise sentinel
        row = original(engine, sql, params)
        if failure == "missing_cell":
            del row["value"]
        elif failure == "extra_cell":
            row["unaccounted"] = "not declared"
        elif failure == "zero_bytes":
            assert row["value"] == "original" and row["_odp_native_present"] is True
            assert row["_odp_native_rows"] == 1 and row["_odp_native_bytes"] > 0
            row["_odp_native_bytes"] = 0
        return row

    with owner.graph.transaction(ids["tenant"]):
        NativeParserCommandBudget(owner.clock)
        with monkeypatch.context() as patch:
            patch.setattr(PostgresEngine, "query_one", altered)
            if failure == "normalization":
                def failed_normalization(row):
                    raise sentinel
                patch.setattr(postgresql, "_normalize_row", failed_normalization)
            with pytest.raises(RuntimeError if failure in {"original_refusal", "normalization"} else JobFenceRejectedError) as error:
                owner.engine.native_query(statement, columns=("value",), one=True)
            if failure in {"original_refusal", "normalization"}:
                assert error.value is sentinel
        assert calls == [True]
        assert owner.engine.enable_native_acquisition()[2] == NATIVE_ACQUISITION_ROOT_MAX_BYTES - NATIVE_ACQUISITION_ROW_MAX_BYTES


def test_original_queue_decode_failure_keeps_its_already_acquired_charge(parser_admission_case):
    owner, ids = parser_admission_case[:2]
    job, _ = _manual(parser_admission_case)
    from json import JSONDecodeError

    from shared.infrastructure.persistence.job_queue import DURABLE_JOB_COLUMNS
    from shared.infrastructure.persistence.postgresql import NATIVE_ACQUISITION_ROOT_MAX_BYTES

    class RollbackProbe(Exception):
        pass

    previous = _persisted(parser_admission_case)
    with pytest.raises(RollbackProbe):
        with owner.graph.transaction(ids["tenant"]):
            owner.engine.execute("UPDATE durable_jobs SET payload_json=to_jsonb('{'::text) WHERE job_id=?", (job["job_id"],))
            measured = owner.engine.query_one("SELECT octet_length(row_to_json(d)::text) AS bytes FROM (SELECT " +
                ",".join(DURABLE_JOB_COLUMNS) + " FROM durable_jobs WHERE job_id=?) d", (job["job_id"],))["bytes"]
            NativeParserCommandBudget(owner.clock)
            with pytest.raises(JSONDecodeError):
                owner.queue.get(job["job_id"])
            assert owner.engine.enable_native_acquisition()[2] == NATIVE_ACQUISITION_ROOT_MAX_BYTES - measured
            raise RollbackProbe
    assert _persisted(parser_admission_case) == previous



def test_unused_outbox_publisher_error_never_enters_either_complete_manual_proof(parser_admission_case, monkeypatch):
    owner, ids = parser_admission_case[:2]
    job, delivery = _manual(parser_admission_case)
    with owner.graph.transaction(ids["tenant"]):
        owner.engine.execute("UPDATE workflow.outbox_events SET last_error=jsonb_build_object('error',repeat('店',2100000)) WHERE tenant_id=?", (ids["tenant"],))
    query, query_one, observed = PostgresEngine.query, PostgresEngine.query_one, []

    def inspect(sql, rows):
        if "workflow.outbox_events" in sql and rows and "_odp_native_allowed" in rows[0]:
            # Necessary workflow job cells in a joined proof stay intact.
            # Every outbox publisher last_error is huge and non-NULL above.
            assert all(value is None for row in rows for key, value in row.items()
                if key.endswith("last_error"))
            observed.append(len(rows))

    def many(engine, sql, params=()):
        rows = query(engine, sql, params)
        if engine is owner.engine:
            inspect(sql, rows)
        return rows

    def one(engine, sql, params=()):
        row = query_one(engine, sql, params)
        if engine is owner.engine:
            inspect(sql, [] if row is None else [row])
        return row

    results = []
    with monkeypatch.context() as patch:
        patch.setattr(PostgresEngine, "query", many)
        patch.setattr(PostgresEngine, "query_one", one)
        for budget_type in (NativeIntakeCommandBudget, NativeParserCommandBudget):
            with owner.graph.transaction(ids["tenant"]):
                results.append(NativeAssistedEntryHistory(owner.graph).committed_result_with_command(
                    delivery, ids["tenant"], 1, before=budget_type(owner.clock)))
                assert owner.engine.enable_native_acquisition()[2] > 31 * 1024 * 1024
    assert observed and results[0] is not None and results[0] == results[1]
    assert results[0][2] == job


def test_one_oversized_necessary_audit_suppresses_the_whole_original_manual_batch(parser_admission_case, monkeypatch):
    from modules.listing.application.native_intake_assisted_entry_request_proof import (
        request_audit_id,
    )

    owner, ids = parser_admission_case[:2]
    job, delivery = _manual(parser_admission_case)
    target = request_audit_id(_object(job["payload"])["request_id"])
    with owner.graph.transaction(ids["tenant"]):
        owner.engine.execute("UPDATE audit.audit_events SET after_value=after_value || "
            "jsonb_build_object('oversized',repeat('店',1500000)) WHERE tenant_id=? AND audit_event_id=?", (ids["tenant"], target))
    original, acquired = PostgresEngine.query, []

    def observe(engine, sql, params=()):
        rows = original(engine, sql, params)
        if engine is owner.engine and " FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=ANY(" in sql:
            acquired.append((len(rows), all(row[k] is None for row in rows for k in row if not k.startswith("_odp_native_"))))
        return rows

    monkeypatch.setattr(PostgresEngine, "query", observe)
    with owner.graph.transaction(ids["tenant"]):
        with pytest.raises(JobFenceRejectedError, match="^NATIVE_ACQUISITION_LIMIT_OR_TRANSPORT_REJECTED$"):
            NativeAssistedEntryHistory(owner.graph).committed_result_with_command(
                delivery, ids["tenant"], 1, before=NativeParserCommandBudget(owner.clock))
    assert acquired == [(4, True)]
