from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from models.shared_ml.artifact_store import (
    ArtifactKind,
    InMemoryArtifactStore,
    build_model_registry_evidence,
)
from models.shared_ml.model_card import ModelCard, ModelCardApproval, ModelRiskLevel
from models.shared_ml.oss_capabilities import inspect_oss_stack
from models.shared_ml.registry import ModelStage, ModelVersion
from models.shared_ml.validation import MetricThreshold, SegmentMetric
from modules.learninghub.application import (
    LearningHubConflictError,
    LearningHubError,
    LearningHubPreconditionRequiredError,
    LearningHubService,
    ReleaseType,
)
from modules.learninghub.runtime import (
    LearningHubRuntimeConfigurationError,
    learninghub_production_required,
)
from shared.audit import AuditEvent, InMemoryAuditLog

try:
    from fastapi import APIRouter, Depends, HTTPException, Request, status
    from pydantic import BaseModel, Field
except ModuleNotFoundError:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment]
else:
    from modules.learninghub.infrastructure import (
        InMemoryLearningHubRepository,
        MlflowRegistryAdapter,
    )


    class DatasetSnapshotPayload(BaseModel):
        rows: list[dict[str, Any]] = Field(min_length=1)
        dataset_snapshot_id: str | None = None
        require_training_eligible: bool = True
        # These identifiers bind the snapshot to the governed feature/label
        # registries.  The service enforces the BLOCKED feature/label gate when
        # they are supplied, so they must remain part of the HTTP contract.
        feature_set_id: str | None = None
        label_set_id: str | None = None


    class DqTriagePayload(BaseModel):
        action: str = Field(min_length=1)
        rationale: str = Field(min_length=1)
        actor: str | None = None


    class ThresholdPayload(BaseModel):
        metric_name: str = Field(min_length=1)
        min_value: float | None = None
        max_value: float | None = None
        warning_min_value: float | None = None
        warning_max_value: float | None = None
        max_degradation: float | None = None
        max_relative_degradation: float | None = None
        warning_max_degradation: float | None = None
        warning_max_relative_degradation: float | None = None
        higher_is_better: bool | None = None


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


    class ModelCardApprovalPayload(BaseModel):
        decision: str = "approved"


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


    class MonitorGuardrailPayload(BaseModel):
        metric_name: str = Field(min_length=1)
        min_value: float | None = None
        max_value: float | None = None
        warning_min_value: float | None = None
        warning_max_value: float | None = None
        max_degradation: float | None = None
        max_relative_degradation: float | None = None
        warning_max_degradation: float | None = None
        warning_max_relative_degradation: float | None = None
        higher_is_better: bool | None = None


    class ReleaseMonitorPayload(BaseModel):
        observed_metrics: dict[str, float] = Field(min_length=1)
        # A monitor run must carry the comparison snapshot when it is not
        # available from the released model registry record.  ``None`` keeps
        # the existing fallback to the released model's metrics for callers
        # that use that durable baseline.
        baseline_metrics: dict[str, float] | None = None
        guardrails: list[MonitorGuardrailPayload] = Field(min_length=1)
        # Bound from the authenticated principal, like release actors.
        evaluated_by: str | None = None


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
        # ``requested_by`` is bound from the authenticated principal. It stays in
        # the schema only so a client that echoes it back gets an explicit
        # mismatch rejection instead of silently having its value ignored.
        requested_by: str | None = None
        approved_by: str = Field(min_length=1)
        # Optional in the schema, mandatory in the contract: a missing
        # precondition is answered with 428, not a schema-level 422.
        expected_release_revision: int | None = Field(default=None, ge=0)
        idempotency_key: str | None = None
        release_scope: str = "global"


    def create_learninghub_router(
        *,
        repository: InMemoryLearningHubRepository | None = None,
        artifact_store: Any | None = None,
        audit_log: InMemoryAuditLog | None = None,
        registry: MlflowRegistryAdapter | None = None,
        runtime_mode: str | None = None,
    ) -> APIRouter:
        from apps.api.app.routes._common import runtime_binding_guard
        from apps.api.oday_api.security.dependencies import build_engine, require_permission
        from shared.auth import Action

        production_runtime_required = learninghub_production_required(runtime_mode)
        active_repository = (
            repository
            if production_runtime_required
            else repository or InMemoryLearningHubRepository()
        )
        active_artifacts = (
            artifact_store
            if production_runtime_required
            else artifact_store or InMemoryArtifactStore()
        )
        active_audit_log = audit_log or InMemoryAuditLog()
        authz_engine = build_engine(audit_log=active_audit_log)
        composition_error: LearningHubRuntimeConfigurationError | None = None
        if production_runtime_required and registry is None:
            composition_error = LearningHubRuntimeConfigurationError(
                "Learning Hub production requires an injected remote MLflow registry"
            )
            service = None
        else:
            try:
                service = LearningHubService(
                    repository=active_repository,
                    registry=registry,
                    audit_log=active_audit_log,
                    artifact_store=active_artifacts,
                    runtime_mode=runtime_mode,
                )
            except LearningHubRuntimeConfigurationError as exc:
                composition_error = exc
                service = None

        require_runtime_binding = runtime_binding_guard(composition_error)

        router = APIRouter(
            prefix="/learninghub",
            tags=["learninghub"],
            dependencies=[Depends(require_runtime_binding)],
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
                    feature_set_id=body.feature_set_id,
                    label_set_id=body.label_set_id,
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
                _trusted_actor(request),
                "register_dataset_snapshot",
                f"learninghub/dataset-snapshots/{snapshot.dataset_snapshot_id}",
                {"entity_count": snapshot.entity_count},
            )
            return payload

        @router.post(
            "/dataset-snapshots/{dataset_snapshot_id}/triage",
            status_code=status.HTTP_201_CREATED,
            dependencies=[Depends(require_permission("data_quality", Action.UPDATE, engine=authz_engine))],
        )
        def record_dq_triage(
            dataset_snapshot_id: str, body: DqTriagePayload, request: Request
        ) -> dict[str, Any]:
            actor = _trusted_actor(request)
            if body.actor is not None and body.actor.strip() != actor:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "UNTRUSTED_TRIAGE_ACTOR",
                        "message": "actor must match the authenticated principal",
                    },
                )
            try:
                record = service.record_dq_triage(
                    dataset_snapshot_id=dataset_snapshot_id,
                    action=body.action,
                    rationale=body.rationale,
                    actor=actor,
                    correlation_id=getattr(request.state, "correlation_id", None),
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(exc),
                ) from exc
            return record.to_dict()

        @router.get(
            "/dataset-snapshots/{dataset_snapshot_id}/triage",
            dependencies=[Depends(require_permission("data_quality", Action.VIEW, engine=authz_engine))],
        )
        def list_dq_triages(dataset_snapshot_id: str) -> dict[str, Any]:
            records = active_repository.list_dq_triages(dataset_snapshot_id)
            return {"items": [r.to_dict() for r in records], "count": len(records)}

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
                _trusted_actor(request),
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
            requested_by = _trusted_actor(request)
            if body.requested_by is not None and body.requested_by.strip() != requested_by:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "UNTRUSTED_RELEASE_ACTOR",
                        "message": (
                            "requested_by must match the authenticated principal; "
                            "release actors are never taken from the request body"
                        ),
                    },
                )
            if body.approved_by.strip() == requested_by:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "MODEL_RELEASE_SELF_REVIEW",
                        "message": (
                            "the authenticated requester cannot also be the release "
                            "approver"
                        ),
                    },
                )
            missing_preconditions = sorted(
                name
                for name, present in (
                    ("expected_release_revision", body.expected_release_revision is not None),
                    ("idempotency_key", bool((body.idempotency_key or "").strip())),
                )
                if not present
            )
            if missing_preconditions:
                raise HTTPException(
                    status_code=status.HTTP_428_PRECONDITION_REQUIRED,
                    detail={
                        "code": "RELEASE_PRECONDITION_REQUIRED",
                        "message": (
                            "release commands must bind "
                            + " and ".join(missing_preconditions)
                        ),
                    },
                )
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
                    requested_by=requested_by,
                    approved_by=body.approved_by,
                    correlation_id=request.state.correlation_id,
                    expected_release_revision=int(body.expected_release_revision),
                    idempotency_key=str(body.idempotency_key),
                    release_scope=body.release_scope,
                )
            except LearningHubPreconditionRequiredError as exc:
                raise HTTPException(
                    status_code=status.HTTP_428_PRECONDITION_REQUIRED,
                    detail={
                        "code": "RELEASE_PRECONDITION_REQUIRED",
                        "message": str(exc),
                    },
                ) from exc
            except LearningHubConflictError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "RELEASE_CONFLICT",
                        "message": str(exc),
                    },
                ) from exc
            except (LearningHubError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(exc),
                ) from exc
            payload = decision.to_dict()
            payload["correlation_id"] = request.state.correlation_id
            return payload

        @router.post("/releases/{release_id}/monitor", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permission("model", Action.PUBLISH, engine=authz_engine))])
        def monitor_release(
            release_id: str, body: ReleaseMonitorPayload, request: Request
        ) -> dict[str, Any]:
            evaluated_by = _trusted_actor(request)
            if body.evaluated_by is not None and body.evaluated_by.strip() != evaluated_by:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "UNTRUSTED_MONITOR_ACTOR",
                        "message": (
                            "evaluated_by must match the authenticated principal"
                        ),
                    },
                )
            try:
                assessment = service.monitor_release(
                    release_id=release_id,
                    observed_metrics=body.observed_metrics,
                    guardrails=[_threshold(item) for item in body.guardrails],
                    baseline_metrics=body.baseline_metrics,
                    evaluated_by=evaluated_by,
                    correlation_id=request.state.correlation_id,
                )
            except (LearningHubError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(exc),
                ) from exc
            payload = assessment.to_dict()
            payload["correlation_id"] = request.state.correlation_id
            return payload

        @router.get(
            "/models",
            dependencies=[Depends(require_permission("model", Action.VIEW, engine=authz_engine))],
        )
        def list_models() -> dict[str, Any]:
            versions = active_repository.list_all_model_versions()
            rows = sorted(
                (version.to_dict() for version in versions),
                key=lambda item: (
                    str(item.get("model_name", "")),
                    str(item.get("version", "")),
                ),
            )
            return {"items": rows, "count": len(rows)}

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

        @router.post(
            "/models/{model_name}/versions/{version}/approval",
            dependencies=[
                Depends(
                    require_permission("model", Action.APPROVE, engine=authz_engine)
                )
            ],
        )
        def approve_model_card(
            model_name: str,
            version: str,
            body: ModelCardApprovalPayload,
            request: Request,
        ) -> dict[str, Any]:
            card = active_repository.get_model_card(model_name, version)
            if card is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"unknown model card {model_name}:{version}",
                )
            approver = _trusted_actor(request)
            if approver == card.owner:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "MODEL_CARD_SELF_REVIEW",
                        "message": "a model card owner cannot approve their own card",
                    },
                )
            decision = body.decision.strip().lower()
            if decision not in {"approved", "rejected"}:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="decision must be approved or rejected",
                )
            approval = ModelCardApproval(
                approver=approver,
                role="model-review-board",
                decision=decision,
            )
            updated = replace(
                card,
                approvals=tuple(
                    item for item in card.approvals if item.approver != approver
                )
                + (approval,),
            )
            active_repository.save_model_card(updated)
            return updated.to_dict()

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

        @router.get(
            "/oss-capabilities",
            dependencies=[Depends(require_permission("model", Action.VIEW, engine=authz_engine))],
        )
        def get_oss_capabilities() -> dict[str, Any]:
            statuses = inspect_oss_stack()
            return {
                "status": "ready" if all(item.available for item in statuses) else "blocked",
                "items": [item.to_dict() for item in statuses],
                "count": len(statuses),
                "unavailable_count": sum(not item.available for item in statuses),
            }

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
            "feature_set_id": snapshot.feature_set_id,
            "label_set_id": snapshot.label_set_id,
            "created_at": snapshot.created_at.isoformat(),
        }


    def _threshold(item: ThresholdPayload | MonitorGuardrailPayload) -> MetricThreshold:
        return MetricThreshold(
            metric_name=item.metric_name,
            min_value=item.min_value,
            max_value=item.max_value,
            warning_min_value=item.warning_min_value,
            warning_max_value=item.warning_max_value,
            max_degradation=item.max_degradation,
            max_relative_degradation=item.max_relative_degradation,
            warning_max_degradation=item.warning_max_degradation,
            warning_max_relative_degradation=item.warning_max_relative_degradation,
            higher_is_better=item.higher_is_better,
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
            # Approval provenance is established only by the authenticated
            # approval endpoint; registration payloads cannot mint it.
            approvals=(),
        )


    def _trusted_actor(request: Request) -> str:
        """Resolve the authenticated caller; never trust a body-supplied actor.

        ``require_permission`` puts the verified principal on request state, so
        an actor identity that reaches the audit log or a release approval is
        always the one the auth boundary established.
        """

        principal = getattr(request.state, "operator_principal", None)
        subject_id = str(getattr(principal, "subject_id", "") or "").strip()
        if not subject_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "AUTHENTICATED_ACTOR_REQUIRED",
                    "message": (
                        "Learning Hub actions require an authenticated principal"
                    ),
                },
            )
        return subject_id


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
        "DqTriagePayload",
        "ModelVersionPayload",
        "ReleasePayload",
        "create_learninghub_router",
    ]
