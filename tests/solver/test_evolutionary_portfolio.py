from solver.evolutionary import EvolutionaryPortfolioOption, solve_portfolio_frontier


def test_nsga2_returns_only_budget_feasible_pareto_candidates() -> None:
    options = (
        EvolutionaryPortfolioOption("open-a", 120.0, 70.0, 0.4),
        EvolutionaryPortfolioOption("improve-b", 80.0, 30.0, 0.1),
        EvolutionaryPortfolioOption("move-c", 150.0, 100.0, 0.2),
        EvolutionaryPortfolioOption("exit-d", 30.0, 10.0, 0.02),
    )

    result = solve_portfolio_frontier(
        options=options,
        max_budget=110.0,
        min_selected=1,
        max_selected=3,
        population_size=40,
        generations=40,
        seed=11,
    )

    assert result.status == "optimal_frontier"
    assert len(result.candidates) >= 2
    assert all(candidate.budget_cost <= 110.0 for candidate in result.candidates)
    assert all(1 <= len(candidate.option_ids) <= 3 for candidate in result.candidates)
    assert any(candidate.expected_gross_margin >= 180.0 for candidate in result.candidates)
