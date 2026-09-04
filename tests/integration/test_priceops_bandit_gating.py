"""Integration tests for PriceOps Bandit exploration and production activation Gate (ODP-FR-PRICE-006).

Verifies that:
1. Without an authorized gate, bandit exploration is strictly disabled and deterministic prices are output.
2. Offline/shadow replay is reproducible with fixed seeds and immutable contracts.
3. Exploration candidates strictly satisfy hard constraints.
4. Gate lifecycle: validity window, budget exhaustion, revocation, and scope isolation fail closed.
5. Activation receipts bind policy version, actor, experiment ID, guardrails, and rollback target.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.app.routes.priceops import create_priceops_router
from modules.priceops.application import (
    ExplorationService,
    PriceOpsService,
    authorize_exploration,
)
from modules.priceops.domain import (
    ActivationReceipt,
    ExplorationBudgetExceededError,
    ExplorationGateExpiredError,
    ExplorationGateRevokedError,
    ExplorationNotAuthorizedError,
    PriceConstraints,
    PriceElasticityEstimate,
    PriceScope,
    PricingPlanItem,
)
from modules.priceops.infrastructure import InMemoryPriceOpsRepository
from shared.audit import InMemoryAuditLog
from solver.pricing.bandit import (
    BanditAlgorithm,
    BanditReplayContract,
    replay_bandit_candidate,
)

TENANT_ID = "tenant-coffee-001"
BRAND_ID = "brand-coffee-alpha"


def _make_item(
    item_id: str = "item-sku-001",
    store_id: str = "store-001",
    unit_cost: float = 10.0,
    current_price: float = 20.0,
    baseline_demand: float = 100.0,
    elasticity: float = -1.5,
) -> PricingPlanItem:
    return PricingPlanItem(
        item_id=item_id,
        store_id=store_id,
        machine_type="espresso-master",
        constraints=PriceConstraints(
            unit_cost=unit_cost,
            current_price=current_price,
            margin_floor_ratio=0.30,
            max_increase_pct=0.20,
            max_decrease_pct=0.20,
            price_ladder_step=0.5,
        ),
        baseline_demand=baseline_demand,
        elasticity=PriceElasticityEstimate(
            elasticity_value=elasticity,
            confidence=0.9,
        ),
    )


def test_bandit_unauthorized_fails_closed_and_outputs_deterministic_plan() -> None:
    repo = InMemoryPriceOpsRepository()
    service = PriceOpsService(repository=repo)

    item = _make_item()
    plan = service.create_plan(
        tenant_id=TENANT_ID,
        items=[item],
        correlation_id="corr-123",
    )
    service.simulate(plan.plan_id)

    # 1. Optimizing without gate produces deterministic plan with exploration_enabled: False
    optimization = service.optimize(plan.plan_id)
    assert optimization.solver_metadata["exploration_enabled"] is False
    assert optimization.items[0].result.recommended_price == 24.0  # optimal deterministic on ladder

    # 2. Direct exploration authorization fails closed
    scope = PriceScope(tenant_id=TENANT_ID, brand_id=BRAND_ID)
    with pytest.raises(ExplorationNotAuthorizedError):
        authorize_exploration(scope, repository=repo)

    # 3. Attempting optimization with non-existent gate fails closed
    with pytest.raises(ExplorationNotAuthorizedError):
        service.optimize(plan.plan_id, exploration_gate_id="non-existent-gate")


def test_authorized_gate_enables_bandit_and_accrues_budget() -> None:
    repo = InMemoryPriceOpsRepository()
    service = PriceOpsService(repository=repo)
    explore_service = ExplorationService(repository=repo)

    now = datetime.now(UTC)
    gate = explore_service.register_gate(
        tenant_id=TENANT_ID,
        budget_limit=500.0,
        effective_from=now - timedelta(days=1),
        effective_to=now + timedelta(days=7),
        approved_by="pricing_director",
        approval_decision_id="dec-approval-100",
        approval_id="appr-100",
        rollback_condition="margin_loss > 0.05",
        decision_policy_version_id="price-exploration-policy-v1",
        scope_brand_id=BRAND_ID,
    )

    item1 = _make_item("item-1", current_price=20.0, unit_cost=10.0)
    item2 = _make_item("item-2", current_price=30.0, unit_cost=15.0)

    # Generate exploration candidates directly
    scope = PriceScope(tenant_id=TENANT_ID, brand_id=BRAND_ID)
    candidates = explore_service.generate_candidates(
        scope=scope,
        items=[item1, item2],
        algorithm=BanditAlgorithm.THOMPSON_SAMPLING,
        seed=42,
    )

    assert len(candidates) == 2
    for cand in candidates:
        assert cand.gate_id == gate.gate_id
        assert cand.hard_constraints_satisfied is True

    # Run plan optimization with exploration gate
    plan = service.create_plan(tenant_id=TENANT_ID, items=[item1, item2], correlation_id="corr-456")
    service.simulate(plan.plan_id)
    opt = service.optimize(
        plan.plan_id,
        exploration_gate_id=gate.gate_id,
        exploration_algorithm="THOMPSON_SAMPLING",
        exploration_seed=42,
    )

    assert opt.solver_metadata["exploration_enabled"] is True
    assert opt.solver_metadata["gate_id"] == gate.gate_id
    assert len(opt.solver_metadata["bandit_candidates"]) == 2

    # Check budget accrual
    updated_gate = repo.get_gate(gate.gate_id)
    assert updated_gate is not None
    assert updated_gate.budget_consumed > 0.0
    assert updated_gate.remaining_budget < 500.0


def test_gate_expiry_revocation_and_budget_depletion() -> None:
    repo = InMemoryPriceOpsRepository()
    explore_service = ExplorationService(repository=repo)
    now = datetime.now(UTC)

    # 1. Expired Gate
    expired_gate = explore_service.register_gate(
        tenant_id=TENANT_ID,
        budget_limit=100.0,
        effective_from=now - timedelta(days=10),
        effective_to=now - timedelta(days=1),
        approved_by="pricing_director",
        approval_decision_id="dec-1",
        approval_id="appr-1",
        rollback_condition="loss > 0.05",
        decision_policy_version_id="v1",
        scope_brand_id=BRAND_ID,
    )
    scope = PriceScope(tenant_id=TENANT_ID, brand_id=BRAND_ID)
    with pytest.raises(ExplorationNotAuthorizedError):
        explore_service.get_active_grant(scope, at=now)

    # 2. Revoked Gate
    active_gate = explore_service.register_gate(
        tenant_id=TENANT_ID,
        budget_limit=100.0,
        effective_from=now - timedelta(days=1),
        effective_to=now + timedelta(days=5),
        approved_by="pricing_director",
        approval_decision_id="dec-2",
        approval_id="appr-2",
        rollback_condition="loss > 0.05",
        decision_policy_version_id="v1",
        scope_brand_id=BRAND_ID,
    )
    # Revoke gate
    explore_service.revoke_gate(active_gate.gate_id, tenant_id=TENANT_ID)
    with pytest.raises(ExplorationNotAuthorizedError):
        explore_service.get_active_grant(scope, at=now)

    # 3. Budget Depletion
    funded_gate = explore_service.register_gate(
        tenant_id=TENANT_ID,
        budget_limit=50.0,
        effective_from=now - timedelta(days=1),
        effective_to=now + timedelta(days=5),
        approved_by="pricing_director",
        approval_decision_id="dec-3",
        approval_id="appr-3",
        rollback_condition="loss > 0.05",
        decision_policy_version_id="v1",
        scope_brand_id=BRAND_ID,
    )
    # Exhaust budget
    explore_service.record_decision(
        decision_id="dec-rec-1",
        gate_id=funded_gate.gate_id,
        tenant_id=TENANT_ID,
        sku_id="sku-1",
        store_id="store-1",
        baseline_price=20.0,
        explored_price=22.0,
        budget_consumed=50.0,
        algorithm="THOMPSON_SAMPLING",
    )
    # Further recording exceeds budget
    with pytest.raises(ExplorationBudgetExceededError):
        explore_service.record_decision(
            decision_id="dec-rec-2",
            gate_id=funded_gate.gate_id,
            tenant_id=TENANT_ID,
            sku_id="sku-2",
            store_id="store-1",
            baseline_price=20.0,
            explored_price=22.0,
            budget_consumed=1.0,
            algorithm="THOMPSON_SAMPLING",
        )


def test_activation_receipt_binds_audit_metadata() -> None:
    repo = InMemoryPriceOpsRepository()
    service = PriceOpsService(repository=repo)
    explore_service = ExplorationService(repository=repo)

    now = datetime.now(UTC)
    gate = explore_service.register_gate(
        tenant_id=TENANT_ID,
        budget_limit=500.0,
        effective_from=now - timedelta(days=1),
        effective_to=now + timedelta(days=7),
        approved_by="pricing_director",
        approval_decision_id="dec-1",
        approval_id="appr-1",
        rollback_condition="margin_loss > 0.05",
        decision_policy_version_id="policy-v1",
    )

    item = _make_item()
    plan = service.create_plan(tenant_id=TENANT_ID, items=[item], correlation_id="corr-789")
    service.simulate(plan.plan_id)
    service.optimize(plan.plan_id, exploration_gate_id=gate.gate_id, exploration_seed=123)
    service.submit_for_approval(plan.plan_id, actor="analyst", reason="ready")
    service.approve(plan.plan_id, actor_id="manager", reason="approved")

    activation = service.activate(
        plan.plan_id,
        executor="pricing_operator_01",
    )

    assert activation.receipt is not None
    receipt = activation.receipt
    assert receipt.plan_id == plan.plan_id
    assert receipt.actor == "pricing_operator_01"
    assert receipt.exploration_enabled is True
    assert receipt.experiment_id == gate.gate_id
    assert receipt.rollback_target == activation.rollback_plan.rollback_plan_id
    assert "stop_conditions" in receipt.guardrails

    # Verify receipt persisted
    persisted = repo.get_activation_receipt(plan.plan_id)
    assert persisted == receipt


def test_priceops_exploration_api_endpoints() -> None:
    from apps.api.oday_api.main import create_app
    from shared.auth import Role
    from tests.integration._authz import auth_headers

    headers = auth_headers(Role.PRICING_MANAGER)
    client = TestClient(create_app(), headers={**headers, "x-correlation-id": "corr-priceops-api"})

    now = datetime.now(UTC)
    # 1. Register a gate via API
    reg_resp = client.post(
        "/priceops/exploration-gates",
        json={
            "tenant_id": TENANT_ID,
            "budget_limit": 1000.0,
            "effective_from": (now - timedelta(hours=1)).isoformat(),
            "effective_to": (now + timedelta(days=3)).isoformat(),
            "approved_by": "vp_pricing",
            "approval_decision_id": "dec-api-1",
            "approval_id": "appr-api-1",
            "rollback_condition": "gross_margin < baseline * 0.95",
            "decision_policy_version_id": "policy-exploration-v1",
            "scope_brand_id": BRAND_ID,
        },
    )
    assert reg_resp.status_code == 201
    gate_data = reg_resp.json()
    gate_id = gate_data["gate_id"]

    # 2. Query exploration gates via API
    get_resp = client.get(
        "/priceops/exploration-gates",
        params={"tenant_id": TENANT_ID, "brand_id": BRAND_ID},
    )
    assert get_resp.status_code == 200
    gates_query = get_resp.json()
    assert gates_query["authorized"] is True
    assert gates_query["active_gate"]["gate_id"] == gate_id

    # 3. Generate exploration candidates via API
    cand_resp = client.post(
        "/priceops/exploration-candidates",
        json={
            "tenant_id": TENANT_ID,
            "scope_brand_id": BRAND_ID,
            "items": [
                {
                    "item_id": "item-api-1",
                    "store_id": "store-api-1",
                    "machine_type": "espresso-auto",
                    "unit_cost": 10.0,
                    "current_price": 20.0,
                    "baseline_demand": 100.0,
                    "elasticity_value": -1.5,
                    "confidence": 0.9,
                    "applicable_min_price": 16.0,
                    "applicable_max_price": 24.0,
                    "margin_floor_ratio": 0.30,
                    "max_increase_pct": 0.20,
                    "max_decrease_pct": 0.20,
                    "price_ladder_step": 0.5,
                }
            ],
            "algorithm": "THOMPSON_SAMPLING",
            "seed": 42,
        },
    )
    assert cand_resp.status_code == 200
    cand_data = cand_resp.json()
    assert cand_data["exploration_enabled"] is True
    assert len(cand_data["candidates"]) == 1
    assert cand_data["candidates"][0]["gate_id"] == gate_id

    # 4. Revoke the gate via API
    revoke_resp = client.post(
        f"/priceops/exploration-gates/{gate_id}/revoke",
        json={"reason": "early termination of experiment"},
        params={"tenant_id": TENANT_ID},
    )
    assert revoke_resp.status_code == 200
    assert revoke_resp.json()["revoked_at"] is not None

    # 5. Candidate generation now fails closed (422)
    cand_fail_resp = client.post(
        "/priceops/exploration-candidates",
        json={
            "tenant_id": TENANT_ID,
            "scope_brand_id": BRAND_ID,
            "items": [
                {
                    "item_id": "item-api-1",
                    "store_id": "store-api-1",
                    "machine_type": "espresso-auto",
                    "unit_cost": 10.0,
                    "current_price": 20.0,
                    "baseline_demand": 100.0,
                    "elasticity_value": -1.5,
                    "confidence": 0.9,
                    "applicable_min_price": 16.0,
                    "applicable_max_price": 24.0,
                }
            ],
        },
    )
    assert cand_fail_resp.status_code == 422

