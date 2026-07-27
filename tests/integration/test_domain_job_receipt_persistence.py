from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.avm import create_avm_router
from apps.api.app.routes.forecastops import create_forecastops_router
from apps.api.app.routes.interventions import create_interventions_router
from apps.api.app.routes.priceops import create_priceops_router
from apps.api.app.routes.sitescore import create_sitescore_router
from apps.api.oday_api.main import create_app
from shared.api.idempotency import IdempotencyConflictError
from shared.infrastructure.persistence.command_receipts import (
    TenantScopedCommandReceiptStore,
)
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.jobs.queue import InMemoryJobQueue, JobRequest
from tests.integration._authz import (
    ADLIFT_HEADERS,
    AVM_HEADERS,
    FORECASTOPS_HEADERS,
    INTERVENTION_HEADERS,
    PRICEOPS_HEADERS,
    SITESCORE_HEADERS,
)

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"


def _assert_receipts_are_not_worker_jobs(queue, claim) -> None:
    for index, job_type in enumerate(
        ("priceops.command-receipt", "forecastops.receipt"),
        start=1,
    ):
        queue.enqueue(
            JobRequest(
                job_type=job_type,
                payload={"tenant_id": TENANT_A},
                idempotency_key=f"receipt-{index}",
            ),
            correlation_id=f"corr-receipt-{index}",
        )

    assert queue.count_active_jobs(tenant_id=TENANT_A) == 0
    assert claim() is None

    queue.enqueue(
        JobRequest(
            job_type="forecast",
            payload={"tenant_id": TENANT_A, "store_id": "store-live-1"},
            idempotency_key="forecast-live-1",
        ),
        correlation_id="corr-forecast-live-1",
    )
    assert queue.count_active_jobs(tenant_id=TENANT_A) == 1
    claimed = claim()
    assert claimed is not None
    assert claimed.job_type == "forecast"
    assert claim() is None


def test_in_memory_worker_claims_and_leases_skip_durable_receipts() -> None:
    claim_queue = InMemoryJobQueue()
    _assert_receipts_are_not_worker_jobs(
        claim_queue,
        lambda: claim_queue.claim_next(worker_id="test-worker"),
    )

    lease_queue = InMemoryJobQueue()
    _assert_receipts_are_not_worker_jobs(
        lease_queue,
        lambda: lease_queue.lease(lease_duration_seconds=45),
    )


def test_durable_worker_claims_and_leases_skip_durable_receipts(tmp_path) -> None:
    claim_bundle = _durable_bundle(tmp_path / "claim-receipts.sqlite3")
    try:
        _assert_receipts_are_not_worker_jobs(
            claim_bundle.job_queue,
            lambda: claim_bundle.job_queue.claim_next(worker_id="test-worker"),
        )
    finally:
        claim_bundle.engine.close()

    lease_bundle = _durable_bundle(tmp_path / "lease-receipts.sqlite3")
    try:
        _assert_receipts_are_not_worker_jobs(
            lease_bundle.job_queue,
            lambda: lease_bundle.job_queue.lease(lease_duration_seconds=45),
        )
    finally:
        lease_bundle.engine.close()


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


def _priceops_payload(tenant_id: str, *, current_price: float = 4.0) -> dict:
    return {
        "tenant_id": tenant_id,
        "items": [
            {
                "store_id": "store-priceops",
                "machine_type": "washer",
                "unit_cost": 3.0,
                "current_price": current_price,
                "baseline_demand": 100.0,
                "elasticity_value": -1.1,
                "confidence": 0.8,
            }
        ],
    }


def test_command_receipt_survives_restart_and_enforces_tenant_fingerprint(
    tmp_path,
) -> None:
    db_path = tmp_path / "command-receipts.sqlite3"
    bundle = _durable_bundle(db_path)
    try:
        store = TenantScopedCommandReceiptStore(
            queue=bundle.job_queue,
            service="priceops",
        )
        first = store.run(
            tenant_id=TENANT_A,
            idempotency_key="same-key",
            scope="priceops:create-plan",
            payload={"rent": 50_000},
            correlation_id="corr-command-first",
            operation=lambda receipt_id: {
                "receipt_id": receipt_id,
                "plan_id": "plan-a",
            },
        )
        assert first.replayed is False
        assert first.value["receipt_id"] == first.receipt_id
    finally:
        bundle.engine.close()

    reopened = _durable_bundle(db_path)
    try:
        store = TenantScopedCommandReceiptStore(
            queue=reopened.job_queue,
            service="priceops",
        )
        replay = store.run(
            tenant_id=TENANT_A,
            idempotency_key="same-key",
            scope="priceops:create-plan",
            payload={"rent": 50_000},
            correlation_id="corr-command-replay",
            operation=lambda _receipt_id: pytest.fail(
                "a durable replay must not execute the command again"
            ),
        )
        assert replay.replayed is True
        assert replay.receipt_id == first.receipt_id
        assert replay.value["plan_id"] == "plan-a"

        with pytest.raises(IdempotencyConflictError):
            store.run(
                tenant_id=TENANT_A,
                idempotency_key="same-key",
                scope="priceops:create-plan",
                payload={"rent": 75_000},
                correlation_id="corr-command-conflict",
                operation=lambda _receipt_id: {},
            )

        other_tenant = store.run(
            tenant_id=TENANT_B,
            idempotency_key="same-key",
            scope="priceops:create-plan",
            payload={"rent": 75_000},
            correlation_id="corr-command-other-tenant",
            operation=lambda receipt_id: {
                "receipt_id": receipt_id,
                "plan_id": "plan-b",
            },
        )
        assert other_tenant.replayed is False
        assert other_tenant.receipt_id != first.receipt_id
        assert (
            store.get(
                tenant_id=TENANT_B,
                receipt_id=first.receipt_id,
            )
            is None
        )
    finally:
        reopened.engine.close()


def test_command_receipt_waits_for_concurrent_owner_and_replays(tmp_path) -> None:
    bundle = _durable_bundle(tmp_path / "concurrent-command-receipts.sqlite3")
    executions = 0
    executions_lock = threading.Lock()
    try:
        store = TenantScopedCommandReceiptStore(
            queue=bundle.job_queue,
            service="priceops",
        )

        def invoke() -> object:
            def operation(receipt_id: str) -> dict:
                nonlocal executions
                with executions_lock:
                    executions += 1
                time.sleep(0.05)
                return {"receipt_id": receipt_id, "plan_id": "plan-once"}

            return store.run(
                tenant_id=TENANT_A,
                idempotency_key="concurrent-key",
                scope="priceops:create-plan",
                payload={"rent": 50_000},
                correlation_id="corr-concurrent",
                operation=operation,
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            outcomes = list(executor.map(lambda _: invoke(), range(8)))

        assert executions == 1
        assert sum(not outcome.replayed for outcome in outcomes) == 1
        assert len({outcome.receipt_id for outcome in outcomes}) == 1
        assert {outcome.value["plan_id"] for outcome in outcomes} == {"plan-once"}
    finally:
        bundle.engine.close()


def test_in_memory_command_receipt_reservation_is_atomic() -> None:
    executions = 0
    executions_lock = threading.Lock()
    store = TenantScopedCommandReceiptStore(
        queue=InMemoryJobQueue(),
        service="priceops",
    )

    def invoke() -> object:
        def operation(receipt_id: str) -> dict:
            nonlocal executions
            with executions_lock:
                executions += 1
            time.sleep(0.05)
            return {"receipt_id": receipt_id, "plan_id": "plan-once"}

        return store.run(
            tenant_id=TENANT_A,
            idempotency_key="memory-concurrent-key",
            scope="priceops:create-plan",
            payload={"rent": 50_000},
            correlation_id="corr-memory-concurrent",
            operation=operation,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(lambda _: invoke(), range(8)))

    assert executions == 1
    assert sum(not outcome.replayed for outcome in outcomes) == 1
    assert len({outcome.receipt_id for outcome in outcomes}) == 1
    assert {outcome.value["plan_id"] for outcome in outcomes} == {"plan-once"}


def test_priceops_command_replays_after_app_restart_and_rejects_payload_change(
    tmp_path,
) -> None:
    db_path = tmp_path / "priceops-command-receipts.sqlite3"
    bundle = _durable_bundle(db_path)
    try:
        client = TestClient(create_app(persistence=bundle))
        first = client.post(
            "/priceops/plans",
            headers=_headers(PRICEOPS_HEADERS, TENANT_A, "same-key"),
            json=_priceops_payload(TENANT_A),
        )
        assert first.status_code == 201, first.text
        first_payload = first.json()
        assert first_payload["created"] is True
    finally:
        bundle.engine.close()

    reopened = _durable_bundle(db_path)
    try:
        client = TestClient(create_app(persistence=reopened))
        replay = client.post(
            "/priceops/plans",
            headers=_headers(PRICEOPS_HEADERS, TENANT_A, "same-key"),
            json=_priceops_payload(TENANT_A),
        )
        assert replay.status_code == 201, replay.text
        assert replay.json()["created"] is False
        assert replay.json()["plan_id"] == first_payload["plan_id"]

        conflict = client.post(
            "/priceops/plans",
            headers=_headers(PRICEOPS_HEADERS, TENANT_A, "same-key"),
            json=_priceops_payload(TENANT_A, current_price=6.0),
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "idempotency_conflict"

        other_tenant = client.post(
            "/priceops/plans",
            headers=_headers(PRICEOPS_HEADERS, TENANT_B, "same-key"),
            json=_priceops_payload(TENANT_B),
        )
        assert other_tenant.status_code == 201, other_tenant.text
        assert other_tenant.json()["created"] is True
        assert other_tenant.json()["plan_id"] != first_payload["plan_id"]
    finally:
        reopened.engine.close()


def test_forecast_receipt_and_idempotency_survive_app_restart_by_tenant(
    tmp_path,
) -> None:
    db_path = tmp_path / "forecast-receipts.sqlite3"
    original_payload = _forecast_payload("store-a")
    bundle = _durable_bundle(db_path)
    try:
        first_client = TestClient(create_app(persistence=bundle))
        first = first_client.post(
            "/forecastops/forecast-jobs",
            headers=_headers(FORECASTOPS_HEADERS, TENANT_A, "same-key"),
            json=original_payload,
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
            json=original_payload,
        )
        assert replay.status_code == 202, replay.text
        assert replay.json()["created"] is False
        assert replay.json()["job_id"] == first_receipt["job_id"]

        conflict = second_client.post(
            "/forecastops/forecast-jobs",
            headers=_headers(FORECASTOPS_HEADERS, TENANT_A, "same-key"),
            json=_forecast_payload("store-a-changed"),
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"

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


def test_adlift_receipt_and_idempotency_survive_app_restart_by_tenant(
    tmp_path,
) -> None:
    db_path = tmp_path / "adlift-receipts.sqlite3"
    bundle = _durable_bundle(db_path)
    try:
        first_client = TestClient(create_app(persistence=bundle))
        first = first_client.post(
            "/adlift/incrementality-jobs",
            headers=_headers(ADLIFT_HEADERS, TENANT_A, "same-key"),
            json={"campaigns": []},
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
            "/adlift/incrementality-jobs",
            headers=_headers(ADLIFT_HEADERS, TENANT_A, "same-key"),
            json={"campaigns": []},
        )
        assert replay.status_code == 202, replay.text
        assert replay.json()["created"] is False
        assert replay.json()["job_id"] == first_receipt["job_id"]

        fetched = second_client.get(
            f"/adlift/incrementality-jobs/{first_receipt['job_id']}",
            headers=_headers(ADLIFT_HEADERS, TENANT_A, "read-key"),
        )
        assert fetched.status_code == 200
        assert fetched.json()["job_id"] == first_receipt["job_id"]

        hidden = second_client.get(
            f"/adlift/incrementality-jobs/{first_receipt['job_id']}",
            headers=_headers(ADLIFT_HEADERS, TENANT_B, "read-key"),
        )
        assert hidden.status_code == 404
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
    from apps.api.app.routes.adlift import create_adlift_router

    app.include_router(
        create_adlift_router(
            job_queue=InMemoryJobQueue(),
            require_durable_jobs=True,
        )
    )
    app.include_router(
        create_avm_router(
            job_queue=InMemoryJobQueue(),
            require_durable_commands=True,
        )
    )
    app.include_router(
        create_priceops_router(
            job_queue=InMemoryJobQueue(),
            require_durable_commands=True,
        )
    )
    app.include_router(
        create_interventions_router(
            job_queue=InMemoryJobQueue(),
            require_durable_commands=True,
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

    adlift = client.post(
        "/adlift/incrementality-jobs",
        headers=_headers(ADLIFT_HEADERS, TENANT_A, "adlift-memory"),
        json={"campaigns": []},
    )
    assert adlift.status_code == 503
    assert adlift.json()["detail"]["code"] == "DURABLE_JOB_RECEIPT_STORE_REQUIRED"

    avm = client.post(
        "/avm/cases",
        headers=_headers(AVM_HEADERS, TENANT_A, "avm-memory"),
        json={
            "store_id": "store-a",
            "gm_ttm": 1_000_000,
            "forecast_gm_next_12m": 1_100_000,
            "asset_book_value": 600_000,
            "equipment_fair_value": 450_000,
            "created_by": "finance-a",
        },
    )
    assert avm.status_code == 503
    assert avm.json()["detail"]["code"] == "DURABLE_COMMAND_STORE_REQUIRED"

    priceops = client.post(
        "/priceops/plans",
        headers=_headers(PRICEOPS_HEADERS, TENANT_A, "priceops-memory"),
        json={
            "tenant_id": TENANT_A,
            "items": [
                {
                    "store_id": "store-a",
                    "machine_type": "washer",
                    "unit_cost": 3,
                    "current_price": 4,
                    "baseline_demand": 100,
                    "elasticity_value": -1.1,
                    "confidence": 0.8,
                }
            ],
        },
    )
    assert priceops.status_code == 503
    assert priceops.json()["detail"]["code"] == "DURABLE_COMMAND_STORE_REQUIRED"

    intervention = client.post(
        "/interventions",
        headers=_headers(
            INTERVENTION_HEADERS,
            TENANT_A,
            "intervention-memory",
        ),
        json={
            "store_id": "store-a",
            "kind": "PRICE_CHANGE",
            "expected_outcome": "recover gross margin",
            "planned_start": "2026-07-25T00:00:00Z",
            "planned_end": "2026-08-01T00:00:00Z",
            "created_by": "ops-a",
        },
    )
    assert intervention.status_code == 503
    assert intervention.json()["detail"]["code"] == "DURABLE_COMMAND_STORE_REQUIRED"
