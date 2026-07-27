"""Shared Learning Hub release fixtures.

The release suites (in-memory, SQLite, and PostgreSQL) all need the same
model-ready rows, candidate model version, and governed model card, so they live
here instead of being duplicated per storage backend. The model card records two
independent approvers in governance roles, which is what lets a release name an
approver who is not the requester.
"""

from __future__ import annotations

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


def prepare_candidate(
    service: LearningHubService,
    version: str,
    *,
    model_name: str = DEFAULT_MODEL_NAME,
) -> ModelVersion:
    snapshot = service.register_dataset_snapshot(
        dataset_rows(), dataset_snapshot_id=f"{model_name}-training-{version}"
    )
    validation = service.validate_candidate(
        model_name=model_name,
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
    "prepare_candidate",
]
