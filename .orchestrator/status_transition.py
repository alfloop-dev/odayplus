from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import fcntl
import subprocess
import sys
import uuid

import ai_status as runtime_ai_status

from github_reconciliation import (
CI_FAILURE,
CI_PENDING,
CI_UNRESOLVED,
    HEAD_MISMATCH,
    HEAD_UNRESOLVED,
    MISSING_APPROVED_HEAD,
    PR_NOT_MERGED,
    READY,
)


STATUS_WRITE_REVISION_FIELD = "_status_write_revision"
AGENT_OPEN_TASK_STATUSES = ("todo", "in_progress", "review", "review_approved", "blocked")


def _supervisor_module():
    import supervisor

    return supervisor


def write_status_snapshot_if_current(config: dict[str, Any], status: dict[str, Any]) -> bool:
    """Atomically reject stale supervisor status snapshots.

    Supervisor probes can spend seconds in git/GitHub calls after loading the
    board. A worker or operator may complete a canonical status command during
    that interval. Both writers share the same lock, and every successful write
    advances a UUID revision, so the old Supervisor snapshot fails closed and
    the next tick starts from canonical disk state.
    """

    sv = _supervisor_module()
    status_path = sv.config_path(config, "status_file")
    lock_path = status_path.with_name(f"{status_path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    expected_revision = status.get(STATUS_WRITE_REVISION_FIELD)

    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            latest = sv.load_json(status_path, default={}) or {}
            actual_revision = latest.get(STATUS_WRITE_REVISION_FIELD)
            if (
                expected_revision is not None
                and actual_revision is not None
                and actual_revision != expected_revision
            ):
                status.clear()
                status.update(latest)
                sv.write_activity_log(
                    config,
                    {
                        "type": "stale_status_write_rejected",
                        "message": (
                            "Supervisor discarded a stale ai-status.json snapshot after "
                            "a newer canonical writer advanced the status revision."
                        ),
                        "expected_revision": expected_revision,
                        "actual_revision": actual_revision,
                    },
                )
                return False

            status[STATUS_WRITE_REVISION_FIELD] = uuid.uuid4().hex
            sv.write_json(status_path, status)
            return True
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def sync_status_pipeline(config: dict[str, Any]) -> bool:
    sv = _supervisor_module()
    external_sync = getattr(sv, "sync_status_pipeline", None)
    if callable(external_sync):
        external_module = getattr(external_sync, "__module__", "")
        external_name = getattr(external_sync, "__name__", "")
        if not (external_module == "supervisor" and external_name == "sync_status_pipeline"):
            return bool(external_sync(config))

    script = sv.config_path(config, "status_file").parent / "scripts" / "ai_status.py"
    if not script.exists():
        sv.write_activity_log(
            config,
            {
                "type": "task_reassignment_sync_failed",
                "message": f"Status sync script not found at {script}.",
            },
        )
        return False

    timeout_seconds = float(config.get("supervisor", {}).get("external_command_timeout_seconds", 30))
    try:
        result = subprocess.run(
            [sys.executable, str(script), "sync"],
            cwd=str(sv.config_path(config, "status_file").parent),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        sv.write_activity_log(
            config,
            {
                "type": "task_reassignment_sync_failed",
                "message": f"Status sync timed out after {timeout_seconds:g}s.",
            },
        )
        return False
    if result.returncode == 0:
        return True
    sv.write_activity_log(
        config,
        {
            "type": "task_reassignment_sync_failed",
            "message": f"Status sync failed after reassignment: {result.stderr.strip() or result.stdout.strip() or 'unknown error'}",
        },
    )
    return False


def commit_canonical_task_transition(config: dict[str, Any], status: dict[str, Any]) -> bool:
    """Commit a scheduler transition through one canonical write/sync path."""

    return write_status_snapshot_if_current(config, status) and sync_status_pipeline(config)


def _task_index_from_status(config: dict[str, Any], status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    sv = _supervisor_module()
    schema = config.get("schema", {})
    tasks_path = schema.get("tasks_path", "tasks")
    task_id_field = schema.get("task_id_field", "id")
    return {
        str(task.get(task_id_field)): task
        for task in status.get(tasks_path, [])
        if task.get(task_id_field)
    }


def sync_dispatched_task_status(config: dict[str, Any], event: dict[str, Any]) -> bool:
    sv = _supervisor_module()
    reason = str(event.get("reason") or "").strip()
    from dispatch_policy import DISPATCH_STATUS_ACTIONS, REASON_OWNED_FINALIZE, REASON_OWNED_IN_PROGRESS, REASON_OWNED_READY

    action = DISPATCH_STATUS_ACTIONS.get(reason)
    if action is None:
        return False
    if not config.get("paths", {}).get("status_file"):
        return False

    script = sv.config_path(config, "status_file").parent / "scripts" / "ai_status.py"
    if not script.exists():
        sv.write_activity_log(
            config,
            {
                "type": "task_dispatch_sync_failed",
                "task_id": event.get("task_id"),
                "message": f"Dispatch status sync script not found at {script}.",
            },
        )
        return False

    task_id = str(event.get("task_id") or "").strip()
    target_agent = str(event.get("target_display_name") or sv.display_name_for(config, str(event.get("target_agent") or ""))).strip()
    if not task_id or not target_agent:
        return False

    command_name, eligible_statuses = action
    task = _task_index_from_status(config, sv.load_status(config)).get(task_id)
    if not task:
        return False
    if str(task.get("owner") or "").strip() != target_agent:
        return False
    if str(task.get("status") or "").lower() not in eligible_statuses:
        return False

    message = {
        REASON_OWNED_READY: f"Supervisor auto-started {task_id} after successful dispatch.",
        REASON_OWNED_FINALIZE: f"Supervisor resumed {task_id} for finalize after successful dispatch.",
        REASON_OWNED_IN_PROGRESS: f"Supervisor re-dispatched {task_id}; task remains in progress.",
    }[reason]
    env = __import__("os").environ.copy()
    env["AI_NAME"] = target_agent
    timeout_seconds = float(config.get("supervisor", {}).get("external_command_timeout_seconds", 30))
    try:
        result = subprocess.run(
            [sys.executable, str(script), command_name, task_id, message],
            cwd=str(sv.config_path(config, "status_file").parent),
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        sv.write_activity_log(
            config,
            {
                "type": "task_dispatch_sync_failed",
                "task_id": task_id,
                "target_agent": target_agent,
                "dispatch_reason": reason,
                "message": f"Dispatch status sync timed out after {timeout_seconds:g}s.",
            },
        )
        return False
    if result.returncode == 0:
        sv.write_activity_log(
            config,
            {
                "type": "task_dispatch_synced",
                "task_id": task_id,
                "target_agent": target_agent,
                "dispatch_reason": reason,
                "message": message,
            },
        )
        return True

    sv.write_activity_log(
        config,
        {
            "type": "task_dispatch_sync_failed",
            "task_id": task_id,
            "target_agent": target_agent,
            "dispatch_reason": reason,
            "message": result.stderr.strip() or result.stdout.strip() or "Dispatch status sync failed.",
        },
    )
    return False


def sync_preempted_task_status(config: dict[str, Any], worker: dict[str, Any]) -> bool:
    sv = _supervisor_module()
    from dispatch import worker_logical_dispatch_agent_id
    from dispatch_policy import REASON_OWNED_FINALIZE, REASON_OWNED_IN_PROGRESS, REASON_OWNED_READY

    if not config.get("paths", {}).get("status_file"):
        return False

    dispatch_reason = str(worker.get("request_snapshot", {}).get("reason") or "").strip()
    task_id = str(worker.get("task_id") or "").strip()
    target_agent = sv.display_name_for(
        config,
        worker_logical_dispatch_agent_id(config, worker) or str(worker.get("provider") or ""),
    ).strip()
    if not task_id or not target_agent:
        return False

    status = sv.load_status(config)
    task = _task_index_from_status(config, status).get(task_id)
    if not task:
        return False
    if str(task.get("owner") or "").strip() != target_agent:
        return False

    task_status = str(task.get("status") or "").lower()
    timestamp = sv.utc_now()
    message = ""

    if dispatch_reason in {REASON_OWNED_READY, REASON_OWNED_IN_PROGRESS}:
        if task_status != "in_progress":
            return False
        task["status"] = "todo"
        message = (
            f"Supervisor preempted {task_id} to free {target_agent} for higher-priority review/finalize work; "
            "task returned to todo until a fresh run restarts it."
        )
    elif dispatch_reason == REASON_OWNED_FINALIZE:
        if task_status != "review_approved":
            return False
        message = (
            f"Supervisor paused finalize on {task_id} to free {target_agent} for higher-priority review work; "
            "task remains review_approved."
        )
    else:
        return False

    task["last_update"] = timestamp
    task["next"] = message
    synced = commit_canonical_task_transition(config, status)
    if synced:
        sv.write_activity_log(
            config,
            {
                "type": "task_preempted_synced",
                "task_id": task_id,
                "target_agent": target_agent,
                "dispatch_reason": dispatch_reason,
                "message": message,
            },
        )
    else:
        sv.write_activity_log(
            config,
            {
                "type": "task_preempt_sync_failed",
                "task_id": task_id,
                "target_agent": target_agent,
                "dispatch_reason": dispatch_reason,
                "message": f"Failed to persist preempted task truth for {task_id}.",
            },
        )
    return synced


def reassert_approved_review_gate_if_due(
    config: dict[str, Any],
    task: dict[str, Any],
    *,
    now_ts: float | None = None,
) -> bool:
    sv = _supervisor_module()
    settings = sv.ready_dispatch_settings(config)
    try:
        interval = max(30.0, float(settings.get("review_gate_reassert_seconds", 300)))
    except (TypeError, ValueError):
        interval = 300.0
    current_ts = now_ts if now_ts is not None else datetime.now(UTC).timestamp()
    try:
        last_ts = float(task.get("review_gate_reasserted_at_ts") or 0)
    except (TypeError, ValueError):
        last_ts = 0.0
    if last_ts and current_ts - last_ts < interval:
        return False

    runtime_ai_status.emit_task_review_status_check(task, "review_approved")
    task["review_gate_reasserted_at_ts"] = current_ts
    task["review_gate_reasserted_at"] = sv.utc_now()
    return True


def normalized_business_priority(value: Any, default: str = "P2") -> str:
    priority = str(value or "").strip().upper()
    return priority if priority in {"P0", "P1", "P2", "P3"} else default


def repair_open_task_metadata(config: dict[str, Any], status: dict[str, Any]) -> bool:
    sv = _supervisor_module()
    paths = config.get("paths") or {}
    if not paths.get("status_file") or not paths.get("activity_log"):
        return False

    tasks = status.get("tasks", []) or []
    task_map = {str(task.get("id") or ""): task for task in tasks if task.get("id")}
    changed = False
    timestamp = sv.utc_now()
    for task in tasks:
        if str(task.get("status") or "").strip().lower() not in AGENT_OPEN_TASK_STATUSES:
            continue
        if str(task.get("priority") or "").strip().upper() in {"P0", "P1", "P2", "P3"}:
            continue
        task_id = str(task.get("id") or "").strip()
        parent_id = str(task.get("helper_parent") or "").strip()
        if not parent_id and "-SIDECAR-" in task_id:
            parent_id = task_id.split("-SIDECAR-", 1)[0]
        parent = task_map.get(parent_id, {})
        priority = normalized_business_priority(parent.get("priority"), default="P2")
        task["priority"] = priority
        task["last_update"] = timestamp
        sv.write_activity_log(
            config,
            {
                "type": "task_metadata_integrity_repaired",
                "task_id": task_id,
                "message": f"Backfilled required business priority as {priority}.",
            },
        )
        changed = True
    if changed:
        if not commit_canonical_task_transition(config, status):
            return False
    return changed


def repair_unsubmitted_review_tasks(config: dict[str, Any], status: dict[str, Any]) -> bool:
    sv = _supervisor_module()
    paths = config.get("paths") or {}
    if not paths.get("status_file") or not paths.get("activity_log"):
        return False

    changed = False
    timestamp = sv.utc_now()
    for task in status.get("tasks", []) or []:
        if str(task.get("status") or "").strip().lower() != "review":
            continue
        if sv.review_submission_is_complete(config, task):
            continue
        task_id = str(task.get("id") or "").strip()
        if sv.task_is_human_gate(task):
            task["status"] = "blocked"
            task["waiting_for"] = "Human/Ops"
            message = (
                "Review state repaired: this is a Human/Ops input/approval gate, not a submitted code review. "
                "It remains blocked until the accountable human evidence is supplied."
            )
        else:
            task["status"] = "in_progress"
            task.pop("waiting_for", None)
            if sv.task_is_sidecar(task) and task.get("depends_on"):
                task["review_submission_context_dependencies"] = list(task.get("depends_on") or [])
                task["depends_on"] = []
            message = (
                "Review state repaired: no verified remote task PR was recorded. The owner must publish via "
                "delivery_toolchain/git/task_finalize.sh before review can be dispatched."
            )
        task["last_update"] = timestamp
        task["next"] = message
        task.pop("approved_head", None)
        for handoff in status.get("handoffs", ()) or []:
            if handoff.get("task_id") == task_id and handoff.get("status") != "done":
                handoff["status"] = "done"
                handoff["resolved_at"] = timestamp
        sv.write_activity_log(config, {"type": "review_submission_repaired", "task_id": task_id, "message": message})
        changed = True
    if changed:
        if not commit_canonical_task_transition(config, status):
            return False
    return changed


def requeue_task_for_ci_repair(
    config: dict[str, Any],
    status: dict[str, Any],
    task: dict[str, Any],
    *,
    message: str,
    clear_approval: bool,
    requeued_head: str | None = None,
    now_ts: float | None = None,
) -> bool:
    sv = _supervisor_module()
    task_id = str(task.get("id") or "")
    status_tasks = status.get("tasks", []) or []
    if (
        not task_id
        or not any(item is task for item in status_tasks)
        or sv.task_is_human_gate(task)
        or bool(task.get("non_dispatchable"))
    ):
        return False

    if str(task.get("status") or "").lower() != "review_approved":
        return False

    task["status"] = "in_progress"
    task["last_update"] = sv.utc_now()
    task["next"] = message
    task.pop("ci_pending_since_ts", None)
    task.pop("ci_pending_since", None)
    task["ci_repair_last_requeued_ts"] = (
        datetime.now(UTC).timestamp() if now_ts is None else now_ts
    )
    if requeued_head is not None:
        task["ci_repair_requeued_head"] = requeued_head
    if clear_approval:
        task.pop("approved_head", None)
    if not _supervisor_module().commit_canonical_task_transition(config, status):
        return False
    sv.write_activity_log(
        config,
        {
            "type": "ci_repair_requeued",
            "task_id": task_id,
            "message": message,
            "approval_cleared": clear_approval,
        },
    )
    return True
