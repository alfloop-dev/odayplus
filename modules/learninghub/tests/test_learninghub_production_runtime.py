from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from models.shared_ml import (
    ArtifactKind,
    MetricThreshold,
    ModelCard,
    ModelCardApproval,
    ModelRiskLevel,
    ModelVersion,
)
from modules.learninghub import (
    InMemoryLearningHubRepository,
    LearningHubRuntimeConfigurationError,
    LearningHubService,
)
from modules.learninghub.infrastructure.mlflow_adapter import MlflowRegistryAdapter
from shared.audit import InMemoryAuditLog
from shared.infrastructure.persistence import (
    DurableArtifactStore,
    DurableAuditLog,
    DurableLearningHubRepository,
    SqliteDocumentStore,
    SqliteEngine,
)

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
MODEL_NAME = "forecastops"
VERSION = "2026.07.24"


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


def _rows() -> list[dict[str, Any]]:
    return [
        {
            "view_name": "store_machine_timeseries_view",
            "view_version": "store-machine-timeseries-view-v1",
            "entity_id": "store-live-001",
            "feature_snapshot_time": NOW.isoformat(),
            "prediction_origin_time": NOW.isoformat(),
            "source_snapshot_ids": ["pos-live-001"],
            "features": {"event_time": NOW.isoformat(), "revenue_lag_7d": 92_000.0},
            "labels": {"w4_revenue": 410_000.0},
            "label_maturity_time": NOW.isoformat(),
        }
    ]


def _card(snapshot_id: str, validation_run_id: str) -> ModelCard:
    return ModelCard(
        model_name=MODEL_NAME,
        model_version=VERSION,
        owner="ml-platform",
        risk_level=ModelRiskLevel.R3,
        intended_use="Production ForecastOps interval inference",
        not_intended_use="Automated store closure",
        dataset_snapshot_id=snapshot_id,
        validation_run_id=validation_run_id,
        feature_set_id="forecastops-features-v1",
        label_set_id="forecastops-labels-v1",
        training_period="2026-01-01/2026-06-30",
        validation_period="2026-07-01/2026-07-23",
        algorithm="StatsForecast AutoETS",
        baseline="SeasonalNaive",
        metrics_summary={"w4_smape": 0.11},
        segment_metrics=(),
        calibration_summary={"p80_coverage": 0.82},
        explainability_method="forecast-components",
        limitations=("Requires complete POS history",),
        known_biases=("New stores have wider intervals",),
        rollback_conditions=("w4_smape > 0.15",),
        approvals=(
            ModelCardApproval(
                approver="model-review-board",
                role="model-risk-reviewer",
            ),
        ),
    )


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


def test_production_registration_invokes_registry_and_survives_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "learninghub.sqlite3"
    engine, repository, artifacts, audit = _durable(database)
    registry = RecordingRemoteRegistry(repository)
    try:
        service = LearningHubService(
            repository=repository,
            registry=registry,  # type: ignore[arg-type]
            audit_log=audit,  # type: ignore[arg-type]
            artifact_store=artifacts,
            runtime_mode="production",
        )
        snapshot = service.register_dataset_snapshot(
            _rows(),
            dataset_snapshot_id="forecastops-live-training-001",
        )
        validation = service.validate_candidate(
            model_name=MODEL_NAME,
            model_version=VERSION,
            dataset_snapshot_id=snapshot.dataset_snapshot_id,
            metrics={"w4_smape": 0.11},
            baseline_metrics={"w4_smape": 0.16},
            thresholds=(MetricThreshold("w4_smape", max_value=0.12),),
        )
        model = ModelVersion(
            model_name=MODEL_NAME,
            version=VERSION,
            artifact_uri="gs://oday-models/forecastops/2026.07.24/model.zip",
            dataset_snapshot_id=snapshot.dataset_snapshot_id,
            feature_schema_version="store-machine-timeseries-view-v1",
            label_version="forecastops-w4-revenue-v1",
            metrics={"w4_smape": 0.11},
            run_id="mlflow-run-forecastops-001",
            git_sha="abc1234",
            monitoring_config={"artifact_sha256": "sha256:" + ("b" * 64)},
        )
        registered = service.register_model_version(
            model_version=model,
            model_card=_card(snapshot.dataset_snapshot_id, validation.validation_run_id),
            validation_run=validation,
        )
        assert registered == model
        assert registry.validated == [model]
        assert registry.registered == [model]
        card_artifact_id = f"{MODEL_NAME}/{VERSION}/{ArtifactKind.MODEL_CARD.value}"
        assert artifacts.verify(card_artifact_id)
    finally:
        engine.close()

    reopened_engine, reopened_repository, reopened_artifacts, _ = _durable(database)
    try:
        assert reopened_repository.get_model_version(MODEL_NAME, VERSION) == model
        assert reopened_repository.get_model_card(MODEL_NAME, VERSION) is not None
        assert reopened_artifacts.verify(card_artifact_id)
    finally:
        reopened_engine.close()


def test_production_rejects_implicit_or_memory_bindings(tmp_path: Path) -> None:
    with pytest.raises(
        LearningHubRuntimeConfigurationError,
        match="injected durable repository",
    ):
        LearningHubService(runtime_mode="production")
    with pytest.raises(
        LearningHubRuntimeConfigurationError,
        match="injected durable repository",
    ):
        LearningHubService(
            repository=InMemoryLearningHubRepository(),
            runtime_mode="production",
        )

    engine, repository, artifacts, audit = _durable(tmp_path / "learninghub.sqlite3")
    registry = RecordingRemoteRegistry(repository)
    try:
        with pytest.raises(
            LearningHubRuntimeConfigurationError,
            match="durable audit log",
        ):
            LearningHubService(
                repository=repository,
                registry=registry,  # type: ignore[arg-type]
                artifact_store=artifacts,
                runtime_mode="production",
            )
        with pytest.raises(
            LearningHubRuntimeConfigurationError,
            match="durable artifact store",
        ):
            LearningHubService(
                repository=repository,
                registry=registry,  # type: ignore[arg-type]
                audit_log=audit,  # type: ignore[arg-type]
                runtime_mode="production",
            )
        with pytest.raises(
            LearningHubRuntimeConfigurationError,
            match="durable artifact store",
        ):
            LearningHubService(
                repository=repository,
                registry=registry,  # type: ignore[arg-type]
                audit_log=audit,  # type: ignore[arg-type]
                artifact_store=None,
                runtime_mode="production",
            )
        with pytest.raises(
            LearningHubRuntimeConfigurationError,
            match="durable audit log",
        ):
            LearningHubService(
                repository=repository,
                registry=registry,  # type: ignore[arg-type]
                audit_log=InMemoryAuditLog(),
                artifact_store=artifacts,
                runtime_mode="production",
            )
    finally:
        engine.close()


def test_production_mlflow_rejects_local_sqlite_and_accepts_remote_client(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        LearningHubRuntimeConfigurationError,
        match="rejects local file or SQLite",
    ):
        MlflowRegistryAdapter(
            InMemoryLearningHubRepository(),
            tracking_uri=f"sqlite:///{tmp_path / 'mlflow.db'}",
            client=object(),  # type: ignore[arg-type]
            runtime_mode="production",
        )

    adapter = MlflowRegistryAdapter(
        InMemoryLearningHubRepository(),
        tracking_uri="https://mlflow.internal.example",
        client=object(),  # type: ignore[arg-type]
        runtime_mode="production",
    )
    adapter.require_production_binding()
