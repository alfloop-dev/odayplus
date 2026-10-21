#!/usr/bin/env python3
"""Validate that an entire task delivery has one immutable identity.

Every non-merge commit between the integration base and delivery head must
carry the requested task id and valid owner/reviewer trailers.  Callers may
also bind the delivery to an expected branch.  This prevents a reused branch
or a correct-looking HEAD from hiding older commits for another task.

The same scan is also the record of who wrote the delivery. Each non-merge
commit names its source actor in its ``LLM-Agent`` trailer, and the set of
those actors is what an independent reviewer has to be independent of -- not
whoever later merged a base into the branch or submitted the PR. The scan
reports them exactly as written; resolving a name to a registered account is
the caller's job, so an unknown label stays visible instead of being guessed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    from .check_commit_trailers import parse_trailers, validate_message
except ImportError:  # Direct script execution from task_finalize.sh.
    from check_commit_trailers import parse_trailers, validate_message


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed"
        raise ValueError(detail)
    return result.stdout.strip()


def resolve_base_ref(repo: Path, base: str) -> str:
    """Prefer the remote integration ref while retaining offline compatibility."""
    candidates = [base]
    if not base.startswith(("origin/", "refs/")):
        candidates.insert(0, f"origin/{base}")
    for candidate in candidates:
        try:
            _git(repo, "rev-parse", "--verify", f"{candidate}^{{commit}}")
            return candidate
        except ValueError:
            continue
    raise ValueError(f"integration base {base!r} does not resolve to a commit")


@dataclass(frozen=True)
class DeliveryIdentity:
    """What one immutable ``base..head`` delivery range proves.

    ``source_agents`` is only meaningful when ``errors`` is empty: a range that
    failed validation has not established who wrote it.
    """

    errors: list[str] = field(default_factory=list)
    base_sha: str = ""
    head_sha: str = ""
    #: ``(commit sha, LLM-Agent)`` for every non-merge commit, oldest first.
    commits: tuple[tuple[str, str], ...] = ()

    @property
    def source_agents(self) -> tuple[str, ...]:
        """Every distinct source actor of the range, in first-commit order."""
        return tuple(dict.fromkeys(agent for _sha, agent in self.commits if agent))


def inspect_delivery_identity(
    repo: Path,
    *,
    task_id: str,
    base: str,
    head: str,
    expected_branch: str | None = None,
    actual_branch: str | None = None,
) -> DeliveryIdentity:
    """Validate the delivery range and report the source actor of every commit."""
    errors: list[str] = []
    repo = repo.resolve()
    task_id = task_id.strip()
    if not task_id:
        return DeliveryIdentity(errors=["task id is empty"])
    if expected_branch and actual_branch and expected_branch != actual_branch:
        errors.append(
            f"branch {actual_branch!r} does not match recorded task branch {expected_branch!r}"
        )

    try:
        base_ref = resolve_base_ref(repo, base)
        base_sha = _git(repo, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
        head_sha = _git(repo, "rev-parse", "--verify", f"{head}^{{commit}}")
        commits_text = _git(repo, "rev-list", "--reverse", "--no-merges", f"{base_ref}..{head}")
    except ValueError as exc:
        return DeliveryIdentity(errors=[*errors, str(exc)])

    commits = [line for line in commits_text.splitlines() if line]
    if not commits:
        return DeliveryIdentity(
            errors=[*errors, f"delivery range {base_ref}..{head} contains no non-merge commits"],
            base_sha=base_sha,
            head_sha=head_sha,
        )

    sources: list[tuple[str, str]] = []
    for commit in commits:
        try:
            message = _git(repo, "show", "-s", "--format=%B", commit)
        except ValueError as exc:
            errors.append(f"commit {commit[:12]} cannot be inspected: {exc}")
            continue
        violations = validate_message(
            message,
            task_id=task_id,
            allow_maintenance_skip=False,
            require_subject_length_limit=False,
        )
        errors.extend(f"commit {commit[:12]}: {violation}" for violation in violations)
        sources.append((commit, parse_trailers(message).get("llm-agent", "").strip()))
    return DeliveryIdentity(
        errors=errors,
        base_sha=base_sha,
        head_sha=head_sha,
        commits=tuple(sources),
    )


def validate_delivery_identity(
    repo: Path,
    *,
    task_id: str,
    base: str,
    head: str,
    expected_branch: str | None = None,
    actual_branch: str | None = None,
) -> list[str]:
    """Return delivery identity violations; empty means the range is coherent."""
    return inspect_delivery_identity(
        repo,
        task_id=task_id,
        base=base,
        head=head,
        expected_branch=expected_branch,
        actual_branch=actual_branch,
    ).errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--expected-branch")
    parser.add_argument("--actual-branch")
    args = parser.parse_args(argv)
    errors = validate_delivery_identity(
        Path(args.repo),
        task_id=args.task_id,
        base=args.base,
        head=args.head,
        expected_branch=args.expected_branch,
        actual_branch=args.actual_branch,
    )
    if errors:
        print("check_task_delivery_identity: delivery rejected:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
