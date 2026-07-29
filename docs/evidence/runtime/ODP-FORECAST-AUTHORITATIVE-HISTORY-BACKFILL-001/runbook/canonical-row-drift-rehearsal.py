"""Rehearse the core-relation refresh fix against the LIVE pair, and roll it back.

`canonical_row_drift_audit.json` measured the damage: 1 847 `core.transactions`
rows on the PG16 target no longer match the PG15 source, one of them a
transaction the source records as `refunded` at 0.00 while the target still
reports it `succeeded` at 230.00 and the view still counts it as revenue. The
fix gives every `core` relation a `refresh_key` equal to its primary key, so
`copy_relation` converges the row instead of skipping it.

Why a live rehearsal rather than a reading of the SQL. This is the second time
this task has changed `copy_relation`'s conflict behaviour, and the first time
-- Defect E -- was a case where the deductive reading looked safe and was wrong:
`prune_sql`'s fail-closed keeper check is real, it just interrogates STAGING
rather than the post-insert TARGET, and 1 841 lineage rows were already gone
before anyone measured. `lineage-convergence-rehearsal.py` was written for
exactly that reason and this follows it, because the same argument applies with
more force here: the finisher runs `activate` ONCE, at the end of the backfill,
and there is no second activation to correct whatever the first one gets wrong.

The specific risk worth measuring, and it is not "does the refresh work". It is
whether a refresh over `core` can DESTROY something. Three things could:

  * a refresh key that is not unique would let one staged row rewrite several
    target rows. The audit takes each key from `pg_index ... indisprimary`, and
    this rehearsal counts the rows each arm actually rewrote -- a refresh that
    touched more rows than the audit found drifted would be exactly that bug.
  * a refresh could overwrite a column the target legitimately owns. No `core`
    relation has such a column: the target is a copy of the approved source and
    is written by nothing else. That is an argument, so the arms below report
    the row counts before and after, where any net change would contradict it.
  * a refresh could interact with the prune. No `core` relation prunes, and the
    unit tests assert it, so a refresh here can correct a row and can never
    remove one. Both arms report `target_rows_pruned` per relation regardless.

Method. Per arm: open one transaction on the target, COPY a
`(transaction_id, digest)` probe of the source's view-material columns into a
temp table, measure how many target rows disagree with it, drive the REAL
`copy_relation` over the REAL `ACTIVATION_RELATIONS` chain -- same order, same
statement timeout, same advisory lock as `run_activation` -- measure the
disagreement again, and ROLL BACK. Two arms, one variable:

  frozen_core      every `core` relation with `refresh_key = ()`   (pre-fix)
  refreshing_core  `ACTIVATION_RELATIONS` exactly as shipped        (post-fix)

`ingestion_runs` and `canonical_lineage` keep their keys in BOTH arms: those are
the Defect B and Defect E fixes and are not the variable under test.

Effect on the live system. The target is written and rolled back; nothing
survives but dead tuples for autovacuum. The source is opened `read_only` and is
only COPYed out of. No slice is suspended, resumed or patched to run this.

Usage: source /tmp/odp-forecast-dsn.env && python3 canonical-row-drift-rehearsal.py
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
    "ODP_DRIFT_REHEARSAL_OUT",
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "canonical_row_drift_rehearsal.json"
    ),
)

# The columns forecast_training_view reads out of core.transactions. Drift in
# one of these changes a training row; drift outside them does not, so this is
# the set the rehearsal scores itself against.
VIEW_MATERIAL_DIGEST = (
    "md5("
    "coalesce(store_id::text,'~')||'|'||coalesce(event_time::text,'~')||'|'||"
    "coalesce(observation_time::text,'~')||'|'||coalesce(ingested_at::text,'~')||'|'||"
    "coalesce(net_amount::text,'~')||'|'||coalesce(transaction_status::text,'~')||'|'||"
    "coalesce(currency::text,'~')"
    ")"
)

VIEW_FILTER = "transaction_status::text = 'succeeded' AND currency::text = 'TWD'"

PROBE_DDL = (
    "CREATE TEMP TABLE rehearsal_source_probe "
    "(transaction_id uuid PRIMARY KEY, view_digest text) ON COMMIT DROP"
)


def frozen_core_relations():
    """ACTIVATION_RELATIONS with the core refresh keys backed out."""

    return tuple(
        replace(relation, refresh_key=()) if relation.schema == "core" else relation
        for relation in ACTIVATION_RELATIONS
    )


def load_source_probe(source: psycopg.Connection, target: psycopg.Connection) -> int:
    """Stream the source's view-material digest into a target temp table."""

    target.execute(PROBE_DDL)
    read_sql = (
        f"COPY (SELECT transaction_id, {VIEW_MATERIAL_DIGEST} FROM core.transactions) TO STDOUT"
    )
    write_sql = "COPY rehearsal_source_probe (transaction_id, view_digest) FROM STDIN"
    with source.cursor() as reader, target.cursor() as writer:
        with reader.copy(read_sql) as outbound, writer.copy(write_sql) as inbound:
            for block in outbound:
                inbound.write(block)
    target.execute("ANALYZE rehearsal_source_probe")
    row = target.execute("SELECT count(*) FROM rehearsal_source_probe").fetchone()
    return int(row[0]) if row else 0


def measure(target: psycopg.Connection) -> dict:
    """Target state against the source probe, entirely inside the transaction."""

    def scalar(sql: str) -> int:
        row = target.execute(sql).fetchone()
        return int(row[0]) if row else 0

    return {
        "transactions": scalar("SELECT count(*) FROM core.transactions"),
        "rows_disagreeing_with_source": scalar(
            "SELECT count(*) FROM ("
            f"SELECT transaction_id, {VIEW_MATERIAL_DIGEST} AS view_digest "
            "FROM core.transactions"
            ") t JOIN rehearsal_source_probe p ON p.transaction_id = t.transaction_id "
            "WHERE t.view_digest IS DISTINCT FROM p.view_digest"
        ),
        "rows_absent_from_source_probe": scalar(
            "SELECT count(*) FROM core.transactions t WHERE NOT EXISTS ("
            "SELECT 1 FROM rehearsal_source_probe p WHERE p.transaction_id = t.transaction_id)"
        ),
        "transactions_admitted_by_the_view_filter": scalar(
            f"SELECT count(*) FROM core.transactions WHERE {VIEW_FILTER}"
        ),
    }


def run_arm(name: str, relations, *, source_dsn: str, target_dsn: str) -> dict:
    """One full activation chain against the live pair, then ROLLBACK."""

    source = psycopg.connect(source_dsn, connect_timeout=30)
    target = psycopg.connect(target_dsn, connect_timeout=30)
    try:
        source.read_only = True
        source.execute(f"SET statement_timeout = {COPY_RELATION_TIMEOUT_MS}")
        target.execute(f"SET statement_timeout = {COPY_RELATION_TIMEOUT_MS}")

        probe_rows = load_source_probe(source, target)
        before = measure(target)

        receipts = []
        started = time.monotonic()
        with _activation_lock(target):
            for relation in relations:
                at = time.monotonic()
                receipt = copy_relation(source, target, relation)
                receipt["elapsed_seconds"] = round(time.monotonic() - at, 1)
                receipts.append(receipt)
        elapsed = round(time.monotonic() - started, 1)
        after = measure(target)
        return {
            "arm": name,
            "source_probe_rows": probe_rows,
            "relations": receipts,
            "chain_elapsed_seconds": elapsed,
            "before": before,
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

    arms = [
        run_arm("frozen_core", frozen_core_relations(), source_dsn=source_dsn, target_dsn=target_dsn),
        run_arm(
            "refreshing_core", ACTIVATION_RELATIONS, source_dsn=source_dsn, target_dsn=target_dsn
        ),
    ]
    by_name = {arm["arm"]: arm for arm in arms}
    frozen, fixed = by_name["frozen_core"], by_name["refreshing_core"]

    def core_refreshed(arm: dict) -> int:
        return sum(
            receipt["target_rows_refreshed"]
            for receipt in arm["relations"]
            if receipt["relation"].startswith("core.")
        )

    def core_pruned(arm: dict) -> int:
        return sum(
            receipt["target_rows_pruned"]
            for receipt in arm["relations"]
            if receipt["relation"].startswith("core.")
        )

    out = {
        "artifact": "canonical_row_drift_rehearsal",
        "task": "ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001",
        "captured_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "captured_by": "Claude3",
        "what_this_is": (
            "The finisher's activate, run twice against the live PG15 source and PG16 "
            "target inside a transaction that is rolled back: once with the core "
            "relations frozen as they were, once with each of them refreshing on its "
            "primary key. Only refresh_key on the core relations differs between arms."
        ),
        "read_only_in_effect": False,
        "committed": False,
        "how_it_stays_safe": (
            "Both arms write to the target inside one transaction and ROLL BACK -- "
            "PostgreSQL offers no other way to observe a multi-statement convergence. "
            "The source is opened read_only and is only COPYed out of."
        ),
        "redaction": "row counts, relation names and elapsed seconds only",
        "arms": arms,
        "verdict": {
            "drift_left_by_the_frozen_arm": frozen["after"]["rows_disagreeing_with_source"],
            "drift_left_by_the_refreshing_arm": fixed["after"]["rows_disagreeing_with_source"],
            "core_rows_refreshed_frozen_arm": core_refreshed(frozen),
            "core_rows_refreshed_refreshing_arm": core_refreshed(fixed),
            "core_rows_pruned_either_arm": core_pruned(frozen) + core_pruned(fixed),
            "view_admitted_before": fixed["before"]["transactions_admitted_by_the_view_filter"],
            "view_admitted_after_refreshing": fixed["after"][
                "transactions_admitted_by_the_view_filter"
            ],
            "reading": (
                "The frozen arm is what the acceptance activation would have done: it "
                "inserts the backlog and leaves every already-present row at whatever "
                "the first copy saw. The refreshing arm converges them. Rows pruned "
                "from core must be zero in both arms -- no core relation declares a "
                "prune, so a refresh can correct a record and can never remove one."
            ),
        },
    }
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=2, sort_keys=False)
        handle.write("\n")
    print(json.dumps(out["verdict"], indent=2))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
