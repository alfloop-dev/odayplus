"""Isolated search browser inputs on the actual canonical PostgreSQL app.

This module is test-only. Importing the existing fixture enforces an explicitly
owned odp_score_browser_ database, synthetic control key and POC mode. No
production app imports this module or enables these control routes.
"""

import hmac
import os
from datetime import UTC, datetime
from threading import Lock
from uuid import uuid4

from fastapi import Depends, Header, HTTPException
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from apps.worker.oday_worker.main import ODayWorker
from modules.heatzone.domain.scoring import HeatZoneScoreResult, HeatZoneState
from modules.heatzone.workers import HeatZoneBatchScoreResult
from shared.infrastructure.persistence.factory import build_persistence
from tests.support.intake_score_browser_server import app as controls_app

SCOPE = "ISOLATED_SYNTHETIC_LISTING_SEARCH_BROWSER_NO_PRODUCTION_CLAIM"
_lock = Lock()

# The normal app owns every product router and its actual correlation, error,
# authorization and telemetry middleware. Only isolated test controls are
# imported from the existing fixture; they share the same real SQL bundle.
app = create_app(persistence=controls_app.state.persistence_bundle)
for route in controls_app.routes:
    if getattr(route, "path", "").startswith("/_synthetic/"):
        app.router.routes.append(route)


def control(x_synthetic_control: str = Header(default="")):
    if not hmac.compare_digest(x_synthetic_control, os.environ["ODP_SCORE_BROWSER_CONTROL_KEY"]):
        raise HTTPException(403, "Synthetic fixture control key required")


@app.post("/_synthetic/listing-search/setup", dependencies=[Depends(control)])
def setup():
    with _lock:
        result = TestClient(app).post(
            "/_synthetic/scenarios",
            headers={"x-synthetic-control": os.environ["ODP_SCORE_BROWSER_CONTROL_KEY"]},
        )
        if result.status_code != 200:
            raise RuntimeError("The canonical synthetic manual receipt was not created")
        case = result.json()
        bundle = app.state.persistence_bundle
        tenant, zone = case["tenant_id"], str(uuid4())
        now = datetime.now(UTC)
        listing = bundle.listing_repository_for_tenant(tenant).get_listing(case["listing_id"])
        if listing is None:
            raise RuntimeError("The canonical measured listing is missing")
        score = HeatZoneScoreResult(
            heat_zone_id=zone,
            h3_index="8929a1d4d67ffff",
            h3_resolution=9,
            score=0.6,
            priority_rank=1,
            unmet_demand_score=0.6,
            format_fit_score=0.6,
            cannibalization_risk_score=0.2,
            rent_feasibility_score=0.6,
            listing_availability_score=0.6,
            confidence=0.6,
            state=HeatZoneState.STILL_EXPANDABLE,
            feature_snapshot_time=now,
            prediction_origin_time=now,
            last_scored_at=now,
            model_version="synthetic-search-browser-only-v1",
            feature_version="synthetic-search-browser-only-v1",
            source_snapshot_ids=(listing.snapshot_id,),
            reasons=(SCOPE,),
            admin_city="Synthetic",
            admin_district="Search browser",
        )
        bundle.heatzone_store_for_tenant(tenant).put(
            HeatZoneBatchScoreResult(
                job_id=str(uuid4()),
                status="succeeded",
                scores=(score,),
                completed_at=now,
                tenant_id=tenant,
            )
        )
        store = bundle.assisted_intake_store
        store.refresh(tenant)
        with store.transaction():
            receipt = store.intakes[case["intake_id"]]
            receipt["scope"]["heat_zone_id"] = zone
            receipt["version"] += 1
            # The inputs come from this fixture's committed measured listing,
            # rather than substituting values in a production matcher or API.
            by_path = {f["field_path"]: f for f in receipt.get("fields", [])}
            for field, value in (
                ("rent_amount", listing.rent_amount),
                ("area_ping", listing.area_ping),
                ("floor", listing.floor),
            ):
                by_path[field] = {
                    "field_path": field,
                    "classification": "INTERNAL",
                    "masked": False,
                    "effective": value,
                    "normalized": value,
                    "parsed": value,
                    "confidence": None,
                }
            receipt["fields"] = list(by_path.values())
            store.flush()
        return {**case, "zone_id": zone, "search_scope": SCOPE}


@app.post("/_synthetic/listing-search/drain", dependencies=[Depends(control)])
def drain():
    with _lock:
        bundle = build_persistence(mode="postgresql")
        worker = ODayWorker(bundle, worker_id="isolated-synthetic-listing-search-browser-worker")
        try:
            for _ in range(500):
                if not worker.run_once():
                    break
            rows = bundle.engine.query(
                "SELECT job_id,status,attempts,error_message FROM durable_jobs WHERE job_type=?",
                ("listing-search-receipt-match",),
            )
            if any(row["status"] != "succeeded" for row in rows):
                raise RuntimeError(
                    "The real matching worker did not settle every committed search event"
                )
            return {"scope": SCOPE, "matching_jobs": rows}
        finally:
            bundle.engine.close()
