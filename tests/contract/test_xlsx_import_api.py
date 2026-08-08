"""Contract & integration tests for XLSX import API routes (ODP-CAP-XLSX-IMPORT-001)."""

import base64

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.listings import create_assisted_intake_router
from tests.unit.listing.test_xlsx_import import _create_mock_xlsx

app = FastAPI()
router = create_assisted_intake_router()
app.include_router(router)
client = TestClient(app)

TENANT_A = "00000000-0000-0000-0000-000000000001"
ACTOR_A = "00000000-0000-0000-0000-000000000101"

HEADERS = {
    "x-subject-id": ACTOR_A,
    "x-tenant-id": TENANT_A,
    "x-roles": "site_reviewer,data_owner,expansion_user",
    "x-operator-role": "expansion-manager",
}


def test_preview_xlsx_api_endpoint():
    """Verify POST /intake-batches/xlsx/preview parses safely and performs no writes."""

    xlsx_bytes = _create_mock_xlsx([
        ["地址", "租金", "坪數", "樓層"],
        ["台北市信義區忠孝東路五段100號", "65000", "22", "1F"],
        ["新北市板橋區文化路一段200號", "42000", "18", "2F"],
    ])
    b64_data = base64.b64encode(xlsx_bytes).decode("utf-8")

    res = client.post(
        "/intake-batches/xlsx/preview",
        headers=HEADERS,
        json={
            "file_base64": b64_data,
            "scope": {"tenant_id": TENANT_A},
        },
    )

    assert res.status_code == 200
    data = res.json()
    assert data["batch_id"].startswith("xlsx-batch-")
    assert data["total_rows"] == 2
    assert data["valid_count"] == 2
    assert data["rejected_count"] == 0
    assert len(data["valid_rows"]) == 2
    assert data["valid_rows"][0]["address_raw"] == "台北市信義區忠孝東路五段100號"


def test_commit_xlsx_api_endpoint_idempotent():
    """Verify POST /intake-batches/xlsx/commit writes validated rows and is idempotent."""

    rows = [
        {"address_raw": "台中市西屯區台灣大道三段99號", "rent_amount": 75000, "area_ping": 35},
    ]

    idempotency_key = "IDEM-API-TEST-99887766"
    commit_headers = {**HEADERS, "Idempotency-Key": idempotency_key}

    # 1. First Commit Call
    res1 = client.post(
        "/intake-batches/xlsx/commit",
        headers=commit_headers,
        json={
            "batch_id": "test-api-batch-101",
            "rows": rows,
            "scope": {"tenant_id": TENANT_A},
        },
    )

    assert res1.status_code == 202
    data1 = res1.json()
    assert data1["accepted_count"] == 1
    assert data1["rejected_count"] == 0
    assert len(data1["intake_ids"]) == 1
    assert res1.headers.get("idempotency-replayed") == "false"

    # 2. Duplicate Commit Call (Idempotent Replay)
    res2 = client.post(
        "/intake-batches/xlsx/commit",
        headers=commit_headers,
        json={
            "batch_id": "test-api-batch-101",
            "rows": rows,
            "scope": {"tenant_id": TENANT_A},
        },
    )

    assert res2.status_code == 202
    data2 = res2.json()
    assert data2["batch_id"] == data1["batch_id"]
    assert data2["intake_ids"] == data1["intake_ids"]
    assert res2.headers.get("idempotency-replayed") == "true"


def test_export_xlsx_errors_api_endpoint():
    """Verify GET /intake-batches/xlsx/errors/{batch_id}/export returns masked error files."""

    xlsx_bytes = _create_mock_xlsx([
        ["地址", "租金"],
        ["", "45000"],  # Missing address -> error
    ])
    b64_data = base64.b64encode(xlsx_bytes).decode("utf-8")

    preview_res = client.post(
        "/intake-batches/xlsx/preview",
        headers=HEADERS,
        json={"file_base64": b64_data, "scope": {"tenant_id": TENANT_A}},
    )
    assert preview_res.status_code == 200
    batch_id = preview_res.json()["batch_id"]

    # Export CSV
    export_csv = client.get(
        f"/intake-batches/xlsx/errors/{batch_id}/export?format=csv",
        headers=HEADERS,
    )
    assert export_csv.status_code == 200
    assert "text/csv" in export_csv.headers["content-type"]
    assert "Row Index" in export_csv.text

    # Export XLSX
    export_xlsx = client.get(
        f"/intake-batches/xlsx/errors/{batch_id}/export?format=xlsx",
        headers=HEADERS,
    )
    assert export_xlsx.status_code == 200
    assert "spreadsheetml" in export_xlsx.headers["content-type"]


TENANT_B = "00000000-0000-0000-0000-000000000002"
ACTOR_B = "00000000-0000-0000-0000-000000000102"

HEADERS_B = {
    "x-subject-id": ACTOR_B,
    "x-tenant-id": TENANT_B,
    "x-roles": "site_reviewer,data_owner,expansion_user",
    "x-operator-role": "expansion-manager",
}


def test_committed_intake_ids_resolve_to_stored_intakes():
    """Every intake_id the commit receipt returns must be retrievable."""

    res = client.post(
        "/intake-batches/xlsx/commit",
        headers={**HEADERS, "Idempotency-Key": "IDEM-API-PERSIST-11223344"},
        json={
            "batch_id": "test-api-batch-persist",
            "rows": [{"address_raw": "台北市內湖區瑞光路100號", "rent_amount": 68000, "area_ping": 30}],
            "scope": {"tenant_id": TENANT_A},
        },
    )
    assert res.status_code == 202
    intake_ids = res.json()["intake_ids"]
    assert len(intake_ids) == 1

    detail = client.get(f"/intakes/{intake_ids[0]}", headers=HEADERS)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["scope"]["tenant_id"] == TENANT_A
    field_paths = {field["field_path"] for field in body["fields"]}
    assert "address_raw" in field_paths


def test_commit_enforces_domain_validation_on_client_supplied_rows():
    """Preview is not advisory: the commit route re-runs the same domain rules."""

    res = client.post(
        "/intake-batches/xlsx/commit",
        headers={**HEADERS, "Idempotency-Key": "IDEM-API-VALIDATE-5566778899"},
        json={
            "rows": [
                {"address_raw": "台北市士林區中山北路五段1號", "original_url": "javascript:alert(1)"},
                {"address_raw": "台北市士林區中山北路五段3號", "area_ping": -999},
            ],
            "scope": {"tenant_id": TENANT_A},
        },
    )

    assert res.status_code == 202
    data = res.json()
    assert data["accepted_count"] == 0
    assert data["rejected_count"] == 2
    assert data["intake_ids"] == []


def test_idempotency_key_reuse_across_tenants_does_not_leak():
    """Tenant B reusing tenant A's key must commit its own rows, not read A's."""

    shared_key = "IDEM-API-CROSS-TENANT-4242"

    res_a = client.post(
        "/intake-batches/xlsx/commit",
        headers={**HEADERS, "Idempotency-Key": shared_key},
        json={
            "batch_id": "tenant-a-batch",
            "rows": [{"address_raw": "台北市南港區經貿二路1號", "rent_amount": 55000}],
            "scope": {"tenant_id": TENANT_A},
        },
    )
    assert res_a.status_code == 202
    data_a = res_a.json()

    res_b = client.post(
        "/intake-batches/xlsx/commit",
        headers={**HEADERS_B, "Idempotency-Key": shared_key},
        json={
            "batch_id": "tenant-b-batch",
            "rows": [
                {"address_raw": "台南市東區長榮路一段5號", "rent_amount": 18000},
                {"address_raw": "台南市東區長榮路一段7號", "rent_amount": 19000},
            ],
            "scope": {"tenant_id": TENANT_B},
        },
    )
    assert res_b.status_code == 202
    data_b = res_b.json()

    assert res_b.headers.get("idempotency-replayed") == "false"
    assert data_b["batch_id"] == "tenant-b-batch"
    assert data_b["accepted_count"] == 2
    assert set(data_b["intake_ids"]).isdisjoint(data_a["intake_ids"])

    # Tenant B's rows were really written, under tenant B's scope.
    detail = client.get(f"/intakes/{data_b['intake_ids'][0]}", headers=HEADERS_B)
    assert detail.status_code == 200
    assert detail.json()["scope"]["tenant_id"] == TENANT_B


def test_commit_rejects_a_scope_that_is_not_the_authenticated_tenant():
    res = client.post(
        "/intake-batches/xlsx/commit",
        headers={**HEADERS, "Idempotency-Key": "IDEM-API-SCOPE-9090909090"},
        json={
            "rows": [{"address_raw": "台北市大同區民權西路1號"}],
            "scope": {"tenant_id": TENANT_B},
        },
    )
    assert res.status_code == 403


def test_export_of_unknown_or_foreign_batch_returns_404():
    """An empty 200 would read to an operator as 'your import had no errors'."""

    unknown = client.get(
        "/intake-batches/xlsx/errors/xlsx-batch-does-not-exist/export?format=csv",
        headers=HEADERS,
    )
    assert unknown.status_code == 404

    xlsx_bytes = _create_mock_xlsx([["地址", "租金"], ["", "45000"]])
    preview_res = client.post(
        "/intake-batches/xlsx/preview",
        headers=HEADERS,
        json={"file_base64": base64.b64encode(xlsx_bytes).decode("utf-8"), "scope": {"tenant_id": TENANT_A}},
    )
    assert preview_res.status_code == 200
    batch_id = preview_res.json()["batch_id"]

    foreign = client.get(
        f"/intake-batches/xlsx/errors/{batch_id}/export?format=csv",
        headers=HEADERS_B,
    )
    assert foreign.status_code == 404

    owner = client.get(
        f"/intake-batches/xlsx/errors/{batch_id}/export?format=csv",
        headers=HEADERS,
    )
    assert owner.status_code == 200
