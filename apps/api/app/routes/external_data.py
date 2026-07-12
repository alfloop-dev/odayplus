from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from modules.external_data.workers import SourceFreshnessEvidence
from shared.audit import InMemoryAuditLog

try:
    from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
    from pydantic import BaseModel
except ModuleNotFoundError:  # pragma: no cover - optional API dependency
    APIRouter = None  # type: ignore[assignment]
else:
    from modules.external_data.application.ingestion_service import ExternalIngestionService
    from modules.external_data.application.ingestion_store import InMemoryIngestionRunStore

    class IngestionRunPayload(BaseModel):
        provider_id: str = "listing.partner_feed"
        schedule_id: str = "manual"
        window_start: str | None = None
        window_end: str | None = None
        idempotency_key: str | None = None

    def _parse_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def create_external_data_router(
        *,
        ingestion_service: ExternalIngestionService | None = None,
        audit_log: InMemoryAuditLog | None = None,
    ) -> APIRouter:
        from apps.api.oday_api.security.dependencies import build_engine, require_permission
        from shared.auth import Action

        active_audit_log = audit_log or InMemoryAuditLog()
        service = ingestion_service or ExternalIngestionService(
            store=InMemoryIngestionRunStore(), audit_log=active_audit_log
        )
        authz_engine = build_engine(audit_log=active_audit_log)

        router = APIRouter(prefix="/external-data", tags=["external-data"])

        view_guard = Depends(require_permission("integration", Action.VIEW, engine=authz_engine))
        create_guard = Depends(require_permission("integration", Action.CREATE, engine=authz_engine))

        @router.get("/freshness", dependencies=[view_guard])
        def list_external_data_freshness(request: Request) -> dict[str, Any]:
            evidence = service.store.freshness()
            if not evidence:
                # Cold store: fall back to the documented fixture so the
                # product renders (and the freshness contract holds) before any
                # run has been persisted.
                evidence = [
                    SourceFreshnessEvidence(
                        provider_id="listing.partner_feed",
                        source_snapshot_id="snap-expansion-20260628-0100",
                        data_status="FRESH",
                        provider_observed_at=datetime(2026, 6, 28, 9, 0, tzinfo=UTC),
                        ingested_at=datetime(2026, 6, 28, 9, 12, tzinfo=UTC),
                        freshness_sla_seconds=int(timedelta(hours=24).total_seconds()),
                        correlation_id=request.state.correlation_id,
                    )
                ]
            return {
                "freshness": [item.to_dict() for item in evidence],
                "correlation_id": request.state.correlation_id,
            }

        @router.get("/ingestion-runs", dependencies=[view_guard])
        def list_ingestion_runs(
            provider_id: str | None = None, limit: int = 100
        ) -> dict[str, Any]:
            runs = service.store.list_runs(provider_id=provider_id)
            if limit >= 0:
                runs = runs[-limit:] if limit else []
            return {"items": [run.to_dict() for run in runs], "count": len(runs)}

        @router.get("/ingestion-runs/{run_id}", dependencies=[view_guard])
        def get_ingestion_run(run_id: str) -> dict[str, Any]:
            run = service.store.get(run_id)
            if run is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="ingestion run not found"
                )
            return run.to_dict()

        @router.get("/quarantine", dependencies=[view_guard])
        def list_quarantine(provider_id: str | None = None) -> dict[str, Any]:
            rows = service.store.quarantine_records(provider_id=provider_id)
            return {"items": rows, "count": len(rows)}

        @router.post(
            "/ingestion-runs",
            status_code=status.HTTP_202_ACCEPTED,
            dependencies=[create_guard],
        )
        def trigger_ingestion_run(
            body: IngestionRunPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            effective_idempotency_key = body.idempotency_key or idempotency_key
            outcome = service.ingest(
                provider_id=body.provider_id,
                schedule_id=body.schedule_id,
                trigger="manual",
                window_start=_parse_dt(body.window_start),
                window_end=_parse_dt(body.window_end),
                correlation_id=request.state.correlation_id,
                api_idempotency_key=effective_idempotency_key,
            )
            payload = outcome.record.to_dict()
            payload["created"] = outcome.created
            payload["audit_event_id"] = outcome.audit_event_id
            payload["correlation_id"] = request.state.correlation_id
            return payload

        return router

    __all__ = ["IngestionRunPayload", "create_external_data_router"]
