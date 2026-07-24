"""API integration tests for the PriceOps elasticity binding (ODP-GAP-ML-003).

The PriceOps plan endpoint used to require the caller to hand-feed an
``elasticity_value``. Part 3 of the ML pipeline binds the elasticity estimator
(``models.priceops.elasticity``) into the plan-creation path so a plan item can
be built from observed ``(price, demand)`` history, and *fails closed* (HTTP
422) when neither live observations nor a client value are available.

Covers:
* a plan item with sufficient observations is priced from an estimated
  elasticity, with model-binding metadata on the response and audit event;
* a plan item with a client-supplied value keeps working (backward compatible);
* a plan item with neither observations nor a supplied value is refused (422);
* the batch optimizer path is also fail-closed.
"""

from __future__ import annotations

import math

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from tests.integration._authz import PRICEOPS_HEADERS


def _client() -> TestClient:
    return TestClient(create_app(), headers=PRICEOPS_HEADERS)


def _loglog_observations() -> list[dict[str, float]]:
    # log(q) = 5.0 - 1.5 * log(p)  =>  true elasticity = -1.5
    return [
        {"price": p, "demand": math.exp(5.0) * (p**-1.5)} for p in (2.0, 3.0, 4.0, 5.0, 6.0, 7.0)
    ]


def _plan_item(**overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "store_id": "store-ml3",
        "machine_type": "washer-20kg",
        "unit_cost": 3.0,
        "current_price": 4.0,
        "baseline_demand": 1000.0,
    }
    item.update(overrides)
    return item


def test_plan_estimates_elasticity_from_observations() -> None:
    client = _client()
    resp = client.post(
        "/priceops/plans",
        json={
            "tenant_id": "tenant-ml3",
            "items": [_plan_item(price_demand_observations=_loglog_observations())],
        },
        headers={"x-correlation-id": "corr-ml3-est"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    bindings = body["elasticity_bindings"]
    assert len(bindings) == 1
    binding = bindings[0]
    assert binding["elasticity_source"] == "estimated"
    assert binding["sample_size"] == 6
    # regression recovers the true -1.5 elasticity from the log-log curve
    assert abs(binding["elasticity_value"] - (-1.5)) < 1e-3
    assert binding["confidence"] > 0.4
    assert binding["model_version"] == "priceops-elasticity-baseline-v1"

    # the estimated elasticity flows into the plan item and can be simulated
    plan_id = body["plan_id"]
    sim = client.post(
        f"/priceops/plans/{plan_id}/simulate",
        json={"actor": "pricing-a"},
        headers={"x-correlation-id": "corr-ml3-est"},
    )
    assert sim.status_code == 200, sim.text

    # the model binding is captured on the audit trail
    audit = client.get("/audit/events", params={"correlation_id": "corr-ml3-est"})
    created = [e for e in audit.json()["events"] if e["event_type"] == "priceops.plan_created.v1"]
    assert created, "plan-created audit event missing"
    audited = created[0]["metadata"]["elasticity_bindings"][0]
    assert audited["elasticity_source"] == "estimated"


def test_plan_accepts_client_supplied_elasticity() -> None:
    client = _client()
    resp = client.post(
        "/priceops/plans",
        json={
            "tenant_id": "tenant-ml3",
            "items": [_plan_item(elasticity_value=-1.1, confidence=0.8)],
        },
    )
    assert resp.status_code == 201, resp.text
    binding = resp.json()["elasticity_bindings"][0]
    assert binding["elasticity_source"] == "client_supplied"
    assert binding["elasticity_value"] == -1.1
    assert binding["confidence"] == 0.8


def test_plan_fails_closed_without_elasticity_signal() -> None:
    client = _client()
    resp = client.post(
        "/priceops/plans",
        json={"tenant_id": "tenant-ml3", "items": [_plan_item()]},
    )
    assert resp.status_code == 422, resp.text
    assert "elasticity" in resp.json()["detail"].lower()


def test_plan_fails_closed_with_insufficient_observations() -> None:
    client = _client()
    # only 3 usable observations (below the estimator's minimum) and no value
    sparse = [
        {"price": 4.0, "demand": 100.0},
        {"price": 4.5, "demand": 90.0},
        {"price": 5.0, "demand": 80.0},
    ]
    resp = client.post(
        "/priceops/plans",
        json={
            "tenant_id": "tenant-ml3",
            "items": [_plan_item(price_demand_observations=sparse)],
        },
    )
    assert resp.status_code == 422, resp.text


def test_optimizer_job_fails_closed_without_elasticity_signal() -> None:
    client = _client()
    resp = client.post(
        "/priceops/optimizer-jobs",
        json={
            "plans": [
                {"tenant_id": "tenant-ml3", "items": [_plan_item()]},
            ]
        },
    )
    assert resp.status_code == 422, resp.text


def test_optimizer_job_estimates_from_observations() -> None:
    client = _client()
    resp = client.post(
        "/priceops/optimizer-jobs",
        json={
            "plans": [
                {
                    "tenant_id": "tenant-ml3",
                    "items": [_plan_item(price_demand_observations=_loglog_observations())],
                }
            ]
        },
        headers={"x-correlation-id": "corr-ml3-batch"},
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["hard_constraint_violation_count"] == 0
