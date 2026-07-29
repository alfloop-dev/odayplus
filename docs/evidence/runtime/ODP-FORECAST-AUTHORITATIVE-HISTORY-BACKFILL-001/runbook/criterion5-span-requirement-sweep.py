#!/usr/bin/env python3
"""How many eligible dates criterion 5 actually demands, measured from the contract.

Criterion 3 asks the view for complete 7/14/28-day per-store windows. Criterion 5
asks something else -- that ODP-PRODUCTION-MODEL-REGISTRY-001 can resume training
-- and the registry reaches the data through its own chain:

    expand_forecast_horizon_rows -> prepare_model_rows -> _temporal_split
                                 -> _segment_validation (minimum_segment_rows)

Every span number this task has produced so far was computed against the FIRST of
those, the h28 window. Nothing had asked what the LAST one needs. That gap has a
sharp edge:

  * `_temporal_split` splits on DISTINCT temporal values -- origin dates -- not on
    row counts. Adding stores does not add holdout dates.
  * `minimum_segment_rows` is applied to the HOLDOUT only. `_segment_validation`
    skips any store with fewer than that many holdout rows and fails outright if
    no store survives.
  * A store contributes at most one row per origin date per qualifying horizon,
    and the longer horizons' origins sit at the START of the span -- i.e. in the
    training partition, not the holdout.

So the holdout is roughly a fifth of the eligible origin dates, and every store
gets at most that many rows in it. A span that comfortably clears `minimum_rows`
(90 rows, trivially met by ~400 stores) can still die on `minimum_segment_rows`.

This sweep does not guess at that arithmetic. It runs the registry's OWN
functions -- imported, not restated -- over a uniform synthetic store x date grid,
increasing the eligible span until each gate clears, and reports the smallest span
that does.

WHAT THE SYNTHETIC GRID IS AND IS NOT. It is not data, it is not ingested, and
nothing here is claimed about the target. It is a parameter sweep of a contract:
the question "how many consecutive eligible dates must exist before this gate can
pass?" has an answer that depends only on the contract, and the honest way to get
it is to ask the contract. The grid is deliberately UNIFORM -- every store trades
every date -- which is the most favourable shape the real data could possibly
take. Real stores break their islands. So every number this reports is a
NECESSARY condition and a floor: criterion 5 cannot pass with fewer eligible dates
than this, however dense the store population turns out to be. It can easily need
more.

Read-only: touches no database and no cluster.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from collections import Counter
from datetime import UTC, date, datetime, timedelta

REPO = pathlib.Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO))

from modules.forecastops.model_contract import (  # noqa: E402
    FORECASTOPS_HORIZON_WEEKS,
)
from scripts.models.contracts import MODEL_SPECS  # noqa: E402
from scripts.models.forecast_training import (  # noqa: E402
    ForecastHorizonContractError,
    expand_forecast_horizon_rows,
)
from scripts.models.release import PreparedRow, _temporal_split  # noqa: E402

OUT = os.environ.get(
    "PROBE_OUT",
    str(pathlib.Path(__file__).resolve().parents[1] / "criterion5_span_requirement.json"),
)

SPEC = MODEL_SPECS["forecastops"]
TENANT = "sweep-tenant"
# Two stores is enough: the split is over dates, and in a uniform grid every
# store carries the identical per-date row set, so the per-segment holdout count
# does not depend on how many stores there are. Store COUNT only enters through
# minimum_rows, which is reported separately as rows-per-store x stores.
STORES = ("sweep-store-a", "sweep-store-b")
ANCHOR = date(2026, 1, 1)


def _daily_rows(eligible_dates: int) -> list[dict[str, object]]:
    """A uniform grid of eligible daily view rows: every store, every date."""
    rows: list[dict[str, object]] = []
    for store in STORES:
        for offset in range(eligible_dates):
            day = ANCHOR + timedelta(days=offset)
            origin = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
            rows.append(
                {
                    "tenant_id": TENANT,
                    "store_id": store,
                    "date": day,
                    "daily_net_revenue": 1000.0,
                    # Ordering constraints the expansion enforces:
                    # feature_snapshot_time < prediction_origin_time <= label_maturity_time
                    "feature_snapshot_time": origin - timedelta(days=1),
                    "prediction_origin_time": origin,
                    "label_maturity_time": origin + timedelta(days=400),
                    "source_snapshot_ids": ("sweep-snapshot",),
                }
            )
    return rows


def _measure(eligible_dates: int) -> dict[str, object]:
    """Run the real expansion + real temporal split over a span of this width."""
    result: dict[str, object] = {"eligible_dates": eligible_dates}
    try:
        expanded = expand_forecast_horizon_rows(_daily_rows(eligible_dates))
    except ForecastHorizonContractError as exc:
        result.update(
            expansion="raised",
            expansion_detail=str(exc),
            horizon_expansion_passes=False,
            minimum_rows_passes=False,
            minimum_segment_rows_passes=False,
        )
        return result

    per_store = len(expanded) // len(STORES)
    horizons_built = sorted({int(r["horizon_weeks"]) for r in expanded})
    result.update(
        expansion="built",
        horizon_expansion_passes=True,
        expanded_rows=len(expanded),
        expanded_rows_per_store=per_store,
        horizons_with_a_complete_window=horizons_built,
    )

    # prepare_model_rows' per-row gates are not re-implemented here; the uniform
    # grid is constructed to pass them, and what this sweep measures is the SHAPE
    # the split sees. PreparedRow is the real dataclass, and _temporal_split is
    # the real function.
    prepared = tuple(
        PreparedRow(
            mapping=row,
            temporal_value=datetime.combine(
                row["date"] if isinstance(row["date"], date) else ANCHOR,
                datetime.min.time(),
                tzinfo=UTC,
            ),
            segment_value=str(row["store_id"]),
        )
        for row in expanded
    )

    result["minimum_rows"] = SPEC.minimum_rows
    result["minimum_rows_passes"] = len(prepared) >= SPEC.minimum_rows

    try:
        training, holdout = _temporal_split(prepared, holdout_fraction=SPEC.holdout_fraction)
    except Exception as exc:  # ModelReadyDataError and anything it wraps
        result.update(
            temporal_split="raised",
            temporal_split_detail=str(exc),
            minimum_segment_rows_passes=False,
        )
        return result

    by_segment = Counter(r.segment_value for r in holdout)
    best = max(by_segment.values(), default=0)
    result.update(
        temporal_split="split",
        training_rows=len(training),
        holdout_rows=len(holdout),
        distinct_holdout_dates=len({r.temporal_value for r in holdout}),
        max_segment_holdout_rows=best,
        minimum_segment_rows=SPEC.minimum_segment_rows,
        minimum_segment_rows_passes=best >= SPEC.minimum_segment_rows,
    )
    return result


def _linearity_check(eligible_dates: int) -> dict[str, object]:
    """Confirm total rows scale with store count, so minimum_rows can be scaled.

    minimum_rows is the one gate whose answer depends on how many stores there
    are, and the grid deliberately carries only two. Rather than assume the
    scaling is linear, measure it: run the same span at two grid widths and check
    that rows-per-store is identical.
    """
    global STORES
    original = STORES
    try:
        STORES = ("sweep-store-a", "sweep-store-b")
        two = _measure(eligible_dates)
        STORES = ("sweep-store-a", "sweep-store-b", "sweep-store-c", "sweep-store-d")
        four = _measure(eligible_dates)
    finally:
        STORES = original
    return {
        "at_eligible_dates": eligible_dates,
        "rows_at_2_stores": two.get("expanded_rows"),
        "rows_at_4_stores": four.get("expanded_rows"),
        "rows_per_store_2": two.get("expanded_rows_per_store"),
        "rows_per_store_4": four.get("expanded_rows_per_store"),
        "linear_in_store_count": (
            two.get("expanded_rows_per_store") == four.get("expanded_rows_per_store")
            and four.get("expanded_rows") == 2 * (two.get("expanded_rows") or 0)
        ),
        "holdout_dates_unchanged_by_store_count": (
            two.get("distinct_holdout_dates") == four.get("distinct_holdout_dates")
        ),
    }


def main() -> int:
    sweep = [_measure(n) for n in range(1, 121)]
    linearity = _linearity_check(58)

    def first_pass(gate: str) -> int | None:
        for row in sweep:
            if row.get(gate):
                return int(row["eligible_dates"])
        return None

    # minimum_rows is the only gate that depends on the store population, and the
    # grid carries two stores. Scale it instead of reporting the grid's own
    # answer: rows = stores x rows_per_store (verified linear above), and
    # rows_per_store is >= 1 wherever expansion succeeds at all. So for any store
    # population of at least minimum_rows, this gate clears exactly where the
    # expansion gate does, and it never governs.
    expansion_floor = first_pass("horizon_expansion_passes")
    min_rows_store_break_even = SPEC.minimum_rows  # rows_per_store >= 1 at the floor
    minimum_rows_scaled = (
        expansion_floor
        if expansion_floor is not None
        else None
    )

    binding = {
        "horizon_expansion_and_row_gates": expansion_floor,
        "minimum_rows_at_two_store_grid": first_pass("minimum_rows_passes"),
        "minimum_rows_scaled_to_population": minimum_rows_scaled,
        "minimum_segment_rows": first_pass("minimum_segment_rows_passes"),
    }
    store_count_independent = {
        "horizon_expansion_and_row_gates": binding["horizon_expansion_and_row_gates"],
        "minimum_rows_scaled_to_population": binding["minimum_rows_scaled_to_population"],
        "minimum_segment_rows": binding["minimum_segment_rows"],
    }
    governing = max(v for v in store_count_independent.values() if v is not None)
    governing_gate = [k for k, v in store_count_independent.items() if v == governing]

    receipt = {
        "artifact": "criterion5_span_requirement",
        "task": "ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001",
        "captured_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "purpose": (
            "The eligible span criterion 5 requires, derived by running the registry's "
            "own horizon expansion and temporal split rather than by reasoning about "
            "them. Criterion 3's h28 window is a much weaker condition and every span "
            "number produced for this task so far was computed against that one."
        ),
        "method": {
            "plane": "contract only -- no database, no cluster, read-only",
            "imported_under_test": [
                "scripts.models.forecast_training.expand_forecast_horizon_rows",
                "scripts.models.release._temporal_split",
                "scripts.models.release.PreparedRow",
                "scripts.models.contracts.MODEL_SPECS['forecastops']",
            ],
            "input": (
                "A UNIFORM synthetic store x date grid. Not data, not ingested, and "
                "nothing is claimed about the target from it. Uniform is the most "
                "favourable shape real data can take -- every store trades every date "
                "-- so every span below is a NECESSARY condition and a floor. Real "
                "stores break their islands and can only need more."
            ),
            "stores_in_grid": len(STORES),
            "sweep_range": "1..120 consecutive eligible dates",
        },
        "contract": {
            "horizon_weeks": list(FORECASTOPS_HORIZON_WEEKS),
            "shortest_horizon_days": min(FORECASTOPS_HORIZON_WEEKS) * 7,
            "minimum_rows": SPEC.minimum_rows,
            "minimum_segment_rows": SPEC.minimum_segment_rows,
            "holdout_fraction": SPEC.holdout_fraction,
            "segment_column": SPEC.segment_column,
            "temporal_column": SPEC.temporal_column,
        },
        "why_the_last_gate_binds": (
            "_temporal_split splits on DISTINCT temporal values (origin dates), not on "
            "row counts, so adding stores never adds holdout dates. "
            "_segment_validation then requires some single store to carry at least "
            "minimum_segment_rows rows IN THE HOLDOUT, and a store contributes at most "
            "one row per origin date per qualifying horizon. The longer horizons' "
            "origins sit at the start of the span, i.e. in the training partition. So "
            "the per-store holdout count is bounded by the number of holdout DATES, "
            "which is about a fifth of the eligible origin dates."
        ),
        "minimum_eligible_dates_by_gate": binding,
        "store_count_scaling": {
            "linearity_check": linearity,
            "why": (
                "minimum_rows is the only gate whose answer depends on the store "
                "population, and the grid carries two stores. Its grid answer is "
                "reported but excluded from the governing gate. Rows scale linearly "
                "with store count (measured above) while holdout DATES do not move at "
                "all, so for any population of at least minimum_rows stores the gate "
                "clears wherever the expansion gate does and never governs. The landed "
                "population is in the hundreds."
            ),
            "store_break_even": min_rows_store_break_even,
        },
        "governing_gate": governing_gate,
        "minimum_eligible_dates": governing,
        "translation": {
            "eligible_dates_to_attested_days": (
                "prior_day_count_28 = 28, so N contiguous attested days yield N-28 "
                "eligible dates. The required contiguous attested span is therefore "
                "minimum_eligible_dates + 28."
            ),
            "required_contiguous_attested_days": governing + 28,
        },
        "sweep": sweep,
        "limits": (
            "A floor, not a forecast. It says criterion 5 cannot pass below this span; "
            "it does not say it passes at this span, because that additionally needs "
            "some real store to trade every one of the holdout dates. It is also a "
            "statement about today's contract: it is falsified by a change to "
            "FORECASTOPS_HORIZON_WEEKS, holdout_fraction, minimum_segment_rows or the "
            "split rule, so re-run it after any of those move."
        ),
    }

    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(receipt, handle, indent=2)
    print(f"wrote {OUT}")
    print(f"governing gate {governing_gate} needs {governing} eligible dates")
    print(f"= {governing + 28} contiguous attested days")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
