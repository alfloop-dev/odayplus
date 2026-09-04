from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import ai_status as runtime_ai_status
from common import utc_now, write_activity_log


@dataclass(frozen=True)
class FinalizeGateResult:
    status: str
    current_head: str | None = None
    approved_head: str | None = None
    pr_status: str | None = None
    ci_status: str | None = None
    error: str | None = None


READY = "ready"
MISSING_APPROVED_HEAD = "missing_approved_head"
HEAD_MISMATCH = "head_mismatch"
HEAD_UNRESOLVED = "head_unresolved"
CI_PENDING = "ci_pending"
CI_FAILURE = "ci_failure"
CI_UNRESOLVED = "ci_unresolved"
PR_NOT_MERGED = "pr_not_merged"

MERGE_GROUP_CONCLUSION_SUCCESS = "success"
MERGE_GROUP_CONCLUSION_FAILURE = "failure"
MERGE_GROUP_CONCLUSION_TIMED_OUT = "timed_out"
MERGE_GROUP_CONCLUSION_CANCELLED = "cancelled"
MERGE_GROUP_CONCLUSION_ACTION_REQUIRED = "action_required"
MERGE_GROUP_CONCLUSION_STARTUP_FAILURE = "startup_failure"
MERGE_GROUP_CONCLUSION_NEUTRAL = "neutral"
MERGE_GROUP_CONCLUSION_SKIPPED = "skipped"

FAILURE_CONCLUSIONS = frozenset({
    MERGE_GROUP_CONCLUSION_FAILURE,
    MERGE_GROUP_CONCLUSION_TIMED_OUT,
    MERGE_GROUP_CONCLUSION_CANCELLED,
    MERGE_GROUP_CONCLUSION_ACTION_REQUIRED,
    MERGE_GROUP_CONCLUSION_STARTUP_FAILURE,
})

SUCCESS_CONCLUSIONS = frozenset({
    MERGE_GROUP_CONCLUSION_SUCCESS,
    MERGE_GROUP_CONCLUSION_NEUTRAL,
    MERGE_GROUP_CONCLUSION_SKIPPED,
})

MERGE_GROUP_QUEUE_REF_PATTERN = re.compile(
    r"(?:^|/)(?:gh-readonly-queue/[^/]+/)?pr-(?P<pr>\d+)-[0-9a-fA-F]+",
    re.IGNORECASE,
)


def evaluate_finalize_gate(task: dict[str, Any]) -> FinalizeGateResult:
    """Evaluate whether a review_approved task is ready for finalize dispatch.

    The check intentionally mirrors the previous in-supervisor logic in
    `dispatch_ready_tasks` and `dispatch_priority_for_task` so both paths share
    one source of truth and do not diverge.
    """

    task_id = str(task.get("id") or "")
    approved_head = task.get("approved_head")
    if not approved_head:
        return FinalizeGateResult(status=MISSING_APPROVED_HEAD, approved_head=None)

    try:
        current_head = runtime_ai_status.resolve_task_checkout_sha(task, force_refresh=True)
    except Exception as exc:
        return FinalizeGateResult(
            status=HEAD_UNRESOLVED,
            approved_head=str(approved_head),
            error=f"{type(exc).__name__}: {exc}",
        )

    if current_head is not None:
        try:
            current_head = str(current_head).strip()
        except Exception:
            current_head = None

    if not current_head:
        return FinalizeGateResult(
            status=HEAD_UNRESOLVED,
            approved_head=str(approved_head),
            error="Unable to resolve current task HEAD.",
        )

    if not runtime_ai_status.is_approved_head_satisfied(task, current_head, approved_head):
        return FinalizeGateResult(
            status=HEAD_MISMATCH,
            current_head=current_head,
            approved_head=str(approved_head),
        )

    try:
        pr_status, ci_status = runtime_ai_status.task_pr_ci_status(task_id)
    except Exception as exc:
        return FinalizeGateResult(
            status=CI_UNRESOLVED,
            current_head=current_head,
            approved_head=str(approved_head),
            error=f"{type(exc).__name__}: {exc}",
        )

    pr_status = str(pr_status or "").strip().upper()
    ci_status = str(ci_status or "").strip().lower()
    if ci_status == "pending":
        return FinalizeGateResult(
            status=CI_PENDING,
            current_head=current_head,
            approved_head=str(approved_head),
            pr_status=pr_status,
            ci_status=ci_status,
        )
    if ci_status == "failure":
        return FinalizeGateResult(
            status=CI_FAILURE,
            current_head=current_head,
            approved_head=str(approved_head),
            pr_status=pr_status,
            ci_status=ci_status,
        )
    if ci_status not in {"success", "none"}:
        return FinalizeGateResult(
            status=CI_UNRESOLVED,
            current_head=current_head,
            approved_head=str(approved_head),
            pr_status=pr_status,
            ci_status=ci_status,
        )

    if pr_status != "MERGED":
        return FinalizeGateResult(
            status=PR_NOT_MERGED,
            current_head=current_head,
            approved_head=str(approved_head),
            pr_status=pr_status,
            ci_status=ci_status,
        )

    return FinalizeGateResult(
        status=READY,
        current_head=current_head,
        approved_head=str(approved_head),
        pr_status=pr_status,
        ci_status=ci_status,
    )


def parse_merge_group_pr_number(queue_ref: str | None) -> int | None:
    """Extract candidate PR number from a merge_group queue ref.

    Expected ref formats include:
      - refs/heads/gh-readonly-queue/<base>/pr-<number>-<base_sha>
      - gh-readonly-queue/<base>/pr-<number>-<base_sha>
      - pr-<number>-<base_sha>
    """
    if not queue_ref or not isinstance(queue_ref, str):
        return None
    raw = queue_ref.strip()
    if not raw:
        return None
    match = MERGE_GROUP_QUEUE_REF_PATTERN.search(raw)
    if not match:
        return None
    try:
        return int(match.group("pr"))
    except (TypeError, ValueError):
        return None


def correlate_merge_group_task(
    status: dict[str, Any],
    pr_number: int,
    bus_state: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Correlate a candidate PR number to a single task safely.

    Returns (task, "matched"), (None, "ambiguous"), or (None, "unmatched").
    """
    if not pr_number or pr_number <= 0:
        return None, "unmatched"

    bus_tasks = (bus_state.get("tasks", {}) or {}) if isinstance(bus_state, dict) else {}
    matched: list[dict[str, Any]] = []

    for task in status.get("tasks", []) or []:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or "")
        task_pr = None
        try:
            if task.get("pr_number") is not None:
                task_pr = int(task.get("pr_number") or 0)
        except (TypeError, ValueError):
            pass

        sub_pr = None
        sub = task.get("review_submission")
        if isinstance(sub, dict) and sub.get("pr_number") is not None:
            try:
                sub_pr = int(sub.get("pr_number") or 0)
            except (TypeError, ValueError):
                pass

        route_pr = None
        route = task.get("merge_route")
        if isinstance(route, dict) and route.get("pr_number") is not None:
            try:
                route_pr = int(route.get("pr_number") or 0)
            except (TypeError, ValueError):
                pass

        bus_pr = None
        bus_entry = bus_tasks.get(task_id) or {}
        if isinstance(bus_entry, dict):
            review_pr = bus_entry.get("review_pr") or {}
            if isinstance(review_pr, dict) and review_pr.get("number") is not None:
                try:
                    bus_pr = int(review_pr.get("number") or 0)
                except (TypeError, ValueError):
                    pass

        if pr_number in {task_pr, sub_pr, route_pr, bus_pr}:
            matched.append(task)

    if len(matched) == 1:
        return matched[0], "matched"
    if len(matched) > 1:
        return None, "ambiguous"
    return None, "unmatched"


def _record_seen_run_ids(bus_state: dict[str, Any], new_keys: list[str], max_ids: int = 2000) -> None:
    current = bus_state.setdefault("processed_merge_group_run_ids", [])
    seen = set(current)
    for k in new_keys:
        if k not in seen:
            current.append(k)
            seen.add(k)
    bus_state["processed_merge_group_run_ids"] = current[-max_ids:]


def fetch_merge_group_runs(repo: str, limit: int = 30) -> list[dict[str, Any]]:
    """Fetch merge_group workflow runs from GitHub via gh_json."""
    from github_bus import gh_json

    data = gh_json(["api", f"repos/{repo}/actions/runs?event=merge_group&per_page={limit}"])
    if isinstance(data, dict):
        runs = data.get("workflow_runs", [])
        return runs if isinstance(runs, list) else []
    if isinstance(data, list):
        return data
    return []


def reconcile_merge_group_runs(
    config: dict[str, Any],
    bus_state: dict[str, Any],
    status: dict[str, Any],
    repo: str,
    runs: list[dict[str, Any]],
) -> bool:
    """Reconcile merge_group workflow runs.

    Processes merge queue failures, saving exact run, queue ref, head SHA,
    and correlated PR/task as auditable events, and dispatches a one-time
    reviewer recovery handoff.

    Never automatically requeues, reopens, merges, or mutates product tasks.
    Success, stale, ambiguous, or duplicate events produce no side effects.
    """
    from status_transition import commit_canonical_task_transition

    if not isinstance(runs, list) or not runs:
        return False

    seen = set(bus_state.get("processed_merge_group_run_ids", []))
    non_mutating_seen: list[str] = []
    mutating_failures: list[tuple[str, str, dict[str, Any]]] = []
    changed = False

    for run in runs:
        if not isinstance(run, dict):
            continue
        run_id = run.get("id") or run.get("databaseId")
        if run_id is None:
            continue
        run_key = f"merge_group_run:{run_id}"
        if run_key in seen or str(run_id) in seen:
            continue

        conclusion = str(run.get("conclusion") or "").strip().lower()
        status_val = str(run.get("status") or "").strip().lower()
        queue_ref = str(run.get("head_branch") or run.get("headRef") or run.get("head_ref") or "").strip()
        head_sha = str(run.get("head_sha") or run.get("headSha") or "").strip()
        html_url = run.get("html_url") or run.get("url")

        # If run is not completed yet, wait for completion before marking processed
        if status_val in {"in_progress", "queued", "waiting", "requested", "pending"} and not conclusion:
            continue

        if conclusion in SUCCESS_CONCLUSIONS:
            # Success run: mark as processed, no failure side effects
            non_mutating_seen.append(run_key)
            continue

        if conclusion not in FAILURE_CONCLUSIONS:
            # Non-failure / unrecognized completed conclusion
            non_mutating_seen.append(run_key)
            continue

        # Failure processing
        pr_number = parse_merge_group_pr_number(queue_ref)
        if not pr_number:
            write_activity_log(config, {
                "type": "merge_group_failure_unparseable",
                "run_id": run_id,
                "queue_ref": queue_ref,
                "head_sha": head_sha,
                "conclusion": conclusion,
                "url": html_url,
                "message": f"Merge group run {run_id} failed on ref '{queue_ref}', but could not parse a valid PR number.",
            })
            non_mutating_seen.append(run_key)
            continue

        task, match_status = correlate_merge_group_task(status, pr_number, bus_state)
        if match_status == "ambiguous" or not task:
            write_activity_log(config, {
                "type": "merge_group_failure_ambiguous" if match_status == "ambiguous" else "merge_group_failure_unmatched",
                "run_id": run_id,
                "queue_ref": queue_ref,
                "head_sha": head_sha,
                "pr_number": pr_number,
                "conclusion": conclusion,
                "url": html_url,
                "message": f"Merge group run {run_id} failed for PR #{pr_number} ({queue_ref}), but task correlation was {match_status}.",
            })
            non_mutating_seen.append(run_key)
            continue

        task_id = str(task.get("id") or "")
        task_status = str(task.get("status") or "").lower()
        if task_status != "review_approved":
            write_activity_log(config, {
                "type": "merge_group_failure_stale",
                "task_id": task_id,
                "run_id": run_id,
                "queue_ref": queue_ref,
                "head_sha": head_sha,
                "pr_number": pr_number,
                "conclusion": conclusion,
                "url": html_url,
                "message": f"Merge group run {run_id} failed for PR #{pr_number} on {queue_ref}, but task {task_id} is in status '{task_status}' (expected 'review_approved').",
            })
            non_mutating_seen.append(run_key)
            continue

        existing_handoffs = status.get("handoffs", []) or []
        already_handed_off = any(
            h.get("task_id") == task_id
            and h.get("status") == "pending"
            and (
                str(h.get("run_id") or "") == str(run_id)
                or f"run {run_id}" in str(h.get("message") or "")
                or h.get("reason") == "merge_group_failure"
            )
            for h in existing_handoffs
        )
        if already_handed_off:
            non_mutating_seen.append(run_key)
            continue

        log_entry = {
            "type": "merge_group_failure_reconciled",
            "task_id": task_id,
            "run_id": run_id,
            "queue_ref": queue_ref,
            "head_sha": head_sha,
            "pr_number": pr_number,
            "conclusion": conclusion,
            "url": html_url,
            "message": (
                f"Merge group failure in run {run_id} on {queue_ref} "
                f"(head {head_sha[:8] if head_sha else 'unknown'}) correlated to PR #{pr_number} (task {task_id}). "
                f"Dispatching reviewer recovery handoff."
            ),
        }
        write_activity_log(config, log_entry)

        failure_record = {
            "run_id": run_id,
            "queue_ref": queue_ref,
            "head_sha": head_sha,
            "pr_number": pr_number,
            "conclusion": conclusion,
            "url": html_url,
            "reconciled_at": utc_now(),
        }

        reviewer = str(task.get("reviewer") or "").strip()
        owner = str(task.get("owner") or "").strip()
        handoff_to = reviewer or owner
        handoff_from = owner or "Supervisor"
        now_ts = utc_now()
        handoff_msg = (
            f"Merge group failed for PR #{pr_number} in run {run_id} (ref {queue_ref}, head {head_sha[:8] if head_sha else 'unknown'}). "
            f"Reviewer recovery handoff: inspect failure and coordinate remediation."
        )

        for h in status.get("handoffs", []):
            if h.get("task_id") == task_id and h.get("status") != "done":
                h["status"] = "done"
                h["resolved_at"] = now_ts

        handoff_entry = {
            "task_id": task_id,
            "from": handoff_from,
            "to": handoff_to,
            "message": handoff_msg,
            "status": "pending",
            "created_at": now_ts,
            "reason": "merge_group_failure",
            "run_id": run_id,
            "queue_ref": queue_ref,
            "head_sha": head_sha,
            "pr_number": pr_number,
        }
        status.setdefault("handoffs", []).append(handoff_entry)

        task["status"] = "review"
        task.pop("approved_head", None)
        task.pop("waiting_for", None)
        task["next"] = (
            f"Merge group run {run_id} failed on {queue_ref}; reviewer recovery handoff dispatched to {handoff_to}."
        )
        task["last_update"] = now_ts

        mutating_failures.append((run_key, task_id, failure_record))
        changed = True

    if changed:
        committed = commit_canonical_task_transition(config, status)
        if not committed:
            _record_seen_run_ids(bus_state, non_mutating_seen)
            return False

        _record_seen_run_ids(bus_state, non_mutating_seen + [item[0] for item in mutating_failures])
        for _, task_id, failure_record in mutating_failures:
            task_entry = bus_state.setdefault("tasks", {}).setdefault(task_id, {})
            task_entry["last_merge_group_failure"] = failure_record
        return True

    if non_mutating_seen:
        _record_seen_run_ids(bus_state, non_mutating_seen)

    return False


def poll_merge_group_runs(
    config: dict[str, Any],
    bus_state: dict[str, Any],
    status: dict[str, Any],
    repo: str,
) -> bool:
    """Poll GitHub merge_group runs and reconcile them."""
    limit = int((config.get("github_bus", {}) or {}).get("poll_batch_sizes", {}).get("merge_group_runs", 30))
    runs = fetch_merge_group_runs(repo, limit=limit)
    if not runs:
        return False
    return reconcile_merge_group_runs(config, bus_state, status, repo, runs)
