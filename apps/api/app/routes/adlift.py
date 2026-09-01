from __future__ import annotations

from typing import Any

from models.shared_ml import (
    ProductionExecutionConfigurationError,
    production_execution_required,
)
from shared.audit import AuditEvent, InMemoryAuditLog
from shared.infrastructure.persistence.job_receipts import (
    JobQueue,
    JobReceiptIncompleteError,
    TenantScopedJobReceiptStore,
)
from shared.jobs.queue import InMemoryJobQueue

try:
    from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
    from pydantic import BaseModel, Field
except ModuleNotFoundError:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment]
else:
    from modules.adlift.application import AdLiftService
    from modules.adlift.infrastructure import InMemoryAdLiftRepository
    from modules.adlift.workers import AdLiftBatchResult, run_adlift_incrementality_batch


    class AdLiftIncrementalityJobPayload(BaseModel):
        campaigns: list[dict[str, Any]] = Field(default_factory=list)
        generated_at: str | None = None
        idempotency_key: str | None = None


    class AdLiftJobStore:
        def __init__(self) -> None:
            self._jobs: dict[str, AdLiftBatchResult] = {}
            self._idempotency_index: dict[str, str] = {}

        def put(
            self, result: AdLiftBatchResult, *, idempotency_key: str | None = None
        ) -> tuple[AdLiftBatchResult, bool]:
            if idempotency_key and idempotency_key in self._idempotency_index:
                return self._jobs[self._idempotency_index[idempotency_key]], False
            self._jobs[result.job_id] = result
            if idempotency_key:
                self._idempotency_index[idempotency_key] = result.job_id
            return result, True

        def get_by_idempotency_key(self, idempotency_key: str | None) -> AdLiftBatchResult | None:
            if not idempotency_key:
                return None
            job_id = self._idempotency_index.get(idempotency_key)
            if job_id is None:
                return None
            return self._jobs[job_id]

        def get(self, job_id: str) -> AdLiftBatchResult | None:
            return self._jobs.get(job_id)


    def create_adlift_router(
        *,
        repository: InMemoryAdLiftRepository | None = None,
        audit_log: InMemoryAuditLog | None = None,
        job_store: AdLiftJobStore | None = None,
        job_queue: JobQueue | None = None,
        require_durable_jobs: bool | None = None,
        runtime_mode: str | None = None,
    ) -> APIRouter:
        from apps.api.app.routes._common import runtime_binding_guard
        from apps.api.oday_api.security.dependencies import build_engine, require_permission
        from shared.auth import Action

        production_required = production_execution_required(runtime_mode)
        durable_jobs_required = (
            production_required
            if require_durable_jobs is None
            else require_durable_jobs
        )
        adlift_repository = (
            repository
            if production_required
            else repository or InMemoryAdLiftRepository()
        )
        active_audit_log = audit_log or InMemoryAuditLog()
        authz_engine = build_engine(audit_log=active_audit_log)
        local_job_queue = None if durable_jobs_required else InMemoryJobQueue()
        composition_error: ProductionExecutionConfigurationError | None = None
        try:
            AdLiftService(
                repository=adlift_repository,
                runtime_mode=runtime_mode,
            )
        except ProductionExecutionConfigurationError as exc:
            composition_error = exc

        require_runtime_binding = runtime_binding_guard(composition_error)

        router = APIRouter(
            prefix="/adlift",
            tags=["adlift"],
            dependencies=[Depends(require_runtime_binding)],
        )

        def receipt_store(request: Request) -> TenantScopedJobReceiptStore:
            active_queue = job_queue
            if active_queue is None:
                app = request.scope.get("app")
                active_queue = getattr(
                    getattr(app, "state", None), "job_queue", None
                )
            active_queue = active_queue or local_job_queue
            if active_queue is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "DURABLE_JOB_RECEIPT_STORE_REQUIRED",
                        "message": "AdLift production jobs require durable persistence",
                    },
                )
            store = TenantScopedJobReceiptStore(
                queue=active_queue,
                service="adlift.incrementality",
            )
            if durable_jobs_required and not store.is_durable:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "DURABLE_JOB_RECEIPT_STORE_REQUIRED",
                        "message": "AdLift production jobs require durable persistence",
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
                        "message": "AdLift production jobs require tenant scope",
                    },
                )
            return "__local__"

        @router.post("/incrementality-jobs", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_permission("adlift", Action.CREATE, engine=authz_engine))])
        def create_incrementality_job(
            body: AdLiftIncrementalityJobPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            effective_key = body.idempotency_key or idempotency_key
            active_tenant_id = tenant_id(request)
            if job_store is not None:
                if durable_jobs_required:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail={
                            "code": "DURABLE_JOB_RECEIPT_STORE_REQUIRED",
                            "message": "AdLift production jobs reject process-local stores",
                        },
                    )
                result = job_store.get_by_idempotency_key(effective_key)
                created = result is None
                if result is None:
                    result, created = job_store.put(
                        run_adlift_incrementality_batch(
                            campaigns=body.campaigns,
                            generated_at=body.generated_at,
                            repository=adlift_repository,
                            runtime_mode=runtime_mode,
                        ),
                        idempotency_key=effective_key,
                    )
                payload = result.to_dict()
            else:
                active_receipts = receipt_store(request)
                try:
                    replay = active_receipts.get_by_idempotency_key(
                        active_tenant_id,
                        effective_key,
                    )
                except JobReceiptIncompleteError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "code": "JOB_RECEIPT_INCOMPLETE",
                            "message": str(exc),
                        },
                    ) from exc
                if replay is not None:
                    payload = replay
                    created = False
                else:
                    computed = run_adlift_incrementality_batch(
                        campaigns=body.campaigns,
                        generated_at=body.generated_at,
                        repository=adlift_repository,
                        runtime_mode=runtime_mode,
                    )
                    try:
                        payload, created = active_receipts.put_completed(
                            tenant_id=active_tenant_id,
                            idempotency_key=effective_key,
                            correlation_id=request.state.correlation_id,
                            build_receipt=lambda _job_id: computed.to_dict(),
                        )
                    except JobReceiptIncompleteError as exc:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail={
                                "code": "JOB_RECEIPT_INCOMPLETE",
                                "message": str(exc),
                            },
                        ) from exc
            audit_event = active_audit_log.record(
                AuditEvent(
                    event_type="adlift.incrementality_evaluated.v1",
                    actor="system",
                    action="run_model",
                    resource="adlift/incrementality-job",
                    outcome="accepted" if created else "idempotent_replay",
                    correlation_id=request.state.correlation_id,
                    job_id=str(payload["job_id"]),
                    metadata={
                        "idempotency_key": effective_key,
                        "campaign_count": len(body.campaigns),
                        "created": created,
                        "tenant_id": active_tenant_id,
                    },
                )
            )
            payload["created"] = created
            payload["audit_event_id"] = audit_event.event_id
            payload["correlation_id"] = request.state.correlation_id
            return payload

        @router.get("/incrementality-jobs/{job_id}", dependencies=[Depends(require_permission("adlift", Action.VIEW, engine=authz_engine))])
        def get_incrementality_job(job_id: str, request: Request) -> dict[str, Any]:
            if job_store is not None:
                if durable_jobs_required:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail={
                            "code": "DURABLE_JOB_RECEIPT_STORE_REQUIRED",
                            "message": "AdLift production jobs reject process-local stores",
                        },
                    )
                result = job_store.get(job_id)
                if result is not None:
                    return result.to_dict()
            else:
                result = receipt_store(request).get(tenant_id(request), job_id)
                if result is not None:
                    return result
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="incrementality job not found",
            )

        @router.get("/reports", dependencies=[Depends(require_permission("adlift", Action.VIEW, engine=authz_engine))])
        def list_reports(evidence_level: str | None = None) -> dict[str, Any]:
            # An unassessable report carries no ladder level (ADR-0004 D3), so it
            # matches no level filter -- and must not be dereferenced here.
            reports = [
                report
                for report in adlift_repository.latest_reports()
                if evidence_level is None
                or (
                    report.evidence_level is not None
                    and report.evidence_level.value == evidence_level
                )
            ]
            return {"items": [report.to_dict() for report in reports], "count": len(reports)}

        @router.get("/reports/{campaign_id}", dependencies=[Depends(require_permission("adlift", Action.VIEW, engine=authz_engine))])
        def get_report(campaign_id: str) -> dict[str, Any] | None:
            report = adlift_repository.latest_for_campaign(campaign_id)
            if report is None:
                return None
            return report.to_dict()

        return router


    __all__ = [
        "AdLiftIncrementalityJobPayload",
        "AdLiftJobStore",
        "create_adlift_router",
    ]
