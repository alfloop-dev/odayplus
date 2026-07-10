from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from models.shared_ml import (
    FeatureDefinition,
    FeatureSet,
    LabelDefinition,
    LabelSet,
    ModelAlias,
    ModelCard,
    ModelRegistryError,
    ModelVersion,
)
from models.shared_ml.validation import ValidationRun
from modules.learninghub.domain import DatasetSnapshot


class ReleaseDecisionRecord(Protocol):
    release_id: str


@runtime_checkable
class LearningHubRepository(Protocol):
    """Storage-neutral persistence surface for the Learning Hub registry.

    Both :class:`InMemoryLearningHubRepository` and the durable SQLite-backed
    repository in ``shared/infrastructure/persistence`` satisfy this protocol,
    so :class:`~modules.learninghub.application.LearningHubService` and the
    MLflow adapter accept either without code changes.
    """

    def save_dataset_snapshot(self, snapshot: DatasetSnapshot) -> DatasetSnapshot: ...
    def get_dataset_snapshot(self, dataset_snapshot_id: str) -> DatasetSnapshot | None: ...
    def save_model_version(self, model_version: ModelVersion) -> ModelVersion: ...
    def get_model_version(self, model_name: str, version: str) -> ModelVersion | None: ...
    def list_model_versions(self, model_name: str) -> list[ModelVersion]: ...
    def save_model_card(self, model_card: ModelCard) -> ModelCard: ...
    def get_model_card(self, model_name: str, version: str) -> ModelCard | None: ...
    def save_validation_run(self, validation_run: ValidationRun) -> ValidationRun: ...
    def get_validation_run(self, validation_run_id: str) -> ValidationRun | None: ...
    def set_alias(self, model_name: str, alias: ModelAlias, version: str) -> ModelVersion: ...
    def clear_alias(self, model_name: str, alias: ModelAlias) -> None: ...
    def get_alias(self, model_name: str, alias: ModelAlias) -> ModelVersion | None: ...
    def save_release_decision(
        self, decision: ReleaseDecisionRecord
    ) -> ReleaseDecisionRecord: ...
    def get_release_decision(self, release_id: str) -> object | None: ...
    def list_release_decisions(self) -> list[object]: ...


@dataclass
class InMemoryLearningHubRepository:
    _datasets: dict[str, DatasetSnapshot] = field(default_factory=dict)
    _model_versions: dict[tuple[str, str], ModelVersion] = field(default_factory=dict)
    _model_cards: dict[tuple[str, str], ModelCard] = field(default_factory=dict)
    _validation_runs: dict[str, ValidationRun] = field(default_factory=dict)
    _aliases: dict[str, dict[ModelAlias, str]] = field(default_factory=dict)
    _release_decisions: dict[str, object] = field(default_factory=dict)
    _features: dict[tuple[str, str], FeatureDefinition] = field(default_factory=dict)
    _labels: dict[tuple[str, str], LabelDefinition] = field(default_factory=dict)
    _feature_sets: dict[str, FeatureSet] = field(default_factory=dict)
    _label_sets: dict[str, LabelSet] = field(default_factory=dict)

    def save_dataset_snapshot(self, snapshot: DatasetSnapshot) -> DatasetSnapshot:
        self._datasets[snapshot.dataset_snapshot_id] = snapshot
        return snapshot

    def get_dataset_snapshot(self, dataset_snapshot_id: str) -> DatasetSnapshot | None:
        return self._datasets.get(dataset_snapshot_id)

    def save_model_version(self, model_version: ModelVersion) -> ModelVersion:
        self._model_versions[(model_version.model_name, model_version.version)] = model_version
        return model_version

    def get_model_version(self, model_name: str, version: str) -> ModelVersion | None:
        return self._model_versions.get((model_name, version))

    def list_model_versions(self, model_name: str) -> list[ModelVersion]:
        return [
            version
            for (stored_model_name, _), version in self._model_versions.items()
            if stored_model_name == model_name
        ]

    def save_model_card(self, model_card: ModelCard) -> ModelCard:
        self._model_cards[(model_card.model_name, model_card.model_version)] = model_card
        return model_card

    def get_model_card(self, model_name: str, version: str) -> ModelCard | None:
        return self._model_cards.get((model_name, version))

    def save_validation_run(self, validation_run: ValidationRun) -> ValidationRun:
        self._validation_runs[validation_run.validation_run_id] = validation_run
        return validation_run

    def get_validation_run(self, validation_run_id: str) -> ValidationRun | None:
        return self._validation_runs.get(validation_run_id)

    def set_alias(self, model_name: str, alias: ModelAlias, version: str) -> ModelVersion:
        model_version = self.get_model_version(model_name, version)
        if model_version is None:
            raise ModelRegistryError(f"unknown model version {model_name}:{version}")

        model_aliases = self._aliases.setdefault(model_name, {})
        previous_version = model_aliases.get(alias)
        if previous_version:
            previous = self.get_model_version(model_name, previous_version)
            if previous is not None:
                self.save_model_version(previous.with_aliases(previous.aliases - {alias}))

        model_aliases[alias] = version
        updated = model_version.with_aliases(model_version.aliases | {alias})
        self.save_model_version(updated)
        return updated

    def clear_alias(self, model_name: str, alias: ModelAlias) -> None:
        version = self._aliases.get(model_name, {}).pop(alias, None)
        if version is None:
            return
        model_version = self.get_model_version(model_name, version)
        if model_version is not None:
            self.save_model_version(model_version.with_aliases(model_version.aliases - {alias}))

    def get_alias(self, model_name: str, alias: ModelAlias) -> ModelVersion | None:
        version = self._aliases.get(model_name, {}).get(alias)
        if version is None:
            return None
        return self.get_model_version(model_name, version)

    def save_release_decision(self, decision: ReleaseDecisionRecord) -> ReleaseDecisionRecord:
        self._release_decisions[decision.release_id] = decision
        return decision

    def get_release_decision(self, release_id: str) -> object | None:
        return self._release_decisions.get(release_id)

    def list_release_decisions(self) -> list[object]:
        return list(self._release_decisions.values())

    def save_feature(self, feature: FeatureDefinition) -> FeatureDefinition:
        self._features[(feature.feature_name, feature.version)] = feature
        return feature

    def get_feature(
        self, feature_name: str, version: str | None = None
    ) -> FeatureDefinition | None:
        if version:
            return self._features.get((feature_name, version))
        matches = [f for (name, _), f in self._features.items() if name == feature_name]
        if not matches:
            return None
        return max(matches, key=lambda f: f.version)

    def list_features(self) -> list[FeatureDefinition]:
        return list(self._features.values())

    def save_label(self, label: LabelDefinition) -> LabelDefinition:
        self._labels[(label.label_name, label.version)] = label
        return label

    def get_label(self, label_name: str, version: str | None = None) -> LabelDefinition | None:
        if version:
            return self._labels.get((label_name, version))
        matches = [lbl for (name, _), lbl in self._labels.items() if name == label_name]
        if not matches:
            return None
        return max(matches, key=lambda lbl: lbl.version)

    def list_labels(self) -> list[LabelDefinition]:
        return list(self._labels.values())

    def save_feature_set(self, feature_set: FeatureSet) -> FeatureSet:
        self._feature_sets[feature_set.feature_set_id] = feature_set
        return feature_set

    def get_feature_set(self, feature_set_id: str) -> FeatureSet | None:
        return self._feature_sets.get(feature_set_id)

    def save_label_set(self, label_set: LabelSet) -> LabelSet:
        self._label_sets[label_set.label_set_id] = label_set
        return label_set

    def get_label_set(self, label_set_id: str) -> LabelSet | None:
        return self._label_sets.get(label_set_id)


__all__ = ["InMemoryLearningHubRepository", "LearningHubRepository", "ReleaseDecisionRecord"]
