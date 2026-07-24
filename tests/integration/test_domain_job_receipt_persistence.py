from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.forecastops import create_forecastops_router
from apps.api.app.routes.sitescore import create_sitescore_router
from apps.api.oday_api.main import create_app
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.jobs.queue import InMemoryJobQueue
from tests.integration._authz import FORECASTOPS_HEADERS, SITESCORE_HEADERS

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"


def _headers(base: dict[str, str], tenant_id: str, key: str) -> dict[str, str]:
    return {
        **base,
        "x-tenant-id": tenant_id,
        "x-correlation-id": f"corr-{tenant_id}-{key}",
        "Idempotency-Key": key,
    }


def _forecast_payload(store_id: str) -> dict:
    return {
        "prediction_origin_time": "2026-07-24T09:00:00Z",
        "inputs": [
            {
                "store_id": store_id,
                "observations": [
                    {
                        "business_date": "2026-07-22",
                        "actual_revenue": 120_000,
                        "site_score_baseline_p50": 120_000,
                    },
                    {
                        "business_date": "2026-07-23",
                        "actual_revenue": 90_000,
                        "site_score_baseline_p50": 120_000,
                    },
                ],
            }
        ],
    }


def _sitescore_payload(candidate_id: str) -> dict:
    return {
        "prediction_origin_time": "2026-07-24T09:00:00Z",
        "features": [
            {
                "candidate_site_id": candidate_id,
                "feature_snapshot_time": "2026-07-24T08:00:00Z",
                "heat_zone_score": 82,
                "monthly_rent": 60_000,
                "area_ping": 25,
                "comparable_store_count": 5,
                "comparable_monthly_revenue_p50": 480_000,
                "buildout_capex": 2_500_000,
                "gross_margin_ratio": 0.6,
                "average_confidence": 0.92,
                "data_quality_score": 0.95,
                "source_snapshot_ids": ["listing-live-20260724"],
            }
        ],
    }


def test_forecast_receipt_and_idempotency_survive_app_restart_by_tenant(
    tmp_path,
) -> None:
    db_path = tmp_path / "forecast-receipts.sqlite3"
    bundle = _durable_bundle(db_path)
    try:
        first_client = TestClient(create_app(persistence=bundle))
        first = first_client.post(
            "/forecastops/forecast-jobs",
            headers=_headers(FORECASTOPS_HEADERS, TENANT_A, "same-key"),
            json=_forecast_payload("store-a"),
        )
        assert first.status_code == 202, first.text
        first_receipt = first.json()
        assert first_receipt["created"] is True
    finally:
        bundle.engine.close()

    reopened = _durable_bundle(db_path)
    try:
        second_client = TestClient(create_app(persistence=reopened))
        replay = second_client.post(
            "/forecastops/forecast-jobs",
            headers=_headers(FORECASTOPS_HEADERS, TENANT_A, "same-key"),
            json={"inputs": []},
        )
        assert replay.status_code == 202, replay.text
        assert replay.json()["created"] is False
        assert replay.json()["job_id"] == first_receipt["job_id"]

        fetched = second_client.get(
            f"/forecastops/forecast-jobs/{first_receipt['job_id']}",
            headers=_headers(FORECASTOPS_HEADERS, TENANT_A, "read-key"),
        )
        assert fetched.status_code == 200
        assert fetched.json()["job_id"] == first_receipt["job_id"]

        hidden = second_client.get(
            f"/forecastops/forecast-jobs/{first_receipt['job_id']}",
            headers=_headers(FORECASTOPS_HEADERS, TENANT_B, "read-key"),
        )
        assert hidden.status_code == 404

        other_tenant = second_client.post(
            "/forecastops/forecast-jobs",
            headers=_headers(FORECASTOPS_HEADERS, TENANT_B, "same-key"),
            json=_forecast_payload("store-b"),
        )
        assert other_tenant.status_code == 202, other_tenant.text
        assert other_tenant.json()["created"] is True
        assert other_tenant.json()["job_id"] != first_receipt["job_id"]
    finally:
        reopened.engine.close()


def test_sitescore_receipt_and_idempotency_survive_app_restart_by_tenant(
    tmp_path,
) -> None:
    db_path = tmp_path / "sitescore-receipts.sqlite3"
    bundle = _durable_bundle(db_path)
    try:
        first_client = TestClient(create_app(persistence=bundle))
        first = first_client.post(
            "/sitescore/score-jobs",
            headers=_headers(SITESCORE_HEADERS, TENANT_A, "same-key"),
            json=_sitescore_payload("candidate-a"),
        )
        assert first.status_code == 202, first.text
        first_receipt = first.json()
        assert first_receipt["created"] is True
    finally:
        bundle.engine.close()

    reopened = _durable_bundle(db_path)
    try:
        second_client = TestClient(create_app(persistence=reopened))
        replay = second_client.post(
            "/sitescore/score-jobs",
            headers=_headers(SITESCORE_HEADERS, TENANT_A, "same-key"),
            json={"features": []},
        )
        assert replay.status_code == 202, replay.text
        assert replay.json()["created"] is False
        assert replay.json()["job_id"] == first_receipt["job_id"]
        assert replay.json()["reports"][0]["report_id"] == first_receipt["reports"][0]["report_id"]

        other_tenant = second_client.post(
            "/sitescore/score-jobs",
            headers=_headers(SITESCORE_HEADERS, TENANT_B, "same-key"),
            json=_sitescore_payload("candidate-b"),
        )
        assert other_tenant.status_code == 202, other_tenant.text
        assert other_tenant.json()["created"] is True
        assert other_tenant.json()["job_id"] != first_receipt["job_id"]
    finally:
        reopened.engine.close()


def test_production_routes_reject_in_memory_job_receipts() -> None:
    app = FastAPI()
    app.include_router(
        create_forecastops_router(
            job_queue=InMemoryJobQueue(),
            require_durable_jobs=True,
            require_production_model=False,
        )
    )
    app.include_router(
        create_sitescore_router(
            job_queue=InMemoryJobQueue(),
            require_durable_jobs=True,
            require_production_model=False,
        )
    )
    client = TestClient(app)

    forecast = client.post(
        "/forecastops/forecast-jobs",
        headers=_headers(FORECASTOPS_HEADERS, TENANT_A, "forecast-memory"),
        json=_forecast_payload("store-a"),
    )
    assert forecast.status_code == 503
    assert forecast.json()["detail"]["code"] == "DURABLE_JOB_RECEIPT_STORE_REQUIRED"

    sitescore = client.post(
        "/sitescore/score-jobs",
        headers=_headers(SITESCORE_HEADERS, TENANT_A, "sitescore-memory"),
        json=_sitescore_payload("candidate-a"),
    )
    assert sitescore.status_code == 503
    assert sitescore.json()["detail"]["code"] == "DURABLE_JOB_RECEIPT_STORE_REQUIRED"
