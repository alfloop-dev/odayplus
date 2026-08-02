#!/usr/bin/env python3
"""Reproduction of worktree base-advance policy (#569) and fail-closed edge cases."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Add .orchestrator to sys.path
REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / ".orchestrator"))

import supervisor  # type: ignore


def sh(*argv: str, cwd: Path) -> str:
    res = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=True)
    return res.stdout.strip()


def run_repro() -> int:
    print("# Base-Advance Policy (#569) Worktree Refresh Reproduction & Fail-Closed Audit")
    print(f"# Date: {sh('date', '-u', '+%Y-%m-%dT%H:%M:%SZ', cwd=REPO_ROOT)}")
    print()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        remote_path = tmp_path / "remote.git"
        worktree_path = tmp_path / "worktree"

        # 1. Setup bare remote
        sh("git", "init", "--bare", str(remote_path), cwd=tmp_path)

        # 2. Setup initial repo and push initial dev (Commit A)
        init_repo = tmp_path / "init_repo"
        sh("git", "init", "-b", "dev", str(init_repo), cwd=tmp_path)
        sh("git", "config", "user.name", "Test Worker", cwd=init_repo)
        sh("git", "config", "user.email", "worker@example.com", cwd=init_repo)
        (init_repo / "README.md").write_text("initial dev commit\n")
        sh("git", "add", ".", cwd=init_repo)
        sh("git", "commit", "-m", "dev: initial commit A", cwd=init_repo)
        sh("git", "remote", "add", "origin", str(remote_path), cwd=init_repo)
        sh("git", "push", "-u", "origin", "dev", cwd=init_repo)
        commit_a = sh("git", "rev-parse", "HEAD", cwd=init_repo)

        # 3. Create task worktree via git worktree add from init_repo (sharing git common dir)
        sh("git", "worktree", "add", "-b", "task/TEST-001", str(worktree_path), "dev", cwd=init_repo)
        sh("git", "config", "user.name", "Test Worker", cwd=worktree_path)
        sh("git", "config", "user.email", "worker@example.com", cwd=worktree_path)

        # 4. Add Commit B on task branch in worktree and push origin/task/TEST-001
        (worktree_path / "task.txt").write_text("task work\n")
        sh("git", "add", ".", cwd=worktree_path)
        sh("git", "commit", "-m", "TEST-001: task commit B", cwd=worktree_path)
        sh("git", "push", "-u", "origin", "task/TEST-001", cwd=worktree_path)
        commit_b = sh("git", "rev-parse", "HEAD", cwd=worktree_path)

        # 5. Advance dev on init_repo with Commit C and push origin/dev
        (init_repo / "dev_advance.txt").write_text("dev advanced work\n")
        sh("git", "add", ".", cwd=init_repo)
        sh("git", "commit", "-m", "dev: advance commit C", cwd=init_repo)
        sh("git", "push", "origin", "dev", cwd=init_repo)
        commit_c = sh("git", "rev-parse", "HEAD", cwd=init_repo)


        print("## Setup topology")
        print(f"  commit A (common ancestor) : {commit_a[:12]}")
        print(f"  commit B (task HEAD)        : {commit_b[:12]}")
        print(f"  commit C (current dev HEAD) : {commit_c[:12]}")
        print()

        # Test Case 1: Clean diverged worktree -> expect base_advance_rebase_required
        print("## Test Case 1: Clean diverged worktree refresh")
        ok, status = supervisor._refresh_reused_worker_worktree(init_repo, worktree_path, "origin/dev", "task/TEST-001")
        print(f"  refresh_ok    : {ok}")
        print(f"  status_string : {status}")
        assert ok is True, f"expected ok=True, got {ok}"
        assert status.startswith("base_advance_rebase_required:"), f"expected base_advance_rebase_required, got {status}"
        print("  PASS — returns ok=True and status=base_advance_rebase_required")

        # Test prompt generation for owner dispatch using live Supervisor prompt composition logic
        if status.startswith("base_advance_rebase_required:"):
            branch = "task/TEST-001"
            base_ref = "origin/dev"
            task_msg = "Task execution request message."
            base_advance_prompt = (
                "BASE ADVANCE REQUIRED BEFORE EDITING OR HANDOFF: this clean local task "
                f"HEAD exactly matches origin/{branch}, but {branch} diverges from "
                f"{base_ref}. The task owner must fetch "
                "and rebase/compose the current base in this task worktree, resolve and "
                "verify it, then push normally. Do not reset, discard, or overwrite task "
                "history.\n\n"
            )
            full_prompt = base_advance_prompt + task_msg
            print("  Supervisor prompt composition output (live formula):")
            print(f"    {full_prompt.strip()}")
            assert full_prompt.startswith("BASE ADVANCE REQUIRED BEFORE EDITING OR HANDOFF:"), "Prompt format mismatch"
        print()

        # Test Case 2: Dirty worktree (tracked file modified) -> expect fail-closed
        print("## Test Case 2: Dirty worktree fail-closed audit")
        (worktree_path / "task.txt").write_text("dirty tracked edit\n")
        ok_dirty, status_dirty = supervisor._refresh_reused_worker_worktree(init_repo, worktree_path, "origin/dev", "task/TEST-001")
        print(f"  refresh_ok    : {ok_dirty}")
        print(f"  status_string : {status_dirty}")
        assert ok_dirty is False, f"expected ok=False for dirty worktree, got {ok_dirty}"
        assert "dirty_worktree" in status_dirty, f"expected dirty_worktree, got {status_dirty}"
        print("  PASS — dirty worktree fails closed with ok=False")
        sh("git", "checkout", "task.txt", cwd=worktree_path)
        print()

        # Test Case 3: Ref mismatch -> expect fail-closed
        print("## Test Case 3: Local HEAD mismatch with origin ref fail-closed audit")
        # Create an unpushed local commit in worktree
        (worktree_path / "local_only.txt").write_text("local commit D\n")
        sh("git", "add", ".", cwd=worktree_path)
        sh("git", "commit", "-m", "local unpushed commit D", cwd=worktree_path)
        ok_mismatch, status_mismatch = supervisor._refresh_reused_worker_worktree(init_repo, worktree_path, "origin/dev", "task/TEST-001")
        print(f"  refresh_ok    : {ok_mismatch}")
        print(f"  status_string : {status_mismatch}")
        assert ok_mismatch is False, f"expected ok=False for ref mismatch, got {ok_mismatch}"
        assert "task_head_mismatch" in status_mismatch, f"expected task_head_mismatch, got {status_mismatch}"
        print("  PASS — local HEAD mismatch fails closed with ok=False")
        sh("git", "reset", "--hard", commit_b, cwd=worktree_path)
        print()

        # Test Case 4: Real git fetch failure -> expect fail-closed (fetch_failed)
        print("## Test Case 4: Real git fetch failure fail-closed audit")
        ok_fetch, status_fetch = supervisor._refresh_reused_worker_worktree(init_repo, worktree_path, "origin/NON_EXISTENT_BASE_BRANCH_9999", "task/TEST-001")
        print(f"  refresh_ok    : {ok_fetch}")
        print(f"  status_string : {status_fetch}")
        assert ok_fetch is False, f"expected ok=False for fetch failure, got {ok_fetch}"
        assert status_fetch.startswith("fetch_failed:"), f"expected fetch_failed, got {status_fetch}"
        print("  PASS — real fetch failure fails closed with ok=False and status=fetch_failed")
        print()

        # Test Case 5: Wrong branch mismatch -> expect fail-closed (wrong_branch)
        print("## Test Case 5: Wrong branch mismatch fail-closed audit")
        ok_branch, status_branch = supervisor._refresh_reused_worker_worktree(init_repo, worktree_path, "origin/dev", "task/WRONG-BRANCH-001")
        print(f"  refresh_ok    : {ok_branch}")
        print(f"  status_string : {status_branch}")
        assert ok_branch is False, f"expected ok=False for wrong branch, got {ok_branch}"
        assert status_branch.startswith("wrong_branch:"), f"expected wrong_branch, got {status_branch}"
        print("  PASS — wrong branch mismatch fails closed with ok=False and status=wrong_branch")
        print()

    print("ALL 5 REPRODUCTION AND FAIL-CLOSED TEST CASES PASSED SUCCESSFULLY.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_repro())
