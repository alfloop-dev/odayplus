"""External-data provenance routes, with a reversible cutover switch.

Today this module still owns both halves of the legacy surface: the operator
provenance reads (freshness, ingestion-run history, DQ quarantine) and the
manual ``POST /external-data/ingestion-runs`` trigger behind them.

ODP-XR-CUTOVER-PREP-002 prepares the handover to ``oday-data-platform`` without
performing it. Every request resolves the cutover mode from
:mod:`modules.external_data.application.market_data_facade`:

``LEGACY_ONLY`` (default)
    Exactly the behaviour that shipped before this task. Nothing is disabled.

``DUAL_RUN``
    Unchanged legacy behaviour, plus the data-platform snapshot read alongside
    it under ``dual_run`` so an operator can compare the two sources before
    selecting the cutover.

``PLATFORM_PRIMARY``
    Freshness is served from the published platform snapshot, and the manual
    trigger answers ``410 Gone`` with a code an operator can branch on -- a bare
    ``405`` would read like a routing bug rather than a decision.

The mode is read per request, never cached at router build time, because the
rollback lever (``ODAY_MARKET_DATA_KILL_SWITCH_ACTIVE``) has to take effect on a
running deployment without a redeploy.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from apps.api.oday_api.runtime_mode import live_data_required
from modules.external_data.workers import SourceFreshnessEvidence
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
        ingestion_run_store_for_tenant: Any | None = None,
        audit_log: InMemoryAuditLog | None = None,
        require_provider: Callable[[], None] | None = None,
        market_data_facade: Any | None = None,
    ) -> APIRouter:
        from apps.api.app.routes._common import resolve_tenant_id
        from apps.api.oday_api.security.dependencies import build_engine, require_permission
        from modules.external_data.application.market_data_facade import (
            CUTOVER_MODE_PLATFORM_PRIMARY,
            MarketDataValidationError,
            cutover_state,
        )
        from shared.api.errors import ApiError
        from shared.auth import Action

        active_audit_log = audit_log or InMemoryAuditLog()
        service = ingestion_service or ExternalIngestionService(
            store=InMemoryIngestionRunStore(),
            ingestion_run_store_for_tenant=ingestion_run_store_for_tenant,
            audit_log=active_audit_log,
        )
        if ingestion_run_store_for_tenant is not None and getattr(service, "ingestion_run_store_for_tenant", None) is None:
            service.ingestion_run_store_for_tenant = ingestion_run_store_for_tenant
        authz_engine = build_engine(audit_log=active_audit_log)

        router = APIRouter(prefix="/external-data", tags=["external-data"])

        def current_cutover_state() -> dict[str, Any]:
            """Resolve the switch for this request, or fail loudly."""
            try:
                return cutover_state()
            except MarketDataValidationError as exc:
                raise ApiError(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    str(exc),
                    code="external_data_cutover_mode_invalid",
                    next_action=(
                        "Set the cutover mode env var to one of LEGACY_ONLY, "
                        "DUAL_RUN or PLATFORM_PRIMARY on this deployment, or "
                        "unset it to keep the legacy behaviour."
                    ),
                ) from exc

        def refuse_retired_ingestion_trigger() -> None:
            """Answer 410 once the cutover has retired the manual trigger.

            Registered as the *first* route dependency so it runs before the
            authorization and live-provider guards. There is nothing to
            authorize on a route this deployment no longer serves, the answer is
            the same for every caller, and letting the live-provider guard
            answer first would report a decommissioned deployment as a 503
            provider outage.
            """
            state = current_cutover_state()
            if state["legacy_external_fetch_enabled"]:
                return
            raise ApiError(
                status.HTTP_410_GONE,
                "Manual external-data ingestion is decommissioned on this "
                "deployment (cutover mode "
                f"{state['mode']}); it cannot fetch external sources.",
                code="external_fetch_decommissioned",
                next_action=(
                    "Read the published dataset through the market data facade. "
                    "The GET routes on this prefix still serve the provenance of "
                    "runs ingested before the cutover."
                ),
            )

        def platform_snapshot_arm(correlation_id: str) -> dict[str, Any]:
            """Read the data-platform snapshot arm, never raising into the legacy arm.

            In ``DUAL_RUN`` this is the comparison arm and the legacy answer is
            still authoritative, so a platform-side failure has to be reported
            as an unavailable arm rather than taken out on the caller.
            """
            if market_data_facade is None:
                return {
                    "freshness": [],
                    "availability": {
                        "status": "UNAVAILABLE",
                        "reason_code": "PLATFORM_FACADE_NOT_WIRED",
                        "source": "data_platform",
                    },
                }
            try:
                snapshot = market_data_facade.get_platform_snapshot(
                    correlation_id=correlation_id
                )
            except Exception as exc:  # noqa: BLE001 - comparison arm must not break the read
                return {
                    "freshness": [],
                    "availability": {
                        "status": "UNAVAILABLE",
                        "reason_code": "PLATFORM_SNAPSHOT_READ_FAILED",
                        "source": "data_platform",
                    },
                    "error": str(exc),
                }
            rows = list(snapshot.get("freshness") or [])
            return {
                "freshness": rows,
                "availability": {
                    "status": "AVAILABLE" if rows else "UNAVAILABLE",
                    "reason_code": None if rows else "NO_PUBLISHED_PLATFORM_SNAPSHOT",
                    "source": "data_platform",
                },
                "release": snapshot.get("release", {}),
                "status": snapshot.get("status"),
            }

        view_guard = Depends(require_permission("integration", Action.VIEW, engine=authz_engine))
        create_guard = Depends(require_permission("integration", Action.CREATE, engine=authz_engine))
        ingestion_dependencies = [Depends(refuse_retired_ingestion_trigger), create_guard]
        if require_provider is not None:
            ingestion_dependencies.append(Depends(require_provider))

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
            if hasattr(service, "_resolve_store"):
                return service._resolve_store(tid)
            return service.store

        @router.get("/freshness", dependencies=[view_guard])
        def list_external_data_freshness(request: Request) -> dict[str, Any]:
            state = current_cutover_state()
            correlation_id = request.state.correlation_id

            if state["mode"] == CUTOVER_MODE_PLATFORM_PRIMARY:
                # The platform arm is the answer, not a second opinion: this
                # deployment ran no ingestion of its own to report provenance for.
                arm = platform_snapshot_arm(correlation_id)
                return {
                    "freshness": arm["freshness"],
                    "availability": arm["availability"],
                    "correlation_id": correlation_id,
                    "cutover": state,
                }

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
                        correlation_id=correlation_id,
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
            payload: dict[str, Any] = {
                "freshness": [item.to_dict() for item in evidence],
                "availability": availability,
                "correlation_id": correlation_id,
                "cutover": state,
            }
            if state["platform_read_enabled"]:
                payload["dual_run"] = platform_snapshot_arm(correlation_id)
            return payload

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

        @router.post(
            "/ingestion-runs",
            status_code=status.HTTP_202_ACCEPTED,
            dependencies=ingestion_dependencies,
        )
        def trigger_ingestion_run(
            body: IngestionRunPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            effective_idempotency_key = body.idempotency_key or idempotency_key
            tid = resolve_tenant_id(request)
            try:
                outcome = service.ingest(
                    provider_id=body.provider_id,
                    schedule_id=body.schedule_id,
                    trigger="manual",
                    window_start=_parse_dt(body.window_start),
                    window_end=_parse_dt(body.window_end),
                    correlation_id=request.state.correlation_id,
                    api_idempotency_key=effective_idempotency_key,
                    tenant_id=tid,
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to execute tenant ingestion run: {exc}",
                ) from exc
            payload = outcome.record.to_dict()
            payload["created"] = outcome.created
            payload["audit_event_id"] = outcome.audit_event_id
            payload["correlation_id"] = request.state.correlation_id
            return payload

        return router

    __all__ = ["IngestionRunPayload", "create_external_data_router"]
