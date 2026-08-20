from __future__ import annotations

from typing import Any

from common import normalize_agent_id

REASON_REVIEW_READY = "review_ready_dispatch"
REASON_OWNED_FINALIZE = "owned_finalize_dispatch"
REASON_OWNED_IN_PROGRESS = "owned_in_progress_dispatch"
REASON_OWNED_READY = "owned_ready_dispatch"
REASON_HELPER_CLAIM = "helper_claim_dispatch"

EXECUTION_DISPATCH_REASONS = {
    REASON_REVIEW_READY,
    REASON_OWNED_FINALIZE,
    REASON_OWNED_IN_PROGRESS,
    REASON_OWNED_READY,
    REASON_HELPER_CLAIM,
}

DISPATCH_REASON_PRIORITIES = {
    REASON_REVIEW_READY: 0,
    REASON_OWNED_FINALIZE: 1,
    REASON_OWNED_IN_PROGRESS: 2,
    REASON_OWNED_READY: 3,
    REASON_HELPER_CLAIM: 4,
}

DISPATCH_STATUS_ACTIONS = {
    REASON_OWNED_READY: ("start", {"todo"}),
    REASON_OWNED_FINALIZE: ("note", {"review_approved"}),
    REASON_OWNED_IN_PROGRESS: ("progress", {"in_progress"}),
    REASON_HELPER_CLAIM: ("progress", {"todo", "in_progress"}),
}

DEFAULT_REVIEW_STATUSES = ["review"]
DEFAULT_FINALIZE_STATUSES = ["review_approved"]
DEFAULT_OWNED_STATUSES = ["in_progress", "todo"]
DEFAULT_SIDECAR_ONLY_AGENTS: list[str] = []
DEFAULT_DISABLED_AGENTS: list[str] = []
DEFAULT_DEPENDENCY_DONE_STATUSES = ["done"]
DEFAULT_WORKER_TERMINAL_STATUSES = ["review", "done", "review_approved"]
DEFAULT_ACTIVE_WORKER_STATUSES = [
    "running",
    "waiting_approval",
    "retry_backoff",
    "stalled",
]
DEFAULT_MAX_DISPATCHES_PER_TICK = 4
DEFAULT_ORPHANED_QUEUE_EVENT_GRACE_SECONDS = 300
DEFAULT_WORKER_OS_DUPLICATE_GUARD = True
DEFAULT_MAX_ACTIVE_WORKERS_PER_TASK = 1


def dispatch_reason_priority(reason: str | None) -> int | None:
    return DISPATCH_REASON_PRIORITIES.get(str(reason or ""))


def is_execution_dispatch_reason(reason: str | None) -> bool:
    return str(reason or "") in EXECUTION_DISPATCH_REASONS


def task_priority_rank(task: dict[str, Any] | None) -> int:
    """Return the durable business priority rank used by the ready dispatcher.

    Lifecycle work (review/finalize/execute) remains a tie-breaker, not the
    primary priority.  Older state files often omit a priority, so an unset or
    malformed value deliberately sorts after P0-P3 instead of silently being
    treated as P0.
    """
    value = str((task or {}).get("priority") or "").strip().upper()
    return {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(value, 4)


def normalized_status_set(values: Any, default: list[str]) -> set[str]:
    if values is None:
        values = default
    if isinstance(values, str):
        values = [values]
    return {str(value).lower() for value in list(values or [])}


def ready_dispatch_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = dict(config.get("ready_dispatcher", {}) or {})
    settings.setdefault("enabled", True)
    settings.setdefault("review_statuses", list(DEFAULT_REVIEW_STATUSES))
    settings.setdefault("finalize_statuses", list(DEFAULT_FINALIZE_STATUSES))
    settings.setdefault("owned_statuses", list(DEFAULT_OWNED_STATUSES))
    settings.setdefault("sidecar_only_agents", list(DEFAULT_SIDECAR_ONLY_AGENTS))
    settings.setdefault("disabled_agents", list(DEFAULT_DISABLED_AGENTS))
    legacy_done_statuses = settings.get("done_statuses", list(DEFAULT_WORKER_TERMINAL_STATUSES))
    settings.setdefault("dependency_done_statuses", list(DEFAULT_DEPENDENCY_DONE_STATUSES))
    settings.setdefault("worker_terminal_statuses", legacy_done_statuses)
    settings.setdefault("active_worker_statuses", list(DEFAULT_ACTIVE_WORKER_STATUSES))
    settings.setdefault("max_dispatches_per_tick", DEFAULT_MAX_DISPATCHES_PER_TICK)
    settings.setdefault("orphaned_queue_event_grace_seconds", DEFAULT_ORPHANED_QUEUE_EVENT_GRACE_SECONDS)
    settings.setdefault("worker_os_duplicate_guard", DEFAULT_WORKER_OS_DUPLICATE_GUARD)
    settings.setdefault("max_active_workers_per_task", DEFAULT_MAX_ACTIVE_WORKERS_PER_TASK)
    helper = dict(settings.get("helper_execution_lease", {}) or {})
    helper.setdefault("enabled", True)
    helper.setdefault("claimable_statuses", ["todo"])
    helper.setdefault("dispatch_sla_seconds", 600)
    helper.setdefault("lease_seconds", 1800)
    helper.setdefault("max_claims_per_tick", 4)
    helper.setdefault("max_claims_per_agent", 2)
    helper.setdefault("require_owner_saturated", True)
    settings["helper_execution_lease"] = helper
    return settings


def worker_logical_dispatch_agent_id(config: dict[str, Any], worker: dict[str, Any]) -> str:
    """Resolve the logical agent a worker's dispatch slot belongs to.

    Lives here rather than in `dispatch_engine` because it is the one name that
    made the module graph cyclic: `dispatch_engine` needs seven names from
    `worker_failure_policy`, and `worker_failure_policy` needed only this one
    back. It is a pure function of `config` and the worker record -- no dispatch
    state -- so a leaf is where it belongs.
    """
    explicit = normalize_agent_id(str(worker.get("logical_agent_id") or ""))
    if explicit:
        return explicit
    agent_id = normalize_agent_id(str(worker.get("agent_id") or worker.get("provider") or ""))
    agent = config.get("agents", {}).get(agent_id, {}) or {}
    return normalize_agent_id(str(agent.get("dispatch_slot_for") or agent_id))
