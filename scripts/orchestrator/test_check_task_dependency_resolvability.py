"""Tests for the orchestrator task dependency resolvability check."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from check_task_dependency_resolvability import (
    DependencyGraphError,
    check,
    load_archive,
    load_board,
    main,
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


def test_resolvable_through_board_and_archive(tmp_path: Path) -> None:
    board_path = write_board(
        tmp_path,
        [
            {"id": "A", "depends_on": ["B", "C"]},
            {"id": "B"},
        ],
    )
    archive_dir = write_archive(tmp_path, [{"task_id": "C", "terminal_status": "done"}])

    assert check(load_board(board_path), load_archive(archive_dir)) == []


def test_dangling_dependency_fails_closed(tmp_path: Path) -> None:
    board_path = write_board(tmp_path, [{"id": "A", "depends_on": ["GHOST"]}])
    archive_dir = write_archive(tmp_path, [])

    failures = check(load_board(board_path), load_archive(archive_dir))

    assert len(failures) == 1
    assert "DANGLING" in failures[0]
    assert "GHOST" in failures[0]


def test_dependency_on_both_board_and_archive_fails(tmp_path: Path) -> None:
    board_path = write_board(
        tmp_path,
        [{"id": "A", "depends_on": ["B"]}, {"id": "B"}],
    )
    archive_dir = write_archive(tmp_path, [{"task_id": "B", "terminal_status": "done"}])

    failures = check(load_board(board_path), load_archive(archive_dir))

    assert len(failures) == 1
    assert "BOTH" in failures[0]


def test_archive_snapshot_must_be_done(tmp_path: Path) -> None:
    board_path = write_board(tmp_path, [{"id": "A", "depends_on": ["B"]}])
    archive_dir = write_archive(
        tmp_path, [{"task_id": "B", "terminal_status": "in_progress"}]
    )

    failures = check(load_board(board_path), load_archive(archive_dir))

    assert len(failures) == 1
    assert "does not satisfy" in failures[0]


def test_malformed_snapshot_does_not_satisfy_dependency(tmp_path: Path) -> None:
    board_path = write_board(tmp_path, [{"id": "A", "depends_on": ["B"]}])
    archive_dir = tmp_path / "tasks"
    archive_dir.mkdir(parents=True)
    (archive_dir / "B.json").write_text("{not json", encoding="utf-8")

    failures = check(load_board(board_path), load_archive(archive_dir))

    assert len(failures) == 1
    assert "DANGLING" in failures[0]


def test_self_dependency_fails(tmp_path: Path) -> None:
    board_path = write_board(tmp_path, [{"id": "A", "depends_on": ["A"]}])
    archive_dir = write_archive(tmp_path, [])

    failures = check(load_board(board_path), load_archive(archive_dir))

    assert any("depends on itself" in failure for failure in failures)


def test_cycle_is_reported(tmp_path: Path) -> None:
    board_path = write_board(
        tmp_path,
        [
            {"id": "A", "depends_on": ["B"]},
            {"id": "B", "depends_on": ["A"]},
        ],
    )
    archive_dir = write_archive(tmp_path, [])

    failures = check(load_board(board_path), load_archive(archive_dir))

    assert any(failure.startswith("dependency cycle:") for failure in failures)


def test_task_id_matching_is_case_sensitive(tmp_path: Path) -> None:
    # The Supervisor resolves dependencies through `normalize_task_id`, which
    # only strips whitespace. `ODP-B` and `ODP-b` are therefore two different
    # tasks at dispatch time, and certifying this graph as sound would leave
    # `odp-a` waiting forever on an edge nothing can satisfy.
    board_path = write_board(
        tmp_path,
        [{"id": "odp-a", "depends_on": ["ODP-B"]}, {"id": "ODP-b"}],
    )
    archive_dir = write_archive(tmp_path, [])

    failures = check(load_board(board_path), load_archive(archive_dir))

    assert len(failures) == 1, failures
    assert "differs only by case from ODP-b" in failures[0]
    assert "case-sensitive" in failures[0]


def test_exact_task_id_still_resolves(tmp_path: Path) -> None:
    board_path = write_board(
        tmp_path,
        [{"id": "odp-a", "depends_on": ["ODP-b"]}, {"id": "ODP-b"}],
    )
    archive_dir = write_archive(tmp_path, [])

    assert check(load_board(board_path), load_archive(archive_dir)) == []


def test_scope_selection_tolerates_operator_case(tmp_path: Path) -> None:
    # `--task` is a report filter, not a dispatch grant: a mistyped case must
    # narrow the report to the task the operator meant rather than silently
    # scan nothing and print OK.
    board_path = write_board(tmp_path, [{"id": "ODP-A", "depends_on": ["GHOST"]}])
    archive_dir = write_archive(tmp_path, [])

    exit_code = main(
        ["--status", str(board_path), "--archive-dir", str(archive_dir), "--task", "odp-a"]
    )

    assert exit_code == 1, "a mistyped --task case must not hide a dangling edge"


def test_scope_restricts_report_to_task_closure(tmp_path: Path) -> None:
    board_path = write_board(
        tmp_path,
        [
            {"id": "A", "depends_on": ["B"]},
            {"id": "B"},
            {"id": "UNRELATED", "depends_on": ["GHOST"]},
        ],
    )
    archive_dir = write_archive(tmp_path, [])

    exit_code = main(
        [
            "--status",
            str(board_path),
            "--archive-dir",
            str(archive_dir),
            "--task",
            "A",
        ]
    )

    assert exit_code == 0, "UNRELATED's dangling dependency must be out of scope"


def test_main_returns_one_on_dangling_dependency(tmp_path: Path) -> None:
    board_path = write_board(tmp_path, [{"id": "A", "depends_on": ["GHOST"]}])
    archive_dir = write_archive(tmp_path, [])

    assert main(["--status", str(board_path), "--archive-dir", str(archive_dir)]) == 1


def test_missing_status_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(DependencyGraphError):
        load_board(tmp_path / "absent.json")


def test_missing_archive_dir_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(DependencyGraphError):
        load_archive(tmp_path / "absent")
