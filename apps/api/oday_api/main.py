"""ODay Plus Domain API Service (FastAPI).

Exposes integration, opsboard, data, and ML domain endpoints wired to durable
repositories, mapping components, and the artifact store. Also sets up the
correlation ID tracking middleware, job queues, and the audit log.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime
from typing import Any

from apps.api.oday_api.routes.heatzone import HeatZoneResultStore, create_heatzone_router
from apps.api.oday_api.runtime_mode import deployment_mode, live_data_required
from models.shared_ml.production_contracts import (
    PRODUCTION_MODEL_CONTRACTS,
    governed_disabled_services,
    production_model_names,
    required_production_model_services,
)
from modules.external_data.connectors import (
    probe_external_provider_connectivity,
    validate_external_providers_or_raise,
)
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


def _redacted_provider_error(error: Any) -> dict[str, str]:
    return {
        "provider_id": str(getattr(error, "provider_id", "provider_registry")),
        "code": str(getattr(error, "code", "configuration_invalid")),
        "env_var": str(getattr(error, "env_var", "")),
    }


def health_payload() -> dict[str, str]:
    return {"status": "ok", "service": "oday-api"}


def health_detail_payload(*, correlation_id: str) -> dict[str, str]:
    return {
        **health_payload(),
        "version": API_VERSION,
        "time": datetime.now(UTC).isoformat(),
        "correlation_id": correlation_id,
    }


def release_sha_from_environment() -> str:
    """Resolve the exact runtime release identity.

    ``ODAY_RELEASE_SHA`` remains the deployment contract. The training stack
    and existing Cloud Run revision use ``ODP_RELEASE_COMMIT_SHA``, so runtime
    probes accept it as the first compatibility fallback.
    """
    from shared.runtime_config import get_release_identity

    return get_release_identity("local")



def release_version_payload(*, correlation_id: str) -> dict[str, str]:
    return {
        **health_payload(),
        "api_version": API_VERSION,
        "release_sha": release_sha_from_environment(),
        "time": datetime.now(UTC).isoformat(),
        "correlation_id": correlation_id,
    }


def production_feature_schema_versions() -> dict[str, str]:
    """Return the canonical runtime schema expected by each production model."""

    from modules.avm.domain import AVM_FEATURE_VERSION
    from modules.forecastops.model_contract import FORECASTOPS_FEATURE_SCHEMA_ID
    from modules.heatzone.domain import HEATZONE_FEATURE_VERSION
    from modules.sitescore.domain import SITESCORE_FEATURE_VERSION

    return {
        "avm": AVM_FEATURE_VERSION,
        "forecastops": FORECASTOPS_FEATURE_SCHEMA_ID,
        "heatzone": HEATZONE_FEATURE_VERSION,
        "sitescore": SITESCORE_FEATURE_VERSION,
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
        netplan_approval_verifier: Any = None,
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
        operator_live_repository: Any = None,
        persistence: Any = None,
        external_provider_validation: Any = None,
        external_provider_connectivity_probe: Any = None,
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
        configured_persistence_mode = (
            os.environ.get("ODP_PERSISTENCE", persistence_mode).strip().lower()
        )
        provider_mode = _provider_mode_label(provider_validation)
        production_persistence_supported = persistence_mode in {"postgres", "postgresql"} and bool(
            bundle.is_production
        )
        if operator_live_repository is None and require_live_data and production_persistence_supported:
            from modules.opsboard.application.operator_live_repository import (
                OperatorLiveRepository,
            )

            operator_live_repository = OperatorLiveRepository(bundle)
        production_model_bindings_ready = False
        production_model_error: str | None = None
        model_runtime: Any | None = None
        required_model_services = required_production_model_services()
        _governed_disabled = governed_disabled_services()
        production_model_capabilities: dict[str, dict[str, Any]] = {
            service: {
                "service": service,
                "modelName": contract.model_name,
                "trainingSpecKey": contract.training_spec_key,
                "trainable": contract.trainable,
                "requiredForPlatformReadiness": (contract.required_for_platform_readiness),
                "outcomeContractRequired": contract.outcome_contract_required,
                # Governed-disabled capabilities are available=False but expose
                # full evidence so the runtime can report productionBindingsReady=True
                # without fabricating an alias or pretending the service works.
                "available": False,
                "reasonCode": (
                    contract.unavailable_reason
                    if contract.is_governed_disabled or not contract.trainable
                    else "PRODUCTION_BINDING_NOT_RESOLVED"
                ),
                "governedDisabled": contract.is_governed_disabled,
                "governedDisabledEvidence": (
                    contract.governed_disabled_binding.to_audit_dict()
                    if contract.governed_disabled_binding is not None
                    else None
                ),
                "error": None,
            }
            for service, contract in PRODUCTION_MODEL_CONTRACTS.items()
        }
        model_binding_mode = (
            "mlflow-production-unverified" if require_live_data else "local-baseline-seed"
        )
        audit_log = audit_log or bundle.audit_log
        evidence_store = evidence_store or bundle.evidence_store
        job_queue = job_queue or bundle.job_queue
        heatzone_store = heatzone_store or bundle.heatzone_store

        from modules.external_data.application.ingestion_service import ExternalIngestionService

        heatzone_store_for_tenant = (
            bundle.heatzone_store_for_tenant if bundle.is_durable else None
        )
        ingestion_run_store_for_tenant = (
            bundle.ingestion_run_store_for_tenant if bundle.is_durable else None
        )
        sitescore_decision_store_for_tenant = (
            bundle.sitescore_decision_store_for_tenant if bundle.is_durable else None
        )
        ingestion_service = external_ingestion_service or ExternalIngestionService(
            store=bundle.ingestion_run_store,
            ingestion_run_store_for_tenant=ingestion_run_store_for_tenant,
            state_store=bundle.external_fetch_state_store,
            audit_log=audit_log,
        )
        api = FastAPI(title="ODay Plus API", version=API_VERSION)
        provider_probe_lock = threading.Lock()
        provider_probe_cache: tuple[float, Any] | None = None

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

        def provider_configuration_health() -> tuple[bool, tuple[Any, ...]]:
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

        def provider_health(
            *, correlation_id: str | None = None
        ) -> tuple[bool, dict[str, Any], tuple[Any, ...]]:
            nonlocal provider_probe_cache
            configuration_valid, configuration_errors = provider_configuration_health()
            base_report: dict[str, Any] = {
                "status": "healthy" if configuration_valid else "unhealthy",
                "mode": provider_mode,
                "configuration_valid": configuration_valid,
                "connectivity_healthy": None,
                "required_provider_ids": [],
                "checked_at": None,
                "expires_at": None,
                "probes": [],
                "configuration_errors": [
                    _redacted_provider_error(error) for error in configuration_errors
                ],
            }
            if provider_mode != "live":
                return configuration_valid, base_report, configuration_errors
            if not configuration_valid:
                return False, base_report, configuration_errors
            try:
                with provider_probe_lock:
                    now = time.monotonic()
                    if provider_probe_cache is not None and provider_probe_cache[0] > now:
                        connectivity = provider_probe_cache[1]
                    else:
                        if external_provider_connectivity_probe is None:
                            connectivity = probe_external_provider_connectivity(
                                validation=provider_validation,
                                correlation_id=correlation_id,
                            )
                        else:
                            connectivity = external_provider_connectivity_probe(
                                validation=provider_validation,
                                correlation_id=correlation_id,
                            )
                        provider_probe_cache = (now + 30.0, connectivity)
            except Exception:
                base_report.update(
                    {
                        "status": "unhealthy",
                        "connectivity_healthy": False,
                        "probe_error": "connectivity_probe_failed",
                    }
                )
                return False, base_report, ("connectivity_probe_failed",)
            try:
                connectivity_report = (
                    connectivity.to_dict()
                    if hasattr(connectivity, "to_dict")
                    else dict(connectivity)
                )
            except Exception:
                base_report.update(
                    {
                        "status": "unhealthy",
                        "connectivity_healthy": False,
                        "probe_error": "connectivity_evidence_invalid",
                    }
                )
                return False, base_report, ("connectivity_evidence_invalid",)
            connectivity_healthy = bool(connectivity_report.get("connectivity_healthy"))
            report = {
                **base_report,
                **connectivity_report,
                "status": "healthy" if connectivity_healthy else "unhealthy",
                "configuration_valid": configuration_valid,
                "connectivity_healthy": connectivity_healthy,
            }
            probe_errors = tuple(
                str(probe.get("reason_code") or "probe_failed")
                for probe in connectivity_report.get("probes", [])
                if isinstance(probe, dict) and not bool(probe.get("connectivity_healthy"))
            )
            return connectivity_healthy, report, probe_errors

        def require_live_external_provider() -> None:
            if not require_live_data:
                return
            provider_ok, provider_report, provider_errors = provider_health()
            if provider_ok and provider_mode == "live":
                return
            raise ApiError(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "The required production external provider is unavailable; "
                "fixture providers are disabled.",
                code="external_provider_unavailable",
                next_action=("Restore an approved live provider configuration, then retry."),
                details=[
                    {
                        "dependency": "external_provider",
                        "provider_mode": provider_mode,
                        "configuration_valid": provider_report["configuration_valid"],
                        "connectivity_healthy": provider_report["connectivity_healthy"],
                        "errors": list(provider_errors),
                    }
                ],
            )

        def production_persistence_blocking_reasons(*, persistence_reachable: bool) -> list[str]:
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
            provider_report: dict[str, Any],
            persistence_reachable: bool,
        ) -> dict[str, Any]:
            provider_configuration_valid = bool(provider_report["configuration_valid"])
            provider_connectivity_healthy = bool(provider_report["connectivity_healthy"])
            provider_live_ready = (
                provider_mode == "live"
                and provider_configuration_valid
                and provider_connectivity_healthy
            )
            operator_probe = (
                operator_live_repository.probe() if operator_live_repository is not None else None
            )
            operator_repository_ready = bool(operator_probe is not None and operator_probe.ready)
            live_ready = (
                production_persistence_supported
                and persistence_reachable
                and provider_live_ready
                and operator_repository_ready
            )
            model_blocking_reasons = (
                ["PRODUCTION_MODEL_BINDINGS_UNVERIFIED"]
                if require_live_data and not production_model_bindings_ready
                else []
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
                if (
                    provider_mode == "live"
                    and provider_configuration_valid
                    and not provider_connectivity_healthy
                ):
                    blocking_reasons.append("PROVIDER_CONNECTIVITY_UNHEALTHY")
                if not operator_repository_ready:
                    blocking_reasons.append("OPERATOR_LIVE_REPOSITORY_UNAVAILABLE")
            return {
                "requireLiveData": require_live_data,
                "deploymentMode": active_deployment_mode,
                "persistence": {
                    "configuredMode": configured_persistence_mode,
                    "runtimeMode": persistence_mode,
                    "durable": bool(bundle.is_durable),
                    "reachable": persistence_reachable,
                    "production_persistence_supported": (production_persistence_supported),
                },
                "provider": {
                    "mode": provider_mode,
                    "configurationValid": provider_configuration_valid,
                    "connectivityHealthy": provider_report["connectivity_healthy"],
                    "healthy": (
                        provider_live_ready
                        if provider_mode == "live" or require_live_data
                        else provider_configuration_valid
                    ),
                    "live": provider_live_ready,
                    "probeEvidence": provider_report,
                },
                "models": {
                    "mode": model_binding_mode,
                    "productionBindingsReady": production_model_bindings_ready,
                    "requiredServices": sorted(required_model_services),
                    "capabilities": production_model_capabilities,
                    "error": production_model_error,
                    "autoSeeded": (not require_live_data and production_model_bindings_ready),
                    "blockingReasons": model_blocking_reasons,
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
                        operator_probe.to_dict() if operator_probe is not None else None
                    ),
                    "liveReady": live_ready,
                    "blockingReasons": blocking_reasons,
                },
            }

        class TelemetryMiddleware:
            """Production HTTP telemetry middleware recording api_request_count, api_error_count, and api_latency_ms."""
            pass

        @api.middleware("http")
        async def attach_correlation_id(request: Request, call_next: Any) -> Response:
            context = CorrelationContext.from_header(request.headers.get(CORRELATION_ID_HEADER))
            request.state.correlation_id = context.correlation_id

            trace_ctx = TraceContext(
                correlation_id=context.correlation_id,
                actor_id="user",
                request_id=context.correlation_id,
            )

            start_t = time.monotonic()

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
                    "/platform/observability",
                    "/api/v1/platform/observability",
                    "/platform/metrics/export",
                    "/api/v1/platform/metrics/export",
                    "/platform/dashboards/provisioned",
                    "/api/v1/platform/dashboards/provisioned",
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
                        response.headers[CORRELATION_ID_HEADER] = context.correlation_id
                        span.status = SpanStatus.ERROR
                        span.error_code = "HTTP_503"
                        status_str = "503"
                        telemetry.metrics.increment("api_request_count", labels={"service": "oday-api", "route": request.url.path, "status": status_str})
                        telemetry.metrics.increment("api_error_count", labels={"service": "oday-api", "route": request.url.path, "status": status_str})
                        return response
                response = await call_next(request)
                status_str = str(response.status_code)
                duration_ms = (time.monotonic() - start_t) * 1000.0
                telemetry.metrics.increment("api_request_count", labels={"service": "oday-api", "route": request.url.path, "status": status_str})
                telemetry.metrics.observe("api_latency_ms", duration_ms, labels={"service": "oday-api", "route": request.url.path})
                if response.status_code >= 400:
                    span.status = SpanStatus.ERROR
                    span.error_code = f"HTTP_{response.status_code}"
                    telemetry.metrics.increment("api_error_count", labels={"service": "oday-api", "route": request.url.path, "status": status_str})
                response.headers[CORRELATION_ID_HEADER] = context.correlation_id
                return response

        @api.get("/healthz", tags=["system"])
        def healthz() -> dict[str, str]:
            # Liveness: simply check that process is running
            return {"status": "ok", "service": "oday-api"}

        @api.get("/readiness", tags=["system"])
        def readiness(response: Response) -> dict[str, Any]:
            db_ok, db_details = database_health()
            provider_ok, provider_report, _provider_errors = provider_health()
            modes = runtime_modes(
                provider_report=provider_report,
                persistence_reachable=db_ok,
            )
            persistence_ok = db_ok and (
                not require_live_data
                or bool(modes["persistence"]["production_persistence_supported"])
            )
            provider_ready = provider_ok and (
                not require_live_data or bool(modes["provider"]["live"])
            )
            live_gate_ok = not require_live_data or bool(modes["data"]["liveReady"])
            overall_ok = persistence_ok and provider_ready and live_gate_ok
            if require_live_data and not modes["persistence"]["production_persistence_supported"]:
                db_details = (
                    f"unsupported for production live data: runtime mode {persistence_mode}"
                )
            details = {
                "database": db_details,
                "external_providers": provider_report,
                "data_mode": modes["data"]["mode"],
                **modes,
            }

            if not overall_ok:
                response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
                return {
                    "status": "unhealthy",
                    "service": "oday-api",
                    "data_mode": modes["data"]["mode"],
                    "details": details,
                }
            return {
                "status": "ok",
                "service": "oday-api",
                "data_mode": modes["data"]["mode"],
                "details": details,
            }

        @api.get("/health", tags=["platform"])
        @api.get("/platform/health", tags=["platform"])
        def health(request: Request, response: Response) -> dict[str, Any]:
            # Detailed health: check database, job queue, and external providers
            db_ok, db_details = database_health()
            provider_ok, provider_report, _provider_errors = provider_health(
                correlation_id=request.state.correlation_id
            )

            queue_ok = True
            if bundle.mode == "postgresql":
                queue_details = "healthy (durable postgresql job queue)"
            elif bundle.mode == "durable":
                queue_details = "healthy (durable sqlite job queue)"
            else:
                queue_details = "healthy (in-memory job queue)"
            try:
                if bundle.is_durable:
                    bundle.engine.query("SELECT COUNT(*) FROM durable_jobs")
            except Exception as exc:
                queue_ok = False
                queue_details = f"unhealthy: {exc}"

            modes = runtime_modes(
                provider_report=provider_report,
                persistence_reachable=db_ok,
            )
            persistence_ok = db_ok and (
                not require_live_data
                or bool(modes["persistence"]["production_persistence_supported"])
            )
            provider_ready = provider_ok and (
                not require_live_data or bool(modes["provider"]["live"])
            )
            live_gate_ok = not require_live_data or bool(modes["data"]["liveReady"])
            if require_live_data and not modes["persistence"]["production_persistence_supported"]:
                db_details = (
                    f"unsupported for production live data: runtime mode {persistence_mode}"
                )
            overall_ok = persistence_ok and provider_ready and queue_ok and live_gate_ok
            if not overall_ok:
                response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

            return {
                "status": "ok" if overall_ok else "unhealthy",
                "service": "oday-api",
                "version": API_VERSION,
                "time": datetime.now(UTC).isoformat(),
                "correlation_id": request.state.correlation_id,
                "data_mode": modes["data"]["mode"],
                "dependencies": {
                    "database": db_details,
                    "job_queue": queue_details,
                    "external_providers": provider_report,
                },
                "modes": modes,
            }

        @api.get("/platform/version", tags=["platform"])
        def platform_version(request: Request) -> dict[str, str]:
            return release_version_payload(correlation_id=request.state.correlation_id)

        platform_observability_router = APIRouter()

        @platform_observability_router.get("/platform/observability", tags=["platform"])
        @platform_observability_router.get("/platform/metrics/export", tags=["platform"])
        def platform_metrics_export(request: Request) -> dict[str, Any]:
            from shared.observability import ProductionMetricsExporter, default_registry

            sha = release_sha_from_environment()
            if require_live_data and (not sha or sha.strip() == "local"):
                raise ApiError(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Production metrics export requires an exact full 40-character release SHA in environment. Fail-closed gate enforced.",
                    code="invalid_release_sha",
                )
            try:
                exporter = ProductionMetricsExporter(release_sha=sha, registry=default_registry())
                return exporter.export_metrics()
            except ValueError as exc:
                raise ApiError(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    f"Production metrics export failed-closed: {exc}",
                    code="invalid_release_sha",
                ) from exc

        @platform_observability_router.get("/platform/dashboards/provisioned", tags=["platform"])
        def platform_dashboards_provisioned(request: Request) -> dict[str, Any]:
            from shared.observability import render_dashboard_provisioning

            sha = release_sha_from_environment()
            if require_live_data and (not sha or sha.strip() == "local"):
                raise ApiError(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Production dashboard provisioning requires an exact full 40-character release SHA in environment. Fail-closed gate enforced.",
                    code="invalid_release_sha",
                )
            try:
                return render_dashboard_provisioning(release_sha=sha)
            except ValueError as exc:
                raise ApiError(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    f"Production dashboard provisioning failed-closed: {exc}",
                    code="invalid_release_sha",
                ) from exc

        mount_versioned(api, platform_observability_router)




        # Jobs and audit-event reads are product operations, so they are
        # versioned like every domain router rather than declared inline on the
        # app (ODP-PGAP-API-001). The health/version probes above stay
        # unversioned on purpose: they are wired into deploy manifests and load
        # balancers that must not be asked to learn a version prefix.
        platform_router = APIRouter()

        def forecast_job_tenant(request: Request, *, action: str) -> str:
            from apps.api.oday_api.security.dependencies import principal_from_headers
            from shared.auth import Action, rbac_allows

            principal = principal_from_headers(request.headers)
            if not principal.authenticated:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "code": "AUTHENTICATION_REQUIRED",
                        "message": "Forecast jobs require an authenticated principal",
                    },
                    headers={"WWW-Authenticate": "Bearer"},
                )
            required_action = Action.EXECUTE if action == "execute" else Action.VIEW
            if not rbac_allows(principal, "forecastops", required_action):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "FORECAST_EXECUTE_FORBIDDEN",
                        "message": "Principal cannot access ForecastOps jobs",
                    },
                )
            active_tenant_id = str(principal.tenant_id or "").strip()
            if not active_tenant_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "TENANT_SCOPE_REQUIRED",
                        "message": "Forecast jobs require an authenticated tenant scope",
                    },
                )
            return active_tenant_id

        @platform_router.post("/jobs", status_code=status.HTTP_202_ACCEPTED, tags=["jobs"])
        def enqueue_job(
            body: JobCreatePayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            payload = body.payload
            idempotency_tenant_id: str | None = None
            if body.job_type == "forecast":
                active_tenant_id = forecast_job_tenant(request, action="execute")
                supplied_tenant_id = str(payload.get("tenant_id") or "").strip()
                if supplied_tenant_id and supplied_tenant_id != active_tenant_id:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail={
                            "code": "TENANT_SCOPE_MISMATCH",
                            "message": (
                                "Forecast job tenant does not match the authenticated tenant scope"
                            ),
                        },
                    )
                payload = {**payload, "tenant_id": active_tenant_id}
                idempotency_tenant_id = active_tenant_id

            effective_idempotency_key = body.idempotency_key or idempotency_key
            queue_idempotency_key = effective_idempotency_key
            if effective_idempotency_key and idempotency_tenant_id is not None:
                queue_idempotency_key = (
                    f"forecast:v1:{idempotency_tenant_id}:{effective_idempotency_key}"
                )
            job, created = job_queue.enqueue(
                JobRequest(
                    job_type=body.job_type,
                    payload=payload,
                    idempotency_key=queue_idempotency_key,
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
                "idempotency_key": effective_idempotency_key,
                "job": job.to_dict(),
                "created": created,
                "audit_event_id": audit_event.event_id,
            }

        @platform_router.get("/jobs/{job_id}", tags=["jobs"])
        def get_job(job_id: str, request: Request) -> dict[str, Any]:
            job = job_queue.get(job_id)
            if job is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
            if job.job_type == "forecast":
                active_tenant_id = forecast_job_tenant(request, action="view")
                owner_tenant_id = str(job.payload.get("tenant_id") or "").strip()
                if not owner_tenant_id:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail={
                            "code": "JOB_TENANT_SCOPE_MISSING",
                            "message": "Forecast job receipt has no tenant ownership scope",
                        },
                    )
                if owner_tenant_id != active_tenant_id:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="job not found",
                    )
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
        realization_hook = CandidateSiteRealizationHook(store=bundle.sitescore_realized_store)
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

        release_sha = release_sha_from_environment()
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
                    model_names=production_model_names()
                )
            except ProductionModelRuntimeError as exc:
                for service, capability in production_model_capabilities.items():
                    if capability["trainable"] and service not in _governed_disabled:
                        capability["reasonCode"] = exc.code
                        capability["error"] = str(exc)
                        if service in required_model_services:
                            production_composition_errors.append(f"{service}: {exc.code}: {exc}")
                model_runtime = None
            if model_runtime is not None:
                feature_schema_versions = {
                    "avm": AVM_FEATURE_VERSION,
                    "forecastops": FORECASTOPS_FEATURE_VERSION,
                    "heatzone": HEATZONE_FEATURE_VERSION,
                    "sitescore": SITESCORE_FEATURE_VERSION,
                }
                for service, feature_schema_version in feature_schema_versions.items():
                    if service in _governed_disabled:
                        # Governed-disabled services have no production alias by definition.
                        # Do not attempt MLflow resolution; the capability record already
                        # carries full governed-disabled evidence from the contract.
                        # Attempting to resolve would always fail and would pollute
                        # production_model_error, breaking the runtime:model_bindings gate check.
                        continue
                    try:
                        executable = model_runtime.resolve(
                            service=service,
                            expected_feature_schema_version=feature_schema_version,
                        )
                    except ProductionModelRuntimeError as exc:
                        capability = production_model_capabilities[service]
                        capability["reasonCode"] = exc.code
                        capability["error"] = str(exc)
                        if service in required_model_services:
                            production_composition_errors.append(f"{service}: {exc.code}: {exc}")
                    else:
                        scoring_bindings[service] = executable.binding
                        capability = production_model_capabilities[service]
                        capability["available"] = True
                        capability["reasonCode"] = None
                        capability["error"] = None
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
                if production_model_capabilities["avm"]["available"]:
                    try:
                        avm_production_executor = AVMProductionExecutor.from_environment(
                            model_runtime=model_runtime
                        )
                    except AVMProductionExecutionError as exc:
                        capability = production_model_capabilities["avm"]
                        capability["available"] = False
                        capability["reasonCode"] = "AVM_PRODUCTION_EXECUTION_UNAVAILABLE"
                        capability["error"] = str(exc)
                        production_composition_errors.append(
                            f"avm: AVM_PRODUCTION_EXECUTION_UNAVAILABLE: {exc}"
                        )
                        scoring_bindings.pop("avm", None)
                        avm_production_executor = None
            netplan_production_executor = NetPlanProductionExecutor()
            priceops_production_optimizer = PriceOpsProductionOptimizer()
            production_model_error = (
                "; ".join(production_composition_errors) if production_composition_errors else None
            )
            # productionBindingsReady is True when:
            forecastops_active = (
                production_model_capabilities.get("forecastops", {}).get("available") is True
            )
            all_required_resolved = all(
                production_model_capabilities[service]["available"] or service in _governed_disabled
                for service in required_model_services
            )
            production_model_bindings_ready = (
                forecastops_active
                and all_required_resolved
                and model_runtime is not None
                and learninghub_registry is not None
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
                heatzone_store_for_tenant=heatzone_store_for_tenant,
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
                ingestion_run_store_for_tenant=ingestion_run_store_for_tenant,
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
        if production_persistence_supported and assisted_intake_store is None:
            raise RuntimeError("Production PostgreSQL requires a durable Assisted Intake store")
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
                job_queue=job_queue,
                require_durable_commands=require_live_data,
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
                approval_verifier=netplan_approval_verifier,
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
                job_queue=job_queue,
                require_durable_commands=require_live_data,
                production_optimizer=priceops_production_optimizer,
                runtime_mode=domain_runtime_mode,
            ),
        )
        mount_versioned(
            api,
            create_sitescore_router(
                repository=site_repository,
                workflow=decision_workflow,
                sitescore_decision_repository_for_tenant=sitescore_decision_store_for_tenant,
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
                job_queue=job_queue,
                require_durable_jobs=require_live_data,
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
                job_queue=job_queue,
                require_durable_commands=require_live_data,
            ),
        )
        from modules.opsboard.application.network_listings import InMemoryAssistedIntakeRepository
        from shared.infrastructure.persistence.operator_network_listings import (
            DurableAssistedIntakeRepository,
        )

        operator_intake_repository = getattr(bundle, "operator_intake_repository", None) or (
            DurableAssistedIntakeRepository(operator_document_store)
            if operator_document_store is not None
            else InMemoryAssistedIntakeRepository()
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
                return DurableNetPlanRepository(tenant_document_store(tenant_id))

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
                sitescore_decision_repository_for_tenant=(sitescore_decision_repository_for_tenant),
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
                allow_test_reset=os.environ.get("ODP_E2E_MODE", "").strip().lower() == "true",
            ),
        )

        from apps.api.oday_api.routes.feature_flags import create_feature_flags_router

        mount_versioned(
            api,
            create_feature_flags_router(audit_log=audit_log),
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
        api.state.production_model_capabilities = production_model_capabilities
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
        api.state.external_ingestion_service = ingestion_service
        api.state.persistence = bundle
        api.state.external_provider_validation = provider_validation
        api.state.require_live_data = require_live_data
        api.state.persistence_mode = persistence_mode
        api.state.provider_mode = provider_mode
        return api

    app = create_app()
