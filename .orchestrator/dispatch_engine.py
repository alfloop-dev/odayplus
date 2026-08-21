from __future__ import annotations

"""Dispatch-focused logic extracted from legacy supervisor."""
# ruff: noqa: F821

from typing import Any

from dispatch_policy import REASON_HELPER_CLAIM, worker_logical_dispatch_agent_id


def _supervisor_module():
    import supervisor
    return supervisor


def _sync_supervisor_scope() -> None:
    sv = _supervisor_module()
    excluded = {"__name__", "__doc__", "__package__", "__loader__", "__spec__", "__file__", "__cached__", "__builtins__", "Any", "_supervisor_module", "_sync_supervisor_scope", "_entrypoint", "_sync_scope_guard"}
    module_exports = {

        'task_index_from_status', 
        'current_dispatch_event_key', 
        'dispatch_priority_for_task', 
        'agent_dispatch_loads', 
        'configured_worker_slot_total', 
        'default_max_dispatches_per_tick', 
        'reassign_unavailable_reviewers', 
        'is_sidecar_review_of_current_parent', 
        'worker_logical_dispatch_agent_id', 
        'higher_priority_ready_task_exists', 
        'worker_matches_current_assignment', 
        'stale_dispatch_skip_message', 
        'ready_dispatch_signature', 
        'worktree_block_still_matches_dispatch', 
        'reconcile_task_reality', 
        '_this_repository_slug', 
        'task_reality_reconcile_is_due', 
        'escalated_lease_block', 
        'build_dispatch_event', 
        'dispatch_discussion_planning', 
        'dispatch_ready_tasks'
    }
    # Skip only dunders. The four copies of this function used to disagree --
    # two skipped every `_`-prefixed name, two skipped only `__` -- so whether a
    # single-underscore helper resolved depended on which file asked. That is how
    # `_reset_queue_record_for_redispatch` came to be called in `process_queue`
    # while never being present in this module's globals: supervisor defines it,
    # and this module's rule filtered it out. Module-local names that must NOT be
    # replaced are listed in `excluded` by name rather than inferred from a prefix.
    g = globals()
    for key, value in sv.__dict__.items():
        if key in excluded or key in module_exports or key.startswith("__"):
            continue
        g[key] = value


def _entrypoint(func):
    def _sync_scope_guard(*args, **kwargs):
        _sync_supervisor_scope()
        return func(*args, **kwargs)
    return _sync_scope_guard


MERGE_ROUTE_FIELD = "merge_route"
# How long a recorded enqueue is trusted before it is tried again, and how many
# enqueues may be recorded for one reviewed head before the task is reported as
# stalled rather than retried further.
MERGE_ROUTE_RETRY_AFTER_SECONDS = 1800.0
MERGE_ROUTE_MAX_ATTEMPTS = 4
#: Reconciling costs one `gh pr view` per task with a PR, so it runs on its own
#: cadence rather than every tick. Drift is measured in hours, not seconds.
TASK_REALITY_RECONCILE_INTERVAL_SECONDS = 900.0


def _pr_changed_paths(pr_number: int) -> list[str] | None:
    """Return the repository paths a PR touches, or None if GitHub cannot say."""
    import json as _json

    from github_bus import GitHubBusError, GitHubBusOffline, run_gh

    try:
        proc = run_gh(["pr", "view", str(pr_number), "--json", "files"])
    except (GitHubBusError, GitHubBusOffline):
        return None
    try:
        payload = _json.loads(proc.stdout or "{}")
    except ValueError:
        return None
    files = payload.get("files")
    if not isinstance(files, list):
        return None
    paths = [str(item.get("path") or "") for item in files if isinstance(item, dict)]
    # An empty list is an answer, not a failure: GitHub is saying this PR
    # changes no file. Collapsing it into None made a zero-diff PR - one whose
    # content already reached the base by another route - indistinguishable
    # from an unreadable diff, so it was classified "unknown" and parked
    # forever instead of being routed.
    return [path for path in paths if path]


def _pr_merge_state(pr_number: int) -> str | None:
    """Return GitHub's own mergeStateStatus, or None when it cannot be read."""
    import json as _json

    from github_bus import GitHubBusError, GitHubBusOffline, run_gh

    try:
        proc = run_gh(["pr", "view", str(pr_number), "--json", "mergeStateStatus"])
    except (GitHubBusError, GitHubBusOffline):
        return None
    try:
        payload = _json.loads(proc.stdout or "{}")
    except ValueError:
        return None
    return str(payload.get("mergeStateStatus") or "").strip().upper() or None


def _remote_branch_names() -> set[str] | None:
    """Every branch that exists on `origin`, or None when it cannot be read."""
    import subprocess

    from common import ROOT as _root

    try:
        proc = subprocess.run(
            ["git", "ls-remote", "--heads", "origin"],
            cwd=_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    names: set[str] = set()
    for line in (proc.stdout or "").splitlines():
        _, _, ref = line.partition("\t")
        ref = ref.strip()
        if ref.startswith("refs/heads/"):
            names.add(ref[len("refs/heads/") :])
    return names


def _pull_request_record(pr_number: int) -> dict[str, Any] | None:
    """The PR's state, or None when there is no readable PR."""
    import json as _json

    from github_bus import GitHubBusError, GitHubBusOffline, run_gh

    try:
        proc = run_gh(
            ["pr", "view", str(pr_number), "--json", "number,state,merged,headRefName"]
        )
    except (GitHubBusError, GitHubBusOffline):
        return None
    try:
        payload = _json.loads(proc.stdout or "{}")
    except ValueError:
        return None
    return payload if payload.get("number") else None


def task_reality_reconcile_is_due(
    state: dict[str, Any],
    *,
    interval_seconds: float = TASK_REALITY_RECONCILE_INTERVAL_SECONDS,
) -> bool:
    """Whether enough time has passed to re-probe reality."""
    from datetime import UTC as _utc
    from datetime import datetime as _dt

    from common import parse_iso_timestamp as _parse

    last = _parse(str(state.get("task_reality_reconciled_at") or "") or None)
    if last is None:
        return True
    return (_dt.now(_utc) - last).total_seconds() >= interval_seconds


def _this_repository_slug() -> str | None:
    """`owner/name` for this checkout's `origin`, or None when unreadable."""
    import re
    import subprocess

    from common import ROOT as _root

    try:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", (proc.stdout or "").strip())
    return match.group(1) if match else None


def reconcile_task_reality(config: dict[str, Any], status: dict[str, Any]) -> bool:
    """Repair what reality determines, report what it does not.

    Runs against the branches `origin` actually has and the PRs GitHub actually
    reports.  A probe that cannot answer yields no findings, so an unreachable
    `gh` or remote never looks like drift.
    """
    import task_reality

    schema = config.get("schema", {})
    tasks_path = schema.get("tasks_path", "tasks")
    task_id_field = schema.get("task_id_field", "id")
    all_tasks = [t for t in (status.get(tasks_path) or []) if t.get(task_id_field)]
    if not all_tasks:
        return False

    # A task that declares another repository names branches and PRs that live
    # there, and probing this checkout's `origin` for them reports drift that
    # does not exist. On 2026-08-20 three cross-repo tasks were reported as
    # naming branches that "do not exist on the remote" while those branches
    # were present in `oday-data-platform`. Reporting a false drift is worse
    # than reporting none, so anything not belonging to this repository is left
    # to whatever reconciles that one.
    this_repo = _this_repository_slug()
    tasks = []
    for task in all_tasks:
        declared = str(task.get("repository") or "").strip()
        if declared and this_repo and declared.lower() != this_repo.lower():
            continue
        if declared and not this_repo:
            # Cannot tell whose repository this is; declining is the safe half.
            continue
        tasks.append(task)
    if not tasks:
        return False

    remote_branches = _remote_branch_names()
    if remote_branches is None:
        # Without the branch list every task would look as though its branch had
        # vanished. Reconciling from a failed lookup is how a repair becomes a
        # corruption.
        return False

    def probe(task: dict[str, Any]) -> dict[str, Any]:
        try:
            pr_number = int(task.get("pr_number") or 0)
        except (TypeError, ValueError):
            pr_number = 0
        return {
            "pull_request": _pull_request_record(pr_number) if pr_number > 0 else None,
            "branch_exists": str(task.get("branch") or "") in remote_branches,
        }

    results = task_reality.reconcile_tasks(tasks, probe=probe)
    if not results:
        return False

    changed = False
    for result in results:
        task_id = str(result.get("task_id") or "")
        for applied in result.get("applied") or []:
            changed = True
            write_activity_log(
                config,
                {
                    "type": "task_reality_repaired",
                    "task_id": task_id,
                    "message": (
                        f"Repaired `{applied.get('field')}` on {task_id}: {applied.get('detail')}."
                    ),
                },
            )
        summary = str(result.get("summary") or "")
        if not summary:
            continue
        task = next((t for t in tasks if str(t.get(task_id_field)) == task_id), None)
        if task is None or task.get("next") == summary:
            continue
        task["next"] = summary
        changed = True
        write_activity_log(
            config,
            {"type": "task_reality_unresolved", "task_id": task_id, "message": summary},
        )

    if changed:
        commit_canonical_task_transition(config, status)
    return changed


def approved_pr_change_scope(pr_number: int) -> str | None:
    """Classify a PR as ``development_tooling`` or ``product_or_mixed``.

    The classifier is fail-closed: anything outside the checked-in tooling
    manifest counts as product, so an unreadable diff yields None and the
    caller must not take the direct-merge path. A PR that changes no file at
    all is readable, not unknown, and classifies as product - which routes it
    through the merge queue rather than an admin bypass.
    """
    import sys
    from pathlib import Path

    paths = _pr_changed_paths(pr_number)
    if paths is None:
        return None
    governance = Path(__file__).resolve().parents[1] / "delivery_toolchain" / "governance"
    if str(governance) not in sys.path:
        sys.path.insert(0, str(governance))
    try:
        from classify_change_review_scope import classify_paths, load_manifest
    except ImportError:
        return None
    try:
        return str(classify_paths(paths, load_manifest()).get("scope") or "") or None
    except (OSError, ValueError, KeyError):
        return None


def _task_repository_slug(config: dict[str, Any], task: dict[str, Any]) -> str:
    """The task's repository slug, or "" when it cannot be resolved.

    Routing used to rely on the supervisor's cwd, which silently answered for
    Pantheon whatever repository the task belonged to.
    """
    declared = str((task or {}).get("repository") or "").strip()
    if declared:
        return declared
    try:
        from multi_repo_registry import resolve_task_repository

        binding = resolve_task_repository(config, task)
        return str(binding.slug or "")
    except Exception:
        return ""


_MERGE_QUEUE_BY_REPO: dict[str, bool] = {}


def repository_has_merge_queue(slug: str | None, base: str) -> bool | None:
    """Does `base` in this repository have a merge queue? None when unreadable.

    Routing was written against Pantheon, whose `dev` requires a queue, and
    assumed every repository looked the same. `oday-data-platform` is private on
    a plan without branch protection -- the protection API answers 403 -- so it
    has no queue at all, and a bare `gh pr merge` there enqueues nothing. Four
    reviewed, CI-green PRs sat until they were reported `stalled` and merged by
    hand, and every DPF task that reached review_approved would have repeated it.

    Cached per repository for the process: a queue is not created or removed
    between ticks, and the alternative is a GraphQL round trip per PR per tick.
    """
    import json as _json

    from github_bus import GitHubBusError, GitHubBusOffline, run_gh

    slug = str(slug or "").strip()
    if not slug or "/" not in slug:
        return None
    if slug in _MERGE_QUEUE_BY_REPO:
        return _MERGE_QUEUE_BY_REPO[slug]
    owner, _, name = slug.partition("/")
    query = (
        f"{{repository(owner:{_json.dumps(owner)},name:{_json.dumps(name)})"
        f"{{mergeQueue(branch:{_json.dumps(base)}){{id}}}}}}"
    )
    try:
        proc = run_gh(["api", "graphql", "-f", f"query={query}"])
    except (GitHubBusError, GitHubBusOffline):
        return None
    try:
        payload = _json.loads(proc.stdout or "{}")
    except ValueError:
        return None
    repo = ((payload.get("data") or {}).get("repository")) or {}
    if "mergeQueue" not in repo:
        return None
    present = repo.get("mergeQueue") is not None
    _MERGE_QUEUE_BY_REPO[slug] = present
    return present


def route_approved_pr_to_merge(config: dict[str, Any], task: dict[str, Any]) -> tuple[str, str]:
    """Enqueue a reviewed, CI-green PR for merge.

    The merge queue only ever contains what was explicitly enqueued, so a green
    PR that nobody enqueues waits forever. Enqueueing is the whole job: `dev`
    carries a ruleset that requires it ("Changes must be made through the merge
    queue"), so there is no second path to choose between and nothing here
    decides policy. An earlier version branched on change scope and took
    `--admin` for development tooling; the ruleset rejects that outright, and
    every tooling PR failed once per tick until it was removed. Review scope
    still governs whether the workflow auto-stamps `task-review-gate` - that is
    the workflow's decision, upstream of this and not repeated here.

    Returns ``(route, detail)`` where route is ``queued``, ``waiting`` (recently
    routed, or nothing to do), ``stalled`` (enqueued repeatedly and still not
    merged), ``ejected`` or ``blocked``.
    """
    from github_bus import GitHubBusError, GitHubBusOffline, run_gh

    try:
        pr_number = int(task.get("pr_number") or 0)
    except (TypeError, ValueError):
        pr_number = 0
    if pr_number <= 0:
        return "waiting", "no PR number recorded"

    approved_head = str(task.get("approved_head") or "").strip()
    previous = task.get(MERGE_ROUTE_FIELD)
    if isinstance(previous, dict) and str(previous.get("head") or "") == approved_head:
        # Already actioned for this exact reviewed head; re-issuing the command
        # every tick would spam GitHub and re-queue an entry already in flight.
        # An entry can still be ejected afterwards - when an earlier PR merges
        # first and leaves this one conflicting - and the queue does not put it
        # back.  Report that instead of waiting on an entry that no longer
        # exists; only the owner can advance the base.
        if _pr_merge_state(pr_number) == "DIRTY":
            return "ejected", "conflicts with base after an earlier merge"

        # The record is evidence that an enqueue was issued, not that the PR is
        # in the queue. Nothing here can observe the queue directly, and on
        # 2026-08-19 four reviewed, CI-green PRs each carried `route: queued`
        # while the dev queue was empty; the guard suppressed every retry and
        # they sat for hours until an operator cleared the field by hand.
        #
        # So trust it for a window and then try again - re-enqueueing something
        # already queued is harmless, whereas never retrying is what stalled
        # them. Bounded, because retrying forever is its own failure: past
        # MERGE_ROUTE_MAX_ATTEMPTS the enqueue is demonstrably not the missing
        # step and the caller reports that instead of issuing a fifth.
        attempts = int(previous.get("attempts") or 1)
        if attempts >= MERGE_ROUTE_MAX_ATTEMPTS:
            return "stalled", (
                f"enqueued {attempts} times for this reviewed head without merging"
            )
        routed_at = parse_runtime_timestamp(str(previous.get("at") or "") or None)
        if (
            routed_at is not None
            and (datetime.now(UTC) - routed_at).total_seconds() < MERGE_ROUTE_RETRY_AFTER_SECONDS
        ):
            return "waiting", str(previous.get("route") or "already routed")

    # A repository without a merge queue cannot be enqueued into; `gh pr merge`
    # with no strategy has nothing to do there. Ask once per repository and pick
    # the only route that repository actually has.
    slug = _task_repository_slug(config, task)
    base = str(task.get("base_branch") or "dev").strip() or "dev"
    queued_repo = repository_has_merge_queue(slug, base)
    if queued_repo is False:
        args, route = ["pr", "merge", str(pr_number), "--merge"], "merged"
    else:
        # None means unreadable. Enqueueing is the conservative choice: it is
        # what a queue-protected repository requires, and a direct merge there
        # would be the bypass this routing exists to avoid.
        args, route = ["pr", "merge", str(pr_number)], "queued"
    if slug:
        args += ["--repo", slug]

    try:
        run_gh(args)
    except (GitHubBusError, GitHubBusOffline) as exc:
        return "blocked", str(exc)

    # Recorded for the audit trail only. Classification must never gate the
    # enqueue: an unreadable diff is a reason to say so, not to strand a
    # reviewed PR that the queue would have accepted.
    scope = approved_pr_change_scope(pr_number) or "unknown"
    previous_attempts = 0
    if isinstance(previous, dict) and str(previous.get("head") or "") == approved_head:
        previous_attempts = int(previous.get("attempts") or 1)
    task[MERGE_ROUTE_FIELD] = {
        "head": approved_head,
        "scope": scope,
        "route": route,
        "pr_number": pr_number,
        "at": utc_now(),
        "attempts": previous_attempts + 1,
    }
    write_activity_log(
        config,
        {
            "type": "merge_route_applied",
            "task_id": str(task.get("id") or ""),
            "message": (
                f"PR #{pr_number} {'merged directly' if route == 'merged' else 'enqueued for merge'} "
                f"(scope {scope}; repository has no merge queue)."
                if route == "merged"
                else f"PR #{pr_number} enqueued for merge (scope {scope})."
            ),
        },
    )
    return route, f"scope={scope}"


def advance_approved_prs_to_merge(
    config: dict[str, Any],
    status: dict[str, Any],
    finalize_statuses: set[str],
) -> bool:
    """Route every reviewed, CI-green PR onto the merge path its scope requires.

    This deliberately runs outside the per-agent dispatch loop.  That loop skips
    any owner already at worker capacity, and owners reach the cap precisely
    because approved-but-unmerged work accumulates against them, so routing from
    inside it deadlocks: the PRs cannot be merged because the owner is full, and
    the owner stays full because the PRs are never merged.  Routing issues a
    `gh` call rather than starting a worker, so it needs no slot.
    """
    schema = config.get("schema", {})
    tasks_path = schema.get("tasks_path", "tasks")
    task_id_field = schema.get("task_id_field", "id")

    changed = False
    for task in status.get(tasks_path, []) or []:
        task_id = str(task.get(task_id_field) or "")
        if not task_id:
            continue
        if str(task.get("status") or "").lower() not in finalize_statuses:
            continue
        if not str(task.get("approved_head") or "").strip():
            continue
        try:
            pr_status, ci_status = runtime_ai_status.task_pr_ci_status(task_id)
        except Exception:
            continue
        if ci_status != "success" or str(pr_status or "").strip().upper() == "MERGED":
            continue

        route, detail = route_approved_pr_to_merge(config, task)
        if route == "queued":
            task["next"] = (
                f"PR for task {task_id} was enqueued in the dev merge queue ({detail}); "
                "approved branch head remains immutable until the queue merges it."
            )
        elif route == "blocked":
            # Reported here rather than left silent: a PR GitHub refuses to
            # enqueue is parked indefinitely, and the operator needs the reason.
            msg = (
                f"PR for task {task_id} is CI-green but could not be routed to a merge path "
                f"({detail}); finalize dispatch is deferred until that resolves."
            )
            if task.get("next") == msg:
                continue
            task["next"] = msg
            write_activity_log(
                config,
                {"type": "merge_route_blocked", "task_id": task_id, "message": msg},
            )
        elif route == "stalled":
            # Terminal until the head changes: the enqueue has been issued
            # repeatedly and the PR has still not merged, so whatever is missing
            # is not another `gh pr merge`. Same shape as an escalated worktree
            # lease - stop, and say so where an owner looks.
            msg = (
                f"PR for task {task_id} is CI-green but has not merged after being enqueued "
                f"repeatedly ({detail}); enqueueing again will not resolve it and an owner "
                "must look at the merge queue."
            )
            if task.get("next") == msg:
                continue
            task["next"] = msg
            write_activity_log(
                config,
                {"type": "merge_route_stalled", "task_id": task_id, "message": msg},
            )
        elif route == "waiting":
            # Nothing to route - already in the queue, or no PR to enqueue.
            # This lane still owns the explanation, because the finalize lane
            # below deliberately no longer re-routes and so has nothing to say.
            msg = (
                f"PR for task {task_id} is CI-green and awaiting merge queue; "
                "approved branch head remains immutable and finalize dispatch is deferred."
            )
            if task.get("next") == msg:
                continue
            task["next"] = msg
        elif route == "ejected":
            # The queue dropped it and will not retry on its own.  Advancing the
            # base rewrites the reviewed head, so this has to go back to the
            # owner and be reviewed again rather than silently re-enqueued.
            if requeue_task_for_ci_repair(
                config,
                status,
                task,
                message=(
                    f"PR for task {task_id} was ejected from the merge queue ({detail}); "
                    "owner must advance the base and resubmit for review."
                ),
                clear_approval=True,
            ):
                changed = True
            continue
        else:
            continue
        changed = True

    if changed:
        commit_canonical_task_transition(config, status)
    return changed


@_entrypoint

def task_index_from_status(config: dict[str, Any], status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    schema = config.get("schema", {})
    tasks_path = schema.get("tasks_path", "tasks")
    task_id_field = schema.get("task_id_field", "id")
    return {
        str(task.get(task_id_field)): task
        for task in status.get(tasks_path, [])
        if task.get(task_id_field)
    }

@_entrypoint

def current_dispatch_event_key(config: dict[str, Any], event: dict[str, Any], task_map: dict[str, dict[str, Any]]) -> str | None:
    reason = str(event.get("reason") or "")
    if not is_execution_dispatch_reason(reason):
        return None

    task_id = str(event.get("task_id") or "")
    task = task_map.get(task_id)
    if not task:
        return None

    schema = config.get("schema", {})
    owner_field = schema.get("assignee_field", "owner")
    reviewer_field = schema.get("reviewer_field", "reviewer")
    target_agent = str(event.get("target_display_name") or display_name_for(config, str(event.get("target_agent") or "")))
    settings = ready_dispatch_settings(config)
    review_statuses = normalized_status_set(settings.get("review_statuses"), ["review"])
    finalize_statuses = normalized_status_set(settings.get("finalize_statuses"), ["review_approved"])
    dependency_done_statuses = normalized_status_set(settings.get("dependency_done_statuses"), ["done"])
    task_status = str(task.get("status") or "").lower()

    eligible = False
    if reason == REASON_REVIEW_READY:
        eligible = task_status in review_statuses and task.get(reviewer_field) == target_agent
    elif reason == REASON_OWNED_FINALIZE:
        eligible = task_status in finalize_statuses and task.get(owner_field) == target_agent
    elif reason == REASON_OWNED_IN_PROGRESS:
        eligible = task_status == "in_progress" and task.get(owner_field) == target_agent and dependencies_satisfied(task, task_map, dependency_done_statuses)
    elif reason == REASON_OWNED_READY:
        eligible = task_status in {"todo", "in_progress"} and task.get(owner_field) == target_agent and dependencies_satisfied(task, task_map, dependency_done_statuses)
    elif reason == REASON_HELPER_CLAIM:
        claim = task.get("helper_execution_lease") or {}
        eligible = (
            task_status in {"todo", "in_progress"}
            and normalize_agent_id(str(claim.get("claimed_by") or "")) == normalize_agent_id(target_agent)
            and helper_claim_is_live(claim)
            and dependencies_satisfied(task, task_map, dependency_done_statuses)
        )

    if not eligible:
        return None

    return str(build_dispatch_event(task, target_agent, reason, task_map).get("key") or "")

@_entrypoint

def dispatch_priority_for_task(
    config: dict[str, Any],
    task: dict[str, Any],
    agent_name: str,
    *,
    task_map: dict[str, dict[str, Any]] | None = None,
    dependencies_done_statuses: set[str] | None = None,
) -> int | None:
    settings = ready_dispatch_settings(config)
    review_statuses = normalized_status_set(settings.get("review_statuses"), ["review"])
    finalize_statuses = normalized_status_set(settings.get("finalize_statuses"), ["review_approved"])
    dependency_done_statuses = dependencies_done_statuses or normalized_status_set(
        settings.get("dependency_done_statuses"),
        ["done"],
    )
    schema = config.get("schema", {})
    owner_field = schema.get("assignee_field", "owner")
    reviewer_field = schema.get("reviewer_field", "reviewer")
    task_status = str(task.get("status") or "").lower()
    tmap = task_map if task_map is not None else {str(task.get("id") or ""): task}

    norm_target = normalize_agent_id(agent_name or "")
    task_owner = normalize_agent_id(str(task.get(owner_field) or ""))
    task_reviewer = normalize_agent_id(str(task.get(reviewer_field) or ""))

    if task_status in review_statuses and task_reviewer == norm_target:
        return 0
    if task_status in finalize_statuses and task_owner == norm_target:
        approved_head = task.get("approved_head")
        # B22: a missing approved_head is not "no freeze configured", it is a task
        # whose reviewed commit is unknown. Fail closed like every other branch of
        # this gate; the reviewer clears it with `restore_approved_head`.
        if not approved_head:
            return None
        try:
            curr_head = runtime_ai_status.resolve_task_checkout_sha(
                task, force_refresh=True
            )
            if not curr_head or not runtime_ai_status.is_approved_head_satisfied(task, curr_head, approved_head):
                return None
        except Exception:
            return None
        try:
            pr_status, ci_status = runtime_ai_status.task_pr_ci_status(str(task.get("id") or ""))
            if str(pr_status or "").strip().upper() != "MERGED" or ci_status not in {"success", "none"}:
                return None
        except Exception:
            return None
        return 1
    if (
        task_status == "in_progress"
        and task_owner == norm_target
        and dependencies_satisfied(task, tmap, dependency_done_statuses)
    ):
        return 2
    if (
        task_status == "todo"
        and task_owner == norm_target
        and dependencies_satisfied(task, tmap, dependency_done_statuses)
    ):
        return 3
    claim = task.get("helper_execution_lease") or {}
    if (
        task_status in {"todo", "in_progress"}
        and normalize_agent_id(str(claim.get("claimed_by") or "")) == norm_target
        and helper_claim_is_live(claim)
        and dependencies_satisfied(task, tmap, dependency_done_statuses)
    ):
        return 4
    return None


def helper_claim_is_live(claim: dict[str, Any], *, now: datetime | None = None) -> bool:
    if not isinstance(claim, dict) or not claim.get("claimed_by"):
        return False
    expires_at = parse_iso_timestamp(str(claim.get("lease_expires_at") or ""))
    if expires_at is None:
        return False
    current = now or datetime.now(UTC)
    return expires_at > current


def helper_owner_is_saturated(
    config: dict[str, Any],
    task: dict[str, Any],
    agent_loads: dict[str, list[int]],
    helper_settings: dict[str, Any],
) -> bool:
    owner = str(task.get("owner") or "")
    owner_load = len(agent_loads.get(owner, []))
    if owner_load >= agent_dispatch_capacity(config, owner):
        return True
    last_update = parse_iso_timestamp(str(task.get("last_update") or ""))
    if last_update is None:
        return not helper_settings.get("require_owner_saturated", True)
    age = (datetime.now(UTC) - last_update).total_seconds()
    return age >= float(helper_settings.get("dispatch_sla_seconds", 600))

@_entrypoint

def agent_dispatch_loads(
    config: dict[str, Any],
    state: dict[str, Any],
    active_statuses: set[str],
) -> dict[str, list[int]]:
    loads: dict[str, list[int]] = {}
    active_event_ids: set[str] = set()

    for worker in state.get("workers", {}).values():
        if not worker_counts_as_active_capacity(config, worker, active_statuses):
            continue
        event_id = str(worker.get("queue_event_id") or "")
        if event_id:
            active_event_ids.add(event_id)
        reason = str(worker.get("request_snapshot", {}).get("reason") or "")
        priority = dispatch_reason_priority(reason)
        if priority is None:
            continue
        # A pool slot is only an execution resource. Attribute its work to the
        # logical ownership role so role load balancing remains meaningful when
        # many aliases share a small slot set.
        logical_agent_id = worker_logical_dispatch_agent_id(config, worker)
        agent_name = display_name_for(config, logical_agent_id)
        if not agent_name:
            continue
        loads.setdefault(agent_name, []).append(priority)

    queue_records = state.get("queue", {}).get("events", {})
    for event in load_event_queue(config):
        event_id = str(event.get("event_id") or "")
        if not event_id:
            continue
        if event_id in active_event_ids:
            continue
        record = queue_records.get(event_id, {})
        if record.get("status") in {"completed", "failed"}:
            continue
        reason = str(event.get("reason") or "")
        priority = dispatch_reason_priority(reason)
        if priority is None:
            continue
        agent_name = str(event.get("target_display_name") or display_name_for(config, str(event.get("target_agent") or "")))
        if not agent_name:
            continue
        loads.setdefault(agent_name, []).append(priority)

    return loads

@_entrypoint

def reassign_unavailable_reviewers(
    config: dict[str, Any],
    state: dict[str, Any],
    status: dict[str, Any],
    *,
    provider_report: dict[str, Any] | None = None,
) -> bool:
    settings = ready_dispatch_settings(config)
    if not reviewer_failover_settings(config).get("enabled", True):
        return False

    schema = config.get("schema", {})
    tasks_path = schema.get("tasks_path", "tasks")
    task_id_field = schema.get("task_id_field", "id")
    owner_field = schema.get("assignee_field", "owner")
    reviewer_field = schema.get("reviewer_field", "reviewer")
    review_statuses = {str(value).lower() for value in settings.get("review_statuses", ["review"])}
    finalize_statuses = {
        str(value).lower() for value in settings.get("finalize_statuses", ["review_approved"])
    }
    owned_statuses = {
        str(value).lower() for value in settings.get("owned_statuses", ["in_progress", "todo"])
    }
    active_statuses = active_worker_statuses(config)
    active_agents, active_task_agents = active_worker_indexes(state, active_statuses, config)
    pending_agents, pending_task_agents, _pending_event_keys = outstanding_delivery_indexes(config, state)
    reserved_agents = set(active_agents) | set(pending_agents)
    reserved_tasks = {task_id for task_id, _agent_id in active_task_agents | pending_task_agents}
    candidate_agent_ids = dispatch_loop_agent_ids(config)
    changed = False

    for task in status.get(tasks_path, []) or []:
        task_id = str(task.get(task_id_field) or "")
        if not task_id or task_id in reserved_tasks:
            continue
        task_status = str(task.get("status") or "").lower()
        if task_status in review_statuses:
            claimed_role = "reviewer"
            claimed_field = reviewer_field
            counterpart = str(task.get(owner_field) or "").strip()
        elif task_status in finalize_statuses or task_status in owned_statuses:
            claimed_role = "owner"
            claimed_field = owner_field
            counterpart = str(task.get(reviewer_field) or "").strip()
        else:
            continue

        claimed_agent = str(task.get(claimed_field) or "").strip()
        if not claimed_agent or is_human_gate_agent(claimed_agent):
            continue
        if claimed_role == "owner":
            claimed_id = normalize_agent_id(claimed_agent)
            if agent_dispatch_paused(config, state, claimed_id):
                claimed_block_reason = (
                    f"dispatch is paused or disabled for {display_name_for(config, claimed_id) or claimed_agent}"
                )
            elif account_pool_dispatch_block_reason(config, claimed_id, runtime_state=state):
                claimed_block_reason = account_pool_dispatch_block_reason(
                    config, claimed_id, runtime_state=state
                )
            else:
                claimed_block_reason = None
            reviewer_same_pool = False
        else:
            claimed_block_reason = agent_auto_dispatch_block_reason(
                config,
                state,
                normalize_agent_id(claimed_agent),
                provider_report,
            )
            reviewer_same_pool = bool(
                counterpart
                and not is_human_gate_agent(counterpart)
                and not review_is_independent(config, counterpart, claimed_agent)
            )
        if not claimed_block_reason and not reviewer_same_pool:
            continue

        replacement = ""
        replacement_id = ""
        for candidate_id in candidate_agent_ids:
            candidate = display_name_for(config, candidate_id)
            candidate_config = (config.get("agents", {}) or {}).get(candidate_id)
            owner_for_independence = candidate if claimed_role == "owner" else counterpart
            reviewer_for_independence = counterpart if claimed_role == "owner" else candidate
            if (
                not candidate
                or candidate in {claimed_agent, counterpart}
                or candidate_id in reserved_agents
                or not isinstance(candidate_config, dict)
                or agent_is_dispatch_slot(candidate_config)
                or is_human_gate_agent(candidate)
                or not agent_can_take_task(config, candidate, task)
                or (
                    bool(counterpart and not is_human_gate_agent(counterpart))
                    and not review_is_independent(config, owner_for_independence, reviewer_for_independence)
                )
                or agent_auto_dispatch_block_reason(config, state, candidate_id, provider_report)
            ):
                continue
            replacement = candidate
            replacement_id = candidate_id
            break
        if not replacement:
            continue

        if reviewer_same_pool:
            message = (
                f"Reassigned review to {replacement}: {claimed_agent} shares account pool "
                f"with owner {counterpart}, so independent review requires a different pool."
            )
        else:
            message = (
                f"Automatically reassigned {claimed_role} to {replacement} while {claimed_role} {claimed_agent} "
                f"is dispatch-paused: {claimed_block_reason}"
            )
        new_owner = replacement if claimed_role == "owner" else counterpart
        new_reviewer = counterpart if claimed_role == "owner" else replacement
        if not persist_task_reassignment(
            config,
            task_id=task_id,
            new_owner=new_owner,
            new_reviewer=new_reviewer,
            message=message,
            handoff_to=replacement,
            handoff_from=claimed_agent,
        ):
            continue
        task[claimed_field] = replacement
        task["next"] = message
        reserved_agents.add(replacement_id)
        reserved_tasks.add(task_id)
        changed = True
        write_activity_log(
            config,
            {
                "type": f"task_{claimed_role}_reassigned",
                "task_id": task_id,
                "message": message,
                "owner": new_owner,
                "from_reviewer": claimed_agent if claimed_role == "reviewer" else None,
                "to_reviewer": replacement if claimed_role == "reviewer" else None,
                "role": claimed_role,
                "counterpart": counterpart,
                f"from_{claimed_role}": claimed_agent,
                f"to_{claimed_role}": replacement,
            },
        )
        console_log(
            f"{claimed_role} failover: task={task_id} from={claimed_agent} to={replacement}",
            quiet=SUPERVISOR_LOG_QUIET,
        )

    return changed

@_entrypoint

def is_sidecar_review_of_current_parent(
    candidate_task: dict[str, Any],
    current_task: dict[str, Any] | None,
    *,
    agent_name: str,
    review_statuses: set[str],
    owner_field: str,
    reviewer_field: str,
) -> bool:
    if not current_task:
        return False
    candidate_status = str(candidate_task.get("status") or "").lower()
    if candidate_status not in review_statuses:
        return False
    if candidate_task.get(reviewer_field) != agent_name:
        return False
    if current_task.get(owner_field) != agent_name:
        return False
    current_task_id = str(current_task.get("id") or "")
    helper_parent = str(candidate_task.get("helper_parent") or "").strip()
    if not current_task_id or helper_parent != current_task_id:
        return False
    task_class = str(candidate_task.get("task_class") or "").lower()
    return task_class == "sidecar" or bool(candidate_task.get("helper_kind"))

@_entrypoint

def higher_priority_ready_task_exists(
    config: dict[str, Any],
    worker: dict[str, Any],
    task_map: dict[str, dict[str, Any]],
    state: dict[str, Any] | None = None,
) -> bool:
    if worker_is_discussion_planning(worker) or worker_is_coordination_dispatch(worker):
        return False
    current_priority = dispatch_reason_priority(worker.get("request_snapshot", {}).get("reason"))
    if current_priority is None:
        return False

    logical_agent_id = worker_logical_dispatch_agent_id(config, worker)
    agent_name = display_name_for(config, logical_agent_id)
    current_task_id = str(worker.get("task_id") or "")
    settings = ready_dispatch_settings(config)
    active_statuses = active_worker_statuses(config)
    review_statuses = normalized_status_set(settings.get("review_statuses"), ["review"])
    dependency_done_statuses = normalized_status_set(settings.get("dependency_done_statuses"), ["done"])
    schema = config.get("schema", {})
    owner_field = schema.get("assignee_field", "owner")
    reviewer_field = schema.get("reviewer_field", "reviewer")
    current_task = task_map.get(current_task_id)
    higher_priority_task_ids: set[str] = set()
    slot_count = len(logical_worker_slot_ids(config, logical_agent_id))
    urgent_priority_cutoff = dispatch_reason_priority(REASON_OWNED_FINALIZE)

    for task_id, task in task_map.items():
        if task_id == current_task_id:
            continue
        if task_is_sidecar(task) and not task_is_sidecar(current_task or {}):
            continue
        task_status = str(task.get("status") or "").lower()
        candidate_priority = None
        if task_status in review_statuses and task.get(reviewer_field) == agent_name:
            if is_sidecar_review_of_current_parent(
                task,
                current_task,
                agent_name=agent_name,
                review_statuses=review_statuses,
                owner_field=owner_field,
                reviewer_field=reviewer_field,
            ):
                continue
            candidate_priority = 0
        else:
            candidate_priority = dispatch_priority_for_task(
                config,
                task,
                agent_name,
                task_map=task_map,
                dependencies_done_statuses=dependency_done_statuses,
            )

        if candidate_priority is not None and candidate_priority < current_priority:
            if (
                slot_count
                and urgent_priority_cutoff is not None
                and candidate_priority > urgent_priority_cutoff
            ):
                continue
            higher_priority_task_ids.add(str(task_id))

    if not higher_priority_task_ids:
        return False

    effective_state = state or {
        "workers": {str(worker.get("run_id") or "__current__"): worker},
        "queue": {"events": {}},
    }
    occupied_count = 0
    served_higher_priority_task_ids: set[str] = set()
    active_event_ids: set[str] = set()
    current_run_id = str(worker.get("run_id") or "")

    for run_id, other in (effective_state.get("workers", {}) or {}).items():
        if not worker_counts_as_active_capacity(config, other, active_statuses):
            continue
        other_agent_id = worker_logical_dispatch_agent_id(config, other)
        if display_name_for(config, other_agent_id) != agent_name:
            continue
        occupied_count += 1
        event_id = str(other.get("queue_event_id") or "")
        if event_id:
            active_event_ids.add(event_id)
        other_priority = dispatch_reason_priority(other.get("request_snapshot", {}).get("reason"))
        other_task_id = str(other.get("task_id") or "")
        if str(run_id) != current_run_id and other_priority is not None and other_priority < current_priority and other_task_id:
            served_higher_priority_task_ids.add(other_task_id)

    queue_records = (effective_state.get("queue", {}) or {}).get("events", {}) or {}
    try:
        queued_events = load_event_queue(config)
    except KeyError:
        queued_events = []
    for event in queued_events:
        event_id = str(event.get("event_id") or "")
        if not event_id or event_id in active_event_ids:
            continue
        record = queue_records.get(event_id, {})
        if record.get("status") in {"completed", "failed"}:
            continue
        target_agent = str(event.get("target_display_name") or display_name_for(config, str(event.get("target_agent") or "")))
        if target_agent != agent_name:
            continue
        occupied_count += 1
        event_priority = dispatch_reason_priority(str(event.get("reason") or ""))
        event_task_id = str(event.get("task_id") or "")
        if event_priority is not None and event_priority < current_priority and event_task_id:
            served_higher_priority_task_ids.add(event_task_id)

    agent_capacity = agent_dispatch_capacity(config, logical_agent_id)
    free_slots = max(0, agent_capacity - occupied_count)
    unserved_higher_priority = higher_priority_task_ids - served_higher_priority_task_ids
    return len(unserved_higher_priority) > free_slots

@_entrypoint

def worker_matches_current_assignment(
    config: dict[str, Any],
    worker: dict[str, Any],
    task_map: dict[str, dict[str, Any]],
) -> bool:
    if worker_is_discussion_planning(worker):
        return True
    if worker_is_coordination_dispatch(worker):
        return True
    task_id = str(worker.get("task_id") or "")
    task = task_map.get(task_id)
    if not task:
        return False
    agent_name = display_name_for(config, worker_logical_dispatch_agent_id(config, worker))
    settings = ready_dispatch_settings(config)
    review_statuses = normalized_status_set(settings.get("review_statuses"), ["review"])
    finalize_statuses = normalized_status_set(settings.get("finalize_statuses"), ["review_approved"])
    owned_statuses = normalized_status_set(settings.get("owned_statuses"), ["in_progress", "todo"])
    dependency_done_statuses = normalized_status_set(settings.get("dependency_done_statuses"), ["done"])
    schema = config.get("schema", {})
    owner_field = schema.get("assignee_field", "owner")
    reviewer_field = schema.get("reviewer_field", "reviewer")
    task_status = str(task.get("status") or "").lower()
    if task_status in dependency_done_statuses:
        return False
    if task_status in review_statuses:
        return task.get(reviewer_field) == agent_name
    if task_status in finalize_statuses:
        return task.get(owner_field) == agent_name
    if task_status in owned_statuses:
        claim = task.get("helper_execution_lease") or {}
        return task.get(owner_field) == agent_name or (
            helper_claim_is_live(claim)
            and normalize_agent_id(str(claim.get("claimed_by") or ""))
            == normalize_agent_id(agent_name)
        )
    return False

@_entrypoint

def stale_dispatch_skip_message(config: dict[str, Any], event: dict[str, Any], task_map: dict[str, dict[str, Any]]) -> str | None:
    reason = str(event.get("reason") or "")
    if not is_execution_dispatch_reason(reason):
        return None

    expected_key = current_dispatch_event_key(config, event, task_map)
    task_id = str(event.get("task_id") or "unknown task")
    task = task_map.get(task_id) or {}
    owner = str(task.get("owner") or "")
    target = str(event.get("target_display_name") or display_name_for(config, str(event.get("target_agent") or "")))
    task_status = str(task.get("status") or "").lower()

    if expected_key is None:
        if reason == REASON_OWNED_READY and task_status == "in_progress" and owner == target:
            return None
        return f"Skipped stale queued wake event for {task_id}: task is no longer eligible for {reason}."

    queued_key = str(event.get("event_key") or "")
    if queued_key and queued_key != expected_key:
        if reason == REASON_OWNED_READY and task_status == "in_progress" and owner == target:
            return None
        return f"Skipped stale queued wake event for {task_id}: task state changed after the wake-up was queued."

    return None

@_entrypoint

def ready_dispatch_signature(task: dict[str, Any], reason: str, task_map: dict[str, dict[str, Any]]) -> str:
    # `last_update` is deliberately excluded. Notes, status-check retries, and
    # generated-view synchronization may update that timestamp after a wake is
    # queued without changing who may execute the task. Role/status/dependency
    # changes below remain part of the key and still invalidate stale wakes.
    return json.dumps(
        {
            "task_id": task.get("id"),
            "status": task.get("status"),
            "reason": reason,
            "owner": task.get("owner"),
            "reviewer": task.get("reviewer"),
            "depends_on": list(task.get("depends_on", []) or []),
            "dependency_signature": task_dependency_signature(task, task_map),
        },
        sort_keys=True,
        ensure_ascii=True,
    )

LEASE_BLOCK_RETRY_AFTER_SECONDS = 1800.0


def lease_block_retry_after_seconds(config: dict[str, Any]) -> float:
    try:
        value = float(worker_runtime_settings(config).get("lease_block_retry_after_seconds", LEASE_BLOCK_RETRY_AFTER_SECONDS))
    except (TypeError, ValueError):
        return LEASE_BLOCK_RETRY_AFTER_SECONDS
    return value if value > 0 else LEASE_BLOCK_RETRY_AFTER_SECONDS


@_entrypoint
def worktree_block_still_matches_dispatch(
    state: dict[str, Any],
    task: dict[str, Any],
    reason: str,
    task_map: dict[str, dict[str, Any]],
    *,
    retry_after_seconds: float = LEASE_BLOCK_RETRY_AFTER_SECONDS,
) -> bool:
    """Do not recreate an identical wake after a fail-closed worktree block.

    Any ownership, lifecycle, dependency or branch-state update changes the
    dispatch signature and makes the task eligible again.  This preserves
    automatic recovery without burning a provider slot every supervisor tick.

    The signature alone is not enough to guarantee that recovery. A task parked
    in `in_progress` under a stable owner never changes signature, so the block
    suppressed the only thing that could have cleared it - the lease attempt -
    and the task waited forever. On 2026-08-17 six worktrees jammed this way and
    the fleet ran nothing at all for ten hours. Expire the suppression instead:
    retry once the window elapses, so a repaired or repairable worktree comes
    back on its own while a genuinely stuck one still costs one attempt per
    window rather than one per tick.
    """
    task_id = str(task.get("id") or "")
    entry = (state.get("worker_worktree_lease_blocks") or {}).get(normalize_agent_id(task_id) or task_id)
    if not isinstance(entry, dict):
        return False
    if str(entry.get("dispatch_signature") or "") != ready_dispatch_signature(task, reason, task_map):
        return False
    if entry.get("escalated"):
        # Retrying is the thing that has already been established not to work:
        # this block has repeated unchanged past the escalation threshold. The
        # window exists to let a repairable worktree come back on its own, and
        # by definition this one has not. Suppress until something actually
        # changes - the entry resets whenever `refresh_status` differs, and an
        # operator clearing the block removes it outright.
        #
        # Suppressing alone was tried on 2026-08-17 and jammed six worktrees for
        # ten hours, because nothing said so anywhere an operator looks. That is
        # why the caller writes the reason onto the task record: stopping and
        # saying so are one change, and either half without the other is how
        # this has failed twice.
        return True
    blocked_at = parse_runtime_timestamp(str(entry.get("last_at") or "") or None)
    if blocked_at is None:
        return True
    return (datetime.now(UTC) - blocked_at).total_seconds() < retry_after_seconds


@_entrypoint
def escalated_lease_block(state: dict[str, Any], task: dict[str, Any]) -> dict[str, Any] | None:
    """The lease block that has stopped this task's dispatch, if there is one."""
    task_id = str(task.get("id") or "")
    entry = (state.get("worker_worktree_lease_blocks") or {}).get(normalize_agent_id(task_id) or task_id)
    if isinstance(entry, dict) and entry.get("escalated"):
        return entry
    return None

@_entrypoint

def build_dispatch_event(task: dict[str, Any], target_agent: str, reason: str, task_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    task_payload = {
        **task_progress_snapshot(task),
        "artifacts": list(task.get("artifacts", []) or []),
        "last_update": task.get("last_update"),
    }
    for key in (
        "task_class",
        "auto_generated",
        "helper_parent",
        "helper_kind",
        "mutates_canonical",
        "auto_created_by",
        "helper_execution_lease",
    ):
        if key in task:
            task_payload[key] = task.get(key)
    signature = ready_dispatch_signature(task, reason, task_map)
    return {
        "key": f"dispatcher:{target_agent}:{task.get('id')}:{reason}:{signature}",
        "task_id": task.get("id"),
        "target_agent": target_agent,
        "reason": reason,
        "task": task_payload,
    }

@_entrypoint

def dispatch_discussion_planning(
    config: dict[str, Any],
    state: dict[str, Any],
    planning_state: dict[str, Any] | None = None,
    provider_report: dict[str, Any] | None = None,
) -> bool:
    planning_state = planning_state or load_discussion_planning_state()
    if not discussion_planning_is_active(planning_state):
        return False
    paths = config.get("paths", {}) or {}
    if not paths.get("event_queue") or not paths.get("activity_log"):
        return False

    active_statuses = active_worker_statuses(config)
    active_agents, _active_task_agents = active_worker_indexes(state, active_statuses, config)
    pending_agents, _pending_task_agents, pending_event_keys = outstanding_delivery_indexes(config, state)
    changed = False

    for agent_name, readout in (planning_state.get("readouts", {}) or {}).items():
        agent_id = normalize_agent_id(agent_name)
        if not agent_id or agent_id not in config.get("agents", {}):
            continue
        if agent_auto_dispatch_block_reason(config, state, agent_id, provider_report):
            continue
        readout_status = str((readout or {}).get("status") or "").lower()
        if readout_status in {"submitted", "accepted"}:
            continue
        if agent_id in active_agents or agent_id in pending_agents:
            continue
        reason = "discussion_planning_baton_dispatch" if str(planning_state.get("baton_owner") or "") == agent_name else "discussion_planning_readout_dispatch"
        event_key = (
            f"discussion:{planning_state.get('session_id')}:{agent_name}:{reason}:"
            f"round-{planning_state.get('current_round', 0)}:{planning_state.get('consensus_status', 'not_started')}"
        )
        if event_key in pending_event_keys:
            continue
        queued_event_key = queue_discussion_planning_event(config, planning_state, agent_name=agent_name, reason=reason)
        pending_event_keys.add(queued_event_key)
        changed = True

    return changed

def configured_worker_slot_total(config: dict[str, Any]) -> int:
    """How many worker processes this configuration can actually run at once."""
    agents = config.get("agents", {}) or {}
    return sum(1 for agent in agents.values() if isinstance(agent, dict) and agent.get("slot_id"))


def default_max_dispatches_per_tick(config: dict[str, Any]) -> int:
    """Enough dispatches to fill every slot the configuration declares.

    A fixed cap is a second limit on top of the one that already exists: slots
    already bound concurrency, and `agent_dispatch_capacity` already refuses to
    exceed them. Capping ticks as well only decides how *slowly* free capacity
    is taken up.

    On 2026-08-20 the two compounded. Eleven slots were configured, the cap was
    3, and the poll interval was 300s - so at most three workers could start
    every five minutes, while workers finish in one to five. The fleet sat at a
    fraction of its capacity with eligible work on the board, and an operator
    had to keep prodding it.

    So the default is the slot total. An operator who wants a smaller batch can
    still say so; nothing here overrides an explicit setting.
    """
    return max(4, configured_worker_slot_total(config))


@_entrypoint

def dispatch_ready_tasks(
    config: dict[str, Any],
    state: dict[str, Any],
    provider_report: dict[str, Any] | None = None,
    agent_ids_override: list[str] | None = None,
    max_dispatches_override: int | None = None,
) -> bool:
    settings = ready_dispatch_settings(config)
    if not settings.get("enabled", True):
        return False

    status = load_status(config)
    schema = config.get("schema", {})
    tasks_path = schema.get("tasks_path", "tasks")
    task_id_field = schema.get("task_id_field", "id")
    owner_field = schema.get("assignee_field", "owner")
    reviewer_field = schema.get("reviewer_field", "reviewer")

    metadata_repaired = repair_open_task_metadata(config, status)
    if metadata_repaired:
        status = load_status(config)
    review_states_repaired = repair_unsubmitted_review_tasks(config, status)
    if review_states_repaired:
        status = load_status(config)

    tasks = [task for task in status.get(tasks_path, []) if task.get(task_id_field)]
    task_map = {task.get(task_id_field): task for task in tasks}
    review_statuses = {str(value).lower() for value in settings.get("review_statuses", ["review"])}
    finalize_statuses = {str(value).lower() for value in settings.get("finalize_statuses", ["review_approved"])}
    dependency_done_statuses = {str(value).lower() for value in settings.get("dependency_done_statuses", ["done"])}
    active_statuses = active_worker_statuses(config)
    max_dispatches_per_tick = max(
        1,
        int(
            max_dispatches_override
            or settings.get("max_dispatches_per_tick")
            or default_max_dispatches_per_tick(config)
        ),
    )

    active_agents, active_task_agents = active_worker_indexes(state, active_statuses, config)
    pending_agents, pending_task_agents, pending_event_keys = outstanding_delivery_indexes(config, state)
    active_task_ids = {task_id for task_id, _agent_id in active_task_agents if task_id}
    pending_task_ids = {task_id for task_id, _agent_id in pending_task_agents if task_id}
    agent_loads = agent_dispatch_loads(config, state, active_statuses)
    active_quota_counts = active_quota_group_counts(config, state, active_statuses)
    pending_quota_counts = queued_quota_group_counts(config, state)

    changed = metadata_repaired or review_states_repaired
    normalized = False
    for task in tasks:
        task_id = str(task.get(task_id_field) or "")
        if not task_id or task_id in active_task_ids or task_id in pending_task_ids:
            continue
        assignment_normalized = normalize_task_assignment_integrity(config, state, status, task)
        normalized = assignment_normalized or normalized
        # Both normalizers persist from canonical disk state. Avoid letting the
        # legacy mainline guard immediately overwrite a repair using this
        # loop's stale pre-repair task object.
        if not assignment_normalized:
            normalized = normalize_mainline_task_assignment(config, task, task_map) or normalized

    if normalized:
        changed = True
        status = load_status(config)
        tasks = [task for task in status.get(tasks_path, []) if task.get(task_id_field)]
        task_map = {task.get(task_id_field): task for task in tasks}

    if reassign_unavailable_reviewers(
        config,
        state,
        status,
        provider_report=provider_report,
    ):
        changed = True
        status = load_status(config)
        tasks = [task for task in status.get(tasks_path, []) if task.get(task_id_field)]
        task_map = {task.get(task_id_field): task for task in tasks}

    if task_reality_reconcile_is_due(state):
        state["task_reality_reconciled_at"] = utc_now()
        if reconcile_task_reality(config, status):
            changed = True
            status = load_status(config)
            tasks = [task for task in status.get(tasks_path, []) if task.get(task_id_field)]
            task_map = {task.get(task_id_field): task for task in tasks}

    if advance_approved_prs_to_merge(config, status, finalize_statuses):
        changed = True
        status = load_status(config)
        tasks = [task for task in status.get(tasks_path, []) if task.get(task_id_field)]
        task_map = {task.get(task_id_field): task for task in tasks}

    dispatches = 0
    agent_sequence = (
        [normalize_agent_id(agent_id) for agent_id in agent_ids_override if normalize_agent_id(agent_id)]
        if agent_ids_override
        else dispatch_loop_agent_ids(config)
    )
    dispatch_state = state.setdefault("ready_dispatcher", {})
    try:
        dispatch_cursor = int(dispatch_state.get("dispatch_cursor", 0))
    except (TypeError, ValueError):
        dispatch_cursor = 0
    if agent_sequence:
        dispatch_cursor %= len(agent_sequence)
        agent_ids = agent_sequence[dispatch_cursor:] + agent_sequence[:dispatch_cursor]
    else:
        agent_ids = []
    considered_agents = 0
    for agent_id in agent_ids:
        if dispatches >= max_dispatches_per_tick:
            break
        considered_agents += 1
        target_agent = display_name_for(config, agent_id)
        if agent_auto_dispatch_block_reason(config, state, agent_id, provider_report):
            continue
        # A logical agent without explicit worker slots can run only one
        # process at a time. Do not build a same-agent queue backlog.
        if not logical_worker_slot_ids(config, agent_id) and (
            agent_id in active_agents or agent_id in pending_agents
        ):
            continue
        quota_limit = account_pool_effective_concurrency(config, state, agent_id)
        quota_group = agent_quota_group_id(config, agent_id)
        quota_used = active_quota_counts.get(quota_group, 0) + pending_quota_counts.get(quota_group, 0)
        if quota_limit and quota_group and quota_used >= quota_limit:
            continue
        agent_capacity = agent_dispatch_capacity(config, agent_id)
        current_agent_load = len(agent_loads.get(target_agent, []))
        if current_agent_load >= agent_capacity:
            continue
        available_agent_slots = agent_capacity - current_agent_load
        if quota_limit and quota_group:
            available_agent_slots = min(available_agent_slots, max(0, quota_limit - quota_used))
            if available_agent_slots <= 0:
                continue
        # Sort first by the business priority carried by the task (P0..P3),
        # then by lifecycle action (review/finalize/execute), then stable board
        # order.  The previous implementation ignored task.priority entirely.
        candidates: list[tuple[int, int, int, dict[str, Any], str]] = []
        for index, task in enumerate(tasks):
            task_id = str(task.get(task_id_field) or "")
            if not task_id:
                continue
            if task_id in active_task_ids or task_id in pending_task_ids:
                continue
            is_sidecar_task = task_is_sidecar(task)
            task_status = str(task.get("status") or "").lower()
            task_owner = task.get(owner_field)
            task_reviewer = task.get(reviewer_field)
            norm_target = normalize_agent_id(target_agent or "")
            norm_task_owner = normalize_agent_id(str(task_owner or ""))
            norm_task_reviewer = normalize_agent_id(str(task_reviewer or ""))

            if (task_id, agent_id) in active_task_agents or (task_id, agent_id) in pending_task_agents:
                continue

            reason = None
            priority = None
            if task_status in review_statuses and norm_task_reviewer == norm_target:
                # The status CLI rejects identical owner/reviewer assignments,
                # but dispatch must still fail closed if a stale or externally
                # edited snapshot reaches the Supervisor. Never spend a worker
                # slot on an approval that would be an owner self-review.
                if norm_task_owner == norm_task_reviewer:
                    continue
                if not review_is_independent(config, str(task_owner or ""), target_agent):
                    # The reassignment helper above repairs this when another
                    # healthy pool is available.  Do not write an event on
                    # every dispatch tick if all alternate pools are busy.
                    continue
                reason = "review_ready_dispatch"
                priority = 0
            elif task_status in finalize_statuses and norm_task_owner == norm_target:
                approved_head = task.get("approved_head")
                current_head = None
                try:
                    current_head = runtime_ai_status.resolve_task_checkout_sha(task, force_refresh=True)
                except Exception as err:
                    console_log(f"Failed to resolve sha for {task_id}: {err}", quiet=SUPERVISOR_LOG_QUIET)
                # B22: a task in a finalize status with no approved_head has no
                # verifiable reviewed commit, so finalize dispatch fails closed
                # here too. Pre-freeze tasks do land in this shape, but backward
                # compatibility has to be an explicit audited migration
                # (`ai_status.py restore_approved_head`, reviewer-only), not an
                # automatic bypass of the control this gate exists to apply.
                # Say so once so the operator sees why the task is parked.
                if not approved_head:
                    msg = (
                        f"Task {task_id} is {task_status} with no reviewer-approved head; "
                        "finalize dispatch suppressed. The reviewer must attest the reviewed "
                        f"commit (`restore_approved_head {task_id} <sha> <reason>`) or send it "
                        "back for re-review."
                    )
                    if task.get("next") != msg:
                        task["next"] = msg
                        if not commit_canonical_task_transition(config, status):
                            return changed
                        write_activity_log(
                            config,
                            {
                                "type": "approved_head_missing",
                                "task_id": task_id,
                                "message": msg,
                            },
                        )
                    continue

                if not current_head or not runtime_ai_status.is_approved_head_satisfied(task, current_head, approved_head):
                    if current_head and not runtime_ai_status.is_approved_head_satisfied(task, current_head, approved_head):
                        task["status"] = "review"
                        task["last_update"] = utc_now()
                        task["next"] = (
                            f"Branch HEAD ({current_head[:8]}) mutated after reviewer approval "
                            f"({approved_head[:8]}); re-review required."
                        )
                        task.pop("approved_head", None)
                        if not commit_canonical_task_transition(config, status):
                            return changed
                        write_activity_log(
                            config,
                            {
                                "type": "re-review_required",
                                "task_id": task_id,
                                "message": task["next"],
                            },
                        )
                        changed = True
                    else:
                        # B20: head unresolvable. Suppressing finalize here
                        # is correct, but doing it silently leaves the task
                        # parked in review_approved with no explanation for
                        # the operator. Emit once, not every cycle.
                        msg = (
                            f"Cannot verify branch HEAD for task {task_id} against the "
                            f"reviewer-approved head ({approved_head[:8]}); finalize dispatch "
                            "suppressed until it resolves."
                        )
                        if task.get("next") != msg:
                            task["next"] = msg
                            if not commit_canonical_task_transition(config, status):
                                return changed
                            write_activity_log(
                                config,
                                {
                                    "type": "approved_head_unresolved",
                                    "task_id": task_id,
                                    "message": msg,
                                },
                            )
                    continue

                pr_status = "UNKNOWN"
                ci_status = "unknown"
                try:
                    pr_status, ci_status = runtime_ai_status.task_pr_ci_status(task_id)
                except Exception as err:
                    console_log(f"Failed to check CI status for {task_id}: {err}", quiet=SUPERVISOR_LOG_QUIET)

                if ci_status == "pending":
                    now_ts = datetime.now(UTC).timestamp()
                    status_dirty = reassert_approved_review_gate_if_due(
                        config,
                        task,
                        now_ts=now_ts,
                    )
                    start_ts = task.get("ci_pending_since_ts")
                    if not start_ts:
                        task["ci_pending_since_ts"] = now_ts
                        task["ci_pending_since"] = utc_now()
                        status_dirty = True
                    elif now_ts - float(start_ts) > 1800:
                        approved_key = str(approved_head or "")
                        last_requeued_ts = task.get("ci_repair_last_requeued_ts")
                        try:
                            retry_due = (
                                last_requeued_ts is None
                                or now_ts - float(last_requeued_ts) >= 1800
                            )
                        except (TypeError, ValueError):
                            retry_due = True
                        if task.get("ci_repair_requeued_head") != approved_key or retry_due:
                            msg = (
                                f"CI status for task {task_id} has been pending for over 30 minutes; "
                                "owner requeued to refresh CI automatically."
                            )
                            if not requeue_task_for_ci_repair(
                                config,
                                status,
                                task,
                                message=msg,
                                clear_approval=False,
                                requeued_head=approved_key,
                                now_ts=now_ts,
                            ):
                                return changed
                            changed = True
                            continue
                    if status_dirty:
                        if not commit_canonical_task_transition(config, status):
                            return changed

                    continue
                elif ci_status == "failure":
                    msg = f"CI checks for task {task_id} failed; owner requeued to repair CI before re-review."
                    if requeue_task_for_ci_repair(
                        config,
                        status,
                        task,
                        message=msg,
                        clear_approval=True,
                    ):
                        changed = True
                    continue
                elif ci_status not in {"success", "none"}:
                    # B20: catch-all for probe states that are neither pending,
                    # failure, nor green (e.g. "unknown" when `gh` is
                    # unreachable). Fail closed, but say so once.
                    msg = (
                        f"CI status for task {task_id} is unresolved ({ci_status}); "
                        "finalize dispatch suppressed until it is conclusive."
                    )
                    if task.get("next") != msg:
                        task["next"] = msg
                        if not commit_canonical_task_transition(config, status):
                            return changed
                        write_activity_log(
                            config,
                            {
                                "type": "ci_status_unresolved",
                                "task_id": task_id,
                                "message": msg,
                            },
                        )
                    continue
                else:
                    if task.pop("ci_pending_since_ts", None) is not None:
                        if not commit_canonical_task_transition(config, status):
                            return changed

                # CI success on an open PR is only merge readiness, not task
                # completion. Dispatching an LLM here caused it to compose dev
                # and create a closeout commit, invalidating the exact head the
                # reviewer had frozen. The merge queue owns base composition;
                # the owner finalize lane starts only after GitHub says MERGED.
                if str(pr_status or "").strip().upper() != "MERGED":
                    # Enqueueing and explaining both belong to
                    # `advance_approved_prs_to_merge`, which runs earlier in
                    # this same call over a strictly wider set of tasks - it is
                    # not gated by owner capacity or head match. Routing here
                    # too meant a PR GitHub had just refused was retried a
                    # second time in the same tick, under a second message.
                    # This lane only has to keep the finalize worker away from
                    # the head the reviewer froze.
                    continue

                reason = "owned_finalize_dispatch"
                priority = 1
            elif task_status == "in_progress" and norm_task_owner == norm_target and dependencies_satisfied(task, task_map, dependency_done_statuses):
                reason = "owned_in_progress_dispatch"
                priority = 2
            elif task_status == "todo" and norm_task_owner == norm_target and dependencies_satisfied(task, task_map, dependency_done_statuses):
                reason = "owned_ready_dispatch"
                priority = 3

            helper_settings = settings.get("helper_execution_lease", {}) or {}
            if reason is None and helper_settings.get("enabled", True):
                claimable_statuses = {
                    str(value).lower()
                    for value in helper_settings.get("claimable_statuses", ["todo"])
                }
                claim = task.get("helper_execution_lease") or {}
                claimed_by = normalize_agent_id(str(claim.get("claimed_by") or ""))
                existing_claim_live = helper_claim_is_live(claim)
                independent = norm_target not in {norm_task_owner, norm_task_reviewer}
                owner_saturated = helper_owner_is_saturated(
                    config, task, agent_loads, helper_settings
                )
                if (
                    task_status in claimable_statuses
                    and dependencies_satisfied(task, task_map, dependency_done_statuses)
                    and independent
                    and (not existing_claim_live or claimed_by == norm_target)
                    and (
                        owner_saturated
                        or not helper_settings.get("require_owner_saturated", True)
                    )
                ):
                    reason = REASON_HELPER_CLAIM
                    priority = 4

            if reason is not None and not agent_can_take_task(config, target_agent, task):
                continue
            if reason is None or priority is None:
                continue
            if worktree_block_still_matches_dispatch(
                state,
                task,
                reason,
                task_map,
                retry_after_seconds=lease_block_retry_after_seconds(config),
            ):
                # An escalated block is terminal until something changes, so it
                # has to appear where an owner looks. Between 2026-08-19 and
                # 2026-08-20 this shape produced 341 blocked dispatches whose
                # only record was an activity-log line, and every one was
                # cleared by a person editing state by hand.
                blocked = escalated_lease_block(state, task)
                if blocked is not None:
                    msg = (
                        f"Dispatch for task {task_id} is stopped: the worker worktree lease has "
                        f"been blocked {blocked.get('count')} consecutive times with "
                        f"`{blocked.get('refresh_status')}`. Retrying does not clear this; an "
                        "owner must repair the worktree or correct the task record."
                    )
                    if task.get("next") != msg:
                        task["next"] = msg
                        if not commit_canonical_task_transition(config, status):
                            return changed
                        write_activity_log(
                            config,
                            {
                                "type": "dispatch_stopped_worktree_lease",
                                "task_id": task_id,
                                "message": msg,
                                "refresh_status": blocked.get("refresh_status"),
                                "consecutive_blocks": blocked.get("count"),
                            },
                        )
                        changed = True
                continue

            if is_sidecar_task:
                priority += SIDECAR_READY_PRIORITY_OFFSET

            event = build_dispatch_event(task, target_agent, reason, task_map)
            if event["key"] in pending_event_keys:
                continue
            candidates.append((task_priority_rank(task), priority, index, task, reason))

        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        queued_for_agent = 0
        for _, _, _, task, reason in candidates[:available_agent_slots]:
            if reason == REASON_HELPER_CLAIM:
                helper_settings = settings.get("helper_execution_lease", {}) or {}
                active_claims_for_agent = sum(
                    1
                    for candidate in tasks
                    if helper_claim_is_live(candidate.get("helper_execution_lease") or {})
                    and normalize_agent_id(
                        str((candidate.get("helper_execution_lease") or {}).get("claimed_by") or "")
                    )
                    == normalize_agent_id(target_agent)
                )
                if active_claims_for_agent >= int(helper_settings.get("max_claims_per_agent", 2)):
                    continue
                helper_dispatches = int(dispatch_state.get("helper_dispatches_this_tick", 0) or 0)
                chair_max = int(
                    ((state.get("capacity_controller", {}) or {}).get("chair_decision", {}) or {}).get(
                        "max_helper_claims", helper_settings.get("max_claims_per_tick", 4)
                    )
                    or 0
                )
                max_helper = min(int(helper_settings.get("max_claims_per_tick", 4)), chair_max or 0)
                if helper_dispatches >= max_helper:
                    continue
                now = datetime.now(UTC)
                generation = int((task.get("helper_execution_lease") or {}).get("generation", 0) or 0) + 1
                task["helper_execution_lease"] = {
                    "claimed_by": target_agent,
                    "original_owner": task.get(owner_field),
                    "claimed_at": now.isoformat().replace("+00:00", "Z"),
                    "lease_expires_at": (
                        now + timedelta(seconds=float(helper_settings.get("lease_seconds", 1800)))
                    ).isoformat().replace("+00:00", "Z"),
                    "reason": "owner_capacity_saturated_or_dispatch_sla_exceeded",
                    "generation": generation,
                }
                if not commit_canonical_task_transition(config, status):
                    task.pop("helper_execution_lease", None)
                    continue
                dispatch_state["helper_dispatches_this_tick"] = helper_dispatches + 1
                write_activity_log(
                    config,
                    {
                        "type": "helper_claim_leased",
                        "task_id": task.get(task_id_field),
                        "claimed_by": target_agent,
                        "owner": task.get(owner_field),
                        "lease_expires_at": task["helper_execution_lease"]["lease_expires_at"],
                        "message": "Idle capacity leased existing canonical work without changing owner.",
                    },
                )
            event = build_dispatch_event(task, target_agent, reason, task_map)
            if queue_dispatch_event_safely(config, event):
                pending_event_keys.add(event["key"])
                pending_agents.add(agent_id)
                pending_task_ids.add(str(task.get(task_id_field) or ""))
                pending_task_agents.add((str(task.get(task_id_field) or ""), agent_id))
                agent_loads.setdefault(target_agent, []).append(dispatch_reason_priority(reason) or 9)
                if quota_group:
                    pending_quota_counts[quota_group] = pending_quota_counts.get(quota_group, 0) + 1
                changed = True
                dispatches += 1
                queued_for_agent += 1
                if dispatches >= max_dispatches_per_tick:
                    break

        if dispatches >= max_dispatches_per_tick:
            break

    if agent_sequence and considered_agents and not agent_ids_override:
        dispatch_state["dispatch_cursor"] = (dispatch_cursor + considered_agents) % len(agent_sequence)
        raw_cursor_revision = dispatch_state.get("dispatch_cursor_revision", 0)
        try:
            if isinstance(raw_cursor_revision, bool):
                raise ValueError
            cursor_revision = int(raw_cursor_revision)
        except (TypeError, ValueError):
            cursor_revision = 0
        dispatch_state["dispatch_cursor_revision"] = max(0, cursor_revision) + 1
        dispatch_state["dispatch_cursor_updated_at"] = utc_now()
    dispatch_state["helper_dispatches_this_tick"] = 0
    return changed
