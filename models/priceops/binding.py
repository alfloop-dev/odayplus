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


def resolve_elasticity(
    *,
    current_price: float,
    observations: list[dict[str, float]] | None = None,
    supplied_value: float | None = None,
    supplied_confidence: float | None = None,
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
        return estimate, _binding_metadata("estimated", estimate, len(usable))

    if supplied_value is not None:
        estimate = PriceElasticityEstimate(
            elasticity_value=supplied_value,
            confidence=(
                supplied_confidence
                if supplied_confidence is not None
                else DEFAULT_SUPPLIED_CONFIDENCE
            ),
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
        "horizon": estimate.horizon,
    }


__all__ = [
    "DEFAULT_SUPPLIED_CONFIDENCE",
    "ElasticityInputError",
    "MIN_OBSERVATIONS",
    "resolve_elasticity",
]
