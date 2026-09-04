"""Unit tests for pricing bandit exploration framework (ODP-FR-PRICE-006)."""

from __future__ import annotations

from solver.pricing.bandit import (
    BanditAlgorithm,
    BanditCandidate,
    BanditReplayContract,
    explore_price_candidate,
    replay_bandit_candidate,
    select_bandit_arm,
)
from solver.pricing.constraints import PriceConstraints
from solver.pricing.demand import simulate_price
from solver.pricing.optimizer import build_safe_action_set


def test_bandit_candidate_strictly_respects_hard_constraints() -> None:
    constraints = PriceConstraints(
        unit_cost=10.0,
        current_price=20.0,
        margin_floor_ratio=0.30,  # margin floor price = 10 / (1 - 0.3) = 14.2857 -> 14.5 on ladder
        max_increase_pct=0.20,    # upper bound = 24.0
        max_decrease_pct=0.20,    # lower bound = 16.0
        price_ladder_step=0.5,
    )

    safe_arms = build_safe_action_set(constraints)
    assert len(safe_arms) > 0

    for algo in [BanditAlgorithm.EPSILON_GREEDY, BanditAlgorithm.UCB1, BanditAlgorithm.THOMPSON_SAMPLING]:
        for seed in [1, 42, 100, 999]:
            candidate = explore_price_candidate(
                constraints=constraints,
                baseline_demand=100.0,
                elasticity=-1.5,
                confidence=0.9,
                gate_id="gate-123",
                sku_id="sku-1",
                store_id="store-1",
                algorithm=algo,
                seed=seed,
            )

            assert candidate.hard_constraints_satisfied is True
            assert candidate.explored_price in safe_arms
            assert candidate.baseline_price == 20.0
            assert candidate.gate_id == "gate-123"
            assert constraints.is_feasible(candidate.explored_price)
            assert candidate.estimated_exploration_cost >= 0.0


def test_deterministic_offline_replay_reproducibility() -> None:
    constraints = PriceConstraints(
        unit_cost=12.0,
        current_price=25.0,
        margin_floor_ratio=0.25,
        max_increase_pct=0.15,
        max_decrease_pct=0.15,
        price_ladder_step=1.0,
    )

    history = (
        (24.0, 120.0),
        (25.0, 150.0),
        (26.0, 180.0),
        (27.0, 110.0),
    )

    contract = BanditReplayContract(
        seed=1337,
        algorithm="THOMPSON_SAMPLING",
        baseline_price=25.0,
        unit_cost=12.0,
        baseline_demand=80.0,
        elasticity=-1.2,
        confidence=0.85,
        history=history,
        hyperparameters={"epsilon": 0.05, "ucb_c": 1.5},
    )

    # Replay twice independently
    candidate1 = replay_bandit_candidate(
        contract,
        constraints=constraints,
        gate_id="gate-test",
        sku_id="sku-test",
        store_id="store-test",
        candidate_id="cand-1",
    )
    candidate2 = replay_bandit_candidate(
        contract,
        constraints=constraints,
        gate_id="gate-test",
        sku_id="sku-test",
        store_id="store-test",
        candidate_id="cand-1",
    )

    assert candidate1.explored_price == candidate2.explored_price
    assert candidate1.expected_reward == candidate2.expected_reward
    assert candidate1.uncertainty == candidate2.uncertainty
    assert candidate1.estimated_exploration_cost == candidate2.estimated_exploration_cost
    assert candidate1.to_dict() == candidate2.to_dict()


def test_bandit_explores_higher_reward_arms() -> None:
    constraints = PriceConstraints(
        unit_cost=5.0,
        current_price=10.0,
        margin_floor_ratio=0.2,
        max_increase_pct=0.3,  # bounds: [7.0, 13.0]
        max_decrease_pct=0.3,
        price_ladder_step=1.0,
    )

    # Heavily reward 12.0 across all feasible arms (7 to 13)
    history = [
        (7.0, 10.0),
        (8.0, 10.0),
        (9.0, 15.0),
        (10.0, 20.0),
        (11.0, 25.0),
        (12.0, 200.0),
        (12.0, 210.0),
        (12.0, 190.0),
        (13.0, 5.0),
    ]

    # With exploitation / UCB1
    candidate_ucb = explore_price_candidate(
        constraints=constraints,
        baseline_demand=50.0,
        elasticity=-1.0,
        confidence=0.95,
        gate_id="gate-1",
        sku_id="sku-1",
        store_id="store-1",
        algorithm=BanditAlgorithm.UCB1,
        history=history,
        seed=42,
    )
    assert candidate_ucb.explored_price == 12.0


def test_bandit_single_arm_fallback() -> None:
    # A tightly constrained setting where only current price is feasible
    constraints = PriceConstraints(
        unit_cost=10.0,
        current_price=12.0,
        margin_floor_ratio=0.1666,
        max_increase_pct=0.0,
        max_decrease_pct=0.0,
        price_ladder_step=1.0,
    )
    candidate = explore_price_candidate(
        constraints=constraints,
        baseline_demand=100.0,
        elasticity=-1.0,
        confidence=0.9,
        gate_id="gate-1",
        sku_id="sku-1",
        store_id="store-1",
        algorithm=BanditAlgorithm.THOMPSON_SAMPLING,
    )
    assert candidate.explored_price == 12.0
    assert candidate.delta_ratio == 0.0
