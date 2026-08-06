"""Tests for the orchestrator finalize lane remediation diagnostic tool."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from diagnose_finalize_lane_remediation import (
    CAT_CI_FAILED,
    CAT_CI_UNRESOLVED,
    CAT_MISSING_PR,
    CAT_OWNER_UNAVAILABLE,
    CAT_STALE_BASE,
    FinalizeDiagnosisError,
    diagnose,
    main,
)


def write_status_file(
    tmp_path: Path,
    tasks: list[dict],
    agents: list[dict] | None = None,
) -> Path:
    path = tmp_path / "ai-status.json"
    payload = {
        "tasks": tasks,
        "agents": agents or [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_ci_unresolved_category(tmp_path: Path) -> None:
    status_path = write_status_file(
        tmp_path,
        [
            {
                "id": "TASK-1",
                "status": "review_approved",
                "owner": "Worker1",
                "pr": 101,
                "next": "CI status for task TASK-1 is unresolved (unknown); finalize dispatch suppressed.",
            }
        ],
        [{"name": "Worker1", "status": "finalize"}],
    )

    report = diagnose(status_path)
    assert report["stranded_count"] == 1
    task_diag = report["tasks"][0]
    assert task_diag["task_id"] == "TASK-1"
    assert task_diag["category"] == CAT_CI_UNRESOLVED


def test_ci_failed_category(tmp_path: Path) -> None:
    status_path = write_status_file(
        tmp_path,
        [
            {
                "id": "TASK-2",
                "status": "review_approved",
                "owner": "Worker1",
                "pr": 102,
                "next": "CI checks for task TASK-2 failed; resolve failing checks before finalization.",
            }
        ],
        [{"name": "Worker1", "status": "finalize"}],
    )

    report = diagnose(status_path)
    assert report["stranded_count"] == 1
    task_diag = report["tasks"][0]
    assert task_diag["task_id"] == "TASK-2"
    assert task_diag["category"] == CAT_CI_FAILED


def test_stale_base_category(tmp_path: Path) -> None:
    status_path = write_status_file(
        tmp_path,
        [
            {
                "id": "TASK-3",
                "status": "review_approved",
                "owner": "Worker1",
                "pr": 103,
                "next": "PR branch is behind base dev; rebase required.",
            }
        ],
        [{"name": "Worker1", "status": "finalize"}],
    )

    report = diagnose(status_path)
    assert report["stranded_count"] == 1
    task_diag = report["tasks"][0]
    assert task_diag["task_id"] == "TASK-3"
    assert task_diag["category"] == CAT_STALE_BASE


def test_missing_pr_category(tmp_path: Path) -> None:
    status_path = write_status_file(
        tmp_path,
        [
            {
                "id": "TASK-4",
                "status": "review_approved",
                "owner": "Worker1",
                "pr": None,
                "next": "Approved task ready for finalize.",
            }
        ],
        [{"name": "Worker1", "status": "finalize"}],
    )

    report = diagnose(status_path)
    assert report["stranded_count"] == 1
    task_diag = report["tasks"][0]
    assert task_diag["task_id"] == "TASK-4"
    assert task_diag["category"] == CAT_MISSING_PR


def test_owner_unavailable_category(tmp_path: Path) -> None:
    status_path = write_status_file(
        tmp_path,
        [
            {
                "id": "TASK-5",
                "status": "review_approved",
                "owner": "Worker2",
                "pr": 105,
                "next": "Task awaiting finalization.",
            }
        ],
        [{"name": "Worker2", "status": "blocked"}],
    )

    report = diagnose(status_path)
    assert report["stranded_count"] == 1
    task_diag = report["tasks"][0]
    assert task_diag["task_id"] == "TASK-5"
    assert task_diag["category"] == CAT_OWNER_UNAVAILABLE


def test_task_id_filter(tmp_path: Path) -> None:
    status_path = write_status_file(
        tmp_path,
        [
            {"id": "TASK-A", "status": "review_approved", "owner": "W1", "pr": 1, "next": "CI failed"},
            {"id": "TASK-B", "status": "review_approved", "owner": "W1", "pr": 2, "next": "CI unresolved"},
        ],
        [{"name": "W1", "status": "finalize"}],
    )

    report = diagnose(status_path, task_ids=["TASK-A"])
    assert report["stranded_count"] == 1
    assert report["tasks"][0]["task_id"] == "TASK-A"


def test_category_filter(tmp_path: Path) -> None:
    status_path = write_status_file(
        tmp_path,
        [
            {"id": "TASK-A", "status": "review_approved", "owner": "W1", "pr": 1, "next": "CI failed"},
            {"id": "TASK-B", "status": "review_approved", "owner": "W1", "pr": None, "next": "Approved"},
        ],
        [{"name": "W1", "status": "finalize"}],
    )

    report = diagnose(status_path, category_filter=CAT_MISSING_PR)
    assert report["stranded_count"] == 1
    assert report["tasks"][0]["task_id"] == "TASK-B"


def test_main_json_and_fail_on_stranded(tmp_path: Path) -> None:
    status_path = write_status_file(
        tmp_path,
        [{"id": "TASK-1", "status": "review_approved", "owner": "W1", "pr": 1, "next": "CI failed"}],
        [{"name": "W1", "status": "finalize"}],
    )

    exit_code = main(["--status", str(status_path), "--json", "--fail-on-stranded"])
    assert exit_code == 1


def test_missing_file_raises_error(tmp_path: Path) -> None:
    with pytest.raises(FinalizeDiagnosisError):
        diagnose(tmp_path / "nonexistent.json")
