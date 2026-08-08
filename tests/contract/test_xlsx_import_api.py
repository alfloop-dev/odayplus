"""Contract & integration tests for XLSX import API routes (ODP-CAP-XLSX-IMPORT-001)."""

import base64
import pytest
from fastapi.testclient import TestClient

from apps.api.app.routes.listings import create_assisted_intake_router
from fastapi import FastAPI
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
