"""Hard pricing constraints and feasibility checks.

The PriceOps optimizer (ODP-MOD-06) must never recommend a price that violates a
hard constraint (AC-06-01: hard-constraint violation rate must be 0). This module
defines the constraint model and the feasibility primitives the safe-action-set
builder and optimizer rely on so that infeasible prices are filtered *before* a
price ever reaches a plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PRICING_POLICY_VERSION = "brand-pricing-policy-v1"

# Codes recorded on a ConstraintViolation. Stable strings so audit/evidence can
# trace why a candidate price was rejected.
VIOLATION_MARGIN_FLOOR = "margin_floor"
VIOLATION_MAX_INCREASE = "max_increase_exceeded"
VIOLATION_MAX_DECREASE = "max_decrease_exceeded"
VIOLATION_BELOW_MIN = "below_min_price"
VIOLATION_ABOVE_MAX = "above_max_price"
VIOLATION_OFF_LADDER = "off_price_ladder"


@dataclass(frozen=True)
class ConstraintViolation:
    """A single hard-constraint breach for a candidate price."""

    code: str
    message: str
    price: float
    limit: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "price": self.price,
            "limit": self.limit,
        }


@dataclass(frozen=True)
class PriceConstraints:
    """Hard bounds for a single store/machine price.

    All bounds are *hard*: a price that breaches any of them is infeasible and
    must never be recommended. ``margin_floor_ratio`` is a gross-margin ratio,
    i.e. ``(price - unit_cost) / price`` must stay at or above the floor.
    """

    unit_cost: float
    current_price: float
    margin_floor_ratio: float = 0.15
    max_increase_pct: float = 0.15
    max_decrease_pct: float = 0.15
    price_ladder_step: float = 0.5
    min_price: float | None = None
    max_price: float | None = None
    policy_version: str = PRICING_POLICY_VERSION

    @property
    def margin_floor_price(self) -> float:
        """Lowest price that still satisfies the gross-margin floor."""
        denom = max(1.0 - self.margin_floor_ratio, 1e-9)
        return round(self.unit_cost / denom, 4)

    @property
    def lower_bound(self) -> float:
        """Tightest lower bound across max-decrease, margin floor and min price."""
        candidates = [
            self.current_price * (1.0 - self.max_decrease_pct),
            self.margin_floor_price,
        ]
        if self.min_price is not None:
            candidates.append(self.min_price)
        return round(max(candidates), 4)

    @property
    def upper_bound(self) -> float:
        """Tightest upper bound across max-increase and max price."""
        candidates = [self.current_price * (1.0 + self.max_increase_pct)]
        if self.max_price is not None:
            candidates.append(self.max_price)
        return round(min(candidates), 4)

    @property
    def is_feasible_region(self) -> bool:
        """True when at least one price can satisfy every hard constraint."""
        return self.lower_bound <= self.upper_bound + 1e-9

    def margin_ratio(self, price: float) -> float:
        if price <= 0:
            return 0.0
        return round((price - self.unit_cost) / price, 6)

    def on_ladder(self, price: float) -> bool:
        step = self.price_ladder_step
        if step <= 0:
            return True
        remainder = round(price / step) * step
        return abs(remainder - price) <= 1e-6

    def violations(self, price: float) -> list[ConstraintViolation]:
        """All hard-constraint breaches for ``price`` (empty when feasible)."""
        breaches: list[ConstraintViolation] = []
        if self.margin_ratio(price) < self.margin_floor_ratio - 1e-9:
            breaches.append(
                ConstraintViolation(
                    code=VIOLATION_MARGIN_FLOOR,
                    message="gross margin ratio below policy floor",
                    price=price,
                    limit=self.margin_floor_ratio,
                )
            )
        max_up = self.current_price * (1.0 + self.max_increase_pct)
        if price > max_up + 1e-9:
            breaches.append(
                ConstraintViolation(
                    code=VIOLATION_MAX_INCREASE,
                    message="price increase exceeds max delta",
                    price=price,
                    limit=round(max_up, 4),
                )
            )
        max_down = self.current_price * (1.0 - self.max_decrease_pct)
        if price < max_down - 1e-9:
            breaches.append(
                ConstraintViolation(
                    code=VIOLATION_MAX_DECREASE,
                    message="price decrease exceeds max delta",
                    price=price,
                    limit=round(max_down, 4),
                )
            )
        if self.min_price is not None and price < self.min_price - 1e-9:
            breaches.append(
                ConstraintViolation(
                    code=VIOLATION_BELOW_MIN,
                    message="price below configured minimum",
                    price=price,
                    limit=self.min_price,
                )
            )
        if self.max_price is not None and price > self.max_price + 1e-9:
            breaches.append(
                ConstraintViolation(
                    code=VIOLATION_ABOVE_MAX,
                    message="price above configured maximum",
                    price=price,
                    limit=self.max_price,
                )
            )
        if not self.on_ladder(price):
            breaches.append(
                ConstraintViolation(
                    code=VIOLATION_OFF_LADDER,
                    message="price not aligned to price ladder step",
                    price=price,
                    limit=self.price_ladder_step,
                )
            )
        return breaches

    def is_feasible(self, price: float) -> bool:
        return not self.violations(price)

    def binding_constraints(self, price: float, tolerance: float | None = None) -> list[str]:
        """Which hard bounds the price sits on (the active/binding constraints).

        Used for explainability (ODP-OR-01 §5.6 ``binding_constraints``): it
        names the constraints that stop the price moving further toward higher
        margin, e.g. the max-increase delta or the margin floor.
        """
        tol = tolerance if tolerance is not None else max(self.price_ladder_step / 2, 1e-6)
        binding: list[str] = []
        if abs(price - self.upper_bound) <= tol:
            if self.max_price is not None and abs(self.upper_bound - self.max_price) <= 1e-6:
                binding.append("max_price_ceiling")
            else:
                binding.append("max_increase_delta")
        if abs(price - self.lower_bound) <= tol:
            if abs(self.lower_bound - self.margin_floor_price) <= 1e-6:
                binding.append("margin_floor")
            elif self.min_price is not None and abs(self.lower_bound - self.min_price) <= 1e-6:
                binding.append("min_price_floor")
            else:
                binding.append("max_decrease_delta")
        return binding

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_cost": self.unit_cost,
            "current_price": self.current_price,
            "margin_floor_ratio": self.margin_floor_ratio,
            "max_increase_pct": self.max_increase_pct,
            "max_decrease_pct": self.max_decrease_pct,
            "price_ladder_step": self.price_ladder_step,
            "min_price": self.min_price,
            "max_price": self.max_price,
            "policy_version": self.policy_version,
            "lower_bound": self.lower_bound,
            "upper_bound": self.upper_bound,
            "margin_floor_price": self.margin_floor_price,
        }


__all__ = [
    "PRICING_POLICY_VERSION",
    "VIOLATION_ABOVE_MAX",
    "VIOLATION_BELOW_MIN",
    "VIOLATION_MARGIN_FLOOR",
    "VIOLATION_MAX_DECREASE",
    "VIOLATION_MAX_INCREASE",
    "VIOLATION_OFF_LADDER",
    "ConstraintViolation",
    "PriceConstraints",
]
