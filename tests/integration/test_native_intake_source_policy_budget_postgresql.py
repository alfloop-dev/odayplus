"""Original source-policy SQL deadline adoption; source only for Root execution.

Timeout controls use an isolated real PostgreSQL engine. Terminal facts come
from parser_admission_case's actual HTTP/SQL producer chain; its provider,
identity, approval and storage inputs remain explicitly synthetic. Separate
legacy/reference roots are differential oracles, never retries. No timing
measurement here qualifies performance, whole-runtime behavior or rollout.
"""
from datetime import timedelta

import pytest

from modules.listing.application.native_intake_producer_reads import _PAIR_SQL
from modules.listing.application.native_intake_source_policy import (
    ADMISSION_BUDGET_MILLISECONDS,
    NativeIntakeSourcePolicyConflict,
    NativeIntakeSourcePolicyOwner,
)
from modules.listing.application.native_intake_source_policy_budget import (
    NativeSourcePolicyAdmissionBudget,
)
from modules.listing.application.native_intake_terminal_evidence import native_terminal_evidence
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
from tests.integration.test_native_intake_terminal_evidence_postgresql import (
    _batch_calls,
    _observe,
    _persisted,
)

pytestmark = pytest.mark.requires_live_env


def _settings(engine):
    return engine.query_one("SELECT current_setting('statement_timeout') AS statement, "
        "current_setting('lock_timeout') AS lock")


def _milliseconds(settings):
    assert settings["statement"] == settings["lock"]
    assert all(value.endswith("ms") for value in settings.values())
    values = {name: int(value[:-2]) for name, value in settings.items()}
    assert all(1 <= value < ADMISSION_BUDGET_MILLISECONDS for value in values.values())
    return values


def test_adoption_charges_elapsed_original_sql_root_and_keeps_both_deadlines(
        intake_blank_db, record_property):
    # One real pooled connection also makes the post-root GUC check observe
    # the same PostgreSQL session; no connection or clock adapter is installed.
    engine = PostgresEngine(intake_blank_db.url(), max_pool_size=1,
        bootstrap=False, validate_schema=False)
    source = object.__new__(NativeIntakeSourcePolicyOwner)
    source.engine = engine  # This clock-only component needs no graph facts.
    try:
        with engine.transaction():
            connection, baseline = engine._local.connection, _settings(engine)
        with engine.transaction():
            assert engine._local.connection is connection
            engine.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            original_bound = source._clock() + timedelta(milliseconds=ADMISSION_BUDGET_MILLISECONDS)
            source._admission_budget(original_bound)
            first = _milliseconds(_settings(engine))
            # Actual elapsed original budget before adoption, not a benchmark
            # or an attempt to exhaust the root.
            engine.query_one("SELECT pg_sleep(0.015)")
            before = NativeSourcePolicyAdmissionBudget(source, sql_deadline=original_bound)
            original = original_bound, before._monotonic_deadline
            before()
            adopted = _milliseconds(_settings(engine))
            assert all(adopted[name] < first[name] for name in first)
            assert (before.deadline, before._monotonic_deadline) == original
            previous = adopted
            for _ in range(2):
                before()
                current = _milliseconds(_settings(engine))
                assert all(current[name] <= previous[name] for name in current)
                assert (before.deadline, before._monotonic_deadline) == original
                previous = current
            assert engine.query_one("SHOW transaction_isolation")["transaction_isolation"] == "serializable"
            before.check_in_memory()
        with engine.transaction():
            assert engine._local.connection is connection
            assert _settings(engine) == baseline, "SET LOCAL must end with its original root"
        record_property("original_sql_deadline", original_bound.isoformat())
        record_property("first_both_timeouts_ms", first)
        record_property("adopted_both_timeouts_ms", adopted)
        record_property("original_root_deadlines_unchanged", "true")
    finally:
        engine.close()


@pytest.mark.parametrize("seconds,code", [
    (-1, "NATIVE_SOURCE_POLICY_ADMISSION_DEADLINE_EXPIRED"),
    (40, "NATIVE_SOURCE_POLICY_DEADLINE_NOT_ADMISSION_ROOT"),
    (45, "NATIVE_SOURCE_POLICY_DEADLINE_NOT_ADMISSION_ROOT"),
])
def test_invalid_sql_deadline_adoption_preserves_both_existing_timeouts(
        intake_blank_db, seconds, code):
    engine = PostgresEngine(intake_blank_db.url(), max_pool_size=1,
        bootstrap=False, validate_schema=False)
    source = object.__new__(NativeIntakeSourcePolicyOwner)
    source.engine = engine
    try:
        with engine.transaction():
            connection, baseline = engine._local.connection, _settings(engine)
        with engine.transaction():
            assert engine._local.connection is connection
            engine.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            original_bound = source._clock() + timedelta(milliseconds=ADMISSION_BUDGET_MILLISECONDS)
            source._admission_budget(original_bound)
            existing = _settings(engine)
            _milliseconds(existing)
            # A past SQL instant or a business-sized deadline is only a
            # negative constructor input. Neither becomes a replacement root.
            rejected_bound = source._clock() + timedelta(seconds=seconds)
            with pytest.raises(NativeIntakeSourcePolicyConflict, match="^" + code + "$"):
                NativeSourcePolicyAdmissionBudget(source, sql_deadline=rejected_bound)
            assert _settings(engine) == existing
        # Observe normal SET LOCAL cleanup; do not retry the refused adoption.
        with engine.transaction():
            assert engine._local.connection is connection
            assert _settings(engine) == baseline
    finally:
        engine.close()


def test_original_source_budget_enables_native_terminal_reads_and_matches_legacy(
        parser_admission_case, monkeypatch):
    case = parser_admission_case
    owner, ids = case[:2]
    source = NativeIntakeSourcePolicyOwner(owner.graph)
    delivery = _source_delivery(case)
    original_rows, calls = _persisted(case), _observe(case, monkeypatch)
    workflow_read = ("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
        (ids["tenant"], delivery.job_id))
    batch_params = ids["tenant"], delivery.job_id, delivery.job_id
    with owner.graph.transaction(ids["tenant"]):
        legacy = source._committed_result(delivery, ids["tenant"], 1)
        assert legacy is not None and legacy["processing_state"] == "AWAITING_ASSISTED_ENTRY"
        assert _batch_calls(calls) == [] and workflow_read in calls

    # A deliberately separate differential root over the same genuine facts.
    # Any failed read propagates; there is no retry or replacement budget.
    begin = len(calls)
    with owner.graph.transaction(ids["tenant"]):
        assert owner.engine.query_one("SELECT current_setting('transaction_isolation') AS isolation, "
            "current_setting('app.tenant_id') AS tenant") == {
                "isolation": "serializable", "tenant": ids["tenant"]}
        original_bound = source._clock() + timedelta(milliseconds=ADMISSION_BUDGET_MILLISECONDS)
        source._admission_budget(original_bound)
        before = NativeSourcePolicyAdmissionBudget(source, sql_deadline=original_bound)
        original = original_bound, before._monotonic_deadline
        view = source._producer_reads(before)
        evidence = native_terminal_evidence(view, ids["tenant"], delivery.job_id)
        assert evidence is not None
        assert _batch_calls(calls[begin:]) == [batch_params]
        closed_at = len(calls)
        physical, workflow = evidence.pair()
        assert physical == delivery
        assert workflow["job_id"] == delivery.job_id and workflow["status"] == "SUCCEEDED"
        evidence.finish()
        assert len(calls) == closed_at
        assert (before.deadline, before._monotonic_deadline) == original

        current = NativeIntakeSourcePolicyOwner(view)._committed_result(delivery, ids["tenant"], 1)
        assert current == legacy
        # The owner performs a fresh source batch after the direct helper
        # invocation. Its nested producer checks retain their original reads.
        assert _batch_calls(calls[closed_at:]).count(batch_params) == 1
        assert _batch_calls(calls[begin:]).count(batch_params) == 2
        assert workflow_read not in calls[begin:]
        assert (_PAIR_SQL, batch_params) not in calls[begin:]
        guarded = [params for statement, params in calls[begin:]
            if statement.startswith("WITH observed AS MATERIALIZED (")]
        assert len(guarded) >= 3
        assert all(params == (original_bound.isoformat(),) for params in guarded)
        before.check_in_memory()
        assert (before.deadline, before._monotonic_deadline) == original
    assert _persisted(case) == original_rows
