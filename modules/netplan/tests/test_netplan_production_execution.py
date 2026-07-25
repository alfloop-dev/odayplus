"""Production NetPlan OSS execution contract tests."""

from __future__ import annotations

import pytest
from ortools.sat.python import cp_model

import modules.netplan.application.planning as planning
import modules.netplan.application.production as production
from modules.netplan import (
    CandidateSiteInput,
    ExistingStoreInput,
    InMemoryNetPlanRepository,
    NetPlanProductionExecutionError,
    NetPlanScenarioStatus,
    NetPlanService,
)
from solver.netplan import NetPlanConstraints


def _service() -> tuple[NetPlanService, InMemoryNetPlanRepository, str]:
    repository = InMemoryNetPlanRepository()
    service = NetPlanService(repository=repository)
    scenario = service.create_scenario(
        tenant_id="tenant-live",
        scenario_name="live network plan",
        planning_horizon="2026Q4",
        constraints=NetPlanConstraints(max_budget=500_000),
        existing_stores=(
            ExistingStoreInput(
                store_id="store-a",
                baseline_gross_margin=400_000,
                improve_gross_margin_uplift=80_000,
                improve_cost=90_000,
                source_snapshot_ids=("store-a-live",),
            ),
        ),
        candidate_sites=(
            CandidateSiteInput(
                candidate_site_id="site-b",
                expected_gross_margin=250_000,
                open_cost=180_000,
                risk_score=0.2,
                source_snapshot_ids=("site-b-live",),
            ),
        ),
        correlation_id="corr-netplan-live",
    )
    return service, repository, scenario.scenario_id


def test_production_netplan_executes_all_three_oss_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setattr(
        planning,
        "solve_network_plan",
        lambda **_kwargs: pytest.fail("legacy NetPlan fallback was called"),
    )
    calls = {"ortools": 0, "cvxpy": 0, "pymoo": 0}
    originals = (
        production.solve_robust_network_plan,
        production.solve_portfolio_frontier,
        cp_model.CpSolver.solve,
    )

    def ortools_spy(self, *args, **kwargs):
        calls["ortools"] += 1
        return originals[2](self, *args, **kwargs)

    def cvxpy_spy(**kwargs):
        calls["cvxpy"] += 1
        return originals[0](**kwargs)

    def pymoo_spy(**kwargs):
        calls["pymoo"] += 1
        kwargs.update(population_size=12, generations=4, seed=7)
        return originals[1](**kwargs)

    monkeypatch.setattr(cp_model.CpSolver, "solve", ortools_spy)
    monkeypatch.setattr(production, "solve_robust_network_plan", cvxpy_spy)
    monkeypatch.setattr(production, "solve_portfolio_frontier", pymoo_spy)
    service, _repository, scenario_id = _service()

    solve = service.solve(scenario_id)

    assert calls == {"ortools": 1, "cvxpy": 1, "pymoo": 1}
    engines = solve.execution_metadata["engines"]
    assert engines["authoritative"]["library"] == "ortools"
    assert engines["robust"]["library"] == "cvxpy"
    assert engines["frontier"]["library"] == "pymoo"
    assert engines["robust"]["selected_action_ids"]
    assert engines["frontier"]["candidates"]
    assert engines["authoritative"]["contract_version"] == solve.result.solver_version
    assert solve.execution_metadata["model_version"]
    assert solve.execution_metadata["feature_version"]
    assert solve.execution_metadata["policy_version"]
    assert solve.execution_metadata["source_snapshot_ids"] == [
        "site-b-live",
        "store-a-live",
    ]


def test_production_netplan_runtime_failure_leaves_scenario_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setattr(
        production,
        "_solve_ortools_cp_sat",
        lambda _scenario: (_ for _ in ()).throw(
            NetPlanProductionExecutionError("CP-SAT unavailable")
        ),
    )
    service, repository, scenario_id = _service()

    with pytest.raises(NetPlanProductionExecutionError):
        service.solve(scenario_id)

    assert repository.get_solve(scenario_id) is None
    assert repository.get_scenario(scenario_id).status is NetPlanScenarioStatus.DRAFT
