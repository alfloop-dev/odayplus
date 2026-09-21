"""Published-contract rows for the HZ-004 absorption recording entry.

These build `oday.store-daily-performance.v1` and
`oday.operational-start-observation.v1` payloads in the shape the platform
actually publishes, because the recording route computes absorption from them
rather than accepting a computed outcome. A helper that emitted a convenient
shortcut would let a test record evidence that production could not.

The day loop is deliberate: `assemble_zone_absorption` refuses a window with any
missing business date, so a fixture that skipped days would fail closed and the
test would prove nothing about the writer.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

#: Long enough to clear the seeded `min_observation_days` of 180, so the stores
#: are past their ramp and their revenue counts as absorption.
OPENED_ON = date(2025, 1, 1)

_TIME_CONTRACT: dict[str, Any] = {
    "contract_version": "emgi.time-contract.v4",
    "materialization_kind": "observation",
    "materialization_environment": "development",
    "store_timezone": "Asia/Taipei",
}


def business_dates(window_start: date, window_end: date) -> list[date]:
    return [
        window_start + timedelta(days=offset)
        for offset in range((window_end - window_start).days + 1)
    ]


def performance_rows(
    *,
    store_ids: tuple[str, ...],
    window_start: date,
    window_end: date,
    daily_revenue: float,
    fingerprint: str = "sdp-fingerprint",
) -> list[dict[str, Any]]:
    """One complete, traceable store-day per store per date in the window."""
    rows: list[dict[str, Any]] = []
    for store_id in store_ids:
        for business_date in business_dates(window_start, window_end):
            key = business_date.isoformat()
            rows.append(
                {
                    "business_date": key,
                    "coverage_id": f"cov-{store_id}-{key}",
                    "coverage_state": "complete",
                    "is_complete": True,
                    # The basis snapshot id the writer records comes from here,
                    # which is what makes a recorded outcome traceable.
                    "raw_contract_fingerprint": f"{fingerprint}-{store_id}-{key}",
                    "store_id": store_id,
                    "time_contract": dict(_TIME_CONTRACT),
                    "window_start": f"{key}T00:00:00+08:00",
                    "window_end": f"{key}T23:59:59+08:00",
                    "paid_amount": daily_revenue,
                    "gross_amount": daily_revenue,
                    "transaction_count": 40,
                    "is_valid_zero": False,
                }
            )
    return rows


def operational_start_rows(
    *,
    store_ids: tuple[str, ...],
    window_start: date,
    window_end: date,
    opened_on: date = OPENED_ON,
    method: str = "FIRST_OBSERVED_TRANSACTION",
    confidence: str = "HIGH",
) -> list[dict[str, Any]]:
    """An observed -- not declared -- start date per store.

    The seeded absorption policy refuses `DECLARED` starts and LOW/UNKNOWN
    confidence, so these default to the admissible combination and a test that
    wants the refusal passes the inadmissible one explicitly.
    """
    return [
        {
            "method": method,
            "confidence": confidence,
            "observation_window_start": window_start.isoformat(),
            "observation_window_end": window_end.isoformat(),
            "observed_start_business_date": opened_on.isoformat(),
            "store_id": store_id,
            "time_contract": dict(_TIME_CONTRACT),
            "is_left_censored": False,
        }
        for store_id in store_ids
    ]


def outcome_request(
    *,
    cell_id: str,
    window_start: date,
    window_end: date,
    store_ids: tuple[str, ...] = ("store-1", "store-2"),
    original_demand: float = 100_000.0,
    daily_revenue: float = 500.0,
    barrier_side: str | None = None,
    barrier_description: str = "",
    method: str = "FIRST_OBSERVED_TRANSACTION",
    confidence: str = "HIGH",
    fingerprint: str = "sdp-fingerprint",
) -> dict[str, Any]:
    """A complete request body for POST /heatzones/absorption/outcomes."""
    body: dict[str, Any] = {
        "cell_id": cell_id,
        "period_start": window_start.isoformat(),
        "period_end": window_end.isoformat(),
        "original_demand": original_demand,
        "store_ids": list(store_ids),
        "performances": performance_rows(
            store_ids=store_ids,
            window_start=window_start,
            window_end=window_end,
            daily_revenue=daily_revenue,
            fingerprint=fingerprint,
        ),
        "operational_starts": operational_start_rows(
            store_ids=store_ids,
            window_start=window_start,
            window_end=window_end,
            method=method,
            confidence=confidence,
        ),
    }
    if barrier_side is not None:
        body["barrier_side"] = barrier_side
        body["barrier_description"] = barrier_description
    return body


__all__ = [
    "OPENED_ON",
    "business_dates",
    "operational_start_rows",
    "outcome_request",
    "performance_rows",
]
