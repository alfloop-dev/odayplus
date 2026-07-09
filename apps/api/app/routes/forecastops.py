from __future__ import annotations

from typing import Any

from shared.audit import AuditEvent, InMemoryAuditLog

try:
    from fastapi import APIRouter, Header, Request, status, Depends
    from pydantic import BaseModel, Field
except ModuleNotFoundError:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment]
else:
    from modules.forecastops.application import ForecastOpsService
    from modules.forecastops.infrastructure import InMemoryForecastOpsRepository
    from modules.forecastops.workers import ForecastOpsBatchResult, run_forecastops_batch_forecast


    class ForecastOpsTimeseriesPayload(BaseModel):
        observations: list[dict[str, Any]] = Field(default_factory=list)


    class ForecastOpsForecastJobPayload(BaseModel):
        inputs: list[dict[str, Any]] = Field(default_factory=list)
        prediction_origin_time: str | None = None
        idempotency_key: str | None = None


    class ForecastOpsJobStore:
        def __init__(self) -> None:
            self._jobs: dict[str, ForecastOpsBatchResult] = {}
            self._idempotency_index: dict[str, str] = {}

        def put(
            self, result: ForecastOpsBatchResult, *, idempotency_key: str | None = None
        ) -> tuple[ForecastOpsBatchResult, bool]:
            if idempotency_key and idempotency_key in self._idempotency_index:
                return self._jobs[self._idempotency_index[idempotency_key]], False
            self._jobs[result.job_id] = result
            if idempotency_key:
                self._idempotency_index[idempotency_key] = result.job_id
            return result, True

        def get_by_idempotency_key(
            self, idempotency_key: str | None
        ) -> ForecastOpsBatchResult | None:
            if not idempotency_key:
                return None
            job_id = self._idempotency_index.get(idempotency_key)
            if job_id is None:
                return None
            return self._jobs[job_id]

        def get(self, job_id: str) -> ForecastOpsBatchResult | None:
            return self._jobs.get(job_id)


    def create_forecastops_router(
        *,
        repository: InMemoryForecastOpsRepository | None = None,
        audit_log: InMemoryAuditLog | None = None,
        job_store: ForecastOpsJobStore | None = None,
    ) -> APIRouter:
        from shared.auth import Action
        from apps.api.oday_api.security.dependencies import build_engine, require_permission

        router = APIRouter(prefix="/forecastops", tags=["forecastops"])
        forecast_repository = repository or InMemoryForecastOpsRepository()
        active_audit_log = audit_log or InMemoryAuditLog()
        authz_engine = build_engine(audit_log=active_audit_log)
        jobs = job_store or ForecastOpsJobStore()
        service = ForecastOpsService(repository=forecast_repository)

        @router.post("/timeseries", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_permission("forecastops", Action.CREATE, engine=authz_engine))])
        def ingest_timeseries(body: ForecastOpsTimeseriesPayload) -> dict[str, Any]:
            series = service.ingest_timeseries(body.observations)
            return {
                "items": [item.to_dict() for item in series],
                "count": len(series),
            }

        @router.get("/timeseries", dependencies=[Depends(require_permission("forecastops", Action.VIEW, engine=authz_engine))])
        def list_timeseries() -> dict[str, Any]:
            series = forecast_repository.list_series()
            return {"items": [item.to_dict() for item in series], "count": len(series)}

        @router.post("/forecast-jobs", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_permission("forecastops", Action.EXECUTE, engine=authz_engine))])
        def create_forecast_job(
            body: ForecastOpsForecastJobPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            effective_key = body.idempotency_key or idempotency_key
            result = jobs.get_by_idempotency_key(effective_key)
            created = result is None
            if result is None:
                result, created = jobs.put(
                    run_forecastops_batch_forecast(
                        inputs=body.inputs,
                        prediction_origin_time=body.prediction_origin_time,
                        repository=forecast_repository,
                    ),
                    idempotency_key=effective_key,
                )
            audit_event = active_audit_log.record(
                AuditEvent(
                    event_type="forecastops.forecasted.v1",
                    actor="system",
                    action="run_model",
                    resource="forecastops/forecast-job",
                    outcome="accepted" if created else "idempotent_replay",
                    correlation_id=request.state.correlation_id,
                    job_id=result.job_id,
                    metadata={
                        "idempotency_key": effective_key,
                        "store_count": len(body.inputs),
                        "created": created,
                    },
                )
            )
            payload = result.to_dict()
            payload["created"] = created
            payload["audit_event_id"] = audit_event.event_id
            payload["correlation_id"] = request.state.correlation_id
            return payload

        @router.get("/forecast-jobs/{job_id}", dependencies=[Depends(require_permission("forecastops", Action.VIEW, engine=authz_engine))])
        def get_forecast_job(job_id: str) -> dict[str, Any] | None:
            result = jobs.get(job_id)
            if result is None:
                return None
            return result.to_dict()

        @router.get("/forecasts", dependencies=[Depends(require_permission("forecastops", Action.VIEW, engine=authz_engine))])
        def list_forecasts() -> dict[str, Any]:
            forecasts = forecast_repository.latest_forecasts()
            return {"items": [forecast.to_dict() for forecast in forecasts], "count": len(forecasts)}

        @router.get("/alerts", dependencies=[Depends(require_permission("forecastops", Action.VIEW, engine=authz_engine))])
        def list_alerts(level: str | None = None) -> dict[str, Any]:
            alerts = [
                alert
                for alert in forecast_repository.list_alerts()
                if level is None or alert.alert_level.value == level
            ]
            return {"items": [alert.to_dict() for alert in alerts], "count": len(alerts)}

        @router.get("/intervention-handoffs", dependencies=[Depends(require_permission("forecastops", Action.VIEW, engine=authz_engine))])
        def list_handoffs() -> dict[str, Any]:
            handoffs = forecast_repository.list_handoffs()
            return {"items": [handoff.to_dict() for handoff in handoffs], "count": len(handoffs)}

        return router


    __all__ = [
        "ForecastOpsForecastJobPayload",
        "ForecastOpsJobStore",
        "ForecastOpsTimeseriesPayload",
        "create_forecastops_router",
    ]
