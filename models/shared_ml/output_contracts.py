from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

SITESCORE_OUTPUT_TRANSFORM: Mapping[str, Any] = {
    "version": "sitescore-90d-net-revenue-to-mature-monthly-v1",
    "kind": "fixed_horizon_sum_to_monthly_rate",
    "input_unit": "TWD_NET_REVENUE_90D",
    "output_unit": "TWD_NET_REVENUE_MONTHLY",
    "horizon_days": 90,
    "days_per_month": 30.4375,
}

HEATZONE_OUTPUT_TRANSFORM: Mapping[str, Any] = {
    "version": "heatzone-28d-revenue-percentile-priority-v1",
    "kind": "batch_percentile_rank",
    "input_unit": "TWD_NET_REVENUE_28D",
    "output_unit": "PRIORITY_SCORE_0_100",
    "direction": "higher_is_better",
    "tie_method": "average",
}


class ModelOutputContractError(ValueError):
    """Raised when registered output semantics do not match domain semantics."""


def require_output_contract(
    observed: Mapping[str, Any] | None,
    expected: Mapping[str, Any],
) -> Mapping[str, Any]:
    if not isinstance(observed, Mapping):
        raise ModelOutputContractError("registered model output transform is missing")
    mismatches = {
        key: (observed.get(key), value)
        for key, value in expected.items()
        if observed.get(key) != value
    }
    if mismatches:
        raise ModelOutputContractError(
            f"registered model output transform does not match domain contract: {mismatches}"
        )
    return observed


def sitescore_90d_sum_to_monthly(
    values: Sequence[float],
    *,
    contract: Mapping[str, Any],
) -> tuple[float, ...]:
    require_output_contract(contract, SITESCORE_OUTPUT_TRANSFORM)
    horizon_days = float(contract["horizon_days"])
    days_per_month = float(contract["days_per_month"])
    if horizon_days <= 0 or days_per_month <= 0:
        raise ModelOutputContractError("SiteScore output transform has invalid duration")
    factor = days_per_month / horizon_days
    return tuple(max(0.0, float(value)) * factor for value in values)


def heatzone_revenue_to_priority(
    values: Sequence[float],
    *,
    contract: Mapping[str, Any],
) -> tuple[float, ...]:
    require_output_contract(contract, HEATZONE_OUTPUT_TRANSFORM)
    numeric = tuple(float(value) for value in values)
    if not numeric:
        return ()
    ordered = sorted(numeric)
    if len(ordered) == 1:
        return (50.0,)
    rank_bounds: dict[float, list[int]] = {}
    for index, value in enumerate(ordered):
        bounds = rank_bounds.setdefault(value, [index, index])
        bounds[1] = index
    denominator = float(len(ordered) - 1)
    return tuple(
        100.0 * ((rank_bounds[value][0] + rank_bounds[value][1]) / 2.0) / denominator
        for value in numeric
    )


__all__ = [
    "HEATZONE_OUTPUT_TRANSFORM",
    "ModelOutputContractError",
    "SITESCORE_OUTPUT_TRANSFORM",
    "heatzone_revenue_to_priority",
    "require_output_contract",
    "sitescore_90d_sum_to_monthly",
]
