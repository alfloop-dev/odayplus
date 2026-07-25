from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np

from models.shared_ml import (
    ArtifactKind,
    ArtifactRecord,
    ArtifactStore,
    MetricThreshold,
    ModelVersion,
    SegmentMetric,
    SegmentMetricThreshold,
    ValidationRun,
)
from models.shared_ml.oss_estimators import (
    LoadedOSSEstimator,
    OSSEstimatorError,
    load_estimator_artifact,
    train_oss_estimator,
)
from modules.learninghub import LearningHubService
from pipelines.features import FeaturePipelineArtifact


class TrainingPipelineError(ValueError):
    pass


@dataclass(frozen=True)
class TrainingPipelineResult:
    model_version: ModelVersion
    validation_run: ValidationRun
    model_artifact: ArtifactRecord
    validation_report_artifact: ArtifactRecord
    feature_artifact: FeaturePipelineArtifact
    run_id: str
    created_by: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def accepted(self) -> bool:
        return self.validation_run.passed

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_version": self.model_version.to_dict(),
            "validation_run": self.validation_run.to_dict(),
            "model_artifact": self.model_artifact.to_dict(),
            "validation_report_artifact": self.validation_report_artifact.to_dict(),
            "feature_artifact": self.feature_artifact.to_dict(),
            "run_id": self.run_id,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "accepted": self.accepted,
        }


class TrainingPipelineRunner:
    def __init__(self, *, service: LearningHubService, artifact_store: ArtifactStore) -> None:
        self.service = service
        self.artifact_store = artifact_store

    def load_model_artifact(
        self,
        artifact: ArtifactRecord | str,
    ) -> LoadedOSSEstimator:
        artifact_id = artifact.artifact_id if isinstance(artifact, ArtifactRecord) else artifact
        payload = self.artifact_store.open_artifact(artifact_id)
        if payload is None:
            raise TrainingPipelineError(f"model artifact {artifact_id} is unavailable")
        try:
            return load_estimator_artifact(payload)
        except OSSEstimatorError as exc:
            raise TrainingPipelineError(f"cannot load model artifact {artifact_id}: {exc}") from exc

    def run(
        self,
        *,
        model_name: str,
        model_version: str,
        feature_artifact: FeaturePipelineArtifact,
        label_name: str,
        feature_schema_version: str,
        label_version: str,
        thresholds: Sequence[MetricThreshold],
        segment_thresholds: Sequence[SegmentMetricThreshold] = (),
        segment_field: str | None = None,
        algorithm: str = "deterministic_backtest_regressor",
        actor: str = "system",
        run_id: str | None = None,
        git_sha: str | None = None,
    ) -> TrainingPipelineResult:
        snapshot = self.service.repository.get_dataset_snapshot(
            feature_artifact.dataset_snapshot_id
        )
        if snapshot is None:
            raise TrainingPipelineError(
                f"unknown dataset snapshot {feature_artifact.dataset_snapshot_id}"
            )
        rows = [row for row in snapshot.records if row.is_training_eligible]
        if not rows:
            raise TrainingPipelineError("training pipeline requires training-eligible rows")
        labels = [_label_value(row.labels, label_name) for row in rows]
        feature_rows = [
            {name: row.features.get(name) for name in feature_artifact.feature_names}
            for row in rows
        ]
        try:
            training = train_oss_estimator(
                algorithm=algorithm,
                feature_rows=feature_rows,
                labels=labels,
                feature_names=feature_artifact.feature_names,
            )
        except OSSEstimatorError as exc:
            raise TrainingPipelineError(str(exc)) from exc

        backtest = training.backtest
        metrics = _regression_metrics(
            backtest.labels,
            backtest.predictions,
            backtest.lower_predictions,
            backtest.upper_predictions,
        )
        baseline_lower, baseline_upper = _calibrated_interval(
            backtest.labels,
            backtest.baseline_predictions,
        )
        baseline_metrics = _regression_metrics(
            backtest.labels,
            backtest.baseline_predictions,
            baseline_lower,
            baseline_upper,
        )
        segments = _segment_metrics(
            rows,
            backtest.labels,
            backtest.predictions,
            backtest.lower_predictions,
            backtest.upper_predictions,
            segment_field,
        )
        calibration = _calibration_summary(
            backtest.labels,
            backtest.predictions,
            backtest.lower_predictions,
            backtest.upper_predictions,
        )
        run = run_id or f"training-run-{model_name}-{model_version}"

        model_payload = training.estimator.to_artifact_bytes()
        model_record = self.artifact_store.put_artifact(
            model_name=model_name,
            version=model_version,
            kind=ArtifactKind.MODEL,
            data=model_payload,
            content_type="application/vnd.oday.oss-estimator+zip",
            metadata={
                "dataset_snapshot_id": snapshot.dataset_snapshot_id,
                "feature_artifact_id": feature_artifact.artifact_id,
                "feature_artifact_digest": feature_artifact.content_digest,
                "feature_schema_version": feature_schema_version,
                "label_version": label_version,
                "label_name": label_name,
                "requested_algorithm": algorithm,
                "resolved_algorithm": training.resolved_algorithm,
                "engine": training.estimator.spec.engine,
                "objective": training.estimator.spec.objective,
                "training_record_count": len(rows),
                "backtest_fold_count": len(set(backtest.fold_ids)),
                "run_id": run,
                "created_by": actor,
            },
        )
        validation = self.service.validate_candidate(
            model_name=model_name,
            model_version=model_version,
            dataset_snapshot_id=snapshot.dataset_snapshot_id,
            metrics=metrics,
            baseline_metrics=baseline_metrics,
            thresholds=thresholds,
            segment_metrics=segments,
            segment_thresholds=segment_thresholds,
            calibration_summary=calibration,
        )
        validation_payload = {
            "artifact_type": "validation_report",
            "validation_run": validation.to_dict(),
            "segment_acceptance_gates": [
                {
                    "segment_name": gate.segment_name,
                    "segment_value": gate.segment_value,
                    "metric_name": gate.metric_name,
                    "min_value": gate.min_value,
                    "max_value": gate.max_value,
                }
                for gate in segment_thresholds
            ],
        }
        validation_record = self.artifact_store.put_artifact(
            model_name=model_name,
            version=model_version,
            kind=ArtifactKind.VALIDATION_REPORT,
            data=_canonical_json_bytes(validation_payload),
            content_type="application/json",
            metadata={
                "validation_run_id": validation.validation_run_id,
                "dataset_snapshot_id": snapshot.dataset_snapshot_id,
                "run_id": run,
            },
        )
        version = ModelVersion(
            model_name=model_name,
            version=model_version,
            artifact_uri=model_record.uri,
            dataset_snapshot_id=snapshot.dataset_snapshot_id,
            feature_schema_version=feature_schema_version,
            label_version=label_version,
            metrics=metrics,
            run_id=run,
            git_sha=git_sha,
        )
        return TrainingPipelineResult(
            model_version=version,
            validation_run=validation,
            model_artifact=model_record,
            validation_report_artifact=validation_record,
            feature_artifact=feature_artifact,
            run_id=run,
            created_by=actor,
            created_at=model_record.created_at,
        )


def _label_value(labels: Mapping[str, Any], label_name: str) -> float:
    if label_name not in labels:
        raise TrainingPipelineError(f"label {label_name} missing from training row")
    return float(labels[label_name])


def _regression_metrics(
    labels: Sequence[float],
    predictions: Sequence[float],
    lower_predictions: Sequence[float],
    upper_predictions: Sequence[float],
) -> dict[str, float]:
    actual = np.asarray(labels, dtype=float)
    predicted = np.asarray(predictions, dtype=float)
    lower = np.asarray(lower_predictions, dtype=float)
    upper = np.asarray(upper_predictions, dtype=float)
    if not (actual.shape == predicted.shape == lower.shape == upper.shape):
        raise TrainingPipelineError("metric inputs must have identical shapes")
    absolute_errors = np.abs(actual - predicted)
    denominator = float(np.mean(np.abs(actual)))
    normalized_mae = float(np.mean(absolute_errors)) / denominator if denominator else 0.0
    coverage = float(np.mean((actual >= lower) & (actual <= upper)))
    return {
        "mae": round(float(np.mean(absolute_errors)), 6),
        "rmse": round(float(np.sqrt(np.mean(np.square(actual - predicted)))), 6),
        "normalized_mae": round(normalized_mae, 6),
        "p80_coverage": round(coverage, 6),
        "mean_interval_width": round(float(np.mean(upper - lower)), 6),
    }


def _segment_metrics(
    rows: Sequence[Any],
    labels: Sequence[float],
    predictions: Sequence[float],
    lower_predictions: Sequence[float],
    upper_predictions: Sequence[float],
    segment_field: str | None,
) -> tuple[SegmentMetric, ...]:
    if segment_field is None:
        return ()
    grouped: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        grouped.setdefault(str(row.features.get(segment_field, "unknown")), []).append(index)
    metrics: list[SegmentMetric] = []
    for segment_value, indexes in sorted(grouped.items()):
        metrics.append(
            SegmentMetric(
                segment_name=segment_field,
                segment_value=segment_value,
                metrics=_regression_metrics(
                    [labels[index] for index in indexes],
                    [predictions[index] for index in indexes],
                    [lower_predictions[index] for index in indexes],
                    [upper_predictions[index] for index in indexes],
                ),
                record_count=len(indexes),
            )
        )
    return tuple(metrics)


def _calibration_summary(
    labels: Sequence[float],
    predictions: Sequence[float],
    lower_predictions: Sequence[float],
    upper_predictions: Sequence[float],
) -> dict[str, float]:
    actual = np.asarray(labels, dtype=float)
    predicted = np.asarray(predictions, dtype=float)
    lower = np.asarray(lower_predictions, dtype=float)
    upper = np.asarray(upper_predictions, dtype=float)
    coverage = float(np.mean((actual >= lower) & (actual <= upper)))
    return {
        "mean_label": round(float(np.mean(actual)), 6),
        "mean_prediction": round(float(np.mean(predicted)), 6),
        "mean_bias": round(float(np.mean(predicted - actual)), 6),
        "p80_coverage": round(coverage, 6),
        "calibration_error": round(abs(coverage - 0.8), 6),
    }


def _calibrated_interval(
    labels: Sequence[float],
    predictions: Sequence[float],
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    actual = np.asarray(labels, dtype=float)
    predicted = np.asarray(predictions, dtype=float)
    residuals = np.sort(np.abs(actual - predicted))
    rank = min(len(residuals), int(np.ceil((len(residuals) + 1) * 0.8)))
    radius = float(residuals[rank - 1])
    return (
        tuple(float(value) for value in predicted - radius),
        tuple(float(value) for value in predicted + radius),
    )


def _canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()


__all__ = ["TrainingPipelineError", "TrainingPipelineResult", "TrainingPipelineRunner"]
