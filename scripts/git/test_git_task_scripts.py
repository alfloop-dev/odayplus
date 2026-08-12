#!/usr/bin/env python3
"""Tests for the per-task git helper scripts.

These run against throwaway repositories under tmp_path, never against the
live worktree, and never touch the network: task_finalize.sh is exercised in
--dry-run, and its non-dry paths are covered by asserting on the refusals that
happen before any push.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

GIT_DIR = Path(__file__).resolve().parent
REPO_ROOT = GIT_DIR.parents[1]

sys.path.insert(0, str(GIT_DIR))

from check_commit_scope import files_outside_scope, validate_scope  # noqa: E402
from check_commit_trailers import validate_message  # noqa: E402

TASK = "ODP-TEST-001"

GOOD_MESSAGE = f"""{TASK}: add a thing

Body text.

LLM-Agent: Claude2
Task-ID: {TASK}
Reviewer: Antigravity4
"""


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged.update(env or {})
    return subprocess.run(cmd, cwd=str(cwd), env=merged, capture_output=True, text=True)


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = run(["git", *args], cwd)
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"
    return result


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo with a `dev` branch, one commit, and an `origin` it can push to."""
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", "--initial-branch=dev", str(origin)], check=True, capture_output=True)
    subprocess.run(["git", "init", "--initial-branch=dev", str(work)], check=True, capture_output=True)
    git(work, "config", "user.email", "worker@example.test")
    git(work, "config", "user.name", "Worker")
    git(work, "config", "commit.gpgsign", "false")
    (work / "seed.txt").write_text("seed\n", encoding="utf-8")
    git(work, "add", "seed.txt")
    git(work, "commit", "-m", "seed")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "--quiet", "--set-upstream", "origin", "dev")
    return work


def write_msg(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "msg.txt"
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# check_commit_scope
# --------------------------------------------------------------------------


def test_scope_covers_exact_paths_and_directories():
    assert files_outside_scope(["docs/a.md", "docs/sub/b.md"], ["docs"]) == []
    assert files_outside_scope(["scripts/git/x.py"], ["scripts/git/x.py"]) == []


def test_scope_rejects_sibling_prefix_collision():
    # "docs" must not swallow "docs-site": prefix matching is per path segment.
    assert files_outside_scope(["docs-site/a.md"], ["docs"]) == ["docs-site/a.md"]


def test_scope_reports_every_leak():
    leaks = files_outside_scope(["docs/a.md", "apps/api/x.py", "other.txt"], ["docs"])
    assert leaks == ["apps/api/x.py", "other.txt"]


@pytest.mark.parametrize("entry", [".", "*", "-A", "", "/etc/passwd"])
def test_validate_scope_rejects_whole_worktree_spellings(entry):
    assert validate_scope([entry])


def test_validate_scope_rejects_empty_scope():
    assert validate_scope([])


# --------------------------------------------------------------------------
# check_commit_trailers
# --------------------------------------------------------------------------


def test_valid_message_passes():
    assert validate_message(GOOD_MESSAGE, task_id=TASK) == []


def test_missing_trailers_are_reported():
    errors = validate_message(f"{TASK}: no trailers here\n", task_id=TASK)
    assert any("LLM-Agent" in e for e in errors)
    assert any("Reviewer" in e for e in errors)


def test_reviewer_equal_to_owner_is_rejected():
    message = GOOD_MESSAGE.replace("Reviewer: Antigravity4", "Reviewer: Claude2")
    errors = validate_message(message, task_id=TASK)
    assert any("must differ" in e for e in errors)


def test_overlong_subject_is_rejected():
    message = GOOD_MESSAGE.replace("add a thing", "x" * 80)
    assert any("limit is 72" in e for e in validate_message(message, task_id=TASK))


def test_subject_must_start_with_task_id():
    message = GOOD_MESSAGE.replace(f"{TASK}: add a thing", "add a thing")
    assert any("must start with the task id" in e for e in validate_message(message, task_id=TASK))


def test_mismatched_task_id_trailer_is_rejected():
    assert any("does not match" in e for e in validate_message(GOOD_MESSAGE, task_id="ODP-OTHER-002"))


@pytest.mark.parametrize(
    "subject",
    ["Merge pull request #1 from x", "Revert \"a change\"", "promote: dev to main", "OPS-DOC-1: tidy"],
)
def test_maintenance_subjects_skip_the_check(subject):
    assert validate_message(f"{subject}\n\nno trailers at all\n") == []


def test_cross_dir_trailer_required_past_threshold():
    files = ["a/x", "b/x", "c/x", "d/x"]
    assert any("Cross-Dir" in e for e in validate_message(GOOD_MESSAGE, task_id=TASK, files=files))
    ok = GOOD_MESSAGE.rstrip("\n") + "\nCross-Dir: yes\n"
    assert validate_message(ok, task_id=TASK, files=files) == []


def test_comment_lines_are_ignored():
    message = "# git comment\n" + GOOD_MESSAGE
    assert validate_message(message, task_id=TASK) == []


# --------------------------------------------------------------------------
# worker_commit.py
# --------------------------------------------------------------------------


def worker_commit(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run(["python3", str(GIT_DIR / "worker_commit.py"), *args], repo)


def test_worker_commit_commits_only_declared_scope(repo: Path, tmp_path: Path):
    git(repo, "switch", "--quiet", "--create", f"task/{TASK}")
    (repo / "owned.txt").write_text("owned\n", encoding="utf-8")
    (repo / "other-lane.txt").write_text("not mine\n", encoding="utf-8")
    msg = write_msg(tmp_path, GOOD_MESSAGE)

    result = worker_commit(repo, "--task-id", TASK, "--message-file", str(msg), "--scope", "owned.txt")
    assert result.returncode == 0, result.stderr

    committed = git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert committed == ["owned.txt"]
    assert "other-lane.txt" in git(repo, "status", "--short").stdout


def test_worker_commit_ignores_stale_staging_from_another_lane(repo: Path, tmp_path: Path):
    """The 2026-05-16 sweep-in: a leftover staged file must not ride along."""
    git(repo, "switch", "--quiet", "--create", f"task/{TASK}")
    (repo / "owned.txt").write_text("owned\n", encoding="utf-8")
    (repo / "stale.txt").write_text("left by another worker\n", encoding="utf-8")
    git(repo, "add", "stale.txt")  # simulates the interrupted worker

    msg = write_msg(tmp_path, GOOD_MESSAGE)
    result = worker_commit(repo, "--task-id", TASK, "--message-file", str(msg), "--scope", "owned.txt")
    assert result.returncode == 0, result.stderr

    committed = git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert committed == ["owned.txt"]
    assert "stale.txt" not in git(repo, "ls-tree", "-r", "--name-only", "HEAD").stdout


def test_worker_commit_refuses_whole_worktree_scope(repo: Path, tmp_path: Path):
    git(repo, "switch", "--quiet", "--create", f"task/{TASK}")
    (repo / "owned.txt").write_text("owned\n", encoding="utf-8")
    msg = write_msg(tmp_path, GOOD_MESSAGE)

    result = worker_commit(repo, "--task-id", TASK, "--message-file", str(msg), "--scope", ".")
    assert result.returncode == 1
    assert "whole-worktree stage" in result.stderr


def test_worker_commit_refuses_protected_branch(repo: Path, tmp_path: Path):
    (repo / "owned.txt").write_text("owned\n", encoding="utf-8")
    msg = write_msg(tmp_path, GOOD_MESSAGE)

    result = worker_commit(repo, "--task-id", TASK, "--message-file", str(msg), "--scope", "owned.txt")
    assert result.returncode == 1
    assert "protected branch" in result.stderr


def test_worker_commit_refuses_empty_commit(repo: Path, tmp_path: Path):
    git(repo, "switch", "--quiet", "--create", f"task/{TASK}")
    msg = write_msg(tmp_path, GOOD_MESSAGE)

    result = worker_commit(repo, "--task-id", TASK, "--message-file", str(msg), "--scope", "seed.txt")
    assert result.returncode == 1
    assert "empty commit" in result.stderr


def test_worker_commit_refuses_bad_message(repo: Path, tmp_path: Path):
    git(repo, "switch", "--quiet", "--create", f"task/{TASK}")
    (repo / "owned.txt").write_text("owned\n", encoding="utf-8")
    msg = write_msg(tmp_path, f"{TASK}: add a thing\n\nno trailers\n")

    result = worker_commit(repo, "--task-id", TASK, "--message-file", str(msg), "--scope", "owned.txt")
    assert result.returncode == 1
    assert "commit message rejected" in result.stderr


def test_worker_commit_uses_the_requested_index_file(repo: Path, tmp_path: Path):
    git(repo, "switch", "--quiet", "--create", f"task/{TASK}")
    (repo / "owned.txt").write_text("owned\n", encoding="utf-8")
    msg = write_msg(tmp_path, GOOD_MESSAGE)
    index_file = tmp_path / "private-index"

    result = worker_commit(
        repo, "--task-id", TASK, "--message-file", str(msg),
        "--scope", "owned.txt", "--index-file", str(index_file),
    )
    assert result.returncode == 0, result.stderr
    assert index_file.exists()


def test_worker_commit_dry_run_makes_no_commit(repo: Path, tmp_path: Path):
    git(repo, "switch", "--quiet", "--create", f"task/{TASK}")
    (repo / "owned.txt").write_text("owned\n", encoding="utf-8")
    msg = write_msg(tmp_path, GOOD_MESSAGE)
    before = git(repo, "rev-parse", "HEAD").stdout.strip()

    result = worker_commit(
        repo, "--task-id", TASK, "--message-file", str(msg), "--scope", "owned.txt", "--dry-run"
    )
    assert result.returncode == 0, result.stderr
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == before


# --------------------------------------------------------------------------
# task_start.sh
# --------------------------------------------------------------------------


def task_start(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run(["bash", str(GIT_DIR / "task_start.sh"), *args], repo)


def test_task_start_creates_branch_from_base(repo: Path):
    result = task_start(repo, TASK)
    assert result.returncode == 0, result.stderr
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == f"task/{TASK}"


def test_task_start_is_idempotent(repo: Path):
    assert task_start(repo, TASK).returncode == 0
    again = task_start(repo, TASK)
    assert again.returncode == 0
    assert "already on" in again.stdout


def test_task_start_refuses_dirty_tracked_tree(repo: Path):
    (repo / "seed.txt").write_text("dirtied by a previous task\n", encoding="utf-8")
    result = task_start(repo, TASK)
    assert result.returncode == 1
    assert "record a blocker" in result.stderr


def test_task_start_ignores_untracked_state_mirrors(repo: Path):
    (repo / "ai-status.json").write_text("{}\n", encoding="utf-8")
    assert task_start(repo, TASK).returncode == 0


def test_task_start_resumes_existing_branch(repo: Path):
    git(repo, "switch", "--quiet", "--create", f"task/{TASK}")
    git(repo, "switch", "--quiet", "dev")
    result = task_start(repo, TASK)
    assert result.returncode == 0, result.stderr
    assert "resumed existing" in result.stdout


def test_task_start_requires_a_task_id(repo: Path):
    assert task_start(repo).returncode == 2


# --------------------------------------------------------------------------
# task_finalize.sh
# --------------------------------------------------------------------------


def task_finalize(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run(["bash", str(GIT_DIR / "task_finalize.sh"), *args], repo)


def commit_on_task_branch(repo: Path, tmp_path: Path) -> None:
    git(repo, "switch", "--quiet", "--create", f"task/{TASK}")
    (repo / "owned.txt").write_text("owned\n", encoding="utf-8")
    msg = write_msg(tmp_path, GOOD_MESSAGE)
    result = worker_commit(repo, "--task-id", TASK, "--message-file", str(msg), "--scope", "owned.txt")
    assert result.returncode == 0, result.stderr


def test_task_finalize_dry_run_plans_push_pr_and_auto_merge(repo: Path, tmp_path: Path):
    commit_on_task_branch(repo, tmp_path)
    result = task_finalize(repo, TASK, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "dry-run: git push" in result.stdout
    assert "pr create --base dev" in result.stdout
    assert "--auto" in result.stdout
    # Nothing reached origin.
    assert "task" not in run(["git", "ls-remote", "--heads", "origin"], repo).stdout


def test_task_finalize_no_auto_merge_flag(repo: Path, tmp_path: Path):
    commit_on_task_branch(repo, tmp_path)
    result = task_finalize(repo, TASK, "--dry-run", "--no-auto-merge")
    assert result.returncode == 0, result.stderr
    assert "--auto" not in result.stdout


def test_task_finalize_refuses_wrong_branch(repo: Path):
    result = task_finalize(repo, TASK, "--dry-run")
    assert result.returncode == 1
    assert "task_start.sh" in result.stderr


def test_task_finalize_refuses_uncommitted_changes(repo: Path, tmp_path: Path):
    commit_on_task_branch(repo, tmp_path)
    (repo / "owned.txt").write_text("edited after the commit\n", encoding="utf-8")
    result = task_finalize(repo, TASK, "--dry-run")
    assert result.returncode == 1
    assert "uncommitted tracked changes" in result.stderr


@pytest.mark.parametrize(
    ("message", "expected_error"),
    [
        ("fix: missing task id", "subject must include task id"),
        (f"{TASK}: missing metadata", "missing required metadata: LLM-Agent"),
        (
            f"{TASK}: wrong task metadata\n\nLLM-Agent: Claude\nTask-ID: OTHER\nReviewer: Antigravity\n",
            "expected 'ODP-TEST-001'",
        ),
    ],
)
def test_task_finalize_rejects_head_that_done_cannot_close(
    repo: Path,
    message: str,
    expected_error: str,
):
    git(repo, "switch", "--quiet", "--create", f"task/{TASK}")
    (repo / "owned.txt").write_text("owned\n", encoding="utf-8")
    git(repo, "add", "owned.txt")
    git(repo, "commit", "-m", message)

    result = task_finalize(repo, TASK, "--dry-run")

    assert result.returncode == 1
    assert expected_error in result.stderr
    assert "dry-run: git push" not in result.stdout


def test_task_finalize_reports_already_merged_branch(repo: Path):
    git(repo, "switch", "--quiet", "--create", f"task/{TASK}")
    result = task_finalize(repo, TASK, "--dry-run")
    assert result.returncode == 0
    assert "already an ancestor" in result.stdout
    assert "ai-status.sh done" in result.stdout


def test_task_finalize_requires_a_task_id(repo: Path):
    assert task_finalize(repo).returncode == 2


# --------------------------------------------------------------------------
# hook + syntax
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "script",
    ["scripts/git/task_start.sh", "scripts/git/task_finalize.sh", "scripts/git/install_hooks.sh", ".githooks/commit-msg"],
)
def test_shell_scripts_parse_and_are_executable(script):
    path = REPO_ROOT / script
    assert path.exists(), f"{script} is missing"
    assert os.access(path, os.X_OK), f"{script} is not executable"
    assert subprocess.run(["bash", "-n", str(path)], capture_output=True).returncode == 0


def test_commit_msg_hook_blocks_a_bad_message(repo: Path):
    hooks = repo / ".githooks"
    hooks.mkdir()
    (hooks / "commit-msg").write_text((REPO_ROOT / ".githooks" / "commit-msg").read_text(), encoding="utf-8")
    (hooks / "commit-msg").chmod(0o755)
    scripts_git = repo / "scripts" / "git"
    scripts_git.mkdir(parents=True)
    (scripts_git / "check_commit_trailers.py").write_text(
        (GIT_DIR / "check_commit_trailers.py").read_text(), encoding="utf-8"
    )
    git(repo, "config", "core.hooksPath", ".githooks")
    git(repo, "switch", "--quiet", "--create", f"task/{TASK}")

    (repo / "owned.txt").write_text("owned\n", encoding="utf-8")
    git(repo, "add", "owned.txt")
    bad = run(["git", "commit", "-m", "no trailers here"], repo)
    assert bad.returncode != 0
    assert "check_commit_trailers" in bad.stderr

    good = run(["git", "commit", "-m", GOOD_MESSAGE], repo)
    assert good.returncode == 0, good.stderr
