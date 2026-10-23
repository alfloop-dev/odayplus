"""Actual canonical rows, fresh final proof and the unchanged parser deadline."""
import time

import pytest

from modules.listing.application.native_intake_assisted_entry_request_proof import (
    _object,
    request_audit_id,
)
from modules.listing.application.native_intake_parser_input import NativeParserManualInputReader
from modules.listing.application.native_intake_parser_policy import NativeParserConflict
from modules.listing.application.native_intake_parser_prerequisites import NativeParserCommandBudget
from modules.listing.application.native_intake_pending_commands import NativeIntakeCommandBudget
from tests.integration.test_native_intake_parser_admission_postgresql import (
    admit,
    persisted,
)
from tests.integration.test_native_intake_parser_admission_postgresql import (
    authority as authority,
)
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
AUDITS = ("request", "admission", "acceptance", "completion")
EVENTS = ("request", "state", "snapshot")
ROLES = [("audit", role) for role in AUDITS] + [("event", role) for role in EVENTS]


def actual_ids(case):
    owner, ids, _, result, *_ = case
    with owner.graph.transaction(ids["tenant"]):
        job = owner.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
            (ids["tenant"], result["job_id"]))
    payload = _object(job["payload"])
    return {"audit": {"request": request_audit_id(payload["request_id"]),
        "admission": payload["admission_receipt"]["audit_event_id"],
        "acceptance": payload["acceptance_receipt"]["audit_event_id"], "completion": result["audit_event_id"]},
        "event": {"request": payload["request_id"], "state": result["outbox_event_id"],
            "snapshot": result["snapshot_created_event_id"]}}


@pytest.mark.parametrize("kind,role", ROLES)
def test_missing_actual_history_row_rejects_without_any_parser_admission(parser_admission_case, kind, role):
    case = parser_admission_case
    table, column = ("audit.audit_events", "audit_event_id") if kind == "audit" else ("workflow.outbox_events", "event_id")
    with case[2].connect() as connection:
        assert connection.execute(f"DELETE FROM {table} WHERE {column}=%s",
            (actual_ids(case)[kind][role],)).rowcount == 1
    previous = persisted(case)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_MANUAL_INPUT_REJECTED$"):
        admit(case)
    assert persisted(case) == previous


@pytest.mark.parametrize("role", AUDITS)
@pytest.mark.parametrize("column,value", [("service_principal", "unexpected-service"),
    ("resource_type", "unexpected-resource"), ("action", "intake.parser_admitted.v1")])
def test_batched_audit_preserves_every_original_SQL_predicate(parser_admission_case, role, column, value):
    case = parser_admission_case
    with case[2].connect() as connection:
        assert connection.execute(f"UPDATE audit.audit_events SET {column}=%s WHERE audit_event_id=%s",
            (value, actual_ids(case)["audit"][role])).rowcount == 1
    previous = persisted(case)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_MANUAL_INPUT_REJECTED$"):
        admit(case)
    assert persisted(case) == previous


@pytest.mark.parametrize("role", EVENTS)
def test_event_boolean_version_is_not_an_integer(parser_admission_case, role):
    case = parser_admission_case
    with case[2].connect() as connection:
        assert connection.execute("UPDATE workflow.outbox_events SET payload=jsonb_set(payload,"
            "'{event_version}','false'::jsonb) WHERE event_id=%s", (actual_ids(case)["event"][role],)).rowcount == 1
    previous = persisted(case)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_MANUAL_INPUT_REJECTED$"):
        admit(case)
    assert persisted(case) == previous


@pytest.mark.parametrize("kind,role", ROLES)
def test_final_positive_boundary_reloads_own_root_mutations_and_rolls_every_write_back(
        parser_admission_case, monkeypatch, kind, role):
    case = parser_admission_case
    owner = case[0]
    targets, previous, confirm, mutated = actual_ids(case), persisted(case), owner._confirm, []

    def change_actual_history(*args, **kwargs):
        # All tentative admission writes/audit already exist. The next action
        # must reconstruct the final proof, including our own preceding write.
        if kind == "audit":
            statement = "UPDATE audit.audit_events SET after_value='{}'::jsonb WHERE audit_event_id=?"
        else:
            statement = "UPDATE workflow.outbox_events SET payload=jsonb_set(payload,'{event_version}','false'::jsonb) WHERE event_id=?"
        assert owner.engine.execute(statement, (targets[kind][role],)).rowcount == 1
        mutated.append(True)
        return confirm(*args, **kwargs)

    monkeypatch.setattr(owner, "_confirm", change_actual_history)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_MANUAL_INPUT_REJECTED$"):
        admit(case)
    assert mutated == [True]
    assert persisted(case) == previous


def test_real_batch_read_elapsed_time_cannot_restart_original_monotonic_deadline(parser_admission_case, monkeypatch):
    case = parser_admission_case
    owner = case[0]
    previous, query, delayed = persisted(case), owner.engine.query, []

    def delayed_canonical_rows(statement, parameters=()):
        result = query(statement, parameters)
        if statement.startswith("SELECT * FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=ANY("):
            assert not delayed
            delayed.append(True)
            time.sleep(.8)
        return result

    monkeypatch.setattr(owner.engine, "query", delayed_canonical_rows)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_ROOT_DEADLINE_EXPIRED$"):
        admit(case)
    assert delayed == [True]
    assert persisted(case) == previous


def test_batch_and_original_reader_prove_identical_actual_history_with_fewer_SQL_guards(parser_admission_case, monkeypatch):
    case = parser_admission_case
    owner, ids, _, result, *_ = case
    query_one, query = owner.engine.query_one, owner.engine.query
    observations = []

    def observe_one(statement, parameters=()):
        observations.append(statement)
        return query_one(statement, parameters)

    def observe_many(statement, parameters=()):
        observations.append(statement)
        return query(statement, parameters)

    monkeypatch.setattr(owner.engine, "query_one", observe_one)
    monkeypatch.setattr(owner.engine, "query", observe_many)
    results, traces = [], []
    previous = persisted(case)
    for budget_type in (NativeIntakeCommandBudget, NativeParserCommandBudget):
        with owner.graph.transaction(ids["tenant"]):
            budget = budget_type(owner.clock)
            budget()
            intake = owner.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE",
                (ids["tenant"], result["intake_id"]))
            observations.clear()
            results.append(NativeParserManualInputReader(owner.graph).read(intake, before=budget))
            budget()
            traces.append(observations[:])
    assert results[0] == results[1]
    # These are observed calls to the real engine, not mock rows or a latency
    # guarantee. Complete parent readers execute in both actual SQL roots.
    assert len(traces[0]) - len(traces[1]) == 14
    assert sum(s.startswith("WITH observed AS MATERIALIZED") for s in traces[0]) - sum(
        s.startswith("WITH observed AS MATERIALIZED") for s in traces[1]) == 7
    assert persisted(case) == previous
