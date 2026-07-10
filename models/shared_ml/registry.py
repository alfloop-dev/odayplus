from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class ModelRegistryError(ValueError):
    pass


class ModelStage(StrEnum):
    DEV = "dev"
    SHADOW = "shadow"
    CANARY = "canary"
    PRODUCTION = "production"
    RETIRED = "retired"
    ROLLED_BACK = "rolled_back"
    BLOCKED = "blocked"


class ModelAlias(StrEnum):
    CHAMPION = "champion"
    CHALLENGER = "challenger"
    SHADOW = "shadow"
    CANARY = "canary"
    PRODUCTION = "production"
    PREVIOUS_PRODUCTION = "previous_production"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class ModelVersion:
    model_name: str
    version: str
    artifact_uri: str
    dataset_snapshot_id: str
    feature_schema_version: str
    label_version: str
    metrics: Mapping[str, float]
    stage: ModelStage = ModelStage.DEV
    aliases: frozenset[ModelAlias] = frozenset()
    run_id: str | None = None
    git_sha: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    approved_by: str | None = None
    approved_at: datetime | None = None
    rollback_target: str | None = None
    monitoring_config: Mapping[str, Any] = field(default_factory=dict)

    @property
    def model_id(self) -> str:
        return f"{self.model_name}:{self.version}"

    def with_stage(self, stage: ModelStage) -> ModelVersion:
        return self._replace(stage=stage)

    def with_aliases(self, aliases: frozenset[ModelAlias]) -> ModelVersion:
        return self._replace(aliases=aliases)

    def with_approval(self, approver: str, approved_at: datetime | None = None) -> ModelVersion:
        return self._replace(
            approved_by=approver,
            approved_at=approved_at or datetime.now(UTC),
        )

    def _replace(self, **changes: Any) -> ModelVersion:
        values = {
            "model_name": self.model_name,
            "version": self.version,
            "artifact_uri": self.artifact_uri,
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "feature_schema_version": self.feature_schema_version,
            "label_version": self.label_version,
            "metrics": self.metrics,
            "stage": self.stage,
            "aliases": self.aliases,
            "run_id": self.run_id,
            "git_sha": self.git_sha,
            "created_at": self.created_at,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "rollback_target": self.rollback_target,
            "monitoring_config": self.monitoring_config,
        }
        values.update(changes)
        return ModelVersion(**values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "version": self.version,
            "model_id": self.model_id,
            "artifact_uri": self.artifact_uri,
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "feature_schema_version": self.feature_schema_version,
            "label_version": self.label_version,
            "metrics": dict(self.metrics),
            "stage": self.stage.value,
            "aliases": sorted(alias.value for alias in self.aliases),
            "run_id": self.run_id,
            "git_sha": self.git_sha,
            "created_at": self.created_at.isoformat(),
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "rollback_target": self.rollback_target,
            "monitoring_config": dict(self.monitoring_config),
        }


@dataclass(frozen=True)
class RegisteredModel:
    model_name: str
    owner: str
    description: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "owner": self.owner,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class FeatureDefinition:
    feature_id: str
    feature_name: str
    version: str
    status: str  # DRAFT | ACTIVE | DEPRECATED | BLOCKED
    owner: str
    domain: str
    entity_type: str
    entity_key: Sequence[str]
    grain: str
    value_type: str
    unit: str
    semantic_type: str
    source_table: str
    source_view: str
    source_system: str
    calculation_sql_uri: str
    feature_available_time_rule: str
    refresh_frequency: str
    allowed_model_names: Sequence[str] = ()
    forbidden_model_names: Sequence[str] = ()
    quality_rules: Sequence[str] = ()
    null_policy: str = "ALLOW"
    pii_classification: str = "NONE"
    lineage: Sequence[str] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    approved_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "feature_name": self.feature_name,
            "version": self.version,
            "status": self.status,
            "owner": self.owner,
            "domain": self.domain,
            "entity_type": self.entity_type,
            "entity_key": list(self.entity_key),
            "grain": self.grain,
            "value_type": self.value_type,
            "unit": self.unit,
            "semantic_type": self.semantic_type,
            "source_table": self.source_table,
            "source_view": self.source_view,
            "source_system": self.source_system,
            "calculation_sql_uri": self.calculation_sql_uri,
            "feature_available_time_rule": self.feature_available_time_rule,
            "refresh_frequency": self.refresh_frequency,
            "allowed_model_names": list(self.allowed_model_names),
            "forbidden_model_names": list(self.forbidden_model_names),
            "quality_rules": list(self.quality_rules),
            "null_policy": self.null_policy,
            "pii_classification": self.pii_classification,
            "lineage": list(self.lineage),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "approved_by": self.approved_by,
        }


@dataclass(frozen=True)
class LabelDefinition:
    label_id: str
    label_name: str
    version: str
    status: str  # DRAFT | ACTIVE | DEPRECATED | BLOCKED
    owner: str
    entity_type: str
    entity_key: Sequence[str]
    outcome_definition: str
    outcome_unit: str
    label_window_start_rule: str
    label_window_end_rule: str
    label_maturity_rule: str
    source_table: str
    calculation_sql_uri: str
    allowed_models: Sequence[str] = ()
    forbidden_models: Sequence[str] = ()
    quality_rules: Sequence[str] = ()
    treatment_dependency: str = "NONE"
    contamination_risk: str = "LOW"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    approved_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label_id": self.label_id,
            "label_name": self.label_name,
            "version": self.version,
            "status": self.status,
            "owner": self.owner,
            "entity_type": self.entity_type,
            "entity_key": list(self.entity_key),
            "outcome_definition": self.outcome_definition,
            "outcome_unit": self.outcome_unit,
            "label_window_start_rule": self.label_window_start_rule,
            "label_window_end_rule": self.label_window_end_rule,
            "label_maturity_rule": self.label_maturity_rule,
            "source_table": self.source_table,
            "calculation_sql_uri": self.calculation_sql_uri,
            "allowed_models": list(self.allowed_models),
            "forbidden_models": list(self.forbidden_models),
            "quality_rules": list(self.quality_rules),
            "treatment_dependency": self.treatment_dependency,
            "contamination_risk": self.contamination_risk,
            "created_at": self.created_at.isoformat(),
            "approved_by": self.approved_by,
        }


@dataclass(frozen=True)
class FeatureSet:
    feature_set_id: str
    model_name: str
    version: str
    features: Sequence[str]
    point_in_time_policy_id: str
    approved_by: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_set_id": self.feature_set_id,
            "model_name": self.model_name,
            "version": self.version,
            "features": list(self.features),
            "point_in_time_policy_id": self.point_in_time_policy_id,
            "approved_by": self.approved_by,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class LabelSet:
    label_set_id: str
    labels: Sequence[str]
    maturity_policy: str
    excluded_conditions: Sequence[str] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "label_set_id": self.label_set_id,
            "labels": list(self.labels),
            "maturity_policy": self.maturity_policy,
            "excluded_conditions": list(self.excluded_conditions),
            "created_at": self.created_at.isoformat(),
        }


__all__ = [
    "ModelAlias",
    "ModelRegistryError",
    "ModelStage",
    "ModelVersion",
    "RegisteredModel",
    "FeatureDefinition",
    "LabelDefinition",
    "FeatureSet",
    "LabelSet",
]
