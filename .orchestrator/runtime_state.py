#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from common import (
    append_jsonl,
    approval_tool_input_preview,
    approval_tool_input_signature,
    config_path,
    load_json,
    load_jsonl,
    new_runtime_id,
    summarize_failure_reason,
    utc_now,
    write_json,
)


def default_state() -> dict[str, Any]:
    return {
        "version": 2,
        "initialized_at": None,
        "last_scan_at": None,
        "tasks": {},
        "recent_terminal_tasks": [],
        "pending_handoff_keys": [],
        "seen_event_keys": {},
        # The round-robin ready-dispatch cursor is durable scheduling state. If
        # it is dropped between loops, agents near the front of the sequence
        # can consume the per-tick budget forever and starve later reviewers.
        "ready_dispatcher": {
            "dispatch_cursor": 0,
            "dispatch_cursor_revision": 0,
            "dispatch_cursor_updated_at": None,
        },
        "queue": {
            "events": {},
        },
        "workers": {},
        "worker_worktrees": {
            "leases": {},
        },
        # Consecutive worktree-lease block counts per task. This has to be
        # durable: the escalation it feeds only fires after several *consecutive*
        # supervisor ticks, so a counter that resets on every save can never
        # reach its threshold and the alarm never fires at all.
        "worker_worktree_lease_blocks": {},
        "approvals": {
            "last_reconciled_at": None,
        },
        "provider_guardrails": {
            "dispatch_pauses": {},
            "task_failure_streaks": {},
        },
        "worker_runtime_metrics": {
            "version": 1,
            "updated_at": None,
            "totals": {},
            "last_measurements": {},
        },
        "watchdog": {
            "safe_mode_until": None,
            "safe_mode_reason": None,
            "safe_mode_started_at": None,
            "last_decision": None,
            "last_safe_mode_observed_until": None,
        },
        "coordination": {
            "last_scan_at": None,
            "files": {},
            "features": {},
        },
        "supervisor": {
            "pid": None,
            "started_at": None,
            "last_heartbeat_at": None,
            "lifecycle": "idle",
            "last_successful_loop_at": None,
            "last_loop_started_at": None,
            "last_loop_finished_at": None,
            "last_loop_duration_ms": None,
            "last_loop_error": None,
            "last_stage_timings_ms": {},
            "slowest_stage": None,
            "slowest_stage_duration_ms": None,
            "focus_mode": None,
            "mode_status": "idle",
            "mode_switch_requested": None,
            "last_mode_switch_at": None,
            "mode_occupancy": {
                "planning": {"running": 0, "pending": 0, "queued": 0},
                "execution": {"running": 0, "pending": 0, "queued": 0},
                "coordination": {"running": 0, "pending": 0, "queued": 0},
            },
        },
    }


# Top-level state keys that were deliberately removed from the runtime state
# contract. Only these are dropped by `migrate_state`; every other key on disk
# survives, whether or not `default_state()` knows about it.
RETIRED_STATE_KEYS: frozenset[str] = frozenset(
    {
        # Retired with the heuristic chair/sidecar scheduler.  Keeping these
        # opaque blobs made obsolete decisions look live after every restart.
        "chair_rotation",
        "underutilization",
        # A pre-runtime audit cache that is no longer read by any scheduler.
        "dispatch_audit",
    }
)


def _nonnegative_cursor_revision(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def migrate_state(raw: dict[str, Any] | None) -> dict[str, Any]:
    state = deepcopy(default_state())
    if not raw:
        return state
    # Preserve every top-level key the writer put in state, minus the keys
    # explicitly retired above. The previous filter kept only keys already
    # present in `default_state()` plus a hardcoded whitelist, which turned
    # "added a state key without also editing default_state()" into silent,
    # total data loss on every single save. That is exactly how
    # `worker_worktree_lease_blocks` was discarded for the entire life of the
    # worktree-lease escalation: the counter reset to 1 on each tick, its
    # threshold of 5 was unreachable, and one task blocked 372 consecutive
    # times over 23h without a single alarm (2026-08-07 -> 2026-08-08).
    # Retiring a key is now a deliberate edit to RETIRED_STATE_KEYS, not the
    # default outcome of forgetting one.
    state.update({k: v for k, v in raw.items() if k not in RETIRED_STATE_KEYS})
    state.setdefault("tasks", {})
    recent_terminal_tasks = state.get("recent_terminal_tasks")
    state["recent_terminal_tasks"] = recent_terminal_tasks if isinstance(recent_terminal_tasks, list) else []
    state.setdefault("pending_handoff_keys", [])
    state.setdefault("seen_event_keys", {})
    ready_dispatcher = state.get("ready_dispatcher")
    if not isinstance(ready_dispatcher, dict):
        ready_dispatcher = {}
        state["ready_dispatcher"] = ready_dispatcher
    legacy_cursor = ready_dispatcher.pop("weighted_cursor", 0)
    legacy_revision = ready_dispatcher.pop("weighted_cursor_revision", 0)
    legacy_updated_at = ready_dispatcher.pop("weighted_cursor_updated_at", None)
    try:
        ready_dispatcher["dispatch_cursor"] = max(
            0, int(ready_dispatcher.get("dispatch_cursor", legacy_cursor))
        )
    except (TypeError, ValueError):
        ready_dispatcher["dispatch_cursor"] = 0
    ready_dispatcher["dispatch_cursor_revision"] = _nonnegative_cursor_revision(
        ready_dispatcher.get("dispatch_cursor_revision", legacy_revision)
    )
    cursor_updated_at = ready_dispatcher.get("dispatch_cursor_updated_at", legacy_updated_at)
    if isinstance(cursor_updated_at, str) and cursor_updated_at:
        try:
            datetime.fromisoformat(cursor_updated_at.replace("Z", "+00:00"))
        except ValueError:
            cursor_updated_at = None
    else:
        cursor_updated_at = None
    ready_dispatcher["dispatch_cursor_updated_at"] = cursor_updated_at
    state.setdefault("queue", {})
    state["queue"].setdefault("events", {})
    state.setdefault("workers", {})
    state.setdefault("worker_worktrees", {})
    state["worker_worktrees"].setdefault("leases", {})
    lease_blocks = state.get("worker_worktree_lease_blocks")
    state["worker_worktree_lease_blocks"] = (
        {key: entry for key, entry in lease_blocks.items() if isinstance(entry, dict)}
        if isinstance(lease_blocks, dict)
        else {}
    )
    state.setdefault("approvals", {})
    state["approvals"].setdefault("last_reconciled_at", None)
    state.setdefault("provider_guardrails", {})
    state["provider_guardrails"].setdefault("dispatch_pauses", {})
    state["provider_guardrails"].setdefault("task_failure_streaks", {})
    state.setdefault("worker_runtime_metrics", {})
    state["worker_runtime_metrics"].setdefault("version", 1)
    state["worker_runtime_metrics"].setdefault("updated_at", None)
    state["worker_runtime_metrics"].setdefault("totals", {})
    state["worker_runtime_metrics"].setdefault("last_measurements", {})
    state.setdefault("watchdog", {})
    state["watchdog"].setdefault("safe_mode_until", None)
    state["watchdog"].setdefault("safe_mode_reason", None)
    state["watchdog"].setdefault("safe_mode_started_at", None)
    state["watchdog"].setdefault("last_decision", None)
    state["watchdog"].setdefault("last_safe_mode_observed_until", None)
    state.setdefault("coordination", {})
    state["coordination"].setdefault("last_scan_at", None)
    state["coordination"].setdefault("files", {})
    state["coordination"].setdefault("features", {})
    state.setdefault("supervisor", {})
    state["supervisor"].setdefault("pid", None)
    state["supervisor"].setdefault("started_at", None)
    state["supervisor"].setdefault("last_heartbeat_at", None)
    state["supervisor"].setdefault("lifecycle", "idle")
    state["supervisor"].setdefault("last_successful_loop_at", None)
    state["supervisor"].setdefault("last_loop_started_at", None)
    state["supervisor"].setdefault("last_loop_finished_at", None)
    state["supervisor"].setdefault("last_loop_duration_ms", None)
    state["supervisor"].setdefault("last_loop_error", None)
    state["supervisor"].setdefault("last_stage_timings_ms", {})
    state["supervisor"].setdefault("slowest_stage", None)
    state["supervisor"].setdefault("slowest_stage_duration_ms", None)
    state["supervisor"].setdefault("focus_mode", None)
    state["supervisor"].setdefault("mode_status", "idle")
    state["supervisor"].setdefault("mode_switch_requested", None)
    state["supervisor"].setdefault("last_mode_switch_at", None)
    state["supervisor"].setdefault("mode_occupancy", {})
    # `mode_occupancy` is nested beneath the durable supervisor record, so it
    # is not covered by the top-level retirement list above.
    state["supervisor"]["mode_occupancy"].pop("chair_review", None)
    for mode_name in ("planning", "execution", "coordination"):
        bucket = state["supervisor"]["mode_occupancy"].setdefault(mode_name, {})
        bucket.setdefault("running", 0)
        bucket.setdefault("pending", 0)
        bucket.setdefault("queued", 0)
    pauses = state.get("provider_guardrails", {}).get("dispatch_pauses", {}) or {}
    normalized_pauses: dict[str, Any] = {}
    for provider, entry in pauses.items():
        if not isinstance(entry, dict):
            continue
        summary = summarize_failure_reason(entry.get("reason"), provider)
        normalized = deepcopy(entry)
        normalized["summary"] = str(entry.get("summary") or summary.get("summary") or "").strip()
        normalized["detail"] = str(entry.get("detail") or summary.get("detail") or "").strip()
        normalized["failure_kind"] = str(entry.get("failure_kind") or summary.get("kind") or "").strip()
        normalized["reason"] = normalized["summary"]
        normalized_pauses[str(provider)] = normalized
    state["provider_guardrails"]["dispatch_pauses"] = normalized_pauses
    state["version"] = 2
    return state


ACTIVE_QUEUE_STATUSES = {"running", "waiting_approval", "suspended_approval", "retry_backoff", "manual_pending", "stalled", "started", "fallback"}


def _rebuild_queue_records(state: dict[str, Any], queued_events: list[dict[str, Any]]) -> None:
    valid_event_ids = [event.get("event_id") for event in queued_events if event.get("event_id")]
    queue = state.setdefault("queue", {})
    existing_records = queue.setdefault("events", {})
    queue["events"] = {
        event_id: deepcopy(existing_records.get(event_id, {"attempt_count": 0, "status": "queued"}))
        for event_id in valid_event_ids
    }

    workers = state.setdefault("workers", {})
    for event_id, record in queue["events"].items():
        related = [worker for worker in workers.values() if worker.get("queue_event_id") == event_id]
        if not related:
            continue
        latest = sorted(related, key=lambda item: item.get("last_event_at") or "", reverse=True)[0]
        if any(worker.get("status") in ACTIVE_QUEUE_STATUSES for worker in related):
            record["status"] = "manual_pending" if any(worker.get("status") in {"manual_pending", "waiting_approval"} for worker in related) else "started"
            continue
        if any(worker.get("status") == "failed" for worker in related):
            record["status"] = "failed"
            record["processed_at"] = latest.get("last_event_at")
            if latest.get("last_error"):
                record["error"] = latest.get("last_error")
            continue
        record["status"] = "completed"
        record["processed_at"] = latest.get("last_event_at")


def _pending_approval_run_ids(config: dict[str, Any]) -> set[str]:
    """Return approval anchors without letting a missing optional file block state GC."""
    try:
        return {
            str(item.get("worker_run_id") or "")
            for item in load_approval_state(config).get("pending", [])
            if item.get("worker_run_id")
        }
    except KeyError:
        return set()


def prune_orphaned_active_workers(
    state: dict[str, Any], *, pending_approval_runs: set[str] | None = None
) -> None:
    """Remove active-looking records whose durable coordination anchors are gone.

    This must run after every locked disk/memory merge as well as on load.  A
    merge deliberately preserves records written by another process, but an
    orphaned active record is not concurrent work: its queue event (and, for
    approval workers, its approval item) no longer exists.  Without this
    second pass a stale disk snapshot can resurrect a record the singleton
    supervisor already reaped.
    """
    valid_pending_event_ids = set(state.setdefault("queue", {}).setdefault("events", {}))
    workers = state.setdefault("workers", {})
    pending_approval_runs = pending_approval_runs or set()

    stale_manual_workers = [
        run_id
        for run_id, worker in workers.items()
        if isinstance(worker, dict)
        and worker.get("status") == "manual_pending"
        and worker.get("queue_event_id") not in valid_pending_event_ids
    ]
    for run_id in stale_manual_workers:
        workers.pop(run_id, None)

    # Approval-gated workers without a surviving queue event or pending
    # approval are likewise stale runtime leftovers.
    stale_approval_workers = [
        run_id
        for run_id, worker in workers.items()
        if isinstance(worker, dict)
        and worker.get("status") in {"waiting_approval", "suspended_approval"}
        and worker.get("queue_event_id") not in valid_pending_event_ids
        and str(run_id) not in pending_approval_runs
    ]
    for run_id in stale_approval_workers:
        workers.pop(run_id, None)




def prune_worker_records(state: dict[str, Any], tasks_by_id: dict[str, str] | None = None) -> None:
    tasks_by_id = tasks_by_id or {}
    queue_events = state.setdefault("queue", {}).setdefault("events", {})
    workers = state.setdefault("workers", {})
    keep: dict[str, Any] = {}
    for run_id, worker in workers.items():
        status = str(worker.get("status") or "")
        task_id = str(worker.get("task_id") or "")
        event_id = worker.get("queue_event_id")
        task_status = str(tasks_by_id.get(task_id) or "")
        if status in {"running", "started", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "fallback", "stalled"}:
            keep[run_id] = worker
            continue
        if event_id and event_id in queue_events and queue_events[event_id].get("status") not in {"completed", "failed", "done"}:
            keep[run_id] = worker
            continue
        if task_status and task_status not in {"done", "review_approved"} and status == "completed":
            keep[run_id] = worker
            continue
        # Drop terminal workers once the queue event is settled, or the task itself is already terminal.
        if status in {"failed", "completed", "superseded", "reassigned"}:
            continue
        keep[run_id] = worker
    state["workers"] = keep

def load_runtime_state(config: dict[str, Any]) -> dict[str, Any]:
    state = migrate_state(load_json(config_path(config, "state_file"), default=default_state()))
    queued_events = load_jsonl(config_path(config, "event_queue"))
    _rebuild_queue_records(state, queued_events)

    prune_orphaned_active_workers(
        state,
        pending_approval_runs=_pending_approval_run_ids(config),
    )

    prune_worker_records(state)
    return state


TERMINAL_WORKER_STATUSES = {
    "completed",
    "failed",
    "superseded",
    "reassigned",
    "cancelled",
    "done",
}
ACTIVE_WORKER_STATUSES = {
    "running",
    "started",
    "manual_pending",
    "waiting_approval",
    "suspended_approval",
    "retry_backoff",
    "stalled",
    "fallback",
}
TERMINAL_QUEUE_STATUSES = {"completed", "failed", "done", "cancelled"}
QUEUE_STATUS_RANK = {
    "queued": 0,
    "pending": 0,
    "retry_backoff": 1,
    "started": 2,
    "manual_pending": 2,
    "waiting_approval": 2,
    "suspended_approval": 2,
    "completed": 3,
    "failed": 3,
    "done": 3,
    "cancelled": 3,
}


def compact_worker_history(state: dict[str, Any], max_entries: int) -> None:
    """Bound durable worker history after all concurrent state is merged.

    Compaction before ``save_runtime_state`` is not sufficient: the locked
    disk/in-memory union deliberately preserves records created by concurrent
    writers, so terminal records removed by the singleton loop used to be
    resurrected on every save.  Compacting the merged snapshot under the same
    transaction lock preserves concurrent active runs while making retention
    authoritative at the point of persistence.
    """
    workers = state.get("workers")
    if not isinstance(workers, dict):
        state["workers"] = {}
        return
    limit = max(0, int(max_entries))
    if len(workers) <= limit:
        return

    active: dict[str, Any] = {}
    terminal: list[tuple[str, dict[str, Any]]] = []
    for run_id, worker in workers.items():
        if not isinstance(worker, dict):
            continue
        status = str(worker.get("status") or "").lower()
        if status in ACTIVE_WORKER_STATUSES:
            active[run_id] = worker
        else:
            terminal.append((run_id, worker))

    # Active runs are never discarded merely to satisfy a history limit.  If
    # they consume the whole budget, terminal history is temporarily empty.
    terminal_budget = max(0, limit - len(active))
    terminal.sort(
        key=lambda item: (
            str(item[1].get("last_event_at") or ""),
            str(item[0]),
        )
    )
    kept_terminal = dict(terminal[-terminal_budget:]) if terminal_budget else {}
    state["workers"] = {**kept_terminal, **active}


def _merge_worker_record(disk_worker: dict[str, Any], mem_worker: dict[str, Any]) -> dict[str, Any]:
    """Merge one immutable run record without ever reviving a terminal run."""
    disk_status = str(disk_worker.get("status") or "").lower()
    mem_status = str(mem_worker.get("status") or "").lower()

    # A run id is never reused. Once either writer has observed a terminal
    # transition, an older active snapshot cannot make that run active again.
    if mem_status in TERMINAL_WORKER_STATUSES:
        merged = deepcopy(disk_worker)
        merged.update(deepcopy(mem_worker))
        return merged
    if disk_status in TERMINAL_WORKER_STATUSES:
        merged = deepcopy(mem_worker)
        merged.update(deepcopy(disk_worker))
        return merged

    merged = deepcopy(disk_worker)
    merged.update(deepcopy(mem_worker))
    disk_heartbeat = str(disk_worker.get("last_heartbeat_at") or "")
    mem_heartbeat = str(mem_worker.get("last_heartbeat_at") or "")
    if disk_heartbeat > mem_heartbeat:
        merged["last_heartbeat_at"] = disk_worker.get("last_heartbeat_at")
    if not mem_status and disk_status:
        merged["status"] = disk_worker.get("status")
    return merged


def _merge_queue_record(disk_event: dict[str, Any], mem_event: dict[str, Any]) -> dict[str, Any]:
    """Preserve the furthest durable queue transition across concurrent saves."""
    disk_status = str(disk_event.get("status") or "").lower()
    mem_status = str(mem_event.get("status") or "").lower()
    disk_rank = QUEUE_STATUS_RANK.get(disk_status, 0)
    mem_rank = QUEUE_STATUS_RANK.get(mem_status, 0)

    if mem_status in TERMINAL_QUEUE_STATUSES:
        merged = deepcopy(disk_event)
        merged.update(deepcopy(mem_event))
        return merged
    if disk_status in TERMINAL_QUEUE_STATUSES:
        merged = deepcopy(mem_event)
        merged.update(deepcopy(disk_event))
        return merged

    merged = deepcopy(disk_event)
    merged.update(deepcopy(mem_event))
    if disk_rank > mem_rank:
        merged.update(deepcopy(disk_event))
    return merged


def merge_runtime_states(disk_state: dict[str, Any], in_mem_state: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(disk_state, dict):
        return migrate_state(in_mem_state)
    disk_state = migrate_state(disk_state)
    merged = migrate_state(in_mem_state)

    # The singleton loop owns the round-robin cursor. A monotonic revision,
    # advanced atomically with that cursor, prevents an older snapshot from
    # rolling scheduling fairness backward. The timestamp is informational.
    disk_dispatcher = disk_state.get("ready_dispatcher")
    mem_dispatcher = merged.get("ready_dispatcher")
    if isinstance(disk_dispatcher, dict):
        disk_revision = _nonnegative_cursor_revision(
            disk_dispatcher.get("dispatch_cursor_revision", 0)
        )
        mem_revision = (
            _nonnegative_cursor_revision(
                mem_dispatcher.get("dispatch_cursor_revision", 0)
            )
            if isinstance(mem_dispatcher, dict)
            else 0
        )
        if not isinstance(mem_dispatcher, dict) or disk_revision >= mem_revision:
            merged["ready_dispatcher"] = deepcopy(disk_dispatcher)

    disk_workers = disk_state.get("workers", {})
    if isinstance(disk_workers, dict):
        in_mem_workers = merged.setdefault("workers", {})
        for run_id, disk_worker in disk_workers.items():
            if not isinstance(disk_worker, dict):
                continue
            if run_id not in in_mem_workers:
                # A concurrent writer may have created and even finalized the
                # run after this writer loaded state. Preserve the whole record;
                # normal pruning owns history retention.
                in_mem_workers[run_id] = deepcopy(disk_worker)
            else:
                in_mem_workers[run_id] = _merge_worker_record(
                    disk_worker,
                    in_mem_workers[run_id],
                )

    disk_events = (disk_state.get("queue") or {}).get("events")
    if isinstance(disk_events, dict):
        mem_queue = merged.setdefault("queue", {})
        mem_events = mem_queue.setdefault("events", {})
        for evt_id, disk_evt in disk_events.items():
            if not isinstance(disk_evt, dict):
                continue
            if evt_id not in mem_events:
                mem_events[evt_id] = deepcopy(disk_evt)
            else:
                mem_events[evt_id] = _merge_queue_record(
                    disk_evt,
                    mem_events[evt_id],
                )

    if "workers" in merged:
        in_mem_state["workers"] = merged["workers"]
    if "queue" in merged:
        in_mem_state["queue"] = merged["queue"]

    return merged


@contextmanager
def runtime_state_transaction_lock(state_path: Path):
    """Serialize the complete state read/merge/write transaction across processes."""
    lock_path = state_path.with_name(f"{state_path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def save_runtime_state(config: dict[str, Any], state: dict[str, Any]) -> None:
    path = config_path(config, "state_file")
    with runtime_state_transaction_lock(path):
        disk_state = load_json(path, default={})
        if disk_state and isinstance(disk_state, dict):
            merged = merge_runtime_states(disk_state, state)
        else:
            merged = state

        # The append-only event queue is the authority for which queue event
        # ids still exist.  ``merge_runtime_states`` must preserve records
        # created by a concurrent writer, but its union semantics cannot tell a
        # concurrent addition from an event deliberately pruned from the
        # canonical queue.  Re-filter after the locked merge so a stale disk
        # snapshot cannot resurrect a deleted ``started`` lease forever.  A
        # concurrently appended event remains present because it is read from
        # the canonical queue at the latest point in the transaction.
        try:
            queued_events = load_jsonl(config_path(config, "event_queue"))
        except KeyError:
            queued_events = None
        if queued_events is not None:
            _rebuild_queue_records(merged, queued_events)
            # Re-apply the same authoritative-anchor pruning after the locked
            # union.  Otherwise a stale record present only on disk can be
            # reintroduced between `load_runtime_state` and this save.
            prune_orphaned_active_workers(
                merged,
                pending_approval_runs=_pending_approval_run_ids(config),
            )

        compact_worker_history(
            merged,
            int(config.get("supervisor", {}).get("max_worker_history", 200)),
        )

        persisted = migrate_state(merged)
        write_json(path, persisted)

    # Keep the caller's live view aligned with concurrent data incorporated
    # under the transaction lock.
    state.clear()
    state.update(deepcopy(persisted))



def load_event_queue(config: dict[str, Any]) -> list[dict[str, Any]]:
    return load_jsonl(config_path(config, "event_queue"))


EVENT_REQUIRED_FIELDS = ("target_agent", "provider", "reason", "message")


@contextmanager
def event_queue_transaction_lock(queue_path: Path):
    lock_path = queue_path.with_name(f"{queue_path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    """Validate and complete the only event envelope accepted by the queue."""
    if not isinstance(event, dict):
        raise TypeError("queue event must be an object")
    payload = deepcopy(event)
    for field in EVENT_REQUIRED_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"queue event field {field} must be a non-empty string")
        payload[field] = value.strip()
    payload["event_id"] = str(payload.get("event_id") or new_runtime_id("evt")).strip()
    payload["created_at"] = str(payload.get("created_at") or utc_now()).strip()
    for field in ("context_files", "target_files"):
        value = payload.get(field)
        if value is None:
            payload[field] = []
        elif not isinstance(value, list):
            raise TypeError(f"queue event {field} must be a list")
    metadata = payload.get("metadata")
    if metadata is None:
        payload["metadata"] = {}
    elif not isinstance(metadata, dict):
        raise TypeError("queue event metadata must be an object")
    return payload


def enqueue_event(config: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Normalize and append one delivery event under the queue transaction lock."""
    payload = _normalize_event(event)
    path = config_path(config, "event_queue")
    with event_queue_transaction_lock(path):
        append_jsonl(path, payload)
    return payload


def replace_event_queue(
    config: dict[str, Any],
    *,
    original_events: list[dict[str, Any]],
    retained_events: list[dict[str, Any]],
) -> None:
    """Atomically compact a queue snapshot without dropping later appends."""
    path = config_path(config, "event_queue")
    original_ids = {str(event.get("event_id") or "") for event in original_events}
    retained_ids = {str(event.get("event_id") or "") for event in retained_events}
    with event_queue_transaction_lock(path):
        current_events = load_jsonl(path)
        concurrent_events = [
            event
            for event in current_events
            if str(event.get("event_id") or "") not in original_ids
            and str(event.get("event_id") or "") not in retained_ids
        ]
        payload = [*retained_events, *concurrent_events]
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = "".join(f"{json.dumps(event, ensure_ascii=False)}\n" for event in payload)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, path)


def queue_event_record(state: dict[str, Any], event_id: str) -> dict[str, Any]:
    queue = state.setdefault("queue", {})
    events = queue.setdefault("events", {})
    record = events.setdefault(event_id, {"attempt_count": 0, "status": "queued"})
    return record


def default_approval_state() -> dict[str, Any]:
    return {
        "version": 2,
        "updated_at": None,
        "pending": [],
        "history": [],
    }


def _normalize_approval_item(item: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(item)
    tool_input = normalized.get("tool_input")
    signature = str(normalized.get("tool_input_signature") or "").strip()
    preview = str(normalized.get("tool_input_preview") or "").strip()
    if not signature:
        signature = approval_tool_input_signature(tool_input if tool_input is not None else {})
    if not preview and tool_input is not None:
        preview = approval_tool_input_preview(tool_input)
    normalized["tool_input_signature"] = signature
    normalized["tool_input_preview"] = preview
    normalized.pop("tool_input", None)
    normalized.pop("request_payload", None)
    normalized.pop("broker_decision", None)
    normalized.pop("permission_payload", None)
    return normalized


def load_approval_state(config: dict[str, Any]) -> dict[str, Any]:
    raw = load_json(config_path(config, "approval_queue"), default=default_approval_state())
    state = deepcopy(default_approval_state())
    if isinstance(raw, dict):
        state.update(raw)
    state.setdefault("pending", [])
    state.setdefault("history", [])
    state["pending"] = [_normalize_approval_item(item) for item in state["pending"] if isinstance(item, dict)]
    state["history"] = [_normalize_approval_item(item) for item in state["history"] if isinstance(item, dict)]
    state["version"] = 2
    return state


def save_approval_state(config: dict[str, Any], state: dict[str, Any]) -> None:
    payload = deepcopy(state)
    payload["pending"] = [_normalize_approval_item(item) for item in payload.get("pending", []) if isinstance(item, dict)]
    payload["history"] = [_normalize_approval_item(item) for item in payload.get("history", []) if isinstance(item, dict)]
    payload["version"] = 2
    payload["updated_at"] = utc_now()
    write_json(config_path(config, "approval_queue"), payload)
