"""Measure what one activation does to TARGET eligibility, and roll it back.

Why this exists. Section 9 established that the PG16 target is frozen at an old
activation: its training-eligible span is `2026-06-19..2026-07-01`, and
`2026-07-02` holds rows of which zero are eligible, every one
`SOURCE_RUN_NOT_COMPLETE`. It then asserted the remedy -- that `refresh_key`
(Defect B, `10c0b78e`) "is what lets the next activation reconsider them at
all". That assertion is a reading of the SQL, not a measurement, and this task
has already been burned twice by a careful reading of `copy_relation`: Defect E
was a fail-closed guard that interrogated the wrong relation, and Defect B
itself was invisible until the target was compared against the source.

The assertion is also load-bearing for criterion 5 in a way no other receipt
covers. `-b3` buys the 28-day span; it does nothing whatsoever about a target
row that `ON CONFLICT DO NOTHING` refuses to revisit. If the frozen region does
not actually thaw, the acceptance activation produces a target that still ends
at `2026-07-01` and the registry still cannot train -- and that would be
discovered after the whole backfill queue has drained, not now, while there is
still time to do something about it.

So this measures the consequence rather than the mechanism. It runs the REAL
activation chain against the live pair inside a transaction that is rolled back,
and then asks the REAL `forecast_horizon_windows` -- the same function
`activate`/`verify` report from -- what the target's eligibility looks like in
that uncommitted state. Everything reported is produced by the code under test.

Two arms, one variable, the same shape the Defect E rehearsal used:

    unfixed_b   `data_plane.ingestion_runs` with `refresh_key = ()`  (pre-fix)
    fixed       `ACTIVATION_RELATIONS` exactly as shipped

`canonical_lineage` keeps its Defect E fix in BOTH arms: that fix is already
measured in `lineage_convergence_rehearsal.json` and is not the variable here.
The arms therefore separate the two distinct things one activation does to
`ingestion_runs` -- INSERT rows the target never had (which both arms do, and
which needs no fix) from REFRESH of a row the target already holds at a stale
non-terminal status (which only the fixed arm does). The target currently holds
exactly one such row, run `0157f8d9` on partition `2026-07-02__2026-07-03`,
`RUNNING` in the target and `SUCCEEDED` in the source.

What it does NOT claim. This is a pre-`-b3` measurement, so the absolute h28
figure it reports is not the acceptance number and is not comparable to section
7's source-side projection -- the target has not received `-s3`/`-s4`/`-b1` yet
and the backwards slices are still landing. The quantity under test is the
DIFFERENCE between the arms and against the baseline, on a target that is
identical for all three.

Effect on the live system. The target is written and rolled back -- PostgreSQL
offers no other way to observe a multi-statement convergence -- so nothing
survives but dead tuples for autovacuum. The source is opened `read_only` and is
only ever COPYed out of. No job is suspended, resumed or patched. Every phase
writes the receipt as soon as it finishes, so an expensive measurement degrades
to a missing phase and never to a missing receipt.

Usage (detached -- it outlives a worker turn):
    setsid nohup runbook/activation-eligibility-rehearsal-runner.sh \
        >>/tmp/odp-activation-eligibility-rehearsal.log 2>&1 </dev/null & disown
"""

import json
import os
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime

import psycopg

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", ".."),
)

from scripts.data_plane.forecast_history_activation import (  # noqa: E402
    ACTIVATION_RELATIONS,
    COPY_RELATION_TIMEOUT_MS,
    _activation_lock,
    copy_relation,
    forecast_horizon_windows,
)

OUT = os.environ.get(
    "PROBE_OUT",
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "activation_eligibility_rehearsal.json"
    ),
)

RUNS = "data_plane.ingestion_runs"
HORIZONS = (7, 14, 28, 56)

# The dates worth reporting individually: the frozen boundary section 9 found,
# plus enough either side to show whether the island is split or continuous.
PER_DATE_FROM = "2026-06-25"
PER_DATE_TO = "2026-07-27"


def _runs_relation():
    return next(r for r in ACTIVATION_RELATIONS if r.qualified == RUNS)


def unfixed_b_relations():
    """ACTIVATION_RELATIONS with the Defect B fix backed out of ingestion_runs only."""

    return tuple(
        replace(relation, refresh_key=()) if relation.qualified == RUNS else relation
        for relation in ACTIVATION_RELATIONS
    )


def run_status_census(connection) -> dict:
    """Per-status counts of the runs the activation actually selects.

    The predicate is read off the Relation under test rather than restated, so
    this census can never drift from the set `copy_relation` copies.
    """

    predicate = _runs_relation().source_predicate
    rows = connection.execute(
        f"SELECT status, count(*)::bigint FROM {RUNS} WHERE {predicate} GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    census = {str(status): int(count) for status, count in rows}
    return {
        "by_status": census,
        "total": sum(census.values()),
        "non_terminal": sum(v for k, v in census.items() if k != "SUCCEEDED"),
    }


def measure_eligibility(connection, *, label: str) -> dict:
    """The target's eligibility as the REAL activation reporting path sees it.

    `forecast_horizon_windows` leaves its `ON COMMIT DROP` projection behind, so
    the per-date breakdown costs nothing beyond the single view scan it already
    paid for.
    """

    started = time.monotonic()
    connection.execute(f"SET statement_timeout = {COPY_RELATION_TIMEOUT_MS}")
    windows = forecast_horizon_windows(connection, horizons=HORIZONS)
    result = {
        "label": label,
        "windows": windows,
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }
    if not windows.get("available"):
        return result

    result["per_date"] = [
        {
            "date": str(row[0]),
            "rows": int(row[1]),
            "eligible": int(row[2]),
            "top_exclusion_reason": row[3],
        }
        for row in connection.execute(
            """
            SELECT date,
                   count(*)::bigint,
                   count(*) FILTER (WHERE is_training_eligible)::bigint,
                   (array_agg(exclusion_reason ORDER BY exclusion_reason)
                        FILTER (WHERE exclusion_reason IS NOT NULL))[1]
            FROM pg_temp.forecast_eligibility_probe
            WHERE date BETWEEN %s AND %s
            GROUP BY 1 ORDER BY 1
            """,
            (PER_DATE_FROM, PER_DATE_TO),
        ).fetchall()
    ]
    # Every distinct eligible island, so a split shows up as two rows rather
    # than as a smaller maximum that could equally mean "less history".
    result["eligible_islands"] = [
        {"start": str(row[0]), "end": str(row[1]), "days": int(row[2]), "stores": int(row[3])}
        for row in connection.execute(
            """
            SELECT streak_start, streak_end, streak_days, count(DISTINCT store_id)::bigint
            FROM pg_temp.forecast_eligible_streaks
            GROUP BY 1, 2, 3
            HAVING count(DISTINCT store_id) >= 10
            ORDER BY 3 DESC, 1
            LIMIT 15
            """
        ).fetchall()
    ]
    return result


def measure_phase(connection, *, label: str) -> dict:
    return {
        "label": label,
        "run_census": run_status_census(connection),
        "eligibility": measure_eligibility(connection, label=label),
    }


def run_arm(name: str, relations, *, source_dsn: str, target_dsn: str) -> dict:
    """One full activation chain against the live pair, measured, then ROLLED BACK."""

    source = psycopg.connect(source_dsn, connect_timeout=30)
    target = psycopg.connect(target_dsn, connect_timeout=30)
    try:
        source.read_only = True
        source.execute(f"SET statement_timeout = {COPY_RELATION_TIMEOUT_MS}")
        target.execute(f"SET statement_timeout = {COPY_RELATION_TIMEOUT_MS}")

        receipts = []
        started = time.monotonic()
        with _activation_lock(target):
            for relation in relations:
                at = time.monotonic()
                receipt = copy_relation(source, target, relation)
                receipt["elapsed_seconds"] = round(time.monotonic() - at, 1)
                receipts.append(receipt)
        chain_elapsed = round(time.monotonic() - started, 1)

        after = measure_phase(target, label=f"after_{name}")
        return {
            "arm": name,
            "relations": receipts,
            "chain_elapsed_seconds": chain_elapsed,
            "after": after,
            "committed": False,
        }
    finally:
        target.rollback()
        target.close()
        source.close()


def _windows(phase: dict) -> dict:
    return phase["eligibility"]["windows"]


def _h(phase: dict, horizon: int) -> int:
    windows = _windows(phase).get("horizon_windows", {})
    return int(windows.get(f"h{horizon}", {}).get("stores_with_complete_window", 0))


def _emit(payload: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")


def main() -> int:
    source_dsn = os.environ["ODP_LEGACY_DATABASE_URL"]
    target_dsn = os.environ["ODAY_DATABASE_URL"]

    payload = {
        "artifact": "activation_eligibility_rehearsal",
        "task": "ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001",
        "captured_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "captured_by": "Claude3",
        "question": (
            "Does one activation actually move the frozen PG16 target off its "
            "2026-07-01 eligible ceiling, and is the Defect B refresh_key what does it?"
        ),
        "method": (
            "Runs the real ACTIVATION_RELATIONS chain against the live pair inside a "
            "rolled-back transaction, then reports the real forecast_horizon_windows "
            "from that uncommitted state. Two arms differing only in ingestion_runs' "
            "refresh_key. No rule is restated; the run predicate is read off the "
            "Relation under test."
        ),
        "read_only_in_effect": True,
        "rollback_proof": "no arm commits; both call rollback() before closing the target",
        "redaction": "row counts, run counts, relation names and calendar dates only",
        "scope_limit": (
            "Pre--b3 measurement. The absolute h28 figure is not the acceptance number "
            "and is not comparable to section 7's source-side projection; the quantity "
            "under test is the difference between arms on an identical target."
        ),
        "horizons": list(HORIZONS),
        "phases": {},
    }

    # Baseline. `forecast_horizon_windows` needs CREATE TEMP TABLE, which a
    # read-only transaction forbids, so this uses a normal connection that is
    # rolled back -- exactly what `probe_target` does.
    baseline_conn = psycopg.connect(target_dsn, connect_timeout=30)
    try:
        payload["phases"]["baseline"] = measure_phase(baseline_conn, label="baseline")
    finally:
        baseline_conn.rollback()
        baseline_conn.close()
    _emit(payload)

    for name, relations in (
        ("unfixed_b_insert_only", unfixed_b_relations()),
        ("fixed_insert_then_refresh", ACTIVATION_RELATIONS),
    ):
        payload["phases"][name] = run_arm(
            name, relations, source_dsn=source_dsn, target_dsn=target_dsn
        )
        _emit(payload)

    # Nothing committed: re-read the target on a fresh connection and require it
    # to match the baseline census exactly.
    verify_conn = psycopg.connect(target_dsn, connect_timeout=30)
    try:
        verify_conn.read_only = True
        after_rollback = run_status_census(verify_conn)
    finally:
        verify_conn.close()
    payload["rollback_verified"] = {
        "run_census_after_rollback": after_rollback,
        "matches_baseline": after_rollback == payload["phases"]["baseline"]["run_census"],
    }

    baseline = payload["phases"]["baseline"]
    unfixed = payload["phases"]["unfixed_b_insert_only"]["after"]
    fixed = payload["phases"]["fixed_insert_then_refresh"]["after"]
    runs_fixed = next(
        r for r in payload["phases"]["fixed_insert_then_refresh"]["relations"] if r["relation"] == RUNS
    )
    runs_unfixed = next(
        r for r in payload["phases"]["unfixed_b_insert_only"]["relations"] if r["relation"] == RUNS
    )

    payload["findings"] = {
        "F1_target_is_stale_not_broken": {
            "baseline_eligible_max": _windows(baseline).get("eligible_max_date"),
            "unfixed_eligible_max": _windows(unfixed).get("eligible_max_date"),
            "fixed_eligible_max": _windows(fixed).get("eligible_max_date"),
            "meaning": (
                "How far one activation moves the eligible ceiling. Movement in the "
                "unfixed arm is plain INSERT of runs the target never held; movement "
                "beyond that is attributable to refresh_key."
            ),
        },
        "F2_refresh_is_the_variable": {
            "runs_inserted_unfixed": runs_unfixed["target_rows_inserted"],
            "runs_inserted_fixed": runs_fixed["target_rows_inserted"],
            "runs_refreshed_unfixed": runs_unfixed["target_rows_refreshed"],
            "runs_refreshed_fixed": runs_fixed["target_rows_refreshed"],
            "non_terminal_runs_baseline": baseline["run_census"]["non_terminal"],
            "non_terminal_runs_unfixed": unfixed["run_census"]["non_terminal"],
            "non_terminal_runs_fixed": fixed["run_census"]["non_terminal"],
            "meaning": (
                "A run left non-terminal in the target while the source holds it "
                "terminal pins source_run_complete false for every transaction it "
                "owns. Only the fixed arm can clear one."
            ),
        },
        "F3_horizon_consequence": {
            "h28_stores_baseline": _h(baseline, 28),
            "h28_stores_unfixed": _h(unfixed, 28),
            "h28_stores_fixed": _h(fixed, 28),
            "h14_stores_fixed": _h(fixed, 14),
            "h7_stores_fixed": _h(fixed, 7),
            "longest_streak_baseline": _windows(baseline).get("longest_eligible_streak_days"),
            "longest_streak_unfixed": _windows(unfixed).get("longest_eligible_streak_days"),
            "longest_streak_fixed": _windows(fixed).get("longest_eligible_streak_days"),
            "meaning": (
                "h28 counts stores holding 28 consecutive eligible dates, so a single "
                "frozen date inside the span splits an island rather than shortening "
                "it. This is the criterion-3/5 quantity, measured on the target."
            ),
        },
    }
    _emit(payload)
    print(json.dumps(payload["findings"], indent=2))
    print(f"receipt: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
