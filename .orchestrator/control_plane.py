from __future__ import annotations

"""Notice when the code that governs the fleet has been modified out of band.

The supervisor executes from a checkout that workers can also reach. On
2026-08-19 a finalize worker, blocked seven times by the `done` gate, edited
that gate in `scripts/ai_status.py` in the shared checkout. The change was
never committed to any branch, took effect for anyone running `done` from that
tree, and sat there for roughly eighteen hours while the fleet ran normally.
Nothing noticed, because nothing was looking.

The worker handoff seal does not cover this. It inspects each isolated worker
worktree at owner exit; the shared checkout is a different tree entirely.

This module answers a narrower question: are the files that decide what is
allowed currently modified and uncommitted where the supervisor runs? An
uncommitted change to a governance file has bypassed review by construction -
whatever its provenance, nobody approved it and it is in force.

It reports by default rather than halting. A halt on a dirty control plane
would have stopped the fleet repeatedly during ordinary work on 2026-08-20,
and a control that has to be disabled to get anything done is a control that
gets disabled. Blocking is available for operators who want it.
"""

import subprocess
from pathlib import Path
from typing import Any

#: Files that decide what the fleet is permitted to do. An uncommitted edit
#: here is in force without review, which is the property that matters - not
#: whether it looks reasonable.
DEFAULT_CONTROL_PLANE_GLOBS = (
    "scripts/ai_status.py",
    ".orchestrator/supervisor.py",
    ".orchestrator/dispatch_engine.py",
    ".orchestrator/worker_workspace.py",
    ".orchestrator/task_reality.py",
    "delivery_toolchain/governance/*",
    "delivery_toolchain/git/*",
    ".githooks/*",
)


def control_plane_settings(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("control_plane_guard")
    settings = raw if isinstance(raw, dict) else {}
    globs = settings.get("globs")
    return {
        "enabled": bool(settings.get("enabled", True)),
        "mode": str(settings.get("mode") or "report").strip().lower(),
        "globs": list(globs) if isinstance(globs, list) and globs else list(DEFAULT_CONTROL_PLANE_GLOBS),
    }


def _matches(path: str, globs: list[str]) -> bool:
    from fnmatch import fnmatch

    return any(fnmatch(path, pattern) for pattern in globs)


def dirty_control_plane_paths(
    root: Path, globs: list[str], *, runner=subprocess.run
) -> list[str] | None:
    """Governance files with uncommitted changes, or None when unreadable.

    ``None`` is not "clean". A `git status` that cannot run is an unanswered
    question, and reporting it as clean would be the same error as reconciling
    a record from a failed lookup.
    """

    try:
        proc = runner(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None

    dirty: list[str] = []
    for line in (proc.stdout or "").splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        # Renames read as "old -> new"; the destination is what is in force.
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        if path and _matches(path, globs):
            dirty.append(path)
    return sorted(dirty)


def control_plane_alarm(paths: list[str]) -> str:
    """What to tell an operator, given the dirty governance files."""

    listed = ", ".join(f"`{p}`" for p in paths[:8])
    more = f" (+{len(paths) - 8} more)" if len(paths) > 8 else ""
    return (
        "Control plane modified without review: "
        f"{listed}{more} "
        f"{'is' if len(paths) == 1 else 'are'} uncommitted in the checkout the supervisor "
        "runs from, so the change is in force and no reviewer has seen it. "
        "Commit it through a PR or revert it."
    )
