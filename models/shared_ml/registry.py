from __future__ import annotations

from collections.abc import Mapping
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


__all__ = [
    "ModelAlias",
    "ModelRegistryError",
    "ModelStage",
    "ModelVersion",
    "RegisteredModel",
]
