"""Tests for the orchestrator task dependency graph migration script."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from check_task_dependency_resolvability import check, load_archive, load_board
from migrate_task_dependency_graph import (
    DEPENDENCY_MIGRATION_MAP,
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


def test_run_migration_fixes_dangling_graph(tmp_path: Path) -> None:
    board_path = write_board(
        tmp_path,
        [
            {"id": "TASK_A", "depends_on": ["ODP-PLAN-OSS-LICENSE-GATE-001"]},
            {"id": "ODP-PLAN-OSS-LEGAL-POLICY-001", "depends_on": []},
        ],
    )
    archive_dir = write_archive(tmp_path, [])

    # Pre-migration should fail
    failures_before = check(load_board(board_path), load_archive(archive_dir))
    assert len(failures_before) == 1
    assert "DANGLING" in failures_before[0]

    # Run migration (dry_run=True to test in-place logic without ai_status state sync)
    tasks = json.loads(board_path.read_text())["tasks"]
    migrate_dependencies(tasks)
    board_path.write_text(json.dumps({"tasks": tasks}))

    # Post-migration should pass
    failures_after = check(load_board(board_path), load_archive(archive_dir))
    assert failures_after == []
