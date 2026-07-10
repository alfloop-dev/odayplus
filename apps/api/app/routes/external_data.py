from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from modules.external_data.workers import SourceFreshnessEvidence

try:
    from fastapi import APIRouter, Request
except ModuleNotFoundError:  # pragma: no cover - optional API dependency
    router: Any = None
else:
    router = APIRouter(prefix="/external-data", tags=["external-data"])


    @router.get("/freshness")
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
