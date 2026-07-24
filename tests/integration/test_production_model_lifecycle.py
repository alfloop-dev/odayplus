from __future__ import annotations

from datetime import UTC, datetime

import pytest

from models.shared_ml import (
    MetricThreshold,
    ModelAlias,
    ModelCard,
    ModelCardApproval,
    ModelRiskLevel,
    ModelStage,
    SegmentMetricThreshold,
)
from modules.learninghub import (
    InferenceComparisonMode,
    LearningHubError,
    LearningHubService,
    MlflowRegistryAdapter,
    MonitoringSignalType,
    ReleaseType,
)
from pipelines.features import FeaturePipelineRunner
from pipelines.training import TrainingPipelineResult, TrainingPipelineRunner
from shared.infrastructure.persistence import (
    DurableArtifactStore,
    DurableAuditLog,
    DurableLearningHubRepository,
    SqliteDocumentStore,
    SqliteEngine,
)

MODEL_NAME = "forecast_revenue_interval"
SNAPSHOT_TIME = datetime(2026, 7, 1, 8, 0, tzinfo=UTC)
PREDICTION_TIME = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "production_model_lifecycle.sqlite3")


def _rows(snapshot_id: str = "pos-20260701") -> list[dict[str, object]]:
    return [
        _row("store-001", "north", 100.0, 42.0, snapshot_id),
        _row("store-002", "north", 104.0, 43.0, snapshot_id),
        _row("store-003", "south", 120.0, 51.0, snapshot_id),
        _row("store-004", "south", 124.0, 52.0, snapshot_id),
    ]


def _row(
    entity_id: str,
    region: str,
    label: float,
    visits: float,
    snapshot_id: str,
) -> dict[str, object]:
    return {
        "view_name": "store_machine_timeseries_view",
        "view_version": "store-machine-timeseries-view-v2",
        "entity_id": entity_id,
        "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
        "prediction_origin_time": PREDICTION_TIME.isoformat(),
        "source_snapshot_ids": [snapshot_id],
        "labels": {"w4_revenue": label},
        "label_maturity_time": SNAPSHOT_TIME.isoformat(),
        "features": {
            "event_time": SNAPSHOT_TIME.isoformat(),
            "visits_lag_7d": visits,
            "region": region,
        },
    }


def _build_durable(db_path: str) -> tuple[SqliteEngine, LearningHubService, DurableArtifactStore]:
    engine = SqliteEngine(db_path)
    store = SqliteDocumentStore(engine)
    repository = DurableLearningHubRepository(store)
    service = LearningHubService(
        repository=repository,
        registry=MlflowRegistryAdapter(repository),
        audit_log=DurableAuditLog(engine),
    )
    return engine, service, DurableArtifactStore(store)


def _feature_and_train(
    service: LearningHubService,
    artifacts: DurableArtifactStore,
    *,
    version: str,
    dataset_snapshot_id: str,
    segment_thresholds: tuple[SegmentMetricThreshold, ...] = (),
) -> TrainingPipelineResult:
    feature = FeaturePipelineRunner(
        repository=service.repository,
        artifact_store=artifacts,
    ).run(
        model_name=MODEL_NAME,
        dataset_snapshot_id=dataset_snapshot_id,
        feature_schema_version="store-machine-timeseries-view-v2",
        feature_set_id="fs_forecastops_v2",
        actor="Codex2",
        run_id=f"feature-{version}",
    )
    return TrainingPipelineRunner(service=service, artifact_store=artifacts).run(
        model_name=MODEL_NAME,
        model_version=version,
        feature_artifact=feature,
        label_name="w4_revenue",
        feature_schema_version="store-machine-timeseries-view-v2",
        label_version="forecast-w4-revenue-v2",
        thresholds=(
            MetricThreshold("normalized_mae", max_value=0.05),
            MetricThreshold("p80_coverage", min_value=0.80),
        ),
        segment_thresholds=segment_thresholds,
        segment_field="region",
        actor="Codex2",
        run_id=f"training-{version}",
        git_sha="abc1234",
    )


def _card(result: TrainingPipelineResult) -> ModelCard:
    return ModelCard(
        model_name=MODEL_NAME,
        model_version=result.model_version.version,
        owner="ml-platform",
        risk_level=ModelRiskLevel.R3,
        intended_use="ForecastOps revenue interval decisions",
        not_intended_use="Automatic store closure, direct pricing, or unsupervised promotion",
        dataset_snapshot_id=result.model_version.dataset_snapshot_id,
        validation_run_id=result.validation_run.validation_run_id,
        feature_set_id=result.feature_artifact.feature_set_id or result.feature_artifact.version,
        label_set_id="ls_forecastops_w4_v2",
        training_period="2026-01-01/2026-06-30",
        validation_period="2026-07-01/2026-07-01",
        algorithm="deterministic_backtest_regressor",
        baseline="mean_label_baseline",
        metrics_summary=result.validation_run.metrics,
        segment_metrics=[metric.to_dict() for metric in result.validation_run.segment_metrics],
        calibration_summary=result.validation_run.calibration_summary,
        explainability_method="feature_attribution",
        limitations=("deterministic integration fixture",),
        known_biases=("low volume stores require wider prediction intervals",),
        rollback_conditions=("normalized_mae > 0.10", "p80_coverage < 0.75"),
        approvals=(ModelCardApproval(approver="reviewer-a", role="model-review-board"),),
    )


def _register_candidate(service: LearningHubService, result: TrainingPipelineResult) -> None:
    service.register_model_version(
        model_version=result.model_version,
        model_card=_card(result),
        validation_run=result.validation_run,
    )


def test_feature_training_artifacts_are_reproducible_and_promotion_is_bound(db_path) -> None:
    engine, service, artifacts = _build_durable(db_path)
    try:
        snapshot = service.register_dataset_snapshot(
            _rows(),
            dataset_snapshot_id="forecast-training-1.0.0",
            feature_set_id=None,
            label_set_id=None,
        )
        runner = FeaturePipelineRunner(repository=service.repository, artifact_store=artifacts)
        first = runner.run(
            model_name=MODEL_NAME,
            dataset_snapshot_id=snapshot.dataset_snapshot_id,
            feature_schema_version="store-machine-timeseries-view-v2",
            feature_set_id="fs_forecastops_v2",
            actor="Codex2",
        )
        second = runner.run(
            model_name=MODEL_NAME,
            dataset_snapshot_id=snapshot.dataset_snapshot_id,
            feature_schema_version="store-machine-timeseries-view-v2",
            feature_set_id="fs_forecastops_v2",
            actor="Codex2",
        )
        assert second.version == first.version
        assert second.content_digest == first.content_digest
        assert artifacts.verify(first.artifact_id)

        result = _feature_and_train(
            service,
            artifacts,
            version="1.0.0",
            dataset_snapshot_id=snapshot.dataset_snapshot_id,
            segment_thresholds=(
                SegmentMetricThreshold("region", "normalized_mae", max_value=0.01),
            ),
        )
        assert result.accepted
        assert artifacts.verify(result.model_artifact.artifact_id)
        assert artifacts.verify(result.validation_report_artifact.artifact_id)

        _register_candidate(service, result)
        decision = service.request_release(
            model_name=MODEL_NAME,
            version="1.0.0",
            release_type=ReleaseType.FULL,
            reason="promote reproducible champion",
            approval_id="approval-full-001",
            rollback_target="1.0.0",
            monitoring_window="48h",
            success_criteria=("registry evidence complete",),
            fail_criteria=("validation regression",),
            requested_by="ml-owner",
            approved_by="reviewer-a",
            correlation_id="corr-promote-v1",
        )
    finally:
        engine.close()

    assert decision.dataset_snapshot_id == "forecast-training-1.0.0"
    assert decision.feature_schema_version == "store-machine-timeseries-view-v2"
    assert decision.label_version == "forecast-w4-revenue-v2"
    assert decision.approval_id == "approval-full-001"
    assert decision.rollback_target == "1.0.0"
    assert decision.requested_by == "ml-owner"
    assert decision.model_card_checksum and decision.model_card_checksum.startswith("sha256:")
    assert decision.model_artifact_uri and decision.model_artifact_uri.startswith(
        "odp-artifact://sha256/"
    )

    reopened, service2, artifacts2 = _build_durable(db_path)
    try:
        assert service2.repository.get_alias(MODEL_NAME, ModelAlias.PRODUCTION).version == "1.0.0"
        assert artifacts2.verify(f"{MODEL_NAME}/1.0.0/model")
        assert artifacts2.verify(f"{MODEL_NAME}/1.0.0/validation_report")
    finally:
        reopened.close()


def test_segment_acceptance_failure_rejects_governed_release(db_path) -> None:
    engine, service, artifacts = _build_durable(db_path)
    try:
        snapshot = service.register_dataset_snapshot(
            _rows(),
            dataset_snapshot_id="forecast-training-rejected",
        )
        rejected = _feature_and_train(
            service,
            artifacts,
            version="2.0.0",
            dataset_snapshot_id=snapshot.dataset_snapshot_id,
            segment_thresholds=(
                SegmentMetricThreshold(
                    "region",
                    "p80_coverage",
                    min_value=0.90,
                    segment_value="north",
                ),
            ),
        )
        assert not rejected.accepted
        _register_candidate(service, rejected)

        with pytest.raises(LearningHubError, match="passed validation"):
            service.request_release(
                model_name=MODEL_NAME,
                version="2.0.0",
                release_type=ReleaseType.FULL,
                reason="must fail segment gate",
                approval_id="approval-full-002",
                rollback_target="1.0.0",
                monitoring_window="48h",
                success_criteria=("none",),
                fail_criteria=("segment gate failed",),
            )
    finally:
        engine.close()


def test_monitoring_comparison_restart_safety_and_governed_rollback(db_path) -> None:
    engine, service, artifacts = _build_durable(db_path)
    try:
        ds1 = service.register_dataset_snapshot(
            _rows("pos-20260701"),
            dataset_snapshot_id="forecast-training-1.0.0",
        )
        ds2 = service.register_dataset_snapshot(
            _rows("pos-20260702"),
            dataset_snapshot_id="forecast-training-1.1.0",
        )
        v1 = _feature_and_train(
            service,
            artifacts,
            version="1.0.0",
            dataset_snapshot_id=ds1.dataset_snapshot_id,
        )
        v2 = _feature_and_train(
            service,
            artifacts,
            version="1.1.0",
            dataset_snapshot_id=ds2.dataset_snapshot_id,
        )
        _register_candidate(service, v1)
        _register_candidate(service, v2)
        service.request_release(
            model_name=MODEL_NAME,
            version="1.0.0",
            release_type=ReleaseType.FULL,
            reason="promote champion",
            approval_id="approval-full-001",
            rollback_target="1.0.0",
            monitoring_window="48h",
            success_criteria=("ok",),
            fail_criteria=("regress",),
            requested_by="ml-owner",
            correlation_id="corr-v1",
        )
        service.request_release(
            model_name=MODEL_NAME,
            version="1.1.0",
            release_type=ReleaseType.CANARY,
            reason="same-input canary comparison",
            approval_id="approval-canary-001",
            rollback_target="1.0.0",
            monitoring_window="24h",
            success_criteria=("same inputs within tolerance",),
            fail_criteria=("delta breach",),
            requested_by="ml-owner",
            correlation_id="corr-canary",
        )

        drift_request = service.evaluate_monitoring(
            model_name=MODEL_NAME,
            dataset_snapshot_id=ds2.dataset_snapshot_id,
            signal_type=MonitoringSignalType.DRIFT,
            observed_metrics={"population_stability_index": 0.42},
            baseline_metrics={"population_stability_index": 0.08},
            thresholds=(MetricThreshold("population_stability_index", max_value=0.20),),
            requested_by="monitoring-worker",
        )
        outcome_request = service.ingest_outcome_monitoring(
            model_name=MODEL_NAME,
            dataset_snapshot_id=ds2.dataset_snapshot_id,
            observed_metrics={"normalized_mae": 0.18},
            baseline_metrics={"normalized_mae": 0.04},
            thresholds=(MetricThreshold("normalized_mae", max_value=0.10),),
            requested_by="outcome-worker",
        )
        assert drift_request and not drift_request.auto_promotion
        assert outcome_request and not outcome_request.auto_promotion
        assert service.repository.get_alias(MODEL_NAME, ModelAlias.PRODUCTION).version == "1.0.0"

        def predictor(model_version, row):
            base = float(row["value"])
            if model_version.version == "1.1.0":
                return base + 12.0
            return base

        comparison = service.compare_inference(
            model_name=MODEL_NAME,
            challenger_version="1.1.0",
            inputs=(
                {"input_id": "store-001", "value": 100.0},
                {"input_id": "store-002", "value": 104.0},
            ),
            predictor=predictor,
            mode=InferenceComparisonMode.CANARY,
            tolerance=5.0,
            requested_by="canary-worker",
        )
        assert comparison.rollback_recommended
    finally:
        engine.close()

    restarted, service2, _ = _build_durable(db_path)
    try:
        stored_requests = service2.repository.list_retraining_requests(MODEL_NAME)
        assert {request.trigger_type for request in stored_requests} == {
            MonitoringSignalType.DRIFT,
            MonitoringSignalType.OUTCOME,
        }
        stored_comparison = service2.repository.get_inference_comparison(comparison.comparison_id)
        assert stored_comparison.rollback_recommended
        assert [p.input_id for p in stored_comparison.champion_predictions] == [
            p.input_id for p in stored_comparison.challenger_predictions
        ]

        service2.request_release(
            model_name=MODEL_NAME,
            version="1.1.0",
            release_type=ReleaseType.FULL,
            reason="promote canary after manual approval",
            approval_id="approval-full-011",
            rollback_target="1.0.0",
            monitoring_window="48h",
            success_criteria=("ok",),
            fail_criteria=("regress",),
            requested_by="ml-owner",
            correlation_id="corr-v2-full",
        )
        assert service2.repository.get_alias(MODEL_NAME, ModelAlias.PRODUCTION).version == "1.1.0"
        rollback = service2.request_rollback_from_comparison(
            comparison_id=comparison.comparison_id,
            reason="same-input canary delta breached rollback policy",
            approval_id="approval-rollback-001",
            requested_by="on-call",
        )
        assert rollback.release_type is ReleaseType.ROLLBACK
        assert rollback.rollback_target == "1.0.0"
        assert service2.repository.get_alias(MODEL_NAME, ModelAlias.PRODUCTION).version == "1.0.0"
        assert (
            service2.repository.get_model_version(MODEL_NAME, "1.0.0").stage
            is ModelStage.PRODUCTION
        )
        assert (
            service2.repository.get_model_version(MODEL_NAME, "1.1.0").stage
            is ModelStage.ROLLED_BACK
        )
    finally:
        restarted.close()

    verified, service3, _ = _build_durable(db_path)
    try:
        assert service3.repository.get_alias(MODEL_NAME, ModelAlias.PRODUCTION).version == "1.0.0"
        assert any(
            decision.release_type is ReleaseType.ROLLBACK
            for decision in service3.repository.list_release_decisions()
        )
        assert len(service3.audit_log.list_events(correlation_id=comparison.comparison_id)) == 1
    finally:
        verified.close()
