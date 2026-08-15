from __future__ import annotations

from fastapi import status
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.auth import Role
from tests.integration._authz import auth_headers

PRICEOPS_HEADERS = auth_headers(Role.PRICING_MANAGER)


def test_priceops_comparison_api_blocks_infeasible_approval() -> None:
    client = TestClient(
        create_app(),
        headers={**PRICEOPS_HEADERS, "x-correlation-id": "corr-priceops-api"},
    )
    plan_id = "api-price-plan-infeasible"

    optimizer = client.post(
        "/priceops/optimizer-jobs",
        json={
            "optimized_at": "2026-06-28T03:00:00Z",
            "plans": [
                {
                    "tenant_id": "oday-tw",
                    "plan_id": plan_id,
                    "items": [
                        {
                            "item_id": "api-price-item-1",
                            "store_id": "api-store-1",
                            "machine_type": "washer-20kg",
                            "unit_cost": 10.0,
                            "current_price": 5.0,
                            "baseline_demand": 500.0,
                            "elasticity_value": -1.0,
                            "margin_floor_ratio": 0.2,
                            "max_increase_pct": 0.1,
                        }
                    ],
                }
            ],
        },
    )

    assert optimizer.status_code == status.HTTP_202_ACCEPTED
    comparison = client.get(f"/priceops/plans/{plan_id}/comparison")

    assert comparison.status_code == status.HTTP_200_OK
    comparison_body = comparison.json()
    assert comparison_body["is_feasible"] is False
    assert comparison_body["is_approvable"] is False
    assert comparison_body["items"][0]["constraint_status"] == "HARD_CONSTRAINT_FAILED"
    assert comparison_body["rollback_ready"] is True

    submit = client.post(
        f"/priceops/plans/{plan_id}/submit",
        json={"actor": "pricing-manager", "reason": "send infeasible plan to approval"},
    )
    assert submit.status_code == status.HTTP_200_OK

    approval = client.post(
        f"/priceops/plans/{plan_id}/approve",
        json={
            "actor_id": "pricing-director",
            "reason": "attempted approval should be blocked",
            "decision": "APPROVE",
        },
    )

    assert approval.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "cannot be approved" in approval.json()["detail"]
