"""Real SQL owner/CAS/rollback proofs with explicit synthetic inference only."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest

from models.shared_ml.production_runtime import ProductionModelApprovalError
from modules.listing.application.intake_score_delivery import (
    ScoreDeliveryConflict,
    ScoreDeliveryDenied,
)
from modules.sitescore.application.intake_score_worker import (
    handle_intake_score,
    handle_score_execution,
)
from modules.sitescore.application.score_delivery import ScoreExecutionJobRepository
from modules.sitescore.domain.execution import ScoreExecutionCommand
from modules.sitescore.tests.test_sitescore_production_runtime import RecordingRuntime
from shared.auth import Principal, Role, Scope
from shared.infrastructure.persistence.sitescore_execution import ScoreExecutionDocuments
from shared.jobs.queue import JobStatus
from tests.intake_registry_fixture import MANAGER, TENANT
from tests.integration.test_intake_registry_calendar_postgresql import _bundle
from tests.integration.test_intake_score_delivery_postgresql import (
    _approved_score,
    _claim,
    _physical,
)
from tests.integration.test_intake_score_delivery_postgresql import (
    explicit_local_signing_key as explicit_local_signing_key,
)
from tests.integration.test_operator_intake_promotion_postgresql import _promotion_client
from tests.integration.test_operator_intake_workflow_postgresql import (
    bridge_database as bridge_database,
)

pytestmark = pytest.mark.requires_live_env


class SyntheticModelAuthority:
    """Explicit local model port; no remote registry, artifact or grant access."""
    def __init__(self, bundle, runtime):
        self.bundle, self.runtime, self.revoked = bundle, runtime, False
        self.receipt = {"model_id": runtime.binding.model_id, "artifact_sha256": runtime.binding.artifact_sha256}

    def read(self, **kwargs):
        if self.revoked:
            raise ProductionModelApprovalError("synthetic-current-model-revoked")
        return deepcopy(self.receipt)

    @contextmanager
    def guard(self, receipt, *, tenant_id):
        assert tenant_id == TENANT
        with self.bundle.learninghub_repository.release_recovery_guard("sitescore"):
            if receipt != self.read():
                raise ProductionModelApprovalError("synthetic-current-model-changed")
            yield

    def verify_inference(self, receipt, result):
        if (result.binding.model_id != receipt["model_id"] or result.artifact_sha256 != receipt["artifact_sha256"]):
            raise ProductionModelApprovalError("synthetic-inference-pin-mismatch")


@pytest.fixture
def successful(bridge_database, monkeypatch):
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        _, _, promotion = _approved_score(bridge_database, bundle, client)
        runtime = RecordingRuntime()
        handle_intake_score(_claim(bundle, promotion["site_score_job_id"]), bundle, model_runtime=runtime)
        assert _physical(bridge_database, promotion["site_score_job_id"])["status"] == "SUCCEEDED"
        bundle.assisted_intake_store.refresh(TENANT)
        principal = Principal(MANAGER, frozenset({Role.EXPANSION_USER}), Scope(tenant_id=TENANT))
        authority = SyntheticModelAuthority(bundle, runtime)
        delivery = ScoreExecutionJobRepository(bundle, model_admission=authority)
        yield bridge_database, bundle, promotion, runtime, principal, authority, delivery
    finally:
        bundle.engine.close()


def command(delivery, promotion, principal, *, key=None, reason="Synthetic local fresh execution"):
    context = delivery.execution_context(promotion["candidate_site_id"], principal=principal)
    return ScoreExecutionCommand(promotion["candidate_site_id"], context["reportId"], context["reportVersion"],
                                 context["jobVersion"], reason, key or str(uuid4()), str(uuid4()))


def operator_client(bundle, runtime, delivery):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from apps.api.app.routes.operator import create_operator_router
    from modules.opsboard.application.operator_live_repository import OperatorLiveRepository
    from modules.sitescore.application.score_execution import SiteScoreExecutionService
    app = FastAPI()
    app.state.job_queue = bundle.job_queue
    app.include_router(create_operator_router(audit_log=bundle.audit_log, document_store=bundle.assisted_intake_store._store,
        job_queue=bundle.job_queue, listing_repository_for_tenant=bundle.listing_repository_for_tenant,
        sitescore_repository_for_tenant=lambda tenant: bundle._scoped_repository("sitescore_repository", tenant),
        sitescore_decision_repository_for_tenant=bundle.sitescore_decision_store_for_tenant,
        avm_repository_for_tenant=lambda tenant: bundle._scoped_repository("avm_repository", tenant),
        netplan_repository_for_tenant=lambda tenant: bundle._scoped_repository("netplan_repository", tenant),
        priceops_repository_for_tenant=lambda tenant: bundle._scoped_repository("priceops_repository", tenant),
        model_runtime=runtime, score_execution_service=SiteScoreExecutionService(delivery) if delivery is not None else None,
        intake_workflow_store=bundle.assisted_intake_store, intake_workflow_policy=bundle.assisted_intake_workflow_policy,
        live_repository=OperatorLiveRepository(bundle), require_live_data=True, persistence_mode="postgresql", provider_mode="live"), prefix="/api/v1")
    return TestClient(app)


def test_fresh_same_candidate_next_report_and_original_acceptance_are_immutable(successful):
    db, bundle, promotion, runtime, principal, authority, delivery = successful
    before = _physical(db, promotion["site_score_job_id"])
    original_promotion = deepcopy(bundle.assisted_intake_store.promotions[promotion["promotion_decision_id"]])
    repository = bundle.listing_repository_for_tenant(TENANT)
    candidate = repository.list_candidates()[0]
    candidate.candidate_site = replace(candidate.candidate_site, site_status="approved")
    repository.save_candidate(candidate)
    request = delivery.create_execution(command(delivery, promotion, principal), principal=principal)
    assert request["status"] == "QUEUED" and request["result"] is None and request["actualVersion"] == 1
    assert request["jobId"] != before["job_id"]
    assert len(runtime.calls) == 1
    claimed = _claim(bundle, request["jobId"])
    handle_score_execution(claimed, bundle, model_runtime=runtime, model_admission=authority)
    completed = delivery.get_execution(request["executionRequestId"], principal=principal)
    assert completed["status"] == "SUCCEEDED" and completed["actualVersion"] > 1
    assert completed["result"]["report_version"] == 2
    assert completed["result"]["report_id"] != before["result"]["report_id"]
    assert _physical(db, promotion["site_score_job_id"]) == before
    bundle.assisted_intake_store.refresh(TENANT)
    assert bundle.assisted_intake_store.promotions[promotion["promotion_decision_id"]] == original_promotion
    assert repository.list_candidates()[0].candidate_site.site_status == "approved"
    assert len(runtime.calls) == 2


@pytest.mark.parametrize("max_attempts", [1, 2], ids=["exhausted", "in_budget"])
def test_fresh_failure_emits_dead_letter_only_at_recorded_budget(successful, monkeypatch, max_attempts):
    import json
    from datetime import datetime

    from apps.worker.oday_worker.handlers import build_default_registry
    from modules.listing.application.intake_score_retry_policy import RETRY_POLICY_ENV
    from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError

    db, bundle, promotion, runtime, principal, authority, delivery = successful
    # Bind a legal policy before admission; never rewrite an admitted attempt/budget.
    policy = {"policy_id": "synthetic-fresh-dlq-v1", "version": 1,
              "max_attempts": max_attempts, "backoff_seconds": [600] * (max_attempts - 1)}
    monkeypatch.setenv(RETRY_POLICY_ENV, json.dumps(policy))
    original_job = _physical(db, promotion["site_score_job_id"])
    original_promotion = deepcopy(bundle.assisted_intake_store.promotions[promotion["promotion_decision_id"]])
    candidates = deepcopy(bundle.listing_repository_for_tenant(TENANT).list_candidates())
    reports = delivery._reports(TENANT).history(promotion["candidate_site_id"])
    original_command = command(delivery, promotion, principal)
    request = delivery.create_execution(original_command, principal=principal)
    admitted = _physical(db, request["jobId"])
    assert admitted["attempt"] == 0 and admitted["max_attempts"] == max_attempts
    assert admitted["payload"]["retry_policy"] == policy
    failed_calls = []

    def fail(**kwargs):
        failed_calls.append(kwargs)
        raise TimeoutError("synthetic-fresh-score-transient")

    monkeypatch.setattr(runtime, "infer", fail)
    claimed = _claim(bundle, request["jobId"])
    handler = build_default_registry().get(claimed.job_type)
    assert handler is handle_score_execution
    handler(claimed, bundle, model_runtime=runtime, model_admission=authority)
    row = _physical(db, request["jobId"])
    receipt = delivery.get_execution(request["executionRequestId"], principal=principal)
    queued = bundle.job_queue.get(request["jobId"])
    assert len(failed_calls) == 1
    assert row["attempt"] == 1 and row["max_attempts"] == admitted["max_attempts"]
    assert row["payload"]["retry_policy"] == policy
    assert row["payload"]["admission"] == admitted["payload"]["admission"]
    acceptance = row["payload"]["worker_acceptance"]
    assert acceptance["job_id"] == request["jobId"]
    assert acceptance["execution_request_id"] == request["executionRequestId"]
    assert acceptance["accepted_fence"] == row["fence_token"]
    assert row["last_error"] == {"code": "SITESCORE_INFERENCE_UNAVAILABLE", "retryable": True}
    assert row["result"] is None and receipt["result"] is None
    assert queued.attempts == 1 and queued.max_retries == max_attempts
    events = bundle.outbox_repository.get_unpublished_events()
    job_events = [event for event in events if event.payload.get("job_id") == request["jobId"]]
    dead_letters = [event for event in job_events if event.event_type == "job.dead_lettered"]
    failures = [event for event in job_events if event.event_type == "sitescore.execution_failed"]
    if max_attempts == 1:
        assert row["status"] == receipt["status"] == "DEAD_LETTER"
        assert queued.status == JobStatus.FAILED and queued.delivery_state == JobDeliveryState.DEAD_LETTER
        assert row["next_attempt_at"] is None and row["dead_lettered_at"] is not None
        # The existing sitescore.failed call is mapped to this fresh-request event.
        assert len(failures) == 1
        assert failures[0].aggregate_id == request["executionRequestId"]
        assert failures[0].payload["status"] == "DEAD_LETTER"
        assert len(dead_letters) == 1
        event = dead_letters[0]
        assert event.event_version == 1 and event.producer == "job_platform"
        assert event.schema_ref == "#/payloads/JobDeadLetteredV1"
        assert event.tenant_id == TENANT and event.aggregate_type == "job"
        assert event.aggregate_id == request["jobId"] and event.aggregate_version == row["version"]
        assert event.partition_key == f"{TENANT}:{request['jobId']}"
        assert event.correlation_id == row["correlation_id"]
        assert event.sensitive_fields == ["payload.last_error_details"]
        assert {key: event.payload[key] for key in ("job_id", "job_type", "checkpoint", "attempt", "error_code")} == {
            "job_id": row["job_id"], "job_type": row["job_type"], "checkpoint": row["checkpoint"],
            "attempt": row["attempt"], "error_code": row["last_error"]["code"],
        }
        assert datetime.fromisoformat(event.payload["dead_lettered_at"]) == datetime.fromisoformat(row["dead_lettered_at"])
        # Neither an ordinary retry nor a newly configured budget may reopen this job.
        retry_key = str(uuid4())
        for offered_policy in (policy, {**policy, "max_attempts": 5, "backoff_seconds": [10, 30, 120, 600]}):
            monkeypatch.setenv(RETRY_POLICY_ENV, json.dumps(offered_policy))
            with pytest.raises(ScoreDeliveryConflict, match="VERSION_OR_STATE"):
                delivery.retry_execution(request["executionRequestId"], principal=principal,
                    expected_version=receipt["actualVersion"], expected_job_version=receipt["jobVersion"],
                    reason="Synthetic exhausted retry", idempotency_key=retry_key, correlation_id=str(uuid4()))
            with pytest.raises(ScoreDeliveryConflict, match="ACTIVE_OR_FAILED"):
                delivery.create_execution(replace(original_command, idempotency_key=str(uuid4())), principal=principal)
    else:
        assert row["status"] == receipt["status"] == "RETRYING"
        assert queued.status == JobStatus.QUEUED and queued.delivery_state == JobDeliveryState.RETRYING
        assert row["next_attempt_at"] is not None and row["dead_lettered_at"] is None
        assert dead_letters == [] and failures == []
    replay = delivery.create_execution(original_command, principal=principal)
    assert replay == receipt and replay["created"] is False
    with pytest.raises(JobFenceRejectedError):
        handler(claimed, bundle, model_runtime=runtime, model_admission=authority)
    assert len(failed_calls) == 1
    assert _physical(db, request["jobId"]) == row
    assert bundle.job_queue.get(request["jobId"]) == queued
    assert delivery.get_execution(request["executionRequestId"], principal=principal) == receipt
    assert bundle.outbox_repository.get_unpublished_events() == events
    assert _physical(db, promotion["site_score_job_id"]) == original_job
    bundle.assisted_intake_store.refresh(TENANT)
    assert bundle.assisted_intake_store.promotions[promotion["promotion_decision_id"]] == original_promotion
    assert bundle.listing_repository_for_tenant(TENANT).list_candidates() == candidates
    assert delivery._reports(TENANT).history(promotion["candidate_site_id"]) == reports


@pytest.mark.parametrize("fault", ["audit", "outbox"])
def test_fresh_dead_letter_settlement_rolls_back_and_rejects_stale_attempt(successful, monkeypatch, fault):
    import json

    from modules.listing.application.intake_score_retry_policy import RETRY_POLICY_ENV
    from shared.jobs.queue import JobFenceRejectedError

    db, bundle, promotion, _, principal, _, delivery = successful
    monkeypatch.setenv(RETRY_POLICY_ENV, json.dumps({
        "policy_id": "synthetic-fresh-dlq-fault-v1", "version": 1,
        "max_attempts": 1, "backoff_seconds": [],
    }))
    request = delivery.create_execution(command(delivery, promotion, principal), principal=principal)
    claimed = _claim(bundle, request["jobId"])
    attempt = delivery.begin_attempt(claimed)
    assert attempt is not None
    before = _physical(db, request["jobId"])
    assert before["status"] == "RUNNING" and before["attempt"] == before["max_attempts"] == 1
    assert before["payload"]["worker_acceptance"]["accepted_fence"] == attempt.token["fence"]
    original_job = _physical(db, promotion["site_score_job_id"])
    original_promotion = deepcopy(bundle.assisted_intake_store.promotions[promotion["promotion_decision_id"]])
    candidates = deepcopy(bundle.listing_repository_for_tenant(TENANT).list_candidates())
    reports = delivery._reports(TENANT).history(promotion["candidate_site_id"])
    documents = delivery._documents(TENANT)
    saved_documents = {collection: documents.store.list_all(collection)
                       for collection in (documents.REQUESTS, documents.HEADS, documents.KEYS)}
    # Include audit delivery rows so a leaked audit enqueue cannot escape detection.
    queue_rows = bundle.engine.query("SELECT * FROM durable_jobs ORDER BY job_id")
    audits = deepcopy(bundle.assisted_intake_store.workflow_audits)
    audit_writer = bundle.assisted_intake_store._canonical_audit_sink.writer
    canonical_audits = audit_writer.list_events(TENANT)
    events = bundle.outbox_repository.get_unpublished_events()
    assert not any(event.event_type == "job.dead_lettered" and event.payload.get("job_id") == request["jobId"]
                   for event in events)
    with monkeypatch.context() as fault_patch:
        if fault == "audit":
            original_flush = bundle.assisted_intake_store.flush

            def fail_flush():
                original_flush()
                raise RuntimeError("synthetic-dlq-audit-fault")

            fault_patch.setattr(bundle.assisted_intake_store, "flush", fail_flush)
        else:
            original_save = bundle.outbox_repository.save

            def fail_save(event):
                original_save(event)
                if event.event_type == "job.dead_lettered" and event.payload.get("job_id") == request["jobId"]:
                    raise RuntimeError("synthetic-dlq-outbox-fault")

            fault_patch.setattr(bundle.outbox_repository, "save", fail_save)
        with pytest.raises(RuntimeError, match=f"synthetic-dlq-{fault}-fault"):
            delivery.fail_attempt(claimed, attempt, error_code="SITESCORE_INFERENCE_UNAVAILABLE", retryable=True)
    assert _physical(db, request["jobId"]) == before
    assert bundle.engine.query("SELECT * FROM durable_jobs ORDER BY job_id") == queue_rows
    assert {collection: documents.store.list_all(collection) for collection in saved_documents} == saved_documents
    bundle.assisted_intake_store.refresh(TENANT)
    assert bundle.assisted_intake_store.workflow_audits == audits
    assert audit_writer.list_events(TENANT) == canonical_audits
    assert bundle.outbox_repository.get_unpublished_events() == events
    # Expire the actual accepted lease using the existing fault-test mechanism.
    with db.connect() as connection:
        connection.execute("UPDATE workflow.jobs SET lease_expires_at=now()-interval '1 minute' WHERE job_id=%s",
                           (request["jobId"],))
    stale = _physical(db, request["jobId"])
    with pytest.raises(JobFenceRejectedError, match="SITESCORE_RESULT_FENCE_REJECTED"):
        delivery.fail_attempt(claimed, attempt, error_code="SITESCORE_INFERENCE_UNAVAILABLE", retryable=True)
    assert _physical(db, request["jobId"]) == stale
    assert bundle.engine.query("SELECT * FROM durable_jobs ORDER BY job_id") == queue_rows
    assert {collection: documents.store.list_all(collection) for collection in saved_documents} == saved_documents
    bundle.assisted_intake_store.refresh(TENANT)
    assert bundle.assisted_intake_store.workflow_audits == audits
    assert audit_writer.list_events(TENANT) == canonical_audits
    assert bundle.outbox_repository.get_unpublished_events() == events
    assert _physical(db, promotion["site_score_job_id"]) == original_job
    assert bundle.assisted_intake_store.promotions[promotion["promotion_decision_id"]] == original_promotion
    assert bundle.listing_repository_for_tenant(TENANT).list_candidates() == candidates
    assert delivery._reports(TENANT).history(promotion["candidate_site_id"]) == reports


def test_same_key_pending_and_successful_replay_never_enqueue_or_infer(successful):
    _, bundle, promotion, runtime, principal, authority, delivery = successful
    original_command = command(delivery, promotion, principal)
    first = delivery.create_execution(original_command, principal=principal)
    pending = delivery.create_execution(original_command, principal=principal)
    assert pending["executionRequestId"] == first["executionRequestId"] and pending["created"] is False
    handle_score_execution(_claim(bundle, first["jobId"]), bundle, model_runtime=runtime, model_admission=authority)
    authority.revoked = True
    replay = delivery.create_execution(original_command, principal=principal)
    assert replay["status"] == "SUCCEEDED" and replay["created"] is False
    assert len(runtime.calls) == 2
    assert len(delivery._documents(TENANT).store.list_all(ScoreExecutionDocuments.REQUESTS)) == 1


def test_new_key_after_success_uses_latest_predecessor_but_original_parent_stays_fixed(successful):
    db, bundle, promotion, runtime, principal, authority, delivery = successful
    first = delivery.create_execution(command(delivery, promotion, principal), principal=principal)
    handle_score_execution(_claim(bundle, first["jobId"]), bundle, model_runtime=runtime, model_admission=authority)
    second = delivery.create_execution(command(delivery, promotion, principal), principal=principal)
    handle_score_execution(_claim(bundle, second["jobId"]), bundle, model_runtime=runtime, model_admission=authority)
    saved = delivery.get_execution(second["executionRequestId"], principal=principal)
    assert saved["result"]["report_version"] == 3
    document = delivery._documents(TENANT).store.get(ScoreExecutionDocuments.REQUESTS, second["executionRequestId"])
    assert document["original_job_id"] == promotion["site_score_job_id"]
    assert document["predecessor_job_id"] == first["jobId"]
    assert _physical(db, promotion["site_score_job_id"])["status"] == "SUCCEEDED"


def test_same_key_changed_payload_and_other_active_key_are_conflicts(successful):
    _, _, promotion, runtime, principal, _, delivery = successful
    original_command = command(delivery, promotion, principal)
    delivery.create_execution(original_command, principal=principal)
    with pytest.raises(ScoreDeliveryConflict, match="IDEMPOTENCY"):
        delivery.create_execution(replace(original_command, reason="changed command"), principal=principal)
    with pytest.raises(ScoreDeliveryConflict, match="ACTIVE"):
        delivery.create_execution(replace(original_command, idempotency_key=str(uuid4())), principal=principal)
    assert len(runtime.calls) == 1


def test_revocation_during_infer_never_publishes_or_downgrades_original_human_status(successful):
    db, bundle, promotion, runtime, principal, authority, delivery = successful
    repo = bundle.listing_repository_for_tenant(TENANT)
    candidate = repo.list_candidates()[0]
    candidate.candidate_site = replace(candidate.candidate_site, site_status="opened")
    repo.save_candidate(candidate)
    before = _physical(db, promotion["site_score_job_id"])
    request = delivery.create_execution(command(delivery, promotion, principal), principal=principal)
    old_infer = runtime.infer
    def revoke(**kwargs):
        result = old_infer(**kwargs)
        authority.revoked = True
        return result
    runtime.infer = revoke
    handle_score_execution(_claim(bundle, request["jobId"]), bundle, model_runtime=runtime, model_admission=authority)
    failed = delivery.get_execution(request["executionRequestId"], principal=principal)
    assert failed["status"] == "FAILED" and failed["result"] is None
    assert delivery._reports(TENANT).latest(request["candidateSiteId"]).report_version == 1
    assert _physical(db, promotion["site_score_job_id"]) == before
    assert repo.list_candidates()[0].candidate_site.site_status == "opened"
    authority.revoked = False
    with pytest.raises(ScoreDeliveryConflict, match="ACTIVE_OR_FAILED"):
        delivery.create_execution(command(delivery, promotion, principal), principal=principal)


def test_enqueue_fault_rolls_back_request_head_job_delivery_and_audit(successful, monkeypatch):
    db, bundle, promotion, _, principal, _, delivery = successful
    before = _physical(db, promotion["site_score_job_id"])
    original = bundle.job_queue.enqueue
    def broken(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("synthetic-enqueue-fault")
    monkeypatch.setattr(bundle.job_queue, "enqueue", broken)
    with pytest.raises(RuntimeError, match="synthetic-enqueue-fault"):
        delivery.create_execution(command(delivery, promotion, principal), principal=principal)
    assert delivery._documents(TENANT).store.list_all(ScoreExecutionDocuments.REQUESTS) == []
    assert delivery._documents(TENANT).store.list_all(ScoreExecutionDocuments.HEADS) == []
    with db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM workflow.jobs WHERE job_type='sitescore-execution'").fetchone()[0] == 0
    assert _physical(db, promotion["site_score_job_id"]) == before


def test_current_recorded_hard_rule_withdrawal_denies_before_new_job(successful):
    _, bundle, promotion, runtime, principal, _, delivery = successful
    expected = command(delivery, promotion, principal)
    repo = bundle.listing_repository_for_tenant(TENANT)
    listing_id = repo.list_candidates()[0].candidate_site.listing_id
    documents = delivery._documents(TENANT)
    from shared.infrastructure.persistence.operator_network_listings import (
        DurableAssistedIntakeRepository,
    )
    metadata = deepcopy(documents.store.get(DurableAssistedIntakeRepository._LISTING_META, listing_id) or {})
    documents.store.put(DurableAssistedIntakeRepository._LISTING_META, listing_id, {"hardRuleFailures": None})
    with pytest.raises(ScoreDeliveryDenied, match="CURRENT_HARD_RULE"):
        delivery.create_execution(expected, principal=principal)
    assert len(runtime.calls) == 1
    assert documents.store.list_all(ScoreExecutionDocuments.REQUESTS) == []

    # The completed original job is historical. A fresh request must reject a
    # current domain/metadata conflict before pinning its own new fingerprint.
    candidate = repo.list_candidates()[0]
    recorded = getattr(candidate.listing, "prior_90d_cell_net_revenue", None)
    assert recorded is not None and recorded == candidate.prior_90d_cell_net_revenue
    metadata["prior90dCellNetRevenue"] = recorded
    documents.store.put(DurableAssistedIntakeRepository._LISTING_META, listing_id, metadata)
    delivery._current_inputs(TENANT, candidate)
    current = repo.get_listing(listing_id)
    object.__setattr__(current, "prior_90d_cell_net_revenue", recorded + 1)
    # Replace only the existing recorded row; identity/address/dedup stay intact.
    repo._store.put(repo._LISTINGS, listing_id, current)
    with pytest.raises(ScoreDeliveryDenied, match="CURRENT_INPUT_EVIDENCE_CONFLICT"):
        delivery.create_execution(expected, principal=principal)
    assert len(runtime.calls) == 1
    assert documents.store.list_all(ScoreExecutionDocuments.REQUESTS) == []


def test_retry_failed_execution_uses_same_job_generation_and_preserves_candidate_status(successful):
    db, bundle, promotion, runtime, principal, authority, delivery = successful
    request = delivery.create_execution(command(delivery, promotion, principal), principal=principal)
    original_infer = runtime.infer
    def fail(**kwargs):
        raise ProductionModelApprovalError("synthetic-attempt-failure")
    runtime.infer = fail
    handle_score_execution(_claim(bundle, request["jobId"]), bundle, model_runtime=runtime, model_admission=authority)
    failed = delivery.get_execution(request["executionRequestId"], principal=principal)
    old = _physical(db, request["jobId"])
    key = str(uuid4())
    retried = delivery.retry_execution(request["executionRequestId"], principal=principal,
        expected_version=failed["actualVersion"], expected_job_version=failed["jobVersion"], reason="Synthetic local retry", idempotency_key=key, correlation_id=str(uuid4()))
    assert retried["jobId"] == request["jobId"] and retried["status"] == "QUEUED"
    assert _physical(db, request["jobId"])["payload"]["generation"] == old["payload"]["generation"] + 1
    replay = delivery.retry_execution(request["executionRequestId"], principal=principal,
        expected_version=failed["actualVersion"], expected_job_version=failed["jobVersion"], reason="Synthetic local retry", idempotency_key=key, correlation_id=str(uuid4()))
    assert replay["jobVersion"] == retried["jobVersion"]
    runtime.infer = original_infer
    handle_score_execution(_claim(bundle, request["jobId"]), bundle, model_runtime=runtime, model_admission=authority)
    assert delivery.get_execution(request["executionRequestId"], principal=principal)["status"] == "SUCCEEDED"
    assert _physical(db, promotion["site_score_job_id"])["status"] == "SUCCEEDED"


@pytest.mark.parametrize("same_key", [True, False])
def test_independent_engines_concurrent_requests_reserve_one_active_owner(successful, monkeypatch, same_key):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    db, bundle, promotion, runtime, principal, _, first_owner = successful
    other = _bundle(db, monkeypatch)
    try:
        second_owner = ScoreExecutionJobRepository(other, model_admission=SyntheticModelAuthority(other, runtime))
        original = command(first_owner, promotion, principal)
        alternate = original if same_key else replace(original, idempotency_key=str(uuid4()))
        barrier = Barrier(2)
        def create(owner, cmd):
            barrier.wait(timeout=10)
            try:
                return owner.create_execution(cmd, principal=principal)
            except ScoreDeliveryConflict as exc:
                return exc
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(create, first_owner, original), pool.submit(create, second_owner, alternate)]
            results = [f.result(timeout=30) for f in futures]
        receipts = [value for value in results if isinstance(value, dict)]
        assert len(receipts) == (2 if same_key else 1)
        assert len({receipt["executionRequestId"] for receipt in receipts}) == 1
        assert len(first_owner._documents(TENANT).store.list_all(ScoreExecutionDocuments.REQUESTS)) == 1
        assert len(runtime.calls) == 1
    finally:
        other.engine.close()


def test_publication_fault_rolls_back_report_prediction_candidate_and_job(successful, monkeypatch):
    from modules.sitescore.application.intake_score_worker import _JobResultRepository
    db, bundle, promotion, runtime, principal, authority, delivery = successful
    request = delivery.create_execution(command(delivery, promotion, principal), principal=principal)
    before = deepcopy(bundle.listing_repository_for_tenant(TENANT).list_candidates()[0])
    original = _JobResultRepository.save_prediction_run
    def fault(self, run):
        original(self, run)
        raise RuntimeError("synthetic-result-fault")
    monkeypatch.setattr(_JobResultRepository, "save_prediction_run", fault)
    with pytest.raises(RuntimeError, match="synthetic-result-fault"):
        handle_score_execution(_claim(bundle, request["jobId"]), bundle, model_runtime=runtime, model_admission=authority)
    row = _physical(db, request["jobId"])
    assert row["status"] == "RUNNING" and row["result"] is None
    assert delivery._reports(TENANT).latest(promotion["candidate_site_id"]).report_version == 1
    assert vars(bundle.listing_repository_for_tenant(TENANT).list_candidates()[0]) == vars(before)


@pytest.mark.parametrize("operation", ["create", "retry"])
@pytest.mark.parametrize("constraint", ["body", "header", "tenant"])
def test_signed_score_actor_intent_conflict_does_not_write(successful, tmp_path, monkeypatch,
                                                        operation, constraint):
    from datetime import UTC, datetime, timedelta

    from apps.api.oday_api.security import dependencies
    from models.shared_ml.current_model_admission import CurrentModelAdmissionPort
    from modules.opsboard.auth import (
        AuthBoundaryConfig,
        AuthenticationBoundary,
        SigningKey,
        encode_compact_jwt,
    )
    from tests.integration.test_production_model_runtime import _registered_governed_runtime

    db, bundle, promotion, _, _, _, _ = successful
    actor_b = "00000000-0000-0000-0000-000000000203"
    actor_denied = "00000000-0000-0000-0000-000000000204"
    with monkeypatch.context() as fixture_environment:
        fixture_environment.setenv("ODP_REQUIRE_LIVE_DATA", "false")
        fixture_environment.setenv("ODP_PRODUCT_MODE", "poc")
        fixture_environment.setenv("ODAY_ENV", "test")
        runtime, _ = _registered_governed_runtime(tmp_path / "signed-context-model", bundle.learninghub_repository)
    port = CurrentModelAdmissionPort(runtime=runtime, registry_repository=bundle.learninghub_repository,
                                     engine=bundle.engine, tenant_id=TENANT)
    owner = ScoreExecutionJobRepository(bundle, model_admission=port)
    client = operator_client(bundle, runtime, owner)
    signing_key = SigningKey(kid="synthetic-score-intent", algorithm="HS256",
                             secret=b"explicit-local-synthetic-score-intent-key")
    boundary = AuthenticationBoundary(AuthBoundaryConfig(
        issuer="https://score-intent.example.invalid", audiences=frozenset({"local-score-intent"}),
        signing_keys={signing_key.kid: signing_key}, principal_mappings={
            MANAGER: {"roles": ["site_reviewer", "expansion_user"], "scope": {"tenant_id": TENANT}},
            actor_b: {"roles": ["site_reviewer", "expansion_user"], "scope": {"tenant_id": TENANT}},
            actor_denied: {"roles": [], "scope": {"tenant_id": TENANT}},
        }))
    monkeypatch.setattr(dependencies, "_default_boundary", boundary)
    calls = []
    original_infer = runtime.infer
    def infer(**kwargs):
        calls.append(kwargs)
        return original_infer(**kwargs)
    monkeypatch.setattr(runtime, "infer", infer)
    key, correlation = str(uuid4()), str(uuid4())
    def headers(actor, **extra):
        now = datetime.now(UTC)
        token = encode_compact_jwt({"sub": actor, "iss": "https://score-intent.example.invalid",
            "aud": "local-score-intent", "iat": now.timestamp(),
            "exp": (now + timedelta(minutes=20)).timestamp()}, signing_key)
        return {"Authorization": "Bearer " + token, "Idempotency-Key": key,
                "X-Correlation-Id": correlation, **extra}
    def business_counts():
        return {table: bundle.engine.query_one(f"SELECT count(*) AS n FROM {table}")["n"]
                for table in ("workflow.jobs", "durable_jobs", "durable_documents")}
    candidate_id = promotion["candidate_site_id"]
    response = client.get(f"/api/v1/operator/network-scoring/candidates/{candidate_id}/execution-context",
                          headers=headers(MANAGER))
    assert response.status_code == 200, response.text
    context = response.json()
    assert context["tenantId"] == TENANT
    body = {"expectedReportId": context["reportId"], "expectedReportVersion": context["reportVersion"],
            "expectedJobVersion": context["jobVersion"], "reason": "Synthetic signed score intent"}
    url = f"/api/v1/operator/network-scoring/candidates/{candidate_id}/score"
    before = business_counts()
    denied = client.post(url, headers=headers(actor_denied), json=body)
    assert denied.status_code == 403
    assert business_counts() == before
    prior_job = None
    if operation == "retry":
        first = client.post(url, headers=headers(MANAGER), json=body)
        assert first.status_code == 202, first.text
        receipt = first.json()["executionRequest"]
        assert first.json()["commandReceipt"]["actorSubjectId"] == MANAGER
        with db.connect() as connection:
            connection.execute("UPDATE workflow.jobs SET cancellation_requested_at=now() WHERE job_id=%s",
                               (receipt["jobId"],))
        handle_score_execution(_claim(bundle, receipt["jobId"]), bundle, model_runtime=runtime, model_admission=port)
        cancelled = client.get(f"/api/v1/operator/network-scoring/execution-requests/{receipt['executionRequestId']}",
                               headers=headers(MANAGER)).json()
        assert cancelled["status"] == "CANCELLED"
        prior_job = deepcopy(_physical(db, receipt["jobId"]))
        body = {"expectedVersion": cancelled["actualVersion"], "expectedJobVersion": cancelled["jobVersion"],
                "reason": "Synthetic same-job retry intent"}
        url = f"/api/v1/operator/network-scoring/execution-requests/{receipt['executionRequestId']}/retry"
    stale_headers = headers(actor_b)
    if constraint == "body":
        body["expectedActorSubjectId"] = MANAGER
    elif constraint == "header":
        stale_headers["If-Actor-Match"] = MANAGER
    else:
        body["expectedTenantId"] = str(uuid4())
    before = business_counts()
    refused = client.post(url, headers=stale_headers, json=body)
    assert refused.status_code == 409, refused.text
    assert business_counts() == before
    if prior_job is not None:
        assert _physical(db, receipt["jobId"]) == prior_job
    assert owner._reports(TENANT).latest(candidate_id).report_version == 1
    assert calls == []


def test_actual_foreign_registry_cannot_verify_current_candidate_model(successful, tmp_path, monkeypatch):
    from models.shared_ml.current_model_admission import CurrentModelAdmissionPort
    from models.shared_ml.production_runtime import ProductionModelRegistryError
    from modules.sitescore.domain.scoring import SITESCORE_FEATURE_VERSION
    from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
    from shared.infrastructure.persistence.repositories import DurableLearningHubRepository
    from tests.integration.test_operator_intake_workflow_postgresql import _headers
    from tests.integration.test_production_model_runtime import _registered_governed_runtime

    _, bundle, promotion, _, principal, _, _ = successful
    foreign_store = TenantScopedDocumentStore(bundle.learninghub_repository._store, str(uuid4()))
    foreign_repository = DurableLearningHubRepository(foreign_store)
    with monkeypatch.context() as fixture_environment:
        fixture_environment.setenv("ODP_REQUIRE_LIVE_DATA", "false")
        fixture_environment.setenv("ODP_PRODUCT_MODE", "poc")
        fixture_environment.setenv("ODAY_ENV", "test")
        runtime, artifact = _registered_governed_runtime(tmp_path / "foreign-model", foreign_repository)
    loads, inferences = [], []
    original_loader, original_infer = runtime.artifact_loader, runtime.infer
    def load(*args):
        loads.append(args)
        return original_loader(*args)
    def infer(**kwargs):
        inferences.append(kwargs)
        return original_infer(**kwargs)
    monkeypatch.setattr(runtime, "artifact_loader", load)
    monkeypatch.setattr(runtime, "infer", infer)
    port = CurrentModelAdmissionPort(runtime=runtime, registry_repository=foreign_repository,
                                    engine=bundle.engine, tenant_id=None)
    delivery = ScoreExecutionJobRepository(bundle, model_admission=port)
    reports = delivery._reports(TENANT)
    before_report = reports.latest(promotion["candidate_site_id"])
    before_jobs = bundle.engine.query_one("SELECT count(*) AS n FROM workflow.jobs")["n"]
    # Use real approved metadata in the foreign store so an empty registry
    # cannot accidentally make this tenant isolation regression pass.
    model_receipt = port.read(service="sitescore", expected_feature_schema_version=SITESCORE_FEATURE_VERSION)
    assert model_receipt["receipt_sha256"] and artifact.is_file()
    assert foreign_store.engine is bundle.engine
    response = operator_client(bundle, runtime, delivery).get("/api/v1/operator/network-scoring", headers=_headers())
    assert response.status_code == 200, response.text
    candidate = next(row for row in response.json()["candidates"] if row["id"] == promotion["candidate_site_id"])
    assert candidate["currentAdmission"]["model"]["state"] == "unverified"
    with pytest.raises(ProductionModelRegistryError, match="different tenant scope"):
        with port.guard(model_receipt, tenant_id=TENANT):
            pytest.fail("foreign registry reached publication")
    with pytest.raises(ProductionModelRegistryError, match="different tenant scope"):
        delivery.create_execution(command(delivery, promotion, principal), principal=principal)
    assert reports.latest(promotion["candidate_site_id"]) == before_report
    assert bundle.engine.query_one("SELECT count(*) AS n FROM workflow.jobs")["n"] == before_jobs
    assert loads == [] and inferences == []


def test_authoritative_fresh_worker_executes_actual_registered_artifact(successful, tmp_path, monkeypatch):
    from models.shared_ml.current_model_admission import CurrentModelAdmissionPort
    from tests.integration.test_production_model_runtime import _registered_governed_runtime
    db, bundle, promotion, _, principal, _, _ = successful
    # Build only the explicit local model fixture with live mode disabled;
    # restore it before exercising the production request/queue/worker boundary.
    with monkeypatch.context() as fixture_environment:
        fixture_environment.setenv("ODP_REQUIRE_LIVE_DATA", "false")
        fixture_environment.setenv("ODP_PRODUCT_MODE", "poc")
        fixture_environment.setenv("ODAY_ENV", "test")
        runtime, artifact = _registered_governed_runtime(tmp_path / "synthetic-model", bundle.learninghub_repository)
    authority = CurrentModelAdmissionPort(runtime=runtime, registry_repository=bundle.learninghub_repository, engine=bundle.engine, tenant_id=TENANT)
    delivery = ScoreExecutionJobRepository(bundle, model_admission=authority)
    actual = []
    builds = []
    import modules.listing.application.intake_score_delivery as worker_delivery
    original_builder = worker_delivery.feature_from_candidate
    def build(actual_draft, *, tenant_id):
        feature = original_builder(actual_draft, tenant_id=tenant_id)
        builds.append((actual_draft, tenant_id, feature))
        return feature
    monkeypatch.setattr(worker_delivery, "feature_from_candidate", build)
    original = runtime.infer
    loads = []
    original_loader = runtime.artifact_loader
    def artifact_loader(*args):
        loads.append(args)
        return original_loader(*args)
    monkeypatch.setattr(runtime, "artifact_loader", artifact_loader)
    def infer(**kwargs):
        result = original(**kwargs)
        actual.append(result)
        return result
    monkeypatch.setattr(runtime, "infer", infer)
    from tests.integration.test_operator_intake_workflow_postgresql import _headers
    client = operator_client(bundle, runtime, delivery)
    read = client.get("/api/v1/operator/network-scoring", headers=_headers())
    assert read.status_code == 200, read.text
    current_candidate = next(row for row in read.json()["candidates"] if row["id"] == promotion["candidate_site_id"])
    assert current_candidate["currentAdmission"]["model"]["state"] == "verified"
    assert all(current_candidate["currentAdmission"][key]["state"] == "verified" for key in ("currentInputs", "source", "pit"))
    assert actual == [] and builds == [] and loads == []
    import json
    import os
    from pathlib import Path
    if readback_path := os.getenv("ODAY_CANONICAL_SCORE_READBACK"):
        network = client.get("/api/v1/operator/network-listings", headers=_headers())
        assert network.status_code == 200, network.text
        Path(readback_path).write_text(json.dumps({"network": network.json(), "scoring": read.json(), "candidateId": promotion["candidate_site_id"]}, indent=2) + "\n")
    request = delivery.create_execution(command(delivery, promotion, principal), principal=principal)
    assert request["status"] == "QUEUED" and actual == [] and builds == [] and artifact.is_file()
    from apps.worker.oday_worker.handlers import build_default_registry
    from models.shared_ml.production_runtime import MlflowProductionModelRuntime
    monkeypatch.setattr(MlflowProductionModelRuntime, "from_environment", classmethod(lambda cls: runtime))
    claimed = _claim(bundle, request["jobId"])
    build_default_registry().get(claimed.job_type)(claimed, bundle)
    completed = delivery.get_execution(request["executionRequestId"], principal=principal)
    assert completed["status"] == "SUCCEEDED" and len(actual) == 1
    assert len(builds) == 1
    observed, tenant, feature = builds[0]
    pinned = delivery._documents(TENANT).store.get(ScoreExecutionDocuments.REQUESTS, request["executionRequestId"])
    bundle.assisted_intake_store.refresh(TENANT)
    intake = bundle.assisted_intake_store.intakes[promotion["intake_id"]]
    initial_promotion = bundle.assisted_intake_store.promotions[promotion["promotion_decision_id"]]
    assert tenant == TENANT and observed.candidate_site.candidate_site_id == promotion["candidate_site_id"]
    assert observed.candidate_site.listing_id == pinned["listing_id"]
    assert observed.candidate_site.address_id == observed.address.address_id
    assert delivery._fingerprint(intake, initial_promotion, observed) == pinned["input_sha256"]
    assert feature.monthly_rent == observed.listing.rent_amount == 54000
    assert feature.area_ping == observed.listing.area_ping == 22
    assert feature.geocode_confidence == observed.address.geocode_confidence == 0.95
    assert feature.prior_90d_cell_net_revenue == 1_800_000.0
    assert feature.prior_90d_cell_transaction_count == 720
    assert feature.prior_90d_cell_store_count == 4
    assert feature.feature_snapshot_time == observed.feature_snapshot_time
    result = _physical(db, request["jobId"])["result"]
    assert result["model_lineage"]["model_engine"] == "lightgbm.LGBMRegressor"
    assert result["model_lineage"]["model_approved_by"] == "reviewer-a"
    assert result["model_version"] == "sitescore_propensity:2026.07.24"
    assert completed["result"]["report_version"] == 2
    report = delivery._reports(TENANT).get_report(result["report_id"])
    from modules.opsboard.application.network_scoring import NetworkScoringService
    assert NetworkScoringService._scorecard_from_report(report)["score"] is None
    assert report.model_version == result["model_version"]
    assert report.feature_snapshot_time is not None and report.prediction_origin_time is not None
    assert report.sitescore_run_id == result["sitescore_run_id"]
    assert delivery._reports(TENANT).get_prediction_run(result["prediction_run_id"]).model_version_id == result["model_version"]
    assert _physical(db, promotion["site_score_job_id"])["status"] == "SUCCEEDED"
    # Current metadata reads preserve saved reports without loading estimators
    # or treating their historical modelVersion as today's approval.
    def snapshot(client):
        response = client.get("/api/v1/operator/network-scoring", headers=_headers())
        assert response.status_code == 200, response.text
        return next(row for row in response.json()["candidates"] if row["id"] == promotion["candidate_site_id"])
    before_reads = (len(actual), len(builds), len(loads))
    without_owner = snapshot(operator_client(bundle, runtime, None))
    assert without_owner["currentAdmission"]["model"]["state"] == "unverified"
    listing = bundle.listing_repository_for_tenant(TENANT).list_candidates()[0].listing
    with db.connect() as connection:
        connection.execute("UPDATE intake.source_registry SET kill_switch=true WHERE source_id=%s", (listing.source_id,))
    assert snapshot(client)["currentAdmission"]["source"]["state"] == "blocked"
    with db.connect() as connection:
        connection.execute("UPDATE intake.source_registry SET kill_switch=false WHERE source_id=%s", (listing.source_id,))
    from datetime import timedelta
    repository = bundle.listing_repository_for_tenant(TENANT)
    stale = repository.list_candidates()[0]
    stale.feature_snapshot_time -= timedelta(days=365)
    repository.save_candidate(stale)
    assert snapshot(client)["currentAdmission"]["pit"]["state"] == "blocked"
    from models.shared_ml.registry import ModelStage
    from modules.learninghub.infrastructure.mlflow_adapter import MlflowRegistryAdapter
    with monkeypatch.context() as fixture_environment:
        fixture_environment.setenv("ODP_REQUIRE_LIVE_DATA", "false")
        fixture_environment.setenv("ODP_PRODUCT_MODE", "poc")
        fixture_environment.setenv("ODAY_ENV", "test")
        registry = MlflowRegistryAdapter(bundle.learninghub_repository, tracking_uri=runtime.tracking_uri, client=runtime.client, runtime_mode="test")
    registry.transition_stage(model_name="sitescore_propensity", version="2026.07.24", stage=ModelStage.RETIRED)
    withdrawn = snapshot(client)
    assert withdrawn["currentAdmission"]["model"]["state"] == "blocked"
    assert withdrawn["modelVersion"] == report.model_version
    assert (len(actual), len(builds), len(loads)) == before_reads
    assert delivery._reports(TENANT).latest(promotion["candidate_site_id"]).report_version == 2


@pytest.mark.parametrize("revoke", [False, True])
def test_original_worker_current_model_guard_uses_actual_same_authority(bridge_database, monkeypatch, tmp_path, revoke):
    from models.shared_ml.registry import ModelStage
    from modules.learninghub.infrastructure.mlflow_adapter import MlflowRegistryAdapter
    from tests.integration.test_production_model_runtime import _registered_governed_runtime
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        _, _, promotion = _approved_score(bridge_database, bundle, _promotion_client(bundle))
        with monkeypatch.context() as fixture_environment:
            fixture_environment.setenv("ODP_REQUIRE_LIVE_DATA", "false")
            fixture_environment.setenv("ODP_PRODUCT_MODE", "poc")
            fixture_environment.setenv("ODAY_ENV", "test")
            runtime, _ = _registered_governed_runtime(tmp_path / "synthetic-initial-model", bundle.learninghub_repository)
            registry = MlflowRegistryAdapter(bundle.learninghub_repository, tracking_uri=runtime.tracking_uri, client=runtime.client, runtime_mode="test")
        original = runtime.infer
        calls = []
        def infer(**kwargs):
            result = original(**kwargs)
            calls.append(result)
            if revoke:
                # The adapter was built as an explicit local fixture; its actual
                # writer key is still the production worker's PostgreSQL key.
                registry.transition_stage(model_name="sitescore_propensity", version="2026.07.24", stage=ModelStage.RETIRED)
            return result
        monkeypatch.setattr(runtime, "infer", infer)
        handle_intake_score(_claim(bundle, promotion["site_score_job_id"]), bundle, model_runtime=runtime)
        row = _physical(bridge_database, promotion["site_score_job_id"])
        assert len(calls) == 1
        assert row["payload"]["model_admission"]["model_id"] == "sitescore_propensity:2026.07.24"
        assert row["status"] == ("FAILED" if revoke else "SUCCEEDED")
        from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
        from shared.infrastructure.persistence.repositories import DurableSiteScoreRepository
        reports = DurableSiteScoreRepository(TenantScopedDocumentStore(bundle.assisted_intake_store._store, TENANT))
        assert (reports.latest(promotion["candidate_site_id"]) is None) is revoke
        if not revoke:
            assert row["result"]["model_lineage"]["model_engine"] == "lightgbm.LGBMRegressor"
    finally:
        bundle.engine.close()


@pytest.mark.parametrize("change", ["source", "hard_rule", "foreign_model"])
def test_current_authority_change_during_infer_has_zero_new_result(successful, change):
    db, bundle, promotion, runtime, principal, authority, delivery = successful
    request = delivery.create_execution(command(delivery, promotion, principal), principal=principal)
    original = runtime.infer
    def infer(**kwargs):
        result = original(**kwargs)
        if change == "source":
            listing = bundle.listing_repository_for_tenant(TENANT).list_candidates()[0].listing
            with db.connect() as connection:
                connection.execute("UPDATE intake.source_registry SET kill_switch=true WHERE source_id=%s", (listing.source_id,))
        elif change == "hard_rule":
            from shared.infrastructure.persistence.operator_network_listings import (
                DurableAssistedIntakeRepository,
            )
            listing_id = bundle.listing_repository_for_tenant(TENANT).list_candidates()[0].candidate_site.listing_id
            delivery._documents(TENANT).store.put(DurableAssistedIntakeRepository._LISTING_META, listing_id, {"hardRuleFailures": None})
        else:
            result = replace(result, artifact_sha256="sha256:" + "f" * 64)
        return result
    runtime.infer = infer
    handle_score_execution(_claim(bundle, request["jobId"]), bundle, model_runtime=runtime, model_admission=authority)
    failed = delivery.get_execution(request["executionRequestId"], principal=principal)
    assert failed["status"] == "FAILED" and failed["result"] is None
    assert delivery._reports(TENANT).latest(promotion["candidate_site_id"]).report_version == 1
    assert _physical(db, promotion["site_score_job_id"])["status"] == "SUCCEEDED"


def test_late_report_append_before_claim_does_not_infer_new_request(successful):
    _, bundle, promotion, runtime, principal, authority, delivery = successful
    request = delivery.create_execution(command(delivery, promotion, principal), principal=principal)
    reports = delivery._reports(TENANT)
    historical = reports.latest(promotion["candidate_site_id"])
    reports.save_report(historical)
    handle_score_execution(_claim(bundle, request["jobId"]), bundle, model_runtime=runtime, model_admission=authority)
    assert len(runtime.calls) == 1
    assert delivery._reports(TENANT).latest(promotion["candidate_site_id"]).report_version == 2
    with bundle.engine.lock:
        document = delivery._documents(TENANT).store.get(ScoreExecutionDocuments.REQUESTS, request["executionRequestId"])
    assert document["status"] == "FAILED" and document["result"] is None


def test_stale_dual_lease_never_invokes_result_callback(successful):
    from shared.jobs.queue import JobFenceRejectedError
    db, bundle, promotion, _, principal, _, delivery = successful
    request = delivery.create_execution(command(delivery, promotion, principal), principal=principal)
    claimed = _claim(bundle, request["jobId"])
    attempt = delivery.begin_attempt(claimed)
    with db.connect() as connection:
        connection.execute("UPDATE workflow.jobs SET lease_expires_at=now()-interval '1 minute' WHERE job_id=%s", (request["jobId"],))
    called = []
    with pytest.raises(JobFenceRejectedError):
        delivery.commit_success(claimed, attempt, lambda: called.append(True))
    assert called == []
    assert delivery._reports(TENANT).latest(promotion["candidate_site_id"]).report_version == 1


def test_canonical_handler_registry_and_main_route_new_type_through_dual_owner():
    from apps.worker.oday_worker.handlers import build_default_registry
    from modules.sitescore.application.score_delivery import SITESCORE_EXECUTION_JOB_TYPE
    registry = build_default_registry()
    assert registry.get(SITESCORE_EXECUTION_JOB_TYPE) is handle_score_execution


@pytest.mark.parametrize("settle", [False, True])
def test_main_new_job_uses_dual_heartbeat_and_only_owner_settlement(successful, monkeypatch, settle):
    import time
    from threading import Event, Thread

    from apps.worker.oday_worker.main import ODayWorker
    from modules.sitescore.application.score_delivery import SITESCORE_EXECUTION_JOB_TYPE
    from shared.jobs.registry import JobRegistry
    db, bundle, promotion, runtime, principal, authority, delivery = successful
    request = delivery.create_execution(command(delivery, promotion, principal), principal=principal)
    claimed = _claim(bundle, request["jobId"])
    supplied = []
    def claim(**kwargs):
        if supplied:
            return None
        supplied.append(True)
        return claimed
    monkeypatch.setattr(bundle.job_queue, "claim_next", claim)
    registry = JobRegistry()
    entered, release = Event(), Event()
    original = runtime.infer
    def infer(**kwargs):
        entered.set()
        assert release.wait(timeout=15)
        return original(**kwargs)
    runtime.infer = infer
    def handler(job, persistence):
        if settle:
            handle_score_execution(job, persistence, model_runtime=runtime, model_admission=authority)
    registry.register(SITESCORE_EXECUTION_JOB_TYPE, handler)
    worker = ODayWorker(persistence=bundle, registry=registry, worker_id=claimed.locked_by, heartbeat_interval_seconds=0.03)
    results = []
    thread = Thread(target=lambda: results.append(worker.run_once()))
    thread.start()
    if settle:
        assert entered.wait(timeout=15)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            current = _physical(db, request["jobId"])
            if current["payload"].get("heartbeat_count", 0) > 0:
                break
            time.sleep(0.02)
        assert current["payload"]["heartbeat_count"] > 0
        release.set()
    thread.join(timeout=20)
    assert not thread.is_alive() and results == [True]
    canonical = _physical(db, request["jobId"])
    if settle:
        assert canonical["status"] == "SUCCEEDED" and bundle.job_queue.get(request["jobId"]).status == JobStatus.SUCCEEDED
    else:
        assert canonical["status"] == "QUEUED" and canonical["result"] is None
        assert bundle.job_queue.get(request["jobId"]).status == JobStatus.RUNNING


@pytest.mark.parametrize("tamper", ["prediction_status", "feature_time", "prediction_origin", "summary"])
def test_predecessor_trace_mismatch_cannot_authorize_fresh_request(successful, tamper):
    from datetime import timedelta
    _, _, promotion, runtime, principal, _, delivery = successful
    reports = delivery._reports(TENANT)
    report = reports.latest(promotion["candidate_site_id"])
    run = reports.get_sitescore_run(report.sitescore_run_id)
    prediction = reports.get_prediction_run(run.prediction_run_id)
    if tamper == "prediction_status":
        prediction = replace(prediction, run_status="failed")
    elif tamper == "feature_time":
        prediction = replace(prediction, feature_snapshot_time=prediction.feature_snapshot_time + timedelta(seconds=1))
    elif tamper == "prediction_origin":
        prediction = replace(prediction, prediction_origin_time=prediction.prediction_origin_time + timedelta(seconds=1))
    else:
        run = replace(run, m12_p50=run.m12_p50 + 1)
    reports.save_prediction_run(prediction)
    reports.save_sitescore_run(run)
    with pytest.raises(ScoreDeliveryDenied, match="TRACE_UNVERIFIED"):
        command(delivery, promotion, principal)
    assert runtime.calls and len(runtime.calls) == 1
    assert delivery._documents(TENANT).store.list_all(ScoreExecutionDocuments.REQUESTS) == []


def test_actual_operator_factory_202_queued_job_worker_and_exact_receipt_get(successful):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from apps.api.app.routes.operator import create_operator_router
    from modules.opsboard.application.operator_live_repository import OperatorLiveRepository
    from modules.sitescore.application.score_execution import SiteScoreExecutionService
    from tests.integration.test_operator_intake_workflow_postgresql import _headers
    _, bundle, promotion, runtime, principal, authority, delivery = successful
    app = FastAPI()
    app.state.job_queue = bundle.job_queue
    app.include_router(create_operator_router(audit_log=bundle.audit_log, document_store=bundle.assisted_intake_store._store,
        job_queue=bundle.job_queue, listing_repository_for_tenant=bundle.listing_repository_for_tenant,
        sitescore_repository_for_tenant=lambda tenant: bundle._scoped_repository("sitescore_repository", tenant),
        sitescore_decision_repository_for_tenant=bundle.sitescore_decision_store_for_tenant,
        avm_repository_for_tenant=lambda tenant: bundle._scoped_repository("avm_repository", tenant),
        netplan_repository_for_tenant=lambda tenant: bundle._scoped_repository("netplan_repository", tenant),
        priceops_repository_for_tenant=lambda tenant: bundle._scoped_repository("priceops_repository", tenant),
        model_runtime=runtime, score_execution_service=SiteScoreExecutionService(delivery),
        intake_workflow_store=bundle.assisted_intake_store, intake_workflow_policy=bundle.assisted_intake_workflow_policy,
        live_repository=OperatorLiveRepository(bundle), require_live_data=True, persistence_mode="postgresql", provider_mode="live"), prefix="/api/v1")
    client = TestClient(app)
    headers = {**_headers(), "X-Correlation-Id": str(uuid4()), "Idempotency-Key": str(uuid4())}
    path = f"/api/v1/operator/network-scoring/candidates/{promotion['candidate_site_id']}"
    current = client.get(path + "/execution-context", headers=headers)
    assert current.status_code == 200, current.text
    context = current.json()
    response = client.post(path + "/score", headers=headers, json={"actorRoleId": "platformAdmin",
        "expectedReportId": context["reportId"], "expectedReportVersion": context["reportVersion"],
        "expectedJobVersion": context["jobVersion"], "reason": "Synthetic API exact context"})
    assert response.status_code == 202, response.text
    request = response.json()["executionRequest"]
    assert request["status"] == "QUEUED" and request["result"] is None and len(runtime.calls) == 1
    handle_score_execution(_claim(bundle, request["jobId"]), bundle, model_runtime=runtime, model_admission=authority)
    read = client.get(f"/api/v1/operator/network-scoring/execution-requests/{request['executionRequestId']}", headers=headers)
    assert read.status_code == 200 and read.json()["status"] == "SUCCEEDED"
    assert read.json()["result"]["report_version"] == 2
    replay = client.post(path + "/score", headers=headers, json={"expectedReportId": context["reportId"],
        "expectedReportVersion": context["reportVersion"], "expectedJobVersion": context["jobVersion"], "reason": "Synthetic API exact context"})
    assert replay.status_code == 202 and replay.json()["executionRequest"]["created"] is False
    assert len(runtime.calls) == 2


@pytest.mark.parametrize("after_infer", [False, True])
def test_cancelled_execution_retains_budget_and_authorized_retry_owns_same_job(successful, after_infer):
    db, bundle, promotion, runtime, principal, authority, delivery = successful
    request = delivery.create_execution(command(delivery, promotion, principal), principal=principal)
    original_job = _physical(db, promotion["site_score_job_id"])
    before = _physical(db, request["jobId"])
    original_infer = runtime.infer
    def request_cancel():
        # Explicit trusted SQL cancellation control, never a user payload flag.
        with db.connect() as connection:
            connection.execute("UPDATE workflow.jobs SET cancellation_requested_at=now() WHERE job_id=%s", (request["jobId"],))
    if after_infer:
        def cancel(**kwargs):
            result = original_infer(**kwargs)
            request_cancel()
            return result
        runtime.infer = cancel
    else:
        request_cancel()
    handle_score_execution(_claim(bundle, request["jobId"]), bundle, model_runtime=runtime, model_admission=authority)
    cancelled = delivery.get_execution(request["executionRequestId"], principal=principal)
    assert cancelled["status"] == "CANCELLED" and cancelled["result"] is None
    assert bundle.job_queue.get(request["jobId"]).status == JobStatus.CANCELLED
    assert delivery._reports(TENANT).latest(promotion["candidate_site_id"]).report_version == 1
    assert len(runtime.calls) == (2 if after_infer else 1)
    replay = delivery.get_execution(request["executionRequestId"], principal=principal)
    assert replay == cancelled
    with pytest.raises(ScoreDeliveryConflict, match="ACTIVE_OR_FAILED"):
        delivery.create_execution(command(delivery, promotion, principal), principal=principal)
    consumed = _physical(db, request["jobId"])["attempt"]
    assert consumed == 1 and consumed < before["max_attempts"]
    retried = delivery.retry_execution(request["executionRequestId"], principal=principal,
        expected_version=cancelled["actualVersion"], expected_job_version=cancelled["jobVersion"],
        reason="Synthetic authorized cancellation retry", idempotency_key=str(uuid4()), correlation_id=str(uuid4()))
    row = _physical(db, request["jobId"])
    assert retried["jobId"] == request["jobId"] and row["attempt"] == consumed
    assert row["max_attempts"] == before["max_attempts"] and row["payload"]["generation"] == before["payload"]["generation"] + 1
    assert row["cancellation_requested_at"] is None
    runtime.infer = original_infer
    handle_score_execution(_claim(bundle, request["jobId"]), bundle, model_runtime=runtime, model_admission=authority)
    assert delivery.get_execution(request["executionRequestId"], principal=principal)["result"]["report_version"] == 2
    assert _physical(db, request["jobId"])["attempt"] == consumed + 1
    assert _physical(db, promotion["site_score_job_id"]) == original_job


def test_cancelled_request_with_zero_remaining_budget_cannot_retry(successful):
    db, bundle, promotion, runtime, principal, authority, delivery = successful
    request = delivery.create_execution(command(delivery, promotion, principal), principal=principal)
    with db.connect() as connection:
        connection.execute("UPDATE workflow.jobs SET cancellation_requested_at=now(),attempt=max_attempts WHERE job_id=%s", (request["jobId"],))
    handle_score_execution(_claim(bundle, request["jobId"]), bundle, model_runtime=runtime, model_admission=authority)
    cancelled = delivery.get_execution(request["executionRequestId"], principal=principal)
    with pytest.raises(ScoreDeliveryConflict, match="VERSION_OR_STATE"):
        delivery.retry_execution(request["executionRequestId"], principal=principal,
            expected_version=cancelled["actualVersion"], expected_job_version=cancelled["jobVersion"], reason="Synthetic no budget",
            idempotency_key=str(uuid4()), correlation_id=str(uuid4()))
    assert len(runtime.calls) == 1 and cancelled["result"] is None


def test_generic_job_api_blocks_new_namespace_and_masks_scoped_receipts(successful, monkeypatch):
    from fastapi.testclient import TestClient

    # Importing this module constructs its default app. Bootstrap only that
    # unrelated in-memory test app before restoring the actual PG live fixture.
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    with monkeypatch.context() as import_environment:
        import_environment.setenv("ODP_REQUIRE_LIVE_DATA", "false")
        import_environment.setenv("ODP_PRODUCT_MODE", "poc")
        from apps.api.oday_api.main import create_app
    from tests.integration.test_operator_intake_workflow_postgresql import _headers
    db, bundle, promotion, runtime, principal, _, delivery = successful
    request = delivery.create_execution(command(delivery, promotion, principal), principal=principal)
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    client = TestClient(create_app(persistence=bundle))
    for prefix in ("/jobs", "/api/v1/jobs"):
        created = client.post(prefix, json={"job_type": "sitescore-execution", "payload": {}})
        assert created.status_code == 400, created.text
        assert client.get(f"{prefix}/{request['jobId']}").status_code == 401
        for headers, expected in (({**_headers(), "x-assigned-area-ids": "foreign-area"}, 403),
                                  ({**_headers(), "x-heat-zone-ids": "foreign-zone"}, 403),
                                  (_headers(tenant=str(uuid4())), 404)):
            assert client.get(f"{prefix}/{request['jobId']}", headers=headers).status_code == expected
        allowed = client.get(f"{prefix}/{request['jobId']}", headers=_headers())
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["executionRequestId"] == request["executionRequestId"]
        assert allowed.json()["status"] == "QUEUED" and allowed.json()["result"] is None
        assert "attempt_token" not in allowed.text and "payload" not in allowed.json()
        denied_retry = client.post(f"{prefix}/{request['jobId']}/retries", headers=_headers())
        assert denied_retry.status_code == 400, denied_retry.text
    with db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM workflow.jobs WHERE job_type='sitescore-execution'").fetchone()[0] == 1
    assert len(runtime.calls) == 1
