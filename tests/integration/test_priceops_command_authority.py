"""Synthetic HTTP proof for canonical PriceOps scope, actor and command acknowledgments."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.priceops import create_priceops_router
from modules.priceops.application import PriceOpsService
from modules.priceops.domain import PriceConstraints, PricingPlanItem
from modules.priceops.domain.pricing import PriceElasticityEstimate
from modules.priceops.infrastructure import InMemoryPriceOpsRepository
from shared.api.errors import install_error_handlers
from shared.audit import InMemoryAuditLog
from shared.auth import Role
from tests.integration._authz import auth_headers


@pytest.fixture
def context():
    repository = InMemoryPriceOpsRepository()
    service = PriceOpsService(repository=repository)
    for plan_id, tenant, store in (("plan-a", "tenant-a", "store-a"), ("plan-b", "tenant-b", "store-b")):
        item = PricingPlanItem.create(
            item_id=f"item-{plan_id}", store_id=store, machine_type="dryer",
            baseline_demand=40,
            elasticity=PriceElasticityEstimate(elasticity_value=-1, confidence=0.8,
                                             applicable_min_price=60, applicable_max_price=100),
            constraints=PriceConstraints(unit_cost=30, current_price=80),
        )
        plan = service.create_plan(tenant_id=tenant, items=(item,), correlation_id="seed", plan_id=plan_id)
        service.simulate(plan.plan_id)
        service.optimize(plan.plan_id)
        service.submit_for_approval(plan.plan_id, actor="requester", reason="synthetic review")
    audit = InMemoryAuditLog()
    app = FastAPI()
    install_error_handlers(app)

    @app.middleware("http")
    async def correlation(request, call_next):
        request.state.correlation_id = request.headers.get("x-correlation-id", "synthetic-correlation")
        return await call_next(request)

    app.include_router(create_priceops_router(repository=repository, audit_log=audit, runtime_mode="poc"))
    headers = {**auth_headers(Role.PRICING_MANAGER, subject="verified-pricer"), "x-tenant-id": "tenant-a"}
    return TestClient(app, headers=headers), service, audit, headers


def scenario(client, plan="plan-a", key="scenario-one"):
    return client.post(f"/priceops/plans/{plan}/simulate-scenario", headers={"Idempotency-Key": key},
                       json={"actor": "forged", "candidate_prices": {f"item-{plan}": 80}})


def test_plan_reads_and_commands_are_scoped_before_replay(context):
    client, service, audit, headers = context
    assert [row["plan_id"] for row in client.get("/priceops/plans").json()["items"]] == ["plan-a"]
    assert client.get("/priceops/plans?tenant_id=tenant-b").json()["items"] == []
    for suffix in ("", "/comparison"):
        assert client.get(f"/priceops/plans/plan-b{suffix}").status_code == 404
    assert scenario(client, "plan-b").status_code == 404
    response = scenario(client)
    assert response.status_code == 200
    assert client.post("/priceops/plans/plan-a/simulate-scenario", headers={**headers, "x-tenant-id": "tenant-b", "Idempotency-Key": "scenario-one"}, json={"candidate_prices": {"item-plan-a": 80}}).status_code == 404
    assert len(service.repository._scenario_simulations) == 1


def test_verified_actor_matching_ack_and_idempotency(context):
    client, service, audit, headers = context
    response = scenario(client)
    assert response.status_code == 200
    body = {"actor": "forged", "decision": "scenario_selected", "reason": "authored synthetic reason", "selected_scenario_id": response.json()["scenario_id"]}
    command_headers = {"Idempotency-Key": "decision-one", "x-correlation-id": "corr-human-command"}
    first = client.post("/priceops/plans/plan-a/decision-writeback", headers=command_headers, json=body)
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["actor"] == "verified-pricer"
    assert payload["correlation_id"] == "corr-human-command"
    assert payload["audit_event_id"]
    assert client.post("/priceops/plans/plan-a/decision-writeback", headers=command_headers, json=body).json()["decision_id"] == payload["decision_id"]
    assert client.post("/priceops/plans/plan-a/decision-writeback", headers=command_headers, json={**body, "reason": "changed"}).status_code == 409
    assert client.post("/priceops/plans/plan-a/decision-writeback", headers={**command_headers, **headers, "x-subject-id": "other-pricer"}, json=body).status_code == 409
    assert len(service.repository.list_decision_writebacks("plan-a")) == 1
    events = [event for event in audit.list_events() if event.event_type == "priceops.decision_written_back.v1"]
    assert len(events) == 1 and events[0].actor == "verified-pricer"
    assert events[0].metadata["decision_id"] == payload["decision_id"]
    assert events[0].metadata["tenant_id"] == "tenant-a"


@pytest.mark.parametrize("overrides", [{"reason": " "}, {"decision": "invented"}, {"selected_scenario_id": "missing"}])
def test_invalid_decision_never_creates_success_record(context, overrides):
    client, service, audit, _ = context
    body = {"actor": "fake", "decision": "scenario_selected", "reason": "review", "selected_scenario_id": scenario(client).json()["scenario_id"]}
    response = client.post("/priceops/plans/plan-a/decision-writeback", json={**body, **overrides})
    assert response.status_code == 422
    assert service.repository.list_decision_writebacks("plan-a") == []


def test_unrelated_item_and_read_only_roles(context):
    client, service, audit, headers = context
    unknown = client.post("/priceops/plans/plan-a/simulate-scenario", json={"candidate_prices": {"other-item": 80}})
    assert unknown.status_code == 400
    denied = client.post("/priceops/plans/plan-a/decision-writeback", headers=auth_headers(Role.MARKETING_MANAGER), json={"actor": "forged", "decision": "rejected", "reason": "review"})
    assert denied.status_code == 403
    comparison = client.get("/priceops/plans/plan-a/comparison").json()
    assert comparison["command_permissions"] == {"simulate": True, "writeback": True}
    assert comparison["actor_id"] == "verified-pricer"


def test_selected_scenario_cannot_belong_to_another_plan(context):
    client, service, audit, _ = context
    foreign = service.simulate_scenario("plan-b", {"item-plan-b": 80})
    response = client.post("/priceops/plans/plan-a/decision-writeback", json={"actor": "forged", "decision": "scenario_selected", "reason": "review", "selected_scenario_id": foreign.scenario_id})
    assert response.status_code == 422
    assert service.repository.list_decision_writebacks("plan-a") == []


@pytest.mark.parametrize("axis", ["x-brand-ids", "x-region-ids", "x-store-ids"])
def test_plan_axes_are_checked_even_when_tenant_matches(context, axis):
    client, service, audit, headers = context
    scoped = {**headers, axis: "outside-plan"}
    assert client.get("/priceops/plans", headers=scoped).json()["items"] == []
    assert client.get("/priceops/plans/plan-a/comparison", headers=scoped).status_code == 404
    assert client.post("/priceops/plans/plan-a/simulate-scenario", headers=scoped, json={"candidate_prices": {"item-plan-a": 80}}).status_code == 404
    assert service.repository._scenario_simulations == {}


def test_approval_records_are_canonical_and_not_just_status_changes(context):
    client, service, audit, _ = context
    response = client.post("/priceops/plans/plan-a/decision-writeback", json={"actor": "forged", "decision": "approved", "reason": "authored approval"})
    assert response.status_code == 200, response.text
    approval = service.repository.list_approvals("plan-a")[-1]
    assert approval.actor_id == "verified-pricer" and approval.decision_reason == "authored approval"
    assert client.get("/priceops/plans/plan-a").json()["status"] == "approved_for_pilot"
    assert len(client.get("/priceops/plans/plan-a").json()["decision_writebacks"]) == 1


def test_alternative_scenario_cannot_approve_different_optimized_prices(context):
    client, service, audit, _ = context
    candidate = scenario(client).json()["scenario_id"]
    # The retained alternative at 80 differs from the optimizer's recommended price.
    response = client.post("/priceops/plans/plan-a/decision-writeback", json={"actor": "forged", "decision": "approved", "reason": "review", "selected_scenario_id": candidate})
    assert response.status_code == 422
    assert service.repository.list_approvals("plan-a") == []
    assert service.repository.get_plan("plan-a").status.value == "pending_approval"
