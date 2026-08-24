#!/usr/bin/env python3
"""Worker-safe, scope-checked task commit.

Every worker skill routes task commits through this script instead of raw
``git add`` / ``git commit``. See
`.orchestrator/skills/task-closeout-finalization.md` § Shared-Index Footgun:
workers can share one worktree and therefore one ``.git/index``, so a plain
``git commit`` silently absorbs whatever a previous (or concurrent) worker
left staged. That is the 2026-05-16 sweep-in incident.

Guarantees, in order:

  1. **Private index.** Staging always happens in a ``GIT_INDEX_FILE`` of our
     own, seeded from ``HEAD`` via ``git read-tree``. A stale or concurrent
     staging in the shared index therefore cannot reach this commit -- and,
     because the index is seeded from HEAD rather than copied from the live
     index, "clear stale staging first" is structural rather than a step that
     can be skipped.
  2. **Explicit scope.** Only ``--scope`` paths are staged, and whole-worktree
     spellings (``.``, ``-A``, ``*``) are rejected outright.
  3. **Leak check.** The resulting staged set is re-read from git and verified
     against the scope by ``check_commit_scope.py`` before committing.
  4. **Message check.** ``check_commit_trailers.py`` validates the subject and
     the LLM-Agent / Task-ID / Reviewer trailers before the commit is made,
     so a bad message fails fast instead of at push time.
  5. **Branch guard.** Refuses to commit onto a protected branch.
  6. **No empty commits** -- they jam the rebase loop.

Usage:

    python3 delivery_toolchain/git/worker_commit.py \\
      --task-id "$TASK" \\
      --message-file /tmp/${TASK}-msg.txt \\
      --scope <path1> <path2> ... \\
      --index-file /tmp/git-index-task-$TASK

Exit codes: 0 = committed, 1 = refused (scope/message/branch/empty), 2 =
usage or git error.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_commit_scope import files_outside_scope, normalize, validate_scope  # noqa: E402
from check_commit_trailers import validate_message  # noqa: E402

PROTECTED_BRANCHES = {"dev", "main", "master"}


class CommitRefused(Exception):
    """A guard rejected the commit. Message is already user-facing."""


def _git(args: list[str], *, cwd: Path, env: dict[str, str] | None = None, check: bool = True):
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=check,
    )


def repo_root(start: Path | None = None) -> Path:
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(start or Path.cwd()),
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(proc.stdout.strip())


def current_branch(root: Path) -> str:
    return _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root).stdout.strip()


def has_head(root: Path) -> bool:
    return _git(["rev-parse", "--verify", "--quiet", "HEAD"], cwd=root, check=False).returncode == 0


def default_index_path(task_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in task_id)
    return str(Path(tempfile.gettempdir()) / f"git-index-task-{safe}")


def seed_private_index(root: Path, index_file: str) -> dict[str, str]:
    """Point GIT_INDEX_FILE at a fresh index matching HEAD."""
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = index_file
    Path(index_file).parent.mkdir(parents=True, exist_ok=True)
    if has_head(root):
        _git(["read-tree", "HEAD"], cwd=root, env=env)
    else:
        _git(["read-tree", "--empty"], cwd=root, env=env)
    return env


def commit(
    *,
    task_id: str,
    message_file: str,
    scope: list[str],
    index_file: str | None = None,
    root: Path | None = None,
    allow_protected_branch: bool = False,
    dry_run: bool = False,
) -> str:
    """Stage ``scope`` and commit. Returns the new commit sha ('' on dry run)."""
    root = root or repo_root()

    problems = validate_scope(scope)
    if problems:
        raise CommitRefused("refusing to stage:\n  - " + "\n  - ".join(problems))

    branch = current_branch(root)
    if branch in PROTECTED_BRANCHES and not allow_protected_branch:
        raise CommitRefused(
            f"refusing to commit onto protected branch {branch!r}. "
            f"Run ./delivery_toolchain/git/task_start.sh \"{task_id}\" first."
        )

    message_path = Path(message_file)
    try:
        message = message_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CommitRefused(f"cannot read --message-file {message_path}: {exc}") from exc

    missing = [normalize(p) for p in scope if not (root / normalize(p)).exists()]
    tracked_missing = []
    for path in missing:
        # A missing path is fine when it is a tracked file this commit deletes.
        known = _git(["ls-files", "--error-unmatch", "--", path], cwd=root, check=False)
        if known.returncode != 0:
            tracked_missing.append(path)
    if tracked_missing:
        raise CommitRefused(
            "these --scope paths do not exist and are not tracked:\n  - "
            + "\n  - ".join(tracked_missing)
        )

    index_file = index_file or default_index_path(task_id)
    env = seed_private_index(root, index_file)

    for path in scope:
        _git(["add", "-f", "--", normalize(path)], cwd=root, env=env)

    staged_proc = _git(["diff", "--cached", "--name-only", "-z"], cwd=root, env=env)
    staged = [p for p in staged_proc.stdout.split("\0") if p]

    if not staged:
        raise CommitRefused(
            "nothing to commit within the declared scope -- refusing to create an "
            "empty commit (empty commits jam the rebase loop)."
        )

    leaks = files_outside_scope(staged, scope)
    if leaks:
        raise CommitRefused(
            f"{len(leaks)} staged file(s) leaked outside --scope:\n  - " + "\n  - ".join(leaks)
        )

    errors = validate_message(message, task_id=task_id, files=staged)
    if errors:
        raise CommitRefused("commit message rejected:\n  - " + "\n  - ".join(errors))

    if dry_run:
        print(f"worker_commit: dry-run, would commit {len(staged)} file(s) on {branch}:")
        for path in staged:
            print(f"  - {path}")
        return ""

    _git(["commit", "-F", str(message_path)], cwd=root, env=env)
    sha = _git(["rev-parse", "HEAD"], cwd=root).stdout.strip()

    # The commit was made against our private index, so the worktree's default
    # index still holds the pre-commit entries for these paths and `git status`
    # would report them as staged changes. Reset only the paths this task owns;
    # a bare `git reset` here would clobber a concurrent worker's staging, which
    # is the very failure this script exists to prevent.
    _git(["reset", "--quiet", "HEAD", "--", *[normalize(p) for p in scope]], cwd=root, check=False)

    print(f"worker_commit: {sha[:12]} on {branch} ({len(staged)} file(s))")
    for path in staged:
        print(f"  - {path}")
    return sha


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--message-file", required=True)
    parser.add_argument("--scope", nargs="+", required=True, help="Repo-relative paths this task owns.")
    parser.add_argument("--index-file", help="Private GIT_INDEX_FILE path (defaults to a per-task temp file).")
    parser.add_argument("--allow-protected-branch", action="store_true", help="Escape hatch; not for workers.")
    parser.add_argument("--dry-run", action="store_true", help="Run every guard but do not commit.")
    args = parser.parse_args(argv)

    try:
        commit(
            task_id=args.task_id,
            message_file=args.message_file,
            scope=list(args.scope),
            index_file=args.index_file,
            allow_protected_branch=args.allow_protected_branch,
            dry_run=args.dry_run,
        )
    except CommitRefused as exc:
        print(f"worker_commit: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(
            f"worker_commit: git command failed: {' '.join(exc.cmd)}\n{(exc.stderr or '').strip()}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
