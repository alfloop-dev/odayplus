from __future__ import annotations

"""Dispatch-focused logic extracted from legacy supervisor."""
# ruff: noqa: F821

from typing import Any


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
        'reassign_unavailable_reviewers', 
        'is_sidecar_review_of_current_parent', 
        'worker_logical_dispatch_agent_id', 
        'higher_priority_ready_task_exists', 
        'worker_matches_current_assignment', 
        'stale_dispatch_skip_message', 
        'ready_dispatch_signature', 
        'worktree_block_still_matches_dispatch', 
        'build_dispatch_event', 
        'dispatch_discussion_planning', 
        'dispatch_ready_tasks'
    }
    g = globals()
    for key, value in sv.__dict__.items():
        if key in excluded or key in module_exports or key.startswith("_"):
            continue
        g[key] = value


def _entrypoint(func):
    def _sync_scope_guard(*args, **kwargs):
        _sync_supervisor_scope()
        return func(*args, **kwargs)
    return _sync_scope_guard


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
    return None

@_entrypoint

def agent_dispatch_loads(
    config: dict[str, Any],
    state: dict[str, Any],
    active_statuses: set[str],
) -> dict[str, list[int]]:
    loads: dict[str, list[int]] = {}

    for worker in state.get("workers", {}).values():
        if worker.get("status") not in active_statuses:
            continue
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
    active_statuses = {str(value) for value in settings.get("active_worker_statuses", [])}
    active_agents, active_task_agents = active_worker_indexes(state, active_statuses)
    pending_agents, pending_task_agents, _pending_event_keys = outstanding_delivery_indexes(config, state)
    reserved_agents = set(active_agents) | set(pending_agents)
    reserved_tasks = {task_id for task_id, _agent_id in active_task_agents | pending_task_agents}
    candidate_agent_ids = dispatch_loop_agent_ids(config)
    changed = False

    for task in status.get(tasks_path, []) or []:
        task_id = str(task.get(task_id_field) or "")
        if not task_id or task_id in reserved_tasks:
            continue
        if str(task.get("status") or "").lower() not in review_statuses:
            continue
        owner = str(task.get(owner_field) or "").strip()
        reviewer = str(task.get(reviewer_field) or "").strip()
        if not reviewer or is_human_gate_agent(reviewer):
            continue
        reviewer_block_reason = agent_auto_dispatch_block_reason(
            config,
            state,
            normalize_agent_id(reviewer),
            provider_report,
        )
        reviewer_same_pool = not review_is_independent(config, owner, reviewer)
        if not reviewer_block_reason and not reviewer_same_pool:
            continue

        replacement = ""
        replacement_id = ""
        for candidate_id in candidate_agent_ids:
            candidate = display_name_for(config, candidate_id)
            candidate_config = (config.get("agents", {}) or {}).get(candidate_id)
            if (
                not candidate
                or candidate in {owner, reviewer}
                or candidate_id in reserved_agents
                or not isinstance(candidate_config, dict)
                or agent_is_dispatch_slot(candidate_config)
                or is_human_gate_agent(candidate)
                or not agent_can_take_task(config, candidate, task)
                or not review_is_independent(config, owner, candidate)
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
                f"Reassigned review to {replacement}: {reviewer} shares account pool "
                f"with owner {owner}, so independent review requires a different pool."
            )
        else:
            message = (
                f"Automatically reassigned review to {replacement} while reviewer {reviewer} "
                f"is dispatch-paused: {reviewer_block_reason}"
            )
        if not persist_task_reassignment(
            config,
            task_id=task_id,
            new_owner=owner,
            new_reviewer=replacement,
            message=message,
            handoff_to=replacement,
            handoff_from=reviewer,
        ):
            continue
        task[reviewer_field] = replacement
        task["next"] = message
        reserved_agents.add(replacement_id)
        reserved_tasks.add(task_id)
        changed = True
        write_activity_log(
            config,
            {
                "type": "task_reviewer_reassigned",
                "task_id": task_id,
                "message": message,
                "owner": owner,
                "from_reviewer": reviewer,
                "to_reviewer": replacement,
            },
        )
        console_log(
            f"reviewer failover: task={task_id} from={reviewer} to={replacement}",
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

def worker_logical_dispatch_agent_id(config: dict[str, Any], worker: dict[str, Any]) -> str:
    explicit = normalize_agent_id(str(worker.get("logical_agent_id") or ""))
    if explicit:
        return explicit
    agent_id = normalize_agent_id(str(worker.get("agent_id") or worker.get("provider") or ""))
    agent = config.get("agents", {}).get(agent_id, {}) or {}
    return normalize_agent_id(str(agent.get("dispatch_slot_for") or agent_id))

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
    active_statuses = {str(value) for value in settings.get("active_worker_statuses", [])}
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
        if other.get("status") not in active_statuses:
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
        return task.get(owner_field) == agent_name
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

@_entrypoint

def worktree_block_still_matches_dispatch(
    state: dict[str, Any],
    task: dict[str, Any],
    reason: str,
    task_map: dict[str, dict[str, Any]],
) -> bool:
    """Do not recreate an identical wake after a fail-closed worktree block.

    Any ownership, lifecycle, dependency or branch-state update changes the
    dispatch signature and makes the task eligible again.  This preserves
    automatic recovery without burning a provider slot every supervisor tick.
    """
    task_id = str(task.get("id") or "")
    entry = (state.get("worker_worktree_lease_blocks") or {}).get(normalize_agent_id(task_id) or task_id)
    if not isinstance(entry, dict):
        return False
    return str(entry.get("dispatch_signature") or "") == ready_dispatch_signature(task, reason, task_map)

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

    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    active_agents, _active_task_agents = active_worker_indexes(state, active_statuses)
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
    active_statuses = {str(value) for value in settings.get("active_worker_statuses", [])}
    max_dispatches_per_tick = max(1, int(max_dispatches_override or settings.get("max_dispatches_per_tick", 4)))

    active_agents, active_task_agents = active_worker_indexes(state, active_statuses)
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
                    msg = (
                        f"PR for task {task_id} is CI-green and awaiting merge queue; "
                        "approved branch head remains immutable and finalize dispatch is deferred."
                    )
                    if task.get("next") != msg:
                        task["next"] = msg
                        if not commit_canonical_task_transition(config, status):
                            return changed
                    continue

                reason = "owned_finalize_dispatch"
                priority = 1
            elif task_status == "in_progress" and norm_task_owner == norm_target and dependencies_satisfied(task, task_map, dependency_done_statuses):
                reason = "owned_in_progress_dispatch"
                priority = 2
            elif task_status == "todo" and norm_task_owner == norm_target and dependencies_satisfied(task, task_map, dependency_done_statuses):
                reason = "owned_ready_dispatch"
                priority = 3

            if reason is not None and not agent_can_take_task(config, target_agent, task):
                continue
            if reason is None or priority is None:
                continue
            if worktree_block_still_matches_dispatch(state, task, reason, task_map):
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
    return changed
