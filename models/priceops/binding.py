"""Elasticity binding layer for PriceOps (ODP-GAP-ML-003).

Bridges the price-elasticity estimator (``models.priceops.elasticity``) into the
PriceOps decision service. A pricing plan item must carry a defensible
elasticity before it can be simulated or optimized. This layer resolves that
value from one of two sources, in priority order:

1. **estimated** — enough live ``(price, demand)`` observations are present to
   run the log-log regression estimator.
2. **client_supplied** — the caller provides an elasticity value directly.

If neither source is available the binding *fails closed* by raising
:class:`ElasticityInputError`; callers surface this as HTTP 422 rather than
silently fabricating a demand curve.

Every resolved estimate also carries an **applicable price range** -- the window
the elasticity is defensible over. For an estimated elasticity that window is the
observed price support of the regression; outside it the demand curve is an
extrapolation, not a fit. Client-supplied applicable bounds may only *narrow*
that window (see :func:`_narrowed_support_range`); a payload can never widen it
back out, because that would silently re-authorise the very extrapolation the
range exists to flag.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from models.priceops.elasticity import MIN_SAMPLES, estimate_elasticity
from modules.priceops.domain.pricing import PriceElasticityEstimate

# A live-data estimate needs at least this many usable observations; below the
# threshold the estimator would only echo its own low-confidence fallback, which
# is not a defensible substitute for a real signal.
MIN_OBSERVATIONS = MIN_SAMPLES

DEFAULT_SUPPLIED_CONFIDENCE = 0.9


class ElasticityInputError(ValueError):
    """No usable elasticity signal for a plan item — fail closed."""


def _usable_observations(
    observations: list[dict[str, float]] | None,
) -> list[dict[str, float]]:
    if not observations:
        return []
    return [
        pt
        for pt in observations
        if float(pt.get("price", 0.0)) > 0 and float(pt.get("demand", 0.0)) > 0
    ]


def _reject_non_positive_bounds(
    supplied_min: float | None, supplied_max: float | None
) -> None:
    for label, value in (
        ("applicable_min_price", supplied_min),
        ("applicable_max_price", supplied_max),
    ):
        if value is not None and value <= 0:
            raise ElasticityInputError(
                f"client-supplied {label} must be positive: {value}"
            )
    if supplied_min is not None and supplied_max is not None and supplied_min > supplied_max:
        raise ElasticityInputError(
            f"client-supplied applicable bounds invalid: min={supplied_min} > max={supplied_max}"
        )


def _narrowed_support_range(
    *,
    fitted_min: float | None,
    fitted_max: float | None,
    supplied_min: float | None,
    supplied_max: float | None,
) -> tuple[float | None, float | None]:
    """Intersect the fitted support range with client-supplied applicable bounds.

    The fitted range is the observed price support of the regression. A caller
    may narrow it -- they may have a business reason to stay closer to today's
    price -- but may never widen it: the estimator has no evidence outside the
    prices it actually saw. The result is therefore the intersection, and a
    request whose supplied window does not overlap the fitted one fails closed.
    """
    _reject_non_positive_bounds(supplied_min, supplied_max)

    low = fitted_min
    if supplied_min is not None:
        low = supplied_min if low is None else max(low, supplied_min)
    high = fitted_max
    if supplied_max is not None:
        high = supplied_max if high is None else min(high, supplied_max)

    if low is not None and high is not None and low > high:
        raise ElasticityInputError(
            "client-supplied applicable bounds do not overlap the estimated support "
            f"range: supplied=[{supplied_min}, {supplied_max}] "
            f"estimated=[{fitted_min}, {fitted_max}]"
        )
    return low, high


def resolve_elasticity(
    *,
    current_price: float,
    observations: list[dict[str, float]] | None = None,
    supplied_value: float | None = None,
    supplied_confidence: float | None = None,
    supplied_min_price: float | None = None,
    supplied_max_price: float | None = None,
    horizon: str = "4week",
    prediction_origin_time: datetime | None = None,
) -> tuple[PriceElasticityEstimate, dict[str, Any]]:
    """Resolve a :class:`PriceElasticityEstimate` and its binding metadata.

    Raises :class:`ElasticityInputError` when live observations are insufficient
    *and* no client-supplied value is available.
    """
    usable = _usable_observations(observations)

    if len(usable) >= MIN_OBSERVATIONS:
        estimate = estimate_elasticity(
            usable,
            current_price=current_price,
            prediction_origin_time=prediction_origin_time,
        )
        if horizon and estimate.horizon != horizon:
            estimate = replace(estimate, horizon=horizon)
        applicable_min, applicable_max = _narrowed_support_range(
            fitted_min=estimate.applicable_min_price,
            fitted_max=estimate.applicable_max_price,
            supplied_min=supplied_min_price,
            supplied_max=supplied_max_price,
        )
        estimate = replace(
            estimate,
            applicable_min_price=applicable_min,
            applicable_max_price=applicable_max,
        )
        return estimate, _binding_metadata("estimated", estimate, len(usable))

    if supplied_value is not None:
        if supplied_min_price is None or supplied_max_price is None:
            raise ElasticityInputError(
                "client-supplied elasticity requires applicable_min_price and applicable_max_price"
            )
        _reject_non_positive_bounds(supplied_min_price, supplied_max_price)
        estimate = PriceElasticityEstimate(
            elasticity_value=supplied_value,
            confidence=(
                supplied_confidence
                if supplied_confidence is not None
                else DEFAULT_SUPPLIED_CONFIDENCE
            ),
            applicable_min_price=supplied_min_price,
            applicable_max_price=supplied_max_price,
            horizon=horizon,
            prediction_origin_time=prediction_origin_time or datetime.now(UTC),
        )
        return estimate, _binding_metadata("client_supplied", estimate, len(usable))

    raise ElasticityInputError(
        f"cannot bind elasticity: {len(usable)} usable observation(s) "
        f"(need >= {MIN_OBSERVATIONS}) and no client-supplied elasticity_value"
    )


def _binding_metadata(
    source: str, estimate: PriceElasticityEstimate, sample_size: int
) -> dict[str, Any]:
    return {
        "elasticity_source": source,
        "model_version": estimate.model_version,
        "feature_version": estimate.feature_version,
        "sample_size": sample_size,
        "elasticity_value": estimate.elasticity_value,
        "confidence": estimate.confidence,
        "applicable_min_price": estimate.applicable_min_price,
        "applicable_max_price": estimate.applicable_max_price,
        "horizon": estimate.horizon,
    }


__all__ = [
    "DEFAULT_SUPPLIED_CONFIDENCE",
    "ElasticityInputError",
    "MIN_OBSERVATIONS",
    "resolve_elasticity",
]
