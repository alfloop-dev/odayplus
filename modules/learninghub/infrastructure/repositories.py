from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from models.shared_ml.model_card import ModelCard
from models.shared_ml.registry import ModelAlias, ModelRegistryError, ModelVersion
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


__all__ = ["InMemoryLearningHubRepository", "LearningHubRepository", "ReleaseDecisionRecord"]
