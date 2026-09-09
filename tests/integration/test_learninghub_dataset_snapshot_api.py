"""HTTP contract tests for governed Learning Hub dataset snapshots."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from models.shared_ml import FeatureDefinition, FeatureSet, LabelSet
from modules.learninghub import InMemoryLearningHubRepository
from shared.auth import Role
from shared.infrastructure.persistence import (
    DurableLearningHubRepository,
    SqliteDocumentStore,
    SqliteEngine,
)
from tests.integration._authz import auth_headers

SNAPSHOT_TIME = datetime(2026, 6, 27, 8, 0, tzinfo=UTC)
PREDICTION_TIME = datetime(2026, 6, 27, 9, 0, tzinfo=UTC)


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


def _client(repository: InMemoryLearningHubRepository) -> TestClient:
    return TestClient(
        create_app(learninghub_repository=repository),
        headers=auth_headers(Role.MODEL_OWNER),
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


def test_http_dataset_snapshot_rejects_missing_data_quality_score() -> None:
    repository = InMemoryLearningHubRepository()
    row = {
        "view_name": "store_machine_timeseries_view",
        "view_version": "v1",
        "entity_id": "store-no-quality",
        "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
        "prediction_origin_time": PREDICTION_TIME.isoformat(),
        "confidence": 0.95,
        "features": {"event_time": SNAPSHOT_TIME.isoformat()},
    }
    response = _client(repository).post(
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


def test_http_dataset_snapshot_rejects_missing_confidence() -> None:
    repository = InMemoryLearningHubRepository()
    row = {
        "view_name": "store_machine_timeseries_view",
        "view_version": "v1",
        "entity_id": "store-no-confidence",
        "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
        "prediction_origin_time": PREDICTION_TIME.isoformat(),
        "data_quality_score": 0.98,
        "features": {"event_time": SNAPSHOT_TIME.isoformat()},
    }
    response = _client(repository).post(
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


def test_http_dataset_snapshot_rejects_explicit_null_quality_fields() -> None:
    repository = InMemoryLearningHubRepository()
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
    response = _client(repository).post(
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


def test_http_dataset_snapshot_rejects_mixed_valid_and_missing_rows_without_partial_write() -> None:
    repository = InMemoryLearningHubRepository()
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
    response = _client(repository).post(
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


def test_http_dataset_snapshot_preserves_explicit_zero_values() -> None:
    repository = InMemoryLearningHubRepository()
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
    response = _client(repository).post(
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


def test_http_dataset_snapshot_durable_rejection_leaves_no_persistence(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "http_learninghub_durable.sqlite3"
    engine = SqliteEngine(db_path)
    store = SqliteDocumentStore(engine)
    repository = DurableLearningHubRepository(store)
    try:
        client = TestClient(
            create_app(learninghub_repository=repository),
            headers=auth_headers(Role.MODEL_OWNER),
        )
        response = client.post(
            "/api/v1/learninghub/dataset-snapshots",
            json={
                "dataset_snapshot_id": "snapshot-durable-rejected",
                "rows": [
                    {
                        "view_name": "store_machine_timeseries_view",
                        "view_version": "v1",
                        "entity_id": "store-durable-no-quality",
                        "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
                        "prediction_origin_time": PREDICTION_TIME.isoformat(),
                        "confidence": 0.90,
                        "features": {"event_time": SNAPSHOT_TIME.isoformat()},
                    }
                ],
            },
        )
        assert response.status_code == 422, response.text
        assert "store-durable-no-quality" in response.json()["detail"]
        assert "data_quality_score" in response.json()["detail"]
        assert repository.get_dataset_snapshot("snapshot-durable-rejected") is None
    finally:
        engine.close()

    # Reopen database to confirm no trace in durable storage
    reopened_engine = SqliteEngine(db_path)
    reopened_store = SqliteDocumentStore(reopened_engine)
    reopened_repository = DurableLearningHubRepository(reopened_store)
    try:
        assert reopened_repository.get_dataset_snapshot("snapshot-durable-rejected") is None
    finally:
        reopened_engine.close()
