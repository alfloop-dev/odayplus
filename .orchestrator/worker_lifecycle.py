from __future__ import annotations

# ruff: noqa: F821

"""Worker lifecycle logic extracted from legacy supervisor."""

from datetime import UTC, datetime
from typing import Any

from common import (
    isoformat_utc,
    parse_iso_timestamp,
)
from runtime_state import HANDED_OFF_WORKER_STATUSES, TERMINAL_WORKER_STATUSES


def _supervisor_module():
    import supervisor
    return supervisor


def _sync_supervisor_scope() -> None:
    sv = _supervisor_module()
    excluded = {"_parse_iso_utc", "__name__", "__doc__", "__package__", "__loader__", "__spec__", "__file__", "__cached__", "__builtins__", "Any", "_supervisor_module", "_sync_supervisor_scope", "_entrypoint", "_sync_scope_guard"}
    module_exports = {"process_queue", "poll_workers"}
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


_parse_iso_utc = parse_iso_timestamp


def _helper_worker_runtime_assignment_is_valid(
    config: dict[str, Any],
    worker: dict[str, Any],
    task_map: dict[str, dict[str, Any]],
    now: datetime,
) -> bool:
    """Keep an active helper alive after its dispatch claim is released.

    ``helper_execution_lease`` controls when another worker may be assigned
    the task; ``worker.lease_expires_at`` controls the process that is already
    running.  The former is intentionally allowed to expire while the latter
    remains valid.  The dispatch snapshot is the only durable record of which
    helper claim generation this process started with, so use it to distinguish
    claim expiry from a real owner/reviewer/claim handoff.
    """
    request = worker.get("request_snapshot")
    if not isinstance(request, dict) or str(request.get("reason") or "") != "helper_claim_dispatch":
        return False

    task_id = str(worker.get("task_id") or "")
    task = task_map.get(task_id)
    dispatched_task = worker_dispatch_task_snapshot(worker)
    if not isinstance(task, dict) or str(dispatched_task.get("id") or "") != task_id:
        return False

    task_status = str(task.get("status") or "").lower()
    owned_statuses = normalized_status_set(
        ready_dispatch_settings(config).get("owned_statuses"),
        ["in_progress", "todo"],
    )
    if task_status not in owned_statuses:
        return False

    schema = config.get("schema", {}) or {}
    owner_field = schema.get("assignee_field", "owner")
    reviewer_field = schema.get("reviewer_field", "reviewer")
    for field in (owner_field, reviewer_field):
        current_identity = normalize_agent_id(str(task.get(field) or ""))
        dispatched_identity = normalize_agent_id(
            str(dispatched_task.get(field) or dispatched_task.get("owner" if field == owner_field else "reviewer") or "")
        )
        if not current_identity or current_identity != dispatched_identity:
            return False

    dispatched_claim = dispatched_task.get("helper_execution_lease")
    if not isinstance(dispatched_claim, dict):
        return False
    generation = dispatched_claim.get("generation")
    if generation is None:
        return False
    try:
        generation = int(generation)
    except (TypeError, ValueError):
        return False

    worker_agent = normalize_agent_id(
        display_name_for(config, worker_logical_dispatch_agent_id(config, worker))
    )
    dispatched_claim_owner = normalize_agent_id(str(dispatched_claim.get("claimed_by") or ""))
    if not worker_agent or worker_agent != dispatched_claim_owner:
        return False

    runtime_lease_expires_at = _parse_iso_utc(str(worker.get("lease_expires_at") or ""))
    if runtime_lease_expires_at is None or now > runtime_lease_expires_at.astimezone(UTC):
        return False

    runtime_settings = worker_runtime_settings(config)
    heartbeat_stale_after = max(
        60,
        int(runtime_settings.get("heartbeat_stale_seconds", 300))
        + int(runtime_settings.get("heartbeat_grace_seconds", 60)),
    )
    process_activity_stale_after = max(
        60,
        int(config.get("supervisor", {}).get("stall_after_seconds", heartbeat_stale_after)),
    )
    has_fresh_activity = False
    for field, stale_after in (
        ("last_heartbeat_at", heartbeat_stale_after),
        ("last_process_activity_at", process_activity_stale_after),
    ):
        activity_at = _parse_iso_utc(str(worker.get(field) or ""))
        if activity_at is not None and (now - activity_at.astimezone(UTC)).total_seconds() <= stale_after:
            has_fresh_activity = True
            break
    if not has_fresh_activity:
        return False

    current_claim = task.get("helper_execution_lease")
    if isinstance(current_claim, dict) and current_claim:
        try:
            current_generation = int(current_claim.get("generation"))
        except (TypeError, ValueError):
            return False
        return (
            current_generation == generation
            and normalize_agent_id(str(current_claim.get("claimed_by") or "")) == worker_agent
        )

    # The supervisor removes an expired helper claim before this lifecycle poll.
    # Only treat an absent claim as that release when the worker's captured
    # claim really did expire; an unexplained claim disappearance must not
    # become a second assignment/lease mechanism.
    claim_expires_at = _parse_iso_utc(str(dispatched_claim.get("lease_expires_at") or ""))
    return claim_expires_at is not None and now >= claim_expires_at.astimezone(UTC)


@_entrypoint

def process_queue(
    config: dict[str, Any],
    state: dict[str, Any],
    provider_report: dict[str, Any],
    *,
    agent_ids_override: list[str] | None = None,
    agent_override: str | None = None,
    base_cache: dict | None = None,
) -> bool:
    allowed_agent_ids: set[str] | None = None
    if agent_ids_override is not None or agent_override is not None:
        allowed_agent_ids = set()
        if agent_ids_override:
            allowed_agent_ids.update(normalize_agent_id(a) for a in agent_ids_override if a)
        if agent_override:
            allowed_agent_ids.add(normalize_agent_id(agent_override))

    changed = False
    task_map = task_index_from_status(config, load_status(config))
    active_statuses = active_worker_statuses(config)
    for event in load_event_queue(config):
        event_id = event.get("event_id")
        if not event_id:
            continue
        if allowed_agent_ids is not None:
            target_agent = str(event.get("target_agent") or event.get("agent_id") or "").strip()
            if not target_agent:
                continue
            target_agent_id = normalize_agent_id(target_agent)
            if not target_agent_id or target_agent_id not in allowed_agent_ids:
                continue

        existing_record = state.get("queue", {}).get("events", {}).get(event_id, {})
        related_workers = [
            worker for worker in state.get("workers", {}).values() if worker.get("queue_event_id") == event_id
        ]
        if queue_event_is_orphaned(config, event, existing_record, related_workers):
            continue
        record = queue_event_record(state, event_id)
        if record.get("status") in {"started", "manual_pending", "completed", "failed"}:
            continue
        if record.get("status") == "retry_backoff":
            next_retry_at = _parse_iso_utc(str(record.get("next_retry_at") or ""))
            if next_retry_at is not None and next_retry_at > datetime.now(UTC):
                continue
        active_worker = next(
            (
                worker
                for worker in state.get("workers", {}).values()
                if (
                    worker.get("queue_event_id") == event_id
                    or (bool(event.get("task_id")) and worker.get("task_id") == event.get("task_id"))
                )
                and worker.get("status") in active_statuses
            ),
            None,
        )
        if active_worker:
            desired_status = "manual_pending" if active_worker.get("status") in {"manual_pending", "waiting_approval"} else "started"
            if record.get("status") != desired_status or record.get("run_id") != active_worker.get("run_id"):
                record["status"] = desired_status
                record["run_id"] = active_worker.get("run_id") or event_id
                record["lease_owner"] = active_worker.get("run_id") or event_id
                record["lease_acquired_at"] = record.get("lease_acquired_at") or active_worker.get("lease_acquired_at") or utc_now()
                record["lease_expires_at"] = active_worker.get("lease_expires_at") or queue_lease_expiry(config)
                record["processed_at"] = record.get("processed_at") or utc_now()
                sync_dispatched_task_status(config, event)
                changed = True
            continue
        skip_message = stale_dispatch_skip_message(config, event, task_map)
        if skip_message:
            record["status"] = "completed"
            record["processed_at"] = utc_now()
            record["skip_reason"] = "stale_dispatch_event"
            write_activity_log(
                config,
                {
                    "type": "wake_skipped",
                    "task_id": event.get("task_id"),
                    "target_agent": event.get("target_display_name") or event.get("target_agent"),
                    "message": skip_message,
                    "queue_event_id": event_id,
                },
            )
            changed = True
            continue
        try:
            request = build_request(config, event)
        except Exception as exc:
            record["status"] = "failed"
            record["processed_at"] = utc_now()
            record["error"] = f"Worker request construction failed closed: {type(exc).__name__}: {exc}"
            write_activity_log(
                config,
                {
                    "type": "wake_failed",
                    "task_id": event.get("task_id"),
                    "target_agent": event.get("target_display_name") or event.get("target_agent"),
                    "provider": event.get("provider"),
                    "message": record["error"],
                    "queue_event_id": event_id,
                },
            )
            changed = True
            continue
        request_provider = getattr(request, "provider", event.get("provider"))
        pause_entry = current_provider_dispatch_pause(state, request_provider, config)
        if pause_entry:
            pause_summary = str(pause_entry.get("summary") or pause_entry.get("reason") or "capacity guardrail active.")
            record["status"] = "failed"
            record["processed_at"] = utc_now()
            record["error"] = (
                f"Dispatch paused for provider {request_provider} until {pause_entry.get('blocked_until')}: "
                f"{pause_summary}"
            )
            write_activity_log(
                config,
                {
                    "type": "wake_skipped",
                    "task_id": event.get("task_id"),
                    "target_agent": event.get("target_display_name") or event.get("target_agent"),
                    "provider": request_provider,
                    "message": record["error"],
                    "queue_event_id": event_id,
                    "raw_ref": pause_entry.get("raw_ref"),
                },
            )
            changed = True
            continue
        request_agent_id = str(getattr(request, "agent_id", event.get("target_agent")) or "")
        auto_block_reason = agent_auto_dispatch_block_reason(config, state, request_agent_id, provider_report)
        if auto_block_reason:
            if auto_dispatch_block_is_temporary_capacity(auto_block_reason):
                record["status"] = "pending"
                record["last_wait_reason"] = f"Auto dispatch waiting for {request_agent_id}: {auto_block_reason}"
                record["capacity_wait_count"] = int(record.get("capacity_wait_count", 0) or 0) + 1
                record["last_capacity_wait_at"] = utc_now()
                reason_changed = record.get("last_capacity_wait_reason") != auto_block_reason
                record["last_capacity_wait_reason"] = auto_block_reason
                record_worker_runtime_measurement(
                    config,
                    state,
                    "dispatch_capacity_wait",
                    {"capacity_pending_queue_events": 1},
                    details={
                        "queue_event_id": event_id,
                        "task_id": event.get("task_id"),
                        "agent_id": request_agent_id,
                        "reason": auto_block_reason,
                        "capacity_wait_count": record["capacity_wait_count"],
                    },
                    emit_activity=reason_changed,
                )
                changed = True
                continue
            record["status"] = "failed"
            record["processed_at"] = utc_now()
            record["error"] = f"Auto dispatch unavailable for {request_agent_id}: {auto_block_reason}"
            write_activity_log(
                config,
                {
                    "type": "wake_skipped",
                    "task_id": event.get("task_id"),
                    "target_agent": event.get("target_display_name") or event.get("target_agent"),
                    "provider": str(getattr(request, "provider", event.get("provider") or "")),
                    "message": record["error"],
                    "queue_event_id": event_id,
                },
            )
            changed = True
            continue
        dispatch_agent_id = select_dispatch_agent_id(config, state, request_agent_id, active_statuses, provider_report)
        if dispatch_agent_id is None:
            record["status"] = "pending"
            record["last_wait_reason"] = f"All worker slots for {request_agent_id} are busy or dispatch-paused."
            changed = True
            continue
        if dispatch_agent_id != request_agent_id:
            request = build_request(config, event, agent_id_override=dispatch_agent_id)
        # Queue records carry enough identity to log a failed preflight even
        # when an adapter/test double returned only a request-like object.
        # Normal production requests always provide these attributes.
        request_provider = str(getattr(request, "provider", event.get("provider") or ""))
        request_agent_id = str(getattr(request, "agent_id", event.get("target_agent") or ""))
        request_task_id = str(getattr(request, "task_id", event.get("task_id") or ""))
        request_metadata = getattr(request, "metadata", {})
        if not isinstance(request_metadata, dict):
            request_metadata = {}
        try:
            workspace_kwargs = {
                "queue_event_id": str(event_id or ""),
                "target_agent": str(event.get("target_display_name") or event.get("target_agent") or ""),
            }
            if base_cache is not None:
                workspace_kwargs["base_cache"] = base_cache
            workspace_ok, workspace_message = prepare_worker_workspace(
                config,
                state,
                request,
                **workspace_kwargs,
            )
        except Exception as exc:
            record["status"] = "failed"
            record["processed_at"] = utc_now()
            record["error"] = f"Worker workspace preparation failed closed: {type(exc).__name__}: {exc}"
            write_activity_log(
                config,
                {
                    "type": "wake_failed",
                    "task_id": event.get("task_id"),
                    "target_agent": event.get("target_display_name") or event.get("target_agent"),
                    "provider": request_provider,
                    "message": record["error"],
                    "queue_event_id": event_id,
                },
            )
            changed = True
            continue
        if not workspace_ok:
            # A failed preflight has not leased a process or a slot.  Leaving it
            # pending turns one bad worktree into head-of-line blocking for the
            # complete logical agent/account pool.  Preserve the evidence, mark
            # this event terminal, and let the ready dispatcher consider the
            # next task immediately.  A later explicit task state/worktree
            # change produces a new event signature and can retry safely.
            record["status"] = "failed"
            record["processed_at"] = utc_now()
            record["error"] = f"Worker workspace preflight blocked: {workspace_message}"
            record["last_wait_reason"] = workspace_message
            record["worktree_lease_blocked_at"] = utc_now()
            task = task_map.get(str(event.get("task_id") or ""))
            if task:
                block_entry = (state.get("worker_worktree_lease_blocks") or {}).get(
                    normalize_agent_id(str(event.get("task_id") or "")) or str(event.get("task_id") or "")
                )
                if isinstance(block_entry, dict):
                    block_entry["dispatch_signature"] = ready_dispatch_signature(
                        task,
                        str(event.get("reason") or ""),
                        task_map,
                    )
            write_activity_log(
                config,
                {
                    "type": "dispatch_preflight_blocked",
                    "task_id": event.get("task_id"),
                    "target_agent": event.get("target_display_name") or event.get("target_agent"),
                    "provider": request_provider,
                    "queue_event_id": event_id,
                    "message": record["error"],
                },
            )
            changed = True
            continue
        record["attempt_count"] = int(record.get("attempt_count", 0)) + 1
        record["last_attempt_at"] = utc_now()
        ok, outcome, delivery = start_worker_for_request(
            config,
            state,
            provider_report,
            request,
            queue_event_id=event_id,
            attempt_count=record["attempt_count"],
            event_id_for_log=event_id,
        )
        if not ok:
            failure_worker = {
                "provider": request_provider,
                "agent_id": request_agent_id,
                "logical_agent_id": request_metadata.get("logical_agent_id"),
                "task_id": request_task_id,
                "queue_event_id": event_id,
                "run_id": record.get("run_id"),
                "retry_count": max(0, int(record.get("attempt_count", 0)) - 1),
            }
            failure_reason = str(outcome or "")
            failure = classify_worker_failure(config, failure_worker, failure_reason)
            failure_summary = summarize_failure_reason(failure_reason, request_provider)
            raw_ref = write_failure_evidence(
                config,
                worker=failure_worker,
                reason=failure_reason,
                failure_kind=str(failure.get("kind") or ""),
            )
            failure_count = record_task_failure_streak(
                state,
                failure_worker,
                failure_reason,
                failure_kind=str(failure.get("kind") or ""),
            )
            failure_kind = str(failure.get("kind") or "")
            if should_pause_dispatch_for_failure_kind(failure_kind):
                mark_provider_dispatch_paused(
                    config,
                    state,
                    request_provider,
                    failure_reason,
                    task_id=request_task_id,
                    failure_kind=str(failure.get("kind") or ""),
                    pause_kind=failure_kind,
                    raw_ref=raw_ref,
                    worker=failure_worker,
                )
            if is_terminal_quota_failure_kind(failure_kind):
                fence_account_pool_workers(config, state, failure_worker, failure_reason)
            if is_retryable_capacity_failure_kind(failure_kind):
                retry = worker_retry_settings(config, request_provider)
                retry_count = int(record.get("retry_count", 0))
                max_attempts = int(retry.get("max_attempts", 5))
                if retry_count < max_attempts:
                    schedule_queue_event_retry(
                        config,
                        record,
                        provider=request_provider,
                        reason=failure_summary.get("summary") or failure_reason,
                    )
                    write_activity_log(
                        config,
                        {
                            "type": "dispatch_retry_scheduled",
                            "provider": request_provider,
                            "task_id": request_task_id,
                            "queue_event_id": event_id,
                            "message": (
                                f"Transient dispatch failure detected ({failure.get('label')}); "
                                f"retry {record.get('retry_count')} scheduled at {record.get('next_retry_at')}: "
                                f"{failure_summary.get('summary') or failure_reason}"
                            ),
                            "next_retry_at": record.get("next_retry_at"),
                            "raw_ref": raw_ref,
                        },
                    )
                    changed = True
                    continue
            preserve_owner_for_pool_fallback = (
                is_terminal_quota_failure_kind(failure_kind)
                and antigravity_pool_fallback_available(config, request_provider)
            )
            reassigned_to = None
            if not preserve_owner_for_pool_fallback:
                reassigned_to = maybe_reassign_task_after_worker_failure(
                    config,
                    state,
                    failure_worker,
                    failure_summary.get("summary") or failure_reason,
                    terminal=True,
                    force=is_terminal_quota_failure_kind(failure_kind),
                    failure_count=failure_count,
                )
            if reassigned_to:
                record["status"] = "completed"
                record["processed_at"] = utc_now()
                record["error"] = failure_summary.get("summary") or ""
                if raw_ref:
                    record["raw_ref"] = raw_ref
                changed = True
                continue
            if preserve_owner_for_pool_fallback:
                _reset_queue_record_for_redispatch(
                    record,
                    reason=(
                        f"{failure_summary.get('summary') or failure_reason}; "
                        "retrying same Antigravity owner on fallback model pool"
                    ),
                )
                changed = True
                continue
            record["status"] = "failed"
            record["error"] = failure_summary.get("summary") or outcome
            if raw_ref:
                record["raw_ref"] = raw_ref
            record["processed_at"] = utc_now()
            changed = True
            continue

        worker_run_id = outcome or event_id
        queue_started_at = datetime.now(UTC)
        record["status"] = "manual_pending" if delivery and delivery.get("manual_confirmation_required") and not delivery.get("auto_delivered") else "started"
        record["run_id"] = worker_run_id
        record["lease_owner"] = worker_run_id
        record["lease_acquired_at"] = isoformat_utc(queue_started_at)
        record["lease_expires_at"] = queue_lease_expiry(config, queue_started_at)
        record["processed_at"] = isoformat_utc(queue_started_at)
        record.pop("last_wait_reason", None)
        sync_dispatched_task_status(config, event)
        changed = True
    return changed

@_entrypoint

def poll_workers(config: dict[str, Any], state: dict[str, Any], provider_report: dict[str, Any] | None = None) -> bool:
    changed = False
    approval_state = load_approval_state(config)
    status_snapshot = load_status(config)
    task_map = task_index_from_status(config, status_snapshot)
    valid_queue_event_ids = set(state.get("queue", {}).get("events", {}))
    redispatch_statuses = redispatch_candidate_statuses(config)
    active_statuses = active_worker_statuses(config)
    pending_by_run: dict[str, list[dict[str, Any]]] = {}
    resolved_by_run: dict[str, list[dict[str, Any]]] = {}
    for item in approval_state.get("pending", []):
        run_id = item.get("worker_run_id")
        if run_id:
            pending_by_run.setdefault(run_id, []).append(item)
    for item in approval_state.get("history", []):
        run_id = item.get("worker_run_id")
        if run_id:
            resolved_by_run.setdefault(run_id, []).append(item)

    stall_after = float(config.get("supervisor", {}).get("stall_after_seconds", 300))
    hard_inactivity_after = float(
        config.get("supervisor", {}).get("hard_inactivity_seconds", 3600)
    )
    now = datetime.now(UTC)
    if provider_report is None:
        provider_report = load_provider_report(config)
    changed = retry_due_workers(config, state, provider_report, now) or changed
    poll_counts = {
        "marker_updates": 0,
        "lease_refreshes": 0,
        "expired_lease_workers_failed": 0,
        "supersede_deferrals": 0,
    }
    workers = state.setdefault("workers", {})
    for run_id, worker in list(workers.items()):
        # These records already have a durable terminal disposition. Re-reading
        # their old marker/log after a later re-review or reviewer reopen must
        # never count the same run again or reassign the current lifecycle.
        if str(worker.get("status") or "").lower() in TERMINAL_WORKER_STATUSES:
            continue
        previous_last_event_at = worker.get("last_event_at")
        previous_last_process_activity_at = worker.get("last_process_activity_at")
        if worker.get("queue_event_id") and worker.get("queue_event_id") not in valid_queue_event_ids:
            if worker.get("status") in {"running", "waiting_approval", "retry_backoff", "manual_pending", "stalled"} and not pid_is_alive(worker.get("pid")):
                task_status = str(task_map.get(worker.get("task_id"), {}).get("status") or "").lower()
                # Preserve before the record is dropped, not after: `workers.pop`
                # is the last moment anything knows where this worker's
                # workspace was.
                preserve_dead_worker_worktree(
                    config,
                    state,
                    worker,
                    task=task_map.get(worker.get("task_id")),
                    trigger="orphaned_queue_event",
                )
                workers.pop(run_id, None)
                write_activity_log(
                    config,
                    {
                        "type": "worker_reaped",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": (
                            "Dropped orphaned worker after its queue event disappeared; open tasks will be redispatched."
                            if task_status in {"todo", "in_progress", "review", "blocked"}
                            else "Dropped orphaned worker after its queue event disappeared."
                        ),
                        "worker_run_id": worker.get("run_id"),
                    },
                )
                changed = True
                continue
        marker_changed = update_worker_runtime_markers(worker)
        if marker_changed:
            poll_counts["marker_updates"] += 1
            changed = True
        update_from_log(config, worker)
        try:
            adopted_approval = correlate_deferred_tool_approval(config, worker, approval_state)
        except Exception as error:  # pragma: no cover - queue write failures must fail closed, not crash
            adopted_approval = None
            write_activity_log(
                config,
                {
                    "type": "worker_deferred_approval_failed",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": f"Could not record deferred tool approval for {run_id}: {error}",
                    "worker_run_id": run_id,
                },
            )
        if adopted_approval is not None:
            bucket = pending_by_run if adopted_approval.get("status") == "pending" else resolved_by_run
            bucket.setdefault(run_id, []).append(adopted_approval)
            approval_state.setdefault(
                "pending" if adopted_approval.get("status") == "pending" else "history", []
            ).append(adopted_approval)
            changed = True
        alive = pid_is_alive(worker.get("pid"))
        if not alive and str(worker.get("status") or "").lower() == "stalled":
            current_task = task_map.get(str(worker.get("task_id") or ""), {})
            terminal_statuses = {
                str(value).lower()
                for value in ready_dispatch_settings(config).get(
                    "worker_terminal_statuses", ["review", "done", "review_approved"]
                )
            }
            progress_outcome = successful_worker_exit_outcome(
                worker,
                current_task,
                terminal_statuses=terminal_statuses,
            )
            if progress_outcome in {"lifecycle_complete", "review_decided", "incremental_progress"}:
                worker["status"] = "completed"
                worker["progress_outcome"] = progress_outcome
                message = (
                    "Reaped dead stalled worker after its task board state advanced; capacity released."
                    if progress_outcome != "incremental_progress"
                    else "Reaped dead stalled worker after recording incremental task progress; capacity released."
                )
                queue_status = "completed"
            else:
                worker["status"] = "failed"
                message = "Reaped dead stalled worker with no durable task progress; capacity released for redispatch."
                worker["last_error"] = message
                queue_status = "failed"
            worker["last_event_at"] = utc_now()
            # Runs for both outcomes. A worker that reached "incremental
            # progress" still died holding whatever it had not committed, and
            # the helper declines cheaply (`nothing_to_preserve`) when the
            # worktree is clean -- so there is no reason to gamble on which
            # exits leave work behind.
            preserve_dead_worker_worktree(
                config,
                state,
                worker,
                task=current_task,
                trigger=f"reaped_dead_stalled_worker:{queue_status}",
            )
            finalize_queue_event_record(
                config,
                state,
                worker,
                queue_status,
                None if queue_status == "completed" else message,
            )
            write_activity_log(
                config,
                {
                    "type": "worker_reaped" if queue_status == "completed" else "worker_failed",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": message,
                    "worker_run_id": worker.get("run_id"),
                    "progress_outcome": progress_outcome,
                },
            )
            changed = True
            continue
        if alive and worker.get("status") in active_statuses and worker.get("last_heartbeat_at"):
            if not worker_heartbeat_is_stale(config, worker, now):
                refresh_worker_lease(config, worker, now)
                poll_counts["lease_refreshes"] += 1
                if worker.get("queue_event_id"):
                    record = queue_event_record(state, worker["queue_event_id"])
                    record["lease_owner"] = worker.get("run_id")
                    record["lease_expires_at"] = queue_lease_expiry(config, now)
        if alive and worker.get("status") in active_statuses and worker_lease_is_expired(config, worker, now):
            terminate_worker_pid(worker.get("pid"))
            worker["status"] = "failed"
            worker["last_event_at"] = utc_now()
            worker["last_error"] = "Worker lease expired after heartbeat became stale."
            write_activity_log(
                config,
                {
                    "type": "worker_failed",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": worker["last_error"],
                    "worker_run_id": worker.get("run_id"),
                },
            )
            finalize_queue_event_record(config, state, worker, "failed", worker["last_error"])
            poll_counts["expired_lease_workers_failed"] += 1
            changed = True
            continue
        last_event_advanced = bool(
            previous_last_event_at
            and worker.get("last_event_at")
            and worker.get("last_event_at") > previous_last_event_at
        )
        process_activity_advanced = bool(
            worker.get("last_process_activity_at")
            and worker.get("last_process_activity_at") > str(previous_last_process_activity_at or "")
        )
        if manual_pending_inbox_can_auto_redeliver(config, state, provider_report, worker):
            changed = (
                requeue_stale_manual_pending_worker(
                    config,
                    state,
                    worker,
                    reason=(
                        "Cleared stale file_inbox/manual_pending worker after provider auto-delivery became available; "
                        "queue event returned to queued for redispatch."
                    ),
                )
                or changed
            )
            continue
        assignment_matches = worker_matches_current_assignment(config, worker, task_map)
        if not assignment_matches and alive:
            assignment_matches = _helper_worker_runtime_assignment_is_valid(
                config,
                worker,
                task_map,
                now,
            )
        accepted_dead_worker_transition = False
        if not assignment_matches and not alive and worker_runner_succeeded(worker):
            accepted_dead_worker_transition = successful_worker_exit_outcome(
                worker,
                task_map.get(str(worker.get("task_id") or ""), {}),
                terminal_statuses={
                    str(value).lower()
                    for value in ready_dispatch_settings(config).get(
                        "worker_terminal_statuses", ["done", "review_approved"]
                    )
                },
            ) in {"lifecycle_complete", "review_decided"}
        if (
            worker.get("queue_event_id")
            and not assignment_matches
            and not accepted_dead_worker_transition
        ):
            if worker.get("status") == "superseded":
                continue
            # A worker that just advanced its own task keeps a fresh heartbeat
            # while it tears down the CLI/MCP session and flushes its final
            # canonical status write. SIGTERM-ing it here truncates that
            # un-landed lifecycle write and forces a wasteful redispatch (the
            # observed owner->review->reopen churn). Defer the supersede while
            # the worker is alive with a fresh heartbeat, up to a bounded grace
            # window, so it can exit on its own and be reconciled cleanly on a
            # later poll. Stalled/dead workers (no fresh heartbeat) still
            # supersede immediately.
            heartbeat_is_fresh = bool(worker.get("last_heartbeat_at")) and not worker_heartbeat_is_stale(
                config, worker, now
            )
            grace_seconds = max(0, int(worker_runtime_settings(config).get("supersede_grace_seconds", 120)))
            if alive and heartbeat_is_fresh and grace_seconds > 0:
                deferred_since = _parse_iso_utc(str(worker.get("supersede_deferred_since") or ""))
                if deferred_since is None:
                    worker["supersede_deferred_since"] = utc_now()
                    poll_counts["supersede_deferrals"] += 1
                    write_activity_log(
                        config,
                        {
                            "type": "worker_supersede_deferred",
                            "provider": worker.get("provider"),
                            "task_id": worker.get("task_id"),
                            "message": (
                                "Deferred supersede: worker is still alive with a fresh heartbeat after its "
                                "task responsibility moved on; letting it finish and flush its lifecycle write."
                            ),
                            "worker_run_id": worker.get("run_id"),
                        },
                    )
                    changed = True
                    continue
                elapsed = (now - deferred_since.astimezone(UTC)).total_seconds()
                if elapsed < grace_seconds:
                    # Still within the grace window; wait for the next poll.
                    continue
                # Grace exhausted: the worker is not exiting on its own, so fall
                # through and reclaim it as before.
            worker.pop("supersede_deferred_since", None)
            if alive:
                terminate_worker_pid(worker.get("pid"))
            worker["status"] = "superseded"
            worker["last_event_at"] = utc_now()
            worker["last_error"] = "Worker superseded after task responsibility moved to another agent."
            finalize_queue_event_record(
                config,
                state,
                worker,
                "completed",
                worker["last_error"],
            )
            write_activity_log(
                config,
                {
                    "type": "worker_superseded",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": worker["last_error"],
                    "worker_run_id": worker.get("run_id"),
                },
            )
            console_log(
                f"worker superseded: task={worker.get('task_id')} provider={worker.get('provider')} run={worker.get('run_id')}",
                quiet=SUPERVISOR_LOG_QUIET,
            )
            changed = True
            continue
        if (
            worker.get("queue_event_id")
            and worker.get("status") in active_statuses
            and higher_priority_ready_task_exists(config, worker, task_map, state)
        ):
            if alive:
                terminate_worker_pid(worker.get("pid"))
            worker["status"] = "superseded"
            worker["last_event_at"] = utc_now()
            worker["last_error"] = "Worker superseded to prioritize higher-priority review/finalize work."
            finalize_queue_event_record(
                config,
                state,
                worker,
                "completed",
                worker["last_error"],
            )
            sync_preempted_task_status(config, worker)
            write_activity_log(
                config,
                {
                    "type": "worker_superseded",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": worker["last_error"],
                    "worker_run_id": worker.get("run_id"),
                },
            )
            console_log(
                f"worker superseded for priority escalation: task={worker.get('task_id')} provider={worker.get('provider')} run={worker.get('run_id')}",
                quiet=SUPERVISOR_LOG_QUIET,
            )
            changed = True
            continue
        if (
            not alive
            and worker.get("queue_event_id")
            and worker.get("status") in {"fallback", "manual_pending", "retry_backoff", "stalled", "waiting_approval", "suspended_approval"}
            and not worker_matches_current_assignment(config, worker, task_map)
        ):
            # Same reason as the orphaned-queue-event drop above: the record is
            # about to be deleted, and it is the only thing that knows where the
            # workspace is. Reassigning the task does not make whatever this
            # dead worker had written worth losing.
            preserve_dead_worker_worktree(
                config,
                state,
                worker,
                task=task_map.get(str(worker.get("task_id") or "")),
                trigger="assignment_moved",
            )
            workers.pop(run_id, None)
            finalize_queue_event_record(
                config,
                state,
                worker,
                "completed",
                "Dropped stale worker after task ownership/review assignment moved to another agent.",
            )
            write_activity_log(
                config,
                {
                    "type": "worker_reaped",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": "Dropped stale worker after task responsibility moved to another agent.",
                    "worker_run_id": worker.get("run_id"),
                },
            )
            changed = True
            continue
        # The stale-assignment reaping above is the only thing a handed-off parent
        # still participates in.  Past this point the loop classifies failures from
        # the worker's own log, which for a `fallback` parent still shows the very
        # failure that triggered the fallback -- replaying it would re-streak the
        # task and can reassign it away from the successor that is running now.
        if str(worker.get("status") or "").lower() in HANDED_OFF_WORKER_STATUSES:
            continue
        pending = pending_by_run.get(worker["run_id"], [])
        resolved = resolved_by_run.get(worker["run_id"], [])
        if pending:
            if not alive and not worker_supports_approval_resume(config, worker):
                worker["status"] = "failed"
                worker["deferred_action"] = None
                worker["deferred_tool_use"] = None
                worker["last_event_at"] = utc_now()
                worker["last_error"] = "Worker exited while waiting for approval."
                for approval in pending:
                    approval_id = approval.get("approval_id")
                    if not approval_id:
                        continue
                    try:
                        resolve_approval(
                            config,
                            approval_id,
                            decision="deny",
                            note="Auto-denied because the worker exited before approval could be applied.",
                            remember=False,
                        )
                    except KeyError:
                        pass
                write_activity_log(
                    config,
                    {
                        "type": "worker_failed",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": worker["last_error"],
                        "worker_run_id": worker["run_id"],
                    },
                )
                finalize_queue_event_record(config, state, worker, "failed", worker["last_error"])
                changed = True
                continue
            approval = pending[0]
            next_status = "waiting_approval" if pid_is_alive(worker.get("pid")) else "suspended_approval"
            if worker.get("status") != next_status:
                worker["status"] = next_status
                worker["deferred_action"] = approval.get("approval_id")
                worker["last_event_at"] = approval.get("created_at") or worker.get("last_event_at") or utc_now()
                write_activity_log(
                    config,
                    {
                        "type": "worker_waiting_approval",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": (
                            f"Worker suspended for approval {approval.get('approval_id')}"
                            if next_status == "suspended_approval"
                            else f"Worker waiting on approval {approval.get('approval_id')}"
                        ),
                        "worker_run_id": worker["run_id"],
                        "approval_id": approval.get("approval_id"),
                    },
                )
                if worker.get("queue_event_id"):
                    queue_event_record(state, worker["queue_event_id"])["status"] = "manual_pending"
                changed = True
            continue

        if worker.get("status") in {"waiting_approval", "suspended_approval"} and resolved:
            latest = resolved[-1]
            if latest.get("approval_id") != worker.get("last_approval_id"):
                worker["last_approval_id"] = latest.get("approval_id")
                if latest.get("decision") == "allow" and provider_uses_claude_cli(config, worker.get("provider")):
                    resumed = resume_claude_worker(config, worker, provider_report, approval=latest)
                    write_activity_log(
                        config,
                        {
                            "type": "worker_resumed",
                            "provider": worker.get("provider"),
                            "task_id": worker.get("task_id"),
                            "message": f"Resumed worker after approval {latest.get('approval_id')}",
                            "worker_run_id": worker["run_id"],
                            "approval_id": latest.get("approval_id"),
                            "command": resumed.get("command") if resumed else None,
                            "log_path": resumed.get("log_path") if resumed else None,
                            "allowed_tools": resumed.get("allowed_tools") if resumed else None,
                        },
                    )
                    changed = True
                    if resumed:
                        continue
                if latest.get("decision") == "deny":
                    worker["status"] = "failed"
                    worker["last_event_at"] = utc_now()
                    write_activity_log(
                        config,
                        {
                            "type": "worker_failed",
                            "provider": worker.get("provider"),
                            "task_id": worker.get("task_id"),
                            "message": latest.get("note") or "Worker approval denied.",
                            "worker_run_id": worker["run_id"],
                            "approval_id": latest.get("approval_id"),
                        },
                    )
                    finalize_queue_event_record(config, state, worker, "failed", latest.get("note") or "Worker approval denied.")
                    changed = True
                    continue
            changed = True

        current_status = worker.get("status")
        if current_status in {"waiting_approval", "suspended_approval"} and not pending:
            worker["deferred_action"] = None
            worker["deferred_tool_use"] = None
            if not resolved:
                worker["last_approval_id"] = None
            if alive:
                worker["status"] = "running"
                worker["last_event_at"] = utc_now()
            else:
                worker["status"] = "failed"
                worker["last_event_at"] = utc_now()
                worker["last_error"] = (
                    "Approval state disappeared before the worker could resume."
                    if current_status == "waiting_approval"
                    else "Approval state disappeared before the suspended worker could resume."
                )
                write_activity_log(
                    config,
                    {
                        "type": "worker_failed",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": worker["last_error"],
                        "worker_run_id": worker["run_id"],
                    },
                )
                finalize_queue_event_record(config, state, worker, "failed", worker["last_error"])
            changed = True

        if alive:
            if worker.get("status") == "stalled" and (last_event_advanced or process_activity_advanced):
                worker["status"] = "running"
                write_activity_log(
                    config,
                    {
                        "type": "worker_recovered",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": "Worker produced output or process activity after being marked stalled; status restored to running.",
                        "worker_run_id": worker["run_id"],
                    },
                )
                console_log(
                    f"worker recovered: task={worker.get('task_id')} provider={worker.get('provider')} run={worker.get('run_id')}",
                    quiet=SUPERVISOR_LOG_QUIET,
                )
                changed = True
                continue
            # Second, much slower tier.  The stall timer above trusts any counter
            # movement, which a CLI blocked forever on an unterminated shell
            # command still produces.  Storage I/O is what such a process stops
            # producing, so a worker that has written nothing for an hour is hung
            # even though it looks busy.  The window is long on purpose: a worker
            # waiting on a slow model reply is also quiet, just not for this long.
            disk_activity_dt = parse_iso_timestamp(str(worker.get("last_disk_activity_at") or ""))
            if hard_inactivity_after > 0 and disk_activity_dt is not None:
                inactive_seconds = (now - disk_activity_dt.astimezone(UTC)).total_seconds()
                if inactive_seconds >= hard_inactivity_after:
                    terminate_worker_pid(worker.get("pid"))
                    worker["status"] = "failed"
                    worker["last_event_at"] = utc_now()
                    worker["last_error"] = (
                        f"Worker produced no storage activity for {int(inactive_seconds)} seconds "
                        "while still reporting heartbeats; terminated for redispatch."
                    )
                    write_activity_log(
                        config,
                        {
                            "type": "worker_failed",
                            "provider": worker.get("provider"),
                            "task_id": worker.get("task_id"),
                            "message": worker["last_error"],
                            "worker_run_id": worker["run_id"],
                        },
                    )
                    finalize_queue_event_record(config, state, worker, "failed", worker["last_error"])
                    console_log(
                        f"worker terminated after hard inactivity: task={worker.get('task_id')} "
                        f"provider={worker.get('provider')} run={worker.get('run_id')}",
                        quiet=SUPERVISOR_LOG_QUIET,
                    )
                    changed = True
                    continue
            activity_times = [
                parse_iso_timestamp(str(value or ""))
                for value in (worker.get("last_event_at"), worker.get("last_process_activity_at"))
            ]
            activity_times = [value.astimezone(UTC) for value in activity_times if value is not None]
            if activity_times:
                last_dt = max(activity_times)
                stalled_for_seconds = (now - last_dt).total_seconds()
                if worker.get("status") == "stalled" and stalled_for_seconds >= stall_after * 2:
                    terminate_worker_pid(worker.get("pid"))
                    worker["status"] = "failed"
                    worker["last_event_at"] = utc_now()
                    worker["last_error"] = f"Worker remained stalled for {int(stalled_for_seconds)} seconds and was terminated for redispatch."
                    write_activity_log(
                        config,
                        {
                            "type": "worker_failed",
                            "provider": worker.get("provider"),
                            "task_id": worker.get("task_id"),
                            "message": worker["last_error"],
                            "worker_run_id": worker["run_id"],
                        },
                    )
                    finalize_queue_event_record(config, state, worker, "failed", worker["last_error"])
                    console_log(
                        f"worker terminated after extended stall: task={worker.get('task_id')} provider={worker.get('provider')} run={worker.get('run_id')}",
                        quiet=SUPERVISOR_LOG_QUIET,
                    )
                    changed = True
                    continue
                if (now - last_dt).total_seconds() >= stall_after and worker.get("status") != "stalled":
                    worker["status"] = "stalled"
                    write_activity_log(
                        config,
                        {
                            "type": "worker_stalled",
                            "provider": worker.get("provider"),
                            "task_id": worker.get("task_id"),
                            "message": f"Worker appears stalled after {int(stall_after)} seconds.",
                            "worker_run_id": worker["run_id"],
                        },
                    )
                    changed = True
            continue

        runner_succeeded = is_structured_successful_worker(worker)
        current_task = task_map.get(worker.get("task_id"), {})
        terminal_statuses = {
            str(value).lower()
            for value in ready_dispatch_settings(config).get(
                "worker_terminal_statuses", ["done", "review_approved"]
            )
        }
        success_outcome = successful_worker_exit_outcome(
            worker,
            current_task,
            terminal_statuses=terminal_statuses,
        )
        is_success = (
            runner_succeeded
            or success_outcome in {"lifecycle_complete", "review_decided", "incremental_progress"}
            or worker_is_discussion_planning(worker)
            or worker_is_coordination_dispatch(worker)
        )
        failure_reason = None if is_success else detect_worker_failure(worker)
        if failure_reason and worker.get("status") != "failed":
            failure = classify_worker_failure(config, worker, failure_reason)
            failure_summary = summarize_failure_reason(failure_reason, str(worker.get("provider") or worker.get("agent_id") or ""))
            raw_ref = write_failure_evidence(
                config,
                worker=worker,
                reason=failure_reason,
                failure_kind=str(failure.get("kind") or ""),
            )
            failure_count = record_task_failure_streak(
                state,
                worker,
                failure_reason,
                failure_kind=str(failure.get("kind") or ""),
            )
            console_log(
                f"worker failure: provider={worker.get('provider')} task={worker.get('task_id')} kind={failure.get('label')} transient={'yes' if failure.get('transient') else 'no'} reason={failure_reason}",
                quiet=SUPERVISOR_LOG_QUIET,
            )
            failure_kind = str(failure.get("kind") or "")
            if should_pause_dispatch_for_failure_kind(failure_kind):
                mark_provider_dispatch_paused(
                    config,
                    state,
                    str(worker.get("provider") or worker.get("agent_id") or ""),
                    failure_reason,
                    task_id=str(worker.get("task_id") or ""),
                    worker_run_id=str(worker.get("run_id") or ""),
                    failure_kind=str(failure.get("kind") or ""),
                    pause_kind=failure_kind,
                    raw_ref=raw_ref,
                    worker=worker,
                )
            if is_terminal_quota_failure_kind(failure_kind):
                fence_account_pool_workers(config, state, worker, failure_reason)
            if is_terminal_quota_failure_kind(failure_kind):
                reassigned_to = None
                if not antigravity_pool_fallback_available(
                    config, str(worker.get("provider") or worker.get("agent_id") or "")
                ):
                    reassigned_to = maybe_reassign_task_after_worker_failure(
                        config,
                        state,
                        worker,
                        failure_summary.get("summary") or failure_reason,
                        terminal=True,
                        force=True,
                        failure_count=failure_count,
                    )
                if reassigned_to:
                    worker["status"] = "reassigned"
                    worker["reassigned_to"] = reassigned_to
                    worker["last_error"] = failure_summary.get("summary") or failure_reason
                    worker["last_error_raw_ref"] = raw_ref
                    worker["last_event_at"] = utc_now()
                    finalize_queue_event_record(config, state, worker, "completed")
                    changed = True
                    continue
                worker["status"] = "failed"
                worker["last_error"] = failure_summary.get("summary") or failure_reason
                worker["last_error_raw_ref"] = raw_ref
                worker["last_event_at"] = utc_now()
                write_activity_log(
                    config,
                    {
                        "type": "worker_failed",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": failure_summary.get("summary") or failure_reason,
                        "worker_run_id": worker["run_id"],
                        "pr_url": worker.get("pr_url"),
                        "session_url": worker.get("session_url"),
                        "raw_ref": raw_ref,
                    },
                )
                finalize_queue_event_record(config, state, worker, "failed", failure_reason)
                changed = True
                continue
            if is_transient_worker_failure(config, worker, failure_reason):
                handled, retry_changed = maybe_trigger_retry_or_fallback(config, state, provider_report, worker, failure_reason)
                if handled:
                    changed = changed or retry_changed
                    continue
            reassigned_to = maybe_reassign_task_after_worker_failure(
                config,
                state,
                worker,
                failure_summary.get("summary") or failure_reason,
                terminal=True,
                failure_count=failure_count,
            )
            if reassigned_to:
                worker["status"] = "reassigned"
                worker["reassigned_to"] = reassigned_to
                worker["last_error"] = failure_summary.get("summary") or failure_reason
                worker["last_error_raw_ref"] = raw_ref
                worker["last_event_at"] = utc_now()
                finalize_queue_event_record(config, state, worker, "completed")
                changed = True
                continue
            worker["status"] = "failed"
            worker["last_error"] = failure_summary.get("summary") or failure_reason
            worker["last_error_raw_ref"] = raw_ref
            worker["last_event_at"] = utc_now()
            write_activity_log(
                config,
                {
                    "type": "worker_failed",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": failure_summary.get("summary") or failure_reason,
                    "worker_run_id": worker["run_id"],
                    "pr_url": worker.get("pr_url"),
                    "session_url": worker.get("session_url"),
                    "raw_ref": raw_ref,
                },
            )
            finalize_queue_event_record(config, state, worker, "failed", failure_summary.get("summary") or failure_reason)
            changed = True
            continue

        if worker.get("status") not in {"completed", "failed", "manual_pending"}:
            if worker_is_discussion_planning(worker):
                worker["status"] = "completed"
                worker["last_event_at"] = utc_now()
                clear_task_failure_streak(state, worker=worker)
                write_activity_log(
                    config,
                    {
                        "type": "worker_completed",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": "Discussion planning worker exited.",
                        "worker_run_id": worker["run_id"],
                        "pr_url": worker.get("pr_url"),
                        "session_url": worker.get("session_url"),
                    },
                )
                record_account_pool_canary_success(config, state, worker)
                finalize_queue_event_record(config, state, worker, "completed")
                changed = True
                continue
            if worker_is_coordination_dispatch(worker):
                worker["status"] = "completed"
                worker["last_event_at"] = utc_now()
                clear_task_failure_streak(state, worker=worker)
                write_activity_log(
                    config,
                    {
                        "type": "worker_completed",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": "Coordination worker exited after completing its handoff step.",
                        "worker_run_id": worker["run_id"],
                        "pr_url": worker.get("pr_url"),
                        "session_url": worker.get("session_url"),
                    },
                )
                finalize_queue_event_record(config, state, worker, "completed")
                changed = True
                continue
            task_status = str(task_map.get(worker.get("task_id"), {}).get("status") or "").lower()
            terminal_statuses = {
                str(value).lower()
                for value in ready_dispatch_settings(config).get("worker_terminal_statuses", ["done", "review_approved"])
            }
            current_task = task_map.get(worker.get("task_id"), {})
            success_outcome = successful_worker_exit_outcome(
                worker,
                current_task,
                terminal_statuses=terminal_statuses,
            )
            if success_outcome in {"lifecycle_complete", "review_decided", "incremental_progress"}:
                request_snapshot = worker.get("request_snapshot")
                raw_dispatch_reason = (
                    request_snapshot.get("reason")
                    if isinstance(request_snapshot, dict)
                    else worker.get("reason")
                )
                dispatch_reason = str(raw_dispatch_reason or "").strip()
                # This is the owner-to-reviewer boundary, not a generic worker
                # exit hook.  Reviewers, helper claims, and coordination runs
                # must never create a continuation record for the task owner.
                needs_owner_handoff_seal = dispatch_reason in {
                    REASON_OWNED_READY,
                    REASON_OWNED_IN_PROGRESS,
                }
                handoff_seal = (
                    seal_worker_handoff(config, state, worker, current_task)
                    if needs_owner_handoff_seal
                    else None
                )
                if handoff_seal is not None and not handoff_seal.accepted:
                    # The owner is already gone, so this must settle the run and
                    # release capacity.  For a submitted review, atomically
                    # revoke the reviewer handoff before dispatch can see it.
                    if str(current_task.get("status") or "").lower() == "review":
                        if not status_transition.reject_unsealed_worker_handoff(
                            config,
                            status_snapshot,
                            current_task,
                            worker_run_id=str(worker.get("run_id") or "") or None,
                            reason=handoff_seal.reason,
                            detail=handoff_seal.detail,
                        ):
                            worker["status"] = "failed"
                            worker["last_event_at"] = utc_now()
                            worker["last_error"] = "Worker handoff seal could not atomically revoke review dispatch."
                            finalize_queue_event_record(config, state, worker, "failed", worker["last_error"])
                            write_activity_log(
                                config,
                                {
                                    "type": "worker_failed",
                                    "provider": worker.get("provider"),
                                    "task_id": worker.get("task_id"),
                                    "message": worker["last_error"],
                                    "worker_run_id": worker.get("run_id"),
                                },
                            )
                            changed = True
                            continue
                    record_unsealed_worker_handoff(config, state, worker, current_task, handoff_seal)
                    worker["status"] = "completed"
                    worker["last_event_at"] = utc_now()
                    worker["progress_outcome"] = "handoff_seal_rejected"
                    worker["last_error"] = f"Handoff seal rejected: {handoff_seal.reason}: {handoff_seal.detail}"
                    finalize_queue_event_record(config, state, worker, "completed", worker["last_error"])
                    write_activity_log(
                        config,
                        {
                            "type": "worker_handoff_rejected",
                            "provider": worker.get("provider"),
                            "task_id": worker.get("task_id"),
                            "message": worker["last_error"],
                            "worker_run_id": worker.get("run_id"),
                            "handoff_reason": handoff_seal.reason,
                            "handoff_detail": handoff_seal.detail,
                        },
                    )
                    changed = True
                    continue
                if handoff_seal is not None:
                    clear_unsealed_worker_handoff(state, worker.get("task_id"))
                worker["status"] = "completed"
                worker["last_event_at"] = utc_now()
                worker["progress_outcome"] = success_outcome
                clear_task_failure_streak(state, worker=worker)
                message = (
                    "Background worker process exited after recording meaningful incremental progress; task remains dispatchable."
                    if success_outcome == "incremental_progress"
                    else "Background worker process exited after completing its required task lifecycle transition."
                )
                write_activity_log(
                    config,
                    {
                        "type": "worker_progress_recorded" if success_outcome == "incremental_progress" else "worker_completed",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": message,
                        "worker_run_id": worker["run_id"],
                        "pr_url": worker.get("pr_url"),
                        "session_url": worker.get("session_url"),
                        "progress_outcome": success_outcome,
                    },
                )
                finalize_queue_event_record(config, state, worker, "completed")
            elif task_status in redispatch_statuses:
                failure_reason = NO_PROGRESS_WORKER_EXIT_REASON
                failure_count = record_task_failure_streak(
                    state,
                    worker,
                    failure_reason,
                    failure_kind="no_progress",
                )
                generic_threshold = max(1, int(provider_guardrail_settings(config).get("generic_exit_reassign_after", 2)))
                reassigned_to = None
                if failure_count >= generic_threshold:
                    reassigned_to = maybe_reassign_task_after_worker_failure(
                        config,
                        state,
                        worker,
                        failure_reason,
                        terminal=True,
                        force=True,
                        failure_count=failure_count,
                    )
                if reassigned_to:
                    worker["status"] = "reassigned"
                    worker["reassigned_to"] = reassigned_to
                    worker["last_error"] = failure_reason
                    worker["last_event_at"] = utc_now()
                    finalize_queue_event_record(config, state, worker, "completed")
                    changed = True
                    continue
                worker["status"] = "failed"
                worker["last_event_at"] = utc_now()
                worker["last_error"] = failure_reason
                write_activity_log(
                    config,
                    {
                        "type": "worker_failed",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": worker["last_error"],
                        "worker_run_id": worker["run_id"],
                        "pr_url": worker.get("pr_url"),
                        "session_url": worker.get("session_url"),
                    },
                )
                finalize_queue_event_record(config, state, worker, "failed", worker["last_error"])
            else:
                worker["status"] = "failed"
                worker["last_event_at"] = utc_now()
                worker["last_error"] = GENERIC_WORKER_EXIT_REASON
                # This branch is reached when a worker died without producing any
                # recognised failure line. It used to update state and write the
                # activity log without printing anything, so a lane could fail
                # every single dispatch and the console would show only the
                # resulting reassignments. Whatever the cause, an unexplained
                # exit is worth one line.
                console_log(
                    f"worker exited unexplained: provider={worker.get('provider')} "
                    f"task={worker.get('task_id')} run={worker.get('run_id')} "
                    f"exit_code={worker.get('exit_code')}",
                    quiet=SUPERVISOR_LOG_QUIET,
                )
                write_activity_log(
                    config,
                    {
                        "type": "worker_failed",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": worker["last_error"],
                        "worker_run_id": worker["run_id"],
                        "pr_url": worker.get("pr_url"),
                        "session_url": worker.get("session_url"),
                    },
                )
                finalize_queue_event_record(config, state, worker, "failed", worker["last_error"])
            changed = True
    record_worker_runtime_measurement(
        config,
        state,
        "poll_workers",
        poll_counts,
        emit_activity=bool(poll_counts["expired_lease_workers_failed"]),
    )
    return changed
