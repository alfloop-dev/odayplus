from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.external_data.geo import GeocodeCandidate, GeoPipeline, StaticGeocodeProvider
from modules.listing import InMemoryListingRepository, ListingPipeline
from tests.integration._authz import LISTING_HEADERS


def _geo_pipeline() -> GeoPipeline:
    return GeoPipeline(
        StaticGeocodeProvider(
            {
                "台北市大安區復興南路二段100號": GeocodeCandidate(
                    latitude=25.026,
                    longitude=121.543,
                    precision="rooftop",
                    confidence=0.92,
                    provider="fixture",
                    admin_city="台北市",
                    admin_district="大安區",
                ),
                "台北市大安區復興南路二段102號": GeocodeCandidate(
                    latitude=25.027,
                    longitude=121.544,
                    precision="rooftop",
                    confidence=0.91,
                    provider="fixture",
                    admin_city="台北市",
                    admin_district="大安區",
                ),
                "台北市大安區復興南路二段200號": GeocodeCandidate(
                    latitude=25.028,
                    longitude=121.545,
                    precision="rooftop",
                    confidence=0.94,
                    provider="fixture",
                    admin_city="台北市",
                    admin_district="大安區",
                ),
            }
        )
    )


def test_listing_pipeline_imports_parses_dedups_and_converts_candidates() -> None:
    repository = InMemoryListingRepository()
    pipeline = ListingPipeline(repository=repository, geo_pipeline=_geo_pipeline())

    result = pipeline.import_records(
        [
            {
                "source_listing_id": "LST-001",
                "address_raw": "台北市大安區復興南路二段100號1樓",
                "rent_amount": 45000.0,
                "currency": "TWD",
                "area_ping": 25.5,
                "floor": "1F",
                "available_from": "2026-07-01",
                "listing_status": "active",
                "confidence": 0.8,
                "snapshot_id": "listing-2026-06-26",
            },
            {
                "source_listing_id": "LST-001",
                "address_raw": "台北市大安區復興南路二段100號1樓",
                "rent_amount": 45000.0,
                "area_ping": 25.5,
                "floor": "1F",
                "listing_status": "active",
                "snapshot_id": "listing-2026-06-26",
            },
            {
                "source_listing_id": "LST-002",
                "address_raw": "台北市大安區復興南路二段102號1樓",
                "rent_amount": 180000.0,
                "area_ping": 20.0,
                "floor": "1F",
                "listing_status": "active",
                "snapshot_id": "listing-2026-06-26",
            },
            {
                "source_listing_id": "LST-003",
                "address_raw": "台北市大安區復興南路二段200號地下1樓",
                "rent_amount": 35000.0,
                "area_ping": 30.0,
                "floor": "B1",
                "listing_status": "active",
                "snapshot_id": "listing-2026-06-26",
            },
            {
                "source_listing_id": "LST-004",
                "rent_amount": 35000.0,
                "listing_status": "active",
                "snapshot_id": "listing-2026-06-26",
            },
        ],
        imported_at=datetime(2026, 6, 28, tzinfo=UTC),
    )

    statuses = [record.status.value for record in result.records]
    assert statuses == ["CANDIDATE", "DUPLICATE", "FAILED_HARD_RULE", "FAILED_HARD_RULE", "RAW"]
    assert result.accepted_count == 1
    assert result.duplicate_count == 1
    assert result.rejected_count == 3
    assert len(result.error_rows) == 3
    assert len(repository.listings) == 3
    assert len(repository.candidates) == 1

    duplicate = result.records[1].duplicate_group
    assert duplicate is not None
    assert duplicate.confidence == 1.0
    assert duplicate.manual_actions == ("merge", "split")
    candidate = result.candidates[0].to_card_dict()
    assert candidate["status"] == "CANDIDATE"
    assert candidate["rent"] == 45000.0
    assert candidate["area"] == 25.5
    assert candidate["heatZone"]
    assert result.records[2].issues[0].code == "rent_per_ping_above_format_maximum"
    assert {issue.code for issue in result.records[3].issues} == {"floor_not_ground_level"}
    assert result.records[4].issues[0].code == "missing_required_field"


def test_listing_pipeline_imports_csv_and_exposes_error_rows() -> None:
    result = ListingPipeline(geo_pipeline=_geo_pipeline()).import_csv(
        "\n".join(
            [
                "source_listing_id,address_raw,rent_amount,area_ping,floor,listing_status,snapshot_id",
                "LST-CSV-001,台北市大安區復興南路二段100號1樓,45000,25.5,1F,active,listing-2026-06-26",
                "LST-CSV-002,,35000,20,1F,active,listing-2026-06-26",
            ]
        )
        + "\n",
        imported_at=datetime(2026, 6, 28, tzinfo=UTC),
    )

    assert [record.status.value for record in result.records] == ["CANDIDATE", "RAW"]
    assert result.error_rows[0]["row_index"] == 2
    assert result.error_rows[0]["issues"][0]["code"] == "missing_required_field"


def test_listing_api_import_endpoint_and_candidate_inbox() -> None:
    app = create_app()
    app.state.listing_repository = InMemoryListingRepository()
    app.state.listing_geo_pipeline = _geo_pipeline()
    client = TestClient(app)

    response = client.post(
        "/listings/import-jobs",
        json={
            "records": [
                {
                    "source_listing_id": "LST-API-001",
                    "address_raw": "台北市大安區復興南路二段100號1樓",
                    "rent_amount": 45000.0,
                    "area_ping": 25.5,
                    "floor": "1F",
                    "listing_status": "active",
                    "snapshot_id": "listing-2026-06-26",
                }
            ],
        },
        headers=LISTING_HEADERS,
    )

    assert response.status_code == 202
    body = response.json()
    assert body["accepted_count"] == 1
    assert body["candidates"][0]["status"] == "CANDIDATE"

    inbox = client.get("/listings/candidates", headers=LISTING_HEADERS)

    assert inbox.status_code == 200
    assert len(inbox.json()["candidates"]) == 1


def test_listing_import_rejects_missing_or_mismatched_tenant_scope() -> None:
    client = TestClient(create_app())
    payload = {
        "records": [
            {
                "source_listing_id": "LST-BYPASS-001",
                "address_raw": "台北市信義區松高路1號",
                "rent_amount": 80000.0,
                "area_ping": 30.0,
            }
        ]
    }

    # 1. Missing x-tenant-id -> 403 TENANT_SCOPE_DENIED
    missing_scope_headers = {
        "x-subject-id": "operator-attacker",
        "x-roles": "expansion_user",
    }
    res_missing = client.post("/listings/import", json=payload, headers=missing_scope_headers)
    assert res_missing.status_code == 403
    assert "TENANT_SCOPE_DENIED" in res_missing.text

    # 2. Mismatched tenant scope (header x-tenant-id="tenant-victim" vs request.state.operator_principal tenant_id="tenant-attacker")
    app = create_app()

    @app.middleware("http")
    async def _inject_attacker_principal(request: Request, call_next):
        from shared.auth import Principal, Scope
        request.state.operator_principal = Principal(
            subject_id="attacker",
            roles=frozenset(),
            scope=Scope(tenant_id="tenant-attacker"),
        )
        return await call_next(request)

    mismatched_client = TestClient(app)
    mismatched_headers = {
        "x-subject-id": "operator-attacker",
        "x-roles": "expansion_user",
        "x-tenant-id": "tenant-victim",
    }
    res_mismatch = mismatched_client.post(
        "/listings/import", json=payload, headers=mismatched_headers
    )
    assert res_mismatch.status_code == 403
    assert "TENANT_SCOPE_DENIED" in res_mismatch.text

