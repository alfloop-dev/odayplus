from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from modules.external_data.workers import SourceFreshnessEvidence
from shared.audit import InMemoryAuditLog

try:
    from fastapi import APIRouter, Depends, Request
except ModuleNotFoundError:  # pragma: no cover - optional API dependency
    APIRouter = None  # type: ignore[assignment]
else:
    def create_external_data_router(
        *,
        audit_log: InMemoryAuditLog | None = None,
    ) -> APIRouter:
        from apps.api.oday_api.security.dependencies import build_engine, require_permission
        from shared.auth import Action

        active_audit_log = audit_log or InMemoryAuditLog()
        authz_engine = build_engine(audit_log=active_audit_log)

        router = APIRouter(prefix="/external-data", tags=["external-data"])

        @router.get(
            "/freshness",
            dependencies=[Depends(require_permission("integration", Action.VIEW, engine=authz_engine))],
        )
        def list_external_data_freshness(request: Request) -> dict[str, Any]:
            evidence = getattr(request.app.state, "external_freshness_evidence", None)
            if evidence is None:
                evidence = (
                    SourceFreshnessEvidence(
                        provider_id="listing.partner_feed",
                        source_snapshot_id="snap-expansion-20260628-0100",
                        data_status="FRESH",
                        provider_observed_at=datetime(2026, 6, 28, 9, 0, tzinfo=UTC),
                        ingested_at=datetime(2026, 6, 28, 9, 12, tzinfo=UTC),
                        freshness_sla_seconds=int(timedelta(hours=24).total_seconds()),
                        correlation_id=request.state.correlation_id,
                    ),
                )
            return {
                "freshness": [item.to_dict() for item in evidence],
                "correlation_id": request.state.correlation_id,
            }

        return router


    __all__ = ["create_external_data_router"]
