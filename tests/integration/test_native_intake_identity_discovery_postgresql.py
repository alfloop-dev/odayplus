"""Identity's recursive start discovery over genuine PostgreSQL producer history.

Source authored for Root's M4 caller integration, absent from historical203.
The existing fixture owns its explicit synthetic approvals and provider inputs;
this file creates no accepted history. Bare-graph full verification is the
independent reference, read before the original 750 ms native command budget.
Discovery statement counts describe transport, not latency or its causes.
"""
import hashlib
import json
from uuid import uuid4

import pytest

from modules.listing.application.native_intake_identity import (
    NativeIntakeIdentityConflict,
    NativeIntakeIdentityOwner,
)
from modules.listing.application.native_intake_parser_prerequisites import NativeParserCommandBudget
from modules.listing.application.native_intake_producer_discovery import _DISCOVERY_SQL
from modules.listing.application.native_intake_source_policy import NativeIntakeSourcePolicyOwner
from modules.listing.application.native_intake_terminal_evidence import _TERMINAL_SQL
from shared.infrastructure.persistence.postgresql import PostgresEngine
from shared.jobs.queue import JobFenceRejectedError
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
from tests.integration.test_native_intake_producer_discovery_postgresql import (
    _event_ids,
    _reference,
)
from tests.integration.test_native_intake_terminal_evidence_postgresql import _persisted

pytestmark = pytest.mark.requires_live_env

_EVENT_SQL = "SELECT * FROM workflow.outbox_events WHERE tenant_id=? AND event_id=?"
_TRANSITION_SQL = (
    "SELECT job_id FROM intake.intake_stage_transitions "
    "WHERE tenant_id=? AND transition_id=? AND intake_id=?"
)
_BUSINESS_SQL = "SELECT payload FROM workflow.jobs WHERE tenant_id=? AND job_id=?"
_PHYSICAL_SQL = "SELECT * FROM durable_jobs WHERE job_id = ?"


def _start_event_id(case):
    # Existing helper follows manual -> source event -> actual source job input.
    # Continue through the identity event's actual producer transition and job;
    # do not select an arbitrary state event or derive a successful job ID.
    owner, ids = case[:2]
    identity_event_id = _event_ids(case)["identity"]
    with owner.graph.transaction(ids["tenant"]):
        row, transition, business, delivery = _reference(owner.graph, ids["tenant"], identity_event_id)
        assert row["aggregate_id"] == case[3]["intake_id"]
        assert row["payload"]["actor_ref"] == "native-intake-identity"
        assert delivery is not None and delivery.job_id == transition["job_id"]
        assert delivery.job_type == "native-intake-identity"
        return business["payload"]["event_id"]


def _caller(case, before):
    view = NativeIntakeSourcePolicyOwner(case[0].graph)._producer_reads(before)
    return NativeIntakeIdentityOwner(view)


def _observe(case, monkeypatch, corrupt=None):
    """Forward real native engine reads without changing adapter eligibility."""
    calls = []
    query, query_one = PostgresEngine.query, PostgresEngine.query_one

    def many(engine, statement, params=()):
        if engine is case[0].engine:
            calls.append((statement, tuple(params)))
        rows = query(engine, statement, params)
        if engine is case[0].engine and statement == _DISCOVERY_SQL and corrupt is not None:
            return corrupt(rows)
        return rows

    def one(engine, statement, params=()):
        if engine is case[0].engine:
            calls.append((statement, tuple(params)))
        return query_one(engine, statement, params)

    monkeypatch.setattr(PostgresEngine, "query", many)
    monkeypatch.setattr(PostgresEngine, "query_one", one)
    return calls


def _facts(calls):
    # Exclude only the unchanged native clock/settings statements. Every other
    # attempted read remains visible, including speculative or fallback reads.
    return [(statement, params) for statement, params in calls
        if statement != "SELECT clock_timestamp() AS now"
        and not statement.startswith("WITH observed AS MATERIALIZED (")]


def _legacy_reads(tenant, event_id, reference):
    row, transition, _, _ = reference
    return [(_EVENT_SQL, (tenant, event_id)),
        (_TRANSITION_SQL, (tenant, row["payload"]["payload"]["transition_id"], row["aggregate_id"])),
        (_BUSINESS_SQL, (tenant, transition["job_id"])),
        (_PHYSICAL_SQL, (transition["job_id"],))]


def test_identity_full_caller_matches_bare_graph_with_one_start_discovery(parser_admission_case, monkeypatch):
    case = parser_admission_case
    owner, ids = case[:2]
    tenant, event_id = ids["tenant"], _start_event_id(case)
    original = _persisted(case)
    with owner.graph.transaction(tenant):
        reference = _reference(owner.graph, tenant, event_id)
        assert reference[3] is not None and reference[3].job_type == "native-intake-start"
        with monkeypatch.context() as trace:
            calls = _observe(case, trace)
            legacy = NativeIntakeIdentityOwner(owner.graph)._event(tenant, event_id)
            assert _facts(calls)[:4] == _legacy_reads(tenant, event_id, reference)
            assert all(statement != _DISCOVERY_SQL for statement, _ in calls)
        assert legacy[:2] == (reference[0], reference[0]["payload"])
        assert legacy[2] == hashlib.sha256(json.dumps(
            reference[0]["payload"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    # Separate differential invocation, with no reference SQL inside its budget.
    with owner.graph.transaction(tenant):
        root, connection = owner.engine._local.root_transaction, owner.engine._local.connection
        budget = NativeParserCommandBudget(owner.clock)
        bounds = budget.deadline, budget._monotonic_deadline
        with monkeypatch.context() as trace:
            calls = _observe(case, trace)
            current = _caller(case, budget)._event(tenant, event_id)
            assert current == legacy
            assert _facts(calls) == [(_DISCOVERY_SQL, (tenant, event_id)),
                (_TERMINAL_SQL, (tenant, reference[3].job_id, reference[3].job_id))]
        assert owner.engine._local.root_transaction is root
        assert owner.engine._local.connection is connection
        assert (budget.deadline, budget._monotonic_deadline) == bounds
    assert _persisted(case) == original


@pytest.mark.parametrize("adapter", ["falsey_callback", "graph_one", "queue_get"])
def test_identity_unknown_surface_preserves_exact_original_refusal(parser_admission_case, monkeypatch, adapter):
    case = parser_admission_case
    owner, ids = case[:2]
    tenant, event_id = ids["tenant"], _start_event_id(case)
    original = _persisted(case)
    sentinel, invocations = RuntimeError("original identity adapter refusal"), []

    class FalseyCallback:
        def __bool__(self):
            return False

        def __call__(self):
            invocations.append("falsey_callback")
            raise sentinel

    def refuse(*args, **kwargs):
        invocations.append(adapter)
        raise sentinel

    with owner.graph.transaction(tenant):
        reference = _reference(owner.graph, tenant, event_id)
        root = owner.engine._local.root_transaction
        budget = NativeParserCommandBudget(owner.clock)
        bounds = budget.deadline, budget._monotonic_deadline
        with monkeypatch.context() as trace:
            if adapter == "graph_one":
                trace.setattr(owner.graph, "_one", refuse)
            elif adapter == "queue_get":
                trace.setattr(owner.queue, "get", refuse)
            before = FalseyCallback() if adapter == "falsey_callback" else budget
            calls = _observe(case, trace)
            with pytest.raises(RuntimeError) as error:
                _caller(case, before)._event(tenant, event_id)
            assert error.value is sentinel and invocations == [adapter]
            assert all(statement != _DISCOVERY_SQL for statement, _ in calls)
            expected = _legacy_reads(tenant, event_id, reference)[:3] if adapter == "queue_get" else []
            assert _facts(calls) == expected
        assert owner.engine._local.root_transaction is root
        assert (budget.deadline, budget._monotonic_deadline) == bounds
    assert _persisted(case) == original


@pytest.mark.parametrize("damage", ["duplicate_result", "missing_physical_column", "foreign_tenant"])
def test_identity_corrupt_selected_discovery_refuses_without_fallback(parser_admission_case, monkeypatch, damage):
    case = parser_admission_case
    owner, ids = case[:2]
    tenant, event_id = ids["tenant"], _start_event_id(case)
    original, foreign_tenant = _persisted(case), str(uuid4())

    def corrupt(rows):
        assert len(rows) == 1
        if damage == "duplicate_result":
            return [*rows, dict(rows[0])]
        row = dict(rows[0])
        if damage == "missing_physical_column":
            del row["discovery_physical_max_retries"]
        else:
            row["tenant_id"] = foreign_tenant
        return [row]

    with owner.graph.transaction(tenant):
        root = owner.engine._local.root_transaction
        budget = NativeParserCommandBudget(owner.clock)
        bounds = budget.deadline, budget._monotonic_deadline
        with monkeypatch.context() as trace:
            calls = _observe(case, trace, corrupt)
            with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARENT_DISCOVERY_REJECTED$"):
                _caller(case, budget)._event(tenant, event_id)
            assert _facts(calls) == [(_DISCOVERY_SQL, (tenant, event_id))]
        assert owner.engine._local.root_transaction is root
        assert (budget.deadline, budget._monotonic_deadline) == bounds
    assert _persisted(case) == original


def test_identity_missing_actual_start_delivery_is_not_reconstructed(parser_admission_case, monkeypatch):
    case = parser_admission_case
    owner, ids, database = case[:3]
    tenant, event_id = ids["tenant"], _start_event_id(case)
    with owner.graph.transaction(tenant):
        reference = _reference(owner.graph, tenant, event_id)
        assert reference[3] is not None and reference[3].job_type == "native-intake-start"
        NativeIntakeIdentityOwner(owner.graph)._event(tenant, event_id)
    with database.connect() as connection:
        assert connection.execute("DELETE FROM odp_runtime.durable_jobs WHERE job_id=%s",
            (reference[3].job_id,)).rowcount == 1
    damaged = _persisted(case)
    with owner.graph.transaction(tenant):
        budget = NativeParserCommandBudget(owner.clock)
        bounds = budget.deadline, budget._monotonic_deadline
        with monkeypatch.context() as trace:
            calls = _observe(case, trace)
            with pytest.raises(NativeIntakeIdentityConflict, match="^NATIVE_IDENTITY_START_PROOF_MISSING$"):
                _caller(case, budget)._event(tenant, event_id)
            assert _facts(calls) == [(_DISCOVERY_SQL, (tenant, event_id))]
        assert (budget.deadline, budget._monotonic_deadline) == bounds
    assert _persisted(case) == damaged


def test_identity_same_caller_reacquires_after_real_mutation_and_rollback(parser_admission_case, monkeypatch):
    case = parser_admission_case
    owner, ids = case[:2]
    tenant, event_id = ids["tenant"], _start_event_id(case)
    original = _persisted(case)
    with owner.graph.transaction(tenant):
        reference = _reference(owner.graph, tenant, event_id)
        legacy = NativeIntakeIdentityOwner(owner.graph)._event(tenant, event_id)
        assert reference[0]["payload"]["actor_ref"] == "native-intake-start"
    with owner.graph.transaction(tenant):
        root, connection = owner.engine._local.root_transaction, owner.engine._local.connection
        # The same caller and view retain this one original budget throughout;
        # neither savepoint rollback nor a fresh acquisition restarts it.
        budget = NativeParserCommandBudget(owner.clock)
        bounds = budget.deadline, budget._monotonic_deadline
        caller = _caller(case, budget)
        with monkeypatch.context() as trace:
            calls = _observe(case, trace)
            assert caller._event(tenant, event_id) == legacy
            budget()
            owner.engine.execute("SAVEPOINT identity_discovery_negative")
            try:
                budget()
                assert owner.engine.execute("UPDATE workflow.outbox_events SET payload=jsonb_set(payload,"
                    "'{actor_ref}',?::jsonb) WHERE tenant_id=? AND event_id=?",
                    (json.dumps("invalid-start-producer"), tenant, event_id)).rowcount == 1
                with pytest.raises(NativeIntakeIdentityConflict, match="^NATIVE_IDENTITY_START_EVENT_CONFLICT$"):
                    caller._event(tenant, event_id)
            finally:
                # Actual rollback restores the genuine producer row; no positive
                # fixture patch or copied success record is written back.
                owner.engine.execute("ROLLBACK TO SAVEPOINT identity_discovery_negative")
                owner.engine.execute("RELEASE SAVEPOINT identity_discovery_negative")
            assert caller._event(tenant, event_id) == legacy
            discovery = (_DISCOVERY_SQL, (tenant, event_id))
            terminal = (_TERMINAL_SQL, (tenant, reference[3].job_id, reference[3].job_id))
            assert _facts(calls) == [discovery, terminal, discovery, discovery, terminal]
        assert owner.engine._local.root_transaction is root
        assert owner.engine._local.connection is connection
        assert (budget.deadline, budget._monotonic_deadline) == bounds
    assert _persisted(case) == original
