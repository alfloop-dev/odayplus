"""A real SQL NULL comparison must not become Python missing-value equality.

Start from the genuine API-created parent chain, then explicitly corrupt one
owned audit header. This is a negative transport/predicate case, not positive
terminal-history or authorization proof.
"""
import pytest

from modules.listing.application.native_intake_parser_prerequisites import NativeParserCommandBudget
from modules.listing.application.native_intake_terminal_evidence import native_terminal_evidence
from tests.integration.test_native_intake_parent_terminal_rows_postgresql import (
    _source_delivery,
    _view,
    persisted,
)
from tests.integration.test_native_intake_parent_terminal_rows_postgresql import (
    authority as authority,
)
from tests.integration.test_native_intake_parent_terminal_rows_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_parent_terminal_rows_postgresql import (
    identity_case as identity_case,
)
from tests.integration.test_native_intake_parent_terminal_rows_postgresql import (
    intake_blank_db as intake_blank_db,
)
from tests.integration.test_native_intake_parent_terminal_rows_postgresql import (
    native_api as native_api,
)
from tests.integration.test_native_intake_parent_terminal_rows_postgresql import (
    parser_admission_case as parser_admission_case,
)
from tests.integration.test_native_intake_parent_terminal_rows_postgresql import (
    parser_realm as parser_realm,
)
from tests.integration.test_native_intake_parent_terminal_rows_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_parent_terminal_rows_postgresql import (
    start_case as start_case,
)

pytestmark = pytest.mark.requires_live_env


def test_parameter_NULL_never_matches_a_null_audit_causation(parser_admission_case):
    case = parser_admission_case
    owner, ids, database = case[:3]
    delivery = _source_delivery(case, "start")
    with database.connect() as connection:
        job = connection.execute(
            "SELECT aggregate_id,result,correlation_id FROM workflow.jobs WHERE tenant_id=%s AND job_id=%s",
            (ids["tenant"], delivery.job_id)).fetchone()
        intake_id, result, correlation = str(job[0]), job[1], str(job[2])
        audit_id = result["audit_event_id"]
        assert connection.execute(
            "UPDATE audit.audit_events SET causation_id=NULL WHERE tenant_id=%s AND audit_event_id=%s",
            (ids["tenant"], audit_id)).rowcount == 1
    original = persisted(case)
    with owner.graph.transaction(ids["tenant"]):
        # SQL `= NULL` is unknown even when the stored value is also NULL.
        # Actor NULL below is a different, intentional IS NULL predicate.
        actual_SQL = owner.engine.query_one(
            "SELECT after_value FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=? "
            "AND resource_type='intake' AND resource_id=? AND actor_subject_id IS NULL AND service_principal=? "
            "AND actor_role='SVC_INTAKE' AND action='intake.identity_check_started.v1' AND result='SUCCEEDED' "
            "AND causation_id=? AND correlation_id=?",
            (ids["tenant"], audit_id, intake_id, "native-intake-start", None, correlation))
        assert actual_SQL is None
        facts = native_terminal_evidence(_view(case, NativeParserCommandBudget(owner.clock)),
            ids["tenant"], delivery.job_id)
        assert facts is not None
        assert facts.result_audit({
            "tenant_id": ids["tenant"], "audit_event_id": audit_id,
            "resource_type": "intake", "resource_id": intake_id, "actor_subject_id": None,
            "service_principal": "native-intake-start", "actor_role": "SVC_INTAKE",
            "action": "intake.identity_check_started.v1", "result": "SUCCEEDED",
            "causation_id": None, "correlation_id": correlation,
        }) is None
    assert persisted(case) == original


def test_unknown_budget_subclass_keeps_its_original_guarded_reads(parser_admission_case, monkeypatch):
    case = parser_admission_case
    owner, ids = case[:2]
    delivery = _source_delivery(case, "start")
    original, calls, checked = persisted(case), [], []
    engine_type = type(owner.engine)
    actual_query = engine_type.query

    def observe(engine, statement, params=()):
        calls.append(statement)
        return actual_query(engine, statement, params)

    class UnknownBudget(NativeParserCommandBudget):
        def check_in_memory(self):
            checked.append("original guard")
            return super().check_in_memory()

    monkeypatch.setattr(engine_type, "query", observe)
    with owner.graph.transaction(ids["tenant"]):
        budget = UnknownBudget(owner.clock)
        view = _view(case, budget)
        before = len(calls), len(checked)
        assert native_terminal_evidence(view, ids["tenant"], delivery.job_id) is None
        assert (len(calls), len(checked)) == before
        # The original adapter still invokes the unknown callback. This
        # explicitly forwards its original deadline; it supplies no authority.
        view.queue.get(delivery.job_id)
        assert len(checked) > before[1]
    assert persisted(case) == original
