"""Native admission, signed transport and existing worker registry composition."""

import json
from dataclasses import replace
from types import MethodType, SimpleNamespace

import pytest

from apps.worker.oday_worker.handlers import build_default_registry
from modules.listing.application.identity_delivery import (
    IDENTITY_POLICY_ENV,
    IDENTITY_SIGNING_KEY_ENV,
    IdentityDeliveryAuthenticator,
    IdentityDeliveryUnavailable,
    IdentityJobAdmission,
)
from modules.listing.application.identity_execution import IdentityExecutionService
from modules.listing.domain.identity_graph import IdentityConflictError
from shared.infrastructure.persistence.factory import PersistenceBundle
from shared.jobs.queue import JobFenceRejectedError, JobStatus
from tests.integration.test_assisted_listing_identity import canonical_graph as canonical_graph
from tests.integration.test_canonical_identity_proposals_postgresql import _propose, _review, _state

pytestmark = pytest.mark.requires_live_env


@pytest.fixture
def approved(canonical_graph, monkeypatch):
    graph, ids, _, _ = canonical_graph
    monkeypatch.setenv(IDENTITY_SIGNING_KEY_ENV, "Explicit synthetic local identity signing key only")
    monkeypatch.setenv(IDENTITY_POLICY_ENV, json.dumps({"policy_id": "synthetic-identity-execution",
        "version": 19, "max_attempts": 3, "deadline_seconds": 120, "backoff_seconds": [1, 2]}))
    repo, proposal = _propose(canonical_graph)
    reviewed = _review(canonical_graph, repo, proposal)
    graph.delivery_authenticator = IdentityDeliveryAuthenticator(graph.engine)
    return canonical_graph, reviewed


def _admit(approved):
    case, decision = approved
    graph, ids, _, _ = case
    return IdentityJobAdmission(graph).enqueue(ids["tenant"], decision["decision_id"],
        expected_version=decision["version"], actor_id=ids["reviewer"])


def _runtime_bundle(case):
    graph, ids, _, _ = case
    bundle = SimpleNamespace(engine=graph.engine, job_queue=graph.queue,
        assisted_intake_store=SimpleNamespace(_canonical_audit_sink=graph.audit_sink),
        is_production=True, audit_log=SimpleNamespace(_worm_sink=graph.audit_sink.delivery.sink))
    bundle.canonical_identity_graph = MethodType(PersistenceBundle.canonical_identity_graph, bundle)
    return bundle


def _claim(case, target):
    graph, ids, _, _ = case
    registry, bundle = build_default_registry(), _runtime_bundle(case)
    # Drain the real preexisting audit deliveries first; no fixture updates to
    # job priority, claim filters, fences or delivery generations.
    for _ in range(20):
        delivery = graph.queue.claim_next(worker_id="synthetic-native-identity-dispatch")
        assert delivery is not None
        if delivery.job_id == target:
            return delivery, registry, bundle
        assert delivery.job_type == "canonical-audit-delivery"
        registry.handle(delivery, bundle)
        latest = graph.queue.get(delivery.job_id)
        if latest.status == JobStatus.RUNNING:
            graph.queue.update_status(delivery.job_id, JobStatus.SUCCEEDED,
                expected_version=latest.version, fence_token=latest.fence_token)
    raise AssertionError("Native identity delivery was not reached")


@pytest.mark.parametrize("mode", ["success", "retry", "poison", "deadline", "cancel"])
def test_real_worker_loop_preserves_canonical_delivery_outcomes_without_generic_payload_retry(approved, monkeypatch, mode):
    from apps.worker.oday_worker.main import ODayWorker

    case, _ = approved
    graph, ids, _, db = case
    receipt = _admit(approved)
    original_payload = dict(graph.queue.get(receipt["job_id"]).payload)
    if mode == "retry":
        class TransientStorageFailure(RuntimeError):
            sqlstate = "40001"

        def unavailable(*_):
            raise TransientStorageFailure("Explicit synthetic retryable preaccept failure")

        monkeypatch.setattr(IdentityExecutionService, "begin", unavailable)
    elif mode == "poison":
        with db.connect() as conn:
            conn.execute("UPDATE identity.properties SET version=version+1 WHERE property_id=%s", (ids["source"],))
    elif mode in {"deadline", "cancel"}:
        with db.connect() as conn:
            if mode == "deadline":
                conn.execute("UPDATE workflow.jobs SET timeout_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",
                    (receipt["job_id"],))
            else:
                conn.execute("UPDATE workflow.jobs SET cancellation_requested_at=clock_timestamp() WHERE job_id=%s",
                    (receipt["job_id"],))
    worker = ODayWorker(persistence=_runtime_bundle(case), worker_id="synthetic-actual-identity-worker-loop")
    for _ in range(20):
        assert worker.run_once()
        delivery = graph.queue.get(receipt["job_id"])
        if delivery.attempts:
            break
    else:
        pytest.fail("Actual worker loop did not reach the signed native job")
    assert delivery.payload == original_payload
    expected = {"success": JobStatus.SUCCEEDED, "retry": JobStatus.QUEUED, "poison": JobStatus.FAILED,
                "deadline": JobStatus.FAILED, "cancel": JobStatus.CANCELLED}
    assert delivery.status == expected[mode]
    assert "_retry_count" not in delivery.payload
    with db.connect() as conn:
        status = conn.execute("SELECT status FROM workflow.jobs WHERE job_id=%s", (receipt["job_id"],)).fetchone()[0]
        assert status == {"success": "SUCCEEDED", "retry": "RETRYING", "poison": "FAILED",
                          "deadline": "FAILED", "cancel": "CANCELLED"}[mode]
    if mode in {"deadline", "cancel"}:
        assert graph.resolve_listing(ids["tenant"], ids["listing"])["effective_property_id"] == ids["source"]
        with db.connect() as conn:
            assert conn.execute("SELECT status FROM identity.match_decisions WHERE match_decision_id=%s",
                (receipt["decision_id"],)).fetchone()[0] == "APPROVED"


def test_signed_native_admission_runs_through_actual_registered_worker_and_preserves_original_listing(approved):
    case, decision = approved
    graph, ids, _, db = case
    receipt = _admit(approved)
    queued = graph.queue.get(receipt["job_id"])
    assert queued.status == JobStatus.QUEUED and queued.max_retries == 3
    assert set(queued.payload) == {"job_id", "tenant_id", "attempt_token"}
    assert "decision_id" not in queued.payload
    with db.connect() as conn:
        row = conn.execute("SELECT status,attempt,max_attempts,payload FROM workflow.jobs WHERE job_id=%s",
            (receipt["job_id"],)).fetchone()
        assert row[:3] == ("QUEUED", 0, 3) and row[3]["policy"]["version"] == 19
    delivery, registry, bundle = _claim(case, receipt["job_id"])
    registry.handle(delivery, bundle)
    with db.connect() as conn:
        row = conn.execute("SELECT status,attempt,result FROM workflow.jobs WHERE job_id=%s", (receipt["job_id"],)).fetchone()
        assert row[:2] == ("SUCCEEDED", 1)
        assert row[2]["decision_id"] == decision["decision_id"]
        assert str(conn.execute("SELECT property_id FROM expansion.listings WHERE listing_id=%s", (ids["listing"],)).fetchone()[0]) == ids["source"]
    resolved = graph.resolve_listing(ids["tenant"], ids["listing"])
    assert resolved["property_id_at_creation"] == ids["source"] and resolved["effective_property_id"] == ids["target"]
    before = _state(case)
    with pytest.raises(JobFenceRejectedError):
        registry.handle(delivery, bundle)  # The claimed lease was acknowledged.
    assert _state(case) == before


def test_preaccept_retry_uses_recorded_backoff_without_mutating_signed_payload(approved, monkeypatch):
    case, _ = approved
    graph, ids, _, db = case
    receipt = _admit(approved)
    delivery, registry, bundle = _claim(case, receipt["job_id"])
    begin = IdentityExecutionService.begin

    class RetryableStorageFailure(RuntimeError):
        sqlstate = "40001"

    def fail_once(*_):
        raise RetryableStorageFailure("Explicit synthetic preaccept storage conflict")

    monkeypatch.setattr(IdentityExecutionService, "begin", fail_once)
    registry.handle(delivery, bundle)
    current = graph.queue.get(delivery.job_id)
    assert current.status == JobStatus.QUEUED and current.payload == delivery.payload
    with db.connect() as conn:
        row = conn.execute("SELECT status,attempt,next_attempt_at IS NOT NULL FROM workflow.jobs WHERE job_id=%s",
            (delivery.job_id,)).fetchone()
        assert row == ("RETRYING", 1, True)
        assert conn.execute("SELECT status FROM identity.match_decisions WHERE match_decision_id=%s",
            (receipt["decision_id"],)).fetchone()[0] == "APPROVED"
    before = _state(case)
    with pytest.raises(JobFenceRejectedError):
        registry.handle(delivery, bundle)
    assert _state(case) == before
    monkeypatch.setattr(IdentityExecutionService, "begin", begin)


def test_preaccept_nonretryable_conflict_deadletters_job_without_mutating_approved_graph(approved):
    case, _ = approved
    graph, ids, _, db = case
    receipt = _admit(approved)
    delivery, registry, bundle = _claim(case, receipt["job_id"])
    with db.connect() as conn:
        conn.execute("UPDATE identity.properties SET version=version+1 WHERE property_id=%s", (ids["source"],))
    registry.handle(delivery, bundle)
    current = graph.queue.get(delivery.job_id)
    assert current.status == JobStatus.FAILED and current.delivery_state.value == "dead_letter"
    assert current.payload == delivery.payload
    with db.connect() as conn:
        assert conn.execute("SELECT status,attempt FROM workflow.jobs WHERE job_id=%s", (delivery.job_id,)).fetchone() == ("FAILED", 1)
        assert conn.execute("SELECT status FROM identity.match_decisions WHERE match_decision_id=%s",
            (receipt["decision_id"],)).fetchone()[0] == "APPROVED"
        event = conn.execute("SELECT payload FROM workflow.outbox_events WHERE event_type='job.dead_lettered'").fetchone()[0]
        from shared.domain.events import validate_event

        assert validate_event(event) == []
    assert graph.resolve_listing(ids["tenant"], ids["listing"])["effective_property_id"] == ids["source"]


def test_native_business_and_transport_heartbeat_renew_together_or_roll_back(approved):
    case, _ = approved
    graph, ids, _, db = case
    receipt = _admit(approved)
    delivery, _, bundle = _claim(case, receipt["job_id"])
    runtime = bundle.canonical_identity_graph()
    IdentityExecutionService(runtime).begin(ids["tenant"], receipt["decision_id"], delivery)
    owner = IdentityJobAdmission(runtime)
    version = owner.heartbeat(delivery.job_id, expected_version=delivery.version, fence_token=delivery.fence_token)
    assert version == delivery.version + 1
    current = graph.queue.get(delivery.job_id)
    with db.connect() as conn:
        row = conn.execute("SELECT lease_expires_at FROM workflow.jobs WHERE job_id=%s", (delivery.job_id,)).fetchone()
        assert row[0] == current.lease_expires_at
        conn.execute("UPDATE workflow.jobs SET cancellation_requested_at=clock_timestamp() WHERE job_id=%s", (delivery.job_id,))
    before = _state(case)
    with pytest.raises(JobFenceRejectedError):
        owner.heartbeat(delivery.job_id, expected_version=version, fence_token=delivery.fence_token)
    assert _state(case) == before


@pytest.mark.parametrize("mutation", ["token", "tenant", "job", "extra", "generation"])
def test_unsigned_or_changed_delivery_cannot_start_a_business_attempt(approved, mutation):
    case, _ = approved
    graph, ids, _, db = case
    receipt = _admit(approved)
    delivery, _, bundle = _claim(case, receipt["job_id"])
    payload = dict(delivery.payload)
    if mutation == "token":
        payload["attempt_token"] = payload["attempt_token"][:-1] + ("a" if payload["attempt_token"][-1] != "a" else "b")
    elif mutation == "tenant":
        payload["tenant_id"] = ids["actor"]
    elif mutation == "job":
        payload["job_id"] = ids["job"]
    elif mutation == "extra":
        payload["_retry_count"] = 1
    else:
        payload["attempt_token"] = IdentityDeliveryAuthenticator(graph.engine).sign(delivery.job_id, ids["tenant"], 2)
    before = _state(case)
    with pytest.raises(JobFenceRejectedError):
        IdentityExecutionService(bundle.canonical_identity_graph()).run(replace(delivery, payload=payload))
    assert _state(case) == before
    with db.connect() as conn:
        assert conn.execute("SELECT status,attempt FROM workflow.jobs WHERE job_id=%s", (receipt["job_id"],)).fetchone() == ("QUEUED", 0)


@pytest.mark.parametrize("missing", [IDENTITY_POLICY_ENV, IDENTITY_SIGNING_KEY_ENV])
def test_missing_server_policy_or_signing_material_cannot_reserve_a_placeholder_job(approved, monkeypatch, missing):
    case, _ = approved
    monkeypatch.delenv(missing)
    before = _state(case)
    with pytest.raises(IdentityDeliveryUnavailable):
        _admit(approved)
    assert _state(case) == before


def test_admission_rechecks_approved_native_resource_versions(approved):
    case, _ = approved
    _, ids, _, db = case
    with db.connect() as conn:
        conn.execute("UPDATE identity.properties SET version=version+1 WHERE property_id=%s", (ids["source"],))
    before = _state(case)
    with pytest.raises(IdentityConflictError, match="VERSION_CONFLICT"):
        _admit(approved)
    assert _state(case) == before
