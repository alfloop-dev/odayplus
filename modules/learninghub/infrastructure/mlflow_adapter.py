from __future__ import annotations

from dataclasses import dataclass

from models.shared_ml.registry import ModelAlias, ModelStage, ModelVersion

from .repositories import LearningHubRepository


@dataclass
class MlflowRegistryAdapter:
    """MLflow-style logical registry backed by the Learning Hub repository."""

    repository: LearningHubRepository

    def register_model_version(self, model_version: ModelVersion) -> ModelVersion:
        return self.repository.save_model_version(model_version)

    def transition_stage(
        self,
        *,
        model_name: str,
        version: str,
        stage: ModelStage,
    ) -> ModelVersion:
        model_version = self.repository.get_model_version(model_name, version)
        if model_version is None:
            raise ValueError(f"unknown model version {model_name}:{version}")
        updated = model_version.with_stage(stage)
        return self.repository.save_model_version(updated)

    def set_alias(self, *, model_name: str, alias: ModelAlias, version: str) -> ModelVersion:
        return self.repository.set_alias(model_name, alias, version)

    def get_by_alias(self, *, model_name: str, alias: ModelAlias) -> ModelVersion | None:
        return self.repository.get_alias(model_name, alias)


__all__ = ["MlflowRegistryAdapter"]
