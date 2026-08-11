#!/usr/bin/env python3
"""Diagnostic and remediation tool for tasks stranded in the finalize lane.

In the Pantheon Orchestrator architecture, the Supervisor's finalize logic acts
primarily as an observer: when encountering non-green CI or unresolvable PR state,
it records a status message and continues. Without automated CI re-triggering or
base-advance rebase code in the Orchestrator loop, tasks in ``review_approved``
(or agents in ``finalize`` status) can become stranded indefinitely.

This tool diagnoses stranded finalize tasks, categorizing them into five distinct
root causes and providing actionable remediation steps for each:

1. ``CI_UNRESOLVED``: CI status is pending, unknown, or unresolved.
2. ``CI_FAILED``: Required CI checks or gate assertions failed (red CI).
3. ``STALE_BASE``: Task branch is behind the base branch (``dev``) or has conflicts.
4. ``MISSING_PR``: Task is in finalize status but has no open GitHub PR.
5. ``OWNER_UNAVAILABLE``: Task owner is unassigned, assigned to a sidecar-only agent,
   or the owner's status is blocked, paused, or quota-terminal.

Exit codes:
- ``0``: Diagnosis completed successfully (or no stranded tasks when ``--fail-on-stranded`` is set).
- ``1``: Stranded tasks found when ``--fail-on-stranded`` is enabled, or execution failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# Standard 5 Root Cause Categories
CAT_CI_UNRESOLVED = "CI_UNRESOLVED"
CAT_CI_FAILED = "CI_FAILED"
CAT_STALE_BASE = "STALE_BASE"
CAT_MISSING_PR = "MISSING_PR"
CAT_OWNER_UNAVAILABLE = "OWNER_UNAVAILABLE"

ALL_CATEGORIES = [
    CAT_CI_UNRESOLVED,
    CAT_CI_FAILED,
    CAT_STALE_BASE,
    CAT_MISSING_PR,
    CAT_OWNER_UNAVAILABLE,
]


class FinalizeDiagnosisError(Exception):
    """Raised when status or config data cannot be loaded."""


def load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FinalizeDiagnosisError(f"file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise FinalizeDiagnosisError(f"file content is not a JSON object: {path}")
        return data
    except json.JSONDecodeError as exc:
        raise FinalizeDiagnosisError(f"failed to parse JSON from {path}: {exc}") from exc


def find_finalize_tasks(
    status_data: dict[str, Any],
    config_data: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return all tasks that are currently in the finalize lane."""
    finalize_statuses = {"review_approved"}
    if config_data and isinstance(config_data.get("ready_dispatch"), dict):
        custom_statuses = config_data["ready_dispatch"].get("finalize_statuses", [])
        if isinstance(custom_statuses, list):
            finalize_statuses.update(str(s).lower() for s in custom_statuses if s)

    tasks: list[dict[str, Any]] = []
    agents_in_finalize: set[str] = set()

    for agent in status_data.get("agents", []) or []:
        if isinstance(agent, dict) and str(agent.get("status") or "").lower() == "finalize":
            agent_name = str(agent.get("name") or "").strip()
            if agent_name:
                agents_in_finalize.add(agent_name)

    for task in status_data.get("tasks", []) or []:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            continue
        task_status = str(task.get("status") or "").lower()
        task_owner = str(task.get("owner") or "").strip()

        if task_status in finalize_statuses or task_owner in agents_in_finalize:
            tasks.append(task)

    return tasks


def classify_stranded_task(
    task: dict[str, Any],
    agents_map: dict[str, dict[str, Any]],
) -> tuple[str, str, str, str]:
    """Classify a stranded task into one of the 5 root cause categories.

    Returns tuple of: (category, reason, remediation_summary, remediation_cmd)
    """
    task_id = str(task.get("id") or "")
    owner_name = str(task.get("owner") or "").strip()
    owner_agent = agents_map.get(owner_name)
    pr_num = task.get("pr")

    task_next = str(task.get("next") or "").strip()
    agent_next = str(owner_agent.get("next") or "").strip() if owner_agent else ""
    combined_next = f"{task_next} {agent_next}".lower()

    # 1. OWNER_UNAVAILABLE
    if not owner_name:
        return (
            CAT_OWNER_UNAVAILABLE,
            f"Task {task_id} has no assigned owner.",
            "Reassign task owner to an active worker agent.",
            f"python3 scripts/ai_status.py assign {task_id} --owner <ActiveWorker>",
        )

    if "sidecar-only" in combined_next or "auto-reassigned" in combined_next:
        return (
            CAT_OWNER_UNAVAILABLE,
            f"Task {task_id} owner '{owner_name}' is a sidecar-only agent or was unassigned from mainline.",
            f"Reassign task {task_id} from sidecar agent '{owner_name}' to an active mainline worker agent.",
            f"python3 scripts/ai_status.py assign {task_id} --owner Antigravity",
        )

    if owner_agent:
        owner_status = str(owner_agent.get("status") or "").lower()
        if owner_status in ("blocked", "paused", "quota_terminal"):
            return (
                CAT_OWNER_UNAVAILABLE,
                f"Task owner '{owner_name}' status is currently '{owner_status}'.",
                f"Reassign task {task_id} to an available active worker or resolve owner's {owner_status} state.",
                f"python3 scripts/ai_status.py assign {task_id} --owner Antigravity",
            )

    # 2. STALE_BASE
    if any(kw in combined_next for kw in ("behind", "rebase", "conflict", "base advance", "head_liveness")):
        return (
            CAT_STALE_BASE,
            f"Task {task_id} PR branch is behind target base 'dev' or has conflicts.",
            f"Advance base for task {task_id} by rebasing/merging origin/dev onto task branch.",
            f"./scripts/git/task_start.sh \"{task_id}\" && git merge origin/dev && ./scripts/git/task_finalize.sh \"{task_id}\"",
        )

    # 3. CI_UNRESOLVED
    if any(kw in combined_next for kw in ("unresolved", "unknown", "pending", "in_progress", "conclusive")):
        pr_ref = f"PR #{pr_num}" if pr_num else "task PR"
        return (
            CAT_CI_UNRESOLVED,
            f"CI status for {pr_ref} is unresolved or pending.",
            f"Query GitHub status check rollup for {pr_ref} and trigger CI rerun if stalled.",
            f"gh pr view {pr_num or '<PR_NUM>'} --json statusCheckRollup",
        )

    # 4. CI_FAILED
    if any(kw in combined_next for kw in ("failed", "failure", "cancelled", "timed_out", "action_required")):
        pr_ref = f"PR #{pr_num}" if pr_num else "task PR"
        return (
            CAT_CI_FAILED,
            f"CI checks for {pr_ref} failed.",
            f"Inspect failing CI logs for {pr_ref}, resolve test/gate failure, and push repair commit.",
            f"gh pr view {pr_num or '<PR_NUM>'} --checks",
        )

    # 5. MISSING_PR
    if pr_num is None:
        return (
            CAT_MISSING_PR,
            f"Task {task_id} is in review_approved / finalize status but carries no open PR number.",
            f"Create task PR for {task_id} via task_finalize.sh.",
            f"./scripts/git/task_finalize.sh \"{task_id}\"",
        )

    # Default fallback to CI_UNRESOLVED if unclassified
    return (
        CAT_CI_UNRESOLVED,
        f"Task {task_id} is stuck in finalize lane: {task_next or 'Pending finalization check'}.",
        "Re-evaluate CI status checks and push status update.",
        f"python3 scripts/ai_status.py status {task_id}",
    )


def diagnose(
    status_path: Path,
    config_path: Path | None = None,
    task_ids: list[str] | None = None,
    category_filter: str | None = None,
) -> dict[str, Any]:
    """Perform diagnosis on finalize lane tasks and return a structured report."""
    status_data = load_json_file(status_path)
    config_data = load_json_file(config_path) if config_path and config_path.exists() else {}

    agents_map = {
        str(a.get("name") or "").strip(): a
        for a in (status_data.get("agents", []) or [])
        if isinstance(a, dict) and a.get("name")
    }

    finalize_tasks = find_finalize_tasks(status_data, config_data)
    if task_ids:
        filter_set = {t.upper() for t in task_ids}
        finalize_tasks = [t for t in finalize_tasks if str(t.get("id") or "").upper() in filter_set]

    category_counts = {cat: 0 for cat in ALL_CATEGORIES}
    diagnosed_items: list[dict[str, Any]] = []

    for task in finalize_tasks:
        task_id = str(task.get("id") or "")
        cat, reason, remediation, remediation_cmd = classify_stranded_task(task, agents_map)

        if category_filter and cat != category_filter.upper():
            continue

        category_counts[cat] += 1
        diagnosed_items.append(
            {
                "task_id": task_id,
                "status": task.get("status"),
                "owner": task.get("owner"),
                "reviewer": task.get("reviewer"),
                "pr_number": task.get("pr"),
                "category": cat,
                "reason": reason,
                "remediation": remediation,
                "remediation_cmd": remediation_cmd,
                "next_note": task.get("next"),
            }
        )

    return {
        "status_file": str(status_path),
        "total_finalize_lane_tasks": len(finalize_tasks),
        "stranded_count": len(diagnosed_items),
        "category_counts": category_counts,
        "tasks": diagnosed_items,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--status",
        "-s",
        default=os.environ.get("ODP_SUPERVISOR_STATUS_FILE", "ai-status.json"),
        help="path to ai-status.json (default: ai-status.json)",
    )
    parser.add_argument(
        "--config",
        "-c",
        default=os.environ.get("ODP_SUPERVISOR_CONFIG_FILE", ".orchestrator/config.json"),
        help="path to .orchestrator/config.json",
    )
    parser.add_argument(
        "--task",
        "-t",
        action="append",
        default=[],
        help="restrict diagnosis to specific task ID(s)",
    )
    parser.add_argument(
        "--category",
        choices=ALL_CATEGORIES,
        help="filter output to a specific root cause category",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="output report in machine-readable JSON format",
    )
    parser.add_argument(
        "--remediate",
        action="store_true",
        help="include explicit remediation commands in human-readable output",
    )
    parser.add_argument(
        "--fail-on-stranded",
        action="store_true",
        help="exit with status code 1 if any stranded tasks are found",
    )

    args = parser.parse_args(argv)

    status_path = Path(args.status)
    config_path = Path(args.config) if args.config else None

    try:
        report = diagnose(
            status_path=status_path,
            config_path=config_path,
            task_ids=args.task,
            category_filter=args.category,
        )
    except FinalizeDiagnosisError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("Finalize Lane Diagnosis Report")
        print("==============================")
        print(f"Status File : {report['status_file']}")
        print(f"Total Tasks : {report['total_finalize_lane_tasks']}")
        print(f"Stranded    : {report['stranded_count']}\n")

        print("Root Cause Category Breakdown:")
        for cat, cnt in report["category_counts"].items():
            print(f"  - {cat:<20}: {cnt}")

        if report["tasks"]:
            print("\nStranded Task Details:")
            print("----------------------")
            for item in report["tasks"]:
                print(f"[{item['category']}] Task: {item['task_id']}")
                print(f"  Owner       : {item['owner']} | Reviewer: {item['reviewer']} | PR: #{item['pr_number'] or 'N/A'}")
                print(f"  Reason      : {item['reason']}")
                print(f"  Remediation : {item['remediation']}")
                if args.remediate and item.get("remediation_cmd"):
                    print(f"  Command     : {item['remediation_cmd']}")
                print()
        else:
            print("\nNo stranded tasks found in finalize lane.")

    if args.fail_on_stranded and report["stranded_count"] > 0:
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
