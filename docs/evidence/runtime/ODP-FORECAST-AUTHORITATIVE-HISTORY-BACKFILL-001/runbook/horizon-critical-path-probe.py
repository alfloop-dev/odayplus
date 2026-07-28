"""Measure which remaining slice actually decides acceptance criterion 3.

Motivation. The queue driver v4 runs is `-s4, -b1, -b2, -b3, -b4, -s5`, and each
slice costs roughly five hours. That ordering was inherited: `-s4`/`-s5` finish
the ORIGINAL gap-fill plan (2026-07-06..2026-07-22), while `-b1`..`-b4` are the
backwards extension added after the 2026-05-23 floor turned out to be a
window-clamp artifact. Nobody had measured which of the two families actually
moves criterion 3 -- "forecast_training_view produces complete 7/14/28-day
per-store windows" -- so this probe asks it directly instead of reasoning from
the day counts.

The distinction matters because `prior_day_count_28` is NOT filtered on
attestation. `transaction_daily` computes `lineage_complete` and
`source_run_complete` per (store, date) and the final SELECT applies them to the
TARGET row only; the `prior` side of the point-in-time LATERAL joins
`mature_daily` with no such predicate. So a date is eligible when

    it is itself attested  AND  the store transacted on all 28 preceding dates

which means the two permanent Defect D holes (2026-07-05, 2026-07-06) do not
erase the days around them from anyone's 28-day prior window -- they only lose
their own eligibility, cutting each store's eligible-date island in two. A
horizon window of `h` days is `h` consecutive eligible dates for one store
(`forecast_horizon_windows` does gaps-and-islands over `is_training_eligible`),
so what matters is the length of the longest island, not the total.

Method. Mirrors `model_ready.forecast_training_view` on the SOURCE (PG15) plane,
because the PG16 target has not been re-activated since `-s3` landed and
re-activating mid-flight is forbidden. Same transaction predicate, same
`bool_and` attestation, same 28-day prior count, same gaps-and-islands. Then it
re-runs the island computation under two counterfactuals that add no data and
change no predicate -- they only mark dates as present/attested -- to attribute
the outcome to a slice:

  * `landed`         -- what is true right now.
  * `+s4,+s5`        -- the gap-fill family completes (2026-07-12..2026-07-22).
  * `+b1..+b3`       -- the backwards family completes (2026-05-05..2026-05-23).

Counterfactual days carry an assumption about WHICH STORES transact on them,
and that assumption is where the first version of this probe went wrong. It
donated the store set of the nearest landed date; for every backwards date the
nearest landed date is 2026-05-22, which holds 3 stores and 3 transactions --
timezone-edge stragglers pulled in by the 2026-05-23 window's lower bound, not a
trading day. Every backwards scenario therefore extended exactly 3 stores and
reported h28 = 3, which is a fact about this probe and not about the plan.

Donor dates are now restricted to DENSE landed days (store count at least half
the landed median), and the projection is BRACKETED rather than pointwise:

  * optimistic -- a store gets the counterfactual day if it traded on the
    nearest dense landed day.
  * strict     -- only if it traded on EVERY one of the nearest 7 dense landed
    days, i.e. it is continuously active at the boundary.

Both are projections of ISLAND LENGTH, not claims about revenue. The `landed`
block is the only measured block and is reported separately.

Read-only. Selects only; no writes, no DDL, no job mutation.

Usage: source /tmp/odp-forecast-dsn.env && python3 horizon-critical-path-probe.py
"""

import json
import os
from collections import defaultdict
from datetime import date, timedelta
from datetime import datetime, timezone

import psycopg

OUT = os.environ.get(
    "PROBE_OUT",
    "/tmp/odp-forecast-evidence/horizon_critical_path.json",
)

# Mirrors transaction_source -> transaction_daily. One row per (store, date):
# whether every qualifying transaction on it carries lineage, and whether every
# owning ingestion run finished SUCCEEDED.
DAILY_SQL = """
WITH src AS (
    SELECT
        store.tenant_id,
        txn.transaction_id,
        txn.store_id,
        (txn.event_time AT TIME ZONE 'UTC')::date AS d,
        source.source_run_complete,
        coalesce(cardinality(source.source_snapshot_ids), 0) > 0 AS has_lineage
    FROM core.transactions AS txn
    INNER JOIN core.stores AS store ON store.store_id = txn.store_id
    LEFT JOIN LATERAL (
        SELECT
            array_agg(DISTINCT lineage.source_snapshot_id::text)
                AS source_snapshot_ids,
            bool_and(
                ingestion.status = 'SUCCEEDED'
                AND ingestion.finished_at IS NOT NULL
            ) AS source_run_complete
        FROM data_plane.canonical_lineage AS lineage
        INNER JOIN data_plane.ingestion_runs AS ingestion
            ON ingestion.run_id = lineage.run_id
        WHERE lineage.tenant_id = store.tenant_id
          AND lineage.canonical_table = 'core.transactions'
          AND lineage.canonical_id = txn.transaction_id
    ) AS source ON TRUE
    WHERE txn.transaction_status = 'succeeded'
      AND txn.currency = 'TWD'
)
SELECT
    store_id::text,
    d,
    coalesce(bool_and(has_lineage), FALSE)
        AND coalesce(bool_and(source_run_complete), FALSE) AS attested
FROM src
GROUP BY store_id, d
ORDER BY store_id, d;
"""

# The two Defect D days: ingested, permanently unattested, no re-run can repair
# them (README section 4). They are the split, and no counterfactual heals them.
DEFECT_D_DAYS = (date(2026, 7, 5), date(2026, 7, 6))

# Windows the suspended Jobs would ingest, read off their applied manifests
# (ODP_ORDERS_HISTORY_START/END, end-exclusive).
SLICE_WINDOWS = {
    "s4": (date(2026, 7, 12), date(2026, 7, 18)),
    "s5": (date(2026, 7, 18), date(2026, 7, 23)),
    "b1": (date(2026, 5, 17), date(2026, 5, 23)),
    "b2": (date(2026, 5, 11), date(2026, 5, 17)),
    "b3": (date(2026, 5, 5), date(2026, 5, 11)),
    "b4": (date(2026, 4, 29), date(2026, 5, 5)),
}

HORIZONS = (7, 14, 28, 56, 84, 168)


def islands(dates):
    """Longest run of consecutive dates, and every run length. Mirrors the
    date - row_number() gaps-and-islands grouping in forecast_horizon_windows."""
    runs = []
    run = 0
    prev = None
    for d in sorted(dates):
        if prev is not None and d == prev + timedelta(days=1):
            run += 1
        else:
            if run:
                runs.append(run)
            run = 1
        prev = d
    if run:
        runs.append(run)
    return runs


def eligible_dates(present, attested):
    """A date is eligible when it is itself attested and the store transacted on
    all 28 preceding dates -- the view's prior_day_count_28 = 28, which is
    counted over mature_daily with NO attestation filter."""
    out = []
    for d in sorted(present):
        if d not in attested:
            continue
        prior = sum(1 for k in range(1, 29) if (d - timedelta(days=k)) in present)
        if prior == 28:
            out.append(d)
    return out


def evaluate(per_store):
    """Longest eligible island per store -> the horizon window counts the
    activation's `verify` would report."""
    streaks = {}
    for store, (present, attested) in per_store.items():
        runs = islands(eligible_dates(present, attested))
        streaks[store] = max(runs) if runs else 0
    return {
        "stores_with_any_eligible_date": sum(1 for v in streaks.values() if v),
        "max_streak_days": max(streaks.values()) if streaks else 0,
        "horizon_windows": {
            f"h{h}": sum(1 for v in streaks.values() if v >= h) for h in HORIZONS
        },
    }, streaks


def dense_dates(base):
    """Landed dates carrying at least half the median store count. Excludes
    timezone-edge stragglers such as 2026-05-22 (3 stores), which are not
    trading days and must never be used as a behavioural donor."""
    per_date = defaultdict(int)
    for present, _ in base.values():
        for d in present:
            per_date[d] += 1
    counts = sorted(per_date.values())
    median = counts[len(counts) // 2]
    return sorted(d for d, c in per_date.items() if c >= median / 2), per_date


def apply_slices(base, names, dense, strict):
    """Counterfactual: mark a slice's window as ingested AND attested for stores
    projected to be trading then. Adds no revenue and changes no predicate; it
    projects island LENGTH only.

    optimistic -- traded on the nearest dense landed day.
    strict     -- traded on every one of the nearest 7 dense landed days."""
    per_store = {s: (set(p), set(a)) for s, (p, a) in base.items()}
    for name in names:
        start, end = SLICE_WINDOWS[name]
        d = start
        while d < end:
            if d not in DEFECT_D_DAYS:
                donors = sorted(dense, key=lambda x: abs((x - d).days))
                donors = donors[:7] if strict else donors[:1]
                for store, (present, attested) in per_store.items():
                    landed_present = base[store][0]
                    if all(x in landed_present for x in donors):
                        present.add(d)
                        attested.add(d)
            d += timedelta(days=1)
    return per_store


def load_daily():
    """Fetch the per-(store, date) attestation grid, caching it because the
    LATERAL attestation join costs ~3.5 min and the scenarios are pure CPU."""
    cache = os.environ.get("PROBE_CACHE", "/tmp/odp-horizon-daily-cache.json")
    if os.path.exists(cache) and os.environ.get("PROBE_REFRESH") != "1":
        with open(cache, encoding="utf-8") as fh:
            raw = json.load(fh)
        return [(s, date.fromisoformat(d), a) for s, d, a in raw["rows"]], raw["fetched_at"]
    dsn = os.environ["ODP_LEGACY_DATABASE_URL"]
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(DAILY_SQL)
        rows = cur.fetchall()
    fetched_at = datetime.now(timezone.utc).isoformat()
    with open(cache, "w", encoding="utf-8") as fh:
        json.dump(
            {"fetched_at": fetched_at,
             "rows": [[s, str(d), a] for s, d, a in rows]},
            fh,
        )
    return rows, fetched_at


def main():
    rows, fetched_at = load_daily()

    base = defaultdict(lambda: (set(), set()))
    for store, d, attested in rows:
        present, att = base[store]
        present.add(d)
        if attested:
            att.add(d)
    base = dict(base)
    dense, per_date_stores = dense_dates(base)

    landed, landed_streaks = evaluate(base)
    scenarios = {"landed_measured": landed}
    for label, names in (
        ("plus_gapfill_s4_s5", ("s4", "s5")),
        ("plus_backwards_b1_b2_b3", ("b1", "b2", "b3")),
        ("plus_backwards_b1_b2_b3_b4", ("b1", "b2", "b3", "b4")),
        ("plus_all_remaining", ("s4", "s5", "b1", "b2", "b3", "b4")),
    ):
        for rule, strict in (("optimistic", False), ("strict", True)):
            scenarios[f"{label}__{rule}"], _ = evaluate(
                apply_slices(base, names, dense, strict)
            )

    all_dates = sorted({d for p, _ in base.values() for d in p})
    attested_dates = sorted(
        {d for _, a in base.values() for d in a}
        - set(DEFECT_D_DAYS)
    )

    receipt = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "purpose": (
            "Attribute acceptance criterion 3 to a specific slice. Measures the "
            "landed per-store eligible-date islands, then re-computes them under "
            "slice-completion counterfactuals to show which remaining family of "
            "Jobs can produce a 28-day horizon window and which cannot."
        ),
        "method": {
            "plane": "SOURCE PG15 (target PG16 not re-activated since -s3; "
                     "re-activating while a partition is RUNNING is forbidden)",
            "eligibility": "target date attested (lineage_complete AND "
                           "source_run_complete) AND prior_day_count_28 = 28, "
                           "where the prior count has NO attestation filter -- "
                           "matching the view's point_in_time LATERAL",
            "horizon_window": "h consecutive eligible dates for one store "
                              "(gaps-and-islands, as forecast_horizon_windows)",
            "counterfactual": "a slice's window is marked ingested+attested for "
                              "stores projected to be trading then; it adds no "
                              "revenue and is a projection of island LENGTH "
                              "only. Defect D days 2026-07-05/07-06 are never "
                              "healed by any counterfactual.",
            "donor_rule": "optimistic = traded on the nearest DENSE landed day; "
                          "strict = traded on all of the nearest 7 dense landed "
                          "days. Dense = store count >= half the landed median, "
                          "which excludes timezone-edge stragglers such as "
                          "2026-05-22 (3 stores). Donating from 2026-05-22 is "
                          "what made this probe's first run report h28 = 3 for "
                          "every backwards scenario.",
            "measured_vs_projected": "landed_measured is measured; every other "
                                     "scenario is projected and bracketed",
            "daily_sql": " ".join(DAILY_SQL.split()),
            "source_fetched_at": fetched_at,
        },
        "landed_per_store_streaks": {
            "note": "longest run of consecutive ELIGIBLE dates per store, landed. "
                    "This is the quantity horizon windows are cut from.",
            "stores": len(landed_streaks),
            "distribution": {
                f"ge_{k}": sum(1 for v in landed_streaks.values() if v >= k)
                for k in (1, 7, 14, 21, 23, 28)
            },
        },
        "landed_sparse_days_excluded_as_donors": [
            {"date": str(d), "stores": c}
            for d, c in sorted(per_date_stores.items())
            if d not in set(dense)
        ],
        "landed_span": {
            "ingested_first": str(all_dates[0]) if all_dates else None,
            "ingested_last": str(all_dates[-1]) if all_dates else None,
            "ingested_day_count": len(all_dates),
            "attested_day_count": len(attested_dates),
            "permanent_unattested_days": [str(d) for d in DEFECT_D_DAYS],
        },
        "slice_windows": {
            k: {"start": str(v[0]), "end_exclusive": str(v[1])}
            for k, v in SLICE_WINDOWS.items()
        },
        "scenarios": scenarios,
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, indent=2, sort_keys=False)
        fh.write("\n")
    print(json.dumps(receipt["scenarios"], indent=2))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
