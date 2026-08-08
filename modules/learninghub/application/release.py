from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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
    DqTriageRecord,
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
    ModelReleaseSaga,
    ReleaseSagaState,
)
from modules.learninghub.infrastructure.repositories import (
    LearningHubReleaseConflict,
    LearningHubReleaseFenced,
)
from modules.learninghub.runtime import (
    LearningHubRuntimeConfigurationError,
    learninghub_production_required,
)
from shared.audit import AuditEvent, AuditRecorder, InMemoryAuditLog


class LearningHubError(ValueError):
    pass


class LearningHubConflictError(LearningHubError):
    """A governed command lost a concurrency boundary (HTTP 409).

    Raised when the compare-and-set release revision is stale, another release
    is already active for the model, an idempotency key is replayed with a
    different command, or a fenced-out worker tried to keep writing.
    """


class LearningHubPreconditionRequiredError(LearningHubError):
    """A governed command omitted a mandatory precondition (HTTP 428).

    Release commands must bind an expected release revision and an idempotency
    key. A caller that sends neither has no safe retry semantics, so the command
    is rejected before any state is touched.
    """


# A release execution lease is held while a worker drives a saga. Recovery only
# takes a saga over once the lease expires, which is what keeps startup or
# operator recovery from compensating a release another live worker is running.
DEFAULT_RELEASE_LEASE_SECONDS = 300.0

# Roles that may carry a release approval, mirroring the approval-document
# contract enforced by ``scripts/models/contracts.require_approval_document``.
_GOVERNANCE_APPROVAL_ROLES = frozenset({"model-review-board", "model-risk-owner"})


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
    release_revision: int | None = None
    idempotency_key: str | None = None
    scope: str = "global"

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
            "release_revision": self.release_revision,
            "idempotency_key": self.idempotency_key,
            "scope": self.scope,
        }


@dataclass(frozen=True)
class AliasReconciliationReceipt:
    release_id: str
    model_name: str
    alias: ModelAlias
    authority: str
    resolved_version: str | None
    reason: str
    requested_by: str
    audit_event_id: str
    release_revision: int
    idempotency_key: str
    scope: str = "global"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class LearningHubService:
    def __init__(
        self,
        *,
        repository: LearningHubRepository | None = None,
        registry: MlflowRegistryAdapter | None = None,
        audit_log: AuditRecorder | None = None,
        artifact_store: ArtifactStore | LocalModelArtifactStore | None = None,
        runtime_mode: str | None = None,
        recover_on_startup: bool = True,
        worker_id: str | None = None,
        release_lease_seconds: float = DEFAULT_RELEASE_LEASE_SECONDS,
    ) -> None:
        self.worker_id = worker_id or f"learninghub-worker-{uuid4()}"
        self.release_lease_seconds = float(release_lease_seconds)
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
        if self.production_required and recover_on_startup:
            self.recover_incomplete_releases(
                requested_by="release-startup-recovery",
            )

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

    def record_dq_triage(
        self,
        *,
        dataset_snapshot_id: str,
        action: str,
        rationale: str,
        actor: str,
        correlation_id: str | None = None,
    ) -> DqTriageRecord:
        if not dataset_snapshot_id or not dataset_snapshot_id.strip():
            raise ValueError("dataset_snapshot_id is required")
        if not action or not action.strip():
            raise ValueError("action is required for DQ triage")
        if not rationale or not rationale.strip():
            raise ValueError("rationale is required for DQ triage")
        if not actor or not actor.strip():
            raise ValueError("actor is required for DQ triage")

        snapshot = self.repository.get_dataset_snapshot(dataset_snapshot_id)
        if snapshot is None:
            raise ValueError(f"unknown dataset snapshot {dataset_snapshot_id}")

        triage_id = f"dq_triage_{uuid4().hex[:12]}"
        record = DqTriageRecord(
            triage_id=triage_id,
            dataset_snapshot_id=dataset_snapshot_id,
            action=action.strip(),
            actor=actor.strip(),
            rationale=rationale.strip(),
            time=datetime.now(UTC),
        )
        saved = self.repository.save_dq_triage(record)

        self.audit_log.record(
            AuditEvent(
                event_type="learninghub.dq_triage_recorded.v1",
                actor=actor,
                action="record_dq_triage",
                resource=f"learninghub/dataset-snapshots/{dataset_snapshot_id}/triage",
                outcome="accepted",
                correlation_id=correlation_id or f"corr_{triage_id}",
                metadata={
                    "triage_id": record.triage_id,
                    "action": record.action,
                    "rationale": record.rationale,
                    "time": record.time.isoformat(),
                },
            )
        )
        return saved

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
        expected_release_revision: int,
        idempotency_key: str,
        approval_sha256: str | None = None,
        release_scope: str = "global",
        tenant_id: str | None = None,
    ) -> ModelReleaseDecision:
        self._assert_global_release_scope(
            release_scope=release_scope,
            tenant_id=tenant_id,
        )
        if not idempotency_key.strip():
            raise LearningHubPreconditionRequiredError("release requires idempotency_key")
        command = {
            "model_name": model_name,
            "version": version,
            "release_type": release_type.value,
            "reason": reason,
            "approval_id": approval_id,
            "rollback_target": rollback_target,
            "monitoring_window": monitoring_window,
            "success_criteria": list(success_criteria),
            "fail_criteria": list(fail_criteria),
            "affected_modules": list(affected_modules),
            "requested_by": requested_by,
            "approved_by": approved_by,
            "correlation_id": correlation_id,
            "expected_release_revision": expected_release_revision,
            "approval_sha256": approval_sha256,
            "release_scope": release_scope,
        }
        request_fingerprint = _release_request_fingerprint(command)
        replay = self._resolve_idempotent_replay(
            model_name=model_name,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        if replay is not None:
            if isinstance(replay, ModelReleaseDecision):
                return replay
            raise LearningHubConflictError(
                f"release {replay.release_id} is {replay.state.value}; "
                "operator recovery is required before retry"
            )

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
            requested_by=requested_by,
            approved_by=approved_by,
        )

        try:
            with self.repository.release_guard(
                model_name,
                expected_revision=expected_release_revision,
            ) as release_revision:
                existing = self.repository.get_release_saga_by_idempotency(
                    model_name,
                    idempotency_key,
                )
                if existing is not None:
                    replay = self._assert_idempotency_fingerprint(
                        existing,
                        request_fingerprint,
                    )
                    if isinstance(replay, ModelReleaseDecision):
                        return replay
                    raise LearningHubConflictError(
                        f"release {existing.release_id} is {existing.state.value}"
                    )
                active = self.repository.list_release_sagas(
                    model_name,
                    include_terminal=False,
                )
                if active:
                    raise LearningHubReleaseConflict(
                        f"model {model_name} already has active release "
                        f"{active[0].release_id}"
                    )
                saga = self._build_release_saga(
                    command=command,
                    idempotency_key=idempotency_key,
                    request_fingerprint=request_fingerprint,
                    release_revision=release_revision,
                    model_version=model_version,
                    model_card=model_card,
                    validation_run=validation_run,
                )
                self.repository.save_release_saga(saga)
        except LearningHubReleaseConflict as exc:
            replay = self._resolve_idempotent_replay(
                model_name=model_name,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
            )
            if isinstance(replay, ModelReleaseDecision):
                return replay
            raise LearningHubConflictError(str(exc)) from exc
        return self._execute_release_saga(saga)

    def _build_release_saga(
        self,
        *,
        command: dict[str, Any],
        idempotency_key: str,
        request_fingerprint: str,
        release_revision: int,
        model_version: ModelVersion,
        model_card: ModelCard,
        validation_run: ValidationRun,
    ) -> ModelReleaseSaga:
        model_name = model_version.model_name
        version = model_version.version
        release_type = ReleaseType(command["release_type"])
        rollback_target = command.get("rollback_target")
        current_production = self.repository.get_alias(model_name, ModelAlias.PRODUCTION)
        from_version = current_production.version if current_production else None
        if release_type is ReleaseType.ROLLBACK:
            if current_production is None:
                raise LearningHubError("rollback requires a current production model")
            if version != current_production.version:
                raise LearningHubError(
                    "rollback command version must equal current production: "
                    f"command={version}, production={current_production.version}"
                )
            rollback_target = rollback_target or current_production.rollback_target
            if not rollback_target or rollback_target == version:
                raise LearningHubError(
                    "rollback requires an approved prior production version"
                )
            rollback_version = self._require_model_version(model_name, rollback_target)
            rollback_card = self._require_model_card(model_name, rollback_target)
            if not rollback_card.is_approved:
                raise LearningHubError("rollback target requires approved model card")
        else:
            rollback_version = None

        if release_type is ReleaseType.FULL:
            staged_versions = tuple(
                candidate
                for candidate in self.repository.list_model_versions(model_name)
                if candidate.version == version
                or candidate.stage is ModelStage.PRODUCTION
                or (
                    current_production is not None
                    and candidate.version == current_production.version
                )
            )
            affected_aliases = (
                (
                    ModelAlias.PREVIOUS_PRODUCTION,
                    ModelAlias.PRODUCTION,
                    ModelAlias.CHAMPION,
                )
                if current_production and current_production.version != version
                else (ModelAlias.PRODUCTION, ModelAlias.CHAMPION)
            )
        elif release_type is ReleaseType.ROLLBACK:
            staged_versions = tuple(
                {
                    candidate.version: candidate
                    for candidate in (
                        *self.repository.list_model_versions(model_name),
                        rollback_version,
                    )
                    if candidate is not None
                    and (
                        candidate.stage is ModelStage.PRODUCTION
                        or candidate.version in {version, rollback_target}
                    )
                }.values()
            )
            affected_aliases = (
                ModelAlias.PRODUCTION,
                ModelAlias.CHAMPION,
                ModelAlias.PREVIOUS_PRODUCTION,
            )
        else:
            staged_versions = (model_version,)
            affected_aliases = (
                ModelAlias.SHADOW
                if release_type is ReleaseType.SHADOW
                else ModelAlias.CANARY,
            )

        version_snapshots = tuple(
            {
                snapshot.version: snapshot
                for snapshot in staged_versions
                if snapshot is not None
            }.values()
        )
        alias_snapshots = {
            alias: (
                current.version
                if (current := self.repository.get_alias(model_name, alias)) is not None
                else None
            )
            for alias in affected_aliases
        }
        release_id = f"model-release-{uuid4()}"
        audit_event = AuditEvent(
            event_type="learninghub.model_release.v1",
            actor=str(command["requested_by"]),
            action="rollback" if release_type is ReleaseType.ROLLBACK else "release",
            resource=f"model/{model_name}:{version}",
            outcome="approved",
            correlation_id=str(command["correlation_id"]),
            metadata={
                "release_id": release_id,
                "release_revision": release_revision,
                "release_type": release_type.value,
                "approval_id": command["approval_id"],
                "rollback_target": rollback_target,
                "affected_modules": list(command["affected_modules"]),
                "metrics": dict(validation_run.metrics),
                "scope": "global",
            },
        )
        decision_model = rollback_version if rollback_version is not None else model_version
        decision = ModelReleaseDecision(
            release_id=release_id,
            model_name=model_name,
            from_version=from_version,
            to_version=decision_model.version,
            release_type=release_type,
            reason=str(command["reason"]),
            approval_id=str(command["approval_id"]),
            rollback_target=rollback_target,
            monitoring_window=str(command["monitoring_window"]),
            success_criteria=tuple(command["success_criteria"]),
            fail_criteria=tuple(command["fail_criteria"]),
            affected_modules=tuple(command["affected_modules"]),
            requested_by=str(command["requested_by"]),
            approved_by=str(command["approved_by"]),
            audit_event_id=audit_event.event_id,
            dataset_snapshot_id=decision_model.dataset_snapshot_id,
            feature_schema_version=decision_model.feature_schema_version,
            label_version=decision_model.label_version,
            model_card_checksum=_model_card_checksum(
                self._require_model_card(model_name, decision_model.version)
            ),
            model_artifact_uri=decision_model.artifact_uri,
            release_revision=release_revision,
            idempotency_key=idempotency_key,
            scope="global",
        )
        command = {
            **command,
            "rollback_target": rollback_target,
            "audit_event": audit_event,
            "decision": decision,
        }
        return ModelReleaseSaga(
            release_id=release_id,
            model_name=model_name,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            release_revision=release_revision,
            operation="MODEL_RELEASE",
            command=command,
            version_snapshots=version_snapshots,
            alias_snapshots=tuple(alias_snapshots.items()),
            decision=decision,
            lease_owner=self.worker_id,
            lease_expires_at=self._lease_expiry(),
            fence_token=1,
        )

    def _execute_release_saga(
        self,
        saga: ModelReleaseSaga,
    ) -> ModelReleaseDecision:
        command = saga.command
        release_type = ReleaseType(command["release_type"])
        model_name = saga.model_name
        version = str(command["version"])
        rollback_target = command.get("rollback_target")
        approved_by = str(command["approved_by"])
        model_version = self._require_model_version(model_name, version)
        target_stage = _stage_for_release_type(release_type)
        rollback_version = (
            self._require_model_version(model_name, str(rollback_target))
            if release_type is ReleaseType.ROLLBACK and rollback_target
            else None
        )
        try:
            # Claiming the attempt is itself a fenced write: a worker that was
            # taken over between reserving the revision and starting execution
            # must fail here rather than mutate MLflow.
            saga = self._save_saga(saga, attempt=saga.attempt + 1, last_error=None)
            governance = self._release_governance_metadata(
                model_name=model_name,
                version=(
                    rollback_version.version if rollback_version is not None else version
                ),
                saga=saga,
                command=command,
            )
            if release_type is ReleaseType.FULL:
                alias_mutations: list[tuple[ModelAlias, str | None]] = []
                current_production = self.repository.get_alias(
                    model_name,
                    ModelAlias.PRODUCTION,
                )
                for existing in self.repository.list_model_versions(model_name):
                    if (
                        existing.version != version
                        and existing.stage is ModelStage.PRODUCTION
                        and (
                            current_production is None
                            or existing.version != current_production.version
                        )
                    ):
                        self.registry.transition_stage(
                            model_name=model_name,
                            version=existing.version,
                            stage=ModelStage.RETIRED,
                        )
                        self.repository.save_model_version(
                            existing.with_stage(ModelStage.RETIRED)
                        )
                if current_production and current_production.version != version:
                    self.registry.transition_stage(
                        model_name=model_name,
                        version=current_production.version,
                        stage=ModelStage.RETIRED,
                    )
                    self.repository.save_model_version(
                        current_production.with_stage(ModelStage.RETIRED)
                    )
                    alias_mutations.append(
                        (ModelAlias.PREVIOUS_PRODUCTION, current_production.version)
                    )
                approved_version = (
                    model_version.with_stage(target_stage)
                    ._replace(
                        rollback_target=rollback_target,
                        monitoring_config={
                            **dict(model_version.monitoring_config),
                            **governance,
                        },
                    )
                    .with_approval(approved_by)
                )
                self.registry.sync_release_metadata(approved_version)
                self.registry.transition_stage(
                    model_name=model_name, version=version, stage=target_stage
                )
                self.repository.save_model_version(approved_version)
                alias_mutations.extend(
                    (
                        (ModelAlias.PRODUCTION, version),
                        (ModelAlias.CHAMPION, version),
                    )
                )
                saga = self._save_saga(saga, state=ReleaseSagaState.MODEL_STATE_APPLIED)
                self._synchronize_aliases(model_name, alias_mutations)
                self.registry.assert_release_governance(
                    model_name=model_name,
                    version=version,
                    expected=governance,
                    alias=ModelAlias.PRODUCTION,
                )
            elif release_type is ReleaseType.ROLLBACK:
                if rollback_version is None:
                    raise LearningHubError("rollback target resolution failed")
                for existing in self.repository.list_model_versions(model_name):
                    if existing.stage is not ModelStage.PRODUCTION:
                        continue
                    replacement_stage = (
                        ModelStage.ROLLED_BACK
                        if existing.version == model_version.version
                        else ModelStage.RETIRED
                    )
                    self.registry.transition_stage(
                        model_name=model_name,
                        version=existing.version,
                        stage=replacement_stage,
                    )
                    self.repository.save_model_version(
                        existing.with_stage(replacement_stage)
                    )
                approved_rollback = (
                    rollback_version.with_stage(ModelStage.PRODUCTION)
                    ._replace(
                        monitoring_config={
                            **dict(rollback_version.monitoring_config),
                            **governance,
                        }
                    )
                    .with_approval(approved_by)
                )
                self.registry.sync_release_metadata(approved_rollback)
                self.registry.transition_stage(
                    model_name=model_name,
                    version=rollback_version.version,
                    stage=ModelStage.PRODUCTION,
                )
                self.repository.save_model_version(approved_rollback)
                saga = self._save_saga(saga, state=ReleaseSagaState.MODEL_STATE_APPLIED)
                self._synchronize_aliases(
                    model_name,
                    (
                        (ModelAlias.PRODUCTION, rollback_version.version),
                        (ModelAlias.CHAMPION, rollback_version.version),
                        (ModelAlias.PREVIOUS_PRODUCTION, model_version.version),
                    ),
                )
                self.registry.assert_release_governance(
                    model_name=model_name,
                    version=rollback_version.version,
                    expected=governance,
                    alias=ModelAlias.PRODUCTION,
                )
            else:
                alias = (
                    ModelAlias.SHADOW
                    if release_type is ReleaseType.SHADOW
                    else ModelAlias.CANARY
                )
                staged_version = model_version.with_stage(target_stage)._replace(
                    monitoring_config={
                        **dict(model_version.monitoring_config),
                        **governance,
                    }
                )
                self.registry.sync_release_metadata(staged_version)
                self.registry.transition_stage(
                    model_name=model_name, version=version, stage=target_stage
                )
                self.repository.save_model_version(staged_version)
                saga = self._save_saga(saga, state=ReleaseSagaState.MODEL_STATE_APPLIED)
                self._synchronize_aliases(model_name, ((alias, version),))
                self.registry.assert_release_governance(
                    model_name=model_name,
                    version=version,
                    expected=governance,
                    alias=alias,
                )
            saga = self._save_saga(saga, state=ReleaseSagaState.ALIASES_APPLIED)
            audit_event = command["audit_event"]
            self.audit_log.record(audit_event)
            saga = self._save_saga(
                saga,
                state=ReleaseSagaState.AUDIT_RECORDED,
                audit_event_id=audit_event.event_id,
            )
            decision = command["decision"]
            self.repository.save_release_decision(decision)
            saga = self._save_saga(
                saga,
                state=ReleaseSagaState.RECEIPT_RECORDED,
                decision=decision,
            )
            self._save_saga(saga, state=ReleaseSagaState.COMPLETED)
            return decision
        except LearningHubReleaseFenced as exc:
            raise LearningHubConflictError(
                f"release {saga.release_id} was fenced by a recovery owner; "
                "the recovery owner is authoritative for compensation"
            ) from exc
        except Exception as exc:
            try:
                compensated = self._compensate_release_saga(saga, failure=exc)
            except LearningHubReleaseFenced as fenced:
                raise LearningHubConflictError(
                    f"release {saga.release_id} was fenced by a recovery owner "
                    "before compensation; the recovery owner is authoritative"
                ) from fenced
            if compensated.state is ReleaseSagaState.COMPENSATION_FAILED:
                raise LearningHubError(
                    f"release {saga.release_id} failed and compensation is incomplete: "
                    + "; ".join(compensated.compensation_errors)
                ) from exc
            raise LearningHubError(
                f"release {saga.release_id} failed; durable receipt, aliases, "
                "remote stages, and model metadata were compensated"
            ) from exc

    def _lease_expiry(self) -> datetime:
        return datetime.now(UTC) + timedelta(seconds=self.release_lease_seconds)

    def _save_saga(self, saga: ModelReleaseSaga, **changes: Any) -> ModelReleaseSaga:
        """Persist saga progress while renewing (or releasing) this lease.

        Every in-flight write renews the lease so a long release keeps recovery
        away; reaching a terminal state releases it so the saga is never left
        pinned to a worker that has finished with it.
        """

        candidate = saga.evolve(**changes)
        if candidate.terminal:
            renewed = candidate.evolve(lease_owner=None, lease_expires_at=None)
        else:
            renewed = candidate.evolve(
                lease_owner=self.worker_id,
                lease_expires_at=self._lease_expiry(),
            )
        return self.repository.save_release_saga(renewed)

    def _release_governance_metadata(
        self,
        *,
        model_name: str,
        version: str,
        saga: ModelReleaseSaga,
        command: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Validation and approval facts that must reach the runtime registry.

        These are written into the released ``ModelVersion.monitoring_config``
        and projected onto MLflow tags, so a production alias can never resolve
        to a version whose model card, validation run, actor, and approval are
        unknown to the runtime.
        """

        model_card = self._require_model_card(model_name, version)
        validation_run = self._require_validation_run(model_card.validation_run_id)
        if not validation_run.passed:
            raise LearningHubError(
                f"release target {model_name}:{version} requires a passed validation run"
            )
        if not model_card.is_complete:
            raise LearningHubError(
                f"release target {model_name}:{version} requires a complete model card"
            )
        if not model_card.is_approved:
            raise LearningHubError(
                f"release target {model_name}:{version} requires an approved model card"
            )
        return {
            "release_id": saga.release_id,
            "release_revision": saga.release_revision,
            "approval_id": str(command["approval_id"]),
            "release_scope": "global",
            "requested_by": str(command["requested_by"]),
            "release_approved_by": str(command["approved_by"]),
            "model_card_checksum": _model_card_checksum(model_card),
            "validation_run_id": model_card.validation_run_id,
            "validation_status": validation_run.status.value,
        }

    def _synchronize_aliases(
        self,
        model_name: str,
        mutations: Sequence[tuple[ModelAlias, str | None]],
    ) -> None:
        previous = {
            alias: (
                current.version
                if (current := self.repository.get_alias(model_name, alias)) is not None
                else None
            )
            for alias, _ in mutations
        }
        attempted: list[ModelAlias] = []
        try:
            for alias, version in mutations:
                attempted.append(alias)
                if version is None:
                    self.registry.clear_alias(model_name=model_name, alias=alias)
                else:
                    self.registry.set_alias(
                        model_name=model_name,
                        alias=alias,
                        version=version,
                    )
        except Exception as exc:
            compensation_errors: list[str] = []
            for alias in reversed(attempted):
                try:
                    prior_version = previous[alias]
                    if prior_version is None:
                        self.registry.clear_alias(model_name=model_name, alias=alias)
                    else:
                        self.registry.set_alias(
                            model_name=model_name,
                            alias=alias,
                            version=prior_version,
                        )
                except Exception as compensation_exc:
                    compensation_errors.append(
                        f"{alias.value}={type(compensation_exc).__name__}: "
                        f"{compensation_exc}"
                    )
            if compensation_errors:
                raise LearningHubError(
                    f"release alias synchronization failed for {model_name}; "
                    "compensation incomplete and manual reconciliation is required: "
                    + "; ".join(compensation_errors)
                ) from exc
            raise LearningHubError(
                f"release alias synchronization failed for {model_name}; "
                "all attempted alias mutations were compensated"
            ) from exc

    def _restore_release_state(
        self,
        *,
        model_name: str,
        versions: Sequence[ModelVersion],
        aliases: Mapping[ModelAlias, str | None],
    ) -> list[str]:
        errors: list[str] = []

        for alias, version in reversed(tuple(aliases.items())):
            try:
                self.registry._restore_alias_versions_unsafe(
                    model_name=model_name,
                    alias=alias,
                    remote_version=version,
                    durable_version=version,
                )
            except Exception as exc:
                errors.append(f"alias:{alias.value}={type(exc).__name__}: {exc}")

        for snapshot in reversed(tuple(versions)):
            try:
                self.registry.restore_release_metadata(snapshot)
            except Exception as exc:
                errors.append(
                    f"remote-metadata:{snapshot.version}={type(exc).__name__}: {exc}"
                )
            try:
                self.registry.transition_stage(
                    model_name=model_name,
                    version=snapshot.version,
                    stage=snapshot.stage,
                )
            except Exception as exc:
                errors.append(
                    f"remote-stage:{snapshot.version}={type(exc).__name__}: {exc}"
                )
            try:
                self.repository.save_model_version(snapshot)
            except Exception as exc:
                errors.append(
                    f"durable-model:{snapshot.version}={type(exc).__name__}: {exc}"
                )

        return errors

    def _resolve_idempotent_replay(
        self,
        *,
        model_name: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> ModelReleaseDecision | ModelReleaseSaga | None:
        saga = self.repository.get_release_saga_by_idempotency(
            model_name,
            idempotency_key,
        )
        if saga is None:
            return None
        return self._assert_idempotency_fingerprint(saga, request_fingerprint)

    @staticmethod
    def _assert_idempotency_fingerprint(
        saga: ModelReleaseSaga,
        request_fingerprint: str,
    ) -> object | ModelReleaseSaga:
        if saga.request_fingerprint != request_fingerprint:
            raise LearningHubConflictError(
                f"idempotency key {saga.idempotency_key!r} was reused with "
                "a different release command"
            )
        if saga.state is ReleaseSagaState.COMPLETED:
            if saga.decision is None:
                raise LearningHubError(
                    f"completed release {saga.release_id} has no durable receipt"
                )
            return saga.decision
        return saga

    def _compensate_release_saga(
        self,
        saga: ModelReleaseSaga,
        *,
        failure: BaseException,
    ) -> ModelReleaseSaga:
        compensation_errors: list[str] = []
        saga = self._save_saga(
            saga,
            state=ReleaseSagaState.COMPENSATING,
            last_error=f"{type(failure).__name__}: {failure}",
        )
        try:
            self.repository.delete_release_decision(saga.release_id)
        except Exception as exc:
            compensation_errors.append(
                f"release-decision={type(exc).__name__}: {exc}"
            )
        if saga.operation == "ALIAS_RECONCILIATION":
            try:
                self.registry._restore_alias_versions_unsafe(
                    model_name=saga.model_name,
                    alias=ModelAlias(str(saga.command["alias"])),
                    remote_version=saga.command.get("remote_before_version"),
                    durable_version=saga.command.get("durable_before_version"),
                )
            except Exception as exc:
                compensation_errors.append(
                    f"alias-reconciliation={type(exc).__name__}: {exc}"
                )
        else:
            compensation_errors.extend(
                self._restore_release_state(
                    model_name=saga.model_name,
                    versions=saga.version_snapshots,
                    aliases=dict(saga.alias_snapshots),
                )
            )
        outcome = "compensation_failed" if compensation_errors else "compensated"
        compensation_event = AuditEvent(
            event_type="learninghub.model_release_compensation.v1",
            actor=str(saga.command.get("requested_by", "release-recovery")),
            action="compensate_release",
            resource=f"model/{saga.model_name}:{saga.command.get('version')}",
            outcome=outcome,
            correlation_id=str(
                saga.command.get("correlation_id", "learninghub-release-recovery")
            ),
            metadata={
                "release_id": saga.release_id,
                "release_revision": saga.release_revision,
                "failed_audit_event_id": saga.audit_event_id
                or getattr(saga.command.get("audit_event"), "event_id", None),
                "failure_type": type(failure).__name__,
                "failure": str(failure),
                "compensation_errors": list(compensation_errors),
                "scope": "global",
            },
        )
        try:
            self.audit_log.record(compensation_event)
        except Exception as exc:
            compensation_errors.append(
                f"audit-compensation={type(exc).__name__}: {exc}"
            )
        final_state = (
            ReleaseSagaState.COMPENSATION_FAILED
            if compensation_errors
            else ReleaseSagaState.COMPENSATED
        )
        return self._save_saga(
            saga,
            state=final_state,
            compensation_errors=tuple(compensation_errors),
        )

    def recover_incomplete_releases(
        self,
        *,
        model_name: str | None = None,
        release_id: str | None = None,
        requested_by: str = "release-recovery",
        take_over_live_leases: bool = False,
    ) -> tuple[ModelReleaseSaga, ...]:
        """Recover orphaned release intents after startup or operator request.

        A committed receipt is the deterministic resume point. Every earlier
        state is compensated back to the snapshots persisted before MLflow was
        touched.

        Recovery only claims a saga whose execution lease has expired (or that
        this worker already owns). A release another live worker is still
        driving keeps its lease and is skipped, so startup recovery can never
        compensate a peer mid-flight. Claiming a saga bumps its fencing token,
        which permanently locks out the previous owner's writes; a stale worker
        that wakes up later fails closed instead of resuming into compensated
        state. ``take_over_live_leases`` is an explicit operator override for a
        worker that is known to be dead before its lease expires.
        """

        if release_id is not None:
            candidate = self.repository.get_release_saga(release_id)
            sagas = [] if candidate is None else [candidate]
        else:
            sagas = self.repository.list_release_sagas(
                model_name,
                include_terminal=False,
            )
        recovered: list[ModelReleaseSaga] = []
        for candidate in sagas:
            with self.repository.release_recovery_guard(candidate.model_name):
                saga = self.repository.get_release_saga(candidate.release_id)
                if saga is None:
                    continue
                if saga.terminal:
                    recovered.append(saga)
                    continue
                if (
                    not take_over_live_leases
                    and saga.lease_active()
                    and saga.lease_owner != self.worker_id
                ):
                    self._record_recovery_skipped(saga, requested_by=requested_by)
                    continue
                saga = self._claim_release_saga(saga, requested_by=requested_by)
                durable_receipt = self.repository.get_release_decision(saga.release_id)
                if (
                    saga.state is ReleaseSagaState.RECEIPT_RECORDED
                    and durable_receipt is not None
                ):
                    completed = self._save_saga(
                        saga,
                        state=ReleaseSagaState.COMPLETED,
                        decision=durable_receipt,
                    )
                    recovered.append(completed)
                    continue
                recovered.append(
                    self._compensate_release_saga(
                        saga,
                        failure=LearningHubError(
                            f"recovered orphaned release from {saga.state.value}"
                        ),
                    )
                )
        return tuple(recovered)

    def _claim_release_saga(
        self,
        saga: ModelReleaseSaga,
        *,
        requested_by: str,
    ) -> ModelReleaseSaga:
        command = dict(saga.command)
        command["requested_by"] = requested_by
        return self._save_saga(
            saga,
            command=command,
            fence_token=saga.fence_token + 1,
        )

    def _record_recovery_skipped(
        self,
        saga: ModelReleaseSaga,
        *,
        requested_by: str,
    ) -> None:
        self.audit_log.record(
            AuditEvent(
                event_type="learninghub.model_release_recovery.v1",
                actor=requested_by,
                action="skip_recovery",
                resource=f"model/{saga.model_name}:{saga.command.get('version')}",
                outcome="lease_held_by_live_worker",
                correlation_id=str(
                    saga.command.get("correlation_id", "learninghub-release-recovery")
                ),
                metadata={
                    "release_id": saga.release_id,
                    "release_revision": saga.release_revision,
                    "state": saga.state.value,
                    "lease_owner": saga.lease_owner,
                    "lease_expires_at": (
                        saga.lease_expires_at.isoformat()
                        if saga.lease_expires_at is not None
                        else None
                    ),
                    "fence_token": saga.fence_token,
                    "scope": "global",
                },
            )
        )

    def reconcile_alias(
        self,
        *,
        model_name: str,
        alias: ModelAlias,
        authority: str,
        reason: str,
        requested_by: str,
        expected_release_revision: int,
        idempotency_key: str,
        release_scope: str = "global",
        tenant_id: str | None = None,
        correlation_id: str = "learninghub-alias-reconciliation",
    ) -> AliasReconciliationReceipt:
        """Governed repair path for remote/durable alias drift."""

        self._assert_global_release_scope(
            release_scope=release_scope,
            tenant_id=tenant_id,
        )
        if authority not in {"remote", "durable"}:
            raise LearningHubError(
                "alias reconciliation authority must be 'remote' or 'durable'"
            )
        if not idempotency_key.strip():
            raise LearningHubPreconditionRequiredError(
                "alias reconciliation requires idempotency_key"
            )
        command = {
            "model_name": model_name,
            "alias": alias.value,
            "authority": authority,
            "reason": reason,
            "requested_by": requested_by,
            "correlation_id": correlation_id,
            "expected_release_revision": expected_release_revision,
            "release_scope": "global",
        }
        fingerprint = _release_request_fingerprint(command)
        existing = self.repository.get_release_saga_by_idempotency(
            model_name,
            idempotency_key,
        )
        if existing is not None:
            self._assert_idempotency_fingerprint(existing, fingerprint)
            if (
                existing.state is ReleaseSagaState.COMPLETED
                and isinstance(existing.decision, AliasReconciliationReceipt)
            ):
                return existing.decision
            raise LearningHubConflictError(
                f"reconciliation {existing.release_id} is {existing.state.value}"
            )

        remote_before = self.registry.get_by_alias(model_name=model_name, alias=alias)
        durable_before = self.repository.get_alias(model_name, alias)
        command["remote_before_version"] = (
            remote_before.version if remote_before is not None else None
        )
        command["durable_before_version"] = (
            durable_before.version if durable_before is not None else None
        )
        release_id = f"alias-reconciliation-{uuid4()}"
        try:
            with self.repository.release_guard(
                model_name,
                expected_revision=expected_release_revision,
            ) as release_revision:
                if self.repository.list_release_sagas(
                    model_name,
                    include_terminal=False,
                ):
                    raise LearningHubReleaseConflict(
                        f"model {model_name} already has an active release"
                    )
                saga = ModelReleaseSaga(
                    release_id=release_id,
                    model_name=model_name,
                    idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint,
                    release_revision=release_revision,
                    operation="ALIAS_RECONCILIATION",
                    command=command,
                    version_snapshots=(),
                    alias_snapshots=(
                        (
                            alias,
                            durable_before.version if durable_before is not None else None,
                        ),
                    ),
                    lease_owner=self.worker_id,
                    lease_expires_at=self._lease_expiry(),
                    fence_token=1,
                )
                self.repository.save_release_saga(saga)
        except LearningHubReleaseConflict as exc:
            existing = self.repository.get_release_saga_by_idempotency(
                model_name,
                idempotency_key,
            )
            if existing is not None:
                replay = self._assert_idempotency_fingerprint(existing, fingerprint)
                if isinstance(replay, AliasReconciliationReceipt):
                    return replay
            raise LearningHubConflictError(str(exc)) from exc

        try:
            resolved = self.registry._reconcile_alias_unsafe(
                model_name=model_name,
                alias=alias,
                authority=authority,
            )
            saga = self._save_saga(saga, state=ReleaseSagaState.ALIASES_APPLIED)
            audit_event = self.audit_log.record(
                AuditEvent(
                    event_type="learninghub.alias_reconciliation.v1",
                    actor=requested_by,
                    action="reconcile_alias",
                    resource=f"model/{model_name}/alias/{alias.value}",
                    outcome="completed",
                    correlation_id=correlation_id,
                    metadata={
                        "release_id": release_id,
                        "authority": authority,
                        "reason": reason,
                        "before_remote": remote_before.version
                        if remote_before is not None
                        else None,
                        "before_durable": durable_before.version
                        if durable_before is not None
                        else None,
                        "resolved_version": resolved.version
                        if resolved is not None
                        else None,
                        "scope": "global",
                    },
                )
            )
            receipt = AliasReconciliationReceipt(
                release_id=release_id,
                model_name=model_name,
                alias=alias,
                authority=authority,
                resolved_version=resolved.version if resolved is not None else None,
                reason=reason,
                requested_by=requested_by,
                audit_event_id=audit_event.event_id,
                release_revision=saga.release_revision,
                idempotency_key=idempotency_key,
            )
            self.repository.save_release_decision(receipt)
            self._save_saga(
                saga,
                state=ReleaseSagaState.COMPLETED,
                decision=receipt,
                audit_event_id=audit_event.event_id,
            )
            return receipt
        except LearningHubReleaseFenced as exc:
            raise LearningHubConflictError(
                f"alias reconciliation {release_id} was fenced by a recovery owner"
            ) from exc
        except Exception as exc:
            compensated = self._compensate_release_saga(saga, failure=exc)
            if compensated.state is ReleaseSagaState.COMPENSATION_FAILED:
                raise LearningHubError(
                    f"alias reconciliation {release_id} failed and compensation "
                    "requires operator repair"
                ) from exc
            raise LearningHubError(
                f"alias reconciliation {release_id} failed and was compensated"
            ) from exc

    @staticmethod
    def _assert_global_release_scope(
        *,
        release_scope: str,
        tenant_id: str | None,
    ) -> None:
        if release_scope != "global" or tenant_id is not None:
            raise LearningHubError(
                "production model aliases are global; tenant-scoped release "
                "commands are not supported"
            )

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
        expected_release_revision: int,
        idempotency_key: str,
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
            expected_release_revision=expected_release_revision,
            idempotency_key=idempotency_key,
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
        requested_by: str,
        approved_by: str,
    ) -> None:
        if not approval_id:
            raise LearningHubError("release requires approval_id")
        self._assert_release_authority(
            model_card=model_card,
            requested_by=requested_by,
            approved_by=approved_by,
        )
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

    @staticmethod
    def _assert_release_authority(
        *,
        model_card: ModelCard,
        requested_by: str,
        approved_by: str,
    ) -> None:
        """Bind the release actors to a recorded, independent approval.

        ``requested_by`` is the authenticated caller (the API layer refuses to
        take it from the request body) and ``approved_by`` must name an approver
        already recorded on the model card in a governance role. Self-review is
        rejected, so a single identity can never both request and approve a
        production release.
        """

        requester = str(requested_by or "").strip()
        approver = str(approved_by or "").strip()
        if not requester:
            raise LearningHubError("release requires an authenticated requested_by actor")
        if not approver:
            raise LearningHubError("release requires an approved_by actor")
        if requester == approver:
            raise LearningHubError("model release self-review is prohibited")
        approvals = [
            approval for approval in model_card.approvals if approval.decision == "approved"
        ]
        matching = [approval for approval in approvals if approval.approver == approver]
        if not matching:
            raise LearningHubError(
                f"approved_by {approver!r} does not match a recorded model card approval"
            )
        if not any(approval.role in _GOVERNANCE_APPROVAL_ROLES for approval in matching):
            raise LearningHubError(
                f"model card approval by {approver!r} is not held in a governance role "
                + " or ".join(sorted(_GOVERNANCE_APPROVAL_ROLES))
            )

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


def _release_request_fingerprint(command: Mapping[str, Any]) -> str:
    payload = {
        key: value
        for key, value in command.items()
        if key not in {"correlation_id", "audit_event", "decision"}
    }
    return f"sha256:{sha256(_canonical_json_bytes(payload)).hexdigest()}"


def _fingerprint_inputs(inputs: Sequence[Mapping[str, Any]]) -> str:
    return f"sha256:{sha256(_canonical_json_bytes(list(inputs))).hexdigest()}"


__all__ = [
    "DEFAULT_RELEASE_LEASE_SECONDS",
    "AliasReconciliationReceipt",
    "LearningHubConflictError",
    "LearningHubError",
    "LearningHubPreconditionRequiredError",
    "LearningHubService",
    "ModelReleaseDecision",
    "ReleaseType",
]
