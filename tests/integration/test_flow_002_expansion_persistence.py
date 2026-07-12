"""Expansion HeatZone -> SiteScore decision flow survives a restart (ODP-FLOW-002).

This exercises the four task acceptance criteria against durable SQLite storage,
simulating a process restart the same way ODP-PV-009 does (close the engine,
build a fresh persistence bundle + app pointed at the same on-disk file, read
the state back through the public HTTP API):

1. HeatZone ranking and listing dedup persist.
2. Candidate and SiteScore versions persist.
3. Review decision and realization hook are audited (durable audit trail).
4. API-backed map/list/detail keep serving the persisted flow after restart.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.infrastructure.persistence.factory import _durable_bundle

pytest.importorskip("h3")  # listing geocode needs H3; skip when unavailable

CORRELATION_ID = "corr-flow-002-expansion"
PREDICTION_TIME = "2026-06-28T02:00:00Z"
SNAPSHOT_TIME = "2026-06-28T01:00:00Z"

HEADERS = {
    "x-correlation-id": CORRELATION_ID,
    "x-subject-id": "flow-002-test",
    "x-roles": (
        "finance_legal,expansion_user,operations_manager,regional_supervisor,"
        "site_reviewer,data_owner,auditor,executive,model_owner,release_owner,"
        "pricing_manager,marketing_manager"
    ),
}


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "expansion.sqlite3")


def _listing_record(
    source_listing_id: str,
    address: str,
    latitude: float,
    longitude: float,
    rent_amount: float,
    area_ping: float,
    floor: str = "1F",
) -> dict:
    return {
        "source_listing_id": source_listing_id,
        "address_raw": address,
        "latitude": latitude,
        "longitude": longitude,
        "city": "台北市",
        "district": "大安區",
        "rent_amount": rent_amount,
        "currency": "TWD",
        "area_ping": area_ping,
        "floor": floor,
        "listing_status": "active",
        "confidence": 0.92,
        "snapshot_id": "flow002-listing-import-v1",
    }


def _score_candidate(client: TestClient, candidate_id: str, key: str, overrides: dict) -> dict:
    resp = client.post(
        "/sitescore/score-jobs",
        headers={**HEADERS, "Idempotency-Key": key},
        json={
            "prediction_origin_time": PREDICTION_TIME,
            "features": [
                {
                    "candidate_site_id": candidate_id,
                    "feature_snapshot_time": SNAPSHOT_TIME,
                    "heat_zone_id": "hz-1049",
                    "heat_zone_score": 91,
                    "monthly_rent": 45000,
                    "area_ping": 25.5,
                    "frontage_m": 8,
                    "competitor_count": 2,
                    "own_store_count_nearby": 1,
                    "comparable_monthly_revenue_p50": 480000,
                    "buildout_capex": 2500000,
                    "gross_margin_ratio": 0.6,
                    "data_quality_score": 0.95,
                    **overrides,
                }
            ],
        },
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


def _open_decision(client: TestClient, report_id: str, created_by: str) -> dict:
    resp = client.post(
        "/sitescore/decisions",
        headers=HEADERS,
        json={"report_id": report_id, "created_by": created_by},
    )
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    assert payload["decision_status"] == "PENDING_REVIEW"
    return payload


def _heatzone_features() -> list[dict]:
    return [
        {
            "h3_index": "8928308280fffff",
            "h3_resolution": 9,
            "poi_count": 188,
            "competitor_count": 2,
            "active_listing_count": 8,
            "median_listing_rent": 128000,
            "competitor_capacity": 0.24,
            "average_confidence": 0.93,
            "source_snapshot_ids": ["poi_snapshot.valid", "listing_raw_snapshot.valid"],
            "existing_store_count": 1,
            "admin_city": "Taipei",
            "admin_district": "Da-an",
        }
    ]


def test_expansion_flow_persists_across_restart(db_path) -> None:
    bundle = _durable_bundle(db_path)
    try:
        client = TestClient(create_app(persistence=bundle))

        # 1. HeatZone ranking ------------------------------------------------
        heatzone = client.post(
            "/heatzones/score-jobs",
            headers={**HEADERS, "Idempotency-Key": "flow002-heatzone"},
            json={"prediction_origin_time": PREDICTION_TIME, "features": _heatzone_features()},
        )
        assert heatzone.status_code == 202, heatzone.text
        hz_body = heatzone.json()
        heatzone_job_id = hz_body["job_id"]
        h3_index = hz_body["scores"][0]["h3_index"]
        assert hz_body["audit_event_id"]

        # 2. Listing import: dedup + candidate conversion --------------------
        listing = client.post(
            "/listings/import-jobs",
            headers=HEADERS,
            json={
                "source_id": "flow002-listing-source",
                "records": [
                    _listing_record("FLOW002-LST-1", "台北市大安區復興南路二段100號1樓", 25.026, 121.543, 45000, 25.5),
                    _listing_record("FLOW002-LST-1", "台北市大安區復興南路二段100號1樓", 25.026, 121.543, 45000, 25.5),
                    _listing_record("FLOW002-LST-2", "台北市大安區復興南路二段200號地下1樓", 25.028, 121.545, 35000, 30, "B1"),
                ],
            },
        )
        assert listing.status_code == 202, listing.text
        listing_body = listing.json()
        assert listing_body["accepted_count"] == 1
        assert listing_body["duplicate_count"] == 1
        assert listing_body["rejected_count"] == 1
        candidate_id = listing_body["candidates"][0]["candidateSiteId"]

        # 3. SiteScore v1 -> REQUEST_REVISION --------------------------------
        first = _score_candidate(
            client,
            candidate_id,
            "flow002-score-1",
            {"comparable_store_count": 0, "average_confidence": 0.46,
             "source_snapshot_ids": ["flow002-v1"]},
        )
        returned_decision = _open_decision(client, first["reports"][0]["report_id"], "analyst")
        returned = client.post(
            f"/sitescore/decisions/{returned_decision['decision_id']}/decision",
            headers=HEADERS,
            json={"action": "REQUEST_REVISION", "actor": "reviewer", "reason": "need comparables"},
        )
        assert returned.status_code == 200
        assert returned.json()["decision_status"] == "DRAFT"

        # 3b. SiteScore v2 -> APPROVE (realization) --------------------------
        second = _score_candidate(
            client,
            candidate_id,
            "flow002-score-2",
            {"comparable_store_count": 6, "comparable_monthly_revenue_p50": 520000,
             "average_confidence": 0.92, "source_snapshot_ids": ["flow002-v2", "flow002-comps"]},
        )
        second_report = second["reports"][0]
        assert second_report["report_version"] == 2
        approved_decision = _open_decision(client, second_report["report_id"], "analyst")
        approval = client.post(
            f"/sitescore/decisions/{approved_decision['decision_id']}/decision",
            headers=HEADERS,
            json={"action": "APPROVE", "actor": "director", "reason": "policy satisfied"},
        )
        assert approval.status_code == 200
        approval_body = approval.json()
        assert approval_body["decision_status"] == "APPROVED"
        assert approval_body["realization_events"][0]["candidate_site_id"] == candidate_id
    finally:
        bundle.engine.close()

    # --- simulated process restart: fresh bundle + app, same DB file --------
    reopened = _durable_bundle(db_path)
    try:
        client2 = TestClient(create_app(persistence=reopened))

        # AC1: HeatZone ranking + map/list/detail persist ---------------------
        hz_list = client2.get("/heatzones", headers=HEADERS).json()
        assert hz_list["count"] == 1
        hz_map = client2.get("/heatzones/map", headers=HEADERS).json()
        assert hz_map["type"] == "FeatureCollection"
        assert hz_map["count"] == 1
        detail = client2.get(f"/heatzones/{h3_index}", headers=HEADERS).json()
        assert detail is not None and detail["h3_index"] == h3_index
        # HeatZone idempotent replay resolves to the original job after restart.
        replay = client2.post(
            "/heatzones/score-jobs",
            headers={**HEADERS, "Idempotency-Key": "flow002-heatzone"},
            json={"prediction_origin_time": PREDICTION_TIME, "features": _heatzone_features()},
        )
        assert replay.status_code == 202
        assert replay.json()["created"] is False
        assert replay.json()["job_id"] == heatzone_job_id

        # AC1: listing dedup persists (re-import same source listing = dup) ----
        redup = client2.post(
            "/listings/import-jobs",
            headers=HEADERS,
            json={
                "source_id": "flow002-listing-source",
                "records": [
                    _listing_record("FLOW002-LST-1", "台北市大安區復興南路二段100號1樓", 25.026, 121.543, 45000, 25.5),
                ],
            },
        )
        assert redup.status_code == 202
        assert redup.json()["duplicate_count"] == 1
        assert redup.json()["accepted_count"] == 0

        # AC2: candidate persists ---------------------------------------------
        candidates = client2.get("/listings/candidates", headers=HEADERS).json()["candidates"]
        assert candidate_id in [c["candidateSiteId"] for c in candidates]

        # AC2: SiteScore versions persist -------------------------------------
        history = client2.get(f"/sitescore/reports/{candidate_id}", headers=HEADERS).json()
        assert history["version_count"] == 2

        # AC3: review decision persists and is queryable ----------------------
        decision = client2.get(
            f"/sitescore/decisions/{approved_decision['decision_id']}", headers=HEADERS
        ).json()
        assert decision["decision_status"] == "APPROVED"

        # AC3: realization hook result persists -------------------------------
        realized = client2.get("/sitescore/realized", headers=HEADERS).json()
        assert candidate_id in [s["candidate_site_id"] for s in realized["items"]]

        # AC3: the whole loop is auditable via the durable audit trail --------
        events = client2.get(
            "/audit/events", headers=HEADERS, params={"correlation_id": CORRELATION_ID}
        ).json()["events"]
        event_types = {e["event_type"] for e in events}
        assert {"heatzone.scored.v1", "sitescore.scored.v1", "sitescore.decision.v1"} <= event_types
        actions = {e["action"] for e in events}
        assert {"run_model", "return", "approve"} <= actions
    finally:
        reopened.engine.close()
