from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.opsboard.application.network_listings import (
    NetworkListingConflict,
    NetworkListingService,
)

HEADERS = {
    "x-subject-id": "operator-expansion-manager",
    "x-roles": "expansion_user",
    "x-operator-role": "expansion-manager",
    "x-tenant-id": "tenant-a",
}


def _write_headers(key: str) -> dict[str, str]:
    return {
        **HEADERS,
        "X-Correlation-Id": f"corr-{key}",
        "Idempotency-Key": f"idem-{key}",
    }


def test_first_submission_contract_test() -> None:
    app = create_app()
    client = TestClient(app)

    # Submit a new valid synthetic URL
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


def test_exact_duplicate_contract_test() -> None:
    app = create_app()
    client = TestClient(app)

    # 1. First submission -> READY
    url = "https://www.synthetic.example/detail-77120345.html"
    resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url, "heatZoneId": "HZ-01"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    id1 = resp.json()["id"]

    # 2. Second submission -> exact duplicate returned (terminal state idempotency)
    resp2 = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url, "heatZoneId": "HZ-01"},
        headers=HEADERS,
    )
    assert resp2.status_code == 200
    assert resp2.json()["id"] == id1

    # 3. Test URL concurrency check (raising conflict if stage is not terminal)
    service = NetworkListingService()
    service.submit_intake(
        url=url,
        heat_zone_id="HZ-01",
        actor_role_id="expansionManager",
        actor_name="林曉青",
        idempotency_key="idem-1",
        correlation_id="corr-1",
    )
    # Manually overwrite stage to RETRIEVING (non-terminal)
    intake = service._state["assistedIntakes"][0]
    intake["stage"] = "RETRIEVING"

    # Submitting same URL again should raise NetworkListingConflict
    with pytest.raises(NetworkListingConflict):
        service.submit_intake(
            url=url,
            heat_zone_id="HZ-01",
            actor_role_id="expansionManager",
            actor_name="林曉青",
            idempotency_key="idem-2",
            correlation_id="corr-2",
        )


def test_changed_price_revision_contract_test() -> None:
    app = create_app()
    client = TestClient(app)

    # Submit a revision URL (L-2024 duplicate but rent is 55000 instead of 58000)
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

    # Perform decision: action="revise"
    decide_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{data['id']}/decide",
        json={
            "action": "revise",
            "reason": "降價更新至 55000",
            "riskSummary": "將以送件版本覆寫既有物件 L-2024 的租金與樓層。",
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
        headers=_write_headers("changed-price-revise"),
    )
    assert decide_resp.status_code == 200
    assert decide_resp.json()["stage"] == "READY"

    # Verify that target listing rent is updated in listings snapshot
    snap_resp = client.get("/api/v1/operator/network-listings", headers=HEADERS)
    listings = snap_resp.json()["listings"]
    l2024 = next(item for item in listings if item["id"] == "L-2024")
    assert l2024["rentPerMonth"] == 55000


def test_ambiguous_entity_match_review_test() -> None:
    app = create_app()
    client = TestClient(app)

    # Possible match - same normalized address as L-2025 but different floor/rent
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

    # Try correct without a reason (identity fields require a reason)
    bad_correct = client.post(
        f"/api/v1/operator/network-listings/intake/{data['id']}/correct",
        json={
            "fields": {"address": "新北市板橋區府中路 99 號 1F"},
            "reason": " ",
            "riskSummary": "修改地址會改變比對結果，可能指向不同物件。",
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
        headers=_write_headers("ambiguous-bad-correct"),
    )
    assert bad_correct.status_code == 422

    # Perform correct with a valid reason
    correct_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{data['id']}/correct",
        json={
            "fields": {"address": "新北市板橋區府中路 99 號 1F"},
            "reason": "勘誤地址以避開衝突",
            "riskSummary": "修改地址會改變比對結果，可能指向不同物件。",
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
        headers=_write_headers("ambiguous-good-correct"),
    )
    assert correct_resp.status_code == 200
    corrected_data = correct_resp.json()
    assert corrected_data["stage"] == "READY"
    assert corrected_data["matchResult"]["outcome"] == "NEW"


def test_malformed_payload_contract_test() -> None:
    app = create_app()
    client = TestClient(app)

    # Malformed payload (empty address raw)
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

    # User manual correction should survive retry
    correct_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{data['id']}/correct",
        json={
            "fields": {"rent": 48000, "address": "新莊興德路店面"},
            "reason": "手動補錄超時物件",
            "riskSummary": "手動補錄的欄位不具來源證據。",
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
        headers=_write_headers("timeout-manual-correct"),
    )
    assert correct_resp.status_code == 200

    # Retry - fails again due to timeout fixture, but checks that manual rent correction survives
    retry_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{data['id']}/retry",
        json={"actorRoleId": "expansionManager"},
        headers=HEADERS,
    )
    assert retry_resp.status_code == 200
    retried_data = retry_resp.json()
    assert retried_data["parsedFields"]["rent"]["correctedValue"] == 48000


def test_fixture_compatible_replay() -> None:
    # Verify that the entire retrieval corpus remains queryable and matches exact schemas
    from modules.external_data.application.assisted_intake import RETRIEVAL_CORPUS
    for result in RETRIEVAL_CORPUS.values():
        assert result.snapshot_id is not None
        if result.ok:
            assert isinstance(result.raw, dict)
            assert result.failure is None
        else:
            assert result.failure is not None
            assert result.failure.code.startswith("ODP-INTAKE-")


def test_role_based_server_checks() -> None:
    app = create_app()
    client = TestClient(app)

    url = "https://www.synthetic.example/detail-77120345.html"
    resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url, "heatZoneId": "HZ-01"},
        headers=HEADERS,
    )
    intake_id = resp.json()["id"]

    # Try correct with an unauthorized role (e.g. platform_admin or franchisee)
    bad_correct = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/correct",
        json={
            "fields": {"address": "新北市板橋區府中路 99 號 1F"},
            "reason": "手動修改",
            "actorRoleId": "franchisee",
        },
        headers=HEADERS,
    )
    assert bad_correct.status_code == 422


def test_promote_intake_contract_test() -> None:
    app = create_app()
    client = TestClient(app)

    # 1. Submit a revision URL to resolve it to L-2024
    url = "https://www.synthetic.example/detail-88520242.html"
    resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url, "heatZoneId": "HZ-01"},
        headers={**HEADERS, "x-subject-id": "operator-expansion-staff", "x-operator-role": "expansion-staff"},
    )
    data = resp.json()
    intake_id = data["id"]

    # Try promote without reason -> expect 422
    bad_promote = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/promote",
        json={"actorRoleId": "expansionManager", "reason": ""},
        headers=_write_headers("bad-promote-no-reason"),
    )
    assert bad_promote.status_code == 422

    # Promote with valid reason
    promote_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/promote",
        json={
            "actorRoleId": "expansionManager",
            "reason": "核准物件轉換為候選店",
            "riskSummary": "轉換為候選店會建立 SiteScore 待審紀錄。",
            "riskAcknowledged": True,
        },
        headers=_write_headers("good-promote"),
    )
    assert promote_resp.status_code == 200
    res_data = promote_resp.json()
    assert res_data["candidate"]["id"] == "CS-1001"
    assert res_data["listing"]["status"] == "candidate"


# --- Risk disclosure contract (ODP-OC-R5-011 review finding P0-2) ---
#
# High-impact writes must carry a caller-supplied risk summary AND an explicit
# acknowledgement. The server must not invent the summary: an audit record is
# only evidence of consent if it stores the text the operator actually saw.


def _ready_intake_id(client) -> str:
    resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": "https://www.synthetic.example/detail-88520242.html", "heatZoneId": "HZ-01"},
        headers={**HEADERS, "x-subject-id": "operator-expansion-staff", "x-operator-role": "expansion-staff"},
    )
    assert resp.status_code == 200
    return resp.json()["id"]


CORRECT_FIELDS = {"fields": {"address": "新北市板橋區府中路 99 號 1F"}, "reason": "勘誤地址"}
DECIDE_BODY = {"action": "revise", "reason": "降價更新"}
PROMOTE_BODY = {"reason": "核准物件轉換為候選店"}


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("correct", CORRECT_FIELDS),
        ("decide", DECIDE_BODY),
        ("promote", PROMOTE_BODY),
    ],
)
def test_high_impact_write_rejects_missing_risk_summary(path, body) -> None:
    app = create_app()
    client = TestClient(app)
    intake_id = _ready_intake_id(client)

    resp = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/{path}",
        json={**body, "riskAcknowledged": True, "actorRoleId": "expansionManager"},
        headers=_write_headers(f"missing-risk-{path}"),
    )
    assert resp.status_code == 422
    assert "risk summary is required" in resp.json()["detail"]


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("correct", CORRECT_FIELDS),
        ("decide", DECIDE_BODY),
        ("promote", PROMOTE_BODY),
    ],
)
def test_high_impact_write_rejects_unacknowledged_risk(path, body) -> None:
    app = create_app()
    client = TestClient(app)
    intake_id = _ready_intake_id(client)

    # Summary supplied, but the operator never accepted it.
    resp = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/{path}",
        json={
            **body,
            "riskSummary": "此變更會覆寫既有物件。",
            "riskAcknowledged": False,
            "actorRoleId": "expansionManager",
        },
        headers=_write_headers(f"unack-risk-{path}"),
    )
    assert resp.status_code == 422
    assert "risk acknowledgement is required" in resp.json()["detail"]


def test_merge_rejects_missing_risk_disclosure() -> None:
    app = create_app()
    client = TestClient(app)

    resp = client.post(
        "/api/v1/operator/network-listings/listings/L-2029/merge",
        json={"targetListingId": "L-2025", "reason": "重複來源", "actorRoleId": "expansionManager"},
        headers=_write_headers("merge-missing-risk"),
    )
    assert resp.status_code == 422
    assert "risk summary is required" in resp.json()["detail"]


def test_correct_persists_caller_risk_summary_in_audit() -> None:
    app = create_app()
    client = TestClient(app)
    intake_id = _ready_intake_id(client)

    caller_summary = "修改地址會改變比對結果，可能指向不同物件。"
    resp = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/correct",
        json={
            **CORRECT_FIELDS,
            "riskSummary": caller_summary,
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
        headers=_write_headers("correct-risk-audit"),
    )
    assert resp.status_code == 200

    audit = resp.json()["auditEvents"][-1]
    assert audit["action"] == "intake.correct"
    # The stored summary is the caller's text verbatim, not a server-built one.
    assert audit["metadata"]["riskSummary"] == caller_summary
    assert audit["metadata"]["riskAcknowledged"] is True


def test_decide_persists_caller_risk_summary_alongside_server_effect() -> None:
    app = create_app()
    client = TestClient(app)
    intake_id = _ready_intake_id(client)

    caller_summary = "將以送件版本覆寫既有物件 L-2024 的租金。"
    resp = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/decide",
        json={
            **DECIDE_BODY,
            "riskSummary": caller_summary,
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
        headers=_write_headers("decide-risk-audit"),
    )
    assert resp.status_code == 200

    audit = resp.json()["auditEvents"][-1]
    assert audit["metadata"]["riskSummary"] == caller_summary
    assert audit["metadata"]["riskAcknowledged"] is True
    # The server-derived description of what happened is kept, but under a
    # separate key so it can never be mistaken for acknowledged text.
    assert "L-2024" in audit["metadata"]["effectSummary"]


def test_promote_persists_caller_risk_summary_in_audit() -> None:
    app = create_app()
    client = TestClient(app)
    intake_id = _ready_intake_id(client)

    caller_summary = "轉換為候選店會建立 SiteScore 待審紀錄。"
    resp = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/promote",
        json={
            **PROMOTE_BODY,
            "riskSummary": caller_summary,
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
        headers=_write_headers("promote-risk-audit"),
    )
    assert resp.status_code == 200

    detail = client.get(
        f"/api/v1/operator/network-listings/intake/{intake_id}", headers=HEADERS
    )
    audit = detail.json()["auditEvents"][-1]
    assert audit["action"] == "intake.promote"
    assert audit["metadata"]["riskSummary"] == caller_summary
    assert audit["metadata"]["riskAcknowledged"] is True
