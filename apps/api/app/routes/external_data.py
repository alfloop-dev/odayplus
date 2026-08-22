"""Read-only external-data provenance routes.

XR-CUTOVER-001 decommissioned odayplus-side external ingestion: the manual
``POST /external-data/ingestion-runs`` trigger and the ingestion service behind
it are gone, and the datasets are now produced by ``oday-data-platform`` and
read through :mod:`modules.external_data.application.market_data_facade`.

What is left here is the operator provenance surface over runs that were
already persisted: freshness, ingestion-run history and DQ quarantine. Nothing
in this module can start a fetch. The trigger's route is kept only to answer
``410 Gone`` with a code an operator can branch on, because a bare ``405 Method
Not Allowed`` reads like a routing bug rather than a decision.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from apps.api.oday_api.runtime_mode import live_data_required
from modules.external_data.application.ingestion_records import (
    InMemoryIngestionRunStore,
    SourceFreshnessEvidence,
)
from shared.api.errors import ApiError
from shared.audit import InMemoryAuditLog

_FIXTURE_PRODUCT_MODES = frozenset({"poc", "test"})


def _fixture_freshness_enabled() -> bool:
    import os

    product_mode = os.environ.get("ODP_PRODUCT_MODE", "").strip().lower()
    provider_mode = os.environ.get("ODP_EXTERNAL_PROVIDER_MODE", "").strip().lower()
    return (
        product_mode in _FIXTURE_PRODUCT_MODES
        and provider_mode != "live"
        and not live_data_required()
    )


try:
    from fastapi import APIRouter, Depends, HTTPException, Request, status
except ModuleNotFoundError:  # pragma: no cover - optional API dependency
    APIRouter = None  # type: ignore[assignment]
else:

    def create_external_data_router(
        *,
        ingestion_run_store: Any | None = None,
        ingestion_run_store_for_tenant: Any | None = None,
        audit_log: InMemoryAuditLog | None = None,
    ) -> APIRouter:
        from apps.api.app.routes._common import resolve_tenant_id
        from apps.api.oday_api.security.dependencies import build_engine, require_permission
        from shared.auth import Action

        active_audit_log = audit_log or InMemoryAuditLog()
        default_store = ingestion_run_store or InMemoryIngestionRunStore()
        authz_engine = build_engine(audit_log=active_audit_log)

        router = APIRouter(prefix="/external-data", tags=["external-data"])

        view_guard = Depends(require_permission("integration", Action.VIEW, engine=authz_engine))

        def store_for_request(request: Request) -> Any:
            tid = resolve_tenant_id(request)
            if ingestion_run_store_for_tenant is not None:
                try:
                    scoped = ingestion_run_store_for_tenant(tid)
                    if scoped is not None:
                        return scoped
                except HTTPException:
                    raise
                except Exception as exc:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail=f"Failed to resolve tenant-scoped ingestion store: {exc}",
                    ) from exc
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to resolve tenant-scoped ingestion store",
                )
            return default_store

        @router.get("/freshness", dependencies=[view_guard])
        def list_external_data_freshness(request: Request) -> dict[str, Any]:
            active_store = store_for_request(request)
            evidence = active_store.freshness()
            fixture_used = False
            if not evidence and _fixture_freshness_enabled():
                fixture_used = True
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
            availability = (
                {
                    "status": "AVAILABLE",
                    "reason_code": None,
                    "source": "fixture" if fixture_used else "persisted",
                }
                if evidence
                else {
                    "status": "UNAVAILABLE",
                    "reason_code": "NO_PERSISTED_FRESHNESS_EVIDENCE",
                    "source": "persisted",
                }
            )
            return {
                "freshness": [item.to_dict() for item in evidence],
                "availability": availability,
                "correlation_id": request.state.correlation_id,
            }

        @router.get("/ingestion-runs", dependencies=[view_guard])
        def list_ingestion_runs(
            request: Request, provider_id: str | None = None, limit: int = 100
        ) -> dict[str, Any]:
            active_store = store_for_request(request)
            runs = active_store.list_runs(provider_id=provider_id)
            if limit >= 0:
                runs = runs[-limit:] if limit else []
            return {"items": [run.to_dict() for run in runs], "count": len(runs)}

        @router.get("/ingestion-runs/{run_id}", dependencies=[view_guard])
        def get_ingestion_run(run_id: str, request: Request) -> dict[str, Any]:
            active_store = store_for_request(request)
            run = active_store.get(run_id)
            if run is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="ingestion run not found"
                )
            return run.to_dict()

        @router.get("/quarantine", dependencies=[view_guard])
        def list_quarantine(request: Request, provider_id: str | None = None) -> dict[str, Any]:
            active_store = store_for_request(request)
            rows = active_store.quarantine_records(provider_id=provider_id)
            return {"items": rows, "count": len(rows)}

        @router.post("/ingestion-runs", status_code=status.HTTP_410_GONE)
        def trigger_ingestion_run() -> dict[str, Any]:
            """Refuse the retired manual ingestion trigger (XR-CUTOVER-001).

            No authorization dependency: there is nothing to authorize. The
            answer is the same for every caller and reveals nothing beyond the
            fact that a route this repository once served is gone.
            """
            raise ApiError(
                status.HTTP_410_GONE,
                "Manual external-data ingestion was decommissioned by "
                "XR-CUTOVER-001; this deployment cannot fetch external sources.",
                code="external_fetch_decommissioned",
                next_action=(
                    "Read the published dataset through the market data facade. "
                    "The GET routes on this prefix still serve the provenance of "
                    "runs ingested before the cutover."
                ),
            )

        return router

    __all__ = ["create_external_data_router"]
