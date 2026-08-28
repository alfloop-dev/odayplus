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
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GIT_DIR = REPO_ROOT / "delivery_toolchain" / "git"

from delivery_toolchain.git.check_commit_scope import files_outside_scope, validate_scope
from delivery_toolchain.git.check_commit_trailers import validate_message

TASK = "ODP-TEST-001"

GOOD_MESSAGE = f"""{TASK}: add a thing

Body text.

LLM-Agent: Claude2
Task-ID: {TASK}
Reviewer: Antigravity4
"""


def _ruff_resolvable() -> bool:
    """Mirror task_finalize.sh's own interpreter resolution.

    The guard degrades to a warning when it cannot find ruff, so a test that
    asserts a refusal is only meaningful where the script would actually find
    one. Checking `which uv` instead would assert on a different environment
    than the one under test.
    """
    candidate = REPO_ROOT / ".venv" / "bin" / "python"
    if not candidate.exists():
        return False
    probe = subprocess.run([str(candidate), "-m", "ruff", "--version"], capture_output=True, text=True)
    return probe.returncode == 0


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
    assert files_outside_scope(["delivery_toolchain/git/x.py"], ["delivery_toolchain/git/x.py"]) == []


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


def test_validate_message_allows_long_subject_when_subject_length_limit_disabled():
    message = GOOD_MESSAGE.replace("add a thing", "x" * 80)
    assert validate_message(message, task_id=TASK, require_subject_length_limit=False) == []


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


def test_worker_commit_refuses_overlong_subject(repo: Path, tmp_path: Path):
    git(repo, "switch", "--quiet", "--create", f"task/{TASK}")
    (repo / "owned.txt").write_text("owned\n", encoding="utf-8")
    msg = write_msg(tmp_path, GOOD_MESSAGE.replace("add a thing", "x" * 80))

    result = worker_commit(repo, "--task-id", TASK, "--message-file", str(msg), "--scope", "owned.txt")
    assert result.returncode == 1
    assert "limit is 72" in result.stderr


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


def test_task_start_refuses_to_create_branch_outside_worker_manager(repo: Path):
    result = task_start(repo, TASK)
    assert result.returncode == 1
    assert "outside its Worker Manager lease" in result.stderr
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "dev"


def test_task_start_is_idempotent(repo: Path):
    git(repo, "switch", "--quiet", "--create", f"task/{TASK}")
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
    result = task_start(repo, TASK)
    assert result.returncode == 1
    assert "outside its Worker Manager lease" in result.stderr


def test_task_start_refuses_to_attach_existing_branch_outside_worker_manager(repo: Path):
    git(repo, "switch", "--quiet", "--create", f"task/{TASK}")
    git(repo, "switch", "--quiet", "dev")
    result = task_start(repo, TASK)
    assert result.returncode == 1
    assert "outside its Worker Manager lease" in result.stderr
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "dev"


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


def test_task_finalize_dry_run_plans_publish_without_auto_merge(repo: Path, tmp_path: Path):
    commit_on_task_branch(repo, tmp_path)
    result = task_finalize(repo, TASK, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "dry-run: git push" in result.stdout
    assert "pr create --base dev" in result.stdout
    assert "pr merge" not in result.stdout
    assert "--auto" not in result.stdout
    # Nothing reached origin.
    assert "task" not in run(["git", "ls-remote", "--heads", "origin"], repo).stdout


def test_task_finalize_rejects_retired_no_auto_merge_flag(repo: Path, tmp_path: Path):
    commit_on_task_branch(repo, tmp_path)
    result = task_finalize(repo, TASK, "--dry-run", "--no-auto-merge")
    assert result.returncode == 2
    assert "unknown option --no-auto-merge" in result.stderr


def test_task_finalize_refuses_wrong_branch(repo: Path):
    result = task_finalize(repo, TASK, "--dry-run")
    assert result.returncode == 1
    assert "task_start.sh" in result.stderr


def test_task_finalize_refuses_uncommitted_changes(repo: Path, tmp_path: Path):
    commit_on_task_branch(repo, tmp_path)
    (repo / "owned.txt").write_text("edited after the commit\n", encoding="utf-8")
    result = task_finalize(repo, TASK, "--dry-run")
    assert result.returncode == 1
    assert "worktree handoff is not clean" in result.stderr
    assert "owned.txt" in result.stderr


@pytest.mark.parametrize(
    ("message", "expected_error"),
    [
        ("fix: missing task id", "subject must start with the task id 'ODP-TEST-001'"),
        (f"{TASK}: missing metadata", "missing required trailer 'LLM-Agent:'"),
        (
            f"{TASK}: wrong task metadata\n\nLLM-Agent: Claude\nTask-ID: OTHER\nReviewer: Antigravity\n",
            "Task-ID trailer 'OTHER' does not match 'ODP-TEST-001'",
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


def test_task_finalize_allows_historical_commit_with_long_subject(repo: Path):
    git(repo, "switch", "--quiet", "--create", f"task/{TASK}")
    (repo / "owned.txt").write_text("owned\n", encoding="utf-8")
    git(repo, "add", "owned.txt")
    long_subject_msg = (
        f"{TASK}: " + "very long summary that exceeds seventy two characters limit intentionally"
        "\n\nBody text.\n\nLLM-Agent: Claude2\nTask-ID: " + TASK + "\nReviewer: Antigravity4\n"
    )
    git(repo, "commit", "--no-verify", "-m", long_subject_msg)

    result = task_finalize(repo, TASK, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "dry-run: git push" in result.stdout


def test_task_finalize_refuses_a_branch_whose_own_python_fails_lint(repo: Path, tmp_path: Path):
    """The lint preflight refuses here rather than letting CI find it 20 minutes later.

    `product` reruns ruff over the same files, so a branch that fails this
    cannot merge. Publishing the PR first only moves the refusal somewhere
    slower. Skipped when no ruff is resolvable -- the guard is deliberately a
    warning in that case, and asserting a refusal would test the environment.
    """
    if not _ruff_resolvable():
        pytest.skip("task_finalize cannot resolve ruff here; the guard warns instead of refusing")

    git(repo, "switch", "--quiet", "--create", f"task/{TASK}")
    # F401: imported but unused -- the exact rule that reddened #968.
    (repo / "owned.py").write_text("import os\n", encoding="utf-8")
    msg = write_msg(tmp_path, GOOD_MESSAGE)
    committed = worker_commit(repo, "--task-id", TASK, "--message-file", str(msg), "--scope", "owned.py")
    assert committed.returncode == 0, committed.stderr

    result = task_finalize(repo, TASK, "--dry-run")

    assert result.returncode == 1
    assert "ruff reports errors" in result.stderr
    assert "F401" in result.stderr
    assert "dry-run: git push" not in result.stdout


def test_task_finalize_lint_preflight_ignores_files_the_branch_did_not_touch(repo: Path, tmp_path: Path):
    """Only the branch's own Python is linted, so a task is never blocked by pre-existing findings."""
    if not _ruff_resolvable():
        pytest.skip("task_finalize cannot resolve ruff here; the guard warns instead of refusing")

    # A lint-broken file that is already on the base branch, not introduced here.
    (repo / "preexisting.py").write_text("import os\n", encoding="utf-8")
    git(repo, "add", "preexisting.py")
    git(repo, "commit", "--quiet", "--no-verify", "-m", "seed a pre-existing lint finding")
    git(repo, "push", "--quiet", "origin", "dev")

    commit_on_task_branch(repo, tmp_path)
    result = task_finalize(repo, TASK, "--dry-run")

    assert result.returncode == 0, result.stderr
    assert "dry-run: git push" in result.stdout


def test_task_finalize_refuses_every_untracked_owner_artifact(repo: Path, tmp_path: Path):
    """The owner must resolve all unknown dirt before reviewer handoff."""
    commit_on_task_branch(repo, tmp_path)
    (repo / "fix_indent.py").write_text("# one-shot patcher\n", encoding="utf-8")
    (repo / "patch_test.py").write_text("# another\n", encoding="utf-8")
    (repo / "worker-output.orig").write_text("old\n", encoding="utf-8")
    (repo / "worker-output.patch").write_text("patch\n", encoding="utf-8")
    (repo / "worker-output.rej").write_text("reject\n", encoding="utf-8")
    (repo / ".python-version").write_text("3.12\n", encoding="utf-8")

    result = task_finalize(repo, TASK, "--dry-run")

    assert result.returncode == 1
    assert "worktree handoff is not clean" in result.stderr
    assert "fix_indent.py" in result.stderr
    assert "patch_test.py" in result.stderr
    assert "worker-output.orig" in result.stderr
    assert "worker-output.patch" in result.stderr
    assert "+1 more" in result.stderr
    assert "dry-run: git push" not in result.stdout


def test_task_finalize_refuses_an_untracked_nested_script(repo: Path, tmp_path: Path):
    """Path names do not prove ownership: nested output is still unknown dirt."""
    commit_on_task_branch(repo, tmp_path)
    (repo / "scripts").mkdir(exist_ok=True)
    (repo / "scripts" / "fix_encoding.py").write_text("# a real tool\n", encoding="utf-8")

    result = task_finalize(repo, TASK, "--dry-run")

    assert result.returncode == 1
    assert "scripts/fix_encoding.py" in result.stderr
    assert "dry-run: git push" not in result.stdout


def test_task_finalize_allows_a_committed_root_level_fix_script(repo: Path, tmp_path: Path):
    """Only *untracked* files are scratch. A committed one is a deliverable."""
    git(repo, "switch", "--quiet", "--create", f"task/{TASK}")
    (repo / "owned.txt").write_text("owned\n", encoding="utf-8")
    (repo / "fix_encoding.py").write_text("# deliberately shipped\n", encoding="utf-8")
    msg = write_msg(tmp_path, GOOD_MESSAGE)
    committed = worker_commit(
        repo, "--task-id", TASK, "--message-file", str(msg), "--scope", "owned.txt", "fix_encoding.py"
    )
    assert committed.returncode == 0, committed.stderr

    result = task_finalize(repo, TASK, "--dry-run")

    assert result.returncode == 0, result.stderr
    assert "dry-run: git push" in result.stdout


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
    ["delivery_toolchain/git/task_start.sh", "delivery_toolchain/git/task_finalize.sh", "delivery_toolchain/git/install_hooks.sh", ".githooks/commit-msg"],
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
    tool_git = repo / "delivery_toolchain" / "git"
    tool_git.mkdir(parents=True)
    (tool_git / "check_commit_trailers.py").write_text(
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


def test_commit_msg_hook_blocks_overlong_subject(repo: Path):
    hooks = repo / ".githooks"
    hooks.mkdir()
    (hooks / "commit-msg").write_text((REPO_ROOT / ".githooks" / "commit-msg").read_text(), encoding="utf-8")
    (hooks / "commit-msg").chmod(0o755)
    tool_git = repo / "delivery_toolchain" / "git"
    tool_git.mkdir(parents=True)
    (tool_git / "check_commit_trailers.py").write_text(
        (GIT_DIR / "check_commit_trailers.py").read_text(), encoding="utf-8"
    )
    git(repo, "config", "core.hooksPath", ".githooks")
    git(repo, "switch", "--quiet", "--create", f"task/{TASK}")

    (repo / "owned.txt").write_text("owned\n", encoding="utf-8")
    git(repo, "add", "owned.txt")
    overlong = GOOD_MESSAGE.replace("add a thing", "x" * 80)
    bad = run(["git", "commit", "-m", overlong], repo)
    assert bad.returncode != 0
    assert "limit is 72" in bad.stderr
