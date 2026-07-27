from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from threading import RLock
from typing import Any, Protocol, runtime_checkable

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
from modules.learninghub.domain import (
    DatasetSnapshot,
    InferenceComparison,
    MonitoringEvaluation,
    RetrainingRequest,
)


class ReleaseDecisionRecord(Protocol):
    release_id: str


class LearningHubReleaseConflict(RuntimeError):
    """Raised when a stale release command loses the per-model CAS boundary."""


class ReleaseSagaState(StrEnum):
    INTENT_RECORDED = "INTENT_RECORDED"
    MODEL_STATE_APPLIED = "MODEL_STATE_APPLIED"
    ALIASES_APPLIED = "ALIASES_APPLIED"
    AUDIT_RECORDED = "AUDIT_RECORDED"
    RECEIPT_RECORDED = "RECEIPT_RECORDED"
    COMPLETED = "COMPLETED"
    COMPENSATING = "COMPENSATING"
    COMPENSATED = "COMPENSATED"
    COMPENSATION_FAILED = "COMPENSATION_FAILED"


_TERMINAL_RELEASE_SAGA_STATES = frozenset(
    {
        ReleaseSagaState.COMPLETED,
        ReleaseSagaState.COMPENSATED,
        ReleaseSagaState.COMPENSATION_FAILED,
    }
)


@dataclass(frozen=True)
class ModelReleaseSaga:
    """Durable, global-scope release intent written before MLflow mutation."""

    release_id: str
    model_name: str
    idempotency_key: str
    request_fingerprint: str
    release_revision: int
    operation: str
    command: dict[str, Any]
    version_snapshots: tuple[ModelVersion, ...]
    alias_snapshots: tuple[tuple[ModelAlias, str | None], ...]
    state: ReleaseSagaState = ReleaseSagaState.INTENT_RECORDED
    attempt: int = 0
    decision: object | None = None
    audit_event_id: str | None = None
    last_error: str | None = None
    compensation_errors: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    scope: str = "global"

    @property
    def terminal(self) -> bool:
        return self.state in _TERMINAL_RELEASE_SAGA_STATES

    def evolve(self, **changes: Any) -> ModelReleaseSaga:
        return replace(self, updated_at=datetime.now(UTC), **changes)


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
    def list_all_model_versions(self) -> list[ModelVersion]: ...
    def save_model_card(self, model_card: ModelCard) -> ModelCard: ...
    def get_model_card(self, model_name: str, version: str) -> ModelCard | None: ...
    def save_validation_run(self, validation_run: ValidationRun) -> ValidationRun: ...
    def get_validation_run(self, validation_run_id: str) -> ValidationRun | None: ...
    def set_alias(self, model_name: str, alias: ModelAlias, version: str) -> ModelVersion: ...
    def clear_alias(self, model_name: str, alias: ModelAlias) -> None: ...
    def get_alias(self, model_name: str, alias: ModelAlias) -> ModelVersion | None: ...
    def save_release_decision(self, decision: ReleaseDecisionRecord) -> ReleaseDecisionRecord: ...
    def delete_release_decision(self, release_id: str) -> None: ...
    def get_release_decision(self, release_id: str) -> object | None: ...
    def list_release_decisions(self) -> list[object]: ...
    def save_release_saga(self, saga: ModelReleaseSaga) -> ModelReleaseSaga: ...
    def get_release_saga(self, release_id: str) -> ModelReleaseSaga | None: ...
    def get_release_saga_by_idempotency(
        self, model_name: str, idempotency_key: str
    ) -> ModelReleaseSaga | None: ...
    def list_release_sagas(
        self,
        model_name: str | None = None,
        *,
        include_terminal: bool = True,
    ) -> list[ModelReleaseSaga]: ...
    def get_release_revision(self, model_name: str) -> int: ...
    def release_guard(
        self,
        model_name: str,
        *,
        expected_revision: int,
    ) -> AbstractContextManager[int]: ...
    def release_recovery_guard(
        self,
        model_name: str,
    ) -> AbstractContextManager[None]: ...
    def save_monitoring_evaluation(
        self, evaluation: MonitoringEvaluation
    ) -> MonitoringEvaluation: ...
    def get_monitoring_evaluation(self, evaluation_id: str) -> MonitoringEvaluation | None: ...
    def list_monitoring_evaluations(
        self, model_name: str | None = None
    ) -> list[MonitoringEvaluation]: ...
    def save_retraining_request(self, request: RetrainingRequest) -> RetrainingRequest: ...
    def get_retraining_request(self, request_id: str) -> RetrainingRequest | None: ...
    def list_retraining_requests(
        self, model_name: str | None = None
    ) -> list[RetrainingRequest]: ...
    def save_inference_comparison(self, comparison: InferenceComparison) -> InferenceComparison: ...
    def get_inference_comparison(self, comparison_id: str) -> InferenceComparison | None: ...
    def list_inference_comparisons(
        self, model_name: str | None = None
    ) -> list[InferenceComparison]: ...


@dataclass
class InMemoryLearningHubRepository:
    _datasets: dict[str, DatasetSnapshot] = field(default_factory=dict)
    _model_versions: dict[tuple[str, str], ModelVersion] = field(default_factory=dict)
    _model_cards: dict[tuple[str, str], ModelCard] = field(default_factory=dict)
    _validation_runs: dict[str, ValidationRun] = field(default_factory=dict)
    _aliases: dict[str, dict[ModelAlias, str]] = field(default_factory=dict)
    _release_decisions: dict[str, object] = field(default_factory=dict)
    _release_sagas: dict[str, ModelReleaseSaga] = field(default_factory=dict)
    _release_saga_idempotency: dict[tuple[str, str], str] = field(default_factory=dict)
    _features: dict[tuple[str, str], FeatureDefinition] = field(default_factory=dict)
    _labels: dict[tuple[str, str], LabelDefinition] = field(default_factory=dict)
    _feature_sets: dict[str, FeatureSet] = field(default_factory=dict)
    _label_sets: dict[str, LabelSet] = field(default_factory=dict)
    _monitoring_evaluations: dict[str, MonitoringEvaluation] = field(default_factory=dict)
    _retraining_requests: dict[str, RetrainingRequest] = field(default_factory=dict)
    _inference_comparisons: dict[str, InferenceComparison] = field(default_factory=dict)
    _release_revisions: dict[str, int] = field(default_factory=dict, repr=False)
    _release_locks: dict[str, RLock] = field(default_factory=dict, repr=False)
    _release_locks_guard: RLock = field(default_factory=RLock, repr=False)

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

    def list_all_model_versions(self) -> list[ModelVersion]:
        return list(self._model_versions.values())

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

    def delete_release_decision(self, release_id: str) -> None:
        self._release_decisions.pop(release_id, None)

    def get_release_decision(self, release_id: str) -> object | None:
        return self._release_decisions.get(release_id)

    def list_release_decisions(self) -> list[object]:
        return list(self._release_decisions.values())

    def save_release_saga(self, saga: ModelReleaseSaga) -> ModelReleaseSaga:
        idempotency_scope = (saga.model_name, saga.idempotency_key)
        existing_release_id = self._release_saga_idempotency.get(idempotency_scope)
        if existing_release_id is not None and existing_release_id != saga.release_id:
            raise LearningHubReleaseConflict(
                f"idempotency key already belongs to release {existing_release_id}"
            )
        self._release_sagas[saga.release_id] = saga
        self._release_saga_idempotency[idempotency_scope] = saga.release_id
        return saga

    def get_release_saga(self, release_id: str) -> ModelReleaseSaga | None:
        return self._release_sagas.get(release_id)

    def get_release_saga_by_idempotency(
        self, model_name: str, idempotency_key: str
    ) -> ModelReleaseSaga | None:
        release_id = self._release_saga_idempotency.get((model_name, idempotency_key))
        return self._release_sagas.get(release_id) if release_id is not None else None

    def list_release_sagas(
        self,
        model_name: str | None = None,
        *,
        include_terminal: bool = True,
    ) -> list[ModelReleaseSaga]:
        sagas = list(self._release_sagas.values())
        if model_name is not None:
            sagas = [saga for saga in sagas if saga.model_name == model_name]
        if not include_terminal:
            sagas = [saga for saga in sagas if not saga.terminal]
        return sagas

    def get_release_revision(self, model_name: str) -> int:
        return self._release_revisions.get(model_name, 0)

    @contextmanager
    def release_guard(
        self,
        model_name: str,
        *,
        expected_revision: int,
    ) -> Iterator[int]:
        with self._release_locks_guard:
            lock = self._release_locks.setdefault(model_name, RLock())
        with lock:
            current = self.get_release_revision(model_name)
            if current != expected_revision:
                raise LearningHubReleaseConflict(
                    f"release revision conflict for {model_name}: "
                    f"expected {expected_revision}, current {current}"
                )
            reserved = current + 1
            self._release_revisions[model_name] = reserved
            try:
                yield reserved
            except BaseException:
                self._release_revisions[model_name] = current
                raise

    @contextmanager
    def release_recovery_guard(self, model_name: str) -> Iterator[None]:
        with self._release_locks_guard:
            lock = self._release_locks.setdefault(model_name, RLock())
        with lock:
            yield

    def save_monitoring_evaluation(self, evaluation: MonitoringEvaluation) -> MonitoringEvaluation:
        self._monitoring_evaluations[evaluation.evaluation_id] = evaluation
        return evaluation

    def get_monitoring_evaluation(self, evaluation_id: str) -> MonitoringEvaluation | None:
        return self._monitoring_evaluations.get(evaluation_id)

    def list_monitoring_evaluations(
        self, model_name: str | None = None
    ) -> list[MonitoringEvaluation]:
        values = list(self._monitoring_evaluations.values())
        if model_name is None:
            return values
        return [evaluation for evaluation in values if evaluation.model_name == model_name]

    def save_retraining_request(self, request: RetrainingRequest) -> RetrainingRequest:
        self._retraining_requests[request.request_id] = request
        return request

    def get_retraining_request(self, request_id: str) -> RetrainingRequest | None:
        return self._retraining_requests.get(request_id)

    def list_retraining_requests(self, model_name: str | None = None) -> list[RetrainingRequest]:
        values = list(self._retraining_requests.values())
        if model_name is None:
            return values
        return [request for request in values if request.model_name == model_name]

    def save_inference_comparison(self, comparison: InferenceComparison) -> InferenceComparison:
        self._inference_comparisons[comparison.comparison_id] = comparison
        return comparison

    def get_inference_comparison(self, comparison_id: str) -> InferenceComparison | None:
        return self._inference_comparisons.get(comparison_id)

    def list_inference_comparisons(
        self, model_name: str | None = None
    ) -> list[InferenceComparison]:
        values = list(self._inference_comparisons.values())
        if model_name is None:
            return values
        return [comparison for comparison in values if comparison.model_name == model_name]

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
