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
from shared.api.errors import install_error_handlers
from shared.api.versioning import install_deprecation_headers, mount_versioned
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
    from fastapi import APIRouter, FastAPI, Header, HTTPException, Request, Response, status
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
        store_ops_repository: Any = None,
        intervention_workflow: Any = None,
        intervention_repository: Any = None,
        intervention_label_registry: Any = None,
        persistence: Any = None,
        external_provider_validation: Any = None,
        external_ingestion_service: Any = None,
        telemetry: Any = None,
    ) -> FastAPI:
        # Defaults come from the persistence factory, which selects in-memory
        # (default) or durable SQLite storage from the environment
        # (ODP_PERSISTENCE / ODP_DB_PATH). Explicit arguments still win, so
        # tests can inject hand-built doubles. See ODP-PV-009.
        from shared.infrastructure.persistence import build_persistence
        from shared.observability import SpanKind, SpanStatus, Telemetry, TraceContext

        telemetry = telemetry or Telemetry("oday-api")
        provider_validation = external_provider_validation or validate_external_providers_or_raise()
        bundle = persistence or build_persistence()
        audit_log = audit_log or bundle.audit_log
        evidence_store = evidence_store or bundle.evidence_store
        job_queue = job_queue or bundle.job_queue
        heatzone_store = heatzone_store or bundle.heatzone_store

        from modules.external_data.application.ingestion_service import ExternalIngestionService

        ingestion_service = external_ingestion_service or ExternalIngestionService(
            store=bundle.ingestion_run_store,
            audit_log=audit_log,
        )
        api = FastAPI(title="ODay Plus API", version=API_VERSION)

        # Normalise every error leaving the app into the one envelope
        # (ODP-PGAP-API-001). Registered before the routers so the 118 legacy
        # `HTTPException(detail="...")` raises are covered without touching
        # their call sites.
        install_error_handlers(api)
        install_deprecation_headers(api)

        @api.middleware("http")
        async def attach_correlation_id(request: Request, call_next: Any) -> Response:
            context = CorrelationContext.from_header(request.headers.get(CORRELATION_ID_HEADER))
            request.state.correlation_id = context.correlation_id

            trace_ctx = TraceContext(
                correlation_id=context.correlation_id,
                actor_id="user",
                request_id=context.correlation_id,
            )

            with telemetry.operation(
                name=f"HTTP {request.method} {request.url.path}",
                kind=SpanKind.API,
                context=trace_ctx,
                resource="HTTP",
                action=request.method,
                latency_labels={"service": "oday-api", "route": request.url.path},
            ) as span:
                response = await call_next(request)
                if response.status_code >= 400:
                    span.status = SpanStatus.ERROR
                    span.error_code = f"HTTP_{response.status_code}"
                response.headers[CORRELATION_ID_HEADER] = context.correlation_id
                return response


        @api.get("/healthz", tags=["system"])
        def healthz() -> dict[str, str]:
            # Liveness: simply check that process is running
            return {"status": "ok", "service": "oday-api"}

        @api.get("/readiness", tags=["system"])
        def readiness(response: Response) -> dict[str, Any]:
            # Readiness: check database connectivity
            db_ok = True
            details = {}
            if bundle.is_durable:
                try:
                    bundle.engine.query("SELECT 1")
                    details["database"] = "healthy"
                except Exception as exc:
                    db_ok = False
                    details["database"] = f"unhealthy: {exc}"
            else:
                details["database"] = "healthy (in-memory)"

            if not db_ok:
                response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
                return {"status": "unhealthy", "service": "oday-api", "details": details}
            return {"status": "ok", "service": "oday-api", "details": details}

        @api.get("/health", tags=["platform"])
        @api.get("/platform/health", tags=["platform"])
        def health(request: Request, response: Response) -> dict[str, Any]:
            # Detailed health: check database, job queue, and external providers
            db_ok = True
            db_details = "healthy (in-memory)"
            if bundle.is_durable:
                try:
                    bundle.engine.query("SELECT 1")
                    db_details = "healthy"
                except Exception as exc:
                    db_ok = False
                    db_details = f"unhealthy: {exc}"

            if hasattr(provider_validation, "ok"):
                provider_ok = provider_validation.ok
                provider_errors = getattr(provider_validation, "errors", ())
            elif callable(provider_validation):
                try:
                    provider_validation()
                    provider_ok = True
                    provider_errors = ()
                except Exception as exc:
                    provider_ok = False
                    provider_errors = (str(exc),)
            else:
                provider_ok = True
                provider_errors = ()

            provider_details = "healthy" if provider_ok else f"unhealthy: {provider_errors}"

            queue_ok = True
            queue_details = "healthy"
            try:
                if bundle.is_durable:
                    bundle.engine.query("SELECT COUNT(*) FROM durable_jobs")
            except Exception as exc:
                queue_ok = False
                queue_details = f"unhealthy: {exc}"

            overall_ok = db_ok and provider_ok and queue_ok
            if not overall_ok:
                response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

            return {
                "status": "ok" if overall_ok else "unhealthy",
                "service": "oday-api",
                "version": API_VERSION,
                "time": datetime.now(UTC).isoformat(),
                "correlation_id": request.state.correlation_id,
                "dependencies": {
                    "database": db_details,
                    "job_queue": queue_details,
                    "external_providers": provider_details,
                }
            }


        @api.get("/platform/version", tags=["platform"])
        def platform_version(request: Request) -> dict[str, str]:
            return release_version_payload(correlation_id=request.state.correlation_id)

        # Jobs and audit-event reads are product operations, so they are
        # versioned like every domain router rather than declared inline on the
        # app (ODP-PGAP-API-001). The health/version probes above stay
        # unversioned on purpose: they are wired into deploy manifests and load
        # balancers that must not be asked to learn a version prefix.
        platform_router = APIRouter()

        @platform_router.post("/jobs", status_code=status.HTTP_202_ACCEPTED, tags=["jobs"])
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

        @platform_router.get("/jobs/{job_id}", tags=["jobs"])
        def get_job(job_id: str) -> dict[str, Any]:
            job = job_queue.get(job_id)
            if job is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
            return job.to_dict()

        @platform_router.get("/audit/events", tags=["audit"])
        def list_audit_events(
            correlation_id: str | None = None,
        ) -> dict[str, Any]:
            return {
                "events": [
                    event.to_dict()
                    for event in audit_log.list_events(correlation_id=correlation_id)
                ]
            }

        mount_versioned(api, platform_router)

        from apps.api.app.routes.adlift import create_adlift_router
        from apps.api.app.routes.audit import create_audit_router
        from apps.api.app.routes.avm import create_avm_router
        from apps.api.app.routes.external_data import create_external_data_router
        from apps.api.app.routes.forecastops import create_forecastops_router
        from apps.api.app.routes.interventions import create_interventions_router
        from apps.api.app.routes.learninghub import create_learninghub_router
        from apps.api.app.routes.listings import create_listings_router
        from apps.api.app.routes.netplan import create_netplan_router
        from apps.api.app.routes.operator import create_operator_router
        from apps.api.app.routes.operator_modules import create_operator_store_ops_router
        from apps.api.app.routes.priceops import create_priceops_router
        from apps.api.app.routes.sitescore import create_sitescore_router
        from modules.intervention.application.workflow import InterventionWorkflow
        from shared.infrastructure.persistence import SqliteDocumentStore
        from shared.workflow.sitescore import (
            CandidateSiteRealizationHook,
            SiteScoreDecisionWorkflow,
        )

        forecast_repository = forecastops_repository or bundle.forecastops_repository
        netplan_repo = netplan_repository or bundle.netplan_repository
        learning_repo = learninghub_repository or bundle.learninghub_repository
        model_artifacts = artifact_store or bundle.artifact_store
        price_repo = priceops_repository or bundle.priceops_repository
        avm_repo = avm_repository or bundle.avm_repository
        site_repository = sitescore_repository or bundle.sitescore_repository
        # ODP-FLOW-002: back the decision workflow and its realization hook with
        # the persistence bundle so decisions and realized sites survive restart.
        realization_hook = CandidateSiteRealizationHook(
            store=bundle.sitescore_realized_store
        )
        decision_workflow = sitescore_workflow or SiteScoreDecisionWorkflow(
            audit_log=audit_log,
            hooks=[realization_hook],
            store=bundle.sitescore_decision_store,
        )
        listing_repository = bundle.listing_repository
        adlift_repo = adlift_repository or bundle.adlift_repository
        store_ops_repo = store_ops_repository or bundle.store_ops_repository
        label_registry = intervention_label_registry or bundle.intervention_label_registry
        intervention_repo = intervention_repository or bundle.intervention_repository
        operator_document_store = SqliteDocumentStore(bundle.engine) if bundle.is_durable else None
        interventions_workflow = intervention_workflow or InterventionWorkflow(
            repository=intervention_repo,
            audit_log=audit_log,
            label_hooks=[label_registry],
        )

        # ODP-GAP-ML-002: register the baseline production model for each
        # scoring/forecast service in the durable registry (idempotent) and bind
        # each router to its resolved PRODUCTION ModelVersion so runs carry
        # auditable governance metadata and fail closed when the model or the
        # live inputs are absent.
        from models.shared_ml import seed_scoring_models

        release_sha = (
            os.environ.get("ODAY_RELEASE_SHA")
            or os.environ.get("GITHUB_SHA")
            or os.environ.get("COMMIT_SHA")
        )
        scoring_bindings = seed_scoring_models(learning_repo, git_sha=release_sha)

        # Every product router is mounted through mount_versioned: once under
        # /api/v1 (the contract the OpenAPI artifact and generated client
        # describe) and once on its legacy unversioned path as a deprecated
        # compatibility alias (ODP-PGAP-API-001). Before this, 12 of 14 routers
        # had no versioned path at all.
        mount_versioned(
            api,
            create_heatzone_router(
                store=heatzone_store,
                audit_log=audit_log,
                model_binding=scoring_bindings.get("heatzone"),
            ),
        )
        mount_versioned(
            api, create_audit_router(audit_log=audit_log, evidence_store=evidence_store)
        )
        mount_versioned(
            api,
            create_external_data_router(
                ingestion_service=ingestion_service, audit_log=audit_log
            ),
        )
        mount_versioned(
            api, create_listings_router(audit_log=audit_log, repository=listing_repository)
        )
        mount_versioned(api, create_avm_router(repository=avm_repo, audit_log=audit_log))
        mount_versioned(
            api,
            create_forecastops_router(
                repository=forecast_repository,
                audit_log=audit_log,
                model_binding=scoring_bindings.get("forecastops"),
            ),
        )
        mount_versioned(api, create_netplan_router(repository=netplan_repo, audit_log=audit_log))
        mount_versioned(
            api,
            create_learninghub_router(
                repository=learning_repo,
                artifact_store=model_artifacts,
                audit_log=audit_log,
            ),
        )
        mount_versioned(api, create_priceops_router(repository=price_repo, audit_log=audit_log))
        mount_versioned(
            api,
            create_sitescore_router(
                repository=site_repository,
                workflow=decision_workflow,
                realization_hook=realization_hook,
                audit_log=audit_log,
                model_binding=scoring_bindings.get("sitescore"),
            ),
        )
        mount_versioned(api, create_adlift_router(repository=adlift_repo, audit_log=audit_log))
        mount_versioned(
            api,
            create_operator_store_ops_router(
                repository=store_ops_repo,
                audit_log=audit_log,
            ),
        )
        mount_versioned(
            api,
            create_interventions_router(
                workflow=interventions_workflow,
                label_registry=label_registry,
            ),
        )
        from modules.opsboard.application.network_listings import InMemoryAssistedIntakeRepository
        from shared.infrastructure.persistence.operator_network_listings import (
            DurableAssistedIntakeRepository,
        )
        operator_intake_repository = (
            DurableAssistedIntakeRepository(operator_document_store)
            if operator_document_store is not None
            else InMemoryAssistedIntakeRepository()
        )

        mount_versioned(
            api,
            create_operator_router(
                audit_log=audit_log,
                document_store=operator_document_store,
                listing_repository=listing_repository,
                evidence_store=evidence_store,
                intake_repository=operator_intake_repository,
            ),
        )

        api.state.audit_log = audit_log
        api.state.evidence_store = evidence_store
        api.state.operator_intake_repository = operator_intake_repository
        api.state.job_queue = job_queue
        api.state.heatzone_store = heatzone_store
        api.state.avm_repository = avm_repo
        api.state.forecastops_repository = forecast_repository
        api.state.netplan_repository = netplan_repo
        api.state.learninghub_repository = learning_repo
        api.state.scoring_bindings = scoring_bindings
        api.state.artifact_store = model_artifacts
        api.state.priceops_repository = price_repo
        api.state.sitescore_repository = site_repository
        api.state.sitescore_workflow = decision_workflow
        api.state.sitescore_realization_hook = realization_hook
        api.state.listing_repository = listing_repository
        api.state.adlift_repository = adlift_repo
        api.state.store_ops_repository = store_ops_repo
        api.state.intervention_workflow = interventions_workflow
        api.state.intervention_repository = intervention_repo
        api.state.intervention_label_registry = label_registry
        api.state.operator_document_store = operator_document_store
        api.state.persistence = bundle
        api.state.external_provider_validation = provider_validation
        return api

    app = create_app()
