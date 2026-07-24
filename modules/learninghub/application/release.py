from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any
from uuid import uuid4

from models.shared_ml import (
    ArtifactKind,
    ArtifactStore,
    FeatureDefinition,
    FeatureSet,
    InMemoryArtifactStore,
    LabelDefinition,
    LabelSet,
    LocalModelArtifactStore,
    SegmentMetricThreshold,
)
from models.shared_ml.artifact_store import compute_content_digest
from models.shared_ml.model_card import ModelCard
from models.shared_ml.registry import ModelAlias, ModelRegistryError, ModelStage, ModelVersion
from models.shared_ml.validation import (
    MetricThreshold,
    SegmentMetric,
    ValidationRun,
    ValidationStatus,
    validate_model_candidate,
)
from modules.learninghub.application.monitor import (
    MonitorStatus,
    RecommendedAction,
    ReleaseMonitorAssessment,
    evaluate_guardrails,
    utcnow,
)
from modules.learninghub.domain import (
    DatasetSnapshot,
    InferenceComparison,
    InferenceComparisonMode,
    InferenceDelta,
    InferencePrediction,
    MonitoringBreach,
    MonitoringEvaluation,
    MonitoringSignalType,
    RetrainingRequest,
    build_dataset_snapshot,
)
from modules.learninghub.infrastructure import (
    InMemoryLearningHubRepository,
    LearningHubRepository,
    MlflowRegistryAdapter,
)
from modules.learninghub.runtime import (
    LearningHubRuntimeConfigurationError,
    learninghub_production_required,
)
from shared.audit import AuditEvent, InMemoryAuditLog


class LearningHubError(ValueError):
    pass


class ReleaseType(StrEnum):
    SHADOW = "SHADOW"
    CANARY = "CANARY"
    FULL = "FULL"
    ROLLBACK = "ROLLBACK"


@dataclass(frozen=True)
class ModelReleaseDecision:
    release_id: str
    model_name: str
    from_version: str | None
    to_version: str
    release_type: ReleaseType
    reason: str
    approval_id: str
    rollback_target: str | None
    monitoring_window: str
    success_criteria: tuple[str, ...]
    fail_criteria: tuple[str, ...]
    affected_modules: tuple[str, ...] = ()
    requested_by: str = "system"
    approved_by: str = "model-review-board"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    audit_event_id: str | None = None
    dataset_snapshot_id: str | None = None
    feature_schema_version: str | None = None
    label_version: str | None = None
    model_card_checksum: str | None = None
    model_artifact_uri: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "release_id": self.release_id,
            "model_name": self.model_name,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "release_type": self.release_type.value,
            "reason": self.reason,
            "approval_id": self.approval_id,
            "rollback_target": self.rollback_target,
            "monitoring_window": self.monitoring_window,
            "success_criteria": list(self.success_criteria),
            "fail_criteria": list(self.fail_criteria),
            "affected_modules": list(self.affected_modules),
            "requested_by": self.requested_by,
            "approved_by": self.approved_by,
            "created_at": self.created_at.isoformat(),
            "audit_event_id": self.audit_event_id,
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "feature_schema_version": self.feature_schema_version,
            "label_version": self.label_version,
            "model_card_checksum": self.model_card_checksum,
            "model_artifact_uri": self.model_artifact_uri,
        }


class LearningHubService:
    def __init__(
        self,
        *,
        repository: LearningHubRepository | None = None,
        registry: MlflowRegistryAdapter | None = None,
        audit_log: InMemoryAuditLog | None = None,
        artifact_store: ArtifactStore | LocalModelArtifactStore | None = None,
        runtime_mode: str | None = None,
    ) -> None:
        self.production_required = learninghub_production_required(runtime_mode)
        if self.production_required and (
            repository is None or isinstance(repository, InMemoryLearningHubRepository)
        ):
            raise LearningHubRuntimeConfigurationError(
                "Learning Hub production requires an injected durable repository"
            )
        if self.production_required and (
            audit_log is None or isinstance(audit_log, InMemoryAuditLog)
        ):
            raise LearningHubRuntimeConfigurationError(
                "Learning Hub production requires an injected durable audit log"
            )
        if self.production_required and (
            artifact_store is None
            or isinstance(
                artifact_store,
                (InMemoryArtifactStore, LocalModelArtifactStore),
            )
        ):
            raise LearningHubRuntimeConfigurationError(
                "Learning Hub production requires an injected durable artifact store"
            )
        self.repository = repository or InMemoryLearningHubRepository()
        self.registry = registry or MlflowRegistryAdapter(
            self.repository,
            runtime_mode=runtime_mode,
        )
        if self.production_required:
            self.registry.require_production_binding()
        self.audit_log = audit_log or InMemoryAuditLog()
        self.artifact_store = artifact_store or LocalModelArtifactStore()

    def register_dataset_snapshot(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        dataset_snapshot_id: str | None = None,
        require_training_eligible: bool = True,
        feature_set_id: str | None = None,
        label_set_id: str | None = None,
    ) -> DatasetSnapshot:
        if feature_set_id:
            feature_set = self.repository.get_feature_set(feature_set_id)
            if feature_set is None:
                raise LearningHubError(f"unknown feature set {feature_set_id}")
            allowed_features = set()
            for f in feature_set.features:
                name = f.split("@")[0] if "@" in f else f
                allowed_features.add(name)
            for f in feature_set.features:
                name = f.split("@")[0] if "@" in f else f
                version = f.split("@")[1] if "@" in f else None
                feat_def = self.repository.get_feature(name, version)
                if feat_def and feat_def.status == "BLOCKED":
                    raise LearningHubError(f"feature {name} is BLOCKED and cannot be used")
        else:
            allowed_features = None

        if label_set_id:
            label_set = self.repository.get_label_set(label_set_id)
            if label_set is None:
                raise LearningHubError(f"unknown label set {label_set_id}")
            allowed_labels = set()
            for lbl in label_set.labels:
                name = lbl.split("@")[0] if "@" in lbl else lbl
                allowed_labels.add(name)
            for lbl in label_set.labels:
                name = lbl.split("@")[0] if "@" in lbl else lbl
                version = lbl.split("@")[1] if "@" in lbl else None
                lbl_def = self.repository.get_label(name, version)
                if lbl_def and lbl_def.status == "BLOCKED":
                    raise LearningHubError(f"label {name} is BLOCKED and cannot be used")
        else:
            allowed_labels = None

        system_features = {"event_time", "observation_time", "available_from", "available_to"}
        for row in rows:
            row_features = row.get("features", {})
            for key in row_features:
                if key in system_features:
                    continue
                if allowed_features is not None and key not in allowed_features:
                    raise LearningHubError(
                        f"feature {key} in dataset is not allowed by feature set {feature_set_id}"
                    )

            row_labels = row.get("labels", {})
            for key in row_labels:
                if allowed_labels is not None and key not in allowed_labels:
                    raise LearningHubError(
                        f"label {key} in dataset is not allowed by label set {label_set_id}"
                    )

        snapshot = build_dataset_snapshot(
            rows,
            dataset_snapshot_id=dataset_snapshot_id,
            require_training_eligible=require_training_eligible,
            feature_set_id=feature_set_id,
            label_set_id=label_set_id,
        )
        return self.repository.save_dataset_snapshot(snapshot)

    def validate_candidate(
        self,
        *,
        model_name: str,
        model_version: str,
        dataset_snapshot_id: str,
        metrics: Mapping[str, float],
        baseline_metrics: Mapping[str, float],
        thresholds: Sequence[MetricThreshold],
        segment_metrics: Sequence[SegmentMetric] = (),
        segment_thresholds: Sequence[SegmentMetricThreshold] = (),
        calibration_summary: Mapping[str, Any] | None = None,
        min_training_records: int = 1,
    ) -> ValidationRun:
        snapshot = self.repository.get_dataset_snapshot(dataset_snapshot_id)
        if snapshot is None:
            raise LearningHubError(f"unknown dataset snapshot {dataset_snapshot_id}")
        validation_run = validate_model_candidate(
            model_name=model_name,
            model_version=model_version,
            dataset_snapshot=snapshot,
            metrics=metrics,
            baseline_metrics=baseline_metrics,
            thresholds=thresholds,
            segment_metrics=segment_metrics,
            segment_thresholds=segment_thresholds,
            calibration_summary=calibration_summary,
            min_training_records=min_training_records,
        )
        return self.repository.save_validation_run(validation_run)

    def register_model_version(
        self,
        *,
        model_version: ModelVersion,
        model_card: ModelCard,
        validation_run: ValidationRun,
    ) -> ModelVersion:
        if self.production_required:
            self.registry.validate_production_model_version(model_version)
        if validation_run.dataset_snapshot_id != model_version.dataset_snapshot_id:
            raise LearningHubError("validation run dataset does not match model version")
        if model_card.validation_run_id != validation_run.validation_run_id:
            raise LearningHubError("model card validation run does not match")
        if model_card.dataset_snapshot_id != model_version.dataset_snapshot_id:
            raise LearningHubError("model card dataset does not match model version")
        self.repository.save_validation_run(validation_run)
        self.repository.save_model_card(model_card)

        if isinstance(self.artifact_store, LocalModelArtifactStore):
            self.artifact_store.save_model_card(
                model_card,
                artifact_uri=model_version.artifact_uri,
            )
        else:
            card_bytes = json.dumps(
                model_card.to_dict(),
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
            record = self.artifact_store.put_artifact(
                model_name=model_version.model_name,
                version=model_version.version,
                kind=ArtifactKind.MODEL_CARD,
                data=card_bytes,
                content_type="application/json",
                metadata={
                    "dataset_snapshot_id": model_version.dataset_snapshot_id,
                    "validation_run_id": validation_run.validation_run_id,
                },
            )
            if not self.artifact_store.verify(record.artifact_id):
                raise LearningHubError("model card artifact verification failed")

        return self.registry.register_model_version(model_version)

    def request_release(
        self,
        *,
        model_name: str,
        version: str,
        release_type: ReleaseType,
        reason: str,
        approval_id: str,
        rollback_target: str | None,
        monitoring_window: str,
        success_criteria: Sequence[str],
        fail_criteria: Sequence[str],
        affected_modules: Sequence[str] = (),
        requested_by: str = "system",
        approved_by: str = "model-review-board",
        correlation_id: str = "learninghub-release",
    ) -> ModelReleaseDecision:
        model_version = self._require_model_version(model_name, version)
        model_card = self._require_model_card(model_name, version)
        validation_run = self._require_validation_run(model_card.validation_run_id)
        self._assert_release_gate(
            model_version=model_version,
            model_card=model_card,
            validation_run=validation_run,
            release_type=release_type,
            approval_id=approval_id,
            rollback_target=rollback_target,
        )

        current_production = self.repository.get_alias(model_name, ModelAlias.PRODUCTION)
        from_version = current_production.version if current_production else None
        target_stage = _stage_for_release_type(release_type)

        if release_type is ReleaseType.FULL:
            if current_production and current_production.version != version:
                self.registry.transition_stage(
                    model_name=model_name,
                    version=current_production.version,
                    stage=ModelStage.RETIRED,
                )
                self.repository.set_alias(
                    model_name, ModelAlias.PREVIOUS_PRODUCTION, current_production.version
                )
            promoted = self.registry.transition_stage(
                model_name=model_name, version=version, stage=target_stage
            )
            self.repository.save_model_version(
                promoted._replace(rollback_target=rollback_target).with_approval(approved_by)
            )
            self.repository.set_alias(model_name, ModelAlias.PRODUCTION, version)
            self.repository.set_alias(model_name, ModelAlias.CHAMPION, version)
        elif release_type is ReleaseType.ROLLBACK:
            target = rollback_target or version
            target_version = self._require_model_version(model_name, target)
            current = self._require_model_version(model_name, version)
            self.repository.save_model_version(current.with_stage(ModelStage.ROLLED_BACK))
            self.repository.save_model_version(target_version.with_stage(ModelStage.PRODUCTION))
            self.repository.set_alias(model_name, ModelAlias.PRODUCTION, target_version.version)
            self.repository.set_alias(model_name, ModelAlias.CHAMPION, target_version.version)
            self.repository.clear_alias(model_name, ModelAlias.PREVIOUS_PRODUCTION)
        else:
            alias = ModelAlias.SHADOW if release_type is ReleaseType.SHADOW else ModelAlias.CANARY
            self.registry.transition_stage(
                model_name=model_name, version=version, stage=target_stage
            )
            self.repository.set_alias(model_name, alias, version)

        audit_event = self.audit_log.record(
            AuditEvent(
                event_type="learninghub.model_release.v1",
                actor=requested_by,
                action="rollback" if release_type is ReleaseType.ROLLBACK else "release",
                resource=f"model/{model_name}:{version}",
                outcome="approved",
                correlation_id=correlation_id,
                metadata={
                    "release_type": release_type.value,
                    "approval_id": approval_id,
                    "rollback_target": rollback_target,
                    "affected_modules": list(affected_modules),
                    "metrics": dict(validation_run.metrics),
                },
            )
        )
        decision = ModelReleaseDecision(
            release_id=f"model-release-{uuid4()}",
            model_name=model_name,
            from_version=from_version,
            to_version=rollback_target
            if release_type is ReleaseType.ROLLBACK and rollback_target
            else version,
            release_type=release_type,
            reason=reason,
            approval_id=approval_id,
            rollback_target=rollback_target,
            monitoring_window=monitoring_window,
            success_criteria=tuple(success_criteria),
            fail_criteria=tuple(fail_criteria),
            affected_modules=tuple(affected_modules),
            requested_by=requested_by,
            approved_by=approved_by,
            audit_event_id=audit_event.event_id,
            dataset_snapshot_id=model_version.dataset_snapshot_id,
            feature_schema_version=model_version.feature_schema_version,
            label_version=model_version.label_version,
            model_card_checksum=_model_card_checksum(model_card),
            model_artifact_uri=model_version.artifact_uri,
        )
        self.repository.save_release_decision(decision)
        return decision

    def evaluate_monitoring(
        self,
        *,
        model_name: str,
        dataset_snapshot_id: str,
        signal_type: MonitoringSignalType,
        observed_metrics: Mapping[str, float],
        baseline_metrics: Mapping[str, float],
        thresholds: Sequence[MetricThreshold],
        requested_by: str = "system",
        reason: str | None = None,
    ) -> RetrainingRequest | None:
        snapshot = self.repository.get_dataset_snapshot(dataset_snapshot_id)
        if snapshot is None:
            raise LearningHubError(f"unknown dataset snapshot {dataset_snapshot_id}")
        production = self.repository.get_alias(model_name, ModelAlias.PRODUCTION)
        if production is None:
            production = self.repository.get_alias(model_name, ModelAlias.CHAMPION)
        if production is None:
            raise LearningHubError(f"no production model registered for {model_name}")

        breaches: list[MonitoringBreach] = []
        for threshold in thresholds:
            if threshold.metric_name not in observed_metrics:
                breaches.append(
                    MonitoringBreach(
                        metric_name=threshold.metric_name,
                        observed_value=float("nan"),
                        threshold_message=f"{threshold.metric_name} missing from monitoring input",
                        severity=ValidationStatus.FAILED.value,
                    )
                )
                continue
            status, message = threshold.evaluate(float(observed_metrics[threshold.metric_name]))
            if status is ValidationStatus.PASSED:
                continue
            breaches.append(
                MonitoringBreach(
                    metric_name=threshold.metric_name,
                    observed_value=float(observed_metrics[threshold.metric_name]),
                    threshold_message=message or threshold.metric_name,
                    severity=status.value,
                )
            )

        evaluation = MonitoringEvaluation(
            evaluation_id=f"monitoring-{uuid4()}",
            model_name=model_name,
            model_version=production.version,
            dataset_snapshot_id=dataset_snapshot_id,
            signal_type=signal_type,
            observed_metrics=dict(observed_metrics),
            baseline_metrics=dict(baseline_metrics),
            breaches=tuple(breaches),
            requested_by=requested_by,
        )
        self.repository.save_monitoring_evaluation(evaluation)
        if not evaluation.triggered:
            return None

        request = RetrainingRequest(
            request_id=f"retrain-{uuid4()}",
            model_name=model_name,
            source_model_version=production.version,
            trigger_evaluation_id=evaluation.evaluation_id,
            trigger_type=signal_type,
            reason=reason or f"{signal_type.value.lower()} monitoring threshold breached",
            dataset_snapshot_id=dataset_snapshot_id,
            observed_metrics=dict(observed_metrics),
            baseline_metrics=dict(baseline_metrics),
            requested_by=requested_by,
            auto_promotion=False,
        )
        return self.repository.save_retraining_request(request)

    def ingest_outcome_monitoring(
        self,
        *,
        model_name: str,
        dataset_snapshot_id: str,
        observed_metrics: Mapping[str, float],
        baseline_metrics: Mapping[str, float],
        thresholds: Sequence[MetricThreshold],
        requested_by: str = "system",
        reason: str | None = None,
    ) -> RetrainingRequest | None:
        return self.evaluate_monitoring(
            model_name=model_name,
            dataset_snapshot_id=dataset_snapshot_id,
            signal_type=MonitoringSignalType.OUTCOME,
            observed_metrics=observed_metrics,
            baseline_metrics=baseline_metrics,
            thresholds=thresholds,
            requested_by=requested_by,
            reason=reason,
        )

    def compare_inference(
        self,
        *,
        model_name: str,
        challenger_version: str,
        inputs: Sequence[Mapping[str, Any]],
        predictor: Callable[[ModelVersion, Mapping[str, Any]], float],
        mode: InferenceComparisonMode,
        tolerance: float,
        champion_version: str | None = None,
        requested_by: str = "system",
    ) -> InferenceComparison:
        if not inputs:
            raise LearningHubError("inference comparison requires at least one input")
        champion = (
            self._require_model_version(model_name, champion_version)
            if champion_version
            else self.repository.get_alias(model_name, ModelAlias.CHAMPION)
        )
        if champion is None:
            champion = self.repository.get_alias(model_name, ModelAlias.PRODUCTION)
        if champion is None:
            raise LearningHubError(f"no champion model registered for {model_name}")
        challenger = self._require_model_version(model_name, challenger_version)

        champion_predictions: list[InferencePrediction] = []
        challenger_predictions: list[InferencePrediction] = []
        deltas: list[InferenceDelta] = []
        for index, input_row in enumerate(inputs):
            input_id = str(input_row.get("input_id") or input_row.get("entity_id") or index)
            champion_value = float(predictor(champion, input_row))
            challenger_value = float(predictor(challenger, input_row))
            champion_predictions.append(
                InferencePrediction(
                    input_id=input_id,
                    model_version=champion.version,
                    value=champion_value,
                )
            )
            challenger_predictions.append(
                InferencePrediction(
                    input_id=input_id,
                    model_version=challenger.version,
                    value=challenger_value,
                )
            )
            deltas.append(
                InferenceDelta(
                    input_id=input_id,
                    champion_value=champion_value,
                    challenger_value=challenger_value,
                )
            )

        comparison = InferenceComparison(
            comparison_id=f"inference-comparison-{uuid4()}",
            model_name=model_name,
            champion_version=champion.version,
            challenger_version=challenger.version,
            mode=mode,
            input_fingerprint=_fingerprint_inputs(inputs),
            champion_predictions=tuple(champion_predictions),
            challenger_predictions=tuple(challenger_predictions),
            deltas=tuple(deltas),
            tolerance=tolerance,
            requested_by=requested_by,
        )
        return self.repository.save_inference_comparison(comparison)

    def request_rollback_from_comparison(
        self,
        *,
        comparison_id: str,
        reason: str,
        approval_id: str,
        requested_by: str = "system",
        approved_by: str = "model-review-board",
    ) -> ModelReleaseDecision:
        comparison = self.repository.get_inference_comparison(comparison_id)
        if comparison is None:
            raise LearningHubError(f"unknown inference comparison {comparison_id}")
        if not comparison.rollback_recommended:
            raise LearningHubError("comparison does not recommend rollback")
        return self.request_release(
            model_name=comparison.model_name,
            version=comparison.challenger_version,
            release_type=ReleaseType.ROLLBACK,
            reason=reason,
            approval_id=approval_id,
            rollback_target=comparison.champion_version,
            monitoring_window="immediate",
            success_criteria=("production alias points to comparison champion",),
            fail_criteria=("champion smoke prediction fails",),
            requested_by=requested_by,
            approved_by=approved_by,
            correlation_id=comparison.comparison_id,
        )

    def monitor_release(
        self,
        *,
        release_id: str,
        observed_metrics: Mapping[str, float],
        guardrails: Sequence[MetricThreshold],
        evaluated_by: str = "release-monitor",
        correlation_id: str = "learninghub-monitor",
    ) -> ReleaseMonitorAssessment:
        """Evaluate a live release's guardrail metrics and record an audit event.

        A breach recommends (never auto-executes) a rollback. The rollback itself
        stays an explicit, approved ``request_release(ROLLBACK)`` action.
        """

        decision = self.repository.get_release_decision(release_id)
        if decision is None:
            raise LearningHubError(f"unknown release {release_id}")

        model_name = decision.model_name
        version = decision.to_version
        monitoring_window = decision.monitoring_window
        rollback_target = decision.rollback_target

        breaches = evaluate_guardrails(observed_metrics, guardrails)
        status = MonitorStatus.BREACHED if breaches else MonitorStatus.HEALTHY
        recommended_action = (
            RecommendedAction.ROLLBACK if breaches and rollback_target else RecommendedAction.NONE
        )

        audit_event = self.audit_log.record(
            AuditEvent(
                event_type="learninghub.release_monitor.v1",
                actor=evaluated_by,
                action="monitor",
                resource=f"model/{model_name}:{version}",
                outcome="breached" if breaches else "healthy",
                correlation_id=correlation_id,
                metadata={
                    "release_id": release_id,
                    "monitoring_window": monitoring_window,
                    "observed_metrics": dict(observed_metrics),
                    "breaches": [breach.to_dict() for breach in breaches],
                    "recommended_action": recommended_action.value,
                    "rollback_target": rollback_target,
                },
            )
        )

        return ReleaseMonitorAssessment(
            release_id=release_id,
            model_name=model_name,
            version=version,
            status=status,
            recommended_action=recommended_action,
            observed_metrics=dict(observed_metrics),
            breaches=breaches,
            monitoring_window=monitoring_window,
            rollback_target=rollback_target,
            evaluated_at=utcnow(),
            audit_event_id=audit_event.event_id,
        )

    def _assert_release_gate(
        self,
        *,
        model_version: ModelVersion,
        model_card: ModelCard,
        validation_run: ValidationRun,
        release_type: ReleaseType,
        approval_id: str,
        rollback_target: str | None,
    ) -> None:
        if not approval_id:
            raise LearningHubError("release requires approval_id")
        if not validation_run.passed:
            raise LearningHubError("release requires passed validation")
        if not model_card.is_complete:
            raise LearningHubError("release requires complete model card")
        if not model_card.is_approved:
            raise LearningHubError("release requires approved model card")
        if release_type in {ReleaseType.FULL, ReleaseType.CANARY} and not rollback_target:
            raise LearningHubError("release requires rollback target")
        if release_type is ReleaseType.ROLLBACK:
            target = rollback_target or model_version.rollback_target
            if not target:
                raise LearningHubError("rollback requires rollback target")
            if self.repository.get_model_version(model_version.model_name, target) is None:
                raise LearningHubError(f"unknown rollback target {target}")

    def _require_model_version(self, model_name: str, version: str) -> ModelVersion:
        model_version = self.repository.get_model_version(model_name, version)
        if model_version is None:
            raise ModelRegistryError(f"unknown model version {model_name}:{version}")
        return model_version

    def _require_model_card(self, model_name: str, version: str) -> ModelCard:
        model_card = self.repository.get_model_card(model_name, version)
        if model_card is None:
            raise LearningHubError(f"missing model card for {model_name}:{version}")
        return model_card

    def _require_validation_run(self, validation_run_id: str) -> ValidationRun:
        validation_run = self.repository.get_validation_run(validation_run_id)
        if validation_run is None:
            raise LearningHubError(f"missing validation run {validation_run_id}")
        return validation_run

    # Feature & Label Registry APIs
    def create_feature(self, feature: FeatureDefinition) -> FeatureDefinition:
        return self.repository.save_feature(feature)

    def get_feature(
        self, feature_name: str, version: str | None = None
    ) -> FeatureDefinition | None:
        return self.repository.get_feature(feature_name, version)

    def list_features(self) -> list[FeatureDefinition]:
        return self.repository.list_features()

    def approve_feature(self, feature_name: str, approved_by: str) -> FeatureDefinition:
        feature = self.repository.get_feature(feature_name)
        if feature is None:
            raise LearningHubError(f"unknown feature {feature_name}")
        approved = FeatureDefinition(
            feature_id=feature.feature_id,
            feature_name=feature.feature_name,
            version=feature.version,
            status="ACTIVE",
            owner=feature.owner,
            domain=feature.domain,
            entity_type=feature.entity_type,
            entity_key=feature.entity_key,
            grain=feature.grain,
            value_type=feature.value_type,
            unit=feature.unit,
            semantic_type=feature.semantic_type,
            source_table=feature.source_table,
            source_view=feature.source_view,
            source_system=feature.source_system,
            calculation_sql_uri=feature.calculation_sql_uri,
            feature_available_time_rule=feature.feature_available_time_rule,
            refresh_frequency=feature.refresh_frequency,
            allowed_model_names=feature.allowed_model_names,
            forbidden_model_names=feature.forbidden_model_names,
            quality_rules=feature.quality_rules,
            null_policy=feature.null_policy,
            pii_classification=feature.pii_classification,
            lineage=feature.lineage,
            created_at=feature.created_at,
            updated_at=datetime.now(UTC),
            approved_by=approved_by,
        )
        return self.repository.save_feature(approved)

    def create_feature_set(self, feature_set: FeatureSet) -> FeatureSet:
        return self.repository.save_feature_set(feature_set)

    def get_feature_set(self, feature_set_id: str) -> FeatureSet | None:
        return self.repository.get_feature_set(feature_set_id)

    def create_label(self, label: LabelDefinition) -> LabelDefinition:
        return self.repository.save_label(label)

    def get_label(self, label_name: str, version: str | None = None) -> LabelDefinition | None:
        return self.repository.get_label(label_name, version)

    def list_labels(self) -> list[LabelDefinition]:
        return self.repository.list_labels()

    def approve_label(self, label_name: str, approved_by: str) -> LabelDefinition:
        label = self.repository.get_label(label_name)
        if label is None:
            raise LearningHubError(f"unknown label {label_name}")
        approved = LabelDefinition(
            label_id=label.label_id,
            label_name=label.label_name,
            version=label.version,
            status="ACTIVE",
            owner=label.owner,
            entity_type=label.entity_type,
            entity_key=label.entity_key,
            outcome_definition=label.outcome_definition,
            outcome_unit=label.outcome_unit,
            label_window_start_rule=label.label_window_start_rule,
            label_window_end_rule=label.label_window_end_rule,
            label_maturity_rule=label.label_maturity_rule,
            source_table=label.source_table,
            calculation_sql_uri=label.calculation_sql_uri,
            allowed_models=label.allowed_models,
            forbidden_models=label.forbidden_models,
            quality_rules=label.quality_rules,
            treatment_dependency=label.treatment_dependency,
            contamination_risk=label.contamination_risk,
            created_at=label.created_at,
            approved_by=approved_by,
        )
        return self.repository.save_label(approved)

    def create_label_set(self, label_set: LabelSet) -> LabelSet:
        return self.repository.save_label_set(label_set)

    def get_label_set(self, label_set_id: str) -> LabelSet | None:
        return self.repository.get_label_set(label_set_id)


def _stage_for_release_type(release_type: ReleaseType) -> ModelStage:
    if release_type is ReleaseType.SHADOW:
        return ModelStage.SHADOW
    if release_type is ReleaseType.CANARY:
        return ModelStage.CANARY
    if release_type is ReleaseType.FULL:
        return ModelStage.PRODUCTION
    return ModelStage.ROLLED_BACK


def _canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()


def _model_card_checksum(model_card: ModelCard) -> str:
    return compute_content_digest(_canonical_json_bytes(model_card.to_dict()))


def _fingerprint_inputs(inputs: Sequence[Mapping[str, Any]]) -> str:
    return f"sha256:{sha256(_canonical_json_bytes(list(inputs))).hexdigest()}"


__all__ = ["LearningHubError", "LearningHubService", "ModelReleaseDecision", "ReleaseType"]
