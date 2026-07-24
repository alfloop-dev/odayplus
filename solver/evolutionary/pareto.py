"""pymoo NSGA-II portfolio frontier for capital, gross margin, and risk."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from models.shared_ml.oss_capabilities import OssCapability, require_oss_capability


@dataclass(frozen=True)
class EvolutionaryPortfolioOption:
    option_id: str
    expected_gross_margin: float
    budget_cost: float
    risk_score: float


@dataclass(frozen=True)
class EvolutionaryPortfolioCandidate:
    option_ids: tuple[str, ...]
    expected_gross_margin: float
    budget_cost: float
    average_risk: float


@dataclass(frozen=True)
class EvolutionaryPortfolioResult:
    status: str
    candidates: tuple[EvolutionaryPortfolioCandidate, ...]
    population_size: int
    generations: int
    seed: int
    engine: str = "pymoo-nsga2"


def solve_portfolio_frontier(
    *,
    options: tuple[EvolutionaryPortfolioOption, ...],
    max_budget: float,
    min_selected: int = 1,
    max_selected: int | None = None,
    population_size: int = 80,
    generations: int = 80,
    seed: int = 42,
) -> EvolutionaryPortfolioResult:
    if not options:
        raise ValueError("evolutionary portfolio requires options")
    if max_budget <= 0:
        raise ValueError("max_budget must be positive")
    max_selected = max_selected or len(options)
    if min_selected < 1 or max_selected < min_selected:
        raise ValueError("invalid selected-option bounds")
    require_oss_capability(OssCapability.EVOLUTIONARY_OPTIMIZATION)

    from pymoo.algorithms.moo.nsga2 import NSGA2
    from pymoo.core.problem import Problem
    from pymoo.operators.crossover.pntx import TwoPointCrossover
    from pymoo.operators.mutation.bitflip import BitflipMutation
    from pymoo.operators.sampling.rnd import BinaryRandomSampling
    from pymoo.optimize import minimize

    gross_margin = np.asarray([item.expected_gross_margin for item in options], dtype=float)
    budget = np.asarray([item.budget_cost for item in options], dtype=float)
    risk = np.asarray([item.risk_score for item in options], dtype=float)

    class PortfolioProblem(Problem):
        def __init__(self) -> None:
            super().__init__(
                n_var=len(options),
                n_obj=2,
                n_ieq_constr=3,
                xl=0,
                xu=1,
                vtype=bool,
            )

        def _evaluate(self, x: np.ndarray, out: dict[str, Any], *args: Any, **kwargs: Any) -> None:
            selected_count = x.sum(axis=1)
            total_margin = x @ gross_margin
            total_budget = x @ budget
            total_risk = x @ risk
            average_risk = np.divide(
                total_risk,
                selected_count,
                out=np.full_like(total_risk, np.inf),
                where=selected_count > 0,
            )
            out["F"] = np.column_stack((-total_margin, average_risk))
            out["G"] = np.column_stack(
                (
                    total_budget - max_budget,
                    min_selected - selected_count,
                    selected_count - max_selected,
                )
            )

    result = minimize(
        PortfolioProblem(),
        NSGA2(
            pop_size=population_size,
            sampling=BinaryRandomSampling(),
            crossover=TwoPointCrossover(),
            mutation=BitflipMutation(),
            eliminate_duplicates=True,
        ),
        ("n_gen", generations),
        seed=seed,
        verbose=False,
    )
    if result.X is None:
        return EvolutionaryPortfolioResult(
            status="infeasible",
            candidates=(),
            population_size=population_size,
            generations=generations,
            seed=seed,
        )

    selections = np.atleast_2d(result.X).astype(bool)
    unique: dict[tuple[str, ...], EvolutionaryPortfolioCandidate] = {}
    for row in selections:
        chosen = tuple(option.option_id for option, selected in zip(options, row, strict=True) if selected)
        if not chosen:
            continue
        selected_options = [
            option for option, selected in zip(options, row, strict=True) if selected
        ]
        unique[chosen] = EvolutionaryPortfolioCandidate(
            option_ids=chosen,
            expected_gross_margin=round(
                sum(option.expected_gross_margin for option in selected_options), 4
            ),
            budget_cost=round(sum(option.budget_cost for option in selected_options), 4),
            average_risk=round(
                sum(option.risk_score for option in selected_options) / len(selected_options), 4
            ),
        )
    frontier = tuple(
        sorted(
            unique.values(),
            key=lambda candidate: (
                -candidate.expected_gross_margin,
                candidate.average_risk,
                candidate.budget_cost,
            ),
        )
    )
    return EvolutionaryPortfolioResult(
        status="optimal_frontier" if frontier else "infeasible",
        candidates=frontier,
        population_size=population_size,
        generations=generations,
        seed=seed,
    )


__all__ = [
    "EvolutionaryPortfolioCandidate",
    "EvolutionaryPortfolioOption",
    "EvolutionaryPortfolioResult",
    "solve_portfolio_frontier",
]
