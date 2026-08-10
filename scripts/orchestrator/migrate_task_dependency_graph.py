#!/usr/bin/env python3
"""Migrate dangling task dependency graph entries in ai-status.json.

Control Pack ``ODP-PLAN-EXECUTION-CONTROL-PACK-001`` and
``check_task_dependency_resolvability.py`` require every task dependency to
resolve to either an active task on the live board or a completed task in the
official archive.

This script updates dangling task dependency IDs in ``ai-status.json`` with
their official replacement active task or archive provenance.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from check_task_dependency_resolvability import check, load_archive

# Explicit migration map from legacy / dangling dependency IDs to canonical task IDs.
# Durable Replacement Provenance:
# - ODP-PLAN-OSS-LICENSE-GATE-001 -> ODP-PLAN-OSS-LEGAL-POLICY-001:
#   Legacy OSS license gate task was merged/restructured into canonical legal policy
#   task ODP-PLAN-OSS-LEGAL-POLICY-001.
DEPENDENCY_MIGRATION_MAP: dict[str, str] = {
    "ODP-PLAN-OSS-LICENSE-GATE-001": "ODP-PLAN-OSS-LEGAL-POLICY-001",
}


def migrate_dependencies(
    tasks: list[dict[str, Any]],
    migration_map: dict[str, str] | None = None,
) -> tuple[int, list[str]]:
    """Migrate task dependencies in place.

    Returns (migrated_task_count, migration_log_messages).
    """

    if migration_map is None:
        migration_map = DEPENDENCY_MIGRATION_MAP

    migrated_count = 0
    logs: list[str] = []

    # Case-insensitive lookup map for migration targets
    mapping_upper = {k.upper(): v for k, v in migration_map.items()}

    for task in tasks:
        raw_deps = task.get("depends_on")
        if not isinstance(raw_deps, list) or not raw_deps:
            continue

        new_deps: list[str] = []
        seen: set[str] = set()
        changed = False

        for dep in raw_deps:
            dep_str = str(dep).strip()
            dep_upper = dep_str.upper()

            target = mapping_upper.get(dep_upper, dep_str)
            if target != dep_str:
                changed = True
                logs.append(
                    f"Task {task.get('id')}: mapped dependency {dep_str} -> {target}"
                )

            target_upper = target.upper()
            if target_upper not in seen:
                seen.add(target_upper)
                new_deps.append(target)
            else:
                changed = True
                logs.append(
                    f"Task {task.get('id')}: removed duplicate dependency {target}"
                )

        if changed:
            task["depends_on"] = new_deps
            migrated_count += 1

    return migrated_count, logs


def run_migration(
    status_path: Path,
    archive_dir: Path,
    dry_run: bool = False,
) -> int:
    """Execute dependency graph migration on status_path."""

    if not status_path.exists():
        print(f"ERROR: status file not found: {status_path}", file=sys.stderr)
        return 1

    if not archive_dir.exists():
        print(f"ERROR: archive directory not found: {archive_dir}", file=sys.stderr)
        return 1

    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: failed to parse {status_path}: {exc}", file=sys.stderr)
        return 1

    tasks = data.get("tasks", [])
    count, logs = migrate_dependencies(tasks)

    if not logs:
        print("No dangling dependency migrations needed.")
    else:
        print(f"Migrated dependencies for {count} task(s):")
        for log in logs:
            print(f"  - {log}")

    # Validate candidate graph BEFORE any mutation or status file update
    board = {str(t.get("id", "")).strip().upper(): t for t in tasks if t.get("id")}
    archive = load_archive(archive_dir)
    failures = check(board, archive)

    if failures:
        print(
            f"\nPost-migration resolvability check: {len(failures)} failure(s)",
            file=sys.stderr,
        )
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    if not dry_run and count > 0:
        import ai_status

        canonical_status_path = ai_status.STATUS_FILE.resolve()
        status_path_resolved = status_path.resolve()
        if status_path_resolved != canonical_status_path:
            print(
                f"ERROR: status path mismatch: {status_path_resolved} does not match "
                f"canonical status file {canonical_status_path}",
                file=sys.stderr,
            )
            return 1

        try:
            state = ai_status.load_state()
            state["tasks"] = tasks
            ai_status.sync_all(state)
            print(f"Successfully updated and synced {status_path}")
        except Exception as exc:
            print(f"ERROR: canonical state sync failed: {exc}", file=sys.stderr)
            return 1

    print(
        f"\nPost-migration resolvability check: OK ({len(board)} tasks scanned, 0 failures)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate dangling task dependency graph entries in ai-status.json."
    )
    parser.add_argument(
        "--status",
        default=os.environ.get("ODP_SUPERVISOR_STATUS_FILE")
        or os.environ.get("PANTHEON_STATUS_ROOT", "") + "/ai-status.json",
        help="path to ai-status.json",
    )
    parser.add_argument(
        "--archive-dir",
        default=os.environ.get("ODP_SUPERVISOR_ARCHIVE_DIR")
        or os.environ.get("PANTHEON_STATUS_ROOT", "") + "/ai-task-archive/tasks",
        help="path to ai-task-archive/tasks",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="preview migration without modifying status file",
    )
    args = parser.parse_args(argv)

    status_path = Path(args.status)
    archive_dir = Path(args.archive_dir)

    return run_migration(status_path, archive_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

