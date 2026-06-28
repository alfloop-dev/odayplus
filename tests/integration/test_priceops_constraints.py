"""Integration tests for PriceOps (ODP-MOD-06).

Anchored on the module acceptance criteria:

* AC-06-01 — hard-constraint violation rate must be 0
* AC-06-02 — every plan shows expected demand / margin / risk intervals
* AC-06-03 — a pilot has an observation window and stop conditions
* AC-06-04 — negative impact beyond threshold produces a rollback recommendation
* AC-06-05 — the price treatment flows into InterventionOps and the Label Registry
"""

from __future__ import annotations

from datetime import UTC, datetime

from modules.priceops import (
    PRICEOPS_SOLVER_VERSION,
    InMemoryPriceOpsRepository,
    InvalidTransitionError,
    MissingRollbackPlanError,
    PlanRequest,
    PlanStatus,
    PriceConstraints,
    PriceElasticityEstimate,
    PriceOpsService,
    PricingPlanItem,
    run_priceops_optimizer_batch,
)
from solver.pricing import (
    STATUS_INFEASIBLE,
    STATUS_OPTIMAL,
    build_safe_action_set,
    optimize_price,
    simulate_price,
)

MOMENT = datetime(2026, 6, 28, 9, 0, tzinfo=UTC)


def _constraints(
    *,
    unit_cost: float = 3.0,
    current_price: float = 5.0,
    margin_floor_ratio: float = 0.2,
    max_increase_pct: float = 0.2,
    max_decrease_pct: float = 0.2,
    price_ladder_step: float = 0.5,
) -> PriceConstraints:
    return PriceConstraints(
        unit_cost=unit_cost,
        current_price=current_price,
        margin_floor_ratio=margin_floor_ratio,
        max_increase_pct=max_increase_pct,
        max_decrease_pct=max_decrease_pct,
        price_ladder_step=price_ladder_step,
    )


def _item(
    *,
    store_id: str = "store-001",
    constraints: PriceConstraints | None = None,
    baseline_demand: float = 1000.0,
    elasticity: float = -1.2,
    confidence: float = 0.8,
) -> PricingPlanItem:
    return PricingPlanItem.create(
        store_id=store_id,
        machine_type="washer-20kg",
        constraints=constraints or _constraints(),
        baseline_demand=baseline_demand,
        elasticity=PriceElasticityEstimate(
            elasticity_value=elasticity,
            confidence=confidence,
            prediction_origin_time=MOMENT,
        ),
    )


# -- solver-level constraint safety (AC-06-01) ----------------------------


def test_safe_action_set_only_contains_feasible_on_ladder_prices() -> None:
    constraints = _constraints()
    safe = build_safe_action_set(constraints)

    assert safe, "feasible region should be non-empty"
    assert all(constraints.is_feasible(price) for price in safe)
    # every price is on the ladder and inside the hard bounds
    assert all(abs((price / 0.5) - round(price / 0.5)) < 1e-9 for price in safe)
    assert min(safe) >= constraints.lower_bound - 1e-9
    assert max(safe) <= constraints.upper_bound + 1e-9
    # margin floor: $3 cost at 20% floor => >= $3.75
    assert all(constraints.margin_ratio(price) >= 0.2 - 1e-9 for price in safe)


def test_optimizer_never_recommends_a_constraint_violating_price() -> None:
    constraints = _constraints()
    result = optimize_price(
        constraints=constraints,
        baseline_demand=1000.0,
        elasticity=-1.2,
        confidence=0.8,
    )

    assert result.infeasible is False
    assert result.solver_status == STATUS_OPTIMAL
    assert constraints.is_feasible(result.recommended_price)
    assert constraints.violations(result.recommended_price) == []
    assert result.solver_version == PRICEOPS_SOLVER_VERSION
    # explainability fields (ODP-OR-01 §5.6)
    assert result.binding_constraints  # at least one binding bound at the optimum
    assert result.risk_level in {"low", "medium", "high"}


def test_infeasible_region_holds_current_price_with_diagnostics() -> None:
    # Unit cost so high the margin floor exceeds the max-increase ceiling.
    constraints = _constraints(unit_cost=10.0, max_increase_pct=0.1)
    result = optimize_price(constraints=constraints, baseline_demand=500.0, elasticity=-1.0)

    assert result.infeasible is True
    assert result.solver_status == STATUS_INFEASIBLE
    assert result.recommended_price == constraints.current_price
    assert result.price_changed is False
    assert result.diagnostics  # explains why no feasible price exists


# -- simulation intervals (AC-06-02) --------------------------------------


def test_simulation_exposes_ordered_demand_margin_and_risk_bands() -> None:
    simulation = simulate_price(
        price=5.5,
        baseline_demand=1000.0,
        baseline_price=5.0,
        unit_cost=3.0,
        elasticity=-1.2,
        confidence=0.7,
    )

    for band in (simulation.demand, simulation.revenue, simulation.gross_margin):
        assert band.p10 <= band.p50 <= band.p90
    # downside (P10) gross margin is the risk figure used for gating
    assert simulation.downside_gross_margin == simulation.gross_margin.p10
    assert simulation.expected_gross_margin == simulation.gross_margin.p50


def test_plan_simulation_reports_per_item_intervals() -> None:
    service = PriceOpsService()
    plan = service.create_plan(
        tenant_id="tenant-1",
        items=[_item(store_id="store-a"), _item(store_id="store-b")],
        correlation_id="corr-1",
    )

    simulation = service.simulate(plan.plan_id, generated_at=MOMENT)

    assert len(simulation.items) == 2
    for item in simulation.items:
        payload = item.to_dict()
        assert {"demand", "revenue", "gross_margin"} <= payload.keys()
        assert payload["demand"]["p10"] <= payload["demand"]["p90"]
    assert simulation.expected_gross_margin > 0


# -- full lifecycle + audit trail -----------------------------------------


def test_full_pilot_lifecycle_records_complete_status_history() -> None:
    service = PriceOpsService()
    plan = service.create_plan(
        tenant_id="tenant-1", items=[_item()], correlation_id="corr-1"
    )
    assert plan.status is PlanStatus.CANDIDATE

    simulation = service.simulate(plan.plan_id, generated_at=MOMENT)
    optimization = service.optimize(plan.plan_id, optimized_at=MOMENT)
    assert optimization.is_constraint_safe
    assert optimization.hard_constraint_violation_count == 0

    service.submit_for_approval(plan.plan_id)
    approval = service.approve(
        plan.plan_id, actor_id="ops-manager", reason="pilot approved", approved_at=MOMENT
    )
    assert approval.is_approved
    assert approval.policy_version  # decision carries policy version (§5.1)

    activation = service.activate(plan.plan_id, executor="ops-runner", executed_at=MOMENT)
    assert activation.plan.status is PlanStatus.ACTIVE

    service.start_observation(plan.plan_id, start_time=MOMENT)

    result = service.evaluate(
        plan.plan_id,
        actual_gross_margin=simulation.expected_gross_margin + 250.0,
        generated_at=MOMENT,
    )
    assert result.plan.status is PlanStatus.CONTINUE
    assert result.evaluation.rollback.recommended is False

    transitions = [
        (t.from_status, t.to_status) for t in result.plan.status_history
    ]
    assert transitions == [
        (PlanStatus.CANDIDATE, PlanStatus.SIMULATED),
        (PlanStatus.SIMULATED, PlanStatus.OPTIMIZED),
        (PlanStatus.OPTIMIZED, PlanStatus.PENDING_APPROVAL),
        (PlanStatus.PENDING_APPROVAL, PlanStatus.APPROVED_FOR_PILOT),
        (PlanStatus.APPROVED_FOR_PILOT, PlanStatus.ACTIVE),
        (PlanStatus.ACTIVE, PlanStatus.OBSERVING),
        (PlanStatus.OBSERVING, PlanStatus.EVALUATED),
        (PlanStatus.EVALUATED, PlanStatus.CONTINUE),
    ]
    # every transition is audit-bearing (§7.1)
    assert all(t.actor and t.reason and t.correlation_id for t in result.plan.status_history)


def test_rejected_plan_moves_to_stop() -> None:
    service = PriceOpsService()
    plan = service.create_plan(
        tenant_id="tenant-1", items=[_item()], correlation_id="corr-1"
    )
    service.simulate(plan.plan_id, generated_at=MOMENT)
    service.optimize(plan.plan_id, optimized_at=MOMENT)
    service.submit_for_approval(plan.plan_id)

    service.approve(
        plan.plan_id,
        actor_id="ops-manager",
        reason="margin risk too high",
        decision="rejected",
        approved_at=MOMENT,
    )

    assert service.repository.get_plan(plan.plan_id).status is PlanStatus.STOP


def test_invalid_transition_is_rejected() -> None:
    service = PriceOpsService()
    plan = service.create_plan(
        tenant_id="tenant-1", items=[_item()], correlation_id="corr-1"
    )
    # cannot approve a plan that was never simulated/optimized/submitted
    try:
        service.approve(plan.plan_id, actor_id="ops-manager", reason="skip ahead")
    except InvalidTransitionError:
        pass
    else:  # pragma: no cover - guard must fire
        raise AssertionError("expected InvalidTransitionError")


# -- observation window stop conditions (AC-06-03) ------------------------


def test_observation_window_has_stop_conditions() -> None:
    service = PriceOpsService()
    plan = service.create_plan(
        tenant_id="tenant-1", items=[_item()], correlation_id="corr-1"
    )
    service.simulate(plan.plan_id, generated_at=MOMENT)
    service.optimize(plan.plan_id, optimized_at=MOMENT)
    service.submit_for_approval(plan.plan_id)
    service.approve(plan.plan_id, actor_id="ops-manager", reason="ok", approved_at=MOMENT)
    service.activate(plan.plan_id, executor="ops-runner", executed_at=MOMENT)

    window = service.start_observation(
        plan.plan_id,
        start_time=MOMENT,
        stop_conditions={"max_gross_margin_drop_ratio": 0.03},
    )

    assert window.end_time > window.start_time
    assert window.stop_conditions["max_gross_margin_drop_ratio"] == 0.03
    # defaults are preserved alongside the override
    assert "min_observation_days" in window.stop_conditions
    assert "max_observation_days" in window.stop_conditions


# -- rollback on negative impact (AC-06-04) -------------------------------


def test_negative_impact_recommends_rollback_and_moves_to_rollback() -> None:
    service = PriceOpsService()
    plan = service.create_plan(
        tenant_id="tenant-1", items=[_item()], correlation_id="corr-1"
    )
    simulation = service.simulate(plan.plan_id, generated_at=MOMENT)
    service.optimize(plan.plan_id, optimized_at=MOMENT)
    service.submit_for_approval(plan.plan_id)
    service.approve(plan.plan_id, actor_id="ops-manager", reason="ok", approved_at=MOMENT)
    service.activate(plan.plan_id, executor="ops-runner", executed_at=MOMENT)
    service.start_observation(plan.plan_id, start_time=MOMENT)

    # realised gross margin 15% below baseline -> beyond the 5% threshold
    result = service.evaluate(
        plan.plan_id,
        actual_gross_margin=simulation.expected_gross_margin * 0.85,
        generated_at=MOMENT,
    )

    assert result.evaluation.rollback.recommended is True
    assert result.evaluation.rollback.reason_code == "negative_margin_impact"
    assert result.evaluation.impact_ratio <= -0.05
    assert result.plan.status is PlanStatus.ROLLBACK
    # outcome output carries the required maturity / evidence fields (§5.1)
    assert result.evaluation.measurement_method
    assert result.evaluation.evidence_level


# -- rollback plan exists before execution (acceptance #4 / OR-007) -------


def test_optimize_creates_rollback_plan_before_execution() -> None:
    service = PriceOpsService()
    plan = service.create_plan(
        tenant_id="tenant-1", items=[_item()], correlation_id="corr-1"
    )
    service.simulate(plan.plan_id, generated_at=MOMENT)

    # no rollback plan before optimization
    assert service.repository.get_rollback_plan(plan.plan_id) is None

    service.optimize(plan.plan_id, optimized_at=MOMENT)

    rollback_plan = service.repository.get_rollback_plan(plan.plan_id)
    assert rollback_plan is not None
    # it reverts each item to its pre-treatment price and names the trigger
    assert rollback_plan.reverts[0].revert_to_price == 5.0
    assert "negative_impact_threshold" in rollback_plan.trigger_conditions


def test_activation_requires_a_rollback_plan() -> None:
    # build a service whose repository is missing the rollback plan to prove the
    # guard fires rather than silently executing.
    repository = InMemoryPriceOpsRepository()
    service = PriceOpsService(repository=repository)
    plan = service.create_plan(
        tenant_id="tenant-1", items=[_item()], correlation_id="corr-1"
    )
    service.simulate(plan.plan_id, generated_at=MOMENT)
    service.optimize(plan.plan_id, optimized_at=MOMENT)
    service.submit_for_approval(plan.plan_id)
    service.approve(plan.plan_id, actor_id="ops-manager", reason="ok", approved_at=MOMENT)

    # drop the rollback plan to simulate the pre-execution invariant being unmet
    repository._rollback_plans.clear()
    try:
        service.activate(plan.plan_id, executor="ops-runner", executed_at=MOMENT)
    except MissingRollbackPlanError:
        pass
    else:  # pragma: no cover - guard must fire
        raise AssertionError("expected MissingRollbackPlanError")


def test_optimization_marks_high_delta_plan_as_requiring_approval() -> None:
    service = PriceOpsService()
    # wide max-increase + low confidence => a high-risk, approval-required move
    item = _item(
        constraints=_constraints(max_increase_pct=0.3),
        confidence=0.55,
    )
    plan = service.create_plan(
        tenant_id="tenant-1", items=[item], correlation_id="corr-1"
    )
    service.simulate(plan.plan_id, generated_at=MOMENT)
    optimization = service.optimize(plan.plan_id, optimized_at=MOMENT)

    assert optimization.requires_approval is True
    assert optimization.items[0].result.risk_level == "high"


# -- treatment handoff to InterventionOps + Label Registry (AC-06-05) -----


def test_activation_hands_off_treatment_to_intervention_and_label_registry() -> None:
    service = PriceOpsService()
    plan = service.create_plan(
        tenant_id="tenant-1", items=[_item()], correlation_id="corr-xyz"
    )
    service.simulate(plan.plan_id, generated_at=MOMENT)
    service.optimize(plan.plan_id, optimized_at=MOMENT)
    service.submit_for_approval(plan.plan_id)
    service.approve(plan.plan_id, actor_id="ops-manager", reason="ok", approved_at=MOMENT)

    activation = service.activate(plan.plan_id, executor="ops-runner", executed_at=MOMENT)

    # InterventionOps hand-off
    handoff = activation.handoff
    assert handoff.intervention_type == "price_adjustment"
    assert handoff.treatments, "a price change must be handed off"
    assert handoff.correlation_id == "corr-xyz"
    assert handoff.label_registry_entry_id == activation.label_entry.entry_id

    # Label Registry entry for outcome maturity
    label = activation.label_entry
    assert label.label_key == f"pricing/{plan.plan_id}"
    assert label.label_maturity_time > MOMENT
    assert label.execution_id == activation.execution.execution_id

    # the treatment records the actual price move
    treatment = handoff.treatments[0]
    assert treatment.from_price == 5.0
    assert treatment.to_price > treatment.from_price

    # repository persists both hand-offs
    assert service.repository.list_handoffs(plan.plan_id) == [handoff]
    assert service.repository.list_label_entries(plan.plan_id) == [label]


# -- batch optimizer worker -----------------------------------------------


def test_optimizer_batch_reports_zero_violations_and_is_id_addressable() -> None:
    requests = [
        PlanRequest(
            tenant_id="tenant-1",
            correlation_id="corr-1",
            items=[_item(store_id="store-a")],
        ),
        PlanRequest(
            tenant_id="tenant-1",
            correlation_id="corr-2",
            items=[_item(store_id="store-b", baseline_demand=750.0, elasticity=-0.9)],
        ),
    ]

    batch = run_priceops_optimizer_batch(
        requests=requests, job_id="priceops-job-1", optimized_at=MOMENT
    )

    assert batch.job_id == "priceops-job-1"
    assert batch.status == "succeeded"
    assert batch.hard_constraint_violation_count == 0
    assert batch.result["plan_count"] == 2
    payload = batch.to_dict()
    assert payload["job_id"] == "priceops-job-1"
    assert payload["hard_constraint_violation_count"] == 0
