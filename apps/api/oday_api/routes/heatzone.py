from __future__ import annotations

from typing import Any

from models.shared_ml import ModelBinding, ScoringInputUnavailableError, require_live_inputs
from shared.audit import AuditEvent, InMemoryAuditLog

try:
    from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
    from pydantic import BaseModel, Field
except ModuleNotFoundError:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment]
else:
    from modules.heatzone.workers import HeatZoneBatchScoreResult, run_heatzone_batch_score


    class HeatZoneScoreJobPayload(BaseModel):
        features: list[dict[str, Any]] = Field(default_factory=list)
        prediction_origin_time: str | None = None
        idempotency_key: str | None = None


    class HeatZoneResultStore:
        def __init__(self) -> None:
            self._latest: HeatZoneBatchScoreResult | None = None
            self._jobs: dict[str, HeatZoneBatchScoreResult] = {}
            self._idempotency_index: dict[str, str] = {}

        def put(
            self,
            result: HeatZoneBatchScoreResult,
            *,
            idempotency_key: str | None = None,
        ) -> tuple[HeatZoneBatchScoreResult, bool]:
            if idempotency_key and idempotency_key in self._idempotency_index:
                existing = self._jobs[self._idempotency_index[idempotency_key]]
                return existing, False
            self._jobs[result.job_id] = result
            self._latest = result
            if idempotency_key:
                self._idempotency_index[idempotency_key] = result.job_id
            return result, True

        def list_scores(self) -> list[dict[str, Any]]:
            if self._latest is None:
                return []
            return [score.to_dict() for score in self._latest.scores]

        def map_features(self) -> list[dict[str, Any]]:
            if self._latest is None:
                return []
            return [score.to_map_feature() for score in self._latest.scores]

        def snapshot(self, snapshot_id: str) -> HeatZoneBatchScoreResult | None:
            if self._latest and snapshot_id == "latest":
                return self._latest
            return self._jobs.get(snapshot_id)


    def create_heatzone_router(
        *,
        store: HeatZoneResultStore | None = None,
        audit_log: InMemoryAuditLog | None = None,
        model_binding: ModelBinding | None = None,
    ) -> APIRouter:
        from apps.api.oday_api.security.dependencies import build_engine, require_permission
        from shared.auth import Action

        router = APIRouter(prefix="/heatzones", tags=["heatzones"])
        result_store = store or HeatZoneResultStore()
        active_audit_log = audit_log or InMemoryAuditLog()
        authz_engine = build_engine(audit_log=active_audit_log)

        @router.get("", dependencies=[Depends(require_permission("heatzone", Action.VIEW, engine=authz_engine))])
        def list_heatzones(limit: int = 100) -> dict[str, Any]:
            scores = result_store.list_scores()[: max(0, limit)]
            return {"items": scores, "count": len(scores)}

        @router.get("/map", dependencies=[Depends(require_permission("heatzone", Action.VIEW, engine=authz_engine))])
        def heatzone_map() -> dict[str, Any]:
            features = result_store.map_features()
            return {
                "type": "FeatureCollection",
                "features": features,
                "count": len(features),
            }

        @router.post(
            "/score-jobs",
            status_code=status.HTTP_202_ACCEPTED,
            dependencies=[Depends(require_permission("heatzone", Action.CREATE, engine=authz_engine))],
        )
        def create_score_job(
            body: HeatZoneScoreJobPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            effective_idempotency_key = body.idempotency_key or idempotency_key
            existing_job_id = (
                result_store._idempotency_index.get(effective_idempotency_key)
                if effective_idempotency_key
                else None
            )
            if existing_job_id is not None:
                result, created = result_store.put(
                    result_store._jobs[existing_job_id],
                    idempotency_key=effective_idempotency_key,
                )
            else:
                # Fail closed: refuse a fresh run when live inputs are absent.
                try:
                    require_live_inputs(body.features, service="heatzone")
                except ScoringInputUnavailableError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
                    ) from exc
                result, created = result_store.put(
                    run_heatzone_batch_score(
                        features=body.features,
                        prediction_origin_time=body.prediction_origin_time,
                    ),
                    idempotency_key=effective_idempotency_key,
                )
            metadata: dict[str, Any] = {
                "idempotency_key": effective_idempotency_key,
                "feature_count": len(body.features),
                "created": created,
            }
            if model_binding is not None:
                metadata["model_binding"] = model_binding.to_audit_metadata()
            audit_event = active_audit_log.record(
                AuditEvent(
                    event_type="heatzone.scored.v1",
                    actor="system",
                    action="run_model",
                    resource="heatzone/score-job",
                    outcome="accepted" if created else "idempotent_replay",
                    correlation_id=request.state.correlation_id,
                    job_id=result.job_id,
                    metadata=metadata,
                )
            )
            payload = result.to_dict()
            payload["created"] = created
            payload["audit_event_id"] = audit_event.event_id
            payload["correlation_id"] = request.state.correlation_id
            if model_binding is not None:
                payload["model_binding"] = model_binding.to_audit_metadata()
            return payload

        @router.get("/snapshots/{snapshot_id}", dependencies=[Depends(require_permission("heatzone", Action.VIEW, engine=authz_engine))])
        def snapshot(snapshot_id: str) -> dict[str, Any] | None:
            result = result_store.snapshot(snapshot_id)
            if result is None:
                return None
            return result.to_dict()

        @router.get("/{h3_index}", dependencies=[Depends(require_permission("heatzone", Action.VIEW, engine=authz_engine))])
        def heatzone_detail(h3_index: str) -> dict[str, Any] | None:
            for item in result_store.list_scores():
                if item["h3_index"] == h3_index:
                    return item
            return None

        return router


    __all__ = ["HeatZoneResultStore", "HeatZoneScoreJobPayload", "create_heatzone_router"]
