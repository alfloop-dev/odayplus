#!/usr/bin/env python3
"""Fail when the running supervisor is too far behind the branch it should track.

Merging to `dev` does not activate anything. The supervisor executes from its own
checkout, and on 2026-08-04 that checkout sat 126 commits behind `origin/dev`,
having drifted there over several days without anything noticing. Every
orchestrator fix merged in that window -- PRs #602, #610, #611, #613, #614 --
was inert in production the whole time.

Three defects were live purely because of that drift:

* the runtime was on a **detached HEAD**, so `current_branch()` returned the
  literal string ``"HEAD"``, which ReviewBus recorded as a branch name and then
  skipped PR creation for, stranding finished tasks with no pull request;
* `github_bus.py` lacked ``task_pr_base_branch()``, so task PRs were based on the
  repository default branch and bypassed dev promotion entirely;
* agent-name matching was case sensitive, so a config saying ``codex2`` never
  matched a board saying ``Codex2``.

None of these were visible as failures. The fleet simply behaved as though the
fixes did not exist, because for that process they did not.

This check makes the drift loud. It also reports a detached HEAD independently of
distance, because that condition alone reproduces the first defect no matter how
current the commit is.

Exit codes: ``0`` when the runtime is within tolerance and on a named branch,
``1`` otherwise.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

DEFAULT_MAX_BEHIND = 25
DETACHED_HEAD_SENTINEL = "HEAD"


def _git(repo: Path, *args: str) -> str | None:
    """Return stdout, or None when git cannot answer.

    A missing or unreadable repo makes subprocess raise rather than return a
    non-zero code, so both have to be treated as "cannot verify" -- which this
    check reports as failure, never as freshness.
    """

    try:
        proc = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def current_branch(repo: Path) -> str | None:
    return _git(repo, "rev-parse", "--abbrev-ref", "HEAD")


def commits_behind(repo: Path, tracking_ref: str) -> int | None:
    out = _git(repo, "rev-list", "--count", f"HEAD..{tracking_ref}")
    if out is None:
        return None
    try:
        return int(out)
    except ValueError:
        return None


def working_tree_dirty(repo: Path) -> bool | None:
    """Return whether tracked runtime files differ from its checked-out commit.

    A runtime with a current-looking HEAD but locally overlaid old files is just
    as unsafe as a runtime that is behind.  It was the direct cause of the
    account-pool scheduler rollback: the service ran dirty copies of old files
    while git reported a much newer commit.
    """
    out = _git(repo, "status", "--porcelain", "--untracked-files=no")
    return None if out is None else bool(out)


def evaluate(
    repo: Path, tracking_ref: str, max_behind: int
) -> tuple[bool, list[str], dict[str, object]]:
    """Return (ok, problems, facts)."""

    problems: list[str] = []
    head = _git(repo, "rev-parse", "HEAD")
    branch = current_branch(repo)
    behind = commits_behind(repo, tracking_ref)
    dirty = working_tree_dirty(repo)

    facts: dict[str, object] = {
        "repo": str(repo),
        "head": head,
        "branch": branch,
        "tracking_ref": tracking_ref,
        "behind": behind,
        "max_behind": max_behind,
        "dirty": dirty,
    }

    if head is None or branch is None:
        problems.append(f"cannot read git state at {repo}")
        return False, problems, facts

    if branch == DETACHED_HEAD_SENTINEL:
        problems.append(
            "runtime is on a detached HEAD; current_branch() reports the literal "
            'string "HEAD", which ReviewBus records as a branch name and then '
            "skips PR creation for. Check out a named branch."
        )

    if dirty is None:
        problems.append("cannot determine whether the runtime working tree is clean")
    elif dirty:
        problems.append(
            "runtime has tracked local modifications; a dirty overlay can silently run "
            "older code than HEAD. Create a fresh runtime worktree instead of updating in place."
        )

    if behind is None:
        problems.append(
            f"cannot compare against {tracking_ref}; fetch it first so drift is measurable"
        )
    elif behind > max_behind:
        problems.append(
            f"runtime is {behind} commits behind {tracking_ref} (tolerance {max_behind}); "
            "merged fixes are not active in production"
        )

    return not problems, problems, facts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", required=True, help="path to the running supervisor checkout")
    parser.add_argument("--tracking-ref", default="origin/dev")
    parser.add_argument("--max-behind", type=int, default=DEFAULT_MAX_BEHIND)
    args = parser.parse_args(argv)

    ok, problems, facts = evaluate(
        Path(args.runtime).resolve(), args.tracking_ref, args.max_behind
    )

    behind = facts["behind"]
    print(
        f"runtime={facts['repo']}\n"
        f"  head={facts['head']} branch={facts['branch']}\n"
        f"  behind {facts['tracking_ref']}: {behind if behind is not None else 'unknown'} "
        f"(tolerance {facts['max_behind']})\n"
        f"  tracked working tree dirty: {facts['dirty'] if facts['dirty'] is not None else 'unknown'}"
    )

    if ok:
        print("\nRuntime freshness: OK")
        return 0

    print("\nRuntime freshness: FAIL")
    for problem in problems:
        print(f"  - {problem}")
    print(
        "\nMerging to the tracking branch does not activate anything; the runtime "
        "checkout must be advanced and the supervisor restarted. See "
        "docs/runbooks/supervisor-runtime-rollout.md"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
