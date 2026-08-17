"""Tests for the retroactive task archive snapshot backfill tool."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from backfill_task_archive_snapshots import (
    build_snapshot,
    find_merge_evidence,
    main,
    plan,
)


def init_repo(tmp_path: Path, subjects: list[str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(  # noqa: E731
        list(a), cwd=repo, check=True, capture_output=True
    )
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    for i, subject in enumerate(subjects):
        (repo / f"f{i}.txt").write_text(str(i), encoding="utf-8")
        run("git", "add", "-A")
        run("git", "commit", "-q", "-m", subject)
    return repo


def test_find_merge_evidence_prefers_merge_commit(tmp_path: Path) -> None:
    repo = init_repo(
        tmp_path,
        [
            "TASK-1: work in progress",
            "Merge pull request #42 from org/task/TASK-1",
        ],
    )

    evidence = find_merge_evidence(repo, "TASK-1", "HEAD")

    assert evidence is not None
    assert evidence["merge_pr"] == "#42"
    assert "Merge pull request" in evidence["subject"]


def test_find_merge_evidence_accepts_squash_merge(tmp_path: Path) -> None:
    repo = init_repo(tmp_path, ["TASK-2: compose everything (#77)"])

    evidence = find_merge_evidence(repo, "TASK-2", "HEAD")

    assert evidence is not None
    assert evidence["merge_pr"] == "#77"


def test_find_merge_evidence_returns_none_when_absent(tmp_path: Path) -> None:
    repo = init_repo(tmp_path, ["unrelated commit"])

    assert find_merge_evidence(repo, "TASK-MISSING", "HEAD") is None


def test_snapshot_shape_satisfies_dependency_rule(tmp_path: Path) -> None:
    evidence = {
        "merge_commit": "a" * 40,
        "merged_at": "2026-07-28T00:00:00+00:00",
        "merge_pr": "#1",
        "subject": "Merge pull request #1 from org/task/TASK-1",
    }

    snapshot = build_snapshot("TASK-1", evidence, [])

    # task_archive.task_satisfies_dependency reads snapshot["task"].
    assert snapshot["task"]["status"] == "done"
    assert snapshot["terminal_outcome"] != "superseded"
    assert snapshot["terminal_status"] == "done"
    assert snapshot["task_id"] == "TASK-1"


def test_snapshot_is_labelled_retroactive(tmp_path: Path) -> None:
    evidence = {
        "merge_commit": "b" * 40,
        "merged_at": "2026-07-28T00:00:00+00:00",
        "merge_pr": "#2",
        "subject": "Merge pull request #2 from org/task/TASK-2",
    }

    snapshot = build_snapshot("TASK-2", evidence, [])

    assert snapshot["backfill"]["retroactive"] is True
    assert snapshot["backfill"]["merge_commit"] == "b" * 40
    assert "not from a live lifecycle transition" in snapshot["backfill"]["note"]


def test_plan_skips_unverified_tasks(tmp_path: Path) -> None:
    repo = init_repo(tmp_path, ["Merge pull request #1 from org/task/TASK-1"])
    archive = tmp_path / "tasks"
    archive.mkdir()

    to_write, existing, unverified = plan(
        repo, archive, ("TASK-1", "TASK-GHOST"), "HEAD"
    )

    assert [t for t, _ in to_write] == ["TASK-1"]
    assert existing == []
    assert unverified == ["TASK-GHOST"]


def test_plan_never_overwrites_existing_snapshot(tmp_path: Path) -> None:
    repo = init_repo(tmp_path, ["Merge pull request #1 from org/task/TASK-1"])
    archive = tmp_path / "tasks"
    archive.mkdir()
    (archive / "TASK-1.json").write_text('{"task_id": "TASK-1"}', encoding="utf-8")

    to_write, existing, _ = plan(repo, archive, ("TASK-1",), "HEAD")

    assert to_write == []
    assert existing == ["TASK-1"]


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    repo = init_repo(tmp_path, ["Merge pull request #1 from org/task/TASK-1"])
    archive = tmp_path / "tasks"
    archive.mkdir()

    exit_code = main(
        [
            "--archive-dir", str(archive),
            "--repo", str(repo),
            "--ref", "HEAD",
            "--task", "TASK-1",
        ]
    )

    assert exit_code == 0
    assert list(archive.glob("*.json")) == []


def test_apply_writes_resolvable_snapshot(tmp_path: Path) -> None:
    repo = init_repo(tmp_path, ["Merge pull request #9 from org/task/TASK-9"])
    archive = tmp_path / "tasks"
    archive.mkdir()

    exit_code = main(
        [
            "--archive-dir", str(archive),
            "--repo", str(repo),
            "--ref", "HEAD",
            "--task", "TASK-9",
            "--apply",
        ]
    )

    assert exit_code == 0
    payload = json.loads((archive / "TASK-9.json").read_text(encoding="utf-8"))
    assert payload["task"]["status"] == "done"
    assert payload["backfill"]["merge_pr"] == "#9"


def test_apply_does_not_touch_index_json(tmp_path: Path) -> None:
    repo = init_repo(tmp_path, ["Merge pull request #3 from org/task/TASK-3"])
    archive = tmp_path / "tasks"
    archive.mkdir()
    index = archive.parent / "index.json"
    index.write_text('{"counts": {"total": 0}}', encoding="utf-8")
    before = index.read_text(encoding="utf-8")

    main(
        [
            "--archive-dir", str(archive),
            "--repo", str(repo),
            "--ref", "HEAD",
            "--task", "TASK-3",
            "--apply",
        ]
    )

    assert index.read_text(encoding="utf-8") == before


def test_missing_archive_dir_fails_closed(tmp_path: Path) -> None:
    repo = init_repo(tmp_path, ["init"])

    exit_code = main(
        ["--archive-dir", str(tmp_path / "absent"), "--repo", str(repo)]
    )

    assert exit_code == 1
