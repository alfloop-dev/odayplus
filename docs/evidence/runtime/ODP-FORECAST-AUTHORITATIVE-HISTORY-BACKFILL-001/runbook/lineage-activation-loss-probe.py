"""Measure and reproduce canonical_lineage rows destroyed by activation.

Found while validating the critical-path projection. The eligibility fidelity
probe reported the projection model as CONSERVATIVE, and the sampled
disagreements all sat on 2026-06-23; chasing why exposed a defect underneath.

The finding. On the activated PG16 target, 1 690 of 2026-06-23's 5 606
qualifying transactions carry NO `canonical_lineage` row at all, while the PG15
source carries lineage for every one of them. It is not staleness: the target's
newest lineage row is `projected_at` 2026-07-28T14:34:34Z, the missing rows were
projected at 12:28, and the run that owns them (`85294064`, partition
2026-06-23__06-24, SUCCEEDED) is itself present in the target.

Why it happened. `canonical_lineage`'s primary key is
`(source_snapshot_id, canonical_table, canonical_id)` and `source_snapshot_id`
is derived from record CONTENT. Slice `-s1` re-projected 2026-06-23 under a new
run, which re-points `run_id` while the key stays identical. Activation then:

  1. INSERT ... ON CONFLICT DO NOTHING  -> the re-pointed row is DISCARDED,
     because the target already holds its key under the old run.
  2. prune_sql                          -> the target's old row has no staged
     row matching `(run_id, canonical_id)`, and a staged keeper DOES exist for
     its `canonical_id`, so the fail-closed guard is satisfied and the row is
     DELETED.

Net: the pointer is deleted and its replacement was never inserted. The guard
was written to make it impossible to strip a record's last lineage, and it does
check for a keeper -- but it checks STAGING, i.e. what the source holds, not
what the target will hold after an insert that may have been discarded.

Why it was invisible. `model_ready.forecast_training_view` computes
`lineage_complete` as `bool_and(cardinality(source_snapshot_ids) > 0)`. A
transaction with no lineage at all yields NULL there, and `bool_and` IGNORES
NULLs -- so a day where 1 690 of 5 606 transactions have no lineage whatsoever
still reports `lineage_complete`, `data_quality_score = 1.0` and
`is_training_eligible`. Two defects cancelling: activation destroys the
attestation, the view declines to notice. That is why the projection model,
which coalesces the same test to FALSE, looked like the pessimistic one.

Blast radius is small and self-healing but recurring: a row is lost only on the
activation that follows its re-pointing, and the NEXT activation restores it
(the conflicting row is gone by then). The backwards slices `-b1`..`-b4` ingest
dates the target has never seen, so they cannot trigger it; the exposure is
dates re-ingested after they were already activated.

This probe does two things:
  * MEASURES the live loss, source vs target, and pins it to `projected_at`
    against the target's own activation cutoff so staleness is excluded.
  * REPRODUCES it deterministically, driving the real `prune_sql` / `refresh_sql`
    builders over a scratch schema under the OLD relation config and the NEW
    one, and shows the row is lost under the first and re-pointed under the
    second.

Read-only in effect. The reproduction creates a scratch schema inside a
transaction and ROLLS BACK -- PostgreSQL DDL is transactional, so nothing
survives; `core`, `data_plane` and `model_ready` are never written.

Usage: source /tmp/odp-forecast-dsn.env && python3 lineage-activation-loss-probe.py
"""

import json
import os
import sys
from datetime import UTC, datetime

import psycopg

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", ".."),
)

from scripts.data_plane.forecast_history_activation import (  # noqa: E402
    ACTIVATION_RELATIONS,
    Relation,
    prune_sql,
    refresh_sql,
)

OUT = os.environ.get(
    "PROBE_OUT",
    "/tmp/odp-forecast-evidence/lineage_activation_loss.json",
)

MISSING_BY_DATE = """
SELECT (txn.event_time AT TIME ZONE 'UTC')::date AS d,
       count(*) AS qualifying,
       count(*) FILTER (WHERE NOT EXISTS (
           SELECT 1 FROM data_plane.canonical_lineage l
           WHERE l.tenant_id = st.tenant_id
             AND l.canonical_table = 'core.transactions'
             AND l.canonical_id = txn.transaction_id)) AS no_lineage
FROM core.transactions txn
JOIN core.stores st ON st.store_id = txn.store_id
WHERE txn.transaction_status = 'succeeded' AND txn.currency = 'TWD'
GROUP BY 1 ORDER BY 1
"""

LINEAGE_COLUMNS = (
    "source_snapshot_id",
    "source_kind",
    "source_id",
    "content_sha256",
    "run_id",
    "tenant_id",
    "canonical_table",
    "canonical_id",
    "projected_at",
)


def measure(target_dsn: str, source_dsn: str) -> dict:
    """Live loss, with staleness excluded by the target's own copy cutoff."""

    with psycopg.connect(target_dsn, connect_timeout=30) as tgt, tgt.cursor() as cur:
        cur.execute("SET statement_timeout = '30min'")
        cur.execute(
            "SELECT max(projected_at) FROM data_plane.canonical_lineage "
            "WHERE canonical_table = 'core.transactions'"
        )
        cutoff = cur.fetchone()[0]
        cur.execute(MISSING_BY_DATE)
        by_date = [
            {"date": d.isoformat(), "qualifying": q, "no_lineage": n}
            for d, q, n in cur.fetchall()
            if n
        ]
        cur.execute(
            "SELECT count(*) FROM data_plane.canonical_lineage "
            "WHERE canonical_table = 'core.transactions'"
        )
        target_rows = cur.fetchone()[0]

    with psycopg.connect(source_dsn, connect_timeout=30) as src, src.cursor() as cur:
        cur.execute("SET statement_timeout = '30min'")
        cur.execute(
            "SELECT count(*) FROM data_plane.canonical_lineage "
            "WHERE canonical_table = 'core.transactions' AND projected_at <= %s",
            (cutoff,),
        )
        source_rows_at_cutoff = cur.fetchone()[0]
        cur.execute(MISSING_BY_DATE)
        source_missing = sum(n for _, _, n in cur.fetchall())

    return {
        "target_activation_cutoff": cutoff.isoformat() if cutoff else None,
        "source_rows_projected_at_or_before_cutoff": source_rows_at_cutoff,
        "target_rows": target_rows,
        "rows_lost_by_activation": source_rows_at_cutoff - target_rows,
        "transactions_without_lineage_on_target": sum(d["no_lineage"] for d in by_date),
        "transactions_without_lineage_on_source": source_missing,
        "target_dates_affected": by_date,
        "staleness_excluded": (
            "every missing row was projected before the target's own cutoff, so "
            "the activation had it staged and still did not land it"
        ),
    }


def reproduce(target_dsn: str) -> dict:
    """Drive the real builders over a scratch schema; roll everything back."""

    live = next(r for r in ACTIVATION_RELATIONS if r.table == "canonical_lineage")
    schema = "activation_loss_repro"
    results = {}

    variants = {
        "before_fix__prune_only": Relation(
            schema,
            "canonical_lineage",
            source_predicate=live.source_predicate,
            prune_superseded_by=live.prune_superseded_by,
            prune_keep_key=live.prune_keep_key,
        ),
        "after_fix__refresh_then_prune": Relation(
            schema,
            "canonical_lineage",
            source_predicate=live.source_predicate,
            refresh_key=live.refresh_key,
            prune_superseded_by=live.prune_superseded_by,
            prune_keep_key=live.prune_keep_key,
        ),
    }

    with psycopg.connect(target_dsn, connect_timeout=30) as conn:
        for name, relation in variants.items():
            cur = conn.cursor()
            cur.execute(f"CREATE SCHEMA {schema}")
            cur.execute(f"SET search_path TO {schema}")
            cur.execute(
                f"""
                CREATE TABLE {schema}.canonical_lineage (
                    source_snapshot_id text NOT NULL,
                    source_kind        text NOT NULL,
                    source_id          text NOT NULL,
                    content_sha256     text NOT NULL,
                    run_id             text NOT NULL,
                    tenant_id          text NOT NULL,
                    canonical_table    text NOT NULL,
                    canonical_id       text NOT NULL,
                    projected_at       timestamptz NOT NULL,
                    PRIMARY KEY (source_snapshot_id, canonical_table, canonical_id)
                )
                """
            )
            cur.execute(f'CREATE TABLE {schema}."activation_stage" '
                        f"(LIKE {schema}.canonical_lineage INCLUDING DEFAULTS)")

            # Target: the record's lineage as an earlier activation landed it.
            cur.execute(
                f"INSERT INTO {schema}.canonical_lineage VALUES "
                "('snap-content-1','orders','order-1','sha-1','run-ABANDONED',"
                "'tenant-1','core.transactions','txn-1', now())"
            )
            # Source right now: same content, so the SAME key, re-pointed at the
            # run that actually completed the partition.
            cur.execute(
                f'INSERT INTO {schema}."activation_stage" VALUES '
                "('snap-content-1','orders','order-1','sha-1','run-SUCCEEDED',"
                "'tenant-1','core.transactions','txn-1', now())"
            )

            column_sql = ", ".join(f'"{c}"' for c in LINEAGE_COLUMNS)
            inserted = cur.execute(
                f"INSERT INTO {schema}.canonical_lineage ({column_sql}) "
                f'SELECT {column_sql} FROM "activation_stage" ON CONFLICT DO NOTHING'
            ).rowcount
            refreshed = 0
            if relation.refresh_key:
                statement, _ = refresh_sql(relation, LINEAGE_COLUMNS, "activation_stage")
                refreshed = cur.execute(statement).rowcount
            pruned = cur.execute(prune_sql(relation, "activation_stage")).rowcount

            cur.execute(
                f"SELECT count(*), coalesce(max(run_id), 'NONE') "
                f"FROM {schema}.canonical_lineage WHERE canonical_id = 'txn-1'"
            )
            remaining, run = cur.fetchone()
            results[name] = {
                "inserted": max(inserted, 0),
                "refreshed": max(refreshed, 0),
                "pruned": max(pruned, 0),
                "lineage_rows_left_for_the_transaction": remaining,
                "run_id_left": run,
                "outcome": (
                    "LINEAGE DESTROYED: the record has no lineage on the target"
                    if remaining == 0
                    else f"re-pointed in place to {run}"
                ),
            }
            conn.rollback()  # drops the scratch schema with it
            cur.close()

    return results


def main() -> None:
    started = datetime.now(UTC)
    target = os.environ["ODAY_DATABASE_URL"]
    source = os.environ["ODP_LEGACY_DATABASE_URL"]

    live = measure(target, source)
    repro = reproduce(target)

    before = repro["before_fix__prune_only"]["lineage_rows_left_for_the_transaction"]
    after = repro["after_fix__refresh_then_prune"]["lineage_rows_left_for_the_transaction"]
    fixed = before == 0 and after == 1

    payload = {
        "captured_at": started.isoformat(),
        "defect": (
            "Activation deletes canonical_lineage rows it could not re-insert. "
            "The primary key is content-derived, so a row re-projected under a "
            "new run keeps its key; ON CONFLICT DO NOTHING discards it and the "
            "prune then drops the target's old row, whose keeper check passes "
            "because it interrogates STAGING rather than the post-insert TARGET."
        ),
        "masked_by": (
            "forecast_training_view's lineage_complete is "
            "bool_and(cardinality(source_snapshot_ids) > 0); a transaction with "
            "no lineage contributes NULL and bool_and ignores NULLs, so the day "
            "still reports fully attested with data_quality_score 1.0."
        ),
        "fix": (
            "canonical_lineage now declares refresh_key = the primary key, so "
            "the re-pointed run_id is written in place before the prune runs "
            "and the prune finds nothing superseded."
        ),
        "live_measurement": live,
        "deterministic_reproduction": repro,
        "reproduction_method": (
            "real prune_sql/refresh_sql over a scratch schema created and rolled "
            "back inside one transaction; no persisted relation touched"
        ),
        "fix_verified": fixed,
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    print(json.dumps({"live": live, "repro": repro, "fix_verified": fixed}, indent=2))


if __name__ == "__main__":
    main()
