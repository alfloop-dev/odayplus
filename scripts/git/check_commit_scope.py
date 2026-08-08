#!/usr/bin/env python3
"""Reject a staged set that leaks outside the task's declared scope.

`.orchestrator/skills/task-closeout-finalization.md` states that raw
`git add .` / `git add -A` is rejected because "check_commit_scope.py will
reject any commit whose staged files leak outside the declared task scope".
This module is that check.

It is used two ways:

  * imported by ``scripts/git/worker_commit.py`` (the enforcing path), and
  * standalone, to audit an already-staged index:

        python3 scripts/git/check_commit_scope.py \\
          --task-id ODP-X-001 --scope docs/ scripts/git/

Exit codes: 0 = every staged path is in scope, 1 = leak detected, 2 = usage
error.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import PurePosixPath

# Staging these would sweep in whatever another lane left dirty, which is the
# whole failure mode --scope exists to prevent.
FORBIDDEN_SCOPE_ENTRIES = {".", "./", "*", "-A", "-a", "--all", ":/", ""}


def normalize(path: str) -> str:
    """Normalize a repo-relative path for prefix comparison."""
    cleaned = str(path).strip().replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.rstrip("/")


def validate_scope(scope: list[str]) -> list[str]:
    """Return human-readable problems with the declared --scope itself."""
    problems: list[str] = []
    if not scope:
        problems.append("--scope is empty; declare the exact paths this task owns")
        return problems
    for entry in scope:
        raw = str(entry).strip()
        if raw in FORBIDDEN_SCOPE_ENTRIES:
            problems.append(
                f"scope entry {raw!r} is a whole-worktree stage; declare explicit task paths instead"
            )
            continue
        if raw.startswith("-"):
            problems.append(f"scope entry {raw!r} looks like a git flag, not a path")
            continue
        if PurePosixPath(normalize(raw)).is_absolute():
            problems.append(f"scope entry {raw!r} must be repo-relative")
    return problems


def is_in_scope(staged_path: str, scope: list[str]) -> bool:
    """True when ``staged_path`` is a scope entry or lives under one."""
    candidate = normalize(staged_path)
    for entry in scope:
        allowed = normalize(entry)
        if not allowed:
            continue
        if candidate == allowed or candidate.startswith(allowed + "/"):
            return True
    return False


def files_outside_scope(staged: list[str], scope: list[str]) -> list[str]:
    """Return the staged paths that the declared scope does not cover."""
    return sorted({normalize(p) for p in staged if p.strip() and not is_in_scope(p, scope)})


def staged_files(cwd: str | None = None, index_file: str | None = None) -> list[str]:
    """Read the staged path list from git, honouring a private index file."""
    env = None
    if index_file:
        import os

        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = index_file
    proc = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return [p for p in proc.stdout.split("\0") if p]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--scope", nargs="+", required=True, help="Repo-relative paths this task owns.")
    parser.add_argument("--index-file", help="Private GIT_INDEX_FILE to inspect instead of the default index.")
    parser.add_argument(
        "--staged",
        nargs="*",
        help="Staged paths to check. Defaults to reading them from git.",
    )
    args = parser.parse_args(argv)

    problems = validate_scope(list(args.scope))
    if problems:
        for problem in problems:
            print(f"check_commit_scope: {problem}", file=sys.stderr)
        return 2

    staged = args.staged if args.staged is not None else staged_files(index_file=args.index_file)
    leaks = files_outside_scope(list(staged), list(args.scope))
    if leaks:
        print(
            f"check_commit_scope: {len(leaks)} staged file(s) outside the declared scope "
            f"for {args.task_id}:",
            file=sys.stderr,
        )
        for path in leaks:
            print(f"  - {path}", file=sys.stderr)
        print(
            "Declared scope: " + ", ".join(normalize(s) for s in args.scope),
            file=sys.stderr,
        )
        print(
            "These belong to another lane. Do not fold them in; record a blocker or "
            "widen --scope only if they really are this task's work.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
