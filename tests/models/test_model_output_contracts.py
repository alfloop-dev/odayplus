from __future__ import annotations

import pytest

from models.shared_ml.output_contracts import (
    HEATZONE_OUTPUT_TRANSFORM,
    SITESCORE_OUTPUT_TRANSFORM,
    ModelOutputContractError,
    heatzone_revenue_to_priority,
    sitescore_90d_sum_to_monthly,
)


def test_sitescore_90_day_revenue_converts_to_mature_monthly_unit() -> None:
    assert sitescore_90d_sum_to_monthly(
        (0.0, 90_000.0, 180_000.0),
        contract=SITESCORE_OUTPUT_TRANSFORM,
    ) == pytest.approx((0.0, 30_437.5, 60_875.0))


def test_heatzone_revenue_becomes_calibrated_priority_with_average_ties() -> None:
    assert heatzone_revenue_to_priority(
        (50_000.0, 10_000.0, 50_000.0, 90_000.0),
        contract=HEATZONE_OUTPUT_TRANSFORM,
    ) == pytest.approx((50.0, 0.0, 50.0, 100.0))


def test_runtime_rejects_unversioned_or_mismatched_output_semantics() -> None:
    with pytest.raises(ModelOutputContractError, match="does not match"):
        sitescore_90d_sum_to_monthly(
            (90_000.0,),
            contract={
                **SITESCORE_OUTPUT_TRANSFORM,
                "input_unit": "TWD_NET_REVENUE_MONTHLY",
            },
        )
