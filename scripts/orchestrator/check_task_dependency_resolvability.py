#!/usr/bin/env python3
"""Fail-closed validator for orchestrator task ``depends_on`` resolvability.

Control Pack ``ODP-PLAN-EXECUTION-CONTROL-PACK-001`` section 3.1 requires the
Supervisor to verify, before dispatch, that *every* dependency of a task
resolves through the live board or the official archive. Nothing enforced that
rule, so unresolvable dependencies accumulated silently: as of 2026-08-03 all
nine transitive dependencies of ``ODP-RUNTIME-GCP-001`` resolved in neither
source, which made three deployment tasks permanently undispatchable while
still reporting a plain ``blocked`` status.

A dependency is *resolvable* when its task id appears in exactly one of:

* the live board (``ai-status.json`` ``tasks[]``), or
* the official archive (``ai-task-archive/tasks/<task-id>.json``).

Everything fails closed:

* a dependency id present in neither source is a **dangling** dependency;
* a dependency id present in *both* sources is a **duplicate lifecycle**
  violation of Control Pack section 3.4.1;
* an archive snapshot whose ``terminal_status`` is not ``done`` does not
  satisfy a dependency;
* a task may not depend on itself, and dependency cycles are reported.

Exit codes: ``0`` when every dependency resolves, ``1`` when any rule fails.

Usage::

    python3 scripts/orchestrator/check_task_dependency_resolvability.py \
        --status /path/to/ai-status.json \
        --archive-dir /path/to/ai-task-archive/tasks

Paths may also be supplied through ``ODP_SUPERVISOR_STATUS_FILE`` and
``ODP_SUPERVISOR_ARCHIVE_DIR``. ``--task`` restricts the report to one task and
its transitive closure, which is what a dispatch-time preflight wants.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

TERMINAL_STATUS_SATISFYING_DEPENDENCY = "done"


class DependencyGraphError(Exception):
    """Raised when the dependency graph cannot be loaded at all."""


def load_board(status_path: Path) -> dict[str, dict[str, Any]]:
    """Return live-board tasks keyed by upper-cased task id."""

    if not status_path.exists():
        raise DependencyGraphError(f"status file not found: {status_path}")
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DependencyGraphError(f"status file is not valid JSON: {exc}") from exc

    tasks: dict[str, dict[str, Any]] = {}
    for entry in payload.get("tasks", []) or []:
        if not isinstance(entry, dict):
            continue
        task_id = str(entry.get("id") or "").strip()
        if task_id:
            tasks[task_id.upper()] = entry
    return tasks


def load_archive(archive_dir: Path) -> dict[str, dict[str, Any]]:
    """Return archive snapshots keyed by upper-cased task id."""

    if not archive_dir.exists():
        raise DependencyGraphError(f"archive directory not found: {archive_dir}")

    snapshots: dict[str, dict[str, Any]] = {}
    for path in sorted(archive_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # A malformed snapshot must not silently satisfy a dependency.
            continue
        task_id = str(payload.get("task_id") or path.stem).strip()
        if task_id:
            snapshots[task_id.upper()] = payload
    return snapshots


def dependencies_of(task: dict[str, Any]) -> list[str]:
    raw = task.get("depends_on") or []
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _closure(
    roots: list[str],
    board: dict[str, dict[str, Any]],
) -> set[str]:
    """Return ``roots`` plus every task id reachable through ``depends_on``."""

    seen: set[str] = set()
    stack = [task_id.upper() for task_id in roots]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        task = board.get(current)
        if task is None:
            continue
        stack.extend(dep.upper() for dep in dependencies_of(task))
    return seen


def find_cycles(board: dict[str, dict[str, Any]]) -> list[list[str]]:
    """Return dependency cycles confined to tasks present on the live board."""

    cycles: list[list[str]] = []
    visiting: set[str] = set()
    visited: set[str] = set()
    path: list[str] = []

    def walk(node: str) -> None:
        if node in visited:
            return
        if node in visiting:
            start = path.index(node)
            cycles.append([*path[start:], node])
            return
        task = board.get(node)
        if task is None:
            visited.add(node)
            return
        visiting.add(node)
        path.append(node)
        for dep in dependencies_of(task):
            walk(dep.upper())
        path.pop()
        visiting.discard(node)
        visited.add(node)

    for task_id in sorted(board):
        walk(task_id)
    return cycles


def check(
    board: dict[str, dict[str, Any]],
    archive: dict[str, dict[str, Any]],
    scope: set[str] | None = None,
) -> list[str]:
    """Return a list of failure messages; empty means the graph is sound."""

    failures: list[str] = []
    task_ids = sorted(board) if scope is None else sorted(scope & set(board))

    for task_id in task_ids:
        task = board[task_id]
        for dep in dependencies_of(task):
            key = dep.upper()

            if key == task_id:
                failures.append(f"{task['id']}: depends on itself ({dep})")
                continue

            on_board = key in board
            snapshot = archive.get(key)

            if on_board and snapshot is not None:
                failures.append(
                    f"{task['id']}: dependency {dep} exists on BOTH the live board "
                    "and the official archive (Control Pack 3.4.1 requires exactly one)"
                )
                continue

            if on_board:
                continue

            if snapshot is None:
                failures.append(
                    f"{task['id']}: dependency {dep} is DANGLING - it resolves in "
                    "neither the live board nor the official archive, so this task "
                    "can never be dispatched"
                )
                continue

            terminal_status = str(snapshot.get("terminal_status") or "").strip().lower()
            if terminal_status != TERMINAL_STATUS_SATISFYING_DEPENDENCY:
                failures.append(
                    f"{task['id']}: dependency {dep} is archived with "
                    f"terminal_status={terminal_status or 'missing'!r}, which does "
                    "not satisfy a dependency"
                )

    for cycle in find_cycles(board):
        if scope is not None and not (set(cycle) & scope):
            continue
        failures.append("dependency cycle: " + " -> ".join(cycle))

    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--status",
        default=os.environ.get("ODP_SUPERVISOR_STATUS_FILE"),
        help="path to ai-status.json",
    )
    parser.add_argument(
        "--archive-dir",
        default=os.environ.get("ODP_SUPERVISOR_ARCHIVE_DIR"),
        help="path to ai-task-archive/tasks",
    )
    parser.add_argument(
        "--task",
        action="append",
        default=[],
        help="restrict the report to this task and its transitive dependencies",
    )
    args = parser.parse_args(argv)

    if not args.status or not args.archive_dir:
        parser.error(
            "--status and --archive-dir are required (or set "
            "ODP_SUPERVISOR_STATUS_FILE / ODP_SUPERVISOR_ARCHIVE_DIR)"
        )

    try:
        board = load_board(Path(args.status))
        archive = load_archive(Path(args.archive_dir))
    except DependencyGraphError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    scope = _closure(args.task, board) if args.task else None
    failures = check(board, archive, scope)

    if failures:
        print(f"Task dependency resolvability: {len(failures)} failure(s)\n")
        for failure in failures:
            print(f"  - {failure}")
        print(
            "\nControl Pack 3.1 requires every dependency to resolve through the "
            "live board or the official archive before dispatch."
        )
        return 1

    scanned = len(board) if scope is None else len(scope & set(board))
    print(f"Task dependency resolvability: OK ({scanned} task(s) scanned)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
