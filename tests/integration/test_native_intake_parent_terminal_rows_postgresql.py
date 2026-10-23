"""Fresh joined terminal rows from genuine canonical manual-parent fixtures.

The existing fixture performs real API/SQL producer work with explicitly
synthetic provider data. Row corruption and alternate adapters below are
negative tests or decoder comparisons, never fabricated positive history.
"""
from dataclasses import replace
from uuid import uuid4

import pytest

from modules.listing.application.native_intake_identity import NativeIntakeIdentityOwner
from modules.listing.application.native_intake_parser_policy import NativeParserConflict
from modules.listing.application.native_intake_parser_prerequisites import NativeParserCommandBudget
from modules.listing.application.native_intake_producer_reads import _PAIR_SQL, native_terminal_pair
from modules.listing.application.native_intake_source_policy import NativeIntakeSourcePolicyOwner
from modules.listing.application.native_intake_start import NativeIntakeStartOwner
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
    persisted,
)
from tests.integration.test_native_intake_parser_admission_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_parser_admission_postgresql import (
    start_case as start_case,
)

pytestmark = pytest.mark.requires_live_env


def _view(case, budget):
    return NativeIntakeSourcePolicyOwner(case[0].graph)._producer_reads(budget)


def _source_delivery(case, role="source"):
    owner, ids = case[:2]
    with owner.graph.transaction(ids["tenant"]):
        manual = owner.graph._one("SELECT payload FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
            (ids["tenant"], case[3]["job_id"]))
        event_id = manual["payload"]["source_policy_event_id"]
        for current_role in ("source", "identity", "start"):
            event = owner.graph._one("SELECT payload FROM workflow.outbox_events WHERE tenant_id=? AND event_id=?",
                (ids["tenant"], event_id))["payload"]
            transition = owner.graph._one("SELECT job_id FROM intake.intake_stage_transitions "
                "WHERE tenant_id=? AND transition_id=?", (ids["tenant"], event["payload"]["transition_id"]))
            delivery = owner.queue.get(transition["job_id"])
            assert delivery is not None
            if current_role == role:
                return delivery
            job = owner.graph._one("SELECT payload FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                (ids["tenant"], delivery.job_id))
            event_id = job["payload"]["event_id"]
    raise AssertionError("An explicit existing parent role is required")


def test_one_fresh_native_query_preserves_every_original_terminal_cell(parser_admission_case, monkeypatch):
    case = parser_admission_case
    owner, ids = case[:2]
    job_id = case[3]["job_id"]
    original, queries = persisted(case), []
    query = PostgresEngine.query

    def observe(engine, statement, params=()):
        if engine is owner.engine and statement == _PAIR_SQL:
            queries.append(params)
        return query(engine, statement, params)

    monkeypatch.setattr(PostgresEngine, "query", observe)
    with owner.graph.transaction(ids["tenant"]):
        expected_physical = owner.queue.get(job_id)
        expected_workflow = owner.engine.query_one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
            (ids["tenant"], job_id))
        view = _view(case, NativeParserCommandBudget(owner.clock))
        first = native_terminal_pair(view, ids["tenant"], job_id)
        assert first is not None and first == (expected_physical, expected_workflow)
        assert queries == [(ids["tenant"], job_id, job_id)]
        # Local decoder output is not shared with any subsequent read.
        first[0].payload["test_only_local_value"] = "not persisted"
        first[1]["version"] = -1
        second = native_terminal_pair(view, ids["tenant"], job_id)
        assert second == (expected_physical, expected_workflow)
        assert len(queries) == 2 and first[0].payload is not second[0].payload
    assert persisted(case) == original


@pytest.mark.parametrize("role,owner_type,code", [
    ("source", NativeIntakeSourcePolicyOwner, "NATIVE_SOURCE_POLICY_TERMINAL_RECEIPT_CONFLICT"),
    ("identity", NativeIntakeIdentityOwner, "NATIVE_IDENTITY_TERMINAL_RECEIPT_CONFLICT"),
    ("start", NativeIntakeStartOwner, "NATIVE_START_TERMINAL_RECEIPT_CONFLICT"),
])
@pytest.mark.parametrize("damage", ["tenant_id", "job_id", "unknown_field"])
def test_actual_discovered_parent_ticket_is_bound_even_when_old_token_bytes_remain(
        parser_admission_case, role, owner_type, code, damage):
    case = parser_admission_case
    owner, ids, database = case[:3]
    original_delivery = _source_delivery(case, role)
    job_id = original_delivery.job_id
    key = "unknown_test_field" if damage == "unknown_field" else damage
    replacement = str(uuid4())
    with database.connect() as conn:
        # A real negative transport mutation. The old signature bytes stay
        # untouched, so equality of two later reads cannot prove this frame.
        assert conn.execute("UPDATE odp_runtime.durable_jobs SET payload_json=jsonb_set(payload_json,"
            "%s::text[],to_jsonb(%s::text)) WHERE job_id=%s", ([key], replacement, job_id)).rowcount == 1
    original = persisted(case)
    with pytest.raises(JobFenceRejectedError, match="^" + code + "$"):
        with owner.graph.transaction(ids["tenant"]):
            discovered = owner.queue.get(job_id)
            assert discovered.payload[key] == replacement
            assert discovered.payload["attempt_token"] == original_delivery.payload["attempt_token"]
            view = _view(case, NativeParserCommandBudget(owner.clock))
            owner_type(view)._committed_result(discovered, ids["tenant"], 1)
    assert persisted(case) == original


@pytest.mark.parametrize("missing", ["workflow", "physical", "both"])
def test_absent_sides_are_independent_and_never_reconstructed(parser_admission_case, missing):
    case = parser_admission_case
    owner, ids, database = case[:3]
    job_id = str(uuid4()) if missing == "both" else case[3]["job_id"]
    # Observe an absent tenant-scoped workflow side alongside a genuine
    # physical delivery. Deleting its real referenced job violates the native
    # transition FK before the read under test can run.
    tenant = str(uuid4()) if missing == "workflow" else ids["tenant"]
    with database.connect() as conn:
        if missing == "physical":
            assert conn.execute("DELETE FROM odp_runtime.durable_jobs WHERE job_id=%s", (job_id,)).rowcount == 1
    original = persisted(case)
    with owner.graph.transaction(tenant):
        pair = native_terminal_pair(_view(case, NativeParserCommandBudget(owner.clock)), tenant, job_id)
        assert pair is not None
        assert (pair[0] is None) == (missing in {"physical", "both"})
        assert (pair[1] is None) == (missing in {"workflow", "both"})
        if missing == "workflow":
            assert pair[0] == owner.queue.get(job_id)
            assert pair[0].payload["tenant_id"] == ids["tenant"] != tenant
    assert persisted(case) == original


def test_each_same_root_read_observes_actual_own_mutations(parser_admission_case):
    case = parser_admission_case
    owner, ids = case[:2]
    job_id = case[3]["job_id"]
    with owner.graph.transaction(ids["tenant"]):
        budget = NativeParserCommandBudget(owner.clock)
        view = _view(case, budget)
        first = native_terminal_pair(view, ids["tenant"], job_id)
        budget()
        assert owner.engine.execute("UPDATE workflow.jobs SET version=version+1 WHERE tenant_id=? AND job_id=?",
            (ids["tenant"], job_id)).rowcount == 1
        budget()
        assert owner.engine.execute("UPDATE durable_jobs SET version=version+1 WHERE job_id=?", (job_id,)).rowcount == 1
        second = native_terminal_pair(view, ids["tenant"], job_id)
        assert second[0].version == first[0].version + 1
        assert second[1]["version"] == first[1]["version"] + 1
        assert second == (owner.queue.get(job_id), owner.engine.query_one(
            "SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?", (ids["tenant"], job_id)))


@pytest.mark.parametrize("status", ["retrying", "dead_letter"])
def test_legacy_delivery_normalization_uses_the_original_native_decoder(parser_admission_case, status):
    case = parser_admission_case
    owner, ids, database = case[:3]
    job_id = case[3]["job_id"]
    with database.connect() as conn:
        assert conn.execute("UPDATE odp_runtime.durable_jobs SET status=%s,delivery_state=NULL,"
            "attempts=3,max_retries=3 WHERE job_id=%s", (status, job_id)).rowcount == 1
    with owner.graph.transaction(ids["tenant"]):
        expected = owner.queue.get(job_id)
        pair = native_terminal_pair(_view(case, NativeParserCommandBudget(owner.clock)), ids["tenant"], job_id)
    assert pair[0] == expected
    assert pair[0].status.value == ("queued" if status == "retrying" else "failed")
    assert pair[0].delivery_state.value == ("retrying" if status == "retrying" else "dead_letter")


def test_current_root_tenant_cannot_be_substituted(parser_admission_case):
    case = parser_admission_case
    owner, ids = case[:2]
    original = persisted(case)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARENT_TERMINAL_ROWS_REJECTED$"):
        with owner.graph.transaction(ids["tenant"]):
            native_terminal_pair(_view(case, NativeParserCommandBudget(owner.clock)), str(uuid4()), case[3]["job_id"])
    assert persisted(case) == original


def test_a_view_cannot_read_after_its_captured_connection_is_released(parser_admission_case):
    case = parser_admission_case
    owner, ids = case[:2]
    with owner.graph.transaction(ids["tenant"]):
        view = _view(case, NativeParserCommandBudget(owner.clock))
    original = persisted(case)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARENT_TERMINAL_ROWS_REJECTED$"):
        native_terminal_pair(view, ids["tenant"], case[3]["job_id"])
    assert persisted(case) == original


def test_expired_original_budget_refuses_before_the_fact_query(parser_admission_case, monkeypatch):
    case = parser_admission_case
    owner, ids = case[:2]
    original, queries = persisted(case), []
    query = PostgresEngine.query

    def observe(engine, statement, params=()):
        if statement == _PAIR_SQL:
            queries.append(statement)
        return query(engine, statement, params)

    monkeypatch.setattr(PostgresEngine, "query", observe)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_ROOT_DEADLINE_EXPIRED$"):
        with owner.graph.transaction(ids["tenant"]):
            budget = NativeParserCommandBudget(owner.clock)
            budget._monotonic_deadline = 0  # Explicit expired-control negative fixture.
            native_terminal_pair(_view(case, budget), ids["tenant"], case[3]["job_id"])
    assert queries == [] and persisted(case) == original


def test_changed_queue_reader_remains_authoritative_and_uses_original_predicates(parser_admission_case, monkeypatch):
    case = parser_admission_case
    owner, ids = case[:2]
    delivery = _source_delivery(case)
    get, calls = owner.queue.get, []

    def changed_fence(job_id):
        value = get(job_id)
        if job_id == delivery.job_id:
            calls.append(job_id)
            return replace(value, fence_token=value.fence_token + 1)
        return value

    original = persisted(case)
    monkeypatch.setattr(owner.queue, "get", changed_fence)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_SOURCE_POLICY_TERMINAL_RECEIPT_CONFLICT$"):
        with owner.graph.transaction(ids["tenant"]):
            view = _view(case, NativeParserCommandBudget(owner.clock))
            assert native_terminal_pair(view, ids["tenant"], delivery.job_id) is None
            NativeIntakeSourcePolicyOwner(view)._committed_result(delivery, ids["tenant"], 1)
    assert calls == [delivery.job_id] and persisted(case) == original


def test_instance_engine_adapter_keeps_the_original_workflow_read(parser_admission_case, monkeypatch):
    case = parser_admission_case
    owner, ids = case[:2]
    delivery = _source_delivery(case)
    query, calls = owner.engine.query_one, []

    def changed_status(statement, params=()):
        value = query(statement, params)
        if statement == "SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?" and params[1] == delivery.job_id:
            calls.append(statement)
            return {**value, "status": "RUNNING"}
        return value

    original = persisted(case)
    monkeypatch.setattr(owner.engine, "query_one", changed_status)
    with owner.graph.transaction(ids["tenant"]):
        view = _view(case, NativeParserCommandBudget(owner.clock))
        assert native_terminal_pair(view, ids["tenant"], delivery.job_id) is None
        assert NativeIntakeSourcePolicyOwner(view)._committed_result(delivery, ids["tenant"], 1) is None
    assert len(calls) == 1 and persisted(case) == original


@pytest.mark.parametrize("damage", ["duplicate", "missing_column", "workflow_tenant", "physical_job"])
def test_malformed_join_transport_is_not_collapsed_or_filled(parser_admission_case, monkeypatch, damage):
    case = parser_admission_case
    owner, ids = case[:2]
    query = PostgresEngine.query

    def corrupt(engine, statement, params=()):
        rows = query(engine, statement, params)
        if engine is not owner.engine or statement != _PAIR_SQL:
            return rows
        if damage == "duplicate":
            return [*rows, dict(rows[0])]
        row = dict(rows[0])
        if damage == "missing_column":
            del row["physical_max_retries"]
        elif damage == "workflow_tenant":
            row["tenant_id"] = str(uuid4())
        else:
            row["physical_job_id"] = str(uuid4())
        return [row]

    original = persisted(case)
    monkeypatch.setattr(PostgresEngine, "query", corrupt)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARENT_TERMINAL_ROWS_REJECTED$"):
        with owner.graph.transaction(ids["tenant"]):
            native_terminal_pair(_view(case, NativeParserCommandBudget(owner.clock)), ids["tenant"], case[3]["job_id"])
    assert persisted(case) == original
