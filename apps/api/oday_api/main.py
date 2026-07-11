"""ODay Plus Domain API Service (FastAPI).

Exposes integration, opsboard, data, and ML domain endpoints wired to durable
repositories, mapping components, and the artifact store. Also sets up the
correlation ID tracking middleware, job queues, and the audit log.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from apps.api.oday_api.routes.heatzone import HeatZoneResultStore, create_heatzone_router
from modules.external_data.connectors import validate_external_providers_or_raise
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


def release_version_payload(*, correlation_id: str) -> dict[str, str]:
    release_sha = (
        os.environ.get("ODAY_RELEASE_SHA")
        or os.environ.get("GITHUB_SHA")
        or os.environ.get("COMMIT_SHA")
        or "local"
    )
    return {
        **health_payload(),
        "api_version": API_VERSION,
        "release_sha": release_sha,
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
        evidence_store: Any = None,
        job_queue: InMemoryJobQueue | None = None,
        heatzone_store: HeatZoneResultStore | None = None,
        avm_repository: Any = None,
        forecastops_repository: Any = None,
        netplan_repository: Any = None,
        learninghub_repository: Any = None,
        artifact_store: Any = None,
        priceops_repository: Any = None,
        sitescore_repository: Any = None,
        sitescore_workflow: Any = None,
        adlift_repository: Any = None,
        intervention_workflow: Any = None,
        intervention_repository: Any = None,
        intervention_label_registry: Any = None,
        persistence: Any = None,
        external_provider_validation: Any = None,
    ) -> FastAPI:
        # Defaults come from the persistence factory, which selects in-memory
        # (default) or durable SQLite storage from the environment
        # (ODP_PERSISTENCE / ODP_DB_PATH). Explicit arguments still win, so
        # tests can inject hand-built doubles. See ODP-PV-009.
        from shared.infrastructure.persistence import build_persistence

        provider_validation = external_provider_validation or validate_external_providers_or_raise()
        bundle = persistence or build_persistence()
        audit_log = audit_log or bundle.audit_log
        evidence_store = evidence_store or bundle.evidence_store
        job_queue = job_queue or bundle.job_queue
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

        @api.get("/platform/version", tags=["platform"])
        def platform_version(request: Request) -> dict[str, str]:
            return release_version_payload(correlation_id=request.state.correlation_id)

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
        from apps.api.app.routes.audit import create_audit_router
        from apps.api.app.routes.avm import create_avm_router
        from apps.api.app.routes.external_data import create_external_data_router
        from apps.api.app.routes.forecastops import create_forecastops_router
        from apps.api.app.routes.interventions import create_interventions_router
        from apps.api.app.routes.learninghub import create_learninghub_router
        from apps.api.app.routes.listings import create_listings_router
        from apps.api.app.routes.netplan import create_netplan_router
        from apps.api.app.routes.priceops import create_priceops_router
        from apps.api.app.routes.sitescore import create_sitescore_router
        from apps.api.app.routes.operator import create_operator_router
        from modules.intervention.application.workflow import InterventionWorkflow
        from shared.workflow.sitescore import SiteScoreDecisionWorkflow

        forecast_repository = forecastops_repository or bundle.forecastops_repository
        netplan_repo = netplan_repository or bundle.netplan_repository
        learning_repo = learninghub_repository or bundle.learninghub_repository
        model_artifacts = artifact_store or bundle.artifact_store
        price_repo = priceops_repository or bundle.priceops_repository
        avm_repo = avm_repository or bundle.avm_repository
        site_repository = sitescore_repository or bundle.sitescore_repository
        decision_workflow = sitescore_workflow or SiteScoreDecisionWorkflow(audit_log=audit_log)
        adlift_repo = adlift_repository or bundle.adlift_repository
        label_registry = intervention_label_registry or bundle.intervention_label_registry
        intervention_repo = intervention_repository or bundle.intervention_repository
        interventions_workflow = intervention_workflow or InterventionWorkflow(
            repository=intervention_repo,
            audit_log=audit_log,
            label_hooks=[label_registry],
        )

        api.include_router(create_heatzone_router(store=heatzone_store, audit_log=audit_log))
        api.include_router(
            create_audit_router(audit_log=audit_log, evidence_store=evidence_store)
        )
        api.include_router(create_external_data_router(audit_log=audit_log))
        api.include_router(create_listings_router(audit_log=audit_log))
        api.include_router(create_avm_router(repository=avm_repo, audit_log=audit_log))
        api.include_router(
            create_forecastops_router(repository=forecast_repository, audit_log=audit_log)
        )
        api.include_router(create_netplan_router(repository=netplan_repo, audit_log=audit_log))
        api.include_router(
            create_learninghub_router(
                repository=learning_repo,
                artifact_store=model_artifacts,
                audit_log=audit_log,
            )
        )
        api.include_router(create_priceops_router(repository=price_repo, audit_log=audit_log))
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
        api.include_router(create_operator_router(audit_log=audit_log), prefix="/api/v1")

        api.state.audit_log = audit_log
        api.state.evidence_store = evidence_store
        api.state.job_queue = job_queue
        api.state.heatzone_store = heatzone_store
        api.state.avm_repository = avm_repo
        api.state.forecastops_repository = forecast_repository
        api.state.netplan_repository = netplan_repo
        api.state.learninghub_repository = learning_repo
        api.state.artifact_store = model_artifacts
        api.state.priceops_repository = price_repo
        api.state.sitescore_repository = site_repository
        api.state.sitescore_workflow = decision_workflow
        api.state.adlift_repository = adlift_repo
        api.state.intervention_workflow = interventions_workflow
        api.state.intervention_repository = intervention_repo
        api.state.intervention_label_registry = label_registry
        api.state.persistence = bundle
        api.state.external_provider_validation = provider_validation
        return api

    app = create_app()
