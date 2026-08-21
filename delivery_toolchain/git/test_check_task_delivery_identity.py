from __future__ import annotations

import subprocess
from pathlib import Path

from delivery_toolchain.git.check_task_delivery_identity import validate_delivery_identity


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def commit(repo: Path, subject: str, task_id: str) -> str:
    marker = repo / f"change-{len(list(repo.glob('change-*'))):02d}-{task_id}"
    marker.write_text(subject, encoding="utf-8")
    git(repo, "add", marker.name)
    message = (
        f"{subject}\n\n"
        "Delivery change.\n\n"
        "LLM-Agent: Codex\n"
        f"Task-ID: {task_id}\n"
        "Reviewer: Codex2\n"
    )
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "dev")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    (repo / "base").write_text("base", encoding="utf-8")
    git(repo, "add", "base")
    git(repo, "commit", "-m", "bootstrap")
    git(repo, "switch", "-c", "task/TASK-ONE")
    return repo


def test_entire_matching_delivery_range_passes(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    commit(repo, "TASK-ONE: add first guard", "TASK-ONE")
    head = commit(repo, "TASK-ONE: add second guard", "TASK-ONE")

    assert validate_delivery_identity(
        repo,
        task_id="TASK-ONE",
        base="dev",
        head=head,
        expected_branch="task/TASK-ONE",
        actual_branch="task/TASK-ONE",
    ) == []


def test_correct_head_cannot_hide_older_commit_for_another_task(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    wrong = commit(repo, "TASK-OLD: unrelated work", "TASK-OLD")
    head = commit(repo, "TASK-ONE: add delivery guard", "TASK-ONE")

    errors = validate_delivery_identity(repo, task_id="TASK-ONE", base="dev", head=head)

    assert any(wrong[:12] in error and "TASK-OLD" in error for error in errors)


def test_maintenance_skip_does_not_bypass_task_delivery_identity(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    marker = repo / "change-maintenance"
    marker.write_text("maintenance", encoding="utf-8")
    git(repo, "add", marker.name)
    git(repo, "commit", "-m", "publish: unrelated snapshot")
    head = commit(repo, "TASK-ONE: add delivery guard", "TASK-ONE")

    errors = validate_delivery_identity(repo, task_id="TASK-ONE", base="dev", head=head)

    assert any("publish: unrelated snapshot" in error for error in errors)


def test_recorded_branch_mismatch_fails(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    head = commit(repo, "TASK-ONE: add delivery guard", "TASK-ONE")

    errors = validate_delivery_identity(
        repo,
        task_id="TASK-ONE",
        base="dev",
        head=head,
        expected_branch="task/TASK-ONE",
        actual_branch="fix/reused-branch",
    )

    assert any("does not match recorded task branch" in error for error in errors)
