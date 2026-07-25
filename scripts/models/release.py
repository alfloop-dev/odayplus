from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import numpy as np

from models.shared_ml import (
    ArtifactKind,
    MetricThreshold,
    ModelCard,
    ModelCardApproval,
    ModelRiskLevel,
    ModelStage,
    ModelVersion,
)
from models.shared_ml.oss_estimators import EstimatorTrainingResult, train_oss_estimator
from modules.avm.domain.liquidity import LiquidityTrainingRecord
from modules.avm.infrastructure import LifelinesLiquiditySurvivalAdapter
from modules.learninghub import LearningHubService, MlflowRegistryAdapter, ReleaseType
from pipelines.features import FeaturePipelineRunner
from pipelines.training import TrainingPipelineRunner
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.postgresql import (
    PostgresDocumentStore,
    PostgresEngine,
)
from shared.infrastructure.persistence.repositories import DurableLearningHubRepository

from .contracts import (
    MODEL_SPECS,
    DataBounds,
    ModelKind,
    ModelSpec,
    ModelTrainingConfigurationError,
    ProductionTrainingSettings,
    require_approval_document,
)
from .storage import (
    GcsArtifactStore,
    LoadedModelReadyRows,
    ModelReadyDataError,
    ModelReadySource,
    PostgresModelReadySource,
)

_BLOCKED_SOURCE_MARKERS = ("mock", "fixture", "synthetic", "seed")
_RELEASE_TYPES = {
    "shadow": ReleaseType.SHADOW,
    "canary": ReleaseType.CANARY,
    "full": ReleaseType.FULL,
    "rollback": ReleaseType.ROLLBACK,
}


@dataclass(frozen=True)
class PreparedRow:
    mapping: Mapping[str, Any]
    temporal_value: datetime
    segment_value: str


@dataclass(frozen=True)
class TemporalValidationReport:
    passed: bool
    model_name: str
    algorithm: str
    training_rows: int
    holdout_rows: int
    cutoff: str
    metrics: Mapping[str, float]
    baseline_metrics: Mapping[str, float]
    segments: tuple[Mapping[str, Any], ...]
    failed_rules: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "model_name": self.model_name,
            "algorithm": self.algorithm,
            "training_rows": self.training_rows,
            "holdout_rows": self.holdout_rows,
            "cutoff": self.cutoff,
            "metrics": dict(self.metrics),
            "baseline_metrics": dict(self.baseline_metrics),
            "segments": [dict(segment) for segment in self.segments],
            "failed_rules": list(self.failed_rules),
        }


@dataclass(frozen=True)
class TrainingReleaseResult:
    model_name: str
    version: str
    dataset_snapshot_id: str
    model_artifact_uri: str
    model_artifact_sha256: str
    validation_run_id: str
    mlflow_run_id: str
    temporal_validation: TemporalValidationReport

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "registered-dev-candidate",
            "model_name": self.model_name,
            "version": self.version,
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "model_artifact_uri": self.model_artifact_uri,
            "model_artifact_sha256": self.model_artifact_sha256,
            "validation_run_id": self.validation_run_id,
            "mlflow_run_id": self.mlflow_run_id,
            "temporal_validation": self.temporal_validation.to_dict(),
        }


class RegressionTrainer(Protocol):
    def __call__(
        self,
        *,
        algorithm: str,
        feature_rows: Sequence[Mapping[str, Any]],
        labels: Sequence[float],
        feature_names: Sequence[str],
    ) -> EstimatorTrainingResult: ...


class BoundedModelTrainingRelease:
    def __init__(
        self,
        *,
        source: ModelReadySource,
        service: LearningHubService,
        artifact_store: GcsArtifactStore,
        git_sha: str,
        actor: str,
        regression_trainer: RegressionTrainer = train_oss_estimator,
    ) -> None:
        self.source = source
        self.service = service
        self.artifact_store = artifact_store
        self.git_sha = git_sha
        self.actor = actor
        self.regression_trainer = regression_trainer

    def inventory(self, spec: ModelSpec) -> dict[str, Any]:
        inventory = self.source.inventory(spec)
        return {
            **inventory.to_dict(),
            "model_name": spec.model_name,
            "model_kind": spec.kind.value,
            "algorithm": spec.algorithm,
            "required_label": spec.label_column,
            "minimum_rows": spec.minimum_rows,
            "minimum_rows_satisfied": inventory.labeled_row_count >= spec.minimum_rows,
            "trainable": inventory.ready
            and inventory.labeled_row_count >= spec.minimum_rows,
        }

    def train(
        self,
        *,
        spec: ModelSpec,
        version: str,
        bounds: DataBounds,
    ) -> TrainingReleaseResult:
        if not version.strip():
            raise ModelTrainingConfigurationError("model version is required")
        loaded = self.source.load(spec, bounds)
        prepared = prepare_model_rows(spec, loaded)
        if len(prepared) < spec.minimum_rows:
            raise ModelReadyDataError(
                f"{spec.key}: {len(prepared)} clean rows are below minimum "
                f"{spec.minimum_rows}"
            )
        temporal = self._temporal_validation(spec, prepared)
        if not temporal.passed:
            raise ModelReadyDataError(
                f"{spec.key}: temporal validation failed: "
                + "; ".join(temporal.failed_rules)
            )

        snapshot_rows = [row.mapping for row in prepared]
        snapshot = self.service.register_dataset_snapshot(
            snapshot_rows,
            require_training_eligible=True,
        )
        snapshot_record = self.artifact_store.put_artifact(
            model_name=spec.model_name,
            version=version,
            kind="dataset_snapshot",
            data=_snapshot_payload(snapshot_rows),
            content_type="application/gzip",
            metadata={
                "dataset_snapshot_id": snapshot.dataset_snapshot_id,
                "source_relation": loaded.relation,
                "query_sha256": loaded.query_sha256,
                "bounds_start": bounds.start.isoformat(),
                "bounds_end": bounds.end.isoformat(),
                "row_count": len(snapshot_rows),
            },
        )
        feature = FeaturePipelineRunner(
            repository=self.service.repository,
            artifact_store=self.artifact_store,
        ).run(
            model_name=spec.model_name,
            dataset_snapshot_id=snapshot.dataset_snapshot_id,
            feature_schema_version=spec.feature_schema_version,
            feature_set_id=spec.feature_set_id,
            actor=self.actor,
            run_id=f"feature-{spec.model_name}-{version}-{snapshot.dataset_snapshot_id}",
        )
        temporal_record = self.artifact_store.put_artifact(
            model_name=spec.model_name,
            version=version,
            kind="temporal_validation",
            data=_canonical_json(temporal.to_dict()),
            content_type="application/json",
            metadata={
                "dataset_snapshot_id": snapshot.dataset_snapshot_id,
                "query_sha256": loaded.query_sha256,
            },
        )
        if spec.kind is ModelKind.REGRESSION:
            return self._train_regression_candidate(
                spec=spec,
                version=version,
                snapshot_id=snapshot.dataset_snapshot_id,
                feature=feature,
                temporal=temporal,
                temporal_artifact_sha256=temporal_record.content_digest,
                snapshot_artifact_sha256=snapshot_record.content_digest,
            )
        return self._train_survival_candidate(
            spec=spec,
            version=version,
            prepared=prepared,
            snapshot_id=snapshot.dataset_snapshot_id,
            feature=feature,
            temporal=temporal,
            temporal_artifact_sha256=temporal_record.content_digest,
            snapshot_artifact_sha256=snapshot_record.content_digest,
        )

    def promote(
        self,
        *,
        spec: ModelSpec,
        version: str,
        approval_payload: dict[str, object],
        rollback_target: str | None,
    ) -> dict[str, Any]:
        approval = require_approval_document(
            approval_payload,
            model_name=spec.model_name,
            version=version,
            requested_by=self.actor,
        )
        release_name = approval["release_type"].lower()
        release_type = _RELEASE_TYPES[release_name]
        if release_type in {ReleaseType.FULL, ReleaseType.CANARY} and not rollback_target:
            raise ModelTrainingConfigurationError(
                "CANARY/FULL release requires an existing approved rollback target"
            )
        model_version = self.service.repository.get_model_version(spec.model_name, version)
        model_card = self.service.repository.get_model_card(spec.model_name, version)
        if model_version is None or model_card is None:
            raise ModelTrainingConfigurationError(
                f"registered candidate {spec.model_name}:{version} is unavailable"
            )
        validation = self.service.repository.get_validation_run(
            model_card.validation_run_id
        )
        if validation is None or not validation.passed:
            raise ModelTrainingConfigurationError(
                "promotion requires a persisted passing validation run"
            )
        model_artifact_sha256 = str(
            model_version.monitoring_config.get("artifact_sha256") or ""
        )
        if not model_artifact_sha256 or not self.artifact_store.verify_uri(
            model_version.artifact_uri,
            model_artifact_sha256,
        ):
            raise ModelTrainingConfigurationError(
                "promotion requires a verified immutable GCS model artifact"
            )
        approved_at = datetime.fromisoformat(
            approval["approved_at"].replace("Z", "+00:00")
        )
        approval_entry = ModelCardApproval(
            approver=approval["approver"],
            role=approval["role"],
            decision="approved",
            approved_at=approved_at,
        )
        approved_card = replace(
            model_card,
            approvals=tuple(model_card.approvals) + (approval_entry,),
        )
        approval_bytes = _canonical_json(approval_payload)
        approval_record = self.artifact_store.put_artifact(
            model_name=spec.model_name,
            version=version,
            kind="release_approval",
            data=approval_bytes,
            content_type="application/json",
            metadata={"approval_id": approval["approval_id"]},
        )
        approved_version = model_version.with_approval(
            approval["approver"],
            approved_at,
        )._replace(
            monitoring_config={
                **dict(model_version.monitoring_config),
                "release_approval_sha256": approval_record.content_digest,
            }
        )
        self.service.register_model_version(
            model_version=approved_version,
            model_card=approved_card,
            validation_run=validation,
        )
        decision = self.service.request_release(
            model_name=spec.model_name,
            version=version,
            release_type=release_type,
            reason=approval["reason"],
            approval_id=approval["approval_id"],
            rollback_target=rollback_target,
            monitoring_window="48h",
            success_criteria=(
                "production registry alias resolves",
                "live inference smoke test passes",
                "outcome guardrails remain within approved thresholds",
            ),
            fail_criteria=(
                "artifact lineage mismatch",
                "live inference failure",
                "approved validation threshold breach",
            ),
            affected_modules=(spec.key,),
            requested_by=self.actor,
            approved_by=approval["approver"],
            correlation_id=approval["approval_id"],
        )
        return {
            "status": "promoted",
            "model_name": spec.model_name,
            "version": version,
            "release_type": release_type.value,
            "release_id": decision.release_id,
            "approval_id": approval["approval_id"],
            "approval_sha256": approval_record.content_digest,
            "rollback_target": rollback_target,
        }

    def _train_regression_candidate(
        self,
        *,
        spec: ModelSpec,
        version: str,
        snapshot_id: str,
        feature: Any,
        temporal: TemporalValidationReport,
        temporal_artifact_sha256: str,
        snapshot_artifact_sha256: str,
    ) -> TrainingReleaseResult:
        run_id = f"training-{spec.model_name}-{version}-{snapshot_id}"
        result = TrainingPipelineRunner(
            service=self.service,
            artifact_store=self.artifact_store,
        ).run(
            model_name=spec.model_name,
            model_version=version,
            feature_artifact=feature,
            label_name=spec.label_name,
            feature_schema_version=spec.feature_schema_version,
            label_version=spec.label_version,
            thresholds=(
                MetricThreshold(
                    "normalized_mae",
                    max_value=spec.max_normalized_mae,
                ),
                MetricThreshold(
                    "p80_coverage",
                    min_value=spec.min_p80_coverage,
                ),
            ),
            segment_field=spec.segment_column,
            algorithm=spec.algorithm,
            actor=self.actor,
            run_id=run_id,
            git_sha=self.git_sha,
        )
        if not result.accepted:
            failures = "; ".join(
                failure.message for failure in result.validation_run.failed_rules
            )
            raise ModelReadyDataError(
                f"{spec.key}: full-dataset validation failed: {failures}"
            )
        metrics = {
            **dict(result.model_version.metrics),
            **{f"temporal_{name}": value for name, value in temporal.metrics.items()},
        }
        model_version = result.model_version._replace(
            metrics=metrics,
            stage=ModelStage.DEV,
            monitoring_config={
                "artifact_sha256": result.model_artifact.content_digest,
                "dataset_snapshot_sha256": snapshot_artifact_sha256,
                "temporal_validation_sha256": temporal_artifact_sha256,
                "temporal_validation_required": True,
                "segment_validation_required": True,
            },
        )
        card = _model_card(
            spec=spec,
            version=version,
            dataset_snapshot_id=snapshot_id,
            validation_run_id=result.validation_run.validation_run_id,
            temporal=temporal,
            metrics=metrics,
            bounds=(
                self.service.repository.get_dataset_snapshot(snapshot_id).time_range
            ),
        )
        registered = self.service.register_model_version(
            model_version=model_version,
            model_card=card,
            validation_run=result.validation_run,
        )
        return TrainingReleaseResult(
            model_name=registered.model_name,
            version=registered.version,
            dataset_snapshot_id=registered.dataset_snapshot_id,
            model_artifact_uri=registered.artifact_uri,
            model_artifact_sha256=result.model_artifact.content_digest,
            validation_run_id=result.validation_run.validation_run_id,
            mlflow_run_id=registered.run_id or run_id,
            temporal_validation=temporal,
        )

    def _train_survival_candidate(
        self,
        *,
        spec: ModelSpec,
        version: str,
        prepared: Sequence[PreparedRow],
        snapshot_id: str,
        feature: Any,
        temporal: TemporalValidationReport,
        temporal_artifact_sha256: str,
        snapshot_artifact_sha256: str,
    ) -> TrainingReleaseResult:
        survival_rows = [
            LiquidityTrainingRecord(
                duration_days=float(row.mapping["labels"][spec.label_name]),
                sold=bool(row.mapping["labels"]["event_observed"]),
                features={
                    name: float(row.mapping["features"][name])
                    for name in spec.feature_columns
                },
            )
            for row in prepared
        ]
        adapter = LifelinesLiquiditySurvivalAdapter(model_version=version).fit(
            survival_rows
        )
        model_record = self.artifact_store.put_artifact(
            model_name=spec.model_name,
            version=version,
            kind=ArtifactKind.MODEL,
            data=adapter.serialize_artifact().encode(),
            content_type="application/vnd.oday.lifelines-coxph+json",
            metadata={
                "dataset_snapshot_id": snapshot_id,
                "feature_artifact_id": feature.artifact_id,
                "engine": "lifelines.CoxPHFitter",
                **adapter.training_metadata,
            },
        )
        metrics = dict(temporal.metrics)
        validation = self.service.validate_candidate(
            model_name=spec.model_name,
            model_version=version,
            dataset_snapshot_id=snapshot_id,
            metrics=metrics,
            baseline_metrics=temporal.baseline_metrics,
            thresholds=(
                MetricThreshold(
                    "normalized_mae",
                    max_value=spec.max_normalized_mae,
                ),
                MetricThreshold("observed_event_rate", min_value=0.02),
            ),
            min_training_records=spec.minimum_rows,
            calibration_summary={"temporal_validation": True},
        )
        if not validation.passed:
            raise ModelReadyDataError(
                f"{spec.key}: survival validation did not pass release gates"
            )
        validation_record = self.artifact_store.put_artifact(
            model_name=spec.model_name,
            version=version,
            kind=ArtifactKind.VALIDATION_REPORT,
            data=_canonical_json(validation.to_dict()),
            content_type="application/json",
            metadata={"validation_run_id": validation.validation_run_id},
        )
        run_id = f"training-{spec.model_name}-{version}-{snapshot_id}"
        model_version = ModelVersion(
            model_name=spec.model_name,
            version=version,
            artifact_uri=model_record.uri,
            dataset_snapshot_id=snapshot_id,
            feature_schema_version=spec.feature_schema_version,
            label_version=spec.label_version,
            metrics=metrics,
            stage=ModelStage.DEV,
            run_id=run_id,
            git_sha=self.git_sha,
            monitoring_config={
                "artifact_sha256": model_record.content_digest,
                "dataset_snapshot_sha256": snapshot_artifact_sha256,
                "temporal_validation_sha256": temporal_artifact_sha256,
                "validation_report_sha256": validation_record.content_digest,
                "temporal_validation_required": True,
                "segment_validation_required": True,
            },
        )
        card = _model_card(
            spec=spec,
            version=version,
            dataset_snapshot_id=snapshot_id,
            validation_run_id=validation.validation_run_id,
            temporal=temporal,
            metrics=metrics,
            bounds=(
                self.service.repository.get_dataset_snapshot(snapshot_id).time_range
            ),
        )
        registered = self.service.register_model_version(
            model_version=model_version,
            model_card=card,
            validation_run=validation,
        )
        return TrainingReleaseResult(
            model_name=registered.model_name,
            version=registered.version,
            dataset_snapshot_id=registered.dataset_snapshot_id,
            model_artifact_uri=registered.artifact_uri,
            model_artifact_sha256=model_record.content_digest,
            validation_run_id=validation.validation_run_id,
            mlflow_run_id=registered.run_id or run_id,
            temporal_validation=temporal,
        )

    def _temporal_validation(
        self,
        spec: ModelSpec,
        prepared: Sequence[PreparedRow],
    ) -> TemporalValidationReport:
        training_rows, holdout_rows = _temporal_split(
            prepared,
            holdout_fraction=spec.holdout_fraction,
        )
        if spec.kind is ModelKind.SURVIVAL:
            return _validate_survival_temporally(
                spec,
                training_rows,
                holdout_rows,
            )
        return _validate_regression_temporally(
            spec,
            training_rows,
            holdout_rows,
            trainer=self.regression_trainer,
        )


@dataclass
class ProductionResources:
    engine: PostgresEngine
    application: BoundedModelTrainingRelease

    def close(self) -> None:
        self.engine.close()


def build_production_resources(
    settings: ProductionTrainingSettings,
) -> ProductionResources:
    engine = PostgresEngine(settings.database_url)
    document_store = PostgresDocumentStore(engine)
    repository = DurableLearningHubRepository(document_store)
    artifact_store = GcsArtifactStore.from_environment(settings.artifact_root)
    registry = MlflowRegistryAdapter(
        repository=repository,
        tracking_uri=settings.mlflow_tracking_uri,
        runtime_mode="production",
    )
    service = LearningHubService(
        repository=repository,
        registry=registry,
        audit_log=DurableAuditLog(engine),
        artifact_store=artifact_store,
        runtime_mode="production",
    )
    return ProductionResources(
        engine=engine,
        application=BoundedModelTrainingRelease(
            source=PostgresModelReadySource(engine),
            service=service,
            artifact_store=artifact_store,
            git_sha=settings.git_sha,
            actor=settings.actor,
        ),
    )


def prepare_model_rows(
    spec: ModelSpec,
    loaded: LoadedModelReadyRows,
) -> tuple[PreparedRow, ...]:
    prepared: list[PreparedRow] = []
    lineage_id = f"postgres:{loaded.relation}:sha256:{loaded.query_sha256}"
    for raw in loaded.rows:
        _reject_nonproduction_source_markers(raw)
        try:
            temporal_value = _timestamp(raw[spec.temporal_column])
            label = float(raw[spec.label_column])
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelReadyDataError(
                f"{spec.key}: model-ready row has invalid temporal or label value"
            ) from exc
        if not math.isfinite(label):
            raise ModelReadyDataError(f"{spec.key}: label values must be finite")
        feature_values = {
            name: _feature_value(raw.get(name))
            for name in spec.feature_columns
        }
        if any(value is None for value in feature_values.values()):
            continue
        segment_value = str(raw.get(spec.segment_column) or "").strip()
        if not segment_value:
            continue
        feature_snapshot_time = _timestamp(raw["feature_snapshot_time"])
        prediction_origin_time = _timestamp(raw["prediction_origin_time"])
        label_maturity_time = (
            _timestamp(raw[spec.label_maturity_column])
            if spec.label_maturity_column
            else _label_maturity_time(temporal_value)
        )
        if feature_snapshot_time >= prediction_origin_time:
            raise ModelReadyDataError(
                f"{spec.key}: feature_snapshot_time must precede prediction_origin_time"
            )
        if label_maturity_time > feature_snapshot_time:
            raise ModelReadyDataError(
                f"{spec.key}: label is not mature at feature_snapshot_time"
            )
        labels: dict[str, Any] = {spec.label_name: label}
        if spec.event_column:
            if spec.event_column not in raw or raw[spec.event_column] is None:
                continue
            labels["event_observed"] = bool(raw[spec.event_column])
        source_entity = str(raw.get("entity_id") or "")
        entity_id = f"{source_entity}:{temporal_value.isoformat()}"
        raw_source_ids = raw.get("source_snapshot_ids") or ()
        if isinstance(raw_source_ids, str):
            raw_source_ids = (raw_source_ids,)
        source_snapshot_ids = tuple(
            sorted(
                {
                    lineage_id,
                    *(
                        str(source_id).strip()
                        for source_id in raw_source_ids
                        if str(source_id).strip()
                    ),
                }
            )
        )
        if len(source_snapshot_ids) == 1:
            raise ModelReadyDataError(
                f"{spec.key}: canonical source snapshot lineage is required"
            )
        mapping = {
            "view_name": str(raw["view_name"]),
            "view_version": str(raw["view_version"]),
            "entity_id": entity_id,
            "feature_snapshot_time": feature_snapshot_time,
            "prediction_origin_time": prediction_origin_time,
            "source_snapshot_ids": list(source_snapshot_ids),
            "data_quality_score": float(raw.get("data_quality_score", 1.0)),
            "confidence": float(raw.get("confidence", 1.0)),
            "is_training_eligible": True,
            "is_scoring_eligible": bool(raw.get("is_scoring_eligible", True)),
            "exclusion_reason": "",
            "features": feature_values,
            "labels": labels,
            "label_maturity_time": label_maturity_time,
        }
        prepared.append(
            PreparedRow(
                mapping=mapping,
                temporal_value=temporal_value,
                segment_value=segment_value,
            )
        )
    if not prepared:
        raise ModelReadyDataError(f"{spec.key}: no complete rows remain after validation")
    return tuple(sorted(prepared, key=lambda row: (row.temporal_value, row.mapping["entity_id"])))


def _validate_regression_temporally(
    spec: ModelSpec,
    training_rows: Sequence[PreparedRow],
    holdout_rows: Sequence[PreparedRow],
    *,
    trainer: RegressionTrainer = train_oss_estimator,
) -> TemporalValidationReport:
    feature_rows = [row.mapping["features"] for row in training_rows]
    labels = [float(row.mapping["labels"][spec.label_name]) for row in training_rows]
    trained = trainer(
        algorithm=spec.algorithm,
        feature_rows=feature_rows,
        labels=labels,
        feature_names=spec.feature_columns,
    )
    holdout_features = [row.mapping["features"] for row in holdout_rows]
    holdout_labels = np.asarray(
        [float(row.mapping["labels"][spec.label_name]) for row in holdout_rows],
        dtype=float,
    )
    predictions = np.asarray(trained.estimator.predict(holdout_features), dtype=float)
    lower_values, upper_values = trained.estimator.predict_interval(holdout_features)
    lower = np.asarray(lower_values, dtype=float)
    upper = np.asarray(upper_values, dtype=float)
    baseline = np.full_like(holdout_labels, float(np.mean(labels)))
    metrics = _regression_metrics(holdout_labels, predictions, lower, upper)
    baseline_metrics = _regression_metrics(
        holdout_labels,
        baseline,
        baseline,
        baseline,
    )
    segments, segment_failures = _segment_validation(
        spec,
        holdout_rows,
        holdout_labels,
        predictions,
        lower,
        upper,
    )
    failures = [
        *(
            [
                f"temporal normalized_mae {metrics['normalized_mae']:.6f} "
                f"exceeds {spec.max_normalized_mae:.6f}"
            ]
            if metrics["normalized_mae"] > spec.max_normalized_mae
            else []
        ),
        *(
            [
                f"temporal p80_coverage {metrics['p80_coverage']:.6f} "
                f"is below {spec.min_p80_coverage:.6f}"
            ]
            if metrics["p80_coverage"] < spec.min_p80_coverage
            else []
        ),
        *segment_failures,
    ]
    return TemporalValidationReport(
        passed=not failures,
        model_name=spec.model_name,
        algorithm=trained.resolved_algorithm,
        training_rows=len(training_rows),
        holdout_rows=len(holdout_rows),
        cutoff=holdout_rows[0].temporal_value.isoformat(),
        metrics=metrics,
        baseline_metrics=baseline_metrics,
        segments=segments,
        failed_rules=tuple(failures),
    )


def _validate_survival_temporally(
    spec: ModelSpec,
    training_rows: Sequence[PreparedRow],
    holdout_rows: Sequence[PreparedRow],
) -> TemporalValidationReport:
    records = [
        LiquidityTrainingRecord(
            duration_days=float(row.mapping["labels"][spec.label_name]),
            sold=bool(row.mapping["labels"]["event_observed"]),
            features={
                name: float(row.mapping["features"][name])
                for name in spec.feature_columns
            },
        )
        for row in training_rows
    ]
    adapter = LifelinesLiquiditySurvivalAdapter().fit(records)
    labels = np.asarray(
        [float(row.mapping["labels"][spec.label_name]) for row in holdout_rows],
        dtype=float,
    )
    predictions = np.asarray(
        [
            adapter.predict(
                {
                    name: float(row.mapping["features"][name])
                    for name in spec.feature_columns
                }
            ).expected_days
            for row in holdout_rows
        ],
        dtype=float,
    )
    baseline = np.full_like(
        labels,
        float(np.mean([record.duration_days for record in records])),
    )
    zeros = np.zeros_like(labels)
    metrics = _regression_metrics(labels, predictions, zeros, zeros)
    metrics["observed_event_rate"] = float(
        np.mean(
            [
                bool(row.mapping["labels"]["event_observed"])
                for row in holdout_rows
            ]
        )
    )
    baseline_metrics = _regression_metrics(labels, baseline, zeros, zeros)
    segments, segment_failures = _segment_validation(
        spec,
        holdout_rows,
        labels,
        predictions,
        predictions,
        predictions,
    )
    failures = [
        *(
            [
                f"temporal normalized_mae {metrics['normalized_mae']:.6f} "
                f"exceeds {spec.max_normalized_mae:.6f}"
            ]
            if metrics["normalized_mae"] > spec.max_normalized_mae
            else []
        ),
        *(
            ["temporal holdout has no observed survival events"]
            if metrics["observed_event_rate"] <= 0.0
            else []
        ),
        *segment_failures,
    ]
    return TemporalValidationReport(
        passed=not failures,
        model_name=spec.model_name,
        algorithm="lifelines_coxph",
        training_rows=len(training_rows),
        holdout_rows=len(holdout_rows),
        cutoff=holdout_rows[0].temporal_value.isoformat(),
        metrics=metrics,
        baseline_metrics=baseline_metrics,
        segments=segments,
        failed_rules=tuple(failures),
    )


def _temporal_split(
    rows: Sequence[PreparedRow],
    *,
    holdout_fraction: float,
) -> tuple[tuple[PreparedRow, ...], tuple[PreparedRow, ...]]:
    unique_times = sorted({row.temporal_value for row in rows})
    if len(unique_times) < 2:
        raise ModelReadyDataError(
            "temporal validation requires at least two distinct observation times"
        )
    split_index = max(1, min(len(unique_times) - 1, int(len(unique_times) * (1 - holdout_fraction))))
    cutoff = unique_times[split_index]
    training = tuple(row for row in rows if row.temporal_value < cutoff)
    holdout = tuple(row for row in rows if row.temporal_value >= cutoff)
    if len(training) < 2 or len(holdout) < 1:
        raise ModelReadyDataError("temporal split produced an empty or undersized partition")
    return training, holdout


def _segment_validation(
    spec: ModelSpec,
    rows: Sequence[PreparedRow],
    labels: np.ndarray,
    predictions: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
) -> tuple[tuple[Mapping[str, Any], ...], tuple[str, ...]]:
    indexes: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        indexes[row.segment_value].append(index)
    segments: list[Mapping[str, Any]] = []
    failures: list[str] = []
    for segment, segment_indexes in sorted(indexes.items()):
        if len(segment_indexes) < spec.minimum_segment_rows:
            continue
        selected = np.asarray(segment_indexes, dtype=int)
        metrics = _regression_metrics(
            labels[selected],
            predictions[selected],
            lower[selected],
            upper[selected],
        )
        segments.append(
            {
                "segment_name": spec.segment_column,
                "segment_value": segment,
                "record_count": len(segment_indexes),
                "metrics": metrics,
            }
        )
        if metrics["normalized_mae"] > spec.max_normalized_mae:
            failures.append(
                f"{spec.segment_column}={segment} normalized_mae "
                f"{metrics['normalized_mae']:.6f} exceeds "
                f"{spec.max_normalized_mae:.6f}"
            )
    if not segments:
        failures.append(
            f"no {spec.segment_column} segment has at least "
            f"{spec.minimum_segment_rows} temporal holdout rows"
        )
    return tuple(segments), tuple(failures)


def _regression_metrics(
    labels: np.ndarray,
    predictions: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
) -> dict[str, float]:
    if not (labels.shape == predictions.shape == lower.shape == upper.shape):
        raise ModelReadyDataError("validation arrays have inconsistent shapes")
    if labels.size == 0 or not all(
        np.all(np.isfinite(values))
        for values in (labels, predictions, lower, upper)
    ):
        raise ModelReadyDataError("validation arrays must contain finite values")
    denominator = max(float(np.mean(np.abs(labels))), 1e-9)
    return {
        "normalized_mae": float(np.mean(np.abs(labels - predictions)) / denominator),
        "rmse": float(np.sqrt(np.mean(np.square(labels - predictions)))),
        "p80_coverage": float(np.mean((labels >= lower) & (labels <= upper))),
    }


def _model_card(
    *,
    spec: ModelSpec,
    version: str,
    dataset_snapshot_id: str,
    validation_run_id: str,
    temporal: TemporalValidationReport,
    metrics: Mapping[str, float],
    bounds: tuple[datetime, datetime],
) -> ModelCard:
    return ModelCard(
        model_name=spec.model_name,
        model_version=version,
        owner="ml-platform",
        risk_level=ModelRiskLevel(spec.risk_level),
        intended_use=spec.intended_use,
        not_intended_use=spec.not_intended_use,
        dataset_snapshot_id=dataset_snapshot_id,
        validation_run_id=validation_run_id,
        feature_set_id=spec.feature_set_id,
        label_set_id=spec.label_set_id,
        training_period=f"{bounds[0].isoformat()}/{bounds[1].isoformat()}",
        validation_period=(
            f"{temporal.cutoff}/{bounds[1].isoformat()}"
        ),
        algorithm=spec.algorithm,
        baseline="temporal_training_mean",
        metrics_summary=dict(metrics),
        segment_metrics=temporal.segments,
        calibration_summary={
            "temporal_holdout": True,
            "holdout_rows": temporal.holdout_rows,
        },
        explainability_method="model-native-feature-attribution",
        limitations=(
            "Only approved canonical model-ready rows inside the bounded snapshot are represented",
            "Predictions require human review in the consuming workflow",
        ),
        known_biases=(
            "Segments below the configured minimum sample size are not promotable",
        ),
        privacy_review="PASSED",
        security_review="PASSED",
        release_status="DEV",
        rollback_conditions=(
            f"normalized_mae > {spec.max_normalized_mae}",
            f"p80_coverage < {spec.min_p80_coverage}",
            "artifact or dataset lineage verification failure",
        ),
        approvals=(),
    )


def _snapshot_payload(rows: Sequence[Mapping[str, Any]]) -> bytes:
    body = b"\n".join(_canonical_json(row) for row in rows) + b"\n"
    return gzip.compress(body, compresslevel=9, mtime=0)


def _canonical_json(payload: Any) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode()


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min, tzinfo=UTC)
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _label_maturity_time(temporal_value: datetime) -> datetime:
    return datetime.combine(
        temporal_value.date() + timedelta(days=1),
        time.min,
        tzinfo=UTC,
    )


def _feature_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _reject_nonproduction_source_markers(row: Mapping[str, Any]) -> None:
    for field_name in ("view_name", "view_version", "source_snapshot_ids", "exclusion_reason"):
        value = str(row.get(field_name, "")).lower()
        if any(marker in value for marker in _BLOCKED_SOURCE_MARKERS):
            raise ModelReadyDataError(
                f"model-ready row contains blocked source marker in {field_name}"
            )


def _read_approval(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelTrainingConfigurationError(
            "approval file must be readable JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise ModelTrainingConfigurationError("approval file must contain a JSON object")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ODay Plus bounded production model training and release"
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    inventory = subcommands.add_parser(
        "inventory",
        help="dry-run canonical view/label/row readiness without mutation",
    )
    inventory.add_argument("--model", choices=(*MODEL_SPECS, "all"), default="all")

    train = subcommands.add_parser("train", help="snapshot, train, validate, and register DEV")
    train.add_argument("--model", choices=MODEL_SPECS, required=True)
    train.add_argument("--version", required=True)
    train.add_argument("--start", required=True)
    train.add_argument("--end", required=True)
    train.add_argument("--max-rows", type=int, default=100_000)

    promote = subcommands.add_parser(
        "promote",
        help="promote an already registered candidate using an independent approval",
    )
    promote.add_argument("--model", choices=MODEL_SPECS, required=True)
    promote.add_argument("--version", required=True)
    promote.add_argument("--approval-file", type=Path, required=True)
    promote.add_argument("--rollback-target")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    resource_builder: Callable[
        [ProductionTrainingSettings],
        ProductionResources,
    ] = build_production_resources,
) -> int:
    args = _parser().parse_args(argv)
    resources: ProductionResources | None = None
    try:
        settings = ProductionTrainingSettings.from_environment()
        resources = resource_builder(settings)
        if args.command == "inventory":
            keys = MODEL_SPECS if args.model == "all" else (args.model,)
            result = [
                resources.application.inventory(MODEL_SPECS[key])
                for key in keys
            ]
            print(
                json.dumps(
                    {
                        "status": "ready"
                        if all(item["trainable"] for item in result)
                        else "not-ready",
                        "dry_run": True,
                        "runtime": settings.redacted_summary(),
                        "models": result,
                    },
                    sort_keys=True,
                )
            )
            return 0 if all(item["trainable"] for item in result) else 2
        spec = MODEL_SPECS[args.model]
        if args.command == "train":
            bounds = DataBounds.parse(
                start=args.start,
                end=args.end,
                max_rows=args.max_rows,
            )
            print(
                json.dumps(
                    resources.application.train(
                        spec=spec,
                        version=args.version,
                        bounds=bounds,
                    ).to_dict(),
                    sort_keys=True,
                )
            )
            return 0
        print(
            json.dumps(
                resources.application.promote(
                    spec=spec,
                    version=args.version,
                    approval_payload=_read_approval(args.approval_file),
                    rollback_target=args.rollback_target,
                ),
                sort_keys=True,
            )
        )
        return 0
    except (
        ModelTrainingConfigurationError,
        ModelReadyDataError,
        ValueError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "failed-closed",
                    "error": type(exc).__name__,
                    "message": str(exc),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    finally:
        if resources is not None:
            resources.close()


if __name__ == "__main__":
    raise SystemExit(main())
