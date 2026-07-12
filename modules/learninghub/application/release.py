from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from models.shared_ml import (
    FeatureDefinition,
    FeatureSet,
    LabelDefinition,
    LabelSet,
    LocalModelArtifactStore,
)
from models.shared_ml.model_card import ModelCard
from models.shared_ml.registry import ModelAlias, ModelRegistryError, ModelStage, ModelVersion
from models.shared_ml.validation import (
    MetricThreshold,
    SegmentMetric,
    ValidationRun,
    validate_model_candidate,
)
from modules.learninghub.application.monitor import (
    MonitorStatus,
    RecommendedAction,
    ReleaseMonitorAssessment,
    evaluate_guardrails,
    utcnow,
)
from modules.learninghub.domain import DatasetSnapshot, build_dataset_snapshot
from modules.learninghub.infrastructure import (
    InMemoryLearningHubRepository,
    LearningHubRepository,
    MlflowRegistryAdapter,
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
        }


class LearningHubService:
    def __init__(
        self,
        *,
        repository: LearningHubRepository | None = None,
        registry: MlflowRegistryAdapter | None = None,
        audit_log: InMemoryAuditLog | None = None,
    ) -> None:
        self.repository = repository or InMemoryLearningHubRepository()
        self.registry = registry or MlflowRegistryAdapter(self.repository)
        self.audit_log = audit_log or InMemoryAuditLog()

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
        if validation_run.dataset_snapshot_id != model_version.dataset_snapshot_id:
            raise LearningHubError("validation run dataset does not match model version")
        if model_card.validation_run_id != validation_run.validation_run_id:
            raise LearningHubError("model card validation run does not match")
        if model_card.dataset_snapshot_id != model_version.dataset_snapshot_id:
            raise LearningHubError("model card dataset does not match model version")
        self.repository.save_validation_run(validation_run)
        self.repository.save_model_card(model_card)

        # Save model card to local model artifact store
        store = LocalModelArtifactStore()
        store.save_model_card(model_card, artifact_uri=model_version.artifact_uri)

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
        )
        self.repository.save_release_decision(decision)
        return decision

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
            RecommendedAction.ROLLBACK
            if breaches and rollback_target
            else RecommendedAction.NONE
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


__all__ = ["LearningHubError", "LearningHubService", "ModelReleaseDecision", "ReleaseType"]
