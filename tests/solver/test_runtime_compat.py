"""Tests for OR-Tools, CVXPY, HiGHS native runtime compatibility and process isolation."""

from __future__ import annotations

import pytest

from models.shared_ml.oss_capabilities import OssCapability, inspect_oss_capability
from solver.netplan.model import ActionOption, NetPlanConstraints, NetworkAction
from solver.netplan.optimizer import solve_network_plan
from solver.netplan.robust import (
    RobustNetPlanConstraints,
    Scenario,
    ScenarioActionOption,
    solve_robust_network_plan,
)
from solver.process_isolation import run_in_process_isolation


def _ortools_solve_worker(budget: float) -> tuple[str, float]:
    actions = (ActionOption("store_1", NetworkAction.OPEN, 100.0, 50.0, 0.1, 1),)
    constraints = NetPlanConstraints(max_budget=budget)
    res = solve_network_plan(options_by_entity={"store_1": actions}, constraints=constraints)
    return res.solver_status, res.objective_value


def _cvxpy_solve_worker(budget: float) -> tuple[str, float]:
    scenarios = (Scenario("s1", 1.0),)
    actions = (
        ScenarioActionOption(
            option_id="opt1",
            entity_id="store_1",
            action=NetworkAction.OPEN,
            scenario_values={"s1": 100.0},
            budget_cost=50.0,
            risk_score=0.1,
            capacity_delta=1,
        ),
    )
    constraints = RobustNetPlanConstraints(max_budget=budget)
    res = solve_robust_network_plan(
        options_by_entity={"store_1": actions},
        scenarios=scenarios,
        constraints=constraints,
    )
    return res.solver_status, res.objective_value


def _crashing_worker() -> None:
    raise ValueError("Simulated worker failure")


def test_process_isolation_runner_executes_solvers_without_abi_conflict() -> None:
    # Run OR-Tools in isolated process
    status1, obj1 = run_in_process_isolation(_ortools_solve_worker, 200.0)
    assert status1 in {"optimal", "feasible", "OPTIMAL", "FEASIBLE"}
    assert obj1 > -100_000.0

    # Run CVXPY / HiGHS in isolated process after OR-Tools
    status2, obj2 = run_in_process_isolation(_cvxpy_solve_worker, 200.0)
    assert status2 in {"optimal", "feasible", "OPTIMAL", "FEASIBLE"}
    assert obj2 > 0.0


def test_inspect_oss_capability_executes_real_import_and_minimal_solve() -> None:
    status = inspect_oss_capability(OssCapability.OPTIMIZATION)

    assert status.capability == OssCapability.OPTIMIZATION
    assert status.available is True
    assert "ortools" in status.packages
    assert "cvxpy" in status.packages
    assert "pyomo" in status.packages
    assert status.packages["ortools"] is not None
    assert status.packages["cvxpy"] is not None
    assert status.packages["pyomo"] is not None


def test_process_isolation_raises_error_on_process_crash() -> None:
    with pytest.raises(ValueError, match="Simulated worker failure"):
        run_in_process_isolation(_crashing_worker)
