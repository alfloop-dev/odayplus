"""Score the backwards projection against backwards dates as they actually land.

Motivation. Criterion 3 was attributed to `-b3` on the strength of three
agreeing routes -- landed-streak donation (419), upstream density (421) and
mappability adjustment (420) -- and `donor_projection_backtest.json` then scored
the donor *rule* on a `-s4` holdout. All four were computed before a single
backwards date existed. `-b1` is now landing them, so the projection can at last
be checked against the thing it was a projection OF, on its own dates, in its
own regime -- which is the one limit the `-s4` backtest explicitly could not
close, since its holdout sat days rather than months from its donors.

The prediction being scored, stated before the data arrived:

  * `backwards_window_store_density.json` measured upstream Atlas at the view's
    grain (`state=TRADE_SUCCESS`, TWD, `place` per `createdAt` UTC day) and
    recorded a per-day distinct-place count for every backwards date.
  * That count is an **upper bound** on landed stores, never an estimate: a
    transaction whose `place` does not resolve through `core.stores` is
    quarantined by `store.py::require_place` and lands nothing.
  * `backwards_window_store_mappability.json` measured how much of that bound
    is lost -- mapping rate **0.9715** across the backwards window, which is the
    factor turning the bound into a point prediction.

So for each landed backwards date this probe asks two questions with different
standing. Whether landed <= upstream is a **falsifiable invariant**: exceeding
the bound would mean the density measurement did not describe the population
that lands, and every number resting on it would have to be recomputed. Whether
landed is near `0.9715 x upstream` is a calibration check, and a miss there is
informative rather than fatal.

Completeness, and why it is not a formality. Only dates every one of whose
transactions is owned by a `SUCCEEDED` ingestion run are scored. A partition
still being written has a `RUNNING` run, so its dates fail that test and are
excluded automatically -- the same attestation predicate
`forecast_training_view` applies, reused here so that a mid-flight slice cannot
be mistaken for a shortfall. The excluded dates are listed, not dropped
silently.

This guard earned itself immediately. Read by hand while `-b1`'s first
partition was in flight, 2026-05-17 showed 518 stores over 8 733 transactions,
which sits neatly inside the density receipt's predicted band and looks like a
confirmation. Twenty-five minutes later the same date read 520 stores over
11 246 transactions and was still climbing, because the partition had not
finished. A partial partition can only ever *under*-count, so it will always
appear to agree with an upper bound, and reading one mid-flight would have
manufactured a passing result out of an unfinished write. Score nothing until
the run that owns it is SUCCEEDED.

A date also is not finished when its OWN partition succeeds. Partition windows
are cut on the source's update cursor while the grain here is `event_time`, so a
few transactions always spill across the UTC day boundary: when
`2026-05-17__2026-05-18` reached SUCCEEDED, two of 2026-05-17's transactions were
owned by the still-RUNNING `2026-05-18__2026-05-19` run, and `bool_and` over the
owning runs -- the same predicate that lets four stray rows cost a whole day in
§6 -- correctly held the date back. In practice a date becomes scoreable only
once the FOLLOWING partition also finishes, so expect a slice to yield its dates
one behind the partition frontier, and the last date of a slice to wait on
whatever ingests the day after it.

That last paragraph described what the first two rules were *observed* to do,
and it took a third rule to make it what they GUARANTEE. `bool_and` over owning
runs only holds a date back once the following run has actually claimed its
spill rows, which leaves a window -- from the moment the following partition
starts to the moment it writes those rows -- in which a date passes both tests
vacuously, on an under-count. 2026-05-21 walked straight into it: its own
partition succeeded at 00:19Z, `2026-05-22__2026-05-23` was three minutes into
its run and owned none of 05-21's transactions yet, and the date scored 512
stores against a 517 upper bound -- comfortably inside the band, indeed at a
ratio in line with every other date, and therefore invisible as an error. This
is the mid-flight trap of the paragraph above wearing different clothes: a
partial read can only under-count, so it always appears to respect an upper
bound. The third rule closes it structurally rather than by timing: **a date is
scored only once a SUCCEEDED run also owns the FOLLOWING day's partition**, so
the only source that can still spill into it is finished. It is a strict
strengthening -- it can only withhold dates, never admit them -- and it makes
"one behind the partition frontier" a property of the code instead of a
description of its luck.

Re-run this after each backwards slice completes; it strengthens monotonically
as `-b2`, `-b3` and `-b4` land, and it is the only check that closes the
distance limit the `-s4` backtest left open.

Read-only. Selects only; no writes, no DDL, no job mutation.

Usage:
    source /tmp/odp-forecast-dsn.env && python3 backwards-landing-validation.py
"""

import json
import os
from datetime import UTC, date, datetime, timedelta

import psycopg

HERE = os.path.dirname(os.path.abspath(__file__))
EVIDENCE = os.path.dirname(HERE)

DENSITY = os.path.join(EVIDENCE, "backwards_window_store_density.json")
MAPPABILITY = os.path.join(EVIDENCE, "backwards_window_store_mappability.json")

OUT = os.environ.get(
    "PROBE_OUT",
    "/tmp/odp-forecast-evidence-stage/backwards_landing_validation.json",
)

# Landed store-days at the view's own grain, with the attestation state that
# decides whether the date is finished. bool_and over SUCCEEDED mirrors
# transaction_daily.source_run_complete; a RUNNING partition drives it false.
# The attestation lookup is joined, not correlated, and that is a performance
# requirement rather than a style preference. `canonical_lineage`'s unique index
# leads with `source_snapshot_id` (it exists to serve the ingestion upsert's
# ON CONFLICT), so a probe filtering on `canonical_id` alone cannot use it. A
# per-row scalar subquery therefore degrades to one scan of a multi-million-row
# table per transaction; the first version of this probe ran over ten minutes
# and was killed. Restricting the lineage side by joining `tx` lets the planner
# make a single hash-join pass instead -- 26 s over the same window.
LANDED_SQL = """
WITH tx AS (
    SELECT
        txn.transaction_id,
        txn.store_id,
        (txn.event_time AT TIME ZONE 'UTC')::date AS d
    FROM core.transactions AS txn
    WHERE txn.transaction_status = 'succeeded'
      AND txn.currency = 'TWD'
      AND (txn.event_time AT TIME ZONE 'UTC')::date >= %s
      AND (txn.event_time AT TIME ZONE 'UTC')::date < %s
),
lin AS (
    SELECT
        lineage.canonical_id,
        bool_and(
            ingestion.status = 'SUCCEEDED'
            AND ingestion.finished_at IS NOT NULL
        ) AS ok
    FROM data_plane.canonical_lineage AS lineage
    INNER JOIN data_plane.ingestion_runs AS ingestion
        ON ingestion.run_id = lineage.run_id
    INNER JOIN tx ON tx.transaction_id = lineage.canonical_id
    WHERE lineage.canonical_table = 'core.transactions'
    GROUP BY lineage.canonical_id
)
SELECT
    tx.d,
    count(DISTINCT tx.store_id) AS stores,
    count(*) AS txns,
    coalesce(bool_and(lin.ok), FALSE) AS all_runs_complete
FROM tx
LEFT JOIN lin ON lin.canonical_id = tx.transaction_id
GROUP BY tx.d
ORDER BY tx.d;
"""

# Which whole-day partitions a SUCCEEDED run has actually covered. partition_key
# is 'YYYY-MM-DD__YYYY-MM-DD', end-exclusive, one UTC day per run.
PARTITIONS_SQL = """
SELECT DISTINCT split_part(partition_key, '__', 1)::date AS d
FROM data_plane.ingestion_runs
WHERE source_kind = 'orders'
  AND status = 'SUCCEEDED'
  AND finished_at IS NOT NULL
  AND partition_key ~ '^\\d{4}-\\d{2}-\\d{2}__\\d{4}-\\d{2}-\\d{2}$'
  AND split_part(partition_key, '__', 2)::date
      = split_part(partition_key, '__', 1)::date + 1;
"""


def main():
    with open(DENSITY, encoding="utf-8") as fh:
        density = json.load(fh)
    with open(MAPPABILITY, encoding="utf-8") as fh:
        mappability = json.load(fh)

    backwards = density["measurements"]["backwards_b1_b4"]
    window = backwards["window"]
    upstream_stores = backwards["per_day_store_counts"]
    upstream_orders = backwards["per_day_order_counts"]
    mapping_rate = mappability["measurements"]["backwards_b1_b4"]["mapping_rate"]

    start = date.fromisoformat(window["start"])
    end = date.fromisoformat(window["end_exclusive"])

    with psycopg.connect(os.environ["ODP_LEGACY_DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute(LANDED_SQL, (start, end))
        rows = cur.fetchall()
        cur.execute(PARTITIONS_SQL)
        covered = {r[0] for r in cur.fetchall()}

    scored = []
    incomplete = []
    for d, stores, txns, complete in rows:
        key = str(d)
        up = upstream_stores.get(key)
        entry = {
            "date": key,
            "landed_stores": stores,
            "landed_txns": txns,
            "upstream_stores": up,
            "upstream_orders": upstream_orders.get(key),
            "own_partition_succeeded": d in covered,
            "following_partition_succeeded": (d + timedelta(days=1)) in covered,
        }
        if d not in covered:
            entry["excluded_reason"] = (
                "no SUCCEEDED run owns this date's own whole-day partition -- it "
                "is a timezone-edge straggler pulled in by a neighbouring "
                "window's bound, not an ingested trading day"
            )
            incomplete.append(entry)
            continue
        if (d + timedelta(days=1)) not in covered:
            entry["excluded_reason"] = (
                "the FOLLOWING day's partition has no SUCCEEDED run, so a source "
                "that can still spill transactions into this date is unfinished. "
                "bool_and over owning runs only holds the date back once that run "
                "has claimed its spill rows, so between the following partition "
                "starting and writing them a date passes the other two tests on "
                "an under-count -- and an under-count always appears to respect "
                "the upper bound"
            )
            incomplete.append(entry)
            continue
        if not complete:
            entry["excluded_reason"] = (
                "not every owning ingestion run is SUCCEEDED -- the partition is "
                "still being written, or holds unreconciled lineage"
            )
            incomplete.append(entry)
            continue
        if up is None:
            entry["excluded_reason"] = (
                "date lies outside the density receipt's measured window, so "
                "there is no prediction to score it against"
            )
            incomplete.append(entry)
            continue
        entry["within_upper_bound"] = stores <= up
        entry["landed_over_upstream"] = round(stores / up, 4)
        entry["predicted_landed_stores"] = round(up * mapping_rate)
        entry["prediction_error_stores"] = stores - round(up * mapping_rate)
        scored.append(entry)

    breaches = [e for e in scored if not e["within_upper_bound"]]
    summary = {
        "dates_scored": len(scored),
        "upper_bound_breaches": len(breaches),
        "breach_dates": [e["date"] for e in breaches],
    }
    if scored:
        ratios = [e["landed_over_upstream"] for e in scored]
        errors = [e["prediction_error_stores"] for e in scored]
        summary.update(
            {
                "landed_over_upstream_min": min(ratios),
                "landed_over_upstream_max": max(ratios),
                "landed_over_upstream_mean": round(sum(ratios) / len(ratios), 4),
                "mapping_rate_used_as_prediction": mapping_rate,
                "prediction_error_stores_min": min(errors),
                "prediction_error_stores_max": max(errors),
                "prediction_error_stores_mean": round(sum(errors) / len(errors), 2),
            }
        )

    receipt = {
        "captured_at": datetime.now(UTC).isoformat(),
        "purpose": (
            "Score the backwards projection against backwards dates as they "
            "land. Closes the one limit donor_projection_backtest.json could "
            "not: its holdout sat days from its donors, these dates sit in the "
            "projected regime itself."
        ),
        "prediction_under_test": {
            "upper_bound": (
                "upstream distinct places at the view's grain, from "
                "backwards_window_store_density.json. Landed stores can never "
                "exceed it -- an unresolvable place is quarantined by "
                "store.py::require_place and lands nothing. A breach falsifies "
                "the density measurement as a description of what lands."
            ),
            "point_prediction": (
                f"upper bound x {mapping_rate} (backwards mapping rate from "
                "backwards_window_store_mappability.json). A miss here is a "
                "calibration finding, not a falsification."
            ),
            "window": window,
        },
        "completeness": {
            "rule": (
                "a date is scored only if ALL THREE hold: a SUCCEEDED run owns "
                "its own whole-day partition (it was ingested as a day, rather "
                "than clipped in by a neighbouring window's bound), a SUCCEEDED "
                "run also owns the FOLLOWING day's partition (so no unfinished "
                "source can still spill transactions into this date), AND every "
                "run owning its transactions is SUCCEEDED (the view's own "
                "predicate). The three are independent -- 2026-07-06 has a "
                "SUCCEEDED partition and still fails the third test, which is "
                "Defect D."
            ),
            "why_three_rules": (
                "the first rule is what excludes 2026-05-16 and 2026-05-22: they "
                "are attested, so the view's predicate alone would admit them, "
                "but they hold 1 and 3 stores because no partition ever covered "
                "them. Scoring a straggler against a ~520-store prediction "
                "reports a 500-store shortfall that means nothing. The second "
                "rule exists because the third one is not self-enforcing: "
                "bool_and over owning runs holds a date back only after the "
                "following run has claimed its cross-midnight spill, so in the "
                "window between that run starting and writing those rows a date "
                "passes on an under-count. 2026-05-21 did exactly that at 00:22Z "
                "-- 512 stores against a 517 bound, at a ratio indistinguishable "
                "from a real reading. A partial read can only under-count, so it "
                "always appears to respect an upper bound."
            ),
            "excluded": incomplete,
        },
        "summary": summary,
        "per_date": scored,
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, indent=2, sort_keys=False)
        fh.write("\n")
    print(json.dumps({"summary": summary,
                      "per_date": scored,
                      "excluded": [e["date"] for e in incomplete]}, indent=2))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
