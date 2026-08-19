from __future__ import annotations

from threading import RLock
from typing import Any
from uuid import uuid4

from models.shared_ml import (
    ModelBinding,
    ProductionModelInputError,
    ProductionModelRuntime,
    ProductionModelRuntimeError,
    ScoringInputUnavailableError,
    production_model_execution_required,
    require_live_inputs,
    require_production_runtime,
)
from shared.api.idempotency import IdempotencyConflictError
from shared.audit import AuditEvent, InMemoryAuditLog
from shared.infrastructure.persistence.command_receipts import (
    CommandReceiptIncompleteError,
    CommandReceiptPersistenceError,
    TenantScopedCommandReceiptStore,
)
from shared.infrastructure.persistence.job_receipts import JobQueue
from shared.jobs.queue import InMemoryJobQueue

try:
    from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
    from pydantic import BaseModel, Field
except ModuleNotFoundError:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment]
else:
    from modules.forecastops.application import (
        ForecastOpsService,
        RegisteredEstimatorForecastEngine,
    )
    from modules.forecastops.domain import ForecastOpsError, ForecastOpsNotFoundError
    from modules.forecastops.infrastructure import InMemoryForecastOpsRepository
    from modules.forecastops.runtime import (
        ForecastOpsRuntimeConfigurationError,
        forecastops_production_required,
    )
    from modules.forecastops.workers import ForecastOpsBatchResult, run_forecastops_batch_forecast

    class ForecastOpsTimeseriesPayload(BaseModel):
        observations: list[dict[str, Any]] = Field(default_factory=list)

    class ForecastOpsAlertAcknowledgePayload(BaseModel):
        actor: str
        note: str | None = None

    class ForecastOpsHandoffExecutePayload(BaseModel):
        actor: str
        intervention_id: str | None = None

    class ForecastOpsForecastJobPayload(BaseModel):
        inputs: list[dict[str, Any]] = Field(default_factory=list)
        prediction_origin_time: str | None = None
        idempotency_key: str | None = None

    class ForecastOpsJobStore:
        def __init__(self) -> None:
            self._jobs: dict[tuple[str, str], ForecastOpsBatchResult] = {}
            self._idempotency_index: dict[tuple[str, str], str] = {}
            self._lock = RLock()

        def put(
            self,
            tenant_id: str,
            result: ForecastOpsBatchResult,
            *,
            idempotency_key: str | None = None,
        ) -> tuple[ForecastOpsBatchResult, bool]:
            with self._lock:
                index_key = (tenant_id, idempotency_key) if idempotency_key else None
                if index_key and index_key in self._idempotency_index:
                    job_id = self._idempotency_index[index_key]
                    return self._jobs[(tenant_id, job_id)], False
                self._jobs[(tenant_id, result.job_id)] = result
                if index_key:
                    self._idempotency_index[index_key] = result.job_id
                return result, True

        def get_by_idempotency_key(
            self, tenant_id: str, idempotency_key: str | None
        ) -> ForecastOpsBatchResult | None:
            if not idempotency_key:
                return None
            with self._lock:
                job_id = self._idempotency_index.get((tenant_id, idempotency_key))
                if job_id is None:
                    return None
                return self._jobs[(tenant_id, job_id)]

        def get(self, tenant_id: str, job_id: str) -> ForecastOpsBatchResult | None:
            with self._lock:
                return self._jobs.get((tenant_id, job_id))

        def run(
            self,
            tenant_id: str,
            *,
            idempotency_key: str | None,
            operation: Any,
        ) -> tuple[ForecastOpsBatchResult, bool]:
            with self._lock:
                existing = self.get_by_idempotency_key(tenant_id, idempotency_key)
                if existing is not None:
                    return existing, False
                job_id = f"forecastops-forecast-{uuid4()}"
                result = operation(job_id)
                return self.put(
                    tenant_id,
                    result,
                    idempotency_key=idempotency_key,
                )

    def create_forecastops_router(
        *,
        repository: InMemoryForecastOpsRepository | None = None,
        audit_log: InMemoryAuditLog | None = None,
        job_store: ForecastOpsJobStore | None = None,
        job_queue: JobQueue | None = None,
        model_binding: ModelBinding | None = None,
        model_runtime: ProductionModelRuntime | None = None,
        require_production_model: bool | None = None,
        require_durable_jobs: bool | None = None,
        runtime_mode: str | None = None,
    ) -> APIRouter:
        from apps.api.app.routes._common import runtime_binding_guard
        from apps.api.oday_api.security.dependencies import build_engine, require_permission
        from shared.auth import Action

        production_runtime_required = forecastops_production_required(runtime_mode)
        production_model_required = production_runtime_required or (
            production_model_execution_required()
            if require_production_model is None
            else require_production_model
        )
        durable_jobs_required = production_runtime_required or (
            production_model_execution_required()
            if require_durable_jobs is None
            else require_durable_jobs
        )
        composition_error: ForecastOpsRuntimeConfigurationError | None = None
        forecast_repository = (
            repository
            if production_runtime_required
            else repository or InMemoryForecastOpsRepository()
        )
        active_audit_log = audit_log or InMemoryAuditLog()
        authz_engine = build_engine(audit_log=active_audit_log)
        local_job_queue = None if durable_jobs_required else InMemoryJobQueue()
        try:
            service = ForecastOpsService(
                repository=forecast_repository,
                model_runtime=model_runtime,
                runtime_mode=runtime_mode,
            )
        except ForecastOpsRuntimeConfigurationError as exc:
            composition_error = exc
            service = None

        require_runtime_binding = runtime_binding_guard(composition_error)

        router = APIRouter(
            prefix="/forecastops",
            tags=["forecastops"],
            dependencies=[Depends(require_runtime_binding)],
        )

        def receipt_store(request: Request) -> TenantScopedCommandReceiptStore:
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
                        "message": "ForecastOps production jobs require durable persistence",
                    },
                )
            store = TenantScopedCommandReceiptStore(
                queue=active_queue,
                service="forecastops.forecast",
            )
            if durable_jobs_required and not store.is_durable:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "DURABLE_JOB_RECEIPT_STORE_REQUIRED",
                        "message": "ForecastOps production jobs require durable persistence",
                    },
                )
            return store

        def tenant_id(request: Request) -> str:
            principal = getattr(request.state, "operator_principal", None)
            scope = getattr(principal, "scope", None)
            value = getattr(scope, "tenant_id", None)
            if value:
                return str(value)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "TENANT_SCOPE_REQUIRED",
                    "message": "ForecastOps jobs require an authenticated tenant scope",
                },
            )

        def tenant_scoped_inputs(
            inputs: list[dict[str, Any]],
            *,
            active_tenant_id: str,
        ) -> list[dict[str, Any]]:
            scoped_inputs: list[dict[str, Any]] = []
            for index, item in enumerate(inputs):
                supplied_tenant_id = str(item.get("tenant_id") or "").strip()
                if supplied_tenant_id and supplied_tenant_id != active_tenant_id:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail={
                            "code": "TENANT_SCOPE_MISMATCH",
                            "message": (
                                f"Forecast input {index} tenant does not match "
                                "the authenticated tenant scope"
                            ),
                        },
                    )
                scoped_inputs.append({**item, "tenant_id": active_tenant_id})
            return scoped_inputs

        @router.post(
            "/timeseries",
            status_code=status.HTTP_202_ACCEPTED,
            dependencies=[
                Depends(require_permission("forecastops", Action.CREATE, engine=authz_engine))
            ],
        )
        def ingest_timeseries(
            body: ForecastOpsTimeseriesPayload, request: Request
        ) -> dict[str, Any]:
            series = service.ingest_timeseries(
                body.observations,
                tenant_id=tenant_id(request),
            )
            return {
                "items": [item.to_dict() for item in series],
                "count": len(series),
            }

        @router.get(
            "/timeseries",
            dependencies=[
                Depends(require_permission("forecastops", Action.VIEW, engine=authz_engine))
            ],
        )
        def list_timeseries(request: Request) -> dict[str, Any]:
            series = forecast_repository.list_series(tenant_id(request))
            return {"items": [item.to_dict() for item in series], "count": len(series)}

        @router.post(
            "/forecast-jobs",
            status_code=status.HTTP_202_ACCEPTED,
            dependencies=[
                Depends(require_permission("forecastops", Action.EXECUTE, engine=authz_engine))
            ],
        )
        def create_forecast_job(
            body: ForecastOpsForecastJobPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            effective_key = body.idempotency_key or idempotency_key
            active_tenant_id = tenant_id(request)
            scoped_inputs = tenant_scoped_inputs(
                body.inputs,
                active_tenant_id=active_tenant_id,
            )
            try:
                require_live_inputs(scoped_inputs, service="forecastops")
            except ScoringInputUnavailableError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(exc),
                ) from exc

            def compute(job_id: str) -> tuple[ForecastOpsBatchResult, ModelBinding | None]:
                registered_engine = None
                if production_model_required:
                    registered_engine = RegisteredEstimatorForecastEngine(
                        require_production_runtime(
                            model_runtime,
                            service="forecastops",
                        )
                    )
                result = run_forecastops_batch_forecast(
                    inputs=scoped_inputs,
                    job_id=job_id,
                    prediction_origin_time=body.prediction_origin_time,
                    repository=forecast_repository,
                    engine=registered_engine,
                )
                binding = model_binding
                if (
                    registered_engine is not None
                    and registered_engine.last_inference is not None
                ):
                    binding = registered_engine.last_inference.binding
                return result, binding

            def build_receipt(
                result: ForecastOpsBatchResult,
                *,
                job_id: str,
                created: bool,
                binding: ModelBinding | None,
            ) -> dict[str, Any]:
                metadata: dict[str, Any] = {
                    "idempotency_key": effective_key,
                    "store_count": len(body.inputs),
                    "created": created,
                    "tenant_id": active_tenant_id,
                }
                if binding is not None:
                    metadata["model_binding"] = binding.to_audit_metadata()
                audit_event = active_audit_log.record(
                    AuditEvent(
                        event_type="forecastops.forecasted.v1",
                        actor="system",
                        action="run_model",
                        resource="forecastops/forecast-job",
                        outcome="accepted" if created else "idempotent_replay",
                        correlation_id=request.state.correlation_id,
                        job_id=job_id,
                        metadata=metadata,
                    )
                )
                payload = result.to_dict()
                payload["job_id"] = job_id
                payload["created"] = created
                payload["audit_event_id"] = audit_event.event_id
                payload["correlation_id"] = request.state.correlation_id
                if binding is not None:
                    payload["model_binding"] = binding.to_audit_metadata()
                return payload

            if job_store is not None:
                if durable_jobs_required:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail={
                            "code": "DURABLE_JOB_RECEIPT_STORE_REQUIRED",
                            "message": "ForecastOps production jobs reject process-local stores",
                        },
                    )
                captured_binding: ModelBinding | None = None

                def local_operation(job_id: str) -> ForecastOpsBatchResult:
                    nonlocal captured_binding
                    result, captured_binding = compute(job_id)
                    return result

                try:
                    result, created = job_store.run(
                        active_tenant_id,
                        idempotency_key=effective_key,
                        operation=local_operation,
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
                return build_receipt(
                    result,
                    job_id=result.job_id,
                    created=created,
                    binding=captured_binding,
                )
            active_receipts = receipt_store(request)

            def durable_operation(job_id: str) -> dict[str, Any]:
                result, binding = compute(job_id)
                return build_receipt(
                    result,
                    job_id=job_id,
                    created=True,
                    binding=binding,
                )

            try:
                outcome = active_receipts.run(
                    tenant_id=active_tenant_id,
                    idempotency_key=effective_key,
                    scope="forecast",
                    payload=body.model_dump(mode="json"),
                    correlation_id=request.state.correlation_id,
                    operation=durable_operation,
                )
            except IdempotencyConflictError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "IDEMPOTENCY_KEY_REUSED", "message": str(exc)},
                ) from exc
            except CommandReceiptIncompleteError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "COMMAND_RECEIPT_INCOMPLETE", "message": str(exc)},
                ) from exc
            except CommandReceiptPersistenceError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"code": "COMMAND_RECEIPT_UNAVAILABLE", "message": str(exc)},
                ) from exc
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
            payload = dict(outcome.value)
            payload["created"] = not outcome.replayed
            if outcome.replayed:
                replay_audit = active_audit_log.record(
                    AuditEvent(
                        event_type="forecastops.forecasted.v1",
                        actor="system",
                        action="run_model",
                        resource="forecastops/forecast-job",
                        outcome="idempotent_replay",
                        correlation_id=request.state.correlation_id,
                        job_id=outcome.receipt_id,
                        metadata={
                            "idempotency_key": effective_key,
                            "store_count": len(body.inputs),
                            "created": False,
                            "tenant_id": active_tenant_id,
                        },
                    )
                )
                payload["audit_event_id"] = replay_audit.event_id
                payload["correlation_id"] = request.state.correlation_id
            return payload

        @router.get(
            "/forecast-jobs/{job_id}",
            dependencies=[
                Depends(require_permission("forecastops", Action.VIEW, engine=authz_engine))
            ],
        )
        def get_forecast_job(job_id: str, request: Request) -> dict[str, Any]:
            if job_store is not None:
                if durable_jobs_required:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail={"code": "DURABLE_JOB_RECEIPT_STORE_REQUIRED"},
                    )
                result = job_store.get(tenant_id(request), job_id)
                if result is not None:
                    return result.to_dict()
            else:
                try:
                    result = receipt_store(request).get(
                        tenant_id=tenant_id(request),
                        receipt_id=job_id,
                    )
                except CommandReceiptIncompleteError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={"code": "COMMAND_RECEIPT_INCOMPLETE", "message": str(exc)},
                    ) from exc
                if result is not None:
                    return result
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="forecast job not found",
            )

        @router.get(
            "/forecasts",
            dependencies=[
                Depends(require_permission("forecastops", Action.VIEW, engine=authz_engine))
            ],
        )
        def list_forecasts(request: Request) -> dict[str, Any]:
            forecasts = forecast_repository.latest_forecasts(tenant_id(request))
            return {
                "items": [forecast.to_dict() for forecast in forecasts],
                "count": len(forecasts),
            }

        @router.get(
            "/alerts",
            dependencies=[
                Depends(require_permission("forecastops", Action.VIEW, engine=authz_engine))
            ],
        )
        def list_alerts(request: Request, level: str | None = None) -> dict[str, Any]:
            alerts = [
                alert
                for alert in forecast_repository.list_alerts(tenant_id(request))
                if level is None or alert.alert_level.value == level
            ]
            return {"items": [alert.to_dict() for alert in alerts], "count": len(alerts)}

        @router.post(
            "/alerts/{alert_id}/acknowledge",
            dependencies=[
                Depends(require_permission("forecastops", Action.CREATE, engine=authz_engine))
            ],
        )
        def acknowledge_alert(
            alert_id: str, body: ForecastOpsAlertAcknowledgePayload, request: Request
        ) -> dict[str, Any]:
            try:
                alert = service.acknowledge_alert(
                    tenant_id(request),
                    alert_id,
                    actor=body.actor,
                    note=body.note,
                )
            except ForecastOpsNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ForecastOpsError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
                ) from exc
            audit_event = active_audit_log.record(
                AuditEvent(
                    event_type="forecastops.alert.acknowledged.v1",
                    actor=body.actor,
                    action="acknowledge",
                    resource=f"forecastops/alert/{alert_id}",
                    outcome="acknowledged",
                    correlation_id=request.state.correlation_id,
                    metadata={
                        "alert_level": alert.alert_level.value,
                        "store_id": alert.store_id,
                        "note": body.note,
                    },
                )
            )
            payload = alert.to_dict()
            payload["audit_event_id"] = audit_event.event_id
            payload["correlation_id"] = request.state.correlation_id
            return payload

        @router.get(
            "/intervention-handoffs",
            dependencies=[
                Depends(require_permission("forecastops", Action.VIEW, engine=authz_engine))
            ],
        )
        def list_handoffs(request: Request) -> dict[str, Any]:
            handoffs = forecast_repository.list_handoffs(tenant_id(request))
            return {"items": [handoff.to_dict() for handoff in handoffs], "count": len(handoffs)}

        @router.post(
            "/intervention-handoffs/{handoff_id}/execute",
            dependencies=[
                Depends(require_permission("forecastops", Action.EXECUTE, engine=authz_engine))
            ],
        )
        def execute_handoff(
            handoff_id: str, body: ForecastOpsHandoffExecutePayload, request: Request
        ) -> dict[str, Any]:
            try:
                handoff = service.execute_handoff(
                    tenant_id(request),
                    handoff_id,
                    actor=body.actor,
                    intervention_id=body.intervention_id,
                )
            except ForecastOpsNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ForecastOpsError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
                ) from exc
            audit_event = active_audit_log.record(
                AuditEvent(
                    event_type="forecastops.handoff.executed.v1",
                    actor=body.actor,
                    action="execute",
                    resource=f"forecastops/intervention-handoff/{handoff_id}",
                    outcome="dispatched",
                    correlation_id=request.state.correlation_id,
                    metadata={
                        "store_id": handoff.store_id,
                        "intervention_type": handoff.intervention_type,
                        "intervention_id": body.intervention_id,
                    },
                )
            )
            payload = handoff.to_dict()
            payload["audit_event_id"] = audit_event.event_id
            payload["correlation_id"] = request.state.correlation_id
            return payload

        @router.get(
            "/prediction-runs/{prediction_run_id}",
            dependencies=[
                Depends(require_permission("forecastops", Action.VIEW, engine=authz_engine))
            ],
        )
        def get_prediction_run(prediction_run_id: str, request: Request) -> dict[str, Any]:
            active_tenant_id = tenant_id(request)
            run = forecast_repository.get_prediction_run(
                active_tenant_id,
                prediction_run_id,
            )
            if run is None:
                raise HTTPException(status_code=404, detail="prediction run not found")
            predictions = forecast_repository.get_predictions(
                active_tenant_id,
                prediction_run_id,
            )
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
                    }
                    for p in predictions
                ],
            }

        @router.get(
            "/forecast-outputs/{forecast_output_id}",
            dependencies=[
                Depends(require_permission("forecastops", Action.VIEW, engine=authz_engine))
            ],
        )
        def get_forecast_output(forecast_output_id: str, request: Request) -> dict[str, Any]:
            forecast = forecast_repository.get_canonical_forecast(
                tenant_id(request),
                forecast_output_id,
            )
            if forecast is None:
                raise HTTPException(status_code=404, detail="forecast output not found")
            return {
                "forecast_output_id": forecast.forecast_output_id,
                "store_id": forecast.store_id,
                "prediction_run_id": forecast.prediction_run_id,
                "horizon_days": forecast.horizon_days,
                "target_metric": forecast.target_metric,
                "p10": forecast.p10,
                "p50": forecast.p50,
                "p90": forecast.p90,
                "trajectory_class": forecast.trajectory_class,
                "turning_point_probability": forecast.turning_point_probability,
                "sitescore_gap_ratio": forecast.sitescore_gap_ratio,
            }

        return router

    __all__ = [
        "ForecastOpsAlertAcknowledgePayload",
        "ForecastOpsForecastJobPayload",
        "ForecastOpsHandoffExecutePayload",
        "ForecastOpsJobStore",
        "ForecastOpsTimeseriesPayload",
        "create_forecastops_router",
    ]
