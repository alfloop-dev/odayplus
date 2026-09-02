from __future__ import annotations

from datetime import UTC, datetime

import pytest

from models.shared_ml import (
    MetricThreshold,
    ModelAlias,
    ModelStage,
    ModelVersion,
    SegmentMetric,
    SegmentMetricThreshold,
    ValidationStatus,
    thresholds_from_decision_policy,
    validate_model_candidate,
)
from modules.learninghub.application import (
    LearningHubService,
    ModelReleaseDecision,
    ReleaseType,
)
from modules.learninghub.application.monitor import (
    MonitorStatus,
    RecommendedAction,
    evaluate_guardrails,
)
from modules.learninghub.domain import (
    DatasetSnapshot,
    MonitoringSignalType,
    RetrainingRequest,
    build_dataset_snapshot,
)
from modules.learninghub.infrastructure import InMemoryLearningHubRepository
from modules.learninghub.workers import run_learninghub_release_monitor
from shared.governance.decision_policy import (
    DecisionPolicy,
    InMemoryDecisionPolicyRepository,
    resolve_policy,
)
from tests.integration._learninghub_fixtures import dataset_rows


def _make_snapshot(snapshot_id: str = "snap-test") -> DatasetSnapshot:
    return build_dataset_snapshot(dataset_rows(), dataset_snapshot_id=snapshot_id)


def test_metric_threshold_absolute_bounds() -> None:
    t_min = MetricThreshold("auc", min_value=0.75, warning_min_value=0.80)
    assert t_min.evaluate(0.90)[0] is ValidationStatus.PASSED
    assert t_min.evaluate(0.78)[0] is ValidationStatus.WARNING
    assert t_min.evaluate(0.70)[0] is ValidationStatus.FAILED

    t_max = MetricThreshold("smape", max_value=0.15, warning_max_value=0.12)
    assert t_max.evaluate(0.10)[0] is ValidationStatus.PASSED
    assert t_max.evaluate(0.14)[0] is ValidationStatus.WARNING
    assert t_max.evaluate(0.18)[0] is ValidationStatus.FAILED


def test_performance_drift_auc_degradation_against_baseline() -> None:
    threshold = MetricThreshold(
        "auc",
        min_value=0.75,
        max_degradation=0.05,
        higher_is_better=True,
    )
    status, msg = threshold.evaluate(0.80, baseline_value=0.92)
    assert status is ValidationStatus.FAILED
    assert msg is not None
    assert "auc degradation 0.1200 exceeds maximum allowed 0.05" in msg

    status, msg = threshold.evaluate(0.89, baseline_value=0.92)
    assert status is ValidationStatus.PASSED
    assert msg is None


def test_performance_drift_relative_degradation() -> None:
    threshold = MetricThreshold(
        "auc",
        min_value=0.75,
        max_relative_degradation=0.10,
        higher_is_better=True,
    )
    status, msg = threshold.evaluate(0.80, baseline_value=0.92)
    assert status is ValidationStatus.FAILED
    assert "relative degradation" in msg


def test_challenger_worse_than_champion_fails_validation() -> None:
    snapshot = _make_snapshot()
    baseline_metrics = {"w4_smape": 0.08, "p80_coverage": 0.85}
    challenger_metrics = {"w4_smape": 0.11, "p80_coverage": 0.84}

    thresholds = [
        MetricThreshold("w4_smape", max_value=0.12, max_degradation=0.02, higher_is_better=False),
        MetricThreshold("p80_coverage", min_value=0.80, max_degradation=0.05, higher_is_better=True),
    ]

    run = validate_model_candidate(
        model_name="sales_forecast",
        model_version="v2.0.0",
        dataset_snapshot=snapshot,
        metrics=challenger_metrics,
        baseline_metrics=baseline_metrics,
        thresholds=thresholds,
    )

    assert run.passed is False
    assert run.status is ValidationStatus.FAILED
    assert len(run.failed_rules) == 1
    assert run.failed_rules[0].rule_name == "w4_smape"
    assert "w4_smape degradation 0.0300 exceeds maximum allowed 0.02" in run.failed_rules[0].message


def test_segment_metric_threshold_evaluates_baseline_degradation() -> None:
    threshold = SegmentMetricThreshold(
        segment_name="region",
        metric_name="w4_smape",
        segment_value="NORTH",
        max_value=0.20,
        max_degradation=0.03,
        higher_is_better=False,
    )

    observed_seg = SegmentMetric(
        segment_name="region",
        segment_value="NORTH",
        metrics={"w4_smape": 0.14},
        record_count=50,
    )
    baseline_seg = SegmentMetric(
        segment_name="region",
        segment_value="NORTH",
        metrics={"w4_smape": 0.09},
        record_count=50,
    )

    status, msg = threshold.evaluate(observed_seg, baseline_segment_metric=baseline_seg)
    assert status is ValidationStatus.FAILED
    assert "degradation 0.0500 exceeds maximum allowed 0.03" in msg


def test_evaluate_monitoring_triggers_retraining_on_performance_drift() -> None:
    repo = InMemoryLearningHubRepository()
    service = LearningHubService(repository=repo)
    snapshot = _make_snapshot()
    repo.save_dataset_snapshot(snapshot)

    v1 = ModelVersion(
        model_name="churn_model",
        version="v1.0.0",
        artifact_uri="gs://models/churn/v1",
        dataset_snapshot_id=snapshot.dataset_snapshot_id,
        feature_schema_version="v1",
        label_version="v1",
        metrics={"auc": 0.92},
        stage=ModelStage.PRODUCTION,
    )
    repo.save_model_version(v1)
    repo.set_alias("churn_model", ModelAlias.PRODUCTION, "v1.0.0")

    retrain_req = service.evaluate_monitoring(
        model_name="churn_model",
        dataset_snapshot_id=snapshot.dataset_snapshot_id,
        signal_type=MonitoringSignalType.DRIFT,
        observed_metrics={"auc": 0.80},
        baseline_metrics={"auc": 0.92},
        thresholds=(
            MetricThreshold("auc", min_value=0.75, max_degradation=0.05, higher_is_better=True),
        ),
        reason="AUC performance drift detected in production",
    )

    assert retrain_req is not None
    assert isinstance(retrain_req, RetrainingRequest)
    assert retrain_req.trigger_type == MonitoringSignalType.DRIFT
    assert retrain_req.source_model_version == "v1.0.0"
    assert retrain_req.observed_metrics == {"auc": 0.80}
    assert retrain_req.baseline_metrics == {"auc": 0.92}

    eval_record = repo.get_monitoring_evaluation(retrain_req.trigger_evaluation_id)
    assert eval_record is not None
    assert len(eval_record.breaches) == 1
    breach = eval_record.breaches[0]
    assert breach.metric_name == "auc"
    assert breach.observed_value == 0.80
    assert breach.baseline_value == 0.92
    assert pytest.approx(breach.degradation, 0.0001) == 0.12


def test_decision_policy_governs_performance_drift_thresholds() -> None:
    now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    policy = DecisionPolicy(
        policy_version_id="model-perf-policy-v1:tenant-corp",
        policy_label="model-perf-policy-v1",
        policy_id="model-perf-policy",
        policy_version="1.0.0",
        policy_kind="model_performance_drift",
        tenant_id="tenant-corp",
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        parameters={
            "default_max_degradation": 0.04,
            "metric_thresholds": {
                "auc": {
                    "min_value": 0.75,
                    "max_degradation": 0.05,
                    "higher_is_better": True,
                },
                "w4_smape": {
                    "max_value": 0.15,
                    "max_degradation": 0.02,
                    "higher_is_better": False,
                },
            },
        },
        declared_inputs=("observed_metrics", "baseline_metrics"),
        change_reason="Enterprise model drift governance policy",
        approved_by="model_risk_officer",
        owner_role="ml_governance",
    )

    policy_repo = InMemoryDecisionPolicyRepository([policy])
    governing = resolve_policy(
        policy_repo,
        policy_kind="model_performance_drift",
        tenant_id="tenant-corp",
        at=now,
    )

    thresholds = thresholds_from_decision_policy(governing)
    assert len(thresholds) == 2
    auc_thresh = next(t for t in thresholds if t.metric_name == "auc")
    assert auc_thresh.min_value == 0.75
    assert auc_thresh.max_degradation == 0.05
    assert auc_thresh.higher_is_better is True

    repo = InMemoryLearningHubRepository()
    service = LearningHubService(repository=repo)
    snapshot = _make_snapshot()
    repo.save_dataset_snapshot(snapshot)

    v1 = ModelVersion(
        model_name="revenue_predictor",
        version="v1.0.0",
        artifact_uri="gs://models/rev/v1",
        dataset_snapshot_id=snapshot.dataset_snapshot_id,
        feature_schema_version="v1",
        label_version="v1",
        metrics={"auc": 0.90, "w4_smape": 0.07},
        stage=ModelStage.PRODUCTION,
    )
    repo.save_model_version(v1)
    repo.set_alias("revenue_predictor", ModelAlias.PRODUCTION, "v1.0.0")

    retrain_req = service.evaluate_monitoring(
        model_name="revenue_predictor",
        dataset_snapshot_id=snapshot.dataset_snapshot_id,
        signal_type=MonitoringSignalType.DRIFT,
        observed_metrics={"auc": 0.81, "w4_smape": 0.10},
        baseline_metrics={"auc": 0.90, "w4_smape": 0.07},
        policy=governing,
    )

    assert retrain_req is not None
    assert retrain_req.decision_policy_version_id == "model-perf-policy-v1:tenant-corp"
    eval_record = repo.get_monitoring_evaluation(retrain_req.trigger_evaluation_id)
    assert eval_record.decision_policy_version_id == "model-perf-policy-v1:tenant-corp"


def test_evaluate_guardrails_and_monitor_release_degradation() -> None:
    guardrails = [
        MetricThreshold("normalized_mae", max_value=0.08, max_degradation=0.015, higher_is_better=False),
    ]

    breaches = evaluate_guardrails(
        observed_metrics={"normalized_mae": 0.06},
        guardrails=guardrails,
        baseline_metrics={"normalized_mae": 0.03},
    )
    assert len(breaches) == 1
    assert breaches[0].metric_name == "normalized_mae"
    assert breaches[0].baseline_value == 0.03
    assert pytest.approx(breaches[0].degradation, 0.0001) == 0.03
    assert "degradation 0.0300 exceeds maximum allowed 0.015" in breaches[0].detail


def test_release_worker_run_monitor_evaluates_baseline_degradation() -> None:
    repo = InMemoryLearningHubRepository()
    service = LearningHubService(repository=repo)
    snapshot = _make_snapshot()
    repo.save_dataset_snapshot(snapshot)

    v1 = ModelVersion(
        model_name="revenue_predictor",
        version="v1.0.0",
        artifact_uri="gs://models/rev/v1",
        dataset_snapshot_id=snapshot.dataset_snapshot_id,
        feature_schema_version="v1",
        label_version="v1",
        metrics={"auc": 0.90, "w4_smape": 0.07},
        stage=ModelStage.PRODUCTION,
    )
    repo.save_model_version(v1)

    decision = ModelReleaseDecision(
        release_id="rel-worker-test-001",
        model_name="revenue_predictor",
        from_version=None,
        to_version="v1.0.0",
        release_type=ReleaseType.FULL,
        reason="initial release",
        approval_id="approval-1",
        rollback_target="v1.0.0",
        monitoring_window="24h",
        success_criteria=(),
        fail_criteria=(),
        affected_modules=(),
        requested_by="ml-engineer",
        approved_by="risk-officer",
        model_card_checksum="sha256:test",
        release_revision=1,
    )
    repo.save_release_decision(decision)

    assessment = run_learninghub_release_monitor(
        {
            "release_id": "rel-worker-test-001",
            "observed_metrics": {"auc": 0.82, "w4_smape": 0.11},
            "guardrails": [
                {
                    "metric_name": "auc",
                    "min_value": 0.75,
                    "max_degradation": 0.05,
                    "higher_is_better": True,
                },
                {
                    "metric_name": "w4_smape",
                    "max_value": 0.15,
                    "max_degradation": 0.02,
                    "higher_is_better": False,
                },
            ],
            "evaluated_by": "on-call-monitor",
            "correlation_id": "corr-worker-monitor-1",
        },
        service=service,
    )

    assert assessment.status is MonitorStatus.BREACHED
    assert assessment.recommended_action is RecommendedAction.ROLLBACK
    assert len(assessment.breaches) == 2
    auc_breach = next(b for b in assessment.breaches if b.metric_name == "auc")
    assert auc_breach.baseline_value == 0.90
    assert pytest.approx(auc_breach.degradation, 0.0001) == 0.08
    assert "auc degradation 0.0800 exceeds maximum allowed 0.05" in auc_breach.detail

    smape_breach = next(b for b in assessment.breaches if b.metric_name == "w4_smape")
    assert smape_breach.baseline_value == 0.07
    assert pytest.approx(smape_breach.degradation, 0.0001) == 0.04
    assert "w4_smape degradation 0.0400 exceeds maximum allowed 0.02" in smape_breach.detail

