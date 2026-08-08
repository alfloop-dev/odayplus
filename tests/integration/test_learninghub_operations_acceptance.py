from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.learninghub import create_learninghub_router
from models.shared_ml import (
    MetricThreshold,
    ModelAlias,
    ModelCardApproval,
    ModelStage,
    ModelVersion,
)
from modules.learninghub import (
    InMemoryLearningHubRepository,
    LearningHubError,
    LearningHubService,
    MlflowRegistryAdapter,
    ReleaseType,
)
from shared.audit import InMemoryAuditLog
from tests.integration._learninghub_fixtures import (
    dataset_rows as _rows,
    model_card as _model_card,
    model_version as _model_version,
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

    # Check persistence in repository
    persisted = repository.list_dq_triages(snapshot.dataset_snapshot_id)
    assert len(persisted) == 1
    assert persisted[0].triage_id == triage.triage_id
    assert persisted[0].actor == "operator-alice"
    assert persisted[0].rationale == "manual DQ review passed after inspecting missing timestamp cause"

    # Check audit log event
    events = audit_log.list_events()
    dq_events = [e for e in events if e.event_type == "learninghub.dq_triage_recorded.v1"]
    assert len(dq_events) == 1
    assert dq_events[0].actor == "operator-alice"
    assert dq_events[0].metadata["action"] == "override"
    assert dq_events[0].metadata["rationale"] == "manual DQ review passed after inspecting missing timestamp cause"


def test_empty_registry_never_fabricates_a_model() -> None:
    repository = InMemoryLearningHubRepository()
    audit_log = InMemoryAuditLog()
    service = LearningHubService(
        repository=repository,
        registry=MlflowRegistryAdapter(repository),
        audit_log=audit_log,
    )

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


def test_learninghub_api_role_gating_and_triage_endpoints() -> None:
    repository = InMemoryLearningHubRepository()
    router = create_learninghub_router(repository=repository)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Unauthenticated / missing operator principal fails closed (403 or 401)
    res = client.get("/learninghub/models")
    assert res.status_code in (401, 403)

    # Route level verification of dependencies / auth guards
    routes_by_path = {route.path: route for route in router.routes}
    assert "/learninghub/models" in routes_by_path
    assert routes_by_path["/learninghub/models"].dependencies
    assert "/learninghub/dataset-snapshots/{dataset_snapshot_id}/triage" in routes_by_path
    assert routes_by_path["/learninghub/dataset-snapshots/{dataset_snapshot_id}/triage"].dependencies
