"""Score the criterion-5 eligible-date LADDER against landed data.

The gap this closes. `orders_history_backfill_jobs_b5_b9.applied.json` names
`-b8` as criterion 5's gate and puts `-b7` exactly one day short, on a ladder
that is pure arithmetic: span end fixed at 2026-07-04, N contiguous attested
days yield N-28 eligible dates. `backwards_landing_validation.json` scores the
*store-count* projection slice by slice, and nothing scored the ladder itself.
A one-day error anywhere in it moves the gate from `-b8` to `-b7` or past `-b8`,
so it is the one remaining projection whose failure would change what the queue
has to do.

Why the measurement is decisive right now, and cheap. `-b2` ingests
2026-05-11..05-16 and its LAST partition (`2026-05-16__2026-05-17`) is still
running. Its earlier partitions have already landed 05-11..05-15. So the landed
state is a hole punched at 05-16 through an otherwise contiguous span -- exactly
the boundary shape the ladder's arithmetic is silent about, and the one where an
off-by-one would show up. `-b7` vs `-b8` is decided by a single day.

TWO PREDICTIONS, NOT ONE, AND THEY DISAGREE MID-SLICE. The ladder in
`orders_history_backfill_jobs_b5_b9.applied.json` reads "N contiguous ATTESTED
days yield N-28 eligible dates". Read `eligible_dates` in the probe this one
imports and that is not quite the rule the view implements:

    d is eligible  <=>  d is attested  AND  all 28 dates before d are PRESENT

Attestation is required on the TARGET date only; the 28 prior dates are counted
over `mature_daily` with no attestation filter at all (README section 7, anchor
`dd7eccc3`). So the bottom 28 days of any span serve purely as priors and never
need to be attested, and a date becomes eligible as soon as its priors are
INGESTED -- not when they are attested. This probe therefore scores both:

  * `predicted_from_attested_span` -- the ladder as written, growing attested
    run minus 28. Mid-slice this UNDER-states, because `-b2`'s already-landed
    05-11..05-15 are present-but-not-yet-settled priors.
  * `predicted_from_present_priors` -- the corrected rule, dates that are
    attested and carry 28 present priors.

They agree in the end state, where every ingested day is also attested, which is
why the `-b5`..`-b8` ladder and the `-b8` gate stand either way. They diverge
only while a slice is in flight, and the divergence is the measurement: it says
which of the two rules the landed data actually obeys. The correction was
derived from `eligible_dates` before this probe was first run, not fitted to its
output.

What it reuses rather than restates. The per-store eligibility, the
gaps-and-islands run length, and the attestation grid all come from
`horizon-critical-path-probe.py` by import -- `eligible_dates` encodes
`prior_day_count_28 = 28` counted with NO attestation filter, which is the
subtle half of the view's rule and must not be re-typed. The settled-date rules
come from `backwards-landing-validation.py`'s hard-won three: a date is scored
only when its own whole-day partition SUCCEEDED, every run owning its
transactions SUCCEEDED, and a SUCCEEDED run also owns the FOLLOWING day's
partition. The third exists because a date otherwise passes vacuously in the
window between the next partition starting and its spill rows landing, and a
partial read can only ever under-count -- so it always appears to respect a
bound.

Direction of error, and its one exception. On the ATTESTED side, reading the
grid while a partition is in flight can only under-state: the settled-date rules
withhold a date until nothing unfinished can still spill into it. On the PRESENT
side there is no such filter and none is wanted, because the view has none
either -- a partially ingested day already counts toward later dates'
`prior_day_count_28`. That is not a flaw in the measurement, it is the
`predicted_from_present_priors` rule being faithful to the view; but it does
mean the eligible run can grow before a slice finishes, so a rung reached
mid-slice must be re-measured once the slice settles rather than banked.

Read-only. One `SELECT` grid plus one partition `SELECT`; no writes, no DDL, no
job mutation, and it never resumes or patches a Job.

Usage:
    source /tmp/odp-forecast-dsn.env && python3 criterion5-ladder-validation.py

    # re-score from the cached grid instead of re-querying (~15 min):
    PROBE_REFRESH=0 python3 criterion5-ladder-validation.py

Both halves cache independently (`PROBE_CACHE`/`PROBE_REFRESH` for the grid,
`PARTITION_CACHE`/`PARTITION_REFRESH` for the partitions) and the cheap half
runs first, so a database that dies mid-probe costs a second rather than the
grid fetch. That ordering is not hypothetical -- see the note on `main`.
"""

from __future__ import annotations

import importlib.util
import json
import os
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

import psycopg

HERE = os.path.dirname(os.path.abspath(__file__))
EVIDENCE = os.path.dirname(HERE)

REQUIREMENT = os.path.join(EVIDENCE, "criterion5_span_requirement.json")
LADDER_B1_B4 = os.path.join(EVIDENCE, "orders_history_backfill_jobs.applied.json")
LADDER_B5_B9 = os.path.join(EVIDENCE, "orders_history_backfill_jobs_b5_b9.applied.json")

OUT = os.environ.get(
    "PROBE_OUT",
    "/tmp/odp-forecast-evidence-stage/criterion5_ladder_validation.json",
)

SPAN_END = date(2026, 7, 4)
PRIOR_DAY_COUNT = 28

# Import the probe under test rather than restating its eligibility rule. Its
# main() is guarded, so importing it runs nothing.
_spec = importlib.util.spec_from_file_location(
    "horizon_critical_path_probe",
    os.path.join(HERE, "horizon-critical-path-probe.py"),
)
hcp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hcp)

# Whole-day partitions a SUCCEEDED run has actually covered. Copied in shape
# from backwards-landing-validation.py, which owns the rule.
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

INFLIGHT_SQL = """
SELECT partition_key, status
FROM data_plane.ingestion_runs
WHERE source_kind = 'orders'
  AND (status <> 'SUCCEEDED' OR finished_at IS NULL)
  AND partition_key ~ '^\\d{4}-\\d{2}-\\d{2}__\\d{4}-\\d{2}-\\d{2}$'
ORDER BY partition_key;
"""


def runs_of(dates):
    """Consecutive-date runs as (start, end, length), longest first."""
    out = []
    ordered = sorted(dates)
    if not ordered:
        return out
    start = prev = ordered[0]
    for current in ordered[1:]:
        if current == prev + timedelta(days=1):
            prev = current
            continue
        out.append((start, prev, (prev - start).days + 1))
        start = prev = current
    out.append((start, prev, (prev - start).days + 1))
    return sorted(out, key=lambda item: -item[2])


def load_partitions():
    """Whole-day partitions a SUCCEEDED run covers, plus anything non-terminal.

    Cached like the grid, and for the same reason: both halves of this probe
    must survive the other half failing. The cache is a set of dates and run
    states -- no store, member, order id or amount -- so it carries nothing the
    receipt does not already publish.
    """
    cache = os.environ.get("PARTITION_CACHE", "/tmp/odp-criterion5-ladder-partitions.json")
    if os.path.exists(cache) and os.environ.get("PARTITION_REFRESH") != "1":
        with open(cache, encoding="utf-8") as handle:
            raw = json.load(handle)
        return {date.fromisoformat(d) for d in raw["covered"]}, raw["inflight"]
    with psycopg.connect(os.environ["ODP_LEGACY_DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute(PARTITIONS_SQL)
        covered = {row[0] for row in cur.fetchall()}
        cur.execute(INFLIGHT_SQL)
        inflight = [{"partition_key": row[0], "status": row[1]} for row in cur.fetchall()]
    with open(cache, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "fetched_at": datetime.now(UTC).isoformat(),
                "covered": sorted(d.isoformat() for d in covered),
                "inflight": inflight,
            },
            handle,
        )
    return covered, inflight


def main() -> None:
    with open(REQUIREMENT, encoding="utf-8") as handle:
        requirement = json.load(handle)
    required_eligible = int(requirement["minimum_eligible_dates"])
    required_days = int(requirement["translation"]["required_contiguous_attested_days"])

    with open(LADDER_B1_B4, encoding="utf-8") as handle:
        ladder_low = json.load(handle)["expected_coverage"]
    with open(LADDER_B5_B9, encoding="utf-8") as handle:
        ladder_high = json.load(handle)["criterion_5_ladder"]["by_slice"]

    # CHEAP QUERY FIRST, DELIBERATELY. The attestation grid costs ~15 minutes and
    # the partition query costs under a second, and the first version ran them in
    # the other order. On 2026-07-29 that cost a whole run: the Cloud SQL proxies'
    # credential hit the same Workspace reauthentication wall as the cluster
    # (`invalid_rapt`) BETWEEN the two, so the grid was fetched, cached, and then
    # thrown away by a connection failure on the one-second query. Ordering it
    # this way makes a dead database fail in a second instead of a quarter hour.
    covered, inflight = load_partitions()

    os.environ.setdefault("PROBE_CACHE", "/tmp/odp-criterion5-ladder-grid.json")
    os.environ.setdefault("PROBE_REFRESH", "1")
    rows, fetched_at = hcp.load_daily()

    base = defaultdict(lambda: (set(), set()))
    for store, day, attested in rows:
        present, att = base[store]
        present.add(day)
        if attested:
            att.add(day)
    base = dict(base)

    # Rules 1 and 3. Rule 2 is already inside the grid's `attested` flag, which
    # is bool_and over the runs owning the date's transactions.
    def settled(day: date) -> bool:
        return day in covered and (day + timedelta(days=1)) in covered

    attested_dates = sorted(
        {d for _, att in base.values() for d in att if settled(d)}
    )
    ingested_dates = sorted({d for present, _ in base.values() for d in present})

    attested_runs = runs_of(attested_dates)
    # The run criterion 5 grows downwards: the one ending at the span end.
    growing_run = next(
        (item for item in attested_runs if item[0] <= SPAN_END <= item[1]), None
    )

    # Global eligible dates: a date on which at least one store is eligible.
    per_store_eligible = {}
    for store, (present, att) in base.items():
        per_store_eligible[store] = set(
            hcp.eligible_dates(present, {d for d in att if settled(d)})
        )
    eligible_dates_global = sorted(
        {d for days in per_store_eligible.values() for d in days}
    )
    eligible_runs = runs_of(eligible_dates_global)
    longest_eligible = eligible_runs[0] if eligible_runs else None

    store_streaks = {}
    for store, days in per_store_eligible.items():
        lengths = hcp.islands(days)
        store_streaks[store] = max(lengths) if lengths else 0
    max_streak = max(store_streaks.values()) if store_streaks else 0

    predicted_from_span = (
        max(0, growing_run[2] - PRIOR_DAY_COUNT) if growing_run else 0
    )
    measured = longest_eligible[2] if longest_eligible else 0

    # The corrected rule, applied to the global date sets: a date qualifies when
    # it is attested-and-settled and all 28 dates before it were INGESTED. Taken
    # over the union of stores this is an UPPER BOUND on the per-store union the
    # view produces -- the two coincide when a dense set of stores spans the
    # whole window, which per_store_streak_headroom.json measured (410 of 583).
    present_set = set(ingested_dates)
    attested_set = set(attested_dates)
    prior_ok = [
        d
        for d in sorted(attested_set)
        if all((d - timedelta(days=k)) in present_set for k in range(1, PRIOR_DAY_COUNT + 1))
    ]
    prior_runs = runs_of(prior_ok)
    prior_growing = next(
        (item for item in prior_runs if item[0] <= SPAN_END <= item[1]), None
    )
    predicted_from_priors = prior_growing[2] if prior_growing else 0

    present_runs = runs_of(ingested_dates)
    present_growing = next(
        (item for item in present_runs if item[0] <= SPAN_END <= item[1]), None
    )

    receipt = {
        "artifact": "criterion5_ladder_validation",
        "task": "ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001",
        "captured_at": datetime.now(UTC).isoformat(),
        "grid_fetched_at": fetched_at,
        "purpose": (
            "Score the criterion-5 eligible-date ladder -- the arithmetic that "
            "names -b8 as the gate and puts -b7 one day short -- against landed "
            "data, on the one rung that is currently settled."
        ),
        "method": {
            "plane": "SOURCE PG15 via cloud-sql-proxy; the PG16 target is frozen "
                     "at an old activation and cannot answer this (README section 9)",
            "imported_under_test": [
                "horizon-critical-path-probe.py::eligible_dates "
                "(prior_day_count_28 = 28, counted with NO attestation filter)",
                "horizon-critical-path-probe.py::islands (gaps-and-islands run length)",
                "horizon-critical-path-probe.py::load_daily (per-store attestation grid)",
            ],
            "settled_date_rules": [
                "1. the date's own whole-day partition has a SUCCEEDED run",
                "2. every run owning the date's transactions SUCCEEDED "
                "(inside the grid's bool_and)",
                "3. a SUCCEEDED run also owns the FOLLOWING day's partition, so "
                "no unfinished source can still spill into it",
            ],
            "error_direction": (
                "On the ATTESTED side an in-flight partition can only under-state: "
                "the settled-date rules withhold a date until nothing unfinished "
                "can spill into it. On the PRESENT side there is deliberately no "
                "such filter, because the view has none either -- a partially "
                "ingested day already counts toward later dates' prior_day_count_28 "
                "-- so the eligible run can grow before a slice finishes and a rung "
                "reached mid-slice must be re-measured once the slice settles."
            ),
            "two_prediction_rules": (
                "The ladder says 'N contiguous ATTESTED days yield N-28 eligible "
                "dates'. eligible_dates implements 'd is attested AND its 28 prior "
                "dates are PRESENT', so attestation is required on the target date "
                "only and the bottom 28 days of a span are priors that never need "
                "it. The two agree in the end state (every ingested day attested) "
                "and diverge only mid-slice; the -b8 gate stands under both."
            ),
        },
        "in_flight_at_capture": inflight,
        "measured": {
            "ingested_span": {
                "min": ingested_dates[0].isoformat() if ingested_dates else None,
                "max": ingested_dates[-1].isoformat() if ingested_dates else None,
                "distinct_dates": len(ingested_dates),
            },
            "attested_runs_top3": [
                {"start": a.isoformat(), "end": b.isoformat(), "days": n}
                for a, b, n in attested_runs[:3]
            ],
            "growing_run": (
                {
                    "start": growing_run[0].isoformat(),
                    "end": growing_run[1].isoformat(),
                    "attested_days": growing_run[2],
                }
                if growing_run
                else None
            ),
            "eligible_runs_top3": [
                {"start": a.isoformat(), "end": b.isoformat(), "dates": n}
                for a, b, n in eligible_runs[:3]
            ],
            "longest_eligible_run_dates": measured,
            "max_per_store_eligible_streak_days": max_streak,
            "stores_meeting_criterion_5_streak": sum(
                1 for v in store_streaks.values() if v >= required_eligible
            ),
            "stores_with_any_eligible_date": sum(1 for v in store_streaks.values() if v),
        },
        "ladder_check": {
            "measured_longest_eligible_run": measured,
            "as_written": {
                "rule": "N contiguous ATTESTED days yield N-28 eligible dates",
                "predicted": predicted_from_span,
                "holds": predicted_from_span == measured,
                "off_by": measured - predicted_from_span,
            },
            "corrected": {
                "rule": (
                    "a date is eligible when it is attested and its 28 prior dates "
                    "are PRESENT; the bottom 28 days of a span are priors only"
                ),
                "predicted": predicted_from_priors,
                "holds": predicted_from_priors == measured,
                "off_by": measured - predicted_from_priors,
                "run": (
                    {
                        "start": prior_growing[0].isoformat(),
                        "end": prior_growing[1].isoformat(),
                    }
                    if prior_growing
                    else None
                ),
            },
            "present_growing_run": (
                {
                    "start": present_growing[0].isoformat(),
                    "end": present_growing[1].isoformat(),
                    "ingested_days": present_growing[2],
                }
                if present_growing
                else None
            ),
            "rung_after_b1": ladder_low.get("after_b1"),
            "rung_after_b2": ladder_low.get("after_b2"),
            "why_this_rung": (
                "-b2's last partition (2026-05-16__2026-05-17) is still running, so "
                "05-16 is a hole in the attested run and 05-11..05-15 have landed as "
                "PRESENT but are not yet settled. The two rules therefore disagree "
                "here by construction: the as-written rule holds the run at the "
                "after_b1 rung while the corrected rule already counts the landed "
                "priors. Which one the data obeys is the measurement."
            ),
        },
        "criterion_5": {
            "minimum_eligible_dates": required_eligible,
            "required_contiguous_attested_days": required_days,
            "eligible_dates_now": measured,
            "shortfall_dates": max(0, required_eligible - measured),
            "gate_slice": "oday-data-platform-orders-history-93cb9f94-b8",
            "remaining_ladder": ladder_high,
        },
        "limits": [
            "Scores the ladder's contiguity arithmetic on ONE settled rung. It does "
            "not validate the store population of the April windows -b5..-b8 enter; "
            "that is april_window_store_density_probe.pod.yaml, committed unrun and "
            "blocked on the cluster re-auth.",
            "The global eligible-date count is 'at least one store eligible'. "
            "Criterion 5 additionally needs one store carrying enough holdout rows, "
            "which is what max_per_store_eligible_streak_days tracks.",
            "Measured on the source plane. The PG16 target is frozen at an old "
            "activation, so these numbers describe what activation will produce, "
            "not what the target currently holds.",
            "The corrected prediction unions dates across stores, so it is an UPPER "
            "BOUND on the per-store union the view produces. It coincides with the "
            "measurement exactly when a dense store set spans the window, which is "
            "the measured regime (per_store_streak_headroom.json: 410 of 583 stores "
            "unbroken across the whole window). A future window with sparser stores "
            "would need the per-store form.",
        ],
        "redaction": "Counts, dates and run states only. No store, member, order id or amount.",
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(receipt, handle, indent=1, sort_keys=False)
        handle.write("\n")

    print(json.dumps(receipt["measured"], indent=1))
    print(json.dumps(receipt["ladder_check"], indent=1))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
