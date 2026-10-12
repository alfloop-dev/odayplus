"""Persisted backlog, real HTTP admission/replay and real queue priority."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from apps.worker.oday_worker.main import ODayWorker
from shared.audit.backlog import AuditBacklogUnavailable, CanonicalAuditBacklog
from shared.audit.canonical_delivery import JOB_TYPE
from shared.auth import Principal, Role, Scope
from shared.jobs.queue import JobRequest, JobStatus
from shared.observability import Telemetry
from shared.observability.metrics import default_registry
from tests.integration import test_canonical_audit_delivery as delivery
from tests.integration import test_intake_canonical_audit_wiring as source

pytestmark = pytest.mark.requires_live_env


@pytest.fixture
def case(intake_blank_db, monkeypatch, tmp_path):
    yield from delivery.case.__wrapped__(intake_blank_db, monkeypatch, tmp_path)


def _prepare(bundle):
    response = source._submit(bundle)
    assert response.status_code == 202, response.text
    iid = response.json()["intake_id"]
    store = bundle.assisted_intake_store
    store.refresh(source.TENANT)
    # Controlled workflow precondition, not a parser execution claim.
    store.intakes[iid]["state"] = "NEEDS_REVIEW"
    store.flush()
    return iid


def _age(bundle, seconds, *, at=None):
    at = at or datetime.now(UTC)
    bundle.engine.execute("UPDATE durable_jobs SET created_at = ? WHERE job_type = ?", ((at - timedelta(seconds=seconds)).isoformat(), JOB_TYPE))
    return at


def _correct(bundle, iid, *, field="address_raw", key="synthetic-backlog-correct", version=1):
    return source._client(bundle).post(
        f"/api/v1/intakes/{iid}/corrections",
        json={"field_path": field, "corrected_value": 12 if field == "floor" else "Synthetic verified address", "reason": "Synthetic documented review", "risk_acknowledged": True},
        headers={**source.HEADERS, "Idempotency-Key": key, "If-Match": f'W/"{version}"'},
    )


def test_exact_five_minute_boundary_and_delivery_intent_clock(case):
    _, bundle, _ = case
    _prepare(bundle)
    at = _age(bundle, 300)
    checker = CanonicalAuditBacklog(bundle.engine)
    health = checker.inspect(source.TENANT, now=at)
    assert health.pending == 3 and health.oldest_seconds == 300
    assert health.blocked is False
    assert checker.inspect(source.TENANT, now=at + timedelta(microseconds=1)).blocked is True


def test_stale_backlog_blocks_identity_write_and_preserves_business_audit_and_replay(case):
    _, bundle, _ = case
    iid = _prepare(bundle)
    _age(bundle, 301)
    before = source._snapshot(bundle)
    response = _correct(bundle, iid)
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "BACKPRESSURE_ACTIVE"
    assert response.json()["retryable"] is True
    assert response.json()["next_action"] == "WAIT"
    assert source._snapshot(bundle) == before
    assert len(delivery._rows(bundle)) == 3
    delivery._drain(bundle)
    assert _correct(bundle, iid).status_code == 201


def test_stale_backlog_preserves_low_risk_correction_and_authorized_cached_receipt(case):
    _, bundle, _ = case
    iid = _prepare(bundle)
    identity = _correct(bundle, iid, key="synthetic-already-committed")
    assert identity.status_code == 201, identity.text
    _age(bundle, 301)
    before = source._snapshot(bundle)
    replay = _correct(bundle, iid, key="synthetic-already-committed")
    assert replay.status_code == identity.status_code, replay.text
    assert replay.json() == identity.json()
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert source._snapshot(bundle) == before
    ordinary = _correct(bundle, iid, field="floor", key="synthetic-low-risk", version=2)
    assert ordinary.status_code == 201, ordinary.text
    assert source._submit(bundle, key="synthetic-low-risk-submit").status_code == 202


def test_foreign_resource_denial_precedes_backlog_and_other_tenant_is_not_counted(case):
    _, bundle, _ = case
    iid = _prepare(bundle)
    _age(bundle, 301)
    foreign = str(uuid4())
    with source._client(bundle) as client:
        denied = client.post(f"/api/v1/intakes/{iid}/corrections", json={"field_path": "address_raw", "corrected_value": "Synthetic", "reason": "Synthetic", "risk_acknowledged": True}, headers={**source.HEADERS, "x-tenant-id": foreign, "Idempotency-Key": "synthetic-foreign-denial", "If-Match": 'W/"1"'})
    assert denied.status_code in (403, 404), denied.text
    assert CanonicalAuditBacklog(bundle.engine).inspect(foreign).pending == 0


@pytest.mark.parametrize("fault", ["missing-intent", "wrong-hash", "query-failure"])
def test_unverifiable_backlog_fails_closed_without_fictitious_zero(case, fault):
    _, bundle, monkeypatch = case
    iid = _prepare(bundle)
    if fault == "missing-intent":
        bundle.engine.execute("DELETE FROM durable_jobs WHERE job_type = ?", (JOB_TYPE,))
    elif fault == "wrong-hash":
        import json
        job = bundle.job_queue.get(delivery._jobs(bundle)[0]["job_id"])
        bundle.engine.execute("UPDATE durable_jobs SET payload_json = ? WHERE job_id = ?", (json.dumps({**job.payload, "event_sha256": "0" * 64}), job.job_id))
    else:
        original = bundle.engine.query

        def unavailable(statement, params=()):
            if "FROM audit.audit_events a LEFT JOIN" in statement:
                raise RuntimeError("synthetic audit observation outage")
            return original(statement, params)

        monkeypatch.setattr(bundle.engine, "query", unavailable)
    with pytest.raises(AuditBacklogUnavailable):
        CanonicalAuditBacklog(bundle.engine).inspect(source.TENANT)
    before = source._snapshot(bundle)
    assert _correct(bundle, iid).status_code == 503
    assert source._snapshot(bundle) == before


def test_dead_lettered_delivery_remains_in_pending_age(case):
    _, bundle, _ = case
    _prepare(bundle)
    _age(bundle, 301)
    bundle.engine.execute("UPDATE durable_jobs SET status='failed',delivery_state='dead_letter' WHERE job_type = ?", (JOB_TYPE,))
    health = CanonicalAuditBacklog(bundle.engine).inspect(source.TENANT)
    assert health.pending == 3 and health.blocked


@pytest.mark.parametrize("method", ["lease", "claim_next"])
def test_actual_queue_prioritizes_audit_recovery_over_an_older_business_job(case, method):
    _, bundle, _ = case
    ordinary, _ = bundle.job_queue.enqueue(JobRequest(job_type="forecast", payload={"synthetic": True}), correlation_id="synthetic-priority")
    _prepare(bundle)
    queue = bundle.job_queue
    get = (lambda: queue.lease(45)) if method == "lease" else (lambda: queue.claim_next(worker_id="synthetic-priority"))
    for _ in range(3):
        job = get()
        assert job.job_type == JOB_TYPE
        if method == "lease":
            assert queue.complete(job.job_id, lease_token=job.leased_until)
        else:
            queue.update_status(job.job_id, JobStatus.SUCCEEDED, expected_version=job.version, fence_token=job.fence_token)
    assert get().job_id == ordinary.job_id
    # Job success alone is never an invented WORM receipt.
    assert CanonicalAuditBacklog(bundle.engine).inspect(source.TENANT).pending == 3


def test_actual_worker_exports_bounded_backlog_signals_and_keeps_recovery_running(case):
    _, bundle, monkeypatch = case
    _prepare(bundle)
    _age(bundle, 301)
    registry = default_registry()
    worker = ODayWorker(persistence=bundle, telemetry=Telemetry("oday-worker", metrics=registry), worker_id="synthetic-backlog-monitor")
    health = worker.sample_audit_backlog()
    assert health.pending == 3 and health.blocked
    snapshot = registry.snapshot()
    assert snapshot["audit_worm_pending_count"][0]["value"] == 3
    assert snapshot["audit_worm_oldest_pending_seconds"][0]["value"] > 300
    assert snapshot["audit_worm_pending_count"][0]["labels"] == {"service": "oday-worker"}
    original = bundle.engine.query

    def unavailable(statement, params=()):
        if "SELECT tenant_id::text AS tenant_id FROM core.tenants UNION" in statement:
            raise RuntimeError("synthetic monitor outage")
        return original(statement, params)

    with monkeypatch.context() as patch:
        patch.setattr(bundle.engine, "query", unavailable)
        assert worker.sample_audit_backlog() is None
        patch.setattr(registry, "increment", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("synthetic metric outage")))
        assert worker.sample_audit_backlog() is None
    after = registry.snapshot()
    assert after["audit_worm_pending_count"][0]["value"] == 3
    assert after["audit_worm_backlog_check_failure_count"][0]["value"] == 1
    assert worker.run_once() is True


@pytest.mark.parametrize("action", ["release_hold", "purge", "restricted_export"])
def test_privacy_service_blocks_stale_high_risk_actions_before_any_mutation(case, action):
    from fastapi import HTTPException

    from modules.listing.application.intake_privacy import IntakePrivacyService, LegalHoldRecord

    _, bundle, monkeypatch = case
    iid = _prepare(bundle)
    _age(bundle, 301)
    before = source._snapshot(bundle)
    principal = Principal(subject_id=source.MANAGER, roles=frozenset({Role.AUDITOR, Role.SITE_REVIEWER}), scope=Scope(tenant_id=source.TENANT), authenticated=True)
    service = IntakePrivacyService(audit_log=bundle.audit_log)
    hold = LegalHoldRecord("synthetic-hold", source.TENANT, "intake", iid, "Synthetic", "synthetic-other-holder", "synthetic-other-approver", datetime.now(UTC))
    monkeypatch.setattr(service, "get_active_hold", lambda *args: hold if action == "release_hold" else None)
    monkeypatch.setattr(service, "_fetch_entity", lambda *args: {"data_classification": "restricted", "created_by": "synthetic-other", "tenant_id": source.TENANT})
    with pytest.raises(HTTPException) as error:
        if action == "release_hold":
            service.release_legal_hold(principal, source.TENANT, "intake", iid, "Synthetic reason", "synthetic-second-actor")
        elif action == "purge":
            service.purge_subject(principal, source.TENANT, "intake", iid, "Synthetic reason", "synthetic-second-actor")
        else:
            service.export_evidence(principal, source.TENANT, "intake", iid, "Synthetic purpose", "synthetic-second-actor", "synthetic-authorization")
    assert error.value.status_code == 503
    assert hold.version == 1 and hold.released_at is None
    assert source._snapshot(bundle) == before


def _operator_case(bundle):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from apps.api.app.routes.operator import create_operator_router
    from modules.opsboard.application.network_listings import NetworkListingService
    from modules.opsboard.application.operator_live_repository import OperatorLiveRepository
    from shared.api.errors import install_error_handlers
    from shared.infrastructure.persistence import PostgresDocumentStore
    from shared.infrastructure.persistence.operator_domains import (
        DurableOperatorDomainStateRepository,
        TenantScopedDocumentStore,
    )
    from shared.infrastructure.persistence.operator_network_listings import (
        DurableAssistedIntakeRepository,
    )

    documents = PostgresDocumentStore(bundle.engine)
    repository = DurableAssistedIntakeRepository(TenantScopedDocumentStore(documents, source.TENANT))
    iid = str(uuid4())
    # Explicit synthetic legacy BFF record; does not assert canonical ID mapping.
    repository.save_intake({
        "id": iid, "tenantId": source.TENANT, "submitter": source.OWNER,
        "owner": source.OWNER, "heatZoneId": "HZ-01", "stage": "NEEDS_REVIEW",
        "parsedFields": {}, "auditEvents": [], "matchResult": None,
        "sourceId": "synthetic", "canonicalUrl": "https://example.invalid/legacy",
    })
    state = NetworkListingService(tenant_id=source.TENANT).export_state()
    for listing in state["listings"]:
        listing["submitter"] = source.OWNER
    state_repository = DurableOperatorDomainStateRepository(documents, "network-listings")
    state_repository.save(source.TENANT, state)
    app = FastAPI()
    app.state.job_queue = bundle.job_queue
    install_error_handlers(app)

    @app.middleware("http")
    async def correlate(request: Request, call_next):
        request.state.correlation_id = request.headers.get("x-correlation-id")
        return await call_next(request)

    app.include_router(create_operator_router(
        audit_log=bundle.audit_log, document_store=documents,
        listing_repository_for_tenant=bundle.listing_repository_for_tenant,
        live_repository=OperatorLiveRepository(bundle), require_live_data=True,
        persistence_mode="postgresql", provider_mode="live",
    ), prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False), iid, repository, state_repository


def _operator_post(client, suffix, *, key="synthetic-bff-guard", **body):
    return client.post("/api/v1/operator/network-listings/" + suffix,
        headers={**source.HEADERS, "x-operator-role": "expansion-manager",
                 "x-correlation-id": str(uuid4()), "Idempotency-Key": key},
        json={"reason": "Synthetic independent review", "riskSummary": "Synthetic disclosed risk",
              "riskAcknowledged": True, **body})


@pytest.mark.parametrize("action", ["identity-correction", "decision", "promotion", "convert", "merge", "quarantine-retry"])
def test_actual_operator_composition_blocks_new_high_risk_bff_writes(case, action):
    _, bundle, _ = case
    _prepare(bundle)
    client, iid, repository, state_repository = _operator_case(bundle)
    if action == "quarantine-retry":
        intake = repository.get_intake(iid)
        intake["stage"] = "QUARANTINED"
        repository.save_intake(intake)
    _age(bundle, 301)
    suffix, body = {
        "identity-correction": (f"intake/{iid}/correct", {"fields": {"address": "Synthetic"}}),
        "decision": (f"intake/{iid}/decide", {"action": "reject"}),
        "promotion": (f"intake/{iid}/promote", {}),
        "convert": ("listings/L-2024/convert", {}),
        "merge": ("listings/L-2029/merge", {"targetListingId": "L-2025"}),
        "quarantine-retry": (f"intake/{iid}/retry", {}),
    }[action]
    before = (repository.list_intakes(), state_repository.load(source.TENANT), source._snapshot(bundle))
    response = _operator_post(client, suffix, **body)
    assert response.status_code == 503, response.text
    assert response.json()["detail"] == "BACKPRESSURE_ACTIVE"
    assert response.headers["Retry-After"] == "30"
    assert (repository.list_intakes(), state_repository.load(source.TENANT), source._snapshot(bundle)) == before
    assert repository.list_idempotency_records() == []


def test_actual_operator_composition_preserves_authorized_replay_conflict_and_low_risk_write(case):
    _, bundle, _ = case
    _prepare(bundle)
    client, iid, repository, _ = _operator_case(bundle)
    suffix = f"intake/{iid}/correct"
    first = _operator_post(client, suffix, key="synthetic-bff-replay", fields={"address": "Synthetic"})
    assert first.status_code == 200, first.text
    _age(bundle, 301)
    before = repository.list_intakes()
    replay = _operator_post(client, suffix, key="synthetic-bff-replay", fields={"address": "Synthetic"})
    assert replay.status_code == 200 and replay.json() == first.json(), replay.text
    assert repository.list_intakes() == before
    conflict = _operator_post(client, suffix, key="synthetic-bff-replay", fields={"address": "Changed"})
    assert conflict.status_code == 409, conflict.text
    ordinary = _operator_post(client, suffix, key="synthetic-bff-floor", fields={"floor": "1"})
    assert ordinary.status_code == 200, ordinary.text
    assert client.get(f"/api/v1/operator/network-listings/intake/{iid}", headers={**source.HEADERS, "x-operator-role": "expansion-manager"}).status_code == 200


def test_actual_operator_scope_denial_precedes_backlog_and_missing_intent_fails_closed(case):
    _, bundle, _ = case
    _prepare(bundle)
    client, iid, repository, _ = _operator_case(bundle)
    intake = repository.get_intake(iid)
    intake["heatZoneId"] = "synthetic-foreign-zone"
    repository.save_intake(intake)
    _age(bundle, 301)
    denied = client.post(f"/api/v1/operator/network-listings/intake/{iid}/correct",
        headers={**source.HEADERS, "x-operator-role": "expansion-manager", "x-heat-zone-ids": "allowed-zone",
                 "x-correlation-id": str(uuid4()), "Idempotency-Key": "synthetic-denied"},
        json={"fields": {"address": "Synthetic"}, "reason": "Synthetic", "riskSummary": "Synthetic", "riskAcknowledged": True})
    assert denied.status_code in (403, 404), denied.text
    bundle.engine.execute("DELETE FROM durable_jobs WHERE job_type = ?", (JOB_TYPE,))
    before = repository.list_intakes()
    unavailable = _operator_post(client, f"intake/{iid}/correct", fields={"address": "Synthetic"})
    assert unavailable.status_code == 503, unavailable.text
    assert repository.list_intakes() == before and repository.list_idempotency_records() == []


def test_actual_postgresql_publisher_delivers_while_general_worker_is_occupied(case):
    from threading import Event, Thread

    from shared.jobs.registry import JobRegistry

    db, bundle, monkeypatch = case
    started, release = Event(), Event()
    registry = JobRegistry()

    def long_work(job, persistence):
        started.set()
        assert release.wait(20), "synthetic general worker was not released"

    registry.register("synthetic-long-work", long_work)
    ordinary, _ = bundle.job_queue.enqueue(JobRequest("synthetic-long-work", {}), correlation_id="synthetic")
    general = ODayWorker(persistence=bundle, registry=registry, worker_id="synthetic-busy-general")
    thread = Thread(target=general.run_once)
    thread.start()
    publisher_bundle = None
    try:
        assert started.wait(5)
        assert source._submit(bundle).status_code == 202
        before = source._snapshot(bundle)
        publisher_bundle = source._bundle(db, monkeypatch)
        publisher = ODayWorker(persistence=publisher_bundle, audit_only=True, worker_id="synthetic-dedicated-publisher")
        for _ in range(3):
            assert publisher.run_once() is True
            assert thread.is_alive()
            assert bundle.job_queue.get(ordinary.job_id).status == JobStatus.RUNNING
        assert publisher.run_once() is False
        assert all(row["worm_object_uri"] for row in delivery._rows(publisher_bundle))
        assert source._snapshot(bundle) == before
    finally:
        release.set()
        thread.join(5)
        if publisher_bundle:
            publisher_bundle.engine.close()
    assert not thread.is_alive()
    assert bundle.job_queue.get(ordinary.job_id).status == JobStatus.SUCCEEDED


def test_actual_postgresql_publisher_ignores_expired_ordinary_and_recovers_expired_audit_with_fencing(case):
    from shared.jobs.queue import JobFenceRejectedError

    _, bundle, _ = case
    ordinary, _ = bundle.job_queue.enqueue(JobRequest("synthetic-ordinary", {}), correlation_id="synthetic")
    assert bundle.job_queue.claim_next(worker_id="synthetic-expired-general").job_id == ordinary.job_id
    expired = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
    bundle.engine.execute("UPDATE durable_jobs SET lease_expires_at = ? WHERE job_id = ?", (expired, ordinary.job_id))
    other, _ = bundle.job_queue.enqueue(JobRequest("synthetic-other", {}), correlation_id="synthetic")
    assert bundle.job_queue.claim_next(worker_id="synthetic-publisher", audit_only=True) is None
    ordinary_before = (bundle.job_queue.get(ordinary.job_id), bundle.job_queue.get(other.job_id))
    assert source._submit(bundle).status_code == 202
    before = source._snapshot(bundle)
    held = bundle.job_queue.claim_next(worker_id="synthetic-expired-publisher", audit_only=True)
    bundle.engine.execute("UPDATE durable_jobs SET lease_expires_at = ? WHERE job_id = ?", (expired, held.job_id))
    publisher = ODayWorker(persistence=bundle, audit_only=True, worker_id="synthetic-recovery-publisher")
    assert publisher.run_once() is True
    recovered = bundle.job_queue.get(held.job_id)
    assert recovered.status == JobStatus.SUCCEEDED and recovered.fence_token > held.fence_token
    with pytest.raises(JobFenceRejectedError):
        bundle.job_queue.update_status(held.job_id, JobStatus.FAILED, expected_version=held.version, fence_token=held.fence_token)
    assert publisher.run_once() is True and publisher.run_once() is True
    assert publisher.run_once() is False
    assert (bundle.job_queue.get(ordinary.job_id), bundle.job_queue.get(other.job_id)) == ordinary_before
    assert all(row["worm_object_uri"] for row in delivery._rows(bundle))
    assert source._snapshot(bundle) == before


def test_actual_publisher_process_bootstraps_and_delivers_only_audits(case, monkeypatch):
    import json
    import subprocess
    import sys
    from pathlib import Path

    _, bundle, _ = case
    assert source._submit(bundle).status_code == 202
    ordinary, _ = bundle.job_queue.enqueue(JobRequest("synthetic-ordinary", {}), correlation_id="synthetic")
    before = source._snapshot(bundle)
    monkeypatch.setenv("ODAY_RELEASE_SHA", "local")
    monkeypatch.setenv("ODP_PERSISTENCE", "postgresql")
    # The worker image sets PYTHONPATH=/app; use this checkout as that root.
    monkeypatch.setenv("PYTHONPATH", str(Path.cwd()))
    completed = subprocess.run(
        [sys.executable, "product_ops/deployment/cloud_run_job_entrypoint.py", "audit-publisher",
         "--run-for-seconds", "3", "--max-jobs", "10"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    print(completed.stdout)
    assert completed.returncode == 0, completed.stderr
    receipts = [json.loads(line) for line in completed.stdout.splitlines() if line.startswith("{")]
    receipt = next(row for row in receipts if row.get("receipt_kind") == "audit_publisher")
    assert receipt["status"] == "observation_window_complete" and receipt["audit_only"]
    assert receipt["release_sha"] == "local" and receipt["processed_count"] == 3
    assert all(row["job_status"] == "succeeded" and row["job_type"] == JOB_TYPE for row in receipt["processed"])
    assert all(row["worm_object_uri"] for row in delivery._rows(bundle))
    assert bundle.job_queue.get(ordinary.job_id).status == JobStatus.QUEUED
    assert bundle.job_queue.get(ordinary.job_id).attempts == 0
    assert source._snapshot(bundle) == before
