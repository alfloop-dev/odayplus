"""Real SQL admission and recovery for durable pricing audit deliveries."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace

import pytest

from apps.worker.oday_worker.main import ODayWorker
from shared.audit.backlog import AuditBacklogUnavailable, DurableAuditBacklog
from shared.audit.durable_delivery import COLLECTION, JOB_TYPE, DurableAuditDelivery
from shared.audit.integrity import sha256_hex
from shared.audit.worm import LocalAppendOnlyWormSink
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.jobs.queue import JobRequest
from shared.observability import Telemetry
from shared.observability.metrics import default_registry
from tests.integration.test_priceops_atomic_commands import effects
from tests.integration.test_priceops_command_authority import proposal
from tests.integration.test_priceops_durable_commands import database as priceops_database
from tests.integration.test_priceops_durable_commands import http_context, seed

database = priceops_database
TENANT = "tenant-a"


@pytest.fixture
def case(database, tmp_path):
    open_engine, _ = database
    engine = open_engine()
    from shared.infrastructure.persistence.document_store import SqliteDocumentStore
    from shared.infrastructure.persistence.feature_flags import DurableFeatureFlagRegistry

    flags = DurableFeatureFlagRegistry(SqliteDocumentStore(engine))
    flags.initialize_defaults()  # This owned fixture starts disabled, never auto-enabled.
    client, repository, audit = http_context(engine, feature_flag_registry=flags)
    sink = LocalAppendOnlyWormSink(tmp_path / "synthetic-backlog-worm")
    audit._worm_sink = sink
    scenario, _, _ = seed(client)
    assert client.post("/priceops/plans/durable-plan/submit", json={"reason": "independent review"}).status_code == 200
    queue = DurableJobQueue(engine, flags=flags)
    bundle = SimpleNamespace(engine=engine, job_queue=queue, audit_log=audit, feature_flag_registry=flags)
    registry = default_registry()
    worker = ODayWorker(persistence=bundle, audit_only=True, worker_id="synthetic-backlog-worker",
                       telemetry=Telemetry("oday-worker", metrics=registry))
    delivery = DurableAuditDelivery(engine, TENANT, queue, sink)
    checker = DurableAuditBacklog(engine, sink.sink_id)
    try:
        yield SimpleNamespace(engine=engine, client=client, repository=repository, audit=audit,
                              sink=sink, queue=queue, worker=worker, registry=registry,
                              delivery=delivery, checker=checker, scenario=scenario,
                              open_engine=open_engine, flags=flags)
    finally:
        engine.close()


def age(case, seconds, *, at=None):
    observed = at or datetime.now(UTC)
    case.engine.execute("UPDATE durable_jobs SET created_at = ? WHERE job_type = ?",
                        ((observed - timedelta(seconds=seconds)).isoformat(), JOB_TYPE))
    return observed


def approval(case, *, decision="approved", key="new-backlog-approval", actor="independent-reviewer"):
    return case.client.post("/priceops/plans/durable-plan/approve",
                            headers={"Idempotency-Key": key, "x-subject-id": actor},
                            json={"actor_id": "forged", "decision": decision, "reason": "authored independent reason"})


def first_delivery(case):
    intent = case.delivery.store.list_all(COLLECTION)[0]
    event = intent["event"]
    job = case.queue.get_by_idempotency_key("durable-audit:" + TENANT + ":" + event.event_id)
    return intent, event, job


def drain(case):
    for _ in range(20):
        if not case.worker.run_once():
            return
    pytest.fail("synthetic queue did not settle within its known six deliveries")


def test_exact_five_minute_boundary_uses_delivery_clock(case):
    at = age(case, 300)
    health = case.checker.inspect(TENANT, now=at)
    assert health.pending == 6 and health.oldest_seconds == 300 and not health.blocked
    assert case.checker.inspect(TENANT, now=at + timedelta(microseconds=1)).blocked
    assert case.checker.all_tenants(now=at).pending == 6
    _, event, _ = first_delivery(case)
    assert event.metadata["audit_delivery_job_type"] == JOB_TYPE
    assert case.audit.verify_chain().ok


@pytest.mark.parametrize("status,delivery_state", [("succeeded", None), ("failed", "dead_letter"), ("cancelled", None)])
def test_job_status_alone_never_hides_pending_delivery(case, status, delivery_state):
    age(case, 301)
    case.engine.execute("UPDATE durable_jobs SET status = ?,delivery_state = ? WHERE job_type = ?",
                        (status, delivery_state, JOB_TYPE))
    health = case.checker.inspect(TENANT)
    assert health.pending == 6 and health.blocked
    before = effects(case.engine)
    response = approval(case)
    assert response.status_code == 503 and response.headers["Retry-After"] == "30"
    assert effects(case.engine) == before and case.repository.list_approvals("durable-plan") == []


@pytest.mark.parametrize("operation", ["approve", "decision-writeback", "exploration-optimize", "exploration-batch"])
def test_stale_backlog_blocks_new_high_risk_commands_before_any_effect(case, operation):
    age(case, 301)
    before = effects(case.engine)
    if operation == "approve":
        response = approval(case, decision=" APPROVE ")
    elif operation == "decision-writeback":
        response = case.client.post("/priceops/plans/durable-plan/decision-writeback",
                                    headers={"Idempotency-Key": "blocked-writeback", "x-subject-id": "independent-reviewer"},
                                    json={"actor": "forged", "decision": "APPROVED", "reason": "authored independent reason"})
    elif operation == "exploration-optimize":
        response = case.client.post("/priceops/plans/durable-plan/optimize", headers={"Idempotency-Key": "blocked-exploration"},
                                    json={"reason": "authored", "exploration_gate_id": "synthetic-gate"})
    else:
        body = proposal("blocked-batch-plan")
        body["exploration_gate_id"] = "synthetic-gate"
        response = case.client.post("/priceops/optimizer-jobs", headers={"Idempotency-Key": "blocked-batch"}, json={"plans": [body]})
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "BACKPRESSURE_ACTIVE"
    assert response.headers["Retry-After"] == "30"
    assert effects(case.engine) == before


def test_scope_and_self_approval_denial_precede_backpressure(case):
    age(case, 301)
    assert approval(case, actor="durable-pricer").status_code == 403
    assert case.client.post("/priceops/plans/durable-plan/approve",
                            headers={"x-tenant-id": "foreign-tenant", "x-subject-id": "independent-reviewer"},
                            json={"actor_id": "forged", "decision": "approved", "reason": "authored"}).status_code == 404
    assert case.checker.inspect("foreign-tenant").pending == 0


def test_low_risk_reads_simulation_and_rejection_remain_available(case):
    age(case, 301)
    assert case.client.get("/priceops/plans/durable-plan/comparison").status_code == 200
    response = case.client.post("/priceops/plans/durable-plan/simulate-scenario",
                                json={"candidate_prices": {"new-item": 80}})
    assert response.status_code == 200, response.text
    rejected = approval(case, decision="rejected", actor="durable-pricer")
    assert rejected.status_code == 200, rejected.text
    assert case.checker.inspect(TENANT).blocked


def enable_synthetic_activation_owner(case):
    # Explicit engineering setup for only these two legitimate go-live flows;
    # not production/human approval, not a global fixture auto-enable.
    for operation, actor in (("approve", "synthetic-flag-admin-a"),
                             ("approve", "synthetic-flag-admin-b"),
                             ("enable", "synthetic-flag-admin-a")):
        case.flags.execute(operation, "high_risk.priceops.execute", actor=actor,
                           correlation_id="synthetic-backlog-fixture-config", audit_log=case.audit,
                           queue=case.queue)


def test_rollback_remains_available_during_stale_backlog(case):
    enable_synthetic_activation_owner(case)
    assert approval(case).status_code == 200
    activated = case.client.post("/priceops/plans/durable-plan/activate", json={"executor": "forged"})
    assert activated.status_code == 200, activated.text
    age(case, 301)
    response = case.client.post("/priceops/plans/durable-plan/rollback", json={"actor": "forged", "reason": "authored rollback"})
    assert response.status_code == 200, response.text
    assert case.repository.get_plan("durable-plan").status.value == "rollback"
    assert case.checker.inspect(TENANT).blocked


def test_authorized_committed_replay_survives_backpressure_and_changed_body_conflicts(case):
    enable_synthetic_activation_owner(case)
    first = approval(case, key="committed-approval")
    assert first.status_code == 200, first.text
    age(case, 301)
    before = effects(case.engine)
    replay = approval(case, key="committed-approval")
    assert replay.status_code == 200 and replay.json()["idempotent_replay"] is True
    assert replay.json()["audit_event_id"] == first.json()["audit_event_id"]
    assert approval(case, key="committed-approval", decision="rejected").status_code == 409
    assert effects(case.engine) == before
    blocked = case.client.post("/priceops/plans/durable-plan/activate", json={"executor": "forged"})
    assert blocked.status_code == 503 and effects(case.engine) == before
    drain(case)
    assert case.checker.inspect(TENANT).pending == 0
    activated = case.client.post("/priceops/plans/durable-plan/activate", headers={"Idempotency-Key": "recovered-activation"},
                                 json={"executor": "forged"})
    assert activated.status_code == 200, activated.text
    assert activated.json()["execution"]["executor"] == "durable-pricer"
    assert case.repository.get_execution("durable-plan").executor == "durable-pricer"
    drain(case)
    assert case.checker.inspect(TENANT).pending == 0 and case.audit.verify_chain().ok


@pytest.mark.parametrize("fault", ["missing-job", "missing-intent", "missing-both", "missing-audit", "wrong-hash", "wrong-sink", "wrong-tenant", "wrong-correlation", "bad-intent-status"])
def test_unverifiable_facts_fail_closed_and_preserve_command_state(case, fault):
    intent, event, job = first_delivery(case)
    if fault in {"missing-job", "missing-both"}:
        case.engine.execute("DELETE FROM durable_jobs WHERE job_id = ?", (job.job_id,))
    if fault in {"missing-intent", "missing-both"}:
        case.engine.execute("DELETE FROM durable_documents WHERE collection = ? AND doc_id = ?",
                            (case.delivery.store._collection(COLLECTION), event.event_id))
    elif fault == "missing-audit":
        case.engine.execute("DELETE FROM durable_audit_events WHERE event_id = ?", (event.event_id,))
    elif fault in {"wrong-hash", "wrong-sink", "wrong-tenant"}:
        field = {"wrong-hash": "event_hash", "wrong-sink": "sink_id", "wrong-tenant": "tenant_id"}[fault]
        case.engine.execute("UPDATE durable_jobs SET payload_json = ? WHERE job_id = ?",
                            (json.dumps({**job.payload, field: "forged"}), job.job_id))
    elif fault == "wrong-correlation":
        case.engine.execute("UPDATE durable_jobs SET correlation_id = ? WHERE job_id = ?", ("forged", job.job_id))
    elif fault == "bad-intent-status":
        case.delivery.store.put(COLLECTION, event.event_id, {**intent, "status": "pretend-success"})
    with pytest.raises(AuditBacklogUnavailable):
        case.checker.inspect(TENANT)
    with pytest.raises(AuditBacklogUnavailable):
        case.checker.all_tenants()
    before = effects(case.engine)
    assert approval(case).status_code == 503
    assert effects(case.engine) == before


@pytest.mark.parametrize("fault", ["checksum", "object-uri", "record-id", "sink-id"])
def test_delivered_status_without_bound_external_receipt_is_not_a_healthy_zero(case, fault):
    intent, event, _ = first_delivery(case)
    receipt = {"record_id": event.event_id, "sink_id": case.sink.sink_id, "record_type": "audit-events",
               "checksum": sha256_hex(event.to_dict()), "object_uri": "file:///synthetic-receipt"}
    field = {"checksum": "checksum", "object-uri": "object_uri", "record-id": "record_id", "sink-id": "sink_id"}[fault]
    receipt[field] = "outbox://pending" if fault == "object-uri" else "forged"
    case.delivery.store.put(COLLECTION, event.event_id, {**intent, "status": "delivered", "receipt": receipt})
    with pytest.raises(AuditBacklogUnavailable):
        case.checker.inspect(TENANT)


def test_orphan_duplicate_jobs_and_wrong_tenant_partition_are_unavailable(case):
    intent, event, job = first_delivery(case)
    orphan, _ = case.queue.enqueue(JobRequest(job_type=JOB_TYPE, payload=job.payload,
                                             idempotency_key="forged-duplicate"), correlation_id=job.correlation_id)
    with pytest.raises(AuditBacklogUnavailable):
        case.checker.inspect(TENANT)
    case.engine.execute("DELETE FROM durable_jobs WHERE job_id = ?", (orphan.job_id,))
    from shared.infrastructure.persistence.document_store import SqliteDocumentStore
    from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
    foreign = TenantScopedDocumentStore(SqliteDocumentStore(case.engine), "foreign-tenant")
    foreign.put(COLLECTION, event.event_id, intent)
    with pytest.raises(AuditBacklogUnavailable):
        case.checker.all_tenants()


def test_clock_and_runtime_sink_mismatch_are_unavailable(case):
    with pytest.raises(AuditBacklogUnavailable):
        case.checker.inspect(TENANT, now=datetime(2026, 1, 1))
    future = age(case, -1)
    with pytest.raises(AuditBacklogUnavailable):
        case.checker.inspect(TENANT, now=future)
    age(case, 0)
    with pytest.raises(AuditBacklogUnavailable):
        DurableAuditBacklog(case.engine, "unapproved-other-sink").inspect(TENANT)
    with pytest.raises(AuditBacklogUnavailable):
        DurableAuditBacklog(case.engine, None).inspect(TENANT)


def test_real_worker_metrics_retain_last_verified_value_when_observation_fails(case, monkeypatch):
    age(case, 301)
    health = case.worker.sample_durable_audit_backlog()
    assert health.pending == 6 and health.blocked
    snapshot = case.registry.snapshot()
    assert snapshot["durable_audit_worm_pending_count"][0]["value"] == 6
    assert snapshot["durable_audit_worm_pending_count"][0]["labels"] == {"service": "oday-worker"}
    assert snapshot["durable_audit_worm_oldest_pending_seconds"][0]["value"] > 300
    original = case.engine.query
    def fail(statement, params=()):
        if statement.startswith("SELECT metadata_json FROM durable_audit_events WHERE"):
            raise RuntimeError("synthetic backlog observation outage")
        return original(statement, params)
    with monkeypatch.context() as patch:
        patch.setattr(case.engine, "query", fail)
        assert case.worker.sample_durable_audit_backlog() is None
        after = case.registry.snapshot()
        assert after["durable_audit_worm_pending_count"][0]["value"] == 6
        assert after["durable_audit_worm_backlog_check_failure_count"][0]["value"] == 1
        # Observation failure does not prevent the audit-only recovery worker.
        assert case.worker.run_once() is True
    drain(case)
    assert case.worker.sample_durable_audit_backlog().pending == 0
    assert case.registry.snapshot()["durable_audit_worm_pending_count"][0]["value"] == 0
    assert case.registry.snapshot()["durable_audit_worm_oldest_pending_seconds"][0]["value"] == 0


@pytest.mark.parametrize("method", ["inspect", "all_tenants"])
def test_arriving_committed_delivery_during_observation_is_counted_without_clock_failure(case, monkeypatch, method):
    other = case.open_engine()
    writer, _, writer_audit = http_context(other)
    writer_audit._worm_sink = case.sink
    reading, resume = Event(), Event()
    original = case.engine.query
    paused = False
    def pause(statement, params=()):
        nonlocal paused
        rows = original(statement, params)
        if statement.startswith("SELECT event_id FROM durable_audit_events WHERE") and not paused:
            paused = True
            reading.set()
            assert resume.wait(10)
        return rows
    monkeypatch.setattr(case.engine, "query", pause)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            observe = (lambda: case.checker.inspect(TENANT)) if method == "inspect" else case.checker.all_tenants
            result = pool.submit(observe)
            assert reading.wait(10)
            try:
                response = writer.post("/priceops/plans", json=proposal("arriving-plan"))
                assert response.status_code == 201, response.text
            finally:
                resume.set()
            health = result.result(timeout=10)
        assert health.pending == 7 and not health.blocked
    finally:
        resume.set()
        other.close()
