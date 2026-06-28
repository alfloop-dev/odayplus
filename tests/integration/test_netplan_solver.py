"""Integration tests for NetPlan (ODP-R5-002).

Acceptance focus:

* scenario builder produces discrete OPEN/KEEP/IMPROVE/MOVE/EXIT action domains
* solver enforces hard constraints and returns alternatives
* infeasible scenarios include structured diagnosis without auto-relaxing limits
* lifecycle records approval, execution, outcome, and status history
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from modules.netplan import (
    CandidateSiteInput,
    ExistingStoreInput,
    InMemoryNetPlanRepository,
    InvalidNetPlanTransitionError,
    NetPlanScenarioStatus,
    NetPlanService,
    ScenarioBuildRequest,
    build_scenario_options,
    run_netplan_solver_batch,
)
from solver.netplan import (
    NETPLAN_POLICY_VERSION,
    STATUS_INFEASIBLE,
    STATUS_OPTIMAL,
    NetPlanConstraints,
    NetworkAction,
    solve_network_plan,
)

MOMENT = datetime(2026, 6, 28, 9, 0, tzinfo=UTC)


def _stores() -> tuple[ExistingStoreInput, ...]:
    return (
        ExistingStoreInput(
            store_id="store-001",
            baseline_gross_margin=500_000,
            improve_gross_margin_uplift=90_000,
            improve_cost=140_000,
            move_gross_margin_uplift=120_000,
            move_cost=260_000,
            keep_risk=0.10,
            improve_risk=0.18,
            move_risk=0.34,
            source_snapshot_ids=("network-store-001",),
        ),
        ExistingStoreInput(
            store_id="store-002",
            baseline_gross_margin=350_000,
            improve_gross_margin_uplift=40_000,
            improve_cost=80_000,
            move_gross_margin_uplift=55_000,
            move_cost=180_000,
            keep_risk=0.12,
            improve_risk=0.16,
            move_risk=0.30,
            source_snapshot_ids=("network-store-002",),
        ),
    )


def _sites() -> tuple[CandidateSiteInput, ...]:
    return (
        CandidateSiteInput(
            candidate_site_id="candidate-a",
            expected_gross_margin=260_000,
            open_cost=190_000,
            risk_score=0.22,
            source_snapshot_ids=("sitescore-candidate-a",),
        ),
        CandidateSiteInput(
            candidate_site_id="candidate-b",
            expected_gross_margin=210_000,
            open_cost=150_000,
            risk_score=0.19,
            source_snapshot_ids=("sitescore-candidate-b",),
        ),
    )


def _constraints(**overrides: object) -> NetPlanConstraints:
    values = {
        "max_budget": 420_000,
        "min_expected_gross_margin": 1_100_000,
        "min_capacity_delta": 1,
        "max_average_risk": 0.22,
        "min_action_counts": {NetworkAction.OPEN: 1},
        "max_action_counts": {NetworkAction.MOVE: 1, NetworkAction.EXIT: 0},
    }
    values.update(overrides)
    return NetPlanConstraints(**values)


def test_scenario_builder_and_solver_return_optimal_plan_with_alternatives() -> None:
    options = build_scenario_options(existing_stores=_stores(), candidate_sites=_sites())
    result = solve_network_plan(options_by_entity=options, constraints=_constraints())

    assert result.solver_status == STATUS_OPTIMAL
    assert result.infeasible is False
    assert result.budget_usage <= 420_000
    assert result.expected_gross_margin >= 1_100_000
    assert result.action_counts[NetworkAction.OPEN] >= 1
    assert result.action_counts.get(NetworkAction.EXIT, 0) == 0
    assert result.alternative_plan_available is True
    assert result.alternatives[0].objective_value <= result.objective_value
    assert all(action.action in set(NetworkAction) for action in result.selected_actions)
    summary = result.to_dict()
    assert summary["alternative_plan_available"] is True
    assert {"objective_value", "budget_usage", "binding_constraints", "solver_status"} <= summary.keys()


def test_infeasible_scenario_reports_structured_diagnosis_without_relaxing() -> None:
    options = build_scenario_options(existing_stores=_stores(), candidate_sites=_sites())
    result = solve_network_plan(
        options_by_entity=options,
        constraints=_constraints(max_budget=100_000, min_expected_gross_margin=2_000_000),
    )

    assert result.solver_status == STATUS_INFEASIBLE
    assert result.infeasible is True
    assert result.selected_actions == ()
    diagnosis = [item.to_dict() for item in result.diagnostics]
    assert {item["violated_constraint"] for item in diagnosis} >= {
        "max_budget",
        "min_expected_gross_margin",
    }
    for item in diagnosis:
        assert {
            "violated_constraint",
            "affected_stores",
            "required_relaxation",
            "business_impact",
            "suggested_action",
        } <= item.keys()


def test_service_lifecycle_tracks_approval_execution_and_outcome() -> None:
    repository = InMemoryNetPlanRepository()
    service = NetPlanService(repository=repository)
    scenario = service.create_scenario(
        tenant_id="tenant-1",
        scenario_name="2026 Q3 expansion",
        planning_horizon="2026Q3",
        existing_stores=_stores(),
        candidate_sites=_sites(),
        constraints=_constraints(),
        scenario_id="netplan-scenario-001",
        correlation_id="corr-netplan-1",
        created_at=MOMENT,
    )

    solve = service.solve(scenario.scenario_id, solved_at=MOMENT)
    assert solve.result.solver_status == STATUS_OPTIMAL
    service.submit_for_approval(scenario.scenario_id, actor="network-planner", occurred_at=MOMENT)
    approval = service.decide(
        scenario.scenario_id,
        actor_id="strategy-director",
        reason="budget and risk within quarterly policy",
        decided_at=MOMENT,
    )
    assert approval.is_approved is True
    assert approval.policy_version == NETPLAN_POLICY_VERSION

    execution = service.execute(scenario.scenario_id, executed_by="ops-runner", executed_at=MOMENT)
    assert len(execution.actions) == len(solve.result.selected_actions)
    outcome = service.record_outcome(
        scenario.scenario_id,
        actual_gross_margin=solve.result.expected_gross_margin + 25_000,
        observed_at=MOMENT,
        source_snapshot_ids=("actuals-2026q3",),
        actor="network-analyst",
    )
    assert outcome.variance == 25_000
    assert outcome.label_registry_payload["label_type"] == "netplan_realized_gross_margin"

    closed = service.close(scenario.scenario_id, actor="network-analyst", occurred_at=MOMENT)
    assert closed.status is NetPlanScenarioStatus.CLOSED
    assert [transition.to_status for transition in closed.status_history] == [
        NetPlanScenarioStatus.SOLVED,
        NetPlanScenarioStatus.PENDING_APPROVAL,
        NetPlanScenarioStatus.APPROVED,
        NetPlanScenarioStatus.EXECUTED,
        NetPlanScenarioStatus.OUTCOME_OBSERVED,
        NetPlanScenarioStatus.CLOSED,
    ]
    assert all(transition.actor and transition.reason for transition in closed.status_history)


def test_infeasible_scenario_cannot_skip_to_approval() -> None:
    service = NetPlanService()
    scenario = service.create_scenario(
        tenant_id="tenant-1",
        scenario_name="impossible",
        planning_horizon="2026Q3",
        existing_stores=_stores(),
        candidate_sites=_sites(),
        constraints=_constraints(max_budget=1),
        scenario_id="netplan-scenario-infeasible",
        correlation_id="corr-netplan-bad",
    )
    solve = service.solve(scenario.scenario_id)
    assert solve.result.infeasible is True

    with pytest.raises(InvalidNetPlanTransitionError):
        service.submit_for_approval(scenario.scenario_id)


def test_batch_worker_solves_multiple_scenarios_and_persists_results() -> None:
    repository = InMemoryNetPlanRepository()
    result = run_netplan_solver_batch(
        requests=[
            ScenarioBuildRequest(
                tenant_id="tenant-1",
                scenario_name="batch solve",
                planning_horizon="2026Q3",
                existing_stores=_stores(),
                candidate_sites=_sites(),
                constraints=_constraints(),
                scenario_id="netplan-batch-001",
                correlation_id="corr-batch-1",
            )
        ],
        job_id="netplan-job-1",
        solved_at=MOMENT,
        repository=repository,
    )

    assert result.status == "succeeded"
    assert result.to_dict()["scenarios"][0]["solve"]["result"]["solver_status"] == STATUS_OPTIMAL
    assert repository.get_scenario("netplan-batch-001").status is NetPlanScenarioStatus.SOLVED
