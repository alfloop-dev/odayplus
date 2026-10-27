"""Discovery transport over genuine canonical API/manual producer history.

Independent four-statement reads and existing legacy full verifiers provide
the oracles. Negative mutations never mint accepted history. Every native
invocation keeps its original750ms root; reference reads precede that budget.
"""
import json
import time
from uuid import uuid4

import pytest

from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryConflict,
)
from modules.listing.application.native_intake_assisted_entry_source import (
    NativeAssistedEntrySourceProof,
)
from modules.listing.application.native_intake_identity import NativeIntakeIdentityOwner
from modules.listing.application.native_intake_parser_policy import NativeParserConflict
from modules.listing.application.native_intake_parser_prerequisites import NativeParserCommandBudget
from modules.listing.application.native_intake_producer_discovery import (
    _DISCOVERY_SQL,
    native_producer_discovery,
)
from modules.listing.application.native_intake_source_policy import NativeIntakeSourcePolicyOwner
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
from tests.integration.test_native_intake_terminal_evidence_postgresql import _persisted

pytestmark = pytest.mark.requires_live_env


def _event_ids(case):
    owner, ids = case[:2]
    with owner.graph.transaction(ids["tenant"]):
        manual = owner.graph._one("SELECT payload FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
            (ids["tenant"], case[3]["job_id"]))
        source_event_id = manual["payload"]["source_policy_event_id"]
        event = owner.graph._one("SELECT payload FROM workflow.outbox_events WHERE tenant_id=? AND event_id=?",
            (ids["tenant"], source_event_id))["payload"]
        transition = owner.graph._one("SELECT job_id FROM intake.intake_stage_transitions "
            "WHERE tenant_id=? AND transition_id=? AND intake_id=?",
            (ids["tenant"], event["payload"]["transition_id"], event["aggregate_id"]))
        source_job = owner.graph._one("SELECT payload FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
            (ids["tenant"], transition["job_id"]))
        identity_event_id = source_job["payload"]["event_id"]
        identity_event = owner.graph._one("SELECT payload FROM workflow.outbox_events WHERE tenant_id=? AND event_id=?",
            (ids["tenant"], identity_event_id))["payload"]
        identity_transition = owner.graph._one("SELECT job_id FROM intake.intake_stage_transitions "
            "WHERE tenant_id=? AND transition_id=? AND intake_id=?",
            (ids["tenant"], identity_event["payload"]["transition_id"], identity_event["aggregate_id"]))
        identity_job = owner.graph._one("SELECT payload FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
            (ids["tenant"], identity_transition["job_id"]))
        return {"source": source_event_id, "identity": identity_event_id,
                "start": identity_job["payload"]["event_id"]}


def _reference(graph, tenant, event_id):
    # Four independent existing reads. No expected value is read from the new
    # acquisition or inferred from a status label or constructed JobRecord.
    row = graph._one("SELECT * FROM workflow.outbox_events WHERE tenant_id=? AND event_id=?", (tenant, event_id))
    event = row["payload"]
    transition = graph._one("SELECT job_id FROM intake.intake_stage_transitions "
        "WHERE tenant_id=? AND transition_id=? AND intake_id=?",
        (tenant, event["payload"]["transition_id"], row["aggregate_id"]))
    business = graph._one("SELECT payload FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
        (tenant, transition["job_id"]))
    return row, transition, business, graph.queue.get(transition["job_id"])


def _view(case, budget):
    return NativeIntakeSourcePolicyOwner(case[0].graph)._producer_reads(budget)


def _observe(case, monkeypatch, corrupt=None):
    calls = []
    original = PostgresEngine.query

    def query(engine, statement, params=()):
        rows = original(engine, statement, params)
        if engine is case[0].engine and statement == _DISCOVERY_SQL:
            calls.append(tuple(params))
            if corrupt is not None:
                return corrupt(rows)
        return rows

    monkeypatch.setattr(PostgresEngine, "query", query)
    return calls


@pytest.mark.parametrize("role", ["source", "identity", "start"])
def test_one_discovery_preserves_all_four_independent_native_reads(parser_admission_case, monkeypatch, role):
    case = parser_admission_case
    owner, ids = case[:2]
    event_id = _event_ids(case)[role]
    original = _persisted(case)
    with owner.graph.transaction(ids["tenant"]):
        expected = _reference(owner.graph, ids["tenant"], event_id)
        budget = NativeParserCommandBudget(owner.clock)
        bounds = budget.deadline, budget._monotonic_deadline
        root = owner.engine._local.root_transaction
        calls = _observe(case, monkeypatch)
        discovery = native_producer_discovery(_view(case, budget), ids["tenant"], event_id)
        assert discovery is not None
        row = discovery.event()
        transition = discovery.transition(ids["tenant"], row["payload"]["payload"]["transition_id"], row["aggregate_id"])
        business = discovery.business(ids["tenant"], transition["job_id"])
        delivery = discovery.delivery(transition["job_id"])
        discovery.finish()
        assert (row, transition, business, delivery) == expected
        assert calls == [(ids["tenant"], event_id)]
        assert owner.engine._local.root_transaction is root
        assert (budget.deadline, budget._monotonic_deadline) == bounds
        # Returned mutable local values are neither shared nor persisted.
        row["payload"]["event_version"] = False
        business["payload"]["test_only_local"] = "never persisted"
        assert discovery.event() == expected[0]
        assert discovery.business(ids["tenant"], transition["job_id"]) == expected[2]
        assert len(calls) == 1
    assert _persisted(case) == original


@pytest.mark.parametrize("role", ["source", "identity", "start"])
def test_full_original_callers_still_prove_the_same_lineage(parser_admission_case, monkeypatch, role):
    case = parser_admission_case
    owner, ids = case[:2]
    events = _event_ids(case)
    event_id = events[role]
    original = _persisted(case)
    with owner.graph.transaction(ids["tenant"]):
        # An unbounded historical reference invocation outside the measured
        # native command, matching the existing differential test convention.
        if role == "source":
            legacy = NativeAssistedEntrySourceProof(owner.graph).parent(ids["tenant"], event_id, lambda: None)
        elif role == "identity":
            legacy = NativeIntakeSourcePolicyOwner(owner.graph)._event(ids["tenant"], event_id)
        else:
            legacy = NativeIntakeIdentityOwner(owner.graph)._event(ids["tenant"], event_id)
    with owner.graph.transaction(ids["tenant"]):
        budget = NativeParserCommandBudget(owner.clock)
        bounds = budget.deadline, budget._monotonic_deadline
        calls = _observe(case, monkeypatch)
        if role == "source":
            current = NativeAssistedEntrySourceProof(owner.graph).parent(ids["tenant"], event_id, budget)
        elif role == "identity":
            current = NativeIntakeSourcePolicyOwner(owner.graph)._event(ids["tenant"], event_id, before=budget)
        else:
            current = NativeIntakeIdentityOwner(_view(case, budget))._event(ids["tenant"], event_id)
        assert current == legacy
        expected = {"source": ("source", "identity", "start"),
                    "identity": ("identity", "start"), "start": ("start",)}[role]
        assert calls == [(ids["tenant"], events[name]) for name in expected]
        assert (budget.deadline, budget._monotonic_deadline) == bounds
    assert _persisted(case) == original


@pytest.mark.parametrize("adapter", ["falsey_callback", "graph", "queue"])
def test_unknown_read_surface_selects_original_read_before_any_discovery_sql(parser_admission_case, monkeypatch, adapter):
    case = parser_admission_case
    owner, ids = case[:2]
    event_id = _event_ids(case)["source"]
    original = _persisted(case)
    sentinel = RuntimeError("original adapter refusal")
    invocations = []

    class FalseyCallback:
        def __bool__(self):
            return False

        def __call__(self):
            invocations.append("callback")
            raise sentinel

    def refuse(*args, **kwargs):
        invocations.append(adapter)
        raise sentinel

    with owner.graph.transaction(ids["tenant"]):
        budget = NativeParserCommandBudget(owner.clock)
        if adapter == "graph":
            monkeypatch.setattr(owner.graph, "_one", refuse)
        elif adapter == "queue":
            monkeypatch.setattr(owner.queue, "get", refuse)
        guard = FalseyCallback() if adapter == "falsey_callback" else budget
        calls = _observe(case, monkeypatch)
        assert native_producer_discovery(_view(case, guard), ids["tenant"], event_id) is None
        assert calls == [] and invocations == []
        with pytest.raises(RuntimeError) as error:
            NativeAssistedEntrySourceProof(owner.graph).parent(ids["tenant"], event_id, guard)
        assert error.value is sentinel and len(invocations) == 1 and calls == []
    assert _persisted(case) == original


@pytest.mark.parametrize("damage", ["duplicate", "missing_column", "foreign_event"])
def test_corrupt_discovery_transport_is_refused_without_legacy_retry(parser_admission_case, monkeypatch, damage):
    case = parser_admission_case
    owner, ids = case[:2]
    event_id = _event_ids(case)["source"]
    original = _persisted(case)

    def corrupt(rows):
        if damage == "duplicate":
            return [*rows, dict(rows[0])]
        row = dict(rows[0])
        if damage == "missing_column":
            del row["discovery_physical_max_retries"]
        else:
            row["tenant_id"] = str(uuid4())
        return [row]

    calls = _observe(case, monkeypatch, corrupt)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARENT_DISCOVERY_REJECTED$"):
        with owner.graph.transaction(ids["tenant"]):
            NativeAssistedEntrySourceProof(owner.graph).parent(ids["tenant"], event_id,
                NativeParserCommandBudget(owner.clock))
    assert calls == [(ids["tenant"], event_id)] and _persisted(case) == original


def test_missing_actual_physical_job_is_not_reconstructed_from_business_history(parser_admission_case):
    case = parser_admission_case
    owner, ids, database = case[:3]
    event_id = _event_ids(case)["source"]
    with owner.graph.transaction(ids["tenant"]):
        expected = _reference(owner.graph, ids["tenant"], event_id)
    with database.connect() as connection:
        assert connection.execute("DELETE FROM odp_runtime.durable_jobs WHERE job_id=%s",
            (expected[3].job_id,)).rowcount == 1
    original = _persisted(case)
    with owner.graph.transaction(ids["tenant"]):
        discovery = native_producer_discovery(_view(case, NativeParserCommandBudget(owner.clock)),
            ids["tenant"], event_id)
        assert discovery.event() == expected[0]
        assert discovery.business(ids["tenant"], expected[3].job_id) == expected[2]
        assert discovery.delivery(expected[3].job_id) is None
    with pytest.raises(NativeAssistedEntryConflict, match="^NATIVE_ASSISTED_SOURCE_PARENT_PROOF_MISSING$"):
        with owner.graph.transaction(ids["tenant"]):
            NativeAssistedEntrySourceProof(owner.graph).parent(ids["tenant"], event_id,
                NativeParserCommandBudget(owner.clock))
    assert _persisted(case) == original


@pytest.mark.parametrize("ending", ["commit", "rollback"])
def test_closed_discovery_cannot_be_consumed_in_a_later_actual_root(parser_admission_case, ending):
    case = parser_admission_case
    owner, ids = case[:2]
    event_id = _event_ids(case)["source"]
    original = _persisted(case)

    class EndRoot(Exception):
        pass

    try:
        with owner.graph.transaction(ids["tenant"]):
            root = owner.engine._local.root_transaction
            discovery = native_producer_discovery(_view(case, NativeParserCommandBudget(owner.clock)),
                ids["tenant"], event_id)
            assert discovery.event()["event_id"] == event_id
            if ending == "rollback":
                raise EndRoot()
    except EndRoot:
        assert ending == "rollback"
    with owner.graph.transaction(ids["tenant"]):
        assert owner.engine._local.root_transaction is not root
        with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARENT_DISCOVERY_REJECTED$"):
            discovery.event()
    assert _persisted(case) == original


def test_actual_original_deadline_expires_during_closed_fact_consumption(parser_admission_case):
    case = parser_admission_case
    owner, ids = case[:2]
    event_id = _event_ids(case)["source"]
    original = _persisted(case)
    with owner.graph.transaction(ids["tenant"]):
        budget = NativeParserCommandBudget(owner.clock)
        bounds = budget.deadline, budget._monotonic_deadline
        discovery = native_producer_discovery(_view(case, budget), ids["tenant"], event_id)
        time.sleep(max(0, budget._monotonic_deadline - time.monotonic()) + 0.01)
        with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_ROOT_DEADLINE_EXPIRED$"):
            discovery.event()
        assert (budget.deadline, budget._monotonic_deadline) == bounds
    assert _persisted(case) == original


def test_new_invocation_reads_actual_same_root_mutation_and_savepoint_rollback(parser_admission_case):
    case = parser_admission_case
    owner, ids = case[:2]
    event_id = _event_ids(case)["source"]
    original = _persisted(case)
    with owner.graph.transaction(ids["tenant"]):
        budget = NativeParserCommandBudget(owner.clock)
        view = _view(case, budget)
        first = native_producer_discovery(view, ids["tenant"], event_id)
        row = first.event()
        job_id = first.transition(ids["tenant"], row["payload"]["payload"]["transition_id"], row["aggregate_id"])["job_id"]
        original_business = first.business(ids["tenant"], job_id)
        budget()
        owner.engine.execute("SAVEPOINT discovery_negative")
        budget()
        assert owner.engine.execute("UPDATE workflow.jobs SET payload=jsonb_set(payload,'{input_sha256}',?::jsonb) "
            "WHERE tenant_id=? AND job_id=?", (json.dumps("0" * 64), ids["tenant"], job_id)).rowcount == 1
        changed = native_producer_discovery(view, ids["tenant"], event_id)
        assert changed.business(ids["tenant"], job_id)["payload"]["input_sha256"] == "0" * 64
        assert original_business["payload"]["input_sha256"] != "0" * 64
        budget()
        owner.engine.execute("ROLLBACK TO SAVEPOINT discovery_negative")
        restored = native_producer_discovery(view, ids["tenant"], event_id)
        assert restored.business(ids["tenant"], job_id) == original_business
        restored.finish()
    assert _persisted(case) == original


@pytest.mark.parametrize("transition_id", ["not-a-uuid", None])
def test_malformed_event_reference_never_causes_a_speculative_sql_cast(parser_admission_case, transition_id):
    case = parser_admission_case
    owner, ids, database = case[:3]
    event_id = _event_ids(case)["source"]
    with database.connect() as connection:
        assert connection.execute("UPDATE workflow.outbox_events SET payload=jsonb_set(payload,"
            "'{payload,transition_id}',%s::jsonb) WHERE tenant_id=%s AND event_id=%s",
            (json.dumps(transition_id), ids["tenant"], event_id)).rowcount == 1
    original = _persisted(case)
    with owner.graph.transaction(ids["tenant"]):
        discovery = native_producer_discovery(_view(case, NativeParserCommandBudget(owner.clock)),
            ids["tenant"], event_id)
        assert discovery.event()["payload"]["payload"]["transition_id"] == transition_id
        discovery.finish()
    # The complete original event/lineage verifier still rejects it.
    with pytest.raises((NativeAssistedEntryConflict, ValueError)):
        with owner.graph.transaction(ids["tenant"]):
            NativeAssistedEntrySourceProof(owner.graph).parent(ids["tenant"], event_id,
                NativeParserCommandBudget(owner.clock))
    assert _persisted(case) == original
