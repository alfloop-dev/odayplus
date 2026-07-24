from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import version
from typing import Any

from modules.priceops.domain.pricing import PricingPlan, PricingPlanItem
from solver.pricing.demand import simulate_price
from solver.pricing.optimizer import (
    HIGH_RISK_DELTA_PCT,
    LOW_CONFIDENCE_THRESHOLD,
    MEDIUM_CONFIDENCE_THRESHOLD,
    MEDIUM_RISK_DELTA_PCT,
    STATUS_FEASIBLE,
    STATUS_INFEASIBLE,
    STATUS_OPTIMAL,
    OptimizationResult,
    PriceCandidate,
    build_safe_action_set,
    diagnose_infeasible,
)

PRICEOPS_OSS_SOLVER_VERSION = "priceops-optuna-cvxpy-v1"


class PriceOpsProductionExecutionError(RuntimeError):
    """Raised when the production Optuna/CVXPY contract cannot execute."""


@dataclass(frozen=True)
class PriceOpsProductionExecution:
    results: tuple[tuple[PricingPlanItem, OptimizationResult], ...]
    metadata: dict[str, Any]


class PriceOpsProductionOptimizer:
    def optimize(self, plan: PricingPlan) -> PriceOpsProductionExecution:
        missing_lineage = sorted(
            item.item_id for item in plan.items if not item.source_snapshot_ids
        )
        if missing_lineage:
            raise PriceOpsProductionExecutionError(
                "production PriceOps items require source snapshot lineage: "
                + ", ".join(missing_lineage)
            )
        try:
            import cvxpy as cp
            import optuna
        except ModuleNotFoundError as exc:
            raise PriceOpsProductionExecutionError(
                "Optuna and CVXPY are required for production PriceOps"
            ) from exc

        candidate_sets: dict[str, tuple[PriceCandidate, ...]] = {}
        safe_sets: dict[str, tuple[float, ...]] = {}
        infeasible_results: dict[str, OptimizationResult] = {}
        optuna_best: dict[str, float] = {}
        for item in plan.items:
            safe_prices = tuple(build_safe_action_set(item.constraints))
            safe_sets[item.item_id] = safe_prices
            if not safe_prices:
                infeasible_results[item.item_id] = _infeasible_result(item)
                continue
            candidates = _run_optuna(item, safe_prices, optuna)
            candidate_sets[item.item_id] = candidates
            optuna_best[item.item_id] = max(
                candidates,
                key=lambda candidate: (
                    candidate.incremental_gross_margin,
                    -candidate.price,
                ),
            ).price

        if infeasible_results:
            results = tuple(
                (
                    item,
                    infeasible_results.get(item.item_id)
                    or _hold_result(item, safe_sets[item.item_id]),
                )
                for item in plan.items
            )
            return PriceOpsProductionExecution(
                results=results,
                metadata={
                    "mode": "production_oss",
                    "solver_version": PRICEOPS_OSS_SOLVER_VERSION,
                    "model_versions": sorted(
                        {item.elasticity.model_version for item in plan.items}
                    ),
                    "feature_versions": sorted(
                        {item.elasticity.feature_version for item in plan.items}
                    ),
                    "status": STATUS_INFEASIBLE,
                    "source_snapshot_ids": sorted(
                        {snapshot for item in plan.items for snapshot in item.source_snapshot_ids}
                    ),
                    "engines": {
                        "search": {
                            "library": "optuna",
                            "library_version": version("optuna"),
                        },
                        "portfolio": {
                            "library": "cvxpy",
                            "library_version": version("cvxpy"),
                            "status": "not_run_infeasible_action_set",
                        },
                    },
                },
            )

        solver_name = _select_mip_solver(cp.installed_solvers())
        if solver_name is None:
            raise PriceOpsProductionExecutionError(
                "CVXPY has no approved mixed-integer backend for production PriceOps"
            )
        selected_prices = _run_cvxpy(
            plan=plan,
            candidate_sets=candidate_sets,
            cp=cp,
            solver_name=solver_name,
        )
        results = tuple(
            (
                item,
                _selected_result(
                    item,
                    candidates=candidate_sets[item.item_id],
                    safe_prices=safe_sets[item.item_id],
                    selected_price=selected_prices[item.item_id],
                ),
            )
            for item in plan.items
        )
        return PriceOpsProductionExecution(
            results=results,
            metadata={
                "mode": "production_oss",
                "solver_version": PRICEOPS_OSS_SOLVER_VERSION,
                "model_versions": sorted(
                    {item.elasticity.model_version for item in plan.items}
                ),
                "feature_versions": sorted(
                    {item.elasticity.feature_version for item in plan.items}
                ),
                "status": STATUS_OPTIMAL,
                "source_snapshot_ids": sorted(
                    {snapshot for item in plan.items for snapshot in item.source_snapshot_ids}
                ),
                "engines": {
                    "search": {
                        "library": "optuna",
                        "library_version": version("optuna"),
                        "sampler": "GridSampler",
                        "best_price_by_item": optuna_best,
                    },
                    "portfolio": {
                        "library": "cvxpy",
                        "library_version": version("cvxpy"),
                        "solver": solver_name,
                        "status": STATUS_OPTIMAL,
                    },
                },
            },
        )


def _run_optuna(
    item: PricingPlanItem,
    safe_prices: tuple[float, ...],
    optuna: Any,
) -> tuple[PriceCandidate, ...]:
    baseline = _simulate(item, item.constraints.current_price)
    candidates: dict[float, PriceCandidate] = {}

    def objective(trial: Any) -> float:
        price = float(trial.suggest_categorical("price", list(safe_prices)))
        simulation = _simulate(item, price)
        candidate = PriceCandidate(
            price=price,
            simulation=simulation,
            incremental_gross_margin=round(
                simulation.expected_gross_margin - baseline.expected_gross_margin,
                4,
            ),
        )
        candidates[price] = candidate
        return candidate.incremental_gross_margin

    try:
        study = optuna.create_study(
            direction="maximize",
            sampler=optuna.samplers.GridSampler({"price": list(safe_prices)}),
        )
        study.optimize(
            objective,
            n_trials=len(safe_prices),
            show_progress_bar=False,
        )
    except Exception as exc:
        raise PriceOpsProductionExecutionError(
            f"Optuna failed for pricing item {item.item_id}"
        ) from exc
    if set(candidates) != set(safe_prices):
        raise PriceOpsProductionExecutionError(
            f"Optuna did not evaluate the full safe action set for {item.item_id}"
        )
    return tuple(candidates[price] for price in safe_prices)


def _run_cvxpy(
    *,
    plan: PricingPlan,
    candidate_sets: dict[str, tuple[PriceCandidate, ...]],
    cp: Any,
    solver_name: str,
) -> dict[str, float]:
    flattened = tuple(
        (item, candidate) for item in plan.items for candidate in candidate_sets[item.item_id]
    )
    selections = cp.Variable(len(flattened), boolean=True, name="price_action")
    constraints: list[Any] = []
    for item in plan.items:
        indexes = [
            index
            for index, (candidate_item, _candidate) in enumerate(flattened)
            if candidate_item.item_id == item.item_id
        ]
        constraints.append(cp.sum(selections[indexes]) == 1)
    objective = cp.sum(
        cp.multiply(
            [candidate.incremental_gross_margin for _item, candidate in flattened],
            selections,
        )
    )
    problem = cp.Problem(cp.Maximize(objective), constraints)
    try:
        problem.solve(solver=solver_name, verbose=False)
    except Exception as exc:
        raise PriceOpsProductionExecutionError(
            f"CVXPY {solver_name} failed to optimize the price portfolio"
        ) from exc
    if problem.status not in {cp.OPTIMAL, cp.OPTIMAL_INACCURATE}:
        raise PriceOpsProductionExecutionError(f"CVXPY price portfolio returned {problem.status!r}")
    selected: dict[str, float] = {}
    for index, (item, candidate) in enumerate(flattened):
        if selections.value is not None and float(selections.value[index]) > 0.5:
            if item.item_id in selected:
                raise PriceOpsProductionExecutionError(
                    "CVXPY selected more than one price for an item"
                )
            selected[item.item_id] = candidate.price
    if len(selected) != len(plan.items):
        raise PriceOpsProductionExecutionError(
            "CVXPY did not select exactly one price for every item"
        )
    return selected


def _selected_result(
    item: PricingPlanItem,
    *,
    candidates: tuple[PriceCandidate, ...],
    safe_prices: tuple[float, ...],
    selected_price: float,
) -> OptimizationResult:
    baseline = _simulate(item, item.constraints.current_price)
    selected = next(candidate for candidate in candidates if candidate.price == selected_price)
    changed = abs(selected.price - item.constraints.current_price) > 1e-9
    incremental = max(0.0, selected.incremental_gross_margin) if changed else 0.0
    if selected.incremental_gross_margin <= 0:
        selected_price = item.constraints.current_price
        selected_simulation = baseline
        changed = False
        incremental = 0.0
        status = STATUS_FEASIBLE
    else:
        selected_simulation = selected.simulation
        status = STATUS_OPTIMAL
    delta_pct = (
        abs(selected_price - item.constraints.current_price) / item.constraints.current_price
        if item.constraints.current_price > 0
        else 0.0
    )
    risk = _risk_level(delta_pct, item.elasticity.confidence) if changed else "low"
    return OptimizationResult(
        recommended_price=selected_price,
        current_price=item.constraints.current_price,
        incremental_gross_margin=incremental,
        expected_demand_change=round(
            selected_simulation.demand.p50 - baseline.demand.p50,
            4,
        ),
        baseline_simulation=baseline,
        recommended_simulation=selected_simulation,
        safe_action_set=safe_prices,
        candidates=candidates,
        solver_status=status,
        risk_level=risk,
        requires_approval=changed and risk == "high",
        binding_constraints=tuple(item.constraints.binding_constraints(selected_price))
        if changed
        else (),
        solver_version=PRICEOPS_OSS_SOLVER_VERSION,
    )


def _hold_result(
    item: PricingPlanItem,
    safe_prices: tuple[float, ...],
) -> OptimizationResult:
    baseline = _simulate(item, item.constraints.current_price)
    return OptimizationResult(
        recommended_price=item.constraints.current_price,
        current_price=item.constraints.current_price,
        incremental_gross_margin=0.0,
        expected_demand_change=0.0,
        baseline_simulation=baseline,
        recommended_simulation=baseline,
        safe_action_set=safe_prices,
        candidates=(),
        solver_status=STATUS_FEASIBLE,
        risk_level="low",
        requires_approval=False,
        solver_version=PRICEOPS_OSS_SOLVER_VERSION,
    )


def _infeasible_result(item: PricingPlanItem) -> OptimizationResult:
    baseline = _simulate(item, item.constraints.current_price)
    return OptimizationResult(
        recommended_price=item.constraints.current_price,
        current_price=item.constraints.current_price,
        incremental_gross_margin=0.0,
        expected_demand_change=0.0,
        baseline_simulation=baseline,
        recommended_simulation=baseline,
        safe_action_set=(),
        candidates=(),
        solver_status=STATUS_INFEASIBLE,
        risk_level="high",
        requires_approval=True,
        constraint_violations=tuple(item.constraints.violations(item.constraints.current_price)),
        infeasible=True,
        diagnostics=tuple(diagnose_infeasible(item.constraints)),
        solver_version=PRICEOPS_OSS_SOLVER_VERSION,
    )


def _simulate(item: PricingPlanItem, price: float) -> Any:
    return simulate_price(
        price=price,
        baseline_demand=item.baseline_demand,
        baseline_price=item.constraints.current_price,
        unit_cost=item.constraints.unit_cost,
        elasticity=item.elasticity.elasticity_value,
        confidence=item.elasticity.confidence,
    )


def _select_mip_solver(installed_solvers: list[str]) -> str | None:
    supported = (
        "SCIP",
        "HIGHS",
        "CBC",
        "GLPK_MI",
        "SCIPY",
        "ECOS_BB",
    )
    installed = set(installed_solvers)
    return next((solver for solver in supported if solver in installed), None)


def _risk_level(delta_pct: float, confidence: float) -> str:
    if delta_pct >= HIGH_RISK_DELTA_PCT or confidence < LOW_CONFIDENCE_THRESHOLD:
        return "high"
    if delta_pct >= MEDIUM_RISK_DELTA_PCT or confidence < MEDIUM_CONFIDENCE_THRESHOLD:
        return "medium"
    return "low"


__all__ = [
    "PRICEOPS_OSS_SOLVER_VERSION",
    "PriceOpsProductionExecution",
    "PriceOpsProductionExecutionError",
    "PriceOpsProductionOptimizer",
]
