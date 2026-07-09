from __future__ import annotations

import json
from typing import Any

from models.shared_ml.artifact_store import (
    ArtifactKind,
    InMemoryArtifactStore,
    build_model_registry_evidence,
)
from models.shared_ml.model_card import ModelCard, ModelCardApproval, ModelRiskLevel
from models.shared_ml.registry import ModelStage, ModelVersion
from models.shared_ml.validation import MetricThreshold, SegmentMetric
from modules.learninghub.application import LearningHubError, LearningHubService, ReleaseType
from shared.audit import AuditEvent, InMemoryAuditLog

try:
    from fastapi import APIRouter, HTTPException, Request, status, Depends
    from pydantic import BaseModel, Field
except ModuleNotFoundError:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment]
else:
    from modules.learninghub.infrastructure import InMemoryLearningHubRepository


    class DatasetSnapshotPayload(BaseModel):
        rows: list[dict[str, Any]] = Field(min_length=1)
        dataset_snapshot_id: str | None = None
        require_training_eligible: bool = True


    class ThresholdPayload(BaseModel):
        metric_name: str = Field(min_length=1)
        min_value: float | None = None
        max_value: float | None = None
        warning_min_value: float | None = None
        warning_max_value: float | None = None


    class SegmentMetricPayload(BaseModel):
        segment_name: str = Field(min_length=1)
        segment_value: str = Field(min_length=1)
        metrics: dict[str, float]
        record_count: int = Field(ge=0)


    class ModelCardPayload(BaseModel):
        owner: str = Field(min_length=1)
        risk_level: str = "R2"
        intended_use: str = Field(min_length=1)
        not_intended_use: str = Field(min_length=1)
        feature_set_id: str = Field(min_length=1)
        label_set_id: str = Field(min_length=1)
        training_period: str = Field(min_length=1)
        validation_period: str = Field(min_length=1)
        algorithm: str = Field(min_length=1)
        baseline: str = Field(min_length=1)
        metrics_summary: dict[str, float]
        segment_metrics: list[dict[str, Any]] = Field(default_factory=list)
        calibration_summary: dict[str, Any] = Field(default_factory=dict)
        explainability_method: str = "shap"
        limitations: list[str] = Field(default_factory=list)
        known_biases: list[str] = Field(default_factory=list)
        privacy_review: str = "PASSED"
        security_review: str = "PASSED"
        release_status: str = "DEV"
        rollback_conditions: list[str] = Field(min_length=1)
        approvals: list[dict[str, str]] = Field(default_factory=list)


    class ModelVersionPayload(BaseModel):
        version: str = Field(min_length=1)
        dataset_snapshot_id: str = Field(min_length=1)
        metrics: dict[str, float]
        baseline_metrics: dict[str, float]
        thresholds: list[ThresholdPayload] = Field(min_length=1)
        segment_metrics: list[SegmentMetricPayload] = Field(default_factory=list)
        calibration_summary: dict[str, Any] = Field(default_factory=dict)
        min_training_records: int = Field(default=1, ge=1)
        feature_schema_version: str = Field(min_length=1)
        label_version: str = Field(min_length=1)
        artifact_kind: str = ArtifactKind.MODEL.value
        artifact_content: str = Field(min_length=1)
        artifact_content_type: str = "application/octet-stream"
        artifact_metadata: dict[str, Any] = Field(default_factory=dict)
        stage: str = ModelStage.DEV.value
        run_id: str | None = None
        git_sha: str | None = None
        rollback_target: str | None = None
        monitoring_config: dict[str, Any] = Field(default_factory=dict)
        model_card: ModelCardPayload


    class ReleasePayload(BaseModel):
        model_name: str = Field(min_length=1)
        version: str = Field(min_length=1)
        release_type: str = Field(min_length=1)
        reason: str = Field(min_length=1)
        approval_id: str = Field(min_length=1)
        rollback_target: str | None = None
        monitoring_window: str = Field(min_length=1)
        success_criteria: list[str] = Field(min_length=1)
        fail_criteria: list[str] = Field(min_length=1)
        affected_modules: list[str] = Field(default_factory=list)
        requested_by: str = "system"
        approved_by: str = "model-review-board"


    def create_learninghub_router(
        *,
        repository: InMemoryLearningHubRepository | None = None,
        artifact_store: InMemoryArtifactStore | None = None,
        audit_log: InMemoryAuditLog | None = None,
    ) -> APIRouter:
        from shared.auth import Action
        from apps.api.oday_api.security.dependencies import build_engine, require_permission

        router = APIRouter(prefix="/learninghub", tags=["learninghub"])
        active_repository = repository or InMemoryLearningHubRepository()
        active_artifacts = artifact_store or InMemoryArtifactStore()
        active_audit_log = audit_log or InMemoryAuditLog()
        authz_engine = build_engine(audit_log=active_audit_log)
        service = LearningHubService(
            repository=active_repository,
            audit_log=active_audit_log,
        )

        @router.post("/dataset-snapshots", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permission("model", Action.CREATE, engine=authz_engine))])
        def register_dataset_snapshot(
            body: DatasetSnapshotPayload, request: Request
        ) -> dict[str, Any]:
            try:
                snapshot = service.register_dataset_snapshot(
                    body.rows,
                    dataset_snapshot_id=body.dataset_snapshot_id,
                    require_training_eligible=body.require_training_eligible,
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(exc),
                ) from exc
            payload = _dataset_to_dict(snapshot)
            payload["audit_event_id"] = _record_audit(
                active_audit_log,
                request,
                "learninghub.dataset_registered.v1",
                "system",
                "register_dataset_snapshot",
                f"learninghub/dataset-snapshots/{snapshot.dataset_snapshot_id}",
                {"entity_count": snapshot.entity_count},
            )
            return payload

        @router.post("/models/{model_name}/versions", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permission("model", Action.CREATE, engine=authz_engine))])
        def register_model_version(
            model_name: str, body: ModelVersionPayload, request: Request
        ) -> dict[str, Any]:
            try:
                validation = service.validate_candidate(
                    model_name=model_name,
                    model_version=body.version,
                    dataset_snapshot_id=body.dataset_snapshot_id,
                    metrics=body.metrics,
                    baseline_metrics=body.baseline_metrics,
                    thresholds=[_threshold(item) for item in body.thresholds],
                    segment_metrics=[_segment(item) for item in body.segment_metrics],
                    calibration_summary=body.calibration_summary,
                    min_training_records=body.min_training_records,
                )
                artifact = active_artifacts.put_artifact(
                    model_name=model_name,
                    version=body.version,
                    kind=body.artifact_kind,
                    data=body.artifact_content.encode("utf-8"),
                    content_type=body.artifact_content_type,
                    metadata=body.artifact_metadata,
                )
                model_version = ModelVersion(
                    model_name=model_name,
                    version=body.version,
                    artifact_uri=artifact.uri,
                    dataset_snapshot_id=body.dataset_snapshot_id,
                    feature_schema_version=body.feature_schema_version,
                    label_version=body.label_version,
                    metrics=body.metrics,
                    stage=ModelStage(body.stage),
                    run_id=body.run_id,
                    git_sha=body.git_sha,
                    rollback_target=body.rollback_target,
                    monitoring_config=body.monitoring_config,
                )
                card = _model_card(
                    model_name,
                    body.version,
                    body.dataset_snapshot_id,
                    body.model_card,
                    validation.validation_run_id,
                )
                registered = service.register_model_version(
                    model_version=model_version,
                    model_card=card,
                    validation_run=validation,
                )
            except (LearningHubError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(exc),
                ) from exc
            payload = {
                "model_version": registered.to_dict(),
                "validation": validation.to_dict(),
                "model_card": card.to_dict(),
                "artifact": artifact.to_dict(),
                "artifact_verified": active_artifacts.verify(artifact.artifact_id),
            }
            payload["audit_event_id"] = _record_audit(
                active_audit_log,
                request,
                "learninghub.model_registered.v1",
                "system",
                "register_model_version",
                f"learninghub/models/{model_name}/versions/{body.version}",
                {
                    "validation_status": validation.status.value,
                    "artifact_digest": artifact.content_digest,
                },
            )
            return payload

        @router.post("/releases", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permission("model", Action.PUBLISH, engine=authz_engine))])
        def request_release(body: ReleasePayload, request: Request) -> dict[str, Any]:
            try:
                decision = service.request_release(
                    model_name=body.model_name,
                    version=body.version,
                    release_type=ReleaseType(body.release_type.upper()),
                    reason=body.reason,
                    approval_id=body.approval_id,
                    rollback_target=body.rollback_target,
                    monitoring_window=body.monitoring_window,
                    success_criteria=body.success_criteria,
                    fail_criteria=body.fail_criteria,
                    affected_modules=body.affected_modules,
                    requested_by=body.requested_by,
                    approved_by=body.approved_by,
                    correlation_id=request.state.correlation_id,
                )
            except (LearningHubError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(exc),
                ) from exc
            payload = decision.to_dict()
            payload["correlation_id"] = request.state.correlation_id
            return payload

        @router.get("/models/{model_name}", dependencies=[Depends(require_permission("model", Action.VIEW, engine=authz_engine))])
        def get_model(model_name: str) -> dict[str, Any]:
            versions = active_repository.list_model_versions(model_name)
            return {
                "model_name": model_name,
                "versions": [version.to_dict() for version in versions],
                "release_decisions": [
                    _to_dict(decision)
                    for decision in active_repository.list_release_decisions()
                    if getattr(decision, "model_name", None) == model_name
                ],
            }

        @router.get("/models/{model_name}/evidence", dependencies=[Depends(require_permission("model", Action.VIEW, engine=authz_engine))])
        def get_model_evidence(model_name: str) -> dict[str, Any]:
            return build_model_registry_evidence(
                model_name=model_name,
                repository=active_repository,
                artifact_store=active_artifacts,
            ).to_dict()

        @router.get("/releases", dependencies=[Depends(require_permission("model", Action.VIEW, engine=authz_engine))])
        def list_releases(model_name: str | None = None) -> dict[str, Any]:
            releases = active_repository.list_release_decisions()
            if model_name is not None:
                releases = [
                    release
                    for release in releases
                    if getattr(release, "model_name", None) == model_name
                ]
            return {"items": [_to_dict(release) for release in releases], "count": len(releases)}

        return router


    def _dataset_to_dict(snapshot: Any) -> dict[str, Any]:
        return {
            "dataset_snapshot_id": snapshot.dataset_snapshot_id,
            "view_versions": dict(snapshot.view_versions),
            "entity_count": snapshot.entity_count,
            "training_record_count": snapshot.training_record_count,
            "scoring_record_count": snapshot.scoring_record_count,
            "feature_snapshot_time": snapshot.feature_snapshot_time.isoformat(),
            "prediction_origin_time": snapshot.prediction_origin_time.isoformat(),
            "time_range": [value.isoformat() for value in snapshot.time_range],
            "source_snapshot_ids": list(snapshot.source_snapshot_ids),
            "created_at": snapshot.created_at.isoformat(),
        }


    def _threshold(item: ThresholdPayload) -> MetricThreshold:
        return MetricThreshold(
            metric_name=item.metric_name,
            min_value=item.min_value,
            max_value=item.max_value,
            warning_min_value=item.warning_min_value,
            warning_max_value=item.warning_max_value,
        )


    def _segment(item: SegmentMetricPayload) -> SegmentMetric:
        return SegmentMetric(
            segment_name=item.segment_name,
            segment_value=item.segment_value,
            metrics=item.metrics,
            record_count=item.record_count,
        )


    def _model_card(
        model_name: str,
        version: str,
        dataset_snapshot_id: str,
        body: ModelCardPayload,
        validation_run_id: str,
    ) -> ModelCard:
        return ModelCard(
            model_name=model_name,
            model_version=version,
            owner=body.owner,
            risk_level=ModelRiskLevel(body.risk_level),
            intended_use=body.intended_use,
            not_intended_use=body.not_intended_use,
            dataset_snapshot_id=dataset_snapshot_id,
            validation_run_id=validation_run_id,
            feature_set_id=body.feature_set_id,
            label_set_id=body.label_set_id,
            training_period=body.training_period,
            validation_period=body.validation_period,
            algorithm=body.algorithm,
            baseline=body.baseline,
            metrics_summary=body.metrics_summary,
            segment_metrics=body.segment_metrics,
            calibration_summary=body.calibration_summary,
            explainability_method=body.explainability_method,
            limitations=body.limitations,
            known_biases=body.known_biases,
            privacy_review=body.privacy_review,
            security_review=body.security_review,
            release_status=body.release_status,
            rollback_conditions=body.rollback_conditions,
            approvals=tuple(
                ModelCardApproval(
                    approver=str(approval["approver"]),
                    role=str(approval.get("role", "model-review-board")),
                    decision=str(approval.get("decision", "approved")),
                )
                for approval in body.approvals
            ),
        )


    def _record_audit(
        audit_log: InMemoryAuditLog,
        request: Request,
        event_type: str,
        actor: str,
        action: str,
        resource: str,
        metadata: dict[str, Any],
    ) -> str:
        event = audit_log.record(
            AuditEvent(
                event_type=event_type,
                actor=actor,
                action=action,
                resource=resource,
                outcome="accepted",
                correlation_id=request.state.correlation_id,
                metadata=metadata,
            )
        )
        return event.event_id


    def _to_dict(value: Any) -> dict[str, Any]:
        if hasattr(value, "to_dict"):
            return value.to_dict()
        return json.loads(json.dumps(value, default=str))


    __all__ = [
        "DatasetSnapshotPayload",
        "ModelVersionPayload",
        "ReleasePayload",
        "create_learninghub_router",
    ]
