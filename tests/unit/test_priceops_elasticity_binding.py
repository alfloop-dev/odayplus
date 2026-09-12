"""Unit coverage for the elasticity applicable-range rule (ODP-PRICEOPS-ELASTICITY-SCOPE-002).

``resolve_elasticity`` returns the price window the elasticity is defensible
over. For an estimated elasticity that window is the observed price support of
the regression. Client-supplied ``applicable_min_price`` / ``applicable_max_price``
may narrow it but must never widen it, otherwise a payload could silently
re-authorise extrapolation beyond the prices the estimator actually saw.
"""

from __future__ import annotations

import math

import pytest

from models.priceops.binding import ElasticityInputError, resolve_elasticity


def _observations() -> list[dict[str, float]]:
    # log(q) = 5.0 - 1.5 * log(p) over the price support [3.9, 4.3]
    return [
        {"price": p, "demand": math.exp(5.0) * (p**-1.5)}
        for p in (3.9, 4.0, 4.1, 4.2, 4.3)
    ]


def test_estimated_range_ignores_wider_supplied_bounds() -> None:
    estimate, binding = resolve_elasticity(
        current_price=4.0,
        observations=_observations(),
        supplied_min_price=1.0,
        supplied_max_price=100.0,
    )
    assert binding["elasticity_source"] == "estimated"
    assert estimate.applicable_min_price == 3.9
    assert estimate.applicable_max_price == 4.3
    assert binding["applicable_min_price"] == 3.9
    assert binding["applicable_max_price"] == 4.3


def test_estimated_range_honours_narrower_supplied_bounds() -> None:
    estimate, _binding = resolve_elasticity(
        current_price=4.0,
        observations=_observations(),
        supplied_min_price=4.0,
        supplied_max_price=4.2,
    )
    assert estimate.applicable_min_price == 4.0
    assert estimate.applicable_max_price == 4.2


def test_estimated_range_narrows_one_side_only() -> None:
    estimate, _binding = resolve_elasticity(
        current_price=4.0,
        observations=_observations(),
        supplied_max_price=4.1,
    )
    assert estimate.applicable_min_price == 3.9
    assert estimate.applicable_max_price == 4.1


def test_supplied_bounds_disjoint_from_fitted_support_fail_closed() -> None:
    with pytest.raises(ElasticityInputError, match="do not overlap"):
        resolve_elasticity(
            current_price=4.0,
            observations=_observations(),
            supplied_min_price=10.0,
            supplied_max_price=20.0,
        )


def test_non_positive_supplied_bounds_fail_closed_on_estimated_path() -> None:
    with pytest.raises(ElasticityInputError, match="must be positive"):
        resolve_elasticity(
            current_price=4.0,
            observations=_observations(),
            supplied_min_price=0.0,
            supplied_max_price=4.2,
        )


def test_inverted_supplied_bounds_fail_closed_on_estimated_path() -> None:
    with pytest.raises(ElasticityInputError, match="invalid"):
        resolve_elasticity(
            current_price=4.0,
            observations=_observations(),
            supplied_min_price=4.3,
            supplied_max_price=3.9,
        )


def test_client_supplied_range_is_the_supplied_window() -> None:
    estimate, binding = resolve_elasticity(
        current_price=4.0,
        observations=None,
        supplied_value=-1.2,
        supplied_min_price=3.0,
        supplied_max_price=5.0,
    )
    assert binding["elasticity_source"] == "client_supplied"
    assert estimate.applicable_min_price == 3.0
    assert estimate.applicable_max_price == 5.0
