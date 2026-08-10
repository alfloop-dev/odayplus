"""Tests for the orchestrator task dependency graph migration script."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import ai_status
import pytest
from check_task_dependency_resolvability import check, load_archive, load_board
from migrate_task_dependency_graph import (
    migrate_dependencies,
    run_migration,
)


def write_board(tmp_path: Path, tasks: list[dict]) -> Path:
    path = tmp_path / "ai-status.json"
    path.write_text(json.dumps({"tasks": tasks}), encoding="utf-8")
    return path


def write_archive(tmp_path: Path, snapshots: list[dict]) -> Path:
    archive_dir = tmp_path / "tasks"
    archive_dir.mkdir(parents=True, exist_ok=True)
    for snapshot in snapshots:
        (archive_dir / f"{snapshot['task_id']}.json").write_text(
            json.dumps(snapshot), encoding="utf-8"
        )
    return archive_dir


def test_migrate_dependencies_replaces_dangling_ids() -> None:
    tasks = [
        {"id": "TASK_A", "depends_on": ["ODP-PLAN-OSS-LICENSE-GATE-001"]},
        {
            "id": "TASK_B",
            "depends_on": [
                "ODP-PLAN-OSS-LEGAL-POLICY-001",
                "ODP-PLAN-OSS-LICENSE-GATE-001",
            ],
        },
        {"id": "ODP-PLAN-OSS-LEGAL-POLICY-001", "depends_on": []},
    ]

    count, logs = migrate_dependencies(tasks)

    assert count == 2
    assert tasks[0]["depends_on"] == ["ODP-PLAN-OSS-LEGAL-POLICY-001"]
    assert tasks[1]["depends_on"] == ["ODP-PLAN-OSS-LEGAL-POLICY-001"]
    assert len(logs) == 3


def test_run_migration_dry_run_no_write(tmp_path: Path) -> None:
    board_path = write_board(
        tmp_path,
        [
            {"id": "TASK_A", "depends_on": ["ODP-PLAN-OSS-LICENSE-GATE-001"]},
            {"id": "ODP-PLAN-OSS-LEGAL-POLICY-001", "depends_on": []},
        ],
    )
    archive_dir = write_archive(tmp_path, [])
    initial_content = board_path.read_text(encoding="utf-8")

    res = run_migration(board_path, archive_dir, dry_run=True)
    assert res == 0
    assert board_path.read_text(encoding="utf-8") == initial_content


def test_run_migration_status_root_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    board_path = write_board(
        tmp_path,
        [
            {"id": "TASK_A", "depends_on": ["ODP-PLAN-OSS-LICENSE-GATE-001"]},
            {"id": "ODP-PLAN-OSS-LEGAL-POLICY-001", "depends_on": []},
        ],
    )
    archive_dir = write_archive(tmp_path, [])
    initial_content = board_path.read_text(encoding="utf-8")

    res = run_migration(board_path, archive_dir, dry_run=False)
    assert res == 1
    captured = capsys.readouterr()
    assert "status path mismatch" in captured.err
    assert board_path.read_text(encoding="utf-8") == initial_content


def test_run_migration_invalid_replacement(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    custom_map = {"DANGLING_SRC": "DANGLING_TARGET"}
    board_path = write_board(
        tmp_path,
        [
            {"id": "TASK_A", "depends_on": ["DANGLING_SRC"]},
        ],
    )
    archive_dir = write_archive(tmp_path, [])

    with patch("migrate_task_dependency_graph.DEPENDENCY_MIGRATION_MAP", custom_map):
        res = run_migration(board_path, archive_dir, dry_run=True)

    assert res == 1
    captured = capsys.readouterr()
    assert "Post-migration resolvability check: 1 failure(s)" in captured.err
    assert "DANGLING_TARGET" in captured.err


def test_run_migration_sync_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    board_path = write_board(
        tmp_path,
        [
            {"id": "TASK_A", "depends_on": ["ODP-PLAN-OSS-LICENSE-GATE-001"]},
            {"id": "ODP-PLAN-OSS-LEGAL-POLICY-001", "depends_on": []},
        ],
    )
    archive_dir = write_archive(tmp_path, [])
    initial_content = board_path.read_text(encoding="utf-8")

    with patch.object(ai_status, "STATUS_FILE", board_path), patch.object(
        ai_status, "load_state", return_value={"tasks": []}
    ), patch.object(
        ai_status, "sync_all", side_effect=RuntimeError("disk write failed")
    ):
        res = run_migration(board_path, archive_dir, dry_run=False)

    assert res == 1
    captured = capsys.readouterr()
    assert "canonical state sync failed" in captured.err
    assert board_path.read_text(encoding="utf-8") == initial_content


def test_run_migration_fixes_dangling_graph(tmp_path: Path) -> None:
    board_path = write_board(
        tmp_path,
        [
            {"id": "TASK_A", "depends_on": ["ODP-PLAN-OSS-LICENSE-GATE-001"]},
            {"id": "ODP-PLAN-OSS-LEGAL-POLICY-001", "depends_on": []},
        ],
    )
    archive_dir = write_archive(tmp_path, [])

    failures_before = check(load_board(board_path), load_archive(archive_dir))
    assert len(failures_before) == 1
    assert "DANGLING" in failures_before[0]

    def dummy_sync_all(state: dict) -> None:
        board_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    with patch.object(ai_status, "STATUS_FILE", board_path), patch.object(
        ai_status, "load_state", return_value={"tasks": []}
    ), patch.object(ai_status, "sync_all", side_effect=dummy_sync_all):
        res = run_migration(board_path, archive_dir, dry_run=False)

    assert res == 0

    failures_after = check(load_board(board_path), load_archive(archive_dir))
    assert failures_after == []

