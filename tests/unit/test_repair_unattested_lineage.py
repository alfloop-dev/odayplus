"""Contract tests for the unattested-lineage repair guards.

The repair deletes governed lineage, so every test here is about what it must
*refuse* to delete. The guards are pure functions precisely so they can be
proven without a live database.
"""

from __future__ import annotations

import json

import pytest

from scripts.data_plane.repair_unattested_lineage import (
    LineageRepairError,
    RepairScope,
    check_run_is_repairable,
    check_scope_is_modelled,
    main,
)

ABANDONED_RUN = {
    "run_id": "069b0984-3cc0-4fbb-811f-07616e148a88",
    "source_kind": "orders",
    "partition_key": "2026-07-06__2026-07-07",
    "status": "RUNNING",
    "finished_at": None,
    "canonical_checksum": None,
    "raw_checksum": None,
    "source_checksum": None,
    "processed_count": 0,
    "valid_loaded": 0,
}


def _run(**overrides: object) -> dict[str, object]:
    run = dict(ABANDONED_RUN)
    run.update(overrides)
    return run


def _scope(**overrides: object) -> RepairScope:
    values: dict[str, object] = {
        "run_id": str(ABANDONED_RUN["run_id"]),
        "source_kind": "orders",
        "partition_key": "2026-07-06__2026-07-07",
        "status": "RUNNING",
        "lineage_rows": 4752,
        "lineage_tables": ("core.transactions",),
        "checkpoint_run_id": "5efc0a7d-e0b6-4012-ae72-1f2b18c9201d",
        "successor_runs": ("5efc0a7d-e0b6-4012-ae72-1f2b18c9201d",),
    }
    values.update(overrides)
    return RepairScope(**values)  # type: ignore[arg-type]


def test_abandoned_unreconciled_run_is_repairable() -> None:
    check_run_is_repairable(_run())
    check_scope_is_modelled(_scope())


@pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED"])
def test_terminal_run_lineage_is_never_deleted(status: str) -> None:
    with pytest.raises(LineageRepairError, match="attested"):
        check_run_is_repairable(_run(status=status))


@pytest.mark.parametrize(
    "column", ["finished_at", "canonical_checksum", "raw_checksum", "source_checksum"]
)
def test_reconciled_run_lineage_is_never_deleted(column: str) -> None:
    # A run can hold a reconciliation value without having been flipped to a
    # terminal status; that value is still an attestation.
    with pytest.raises(LineageRepairError, match="reconciliation values"):
        check_run_is_repairable(_run(**{column: "0" * 64}))


@pytest.mark.parametrize("column", ["processed_count", "valid_loaded"])
def test_run_that_reported_progress_is_never_deleted(column: str) -> None:
    with pytest.raises(LineageRepairError, match=column):
        check_run_is_repairable(_run(**{column: 1}))


def test_lineage_outside_core_transactions_is_out_of_scope() -> None:
    # One partition re-ingest would not rebuild lineage for other canonical
    # tables, so deleting it would strip attestation this module cannot restore.
    with pytest.raises(LineageRepairError, match="core.stores"):
        check_scope_is_modelled(_scope(lineage_tables=("core.stores", "core.transactions")))


def test_run_without_lineage_is_rejected() -> None:
    with pytest.raises(LineageRepairError, match="owns no lineage"):
        check_scope_is_modelled(_scope(lineage_rows=0))


def test_run_without_partition_key_is_rejected() -> None:
    # Without a partition key there is no window to re-ingest, so the delete
    # would be one-way.
    with pytest.raises(LineageRepairError, match="re-ingest window is unknown"):
        check_scope_is_modelled(_scope(partition_key=""))


def test_apply_requires_a_matching_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scripts.data_plane.repair_unattested_lineage.source_dsn",
        lambda: "postgresql://oday_app:secret@10.20.30.40:5432/oday_app",
    )
    with pytest.raises(SystemExit, match="confirm-run-id"):
        main(["apply", "--run-id", "a", "--confirm-run-id", "b", "--backup", "/tmp/x.json"])


def test_apply_requires_a_backup_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scripts.data_plane.repair_unattested_lineage.source_dsn",
        lambda: "postgresql://oday_app:secret@10.20.30.40:5432/oday_app",
    )
    with pytest.raises(SystemExit, match="--backup"):
        main(["apply", "--run-id", "a", "--confirm-run-id", "a"])


def test_restore_rejects_an_empty_backup(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scripts.data_plane.repair_unattested_lineage.source_dsn",
        lambda: "postgresql://oday_app:secret@10.20.30.40:5432/oday_app",
    )
    backup = tmp_path / "backup.json"
    backup.write_text(json.dumps({"lineage": [], "checkpoints": []}), encoding="utf-8")
    with pytest.raises(LineageRepairError, match="no lineage rows"):
        main(["restore", "--backup", str(backup)])
