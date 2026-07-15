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
    ModelVersion,
    SegmentMetric,
)
from modules.learninghub import (
    InMemoryLearningHubRepository,
    LearningHubError,
    LearningHubService,
    MlflowRegistryAdapter,
    MonitorStatus,
    RecommendedAction,
    ReleaseType,
    run_learninghub_release,
    run_learninghub_release_monitor,
)
from shared.audit import InMemoryAuditLog

SNAPSHOT_TIME = datetime(2026, 6, 27, 8, 0, tzinfo=UTC)
PREDICTION_TIME = datetime(2026, 6, 27, 9, 0, tzinfo=UTC)


def _rows() -> list[dict[str, object]]:
    return [
        {
            "view_name": "store_machine_timeseries_view",
            "view_version": "store-machine-timeseries-view-v1",
            "entity_id": "store-001",
            "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
            "prediction_origin_time": PREDICTION_TIME.isoformat(),
            "source_snapshot_ids": ["pos-20260627", "machine-20260627"],
            "labels": {"w4_revenue": 410_000},
            "label_maturity_time": SNAPSHOT_TIME.isoformat(),
            "features": {"event_time": SNAPSHOT_TIME.isoformat(), "revenue_lag_7d": 92_000},
        },
        {
            "view_name": "store_machine_timeseries_view",
            "view_version": "store-machine-timeseries-view-v1",
            "entity_id": "store-002",
            "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
            "prediction_origin_time": PREDICTION_TIME.isoformat(),
            "source_snapshot_ids": ["pos-20260627"],
            "labels": {"w4_revenue": 380_000},
            "label_maturity_time": SNAPSHOT_TIME.isoformat(),
            "features": {"event_time": SNAPSHOT_TIME.isoformat(), "revenue_lag_7d": 88_000},
        },
    ]


def _model_version(version: str, dataset_snapshot_id: str) -> ModelVersion:
    return ModelVersion(
        model_name="forecast_revenue_interval",
        version=version,
        artifact_uri=f"gs://oday-artifacts/models/forecast_revenue_interval/{version}/model",
        dataset_snapshot_id=dataset_snapshot_id,
        feature_schema_version="store-machine-timeseries-view-v1",
        label_version="forecast-w4-revenue-v1",
        metrics={"w4_smape": 0.11, "p80_coverage": 0.82},
        run_id=f"mlflow-run-{version}",
        git_sha="abc1234",
    )


def _model_card(version: str, dataset_snapshot_id: str, validation_run_id: str) -> ModelCard:
    return ModelCard(
        model_name="forecast_revenue_interval",
        model_version=version,
        owner="ml-platform",
        risk_level=ModelRiskLevel.R3,
        intended_use="ForecastOps 4/8/12/24 week revenue interval input",
        not_intended_use="Direct store closure, pricing, or campaign execution",
        dataset_snapshot_id=dataset_snapshot_id,
        validation_run_id=validation_run_id,
        feature_set_id="fs_forecastops_v1",
        label_set_id="ls_forecastops_w4_v1",
        training_period="2026-01-01/2026-05-31",
        validation_period="2026-06-01/2026-06-27",
        algorithm="seasonal_baseline_plus_gradient_boosting",
        baseline="seasonal_naive_v1",
        metrics_summary={"w4_smape": 0.11, "p80_coverage": 0.82},
        segment_metrics=({"segment_name": "region", "segment_value": "north", "w4_smape": 0.10},),
        calibration_summary={"p80_coverage": 0.82},
        explainability_method="feature_importance",
        limitations=("synthetic fixture validation only",),
        known_biases=("low volume stores have wider error bands",),
        rollback_conditions=(
            "p80_coverage < 0.75 for 2 consecutive monitoring windows",
            "red_alert_precision drops below approved threshold",
        ),
        approvals=(ModelCardApproval(approver="reviewer-a", role="model-review-board"),),
    )


def _prepare_candidate(service: LearningHubService, version: str) -> ModelVersion:
    snapshot = service.register_dataset_snapshot(
        _rows(), dataset_snapshot_id=f"forecast-training-{version}"
    )
    validation = service.validate_candidate(
        model_name="forecast_revenue_interval",
        model_version=version,
        dataset_snapshot_id=snapshot.dataset_snapshot_id,
        metrics={"w4_smape": 0.11, "p80_coverage": 0.82},
        baseline_metrics={"w4_smape": 0.15, "p80_coverage": 0.78},
        thresholds=(
            MetricThreshold("w4_smape", max_value=0.12, warning_max_value=0.115),
            MetricThreshold("p80_coverage", min_value=0.80, warning_min_value=0.81),
        ),
        segment_metrics=(
            SegmentMetric(
                segment_name="region",
                segment_value="north",
                metrics={"w4_smape": 0.10},
                record_count=1,
            ),
        ),
        calibration_summary={"p80_coverage": 0.82},
    )
    assert validation.passed
    return service.register_model_version(
        model_version=_model_version(version, snapshot.dataset_snapshot_id),
        model_card=_model_card(version, snapshot.dataset_snapshot_id, validation.validation_run_id),
        validation_run=validation,
    )


def test_learninghub_validates_releases_and_rolls_back_model_aliases() -> None:
    repository = InMemoryLearningHubRepository()
    audit_log = InMemoryAuditLog()
    service = LearningHubService(
        repository=repository,
        registry=MlflowRegistryAdapter(repository),
        audit_log=audit_log,
    )
    v1 = _prepare_candidate(service, "1.0.0")
    v2 = _prepare_candidate(service, "1.1.0")

    shadow = service.request_release(
        model_name=v1.model_name,
        version=v1.version,
        release_type=ReleaseType.SHADOW,
        reason="run shadow predictions before promotion",
        approval_id="approval-shadow-001",
        rollback_target=None,
        monitoring_window="24h",
        success_criteria=("schema valid", "latency normal"),
        fail_criteria=("missing model card",),
        affected_modules=("ForecastOps",),
        requested_by="ml-owner",
        correlation_id="corr-learninghub-1",
    )
    assert shadow.release_type is ReleaseType.SHADOW
    assert repository.get_alias(v1.model_name, ModelAlias.SHADOW).version == "1.0.0"

    service.request_release(
        model_name=v1.model_name,
        version=v1.version,
        release_type=ReleaseType.FULL,
        reason="promote validated champion",
        approval_id="approval-full-001",
        rollback_target=v1.version,
        monitoring_window="48h",
        success_criteria=("smoke prediction passed",),
        fail_criteria=("p80 coverage below threshold",),
        affected_modules=("ForecastOps", "InterventionOps"),
        requested_by="ml-owner",
        correlation_id="corr-learninghub-2",
    )
    assert repository.get_alias(v1.model_name, ModelAlias.PRODUCTION).version == "1.0.0"
    assert repository.get_alias(v1.model_name, ModelAlias.CHAMPION).version == "1.0.0"

    service.request_release(
        model_name=v2.model_name,
        version=v2.version,
        release_type=ReleaseType.FULL,
        reason="better error and coverage than champion",
        approval_id="approval-full-002",
        rollback_target=v1.version,
        monitoring_window="48h",
        success_criteria=("smoke prediction passed", "model_version present in output"),
        fail_criteria=("coverage regression",),
        affected_modules=("ForecastOps",),
        requested_by="ml-owner",
        correlation_id="corr-learninghub-3",
    )
    assert repository.get_alias(v2.model_name, ModelAlias.PRODUCTION).version == "1.1.0"
    assert repository.get_alias(v2.model_name, ModelAlias.PREVIOUS_PRODUCTION).version == "1.0.0"
    assert repository.get_model_version(v2.model_name, "1.1.0").stage is ModelStage.PRODUCTION

    rollback = run_learninghub_release(
        {
            "model_name": v2.model_name,
            "version": v2.version,
            "release_type": "ROLLBACK",
            "reason": "coverage watch window breached",
            "approval_id": "approval-rollback-001",
            "rollback_target": v1.version,
            "monitoring_window": "immediate",
            "success_criteria": ["alias points at previous model"],
            "fail_criteria": ["smoke prediction fails"],
            "affected_modules": ["ForecastOps"],
            "requested_by": "on-call",
            "correlation_id": "corr-learninghub-4",
        },
        service=service,
    )
    assert rollback.release_type is ReleaseType.ROLLBACK
    assert rollback.from_version == "1.1.0"
    assert rollback.to_version == "1.0.0"
    assert repository.get_alias(v2.model_name, ModelAlias.PRODUCTION).version == "1.0.0"
    assert repository.get_model_version(v2.model_name, "1.1.0").stage is ModelStage.ROLLED_BACK
    assert any(event.action == "rollback" for event in audit_log.list_events())


def _release_full(service: LearningHubService, *, version: str, rollback_target: str):
    return service.request_release(
        model_name="forecast_revenue_interval",
        version=version,
        release_type=ReleaseType.FULL,
        reason="promote validated champion",
        approval_id=f"approval-full-{version}",
        rollback_target=rollback_target,
        monitoring_window="48h",
        success_criteria=("p80_coverage >= 0.80",),
        fail_criteria=("p80_coverage < 0.75",),
        affected_modules=("ForecastOps",),
        requested_by="ml-owner",
        correlation_id="corr-monitor-release",
    )


def test_release_monitor_healthy_records_audit_without_recommending_rollback() -> None:
    repository = InMemoryLearningHubRepository()
    audit_log = InMemoryAuditLog()
    service = LearningHubService(repository=repository, audit_log=audit_log)
    _prepare_candidate(service, "1.0.0")
    v2 = _prepare_candidate(service, "1.1.0")
    decision = _release_full(service, version=v2.version, rollback_target="1.0.0")

    assessment = service.monitor_release(
        release_id=decision.release_id,
        observed_metrics={"w4_smape": 0.10, "p80_coverage": 0.83},
        guardrails=(
            MetricThreshold("w4_smape", max_value=0.12),
            MetricThreshold("p80_coverage", min_value=0.75),
        ),
        correlation_id="corr-monitor-1",
    )

    assert assessment.status is MonitorStatus.HEALTHY
    assert assessment.recommended_action is RecommendedAction.NONE
    assert assessment.breaches == ()
    assert assessment.audit_event_id is not None
    monitor_events = [
        event for event in audit_log.list_events()
        if event.event_type == "learninghub.release_monitor.v1"
    ]
    assert len(monitor_events) == 1
    assert monitor_events[0].outcome == "healthy"


def test_release_monitor_breach_recommends_rollback_and_leaves_alias_unchanged() -> None:
    repository = InMemoryLearningHubRepository()
    audit_log = InMemoryAuditLog()
    service = LearningHubService(repository=repository, audit_log=audit_log)
    from models.shared_ml.registry import ModelAlias

    _prepare_candidate(service, "1.0.0")
    v2 = _prepare_candidate(service, "1.1.0")
    decision = _release_full(service, version=v2.version, rollback_target="1.0.0")

    assessment = run_learninghub_release_monitor(
        {
            "release_id": decision.release_id,
            "observed_metrics": {"p80_coverage": 0.70},
            "guardrails": [{"metric_name": "p80_coverage", "min_value": 0.75}],
            "evaluated_by": "on-call-monitor",
            "correlation_id": "corr-monitor-2",
        },
        service=service,
    )

    assert assessment.status is MonitorStatus.BREACHED
    assert assessment.recommended_action is RecommendedAction.ROLLBACK
    assert [breach.metric_name for breach in assessment.breaches] == ["p80_coverage"]
    # Monitor is never optimistic: it recommends, it does not mutate the alias.
    assert repository.get_alias("forecast_revenue_interval", ModelAlias.PRODUCTION).version == "1.1.0"
    assert any(
        event.event_type == "learninghub.release_monitor.v1" and event.outcome == "breached"
        for event in audit_log.list_events()
    )


def test_release_monitor_rejects_unknown_release() -> None:
    service = LearningHubService()
    with pytest.raises(LearningHubError, match="unknown release"):
        service.monitor_release(
            release_id="does-not-exist",
            observed_metrics={"p80_coverage": 0.9},
            guardrails=(MetricThreshold("p80_coverage", min_value=0.75),),
        )


def test_learninghub_blocks_release_without_passed_validation_or_model_card() -> None:
    service = LearningHubService()
    snapshot = service.register_dataset_snapshot(_rows(), dataset_snapshot_id="failed-ds")
    validation = service.validate_candidate(
        model_name="forecast_revenue_interval",
        model_version="2.0.0",
        dataset_snapshot_id=snapshot.dataset_snapshot_id,
        metrics={"w4_smape": 0.18, "p80_coverage": 0.70},
        baseline_metrics={"w4_smape": 0.15, "p80_coverage": 0.78},
        thresholds=(MetricThreshold("w4_smape", max_value=0.12),),
    )
    assert not validation.passed
    service.register_model_version(
        model_version=_model_version("2.0.0", snapshot.dataset_snapshot_id),
        model_card=_model_card("2.0.0", snapshot.dataset_snapshot_id, validation.validation_run_id),
        validation_run=validation,
    )

    with pytest.raises(LearningHubError, match="passed validation"):
        service.request_release(
            model_name="forecast_revenue_interval",
            version="2.0.0",
            release_type=ReleaseType.FULL,
            reason="should fail",
            approval_id="approval-full-003",
            rollback_target="1.0.0",
            monitoring_window="48h",
            success_criteria=("none",),
            fail_criteria=("validation failed",),
        )
