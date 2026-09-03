from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from models.shared_ml import ModelAlias, ModelStage, ModelVersion
from modules.learninghub import LearningHubError, LearningHubService
from modules.learninghub.domain import MonitoringSignalType
from modules.learninghub.infrastructure import (
    EvidentlyDriftMonitor,
    InMemoryLearningHubRepository,
)
from modules.learninghub.workers import run_learninghub_prediction_drift
from shared.audit import InMemoryAuditLog
from shared.governance import DecisionPolicy
from shared.infrastructure.persistence import DurableLearningHubRepository, SqliteDocumentStore, SqliteEngine


def _policy() -> DecisionPolicy:
    tenant = "00000000-0000-0000-0000-000000000001"
    return DecisionPolicy(
        policy_version_id=f"prediction-drift-policy-v1:{tenant}",
        policy_label="prediction-drift-policy-v1",
        policy_id="prediction-drift-policy",
        policy_version="1.0.0",
        policy_kind="model_performance_drift",
        tenant_id=tenant,
        effective_from=datetime(2026, 9, 1, tzinfo=UTC),
        parameters={"prediction_drift": {"drift_share_threshold": 0.5}},
        declared_inputs=("prediction_outputs",),
        change_reason="Govern prediction-output distribution monitoring",
        approved_by="model-risk-owner",
        owner_role="ml-governance",
    )


def _rows(values: range, *, cohort: str = "region:north") -> list[dict[str, object]]:
    return [
        {
            "prediction": float(value),
            "model_name": "revenue-model",
            "model_version": "v1",
            "cohort_key": cohort,
            # This changes with the current population, but is deliberately
            # outside prediction_columns and must not affect the result.
            "entity_id": f"entity-{value}",
        }
        for value in values
    ]


def test_prediction_drift_same_distribution_is_healthy() -> None:
    result = EvidentlyDriftMonitor().run_prediction(
        reference_rows=_rows(range(1, 101)),
        current_rows=_rows(range(1, 101)),
        model_name="revenue-model",
        model_version="v1",
        cohort_key="region:north",
        prediction_columns=("prediction",),
        output_types={"prediction": "numeric"},
        reference_snapshot_id="snapshot-reference",
        current_snapshot_id="snapshot-current",
        policy=_policy(),
    )

    assert result.drift_detected is False
    assert result.drifted_columns == 0
    assert result.reference_snapshot_id == "snapshot-reference"
    assert result.current_snapshot_id == "snapshot-current"
    assert result.model_version == "v1"
    assert result.prediction_output_types == {"prediction": "numeric"}


def test_prediction_drift_shifted_output_alerts() -> None:
    result = EvidentlyDriftMonitor().run_prediction(
        reference_rows=_rows(range(1, 101)),
        current_rows=_rows(range(1001, 1101)),
        model_name="revenue-model",
        model_version="v1",
        cohort_key="region:north",
        prediction_columns=("prediction",),
        output_types={"prediction": "numeric"},
        reference_snapshot_id="snapshot-reference",
        current_snapshot_id="snapshot-current",
        policy=_policy(),
    )

    assert result.drift_detected is True
    assert result.drifted_columns == 1
    assert result.drift_share == 1.0
    assert result.decision_policy_version_id == _policy().policy_version_id


def test_prediction_drift_rejects_mixed_cohort_and_version_rows() -> None:
    rows = _rows(range(1, 3))
    rows[1]["model_version"] = "v2"

    with pytest.raises(ValueError, match="outside the requested cohort"):
        EvidentlyDriftMonitor().run_prediction(
            reference_rows=rows,
            current_rows=_rows(range(1, 3)),
            model_name="revenue-model",
            model_version="v1",
            cohort_key="region:north",
            prediction_columns=("prediction",),
            output_types={"prediction": "numeric"},
            reference_snapshot_id="snapshot-reference",
            current_snapshot_id="snapshot-current",
            policy=_policy(),
        )


def test_prediction_drift_service_persists_receipt_and_alert(tmp_path: Path) -> None:
    database = tmp_path / "prediction-drift.sqlite3"
    engine = SqliteEngine(database)
    repository = DurableLearningHubRepository(SqliteDocumentStore(engine))
    audit_log = InMemoryAuditLog()
    model = ModelVersion(
        model_name="revenue-model",
        version="v1",
        artifact_uri="gs://models/revenue/v1",
        dataset_snapshot_id="training-snapshot",
        feature_schema_version="features-v1",
        label_version="labels-v1",
        metrics={"mae": 0.1},
        stage=ModelStage.PRODUCTION,
        monitoring_config={
            "prediction_columns": ["prediction"],
            "prediction_output_types": {"prediction": "numeric"},
        },
    )
    repository.save_model_version(model)
    repository.set_alias("revenue-model", ModelAlias.PRODUCTION, "v1")
    service = LearningHubService(repository=repository, audit_log=audit_log)

    try:
        receipt = run_learninghub_prediction_drift(
            {
                "model_name": "revenue-model",
                "model_version": "v1",
                "reference_rows": _rows(range(1, 101)),
                "current_rows": _rows(range(1001, 1101)),
                "reference_snapshot_id": "snapshot-reference",
                "current_snapshot_id": "snapshot-current",
                "cohort_key": "region:north",
                "decision_policy": _policy(),
                "requested_by": "on-call-monitor",
            },
            service=service,
        )
        assert receipt.signal_type is MonitoringSignalType.PREDICTION_DRIFT
        assert receipt.reference_snapshot_id == "snapshot-reference"
        assert receipt.current_snapshot_id == "snapshot-current"
        assert receipt.model_version == "v1"
        assert receipt.cohort_key == "region:north"
        assert receipt.decision_policy_version_id == _policy().policy_version_id
        assert receipt.drift_detected is True
        assert receipt.alert_id == receipt.evaluation_id
        assert receipt.audit_event_id is not None
        assert repository.get_monitoring_evaluation(receipt.evaluation_id) == receipt
        requests = repository.list_retraining_requests("revenue-model")
        assert len(requests) == 1
        assert requests[0].trigger_evaluation_id == receipt.evaluation_id
        assert requests[0].trigger_type is MonitoringSignalType.PREDICTION_DRIFT
        assert any(
            event.event_type == "learninghub.prediction_drift.v1"
            and event.outcome == "breached"
            for event in audit_log.list_events()
        )
    finally:
        engine.close()

    reopened_engine = SqliteEngine(database)
    try:
        reopened = DurableLearningHubRepository(SqliteDocumentStore(reopened_engine))
        persisted = reopened.get_monitoring_evaluation(receipt.evaluation_id)
        assert persisted is not None
        assert persisted.reference_snapshot_id == "snapshot-reference"
        assert persisted.current_snapshot_id == "snapshot-current"
        assert persisted.decision_policy_version_id == _policy().policy_version_id
    finally:
        reopened_engine.close()


def test_prediction_drift_service_rejects_non_production_model_version() -> None:
    repository = InMemoryLearningHubRepository()
    repository.save_model_version(
        ModelVersion(
            model_name="revenue-model",
            version="v1",
            artifact_uri="memory://v1",
            dataset_snapshot_id="training-snapshot",
            feature_schema_version="features-v1",
            label_version="labels-v1",
            metrics={},
            stage=ModelStage.PRODUCTION,
        )
    )
    repository.save_model_version(
        ModelVersion(
            model_name="revenue-model",
            version="v2",
            artifact_uri="memory://v2",
            dataset_snapshot_id="training-snapshot",
            feature_schema_version="features-v1",
            label_version="labels-v1",
            metrics={},
            stage=ModelStage.CANARY,
        )
    )
    repository.set_alias("revenue-model", ModelAlias.PRODUCTION, "v1")

    with pytest.raises(LearningHubError, match="current production model version"):
        LearningHubService(repository=repository).monitor_prediction_drift(
            model_name="revenue-model",
            model_version="v2",
            reference_rows=_rows(range(1, 3)),
            current_rows=_rows(range(1, 3)),
            reference_snapshot_id="snapshot-reference",
            current_snapshot_id="snapshot-current",
            cohort_key="region:north",
            prediction_columns=("prediction",),
            output_types={"prediction": "numeric"},
            policy=_policy(),
        )
