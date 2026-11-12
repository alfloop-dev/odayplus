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
from common import parse_utc_timestamp, utc_now, write_activity_log


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

# One queue-ref rule, shared with the canonical status writer.
MERGE_GROUP_QUEUE_REF_PATTERN = runtime_ai_status.MERGE_GROUP_QUEUE_REF_PATTERN


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


parse_merge_group_pr_number = runtime_ai_status.parse_merge_group_pr_number


# Verdicts of the merge_group producer's binding checks.
LEGACY_CAUSE_PROVEN = "proven"
LEGACY_CAUSE_REFUTED = "refuted"
LEGACY_CAUSE_UNRESOLVED = "unresolved"
GITHUB_PULL_URL_PATTERN = re.compile(r"^https://github\.com/([^/\s]+/[^/\s]+)/pull/(\d+)$")


def merge_group_task_repository_verdict(
    config: dict[str, Any],
    task: dict[str, Any],
    repo: str,
    pr_number: Any,
) -> tuple[str, str]:
    """Is this canonical task's PR the bus repository's PR `pr_number`?

    The bus polls one repository, but the board holds tasks of every
    registered repository, and PR numbers and shared source SHAs repeat
    across them. A merge_group run, and a cause recorded from one, names
    only a queue ref and a PR number, so before the merge_group producer
    reads or writes anything on `repo` for a task -- the failure scan's
    demotion and the legacy cause enrichment alike -- the task itself must
    say it lives there: its repository through the registry's one
    task->repository answer, and its submitted PR URL (and the task's, when
    recorded) naming exactly that repository and PR. Returns
    ``(verdict, reason)``: `proven` when bound, `refuted` when the task
    names another repository or PR, `unresolved` when its repository or PR
    URL cannot be read. Only `proven` lets the producer go on.
    """
    from multi_repo_registry import normalized_repository_slug, repository_slug, task_repository_id

    bus_slug = normalized_repository_slug(repo)
    if not bus_slug:
        return LEGACY_CAUSE_UNRESOLVED, "bus_repository_unknown"
    repo_id = task_repository_id(config, task)
    task_slug = normalized_repository_slug(repository_slug(config, repo_id)) if repo_id else None
    if not task_slug:
        return LEGACY_CAUSE_UNRESOLVED, "task_repository_unknown"
    if task_slug != bus_slug:
        return LEGACY_CAUSE_REFUTED, "task_repository_mismatch"
    try:
        wanted_pr = int(pr_number)
    except (TypeError, ValueError):
        return LEGACY_CAUSE_UNRESOLVED, "pr_number_unusable"

    submission = task.get("review_submission")
    urls = [("submission", (submission or {}).get("pr_url") if isinstance(submission, dict) else None)]
    if task.get("pr_url") is not None:
        urls.append(("task", task.get("pr_url")))
    for label, raw in urls:
        url = str(raw or "").strip().rstrip("/") if isinstance(raw, str) else ""
        match = GITHUB_PULL_URL_PATTERN.fullmatch(url)
        if not match:
            return LEGACY_CAUSE_UNRESOLVED, f"{label}_pr_url_unusable"
        if normalized_repository_slug(match.group(1)) != bus_slug:
            return LEGACY_CAUSE_REFUTED, f"{label}_pr_url_repository_mismatch"
        if int(match.group(2)) != wanted_pr:
            return LEGACY_CAUSE_REFUTED, f"{label}_pr_url_number_mismatch"
    return LEGACY_CAUSE_PROVEN, "task_bound_to_bus_repository"


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


MERGE_GROUP_FAILURE_HANDOFF_REASON = runtime_ai_status.MERGE_GROUP_FAILURE_HANDOFF_REASON


def _record_seen_run_ids(bus_state: dict[str, Any], new_keys: list[str], max_ids: int = 2000) -> None:
    current = bus_state.setdefault("processed_merge_group_run_ids", [])
    seen = set(current)
    for k in new_keys:
        if k not in seen:
            current.append(k)
            seen.add(k)
    bus_state["processed_merge_group_run_ids"] = current[-max_ids:]


VALID_PR_STATES = frozenset({"OPEN", "CLOSED", "MERGED"})

PENDING_RUN_STATUSES = frozenset({"in_progress", "queued", "waiting", "requested", "pending"})


def pr_state(pr_facts: Any) -> str:
    """Return the PR state normalized to GitHub's own enum, or '' when unknown.

    Only the three states GitHub actually defines are accepted. Anything else --
    a truncated payload, an unexpected enum, an error shape -- is unknown, and an
    unknown state must never be read as "closed, therefore this failure is
    stale".
    """
    if not isinstance(pr_facts, dict):
        return ""
    state = pr_facts.get("state")
    if not isinstance(state, str):
        return ""
    normalized = state.strip().upper()
    return normalized if normalized in VALID_PR_STATES else ""


def pr_head_sha(pr_facts: Any) -> str:
    """Return the PR head commit SHA from a GitHub PR node, or '' when absent."""
    if not isinstance(pr_facts, dict):
        return ""
    head = pr_facts.get("head")
    nested = head.get("sha") if isinstance(head, dict) else None
    candidate = pr_facts.get("headRefOid") or pr_facts.get("head_sha") or nested
    if not isinstance(candidate, str):
        return ""
    return candidate.strip().lower()


def pr_is_merged(pr_facts: Any) -> bool:
    """Return true when GitHub reports the PR as merged."""
    if not isinstance(pr_facts, dict):
        return False
    if pr_state(pr_facts) == "MERGED":
        return True
    if pr_facts.get("merged") is True:
        return True
    return bool(pr_facts.get("mergedAt") or pr_facts.get("merged_at"))


def pr_queue_identity(pr_facts: Any) -> tuple[bool, str]:
    """Return (is_enrolled, enqueued_at) representing the PR's merge queue generation.

    Position in queue (e.g. 3 -> 2 -> 1) is normal progression, not a new queue
    generation. But `isInMergeQueue` changing or `enqueuedAt` changing indicates
    a new queue enrollment or re-enqueue between checks, which must trigger
    re-evaluation next cycle rather than demoting on an obsolete queue generation.
    """
    from github_bus import is_in_merge_queue

    if not isinstance(pr_facts, dict):
        return False, ""
    enrolled = is_in_merge_queue(pr_facts)
    entry = pr_facts.get("mergeQueueEntry")
    enqueued_at = ""
    if isinstance(entry, dict):
        enqueued_at = str(entry.get("enqueuedAt") or "").strip()
    return enrolled, enqueued_at


def merge_queue_entry_group_head(pr_facts: Any) -> tuple[str, bool]:
    """Return (group_head_sha, known) for the PR's current merge queue entry.

    `MergeQueueEntry.headCommit` is the group commit GitHub built for the
    entry. `("", True)` is a positive answer that there is no group commit to
    consider: the PR is not enrolled, or its entry has not been grouped yet
    (`headCommit: null`). `("", False)` means GitHub's answer cannot say which
    commit it is -- an enrolled PR without an entry, an entry without the
    field, or a malformed oid -- and must never be read as "nothing to revoke".

    "Not enrolled" is only ever GitHub's explicit answer: the reader's GraphQL
    node always carries `isInMergeQueue` and `mergeQueueEntry`, so a node
    missing either field, or with a flag that is not a boolean, did not answer
    the question and is unknown -- never an empty target list. An entry beside
    a flag that is not `true` contradicts itself and is unknown as well.
    """
    if not isinstance(pr_facts, dict):
        return "", False
    if "mergeQueueEntry" not in pr_facts:
        return "", False
    enrolled = pr_facts.get("isInMergeQueue")
    if not isinstance(enrolled, bool):
        return "", False
    entry = pr_facts["mergeQueueEntry"]
    if entry is None:
        return "", not enrolled
    if not enrolled or not isinstance(entry, dict) or "headCommit" not in entry:
        return "", False
    head_commit = entry.get("headCommit")
    if head_commit is None:
        return "", True
    oid = head_commit.get("oid") if isinstance(head_commit, dict) else None
    if not isinstance(oid, str) or not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", oid.strip()):
        return "", False
    return oid.strip().lower(), True


def is_valid_pr_facts(pr_facts: Any) -> bool:
    """Return true only when GitHub returned a PR node this guard may act on.

    A node is actionable only if it carries a state GitHub actually defines and a
    head SHA. Everything else is unknown; the caller keeps the failure for the
    next cycle rather than deciding on an unanswered question.
    """
    if not isinstance(pr_facts, dict) or not pr_facts:
        return False
    if not pr_state(pr_facts):
        return False
    return bool(pr_head_sha(pr_facts))


def fetch_pr_queue_facts(repo: str, pr_number: int) -> dict[str, Any] | None:
    """Read fresh PR state, head SHA and queue enrollment from the one bus reader.

    `github_bus.fetch_pr_merge_queue_status` is the single GraphQL reader for PR
    facts here. Returns None whenever GitHub's answer is missing or unusable.
    """
    from github_bus import GitHubBusOffline, fetch_pr_merge_queue_status

    try:
        facts = fetch_pr_merge_queue_status(repo, pr_number)
    except GitHubBusOffline:
        raise
    except Exception:
        return None
    return facts if is_valid_pr_facts(facts) else None


def match_workflow_identity(target: dict[str, Any], candidate: dict[str, Any]) -> bool | None:
    """Tri-state: do these two runs come from the same workflow?

    True / False / None (neither run carries an identity that can be compared,
    so the question is unanswered). None is not "different": a candidate whose
    workflow cannot be identified is an open question, and an open question
    must not clear the way to revoking an approval.
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

    return None


def compare_run_recency(target: dict[str, Any], candidate: dict[str, Any]) -> bool | None:
    """Tri-state: is `candidate` newer than `target`?

    True / False / None (neither run ids nor timestamps can be compared).
    """
    target_id = target.get("id") or target.get("databaseId")
    cand_id = candidate.get("id") or candidate.get("databaseId")
    if target_id is not None and cand_id is not None:
        try:
            return int(cand_id) > int(target_id)
        except (TypeError, ValueError):
            pass

    cand_created_at = candidate.get("created_at") or candidate.get("run_started_at")
    target_created_at = target.get("created_at") or target.get("run_started_at")
    if cand_created_at and target_created_at:
        try:
            cand_dt = parse_utc_timestamp(cand_created_at)
            target_dt = parse_utc_timestamp(target_created_at)
            if cand_dt and target_dt:
                return cand_dt > target_dt
        except Exception:
            pass

    return None


def fetch_commit_parent_shas(repo: str, sha: str) -> list[str] | None:
    """Read the parent SHAs of a commit, or None when GitHub cannot answer.

    Returns a list (possibly empty) when GitHub answered, None when it did not.
    """
    from github_bus import GitHubBusOffline, gh_json

    sha = str(sha or "").strip()
    if not sha:
        return None
    try:
        data = gh_json(["api", f"repos/{repo}/commits/{sha}"])
    except GitHubBusOffline:
        raise
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    parents = data.get("parents")
    if not isinstance(parents, list) or not parents:
        return None
    shas: list[str] = []
    for parent in parents:
        parent_sha = parent.get("sha") if isinstance(parent, dict) else None
        if not isinstance(parent_sha, str) or not parent_sha.strip():
            # A parent element GitHub could not populate. `[{}]` is a broken
            # payload, not a commit with no parents; reading it as an answer
            # would turn a damaged response into "this group excludes the head".
            return None
        shas.append(parent_sha.strip().lower())
    return shas


def merge_group_incorporates_head(
    repo: str,
    group_head_sha: str,
    approved_head: str,
    parents_cache: dict[str, list[str] | None] | None = None,
) -> bool | None:
    """Tri-state: did this merge group enrol exactly `approved_head`?

    True / False / None (unknown, GitHub did not answer).

    A merge queue group head is the temporary merge commit GitHub builds for the
    group. Its FIRST parent is the base the group was built on; the remaining
    parents are the exact PR heads it enrolled. For PR #1212 the group head
    `17393dd4` has parents `a297b2a0` (base) and `a9e7853f` (the reviewed PR
    head); the next group head `62dfc845` in turn carries `17393dd4` as its own
    base parent. Only a non-base parent is evidence that this group enrolled
    the head -- matching the base parent would credit a group merely built on
    top of an already-merged head to the PR that produced it.

    Ancestry is weaker still. If a PR head advances A -> B, A is an ancestor of
    any group built for B, so an ancestry test would report that a group for B
    covers A and let a failure against A be waved through by an unrelated run.
    Only direct, non-base parenthood distinguishes the two.
    """
    group_sha = str(group_head_sha or "").strip().lower()
    approved_sha = str(approved_head or "").strip().lower()
    if not group_sha or not approved_sha:
        return False
    if group_sha == approved_sha:
        # A group head is a merge commit built on top of the queue base; it is
        # never the PR head itself. Accepting equality as proof would let a
        # fixture assert an association GitHub cannot produce.
        return False

    cache = parents_cache if parents_cache is not None else {}
    if group_sha not in cache:
        cache[group_sha] = fetch_commit_parent_shas(repo, group_sha)
    parents = cache[group_sha]
    if parents is None:
        return None
    if len(parents) < 2:
        # Not the merge-commit shape a merge group head has. Whatever this
        # commit is, its structure cannot answer the question.
        return None
    return approved_sha in parents[1:]


def find_superseding_merge_group_run(
    target_run: dict[str, Any],
    runs: list[dict[str, Any]],
    pr_number: int,
    approved_head: str,
    repo: str,
    parents_cache: dict[str, list[str] | None] | None = None,
) -> tuple[dict[str, Any] | None, str | None, bool]:
    """Find a newer merge group run that enrolled the same approved PR head.

    Returns `(run, reason, unknown)`. `unknown` is True when a candidate that
    could otherwise have superseded this failure -- same PR, newer, pending or
    successful -- could be neither proven nor disproven, because it is missing
    its head SHA, its workflow cannot be identified, its ordering cannot be
    established, or GitHub returned a parent payload that does not answer.
    Those are open questions, not "no candidate", and the caller must retain
    the failure rather than demote on them.
    """
    target_head_sha = str(
        target_run.get("head_sha")
        or target_run.get("headSha")
        or ""
    ).strip().lower()
    target_run_id = target_run.get("id") or target_run.get("databaseId")
    cache = parents_cache if parents_cache is not None else {}
    association_unknown = False

    for candidate in runs:
        if not isinstance(candidate, dict):
            continue
        cand_id = candidate.get("id") or candidate.get("databaseId")
        if cand_id is None or cand_id == target_run_id:
            # The failing run itself. Its own freshness is decided separately,
            # by re-reading the run rather than by treating it as its own
            # replacement.
            continue

        cand_ref = str(
            candidate.get("head_branch")
            or candidate.get("headRef")
            or candidate.get("head_ref")
            or ""
        ).strip()
        if parse_merge_group_pr_number(cand_ref) != pr_number:
            # A different PR's group, or a ref that names no PR at all.
            continue

        cand_conclusion = str(candidate.get("conclusion") or "").strip().lower()
        cand_status = str(candidate.get("status") or "").strip().lower()
        is_pending = (
            cand_status in PENDING_RUN_STATUSES
            and cand_conclusion not in FAILURE_CONCLUSIONS
        )
        is_success = cand_conclusion in SUCCESS_CONCLUSIONS
        if not (is_pending or is_success):
            # A candidate that failed too is not evidence either way.
            continue

        # From here the candidate is a same-PR run that could supersede, so an
        # unanswerable field is an open question rather than a quiet "no".
        is_newer = compare_run_recency(target_run, candidate)
        if is_newer is None:
            association_unknown = True
            continue
        if not is_newer:
            continue

        cand_head_sha = str(
            candidate.get("head_sha")
            or candidate.get("headSha")
            or ""
        ).strip().lower()
        if not cand_head_sha:
            association_unknown = True
            continue
        if target_head_sha and cand_head_sha == target_head_sha:
            # Same group commit, different run: a sibling workflow or a re-run
            # of the group that is failing. It cannot supersede itself.
            continue

        same_workflow = match_workflow_identity(target_run, candidate)
        if same_workflow is None:
            association_unknown = True
            continue
        if not same_workflow:
            continue

        incorporates = merge_group_incorporates_head(
            repo, cand_head_sha, approved_head, cache
        )
        if incorporates is None:
            association_unknown = True
            continue
        if not incorporates:
            continue

        reason = "pending_group" if is_pending else "successful_group"
        return candidate, reason, False

    return None, None, association_unknown


def find_run_by_id(runs: Any, run_id: Any) -> dict[str, Any] | None:
    """Locate a run in a snapshot by id."""
    if not isinstance(runs, list):
        return None
    wanted = str(run_id)
    for item in runs:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id") or item.get("databaseId")
        if item_id is not None and str(item_id) == wanted:
            return item
    return None


def fetch_workflow_run(repo: str, run_id: Any) -> dict[str, Any] | None:
    """Read one workflow run, or None when GitHub could not answer."""
    from github_bus import GitHubBusOffline, gh_json

    try:
        data = gh_json(["api", f"repos/{repo}/actions/runs/{run_id}"])
    except GitHubBusOffline:
        raise
    except Exception:
        return None
    return data if isinstance(data, dict) else None


TARGET_CURRENT_FAILURE = "current_failure"
TARGET_RETRY_SUCCEEDED = "retry_succeeded"
TARGET_RETRYING = "retrying"
TARGET_UNKNOWN = "unknown"


def classify_fresh_target_run(
    repo: str, run_id: Any, fresh_runs: Any
) -> tuple[str, dict[str, Any] | None]:
    """Re-read the failing run itself: is it still a completed failure now?

    The batch that produced this run is a snapshot. GitHub re-runs a merge group
    workflow under the same run id, so a record that read `failure` when polled
    may already be `in_progress` or `success`. The candidate scan cannot catch
    that -- it skips the target's own id -- so the target is re-read here.
    Revoking an approval over a failure that no longer exists is exactly the
    fault this guard is for.
    """
    fresh = find_run_by_id(fresh_runs, run_id)
    if fresh is None:
        fresh = fetch_workflow_run(repo, run_id)
    if not isinstance(fresh, dict) or not fresh:
        return TARGET_UNKNOWN, None

    conclusion = str(fresh.get("conclusion") or "").strip().lower()
    status = str(fresh.get("status") or "").strip().lower()
    if status == "completed":
        if conclusion in FAILURE_CONCLUSIONS:
            return TARGET_CURRENT_FAILURE, fresh
        if conclusion in SUCCESS_CONCLUSIONS:
            return TARGET_RETRY_SUCCEEDED, fresh
        return TARGET_UNKNOWN, fresh
    if status in PENDING_RUN_STATUSES:
        if not conclusion:
            return TARGET_RETRYING, fresh
        return TARGET_UNKNOWN, fresh
    return TARGET_UNKNOWN, fresh


def merge_run_snapshots(*snapshots: Any) -> list[dict[str, Any]]:
    """Union run snapshots by run id, letting later snapshots win."""
    merged: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        if not isinstance(snapshot, list):
            continue
        for item in snapshot:
            if not isinstance(item, dict):
                continue
            key = str(item.get("id") or item.get("databaseId") or "").strip()
            if not key:
                continue
            merged[key] = item
    return list(merged.values())


def fetch_merge_group_runs(repo: str, limit: int = 30) -> list[dict[str, Any]] | None:
    """Fetch merge_group workflow runs, or None when GitHub could not answer.

    None and `[]` are different answers: `[]` means GitHub reported no runs, None
    means the question is unanswered. Collapsing the two would let a transport
    error read as "no newer group exists" and revoke an approval on it.
    """
    from github_bus import GitHubBusOffline, gh_json

    try:
        data = gh_json(["api", f"repos/{repo}/actions/runs?event=merge_group&per_page={limit}"])
    except GitHubBusOffline:
        raise
    except Exception:
        return None
    if isinstance(data, dict):
        runs = data.get("workflow_runs")
        return runs if isinstance(runs, list) else None
    if isinstance(data, list):
        return data
    return None


def merge_group_audit_entry(
    event_type: str,
    run_facts: dict[str, Any],
    reason: str,
    message: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the audit entry shape shared by every merge_group outcome."""
    entry = dict(run_facts)
    entry["type"] = event_type
    entry["reason"] = reason
    entry["message"] = message
    if extra:
        entry.update(extra)
    return entry


def record_review_gate_revocation_intent(
    task: dict[str, Any], repo: str, group_heads: list[str]
) -> None:
    """Write the review-gate revocation intent of a merge_group recovery.

    Confirmed grants move into the existing `review_gate_revocations`, each
    group commit proven to enrol the reviewed head joins them, and
    `review_gate_refresh_pending` makes the canonical `ai_status sync` that
    commits the transition post them and persist the outcome (ACK, or the
    exact payload in `status_check_outbox` for retry). The caller commits it
    through the canonical CAS; nothing is posted from here.
    """
    runtime_ai_status.retain_review_gate_targets(task)
    revocations = {
        runtime_ai_status.status_check_target(target): target
        for target in task.get("review_gate_revocations", []) or []
        if isinstance(target, dict)
    }
    for group_head in group_heads:
        group_target = {"repo_slug": repo, "sha": group_head, "context": "task-review-gate"}
        revocations.setdefault(runtime_ai_status.status_check_target(group_target), group_target)
    if revocations:
        task["review_gate_revocations"] = list(revocations.values())
    task["review_gate_refresh_pending"] = True


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
    from github_bus import is_in_merge_queue
    from status_transition import commit_canonical_task_transition

    if not isinstance(runs, list) or not runs:
        return False

    seen = set(bus_state.get("processed_merge_group_run_ids", []))
    non_mutating_seen: list[str] = []
    mutating_failures: list[tuple[str, str, dict[str, Any]]] = []
    changed = False

    # Fetched at most once per invocation and shared by every failure in the
    # batch: one extra call closes the gap between the poll snapshot and the
    # demote decision without re-asking GitHub the same question per run.
    runs_snapshot_cache: dict[str, list[dict[str, Any]] | None] = {}
    parents_cache: dict[str, list[str] | None] = {}
    poll_limit = int(
        (config.get("github_bus", {}) or {}).get("poll_batch_sizes", {}).get("merge_group_runs", 30)
    )

    def fresh_merge_group_runs() -> list[dict[str, Any]] | None:
        if "runs" not in runs_snapshot_cache:
            runs_snapshot_cache["runs"] = fetch_merge_group_runs(repo, limit=poll_limit)
        return runs_snapshot_cache["runs"]

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
        if status_val in PENDING_RUN_STATUSES and not conclusion:
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

        # One fresh, pre-CAS read of the facts that decide whether this failure is
        # still live. Every branch below either proves the failure stale (audit +
        # dedupe, no mutation), proves it genuine (falls through to the single
        # reviewer-recovery path), or leaves the question open. An open question
        # must never revoke an approval, so it is retained un-processed and asked
        # again next cycle.
        run_facts = {
            "task_id": task_id,
            "run_id": run_id,
            "queue_ref": queue_ref,
            "head_sha": head_sha,
            "pr_number": pr_number,
            "conclusion": conclusion,
            "url": html_url,
        }
        run_label = (
            f"Merge group run {run_id} ({conclusion}) on {queue_ref} for PR #{pr_number} (task {task_id})"
        )

        def record_unresolved(reason: str, detail: str, _facts=run_facts, _label=run_label) -> None:
            write_activity_log(config, merge_group_audit_entry(
                "merge_group_failure_unresolved",
                _facts,
                reason,
                f"{_label}: {detail} Retaining un-processed for the next poll cycle.",
            ))

        def record_stale(
            reason: str,
            detail: str,
            extra: dict[str, Any] | None = None,
            _facts=run_facts,
            _label=run_label,
        ) -> None:
            write_activity_log(config, merge_group_audit_entry(
                "merge_group_failure_stale",
                _facts,
                reason,
                f"{_label} is stale: {detail}",
                extra,
            ))

        # Correlation matched a PR number on the whole multi-repository board;
        # the task must be this repository's PR before anything is read from
        # or written to it. A task proven to be another repository's (or
        # another PR's) is not this run's; one that cannot be bound is asked
        # again next cycle, against whatever the canonical record says then.
        binding, binding_reason = merge_group_task_repository_verdict(config, task, repo, pr_number)
        if binding == LEGACY_CAUSE_REFUTED:
            record_stale(
                binding_reason,
                "the correlated task's repository or submitted PR is not this repository's PR.",
            )
            non_mutating_seen.append(run_key)
            continue
        if binding != LEGACY_CAUSE_PROVEN:
            record_unresolved(
                binding_reason,
                "the correlated task's repository or submitted PR URL cannot be bound to this repository.",
            )
            continue

        approved_head = str(task.get("approved_head") or "").strip().lower()
        if not approved_head:
            record_unresolved(
                "approved_head_missing",
                "the task carries no approved head to correlate against.",
            )
            continue

        def check_pr_facts(
            label: str,
            drift_reason: str,
            _pr_number=pr_number,
            _approved_head=approved_head,
            _seen=None,
        ) -> str:
            """Read PR facts through the one reader and classify the outcome.

            Returns "open", "stale", "unresolved". `stale` and `unresolved`
            have already written their audit entry.
            """
            facts = fetch_pr_queue_facts(repo, _pr_number)
            if facts is None:
                record_unresolved(
                    f"pr_facts_{label}_unavailable",
                    f"GitHub returned no usable PR state and head SHA ({label}).",
                )
                return "unresolved"
            if pr_is_merged(facts):
                record_stale("already_merged", f"the PR is already merged ({label}).")
                return "stale"
            # `fetch_pr_queue_facts` already rejected every state that is not one
            # of GitHub's three, so only a genuine CLOSED reaches the closed-stale
            # path; an unrecognised state string went to `unavailable` above.
            state = pr_state(facts)
            if state != "OPEN":
                record_stale(
                    f"pr_state_{state.lower()}",
                    f"PR state is '{state}' ({label}).",
                )
                return "stale"
            head = pr_head_sha(facts)
            if head != _approved_head:
                record_unresolved(
                    drift_reason,
                    f"PR head {head} does not equal approved head {_approved_head} ({label}).",
                )
                return "unresolved"
            if _seen is not None:
                _seen[label] = facts
            return "open"

        # Facts A: the PR must be open on the reviewed head before spending any
        # further calls on this failure.
        pr_facts_seen: dict[str, dict[str, Any]] = {}
        outcome = check_pr_facts("A", "pr_head_mismatch", _seen=pr_facts_seen)
        if outcome == "stale":
            non_mutating_seen.append(run_key)
            continue
        if outcome != "open":
            continue

        fresh_runs = fresh_merge_group_runs()
        if fresh_runs is None:
            record_unresolved(
                "merge_group_runs_unavailable",
                "GitHub returned no usable merge_group run snapshot, so a newer group "
                "can be neither found nor ruled out.",
            )
            continue

        # The failing run is re-read before anything is concluded from it: a
        # re-run under the same id may already have moved off `failure`, and the
        # candidate scan cannot see that because it skips the target's own id.
        target_kind, fresh_target = classify_fresh_target_run(repo, run_id, fresh_runs)

        superseding_run: dict[str, Any] | None = None
        supersede_reason: str | None = None
        association_unknown = False
        target_group_head = ""
        if target_kind == TARGET_CURRENT_FAILURE:
            effective_target = fresh_target if isinstance(fresh_target, dict) and fresh_target else run
            target_group_head = str(
                effective_target.get("head_sha") or effective_target.get("headSha") or ""
            ).strip().lower()
            if not target_group_head:
                record_unresolved(
                    "target_identity_incomplete",
                    "the failing merge group run's head SHA is missing or unusable.",
                )
                continue

            target_incorporates = merge_group_incorporates_head(
                repo, target_group_head, approved_head, parents_cache
            )
            if target_incorporates is None:
                record_unresolved(
                    "target_association_unknown",
                    "the failing merge group run could not be proven to enrol or "
                    "exclude the reviewed head.",
                )
                continue
            if not target_incorporates:
                # Confirmed not to enrol approved_head (e.g. delayed failure for an
                # earlier head before approval). Stale, so dedupe without task mutation.
                record_stale(
                    "target_not_for_approved_head",
                    (
                        f"the failing merge group run (head {target_group_head[:8]}) did not "
                        f"enrol reviewed head {approved_head}."
                    ),
                    {"in_merge_queue": is_in_merge_queue(pr_facts_seen.get("A"))},
                )
                non_mutating_seen.append(run_key)
                continue

            superseding_run, supersede_reason, association_unknown = find_superseding_merge_group_run(
                effective_target,
                merge_run_snapshots(runs, fresh_runs),
                pr_number,
                approved_head,
                repo,
                parents_cache,
            )

        # Facts B: re-read after the runs and commit-parent calls, before any
        # mutation and before any run is permanently marked processed. Those
        # calls take real time, and the PR can merge, close or advance its head
        # underneath them.
        outcome = check_pr_facts("B", "pr_head_drift", _seen=pr_facts_seen)
        if outcome == "stale":
            non_mutating_seen.append(run_key)
            continue
        if outcome != "open":
            continue

        facts_a = pr_facts_seen.get("A")
        facts_b = pr_facts_seen.get("B")
        if facts_a and facts_b and pr_queue_identity(facts_a) != pr_queue_identity(facts_b):
            record_unresolved(
                "pr_queue_drift",
                "PR merge queue enrollment or generation changed between check A and check B.",
            )
            continue

        # Queue enrollment is corroboration, never proof. GitHub reports that the
        # PR is in *a* group, not which one, so an enrolled PR may be sitting in
        # the very group that just failed. It is recorded for the audit trail and
        # is not on its own allowed to suppress a failure -- only a named newer
        # group that provably enrolled this exact head can do that.
        enrolled_in_queue = is_in_merge_queue(pr_facts_seen.get("B"))

        if target_kind == TARGET_UNKNOWN:
            record_unresolved(
                "target_run_unresolved",
                "the failing run's current state could not be re-read from GitHub.",
            )
            continue

        if target_kind == TARGET_RETRYING:
            record_unresolved(
                "target_run_retrying",
                "the failing run is running again and has no current conclusion.",
            )
            continue

        if target_kind == TARGET_RETRY_SUCCEEDED:
            record_stale(
                "target_run_succeeded_on_retry",
                "the run has since completed successfully under the same run id.",
                {"in_merge_queue": enrolled_in_queue},
            )
            non_mutating_seen.append(run_key)
            continue

        if superseding_run is not None:
            superseding_id = superseding_run.get("id") or superseding_run.get("databaseId")
            superseding_state = str(
                superseding_run.get("status") or superseding_run.get("conclusion") or ""
            )
            record_stale(
                supersede_reason or "superseded",
                (
                    f"superseded by newer merge group run {superseding_id} ({superseding_state}), "
                    f"whose group commit enrolled reviewed head {approved_head}."
                ),
                {
                    "superseded_by_run_id": superseding_id,
                    "superseded_by_status": superseding_state,
                    "in_merge_queue": enrolled_in_queue,
                },
            )
            non_mutating_seen.append(run_key)
            continue

        if association_unknown:
            record_unresolved(
                "group_association_unknown",
                "a newer merge group run for this PR could not be proven to enrol or "
                "exclude the reviewed head.",
            )
            continue

        # Every group commit proven to enrol the reviewed head may carry the
        # success `merge-queue-review-gate.yml` re-asserted from the PR head.
        # Revoking only the PR head would leave those standing. The failing
        # group was proven above; the PR's current queue entry is read from the
        # same facts-B node and must be proven the same way before it is
        # touched, and an entry that cannot be classified leaves the failure
        # open rather than revoking an unknown target or skipping a real one.
        group_targets = [target_group_head] if target_group_head else []
        active_group_head, active_known = merge_queue_entry_group_head(facts_b)
        if not active_known:
            record_unresolved(
                "active_group_identity_unknown",
                "the PR's current merge queue entry does not name a usable group commit.",
            )
            continue
        if active_group_head and active_group_head not in group_targets:
            active_incorporates = merge_group_incorporates_head(
                repo, active_group_head, approved_head, parents_cache
            )
            if active_incorporates is None:
                record_unresolved(
                    "active_group_association_unknown",
                    "the PR's current merge queue group could not be proven to enrol or "
                    "exclude the reviewed head.",
                )
                continue
            if active_incorporates:
                group_targets.append(active_group_head)

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
            "reason": MERGE_GROUP_FAILURE_HANDOFF_REASON,
            "run_id": run_id,
            "queue_ref": queue_ref,
            "head_sha": head_sha,
            "pr_number": pr_number,
            # The reviewed PR head the failing group was proven to enrol. The
            # ready dispatcher accepts this cause only for exactly this head.
            "source_head": approved_head,
        }
        status.setdefault("handoffs", []).append(handoff_entry)

        task["status"] = "review"
        task.pop("approved_head", None)
        # A successful task-review-gate status may still be attached to the PR
        # head and to every group commit that enrolled it.  Recovery requires a
        # fresh reviewer decision, so the revocation intent is written into the
        # same canonical transition: confirmed grants move into the existing
        # `review_gate_revocations`, each proven group commit joins them, and
        # `review_gate_refresh_pending` makes the canonical `ai_status sync`
        # that commits this transition post them and persist the outcome
        # (ACK, or the exact payload in `status_check_outbox` for retry).
        record_review_gate_revocation_intent(task, repo, group_targets)
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
        # No emission from this snapshot. The committed transition carries the
        # revocation intent, and the canonical `ai_status sync` run by the
        # commit is the emitter whose outbox/ACK result is persisted; anything
        # emitted here would be discarded by the caller's immediate reload.
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
    # At most one mutating transition per cycle: an enrichment that wrote (or
    # was refused) leaves `status` carrying its edit, so the failure scan
    # waits for the next cycle's fresh canonical read.
    enriched = enrich_legacy_merge_group_causes(config, status, repo)
    if enriched is not None:
        return enriched
    limit = int((config.get("github_bus", {}) or {}).get("poll_batch_sizes", {}).get("merge_group_runs", 30))
    runs = fetch_merge_group_runs(repo, limit=limit)
    if not runs:
        return False
    return reconcile_merge_group_runs(config, bus_state, status, repo, runs)


def legacy_merge_group_cause_source_verdict(
    repo: str,
    cause: dict[str, Any],
    source_head: str,
    parents_cache: dict[str, list[str] | None] | None = None,
) -> tuple[str, str]:
    """Does GitHub prove this committed cause failed a group enrolling `source_head`?

    Reads the exact run the cause names and the parents of its group commit.
    Returns ``(verdict, reason)``: `proven` only when the run is the same
    repository's `merge_group` run on the recorded queue ref and group head,
    is still a completed failure, and the group commit carries `source_head`
    as a non-base parent. `refuted` is reserved for immutable facts GitHub
    answered differently; anything missing, re-running or unanswerable is
    `unresolved`.
    """
    run_id = cause.get("run_id")
    group_head = str(cause.get("head_sha") or "").strip().lower()
    queue_ref = str(cause.get("queue_ref") or "").strip()
    run = fetch_workflow_run(repo, run_id)
    if not isinstance(run, dict) or not run:
        return LEGACY_CAUSE_UNRESOLVED, "run_unavailable"

    repository = run.get("repository")
    observed = {
        "run_id": str(run.get("id") or run.get("databaseId") or "").strip(),
        "repository": str(repository.get("full_name") or "").strip().lower()
        if isinstance(repository, dict)
        else "",
        "event": str(run.get("event") or "").strip().lower(),
        "queue_ref": str(run.get("head_branch") or run.get("headBranch") or "").strip(),
        "group_head": str(run.get("head_sha") or run.get("headSha") or "").strip().lower(),
    }
    expected = {
        "run_id": str(run_id).strip(),
        "repository": str(repo or "").strip().lower(),
        "event": "merge_group",
        "queue_ref": queue_ref,
        "group_head": group_head,
    }
    for field, wanted in expected.items():
        if not observed[field]:
            return LEGACY_CAUSE_UNRESOLVED, f"run_{field}_missing"
        if observed[field] != wanted:
            return LEGACY_CAUSE_REFUTED, f"run_{field}_mismatch"

    run_status = str(run.get("status") or "").strip().lower()
    conclusion = str(run.get("conclusion") or "").strip().lower()
    if run_status != "completed" or conclusion not in FAILURE_CONCLUSIONS:
        # A re-run under the same id may be running or have succeeded; the
        # cause is not proven, and the next re-run may fail again.
        return LEGACY_CAUSE_UNRESOLVED, "run_not_a_completed_failure"

    incorporates = merge_group_incorporates_head(repo, group_head, source_head, parents_cache)
    if incorporates is None:
        return LEGACY_CAUSE_UNRESOLVED, "group_association_unknown"
    if not incorporates:
        return LEGACY_CAUSE_REFUTED, "group_did_not_enrol_source_head"
    return LEGACY_CAUSE_PROVEN, "group_enrolled_source_head"


def legacy_cause_group_revocation_targets(
    repo: str,
    cause: dict[str, Any],
    source_head: str,
    parents_cache: dict[str, list[str] | None] | None = None,
) -> tuple[list[str], str, str]:
    """Every group commit a proven legacy cause must revoke, or why not.

    The cause's own group was proven to enrol `source_head` by
    `legacy_merge_group_cause_source_verdict`. The PR's current queue entry
    is read from the one PR reader, on the exact PR and source head, and its
    group commit is revoked only when proven to enrol the source as well.
    Returns ``(group_heads, verdict, reason)``; anything GitHub cannot answer
    is `unresolved` and writes nothing.
    """
    group_head = str(cause.get("head_sha") or "").strip().lower()
    try:
        pr_number = int(cause.get("pr_number"))
    except (TypeError, ValueError):
        return [], LEGACY_CAUSE_UNRESOLVED, "pr_number_unusable"
    facts = fetch_pr_queue_facts(repo, pr_number)
    if facts is None:
        return [], LEGACY_CAUSE_UNRESOLVED, "pr_facts_unavailable"
    try:
        facts_number = int(facts.get("number"))
    except (TypeError, ValueError):
        return [], LEGACY_CAUSE_UNRESOLVED, "pr_number_missing"
    if facts_number != pr_number:
        return [], LEGACY_CAUSE_UNRESOLVED, "pr_number_mismatch"
    if pr_head_sha(facts) != source_head:
        return [], LEGACY_CAUSE_UNRESOLVED, "pr_head_mismatch"
    group_heads = [group_head]
    active_group_head, active_known = merge_queue_entry_group_head(facts)
    if not active_known:
        return [], LEGACY_CAUSE_UNRESOLVED, "active_group_identity_unknown"
    if active_group_head and active_group_head not in group_heads:
        incorporates = merge_group_incorporates_head(
            repo, active_group_head, source_head, parents_cache
        )
        if incorporates is None:
            return [], LEGACY_CAUSE_UNRESOLVED, "active_group_association_unknown"
        if incorporates:
            group_heads.append(active_group_head)
    return group_heads, LEGACY_CAUSE_PROVEN, "group_enrolled_source_head"


def enrich_legacy_merge_group_causes(
    config: dict[str, Any],
    status: dict[str, Any],
    repo: str,
) -> bool | None:
    """Record the proven source relation on a cause committed before it existed.

    A recovery cause written before `source_head` was part of the record
    cannot license a reviewer dispatch: the ready dispatcher requires the
    cause to name exactly the routed source head. This is the recovery
    producer completing its own committed record, not a new cause: it only
    touches a cause `ai_status.merge_group_recovery_cause` binds to the
    canonical task as read this cycle, of a task whose registered repository
    and submitted PR URL are this bus's repository and the cause's PR
    (`merge_group_task_repository_verdict`), re-reads the exact run and group
    commit it names, and writes `source_head` through the same canonical CAS
    only when GitHub proves the relation. Nothing is remembered between
    cycles: a refuted or unresolved relation is audited like any unresolved
    merge_group failure and the cause is read and asked again next cycle,
    against whatever the canonical record says then.

    The same CAS carries the review-gate revocation the legacy producer
    never wrote: the cause's proven group and the PR's current queue group,
    when proven to enrol the source, join `review_gate_revocations` through
    the producer's own `record_review_gate_revocation_intent`, and the
    canonical sync that commits it posts them and persists ACK or outbox.
    The route and every other handoff are left unchanged.

    Returns None when nothing was written, otherwise whether the canonical
    CAS accepted the write.
    """
    from status_transition import commit_canonical_task_transition

    schema = config.get("schema", {}) or {}
    task_id_field = schema.get("task_id_field", "id")
    handoffs = status.get("handoffs")
    parents_cache: dict[str, list[str] | None] = {}
    proven: list[dict[str, Any]] = []

    for task in status.get(schema.get("tasks_path", "tasks"), []) or []:
        if not isinstance(task, dict):
            continue
        # Only the producer's own review-phase cause; the owner publication
        # phase reads a cause that already proves its source.
        if str(task.get("status") or "").strip().lower() != "review" or task.get("approved_head"):
            continue
        cause = runtime_ai_status.merge_group_recovery_cause(task, handoffs, require_source_head=False)
        if cause is None or "source_head" in cause:
            continue
        source_head = str((task.get("review_submission") or {}).get("remote_sha") or "").strip().lower()
        task_id = str(task.get(task_id_field) or task.get("id") or "").strip()

        # The cause is bound to the task; the task has to be bound to the
        # repository this bus reads before any of its runs or commits are.
        verdict, reason = merge_group_task_repository_verdict(config, task, repo, cause.get("pr_number"))
        if verdict == LEGACY_CAUSE_PROVEN:
            verdict, reason = legacy_merge_group_cause_source_verdict(
                repo, cause, source_head, parents_cache
            )
        facts = {
            "task_id": task_id,
            "run_id": cause.get("run_id"),
            "queue_ref": cause.get("queue_ref"),
            "head_sha": cause.get("head_sha"),
            "pr_number": cause.get("pr_number"),
            "source_head": source_head,
        }
        if verdict == LEGACY_CAUSE_PROVEN:
            # The legacy producer committed the demotion without revoking the
            # group commits; a success re-asserted on them would outlive the
            # resumed review. The proven group and the PR's current queue
            # group (when it is proven to enrol the source) are revoked in the
            # same CAS that completes the cause, or nothing is written.
            group_heads, verdict, reason = legacy_cause_group_revocation_targets(
                repo, cause, source_head, parents_cache
            )
        if verdict == LEGACY_CAUSE_PROVEN:
            proven.append(dict(facts, revoked_group_heads=group_heads))
            cause["source_head"] = source_head
            record_review_gate_revocation_intent(task, repo, group_heads)
            continue
        write_activity_log(config, merge_group_audit_entry(
            f"merge_group_cause_source_{verdict}",
            facts,
            reason,
            (
                f"Committed merge group failure cause for task {task_id} (run {cause.get('run_id')}) "
                f"carries no source head; the relation to {source_head} is {verdict} ({reason}). "
                "The cause is left unchanged and read again next cycle."
            ),
        ))

    if not proven:
        return None
    committed = bool(commit_canonical_task_transition(config, status))
    if committed:
        for facts in proven:
            write_activity_log(config, merge_group_audit_entry(
                "merge_group_cause_source_proven",
                facts,
                "group_enrolled_source_head",
                (
                    f"Merge group run {facts['run_id']} on {facts['queue_ref']} enrolled source head "
                    f"{facts['source_head']} of task {facts['task_id']}; recorded it on the committed cause "
                    f"and queued review-gate revocation for group commits "
                    f"{', '.join(h[:8] for h in facts['revoked_group_heads'])}."
                ),
            ))
    return committed
