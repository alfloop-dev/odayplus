"""Synthetic approved decisions; real native acceptance/rollback/failure rows."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from modules.listing.application.identity_execution import IdentityExecutionService
from modules.listing.domain.identity_graph import IdentityConflictError
from shared.jobs.queue import JobFenceRejectedError
from tests.integration.test_assisted_listing_identity import (
    _native_snapshot,
    _replace_native_plan,
)
from tests.integration.test_assisted_listing_identity import (
    canonical_graph as canonical_graph,
)

pytestmark = pytest.mark.requires_live_env


def _queued(case):
    repo, _, _, db = case
    with db.connect() as conn:
        conn.execute("UPDATE identity.match_decisions SET status='APPROVED'")
        conn.execute("UPDATE identity.match_cases SET status='APPROVED'")
        conn.execute("UPDATE workflow.jobs SET status='QUEUED',checkpoint='APPROVED',attempt=0,lease_expires_at=NULL")
    return IdentityExecutionService(repo)


def test_native_approved_acceptance_execution_and_replay(canonical_graph):
    service = _queued(canonical_graph)
    repo, _, delivery, db = canonical_graph
    receipt = service.run(delivery)
    assert receipt["status"] == "EXECUTED"
    before = _native_snapshot(canonical_graph)
    assert service.run(delivery) == receipt
    assert _native_snapshot(canonical_graph) == before
    with db.connect() as conn:
        assert conn.execute("SELECT status,version FROM identity.match_decisions").fetchone() == ("EXECUTED", 31)
        assert conn.execute("SELECT status,attempt,fence_token FROM workflow.jobs").fetchone() == ("SUCCEEDED", 1, 32)
        assert conn.execute("SELECT event_type FROM workflow.outbox_events ORDER BY occurred_at,event_type").fetchall() == [
            ("match.decided",), ("identity.resolution_changed",), ("match.decided",),
        ]
    assert repo.audit_sink.writer.verify_chain(canonical_graph[1]["tenant"]).ok


def test_native_preaccept_version_conflict_leaves_approved_and_no_attempt(canonical_graph):
    service = _queued(canonical_graph)
    _, _, delivery, db = canonical_graph
    with db.connect() as conn:
        conn.execute("UPDATE identity.properties SET version=version+1")
    before = _native_snapshot(canonical_graph)
    with pytest.raises(IdentityConflictError, match="VERSION_CONFLICT"):
        service.run(delivery)
    assert _native_snapshot(canonical_graph) == before
    with db.connect() as conn:
        assert conn.execute("SELECT status,attempt FROM workflow.jobs").fetchone() == ("QUEUED", 0)
        assert conn.execute("SELECT status FROM identity.match_decisions").fetchone()[0] == "APPROVED"


@pytest.mark.parametrize("settlement", ["succeeds", "stale_physical", "expired_business"])
def test_native_executing_failure_records_failed_after_full_graph_rollback(canonical_graph, monkeypatch, settlement):
    service = _queued(canonical_graph)
    repo, ids, delivery, db = canonical_graph
    before = _native_snapshot(canonical_graph)

    graph_error = RuntimeError("Synthetic graph failure after source edges were written")

    def fail_after_edge_writes(*args):
        raise graph_error

    if settlement != "succeeds":
        original_fail = service.fail

        def lose_authority_before_failure_settlement(*args, **kwargs):
            # The execution transaction has rolled back before settlement.
            # Change actual SQL authority; do not manufacture a fail receipt.
            with db.connect() as conn:
                if settlement == "stale_physical":
                    conn.execute("UPDATE odp_runtime.durable_jobs SET fence_token=fence_token+1")
                else:
                    conn.execute("UPDATE workflow.jobs SET lease_expires_at=clock_timestamp()-interval '1 second'")
            return original_fail(*args, **kwargs)

        monkeypatch.setattr(service, "fail", lose_authority_before_failure_settlement)

    monkeypatch.setattr(repo, "_redirect", fail_after_edge_writes)
    with pytest.raises(RuntimeError, match="Synthetic graph failure") as observed:
        service.run(delivery)
    assert observed.value is graph_error
    after = _native_snapshot(canonical_graph)
    for table in ("identity.properties", "identity.source_identity_edges", "identity.property_redirects", "expansion.candidate_sites"):
        assert after[table] == before[table]
    with db.connect() as conn:
        if settlement != "succeeds":
            assert conn.execute("SELECT status FROM identity.match_decisions").fetchone()[0] == "EXECUTING"
            assert conn.execute("SELECT status FROM identity.match_cases").fetchone()[0] == "EXECUTING"
            assert conn.execute("SELECT status,attempt,last_error->>'reason_code' FROM workflow.jobs").fetchone() == (
                "RUNNING", 1, None,
            )
            assert conn.execute("SELECT event_type FROM workflow.outbox_events ORDER BY aggregate_version").fetchall() == [
                ("match.decided",),
            ]
            assert any("reconciliation" in note for note in graph_error.__notes__)
            assert repo.audit_sink.writer.verify_chain(ids["tenant"]).ok
            return
        assert conn.execute("SELECT status FROM identity.match_decisions").fetchone()[0] == "FAILED"
        assert conn.execute("SELECT status FROM identity.match_cases").fetchone()[0] == "FAILED"
        assert conn.execute("SELECT status,attempt,last_error->>'reason_code' FROM workflow.jobs").fetchone() == (
            "FAILED", 1, "IDENTITY_GRAPH_TRANSACTION_FAILED",
        )
        assert conn.execute("SELECT event_type FROM workflow.outbox_events ORDER BY aggregate_version").fetchall() == [
            ("match.decided",), ("match.decided",),
        ]
    assert repo.audit_sink.writer.verify_chain(ids["tenant"]).ok


def test_native_stale_worker_cannot_settle_a_failed_attempt(canonical_graph):
    service = _queued(canonical_graph)
    repo, ids, delivery, db = canonical_graph
    fence = service.begin(ids["tenant"], ids["decision"], delivery)
    with db.connect() as conn:
        conn.execute("UPDATE odp_runtime.durable_jobs SET fence_token=fence_token+1")
    before = _native_snapshot(canonical_graph)
    with pytest.raises(JobFenceRejectedError, match="IDENTITY_DELIVERY_FENCE_REJECTED"):
        service.fail(ids["tenant"], ids["decision"], delivery, business_fence=fence)
    assert _native_snapshot(canonical_graph) == before
    assert repo.case_context(ids["tenant"], ids["case"])["match_case"]["status"] == "EXECUTING"


@pytest.mark.parametrize("mutation", [
    "UPDATE workflow.jobs SET next_attempt_at=clock_timestamp()+interval '1 hour'",
    "UPDATE workflow.jobs SET attempt=max_attempts",
    "UPDATE workflow.jobs SET cancellation_requested_at=clock_timestamp()",
])
def test_native_job_schedule_attempt_and_cancel_authority_precede_acceptance(canonical_graph, mutation):
    service = _queued(canonical_graph)
    _, _, delivery, db = canonical_graph
    with db.connect() as conn:
        conn.execute(mutation)
    before = _native_snapshot(canonical_graph)
    with pytest.raises(JobFenceRejectedError, match="IDENTITY_ADMISSION_FENCE_REJECTED"):
        service.run(delivery)
    assert _native_snapshot(canonical_graph) == before


@pytest.mark.parametrize("command", ["begin", "fail"])
def test_native_attempt_checkpoints_require_separate_commits(canonical_graph, command):
    service = IdentityExecutionService(canonical_graph[0])
    repo, ids, delivery, _ = canonical_graph
    before = _native_snapshot(canonical_graph)
    with repo.transaction(ids["tenant"]):
        with pytest.raises(IdentityConflictError, match="MUST_OWN_TRANSACTION"):
            if command == "begin":
                service.begin(ids["tenant"], ids["decision"], delivery)
            else:
                service.fail(ids["tenant"], ids["decision"], delivery, business_fence=31)
    assert _native_snapshot(canonical_graph) == before


def _historical_listing(case, *, omit_version=False, held=False):
    _, ids, _, db = case
    with db.connect() as conn:
        conn.execute(
            "UPDATE expansion.listings SET property_id=%s,legal_hold=%s", (ids["split_target"], held),
        )
        conn.execute(
            "UPDATE identity.properties SET status='MERGED',current_redirect_property_id=%s WHERE property_id=%s",
            (ids["source"], ids["split_target"]),
        )
    if omit_version:
        _replace_native_plan(case, lambda plan: plan.update(expected_listing_versions={}))


def test_native_execution_requires_versions_for_historical_edge_listing(canonical_graph):
    repo, ids, delivery, _ = canonical_graph
    _historical_listing(canonical_graph, omit_version=True, held=True)
    before = _native_snapshot(canonical_graph)
    with pytest.raises(IdentityConflictError, match="LISTING_VERSION_CONFLICT"):
        repo.execute(ids["tenant"], ids["decision"], delivery, business_fence=31)
    assert _native_snapshot(canonical_graph) == before


def test_native_historical_listing_can_merge_with_real_versions(canonical_graph):
    repo, ids, delivery, _ = canonical_graph
    _historical_listing(canonical_graph)
    assert repo.execute(ids["tenant"], ids["decision"], delivery, business_fence=31)["status"] == "EXECUTED"
    resolved = repo.resolve_listing(ids["tenant"], ids["listing"])
    assert resolved["property_id_at_creation"] == ids["split_target"]
    assert resolved["effective_property_id"] == ids["target"]


def test_native_execution_replay_survives_later_case_work(canonical_graph):
    repo, ids, delivery, db = canonical_graph
    receipt = repo.execute(ids["tenant"], ids["decision"], delivery, business_fence=31)
    with db.connect() as conn:
        conn.execute("UPDATE identity.match_cases SET status='PENDING_REVIEW',version=version+2")
    before = _native_snapshot(canonical_graph)
    assert repo.execute(ids["tenant"], ids["decision"], delivery, business_fence=31) == receipt
    assert _native_snapshot(canonical_graph) == before


def test_native_execution_requires_accepted_transport_generation(canonical_graph):
    from psycopg.types.json import Jsonb

    repo, ids, delivery, db = canonical_graph
    with db.connect() as conn:
        conn.execute("UPDATE workflow.jobs SET payload=payload||%s", (Jsonb({
            "delivery_fence": delivery.fence_token, "delivery_worker": delivery.locked_by,
        }),))
        conn.execute("UPDATE odp_runtime.durable_jobs SET fence_token=fence_token+1,locked_by='later-worker'")
    later = repo.queue.get(delivery.job_id)
    before = _native_snapshot(canonical_graph)
    with pytest.raises(JobFenceRejectedError, match="IDENTITY_ACCEPTANCE_BINDING_REJECTED"):
        repo.execute(ids["tenant"], ids["decision"], later, business_fence=31)
    assert _native_snapshot(canonical_graph) == before


def test_concurrent_native_execution_does_not_duplicate_graph(canonical_graph):
    from modules.listing.application.intake_audit import CanonicalIntakeAuditSink
    from shared.audit.canonical_delivery import CanonicalAuditDelivery
    from shared.audit.canonical_postgresql import CanonicalPostgresAuditLog
    from shared.infrastructure.persistence.identity_graph_sql import (
        CanonicalIdentityGraphRepository,
    )
    from shared.infrastructure.persistence.job_queue import DurableJobQueue
    from shared.infrastructure.persistence.postgresql import PostgresEngine

    repo, ids, delivery, db = canonical_graph
    other_engine = PostgresEngine(db.url(), bootstrap=False, validate_schema=False)
    other_queue = DurableJobQueue(other_engine)
    writer = CanonicalPostgresAuditLog(other_engine)
    other = CanonicalIdentityGraphRepository(other_engine, other_queue,
        CanonicalIntakeAuditSink(writer, repo.audit_sink.retention,
            CanonicalAuditDelivery(writer, other_queue, repo.audit_sink.delivery.sink)))
    barrier = Barrier(2)

    def execute(graph):
        barrier.wait(timeout=10)
        try:
            return graph.execute(ids["tenant"], ids["decision"], delivery, business_fence=31)
        except Exception as exc:
            assert getattr(exc, "sqlstate", None) == "40001"
            return None

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(execute, [repo, other]))
        committed = [result for result in results if result is not None]
        assert committed and all(result == committed[0] for result in committed)
        # An explicit caller retry after serialization reads the original
        # receipt; the repository never silently replays a write transaction.
        assert repo.execute(ids["tenant"], ids["decision"], delivery, business_fence=31) == committed[0]
        with db.connect() as conn:
            assert conn.execute("SELECT count(*) FROM identity.source_identity_edges").fetchone()[0] == 2
            assert conn.execute("SELECT count(*) FROM audit.audit_events").fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM workflow.outbox_events").fetchone()[0] == 2
    finally:
        other_engine.close()
