from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.learninghub import create_learninghub_router
from modules.learninghub import (
    InMemoryLearningHubRepository,
    LearningHubError,
    LearningHubService,
    MlflowRegistryAdapter,
    ReleaseType,
)
from shared.audit import InMemoryAuditLog
from shared.auth import Role
from shared.infrastructure.persistence import (
    DurableLearningHubRepository,
    SqliteDocumentStore,
    SqliteEngine,
)
from tests.integration._authz import auth_headers
from tests.integration._learninghub_fixtures import (
    dataset_rows as _rows,
)
from tests.integration._learninghub_fixtures import (
    prepare_candidate as _prepare_candidate,
)


def test_dq_actions_persist_actor_time_and_rationale() -> None:
    repository = InMemoryLearningHubRepository()
    audit_log = InMemoryAuditLog()
    service = LearningHubService(
        repository=repository,
        registry=MlflowRegistryAdapter(repository),
        audit_log=audit_log,
    )
    snapshot = service.register_dataset_snapshot(_rows(), dataset_snapshot_id="ds_triage_test_001")

    # Record DQ triage action with actor, time, and rationale
    triage = service.record_dq_triage(
        dataset_snapshot_id=snapshot.dataset_snapshot_id,
        action="override",
        rationale="manual DQ review passed after inspecting missing timestamp cause",
        actor="operator-alice",
    )

    assert triage.triage_id.startswith("dq_triage_")
    assert triage.dataset_snapshot_id == "ds_triage_test_001"
    assert triage.action == "override"
    assert triage.actor == "operator-alice"
    assert triage.rationale == "manual DQ review passed after inspecting missing timestamp cause"
    assert triage.time is not None
    assert triage.audit_event_id is not None

    # Check persistence in repository
    persisted = repository.list_dq_triages(snapshot.dataset_snapshot_id)
    assert len(persisted) == 1
    assert persisted[0].triage_id == triage.triage_id
    assert persisted[0].actor == "operator-alice"
    assert persisted[0].rationale == "manual DQ review passed after inspecting missing timestamp cause"
    assert persisted[0].audit_event_id == triage.audit_event_id

    # Check audit log event
    events = audit_log.list_events()
    dq_events = [e for e in events if e.event_type == "learninghub.dq_triage_recorded.v1"]
    assert len(dq_events) == 1
    assert dq_events[0].actor == "operator-alice"
    assert dq_events[0].metadata["action"] == "override"
    assert dq_events[0].metadata["rationale"] == "manual DQ review passed after inspecting missing timestamp cause"


def test_dq_triage_durable_restart_persistence(tmp_path) -> None:
    """Verify that DQ triage records written to DurableLearningHubRepository survive process restart."""
    db_path = tmp_path / "learninghub_durable.sqlite3"
    engine = SqliteEngine(db_path)
    repository = DurableLearningHubRepository(SqliteDocumentStore(engine))
    audit_log = InMemoryAuditLog()
    service = LearningHubService(
        repository=repository,
        registry=MlflowRegistryAdapter(repository),
        audit_log=audit_log,
    )
    snapshot = service.register_dataset_snapshot(_rows(), dataset_snapshot_id="ds_durable_001")

    triage = service.record_dq_triage(
        dataset_snapshot_id=snapshot.dataset_snapshot_id,
        action="quarantine",
        rationale="corrupted row indices detected in batch pipeline",
        actor="operator-bob",
    )
    assert triage.triage_id.startswith("dq_triage_")

    # Close the engine to simulate process exit
    engine.close()

    # Reopen database with a new SqliteEngine and DurableLearningHubRepository instance
    reopened_engine = SqliteEngine(db_path)
    reopened_repository = DurableLearningHubRepository(SqliteDocumentStore(reopened_engine))

    restored_records = reopened_repository.list_dq_triages(snapshot.dataset_snapshot_id)
    assert len(restored_records) == 1
    restored = restored_records[0]
    assert restored.triage_id == triage.triage_id
    assert restored.actor == "operator-bob"
    assert restored.action == "quarantine"
    assert restored.rationale == "corrupted row indices detected in batch pipeline"
    assert restored.audit_event_id == triage.audit_event_id
    reopened_engine.close()


def test_empty_registry_never_fabricates_a_model() -> None:
    repository = InMemoryLearningHubRepository()

    # Listing models when empty returns 0 items, no mock models
    all_versions = repository.list_all_model_versions()
    assert len(all_versions) == 0

    # Querying a specific non-existent model returns empty list
    versions = repository.list_model_versions("non_existent_model")
    assert len(versions) == 0

    # Getting model card or version returns None, never creates fake model
    card = repository.get_model_card("non_existent_model", "1.0.0")
    assert card is None

    version = repository.get_model_version("non_existent_model", "1.0.0")
    assert version is None


def test_unsupported_promotion_fails_closed() -> None:
    repository = InMemoryLearningHubRepository()
    audit_log = InMemoryAuditLog()
    service = LearningHubService(
        repository=repository,
        registry=MlflowRegistryAdapter(repository),
        audit_log=audit_log,
    )
    candidate = _prepare_candidate(service, "1.0.0")

    # 1. Missing required parameters in signature fails closed
    with pytest.raises(TypeError, match="missing .* required keyword-only arguments"):
        service.request_release(
            model_name=candidate.model_name,
            version=candidate.version,
            release_type=ReleaseType.FULL,
            reason="promotion test",
            approval_id="app-1",
            approved_by="approver-b",
            requested_by="operator-a",
        )

    # 2. Self-review: requested_by == approved_by must fail closed
    with pytest.raises(LearningHubError, match="self-review is prohibited"):
        service.request_release(
            model_name=candidate.model_name,
            version=candidate.version,
            release_type=ReleaseType.FULL,
            reason="promotion test",
            approval_id="app-2",
            approved_by="operator-a",
            requested_by="operator-a",
            rollback_target=candidate.version,
            monitoring_window="24h",
            success_criteria=("latency ok",),
            fail_criteria=("error rate > 0",),
            expected_release_revision=0,
            idempotency_key="ik-self-review",
        )

    # 3. Missing rollback target for FULL promotion must fail closed
    with pytest.raises(LearningHubError, match="rollback target"):
        service.request_release(
            model_name=candidate.model_name,
            version=candidate.version,
            release_type=ReleaseType.FULL,
            reason="promotion test",
            approval_id="app-3",
            approved_by="reviewer-a",
            requested_by="operator-a",
            rollback_target=None,
            monitoring_window="24h",
            success_criteria=("latency ok",),
            fail_criteria=("error rate > 0",),
            expected_release_revision=0,
            idempotency_key="ik-no-rollback",
        )


def test_learninghub_api_role_gating_and_triage_rbac() -> None:
    """Verify least-privilege RBAC role gating on dataset-snapshots triage endpoint."""
    repository = InMemoryLearningHubRepository()
    audit_log = InMemoryAuditLog()
    router = create_learninghub_router(repository=repository, audit_log=audit_log)
    app = FastAPI()
    app.include_router(router)

    service = LearningHubService(repository=repository, audit_log=audit_log)
    snapshot = service.register_dataset_snapshot(_rows(), dataset_snapshot_id="ds_rbac_001")

    # 1. Unauthenticated -> 403 / 401
    anon_client = TestClient(app)
    res_anon = anon_client.post(
        f"/learninghub/dataset-snapshots/{snapshot.dataset_snapshot_id}/triage",
        json={"action": "approve", "rationale": "unauthenticated probe"},
    )
    assert res_anon.status_code in (401, 403)

    # 2. Positive role test: DATA_OWNER caller -> 201 Created
    data_owner_client = TestClient(app, headers=auth_headers(Role.DATA_OWNER, subject="data-owner-alice"))
    res_owner = data_owner_client.post(
        f"/learninghub/dataset-snapshots/{snapshot.dataset_snapshot_id}/triage",
        json={"action": "approve", "rationale": "data owner triage review passed"},
    )
    assert res_owner.status_code == 201, f"Expected 201 for DATA_OWNER, got {res_owner.status_code}: {res_owner.text}"
    body = res_owner.json()
    assert body["action"] == "approve"
    assert body["actor"] == "data-owner-alice"
    assert body["rationale"] == "data owner triage review passed"
    assert "audit_event_id" in body

    # 3. Negative role tests: MODEL_OWNER, RELEASE_OWNER, EXPANSION_USER, AUDITOR -> 403 Forbidden
    for unauthorized_role in (Role.MODEL_OWNER, Role.RELEASE_OWNER, Role.EXPANSION_USER, Role.AUDITOR):
        client = TestClient(app, headers=auth_headers(unauthorized_role, subject="unauth-user"))
        res_unauth = client.post(
            f"/learninghub/dataset-snapshots/{snapshot.dataset_snapshot_id}/triage",
            json={"action": "override", "rationale": "unauthorized attempt"},
        )
        assert res_unauth.status_code == 403, f"Role {unauthorized_role} should receive 403, got {res_unauth.status_code}"


def test_dq_triage_api_single_audit_event_provenance_and_correlation() -> None:
    """Verify POST /learninghub/dataset-snapshots/{id}/triage records EXACTLY ONE audit event with request correlation ID."""
    repository = InMemoryLearningHubRepository()
    audit_log = InMemoryAuditLog()
    router = create_learninghub_router(repository=repository, audit_log=audit_log)
    app = FastAPI()

    # Add correlation ID middleware matching FastAPI app conventions
    @app.middleware("http")
    async def add_correlation_id(request, call_next):
        corr_id = request.headers.get("X-Correlation-ID", "req_corr_test_999")
        request.state.correlation_id = corr_id
        return await call_next(request)

    app.include_router(router)

    service = LearningHubService(repository=repository, audit_log=audit_log)
    snapshot = service.register_dataset_snapshot(_rows(), dataset_snapshot_id="ds_single_audit_001")

    client = TestClient(
        app,
        headers={
            **auth_headers(Role.DATA_OWNER, subject="operator-charlie"),
            "X-Correlation-ID": "req_corr_single_audit_123",
        },
    )

    res = client.post(
        f"/learninghub/dataset-snapshots/{snapshot.dataset_snapshot_id}/triage",
        json={"action": "flag", "rationale": "outlier variance in target column"},
    )
    assert res.status_code == 201
    body = res.json()
    returned_audit_id = body["audit_event_id"]

    # Assert EXACTLY ONE learninghub.dq_triage_recorded.v1 audit event was recorded
    events = audit_log.list_events()
    triage_events = [e for e in events if e.event_type == "learninghub.dq_triage_recorded.v1"]
    assert len(triage_events) == 1, f"Expected exactly 1 triage audit event, found {len(triage_events)}"

    audit_event = triage_events[0]
    assert audit_event.event_id == returned_audit_id
    assert audit_event.actor == "operator-charlie"
    assert audit_event.correlation_id == "req_corr_single_audit_123"
    assert audit_event.metadata["action"] == "flag"
    assert audit_event.metadata["rationale"] == "outlier variance in target column"
