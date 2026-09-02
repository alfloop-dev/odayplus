"""Hard pricing constraints and feasibility checks.

The PriceOps optimizer (ODP-MOD-06) must never recommend a price that violates a
hard constraint (AC-06-01: hard-constraint violation rate must be 0). This module
defines the constraint model and the feasibility primitives the safe-action-set
builder and optimizer rely on so that infeasible prices are filtered *before* a
price ever reaches a plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from shared.governance import DecisionPolicy

PRICING_POLICY_VERSION = "brand-pricing-policy-v1"
PRICING_POLICY_ID = "brand-pricing-policy"
PRICING_POLICY_KIND = "pricing_policy"
PRICING_POLICY_SEMVER = "1.0.0"

# Codes recorded on a ConstraintViolation. Stable strings so audit/evidence can
# trace why a candidate price was rejected.
VIOLATION_MARGIN_FLOOR = "margin_floor"
VIOLATION_MAX_INCREASE = "max_increase_exceeded"
VIOLATION_MAX_DECREASE = "max_decrease_exceeded"
VIOLATION_BELOW_MIN = "below_min_price"
VIOLATION_ABOVE_MAX = "above_max_price"
VIOLATION_OFF_LADDER = "off_price_ladder"
VIOLATION_BELOW_APPLICABLE_RANGE = "below_applicable_range"
VIOLATION_ABOVE_APPLICABLE_RANGE = "above_applicable_range"


@dataclass(frozen=True)
class ConstraintViolation:
    """A single hard-constraint breach for a candidate price."""

    code: str
    message: str
    price: float
    limit: float
    is_hard: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "price": self.price,
            "limit": self.limit,
            "is_hard": self.is_hard,
        }


def default_pricing_policy(tenant_id: str) -> DecisionPolicy:
    """Build the default brand pricing policy record for a tenant."""
    normalized_tenant_id = str(tenant_id or "").strip()
    if not normalized_tenant_id:
        raise ValueError("tenant_id is required for pricing policy")
    label = PRICING_POLICY_VERSION
    return DecisionPolicy(
        policy_version_id=f"{label}:{normalized_tenant_id}",
        policy_label=label,
        policy_id=PRICING_POLICY_ID,
        policy_version=PRICING_POLICY_SEMVER,
        policy_kind=PRICING_POLICY_KIND,
        tenant_id=normalized_tenant_id,
        effective_from=datetime(1970, 1, 1, tzinfo=UTC),
        parameters={
            "margin_floor_ratio": 0.15,
            "max_increase_pct": 0.15,
            "max_decrease_pct": 0.15,
            "price_ladder_step": 0.5,
            "extrapolation_buffer_ratio": 0.0,
        },
        declared_inputs=("price_demand_observations", "current_price", "unit_cost"),
        change_reason="Brand pricing policy with elasticity support range and margin constraints",
        approved_by="pricing_officer",
        owner_role="pricing_manager",
    )


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
    applicable_min_price: float | None = None
    applicable_max_price: float | None = None
    policy_version: str = PRICING_POLICY_VERSION

    @classmethod
    def from_policy(
        cls,
        policy: DecisionPolicy,
        *,
        unit_cost: float,
        current_price: float,
        applicable_min_price: float | None = None,
        applicable_max_price: float | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
    ) -> PriceConstraints:
        """Create PriceConstraints governed by a DecisionPolicy."""
        params = policy.parameters
        margin_floor = float(params.get("margin_floor_ratio", 0.15))
        max_inc = float(params.get("max_increase_pct", 0.15))
        max_dec = float(params.get("max_decrease_pct", 0.15))
        step = float(params.get("price_ladder_step", 0.5))
        buffer_ratio = float(params.get("extrapolation_buffer_ratio", 0.0))

        eff_min = (
            applicable_min_price * (1.0 - buffer_ratio)
            if applicable_min_price is not None
            else None
        )
        eff_max = (
            applicable_max_price * (1.0 + buffer_ratio)
            if applicable_max_price is not None
            else None
        )

        return cls(
            unit_cost=unit_cost,
            current_price=current_price,
            margin_floor_ratio=margin_floor,
            max_increase_pct=max_inc,
            max_decrease_pct=max_dec,
            price_ladder_step=step,
            min_price=min_price,
            max_price=max_price,
            applicable_min_price=round(eff_min, 4) if eff_min is not None else None,
            applicable_max_price=round(eff_max, 4) if eff_max is not None else None,
            policy_version=policy.policy_label,
        )

    @property
    def margin_floor_price(self) -> float:
        """Lowest price that still satisfies the gross-margin floor."""
        denom = max(1.0 - self.margin_floor_ratio, 1e-9)
        return round(self.unit_cost / denom, 4)

    @property
    def lower_bound(self) -> float:
        """Tightest lower bound across max-decrease, margin floor, min price and applicable min price."""
        candidates = [
            self.current_price * (1.0 - self.max_decrease_pct),
            self.margin_floor_price,
        ]
        if self.min_price is not None:
            candidates.append(self.min_price)
        if self.applicable_min_price is not None:
            candidates.append(self.applicable_min_price)
        return round(max(candidates), 4)

    @property
    def upper_bound(self) -> float:
        """Tightest upper bound across max-increase, max price and applicable max price."""
        candidates = [self.current_price * (1.0 + self.max_increase_pct)]
        if self.max_price is not None:
            candidates.append(self.max_price)
        if self.applicable_max_price is not None:
            candidates.append(self.applicable_max_price)
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
        if self.applicable_min_price is not None and price < self.applicable_min_price - 1e-9:
            breaches.append(
                ConstraintViolation(
                    code=VIOLATION_BELOW_APPLICABLE_RANGE,
                    message="price below elasticity applicable range",
                    price=price,
                    limit=self.applicable_min_price,
                )
            )
        if self.applicable_max_price is not None and price > self.applicable_max_price + 1e-9:
            breaches.append(
                ConstraintViolation(
                    code=VIOLATION_ABOVE_APPLICABLE_RANGE,
                    message="price above elasticity applicable range",
                    price=price,
                    limit=self.applicable_max_price,
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
            if self.applicable_max_price is not None and abs(self.upper_bound - self.applicable_max_price) <= 1e-6:
                binding.append("applicable_max_price_ceiling")
            elif self.max_price is not None and abs(self.upper_bound - self.max_price) <= 1e-6:
                binding.append("max_price_ceiling")
            else:
                binding.append("max_increase_delta")
        if abs(price - self.lower_bound) <= tol:
            if self.applicable_min_price is not None and abs(self.lower_bound - self.applicable_min_price) <= 1e-6:
                binding.append("applicable_min_price_floor")
            elif abs(self.lower_bound - self.margin_floor_price) <= 1e-6:
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
            "applicable_min_price": self.applicable_min_price,
            "applicable_max_price": self.applicable_max_price,
            "policy_version": self.policy_version,
            "lower_bound": self.lower_bound,
            "upper_bound": self.upper_bound,
            "margin_floor_price": self.margin_floor_price,
        }


__all__ = [
    "PRICING_POLICY_ID",
    "PRICING_POLICY_KIND",
    "PRICING_POLICY_SEMVER",
    "PRICING_POLICY_VERSION",
    "VIOLATION_ABOVE_MAX",
    "VIOLATION_ABOVE_APPLICABLE_RANGE",
    "VIOLATION_BELOW_MIN",
    "VIOLATION_BELOW_APPLICABLE_RANGE",
    "VIOLATION_MARGIN_FLOOR",
    "VIOLATION_MAX_DECREASE",
    "VIOLATION_MAX_INCREASE",
    "VIOLATION_OFF_LADDER",
    "ConstraintViolation",
    "PriceConstraints",
    "default_pricing_policy",
]

