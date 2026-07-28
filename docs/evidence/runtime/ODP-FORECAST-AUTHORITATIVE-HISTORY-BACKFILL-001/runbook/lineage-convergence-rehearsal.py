"""Rehearse the acceptance activation against the LIVE pair, and roll it back.

Why this exists. The Defect E fix -- giving `data_plane.canonical_lineage` a
`refresh_key` equal to its primary key so a re-pointed `run_id` is written in
place before `prune_sql` runs -- has so far been proven twice, and neither proof
is the thing that matters:

  * `lineage_activation_loss.json` MEASURED the damage a past activation already
    did (1 841 rows gone, 1 693 transactions left with no lineage at all), and
  * REPRODUCED the mechanism on a scratch schema holding exactly one row.

Neither ran the fixed code over the real source and the real target. That gap is
load-bearing, because Defect E was itself a case where the deductive reading of
`prune_sql` looked safe: its fail-closed keeper check is real, it just
interrogates STAGING instead of the post-insert TARGET. A defect that survived
one careful reading of the SQL deserves a measurement, not a second reading.

It is also the single moment that decides the acceptance receipt. The blast
radius noted in the loss probe -- "a row is lost only on the activation that
follows its re-pointing, and the NEXT activation restores it" -- is reassuring
for a system that activates continuously and useless here: the finisher runs
`activate` exactly ONCE, at the end, immediately after a backfill in which every
resumed partition re-pointed its lineage. There is no next activation to heal
it. Whatever this rehearsal says the unfixed code would destroy is exactly what
the acceptance receipt would have been missing.

What it does. For each arm it opens one transaction on the target, snapshots the
current lineage keys into a temp table, drives the REAL `copy_relation` over the
REAL `ACTIVATION_RELATIONS` chain -- same dependency order, same statement
timeout, same advisory lock as `run_activation` -- measures what happened to
every snapshotted key, and ROLLS BACK. Two arms, one variable:

  unfixed   `canonical_lineage` with `refresh_key = ()`   (prune only, pre-fix)
  fixed     `ACTIVATION_RELATIONS` exactly as shipped     (refresh, then prune)

`ingestion_runs` keeps its `refresh_key` in BOTH arms: that is Defect B's fix and
is not the variable under test. The only difference between the arms is the one
line the fix changed.

Effect on the live system. The target is written and rolled back -- PostgreSQL
gives no other way to observe a multi-statement convergence -- so nothing
survives except dead tuples for autovacuum. The source is opened `read_only` and
is only ever COPYed out of. It is read twice at ~493k rows per relation, which
lands on the same PG15 instance the running slice writes to; that is a sequential
read of a few hundred MB against a workload that is Mongo-read and PG-write
bound, and no slice was suspended, resumed or patched to run it.

Usage: source /tmp/odp-forecast-dsn.env && python3 lineage-convergence-rehearsal.py
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
)

OUT = os.environ.get(
    "ODP_REHEARSAL_OUT",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lineage_convergence_rehearsal.json"),
)

LINEAGE = "data_plane.canonical_lineage"
TX_PREDICATE = "canonical_table = 'core.transactions'"

# The snapshot of target lineage taken before the copies run. Keyed by the
# primary key plus the run pointer, because the question is not only "did the row
# survive" but "did its pointer move".
SNAPSHOT_SQL = f"""
CREATE TEMP TABLE rehearsal_baseline_lineage ON COMMIT DROP AS
SELECT source_snapshot_id, canonical_table, canonical_id, run_id
FROM {LINEAGE} WHERE {TX_PREDICATE}
"""

PK_MATCH = (
    "l.source_snapshot_id = b.source_snapshot_id "
    "AND l.canonical_table = b.canonical_table "
    "AND l.canonical_id = b.canonical_id"
)


def unfixed_relations():
    """ACTIVATION_RELATIONS with the Defect E fix backed out of lineage only."""

    return tuple(
        replace(relation, refresh_key=())
        if relation.qualified == LINEAGE
        else relation
        for relation in ACTIVATION_RELATIONS
    )


def measure_target(connection, *, with_baseline: bool) -> dict:
    """Lineage integrity as the target currently holds it."""

    def scalar(sql: str) -> int:
        row = connection.execute(sql).fetchone()
        return int(row[0]) if row else 0

    result = {
        "lineage_rows_core_transactions": scalar(
            f"SELECT count(*) FROM {LINEAGE} WHERE {TX_PREDICATE}"
        ),
        "transactions": scalar("SELECT count(*) FROM core.transactions"),
        "transactions_without_any_lineage": scalar(
            "SELECT count(*) FROM core.transactions t WHERE NOT EXISTS ("
            f"SELECT 1 FROM {LINEAGE} l WHERE {TX_PREDICATE} "
            "AND l.canonical_id = t.transaction_id)"
        ),
    }
    result["transactions_without_any_lineage_by_date"] = [
        {"event_date": str(row[0]), "transactions": int(row[1])}
        for row in connection.execute(
            "SELECT (t.event_time AT TIME ZONE 'UTC')::date AS d, count(*) "
            "FROM core.transactions t WHERE NOT EXISTS ("
            f"SELECT 1 FROM {LINEAGE} l WHERE {TX_PREDICATE} "
            "AND l.canonical_id = t.transaction_id) "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 20"
        ).fetchall()
    ]
    if not with_baseline:
        return result

    result["baseline_keys_deleted"] = scalar(
        "SELECT count(*) FROM rehearsal_baseline_lineage b WHERE NOT EXISTS ("
        f"SELECT 1 FROM {LINEAGE} l WHERE {PK_MATCH})"
    )
    result["baseline_keys_repointed_in_place"] = scalar(
        "SELECT count(*) FROM rehearsal_baseline_lineage b WHERE EXISTS ("
        f"SELECT 1 FROM {LINEAGE} l WHERE {PK_MATCH} AND l.run_id <> b.run_id)"
    )
    result["transactions_stripped_of_all_lineage"] = scalar(
        "SELECT count(DISTINCT b.canonical_id) FROM rehearsal_baseline_lineage b "
        f"WHERE NOT EXISTS (SELECT 1 FROM {LINEAGE} l WHERE {TX_PREDICATE} "
        "AND l.canonical_id = b.canonical_id)"
    )
    return result


def run_arm(name: str, relations, *, source_dsn: str, target_dsn: str) -> dict:
    """One full activation chain against the live pair, then ROLLBACK."""

    source = psycopg.connect(source_dsn, connect_timeout=30)
    target = psycopg.connect(target_dsn, connect_timeout=30)
    try:
        source.read_only = True
        source.execute(f"SET statement_timeout = {COPY_RELATION_TIMEOUT_MS}")
        target.execute(f"SET statement_timeout = {COPY_RELATION_TIMEOUT_MS}")
        target.execute(SNAPSHOT_SQL)
        target.execute(
            "CREATE INDEX ON rehearsal_baseline_lineage "
            "(source_snapshot_id, canonical_table, canonical_id)"
        )
        target.execute("CREATE INDEX ON rehearsal_baseline_lineage (canonical_id)")
        target.execute("ANALYZE rehearsal_baseline_lineage")

        receipts = []
        started = time.monotonic()
        with _activation_lock(target):
            for relation in relations:
                at = time.monotonic()
                receipt = copy_relation(source, target, relation)
                receipt["elapsed_seconds"] = round(time.monotonic() - at, 1)
                receipts.append(receipt)
        elapsed = round(time.monotonic() - started, 1)
        after = measure_target(target, with_baseline=True)
        return {
            "arm": name,
            "relations": receipts,
            "chain_elapsed_seconds": elapsed,
            "after": after,
            "committed": False,
        }
    finally:
        target.rollback()
        target.close()
        source.close()


def main() -> int:
    source_dsn = os.environ["ODP_LEGACY_DATABASE_URL"]
    target_dsn = os.environ["ODAY_DATABASE_URL"]

    with psycopg.connect(target_dsn, connect_timeout=30) as probe:
        probe.read_only = True
        before = measure_target(probe, with_baseline=False)

    arms = [
        run_arm("unfixed_prune_only", unfixed_relations(), source_dsn=source_dsn, target_dsn=target_dsn),
        run_arm("fixed_refresh_then_prune", ACTIVATION_RELATIONS, source_dsn=source_dsn, target_dsn=target_dsn),
    ]
    by_name = {arm["arm"]: arm for arm in arms}
    unfixed, fixed = by_name["unfixed_prune_only"], by_name["fixed_refresh_then_prune"]

    out = {
        "artifact": "lineage_convergence_rehearsal",
        "task": "ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001",
        "captured_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "captured_by": "Claude3",
        "what_this_is": (
            "The finisher's activate, run twice against the live PG15 source and PG16 "
            "target inside a transaction that is rolled back: once with the Defect E fix "
            "backed out of canonical_lineage, once with it in place. Only refresh_key on "
            "canonical_lineage differs between the arms."
        ),
        "read_only_in_effect": True,
        "rollback_proof": "no arm commits; both call rollback() before closing the target",
        "redaction": "row counts, run counts, relation names and calendar dates only",
        "before": before,
        "arms": arms,
        "findings": {},
    }

    lineage_unfixed = next(
        r for r in unfixed["relations"] if r["relation"] == LINEAGE
    )
    lineage_fixed = next(r for r in fixed["relations"] if r["relation"] == LINEAGE)
    out["findings"] = {
        "R1_unfixed_destroys": {
            "baseline_keys_deleted": unfixed["after"]["baseline_keys_deleted"],
            "transactions_stripped_of_all_lineage": unfixed["after"][
                "transactions_stripped_of_all_lineage"
            ],
            "lineage_rows_pruned": lineage_unfixed["target_rows_pruned"],
            "meaning": (
                "What the acceptance activation would have destroyed if it ran the "
                "pre-fix code today. The finisher activates exactly once, so nothing "
                "would restore these."
            ),
        },
        "R2_fixed_destroys": {
            "baseline_keys_deleted": fixed["after"]["baseline_keys_deleted"],
            "transactions_stripped_of_all_lineage": fixed["after"][
                "transactions_stripped_of_all_lineage"
            ],
            "lineage_rows_refreshed": lineage_fixed["target_rows_refreshed"],
            "lineage_rows_pruned": lineage_fixed["target_rows_pruned"],
            "meaning": (
                "Rows the fix re-points in place instead of deleting. A refreshed row "
                "is one the pre-fix code would have dropped: the insert conflicts on a "
                "content-derived key it already holds, so only an UPDATE can carry the "
                "new run_id across."
            ),
        },
        "R3_repair": {
            "transactions_without_any_lineage_before": before[
                "transactions_without_any_lineage"
            ],
            "transactions_without_any_lineage_after_fixed": fixed["after"][
                "transactions_without_any_lineage"
            ],
            "transactions_without_any_lineage_after_unfixed": unfixed["after"][
                "transactions_without_any_lineage"
            ],
            "meaning": (
                "Whether activation repairs the damage the earlier activation already "
                "did. Rows absent from the target are re-inserted by the plain "
                "ON CONFLICT DO NOTHING insert in either arm; the arms differ only on "
                "rows the target still holds under a superseded run."
            ),
        },
        "R4_chain_cost": {
            "unfixed_seconds": unfixed["chain_elapsed_seconds"],
            "fixed_seconds": fixed["chain_elapsed_seconds"],
            "finisher_first_budget_seconds": 3600,
            "meaning": (
                "Wall clock for the whole relation chain, against the finisher's first "
                "escalating budget. Excludes probe_target, which activate runs after "
                "the copies."
            ),
        },
    }

    path = os.path.abspath(OUT)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=2)
        handle.write("\n")
    print(json.dumps(out["findings"], indent=2))
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
