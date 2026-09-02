from __future__ import annotations

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


def test_elasticity_fit_carries_applicable_range() -> None:
    from solver.pricing.demand import estimate_elasticity

    obs = [(10.0, 100.0), (12.0, 80.0), (14.0, 65.0), (16.0, 50.0), (18.0, 40.0)]
    fit = estimate_elasticity(obs)

    assert fit.applicable_min_price == 10.0
    assert fit.applicable_max_price == 18.0
    assert fit.sample_size == 5
    assert fit.to_dict()["applicable_min_price"] == 10.0
    assert fit.to_dict()["applicable_max_price"] == 18.0


def test_simulation_detects_extrapolation() -> None:
    from solver.pricing.demand import simulate_price

    # Within applicable range
    sim_in = simulate_price(
        price=15.0,
        baseline_demand=100.0,
        baseline_price=15.0,
        unit_cost=8.0,
        elasticity=-1.2,
        applicable_min_price=10.0,
        applicable_max_price=20.0,
    )
    assert sim_in.is_extrapolated is False
    assert sim_in.applicable_min_price == 10.0
    assert sim_in.applicable_max_price == 20.0

    # Below applicable range
    sim_below = simulate_price(
        price=8.0,
        baseline_demand=100.0,
        baseline_price=15.0,
        unit_cost=5.0,
        elasticity=-1.2,
        applicable_min_price=10.0,
        applicable_max_price=20.0,
    )
    assert sim_below.is_extrapolated is True

    # Above applicable range
    sim_above = simulate_price(
        price=25.0,
        baseline_demand=100.0,
        baseline_price=15.0,
        unit_cost=8.0,
        elasticity=-1.2,
        applicable_min_price=10.0,
        applicable_max_price=20.0,
    )
    assert sim_above.is_extrapolated is True


def test_constraints_enforce_applicable_range_as_hard_bound() -> None:
    from solver.pricing.constraints import (
        VIOLATION_ABOVE_APPLICABLE_RANGE,
        VIOLATION_BELOW_APPLICABLE_RANGE,
        PriceConstraints,
    )
    from solver.pricing.optimizer import build_safe_action_set

    # Current price 100, ladder 5, applicable range [95, 105]
    constraints = PriceConstraints(
        unit_cost=50.0,
        current_price=100.0,
        max_increase_pct=0.20,  # normally allowed up to 120
        max_decrease_pct=0.20,  # normally allowed down to 80
        price_ladder_step=5.0,
        applicable_min_price=95.0,
        applicable_max_price=105.0,
    )

    assert constraints.lower_bound == 95.0
    assert constraints.upper_bound == 105.0

    safe = build_safe_action_set(constraints)
    assert safe == [95.0, 100.0, 105.0]

    # Out of range price violations
    v_below = constraints.violations(90.0)
    assert any(v.code == VIOLATION_BELOW_APPLICABLE_RANGE for v in v_below)

    v_above = constraints.violations(110.0)
    assert any(v.code == VIOLATION_ABOVE_APPLICABLE_RANGE for v in v_above)

    # Binding constraints identification
    assert "applicable_max_price_ceiling" in constraints.binding_constraints(105.0)
    assert "applicable_min_price_floor" in constraints.binding_constraints(95.0)


def test_optimizer_refuses_extrapolation_outside_fitted_support() -> None:
    from solver.pricing.constraints import PriceConstraints
    from solver.pricing.optimizer import STATUS_INFEASIBLE, STATUS_OPTIMAL, optimize_price

    # Case 1: Optimizer picks optimal within support boundary
    c_within = PriceConstraints(
        unit_cost=50.0,
        current_price=100.0,
        max_increase_pct=0.50,
        max_decrease_pct=0.50,
        price_ladder_step=5.0,
        applicable_min_price=90.0,
        applicable_max_price=110.0,
    )
    res_within = optimize_price(
        constraints=c_within,
        baseline_demand=100.0,
        elasticity=-1.5,
    )
    assert res_within.solver_status == STATUS_OPTIMAL
    assert 90.0 <= res_within.recommended_price <= 110.0

    # Case 2: Candidate outside support is completely infeasible (e.g. current price is 200, but support is [90, 110])
    c_outside = PriceConstraints(
        unit_cost=50.0,
        current_price=200.0,
        max_increase_pct=0.15,
        max_decrease_pct=0.15,
        price_ladder_step=5.0,
        applicable_min_price=90.0,
        applicable_max_price=110.0,
    )
    res_outside = optimize_price(
        constraints=c_outside,
        baseline_demand=100.0,
        elasticity=-1.5,
    )
    assert res_outside.infeasible is True
    assert res_outside.solver_status == STATUS_INFEASIBLE
    assert res_outside.recommended_price == 200.0
    assert any("applicable max price is below lower bound" in d for d in res_outside.diagnostics)


def test_pricing_policy_governs_constraints() -> None:
    from solver.pricing.constraints import PriceConstraints, default_pricing_policy

    policy = default_pricing_policy("tenant-test")
    assert policy.policy_id == "brand-pricing-policy"
    assert policy.policy_version == "1.0.0"

    constraints = PriceConstraints.from_policy(
        policy,
        unit_cost=40.0,
        current_price=100.0,
        applicable_min_price=80.0,
        applicable_max_price=120.0,
    )
    assert constraints.applicable_min_price == 80.0
    assert constraints.applicable_max_price == 120.0
    assert constraints.margin_floor_ratio == 0.15
    assert constraints.policy_version == "brand-pricing-policy-v1"


def test_invalid_applicable_range_bounds_raise_scenario_error() -> None:
    invalid_bounds_item = PricingPlanItem.create(
        item_id="item-inv-bounds",
        store_id="store-1",
        machine_type="washer",
        constraints=PriceConstraints(
            unit_cost=50.0,
            current_price=100.0,
            applicable_min_price=120.0,
            applicable_max_price=90.0,  # min > max
        ),
        baseline_demand=10.0,
        elasticity=PriceElasticityEstimate(
            elasticity_value=-1.2,
            confidence=0.8,
        ),
    )
    with pytest.raises(InvalidScenarioError, match="invalid applicable bounds"):
        validate_pricing_scenario(invalid_bounds_item)


def test_scenario_simulation_with_applicable_range_violation() -> None:
    item = PricingPlanItem.create(
        item_id="item-app-range",
        store_id="store-1",
        machine_type="washer",
        constraints=PriceConstraints(
            unit_cost=50.0,
            current_price=100.0,
            price_ladder_step=5.0,
            applicable_min_price=90.0,
            applicable_max_price=110.0,
        ),
        baseline_demand=100.0,
        elasticity=PriceElasticityEstimate(
            elasticity_value=-1.2,
            confidence=0.8,
            applicable_min_price=90.0,
            applicable_max_price=110.0,
        ),
    )
    plan = PricingPlan.create(
        tenant_id="tenant-test",
        items=(item,),
        correlation_id="corr-test",
    )

    # Candidate price of 200 is outside [90, 110]
    sim = simulate_candidate_scenario(
        plan,
        candidate_prices={item.item_id: 200.0},
    )
    assert sim.is_feasible is False
    assert sim.hard_constraint_violation_count > 0
    assert any(
        v.code == "above_applicable_range" for v in sim.items[0].constraint_violations
    )


def test_oss_optimizer_simulate_threads_applicable_range_and_detects_extrapolation() -> None:
    from modules.priceops.infrastructure.oss_optimizer import _simulate

    item = PricingPlanItem.create(
        item_id="item-oss-extrap",
        store_id="store-1",
        machine_type="washer",
        constraints=PriceConstraints(
            unit_cost=50.0,
            current_price=100.0,
            price_ladder_step=5.0,
            applicable_min_price=90.0,
            applicable_max_price=110.0,
        ),
        baseline_demand=100.0,
        elasticity=PriceElasticityEstimate(
            elasticity_value=-1.2,
            confidence=0.8,
            applicable_min_price=90.0,
            applicable_max_price=110.0,
        ),
    )

    # Within applicable range
    sim_in = _simulate(item, 100.0)
    assert sim_in.is_extrapolated is False
    assert sim_in.applicable_min_price == 90.0
    assert sim_in.applicable_max_price == 110.0

    # Outside applicable range - above
    sim_above = _simulate(item, 120.0)
    assert sim_above.is_extrapolated is True
    assert sim_above.applicable_min_price == 90.0
    assert sim_above.applicable_max_price == 110.0

    # Outside applicable range - below
    sim_below = _simulate(item, 80.0)
    assert sim_below.is_extrapolated is True
    assert sim_below.applicable_min_price == 90.0
    assert sim_below.applicable_max_price == 110.0


def test_oss_optimizer_production_execution_carries_applicable_range() -> None:
    from modules.priceops.infrastructure.oss_optimizer import PriceOpsProductionOptimizer

    item = PricingPlanItem.create(
        item_id="item-oss-prod",
        store_id="store-1",
        machine_type="washer",
        constraints=PriceConstraints(
            unit_cost=50.0,
            current_price=100.0,
            price_ladder_step=5.0,
            max_increase_pct=0.10,
            max_decrease_pct=0.10,
            applicable_min_price=90.0,
            applicable_max_price=110.0,
        ),
        baseline_demand=100.0,
        elasticity=PriceElasticityEstimate(
            elasticity_value=-1.2,
            confidence=0.8,
            applicable_min_price=90.0,
            applicable_max_price=110.0,
        ),
        source_snapshot_ids=("snap-1",),
    )
    plan = PricingPlan.create(
        tenant_id="tenant-test",
        items=(item,),
        correlation_id="corr-test",
    )

    optimizer = PriceOpsProductionOptimizer()
    execution = optimizer.optimize(plan)

    assert len(execution.results) == 1
    _item, result = execution.results[0]
    assert result.baseline_simulation.applicable_min_price == 90.0
    assert result.baseline_simulation.applicable_max_price == 110.0
    assert result.baseline_simulation.is_extrapolated is False
    assert result.recommended_simulation.applicable_min_price == 90.0
    assert result.recommended_simulation.applicable_max_price == 110.0
    assert result.recommended_simulation.is_extrapolated is False
