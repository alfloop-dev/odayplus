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
from apps.api.oday_api.runtime_mode import deployment_mode, live_data_required
from modules.external_data.connectors import validate_external_providers_or_raise
from shared.api.errors import ApiError, error_response_body, install_error_handlers
from shared.api.versioning import install_deprecation_headers, mount_versioned
from shared.audit import AuditEvent, InMemoryAuditLog
from shared.jobs import InMemoryJobQueue, JobRequest
from shared.observability import CORRELATION_ID_HEADER, CorrelationContext

API_VERSION = "0.1.0"


def _provider_mode_label(provider_validation: Any) -> str:
    mode = getattr(provider_validation, "mode", None)
    value = getattr(mode, "value", mode)
    return str(value).strip().lower() if value is not None else "unknown"


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
    from fastapi.responses import JSONResponse
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
        # Defaults come from the persistence factory, including the production
        # PostgreSQL runtime. Explicit arguments still win so tests can inject
        # hand-built doubles. See ODP-PV-009.
        from shared.infrastructure.persistence import build_persistence
        from shared.observability import SpanKind, SpanStatus, Telemetry, TraceContext

        telemetry = telemetry or Telemetry("oday-api")
        provider_validation = external_provider_validation or validate_external_providers_or_raise()
        bundle = persistence or build_persistence()
        active_deployment_mode = deployment_mode()
        require_live_data = live_data_required()
        domain_runtime_mode = "production" if require_live_data else "local"
        persistence_mode = str(getattr(bundle, "mode", "unknown")).strip().lower()
        configured_persistence_mode = os.environ.get(
            "ODP_PERSISTENCE", persistence_mode
        ).strip().lower()
        provider_mode = _provider_mode_label(provider_validation)
        production_persistence_supported = (
            persistence_mode in {"postgres", "postgresql"}
            and bool(bundle.is_production)
        )
        operator_live_repository: Any | None = None
        if require_live_data and production_persistence_supported:
            from modules.opsboard.application.operator_live_repository import (
                OperatorLiveRepository,
            )

            operator_live_repository = OperatorLiveRepository(bundle)
        production_model_bindings_ready = False
        production_model_error: str | None = None
        model_runtime: Any | None = None
        model_binding_mode = (
            "mlflow-production-unverified"
            if require_live_data
            else "local-baseline-seed"
        )
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

        def database_health() -> tuple[bool, str]:
            if not bundle.is_durable:
                return True, "healthy (in-memory)"
            try:
                bundle.engine.query("SELECT 1")
            except Exception as exc:
                return False, f"unhealthy: {exc}"
            return True, "healthy"

        def provider_health() -> tuple[bool, tuple[Any, ...]]:
            if hasattr(provider_validation, "ok"):
                return bool(provider_validation.ok), tuple(
                    getattr(provider_validation, "errors", ())
                )
            if callable(provider_validation):
                try:
                    provider_validation()
                except Exception as exc:
                    return False, (str(exc),)
            return True, ()

        def require_live_external_provider() -> None:
            if not require_live_data:
                return
            provider_ok, provider_errors = provider_health()
            if provider_ok and provider_mode == "live":
                return
            raise ApiError(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "The required production external provider is unavailable; "
                "fixture providers are disabled.",
                code="external_provider_unavailable",
                next_action=(
                    "Restore an approved live provider configuration, then retry."
                ),
                details=[
                    {
                        "dependency": "external_provider",
                        "provider_mode": provider_mode,
                        "configuration_valid": provider_ok,
                        "errors": [str(error) for error in provider_errors],
                    }
                ],
            )

        def production_persistence_blocking_reasons(
            *, persistence_reachable: bool
        ) -> list[str]:
            reasons: list[str] = []
            if not bundle.is_durable:
                reasons.append("MEMORY_PERSISTENCE")
            elif not production_persistence_supported:
                reasons.append("SQLITE_NOT_PRODUCTION_PERSISTENCE")
            if not persistence_reachable:
                reasons.append("PERSISTENCE_UNREACHABLE")
            if configured_persistence_mode not in {
                "memory",
                "durable",
                "sqlite",
                "postgres",
                "postgresql",
            }:
                reasons.append("UNSUPPORTED_PERSISTENCE_MODE")
            return reasons

        def runtime_modes(
            *,
            provider_ok: bool,
            persistence_reachable: bool,
        ) -> dict[str, Any]:
            provider_live_ready = provider_ok and provider_mode == "live"
            operator_probe = (
                operator_live_repository.probe()
                if operator_live_repository is not None
                else None
            )
            operator_repository_ready = bool(
                operator_probe is not None and operator_probe.ready
            )
            live_ready = (
                production_persistence_supported
                and persistence_reachable
                and provider_live_ready
                and operator_repository_ready
                and production_model_bindings_ready
            )
            blocking_reasons: list[str] = []
            if require_live_data:
                blocking_reasons.extend(
                    production_persistence_blocking_reasons(
                        persistence_reachable=persistence_reachable
                    )
                )
                if not provider_live_ready:
                    blocking_reasons.append("PROVIDER_NOT_LIVE")
                if not operator_repository_ready:
                    blocking_reasons.append("OPERATOR_LIVE_REPOSITORY_UNAVAILABLE")
                if not production_model_bindings_ready:
                    blocking_reasons.append(
                        "PRODUCTION_MODEL_BINDINGS_UNVERIFIED"
                    )
            return {
                "requireLiveData": require_live_data,
                "deploymentMode": active_deployment_mode,
                "persistence": {
                    "configuredMode": configured_persistence_mode,
                    "runtimeMode": persistence_mode,
                    "durable": bool(bundle.is_durable),
                    "reachable": persistence_reachable,
                    "production_persistence_supported": (
                        production_persistence_supported
                    ),
                },
                "provider": {
                    "mode": provider_mode,
                    "configurationValid": provider_ok,
                    "healthy": (
                        provider_ok
                        if not require_live_data
                        else provider_live_ready
                    ),
                    "live": provider_live_ready,
                },
                "models": {
                    "mode": model_binding_mode,
                    "productionBindingsReady": production_model_bindings_ready,
                    "error": production_model_error,
                    "autoSeeded": (
                        not require_live_data and production_model_bindings_ready
                    ),
                },
                "data": {
                    "mode": (
                        "live"
                        if require_live_data and live_ready
                        else "unavailable"
                        if require_live_data
                        else "fixture"
                    ),
                    "origin": (
                        operator_live_repository.data_origin
                        if operator_live_repository is not None
                        else None
                        if require_live_data
                        else "r4-seed"
                    ),
                    "operatorRepositoryReady": operator_repository_ready,
                    "operatorRepositoryProbe": (
                        operator_probe.to_dict()
                        if operator_probe is not None
                        else None
                    ),
                    "liveReady": live_ready,
                    "blockingReasons": blocking_reasons,
                },
            }

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
                if require_live_data and request.url.path not in {
                    "/health",
                    "/healthz",
                    "/openapi.json",
                    "/platform/health",
                    "/platform/version",
                    "/readiness",
                    "/docs",
                    "/docs/oauth2-redirect",
                    "/redoc",
                }:
                    db_ok, _ = database_health()
                    blocking_reasons = production_persistence_blocking_reasons(
                        persistence_reachable=db_ok
                    )
                    if blocking_reasons:
                        message = (
                            "Production persistence is unavailable; "
                            "fixture, seed, and in-memory fallback are disabled."
                        )
                        response = JSONResponse(
                            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            content=error_response_body(
                                code="production_runtime_unavailable",
                                message=message,
                                next_action=(
                                    "Restore the required production PostgreSQL "
                                    "persistence, then retry."
                                ),
                                correlation_id=context.correlation_id,
                                details=[
                                    {
                                        "dependency": "persistence",
                                        "blocking_reasons": blocking_reasons,
                                        "deployment_mode": active_deployment_mode,
                                    }
                                ],
                            ),
                        )
                        response.headers[CORRELATION_ID_HEADER] = (
                            context.correlation_id
                        )
                        span.status = SpanStatus.ERROR
                        span.error_code = "HTTP_503"
                        return response
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
            db_ok, db_details = database_health()
            provider_ok, provider_errors = provider_health()
            modes = runtime_modes(
                provider_ok=provider_ok,
                persistence_reachable=db_ok,
            )
            persistence_ok = db_ok and (
                not require_live_data
                or bool(
                    modes["persistence"][
                        "production_persistence_supported"
                    ]
                )
            )
            provider_ready = provider_ok and (
                not require_live_data or bool(modes["provider"]["live"])
            )
            live_gate_ok = (
                not require_live_data or bool(modes["data"]["liveReady"])
            )
            overall_ok = persistence_ok and provider_ready and live_gate_ok
            if require_live_data and not modes["persistence"][
                "production_persistence_supported"
            ]:
                db_details = (
                    "unsupported for production live data: "
                    f"runtime mode {persistence_mode}"
                )
            provider_details = (
                "healthy"
                if provider_ready
                else (
                    "unsupported for production live data: "
                    f"mode {provider_mode}"
                    if require_live_data and provider_ok
                    else f"unhealthy: {provider_errors}"
                )
            )
            details = {
                "database": db_details,
                "external_providers": provider_details,
                **modes,
            }

            if not overall_ok:
                response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
                return {"status": "unhealthy", "service": "oday-api", "details": details}
            return {"status": "ok", "service": "oday-api", "details": details}

        @api.get("/health", tags=["platform"])
        @api.get("/platform/health", tags=["platform"])
        def health(request: Request, response: Response) -> dict[str, Any]:
            # Detailed health: check database, job queue, and external providers
            db_ok, db_details = database_health()
            provider_ok, provider_errors = provider_health()
            provider_details = (
                "healthy"
                if provider_ok and (
                    not require_live_data or provider_mode == "live"
                )
                else (
                    "unsupported for production live data: "
                    f"mode {provider_mode}"
                    if require_live_data and provider_ok
                    else f"unhealthy: {provider_errors}"
                )
            )

            queue_ok = True
            queue_details = "healthy"
            try:
                if bundle.is_durable:
                    bundle.engine.query("SELECT COUNT(*) FROM durable_jobs")
            except Exception as exc:
                queue_ok = False
                queue_details = f"unhealthy: {exc}"

            modes = runtime_modes(
                provider_ok=provider_ok,
                persistence_reachable=db_ok,
            )
            persistence_ok = db_ok and (
                not require_live_data
                or bool(
                    modes["persistence"][
                        "production_persistence_supported"
                    ]
                )
            )
            provider_ready = provider_ok and (
                not require_live_data or bool(modes["provider"]["live"])
            )
            live_gate_ok = (
                not require_live_data or bool(modes["data"]["liveReady"])
            )
            if require_live_data and not modes["persistence"][
                "production_persistence_supported"
            ]:
                db_details = (
                    "unsupported for production live data: "
                    f"runtime mode {persistence_mode}"
                )
            overall_ok = (
                persistence_ok
                and provider_ready
                and queue_ok
                and live_gate_ok
            )
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
                },
                "modes": modes,
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
        from apps.api.app.routes.listings import (
            create_assisted_intake_router,
            create_listings_router,
        )
        from apps.api.app.routes.netplan import create_netplan_router
        from apps.api.app.routes.operator import create_operator_router
        from apps.api.app.routes.operator_modules import create_operator_store_ops_router
        from apps.api.app.routes.priceops import create_priceops_router
        from apps.api.app.routes.sitescore import create_sitescore_router
        from modules.intervention.application.workflow import InterventionWorkflow
        from shared.infrastructure.persistence import (
            DurableAVMRepository,
            DurableListingRepository,
            DurableNetPlanRepository,
            DurablePriceOpsRepository,
            DurableSiteScoreRepository,
            PostgresDocumentStore,
            SqliteDocumentStore,
        )
        from shared.infrastructure.persistence.operator_domains import (
            TenantScopedDocumentStore,
        )
        from shared.infrastructure.persistence.repositories import (
            DurableDecisionStore,
        )
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
        if bundle.is_production:
            operator_document_store = PostgresDocumentStore(bundle.engine)
        elif bundle.is_durable:
            operator_document_store = SqliteDocumentStore(bundle.engine)
        else:
            operator_document_store = None
        interventions_workflow = intervention_workflow or InterventionWorkflow(
            repository=intervention_repo,
            audit_log=audit_log,
            label_hooks=[label_registry],
        )

        from models.shared_ml import (
            MlflowProductionModelRuntime,
            ProductionModelRuntimeError,
            seed_scoring_models,
        )
        from modules.avm.application import (
            AVMProductionExecutionError,
            AVMProductionExecutor,
        )
        from modules.learninghub.infrastructure import MlflowRegistryAdapter
        from modules.netplan.application import NetPlanProductionExecutor
        from modules.priceops.infrastructure.oss_optimizer import (
            PriceOpsProductionOptimizer,
        )

        release_sha = (
            os.environ.get("ODAY_RELEASE_SHA")
            or os.environ.get("GITHUB_SHA")
            or os.environ.get("COMMIT_SHA")
        )
        learninghub_registry: Any | None = None
        avm_production_executor: Any | None = None
        netplan_production_executor: Any | None = None
        priceops_production_optimizer: Any | None = None
        if require_live_data:
            scoring_bindings: dict[str, Any] = {}
            production_composition_errors: list[str] = []
            try:
                from modules.avm.domain import AVM_FEATURE_VERSION
                from modules.forecastops.domain import FORECASTOPS_FEATURE_VERSION
                from modules.heatzone.domain import HEATZONE_FEATURE_VERSION
                from modules.sitescore.domain import SITESCORE_FEATURE_VERSION

                model_runtime = MlflowProductionModelRuntime.from_environment(
                    model_names={
                        "avm": os.environ.get("ODP_AVM_MODEL_NAME", "avm"),
                        "forecastops": os.environ.get(
                            "ODP_FORECASTOPS_MODEL_NAME",
                            "forecastops",
                        ),
                        "heatzone": os.environ.get(
                            "ODP_HEATZONE_MODEL_NAME",
                            "heatzone",
                        ),
                        "sitescore": os.environ.get(
                            "ODP_SITESCORE_MODEL_NAME",
                            "sitescore",
                        ),
                    }
                )
                for service, feature_schema_version in {
                    "avm": AVM_FEATURE_VERSION,
                    "forecastops": FORECASTOPS_FEATURE_VERSION,
                    "heatzone": HEATZONE_FEATURE_VERSION,
                    "sitescore": SITESCORE_FEATURE_VERSION,
                }.items():
                    executable = model_runtime.resolve(
                        service=service,
                        expected_feature_schema_version=feature_schema_version,
                    )
                    scoring_bindings[service] = executable.binding
            except ProductionModelRuntimeError as exc:
                production_composition_errors.append(f"{exc.code}: {exc}")
                model_runtime = None
                scoring_bindings = {}
            if model_runtime is not None:
                try:
                    learninghub_registry = MlflowRegistryAdapter(
                        learning_repo,
                        tracking_uri=model_runtime.tracking_uri,
                        client=model_runtime.client,
                        runtime_mode=domain_runtime_mode,
                    )
                    learninghub_registry.require_production_binding()
                except Exception as exc:
                    production_composition_errors.append(
                        f"LEARNINGHUB_PRODUCTION_BINDING_REQUIRED: {exc}"
                    )
                    learninghub_registry = None
                try:
                    avm_production_executor = AVMProductionExecutor.from_environment(
                        model_runtime=model_runtime
                    )
                except AVMProductionExecutionError as exc:
                    production_composition_errors.append(
                        f"AVM_PRODUCTION_EXECUTION_UNAVAILABLE: {exc}"
                    )
                    avm_production_executor = None
            netplan_production_executor = NetPlanProductionExecutor()
            priceops_production_optimizer = PriceOpsProductionOptimizer()
            production_model_error = (
                "; ".join(production_composition_errors)
                if production_composition_errors
                else None
            )
            production_model_bindings_ready = (
                len(scoring_bindings) == 4
                and model_runtime is not None
                and learninghub_registry is not None
                and avm_production_executor is not None
            )
            if production_model_bindings_ready:
                model_binding_mode = "mlflow-production"
        else:
            scoring_bindings = seed_scoring_models(
                learning_repo,
                git_sha=release_sha,
            )
            production_model_bindings_ready = bool(scoring_bindings)

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
                model_runtime=model_runtime,
                require_production_model=require_live_data,
            ),
        )
        mount_versioned(
            api, create_audit_router(audit_log=audit_log, evidence_store=evidence_store)
        )
        mount_versioned(
            api,
            create_external_data_router(
                ingestion_service=ingestion_service,
                audit_log=audit_log,
                require_provider=require_live_external_provider,
            ),
        )
        mount_versioned(
            api, create_listings_router(audit_log=audit_log, repository=listing_repository)
        )
        # This router is generated from a separately approved OpenAPI bundle;
        # preserve its per-operation response set instead of adding the generic
        # platform responses to every operation.
        assisted_intake_store = getattr(bundle, "assisted_intake_store", None)
        if (
            production_persistence_supported
            and assisted_intake_store is None
        ):
            raise RuntimeError(
                "Production PostgreSQL requires a durable Assisted Intake store"
            )
        mount_versioned(
            api,
            create_assisted_intake_router(
                store=assisted_intake_store,
                audit_log=audit_log,
            ),
            exact_responses=True,
        )
        mount_versioned(
            api,
            create_avm_router(
                repository=avm_repo,
                audit_log=audit_log,
                production_executor=avm_production_executor,
                runtime_mode=domain_runtime_mode,
            ),
        )
        mount_versioned(
            api,
            create_forecastops_router(
                repository=forecast_repository,
                audit_log=audit_log,
                model_binding=scoring_bindings.get("forecastops"),
                model_runtime=model_runtime,
                require_production_model=require_live_data,
                require_durable_jobs=require_live_data,
                job_queue=job_queue,
                runtime_mode=domain_runtime_mode,
            ),
        )
        mount_versioned(
            api,
            create_netplan_router(
                repository=netplan_repo,
                audit_log=audit_log,
                production_executor=netplan_production_executor,
                runtime_mode=domain_runtime_mode,
            ),
        )
        mount_versioned(
            api,
            create_learninghub_router(
                repository=learning_repo,
                artifact_store=model_artifacts,
                audit_log=audit_log,
                registry=learninghub_registry,
                runtime_mode=domain_runtime_mode,
            ),
        )
        mount_versioned(
            api,
            create_priceops_router(
                repository=price_repo,
                audit_log=audit_log,
                production_optimizer=priceops_production_optimizer,
                runtime_mode=domain_runtime_mode,
            ),
        )
        mount_versioned(
            api,
            create_sitescore_router(
                repository=site_repository,
                workflow=decision_workflow,
                realization_hook=realization_hook,
                audit_log=audit_log,
                model_binding=scoring_bindings.get("sitescore"),
                model_runtime=model_runtime,
                require_production_model=require_live_data,
                require_durable_jobs=require_live_data,
                job_queue=job_queue,
                runtime_mode=domain_runtime_mode,
            ),
        )
        mount_versioned(
            api,
            create_adlift_router(
                repository=adlift_repo,
                audit_log=audit_log,
                runtime_mode=domain_runtime_mode,
            ),
        )
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
            getattr(bundle, "operator_intake_repository", None)
            or (
                DurableAssistedIntakeRepository(operator_document_store)
                if operator_document_store is not None
                else InMemoryAssistedIntakeRepository()
            )
        )
        if operator_document_store is not None:
            def tenant_document_store(tenant_id: str) -> TenantScopedDocumentStore:
                return TenantScopedDocumentStore(operator_document_store, tenant_id)

            def listing_repository_for_tenant(
                tenant_id: str,
            ) -> DurableListingRepository:
                return DurableListingRepository(tenant_document_store(tenant_id))

            def sitescore_repository_for_tenant(
                tenant_id: str,
            ) -> DurableSiteScoreRepository:
                return DurableSiteScoreRepository(tenant_document_store(tenant_id))

            def sitescore_decision_repository_for_tenant(
                tenant_id: str,
            ) -> DurableDecisionStore:
                return DurableDecisionStore(tenant_document_store(tenant_id))

            def avm_repository_for_tenant(
                tenant_id: str,
            ) -> DurableAVMRepository:
                return DurableAVMRepository(tenant_document_store(tenant_id))

            def netplan_repository_for_tenant(
                tenant_id: str,
            ) -> DurableNetPlanRepository:
                return DurableNetPlanRepository(
                    tenant_document_store(tenant_id)
                )

            def priceops_repository_for_tenant(
                tenant_id: str,
            ) -> DurablePriceOpsRepository:
                return DurablePriceOpsRepository(tenant_document_store(tenant_id))
        else:
            listing_repository_for_tenant = None
            sitescore_repository_for_tenant = None
            sitescore_decision_repository_for_tenant = None
            avm_repository_for_tenant = None
            netplan_repository_for_tenant = None
            priceops_repository_for_tenant = None

        mount_versioned(
            api,
            create_operator_router(
                audit_log=audit_log,
                document_store=operator_document_store,
                listing_repository=listing_repository,
                listing_repository_for_tenant=listing_repository_for_tenant,
                sitescore_repository_for_tenant=sitescore_repository_for_tenant,
                sitescore_decision_repository_for_tenant=(
                    sitescore_decision_repository_for_tenant
                ),
                avm_repository_for_tenant=avm_repository_for_tenant,
                netplan_repository_for_tenant=netplan_repository_for_tenant,
                priceops_repository_for_tenant=priceops_repository_for_tenant,
                model_runtime=model_runtime,
                avm_production_executor=avm_production_executor,
                netplan_production_executor=netplan_production_executor,
                evidence_store=evidence_store,
                intake_repository=operator_intake_repository,
                live_repository=operator_live_repository,
                require_live_data=require_live_data,
                persistence_mode=persistence_mode,
                provider_mode=provider_mode,
            ),
        )

        api.state.audit_log = audit_log
        api.state.evidence_store = evidence_store
        api.state.operator_intake_repository = operator_intake_repository
        api.state.assisted_intake_store = assisted_intake_store
        api.state.persistence_bundle = bundle
        api.state.job_queue = job_queue
        api.state.heatzone_store = heatzone_store
        api.state.avm_repository = avm_repo
        api.state.forecastops_repository = forecast_repository
        api.state.netplan_repository = netplan_repo
        api.state.learninghub_repository = learning_repo
        api.state.scoring_bindings = scoring_bindings
        api.state.model_runtime = model_runtime
        api.state.learninghub_registry = learninghub_registry
        api.state.avm_production_executor = avm_production_executor
        api.state.netplan_production_executor = netplan_production_executor
        api.state.priceops_production_optimizer = priceops_production_optimizer
        api.state.domain_runtime_mode = domain_runtime_mode
        api.state.production_model_error = production_model_error
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
        api.state.operator_live_repository = operator_live_repository
        api.state.persistence = bundle
        api.state.external_provider_validation = provider_validation
        api.state.require_live_data = require_live_data
        api.state.persistence_mode = persistence_mode
        api.state.provider_mode = provider_mode
        return api

    app = create_app()
