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
from common import ROOT, parse_utc_timestamp, run_command, utc_now, write_activity_log


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
    r"^(?:refs/heads/)?gh-readonly-queue/[^/]+/pr-(?P<pr>\d+)-[0-9a-fA-F]+$",
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
    """
    if not queue_ref or not isinstance(queue_ref, str):
        return None
    raw = queue_ref.strip()
    if not raw:
        return None
    match = MERGE_GROUP_QUEUE_REF_PATTERN.fullmatch(raw)
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


def is_valid_pr_facts(pr_facts: Any) -> bool:
    """Return true only if pr_facts is a non-empty dictionary containing valid state and head SHA."""
    if not isinstance(pr_facts, dict) or not pr_facts:
        return False
    state = pr_facts.get("state")
    if not state or not isinstance(state, str) or not state.strip():
        return False
    head_sha = (
        pr_facts.get("headRefOid")
        or pr_facts.get("head_sha")
        or (pr_facts.get("head") or {}).get("sha")
    )
    if not head_sha or not isinstance(head_sha, str) or not head_sha.strip():
        return False
    return True


def match_workflow_identity(target: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Match workflow identity strictly.

    Requires both runs to have either matching non-None workflow_ids or
    matching non-empty workflow names. If workflow identity is missing on
    either run, they do not match.
    """
    target_wf_id = target.get("workflow_id")
    cand_wf_id = candidate.get("workflow_id")
    if target_wf_id is not None and cand_wf_id is not None:
        try:
            return int(target_wf_id) == int(cand_wf_id)
        except (TypeError, ValueError):
            return str(target_wf_id).strip() == str(cand_wf_id).strip()

    target_name = str(target.get("name") or "").strip()
    cand_name = str(candidate.get("name") or "").strip()
    if target_name and cand_name:
        return target_name == cand_name

    return False


def verify_group_contains_head(repo: str, cand_head_sha: str, approved_head: str) -> bool:
    """Verify that a merge group head commit contains the full approved PR head SHA."""
    cand_sha = str(cand_head_sha or "").strip().lower()
    app_sha = str(approved_head or "").strip().lower()
    if not cand_sha or not app_sha:
        return False
    if cand_sha == app_sha:
        return True

    # 1. Try local git merge-base check if available
    try:
        proc = run_command(
            ["git", "merge-base", "--is-ancestor", app_sha, cand_sha],
            cwd=ROOT,
        )
        if proc.returncode == 0:
            return True
        if proc.returncode == 1:
            return False
        # returncode 128 indicates the ref/commit is unknown in local object db
    except Exception:
        pass

    # 2. Try GitHub compare API via gh_json
    try:
        from github_bus import gh_json
        data = gh_json(["api", f"repos/{repo}/compare/{app_sha}...{cand_sha}"])
        if isinstance(data, dict):
            status = str(data.get("status") or "").strip().lower()
            behind_by = data.get("behind_by")
            if status in {"ahead", "identical"} and (behind_by == 0 or behind_by is None):
                return True
    except Exception:
        pass

    return False


def find_superseding_merge_group_run(
    target_run: dict[str, Any],
    runs: list[dict[str, Any]],
    pr_number: int,
    approved_head: str,
    repo: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Find a newer pending or successful merge group run superseding target_run for the same PR and approved head."""
    target_run_id = target_run.get("id") or target_run.get("databaseId")
    target_created_at = target_run.get("created_at") or target_run.get("run_started_at")

    for candidate in runs:
        if not isinstance(candidate, dict):
            continue
        cand_id = candidate.get("id") or candidate.get("databaseId")
        if cand_id is None or cand_id == target_run_id:
            continue

        cand_ref = str(
            candidate.get("head_branch")
            or candidate.get("headRef")
            or candidate.get("head_ref")
            or ""
        ).strip()
        cand_pr = parse_merge_group_pr_number(cand_ref)
        if cand_pr != pr_number:
            continue

        cand_head_sha = str(
            candidate.get("head_sha")
            or candidate.get("headSha")
            or ""
        ).strip().lower()
        if not cand_head_sha:
            # Candidate lacks head_sha: cannot prove association or ancestry
            continue

        if not match_workflow_identity(target_run, candidate):
            continue

        cand_is_newer = False
        if target_run_id is not None and cand_id is not None:
            try:
                if int(cand_id) > int(target_run_id):
                    cand_is_newer = True
            except (TypeError, ValueError):
                pass

        if not cand_is_newer:
            cand_created_at = candidate.get("created_at") or candidate.get("run_started_at")
            if cand_created_at and target_created_at:
                try:
                    cand_dt = parse_utc_timestamp(cand_created_at)
                    target_dt = parse_utc_timestamp(target_created_at)
                    if cand_dt and target_dt and cand_dt > target_dt:
                        cand_is_newer = True
                except Exception:
                    pass

        if not cand_is_newer:
            continue

        cand_conclusion = str(candidate.get("conclusion") or "").strip().lower()
        cand_status = str(candidate.get("status") or "").strip().lower()

        is_pending = (
            cand_status in {"in_progress", "queued", "waiting", "requested", "pending"}
            and cand_conclusion not in FAILURE_CONCLUSIONS
        )
        is_success = cand_conclusion in SUCCESS_CONCLUSIONS

        if not (is_pending or is_success):
            continue

        if not verify_group_contains_head(repo, cand_head_sha, approved_head):
            continue

        reason = "pending_group" if is_pending else "successful_group"
        return candidate, reason

    return None, None


def fetch_pr_facts(repo: str, pr_number: int) -> dict[str, Any] | None:
    """Fetch fresh PR state and head commit SHA from GitHub via gh_json."""
    from github_bus import GitHubBusOffline, gh_json

    try:
        data = gh_json([
            "pr", "view", str(pr_number), "--repo", repo,
            "--json", "number,state,headRefOid,mergedAt,url,mergeStateStatus",
        ])
        if isinstance(data, dict):
            return data
    except GitHubBusOffline:
        raise
    except Exception:
        pass
    return None


def fetch_merge_group_runs(repo: str, limit: int = 30) -> list[dict[str, Any]]:
    """Fetch merge_group workflow runs from GitHub via gh_json."""
    from github_bus import GitHubBusOffline, gh_json

    try:
        data = gh_json(["api", f"repos/{repo}/actions/runs?event=merge_group&per_page={limit}"])
        if isinstance(data, dict):
            runs = data.get("workflow_runs", [])
            return runs if isinstance(runs, list) else []
        if isinstance(data, list):
            return data
    except GitHubBusOffline:
        raise
    except Exception:
        pass
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

        # Fetch fresh PR facts from GitHub to ensure PR is open and matches reviewed head
        pr_facts = fetch_pr_facts(repo, pr_number)
        if not is_valid_pr_facts(pr_facts):
            # API or freshness unresolved / empty facts: do NOT demote, do NOT permanently mark processed
            write_activity_log(config, {
                "type": "merge_group_failure_unresolved",
                "task_id": task_id,
                "run_id": run_id,
                "queue_ref": queue_ref,
                "head_sha": head_sha,
                "pr_number": pr_number,
                "conclusion": conclusion,
                "url": html_url,
                "message": (
                    f"Merge group run {run_id} ({conclusion}) on {queue_ref} correlated to PR #{pr_number} "
                    f"(task {task_id}), but valid fresh PR facts could not be resolved from GitHub API. "
                    f"Retaining for next poll cycle."
                ),
            })
            continue

        pr_state = str(pr_facts.get("state") or "").strip().upper()
        merged_at = pr_facts.get("mergedAt") or pr_facts.get("merged_at")
        is_merged = (pr_state == "MERGED") or bool(merged_at) or (pr_facts.get("merged") is True)

        if is_merged:
            write_activity_log(config, {
                "type": "merge_group_failure_stale",
                "task_id": task_id,
                "run_id": run_id,
                "queue_ref": queue_ref,
                "head_sha": head_sha,
                "pr_number": pr_number,
                "conclusion": conclusion,
                "url": html_url,
                "reason": "already_merged",
                "message": (
                    f"Merge group run {run_id} ({conclusion}) on {queue_ref} for PR #{pr_number} "
                    f"(task {task_id}) is stale: PR is already merged."
                ),
            })
            non_mutating_seen.append(run_key)
            continue

        if pr_state != "OPEN":
            write_activity_log(config, {
                "type": "merge_group_failure_stale",
                "task_id": task_id,
                "run_id": run_id,
                "queue_ref": queue_ref,
                "head_sha": head_sha,
                "pr_number": pr_number,
                "conclusion": conclusion,
                "url": html_url,
                "reason": f"pr_state_{pr_state.lower()}",
                "message": (
                    f"Merge group run {run_id} ({conclusion}) on {queue_ref} for PR #{pr_number} "
                    f"(task {task_id}) is stale: PR state is '{pr_state}'."
                ),
            })
            non_mutating_seen.append(run_key)
            continue

        pr_head_sha = str(
            pr_facts.get("headRefOid")
            or pr_facts.get("head_sha")
            or (pr_facts.get("head") or {}).get("sha")
            or ""
        ).strip().lower()
        approved_head = str(task.get("approved_head") or "").strip().lower()

        if not pr_head_sha or not approved_head:
            write_activity_log(config, {
                "type": "merge_group_failure_unresolved",
                "task_id": task_id,
                "run_id": run_id,
                "queue_ref": queue_ref,
                "head_sha": head_sha,
                "pr_number": pr_number,
                "conclusion": conclusion,
                "url": html_url,
                "message": (
                    f"Merge group run {run_id} ({conclusion}) on {queue_ref} correlated to PR #{pr_number} "
                    f"(task {task_id}), but approved_head or PR head SHA is missing. Retaining for next poll cycle."
                ),
            })
            continue

        if pr_head_sha != approved_head:
            write_activity_log(config, {
                "type": "merge_group_failure_unresolved",
                "task_id": task_id,
                "run_id": run_id,
                "queue_ref": queue_ref,
                "head_sha": head_sha,
                "pr_number": pr_number,
                "conclusion": conclusion,
                "url": html_url,
                "message": (
                    f"Merge group run {run_id} ({conclusion}) on {queue_ref} for PR #{pr_number} "
                    f"(task {task_id}) has PR head {pr_head_sha} mismatching approved head {approved_head}."
                ),
            })
            continue

        # Check if this failure is superseded by a newer merge group run in current batch
        superseded_run, superseded_reason = find_superseding_merge_group_run(
            run, runs, pr_number, approved_head, repo
        )

        if superseded_run is not None:
            cand_run_id = superseded_run.get("id") or superseded_run.get("databaseId")
            cand_status_str = str(superseded_run.get("status") or superseded_run.get("conclusion") or "")
            write_activity_log(config, {
                "type": "merge_group_failure_stale",
                "task_id": task_id,
                "run_id": run_id,
                "queue_ref": queue_ref,
                "head_sha": head_sha,
                "pr_number": pr_number,
                "conclusion": conclusion,
                "url": html_url,
                "superseded_by_run_id": cand_run_id,
                "superseded_by_status": cand_status_str,
                "reason": superseded_reason,
                "message": (
                    f"Merge group run {run_id} ({conclusion}) on {queue_ref} for PR #{pr_number} "
                    f"(task {task_id}) is stale: superseded by newer merge group run {cand_run_id} "
                    f"({cand_status_str}) for reviewed head {approved_head}."
                ),
            })
            non_mutating_seen.append(run_key)
            continue

        # Pre-CAS and pre-demote fresh live validation:
        # 1. Fresh PR facts B check
        pr_facts_b = fetch_pr_facts(repo, pr_number)
        if not is_valid_pr_facts(pr_facts_b):
            write_activity_log(config, {
                "type": "merge_group_failure_unresolved",
                "task_id": task_id,
                "run_id": run_id,
                "queue_ref": queue_ref,
                "head_sha": head_sha,
                "pr_number": pr_number,
                "conclusion": conclusion,
                "url": html_url,
                "message": (
                    f"Merge group run {run_id} ({conclusion}) on {queue_ref} correlated to PR #{pr_number} "
                    f"(task {task_id}), but pre-demote fresh PR facts B could not be validated. "
                    f"Retaining for next poll cycle."
                ),
            })
            continue

        pr_state_b = str(pr_facts_b.get("state") or "").strip().upper()
        merged_at_b = pr_facts_b.get("mergedAt") or pr_facts_b.get("merged_at")
        is_merged_b = (pr_state_b == "MERGED") or bool(merged_at_b) or (pr_facts_b.get("merged") is True)
        if is_merged_b:
            write_activity_log(config, {
                "type": "merge_group_failure_stale",
                "task_id": task_id,
                "run_id": run_id,
                "queue_ref": queue_ref,
                "head_sha": head_sha,
                "pr_number": pr_number,
                "conclusion": conclusion,
                "url": html_url,
                "reason": "already_merged",
                "message": (
                    f"Merge group run {run_id} ({conclusion}) on {queue_ref} for PR #{pr_number} "
                    f"(task {task_id}) is stale: PR was merged during reconciliation."
                ),
            })
            non_mutating_seen.append(run_key)
            continue

        if pr_state_b != "OPEN":
            write_activity_log(config, {
                "type": "merge_group_failure_stale",
                "task_id": task_id,
                "run_id": run_id,
                "queue_ref": queue_ref,
                "head_sha": head_sha,
                "pr_number": pr_number,
                "conclusion": conclusion,
                "url": html_url,
                "reason": f"pr_state_{pr_state_b.lower()}",
                "message": (
                    f"Merge group run {run_id} ({conclusion}) on {queue_ref} for PR #{pr_number} "
                    f"(task {task_id}) is stale: PR state transitioned to '{pr_state_b}'."
                ),
            })
            non_mutating_seen.append(run_key)
            continue

        pr_head_b = str(
            pr_facts_b.get("headRefOid")
            or pr_facts_b.get("head_sha")
            or (pr_facts_b.get("head") or {}).get("sha")
            or ""
        ).strip().lower()
        if pr_head_b != approved_head:
            write_activity_log(config, {
                "type": "merge_group_failure_unresolved",
                "task_id": task_id,
                "run_id": run_id,
                "queue_ref": queue_ref,
                "head_sha": head_sha,
                "pr_number": pr_number,
                "conclusion": conclusion,
                "url": html_url,
                "message": (
                    f"Merge group run {run_id} ({conclusion}) on {queue_ref} for PR #{pr_number} "
                    f"(task {task_id}) has PR head {pr_head_b} drifting from approved head {approved_head}."
                ),
            })
            continue

        # 2. Fresh merge queue enrollment check
        try:
            from github_bus import fetch_pr_merge_queue_status, is_in_merge_queue
            fresh_mq_status = fetch_pr_merge_queue_status(repo, pr_number)
            if is_in_merge_queue(fresh_mq_status):
                write_activity_log(config, {
                    "type": "merge_group_failure_stale",
                    "task_id": task_id,
                    "run_id": run_id,
                    "queue_ref": queue_ref,
                    "head_sha": head_sha,
                    "pr_number": pr_number,
                    "conclusion": conclusion,
                    "url": html_url,
                    "reason": "enrolled_in_merge_queue",
                    "message": (
                        f"Merge group run {run_id} ({conclusion}) on {queue_ref} for PR #{pr_number} "
                        f"(task {task_id}) is stale: PR is currently enrolled in merge queue."
                    ),
                })
                non_mutating_seen.append(run_key)
                continue
        except Exception:
            pass

        # 3. Fresh merge group runs snapshot check
        try:
            poll_cfg = (config.get("github_bus", {}) or {}).get("poll_batch_sizes", {})
            poll_limit = int(poll_cfg.get("merge_group_runs", 30))
            fresh_runs = fetch_merge_group_runs(repo, limit=poll_limit)
            fresh_superseded, fresh_reason = find_superseding_merge_group_run(
                run, fresh_runs, pr_number, approved_head, repo
            )
            if fresh_superseded is not None:
                cand_run_id = fresh_superseded.get("id") or fresh_superseded.get("databaseId")
                cand_status_str = str(fresh_superseded.get("status") or fresh_superseded.get("conclusion") or "")
                write_activity_log(config, {
                    "type": "merge_group_failure_stale",
                    "task_id": task_id,
                    "run_id": run_id,
                    "queue_ref": queue_ref,
                    "head_sha": head_sha,
                    "pr_number": pr_number,
                    "conclusion": conclusion,
                    "url": html_url,
                    "superseded_by_run_id": cand_run_id,
                    "superseded_by_status": cand_status_str,
                    "reason": fresh_reason,
                    "message": (
                        f"Merge group run {run_id} ({conclusion}) on {queue_ref} for PR #{pr_number} "
                        f"(task {task_id}) is stale: superseded by newer merge group run {cand_run_id} "
                        f"({cand_status_str}) in fresh runs snapshot for reviewed head {approved_head}."
                    ),
                })
                non_mutating_seen.append(run_key)
                continue
        except Exception:
            pass

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
        # A successful task-review-gate status may still be attached to the
        # exact merge-group head.  Recovery requires a fresh reviewer decision,
        # so remove the local receipt before the canonical transition and
        # overwrite GitHub's status with the pending review state below.
        task.pop("review_gate_sha", None)
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
        for _, task_id, _ in mutating_failures:
            recovered_task = next(
                (
                    candidate
                    for candidate in status.get("tasks", []) or []
                    if isinstance(candidate, dict) and str(candidate.get("id") or "") == task_id
                ),
                None,
            )
            if recovered_task is not None:
                # This must happen after the CAS-backed transition succeeds:
                # a stale snapshot must never emit a recovery gate for a task
                # state that was not committed.
                runtime_ai_status.emit_task_review_status_check(recovered_task, "review")
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
