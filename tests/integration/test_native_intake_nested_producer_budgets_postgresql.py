"""Source171 nested producer regressions, authored for Root execution only.

The existing fixture creates the HTTP/SQL chain; its providers, principals
and approvals remain explicitly synthetic. Separate raw-graph reference
roots and persistence snapshots precede the one unchanged native budget.
No deadline failure is retried, and no runtime/SLO qualification is claimed.
"""
import pytest

from modules.listing.application.native_intake_identity import NativeIntakeIdentityOwner
from modules.listing.application.native_intake_parser_prerequisites import NativeParserCommandBudget
from modules.listing.application.native_intake_producer_reads import _PAIR_SQL
from modules.listing.application.native_intake_source_policy import NativeIntakeSourcePolicyOwner
from modules.listing.application.native_intake_terminal_evidence import (
    _TERMINAL_SQL,
    native_terminal_evidence,
)
from shared.infrastructure.persistence.postgresql import PostgresEngine
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
from tests.integration.test_native_intake_terminal_evidence_postgresql import _persisted

pytestmark = pytest.mark.requires_live_env

_OWNERS = {"source": NativeIntakeSourcePolicyOwner, "identity": NativeIntakeIdentityOwner}
_DESCENDANTS = {"source": ("source", "identity", "start"), "identity": ("identity", "start")}
_JOB_TYPES = {"source": "native-intake-source-policy", "identity": "native-intake-identity",
    "start": "native-intake-start"}
_WORKFLOW_SQL = "SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?"
# The existing clock's actual bound query, independent of any future callback API.
_BUDGET_SQL = (
    "WITH observed AS MATERIALIZED ("
    "SELECT clock_timestamp() AS observed_at), remaining AS MATERIALIZED ("
    "SELECT floor(EXTRACT(EPOCH FROM (?::timestamptz-observed_at))*1000)::bigint-1 AS ms "
    "FROM observed) SELECT ms AS remaining_ms, "
    "CASE WHEN ms>=1 THEN set_config('statement_timeout',ms::text||'ms',true) END AS statement_timeout, "
    "CASE WHEN ms>=1 THEN set_config('lock_timeout',ms::text||'ms',true) END AS lock_timeout "
    "FROM remaining"
)


def _observe(case, monkeypatch, *, budget=None, trace=None):
    """Forward actual class-level SQL; never adapt the native graph or budget."""
    engine = case[0].engine
    root = engine._local.connection
    original = None if budget is None else (budget.deadline, budget._monotonic_deadline)
    calls = []
    query, query_one = PostgresEngine.query, PostgresEngine.query_one

    def forward(method, actual_engine, statement, params):
        if actual_engine is not engine:
            return method(actual_engine, statement, params)
        assert actual_engine._local.connection is root and not root.closed
        if budget is not None:
            assert (budget.deadline, budget._monotonic_deadline) == original
        calls.append((statement, tuple(params)))
        if trace is not None:
            trace.append(("sql", statement, tuple(params)))
        result = method(actual_engine, statement, params)
        assert actual_engine._local.connection is root and not root.closed
        if budget is not None:
            assert (budget.deadline, budget._monotonic_deadline) == original
        return result

    def many(actual_engine, statement, params=()):
        return forward(query, actual_engine, statement, params)

    def one(actual_engine, statement, params=()):
        return forward(query_one, actual_engine, statement, params)

    monkeypatch.setattr(PostgresEngine, "query", many)
    monkeypatch.setattr(PostgresEngine, "query_one", one)
    return calls


def _linked_deliveries(case, role):
    # Follow actual outbox -> transition -> job links, never construct a Job.
    deliveries = {name: _source_delivery(case, name) for name in _DESCENDANTS[role]}
    assert len({delivery.job_id for delivery in deliveries.values()}) == len(deliveries)
    for name, delivery in deliveries.items():
        assert delivery.job_type == _JOB_TYPES[name]
    return deliveries


def _legacy_result(case, monkeypatch, role, delivery):
    owner, ids = case[:2]
    with owner.graph.transaction(ids["tenant"]):
        with monkeypatch.context() as patch:
            calls = _observe(case, patch)
            result = _OWNERS[role](owner.graph)._committed_result(delivery, ids["tenant"], 1)
    assert result is not None
    assert (_WORKFLOW_SQL, (ids["tenant"], delivery.job_id)) in calls
    assert not any(statement == _TERMINAL_SQL for statement, _ in calls)
    return result


def _assert_original_bounds(calls, budget):
    # Every live producer SQL still has its own actual clock/timeout query.
    # An extra root-budget constructor would also emit an unpaired clock read.
    assert calls and len(calls) % 2 == 0
    for guard, read in zip(calls[::2], calls[1::2], strict=True):
        assert guard == (_BUDGET_SQL, (budget.deadline.isoformat(),))
        assert read[0].startswith("SELECT ")
        assert read[0] != "SELECT clock_timestamp() AS now"


@pytest.mark.parametrize("role", ["source", "identity"])
def test_each_linked_descendant_batches_once_under_the_original_root_budget(
        parser_admission_case, monkeypatch, role):
    case = parser_admission_case
    owner, ids = case[:2]
    deliveries = _linked_deliveries(case, role)
    delivery = deliveries[role]
    original = _persisted(case)
    legacy = _legacy_result(case, monkeypatch, role, delivery)
    assert _persisted(case) == original
    # This second root is a deliberate differential invocation, never recovery
    # from a failed budget. Only one budget is constructed for the invocation.
    with owner.graph.transaction(ids["tenant"]):
        budget = NativeParserCommandBudget(owner.clock)
        deadline = budget.deadline, budget._monotonic_deadline
        view = NativeIntakeSourcePolicyOwner(owner.graph)._producer_reads(budget)
        with monkeypatch.context() as patch:
            calls = _observe(case, patch, budget=budget)
            current = _OWNERS[role](view)._committed_result(delivery, ids["tenant"], 1)
        assert (budget.deadline, budget._monotonic_deadline) == deadline
    assert _persisted(case) == original
    assert current == legacy
    # Exact real parameters expose missing descendant batches, extra batches,
    # and replacement IDs; checking only the top-level job misses this defect.
    assert [params for statement, params in calls if statement == _TERMINAL_SQL] == [
        (ids["tenant"], deliveries[name].job_id, deliveries[name].job_id)
        for name in _DESCENDANTS[role]
    ]
    assert not any(statement in {_PAIR_SQL, _WORKFLOW_SQL} for statement, _ in calls)
    _assert_original_bounds(calls, budget)


@pytest.mark.parametrize("role", ["source", "identity"])
def test_unknown_callback_completes_before_every_actual_legacy_producer_read(
        parser_admission_case, monkeypatch, role):
    case = parser_admission_case
    owner, ids = case[:2]
    deliveries = _linked_deliveries(case, role)
    delivery = deliveries[role]
    original = _persisted(case)
    legacy = _legacy_result(case, monkeypatch, role, delivery)
    assert _persisted(case) == original
    with owner.graph.transaction(ids["tenant"]):
        budget = NativeParserCommandBudget(owner.clock)
        deadline = budget.deadline, budget._monotonic_deadline
        trace = []

        def unknown_before():
            trace.append(("callback_enter",))
            budget()
            trace.append(("callback_return",))

        view = NativeIntakeSourcePolicyOwner(owner.graph)._producer_reads(unknown_before)
        with monkeypatch.context() as patch:
            calls = _observe(case, patch, budget=budget, trace=trace)
            assert native_terminal_evidence(view, ids["tenant"], delivery.job_id) is None
            assert calls == [] and trace == []
            current = _OWNERS[role](view)._committed_result(delivery, ids["tenant"], 1)
        assert (budget.deadline, budget._monotonic_deadline) == deadline
    assert _persisted(case) == original
    assert current == legacy
    assert not any(statement == _TERMINAL_SQL for statement, _ in calls)
    assert [params for statement, params in calls if statement == _PAIR_SQL] == [
        (ids["tenant"], deliveries[name].job_id, deliveries[name].job_id)
        for name in _DESCENDANTS[role]
    ]
    _assert_original_bounds(calls, budget)
    # Equality covers every old pair, metadata and linked-delivery SQL read;
    # the callback's real SQL guard must finish before each read is forwarded.
    expected_trace = []
    for guard, read in zip(calls[::2], calls[1::2], strict=True):
        expected_trace.extend([("callback_enter",), ("sql", *guard),
            ("callback_return",), ("sql", *read)])
    assert trace == expected_trace


def test_explicit_falsey_event_callback_escapes_unchanged_before_any_producer_sql(
        parser_admission_case, monkeypatch):
    case = parser_admission_case
    owner, ids = case[:2]
    delivery = _source_delivery(case)
    original = _persisted(case)
    refusal = RuntimeError("explicit falsey nested producer callback refusal")
    invoked = []

    class FalseyCallback:
        def __bool__(self):
            return False

        def __call__(self):
            invoked.append("called")
            raise refusal

    with owner.graph.transaction(ids["tenant"]):
        job = owner.graph._one("SELECT payload FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
            (ids["tenant"], delivery.job_id))
        event_id = job["payload"]["event_id"]
        with monkeypatch.context() as patch:
            calls = _observe(case, patch)
            with pytest.raises(RuntimeError, match="^explicit falsey nested producer callback refusal$") as caught:
                NativeIntakeSourcePolicyOwner(owner.graph)._event(ids["tenant"], event_id,
                    before=FalseyCallback())
            assert caught.value is refusal
            assert invoked == ["called"]
            assert calls == []
    assert _persisted(case) == original
