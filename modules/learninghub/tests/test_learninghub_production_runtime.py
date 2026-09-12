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
    ModelReleaseSaga,
    ReleaseSagaState,
)
from modules.learninghub.domain import DatasetQualityAdmissionError
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
            "data_quality_score": 0.98,
            "confidence": 0.95,
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


def test_production_startup_recovers_orphaned_release_intent(tmp_path: Path) -> None:
    database = tmp_path / "learninghub-startup-recovery.sqlite3"
    engine, repository, _, _ = _durable(database)
    release_id = "release-startup-recovery-001"
    try:
        with repository.release_guard(MODEL_NAME, expected_revision=0) as revision:
            repository.save_release_saga(
                ModelReleaseSaga(
                    release_id=release_id,
                    model_name=MODEL_NAME,
                    idempotency_key=release_id,
                    request_fingerprint="sha256:" + ("a" * 64),
                    release_revision=revision,
                    operation="MODEL_RELEASE",
                    command={
                        "model_name": MODEL_NAME,
                        "version": VERSION,
                        "requested_by": "interrupted-release-worker",
                        "correlation_id": release_id,
                    },
                    version_snapshots=(),
                    alias_snapshots=(),
                )
            )
    finally:
        engine.close()

    reopened, restarted_repository, artifacts, audit = _durable(database)
    try:
        LearningHubService(
            repository=restarted_repository,
            registry=RecordingRemoteRegistry(restarted_repository),  # type: ignore[arg-type]
            audit_log=audit,  # type: ignore[arg-type]
            artifact_store=artifacts,
            runtime_mode="production",
        )
        saga = restarted_repository.get_release_saga(release_id)
        assert saga is not None
        assert saga.state is ReleaseSagaState.COMPENSATED
        events = audit.list_events(correlation_id=release_id)
        assert events[-1].event_type == "learninghub.model_release_compensation.v1"
        assert events[-1].outcome == "compensated"
    finally:
        reopened.close()


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


def test_production_dataset_snapshot_rejects_missing_or_null_quality_fields_and_leaves_no_durable_traces(
    tmp_path: Path,
) -> None:
    database = tmp_path / "learninghub_production_rejection.sqlite3"
    engine, repository, artifacts, audit = _durable(database)
    registry = RecordingRemoteRegistry(repository)
    service = LearningHubService(
        repository=repository,
        registry=registry,  # type: ignore[arg-type]
        audit_log=audit,  # type: ignore[arg-type]
        artifact_store=artifacts,
        runtime_mode="production",
    )

    base_row = {
        "view_name": "store_machine_timeseries_view",
        "view_version": "store-machine-timeseries-view-v1",
        "feature_snapshot_time": NOW.isoformat(),
        "prediction_origin_time": NOW.isoformat(),
        "source_snapshot_ids": ["pos-live-001"],
        "features": {"event_time": NOW.isoformat()},
        "labels": {"w4_revenue": 410_000.0},
    }

    try:
        # Case A: Missing data_quality_score
        with pytest.raises(DatasetQualityAdmissionError) as exc_a:
            service.register_dataset_snapshot(
                [
                    {
                        **base_row,
                        "entity_id": "store-missing-quality",
                        "confidence": 0.95,
                    }
                ],
                dataset_snapshot_id="snapshot-missing-quality",
            )
        assert "store-missing-quality" in str(exc_a.value)
        assert "data_quality_score" in str(exc_a.value)
        assert repository.get_dataset_snapshot("snapshot-missing-quality") is None

        # Case B: Missing confidence
        with pytest.raises(DatasetQualityAdmissionError) as exc_b:
            service.register_dataset_snapshot(
                [
                    {
                        **base_row,
                        "entity_id": "store-missing-confidence",
                        "data_quality_score": 0.98,
                    }
                ],
                dataset_snapshot_id="snapshot-missing-confidence",
            )
        assert "store-missing-confidence" in str(exc_b.value)
        assert "confidence" in str(exc_b.value)
        assert repository.get_dataset_snapshot("snapshot-missing-confidence") is None

        # Case C: Explicit None / null values
        with pytest.raises(DatasetQualityAdmissionError) as exc_c:
            service.register_dataset_snapshot(
                [
                    {
                        **base_row,
                        "entity_id": "store-explicit-none",
                        "data_quality_score": None,
                        "confidence": None,
                    }
                ],
                dataset_snapshot_id="snapshot-explicit-none",
            )
        assert "store-explicit-none" in str(exc_c.value)
        assert "data_quality_score" in str(exc_c.value)
        assert "confidence" in str(exc_c.value)
        assert repository.get_dataset_snapshot("snapshot-explicit-none") is None

        # Case D: Mixed valid and invalid rows
        with pytest.raises(DatasetQualityAdmissionError) as exc_d:
            service.register_dataset_snapshot(
                [
                    {
                        **base_row,
                        "entity_id": "store-valid",
                        "data_quality_score": 0.98,
                        "confidence": 0.95,
                    },
                    {
                        **base_row,
                        "entity_id": "store-mixed-no-quality",
                        "confidence": 0.90,
                    },
                    {
                        **base_row,
                        "entity_id": "store-mixed-no-confidence",
                        "data_quality_score": 0.92,
                    },
                ],
                dataset_snapshot_id="snapshot-mixed-failure",
            )
        assert "store-mixed-no-quality" in str(exc_d.value)
        assert "data_quality_score" in str(exc_d.value)
        assert "store-mixed-no-confidence" in str(exc_d.value)
        assert "confidence" in str(exc_d.value)
        assert repository.get_dataset_snapshot("snapshot-mixed-failure") is None
    finally:
        engine.close()

    # Verify no partial or residual writes exist in durable repository after restart
    reopened_engine, reopened_repository, _, _ = _durable(database)
    try:
        assert reopened_repository.get_dataset_snapshot("snapshot-missing-quality") is None
        assert reopened_repository.get_dataset_snapshot("snapshot-missing-confidence") is None
        assert reopened_repository.get_dataset_snapshot("snapshot-explicit-none") is None
        assert reopened_repository.get_dataset_snapshot("snapshot-mixed-failure") is None
    finally:
        reopened_engine.close()


def test_production_dataset_snapshot_preserves_explicit_zero_and_persists_durable_receipt(
    tmp_path: Path,
) -> None:
    database = tmp_path / "learninghub_production_zero.sqlite3"
    engine, repository, artifacts, audit = _durable(database)
    registry = RecordingRemoteRegistry(repository)
    service = LearningHubService(
        repository=repository,
        registry=registry,  # type: ignore[arg-type]
        audit_log=audit,  # type: ignore[arg-type]
        artifact_store=artifacts,
        runtime_mode="production",
    )

    base_row = {
        "view_name": "store_machine_timeseries_view",
        "view_version": "store-machine-timeseries-view-v1",
        "feature_snapshot_time": NOW.isoformat(),
        "prediction_origin_time": NOW.isoformat(),
        "source_snapshot_ids": ["pos-live-001"],
        "features": {"event_time": NOW.isoformat()},
        "labels": {"w4_revenue": 410_000.0},
    }

    snapshot_id = "forecastops-zero-quality-001"
    try:
        snapshot = service.register_dataset_snapshot(
            [
                {
                    **base_row,
                    "entity_id": "store-zero",
                    "data_quality_score": 0.0,
                    "confidence": 0.0,
                },
                {
                    **base_row,
                    "entity_id": "store-positive",
                    "data_quality_score": 1.0,
                    "confidence": 0.95,
                },
            ],
            dataset_snapshot_id=snapshot_id,
        )
        assert snapshot.dataset_snapshot_id == snapshot_id
        assert len(snapshot.records) == 2
        # Verify 0.0 is preserved exactly and not treated as None or coalesced to 1.0
        assert snapshot.records[0].data_quality_score == 0.0
        assert snapshot.records[0].confidence == 0.0
        assert snapshot.records[1].data_quality_score == 1.0
        assert snapshot.records[1].confidence == 0.95

        saved = repository.get_dataset_snapshot(snapshot_id)
        assert saved is not None
        assert saved.records[0].data_quality_score == 0.0
        assert saved.records[0].confidence == 0.0
        assert saved.records[1].data_quality_score == 1.0
        assert saved.records[1].confidence == 0.95
    finally:
        engine.close()

    # Reopen durable store to confirm receipt survives restart
    reopened_engine, reopened_repository, _, _ = _durable(database)
    try:
        reopened_snapshot = reopened_repository.get_dataset_snapshot(snapshot_id)
        assert reopened_snapshot is not None
        assert reopened_snapshot.records[0].data_quality_score == 0.0
        assert reopened_snapshot.records[0].confidence == 0.0
        assert reopened_snapshot.records[1].data_quality_score == 1.0
        assert reopened_snapshot.records[1].confidence == 0.95
    finally:
        reopened_engine.close()
