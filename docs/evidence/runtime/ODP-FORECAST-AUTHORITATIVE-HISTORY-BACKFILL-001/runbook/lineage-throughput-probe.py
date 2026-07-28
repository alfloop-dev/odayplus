"""Measure what actually governs orders-history partition wall-clock.

Motivation. The per-partition duration measurements were honest that they could
not explain themselves: duration is NOT a function of partition row count (-s1
loaded 13 693 rows in 19.4 min; -s4 loaded 12 214 in 52.8 min), so slice sizing
could only be defended with a tripwire -- "if a partition exceeds 80 min, raise
that slice's activeDeadlineSeconds". A tripwire fires after the fact and needs
someone awake. This probe looks one layer down for the governing quantity, which
is what turns the tripwire into a projection.

Method. Every `data_plane.canonical_lineage` row carries `run_id` and
`projected_at`, so each ingestion run leaves a per-minute trace of its own
projection work. For each run we take the lineage row count, the span from first
to last `projected_at`, and the resulting rows/minute; then we compare that span
against the run's total wall-clock to see what share of a partition is lineage
projection.

Why it matters beyond sizing: `projected_at` advancing is a LIVE liveness signal,
about one row every 0.2s. `ingestion_runs` cannot provide one -- a RUNNING run
reports valid_loaded = 0 until it finishes -- and `core.transactions.ingested_at`
cannot either, because rows land in a single bulk commit in the partition's first
minute. Lineage projection is the only place a running partition is observable
from outside the pod, which is what the deadline guard is built on.

Read-only. Two aggregate SELECTs against the source (PG15) plane. No writes, no
DDL, no job mutation.

Usage: source /tmp/odp-forecast-dsn.env && python3 lineage-throughput-probe.py
"""
import json
import os
from datetime import datetime, timezone

import psycopg

# Slice attribution is by driver resume timestamps, not partition keys: keys
# overlap between -s1 and -s3, so keys alone cannot say which Job produced a run.
SLICES = [
    ("s1", "2026-07-28T12:26:00Z", "2026-07-28T16:28:05Z", 14400),
    ("s3", "2026-07-28T16:32:00Z", "2026-07-28T18:30:14Z", 14400),
    ("s4", "2026-07-28T18:30:15Z", None, 28800),
]


def parse(t):
    return datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


out = {
    "artifact": "lineage_projection_throughput",
    "task": "ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001",
    "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "captured_by": "Claude3",
    "what_this_is": __doc__.strip(),
    "read_only": True,
    "redaction": "run ids truncated to 8 chars; counts, timestamps and rates only. "
                 "No store, member, order id, amount or any row content.",
}

with psycopg.connect(os.environ["ODP_LEGACY_DATABASE_URL"]) as c, c.cursor() as cur:
    cur.execute(
        """
        SELECT run_id::text, count(*), min(projected_at), max(projected_at)
        FROM data_plane.canonical_lineage
        WHERE projected_at >= %s
        GROUP BY run_id
        """,
        (parse("2026-07-28T12:00:00Z"),),
    )
    lineage = {r[0]: {"rows": r[1], "first": r[2], "last": r[3]} for r in cur.fetchall()}

    slices = []
    for name, lo, hi, deadline in SLICES:
        cur.execute(
            """
            SELECT run_id::text, partition_key, status, started_at, finished_at, valid_loaded
            FROM data_plane.ingestion_runs
            WHERE source_kind='orders' AND started_at >= %s
              AND (%s::timestamptz IS NULL OR started_at <= %s::timestamptz)
            ORDER BY started_at
            """,
            (parse(lo), hi, hi),
        )
        parts = []
        for run_id, key, status, started, finished, loaded in cur.fetchall():
            row = {
                "run_id": run_id[:8],
                "partition_key": key,
                "status": status,
                "valid_loaded": loaded,
            }
            if finished and started:
                row["run_minutes"] = round((finished - started).total_seconds() / 60.0, 1)
            lin = lineage.get(run_id)
            if lin:
                span = (lin["last"] - lin["first"]).total_seconds() / 60.0
                row["lineage_rows"] = lin["rows"]
                row["lineage_span_minutes"] = round(span, 1)
                # A sub-30s span is a resumed partition finishing off a handful of
                # records; a rate computed from it is noise, so it is left null.
                row["lineage_rows_per_minute"] = round(lin["rows"] / span, 1) if span > 0.5 else None
                if row.get("run_minutes"):
                    row["lineage_share_of_run"] = round(span / row["run_minutes"], 3)
            parts.append(row)
        slices.append({"slice": name, "active_deadline_seconds": deadline, "partitions": parts})
    out["slices"] = slices


# A RESUMED partition -- one whose predecessor was killed mid-flight -- only has
# a few hundred records left to project, so it finishes in seconds and reports a
# rate of thousands per minute. Those are real measurements of a different thing
# and they MUST NOT enter a planning rate: including them lifts the -s3/-s4 mean
# from ~330 to ~930 rows/min, which is exactly the sort of number that would
# justify a deadline that then kills a slice. Full-length partitions only.
FULL_LENGTH_MIN_SPAN_MINUTES = 5.0


def collect(key, only=None, full_length_only=True):
    return [
        p[key]
        for s in out["slices"]
        if only is None or s["slice"] in only
        for p in s["partitions"]
        if p.get(key)
        and p["status"] == "SUCCEEDED"
        and (not full_length_only or p.get("lineage_span_minutes", 0) >= FULL_LENGTH_MIN_SPAN_MINUTES)
    ]


rates = collect("lineage_rows_per_minute")
shares = collect("lineage_share_of_run")
recent = collect("lineage_rows_per_minute", only=("s3", "s4"))
excluded = collect("lineage_rows_per_minute", full_length_only=False)

# Plan against the worst rate actually observed on the recent slices, not the
# mean: the deadline question is not "how fast is it usually" but "can the slow
# case still finish". PARTITIONS_PER_SLICE and the budget are the live config.
PARTITIONS_PER_SLICE = 6
BUDGET_MINUTES = 28800 / 60
HEAVIEST_DAY_ROWS = 13693  # largest partition seen in the backfill so far

planning_rate = min(recent) if recent else None

out["summary"] = {
    "full_length_partitions_measured": len(rates),
    "resumed_stub_partitions_excluded": len(excluded) - len(rates),
    "lineage_share_of_run_mean": round(sum(shares) / len(shares), 3) if shares else None,
    "all_slices": {
        "rows_per_minute_min": min(rates) if rates else None,
        "rows_per_minute_max": max(rates) if rates else None,
        "rows_per_minute_mean": round(sum(rates) / len(rates), 1) if rates else None,
    },
    "recent_slices_s3_s4": {
        "rows_per_minute_min": min(recent) if recent else None,
        "rows_per_minute_max": max(recent) if recent else None,
        "rows_per_minute_mean": round(sum(recent) / len(recent), 1) if recent else None,
    },
}

if planning_rate:
    worst_partition = HEAVIEST_DAY_ROWS / planning_rate
    out["summary"]["deadline_projection"] = {
        "planning_rate_rows_per_minute": planning_rate,
        "basis": "slowest full-length partition on the two most recent slices",
        "worst_case_partition_minutes": round(worst_partition, 1),
        "worst_case_slice_minutes": round(worst_partition * PARTITIONS_PER_SLICE, 1),
        "budget_minutes": BUDGET_MINUTES,
        "fits": worst_partition * PARTITIONS_PER_SLICE <= BUDGET_MINUTES,
        "rate_at_which_a_slice_exactly_fills_the_budget": round(
            HEAVIEST_DAY_ROWS * PARTITIONS_PER_SLICE / BUDGET_MINUTES, 1
        ),
    }

print(json.dumps(out, indent=2, sort_keys=False))
