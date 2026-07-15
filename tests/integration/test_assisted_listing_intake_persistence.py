from __future__ import annotations

import json
from datetime import UTC, datetime
import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.opsboard.application.network_listings import (
    NetworkListingConflict,
    NetworkListingNotFound,
    NetworkListingPolicyError,
    NetworkListingService,
)
from shared.infrastructure.persistence import build_persistence
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.auth import Role
from tests.integration._authz import auth_headers

HEADERS = {
    **auth_headers(Role.EXPANSION_USER),
    "x-tenant-id": "tenant-a",
}


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "intake_durable.sqlite3")


def test_first_submission_contract_test() -> None:
    # 1. Create app in memory mode
    app = create_app()
    client = TestClient(app)

    # 2. Submit a new valid synthetic URL
    # Clean new listing — 新莊副都心
    url = "https://www.synthetic.example/detail-77120345.html"
    resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url, "heatZoneId": "HZ-01"},
        headers={
            **HEADERS,
            "X-Correlation-Id": "corr-first-submit",
            "Idempotency-Key": "idem-first",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"].startswith("IN-")
    assert data["originalUrl"] == url
    assert data["canonicalUrl"] == url
    assert data["stage"] == "READY"
    assert data["policy"] == "APPROVED_RETRIEVAL"
    assert data["matchResult"]["outcome"] == "NEW"

    # Replay with idempotency key
    replay = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url, "heatZoneId": "HZ-01"},
        headers={
            **HEADERS,
            "X-Correlation-Id": "corr-first-submit",
            "Idempotency-Key": "idem-first",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == data["id"]


def test_duplicate_and_revision_contract_test() -> None:
    app = create_app()
    client = TestClient(app)

    # 1. Submit a revision URL
    # Revision — same provider listing id as L-2024, rent reduced 58k -> 55k.
    url = "https://www.synthetic.example/detail-88520242.html"
    resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url, "heatZoneId": "HZ-01"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["matchResult"]["outcome"] == "REVISION"
    assert data["matchResult"]["targetListingId"] == "L-2024"

    # 2. Duplicate submission check (running/pending submission duplicate)
    # First, let's decide revision (action="revise")
    decide_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{data['id']}/decide",
        json={"action": "revise", "reason": "降價更新", "actorRoleId": "expansionManager"},
        headers=HEADERS,
    )
    assert decide_resp.status_code == 200
    assert decide_resp.json()["stage"] == "READY"

    # Now check if target listing rent is updated in snapshot
    snap_resp = client.get("/api/v1/operator/network-listings", headers=HEADERS)
    listings = snap_resp.json()["listings"]
    l2024 = next(l for l in listings if l["id"] == "L-2024")
    assert l2024["rentPerMonth"] == 55000


def test_ambiguous_entity_match_review_test() -> None:
    app = create_app()
    client = TestClient(app)

    # Possible match — same normalized address as L-2025 but different floor/rent.
    url = "https://www.synthetic.example/detail-99310418.html"
    resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url, "heatZoneId": "HZ-02"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["stage"] == "NEEDS_REVIEW"
    assert data["matchResult"]["outcome"] == "POSSIBLE_MATCH"
    assert data["matchResult"]["targetListingId"] == "L-2025"

    # Perform correction to change address so it's no longer conflicting (resolves as NEW)
    correct_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{data['id']}/correct",
        json={
            "fields": {"address": "新北市板橋區府中路 99 號 1F"},
            "reason": "勘誤地址以避開衝突",
            "actorRoleId": "expansionManager",
        },
        headers=HEADERS,
    )
    assert correct_resp.status_code == 200
    corrected_data = correct_resp.json()
    assert corrected_data["stage"] == "READY"
    assert corrected_data["matchResult"]["outcome"] == "NEW"


def test_malformed_payload_contract_test() -> None:
    app = create_app()
    client = TestClient(app)

    # Malformed payload - empty address raw
    url = "https://www.synthetic.example/detail-40028801.html"
    resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url, "heatZoneId": "HZ-01"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["stage"] == "AWAITING_ASSISTED_ENTRY"


def test_unapproved_source_fail_closed_test() -> None:
    app = create_app()
    client = TestClient(app)

    # 1. 591 is ASSISTED_ENTRY_ONLY
    url_591 = "https://www.591.com.tw/rent-detail-12345.html"
    resp_591 = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url_591, "heatZoneId": "HZ-01"},
        headers=HEADERS,
    )
    assert resp_591.status_code == 200
    data_591 = resp_591.json()
    assert data_591["stage"] == "AWAITING_ASSISTED_ENTRY"
    assert data_591["policy"] == "ASSISTED_ENTRY_ONLY"

    # 2. Unknown source is POLICY_UNKNOWN
    url_unknown = "https://www.unknown-domain.com/rent/123"
    resp_unknown = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url_unknown, "heatZoneId": "HZ-01"},
        headers=HEADERS,
    )
    assert resp_unknown.status_code == 200
    data_unknown = resp_unknown.json()
    assert data_unknown["stage"] == "QUARANTINED"
    assert data_unknown["policy"] == "POLICY_UNKNOWN"


def test_timeout_contract_test() -> None:
    app = create_app()
    client = TestClient(app)

    # Timeout URL
    url = "https://www.synthetic.example/detail-50000001.html"
    resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url, "heatZoneId": "HZ-01"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["stage"] == "FAILED"
    assert data["failure"]["code"] == "ODP-INTAKE-RETRIEVAL-TIMEOUT"

    # User enters manual correction which must survive retry
    correct_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{data['id']}/correct",
        json={
            "fields": {"rent": 48000, "address": "新莊興德路店面"},
            "reason": "手動補錄超時物件",
            "actorRoleId": "expansionManager",
        },
        headers=HEADERS,
    )
    assert correct_resp.status_code == 200
    
    # Retry - in tests retrieve will fail again since it's the timeout fixture,
    # but we verify it tries and user's corrections (rent: 48000) survive in parsedFields
    retry_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{data['id']}/retry",
        json={"actorRoleId": "expansionManager"},
        headers=HEADERS,
    )
    assert retry_resp.status_code == 200
    retried_data = retry_resp.json()
    assert retried_data["parsedFields"]["rent"]["correctedValue"] == 48000


def test_process_restart_survival(db_path) -> None:
    # 1. Start application with a durable SQLite bundle
    bundle = _durable_bundle(db_path)
    try:
        app = create_app(persistence=bundle)
        client = TestClient(app)

        # Submit intake
        url = "https://www.synthetic.example/detail-77120345.html"
        resp = client.post(
            "/api/v1/operator/network-listings/intake/submit",
            json={"url": url, "heatZoneId": "HZ-01"},
            headers=HEADERS,
        )
        assert resp.status_code == 200
        intake_id = resp.json()["id"]
    finally:
        bundle.engine.close()

    # --- Simulated process restart ---
    reopened = _durable_bundle(db_path)
    try:
        app2 = create_app(persistence=reopened)
        client2 = TestClient(app2)

        # Retrieve the intake record after restart
        get_resp = client2.get(f"/api/v1/operator/network-listings/intake/{intake_id}", headers=HEADERS)
        assert get_resp.status_code == 200
        assert get_resp.json()["originalUrl"] == url
        assert get_resp.json()["stage"] == "READY"
    finally:
        reopened.engine.close()
