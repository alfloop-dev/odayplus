#!/usr/bin/env python3
"""Diagnose tasks stranded in the finalize lane, and say exactly how to free them.

The supervisor's finalize step is a pure observer. When it probes a task's CI and
gets anything other than green it records one prose line and ``continue``s:

    elif ci_status == "failure":      -> "resolve failing checks before finalization."
    elif ci_status not in {...}:      -> "is unresolved (...); finalize dispatch suppressed."

Nothing in the loop ever makes CI green again. There is no code path anywhere in
``.orchestrator`` that reruns a workflow (``gh run rerun`` / ``workflow_dispatch``)
or advances a stale task branch onto the current base. So a task that is finished
*and reviewed* can sit in ``review_approved`` forever. On 2026-08-04 nine of them
were in exactly that state.

This tool supplies the missing half: it classifies *why* each task is stuck, which
determines the remedy. The three causes need three different fixes and must not be
conflated:

``NO_PR``
    The branch was pushed but no pull request exists, so the CI probe returns
    "unknown". Usually the ReviewBus detached-HEAD bug: ``current_branch()``
    returns the literal string ``"HEAD"``, ``review_branch_for_task()`` passes it
    through, and the bus records ``state="skipped_unpublished_branch"``. Because
    the bus only creates PRs while ``status == "review"``, a task that has moved
    on to ``review_approved`` can never get one. Remedy: create the PR.

``MISSING_REQUIRED_CHECK``
    A PR exists and its checks are green, but a *required* check never reported,
    so the PR is structurally unmergeable. In this repository that is almost
    always ``task-review-gate``, which only fires for tasks registered on the
    board. Remedy: register the task.

``CI_STALE`` / ``CI_FAILED``
    A PR exists and CI concluded red. If the run predates the current base the
    verdict is stale rather than a real defect. Remedy: advance the branch onto
    the base and rerun; only then is a red result trustworthy.

Read-only by default. ``--emit-commands`` prints the exact remediation for each
finding so a human or a follow-up task can act on it; nothing is mutated here,
because creating PRs and moving branches are governed actions that belong to an
owner, not to a diagnostic.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FINALIZE_STATUSES = ("review_approved",)
DEFAULT_REQUIRED_CHECKS = ("orchestrator", "product", "product-e2e-gate", "task-review-gate")

NO_PR = "NO_PR"
MISSING_REQUIRED_CHECK = "MISSING_REQUIRED_CHECK"
CI_STALE = "CI_STALE"
CI_FAILED = "CI_FAILED"
CI_PENDING = "CI_PENDING"
READY = "READY"

# Ordered worst-first so the report leads with what blocks the most work.
SEVERITY = (NO_PR, MISSING_REQUIRED_CHECK, CI_STALE, CI_FAILED, CI_PENDING, READY)


def _gh_json(args: list[str], cwd: Path) -> Any:
    proc = subprocess.run(
        ["gh", *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def load_board(status_path: Path) -> list[dict[str, Any]]:
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    return [t for t in payload.get("tasks", []) or [] if isinstance(t, dict)]


def branch_for(task: dict[str, Any], prefix: str = "task/") -> str:
    return f"{prefix}{task.get('id')}"


def find_pr(branch: str, repo_root: Path) -> dict[str, Any] | None:
    return _gh_json(
        ["pr", "view", branch, "--json", "number,state,statusCheckRollup,headRefOid"],
        repo_root,
    )


def rollup_verdict(rollup: list[dict[str, Any]]) -> tuple[str, list[str], list[str]]:
    """Return (verdict, failing_check_names, present_check_names)."""

    failing: list[str] = []
    present: list[str] = []
    pending = False
    for check in rollup or []:
        if not isinstance(check, dict):
            continue
        name = str(check.get("name") or check.get("context") or "")
        present.append(name)
        conclusion = str(check.get("conclusion") or "").upper()
        status = str(check.get("status") or "").upper()
        if conclusion in {"FAILURE", "CANCELLED", "TIMED_OUT"}:
            failing.append(name)
        elif not conclusion and status in {"IN_PROGRESS", "QUEUED", "PENDING", ""}:
            pending = True
    if failing:
        return "failure", failing, present
    if pending:
        return "pending", [], present
    return "success", [], present


def branch_is_behind(branch: str, base: str, repo_root: Path) -> bool:
    behind = _git(repo_root, "rev-list", "--count", f"origin/{branch}..origin/{base}")
    try:
        return int(behind) > 0
    except ValueError:
        return False


def classify(
    task: dict[str, Any],
    repo_root: Path,
    base: str,
    required_checks: tuple[str, ...],
) -> dict[str, Any]:
    branch = branch_for(task)
    pr = find_pr(branch, repo_root)
    finding: dict[str, Any] = {
        "task_id": task.get("id"),
        "branch": branch,
        "pr": None,
        "cause": None,
        "detail": "",
    }

    if not pr:
        remote_exists = bool(_git(repo_root, "ls-remote", "--heads", "origin", branch))
        finding["cause"] = NO_PR
        finding["detail"] = (
            "branch is pushed but no pull request exists"
            if remote_exists
            else "no pull request and no remote branch"
        )
        finding["remote_branch"] = remote_exists
        return finding

    finding["pr"] = pr.get("number")
    verdict, failing, present = rollup_verdict(pr.get("statusCheckRollup") or [])
    missing = [c for c in required_checks if c not in present]

    if missing:
        finding["cause"] = MISSING_REQUIRED_CHECK
        finding["detail"] = f"required check(s) never reported: {', '.join(missing)}"
        finding["missing_checks"] = missing
        return finding

    if verdict == "failure":
        stale = branch_is_behind(branch, base, repo_root)
        finding["cause"] = CI_STALE if stale else CI_FAILED
        finding["detail"] = (
            f"failing: {', '.join(failing)}"
            + (f" (branch is behind origin/{base}; verdict may be stale)" if stale else "")
        )
        finding["failing_checks"] = failing
        return finding

    if verdict == "pending":
        finding["cause"] = CI_PENDING
        finding["detail"] = "checks still running"
        return finding

    finding["cause"] = READY
    finding["detail"] = "all required checks green"
    return finding


def remediation(finding: dict[str, Any], base: str) -> list[str]:
    cause = finding["cause"]
    branch = finding["branch"]
    task_id = finding["task_id"]
    if cause == NO_PR:
        if not finding.get("remote_branch"):
            return [f"# {task_id}: no remote branch - the work was never pushed"]
        return [
            f"gh pr create --base {base} --head {branch} \\",
            f'  --title "[ReviewBus] {task_id}" --body "Recovered stranded finalize-lane task."',
        ]
    if cause == MISSING_REQUIRED_CHECK:
        return [
            f"# {task_id}: register on the board so the required check fires",
            f"AI_NAME=<actor> python3 scripts/ai_status.py assign {task_id} <owner> <reviewer> \"<title>\"",
        ]
    if cause in (CI_STALE, CI_FAILED):
        return [
            f"git fetch origin && git checkout {branch}",
            f"git merge --no-edit origin/{base} && git push",
            f"# then rerun: gh pr checks {finding.get('pr')} --watch",
        ]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--status", default=os.environ.get("ODP_SUPERVISOR_STATUS_FILE"))
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base", default="dev")
    parser.add_argument("--emit-commands", action="store_true")
    parser.add_argument(
        "--required-check",
        action="append",
        default=None,
        help="required check name; repeatable. Defaults to this repo's dev protection set.",
    )
    args = parser.parse_args(argv)

    if not args.status:
        parser.error("--status is required (or set ODP_SUPERVISOR_STATUS_FILE)")

    repo_root = Path(args.repo).resolve()
    required = tuple(args.required_check or DEFAULT_REQUIRED_CHECKS)

    tasks = [
        t
        for t in load_board(Path(args.status))
        if t.get("status") in FINALIZE_STATUSES and "SIDECAR" not in str(t.get("id", ""))
    ]

    findings = [classify(t, repo_root, args.base, required) for t in tasks]
    findings.sort(key=lambda f: SEVERITY.index(f["cause"]))

    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    print(f"Finalize-lane doctor  base={args.base}  scanned={len(findings)}  at={stamp}\n")

    counts: dict[str, int] = {}
    for f in findings:
        counts[f["cause"]] = counts.get(f["cause"], 0) + 1
        pr = f"PR #{f['pr']}" if f["pr"] else "no PR"
        print(f"  [{f['cause']:22s}] {f['task_id']:52s} {pr}")
        print(f"      {f['detail']}")
        if args.emit_commands:
            for line in remediation(f, args.base):
                print(f"      $ {line}")
    print()
    print("  summary: " + ", ".join(f"{k}={v}" for k, v in counts.items()))

    stuck = sum(v for k, v in counts.items() if k not in (READY, CI_PENDING))
    if stuck:
        print(
            f"\n{stuck} task(s) are finished and reviewed but cannot merge without "
            "intervention. The supervisor will not clear these on its own."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
