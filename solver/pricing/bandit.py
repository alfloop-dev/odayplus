"""Bandit exploration algorithms for pricing under hard constraints (ODP-FR-PRICE-006).

This module implements online exploration (Multi-Armed Bandit) over discrete on-ladder
price options while strictly respecting hard constraints (AC-06-01: hard-constraint
violation rate is 0).

Key properties:
1. Hard constraints are NEVER relaxed: candidate arms are strictly drawn from
   `build_safe_action_set(constraints)`.
2. Exploration algorithms (Epsilon-Greedy, UCB1, Thompson Sampling) are deterministic
   and reproducible when provided with a random seed.
3. Full replay contracts allow offline and shadow validation without affecting production.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4

from solver.pricing.constraints import PriceConstraints
from solver.pricing.demand import SimulationResult, simulate_price
from solver.pricing.optimizer import build_safe_action_set


class BanditAlgorithm(StrEnum):
    EPSILON_GREEDY = "EPSILON_GREEDY"
    UCB1 = "UCB1"
    THOMPSON_SAMPLING = "THOMPSON_SAMPLING"


@dataclass(frozen=True)
class BanditCandidate:
    """A generated price candidate from a Bandit exploration decision."""

    candidate_id: str
    gate_id: str
    sku_id: str
    store_id: str
    baseline_price: float
    explored_price: float
    delta_ratio: float
    algorithm: str
    expected_reward: float
    uncertainty: float
    estimated_exploration_cost: float
    hard_constraints_satisfied: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "gate_id": self.gate_id,
            "sku_id": self.sku_id,
            "store_id": self.store_id,
            "baseline_price": self.baseline_price,
            "explored_price": self.explored_price,
            "delta_ratio": self.delta_ratio,
            "algorithm": self.algorithm,
            "expected_reward": self.expected_reward,
            "uncertainty": self.uncertainty,
            "estimated_exploration_cost": self.estimated_exploration_cost,
            "hard_constraints_satisfied": self.hard_constraints_satisfied,
        }


@dataclass(frozen=True)
class ArmObservation:
    """Historical reward observation for a specific price arm."""

    price: float
    reward: float


@dataclass(frozen=True)
class ArmStatistics:
    """Summary statistics for an arm."""

    price: float
    pull_count: int = 0
    total_reward: float = 0.0
    mean_reward: float = 0.0
    variance: float = 0.0


@dataclass(frozen=True)
class BanditReplayContract:
    """Immutable replay contract for offline/shadow reproduction."""

    seed: int
    algorithm: str
    baseline_price: float
    unit_cost: float
    baseline_demand: float
    elasticity: float
    confidence: float
    history: tuple[tuple[float, float], ...] = ()
    hyperparameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "algorithm": self.algorithm,
            "baseline_price": self.baseline_price,
            "unit_cost": self.unit_cost,
            "baseline_demand": self.baseline_demand,
            "elasticity": self.elasticity,
            "confidence": self.confidence,
            "history": list(self.history),
            "hyperparameters": dict(self.hyperparameters),
        }


def _compute_arm_statistics(
    arms: Sequence[float],
    history: Sequence[tuple[float, float]] | None,
) -> dict[float, ArmStatistics]:
    """Aggregate history observations into per-arm mean and variance."""
    rewards_by_arm: dict[float, list[float]] = {arm: [] for arm in arms}
    if history:
        for price, reward in history:
            # Match to closest arm within float tolerance
            for arm in arms:
                if abs(arm - price) < 1e-6:
                    rewards_by_arm[arm].append(reward)
                    break

    stats: dict[float, ArmStatistics] = {}
    for arm in arms:
        obs = rewards_by_arm[arm]
        n = len(obs)
        if n == 0:
            stats[arm] = ArmStatistics(price=arm, pull_count=0, total_reward=0.0, mean_reward=0.0, variance=0.0)
        else:
            tot = sum(obs)
            mean = tot / n
            var = sum((x - mean) ** 2 for x in obs) / n if n > 1 else 0.0
            stats[arm] = ArmStatistics(price=arm, pull_count=n, total_reward=tot, mean_reward=mean, variance=var)
    return stats


def select_bandit_arm(
    *,
    arms: Sequence[float],
    simulations: Mapping[float, SimulationResult],
    algorithm: BanditAlgorithm | str = BanditAlgorithm.THOMPSON_SAMPLING,
    history: Sequence[tuple[float, float]] | None = None,
    seed: int | None = None,
    epsilon: float = 0.1,
    ucb_c: float = 1.414,
) -> tuple[float, float, float]:
    """Select a price arm using the designated bandit policy.

    Returns:
        (chosen_price, expected_reward, uncertainty)
    """
    if not arms:
        raise ValueError("Cannot select bandit arm from empty arms set")

    if len(arms) == 1:
        arm = arms[0]
        sim = simulations[arm]
        return arm, sim.expected_gross_margin, max(0.01, sim.gross_margin.p90 - sim.gross_margin.p10)

    algo = BanditAlgorithm(str(algorithm).upper())
    rng = random.Random(seed) if seed is not None else random.Random()
    arm_stats = _compute_arm_statistics(arms, history)

    if algo == BanditAlgorithm.EPSILON_GREEDY:
        if rng.random() < epsilon:
            # Explore: uniform random choice
            chosen = rng.choice(arms)
        else:
            # Exploit: best empirical mean, fallback to simulation expected gross margin
            best_arm = max(
                arms,
                key=lambda a: arm_stats[a].mean_reward if arm_stats[a].pull_count > 0 else simulations[a].expected_gross_margin,
            )
            chosen = best_arm

        sim = simulations[chosen]
        stats = arm_stats[chosen]
        expected_r = stats.mean_reward if stats.pull_count > 0 else sim.expected_gross_margin
        uncertainty = math.sqrt(stats.variance) if stats.pull_count > 1 else (sim.gross_margin.p90 - sim.gross_margin.p10)
        return chosen, round(expected_r, 4), round(uncertainty, 4)

    elif algo == BanditAlgorithm.UCB1:
        total_pulls = sum(st.pull_count for st in arm_stats.values())
        scores: dict[float, float] = {}
        for arm in arms:
            stats = arm_stats[arm]
            sim = simulations[arm]
            prior_mean = sim.expected_gross_margin
            if stats.pull_count == 0:
                # Unpulled arms have exploration bonus
                score = prior_mean + ucb_c * math.sqrt(math.log(total_pulls + 2) / 1.0)
            else:
                score = stats.mean_reward + ucb_c * math.sqrt(math.log(total_pulls + 1) / stats.pull_count)
            scores[arm] = score

        chosen = max(arms, key=lambda a: scores[a])
        sim = simulations[chosen]
        stats = arm_stats[chosen]
        expected_r = stats.mean_reward if stats.pull_count > 0 else sim.expected_gross_margin
        uncertainty = math.sqrt(stats.variance) if stats.pull_count > 1 else (sim.gross_margin.p90 - sim.gross_margin.p10)
        return chosen, round(expected_r, 4), round(uncertainty, 4)

    elif algo == BanditAlgorithm.THOMPSON_SAMPLING:
        samples: dict[float, float] = {}
        for arm in arms:
            sim = simulations[arm]
            stats = arm_stats[arm]
            # Prior based on simulation margin and uncertainty
            prior_mean = sim.expected_gross_margin
            prior_std = max(1.0, (sim.gross_margin.p90 - sim.gross_margin.p10) / 2.0)
            prior_var = prior_std ** 2

            if stats.pull_count == 0:
                post_mean = prior_mean
                post_std = prior_std
            else:
                obs_var = max(1.0, stats.variance)
                n = stats.pull_count
                post_var = 1.0 / (1.0 / prior_var + n / obs_var)
                post_mean = post_var * (prior_mean / prior_var + n * stats.mean_reward / obs_var)
                post_std = math.sqrt(post_var)

            samples[arm] = rng.gauss(post_mean, post_std)

        chosen = max(arms, key=lambda a: samples[a])
        sim = simulations[chosen]
        stats = arm_stats[chosen]
        expected_r = stats.mean_reward if stats.pull_count > 0 else sim.expected_gross_margin
        uncertainty = math.sqrt(stats.variance) if stats.pull_count > 1 else (sim.gross_margin.p90 - sim.gross_margin.p10)
        return chosen, round(expected_r, 4), round(uncertainty, 4)

    raise ValueError(f"Unsupported bandit algorithm: {algo}")


def explore_price_candidate(
    *,
    constraints: PriceConstraints,
    baseline_demand: float,
    elasticity: float,
    confidence: float,
    gate_id: str,
    sku_id: str,
    store_id: str,
    algorithm: BanditAlgorithm | str = BanditAlgorithm.THOMPSON_SAMPLING,
    history: Sequence[tuple[float, float]] | None = None,
    seed: int | None = None,
    candidate_id: str | None = None,
    epsilon: float = 0.1,
    ucb_c: float = 1.414,
) -> BanditCandidate:
    """Generate a single exploration candidate under hard constraints."""
    arms = build_safe_action_set(constraints)
    if not arms:
        # No feasible arms on the ladder; hold current price
        arms = [constraints.current_price]

    simulations = {
        arm: simulate_price(
            price=arm,
            baseline_demand=baseline_demand,
            baseline_price=constraints.current_price,
            unit_cost=constraints.unit_cost,
            elasticity=elasticity,
            confidence=confidence,
            applicable_min_price=constraints.applicable_min_price,
            applicable_max_price=constraints.applicable_max_price,
        )
        for arm in arms
    }

    baseline_sim = simulate_price(
        price=constraints.current_price,
        baseline_demand=baseline_demand,
        baseline_price=constraints.current_price,
        unit_cost=constraints.unit_cost,
        elasticity=elasticity,
        confidence=confidence,
        applicable_min_price=constraints.applicable_min_price,
        applicable_max_price=constraints.applicable_max_price,
    )

    chosen_price, expected_reward, uncertainty = select_bandit_arm(
        arms=arms,
        simulations=simulations,
        algorithm=algorithm,
        history=history,
        seed=seed,
        epsilon=epsilon,
        ucb_c=ucb_c,
    )

    chosen_sim = simulations[chosen_price]
    # Exploration cost: margin delta loss or exploration spread risk
    margin_diff = baseline_sim.expected_gross_margin - chosen_sim.expected_gross_margin
    if abs(chosen_price - constraints.current_price) > 1e-9:
        est_cost = max(0.01, round(max(margin_diff, abs(chosen_price - constraints.current_price)), 4))
    else:
        est_cost = 0.0

    delta_ratio = 0.0
    if constraints.current_price > 0:
        delta_ratio = round((chosen_price - constraints.current_price) / constraints.current_price, 4)

    violations = constraints.violations(chosen_price)
    hard_satisfied = len([v for v in violations if v.is_hard]) == 0

    return BanditCandidate(
        candidate_id=candidate_id or f"bandit-candidate-{uuid4()}",
        gate_id=gate_id,
        sku_id=sku_id,
        store_id=store_id,
        baseline_price=constraints.current_price,
        explored_price=chosen_price,
        delta_ratio=delta_ratio,
        algorithm=str(algorithm).upper(),
        expected_reward=expected_reward,
        uncertainty=uncertainty,
        estimated_exploration_cost=est_cost,
        hard_constraints_satisfied=hard_satisfied,
    )


def replay_bandit_candidate(
    contract: BanditReplayContract,
    *,
    constraints: PriceConstraints,
    gate_id: str,
    sku_id: str,
    store_id: str,
    candidate_id: str | None = None,
) -> BanditCandidate:
    """Deterministically replay a bandit decision using an immutable replay contract."""
    epsilon = float(contract.hyperparameters.get("epsilon", 0.1))
    ucb_c = float(contract.hyperparameters.get("ucb_c", 1.414))
    return explore_price_candidate(
        constraints=constraints,
        baseline_demand=contract.baseline_demand,
        elasticity=contract.elasticity,
        confidence=contract.confidence,
        gate_id=gate_id,
        sku_id=sku_id,
        store_id=store_id,
        algorithm=contract.algorithm,
        history=contract.history,
        seed=contract.seed,
        candidate_id=candidate_id,
        epsilon=epsilon,
        ucb_c=ucb_c,
    )


__all__ = [
    "ArmObservation",
    "ArmStatistics",
    "BanditAlgorithm",
    "BanditCandidate",
    "BanditReplayContract",
    "explore_price_candidate",
    "replay_bandit_candidate",
    "select_bandit_arm",
]
