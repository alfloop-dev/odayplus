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
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FINALIZE_STATUSES = ("review_approved",)
DEFAULT_REQUIRED_CHECKS = ("orchestrator", "product", "product-e2e-gate", "task-review-gate")

ALREADY_MERGED = "ALREADY_MERGED"
NO_PR = "NO_PR"
MISSING_REQUIRED_CHECK = "MISSING_REQUIRED_CHECK"
CI_STALE = "CI_STALE"
CI_FAILED = "CI_FAILED"
CI_PENDING = "CI_PENDING"
READY = "READY"

# Ordered worst-first so the report leads with what blocks the most work.
SEVERITY = (
    ALREADY_MERGED,
    NO_PR,
    MISSING_REQUIRED_CHECK,
    CI_STALE,
    CI_FAILED,
    CI_PENDING,
    READY,
)

# Compatibility categories for the original status-file-only diagnostic API.
# The richer GitHub-aware doctor below is canonical; these names remain stable
# for runbooks and tests that used the first implementation.
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
    """Raised when the compatibility status file cannot be loaded."""


def load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FinalizeDiagnosisError(f"file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FinalizeDiagnosisError(f"failed to parse JSON from {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise FinalizeDiagnosisError(f"file content is not a JSON object: {path}")
    return payload


def find_finalize_tasks(
    status_data: dict[str, Any],
    config_data: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return tasks in the legacy finalize lane from a local status payload."""
    finalize_statuses = {"review_approved"}
    if config_data and isinstance(config_data.get("ready_dispatch"), dict):
        custom = config_data["ready_dispatch"].get("finalize_statuses", [])
        if isinstance(custom, list):
            finalize_statuses.update(str(value).lower() for value in custom if value)

    finalize_agents = {
        str(agent.get("name") or "").strip()
        for agent in status_data.get("agents", []) or []
        if isinstance(agent, dict)
        and str(agent.get("status") or "").lower() == "finalize"
        and agent.get("name")
    }
    tasks: list[dict[str, Any]] = []
    for task in status_data.get("tasks", []) or []:
        if not isinstance(task, dict) or not str(task.get("id") or "").strip():
            continue
        if (
            str(task.get("status") or "").lower() in finalize_statuses
            or str(task.get("owner") or "").strip() in finalize_agents
        ):
            tasks.append(task)
    return tasks


def classify_stranded_task(
    task: dict[str, Any],
    agents_map: dict[str, dict[str, Any]],
) -> tuple[str, str, str, str]:
    """Classify a legacy status record without contacting GitHub."""
    task_id = str(task.get("id") or "")
    owner = str(task.get("owner") or "").strip()
    owner_agent = agents_map.get(owner)
    pr_number = task.get("pr")
    combined_next = " ".join(
        (str(task.get("next") or ""), str(owner_agent.get("next") or "") if owner_agent else "")
    ).lower()

    if not owner:
        return (
            CAT_OWNER_UNAVAILABLE,
            f"Task {task_id} has no assigned owner.",
            "Reassign task owner to an active worker agent.",
            f"python3 scripts/ai_status.py assign {task_id} --owner <ActiveWorker>",
        )
    if "sidecar-only" in combined_next or "auto-reassigned" in combined_next:
        return (
            CAT_OWNER_UNAVAILABLE,
            f"Task {task_id} owner '{owner}' is a sidecar-only agent or was unassigned from mainline.",
            f"Reassign task {task_id} to an active mainline worker agent.",
            f"python3 scripts/ai_status.py assign {task_id} --owner Antigravity",
        )
    if owner_agent and str(owner_agent.get("status") or "").lower() in {
        "blocked",
        "paused",
        "quota_terminal",
    }:
        state = str(owner_agent.get("status")).lower()
        return (
            CAT_OWNER_UNAVAILABLE,
            f"Task owner '{owner}' status is currently '{state}'.",
            f"Reassign task {task_id} or resolve owner's {state} state.",
            f"python3 scripts/ai_status.py assign {task_id} --owner Antigravity",
        )
    if any(value in combined_next for value in ("behind", "rebase", "conflict", "base advance", "head_liveness")):
        return (
            CAT_STALE_BASE,
            f"Task {task_id} PR branch is behind target base 'dev' or has conflicts.",
            f"Advance base for task {task_id} by rebasing/merging origin/dev onto task branch.",
            f'./scripts/git/task_start.sh "{task_id}" && git merge origin/dev && ./scripts/git/task_finalize.sh "{task_id}"',
        )
    if any(value in combined_next for value in ("unresolved", "unknown", "pending", "in_progress", "conclusive")):
        pr_ref = f"PR #{pr_number}" if pr_number else "task PR"
        return (
            CAT_CI_UNRESOLVED,
            f"CI status for {pr_ref} is unresolved or pending.",
            f"Query GitHub status checks for {pr_ref} and trigger a rerun if stalled.",
            f"gh pr view {pr_number or '<PR_NUM>'} --json statusCheckRollup",
        )
    if any(value in combined_next for value in ("failed", "failure", "cancelled", "timed_out", "action_required")):
        pr_ref = f"PR #{pr_number}" if pr_number else "task PR"
        return (
            CAT_CI_FAILED,
            f"CI checks for {pr_ref} failed.",
            f"Inspect failing CI logs for {pr_ref}, resolve the failure, and push a repair commit.",
            f"gh pr view {pr_number or '<PR_NUM>'} --checks",
        )
    if pr_number is None:
        return (
            CAT_MISSING_PR,
            f"Task {task_id} is in finalize status but carries no open PR number.",
            f"Create the task PR for {task_id} via task_finalize.sh.",
            f'./scripts/git/task_finalize.sh "{task_id}"',
        )
    return (
        CAT_CI_UNRESOLVED,
        f"Task {task_id} is stuck in finalize lane: {task.get('next') or 'Pending finalization check'}.",
        "Re-evaluate CI status checks and push a status update.",
        f"python3 scripts/ai_status.py status {task_id}",
    )


def diagnose(
    status_path: Path,
    config_path: Path | None = None,
    task_ids: list[str] | None = None,
    category_filter: str | None = None,
) -> dict[str, Any]:
    """Run the original local/status-only diagnostic API."""
    status_data = load_json_file(status_path)
    config_data = load_json_file(config_path) if config_path and config_path.exists() else {}
    agents = {
        str(agent.get("name") or "").strip(): agent
        for agent in status_data.get("agents", []) or []
        if isinstance(agent, dict) and agent.get("name")
    }
    tasks = find_finalize_tasks(status_data, config_data)
    if task_ids:
        wanted = {value.upper() for value in task_ids}
        tasks = [task for task in tasks if str(task.get("id") or "").upper() in wanted]
    counts = {category: 0 for category in ALL_CATEGORIES}
    findings: list[dict[str, Any]] = []
    for task in tasks:
        category, reason, remediation_text, command = classify_stranded_task(task, agents)
        if category_filter and category != category_filter.upper():
            continue
        counts[category] += 1
        findings.append({
            "task_id": str(task.get("id") or ""),
            "status": task.get("status"),
            "owner": task.get("owner"),
            "reviewer": task.get("reviewer"),
            "pr_number": task.get("pr"),
            "category": category,
            "reason": reason,
            "remediation": remediation_text,
            "remediation_cmd": command,
            "next_note": task.get("next"),
        })
    return {
        "status_file": str(status_path),
        "total_finalize_lane_tasks": len(tasks),
        "stranded_count": len(findings),
        "category_counts": counts,
        "tasks": findings,
    }


def legacy_main(argv: list[str] | None = None) -> int:
    """Compatibility CLI for the original status-file-only doctor."""
    parser = argparse.ArgumentParser(description="Diagnose finalize-lane tasks from local status data")
    parser.add_argument("--status", "-s", default=os.environ.get("ODP_SUPERVISOR_STATUS_FILE", "ai-status.json"))
    parser.add_argument("--config", "-c", default=os.environ.get("ODP_SUPERVISOR_CONFIG_FILE", ".orchestrator/config.json"))
    parser.add_argument("--task", "-t", action="append", default=[])
    parser.add_argument("--category", choices=ALL_CATEGORIES)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--remediate", action="store_true")
    parser.add_argument("--fail-on-stranded", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = diagnose(Path(args.status), Path(args.config), args.task, args.category)
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
        for category, count in report["category_counts"].items():
            print(f"  - {category:<20}: {count}")
        for item in report["tasks"]:
            print(f"[{item['category']}] Task: {item['task_id']}")
            print(f"  Reason      : {item['reason']}")
            print(f"  Remediation : {item['remediation']}")
            if args.remediate:
                print(f"  Command     : {item['remediation_cmd']}")
    return int(args.fail_on_stranded and report["stranded_count"] > 0)


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


PR_JSON_FIELDS = "number,state,statusCheckRollup,headRefOid,baseRefName"


def find_pr(branch: str, repo_root: Path, base: str = "") -> dict[str, Any] | None:
    """Return the pull request for ``branch`` that targets ``base``.

    ``gh pr view <branch>`` answers with whichever PR was updated most recently.
    A task branch usually has two -- the ReviewBus one against ``main`` and the
    real one against ``dev`` -- so that answer is decided by timing rather than
    by which PR governs the merge. The diagnosis then describes the wrong PR's
    checks. Listing and filtering on the base makes the choice a fact.
    """

    candidates = _gh_json(
        ["pr", "list", "--head", branch, "--state", "all", "--limit", "50", "--json", PR_JSON_FIELDS],
        repo_root,
    )
    if isinstance(candidates, list):
        on_base = [
            pr
            for pr in candidates
            if isinstance(pr, dict) and (not base or str(pr.get("baseRefName") or "") == base)
        ]
        if on_base:
            merged = [pr for pr in on_base if str(pr.get("state") or "").upper() == "MERGED"]
            return (merged or on_base)[0]

    return _gh_json(["pr", "view", branch, "--json", PR_JSON_FIELDS], repo_root)


# ``gh`` renders an unset GraphQL ``DateTime`` as Go's zero time rather than
# omitting the field. Kept in step with ``status_check_timestamp`` in
# scripts/ai_status.py, which collapses the same rollup for the finalize gate.
ZERO_TIMESTAMP_PREFIXES = ("0001-01-01", "0000-01-01")


def check_timestamp(check: dict[str, Any]) -> str:
    """The newest real timestamp on one rollup entry, ignoring zero sentinels.

    A re-run that is still running reports ``completedAt:
    "0001-01-01T00:00:00Z"``, which sorts below every real timestamp. Ranking on
    it picks the older completed run, so the doctor calls a PR green while its
    newest run is unfinished and sends the owner to finalize a task whose CI has
    not concluded. Rank on when the run actually last did something instead.
    """

    stamps = [
        stamp
        for field in ("completedAt", "startedAt")
        if (stamp := str(check.get(field) or "").strip())
        and not stamp.startswith(ZERO_TIMESTAMP_PREFIXES)
    ]
    return max(stamps, default="")


def latest_checks_by_name(rollup: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse a rollup to the newest run per check, as branch protection reads it.

    ``statusCheckRollup`` lists every run recorded against the head commit,
    including attempts that were later re-run. PR #575 merged into ``dev``
    carrying a stale ``product`` FAILURE alongside the ``product`` SUCCESS that
    actually gated it. Counting both makes a mergeable PR look permanently red.
    """

    newest: dict[str, tuple[tuple[str, int], dict[str, Any]]] = {}
    order: list[str] = []
    for index, check in enumerate(rollup or []):
        if not isinstance(check, dict):
            continue
        name = str(check.get("name") or check.get("context") or "")
        identity = f"{check.get('workflowName') or ''}\x00{name}"
        key = (check_timestamp(check), index)
        if identity not in newest:
            order.append(identity)
            newest[identity] = (key, check)
        elif key >= newest[identity][0]:
            newest[identity] = (key, check)
    return [newest[identity][1] for identity in order]


def rollup_verdict(rollup: list[dict[str, Any]]) -> tuple[str, list[str], list[str]]:
    """Return (verdict, failing_check_names, present_check_names)."""

    failing: list[str] = []
    present: list[str] = []
    pending = False
    for check in latest_checks_by_name(rollup):
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


def branch_merged_into_base(branch: str, base: str, repo_root: Path) -> bool:
    """True when the branch tip is already an ancestor of the base.

    Found on live state: a task sat at ``review_approved`` for two days after its
    work had merged. The board never learned, so the finalize probe kept asking
    about a PR that had nothing left to propose. Creating a PR for it fails with
    "No commits between ...", which is a confusing way to be told the work is done.
    """

    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", f"origin/{branch}", f"origin/{base}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


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

    if branch_merged_into_base(branch, base, repo_root):
        return {
            "task_id": task.get("id"),
            "branch": branch,
            "pr": None,
            "cause": ALREADY_MERGED,
            "detail": f"branch is already an ancestor of origin/{base}; the work has landed",
        }

    pr = find_pr(branch, repo_root, base)
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
    if cause == ALREADY_MERGED:
        return [
            f"# {task_id}: work already in {base}; close the task rather than opening a PR",
            f"AI_NAME=<owner> python3 scripts/ai_status.py done {task_id} \"<closeout note>\"",
        ]
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
