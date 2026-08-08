from __future__ import annotations

from datetime import UTC, datetime
import pytest

from modules.priceops.application import (
    PriceOpsService,
    UnavailableSimulationResultError,
)
from modules.priceops.domain import (
    InvalidScenarioError,
    PriceConstraints,
    PriceElasticityEstimate,
    PricingPlan,
    PricingPlanItem,
    simulate_candidate_scenario,
    validate_pricing_scenario,
)


def _sample_item(
    *,
    item_id: str = "item-1",
    store_id: str = "store-1",
    current_price: float = 100.0,
    unit_cost: float = 50.0,
    baseline_demand: float = 10.0,
    confidence: float = 0.8,
    max_increase_pct: float = 0.15,
) -> PricingPlanItem:
    return PricingPlanItem.create(
        item_id=item_id,
        store_id=store_id,
        machine_type="washer-20kg",
        constraints=PriceConstraints(
            unit_cost=unit_cost,
            current_price=current_price,
            margin_floor_ratio=0.15,
            max_increase_pct=max_increase_pct,
            max_decrease_pct=0.15,
            price_ladder_step=5.0,
            min_price=10.0,
            max_price=200.0,
        ),
        baseline_demand=baseline_demand,
        elasticity=PriceElasticityEstimate(
            elasticity_value=-1.2,
            confidence=confidence,
        ),
    )


def test_invalid_scenario_execution_blocked() -> None:
    # 1. Invalid current price
    invalid_item_price = _sample_item(current_price=-10.0)
    with pytest.raises(InvalidScenarioError, match="invalid current_price"):
        validate_pricing_scenario(invalid_item_price)

    # 2. Invalid unit cost
    invalid_item_cost = _sample_item(unit_cost=-5.0)
    with pytest.raises(InvalidScenarioError, match="invalid unit_cost"):
        validate_pricing_scenario(invalid_item_cost)

    # 3. Invalid baseline demand
    invalid_item_demand = _sample_item(baseline_demand=-1.0)
    with pytest.raises(InvalidScenarioError, match="invalid baseline_demand"):
        validate_pricing_scenario(invalid_item_demand)

    # 4. Invalid confidence
    invalid_item_confidence = _sample_item(confidence=1.5)
    with pytest.raises(InvalidScenarioError, match="invalid elasticity confidence"):
        validate_pricing_scenario(invalid_item_confidence)

    # 5. Invalid candidate price (<= 0)
    valid_item = _sample_item()
    with pytest.raises(InvalidScenarioError, match="invalid candidate_price"):
        validate_pricing_scenario(valid_item, candidate_price=-20.0)


def test_baseline_and_alternatives_stay_distinguishable() -> None:
    item = _sample_item(current_price=100.0)
    plan = PricingPlan.create(
        tenant_id="tenant-tw",
        items=(item,),
        correlation_id="corr-scen-1",
        plan_id="plan-distinguishable",
    )

    scenario_sim = simulate_candidate_scenario(
        plan,
        candidate_prices={item.item_id: 110.0},
        scenario_id="scen-110",
    )

    assert scenario_sim.is_baseline_distinct is True
    assert scenario_sim.total_baseline_gross_margin > 0
    assert scenario_sim.total_candidate_gross_margin > 0

    item_sim = scenario_sim.items[0]
    assert item_sim.baseline_price == 100.0
    assert item_sim.candidate_price == 110.0
    assert item_sim.baseline_simulation.price == 100.0
    assert item_sim.candidate_simulation.price == 110.0
    assert item_sim.is_baseline_distinct is True

    # P10/P50/P90 bands exist on both baseline and candidate
    assert item_sim.baseline_simulation.demand.p10 <= item_sim.baseline_simulation.demand.p50 <= item_sim.baseline_simulation.demand.p90
    assert item_sim.candidate_simulation.demand.p10 <= item_sim.candidate_simulation.demand.p50 <= item_sim.candidate_simulation.demand.p90


def test_unavailable_results_fail_closed() -> None:
    service = PriceOpsService()
    item = _sample_item()
    plan = service.create_plan(
        tenant_id="tenant-tw",
        items=(item,),
        correlation_id="corr-fail-closed",
        plan_id="plan-no-opt",
    )

    # Decision writeback without optimization / simulation fails closed
    with pytest.raises(UnavailableSimulationResultError, match="cannot perform decision writeback"):
        service.writeback_decision(
            plan.plan_id,
            actor="pricing-officer",
            decision="approved",
            reason="attempt approve without optimization",
        )


def test_decision_writeback_idempotent_and_audited() -> None:
    service = PriceOpsService()
    item = _sample_item()
    plan = service.create_plan(
        tenant_id="tenant-tw",
        items=(item,),
        correlation_id="corr-idempotent-wb",
        plan_id="plan-wb-1",
    )
    service.simulate(plan.plan_id)
    service.optimize(plan.plan_id)

    key = "idem-key-writeback-1001"
    first = service.writeback_decision(
        plan.plan_id,
        actor="pricing-officer",
        decision="approved",
        reason="approved after scenario evaluation",
        idempotency_key=key,
    )

    assert first.decision == "approved"
    assert first.actor == "pricing-officer"
    assert first.idempotency_key == key
    assert first.policy_version == "brand-pricing-policy-v1"

    # Second call with same idempotency key returns exact same record without error or duplicate transition
    second = service.writeback_decision(
        plan.plan_id,
        actor="pricing-officer",
        decision="approved",
        reason="approved after scenario evaluation",
        idempotency_key=key,
    )

    assert second.decision_id == first.decision_id
    assert second.written_back_at == first.written_back_at
