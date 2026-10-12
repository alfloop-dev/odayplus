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
