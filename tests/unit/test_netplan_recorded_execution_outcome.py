"""Synthetic native owner controls; not approval/physical execution evidence."""
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from modules.netplan.application.planning import NetPlanApprovalError, NetPlanService
from modules.netplan.domain.planning import (
    ExecutionRecord,
    NetPlanScenarioStatus,
    ScenarioSolveRecord,
)
from modules.netplan.infrastructure.repositories import InMemoryNetPlanRepository
from solver.netplan import (
    NetPlanConstraints,
    NetworkAction,
    NetworkPlanSolveResult,
    build_feasible_candidates,
)

STAMP = datetime(2026, 10, 15, tzinfo=UTC)


def executed_owner(*, alternative=False, baseline=100, uplift=30, force_keep=False):
    repository = InMemoryNetPlanRepository()
    service = NetPlanService(repository=repository, runtime_mode="poc")
    scenario = service.create_scenario(tenant_id="synthetic-tenant", scenario_name="Recorded execution control",
        planning_horizon="2026-H2", scenario_id="synthetic-executed", constraints=NetPlanConstraints(max_budget=100,
            min_action_counts={NetworkAction.KEEP: 1} if force_keep else {}),
        existing_stores=[{"store_id": "synthetic-store", "baseline_gross_margin": baseline,
                          "improve_gross_margin_uplift": uplift, "improve_cost": 10}],
        correlation_id="synthetic-recorded-outcome-fixture")
    if force_keep:
        # These two controls exercise native outcome arithmetic, not SCIP's
        # representable objective range. Build an explicit synthetic execution
        # from the unchanged exact feasible-candidate enumerator so the numeric
        # precondition cannot silently become an infeasible empty result.
        candidates = build_feasible_candidates(options_by_entity=scenario.options_by_entity,
            constraints=scenario.constraints, risk_penalty=0)
        assert len(candidates) == 1
        candidate = candidates[0]
        assert len(candidate.actions) == 1
        assert candidate.actions[0].action == NetworkAction.KEEP
        assert candidate.expected_gross_margin == baseline
        solve = repository.save_solve(ScenarioSolveRecord(scenario_id=scenario.scenario_id,
            result=NetworkPlanSolveResult(solver_status="optimal", objective_value=candidate.objective_value,
                selected_actions=candidate.actions, expected_gross_margin=candidate.expected_gross_margin,
                budget_usage=candidate.budget_usage, average_risk=candidate.average_risk,
                capacity_delta=candidate.capacity_delta, action_counts=candidate.action_counts,
                binding_constraints=candidate.binding_constraints,
                modelled_constraint_classes=candidate.modelled_constraint_classes,
                unmodelled_constraint_classes=candidate.unmodelled_constraint_classes),
            solved_at=STAMP))
    else:
        solve = service.solve(scenario.scenario_id)
    solved = repository.get_scenario(scenario.scenario_id)
    subject = solve.result.alternatives[0] if alternative else solve.result
    identity = scenario.scenario_id + ":alternative:1" if alternative else scenario.scenario_id
    # Deliberate synthetic durable-owner state for outcome computation only;
    # no claim that these direct fixture writes pass an approval/execute route.
    repository.save_scenario(replace(solved, status=NetPlanScenarioStatus.EXECUTED, selected_candidate_id=identity))
    actions = subject.actions if alternative else subject.selected_actions
    repository.save_execution(ExecutionRecord("synthetic-execution", scenario.scenario_id,
        tuple(actions), "synthetic-recorder", STAMP))
    return service, repository, scenario.scenario_id, solve, tuple(actions)


@pytest.mark.parametrize("alternative", [False, True])
def test_outcome_compares_actual_against_the_recorded_selected_execution_actions(alternative):
    service, repository, identity, solve, actions = executed_owner(alternative=alternative)
    expected = round(sum(action.expected_gross_margin for action in actions), 4)
    if alternative:
        assert expected != solve.result.expected_gross_margin
    outcome = service.record_outcome(identity, actual_gross_margin=expected + 10,
        actor="synthetic-recorder", observed_at=STAMP, source_snapshot_ids=("manual-owned-record",))
    assert outcome.expected_gross_margin == expected
    assert outcome.actual_gross_margin == expected + 10
    assert outcome.variance == 10
    assert outcome.source_snapshot_ids == ("manual-owned-record",)
    assert outcome.label_registry_payload["expected_gross_margin"] == expected
    assert repository.get_execution(identity).actions == actions
    assert repository.get_scenario(identity).status == NetPlanScenarioStatus.OUTCOME_OBSERVED


def test_outcome_refuses_execution_actions_from_a_different_selected_alternative_without_mutating():
    service, repository, identity, solve, _ = executed_owner(alternative=True)
    original = repository.get_scenario(identity)
    execution = repository.get_execution(identity)
    repository.save_execution(replace(execution, actions=tuple(solve.result.selected_actions)))
    with pytest.raises(NetPlanApprovalError, match="recorded execution"):
        service.record_outcome(identity, actual_gross_margin=42, actor="synthetic-recorder")
    assert repository.get_scenario(identity) == original
    assert repository.get_outcome(identity) is None


@pytest.mark.parametrize("amount", [float("nan"), float("inf"), float("-inf"), True])
def test_nonfinite_or_boolean_native_outcome_never_advances_or_saves(amount):
    service, repository, identity, _, _ = executed_owner()
    before = repository.get_scenario(identity)
    with pytest.raises(ValueError, match="must be finite"):
        service.record_outcome(identity, actual_gross_margin=amount)
    assert repository.get_scenario(identity) == before
    assert repository.get_outcome(identity) is None


@pytest.mark.parametrize(("baseline", "actual"), [(0.0001, 1e308), (-1e308, 1e308)])
def test_finite_manual_inputs_that_overflow_the_comparison_never_persist_or_advance(baseline, actual):
    service, repository, identity, _, _ = executed_owner(baseline=baseline, uplift=0, force_keep=True)
    before = repository.get_scenario(identity)
    execution = repository.get_execution(identity)
    with pytest.raises(ValueError, match="comparisons must be finite"):
        service.record_outcome(identity, actual_gross_margin=actual, actor="synthetic-recorder")
    assert repository.get_scenario(identity) == before
    assert repository.get_execution(identity) == execution
    assert repository.get_outcome(identity) is None
