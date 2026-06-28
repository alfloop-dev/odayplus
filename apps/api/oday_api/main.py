from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from apps.api.oday_api.routes.heatzone import HeatZoneResultStore, create_heatzone_router
from shared.audit import AuditEvent, InMemoryAuditLog
from shared.jobs import InMemoryJobQueue, JobRequest
from shared.observability import CORRELATION_ID_HEADER, CorrelationContext

API_VERSION = "0.1.0"


def health_payload() -> dict[str, str]:
    return {"status": "ok", "service": "oday-api"}


def health_detail_payload(*, correlation_id: str) -> dict[str, str]:
    return {
        **health_payload(),
        "version": API_VERSION,
        "time": datetime.now(UTC).isoformat(),
        "correlation_id": correlation_id,
    }


try:
    from fastapi import FastAPI, Header, HTTPException, Request, Response, status
    from pydantic import BaseModel, Field
except ModuleNotFoundError:  # pragma: no cover - dependency added by backend task
    app: Any = None
else:
    class JobCreatePayload(BaseModel):
        job_type: str = Field(min_length=1)
        payload: dict[str, Any] = Field(default_factory=dict)
        idempotency_key: str | None = None


    def create_app(
        *,
        audit_log: InMemoryAuditLog | None = None,
        job_queue: InMemoryJobQueue | None = None,
        heatzone_store: HeatZoneResultStore | None = None,
        forecastops_repository: Any = None,
        sitescore_repository: Any = None,
        sitescore_workflow: Any = None,
        adlift_repository: Any = None,
        intervention_workflow: Any = None,
        intervention_label_registry: Any = None,
    ) -> FastAPI:
        audit_log = audit_log or InMemoryAuditLog()
        job_queue = job_queue or InMemoryJobQueue()
        heatzone_store = heatzone_store or HeatZoneResultStore()
        api = FastAPI(title="ODay Plus API", version=API_VERSION)

        @api.middleware("http")
        async def attach_correlation_id(request: Request, call_next: Any) -> Response:
            context = CorrelationContext.from_header(request.headers.get(CORRELATION_ID_HEADER))
            request.state.correlation_id = context.correlation_id
            response = await call_next(request)
            response.headers[CORRELATION_ID_HEADER] = context.correlation_id
            return response

        @api.get("/healthz", tags=["system"])
        def healthz() -> dict[str, str]:
            return health_payload()

        @api.get("/health", tags=["platform"])
        def health(request: Request) -> dict[str, str]:
            return health_detail_payload(correlation_id=request.state.correlation_id)

        @api.get("/platform/health", tags=["platform"])
        def platform_health(request: Request) -> dict[str, str]:
            return health_detail_payload(correlation_id=request.state.correlation_id)

        @api.post("/jobs", status_code=status.HTTP_202_ACCEPTED, tags=["jobs"])
        def enqueue_job(
            body: JobCreatePayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            effective_idempotency_key = body.idempotency_key or idempotency_key
            job, created = job_queue.enqueue(
                JobRequest(
                    job_type=body.job_type,
                    payload=body.payload,
                    idempotency_key=effective_idempotency_key,
                ),
                correlation_id=request.state.correlation_id,
            )
            audit_event = audit_log.record(
                AuditEvent(
                    event_type="job.enqueue",
                    actor="system",
                    action="enqueue",
                    resource=f"job/{job.job_type}",
                    outcome="accepted" if created else "idempotent_replay",
                    correlation_id=request.state.correlation_id,
                    job_id=job.job_id,
                    metadata={"idempotency_key": effective_idempotency_key, "created": created},
                )
            )
            return {
                "job_id": job.job_id,
                "status": job.status.value,
                "correlation_id": job.correlation_id,
                "idempotency_key": job.idempotency_key,
                "job": job.to_dict(),
                "created": created,
                "audit_event_id": audit_event.event_id,
            }

        @api.get("/jobs/{job_id}", tags=["jobs"])
        def get_job(job_id: str) -> dict[str, Any]:
            job = job_queue.get(job_id)
            if job is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
            return job.to_dict()

        @api.get("/audit/events", tags=["audit"])
        def list_audit_events(correlation_id: str | None = None) -> dict[str, Any]:
            return {
                "events": [
                    event.to_dict()
                    for event in audit_log.list_events(correlation_id=correlation_id)
                ]
            }

        from apps.api.app.routes.adlift import create_adlift_router
        from apps.api.app.routes.forecastops import create_forecastops_router
        from apps.api.app.routes.interventions import create_interventions_router
        from apps.api.app.routes.listings import router as listings_router
        from apps.api.app.routes.sitescore import create_sitescore_router
        from modules.adlift.infrastructure import InMemoryAdLiftRepository
        from modules.forecastops.infrastructure import InMemoryForecastOpsRepository
        from modules.intervention.application.workflow import InterventionWorkflow
        from modules.intervention.infrastructure.repositories import InMemoryLabelRegistry
        from modules.sitescore.infrastructure.repositories import InMemorySiteScoreRepository
        from shared.workflow.sitescore import SiteScoreDecisionWorkflow

        forecast_repository = forecastops_repository or InMemoryForecastOpsRepository()
        site_repository = sitescore_repository or InMemorySiteScoreRepository()
        decision_workflow = sitescore_workflow or SiteScoreDecisionWorkflow(audit_log=audit_log)
        adlift_repo = adlift_repository or InMemoryAdLiftRepository()
        label_registry = intervention_label_registry or InMemoryLabelRegistry()
        interventions_workflow = intervention_workflow or InterventionWorkflow(
            audit_log=audit_log, label_hooks=[label_registry]
        )

        api.include_router(create_heatzone_router(store=heatzone_store, audit_log=audit_log))
        api.include_router(listings_router)
        api.include_router(
            create_forecastops_router(repository=forecast_repository, audit_log=audit_log)
        )
        api.include_router(
            create_sitescore_router(
                repository=site_repository,
                workflow=decision_workflow,
                audit_log=audit_log,
            )
        )
        api.include_router(create_adlift_router(repository=adlift_repo, audit_log=audit_log))
        api.include_router(
            create_interventions_router(
                workflow=interventions_workflow,
                label_registry=label_registry,
            )
        )

        api.state.audit_log = audit_log
        api.state.job_queue = job_queue
        api.state.heatzone_store = heatzone_store
        api.state.forecastops_repository = forecast_repository
        api.state.sitescore_repository = site_repository
        api.state.sitescore_workflow = decision_workflow
        api.state.adlift_repository = adlift_repo
        api.state.intervention_workflow = interventions_workflow
        api.state.intervention_label_registry = label_registry
        return api

    app = create_app()
