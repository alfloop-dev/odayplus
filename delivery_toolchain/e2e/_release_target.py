"""Shared release-target resolution for E2E tooling.

Release checkers should bind to the release manifest rather than embedding a
historical pull-request number in executable code.  The queue payload remains
the machine-readable authority and can be replaced for the next release.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def release_pr_number(queue_path: Path) -> int:
    payload = json.loads(queue_path.read_text(encoding="utf-8"))
    target = payload.get("release_target") or {}
    try:
        number = int(target["pr"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{queue_path}: release_target.pr must be an integer") from exc
    if number <= 0:
        raise ValueError(f"{queue_path}: release_target.pr must be positive")
    return number


def release_pr_label(queue_path: Path) -> str:
    return f"PR #{release_pr_number(queue_path)}"


def release_pr_view_command(
    queue_path: Path,
    *fields: str,
    jq: str | None = None,
) -> str:
    """Render the canonical GitHub PR lookup command for a release packet."""
    if not fields:
        fields = ("headRefOid",)
    command = f"gh pr view {release_pr_number(queue_path)} --json {','.join(fields)}"
    return f"{command} --jq {jq}" if jq else command


def release_pr_head_command(queue_path: Path) -> str:
    return release_pr_view_command(queue_path, "headRefOid", jq=".headRefOid")


def current_release_head(*, root: Path, queue_path: Path) -> str:
    """Read the current release PR head from the queue-selected GitHub PR."""
    number = release_pr_number(queue_path)
    raw = subprocess.check_output(
        ["gh", "pr", "view", str(number), "--json", "headRefOid", "--jq", ".headRefOid"],
        cwd=root,
        text=True,
    )
    return raw.strip()


def release_pr_view_args(queue_path: Path, *fields: str) -> list[str]:
    """Build a ``gh pr view`` argument list from the release manifest."""
    number = release_pr_number(queue_path)
    return ["gh", "pr", "view", str(number), "--json", ",".join(fields)]


def release_pr_comment_args(queue_path: Path) -> list[str]:
    """Build the positional arguments for ``gh pr comment``."""
    return ["gh", "pr", "comment", str(release_pr_number(queue_path))]


def release_label(queue_path: Path) -> str:
    return f"PR #{release_pr_number(queue_path)}"
