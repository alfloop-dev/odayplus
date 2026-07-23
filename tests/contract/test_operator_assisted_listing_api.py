from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.app.routes.operator_modules.network_listings import (
    create_network_listings_sub_router,
    is_record_owner,
)
from apps.api.oday_api.main import create_app
from modules.opsboard.application.network_listings import (
    InMemoryAssistedIntakeRepository,
    NetworkListingConflict,
    NetworkListingService,
)
from shared.auth import DataClassification, Principal, Role, Scope

HEADERS = {
    "x-subject-id": "operator-expansion-manager",
    "x-roles": "site_reviewer,expansion_user",
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


def test_listing_inbox_server_query_contract() -> None:
    app = create_app()
    client = TestClient(app)

    created_ids: list[str] = []
    for suffix in ("query-a", "query-b"):
        response = client.post(
            "/api/v1/operator/network-listings/intake/submit",
            json={
                "url": f"https://www.synthetic.example/detail-{suffix}.html",
                "heatZoneId": "HZ-01",
            },
            headers=_write_headers(suffix),
        )
        assert response.status_code == 200
        assert response.json()["intakeMethod"] == "URL"
        created_ids.append(response.json()["id"])

    page = client.get(
        "/api/v1/operator/network-listings/intake",
        params={
            "page": 1,
            "pageSize": 1,
            "intakeMethod": "URL",
            "sortBy": "id",
            "sortOrder": "asc",
        },
        headers=HEADERS,
    )
    assert page.status_code == 200
    payload = page.json()
    assert payload["page"] == 1
    assert payload["pageSize"] == 1
    assert payload["total"] == 2
    assert len(payload["items"]) == 1
    assert payload["items"][0]["id"] == min(created_ids)
    assert sum(payload["counts"].values()) == 2
    assert payload["evidenceState"] in {"complete", "partial", "degraded"}

    no_match = client.get(
        "/api/v1/operator/network-listings/intake",
        params={"intakeMethod": "APPROVED_FEED"},
        headers=HEADERS,
    )
    assert no_match.status_code == 200
    assert no_match.json()["items"] == []
    assert no_match.json()["total"] == 0


def _inbox_contract_client() -> TestClient:
    repository = InMemoryAssistedIntakeRepository()

    def record(
        intake_id: str,
        *,
        stage: str,
        source_id: str,
        intake_method: str,
        owner: str,
        submitter: str,
        heat_zone_id: str,
        area_id: str,
        assignment_status: str,
        sla_state: str,
        captured_at: str,
        updated_at: str,
        match_outcome: str,
        target_listing_id: str | None = None,
        restricted_data: bool = False,
        retryable: bool = False,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> dict[str, object]:
        parsed_fields: dict[str, object] = {}
        if latitude is not None and longitude is not None:
            parsed_fields = {
                "latitude": {
                    "sourceValue": latitude,
                    "normalizedValue": latitude,
                    "correctedValue": None,
                    "confidence": 1,
                },
                "longitude": {
                    "sourceValue": longitude,
                    "normalizedValue": longitude,
                    "correctedValue": None,
                    "confidence": 1,
                },
            }
        return {
            "id": intake_id,
            "tenantId": "tenant-a",
            "scope": {
                "tenant_id": "tenant-a",
                "assigned_area_id": area_id,
                "brand_id": "ODAY",
                "heat_zone_id": heat_zone_id,
                "region_id": "TW-NORTH",
            },
            "originalUrl": f"https://listings.example.com/{intake_id}",
            "canonicalUrl": f"https://listings.example.com/{intake_id}",
            "submitter": submitter,
            "owner": owner,
            "heatZoneId": heat_zone_id,
            "intakeMethod": intake_method,
            "stage": stage,
            "sourceId": source_id,
            "policy": "APPROVED_RETRIEVAL",
            "policyLabel": "Approved source",
            "policyReason": "Source registry policy is active.",
            "rawSnapshot": {"snapshot": intake_id},
            "snapshotId": f"SNAP-{intake_id}",
            "capturedAt": captured_at,
            "parserVersion": "parser-v1",
            "correlationId": f"corr-{intake_id}",
            "parsedFields": parsed_fields,
            "matchResult": {
                "outcome": match_outcome,
                "outcomeLabel": match_outcome,
                "targetListingId": target_listing_id,
                "confidence": 0.9,
                "agreeingSignals": [],
                "contradictingSignals": [],
                "summary": f"{match_outcome} result",
            },
            "auditEvents": [
                {
                    "id": f"AUD-{intake_id}",
                    "occurredAt": updated_at,
                    "actorRoleId": "expansion-manager",
                    "actorName": owner,
                    "action": "intake.updated",
                    "targetId": intake_id,
                    "message": "Updated",
                    "correlationId": f"corr-{intake_id}",
                }
            ],
            "version": 3,
            "assignmentStatus": assignment_status,
            "slaState": sla_state,
            "dueAt": "2026-07-30T00:00:00Z",
            "restrictedData": restricted_data,
            "failure": (
                {
                    "code": "FETCH_TIMEOUT",
                    "summary": "Retrieval timed out.",
                    "nextAction": "Retry retrieval.",
                    "retryable": retryable,
                }
                if stage == "FAILED"
                else None
            ),
        }

    records = [
        record(
            "IN-A-REVIEW",
            stage="NEEDS_REVIEW",
            source_id="source-591",
            intake_method="URL",
            owner="owner-a",
            submitter="submitter-a",
            heat_zone_id="HZ-01",
            area_id="AREA-01",
            assignment_status="ASSIGNED",
            sla_state="DUE_SOON",
            captured_at="2026-07-10T00:00:00Z",
            updated_at="2026-07-11T00:00:00Z",
            match_outcome="POSSIBLE_MATCH",
            target_listing_id="LISTING-900",
            restricted_data=True,
            latitude=25.033,
            longitude=121.5654,
        ),
        record(
            "IN-B-ENTRY",
            stage="AWAITING_ASSISTED_ENTRY",
            source_id="source-manual",
            intake_method="MANUAL",
            owner="owner-b",
            submitter="submitter-b",
            heat_zone_id="HZ-02",
            area_id="AREA-02",
            assignment_status="UNASSIGNED",
            sla_state="ON_TRACK",
            captured_at="2026-06-01T00:00:00Z",
            updated_at="2026-06-02T00:00:00Z",
            match_outcome="NEW",
        ),
        record(
            "IN-C-FAILED",
            stage="FAILED",
            source_id="source-retry",
            intake_method="CSV",
            owner="owner-c",
            submitter="submitter-c",
            heat_zone_id="HZ-03",
            area_id="AREA-03",
            assignment_status="ESCALATED",
            sla_state="BREACHED",
            captured_at="2026-05-01T00:00:00Z",
            updated_at="2026-05-02T00:00:00Z",
            match_outcome="QUARANTINED",
            retryable=True,
        ),
        record(
            "IN-D-QUARANTINED",
            stage="QUARANTINED",
            source_id="source-blocked",
            intake_method="APPROVED_FEED",
            owner="owner-d",
            submitter="submitter-d",
            heat_zone_id="HZ-04",
            area_id="AREA-04",
            assignment_status="TRANSFERRED",
            sla_state="PAUSED",
            captured_at="2026-04-01T00:00:00Z",
            updated_at="2026-04-02T00:00:00Z",
            match_outcome="QUARANTINED",
        ),
        record(
            "IN-E-READY",
            stage="READY",
            source_id="source-feed",
            intake_method="APPROVED_FEED",
            owner="owner-e",
            submitter="submitter-e",
            heat_zone_id="HZ-05",
            area_id="AREA-05",
            assignment_status="COMPLETED",
            sla_state="COMPLETED",
            captured_at="2026-03-01T00:00:00Z",
            updated_at="2026-03-02T00:00:00Z",
            match_outcome="NEW",
        ),
        record(
            "IN-F-PROCESSING",
            stage="PARSING",
            source_id="source-processing",
            intake_method="URL",
            owner="owner-f",
            submitter="submitter-f",
            heat_zone_id="HZ-06",
            area_id="AREA-06",
            assignment_status="CLAIMED",
            sla_state="ON_TRACK",
            captured_at="2026-07-01T00:00:00Z",
            updated_at="2026-07-02T00:00:00Z",
            match_outcome="NEW",
        ),
    ]
    for item in records:
        repository.save_intake(item)

    service = NetworkListingService(intake_repository=repository)
    app = FastAPI()

    @app.middleware("http")
    async def inject_principal(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.operator_principal = Principal(
            subject_id="operator-expansion-manager",
            roles=frozenset({Role.SITE_REVIEWER, Role.EXPANSION_USER}),
            scope=Scope(
                tenant_id="tenant-a",
                clearance=DataClassification.RESTRICTED,
            ),
        )
        request.state.operator_role_id = "expansion-manager"
        return await call_next(request)

    app.include_router(
        create_network_listings_sub_router(
            service,
            require_view_permission_fn=lambda: None,
            require_write_permission_fn=lambda: None,
        ),
        prefix="/api/v1/operator",
    )
    return TestClient(app)


@pytest.mark.parametrize(
    ("params", "expected_id"),
    [
        ({"savedView": "needsReview"}, "IN-A-REVIEW"),
        ({"savedView": "awaitingEntry"}, "IN-B-ENTRY"),
        ({"savedView": "blocked", "failed": "true"}, "IN-C-FAILED"),
        ({"savedView": "processing"}, "IN-F-PROCESSING"),
        ({"savedView": "ready"}, "IN-E-READY"),
        ({"search": "LISTING-900"}, "IN-A-REVIEW"),
        ({"intakeMethod": "MANUAL"}, "IN-B-ENTRY"),
        ({"intakeStage": "NEEDS_REVIEW"}, "IN-A-REVIEW"),
        ({"matchOutcome": "POSSIBLE_MATCH"}, "IN-A-REVIEW"),
        ({"sourceId": "source-591"}, "IN-A-REVIEW"),
        ({"submittedBy": "submitter-a"}, "IN-A-REVIEW"),
        ({"owner": "owner-a"}, "IN-A-REVIEW"),
        ({"assignmentStatus": "ASSIGNED"}, "IN-A-REVIEW"),
        ({"needsReview": "true"}, "IN-A-REVIEW"),
        ({"slaState": "DUE_SOON"}, "IN-A-REVIEW"),
        ({"heatZoneId": "HZ-01"}, "IN-A-REVIEW"),
        ({"selectedHeatZoneId": "HZ-01"}, "IN-A-REVIEW"),
        ({"areaId": "AREA-01"}, "IN-A-REVIEW"),
        ({"observedFrom": "2026-07-09T00:00:00Z"}, "IN-A-REVIEW"),
        ({"observedTo": "2026-03-31T23:59:59Z"}, "IN-E-READY"),
        ({"updatedFrom": "2026-07-10T00:00:00Z"}, "IN-A-REVIEW"),
        ({"updatedTo": "2026-03-31T23:59:59Z"}, "IN-E-READY"),
        ({"restrictedData": "true"}, "IN-A-REVIEW"),
        ({"quarantined": "true"}, "IN-D-QUARANTINED"),
        ({"failed": "true"}, "IN-C-FAILED"),
        ({"retryable": "true"}, "IN-C-FAILED"),
    ],
)
def test_listing_inbox_applies_every_server_filter(
    params: dict[str, str],
    expected_id: str,
) -> None:
    client = _inbox_contract_client()
    response = client.get(
        "/api/v1/operator/network-listings/intake",
        params=params,
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 1
    assert [item["id"] for item in payload["items"]] == [expected_id]
    assert payload["items"][0]["lastObservedAt"]
    assert payload["items"][0]["lastUpdatedAt"]
    assert "needsReview" in payload["items"][0]
    assert "retryable" in payload["items"][0]


def test_listing_inbox_cursor_stable_sort_and_authoritative_location_contract() -> None:
    client = _inbox_contract_client()
    first = client.get(
        "/api/v1/operator/network-listings/intake",
        params={
            "pageSize": 2,
            "sortBy": "id",
            "sortOrder": "asc",
        },
    )

    assert first.status_code == 200, first.text
    first_page = first.json()
    assert [item["id"] for item in first_page["items"]] == [
        "IN-A-REVIEW",
        "IN-B-ENTRY",
    ]
    assert first_page["nextCursor"]
    assert first_page["previousCursor"] is None
    assert first_page["sortBy"] == "id"
    assert first_page["sortOrder"] == "asc"
    assert first_page["queryFingerprint"]
    assert first_page["items"][0]["location"] == {
        "latitude": 25.033,
        "longitude": 121.5654,
        "confidence": None,
        "source": "parsed-field-or-source-snapshot",
    }
    assert first_page["items"][1]["location"] is None

    second = client.get(
        "/api/v1/operator/network-listings/intake",
        params={
            "pageSize": 2,
            "sortBy": "id",
            "sortOrder": "asc",
            "cursor": first_page["nextCursor"],
        },
    )
    assert second.status_code == 200, second.text
    second_page = second.json()
    assert [item["id"] for item in second_page["items"]] == [
        "IN-C-FAILED",
        "IN-D-QUARANTINED",
    ]
    assert second_page["previousCursor"]

    mismatched_query = client.get(
        "/api/v1/operator/network-listings/intake",
        params={
            "pageSize": 2,
            "sortBy": "id",
            "sortOrder": "desc",
            "cursor": first_page["nextCursor"],
        },
    )
    assert mismatched_query.status_code == 400
    assert "CURSOR_INVALID" in mismatched_query.text

    malformed_cursor = client.get(
        "/api/v1/operator/network-listings/intake",
        params={"cursor": "***not-base64***"},
    )
    assert malformed_cursor.status_code == 400
    assert "CURSOR_INVALID" in malformed_cursor.text


@pytest.mark.parametrize(
    "params",
    [
        {"savedView": "not-a-view"},
        {"sortBy": "not-a-column"},
        {"observedFrom": "not-a-time"},
        {
            "updatedFrom": "2026-07-31T00:00:00Z",
            "updatedTo": "2026-07-01T00:00:00Z",
        },
    ],
)
def test_listing_inbox_rejects_invalid_query_contract(
    params: dict[str, str],
) -> None:
    response = _inbox_contract_client().get(
        "/api/v1/operator/network-listings/intake",
        params=params,
    )

    assert response.status_code == 400
    assert "VALIDATION_FAILED" in response.text


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


def test_created_listing_keeps_address_and_heat_zone_for_v1_promotion_gate() -> None:
    app = create_app()
    client = TestClient(app)
    submitted = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={
            "url": "https://www.synthetic.example/detail-99310418.html",
            "heatZoneId": "HZ-02",
        },
        headers=_write_headers("promotion-gate-submit"),
    )
    assert submitted.status_code == 200

    intake = submitted.json()
    decided = client.post(
        f"/api/v1/operator/network-listings/intake/{intake['id']}/decide",
        json={
            "action": "create",
            "reason": "來源與樓層證據顯示為獨立物件",
            "riskSummary": "建立新物件後將可另行提出 Candidate 晉升。",
            "riskAcknowledged": True,
            "actorRoleId": "expansion-manager",
        },
        headers=_write_headers("promotion-gate-decide"),
    )
    assert decided.status_code == 200
    listing_id = decided.json()["matchResult"]["targetListingId"]

    repository = app.state.listing_repository
    listing = repository.get_listing(listing_id)
    assert listing is not None
    assert listing.address_id == f"ADDR-{listing_id}"
    address = next(item for item in repository.addresses if item.address_id == listing.address_id)
    assert address.normalized_address
    assert address.h3_res_9 == "HZ-02"

    promotion = client.post(
        f"/api/v1/intakes/{intake['id']}/promotion-requests",
        json={
            "target_format_code": "FMT-STANDARD-STORE",
            "reason": "商圈缺口與物件資料均已覆核，提出 Candidate 晉升申請。",
            "gate_snapshot_sha256": "a" * 64,
            "risk_acknowledged": True,
        },
        headers={
            **HEADERS,
            "X-Correlation-Id": "corr-promotion-gate-request",
            "Idempotency-Key": "promotion-gate-request-001",
            "If-Match": f'W/"{decided.json()["version"]}"',
        },
    )
    assert promotion.status_code == 202
    assert promotion.json()["status"] == "PENDING_REVIEW"


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
        headers={
            **HEADERS,
            "x-subject-id": "operator-expansion-staff",
            "x-roles": "expansion_user",
            "x-operator-role": "expansion-staff",
        },
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
    assert res_data["status"] == "PENDING_REVIEW"
    assert "candidate" not in res_data

    review_resp = client.post(
        f"/api/v1/promotion-decisions/{res_data['promotion_decision_id']}/actions/review",
        json={
            "decision": "APPROVE",
            "reason": "Independent review approved",
            "risk_acknowledged": True,
        },
        headers={
            **_write_headers("good-promote-review"),
            "x-subject-id": "00000000-0000-0000-0000-000000000102",
            "If-Match": f'W/"{res_data["version"]}"',
        },
    )
    assert review_resp.status_code == 200, review_resp.text
    assert review_resp.json()["status"] == "COMPLETED"
    assert review_resp.json()["candidate_site_id"]


# --- Risk disclosure contract (ODP-OC-R5-011 review finding P0-2) ---
#
# High-impact writes must carry a caller-supplied risk summary AND an explicit
# acknowledgement. The server must not invent the summary: an audit record is
# only evidence of consent if it stores the text the operator actually saw.


def _ready_intake_id(client) -> str:
    resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": "https://www.synthetic.example/detail-88520242.html", "heatZoneId": "HZ-01"},
        headers={
            **HEADERS,
            "x-subject-id": "operator-expansion-staff",
            "x-roles": "expansion_user",
            "x-operator-role": "expansion-staff",
        },
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

    detail = client.get(f"/api/v1/operator/network-listings/intake/{intake_id}", headers=HEADERS)
    audit = detail.json()["auditEvents"][-1]
    assert audit["action"] == "intake.promote_request"
    assert audit["metadata"]["riskSummary"] == caller_summary
    assert audit["metadata"]["riskAcknowledged"] is True


def test_unassigned_operator_intake_is_not_owned_by_unrelated_staff() -> None:
    principal = Principal(subject_id="staff-a")

    assert not is_record_owner(
        principal,
        {"owner": "unassigned", "submitter": "staff-b"},
    )
    assert is_record_owner(
        principal,
        {"owner": None, "submitter": "staff-a"},
    )
