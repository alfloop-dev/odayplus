from __future__ import annotations

from typing import Any

from models.shared_ml import (
    ModelBinding,
    ProductionModelInputError,
    ProductionModelRuntime,
    ProductionModelRuntimeError,
    ScoringInputUnavailableError,
    production_model_execution_required,
    require_live_inputs,
)
from shared.audit import AuditEvent, InMemoryAuditLog
from shared.infrastructure.persistence.job_receipts import (
    JobQueue,
    JobReceiptIncompleteError,
    TenantScopedJobReceiptStore,
)
from shared.infrastructure.persistence.postgresql import PostgresEngine
from shared.jobs.queue import InMemoryJobQueue

try:
    from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
    from pydantic import BaseModel, Field
except ModuleNotFoundError:  # pragma: no cover - optional API dependency
    APIRouter = None  # type: ignore[assignment]
else:
    from modules.opsboard.application.network_reviews import (
        NetworkReviewConflict,
        NetworkReviewNotFound,
        NetworkReviewRoleError,
        NetworkReviewRuntimeUnavailable,
    )
    from modules.sitescore.application.reporting import SiteScoreReportService
    from modules.sitescore.infrastructure.repositories import InMemorySiteScoreRepository
    from modules.sitescore.runtime import (
        SiteScoreRuntimeConfigurationError,
        sitescore_production_required,
    )
    from shared.workflow.sitescore import (
        CandidateSiteRealizationHook,
        DecisionAction,
        SiteScoreDecisionError,
        SiteScoreDecisionWorkflow,
    )

    class SiteScoreScoreJobPayload(BaseModel):
        features: list[dict[str, Any]] = Field(default_factory=list)
        prediction_origin_time: str | None = None
        idempotency_key: str | None = None

    class OpenDecisionPayload(BaseModel):
        report_id: str
        created_by: str = Field(min_length=1)
        submission_summary: str | None = None
        reviewer_group_id: str | None = None
        expected_actor_subject_id: str | None = None
        expected_tenant_id: str | None = None

    class DecisionPayload(BaseModel):
        action: str
        actor: str = Field(min_length=1)
        reason: str = ""
        expected_version: int | None = None
        expected_actor_subject_id: str | None = None
        expected_tenant_id: str | None = None

    def create_sitescore_router(
        *,
        repository: InMemorySiteScoreRepository | None = None,
        workflow: SiteScoreDecisionWorkflow | None = None,
        sitescore_decision_repository_for_tenant: Any | None = None,
        sitescore_repository_for_tenant: Any | None = None,
        require_canonical_report_reads: bool = False,
        realization_hook: CandidateSiteRealizationHook | None = None,
        audit_log: InMemoryAuditLog | None = None,
        model_binding: ModelBinding | None = None,
        model_runtime: ProductionModelRuntime | None = None,
        require_production_model: bool | None = None,
        job_queue: JobQueue | None = None,
        require_durable_jobs: bool | None = None,
        runtime_mode: str | None = None,
        review_service_resolver: Any | None = None,
    ) -> APIRouter:
        from apps.api.app.routes._common import resolve_tenant_id, runtime_binding_guard
        from apps.api.oday_api.security.dependencies import build_engine, require_permission
        from shared.auth import Action

        production_runtime_required = sitescore_production_required(runtime_mode)
        production_model_required = (
            production_runtime_required
            or (
                production_model_execution_required()
                if require_production_model is None
                else require_production_model
            )
        )
        durable_jobs_required = (
            production_runtime_required
            or (
                production_model_execution_required()
                if require_durable_jobs is None
                else require_durable_jobs
            )
        )
        active_audit_log = audit_log or InMemoryAuditLog()
        authz_engine = build_engine(audit_log=active_audit_log)
        site_repository = (
            repository
            if production_runtime_required
            else repository or InMemorySiteScoreRepository()
        )
        canonical_report_reads = (
            production_runtime_required or require_canonical_report_reads
            or isinstance(getattr(site_repository, "engine", None), PostgresEngine)
        )
        # ODP-FLOW-002: the injected realization hook (durable-backed in E2E mode)
        # is the one exposed via /sitescore/realized, so a realized approval
        # survives a restart. Avoid double-registering it on a provided workflow.
        active_realization_hook = realization_hook or CandidateSiteRealizationHook()
        if workflow is None:
            decision_workflow = SiteScoreDecisionWorkflow(
                audit_log=active_audit_log, hooks=[active_realization_hook]
            )
        else:
            decision_workflow = workflow
            if active_realization_hook not in decision_workflow.hooks:
                decision_workflow.register_hook(active_realization_hook)
        composition_error: SiteScoreRuntimeConfigurationError | None = None
        if production_runtime_required and model_runtime is None:
            composition_error = SiteScoreRuntimeConfigurationError(
                "SiteScore production requires an injected remote MLflow runtime"
            )
            service = None
        else:
            try:
                service = SiteScoreReportService(
                    repository=site_repository,
                    model_runtime=model_runtime,
                    require_production_model=production_model_required,
                    runtime_mode=runtime_mode,
                )
            except SiteScoreRuntimeConfigurationError as exc:
                composition_error = exc
                service = None
        local_job_queue = None if durable_jobs_required else InMemoryJobQueue()

        require_runtime_binding = runtime_binding_guard(composition_error)

        def require_retained_read_binding():
            if canonical_report_reads and (
                    site_repository is None or isinstance(site_repository, InMemorySiteScoreRepository)
                    or not isinstance(getattr(site_repository, "engine", None), PostgresEngine)
                    or sitescore_repository_for_tenant is None or review_service_resolver is None):
                raise HTTPException(503, detail="CANONICAL_REPORT_READ_BINDING_REQUIRED")

        def retained_report_repository(request: Request):
            if not canonical_report_reads:
                require_runtime_binding()
                return site_repository
            tid = resolve_tenant_id(request)
            principal = request.state.operator_principal
            if (principal is None or not principal.authenticated or not principal.subject_id
                    or principal.tenant_id != tid):
                raise HTTPException(403, detail="AUTHENTICATED_REPORT_SCOPE_REQUIRED")
            require_retained_read_binding()
            scoped = sitescore_repository_for_tenant(tid)
            if scoped is None or getattr(scoped, "engine", None) is None or getattr(scoped, "tenant_id", None) != tid:
                raise HTTPException(503, detail="CANONICAL_REPORT_READ_BINDING_REQUIRED")
            request.state.operator_tenant_id = tid
            try:
                review_service_resolver(request).authorize_native_report_candidate(
                    candidate_id=None, principal=principal, report_repository=scoped,
                )
            except (NetworkReviewConflict, NetworkReviewRuntimeUnavailable) as exc:
                raise HTTPException(503, detail="CANONICAL_REPORT_READ_BINDING_REQUIRED") from exc
            return scoped

        def authorize_retained_candidate(request: Request, scoped: Any, candidate_id: str):
            if canonical_report_reads:
                review_service_resolver(request).authorize_native_report_candidate(
                    candidate_id=candidate_id, principal=request.state.operator_principal,
                    report_repository=scoped,
                )

        def require_retained_report_identity(report: Any, candidate_id: str | None = None):
            if (report is None or any(not isinstance(getattr(report, key, None), str) or not getattr(report, key).strip()
                    for key in ("candidate_site_id", "report_id", "sitescore_run_id", "model_version"))
                    or type(getattr(report, "report_version", None)) is not int or report.report_version < 1
                    or (candidate_id is not None and report.candidate_site_id != candidate_id)):
                raise HTTPException(503, detail="CANONICAL_RETAINED_REPORT_IDENTITY_UNAVAILABLE")

        def workflow_for_request(request: Request) -> SiteScoreDecisionWorkflow:
            tid = resolve_tenant_id(request)
            if sitescore_decision_repository_for_tenant is not None:
                scoped_store = sitescore_decision_repository_for_tenant(tid)
                if scoped_store is not None:
                    return SiteScoreDecisionWorkflow(
                        audit_log=active_audit_log,
                        hooks=decision_workflow.hooks,
                        policy_version=decision_workflow.policy_version,
                        store=scoped_store,
                        approval_store=decision_workflow._approval_store,
                    )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to resolve tenant-scoped sitescore decision store",
                )
            return decision_workflow

        router = APIRouter(
            prefix="/sitescore",
            tags=["sitescore"],

        )

        def receipt_store(request: Request) -> TenantScopedJobReceiptStore:
            active_queue = job_queue
            if active_queue is None:
                app = request.scope.get("app")
                active_queue = getattr(getattr(app, "state", None), "job_queue", None)
            active_queue = active_queue or local_job_queue
            if active_queue is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "DURABLE_JOB_RECEIPT_STORE_REQUIRED",
                        "message": "SiteScore production jobs require durable persistence",
                    },
                )
            store = TenantScopedJobReceiptStore(
                queue=active_queue,
                service="sitescore.score",
            )
            if durable_jobs_required and not store.is_durable:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "DURABLE_JOB_RECEIPT_STORE_REQUIRED",
                        "message": "SiteScore production jobs require durable persistence",
                    },
                )
            return store

        def tenant_id(request: Request) -> str:
            principal = getattr(request.state, "operator_principal", None)
            scope = getattr(principal, "scope", None)
            value = getattr(scope, "tenant_id", None)
            if value:
                return str(value)
            if durable_jobs_required:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "TENANT_SCOPE_REQUIRED",
                        "message": "SiteScore production jobs require tenant scope",
                    },
                )
            return "__local__"

        @router.post(
            "/score-jobs",
            status_code=status.HTTP_202_ACCEPTED,
            dependencies=[
                Depends(require_permission("sitescore", Action.EXECUTE, engine=authz_engine))
            ],
        )
        @router.post(
            "/reports",
            status_code=status.HTTP_202_ACCEPTED,
            dependencies=[
                Depends(require_permission("sitescore", Action.EXECUTE, engine=authz_engine))
            ],
        )
        def create_score_job(
            body: SiteScoreScoreJobPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            require_runtime_binding()
            if production_runtime_required or production_model_required or canonical_report_reads:
                raise HTTPException(status_code=503, detail={"code": "SITESCORE_EXECUTION_CONTEXT_REQUIRED", "message": "Use the candidate execution request endpoint with its exact committed report context"})
            effective_key = body.idempotency_key or idempotency_key
            active_tenant_id = tenant_id(request)
            active_receipts = receipt_store(request)
            try:
                replay = active_receipts.get_by_idempotency_key(active_tenant_id, effective_key)
            except JobReceiptIncompleteError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "JOB_RECEIPT_INCOMPLETE", "message": str(exc)},
                ) from exc
            if replay is not None:
                replay["created"] = False
                return replay

            # Fail closed: refuse a fresh run when live inputs are absent.
            try:
                require_live_inputs(body.features, service="sitescore")
            except ScoringInputUnavailableError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
                ) from exc

            try:
                execution = service.score_candidates_with_execution(
                    body.features,
                    prediction_origin_time=_parse_origin(body.prediction_origin_time),
                )
            except ProductionModelInputError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"code": exc.code, "message": str(exc)},
                ) from exc
            except ProductionModelRuntimeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"code": exc.code, "message": str(exc)},
                ) from exc
            reports = list(execution.reports)
            executed_binding = (
                execution.model_inference.binding
                if execution.model_inference is not None
                else model_binding
            )

            def build_receipt(job_id: str) -> dict[str, Any]:
                metadata: dict[str, Any] = {
                    "idempotency_key": effective_key,
                    "candidate_count": len(reports),
                    "tenant_id": active_tenant_id,
                }
                if executed_binding is not None:
                    metadata["model_binding"] = executed_binding.to_audit_metadata()
                audit_event = active_audit_log.record(
                    AuditEvent(
                        event_type="sitescore.scored.v1",
                        actor="system",
                        action="run_model",
                        resource="sitescore/score-job",
                        outcome="accepted",
                        correlation_id=request.state.correlation_id,
                        job_id=job_id,
                        metadata=metadata,
                    )
                )
                payload = {
                    "job_id": job_id,
                    "status": "succeeded",
                    "reports": [report.to_dict() for report in reports],
                    "summaries": [report.to_summary_dict() for report in reports],
                    "audit_event_id": audit_event.event_id,
                    "correlation_id": request.state.correlation_id,
                    "created": True,
                }
                if executed_binding is not None:
                    payload["model_binding"] = executed_binding.to_audit_metadata()
                return payload

            try:
                payload, created = active_receipts.put_completed(
                    tenant_id=active_tenant_id,
                    idempotency_key=effective_key,
                    correlation_id=request.state.correlation_id,
                    build_receipt=build_receipt,
                )
            except JobReceiptIncompleteError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "JOB_RECEIPT_INCOMPLETE", "message": str(exc)},
                ) from exc
            payload["created"] = created
            return payload

        @router.get(
            "/reports",
            dependencies=[
                Depends(require_retained_read_binding),
                Depends(require_permission("sitescore", Action.VIEW, engine=authz_engine))
            ],
        )
        def list_reports(request: Request) -> dict[str, Any]:
            scoped = retained_report_repository(request)
            reports = []
            candidate_ids, report_ids = set(), set()
            for report in scoped.list_latest():
                if canonical_report_reads:
                    require_retained_report_identity(report)
                    if report.candidate_site_id in candidate_ids or report.report_id in report_ids:
                        raise HTTPException(503, detail="CANONICAL_RETAINED_REPORT_HISTORY_CONFLICT")
                    candidate_ids.add(report.candidate_site_id)
                    report_ids.add(report.report_id)
                try:
                    authorize_retained_candidate(request, scoped, report.candidate_site_id)
                except (NetworkReviewNotFound, NetworkReviewRoleError):
                    continue
                except (NetworkReviewConflict, NetworkReviewRuntimeUnavailable) as exc:
                    raise HTTPException(503, detail="CANONICAL_REPORT_READ_BINDING_REQUIRED") from exc
                reports.append(report)
            return {
                "items": [report.to_summary_dict() for report in reports],
                "count": len(reports),
            }

        @router.get(
            "/reports/{candidate_site_id}",
            dependencies=[
                Depends(require_retained_read_binding),
                Depends(require_permission("sitescore", Action.VIEW, engine=authz_engine))
            ],
        )
        def candidate_reports(candidate_site_id: str, request: Request) -> dict[str, Any]:
            scoped = retained_report_repository(request)
            try:
                authorize_retained_candidate(request, scoped, candidate_site_id)
            except (NetworkReviewNotFound, NetworkReviewRoleError) as exc:
                raise decision_error(exc) from exc
            except (NetworkReviewConflict, NetworkReviewRuntimeUnavailable) as exc:
                raise HTTPException(503, detail="CANONICAL_REPORT_READ_BINDING_REQUIRED") from exc
            latest = scoped.latest(candidate_site_id)
            if latest is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="report not found"
                )
            history = scoped.history(candidate_site_id)
            if canonical_report_reads:
                require_retained_report_identity(latest, candidate_site_id)
                for report in history:
                    require_retained_report_identity(report, candidate_site_id)
                if (not history or len({r.report_id for r in history}) != len(history)
                        or len({r.report_version for r in history}) != len(history)
                        or sum(r == latest for r in history) != 1
                        or max(r.report_version for r in history) != latest.report_version):
                    raise HTTPException(503, detail="CANONICAL_RETAINED_REPORT_HISTORY_CONFLICT")
            return {
                "latest": latest.to_dict(),
                "versions": [report.to_dict() for report in history],
                "version_count": len(history),
            }

        def decision_error(exc: Exception) -> HTTPException:
            return HTTPException(404 if isinstance(exc, NetworkReviewNotFound) else
                403 if isinstance(exc, NetworkReviewRoleError) else
                503 if isinstance(exc, NetworkReviewRuntimeUnavailable) else 409, detail=str(exc))

        @router.post(
            "/decisions",
            status_code=status.HTTP_201_CREATED,
            dependencies=[
                Depends(require_permission("sitescore", Action.EXECUTE, engine=authz_engine))
            ],
        )
        def open_decision(body: OpenDecisionPayload, request: Request,
                          idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                          if_actor_match: str | None = Header(default=None, alias="If-Actor-Match")) -> dict[str, Any]:
            principal = request.state.operator_principal
            if review_service_resolver is not None:
                request.state.operator_tenant_id = resolve_tenant_id(request)
                active = review_service_resolver(request)
                # The scoped report lookup belongs to this tenant's owner; the
                # unpartitioned captured site_repository is never consulted.
                try:
                    if (if_actor_match != principal.subject_id or body.expected_actor_subject_id != principal.subject_id
                            or body.expected_tenant_id != principal.tenant_id):
                        raise NetworkReviewConflict("ACTOR_CONTEXT_CONFLICT")
                    receipt = active.submit_native_report(report_id=body.report_id, principal=principal,
                        request_payload=body.model_dump(), idempotency_key=idempotency_key,
                        correlation_id=request.state.correlation_id)
                    return {**receipt['review'], 'submission_receipt': receipt}
                except (NetworkReviewConflict, NetworkReviewNotFound, NetworkReviewRoleError, NetworkReviewRuntimeUnavailable) as exc:
                    raise decision_error(exc) from exc
            if sitescore_decision_repository_for_tenant is not None:
                raise HTTPException(503, detail="CANONICAL_REVIEW_OWNER_REQUIRED")
            report = site_repository.get_report(body.report_id)
            if report is None:
                raise HTTPException(404, detail="report not found")
            active_wf = workflow_for_request(request)
            decision = active_wf.open_decision(report, created_by=principal.subject_id,
                correlation_id=request.state.correlation_id, tenant_id=resolve_tenant_id(request), verified_creator=True)
            decision = active_wf.submit_for_review(decision.decision_id, submitted_by=principal.subject_id,
                correlation_id=request.state.correlation_id, verified_submitter=True)
            return decision.to_dict()

        @router.post(
            "/decisions/{decision_id}/decision",
            dependencies=[
                Depends(require_permission("sitescore", Action.APPROVE, engine=authz_engine))
            ],
        )
        def decide(decision_id: str, body: DecisionPayload, request: Request,
                   if_actor_match: str | None = Header(default=None, alias="If-Actor-Match")) -> dict[str, Any]:
            try:
                action = DecisionAction(body.action)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
                ) from exc
            principal = request.state.operator_principal
            if review_service_resolver is not None:
                request.state.operator_tenant_id = resolve_tenant_id(request)
                if (if_actor_match != principal.subject_id or body.expected_actor_subject_id != principal.subject_id
                        or body.expected_tenant_id != principal.tenant_id):
                    raise HTTPException(409, detail="ACTOR_CONTEXT_CONFLICT")
                try:
                    outcome = review_service_resolver(request).decide_native_review(review_id=decision_id,
                        action=action.value, reason=body.reason, principal=principal, expected_version=body.expected_version,
                        correlation_id=request.state.correlation_id)
                    return {**outcome, "correlation_id": request.state.correlation_id}
                except (NetworkReviewConflict, NetworkReviewNotFound, NetworkReviewRoleError, NetworkReviewRuntimeUnavailable) as exc:
                    if isinstance(exc, NetworkReviewRoleError):
                        from apps.api.oday_api.security.dependencies import (
                            record_review_owner_denial,
                        )
                        record_review_owner_denial(request, audit_log=active_audit_log, resource_id=decision_id, reason=str(exc), module='sitescore')
                    raise decision_error(exc) from exc
            if sitescore_decision_repository_for_tenant is not None:
                raise HTTPException(503, detail="CANONICAL_REVIEW_OWNER_REQUIRED")
            active_wf = workflow_for_request(request)
            current = active_wf.get(decision_id)
            if current and (
                getattr(current, "submission_actor_verified", False) and current.submitted_by == principal.subject_id
                or getattr(current, "created_by_verified", False) and current.created_by == principal.subject_id
            ):
                raise HTTPException(403, detail="proposer cannot approve own submission")
            try:
                outcome = active_wf.decide(
                    decision_id,
                    action=action,
                    actor=principal.subject_id,
                    reason=body.reason,
                    correlation_id=request.state.correlation_id,
                )
            except SiteScoreDecisionError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
                ) from exc
            payload = outcome.to_dict()
            payload["correlation_id"] = request.state.correlation_id
            return payload

        @router.get(
            "/decisions/{decision_id}",
            dependencies=[
                Depends(require_permission("sitescore", Action.VIEW, engine=authz_engine))
            ],
        )
        def get_decision(decision_id: str, request: Request) -> dict[str, Any]:
            if review_service_resolver is not None:
                request.state.operator_tenant_id = resolve_tenant_id(request)
                try:
                    return review_service_resolver(request).read_native_review(review_id=decision_id,
                        principal=request.state.operator_principal)
                except (NetworkReviewConflict, NetworkReviewNotFound, NetworkReviewRoleError, NetworkReviewRuntimeUnavailable) as exc:
                    raise decision_error(exc) from exc
            if sitescore_decision_repository_for_tenant is not None:
                raise HTTPException(503, detail="CANONICAL_REVIEW_OWNER_REQUIRED")
            active_wf = workflow_for_request(request)
            decision = active_wf.get(decision_id)
            if decision is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="decision not found"
                )
            return decision.to_dict()

        @router.get(
            "/realized",
            dependencies=[
                Depends(require_permission("sitescore", Action.VIEW, engine=authz_engine))
            ],
        )
        def list_realized() -> dict[str, Any]:
            require_runtime_binding()
            realized = active_realization_hook.list_realized()
            return {
                "items": [site.to_dict() for site in realized],
                "count": len(realized),
            }

        @router.get("/prediction-runs/{prediction_run_id}")
        def get_prediction_run(prediction_run_id: str) -> dict[str, Any]:
            require_runtime_binding()
            run = site_repository.get_prediction_run(prediction_run_id)
            if run is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="prediction run not found"
                )
            predictions = site_repository.get_predictions(prediction_run_id)
            return {
                "prediction_run": {
                    "prediction_run_id": run.prediction_run_id,
                    "model_version_id": run.model_version_id,
                    "feature_snapshot_time": run.feature_snapshot_time.isoformat(),
                    "prediction_origin_time": run.prediction_origin_time.isoformat(),
                    "prediction_horizon": run.prediction_horizon,
                    "run_status": run.run_status,
                },
                "predictions": [
                    {
                        "prediction_id": p.prediction_id,
                        "prediction_run_id": p.prediction_run_id,
                        "entity_type": p.entity_type,
                        "entity_id": p.entity_id,
                        "target_name": p.target_name,
                        "p10_value": p.p10_value,
                        "p50_value": p.p50_value,
                        "p90_value": p.p90_value,
                        "unit": p.unit,
                        "confidence": p.effective_confidence,
                        "confidenceProvenance": p.effective_confidence_provenance,
                    }
                    for p in predictions
                ],
            }

        @router.get("/runs/{sitescore_run_id}")
        def get_sitescore_run(sitescore_run_id: str) -> dict[str, Any]:
            require_runtime_binding()
            run = site_repository.get_sitescore_run(sitescore_run_id)
            if run is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="sitescore run not found"
                )
            return {
                "sitescore_run_id": run.sitescore_run_id,
                "candidate_site_id": run.candidate_site_id,
                "target_format_code": run.target_format_code,
                "prediction_run_id": run.prediction_run_id,
                "m1_p10": run.m1_p10,
                "m1_p50": run.m1_p50,
                "m1_p90": run.m1_p90,
                "m3_p10": run.m3_p10,
                "m3_p50": run.m3_p50,
                "m3_p90": run.m3_p90,
                "m6_p10": run.m6_p10,
                "m6_p50": run.m6_p50,
                "m6_p90": run.m6_p90,
                "m12_p10": run.m12_p10,
                "m12_p50": run.m12_p50,
                "m12_p90": run.m12_p90,
                "payback_p50_months": run.payback_p50_months,
                "decision_recommendation": run.decision_recommendation,
                "report_uri": run.report_uri,
            }

        return router

    def _parse_origin(value: str | None) -> Any:
        if value is None:
            return None
        from datetime import UTC, datetime

        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed

    __all__ = [
        "DecisionPayload",
        "OpenDecisionPayload",
        "SiteScoreScoreJobPayload",
        "create_sitescore_router",
    ]
