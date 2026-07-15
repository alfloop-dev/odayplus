"""Constant-elasticity demand model and demand/margin simulation.

PriceOps must show expected demand, margin and a risk interval for every price
(AC-06-02). We use a deterministic constant-elasticity response so simulations
are reproducible in tests and an uncertainty band derived from estimate
confidence yields the P10/P50/P90 envelope.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

# Relative width of the elasticity uncertainty band at zero confidence. The band
# shrinks linearly to zero as confidence approaches 1.0.
ELASTICITY_BAND_AT_ZERO_CONFIDENCE = 0.5


@dataclass(frozen=True)
class Band:
    """A P10/P50/P90 interval. ``p10 <= p50 <= p90`` always holds."""

    p10: float
    p50: float
    p90: float

    def to_dict(self) -> dict[str, float]:
        return {"p10": self.p10, "p50": self.p50, "p90": self.p90}


@dataclass(frozen=True)
class ElasticityFit:
    """Result of estimating a constant price elasticity from observed data."""

    elasticity: float
    confidence: float
    r_squared: float
    sample_size: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "elasticity": self.elasticity,
            "confidence": self.confidence,
            "r_squared": self.r_squared,
            "sample_size": self.sample_size,
        }


def estimate_elasticity(
    price_demand_observations: Sequence[tuple[float, float]],
) -> ElasticityFit:
    """Estimate a constant price elasticity from ``(price, demand)`` history.

    Fits the log-log constant-elasticity model ``log q = a + e * log p`` with an
    ordinary-least-squares regression (scikit-learn ``LinearRegression``). The
    slope ``e`` is the elasticity and the coefficient of determination (R²) of
    the fit serves as an estimate confidence in ``[0, 1]``. At least two distinct
    positive prices are required; otherwise the elasticity is not identified and
    a zero-confidence fallback is returned so callers can degrade gracefully.
    """
    points = [
        (float(price), float(demand))
        for price, demand in price_demand_observations
        if price > 0 and demand > 0
    ]
    distinct_prices = {round(price, 10) for price, _ in points}
    if len(points) < 2 or len(distinct_prices) < 2:
        return ElasticityFit(
            elasticity=0.0, confidence=0.0, r_squared=0.0, sample_size=len(points)
        )

    import numpy as np
    from sklearn.linear_model import LinearRegression

    log_price = np.log(np.asarray([price for price, _ in points], dtype=float)).reshape(-1, 1)
    log_demand = np.log(np.asarray([demand for _, demand in points], dtype=float))
    model = LinearRegression().fit(log_price, log_demand)
    r_squared = float(model.score(log_price, log_demand))
    confidence = min(max(r_squared, 0.0), 1.0)
    return ElasticityFit(
        elasticity=round(float(model.coef_[0]), 6),
        confidence=round(confidence, 6),
        r_squared=round(r_squared, 6),
        sample_size=len(points),
    )


@dataclass(frozen=True)
class SimulationResult:
    """Simulated demand, revenue and gross-margin envelopes for one price."""

    price: float
    unit_cost: float
    demand: Band
    revenue: Band
    gross_margin: Band

    @property
    def expected_gross_margin(self) -> float:
        """Central (P50) gross-margin estimate."""
        return self.gross_margin.p50

    @property
    def downside_gross_margin(self) -> float:
        """Worst-case (P10) gross-margin estimate used for risk gating."""
        return self.gross_margin.p10

    def to_dict(self) -> dict[str, Any]:
        return {
            "price": self.price,
            "unit_cost": self.unit_cost,
            "demand": self.demand.to_dict(),
            "revenue": self.revenue.to_dict(),
            "gross_margin": self.gross_margin.to_dict(),
            "expected_gross_margin": self.expected_gross_margin,
            "downside_gross_margin": self.downside_gross_margin,
        }


def expected_demand(
    *,
    baseline_demand: float,
    baseline_price: float,
    price: float,
    elasticity: float,
) -> float:
    """Constant-elasticity demand: ``q = q0 * (p / p0) ** elasticity``.

    ``elasticity`` is expected to be negative (demand falls as price rises).
    """
    if baseline_price <= 0 or baseline_demand <= 0 or price <= 0:
        return 0.0
    ratio = price / baseline_price
    return max(baseline_demand * (ratio**elasticity), 0.0)


def _elasticity_band(elasticity: float, confidence: float) -> tuple[float, float, float]:
    bounded_confidence = max(0.0, min(1.0, confidence))
    spread = (1.0 - bounded_confidence) * ELASTICITY_BAND_AT_ZERO_CONFIDENCE
    # A more-negative elasticity reacts harder to price moves. Returning the
    # mid value plus the two extremes lets the caller sort into a P10/P90 band
    # regardless of whether the price move is up or down.
    return (
        elasticity * (1.0 + spread),
        elasticity,
        elasticity * (1.0 - spread),
    )


def simulate_price(
    *,
    price: float,
    baseline_demand: float,
    baseline_price: float,
    unit_cost: float,
    elasticity: float,
    confidence: float = 1.0,
) -> SimulationResult:
    """Simulate demand/revenue/gross-margin bands for a single ``price``."""
    elasticities = _elasticity_band(elasticity, confidence)
    demands = sorted(
        expected_demand(
            baseline_demand=baseline_demand,
            baseline_price=baseline_price,
            price=price,
            elasticity=value,
        )
        for value in elasticities
    )
    demand = Band(
        p10=round(demands[0], 4),
        p50=round(demands[1], 4),
        p90=round(demands[2], 4),
    )
    revenue = Band(
        p10=round(demand.p10 * price, 4),
        p50=round(demand.p50 * price, 4),
        p90=round(demand.p90 * price, 4),
    )
    unit_margin = price - unit_cost
    gross_margin = Band(
        p10=round(demand.p10 * unit_margin, 4),
        p50=round(demand.p50 * unit_margin, 4),
        p90=round(demand.p90 * unit_margin, 4),
    )
    return SimulationResult(
        price=price,
        unit_cost=unit_cost,
        demand=demand,
        revenue=revenue,
        gross_margin=gross_margin,
    )


__all__ = [
    "Band",
    "ElasticityFit",
    "SimulationResult",
    "estimate_elasticity",
    "expected_demand",
    "simulate_price",
]
