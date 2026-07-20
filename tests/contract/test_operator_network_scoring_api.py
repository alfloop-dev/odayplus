"""Contract tests for the Operator Console R4 Network SiteScore API.

Covers ODP-OC-R4-006 acceptance: the batch scoring job sorts persisted
results, CS-1001 returns GO 82 (SiteScore v2.3 / FS-20260704-0600), CS-1002
WAIT 76 and CS-1004 REJECT 49 expose R4 conditions/reasons, Compare recommends
primary/alternate/avoid consistently, and missing address/geocode/rent/area/
floor/hard-rule data blocks scoring server-side.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app

SCORING_HEADERS = {
    "x-subject-id": "operator-expansion-manager",
    "x-roles": "site_reviewer",
    "x-operator-role": "expansion-manager",
    "x-tenant-id": "tenant-a",
}


def _client() -> TestClient:
    return TestClient(create_app())


def test_candidate_snapshot_exposes_sitescore_golden_flow() -> None:
    client = _client()
    response = client.get(
        "/api/v1/operator/network-scoring",
        headers={**SCORING_HEADERS, "x-correlation-id": "corr-r4-006-snapshot"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "api"
    assert body["modelVersion"] == "SiteScore v2.3"

    ids = {candidate["id"] for candidate in body["candidates"]}
    assert ids >= {"CS-1001", "CS-1002", "CS-1003", "CS-1004"}

    # Scorecards are persisted score-sorted (GO 82 > WAIT 76 > REJECT 49).
    ordered = [(card["id"], card["score"], card["recommendation"]) for card in body["scorecards"]]
    assert ordered == [
        ("CS-1001", 82, "GO"),
        ("CS-1002", 76, "WAIT"),
        ("CS-1004", 49, "REJECT"),
    ]

    cs1001 = next(card for card in body["scorecards"] if card["id"] == "CS-1001")
    assert cs1001["modelVersion"] == "SiteScore v2.3"
    assert cs1001["datasetSnapshotId"] == "FS-20260704-0600"
    assert cs1001["conditions"] == []  # GO has no gating conditions


def test_candidate_wait_and_reject_expose_conditions_and_reasons() -> None:
    client = _client()
    body = client.get("/api/v1/operator/network-scoring", headers=SCORING_HEADERS).json()
    cards = {card["id"]: card for card in body["scorecards"]}

    wait = cards["CS-1002"]
    assert wait["recommendation"] == "WAIT"
    assert wait["conditionTitle"] == "通過條件 — 符合後可重評為 GO"
    assert any("站前施工" in item for item in wait["conditions"])
    assert wait["revenuePath"]["m12"] == 372
    assert wait["band"]["p50"] == "NT$372K"

    reject = cards["CS-1004"]
    assert reject["recommendation"] == "REJECT"
    assert reject["conditionTitle"] == "拒絕原因"
    assert any("回本期 41 個月" in item for item in reject["conditions"])


def test_sitescore_gate_blocks_missing_data_candidate_server_side() -> None:
    client = _client()
    body = client.get("/api/v1/operator/network-scoring", headers=SCORING_HEADERS).json()
    cs1003 = next(candidate for candidate in body["candidates"] if candidate["id"] == "CS-1003")
    assert cs1003["scored"] is False
    assert cs1003["gate"]["passed"] is False
    assert "Geocode ≥ 0.80" in cs1003["gate"]["missing"]

    blocked = client.post(
        "/api/v1/operator/network-scoring/candidates/CS-1003/score",
        headers=SCORING_HEADERS,
        json={"actorRoleId": "expansionManager"},
    )
    assert blocked.status_code == 422, blocked.text
    detail = blocked.json()["detail"]
    assert "需人工確認地址" in detail["message"]
    assert detail["missing"]


def test_batch_sitescore_job_sorts_and_skips_gated_candidate() -> None:
    client = _client()
    response = client.post(
        "/api/v1/operator/network-scoring/score",
        headers={
            **SCORING_HEADERS,
            "idempotency-key": "idem-r4-006-batch",
            "x-correlation-id": "corr-r4-006-batch",
        },
        json={"actorRoleId": "expansionManager", "actorName": "王若寧"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scoredCandidateIds"] == ["CS-1001", "CS-1002", "CS-1004"]
    assert [item["candidateId"] for item in body["skipped"]] == ["CS-1003"]

    priorities = [(row["priority"], row["id"], row["score"]) for row in body["batchResults"]]
    assert priorities == [
        ("P1", "CS-1001", 82),
        ("P2", "CS-1002", 76),
        ("P3", "CS-1004", 49),
    ]

    # Batch re-runs are idempotent on the same Idempotency-Key.
    replay = client.post(
        "/api/v1/operator/network-scoring/score",
        headers={**SCORING_HEADERS, "idempotency-key": "idem-r4-006-batch"},
        json={"actorRoleId": "expansionManager"},
    )
    assert replay.json()["auditEvent"]["id"] == body["auditEvent"]["id"]


def test_compare_recommends_primary_alternate_avoid_consistently() -> None:
    client = _client()
    body = client.get("/api/v1/operator/network-scoring", headers=SCORING_HEADERS).json()
    recommendation = body["compare"]["recommendation"]
    assert recommendation is not None

    assert recommendation["primary"]["id"] == "CS-1001"
    assert recommendation["primary"]["recommendation"] == "GO"
    assert recommendation["primary"]["score"] == 82

    assert recommendation["alternate"]["id"] == "CS-1002"
    assert recommendation["alternate"]["recommendation"] == "WAIT"

    assert recommendation["avoid"]["id"] == "CS-1004"
    assert recommendation["avoid"]["recommendation"] == "REJECT"

    # SiteScore row for every compared candidate reads "<score> <rec>".
    sitescore_row = next(
        metric for metric in body["compare"]["metrics"] if metric["key"] == "sitescore"
    )
    by_id = {value["id"]: value["text"] for value in sitescore_row["values"]}
    assert by_id["CS-1001"] == "82 GO"
    assert by_id["CS-1004"] == "49 REJECT"


def test_sitescore_scoring_requires_execute_permission() -> None:
    client = _client()
    # Missing roles -> fail-closed 403 on both read and write.
    anon = {"x-subject-id": "nobody", "x-tenant-id": "tenant-a"}
    assert client.get("/api/v1/operator/network-scoring", headers=anon).status_code == 403
    assert (
        client.post(
            "/api/v1/operator/network-scoring/candidates/CS-1001/score",
            headers=anon,
            json={"actorRoleId": "expansionManager"},
        ).status_code
        == 403
    )
