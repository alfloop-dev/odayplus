"""Test the README's headline claim instead of arguing it.

The claim, stated in the first paragraph of this evidence directory since the
task opened:

    "No fixture, synthetic, seed, spine, or auto-generated row is introduced
    anywhere in this task. Every transaction that reaches the target is a row
    that already existed in the approved legacy PostgreSQL source."

That is two claims, and nothing had checked either. It is the same shape as the
redaction promise this directory carried for a week before
`evidence-redaction-audit.py` tested it and found it false: a structural
argument about code, standing in for a measurement of what actually landed. The
argument here is also structural -- `forecast_history_activation.py` copies, it
does not generate -- and `install_views.py` separately bans `generate_series(`,
`random(`, `setseed(` and `create table as` from the model-ready SQL. Both are
true. Neither says what is in the database.

So this audit asks the two questions directly, per relation in
`ACTIVATION_RELATIONS`, by comparing the live PG15 source against the live PG16
target:

  1. INJECTION -- is there a target row whose primary key does not exist in the
     source? Such a row was not copied from anywhere, and would falsify the
     claim outright.
  2. DRIFT -- for a key both sides hold, does the target's content still match
     the source's? A row copied before the source re-projected it is not
     synthetic, but it is no longer authoritative either, and the task is named
     for authoritative history.

Question 2 is the one that found something, and it is a defect rather than a
finding about the data. `apps/data_platform/store.py` writes canonical
transactions with `ON CONFLICT (transaction_id) DO UPDATE`, rewriting
`store_id`, `event_time`, `observation_time`, `payment_time`, the three
amounts, `currency`, `transaction_status` and `ingested_at` whenever it
re-projects a changed upstream record. Seven of those columns are exactly what
`model_ready.forecast_training_view` reads. Until this audit,
`Relation("core", "transactions")` carried no `refresh_key`, so `copy_relation`
reached it with `ON CONFLICT DO NOTHING` and a target row could never be
revisited -- Defect B's mechanism, moved from the ingestion ledger onto the fact
table.

Method, and why it is shaped this way. A per-day (or per-relation) aggregate
pass runs first: `count`, a digest of the sorted primary keys, and a digest of
the sorted rows, computed server-side so nothing crosses the wire. Where all
three agree, the two sides are identical and there is nothing to transfer --
which is most of the corpus. Only the disagreeing groups are then pulled row by
row and diffed in Python, which is what turns "these days differ" into "these
columns, this many rows, in this direction".

Redaction. Row counts, column names, dates and digests only. No transaction,
store, tenant, machine or address id is read into the output, and the per-row
pass keeps its keys in local memory and emits only counts. The one monetary
figure published is the amount of the single reversed transaction, which is the
finding itself and identifies nobody.

Cost on the live system. Both connections are opened `read_only`. The aggregate
pass is one sequential scan per relation per side; the drill-down reads only the
groups that disagree. Measured at ~120 s against the source while a backfill
slice was running, which is a few hundred MB of sequential read against a
workload that is Mongo-read and PostgreSQL-write bound.

Usage: source /tmp/odp-forecast-dsn.env && python3 canonical-row-drift-audit.py
"""

import collections
import json
import os
import sys
import time
from datetime import UTC, datetime

import psycopg

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", ".."),
)

from scripts.data_plane.forecast_history_activation import (  # noqa: E402
    ACTIVATION_RELATIONS,
)

OUT = os.environ.get(
    "ODP_DRIFT_AUDIT_OUT",
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "canonical_row_drift_audit.json"
    ),
)

STATEMENT_TIMEOUT_MS = 600_000

# The columns `forecast_training_view` actually reads out of core.transactions,
# taken from the installed view definition rather than from the repo SQL. Drift
# in one of these changes a training row; drift outside them does not.
VIEW_MATERIAL_COLUMNS = (
    "store_id",
    "event_time",
    "observation_time",
    "ingested_at",
    "net_amount",
    "transaction_status",
    "currency",
)

# The view's own admission filter, so "would this row have been trained on" can
# be answered on each side separately.
VIEW_FILTER = "transaction_status::text = 'succeeded' AND currency::text = 'TWD'"


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _connect(dsn: str) -> psycopg.Connection:
    connection = psycopg.connect(dsn, connect_timeout=30)
    connection.read_only = True
    connection.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
    return connection


def primary_key(connection: psycopg.Connection, qualified: str) -> tuple[str, ...]:
    rows = connection.execute(
        "SELECT a.attname FROM pg_index i "
        "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
        "WHERE i.indrelid = %s::regclass AND i.indisprimary "
        "ORDER BY array_position(i.indkey, a.attnum)",
        (qualified,),
    ).fetchall()
    return tuple(row[0] for row in rows)


def columns_of(connection: psycopg.Connection, schema: str, table: str) -> tuple[str, ...]:
    rows = connection.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
        (schema, table),
    ).fetchall()
    return tuple(row[0] for row in rows)


def _key_expr(key: tuple[str, ...]) -> str:
    return "||'|'||".join(f"{_quote(name)}::text" for name in key)


def _row_expr(columns: tuple[str, ...]) -> str:
    # `geom` is compared as EWKB rather than as text: the two servers run the
    # same PostGIS, but a text rendering difference would be indistinguishable
    # from real drift and this audit must not report one as the other.
    parts = []
    for name in columns:
        if name == "geom":
            parts.append(f"coalesce(encode({_quote(name)}::bytea, 'hex'), '~')")
        else:
            parts.append(f"coalesce({_quote(name)}::text, '~')")
    return "||'|'||".join(parts)


def aggregate_pass(
    connection: psycopg.Connection,
    relation,
    key: tuple[str, ...],
    columns: tuple[str, ...],
    group_by: str | None,
) -> dict[str, tuple]:
    """Per-group count + key digest + row digest, all computed server-side."""

    key_expr = _key_expr(key)
    row_expr = _row_expr(columns)
    group_select = f"{group_by} AS grp" if group_by else "'*'::text AS grp"
    group_clause = f" GROUP BY {group_by}" if group_by else ""
    sql = (
        f"SELECT {group_select}, count(*), "
        f"md5(string_agg({key_expr}, ',' ORDER BY {key_expr})), "
        f"md5(string_agg({row_expr}, ',' ORDER BY {key_expr})) "
        f"FROM {relation.qualified}{group_clause}"
    )
    return {str(r[0]): (int(r[1]), r[2], r[3]) for r in connection.execute(sql).fetchall()}


def drill_down(
    connection: psycopg.Connection,
    relation,
    key: tuple[str, ...],
    columns: tuple[str, ...],
    group_by: str | None,
    groups: list[str],
) -> dict[str, dict]:
    """Fetch the disagreeing groups row by row. Keys never leave this process."""

    key_expr = _key_expr(key)
    select = ", ".join(f"{_quote(name)}::text" for name in columns)
    if group_by:
        sql = (
            f"SELECT {group_by}::text, {key_expr}, {select} "
            f"FROM {relation.qualified} WHERE {group_by}::text = ANY(%s)"
        )
        rows = connection.execute(sql, (groups,)).fetchall()
    else:
        sql = f"SELECT '*'::text, {key_expr}, {select} FROM {relation.qualified}"
        rows = connection.execute(sql).fetchall()
    out: dict[str, dict] = {}
    for row in rows:
        out.setdefault(str(row[0]), {})[row[1]] = tuple(row[2:])
    return out


def audit_relation(source, target, relation, *, group_by: str | None) -> dict:
    key = primary_key(target, relation.qualified)
    source_columns = columns_of(source, relation.schema, relation.table)
    target_columns = columns_of(target, relation.schema, relation.table)
    shared = tuple(name for name in target_columns if name in source_columns)

    started = time.monotonic()
    source_groups = aggregate_pass(source, relation, key, shared, group_by)
    target_groups = aggregate_pass(target, relation, key, shared, group_by)

    identical: list[str] = []
    disagreeing: list[str] = []
    target_only_groups: list[str] = []
    for name, (_, target_keys, target_rows) in target_groups.items():
        if name not in source_groups:
            target_only_groups.append(name)
            continue
        _, source_keys, source_rows = source_groups[name]
        if source_keys == target_keys and source_rows == target_rows:
            identical.append(name)
        else:
            disagreeing.append(name)

    drift_columns: collections.Counter = collections.Counter()
    drifted_rows = 0
    target_only_rows = 0
    source_only_rows = 0
    per_group: list[dict] = []
    direction: collections.Counter = collections.Counter()

    if disagreeing or target_only_groups:
        wanted = sorted(disagreeing + target_only_groups)
        source_rows_by_group = drill_down(source, relation, key, shared, group_by, wanted)
        target_rows_by_group = drill_down(target, relation, key, shared, group_by, wanted)
        for name in wanted:
            side_source = source_rows_by_group.get(name, {})
            side_target = target_rows_by_group.get(name, {})
            group_target_only = set(side_target) - set(side_source)
            group_source_only = set(side_source) - set(side_target)
            group_drift = 0
            group_columns: collections.Counter = collections.Counter()
            for identifier in set(side_source) & set(side_target):
                if side_source[identifier] == side_target[identifier]:
                    continue
                group_drift += 1
                for index, column in enumerate(shared):
                    if side_source[identifier][index] != side_target[identifier][index]:
                        group_columns[column] += 1
                        if column in ("observation_time", "ingested_at", "updated_at"):
                            newer = (
                                "source_newer"
                                if side_source[identifier][index] > side_target[identifier][index]
                                else "target_newer"
                            )
                            direction[f"{column}:{newer}"] += 1
            target_only_rows += len(group_target_only)
            source_only_rows += len(group_source_only)
            drifted_rows += group_drift
            drift_columns.update(group_columns)
            per_group.append(
                {
                    "group": name,
                    "source_rows": len(side_source),
                    "target_rows": len(side_target),
                    "target_only_rows": len(group_target_only),
                    "source_only_rows": len(group_source_only),
                    "drifted_rows": group_drift,
                    "drifted_columns": dict(sorted(group_columns.items())),
                }
            )

    return {
        "relation": relation.qualified,
        "primary_key": list(key),
        "grouped_by": group_by or "whole relation",
        "compared_columns": len(shared),
        "source_rows": sum(v[0] for v in source_groups.values()),
        "target_rows": sum(v[0] for v in target_groups.values()),
        "groups_identical": len(identical),
        "groups_disagreeing": len(disagreeing),
        "injection": {
            "target_only_rows": target_only_rows,
            "verdict": "CLEAN" if target_only_rows == 0 else "INJECTED_ROWS_PRESENT",
            "meaning": (
                "A target row whose primary key the source does not hold was not "
                "copied from the approved source. Zero is the only passing value: it "
                "is what the directory's opening claim asserts."
            ),
        },
        "drift": {
            "drifted_rows": drifted_rows,
            "drifted_columns": dict(sorted(drift_columns.items())),
            "timestamp_direction": dict(sorted(direction.items())),
            "source_only_rows": source_only_rows,
            "meaning": (
                "A shared primary key whose content differs. The target holds a copy "
                "taken before the source re-projected the record. source_only_rows is "
                "the ordinary backlog the pending activation exists to close, and is "
                "not a defect."
            ),
        },
        "per_group": sorted(per_group, key=lambda item: item["group"]),
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }


def view_material_impact(source, target, flagged: list[str]) -> dict:
    """What the drift costs the view, measured through the view's own filter.

    Scoped to the days the aggregate pass flagged. Every other day matched on
    both the key digest and the row digest, so it holds no difference to
    classify -- re-reading them would cost a million rows to prove a zero the
    digests already proved.
    """

    def admitted(connection) -> int:
        row = connection.execute(
            f"SELECT count(*) FROM core.transactions WHERE {VIEW_FILTER}"
        ).fetchone()
        return int(row[0]) if row else 0

    key_expr = "transaction_id::text"
    select = ", ".join(f"{_quote(name)}::text" for name in VIEW_MATERIAL_COLUMNS)
    sql = (
        f"SELECT {key_expr}, {select} FROM core.transactions "
        "WHERE (event_time AT TIME ZONE 'UTC')::date = ANY(%s::date[])"
    )

    if not flagged:
        return {
            "view_filter": VIEW_FILTER,
            "flagged_days": [],
            "transactions_admitted_by_source": admitted(source),
            "transactions_admitted_by_target": admitted(target),
            "rows_whose_admission_differs": 0,
            "rows_admitted_by_both_with_a_different_net_amount": 0,
            "reversals": [],
            "meaning": "no day disagreed on either digest, so there is nothing to classify",
        }

    source_rows = {r[0]: tuple(r[1:]) for r in source.execute(sql, (flagged,)).fetchall()}
    target_rows = {r[0]: tuple(r[1:]) for r in target.execute(sql, (flagged,)).fetchall()}

    status_index = VIEW_MATERIAL_COLUMNS.index("transaction_status")
    net_index = VIEW_MATERIAL_COLUMNS.index("net_amount")
    reversals = []
    admission_changes = 0
    net_amount_changes = 0
    for identifier in set(source_rows) & set(target_rows):
        side_source, side_target = source_rows[identifier], target_rows[identifier]
        if side_source == side_target:
            continue
        source_admits = side_source[status_index] == "succeeded"
        target_admits = side_target[status_index] == "succeeded"
        if source_admits != target_admits:
            admission_changes += 1
            reversals.append(
                {
                    "source_status": side_source[status_index],
                    "target_status": side_target[status_index],
                    "source_net_amount": side_source[net_index],
                    "target_net_amount": side_target[net_index],
                    "consequence": (
                        "the target admits this row to the training view and the source "
                        "does not"
                        if target_admits
                        else "the source admits this row and the target does not"
                    ),
                }
            )
        elif side_source[net_index] != side_target[net_index]:
            net_amount_changes += 1

    return {
        "view_filter": VIEW_FILTER,
        "flagged_days": flagged,
        "transactions_admitted_by_source": admitted(source),
        "transactions_admitted_by_target": admitted(target),
        "rows_whose_admission_differs": admission_changes,
        "rows_admitted_by_both_with_a_different_net_amount": net_amount_changes,
        "reversals": reversals,
        "meaning": (
            "A row the target admits and the source does not is a transaction the "
            "view counts as revenue after the source recorded that it was not. It "
            "reaches a training label as a real sale. No amount here identifies "
            "anyone; the transaction, store and tenant ids are never read out."
        ),
    }


def main() -> int:
    source = _connect(os.environ["ODP_LEGACY_DATABASE_URL"])
    target = _connect(os.environ["ODAY_DATABASE_URL"])
    try:
        relations = []
        for relation in ACTIVATION_RELATIONS:
            if relation.schema != "core":
                # ingestion_runs and canonical_lineage already converge, and
                # their drift is measured by lineage_convergence_rehearsal.json
                # against the question that matters for them.
                continue
            group_by = (
                "((event_time AT TIME ZONE 'UTC')::date)::text"
                if relation.table == "transactions"
                else None
            )
            relations.append(audit_relation(source, target, relation, group_by=group_by))

        transactions = next(
            item for item in relations if item["relation"] == "core.transactions"
        )
        impact = view_material_impact(
            source, target, [group["group"] for group in transactions["per_group"]]
        )

        injected = sum(item["injection"]["target_only_rows"] for item in relations)
        drifted = sum(item["drift"]["drifted_rows"] for item in relations)
        out = {
            "artifact": "canonical_row_drift_audit",
            "task": "ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001",
            "captured_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "captured_by": "Claude3",
            "what_this_is": (
                "The live PG15 source and PG16 target compared row for row across every "
                "core relation the activation copies, testing the two halves of this "
                "directory's opening claim: that no row on the target was invented, and "
                "that a row which reached the target is still the row the source holds."
            ),
            "read_only_in_effect": True,
            "redaction": (
                "counts, column names, dates and digests only; primary keys are read "
                "into process memory to compute set differences and are never emitted. "
                "The one published amount belongs to a reversed transaction and "
                "identifies nobody."
            ),
            "claim_under_test": (
                "No fixture, synthetic, seed, spine, or auto-generated row is introduced "
                "anywhere in this task. Every transaction that reaches the target is a "
                "row that already existed in the approved legacy PostgreSQL source."
            ),
            "relations": relations,
            "view_material_impact": impact,
            "summary": {
                "injected_rows_across_all_relations": injected,
                "drifted_rows_across_all_relations": drifted,
                "injection_verdict": "CLEAN" if injected == 0 else "FAILED",
                "drift_verdict": "CLEAN" if drifted == 0 else "STALE_TARGET_ROWS_PRESENT",
            },
        }
        with open(OUT, "w", encoding="utf-8") as handle:
            json.dump(out, handle, indent=2, sort_keys=False)
            handle.write("\n")
        print(json.dumps(out["summary"], indent=2))
        print(f"wrote {OUT}")
        return 0
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    raise SystemExit(main())
