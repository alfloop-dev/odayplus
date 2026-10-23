"""Source-only terminal evidence differential/fence cases for Root execution.

Positive facts come from the existing HTTP/SQL manual-parent chain; providers,
principals and approvals remain explicitly synthetic fixture inputs. Reference
reads/snapshots are outside the helper's logical budget, never a budget restart.
These component comparisons make no whole-runtime or SLO qualification claim.
"""
import json
from dataclasses import replace
from uuid import uuid4

import pytest
from psycopg import sql

from modules.listing.application.native_intake_identity import (
    NativeIntakeIdentityConflict,
    NativeIntakeIdentityOwner,
)
from modules.listing.application.native_intake_parser_policy import NativeParserConflict
from modules.listing.application.native_intake_parser_prerequisites import NativeParserCommandBudget
from modules.listing.application.native_intake_producer_reads import _PAIR_SQL
from modules.listing.application.native_intake_source_policy import (
    NativeIntakeSourcePolicyConflict,
    NativeIntakeSourcePolicyOwner,
)
from modules.listing.application.native_intake_start import NativeIntakeStartOwner
from modules.listing.application.native_intake_terminal_evidence import (
    _TERMINAL_SQL,
    native_terminal_evidence,
)
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

_OWNERS = {"start": NativeIntakeStartOwner, "identity": NativeIntakeIdentityOwner,
    "source": NativeIntakeSourcePolicyOwner}
_PREFIX = {"start": "NATIVE_START", "identity": "NATIVE_IDENTITY", "source": "NATIVE_SOURCE_POLICY"}
# Explicit expectations for this genuine fixture's branches, independent of
# the reader's _GROUPS and of the header values being tested.
_BRANCHES = {
    "start": ("native-intake-start", "SUBMITTED", "CHECKING_IDENTITY",
        "intake.process", "intake.identity_check_started.v1"),
    "identity": ("native-intake-identity", "CHECKING_IDENTITY", "CHECKING_SOURCE_POLICY",
        "source_policy.evaluate", "intake.source_policy_started.v1"),
    "source": ("native-intake-source-policy", "CHECKING_SOURCE_POLICY", "AWAITING_ASSISTED_ENTRY",
        "intake.assisted_entry", "intake.assisted_entry_required.v1"),
}
_WORKFLOW_SQL = "SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?"
_AUDIT_SQL = (
    "SELECT after_value FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=? "
    "AND resource_type='intake' AND resource_id=? AND actor_subject_id IS NULL AND service_principal=? "
    "AND actor_role='SVC_INTAKE' AND action=? AND result='SUCCEEDED' AND causation_id=? AND correlation_id=?"
)
_OUTBOX_SQL = (
    "SELECT payload FROM workflow.outbox_events WHERE tenant_id=? AND event_id=? "
    "AND event_type='intake.state_changed' AND aggregate_type='intake' AND aggregate_id=? "
    "AND aggregate_version=? AND correlation_id=?"
)
_TABLES = {"transition": ("intake", "intake_stage_transitions", "transition_id"),
    "admission_audit": ("audit", "audit_events", "audit_event_id"),
    "result_audit": ("audit", "audit_events", "audit_event_id"),
    "outbox": ("workflow", "outbox_events", "event_id")}


def _view(case, budget):
    return NativeIntakeSourcePolicyOwner(case[0].graph)._producer_reads(budget)


def _persisted(case):
    """All user-table rows in this fixture's owned DB, including transitions.

    This read-only equality oracle does not construct positive evidence and
    runs outside every logical verifier budget. Also retain provider calls.
    """
    with case[2].connect() as connection:
        tables = connection.execute("SELECT schemaname,tablename FROM pg_catalog.pg_tables "
            "WHERE schemaname NOT LIKE 'pg_%' AND schemaname<>'information_schema' "
            "ORDER BY schemaname,tablename").fetchall()
        rows = {(schema, table): connection.execute(sql.SQL(
            "SELECT to_jsonb(t) FROM {} AS t ORDER BY to_jsonb(t)::text"
        ).format(sql.Identifier(schema, table))).fetchall() for schema, table in tables}
    return rows, list(case[5].calls)


def _observe(case, monkeypatch, corrupt=None):
    """Class-level observers forward real native calls, retaining eligibility."""
    calls = []
    query, query_one = PostgresEngine.query, PostgresEngine.query_one

    def many(engine, statement, params=()):
        rows = query(engine, statement, params)
        if engine is case[0].engine:
            calls.append((statement, params))
            if statement == _TERMINAL_SQL and corrupt is not None:
                return corrupt(rows)
        return rows

    def one(engine, statement, params=()):
        row = query_one(engine, statement, params)
        if engine is case[0].engine:
            calls.append((statement, params))
        return row

    monkeypatch.setattr(PostgresEngine, "query", many)
    monkeypatch.setattr(PostgresEngine, "query_one", one)
    return calls


def _batch_calls(calls):
    return [params for statement, params in calls if statement == _TERMINAL_SQL]


def _specs(job, role):
    """Original owner WHERE terms and independent explicit reference SQL.

    No terminal row values are copied into expected headers. The constants
    and branch terms also match Root's r59 predicate-translation artifact.
    """
    payload, result = json.loads(job["payload"]), json.loads(job["result"])
    principal, origin, target, permission, action = _BRANCHES[role]
    assert job["job_type"] == principal and job["status"] == "SUCCEEDED"
    assert result["processing_state"] == target
    tenant, intake, correlation = job["tenant_id"], job["aggregate_id"], job["correlation_id"]
    cause = payload["event_id"]
    transition = {"tenant_id": tenant, "transition_id": result["transition_id"],
        "intake_id": intake, "job_id": job["job_id"], "from_state": origin, "to_state": target,
        "resulting_version": result["version"], "expected_version": payload["intake_version"],
        "actor_subject_id": None, "service_principal": principal, "permission": permission,
        "causation_id": cause, "correlation_id": correlation}
    transition_sql = (
        "SELECT transition_id FROM intake.intake_stage_transitions WHERE tenant_id=? AND transition_id=? "
        "AND intake_id=? AND job_id=? AND from_state=? AND to_state=? AND resulting_version=? "
        "AND expected_version=? AND actor_subject_id IS NULL AND service_principal=? AND permission=?"
    )
    transition_params = (tenant, result["transition_id"], intake, job["job_id"], origin, target,
        result["version"], payload["intake_version"], principal, permission)
    if role != "start":
        proof = result["identity_evidence" if role == "identity" else "policy_decision"]
        transition["source_policy_version"] = proof["source_version"]
        transition_sql += " AND source_policy_version=?"
        transition_params += (proof["source_version"],)
        if role == "source":
            transition["reason_code"] = proof["reason_code"]
            transition_sql += " AND reason_code=?"
            transition_params += (proof["reason_code"],)
    transition_sql += " AND causation_id=? AND correlation_id=?"
    audit = {"tenant_id": tenant, "audit_event_id": result["audit_event_id"],
        "resource_type": "intake", "resource_id": intake, "actor_subject_id": None,
        "service_principal": principal, "actor_role": "SVC_INTAKE", "action": action,
        "result": "SUCCEEDED", "causation_id": cause, "correlation_id": correlation}
    outbox = {"tenant_id": tenant, "event_id": result["outbox_event_id"],
        "event_type": "intake.state_changed", "aggregate_type": "intake", "aggregate_id": intake,
        "aggregate_version": result["version"], "correlation_id": correlation}
    specs = {
        "transition": (transition, transition_sql, transition_params + (cause, correlation)),
        "result_audit": (audit, _AUDIT_SQL,
            (tenant, result["audit_event_id"], intake, principal, action, cause, correlation)),
        "outbox": (outbox, _OUTBOX_SQL,
            (tenant, result["outbox_event_id"], intake, result["version"], correlation)),
    }
    # Start's terminal owner intentionally does not read an admission audit.
    if role != "start":
        admission_id = payload["admission_receipt"]["audit_event_id"]
        admission_action = {"identity": "intake.identity_resolution_requested.v1",
            "source": "intake.source_policy_evaluation_requested.v1"}[role]
        specs["admission_audit"] = ({**audit, "audit_event_id": admission_id, "action": admission_action},
            _AUDIT_SQL, (tenant, admission_id, intake, principal, admission_action, cause, correlation))
    return specs


def _reference(engine, specs):
    return {name: engine.query_one(statement, params) for name, (_, statement, params) in specs.items()}


def _project(evidence, specs):
    return {name: getattr(evidence, name)(expected) for name, (expected, _, _) in specs.items()}


@pytest.mark.parametrize("role", ["start", "identity", "source"])
def test_fresh_batch_matches_explicit_sql_and_returns_isolated_closed_copies(
        parser_admission_case, monkeypatch, role):
    case = parser_admission_case
    owner, ids = case[:2]
    delivery = _source_delivery(case, role)
    original, calls = _persisted(case), _observe(case, monkeypatch)
    with owner.graph.transaction(ids["tenant"]):
        physical = owner.queue.get(delivery.job_id)
        workflow = owner.engine.query_one(_WORKFLOW_SQL, (ids["tenant"], delivery.job_id))
        specs = _specs(workflow, role)
        reference = _reference(owner.engine, specs)
        assert all(row is not None for row in reference.values())
        root = owner.engine.query_one("SELECT current_setting('transaction_isolation') AS isolation, "
            "current_setting('app.tenant_id') AS tenant")
        assert root == {"isolation": "serializable", "tenant": ids["tenant"]}
        budget = NativeParserCommandBudget(owner.clock)
        deadline = budget.deadline, budget._monotonic_deadline
        evidence = native_terminal_evidence(_view(case, budget), ids["tenant"], delivery.job_id)
        assert evidence is not None
        assert _batch_calls(calls) == [(ids["tenant"], delivery.job_id, delivery.job_id)]
        closed_at = len(calls)
        pair, projected = evidence.pair(), _project(evidence, specs)
        assert pair == (physical, workflow) and projected == reference
        pair[0].payload["test_only_local_mutation"] = "not persisted"
        pair[1]["version"] = -1
        for row in projected.values():
            row.clear()
        assert evidence.pair() == (physical, workflow)
        assert _project(evidence, specs) == reference
        evidence.finish()
        assert len(calls) == closed_at
        assert (budget.deadline, budget._monotonic_deadline) == deadline
    assert _persisted(case) == original


@pytest.mark.parametrize("role", ["start", "identity", "source"])
def test_actual_owner_matches_legacy_on_the_same_genuine_facts(parser_admission_case, monkeypatch, role):
    case = parser_admission_case
    owner, ids = case[:2]
    delivery = _source_delivery(case, role)
    original, calls = _persisted(case), _observe(case, monkeypatch)
    with owner.graph.transaction(ids["tenant"]):
        before = len(calls)
        assert native_terminal_evidence(owner.graph, ids["tenant"], delivery.job_id) is None
        assert len(calls) == before
        legacy = _OWNERS[role](owner.graph)._committed_result(delivery, ids["tenant"], 1)
        assert legacy is not None and _batch_calls(calls) == []
        assert (_WORKFLOW_SQL, (ids["tenant"], delivery.job_id)) in calls
    # Deliberate differential roots, not retries or replacement budgets.
    with owner.graph.transaction(ids["tenant"]):
        view = _view(case, NativeParserCommandBudget(owner.clock))
        current = _OWNERS[role](view)._committed_result(delivery, ids["tenant"], 1)
        assert current == legacy
        # Nested original producer verification may invoke another helper.
        assert _batch_calls(calls).count((ids["tenant"], delivery.job_id, delivery.job_id)) == 1
    assert _persisted(case) == original


@pytest.mark.parametrize("role,group", [
    (role, group) for role in ("start", "identity", "source")
    for group in ("transition", "result_audit", "outbox")
] + [("identity", "admission_audit"), ("source", "admission_audit")])
def test_real_header_corruption_is_excluded_by_sql_and_both_actual_owners(
        parser_admission_case, monkeypatch, role, group):
    case = parser_admission_case
    owner, ids, database = case[:3]
    delivery = _source_delivery(case, role)
    with owner.graph.transaction(ids["tenant"]):
        job = owner.engine.query_one(_WORKFLOW_SQL, (ids["tenant"], delivery.job_id))
        specs = _specs(job, role)
        assert _reference(owner.engine, specs)[group] is not None
    expected, statement, params = specs[group]
    schema, table, key = _TABLES[group]
    replacement = str(uuid4())
    with database.connect() as connection:
        select = sql.SQL("SELECT to_jsonb(t) FROM {} AS t WHERE tenant_id=%s AND {}=%s").format(
            sql.Identifier(schema, table), sql.Identifier(key))
        identity = (ids["tenant"], expected[key])
        old = connection.execute(select, identity).fetchone()[0]
        # Damage only a relational header; all JSON payload/audit cells remain
        # byte-equivalent database values. No new terminal history is claimed.
        assert connection.execute(sql.SQL("UPDATE {} SET correlation_id=%s WHERE tenant_id=%s AND {}=%s")
            .format(sql.Identifier(schema, table), sql.Identifier(key)), (replacement, *identity)).rowcount == 1
        assert connection.execute(select, identity).fetchone()[0] == {**old, "correlation_id": replacement}
    original, calls = _persisted(case), _observe(case, monkeypatch)
    with owner.graph.transaction(ids["tenant"]):
        assert owner.engine.query_one(statement, params) is None
    error = JobFenceRejectedError
    if group == "admission_audit":
        error = {"identity": NativeIntakeIdentityConflict, "source": NativeIntakeSourcePolicyConflict}[role]
        suffix = "_ADMISSION_AUDIT_MISSING"
    elif group == "transition":
        suffix = "_TERMINAL_RECEIPT_CONFLICT" if role == "start" else "_TERMINAL_TRANSITION_CONFLICT"
    else:
        suffix = "_TERMINAL_EVIDENCE_MISSING"
    for native in (False, True):
        begin = len(calls)
        with pytest.raises(error, match="^" + _PREFIX[role] + suffix + "$"):
            with owner.graph.transaction(ids["tenant"]):
                graph = _view(case, NativeParserCommandBudget(owner.clock)) if native else owner.graph
                _OWNERS[role](graph)._committed_result(delivery, ids["tenant"], 1)
        observed = calls[begin:]
        assert _batch_calls(observed) == ([(ids["tenant"], delivery.job_id, delivery.job_id)] if native else [])
        if native:
            assert not any(query in {_WORKFLOW_SQL, _PAIR_SQL} for query, _ in observed)
        assert _persisted(case) == original


@pytest.mark.parametrize("role", ["start", "identity", "source"])
def test_same_original_budget_reads_own_workflow_mutation_on_each_invocation(
        parser_admission_case, monkeypatch, role):
    case = parser_admission_case
    owner, ids = case[:2]
    delivery = _source_delivery(case, role)
    original, calls = _persisted(case), _observe(case, monkeypatch)

    class RollBackControlledMutation(Exception):
        pass

    with pytest.raises(RollBackControlledMutation):
        with owner.graph.transaction(ids["tenant"]):
            workflow = owner.engine.query_one(_WORKFLOW_SQL, (ids["tenant"], delivery.job_id))
            budget = NativeParserCommandBudget(owner.clock)
            deadline = budget.deadline, budget._monotonic_deadline
            view = _view(case, budget)
            first = native_terminal_evidence(view, ids["tenant"], delivery.job_id)
            assert first is not None and first.pair()[1] == workflow
            assert _batch_calls(calls) == [(ids["tenant"], delivery.job_id, delivery.job_id)]
            budget()
            assert owner.engine.execute("UPDATE workflow.jobs SET version=version+1 WHERE tenant_id=? AND job_id=?",
                (ids["tenant"], delivery.job_id)).rowcount == 1
            second = native_terminal_evidence(view, ids["tenant"], delivery.job_id)
            assert second is not None and second is not first
            assert _batch_calls(calls) == [(ids["tenant"], delivery.job_id, delivery.job_id)] * 2
            closed_at = len(calls)
            assert second.pair()[1] == {**workflow, "version": workflow["version"] + 1}
            assert first.pair()[1] == workflow
            assert second.pair()[0] == first.pair()[0]
            first.finish()
            second.finish()
            assert len(calls) == closed_at
            assert (budget.deadline, budget._monotonic_deadline) == deadline
            # This deliberately inconsistent version is only freshness evidence,
            # never a new terminal history claim; the owned root rolls it back.
            raise RollBackControlledMutation
    assert _persisted(case) == original


@pytest.mark.parametrize("after_read", [False, True])
def test_expired_original_budget_refuses_query_and_all_closed_projections(
        parser_admission_case, monkeypatch, after_read):
    case = parser_admission_case
    owner, ids = case[:2]
    delivery = _source_delivery(case)
    original, calls = _persisted(case), _observe(case, monkeypatch)
    with owner.graph.transaction(ids["tenant"]):
        specs = _specs(owner.engine.query_one(_WORKFLOW_SQL, (ids["tenant"], delivery.job_id)), "source")
        budget = NativeParserCommandBudget(owner.clock)
        deadline = budget.deadline
        view = _view(case, budget)
        evidence = native_terminal_evidence(view, ids["tenant"], delivery.job_id) if after_read else None
        if after_read:
            assert evidence is not None
            assert evidence.pair()[0] == delivery
            assert all(row is not None for row in _project(evidence, specs).values())
        budget._monotonic_deadline = 0  # Existing explicit expired-control technique; no clock patch.
        closed_at = len(calls)
        with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_ROOT_DEADLINE_EXPIRED$"):
            native_terminal_evidence(view, ids["tenant"], delivery.job_id)
        if after_read:
            for method, args in [(evidence.pair, ()), (evidence.finish, ())] + [
                (getattr(evidence, name), (expected,)) for name, (expected, _, _) in specs.items()
            ]:
                with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_ROOT_DEADLINE_EXPIRED$"):
                    method(*args)
        assert len(calls) == closed_at and budget.deadline == deadline
        assert _batch_calls(calls) == ([(ids["tenant"], delivery.job_id, delivery.job_id)] if after_read else [])
    assert _persisted(case) == original


@pytest.mark.parametrize("after_read", [False, True])
def test_released_captured_root_refuses_query_and_all_closed_projections(
        parser_admission_case, monkeypatch, after_read):
    case = parser_admission_case
    owner, ids = case[:2]
    delivery = _source_delivery(case)
    original, calls = _persisted(case), _observe(case, monkeypatch)
    with owner.graph.transaction(ids["tenant"]):
        specs = _specs(owner.engine.query_one(_WORKFLOW_SQL, (ids["tenant"], delivery.job_id)), "source")
        view = _view(case, NativeParserCommandBudget(owner.clock))
        connection = owner.engine._local.connection
        assert view._connection is connection and not connection.closed
        evidence = native_terminal_evidence(view, ids["tenant"], delivery.job_id) if after_read else None
        if after_read:
            assert evidence is not None
            assert evidence.pair()[0] == delivery
            assert all(row is not None for row in _project(evidence, specs).values())
    assert owner.engine._local.connection is None and view._connection is connection
    closed_at = len(calls)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARENT_TERMINAL_EVIDENCE_REJECTED$"):
        native_terminal_evidence(view, ids["tenant"], delivery.job_id)
    if after_read:
        for method, args in [(evidence.pair, ()), (evidence.finish, ())] + [
            (getattr(evidence, name), (expected,)) for name, (expected, _, _) in specs.items()
        ]:
            with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARENT_TERMINAL_EVIDENCE_REJECTED$"):
                method(*args)
    assert len(calls) == closed_at
    assert _batch_calls(calls) == ([(ids["tenant"], delivery.job_id, delivery.job_id)] if after_read else [])
    assert _persisted(case) == original


def test_wrong_current_tenant_refuses_the_actual_select_without_retry(parser_admission_case, monkeypatch):
    case = parser_admission_case
    owner, ids = case[:2]
    delivery = _source_delivery(case)
    original, calls = _persisted(case), _observe(case, monkeypatch)
    other = str(uuid4())
    with owner.graph.transaction(other):
        view = _view(case, NativeParserCommandBudget(owner.clock))
        with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARENT_TERMINAL_EVIDENCE_REJECTED$"):
            NativeIntakeSourcePolicyOwner(view)._committed_result(delivery, ids["tenant"], 1)
    assert _batch_calls(calls) == [(ids["tenant"], delivery.job_id, delivery.job_id)]
    assert not any(statement in {_PAIR_SQL, _WORKFLOW_SQL} for statement, _ in calls)
    assert _persisted(case) == original


@pytest.mark.parametrize("adapter", ["unknown_callback", "rejecting_callback", "queue", "engine"])
def test_callback_and_instance_fault_adapters_choose_existing_reads_before_batch_sql(
        parser_admission_case, monkeypatch, adapter):
    case = parser_admission_case
    owner, ids = case[:2]
    delivery = _source_delivery(case)
    original = _persisted(case)
    with owner.graph.transaction(ids["tenant"]):
        legacy = NativeIntakeSourcePolicyOwner(owner.graph)._committed_result(delivery, ids["tenant"], 1)
        assert legacy is not None
    calls, adapted = _observe(case, monkeypatch), []
    if adapter == "queue":
        get = owner.queue.get

        def changed_fence(job_id):
            actual = get(job_id)
            if job_id == delivery.job_id:
                adapted.append(job_id)
                return replace(actual, fence_token=actual.fence_token + 1)
            return actual

        monkeypatch.setattr(owner.queue, "get", changed_fence)
    elif adapter == "engine":
        query_one = owner.engine.query_one

        def changed_status(statement, params=()):
            actual = query_one(statement, params)
            if statement == _WORKFLOW_SQL and params == (ids["tenant"], delivery.job_id):
                adapted.append(statement)
                return {**actual, "status": "RUNNING"}
            return actual

        monkeypatch.setattr(owner.engine, "query_one", changed_status)
    with owner.graph.transaction(ids["tenant"]):
        budget = NativeParserCommandBudget(owner.clock)

        def unknown_before():
            adapted.append("before")
            if adapter == "rejecting_callback":
                raise RuntimeError("explicit unknown callback refusal")
            budget()

        view = _view(case, unknown_before if "callback" in adapter else budget)
        before = len(calls)
        assert native_terminal_evidence(view, ids["tenant"], delivery.job_id) is None
        assert len(calls) == before and adapted == []
        verifier = NativeIntakeSourcePolicyOwner(view)
        if adapter == "queue":
            with pytest.raises(JobFenceRejectedError, match="^NATIVE_SOURCE_POLICY_TERMINAL_RECEIPT_CONFLICT$"):
                verifier._committed_result(delivery, ids["tenant"], 1)
            assert adapted == [delivery.job_id]
        elif adapter == "rejecting_callback":
            with pytest.raises(RuntimeError, match="^explicit unknown callback refusal$"):
                verifier._committed_result(delivery, ids["tenant"], 1)
            assert adapted == ["before"] and len(calls) == before
        else:
            actual = verifier._committed_result(delivery, ids["tenant"], 1)
            assert actual == (None if adapter == "engine" else legacy)
            assert adapted
        assert _batch_calls(calls) == []
    assert _persisted(case) == original


@pytest.mark.parametrize("damage", ["duplicate", "missing_column", "workflow_tenant", "physical_key"])
def test_one_actual_batch_with_corrupt_transport_refuses_without_retry_or_fallback(
        parser_admission_case, monkeypatch, damage):
    case = parser_admission_case
    owner, ids = case[:2]
    delivery = _source_delivery(case)
    original = _persisted(case)

    def corrupt(rows):
        # Only the returned transport changes, after forwarding real SQL.
        # These rows are negative transport evidence, never positive fixtures.
        assert len(rows) == 1
        if damage == "duplicate":
            return [*rows, dict(rows[0])]
        row = dict(rows[0])
        if damage == "missing_column":
            del row["terminal_audit_actor_role"]
        elif damage == "workflow_tenant":
            row["tenant_id"] = str(uuid4())
        else:
            row["terminal_physical_job_id"] = str(uuid4())
        return [row]

    calls = _observe(case, monkeypatch, corrupt)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARENT_TERMINAL_EVIDENCE_REJECTED$"):
        with owner.graph.transaction(ids["tenant"]):
            view = _view(case, NativeParserCommandBudget(owner.clock))
            NativeIntakeSourcePolicyOwner(view)._committed_result(delivery, ids["tenant"], 1)
    assert _batch_calls(calls) == [(ids["tenant"], delivery.job_id, delivery.job_id)]
    assert not any(statement in {_PAIR_SQL, _WORKFLOW_SQL} for statement, _ in calls)
    assert _persisted(case) == original
