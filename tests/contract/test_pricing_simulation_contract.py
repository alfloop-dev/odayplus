from __future__ import annotations

from fastapi import status
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.auth import Role
from tests.integration._authz import auth_headers

PRICEOPS_HEADERS = auth_headers(Role.PRICING_MANAGER)


def test_simulate_scenario_contract_valid_and_invalid() -> None:
    client = TestClient(
        create_app(),
        headers={**PRICEOPS_HEADERS, "x-correlation-id": "corr-sim-contract"},
    )
    plan_id = "plan-scen-contract-1"

    # Create plan via optimizer endpoint
    client.post(
        "/priceops/optimizer-jobs",
        json={
            "optimized_at": "2026-06-28T03:00:00Z",
            "plans": [
                {
                    "tenant_id": "oday-tw",
                    "plan_id": plan_id,
                    "items": [
                        {
                            "item_id": "item-contract-1",
                            "store_id": "store-tw-101",
                            "machine_type": "washer-20kg",
                            "unit_cost": 40.0,
                            "current_price": 100.0,
                            "baseline_demand": 50.0,
                            "elasticity_value": -1.2,
                            "margin_floor_ratio": 0.15,
                            "max_increase_pct": 0.20,
                            "max_decrease_pct": 0.20,
                        }
                    ],
                }
            ],
        },
    )

    # 1. Valid scenario simulation
    resp_valid = client.post(
        f"/api/v1/priceops/plans/{plan_id}/simulate-scenario",
        json={
            "actor": "pricing-analyst",
            "reason": "simulate 10% price increase scenario",
            "candidate_prices": {"item-contract-1": 110.0},
        },
    )
    assert resp_valid.status_code == status.HTTP_200_OK
    data = resp_valid.json()
    assert data["plan_id"] == plan_id
    assert data["is_feasible"] is True
    assert data["is_baseline_distinct"] is True
    assert len(data["items"]) == 1

    item_sim = data["items"][0]
    assert item_sim["baseline_price"] == 100.0
    assert item_sim["candidate_price"] == 110.0
    assert item_sim["baseline_simulation"]["price"] == 100.0
    assert item_sim["candidate_simulation"]["price"] == 110.0
    assert item_sim["is_baseline_distinct"] is True

    # 2. Invalid scenario simulation (negative price) -> 400 Bad Request
    resp_invalid = client.post(
        f"/api/v1/priceops/plans/{plan_id}/simulate-scenario",
        json={
            "actor": "pricing-analyst",
            "reason": "invalid negative price scenario",
            "candidate_prices": {"item-contract-1": -50.0},
        },
    )
    assert resp_invalid.status_code == status.HTTP_400_BAD_REQUEST


def test_decision_writeback_contract_idempotency_and_fail_closed() -> None:
    client = TestClient(
        create_app(),
        headers={**PRICEOPS_HEADERS, "x-correlation-id": "corr-wb-contract"},
    )
    plan_id = "plan-wb-contract-1"

    # 1. Fail closed on plan without optimization comparison
    # Create plan via POST /api/v1/priceops/plans
    client.post(
        "/api/v1/priceops/plans",
        json={
            "tenant_id": "oday-tw",
            "plan_id": plan_id,
            "correlation_id": "corr-wb-contract",
            "items": [
                {
                    "item_id": "item-wb-1",
                    "store_id": "store-tw-102",
                    "machine_type": "dryer-15kg",
                    "unit_cost": 30.0,
                    "current_price": 80.0,
                    "baseline_demand": 40.0,
                    "elasticity_value": -1.0,
                    "margin_floor_ratio": 0.15,
                    "max_increase_pct": 0.15,
                }
            ],
        },
    )

    # Attempt decision writeback without simulation/optimization -> fails closed (422)
    resp_fail_closed = client.post(
        f"/api/v1/priceops/plans/{plan_id}/decision-writeback",
        json={
            "actor": "pricing-officer",
            "decision": "approved",
            "reason": "approve without simulation",
        },
    )
    assert resp_fail_closed.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # 2. Now optimize plan so results are available
    client.post(
        f"/api/v1/priceops/plans/{plan_id}/simulate",
        json={"actor": "system", "reason": "simulate"},
    )
    client.post(
        f"/api/v1/priceops/plans/{plan_id}/optimize",
        json={"actor": "system", "reason": "optimize"},
    )

    # 3. Perform decision writeback with Idempotency-Key
    headers_idem = {**PRICEOPS_HEADERS, "Idempotency-Key": "wb-idem-key-888"}
    resp_wb1 = client.post(
        f"/api/v1/priceops/plans/{plan_id}/decision-writeback",
        json={
            "actor": "pricing-officer",
            "decision": "approved",
            "reason": "approved optimal pricing scenario",
        },
        headers=headers_idem,
    )
    assert resp_wb1.status_code == status.HTTP_200_OK
    data1 = resp_wb1.json()
    assert data1["decision"] == "approved"
    assert data1["actor"] == "pricing-officer"
    assert "decision_id" in data1

    # Replaying decision writeback returns identical response
    resp_wb2 = client.post(
        f"/api/v1/priceops/plans/{plan_id}/decision-writeback",
        json={
            "actor": "pricing-officer",
            "decision": "approved",
            "reason": "approved optimal pricing scenario",
        },
        headers=headers_idem,
    )
    assert resp_wb2.status_code == status.HTTP_200_OK
    data2 = resp_wb2.json()
    assert data2["decision_id"] == data1["decision_id"]
