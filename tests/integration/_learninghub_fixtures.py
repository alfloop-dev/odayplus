"""Shared Learning Hub release fixtures.

The release suites (in-memory, SQLite, and PostgreSQL) all need the same
model-ready rows, candidate model version, and governed model card, so they live
here instead of being duplicated per storage backend. The model card records two
independent approvers in governance roles, which is what lets a release name an
approver who is not the requester.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from models.shared_ml import (
    MetricThreshold,
    ModelCard,
    ModelCardApproval,
    ModelRiskLevel,
    ModelVersion,
    SegmentMetric,
)
from modules.learninghub import LearningHubService

DEFAULT_MODEL_NAME = "forecast_revenue_interval"
SNAPSHOT_TIME = datetime(2026, 6, 27, 8, 0, tzinfo=UTC)
PREDICTION_TIME = datetime(2026, 6, 27, 9, 0, tzinfo=UTC)


def dataset_rows() -> list[dict[str, object]]:
    return [
        {
            "view_name": "store_machine_timeseries_view",
            "view_version": "store-machine-timeseries-view-v1",
            "entity_id": "store-001",
            "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
            "prediction_origin_time": PREDICTION_TIME.isoformat(),
            "source_snapshot_ids": ["pos-20260627", "machine-20260627"],
            "data_quality_score": 1.0,
            "confidence": 1.0,
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
            "data_quality_score": 1.0,
            "confidence": 1.0,
            "labels": {"w4_revenue": 380_000},
            "label_maturity_time": SNAPSHOT_TIME.isoformat(),
            "features": {"event_time": SNAPSHOT_TIME.isoformat(), "revenue_lag_7d": 88_000},
        },
    ]


def model_version(
    version: str,
    dataset_snapshot_id: str,
    *,
    model_name: str = DEFAULT_MODEL_NAME,
) -> ModelVersion:
    return ModelVersion(
        model_name=model_name,
        version=version,
        artifact_uri=f"gs://oday-artifacts/models/{model_name}/{version}/model",
        dataset_snapshot_id=dataset_snapshot_id,
        feature_schema_version="store-machine-timeseries-view-v1",
        label_version="forecast-w4-revenue-v1",
        metrics={"w4_smape": 0.11, "p80_coverage": 0.82},
        run_id=f"mlflow-run-{version}",
        git_sha="abc1234",
    )


def model_card(
    version: str,
    dataset_snapshot_id: str,
    validation_run_id: str,
    *,
    model_name: str = DEFAULT_MODEL_NAME,
) -> ModelCard:
    return ModelCard(
        model_name=model_name,
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
        approvals=(
            ModelCardApproval(approver="reviewer-a", role="model-review-board"),
            ModelCardApproval(approver="rollback-reviewer", role="model-risk-owner"),
        ),
    )


from shared.governance import default_model_performance_drift_policy
from shared.governance.decision_policy import DecisionPolicy


def prepare_candidate(
    service: LearningHubService,
    version: str,
    *,
    model_name: str = DEFAULT_MODEL_NAME,
    decision_policy: DecisionPolicy | None = None,
) -> ModelVersion:
    policy = decision_policy or default_model_performance_drift_policy()
    snapshot = service.register_dataset_snapshot(
        dataset_rows(), dataset_snapshot_id=f"{model_name}-training-{version}"
    )
    validation = service.validate_candidate(
        model_name=model_name,
        model_version=version,
        dataset_snapshot_id=snapshot.dataset_snapshot_id,
        metrics={"w4_smape": 0.11, "p80_coverage": 0.82, "normalized_mae": 0.11},
        baseline_metrics={"w4_smape": 0.15, "p80_coverage": 0.78, "normalized_mae": 0.15},
        thresholds=(
            MetricThreshold("w4_smape", max_value=0.12, warning_max_value=0.115),
            MetricThreshold("p80_coverage", min_value=0.80, warning_min_value=0.81),
            MetricThreshold("normalized_mae", max_value=0.35),
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
        decision_policy=policy,
    )
    assert validation.passed
    service.evaluate_backtest(
        model_name=model_name,
        model_version=version,
        dataset_snapshot_id=snapshot.dataset_snapshot_id,
        code_version="abc1234",
        metrics={"w4_smape": 0.11, "p80_coverage": 0.82, "normalized_mae": 0.11},
        baseline_metrics={"w4_smape": 0.15, "p80_coverage": 0.78, "normalized_mae": 0.15},
        thresholds=(
            MetricThreshold("w4_smape", max_value=0.12, warning_max_value=0.115),
            MetricThreshold("p80_coverage", min_value=0.80, warning_min_value=0.81),
            MetricThreshold("normalized_mae", max_value=0.35),
        ),
        decision_policy=policy,
        calibration_summary={"p80_coverage": 0.82},
    )
    return service.register_model_version(
        model_version=model_version(
            version,
            snapshot.dataset_snapshot_id,
            model_name=model_name,
        ),
        model_card=model_card(
            version,
            snapshot.dataset_snapshot_id,
            validation.validation_run_id,
            model_name=model_name,
        ),
        validation_run=validation,
    )


__all__ = [
    "DEFAULT_MODEL_NAME",
    "PREDICTION_TIME",
    "SNAPSHOT_TIME",
    "dataset_rows",
    "model_card",
    "model_version",
    "model_performance_policy_for_model",
    "prepare_candidate",
]


def model_performance_policy_for_model(model_name: str) -> DecisionPolicy:
    """Return the governed fixture policy with explicit rows for ``model_name``.

    Production policy rows are intentionally explicit and fail closed for
    unknown model names. PostgreSQL lifecycle tests use a UUID-suffixed name
    to isolate concurrent runs, so the fixture copies the canonical forecast
    rows into that explicitly requested test model instead of weakening the
    production policy resolver.
    """
    policy = default_model_performance_drift_policy()
    by_model = policy.parameters.get("metric_thresholds_by_model")
    if not isinstance(by_model, dict):
        raise ValueError("model performance policy fixture requires model threshold rows")
    if model_name in by_model:
        return policy
    canonical = by_model.get(DEFAULT_MODEL_NAME)
    if not isinstance(canonical, dict):
        raise ValueError(f"missing canonical threshold rows for {DEFAULT_MODEL_NAME}")
    expanded = dict(by_model)
    expanded[model_name] = {metric_name: dict(config) for metric_name, config in canonical.items()}
    parameters = dict(policy.parameters)
    parameters["metric_thresholds_by_model"] = expanded
    return replace(policy, parameters=parameters)
