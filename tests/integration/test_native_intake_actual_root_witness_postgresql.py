"""Actual pooled transaction lifetimes; source only for Root execution.

Terminal rows come from parser_admission_case's native HTTP/SQL chain and
_source_delivery. Its principals, approvals, source, provider and storage
remain explicitly synthetic fixture inputs. Reopened native repositories
read those persisted facts; they do not construct positive terminal history.

The pooled helper regressions use only original source171 APIs and do not
depend on root_transaction. Root must retain their meaningful original RED
before integrating the fences. No execution or qualification is claimed here.
"""
import json
from contextlib import nullcontext

import pytest
from psycopg import Transaction
from psycopg.pq import TransactionStatus

from modules.listing.application.intake_audit import CanonicalIntakeAuditSink
from modules.listing.application.native_intake_parser_prerequisites import (
    NativeParserCommandBudget,
    NativeParserSQLClock,
)
from modules.listing.application.native_intake_producer_reads import native_terminal_pair
from modules.listing.application.native_intake_source_policy import NativeIntakeSourcePolicyOwner
from modules.listing.application.native_intake_terminal_evidence import native_terminal_evidence
from shared.audit.canonical_postgresql import CanonicalPostgresAuditLog
from shared.infrastructure.persistence.identity_graph_sql import CanonicalIdentityGraphRepository
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.postgresql import PostgresEngine
from shared.jobs.queue import JobFenceRejectedError
from tests.integration.test_native_intake_parent_terminal_rows_postgresql import _source_delivery
from tests.integration.test_native_intake_parser_admission_postgresql import authority as authority
from tests.integration.test_native_intake_parser_admission_postgresql import (
    canonical_graph as canonical_graph,
)
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


class _RollbackRoot(Exception):
    """A real exception escaping the actual PostgreSQL transaction context."""


def _root_identity(engine):
    return engine.query_one(
        "SELECT current_database() AS database, pg_backend_pid() AS backend_pid, "
        "txid_current()::text AS transaction_id, "
        "current_setting('transaction_isolation') AS isolation, "
        "current_setting('app.tenant_id', true) AS tenant"
    )


def _assert_original_budget_unexpired(engine, budget, deadlines):
    assert (budget.deadline, budget._monotonic_deadline) == deadlines
    budget()  # Original SQL deadline and both actual remaining local timeouts.
    assert engine.query_one(
        "SELECT clock_timestamp() < ?::timestamptz AS unexpired",
        (deadlines[0].isoformat(),),
    ) == {"unexpired": True}
    budget.check_in_memory()
    assert (budget.deadline, budget._monotonic_deadline) == deadlines


def test_actual_transaction_object_nested_commit_rollback_and_pooled_reuse(
        intake_blank_db, record_property):
    engine = PostgresEngine(intake_blank_db.url(), max_pool_size=1,
        bootstrap=False, validate_schema=False)
    try:
        assert getattr(engine._local, "root_transaction", None) is None
        with engine.transaction():
            connection = engine._local.connection
            committed = engine._local.root_transaction
            assert type(committed) is Transaction
            assert committed.connection is connection
            assert committed.pgconn is connection.pgconn
            assert committed.status == Transaction.Status.ACTIVE
            assert connection.info.transaction_status == TransactionStatus.INTRANS
            first = _root_identity(engine)
            assert first["database"] == intake_blank_db.dbname
            assert first["backend_pid"] == connection.info.backend_pid
            engine.execute("CREATE TEMPORARY TABLE actual_root_witness "
                "(marker text NOT NULL) ON COMMIT PRESERVE ROWS")
            engine.execute("INSERT INTO actual_root_witness VALUES ('committed')")
            with engine.transaction():
                assert engine._local.root_transaction is committed
                assert engine._local.connection is connection
                assert engine._local.depth == 2
                assert _root_identity(engine) == first
            assert engine._local.root_transaction is committed
            assert engine._local.depth == 1
            assert committed.status == Transaction.Status.ACTIVE
        assert engine._local.root_transaction is None
        assert engine._local.connection is None and engine._local.depth == 0
        assert committed.status == Transaction.Status.COMMITTED
        assert connection.info.transaction_status == TransactionStatus.IDLE

        with pytest.raises(_RollbackRoot, match="^rollback the actual root$"):
            with engine.transaction():
                rolled_back = engine._local.root_transaction
                assert type(rolled_back) is Transaction
                assert rolled_back is not committed
                assert engine._local.connection is connection
                assert rolled_back.connection is connection
                assert rolled_back.pgconn is committed.pgconn
                assert rolled_back.status == Transaction.Status.ACTIVE
                second = _root_identity(engine)
                assert second["backend_pid"] == first["backend_pid"]
                assert second["transaction_id"] != first["transaction_id"]
                with engine.transaction():
                    assert engine._local.root_transaction is rolled_back
                    assert _root_identity(engine) == second
                    engine.execute("INSERT INTO actual_root_witness VALUES ('rolled back')")
                    raise _RollbackRoot("rollback the actual root")
        assert engine._local.root_transaction is None
        assert engine._local.connection is None and engine._local.depth == 0
        assert rolled_back.status == Transaction.Status.ROLLED_BACK_WITH_ERROR
        assert connection.info.transaction_status == TransactionStatus.IDLE

        with engine.transaction():
            reused = engine._local.root_transaction
            assert type(reused) is Transaction
            assert reused is not committed and reused is not rolled_back
            assert engine._local.connection is connection
            assert reused.connection is connection and reused.pgconn is committed.pgconn
            assert reused.status == Transaction.Status.ACTIVE
            third = _root_identity(engine)
            assert third["backend_pid"] == first["backend_pid"]
            assert third["transaction_id"] not in {first["transaction_id"], second["transaction_id"]}
            # Actual committed row and exception rollback on the same session.
            assert engine.query("SELECT marker FROM actual_root_witness ORDER BY marker") == [
                {"marker": "committed"}
            ]
        assert engine._local.root_transaction is None
        assert engine._local.connection is None and engine._local.depth == 0
        assert reused.status == Transaction.Status.COMMITTED
        assert connection.info.transaction_status == TransactionStatus.IDLE
        record_property("actual_root_lifetimes", json.dumps([first, second, third]))
    finally:
        engine.close()


@pytest.mark.parametrize("exit_kind", ["commit", "rollback"])
@pytest.mark.parametrize("reader,code", [
    ("closed_facts", "NATIVE_PARENT_TERMINAL_EVIDENCE_REJECTED"),
    ("read_view", "NATIVE_PARENT_TERMINAL_ROWS_REJECTED"),
])
def test_original_budget_cannot_reuse_old_facts_or_view_in_next_pooled_root(
        parser_admission_case, record_property, exit_kind, reader, code):
    case = parser_admission_case
    owner, ids, database = case[:3]
    delivery = _source_delivery(case)
    provider_calls = list(case[5].calls)
    engine = PostgresEngine(database.url(), max_pool_size=1,
        bootstrap=False, validate_schema=False)
    try:
        queue = DurableJobQueue(engine)
        # Real read-only repository composition on the same fixture database.
        # No audit writes/delivery or replacement fixture authority are needed.
        sink = CanonicalIntakeAuditSink(CanonicalPostgresAuditLog(engine),
            owner.graph.audit_sink.retention)
        graph = CanonicalIdentityGraphRepository(engine, queue, sink)
        source = NativeIntakeSourcePolicyOwner(graph)
        clock = NativeParserSQLClock(engine)
        ending = (pytest.raises(_RollbackRoot, match="^end the original root$")
            if exit_kind == "rollback" else nullcontext())
        with ending:
            with graph.transaction(ids["tenant"]):
                connection = engine._local.connection
                first = _root_identity(engine)
                assert first["database"] == database.dbname
                assert first["backend_pid"] == connection.info.backend_pid
                assert first["isolation"] == "serializable" and first["tenant"] == ids["tenant"]
                # Reference reads precede this one original logical budget.
                workflow = engine.query_one(
                    "SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                    (ids["tenant"], delivery.job_id),
                )
                assert queue.get(delivery.job_id) == delivery
                assert workflow["status"] == "SUCCEEDED"
                assert workflow["job_type"] == "native-intake-source-policy"
                assert workflow["tenant_id"] == ids["tenant"]
                budget = NativeParserCommandBudget(clock)
                deadlines = budget.deadline, budget._monotonic_deadline
                view = source._producer_reads(budget)
                facts = native_terminal_evidence(view, ids["tenant"], delivery.job_id)
                assert facts is not None
                assert facts.pair() == (delivery, workflow)
                assert native_terminal_pair(view, ids["tenant"], delivery.job_id) == (delivery, workflow)
                _assert_original_budget_unexpired(engine, budget, deadlines)
                if exit_kind == "rollback":
                    raise _RollbackRoot("end the original root")
        assert engine._local.connection is None
        assert not connection.closed
        assert connection.info.transaction_status == TransactionStatus.IDLE

        # This must reach the original171 helper even without the new engine
        # field. A native backend/xid pair proves the lifetime change directly.
        with graph.transaction(ids["tenant"]):
            assert engine._local.connection is connection
            _assert_original_budget_unexpired(engine, budget, deadlines)
            second = _root_identity(engine)
            assert second["database"] == first["database"]
            assert second["backend_pid"] == first["backend_pid"] == connection.info.backend_pid
            assert second["transaction_id"] != first["transaction_id"]
            assert second["isolation"] == "serializable" and second["tenant"] == ids["tenant"]
            record_property("pooled_root_reuse", json.dumps({
                "first": first, "second": second, "first_exit": exit_kind, "reader": reader,
                "same_connection_object": engine._local.connection is connection,
                "original_sql_deadline": deadlines[0].isoformat(),
                "current_sql_deadline": budget.deadline.isoformat(),
                "original_monotonic_deadline": deadlines[1],
                "current_monotonic_deadline": budget._monotonic_deadline,
            }))
            _assert_original_budget_unexpired(engine, budget, deadlines)
            # Separate parameter cases ensure both missing fences produce RED.
            # Expiry is never an alternative accepted error for this regression.
            with pytest.raises(JobFenceRejectedError, match="^" + code + "$"):
                if reader == "closed_facts":
                    facts.pair()
                else:
                    native_terminal_pair(view, ids["tenant"], delivery.job_id)
            _assert_original_budget_unexpired(engine, budget, deadlines)
        assert engine._local.connection is None
        assert connection.info.transaction_status == TransactionStatus.IDLE
        assert case[5].calls == provider_calls
    finally:
        engine.close()
