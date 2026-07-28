"""Repair canonical lineage that an abandoned ingestion run left unattested.

Why this exists
---------------
``model_ready.forecast_training_view`` decides a store-day is usable with

    bool_and(ingestion.status = 'SUCCEEDED' AND ingestion.finished_at IS NOT NULL)

over *every* ``data_plane.canonical_lineage`` row of *every* transaction in that
day. One lineage row owned by a run that never reached a terminal status is
therefore enough to disqualify the whole store-day, permanently.

The governed ingestion pipeline cannot repair that by itself, and this is not a
bug in the pipeline so much as an unhandled interaction:

* A partition run commits lineage per batch, so a run killed mid-partition (for
  example by the GKE Job ``activeDeadlineSeconds``) leaves committed lineage
  behind while its own run row stays at ``RUNNING`` forever -- nothing ever
  transitions a process that no longer exists.
* The resuming run starts from ``data_plane.checkpoints`` and only reconciles
  the records it actually read, so the abandoned run's rows are never revisited.
* Re-running the partition does not help either: lineage is inserted
  ``ON CONFLICT (source_snapshot_id, canonical_table, canonical_id) DO NOTHING``
  and ``source_snapshot_id`` is derived from record content, so re-reading an
  unchanged record regenerates the same key and the insert is discarded. The
  row keeps pointing at the abandoned run.

So the only honest repair is to remove the unattested lineage *and* the
partition checkpoint, then let one governed run re-ingest the whole partition
and re-attest those records under a run that actually reconciles.

What this module refuses to do
------------------------------
It never edits an attestation. It does not re-point lineage at a different run,
does not mark a run terminal, and does not relax the view: all three would
declare records attested by a run that never reconciled them. It only deletes
lineage that no completed run vouches for, and only after writing a byte-exact
backup that ``restore`` can put back.

Deleting is destructive and temporarily *widens* the outage -- the affected
transactions have no lineage at all until the re-ingest lands. That is why
``plan`` is the default mode, ``apply`` requires ``--confirm-run-id``, and the
plan reports how many transactions would be left lineage-less.

Modes
-----
``plan``
    Read-only. Validates every guard and reports the exact repair scope.
``apply``
    Writes the backup, then deletes the run's lineage and the partition
    checkpoint in one transaction. Requires ``--confirm-run-id`` and
    ``--backup``.
``restore``
    Re-inserts the backed-up lineage rows and checkpoint, conflict-safe. The
    inverse of ``apply`` for as long as no re-ingest has replaced the rows.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import psycopg

from scripts.data_plane.forecast_history_activation import (
    SOURCE_INSTANCE_ENV,
    SOURCE_URL_ENV,
    _required,
    require_activation_dsn,
)

CANONICAL_TRANSACTION_TABLE = "core.transactions"
TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED"})

# Columns that would be non-null on a run that actually reconciled. A run
# holding any of them has an attestation this module must not touch.
RECONCILIATION_COLUMNS = (
    "finished_at",
    "canonical_checksum",
    "raw_checksum",
    "source_checksum",
)


class LineageRepairError(RuntimeError):
    """Raised when the repair contract cannot be satisfied safely."""


@dataclass(frozen=True)
class RepairScope:
    """Everything the repair touches, resolved before anything is written."""

    run_id: str
    source_kind: str
    partition_key: str
    status: str
    lineage_rows: int
    lineage_tables: tuple[str, ...]
    checkpoint_run_id: str | None
    successor_runs: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "source_kind": self.source_kind,
            "partition_key": self.partition_key,
            "status": self.status,
            "lineage_rows": self.lineage_rows,
            "lineage_tables": list(self.lineage_tables),
            "checkpoint_run_id": self.checkpoint_run_id,
            "successor_runs": list(self.successor_runs),
        }


def source_dsn(env: Mapping[str, str] | None = None) -> str:
    values = dict(os.environ if env is None else env)
    return require_activation_dsn(
        _required(values, SOURCE_URL_ENV),
        field=SOURCE_URL_ENV,
        instance_env=SOURCE_INSTANCE_ENV,
        env=values,
    )


def check_run_is_repairable(run: Mapping[str, Any]) -> None:
    """Fail closed unless the run is abandoned *and* carries no attestation.

    Both halves matter. A non-terminal status alone is not enough -- a run that
    is genuinely still executing would have its in-flight lineage deleted out
    from under it. A missing reconciliation alone is not enough either, because
    a terminal run's lineage is exactly what the view is supposed to trust.
    """

    status = str(run.get("status") or "")
    if status in TERMINAL_STATUSES:
        raise LineageRepairError(
            f"run {run.get('run_id')} is {status}; its lineage is attested and must not be deleted"
        )
    attested = [name for name in RECONCILIATION_COLUMNS if run.get(name) is not None]
    if attested:
        raise LineageRepairError(
            f"run {run.get('run_id')} carries reconciliation values "
            f"({', '.join(attested)}); refusing to delete attested lineage"
        )
    for name in ("processed_count", "valid_loaded"):
        value = run.get(name)
        if value is not None and int(value) > 0:
            raise LineageRepairError(
                f"run {run.get('run_id')} reports {name}={value}; refusing to delete attested lineage"
            )


def check_scope_is_modelled(scope: RepairScope) -> None:
    """Fail closed when the run's lineage is wider than this repair models.

    The re-ingest that restores the deleted rows runs one ``source_kind``
    partition. If the run also owns lineage for other canonical tables, a
    single partition re-run would not necessarily rebuild all of it, and the
    delete would strip attestation this module cannot restore.
    """

    if not scope.lineage_rows:
        raise LineageRepairError(f"run {scope.run_id} owns no lineage; nothing to repair")
    unexpected = [name for name in scope.lineage_tables if name != CANONICAL_TRANSACTION_TABLE]
    if unexpected:
        raise LineageRepairError(
            f"run {scope.run_id} owns lineage for {', '.join(sorted(unexpected))}; "
            "only core.transactions lineage is repairable here"
        )
    if not scope.partition_key:
        raise LineageRepairError(
            f"run {scope.run_id} has no partition_key; the re-ingest window is unknown"
        )


def resolve_scope(connection: psycopg.Connection, run_id: str) -> tuple[RepairScope, dict[str, Any]]:
    cursor = connection.execute(
        "SELECT * FROM data_plane.ingestion_runs WHERE run_id = %s",
        (run_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise LineageRepairError(f"run {run_id} does not exist")
    assert cursor.description is not None
    run = {str(column.name): value for column, value in zip(cursor.description, row, strict=True)}

    lineage = connection.execute(
        """
        SELECT canonical_table, count(*)
        FROM data_plane.canonical_lineage
        WHERE run_id = %s
        GROUP BY canonical_table
        ORDER BY canonical_table
        """,
        (run_id,),
    ).fetchall()

    checkpoint = connection.execute(
        "SELECT run_id FROM data_plane.checkpoints WHERE source_kind = %s AND partition_key = %s",
        (run["source_kind"], run["partition_key"]),
    ).fetchone()

    successors = connection.execute(
        """
        SELECT run_id FROM data_plane.ingestion_runs
        WHERE source_kind = %s AND partition_key = %s AND status = 'SUCCEEDED'
        ORDER BY started_at
        """,
        (run["source_kind"], run["partition_key"]),
    ).fetchall()

    scope = RepairScope(
        run_id=str(run["run_id"]),
        source_kind=str(run["source_kind"]),
        partition_key=str(run["partition_key"] or ""),
        status=str(run["status"]),
        lineage_rows=sum(int(count) for _, count in lineage),
        lineage_tables=tuple(str(table) for table, _ in lineage),
        checkpoint_run_id=str(checkpoint[0]) if checkpoint else None,
        successor_runs=tuple(str(value) for (value,) in successors),
    )
    return scope, run


def _lineage_rows(connection: psycopg.Connection, run_id: str) -> list[dict[str, Any]]:
    cursor = connection.execute(
        "SELECT * FROM data_plane.canonical_lineage WHERE run_id = %s",
        (run_id,),
    )
    assert cursor.description is not None
    names = [column.name for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _checkpoint_rows(
    connection: psycopg.Connection, source_kind: str, partition_key: str
) -> list[dict[str, Any]]:
    cursor = connection.execute(
        "SELECT * FROM data_plane.checkpoints WHERE source_kind = %s AND partition_key = %s",
        (source_kind, partition_key),
    )
    assert cursor.description is not None
    names = [column.name for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _jsonable(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, str | None]]:
    return [{key: None if value is None else str(value) for key, value in row.items()} for row in rows]


def run_plan(dsn: str, run_id: str) -> dict[str, Any]:
    with psycopg.connect(dsn) as connection:
        connection.read_only = True
        scope, run = resolve_scope(connection, run_id)
        check_run_is_repairable(run)
        check_scope_is_modelled(scope)
        sole = connection.execute(
            """
            SELECT count(*)
            FROM data_plane.canonical_lineage AS doomed
            WHERE doomed.run_id = %s
              AND NOT EXISTS (
                    SELECT 1 FROM data_plane.canonical_lineage AS other
                    WHERE other.canonical_table = doomed.canonical_table
                      AND other.canonical_id = doomed.canonical_id
                      AND other.run_id <> doomed.run_id
              )
            """,
            (run_id,),
        ).fetchone()
    return {
        "mode": "plan",
        "scope": scope.as_dict(),
        "deletes": {
            "canonical_lineage": scope.lineage_rows,
            "checkpoints": 1 if scope.checkpoint_run_id else 0,
        },
        "transactions_left_without_lineage": int(sole[0]) if sole else 0,
        "requires_reingest_of": scope.partition_key,
    }


def run_apply(dsn: str, run_id: str, *, backup_path: str) -> dict[str, Any]:
    with psycopg.connect(dsn) as connection:
        scope, run = resolve_scope(connection, run_id)
        check_run_is_repairable(run)
        check_scope_is_modelled(scope)

        backup = {
            "run": _jsonable([run])[0],
            "lineage": _jsonable(_lineage_rows(connection, run_id)),
            "checkpoints": _jsonable(
                _checkpoint_rows(connection, scope.source_kind, scope.partition_key)
            ),
        }
        if len(backup["lineage"]) != scope.lineage_rows:
            raise LineageRepairError("backup row count does not match the resolved scope")
        with open(backup_path, "w", encoding="utf-8") as handle:
            json.dump(backup, handle, indent=2, sort_keys=True)
            handle.write("\n")
        # Re-read what was written: a delete whose backup is unreadable is not
        # reversible, and finding that out afterwards is too late.
        with open(backup_path, encoding="utf-8") as handle:
            verified = json.load(handle)
        if len(verified.get("lineage", [])) != scope.lineage_rows:
            raise LineageRepairError(f"backup at {backup_path} did not round-trip")

        deleted_lineage = connection.execute(
            "DELETE FROM data_plane.canonical_lineage WHERE run_id = %s",
            (run_id,),
        ).rowcount
        # The checkpoint must go in the same transaction. Leaving it behind
        # would make the re-ingest resume past the deleted records and the
        # lineage would never come back.
        deleted_checkpoint = connection.execute(
            "DELETE FROM data_plane.checkpoints WHERE source_kind = %s AND partition_key = %s",
            (scope.source_kind, scope.partition_key),
        ).rowcount
        if deleted_lineage != scope.lineage_rows:
            connection.rollback()
            raise LineageRepairError(
                f"deleted {deleted_lineage} lineage rows but planned {scope.lineage_rows}"
            )
        connection.commit()
    return {
        "mode": "apply",
        "status": "SUCCEEDED",
        "scope": scope.as_dict(),
        "backup_path": backup_path,
        "deleted": {
            "canonical_lineage": deleted_lineage,
            "checkpoints": deleted_checkpoint,
        },
        "next_action": (
            f"re-ingest {scope.source_kind} partition {scope.partition_key} "
            "with one governed run so the records are attested again"
        ),
    }


def run_restore(dsn: str, *, backup_path: str) -> dict[str, Any]:
    with open(backup_path, encoding="utf-8") as handle:
        backup = json.load(handle)
    lineage = backup.get("lineage", [])
    checkpoints = backup.get("checkpoints", [])
    if not lineage:
        raise LineageRepairError(f"backup at {backup_path} holds no lineage rows")
    restored = 0
    with psycopg.connect(dsn) as connection:
        for row in lineage:
            columns = sorted(row)
            statement = (
                "INSERT INTO data_plane.canonical_lineage ("
                + ", ".join(f'"{name}"' for name in columns)
                + ") VALUES ("
                + ", ".join(["%s"] * len(columns))
                + ") ON CONFLICT DO NOTHING"
            )
            restored += connection.execute(statement, [row[name] for name in columns]).rowcount
        for row in checkpoints:
            columns = sorted(row)
            statement = (
                "INSERT INTO data_plane.checkpoints ("
                + ", ".join(f'"{name}"' for name in columns)
                + ") VALUES ("
                + ", ".join(["%s"] * len(columns))
                + ") ON CONFLICT DO NOTHING"
            )
            connection.execute(statement, [row[name] for name in columns])
        connection.commit()
    return {
        "mode": "restore",
        "status": "SUCCEEDED",
        "backup_path": backup_path,
        "restored": {"canonical_lineage": restored, "checkpoints": len(checkpoints)},
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=("plan", "apply", "restore"))
    parser.add_argument("--run-id", default=None, help="Abandoned ingestion run to repair.")
    parser.add_argument(
        "--confirm-run-id",
        default=None,
        help="Must repeat --run-id exactly; required by apply because the delete is destructive.",
    )
    parser.add_argument("--backup", default=None, help="Path for the apply/restore backup JSON.")
    parser.add_argument("--output", default=None, help="Optional path for the JSON receipt.")
    args = parser.parse_args(argv)

    dsn = source_dsn()
    if args.mode == "restore":
        if not args.backup:
            raise SystemExit("restore requires --backup")
        payload = run_restore(dsn, backup_path=args.backup)
    else:
        if not args.run_id:
            raise SystemExit(f"{args.mode} requires --run-id")
        if args.mode == "plan":
            payload = run_plan(dsn, args.run_id)
        else:
            if args.confirm_run_id != args.run_id:
                raise SystemExit("apply requires --confirm-run-id to repeat --run-id exactly")
            if not args.backup:
                raise SystemExit("apply requires --backup")
            payload = run_apply(dsn, args.run_id, backup_path=args.backup)

    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
