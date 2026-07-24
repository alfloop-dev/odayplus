from solver.netplan.model import NetworkAction
from solver.netplan.robust import (
    RobustNetPlanConstraints,
    RobustObjective,
    Scenario,
    ScenarioActionOption,
    solve_robust_network_plan,
)


def _scenarios() -> tuple[Scenario, ...]:
    return (
        Scenario("DOWNSIDE", 0.2),
        Scenario("BASE", 0.5),
        Scenario("UPSIDE", 0.3),
    )


def _options() -> dict[str, tuple[ScenarioActionOption, ...]]:
    return {
        "store-a": (
            ScenarioActionOption(
                "safe",
                "store-a",
                NetworkAction.KEEP,
                {"DOWNSIDE": 80, "BASE": 80, "UPSIDE": 80},
                0,
                0.05,
            ),
            ScenarioActionOption(
                "risky",
                "store-a",
                NetworkAction.IMPROVE,
                {"DOWNSIDE": 20, "BASE": 140, "UPSIDE": 220},
                50,
                0.2,
                1,
            ),
        ),
    }


def test_cvxpy_weighted_and_max_min_objectives_choose_different_actions() -> None:
    weighted = solve_robust_network_plan(
        options_by_entity=_options(),
        scenarios=_scenarios(),
        constraints=RobustNetPlanConstraints(max_budget=50),
        objective=RobustObjective.WEIGHTED_EXPECTED,
        downside_weight=0,
    )
    max_min = solve_robust_network_plan(
        options_by_entity=_options(),
        scenarios=_scenarios(),
        constraints=RobustNetPlanConstraints(max_budget=50),
        objective=RobustObjective.MAX_MIN,
    )

    assert weighted.solver_status in {"OPTIMAL", "FEASIBLE"}
    assert [item.option_id for item in weighted.selected_actions] == ["risky"]
    assert weighted.scenario_values == {
        "DOWNSIDE": 20,
        "BASE": 140,
        "UPSIDE": 220,
    }
    assert weighted.expected_value == 140
    assert weighted.downside_risk == 120
    assert max_min.solver_status in {"OPTIMAL", "FEASIBLE"}
    assert [item.option_id for item in max_min.selected_actions] == ["safe"]
    assert max_min.downside_value == 80
    assert max_min.constraint_evaluation["max_budget"]["satisfied"] is True


def test_cvxpy_cvar_contract_controls_lower_tail() -> None:
    result = solve_robust_network_plan(
        options_by_entity=_options(),
        scenarios=_scenarios(),
        constraints=RobustNetPlanConstraints(max_budget=50),
        objective=RobustObjective.CVAR,
        downside_weight=1,
        cvar_confidence=0.8,
    )

    assert result.solver_status in {"OPTIMAL", "FEASIBLE"}
    assert [item.option_id for item in result.selected_actions] == ["safe"]
    assert result.cvar_value == 80
    assert result.objective_value == 80


def test_cvxpy_infeasible_scenario_floor_has_diagnostics() -> None:
    result = solve_robust_network_plan(
        options_by_entity=_options(),
        scenarios=_scenarios(),
        constraints=RobustNetPlanConstraints(
            max_budget=50,
            min_value_by_scenario={"DOWNSIDE": 100},
        ),
    )

    assert result.solver_status == "INFEASIBLE"
    assert result.selected_actions == ()
    assert result.diagnostics[0].constraint == "min_value_by_scenario.DOWNSIDE"


def test_missing_cvxpy_fails_closed(monkeypatch) -> None:
    import solver.netplan.robust as robust

    monkeypatch.setattr(robust, "_load_cvxpy", lambda: None)
    result = robust.solve_robust_network_plan(
        options_by_entity=_options(),
        scenarios=_scenarios(),
        constraints=RobustNetPlanConstraints(max_budget=50),
    )

    assert result.solver_status == "SOLVER_UNAVAILABLE"
    assert result.selected_actions == ()
    assert result.diagnostics[0].code == "SOLVER_UNAVAILABLE"


def test_missing_mixed_integer_backend_fails_closed(monkeypatch) -> None:
    import solver.netplan.robust as robust

    class ContinuousOnlyCvxpy:
        @staticmethod
        def installed_solvers() -> list[str]:
            return ["CLARABEL", "SCS"]

    monkeypatch.setattr(robust, "_load_cvxpy", lambda: ContinuousOnlyCvxpy())
    result = robust.solve_robust_network_plan(
        options_by_entity=_options(),
        scenarios=_scenarios(),
        constraints=RobustNetPlanConstraints(max_budget=50),
    )

    assert result.solver_status == "SOLVER_UNAVAILABLE"
    assert result.selected_actions == ()
    assert result.diagnostics[0].code == "MIP_SOLVER_UNAVAILABLE"
