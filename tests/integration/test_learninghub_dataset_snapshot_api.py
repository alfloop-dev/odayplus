"""HTTP contract tests for governed Learning Hub dataset snapshots."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from apps.api.app.routes.learninghub import create_learninghub_router
from apps.api.oday_api.main import create_app
from models.shared_ml import (
    FeatureDefinition,
    FeatureSet,
    LabelSet,
    ModelVersion,
)
from modules.learninghub import InMemoryLearningHubRepository
from shared.auth import Role
from shared.infrastructure.persistence import (
    DurableArtifactStore,
    DurableAuditLog,
    DurableLearningHubRepository,
    SqliteDocumentStore,
    SqliteEngine,
)
from tests.integration._authz import auth_headers

SNAPSHOT_TIME = datetime(2026, 6, 27, 8, 0, tzinfo=UTC)
PREDICTION_TIME = datetime(2026, 6, 27, 9, 0, tzinfo=UTC)


class RecordingRemoteRegistry:
    tracking_uri = "https://mlflow.internal.example"

    def __init__(self, repository: DurableLearningHubRepository) -> None:
        self.repository = repository
        self.validated: list[ModelVersion] = []
        self.registered: list[ModelVersion] = []

    def require_production_binding(self) -> None:
        return None

    def validate_production_model_version(self, model_version: ModelVersion) -> None:
        self.validated.append(model_version)
        assert model_version.artifact_uri.startswith("gs://")
        assert model_version.monitoring_config["artifact_sha256"].startswith("sha256:")

    def register_model_version(self, model_version: ModelVersion) -> ModelVersion:
        self.registered.append(model_version)
        return self.repository.save_model_version(model_version)


def _durable(
    path: Path,
) -> tuple[
    SqliteEngine,
    DurableLearningHubRepository,
    DurableArtifactStore,
    DurableAuditLog,
]:
    engine = SqliteEngine(path)
    store = SqliteDocumentStore(engine)
    return (
        engine,
        DurableLearningHubRepository(store),
        DurableArtifactStore(store),
        DurableAuditLog(engine),
    )


def _client(repository: InMemoryLearningHubRepository) -> TestClient:
    return TestClient(
        create_app(learninghub_repository=repository),
        headers=auth_headers(Role.MODEL_OWNER),
    )


def _production_http_environment(
    path: Path,
) -> tuple[
    SqliteEngine,
    DurableLearningHubRepository,
    DurableArtifactStore,
    DurableAuditLog,
    RecordingRemoteRegistry,
    TestClient,
]:
    engine, repository, artifacts, audit = _durable(path)
    registry = RecordingRemoteRegistry(repository)
    router = create_learninghub_router(
        repository=repository,
        artifact_store=artifacts,
        audit_log=audit,
        registry=registry,  # type: ignore[arg-type]
        runtime_mode="production",
    )
    app = FastAPI()

    @app.middleware("http")
    async def _correlation_id_middleware(request: Request, call_next: Any) -> Response:
        request.state.correlation_id = request.headers.get(
            "x-correlation-id", "corr-learninghub-test"
        )
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")
    client = TestClient(
        app,
        headers=auth_headers(Role.MODEL_OWNER),
    )
    return engine, repository, artifacts, audit, registry, client


def _rows() -> list[dict[str, object]]:
    return [
        {
            "view_name": "store_machine_timeseries_view",
            "view_version": "v1",
            "entity_id": "store-001",
            "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
            "prediction_origin_time": PREDICTION_TIME.isoformat(),
            "data_quality_score": 0.98,
            "confidence": 0.95,
            "features": {
                "event_time": SNAPSHOT_TIME.isoformat(),
                "governed_feature": 1,
            },
        }
    ]


def _feature(*, status: str) -> FeatureDefinition:
    return FeatureDefinition(
        feature_id="feature-governed",
        feature_name="governed_feature",
        version="1.0.0",
        status=status,
        owner="data-team",
        domain="TEST",
        entity_type="STORE",
        entity_key=("store_id",),
        grain="store",
        value_type="INTEGER",
        unit="count",
        semantic_type="STATIC",
        source_table="test_features",
        source_view="test_feature_view",
        source_system="test",
        calculation_sql_uri="s3://sql/governed-feature.sql",
        feature_available_time_rule="immediate",
        refresh_frequency="DAILY",
    )


def _feature_set() -> FeatureSet:
    return FeatureSet(
        feature_set_id="fs_governed",
        model_name="test-model",
        version="1.0.0",
        features=("governed_feature@1.0.0",),
        point_in_time_policy_id="pit-v1",
    )


def test_http_dataset_snapshot_reaches_blocked_feature_gate() -> None:
    repository = InMemoryLearningHubRepository()
    repository.save_feature(_feature(status="BLOCKED"))
    repository.save_feature_set(_feature_set())

    response = _client(repository).post(
        "/api/v1/learninghub/dataset-snapshots",
        json={
            "dataset_snapshot_id": "snapshot-blocked",
            "rows": _rows(),
            "feature_set_id": "fs_governed",
        },
    )

    assert response.status_code == 422, response.text
    assert "governed_feature is BLOCKED and cannot be used" in response.json()["detail"]
    assert repository.get_dataset_snapshot("snapshot-blocked") is None


def test_http_dataset_snapshot_returns_registry_bindings() -> None:
    repository = InMemoryLearningHubRepository()
    repository.save_feature(_feature(status="ACTIVE"))
    repository.save_feature_set(_feature_set())
    repository.save_label_set(
        LabelSet(
            label_set_id="ls_governed",
            labels=(),
            maturity_policy="maturity-v1",
        )
    )

    response = _client(repository).post(
        "/api/v1/learninghub/dataset-snapshots",
        json={
            "dataset_snapshot_id": "snapshot-bound",
            "rows": _rows(),
            "feature_set_id": "fs_governed",
            "label_set_id": "ls_governed",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["feature_set_id"] == "fs_governed"
    assert response.json()["label_set_id"] == "ls_governed"


def test_http_dataset_snapshot_rejects_missing_data_quality_score(tmp_path: Path) -> None:
    db_path = tmp_path / "http_no_quality.sqlite3"
    engine, repository, _, _, _, client = _production_http_environment(db_path)
    row = {
        "view_name": "store_machine_timeseries_view",
        "view_version": "v1",
        "entity_id": "store-no-quality",
        "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
        "prediction_origin_time": PREDICTION_TIME.isoformat(),
        "confidence": 0.95,
        "features": {"event_time": SNAPSHOT_TIME.isoformat()},
    }
    try:
        response = client.post(
            "/api/v1/learninghub/dataset-snapshots",
            json={
                "dataset_snapshot_id": "snapshot-no-quality",
                "rows": [row],
            },
        )
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert "store-no-quality" in detail
        assert "data_quality_score" in detail
        assert repository.get_dataset_snapshot("snapshot-no-quality") is None
    finally:
        engine.close()

    reopened_engine, reopened_repository, _, _ = _durable(db_path)
    try:
        assert reopened_repository.get_dataset_snapshot("snapshot-no-quality") is None
    finally:
        reopened_engine.close()


def test_http_dataset_snapshot_rejects_missing_confidence(tmp_path: Path) -> None:
    db_path = tmp_path / "http_no_confidence.sqlite3"
    engine, repository, _, _, _, client = _production_http_environment(db_path)
    row = {
        "view_name": "store_machine_timeseries_view",
        "view_version": "v1",
        "entity_id": "store-no-confidence",
        "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
        "prediction_origin_time": PREDICTION_TIME.isoformat(),
        "data_quality_score": 0.98,
        "features": {"event_time": SNAPSHOT_TIME.isoformat()},
    }
    try:
        response = client.post(
            "/api/v1/learninghub/dataset-snapshots",
            json={
                "dataset_snapshot_id": "snapshot-no-confidence",
                "rows": [row],
            },
        )
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert "store-no-confidence" in detail
        assert "confidence" in detail
        assert repository.get_dataset_snapshot("snapshot-no-confidence") is None
    finally:
        engine.close()

    reopened_engine, reopened_repository, _, _ = _durable(db_path)
    try:
        assert reopened_repository.get_dataset_snapshot("snapshot-no-confidence") is None
    finally:
        reopened_engine.close()


def test_http_dataset_snapshot_rejects_explicit_null_quality_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "http_explicit_null.sqlite3"
    engine, repository, _, _, _, client = _production_http_environment(db_path)
    row = {
        "view_name": "store_machine_timeseries_view",
        "view_version": "v1",
        "entity_id": "store-explicit-null",
        "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
        "prediction_origin_time": PREDICTION_TIME.isoformat(),
        "data_quality_score": None,
        "confidence": None,
        "features": {"event_time": SNAPSHOT_TIME.isoformat()},
    }
    try:
        response = client.post(
            "/api/v1/learninghub/dataset-snapshots",
            json={
                "dataset_snapshot_id": "snapshot-explicit-null",
                "rows": [row],
            },
        )
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert "store-explicit-null" in detail
        assert "data_quality_score" in detail
        assert "confidence" in detail
        assert repository.get_dataset_snapshot("snapshot-explicit-null") is None
    finally:
        engine.close()

    reopened_engine, reopened_repository, _, _ = _durable(db_path)
    try:
        assert reopened_repository.get_dataset_snapshot("snapshot-explicit-null") is None
    finally:
        reopened_engine.close()


def test_http_dataset_snapshot_rejects_mixed_valid_and_missing_rows_without_partial_write(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "http_mixed_rejection.sqlite3"
    engine, repository, _, _, _, client = _production_http_environment(db_path)
    rows = [
        {
            "view_name": "store_machine_timeseries_view",
            "view_version": "v1",
            "entity_id": "store-row-valid",
            "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
            "prediction_origin_time": PREDICTION_TIME.isoformat(),
            "data_quality_score": 0.98,
            "confidence": 0.95,
            "features": {"event_time": SNAPSHOT_TIME.isoformat()},
        },
        {
            "view_name": "store_machine_timeseries_view",
            "view_version": "v1",
            "entity_id": "store-row-no-quality",
            "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
            "prediction_origin_time": PREDICTION_TIME.isoformat(),
            "confidence": 0.90,
            "features": {"event_time": SNAPSHOT_TIME.isoformat()},
        },
        {
            "view_name": "store_machine_timeseries_view",
            "view_version": "v1",
            "entity_id": "store-row-no-confidence",
            "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
            "prediction_origin_time": PREDICTION_TIME.isoformat(),
            "data_quality_score": 0.92,
            "features": {"event_time": SNAPSHOT_TIME.isoformat()},
        },
    ]
    try:
        response = client.post(
            "/api/v1/learninghub/dataset-snapshots",
            json={
                "dataset_snapshot_id": "snapshot-mixed",
                "rows": rows,
            },
        )
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert "store-row-no-quality" in detail
        assert "data_quality_score" in detail
        assert "store-row-no-confidence" in detail
        assert "confidence" in detail
        assert repository.get_dataset_snapshot("snapshot-mixed") is None
    finally:
        engine.close()

    reopened_engine, reopened_repository, _, _ = _durable(db_path)
    try:
        assert reopened_repository.get_dataset_snapshot("snapshot-mixed") is None
    finally:
        reopened_engine.close()


def test_http_dataset_snapshot_preserves_explicit_zero_and_persists_durable_receipt(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "http_zero_quality.sqlite3"
    engine, repository, _, _, _, client = _production_http_environment(db_path)
    rows = [
        {
            "view_name": "store_machine_timeseries_view",
            "view_version": "v1",
            "entity_id": "store-zero-val",
            "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
            "prediction_origin_time": PREDICTION_TIME.isoformat(),
            "data_quality_score": 0.0,
            "confidence": 0.0,
            "features": {"event_time": SNAPSHOT_TIME.isoformat()},
        },
        {
            "view_name": "store_machine_timeseries_view",
            "view_version": "v1",
            "entity_id": "store-full-val",
            "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
            "prediction_origin_time": PREDICTION_TIME.isoformat(),
            "data_quality_score": 1.0,
            "confidence": 0.95,
            "features": {"event_time": SNAPSHOT_TIME.isoformat()},
        },
    ]
    try:
        response = client.post(
            "/api/v1/learninghub/dataset-snapshots",
            json={
                "dataset_snapshot_id": "snapshot-with-zeros",
                "rows": rows,
            },
        )
        assert response.status_code == 201, response.text
        saved = repository.get_dataset_snapshot("snapshot-with-zeros")
        assert saved is not None
        assert len(saved.records) == 2
        assert saved.records[0].data_quality_score == 0.0
        assert saved.records[0].confidence == 0.0
        assert saved.records[1].data_quality_score == 1.0
        assert saved.records[1].confidence == 0.95
    finally:
        engine.close()

    reopened_engine, reopened_repository, _, _ = _durable(db_path)
    try:
        reopened_saved = reopened_repository.get_dataset_snapshot("snapshot-with-zeros")
        assert reopened_saved is not None
        assert len(reopened_saved.records) == 2
        assert reopened_saved.records[0].data_quality_score == 0.0
        assert reopened_saved.records[0].confidence == 0.0
        assert reopened_saved.records[1].data_quality_score == 1.0
        assert reopened_saved.records[1].confidence == 0.95
    finally:
        reopened_engine.close()
