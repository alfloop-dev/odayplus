from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app

NETWORK_HEADERS = {
    "x-subject-id": "operator-expansion-manager",
    "x-roles": "expansion_user",
    "x-operator-role": "expansion-manager",
    "x-tenant-id": "tenant-a",
}


def test_network_listing_snapshot_exposes_r4_golden_flow_ids() -> None:
    client = TestClient(create_app())

    response = client.get(
        "/api/v1/operator/network-listings?selectedHeatZoneId=HZ-01&lens=demand",
        headers={**NETWORK_HEADERS, "x-correlation-id": "corr-r4-005-snapshot"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "api"
    assert body["selectedHeatZoneId"] == "HZ-01"
    assert {zone["id"] for zone in body["heatZones"]} >= {"HZ-01", "HZ-02"}
    assert {listing["id"] for listing in body["listings"]} >= {
        "L-2024",
        "L-2025",
        "L-2029",
        "L-2030",
    }
    assert "CS-1001" not in {candidate["id"] for candidate in body["candidates"]}
    assert {step["state"] for step in body["expansionSteps"]} >= {
        "completed",
        "current",
        "next",
        "blocked",
    }


def test_convert_l2024_creates_cs1001_once_with_idempotency() -> None:
    client = TestClient(create_app())
    headers = {
        **NETWORK_HEADERS,
        "idempotency-key": "idem-r4-005-convert-l2024",
        "x-correlation-id": "corr-r4-005-convert",
    }
    payload = {"actorRoleId": "expansionManager", "actorName": "王若寧"}

    first = client.post(
        "/api/v1/operator/network-listings/listings/L-2024/convert",
        headers=headers,
        json=payload,
    )
    replay = client.post(
        "/api/v1/operator/network-listings/listings/L-2024/convert",
        headers=headers,
        json=payload,
    )

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    first_body = first.json()
    replay_body = replay.json()
    assert first_body["candidate"]["id"] == "CS-1001"
    assert first_body["candidate"]["listingId"] == "L-2024"
    assert first_body["created"] is True
    assert first_body["candidateCount"] == 1
    assert replay_body["auditEvent"]["id"] == first_body["auditEvent"]["id"]
    assert replay_body["candidateCount"] == 1

    snapshot = client.get("/api/v1/operator/network-listings", headers=NETWORK_HEADERS).json()
    candidates = [candidate for candidate in snapshot["candidates"] if candidate["id"] == "CS-1001"]
    assert len(candidates) == 1
    listing = next(item for item in snapshot["listings"] if item["id"] == "L-2024")
    assert listing["status"] == "candidate"
    assert listing["candidateId"] == "CS-1001"


def test_merge_l2029_into_l2025_retains_source_evidence() -> None:
    client = TestClient(create_app())
    snapshot = client.get("/api/v1/operator/network-listings", headers=NETWORK_HEADERS).json()
    source_before = next(item for item in snapshot["listings"] if item["id"] == "L-2029")
    target_before = next(item for item in snapshot["listings"] if item["id"] == "L-2025")

    response = client.post(
        "/api/v1/operator/network-listings/listings/L-2029/merge",
        headers={
            **NETWORK_HEADERS,
            "idempotency-key": "idem-r4-005-merge-l2029",
            "x-correlation-id": "corr-r4-005-merge",
        },
        json={
            "actorRoleId": "expansionManager",
            "actorName": "王若寧",
            "targetListingId": "L-2025",
            "reason": "Same address and broker evidence verified.",
            "riskSummary": "Merging marks L-2029 a duplicate of L-2025 and moves its source evidence.",
            "riskAcknowledged": True,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"]["id"] == "L-2029"
    assert body["source"]["sourceEvidence"] == source_before["sourceEvidence"]
    assert body["source"]["mergedIntoId"] == "L-2025"
    assert set(source_before["sourceEvidence"]).issubset(set(body["target"]["sourceEvidence"]))
    assert len(body["target"]["sourceEvidence"]) > len(target_before["sourceEvidence"])
    assert body["auditEvent"]["metadata"]["sourceEvidenceRetained"] == len(source_before["sourceEvidence"])
    # The caller-supplied reason and acknowledged disclosure are both auditable.
    assert body["auditEvent"]["metadata"]["reason"] == "Same address and broker evidence verified."
    assert body["auditEvent"]["metadata"]["riskSummary"] == (
        "Merging marks L-2029 a duplicate of L-2025 and moves its source evidence."
    )
    assert body["auditEvent"]["metadata"]["riskAcknowledged"] is True
    assert body["auditEvent"]["correlationId"] == "corr-r4-005-merge"


def _merge_l2029(client: TestClient, *, idempotency_key: str, reason: str):
    return client.post(
        "/api/v1/operator/network-listings/listings/L-2029/merge",
        headers={**NETWORK_HEADERS, "idempotency-key": idempotency_key},
        json={
            "actorRoleId": "expansionManager",
            "actorName": "王若寧",
            "targetListingId": "L-2025",
            "reason": reason,
            "riskSummary": "Merging marks L-2029 a duplicate of L-2025.",
            "riskAcknowledged": True,
        },
    )


def test_merge_is_terminal_and_rejects_a_second_request_for_the_same_source() -> None:
    """A merged source is terminal: a NEW request must not merge it again.

    Regression test for the round-5 finding — a second click minted a fresh
    idempotency key, so the write bypassed the replay cache, appended a second
    listing.merge audit event, and overwrote the first merge's reason.
    """
    client = TestClient(create_app())

    first = _merge_l2029(client, idempotency_key="idem-merge-1", reason="FIRST reason")
    assert first.status_code == 200, first.text

    second = _merge_l2029(client, idempotency_key="idem-merge-2", reason="SECOND reason")
    assert second.status_code == 409, second.text
    assert "already merged into L-2025" in second.json()["detail"]

    snapshot = client.get("/api/v1/operator/network-listings", headers=NETWORK_HEADERS).json()
    merge_events = [event for event in snapshot["auditEvents"] if event["action"] == "listing.merge"]
    assert len(merge_events) == 1
    assert merge_events[0]["metadata"]["reason"] == "FIRST reason"

    source = next(item for item in snapshot["listings"] if item["id"] == "L-2029")
    assert source["mergedIntoId"] == "L-2025"
    # The rejected request left the first merge's reason intact.
    assert source["mergeReason"] == "FIRST reason"


def test_merge_replay_of_the_same_idempotency_key_still_returns_the_cached_result() -> None:
    """Terminal-state rejection must not break idempotent retry of one request."""
    client = TestClient(create_app())

    first = _merge_l2029(client, idempotency_key="idem-merge-replay", reason="Verified duplicate.")
    assert first.status_code == 200, first.text

    replay = _merge_l2029(client, idempotency_key="idem-merge-replay", reason="Verified duplicate.")
    assert replay.status_code == 200, replay.text
    assert replay.json()["source"] == first.json()["source"]

    snapshot = client.get("/api/v1/operator/network-listings", headers=NETWORK_HEADERS).json()
    merge_events = [event for event in snapshot["auditEvents"] if event["action"] == "listing.merge"]
    assert len(merge_events) == 1


def test_archive_l2030_requires_reason_and_retains_hard_rule_evidence() -> None:
    client = TestClient(create_app())

    missing_reason = client.post(
        "/api/v1/operator/network-listings/listings/L-2030/archive",
        headers=NETWORK_HEADERS,
        json={"actorRoleId": "expansionManager", "actorName": "王若寧", "reason": ""},
    )
    assert missing_reason.status_code == 422

    archived = client.post(
        "/api/v1/operator/network-listings/listings/L-2030/archive",
        headers={
            **NETWORK_HEADERS,
            "idempotency-key": "idem-r4-005-archive-l2030",
            "x-correlation-id": "corr-r4-005-archive",
        },
        json={
            "actorRoleId": "expansionManager",
            "actorName": "王若寧",
            "reason": "Area and floor exceed ODAY_G2 hard rules.",
        },
    )

    assert archived.status_code == 200, archived.text
    listing = archived.json()["listing"]
    assert listing["id"] == "L-2030"
    assert listing["status"] == "archived"
    assert listing["archivedReason"] == "Area and floor exceed ODAY_G2 hard rules."
    assert listing["hardRuleFailures"] == ["area_above_format_maximum", "floor_not_ground_level"]
    assert listing["sourceEvidence"] == ["EV-L-2030-RAW-591", "EV-L-2030-HARD-RULES"]
