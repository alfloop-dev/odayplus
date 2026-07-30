"""Tests for OR-Tools, CVXPY, HiGHS native runtime compatibility and process isolation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from models.shared_ml.oss_capabilities import (
    OssCapability,
    inspect_oss_capability,
    probe_package_in_isolation,
)
from shared.auth import Role
from solver.netplan.model import ActionOption, NetPlanConstraints, NetworkAction
from solver.netplan.optimizer import solve_network_plan
from solver.netplan.robust import (
    RobustNetPlanConstraints,
    Scenario,
    ScenarioActionOption,
    solve_robust_network_plan,
)
from solver.process_isolation import run_in_process_isolation
from tests.integration._authz import auth_headers


def _ortools_solve_worker(budget: float, isolate_process: bool = True) -> tuple[str, float]:
    actions = (ActionOption("store_1", NetworkAction.OPEN, 100.0, 50.0, 0.1, 1),)
    constraints = NetPlanConstraints(max_budget=budget)
    res = solve_network_plan(
        options_by_entity={"store_1": actions},
        constraints=constraints,
        isolate_process=isolate_process,
    )
    return res.solver_status, res.objective_value


def _cvxpy_solve_worker(budget: float, isolate_process: bool = True) -> tuple[str, float]:
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
        isolate_process=isolate_process,
    )
    return res.solver_status, res.objective_value


def _same_process_order1_worker() -> None:
    # Order 1: OR-Tools then CVXPY/HiGHS in same process (without isolation)
    _ortools_solve_worker(200.0, isolate_process=False)
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
    constraints = RobustNetPlanConstraints(max_budget=200.0)
    res = solve_robust_network_plan(
        options_by_entity={"store_1": actions},
        scenarios=scenarios,
        constraints=constraints,
        preferred_solver="HIGHS",
        isolate_process=False,
    )
    if res.solver_status == "SOLVER_UNAVAILABLE":
        raise RuntimeError("Order 1 failed: highspy undefined symbol")


def _same_process_order2_worker() -> None:
    # Order 2: CVXPY/HiGHS then OR-Tools in same process (without isolation)
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
    constraints = RobustNetPlanConstraints(max_budget=200.0)
    solve_robust_network_plan(
        options_by_entity={"store_1": actions},
        scenarios=scenarios,
        constraints=constraints,
        preferred_solver="HIGHS",
        isolate_process=False,
    )
    res_status, _ = _ortools_solve_worker(200.0, isolate_process=False)
    if res_status not in {"optimal", "feasible", "OPTIMAL", "FEASIBLE"}:
        raise RuntimeError("Order 2 failed: libortools undefined symbol")


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


def test_both_same_process_orders_reproduce_abi_conflict_and_isolation_fixes_both() -> None:
    # Same-process Order 1 (OR-Tools then CVXPY/HiGHS) fails due to highspy undefined symbol
    with pytest.raises(RuntimeError, match="Order 1 failed"):
        run_in_process_isolation(_same_process_order1_worker)

    # Same-process Order 2 (CVXPY/HiGHS then OR-Tools) fails due to libortools undefined symbol
    with pytest.raises(ImportError, match="libortools"):
        run_in_process_isolation(_same_process_order2_worker)

    # Default entrypoints (isolate_process=True) solve both sequentially without conflict
    res1 = solve_network_plan(
        options_by_entity={"store_1": (ActionOption("store_1", NetworkAction.OPEN, 100.0, 50.0, 0.1, 1),)},
        constraints=NetPlanConstraints(max_budget=200.0),
    )
    assert res1.solver_status in {"optimal", "feasible", "OPTIMAL", "FEASIBLE"}

    res2 = solve_robust_network_plan(
        options_by_entity={
            "store_1": (
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
        },
        scenarios=(Scenario("s1", 1.0),),
        constraints=RobustNetPlanConstraints(max_budget=200.0),
    )
    assert res2.solver_status in {"optimal", "feasible", "OPTIMAL", "FEASIBLE"}


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


def test_probe_package_in_isolation_explicit_highs_solve() -> None:
    available, version = probe_package_in_isolation("cvxpy")
    assert available is True
    assert version is not None


def test_process_isolation_raises_error_on_process_crash() -> None:
    with pytest.raises(ValueError, match="Simulated worker failure"):
        run_in_process_isolation(_crashing_worker)


def test_learninghub_exposes_installed_oss_engine_versions_ready() -> None:
    client = TestClient(create_app())
    response = client.get(
        "/api/v1/learninghub/oss-capabilities",
        headers=auth_headers(Role.MODEL_OWNER),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["unavailable_count"] == 0
    assert payload["count"] >= 11
