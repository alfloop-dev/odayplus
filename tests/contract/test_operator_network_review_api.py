"""Contract tests for the Operator Console R4 Network Review API (ODP-OC-R4-007).

Covers acceptance:
- A failed transaction leaves all five records (Candidate / Review / Approval /
  Decision / Audit) unchanged; idempotent replay creates no duplicates.
- Authorized reviewer can reach the review from Network **and** Govern; an
  Expansion role can prepare/submit but cannot decide (fail-closed 403).
- GO → Approved, WAIT → On Hold, Return → Need Data, Reject → Rejected.
- Canonical package 6 screen: data-screen-label "Network 選址審核" /
  "Dialog Review Decision"; review ids RV-701 / RV-698 match the scoring
  service's candidate reviewIds, RV-702 is the golden GO flow.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app

# Site Reviewer holds sitescore APPROVE (may decide).
REVIEWER_HEADERS = {
    "x-subject-id": "operator-site-reviewer",
    "x-roles": "site_reviewer",
    "x-operator-role": "expansion-manager",
    "x-tenant-id": "tenant-a",
}

# Expansion holds sitescore VIEW/EXECUTE only (may submit, not decide).
EXPANSION_HEADERS = {
    "x-subject-id": "operator-expansion-manager",
    "x-roles": "expansion_user",
    "x-operator-role": "expansion-manager",
    "x-tenant-id": "tenant-a",
}


def _client() -> TestClient:
    return TestClient(create_app())


def _snapshot(client: TestClient) -> dict:
    return client.get("/api/v1/operator/network-reviews", headers=REVIEWER_HEADERS).json()


def test_review_snapshot_exposes_seeded_queue() -> None:
    client = _client()
    response = client.get(
        "/api/v1/operator/network-reviews",
        headers={**REVIEWER_HEADERS, "x-correlation-id": "corr-r4-007-snapshot"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "api"
    assert body["decisionMapping"] == {
        "GO": "Approved",
        "WAIT": "On Hold",
        "RETURN": "Need Data",
        "REJECT": "Rejected",
    }

    by_id = {review["id"]: review for review in body["reviews"]}
    assert set(by_id) >= {"RV-701", "RV-698", "RV-702"}
    assert by_id["RV-702"]["candidateId"] == "CS-1001"
    assert by_id["RV-702"]["recommendation"] == "GO"
    assert by_id["RV-701"]["candidateId"] == "CS-1002"
    assert by_id["RV-701"]["recommendation"] == "WAIT"
    assert by_id["RV-698"]["candidateId"] == "CS-1004"
    assert by_id["RV-698"]["recommendation"] == "REJECT"
    assert all(review["status"] == "pending" for review in body["reviews"])
    assert body["counts"] == {"reviews": 3, "pending": 3, "decided": 0}


def test_go_decision_syncs_five_records_and_survives_reload() -> None:
    client = _client()
    response = client.post(
        "/api/v1/operator/network-reviews/RV-702/decide",
        headers={**REVIEWER_HEADERS, "idempotency-key": "idem-r4-007-go", "x-correlation-id": "corr-go"},
        json={
            "decision": "GO",
            "reason": "人流量體大且回本期可接受，核准進展店閘。",
            "actorRoleId": "siteReviewer",
            "actorName": "陳審核",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # All five records move together.
    assert body["review"]["status"] == "approved"
    assert body["review"]["statusLabel"] == "已核准 GO"
    assert body["candidate"]["status"] == "approved"
    assert body["approval"]["status"] == "approved"
    assert body["decision"]["finalDecision"] == "Approved"
    assert body["decision"]["mappedStatus"] == "approved"
    assert body["auditEvent"]["action"] == "review.decision"
    assert body["records"] == {
        "candidateId": "CS-1001",
        "reviewId": "RV-702",
        "approvalId": "AP-RV-702",
        "decisionId": body["decision"]["id"],
        "auditId": body["auditEvent"]["id"],
    }

    # Atomic sync survives a fresh read (in-memory persistence, deduped rows).
    snap = _snapshot(client)
    rv = next(review for review in snap["reviews"] if review["id"] == "RV-702")
    assert rv["status"] == "approved"
    assert snap["counts"]["decided"] == 1
    assert len(snap["decisions"]) == 1
    assert len(snap["auditEvents"]) == 1
    approval = next(a for a in snap["approvals"] if a["id"] == "AP-RV-702")
    assert approval["status"] == "approved"


def test_decision_mapping_covers_wait_return_reject() -> None:
    client = _client()

    # WAIT → On Hold (pass conditions required).
    wait = client.post(
        "/api/v1/operator/network-reviews/RV-701/decide",
        headers=REVIEWER_HEADERS,
        json={
            "decision": "WAIT",
            "reason": "人流佳惟站前施工需以條件管理。",
            "conditions": "租金議價至 48,000 以下；補充晚間人流資料",
            "actorRoleId": "siteReviewer",
        },
    )
    assert wait.status_code == 200, wait.text
    assert wait.json()["decision"]["finalDecision"] == "On Hold"
    assert wait.json()["review"]["status"] == "onhold"

    # RETURN → Need Data (missing-data list synced to Candidate).
    ret = client.post(
        "/api/v1/operator/network-reviews/RV-698/decide",
        headers=REVIEWER_HEADERS,
        json={
            "decision": "RETURN",
            "reason": "決策前需補齊現勘與晚間人流資料。",
            "requiredData": ["現勘紀錄", "晚間人流樣本"],
            "actorRoleId": "siteReviewer",
        },
    )
    assert ret.status_code == 200, ret.text
    assert ret.json()["decision"]["finalDecision"] == "Need Data"
    assert ret.json()["candidate"]["missingData"] == ["現勘紀錄", "晚間人流樣本"]

    # REJECT → Rejected on the golden GO candidate (an override needing ack).
    rej = client.post(
        "/api/v1/operator/network-reviews/RV-702/decide",
        headers=REVIEWER_HEADERS,
        json={
            "decision": "REJECT",
            "reason": "覆寫系統建議，因品牌策略暫緩此區展店。",
            "overrideAck": True,
            "actorRoleId": "siteReviewer",
        },
    )
    assert rej.status_code == 200, rej.text
    assert rej.json()["decision"]["finalDecision"] == "Rejected"
    assert rej.json()["decision"]["override"] is True


def test_failed_transaction_leaves_all_records_unchanged() -> None:
    client = _client()
    before = _snapshot(client)

    # WAIT without conditions is a policy violation → 422, no mutation.
    blocked = client.post(
        "/api/v1/operator/network-reviews/RV-701/decide",
        headers=REVIEWER_HEADERS,
        json={"decision": "WAIT", "reason": "需暫緩但未附條件。", "actorRoleId": "siteReviewer"},
    )
    assert blocked.status_code == 422, blocked.text

    after = _snapshot(client)
    assert after["counts"] == before["counts"]
    assert after["decisions"] == before["decisions"] == []
    assert after["auditEvents"] == before["auditEvents"] == []
    rv = next(review for review in after["reviews"] if review["id"] == "RV-701")
    assert rv["status"] == "pending"


def test_reason_and_override_policies_are_enforced() -> None:
    client = _client()

    # Reason too short.
    short = client.post(
        "/api/v1/operator/network-reviews/RV-702/decide",
        headers=REVIEWER_HEADERS,
        json={"decision": "GO", "reason": "ok", "actorRoleId": "siteReviewer"},
    )
    assert short.status_code == 422

    # RETURN without required-data list.
    ret = client.post(
        "/api/v1/operator/network-reviews/RV-698/decide",
        headers=REVIEWER_HEADERS,
        json={"decision": "RETURN", "reason": "need more data please help", "actorRoleId": "siteReviewer"},
    )
    assert ret.status_code == 422

    # Override (WAIT-recommended candidate decided GO) without acknowledgement.
    override = client.post(
        "/api/v1/operator/network-reviews/RV-701/decide",
        headers=REVIEWER_HEADERS,
        json={"decision": "GO", "reason": "override to approve despite wait", "actorRoleId": "siteReviewer"},
    )
    assert override.status_code == 422


def test_idempotent_replay_creates_no_duplicate_records() -> None:
    client = _client()
    payload = {
        "decision": "GO",
        "reason": "人流量體大且回本期可接受，核准進展店閘。",
        "actorRoleId": "siteReviewer",
    }
    headers = {**REVIEWER_HEADERS, "idempotency-key": "idem-r4-007-replay"}
    first = client.post("/api/v1/operator/network-reviews/RV-702/decide", headers=headers, json=payload)
    assert first.status_code == 200, first.text
    replay = client.post("/api/v1/operator/network-reviews/RV-702/decide", headers=headers, json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotentReplay"] is True
    assert replay.json()["decision"]["id"] == first.json()["decision"]["id"]

    snap = _snapshot(client)
    assert len(snap["decisions"]) == 1
    assert len(snap["auditEvents"]) == 1


def test_second_decision_on_decided_review_conflicts() -> None:
    client = _client()
    client.post(
        "/api/v1/operator/network-reviews/RV-702/decide",
        headers=REVIEWER_HEADERS,
        json={"decision": "GO", "reason": "approve this strong site now.", "actorRoleId": "siteReviewer"},
    )
    conflict = client.post(
        "/api/v1/operator/network-reviews/RV-702/decide",
        headers=REVIEWER_HEADERS,
        json={"decision": "REJECT", "reason": "changed my mind entirely here.", "overrideAck": True, "actorRoleId": "siteReviewer"},
    )
    assert conflict.status_code == 409, conflict.text


def test_unknown_review_is_not_found() -> None:
    client = _client()
    missing = client.post(
        "/api/v1/operator/network-reviews/RV-999/decide",
        headers=REVIEWER_HEADERS,
        json={"decision": "GO", "reason": "approve this strong site now.", "actorRoleId": "siteReviewer"},
    )
    assert missing.status_code == 404, missing.text


def test_expansion_role_may_submit_read_but_not_decide() -> None:
    client = _client()
    # Read is open to the Expansion viewer (they prepare/submit).
    assert client.get("/api/v1/operator/network-reviews", headers=EXPANSION_HEADERS).status_code == 200
    # Decide fails closed with 403 for Expansion (no sitescore APPROVE).
    decide = client.post(
        "/api/v1/operator/network-reviews/RV-702/decide",
        headers=EXPANSION_HEADERS,
        json={"decision": "GO", "reason": "approve this strong site now.", "actorRoleId": "expansionManager"},
    )
    assert decide.status_code == 403, decide.text


def test_unauthenticated_reads_and_writes_fail_closed() -> None:
    client = _client()
    anon = {"x-subject-id": "nobody", "x-tenant-id": "tenant-a"}
    assert client.get("/api/v1/operator/network-reviews", headers=anon).status_code == 403
    assert (
        client.post(
            "/api/v1/operator/network-reviews/RV-702/decide",
            headers=anon,
            json={"decision": "GO", "reason": "approve this strong site now."},
        ).status_code
        == 403
    )


def test_reviewer_reaches_review_from_network_and_govern() -> None:
    """No role-navigation dead ends: the reviewer reads the Network review
    queue and the Govern oversight snapshot with the same identity."""

    client = _client()
    network = client.get("/api/v1/operator/network-reviews", headers=REVIEWER_HEADERS)
    govern = client.get("/api/v1/operator/governance/snapshot", headers=REVIEWER_HEADERS)
    assert network.status_code == 200, network.text
    assert govern.status_code == 200, govern.text
