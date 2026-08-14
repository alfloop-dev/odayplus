#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import fcntl
import fnmatch
import hashlib
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))
SCRIPTS_DIR = THIS_DIR.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

EXPECTED_AI_STATUS_PATH = (SCRIPTS_DIR / "ai_status.py").resolve()
existing_ai_status = sys.modules.get("ai_status")
if existing_ai_status is not None and Path(
    str(getattr(existing_ai_status, "__file__", ""))
).resolve() != EXPECTED_AI_STATUS_PATH:
    sys.modules.pop("ai_status", None)
import ai_status as runtime_ai_status

if Path(str(runtime_ai_status.__file__)).resolve() != EXPECTED_AI_STATUS_PATH:
    raise RuntimeError(
        "Supervisor must load ai_status from its immutable runtime: "
        f"expected {EXPECTED_AI_STATUS_PATH}, got {runtime_ai_status.__file__}"
    )
import model_rotation
from adapters import build_adapter
from adapters.base import DeliveryRequest
from approval_queue import (
    ensure_worker_deferred_approval,
    find_worker_deferred_approval,
    prune_stale_approvals,
    resolve_approval,
)
from branch_drift_alarms import check_branch_drift
from common import (
    PROVIDER_CLI_FAMILY,
    PROVIDER_LAUNCHER_MISSING_PATTERN,
    agent_config_for,
    command_exists,
    config_path,
    display_name_for,
    execution_context_files,
    generate_task_brief_content,
    is_github_cli_auth_failure,
    is_task_brief_stale,
    load_config,
    load_json,
    load_status,
    new_runtime_id,
    normalize_agent_id,
    preserve_github_cli_auth_env,
    provider_launcher_missing_cli,
    relpath,
    selected_shared_files,
    shell_quote,
    spawn_background_process,
    summarize_failure_reason,
    utc_now,
    validate_destination_context_path,
    validate_source_doc_path,
    validate_task_archive_ambiguity,
    worker_runtime_paths,
    write_activity_log,
    write_failure_evidence,
    write_json,
)
from coordination_file_watcher import sync_coordination_files
from dispatch_policy import (
    DISPATCH_STATUS_ACTIONS,
    REASON_OWNED_FINALIZE,
    REASON_OWNED_IN_PROGRESS,
    REASON_OWNED_READY,
    REASON_REVIEW_READY,
    dispatch_reason_priority,
    is_execution_dispatch_reason,
    normalized_status_set,
    ready_dispatch_settings,
    task_priority_rank,
)
from github_bus import sync_github_bus
from provider_permissions import (
    codex_config_health,
    write_provider_capabilities,
)
from provider_permissions import (
    provider_capabilities as build_provider_capabilities,
)
from rebase_helper import continue_or_skip_empty
from runtime_state import (
    compact_worker_history,
    enqueue_event,
    load_approval_state,
    load_event_queue,
    load_runtime_state,
    queue_event_record,
    save_runtime_state,
)
from task_archive import TaskResolver
from watch_events import (
    enqueue_runtime_events_enabled,
    queue_delivery_event,
    run_scan,
    trim_seen_events,
)

SIDECAR_READY_PRIORITY_OFFSET = 10
STATUS_WRITE_REVISION_FIELD = "_status_write_revision"
# Max time the antigravity model-rotation will treat a pool as exhausted before
# re-probing it. Kept SHORT because Gemini's 5-hour limit is a rolling window
# that recovers within minutes — a longer cooldown (e.g. trusting the error's
# "Resets in Xh" hint) falsely locks an already-recovered pool for hours.
ROTATION_PROBE_COOLDOWN_SECONDS = 1800
BLOCKED_OWNER_RESCUE_KEYWORDS = (
    "auth",
    "authentication",
    "credential",
    "credentials",
    "token",
    "permission",
    "quota",
    "rate limit",
    "push",
    "pr push",
)


SESSION_ID_PATTERNS = [
    re.compile(r'"session_id"\s*:\s*"([^"]+)"'),
    re.compile(r'"sessionId"\s*:\s*"([^"]+)"'),
]
URL_PATTERN = re.compile(r"https://github\.com/[^\s)]+")
WORKER_FAILURE_PATTERNS = (
    re.compile(r"^Error when talking to gemini api\b", re.IGNORECASE),
    re.compile(r'"error"\s*:\s*"rate_limit"', re.IGNORECASE),
    re.compile(r'"type"\s*:\s*"rate_limit_event"', re.IGNORECASE),
    re.compile(r'"error"\s*:\s*"authentication_failed"', re.IGNORECASE),
    re.compile(r"quota exceeded", re.IGNORECASE),
    re.compile(r"free daily quota has been reached", re.IGNORECASE),
    re.compile(r"you have no quota", re.IGNORECASE),
    re.compile(r"^Failed to authenticate\b", re.IGNORECASE),
    re.compile(r"\bnot authenticated\b", re.IGNORECASE),
    re.compile(r"invalid authentication credentials", re.IGNORECASE),
    re.compile(
        r"^reason:\s*.*\b("
        r"terminalquotaerror|retryablequotaerror|quota_exhausted|resource_exhausted|"
        r"you have exhausted your capacity|no capacity available for model|"
        r"timed out|etimedout|econnreset|unauthorized"
        r")\b",
        re.IGNORECASE,
    ),
    re.compile(r"^status:\s*(401|429)\b", re.IGNORECASE),
    re.compile(r"^(?:you(?:'ve| have)\s+)?hit your(?:\s+\w+)?\s+limit\b", re.IGNORECASE),
    re.compile(r"^Error loading config\.toml\b", re.IGNORECASE),
    re.compile(r"^An unexpected critical error occurred", re.IGNORECASE),
    re.compile(r"^(?:Error|error|fatal):", re.IGNORECASE),
    PROVIDER_LAUNCHER_MISSING_PATTERN,
)
WORKER_FAILURE_FALSE_POSITIVE_PATTERNS = (
    re.compile(r"^(?:result|error|audit):\s+Optional\[Dict\[str,\s*Any\]\]\s*=\s*None,?$", re.IGNORECASE),
    re.compile(r"^error:\s+BFF?[A-Za-z0-9_]*Error[A-Za-z0-9_]*,?$", re.IGNORECASE),
    re.compile(r"^error:\s+[A-Za-z_][A-Za-z0-9_<>{}\[\], :|?]+?\|\s*null$", re.IGNORECASE),
    re.compile(r"^[+-]?\s*console\.error\(", re.IGNORECASE),
    re.compile(r"^[+-]\s*[A-Za-z_][A-Za-z0-9_.]*\s*=\s*", re.IGNORECASE),
    re.compile(
        r"^[A-Za-z_][A-Za-z0-9_.]*\s*=\s*(?:[rubf]{0,4})(?:'''|\"\"\"|'|\")",
        re.IGNORECASE,
    ),
    re.compile(r"^-\s+\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\s+·\s+", re.IGNORECASE),
    re.compile(r"\bauto-reassigned\b.*\bafter repeated\b", re.IGNORECASE),
)
SEARCH_RESULT_JSON_FIELD_PATTERN = re.compile(
    r"^(?:[^:\s][^:]*:)?\d+[:-]\s*\"[A-Za-z0-9_]+\"\s*:\s*",
    re.IGNORECASE,
)
JSON_FIELD_LINE_PATTERN = re.compile(
    r"^\"[A-Za-z0-9_]+\"\s*:\s*",
    re.IGNORECASE,
)
SEARCH_RESULT_LOG_JSON_PATTERN = re.compile(
    r"^[^\s:]+\.log:\d+[:-]\s*\{",
    re.IGNORECASE,
)
COMMAND_OUTPUT_EXIT_LINE_PATTERN = re.compile(r"^exited\s+\d+\s+in\s+\S+:", re.IGNORECASE)

LOCAL_TZ = ZoneInfo("Asia/Taipei")
SUPERVISOR_LOG_QUIET = False
GENERIC_WORKER_EXIT_REASON = "Worker exited before the task reached a terminal status."
NO_PROGRESS_WORKER_EXIT_REASON = (
    "Worker exited successfully without the required task lifecycle transition or meaningful progress."
)
PLANNING_STATE_FILE = THIS_DIR / "planning-state.json"
PLANNING_PHASE_DIR = THIS_DIR.parent / "docs" / "02-architecture" / "consensus" / "phase1"
_UNSET = object()


def supervisor_pid_path(config: dict[str, Any]) -> Path:
    return config_path(config, "state_file").parent / "supervisor.pid"


def supervisor_lock_path(config: dict[str, Any]) -> Path:
    return config_path(config, "state_file").parent / "supervisor.lock"


# Held open for the lifetime of the winning supervisor process. The advisory
# flock is released automatically by the kernel when the process exits (or is
# killed), so a crashed supervisor never leaves the lock stuck.
_SINGLETON_LOCK_HANDLE: Any = None


def acquire_singleton_lock(config: dict[str, Any]) -> bool:
    """Acquire the exclusive supervisor singleton lock.

    Returns True if this process is now the sole supervisor, False if another
    live supervisor already holds the lock (in which case the caller should
    exit WITHOUT touching the shared pid file or runtime state). This is the
    race-proof single-instance guard that covers every launch path
    (cron/tmux/run-supervisor.sh and the watchdog's direct spawn), replacing
    the PID-ordering heuristic that broke under PID wraparound.
    """
    global _SINGLETON_LOCK_HANDLE
    path = supervisor_lock_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _SINGLETON_LOCK_HANDLE = handle
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
    except OSError:
        pass
    return True


def write_supervisor_pid(config: dict[str, Any]) -> None:
    path = supervisor_pid_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{os.getpid()}\n", encoding="utf-8")


def clear_supervisor_pid(config: dict[str, Any]) -> None:
    path = supervisor_pid_path(config)
    if not path.exists():
        return
    try:
        current = path.read_text(encoding="utf-8").strip()
    except OSError:
        return
    if current == str(os.getpid()):
        try:
            state = load_runtime_state(config)
            supervisor_state = state.setdefault("supervisor", {})
            supervisor_state["pid"] = os.getpid()
            supervisor_state["lifecycle"] = "stopping"
            supervisor_state["last_heartbeat_at"] = utc_now()
            save_runtime_state(config, state)
        except Exception:
            pass
        path.unlink(missing_ok=True)


def cmdline_is_supervisor_process(parts: list[str]) -> bool:
    current_script = str(Path(__file__).resolve())
    current_script_name = str(Path(__file__).name)
    current_script_rel = ".orchestrator/supervisor.py"
    if not parts:
        return False
    executable = Path(parts[0]).name
    if parts[0] in {current_script, current_script_rel}:
        return True
    if not executable.startswith("python"):
        return False
    return any(
        part == current_script
        or part == current_script_rel
        or part.endswith(f"/{current_script_name}")
        for part in parts[1:]
    )


def iter_matching_supervisor_pids() -> list[int]:
    current_repo_root = str(THIS_DIR.parent.resolve())
    matches: list[int] = []
    for proc_dir in Path("/proc").iterdir():
        if not proc_dir.name.isdigit():
            continue
        pid = int(proc_dir.name)
        cmdline_path = proc_dir / "cmdline"
        try:
            raw = cmdline_path.read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        parts = [part.decode("utf-8", errors="ignore") for part in raw.split(b"\x00") if part]
        try:
            proc_cwd = str((proc_dir / "cwd").resolve())
        except OSError:
            proc_cwd = ""
        if cmdline_is_supervisor_process(parts) and proc_cwd == current_repo_root:
            matches.append(pid)
    return sorted(matches)


def terminate_other_supervisors(config: dict[str, Any]) -> None:
    """Terminate every other matching supervisor process except this one.

    Called only by the process that just won the singleton flock, so killing all
    other matches (rather than only lower-PID "older" ones) is safe and clears
    any lock-less straggler from an earlier code version. The previous
    pid < current_pid heuristic silently failed under PID wraparound, which let a
    later-started supervisor with a smaller PID coexist with an earlier one.
    """
    current_pid = os.getpid()
    terminated: list[int] = []
    for pid in iter_matching_supervisor_pids():
        if pid == current_pid:
            continue
        if not pid_is_alive(pid):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue
        deadline = time.time() + 2.0
        while time.time() < deadline and pid_is_alive(pid):
            time.sleep(0.1)
        if pid_is_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
            deadline = time.time() + 1.0
            while time.time() < deadline and pid_is_alive(pid):
                time.sleep(0.05)
        terminated.append(pid)
    for pid in terminated:
        write_activity_log(
            config,
            {
                "type": "supervisor_replaced",
                "message": f"Terminated older supervisor process {pid} while starting {current_pid}.",
                "old_pid": pid,
                "new_pid": current_pid,
            },
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local orchestrator supervisor loop.")
    parser.add_argument("--config", default=".orchestrator/config.json")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--no-watch", action="store_true", help="Process the event queue without running watch_events first.")
    parser.add_argument("--replay", action="store_true", help="Pass replay through to watch_events for the first scan.")
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=None,
        help=(
            "Override supervisor poll interval in seconds. Values below "
            "config.supervisor.poll_interval_seconds require --allow-fast-poll."
        ),
    )
    parser.add_argument(
        "--allow-fast-poll",
        action="store_true",
        help=(
            "Authorize --poll-interval below the configured value. Reserved for "
            "ad-hoc incident debugging; do not use for steady-state runs."
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress terminal heartbeat output.")
    parser.add_argument("--verbose", action="store_true", help="Print active worker and queue details each tick.")
    parser.add_argument("--clear-provider-pause", default=None, help="Manually clear one provider dispatch pause.")
    return parser.parse_args()


CONFIG_DEFAULT_POLL_INTERVAL_SECONDS = 300.0


class FastPollNotAllowedError(SystemExit):
    """Raised when --poll-interval is below config without --allow-fast-poll."""


def resolve_poll_interval(
    config: dict[str, Any],
    *,
    cli_value: float | None,
    allow_fast_poll: bool,
) -> tuple[float, str]:
    configured = float(
        config.get("supervisor", {}).get(
            "poll_interval_seconds", CONFIG_DEFAULT_POLL_INTERVAL_SECONDS
        )
    )
    if cli_value is None:
        return configured, "config"
    if cli_value <= 0:
        raise FastPollNotAllowedError(
            f"--poll-interval must be positive (got {cli_value})."
        )
    if cli_value < configured and not allow_fast_poll:
        raise FastPollNotAllowedError(
            f"--poll-interval={cli_value}s is below config.supervisor.poll_interval_seconds={configured}s. "
            "Pass --allow-fast-poll to authorize an ad-hoc fast cadence, or update config.json "
            "if this is a steady-state change."
        )
    return cli_value, "cli"


def console_log(message: str, *, quiet: bool = False) -> None:
    if quiet:
        return
    timestamp = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def parse_runtime_timestamp(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def heartbeat_lag_seconds(previous_heartbeat: str | None, current_heartbeat: str | None) -> float | None:
    previous_dt = parse_runtime_timestamp(previous_heartbeat)
    current_dt = parse_runtime_timestamp(current_heartbeat)
    if previous_dt is None or current_dt is None:
        return None
    return max(0.0, (current_dt - previous_dt).total_seconds())


def watchdog_safe_mode_active(state: dict[str, Any], now: datetime | None = None) -> bool:
    watchdog = state.get("watchdog", {}) if isinstance(state.get("watchdog"), dict) else {}
    safe_mode_until = parse_runtime_timestamp(str(watchdog.get("safe_mode_until") or ""))
    if safe_mode_until is None:
        return False
    now_dt = now or datetime.now(UTC)
    return now_dt.astimezone(UTC) < safe_mode_until.astimezone(UTC)


def record_watchdog_safe_mode_observed(config: dict[str, Any], state: dict[str, Any], now: str) -> bool:
    watchdog = state.setdefault("watchdog", {})
    safe_mode_until = str(watchdog.get("safe_mode_until") or "").strip()
    if not safe_mode_until:
        return False
    if watchdog.get("last_safe_mode_observed_until") == safe_mode_until:
        return False
    watchdog["last_safe_mode_observed_until"] = safe_mode_until
    write_activity_log(
        config,
        {
            "type": "watchdog_safe_mode_dispatch_suppressed",
            "message": f"Watchdog safe mode suppresses new supervisor dispatch until {safe_mode_until}.",
            "safe_mode_until": safe_mode_until,
            "reason": watchdog.get("safe_mode_reason"),
        },
    )
    return True


def format_runtime_timestamp_local(ts: str | None) -> str:
    dt = parse_runtime_timestamp(ts)
    if dt is None:
        return "-"
    return dt.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")


def summarize_runtime(state: dict[str, Any], approval_state: dict[str, Any]) -> dict[str, Any]:
    workers = state.get("workers", {}) or {}
    queue_events = state.get("queue", {}).get("events", {}) or {}
    pending_approvals = approval_state.get("pending", []) or []
    active_statuses = {"running", "started", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "stalled", "fallback"}
    active_workers = [
        {
            "run_id": run_id,
            "task_id": worker.get("task_id"),
            "agent_id": worker.get("agent_id"),
            "provider": worker.get("provider"),
            "status": worker.get("status"),
        }
        for run_id, worker in workers.items()
        if worker.get("status") in active_statuses
    ]
    queue_items = [
        {
            "event_id": event_id,
            "status": record.get("status"),
            "run_id": record.get("run_id"),
            "error": record.get("error"),
        }
        for event_id, record in queue_events.items()
        if str(record.get("status") or "") not in {"completed", "done"}
    ]
    return {
        "active_worker_count": len(active_workers),
        "queue_count": len(queue_items),
        "pending_approval_count": len(pending_approvals),
        "active_workers": active_workers,
        "queue_items": queue_items,
    }


def refresh_dashboard_runtime_artifacts(config: dict[str, Any]) -> None:
    try:
        status_state = runtime_ai_status.load_state()
        runtime_ai_status.write_dashboard_bundle(status_state)
        runtime_ai_status.sync_docs_site(status_state)
    except Exception as exc:
        console_log(
            f"dashboard bundle refresh failed: {type(exc).__name__}: {exc}",
            quiet=SUPERVISOR_LOG_QUIET,
        )


def safe_load_approval_state(config: dict[str, Any]) -> dict[str, Any]:
    try:
        return load_approval_state(config)
    except KeyError:
        return {"pending": [], "history": []}


def event_dispatch_mode(event: dict[str, Any]) -> str:
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    planning = metadata.get("planning")
    if isinstance(planning, dict) and planning:
        return "planning"
    coordination = metadata.get("coordination")
    if isinstance(coordination, dict) and coordination:
        return "coordination"
    reason = str(event.get("reason") or "").strip()
    if reason.startswith("discussion_planning_"):
        return "planning"
    if reason.startswith("coordination:"):
        return "coordination"
    return "execution"


def worker_dispatch_mode(worker: dict[str, Any]) -> str:
    if worker_is_discussion_planning(worker):
        return "planning"
    if worker_is_coordination_dispatch(worker):
        return "coordination"
    return "execution"


def empty_mode_occupancy() -> dict[str, dict[str, int]]:
    return {
        "planning": {"running": 0, "pending": 0, "queued": 0},
        "execution": {"running": 0, "pending": 0, "queued": 0},
        "coordination": {"running": 0, "pending": 0, "queued": 0},
    }


def mode_has_activity(bucket: dict[str, Any] | None) -> bool:
    if not isinstance(bucket, dict):
        return False
    return any(int(bucket.get(key) or 0) > 0 for key in ("running", "pending", "queued"))


def compute_mode_occupancy(config: dict[str, Any], state: dict[str, Any]) -> dict[str, dict[str, int]]:
    occupancy = empty_mode_occupancy()
    settings = ready_dispatch_settings(config)
    active_worker_statuses = {str(value) for value in settings.get("active_worker_statuses", [])}
    active_worker_statuses.update({"started", "suspended_approval", "fallback"})
    pending_worker_statuses = {"waiting_approval", "manual_pending", "suspended_approval", "retry_backoff"}
    active_event_ids: set[str] = set()

    for worker in state.get("workers", {}).values():
        status = str(worker.get("status") or "")
        if status not in active_worker_statuses:
            continue
        mode = worker_dispatch_mode(worker)
        bucket = occupancy.setdefault(mode, {"running": 0, "pending": 0, "queued": 0})
        if status in pending_worker_statuses:
            bucket["pending"] += 1
        else:
            bucket["running"] += 1
        event_id = str(worker.get("queue_event_id") or "").strip()
        if event_id:
            active_event_ids.add(event_id)

    queue_records = state.get("queue", {}).get("events", {}) or {}
    pending_queue_statuses = {"started", "manual_pending", "waiting_approval", "suspended_approval", "retry_backoff", "stalled", "fallback"}
    try:
        queued_events = load_event_queue(config)
    except KeyError:
        queued_events = []

    for event in queued_events:
        event_id = str(event.get("event_id") or "").strip()
        if not event_id:
            continue
        record = queue_records.get(event_id, {})
        record_status = str(record.get("status") or "queued")
        if record_status in {"completed", "failed", "done"}:
            continue
        if event_id in active_event_ids:
            continue
        mode = event_dispatch_mode(event)
        bucket = occupancy.setdefault(mode, {"running": 0, "pending": 0, "queued": 0})
        if record_status in pending_queue_statuses:
            bucket["pending"] += 1
        else:
            bucket["queued"] += 1

    return occupancy


def stamp_supervisor_runtime_state(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    planning_state: dict[str, Any] | None,
    heartbeat_at: str,
    lifecycle: str | None = None,
    loop_started_at: str | object = _UNSET,
    loop_finished_at: str | object = _UNSET,
    loop_error: str | None | object = _UNSET,
) -> None:
    supervisor_state = state.setdefault("supervisor", {})
    current_pid = os.getpid()
    previous_pid = supervisor_state.get("pid")
    previous_focus = str(supervisor_state.get("focus_mode") or "").strip()

    supervisor_state["pid"] = current_pid
    supervisor_state["last_heartbeat_at"] = heartbeat_at
    if not supervisor_state.get("started_at") or previous_pid != current_pid:
        supervisor_state["started_at"] = heartbeat_at
        supervisor_state["last_successful_loop_at"] = None
        supervisor_state["last_loop_started_at"] = None
        supervisor_state["last_loop_finished_at"] = None
        supervisor_state["last_loop_duration_ms"] = None
        supervisor_state["last_loop_error"] = None

    if lifecycle is not None:
        supervisor_state["lifecycle"] = lifecycle
    if loop_started_at is not _UNSET:
        supervisor_state["last_loop_started_at"] = loop_started_at
    if loop_finished_at is not _UNSET:
        supervisor_state["last_loop_finished_at"] = loop_finished_at
    if loop_error is not _UNSET:
        supervisor_state["last_loop_error"] = loop_error
    effective_loop_started_at = (
        loop_started_at
        if isinstance(loop_started_at, str)
        else supervisor_state.get("last_loop_started_at")
    )
    if (
        loop_finished_at is not _UNSET
        and isinstance(effective_loop_started_at, str)
        and isinstance(loop_finished_at, str)
    ):
        started_dt = parse_runtime_timestamp(effective_loop_started_at)
        finished_dt = parse_runtime_timestamp(loop_finished_at)
        if started_dt is not None and finished_dt is not None:
            supervisor_state["last_loop_duration_ms"] = max(0, int((finished_dt - started_dt).total_seconds() * 1000))
    if (
        loop_finished_at is not _UNSET
        and loop_finished_at is not None
        and loop_error is not _UNSET
        and loop_error is None
    ):
        supervisor_state["last_successful_loop_at"] = loop_finished_at

    occupancy = compute_mode_occupancy(config, state)
    supervisor_state["mode_occupancy"] = occupancy

    desired_focus = "planning" if discussion_planning_is_active(planning_state) else "execution"
    previous_focus_valid = previous_focus in {"planning", "execution"}
    # Discussion planning is additive: keep the visible focus on planning even if
    # execution still has inflight work that should continue to drain in parallel.
    if desired_focus == "planning":
        supervisor_state["focus_mode"] = "planning"
        supervisor_state["mode_status"] = "active" if mode_has_activity(occupancy.get("planning")) else "idle"
        supervisor_state["mode_switch_requested"] = None
        if previous_focus_valid and previous_focus != "planning":
            supervisor_state["last_mode_switch_at"] = heartbeat_at
    elif previous_focus_valid and previous_focus != desired_focus and mode_has_activity(occupancy.get(previous_focus)):
        supervisor_state["focus_mode"] = previous_focus
        supervisor_state["mode_status"] = "draining"
        supervisor_state["mode_switch_requested"] = desired_focus
    else:
        supervisor_state["focus_mode"] = desired_focus
        supervisor_state["mode_status"] = "active" if mode_has_activity(occupancy.get(desired_focus)) else "idle"
        supervisor_state["mode_switch_requested"] = None
        if previous_focus_valid and previous_focus != desired_focus:
            supervisor_state["last_mode_switch_at"] = heartbeat_at


def bootstrap_supervisor_runtime_state(config: dict[str, Any], *, lifecycle: str = "starting") -> dict[str, Any]:
    heartbeat_at = utc_now()
    state = load_runtime_state(config)
    stamp_supervisor_runtime_state(
        config,
        state,
        planning_state=load_discussion_planning_state(),
        heartbeat_at=heartbeat_at,
        lifecycle=lifecycle,
    )
    save_runtime_state(config, state)
    return state


def log_runtime_summary(
    state: dict[str, Any],
    approval_state: dict[str, Any],
    *,
    changed: bool,
    quiet: bool,
    verbose: bool,
    previous_heartbeat: str | None = None,
    warn_after_seconds: float = 10.0,
    once: bool = False,
) -> None:
    summary = summarize_runtime(state, approval_state)
    supervisor_state = state.get("supervisor", {}) or {}
    heartbeat = supervisor_state.get("last_heartbeat_at") or "-"
    heartbeat_local = format_runtime_timestamp_local(heartbeat if heartbeat != "-" else None)
    lag_seconds = heartbeat_lag_seconds(previous_heartbeat, heartbeat)
    lag_summary = f"{lag_seconds:.1f}s" if lag_seconds is not None else "-"
    lifecycle = str(supervisor_state.get("lifecycle") or "idle")
    mode_status = str(supervisor_state.get("mode_status") or "idle")
    mode = "once" if once else "tick"
    console_log(
        (
            f"supervisor {mode}: lifecycle={lifecycle} heartbeat={heartbeat_local} lag={lag_summary} changed={'yes' if changed else 'no'} "
            f"mode={mode_status} "
            f"queue={summary['queue_count']} "
            f"approvals={summary['pending_approval_count']} "
            f"active_workers={summary['active_worker_count']}"
        ),
        quiet=quiet,
    )
    if lag_seconds is not None and lag_seconds > warn_after_seconds:
        console_log(
            f"WARNING heartbeat lag exceeded threshold: {lag_seconds:.1f}s > {warn_after_seconds:.1f}s",
            quiet=quiet,
        )
    if not verbose or quiet:
        return
    console_log(f"heartbeat: {heartbeat_local} (utc={heartbeat}, lag={lag_summary})", quiet=quiet)
    if summary["active_workers"]:
        details = ", ".join(
            f"{item['agent_id'] or item['provider']}:{item['task_id']}({item['status']})"
            for item in summary["active_workers"]
        )
        console_log(f"active workers: {details}", quiet=quiet)
    else:
        console_log("active workers: none", quiet=quiet)
    if summary["queue_items"]:
        details = ", ".join(
            f"{item['event_id']}({item['status']})"
            for item in summary["queue_items"]
        )
        console_log(f"queue: {details}", quiet=quiet)
    else:
        console_log("queue: empty", quiet=quiet)


def load_provider_report(config: dict[str, Any]) -> dict[str, Any]:
    if config.get("supervisor", {}).get("auto_refresh_provider_capabilities", True):
        report = build_provider_capabilities(config)
        write_provider_capabilities(config, report=report)
        return report
    return load_json(config_path(config, "provider_capabilities"), default={}) or {}


def resolve_agent_model_preference(config: dict[str, Any], agent: dict[str, Any]) -> str | None:
    explicit = str(agent.get("model_preference") or "").strip()
    if explicit:
        return explicit

    provider_id = str(agent.get("provider") or agent.get("id") or "").strip()
    provider = config.get("providers", {}).get(provider_id, {})
    model_preference = provider.get("model_preference", {})
    if not isinstance(model_preference, dict):
        return None

    agent_id = str(agent.get("id") or "").strip()
    direct = str(model_preference.get(agent_id) or "").strip()
    if direct:
        return direct

    if agent_id == provider_id:
        default = str(model_preference.get("default") or "").strip()
        if default:
            return default
    return None


def provider_config_entry_for(config: dict[str, Any], provider: str | None) -> tuple[str, dict[str, Any]]:
    providers = config.get("providers", {}) or {}
    raw = str(provider or "").strip()
    if not raw:
        return "", {}
    normalized = normalize_agent_id(raw)
    candidates = [raw, normalized, raw.replace("_", "-"), raw.replace("-", "_")]
    for candidate in candidates:
        if candidate in providers and isinstance(providers[candidate], dict):
            return candidate, providers[candidate]
    return normalized, {}


def provider_config_for(config: dict[str, Any], provider: str | None) -> dict[str, Any]:
    return provider_config_entry_for(config, provider)[1]


def provider_runtime_config_block_reason(config: dict[str, Any], provider: str | None) -> str | None:
    provider_key, provider_cfg = provider_config_entry_for(config, provider)
    if str(provider_cfg.get("delivery_mode") or "").strip().lower() != "codex":
        return None
    health = codex_config_health(config, provider_key or str(provider or "codex"))
    if health.get("valid", True):
        return None
    return str(health.get("error") or f"{provider_key or provider} provider config is invalid.")


def provider_dispatch_group_id(config: dict[str, Any], provider: str | None) -> str:
    provider_id = normalize_agent_id(provider or "")
    if not provider_id:
        return ""
    provider_cfg = provider_config_for(config, provider)
    group = (
        provider_cfg.get("quota_group")
        or provider_cfg.get("dispatch_group")
        or provider_cfg.get("account_group")
    )
    return normalize_agent_id(str(group or provider_id))


def agent_provider_id(config: dict[str, Any], agent_id: str | None) -> str:
    normalized = normalize_agent_id(agent_id or "")
    if not normalized:
        return ""
    agent = (config.get("agents", {}) or {}).get(normalized, {}) or {}
    return normalize_agent_id(str(agent.get("provider") or normalized))


def agent_quota_group_id(config: dict[str, Any], agent_id: str | None) -> str:
    """Return the real account pool for an execution identity.

    `quota_group` was historically provider-scoped, which made aliases such as
    Antigravity2..7 look like independent accounts.  An explicit agent
    `account_pool` is authoritative and lets multiple logical roles share one
    provider account, quota budget, and worker-slot set.
    """
    normalized = normalize_agent_id(agent_id or "")
    agent = (config.get("agents", {}) or {}).get(normalized, {}) or {}
    explicit_pool = agent.get("account_pool") or agent.get("quota_group")
    if explicit_pool:
        return normalize_agent_id(str(explicit_pool))
    provider_id = agent_provider_id(config, agent_id)
    return provider_dispatch_group_id(config, provider_id or agent_id)


def account_pool_settings(config: dict[str, Any], agent_id: str | None) -> tuple[str, dict[str, Any]]:
    """Return the configured real-account pool for an execution identity.

    Logical names are deliberately not a scheduling resource.  A pool is the
    credential/account that actually consumes quota; aliases and dispatch slots
    therefore resolve to the same entry here.
    """
    pool_id = agent_quota_group_id(config, agent_id)
    pools = config.get("account_pools", {}) or {}
    raw = pools.get(pool_id, {}) if isinstance(pools, dict) else {}
    return pool_id, raw if isinstance(raw, dict) else {}


def _account_pool_runtime_bucket(state: dict[str, Any]) -> dict[str, Any]:
    """Runtime-only quota lifecycle for real credential pools.

    Configuration says how much concurrency an account may have.  Runtime
    state says whether that account is temporarily unavailable.  Keeping those
    concerns separate prevents a restart or config reload from accidentally
    resurrecting an exhausted account.
    """
    bucket = state.setdefault("account_pool_runtime", {})
    return bucket if isinstance(bucket, dict) else {}


def account_pool_runtime_state(
    config: dict[str, Any],
    state: dict[str, Any] | None,
    agent_id: str | None,
    *,
    now: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    pool_id, pool = account_pool_settings(config, agent_id)
    configured_state = str(pool.get("state") or "").strip().lower()
    configured_limit = quota_group_concurrency_limit(config, agent_id)
    if not pool_id:
        return "healthy", {}
    if pool.get("enabled") is False or configured_state == "disabled" or configured_limit == 0:
        return "disabled", {"state": "disabled", "effective_concurrency": 0}
    if state is None:
        return "healthy", {"state": "healthy", "effective_concurrency": configured_limit}

    bucket = _account_pool_runtime_bucket(state)
    entry = bucket.setdefault(
        pool_id,
        {
            "state": "healthy",
            "effective_concurrency": configured_limit,
            "generation": 0,
        },
    )
    lifecycle = str(entry.get("state") or "healthy").strip().lower()
    current_time = now or datetime.now(UTC)
    if lifecycle == "cooldown":
        next_probe = _parse_iso_utc(str(entry.get("next_probe_at") or entry.get("blocked_until") or ""))
        if next_probe is not None and next_probe <= current_time:
            # A real task executed on one slot is the authenticated canary
            # probe.  This avoids a second provider-specific probe protocol
            # while still proving the exact credential that will do the work.
            lifecycle = "recovering"
            entry["state"] = lifecycle
            entry["effective_concurrency"] = 1
            entry["last_probe_at"] = utc_now()
            entry["probe_attempts"] = int(entry.get("probe_attempts", 0)) + 1
    if lifecycle == "recovering":
        entry["effective_concurrency"] = min(1, configured_limit or 1)
    elif lifecycle == "healthy":
        entry["effective_concurrency"] = configured_limit
    return lifecycle, entry


def account_pool_effective_concurrency(
    config: dict[str, Any],
    state: dict[str, Any] | None,
    agent_id: str | None,
) -> int | None:
    configured = quota_group_concurrency_limit(config, agent_id)
    lifecycle, entry = account_pool_runtime_state(config, state, agent_id)
    if lifecycle in {"disabled", "cooldown", "paused", "exhausted"}:
        return 0
    runtime_limit = entry.get("effective_concurrency")
    try:
        runtime_limit = max(0, int(runtime_limit))
    except (TypeError, ValueError):
        runtime_limit = configured
    if configured is None:
        return runtime_limit
    if runtime_limit is None:
        return configured
    return min(configured, runtime_limit)


def account_pool_dispatch_block_reason(
    config: dict[str, Any],
    agent_id: str | None,
    runtime_state: dict[str, Any] | None = None,
) -> str | None:
    pool_id, pool = account_pool_settings(config, agent_id)
    if not pool_id:
        return None
    if pool and pool.get("enabled") is False:
        return f"account pool {pool_id} is disabled"
    configured_state = str(pool.get("state") or "").strip().lower()
    if configured_state in {"disabled", "exhausted", "cooldown", "paused"}:
        detail = str(pool.get("reason") or "").strip()
        return f"account pool {pool_id} is {configured_state}" + (f": {detail}" if detail else "")
    lifecycle, runtime = account_pool_runtime_state(config, runtime_state, agent_id)
    if lifecycle in {"disabled", "exhausted", "cooldown", "paused"}:
        detail = str(runtime.get("reason") or "").strip()
        return f"account pool {pool_id} is {lifecycle}" + (f": {detail}" if detail else "")
    return None


def agent_account_pool_id(config: dict[str, Any], agent_id: str | None) -> str:
    """Semantic alias used for independence checks and dashboard reporting."""
    return agent_quota_group_id(config, agent_id)


def review_is_independent(config: dict[str, Any], owner: str | None, reviewer: str | None) -> bool:
    owner_pool = agent_account_pool_id(config, owner)
    reviewer_pool = agent_account_pool_id(config, reviewer)
    return bool(owner_pool and reviewer_pool and owner_pool != reviewer_pool)


def active_quota_group_counts(
    config: dict[str, Any],
    state: dict[str, Any],
    active_statuses: set[str],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for worker in state.get("workers", {}).values():
        if worker.get("status") not in active_statuses:
            continue
        group_id = normalize_agent_id(str(worker.get("quota_group") or ""))
        if not group_id:
            group_id = provider_dispatch_group_id(config, str(worker.get("provider") or worker.get("agent_id") or ""))
        if not group_id:
            continue
        counts[group_id] = counts.get(group_id, 0) + 1
    return counts


def queued_quota_group_counts(config: dict[str, Any], state: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    queue_records = state.get("queue", {}).get("events", {})
    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    active_queue_event_ids = {
        str(worker.get("queue_event_id") or "")
        for worker in state.get("workers", {}).values()
        if worker.get("status") in active_statuses and worker.get("queue_event_id")
    }
    try:
        queued_events = load_event_queue(config)
    except KeyError:
        queued_events = []
    for event in queued_events:
        event_id = str(event.get("event_id") or "")
        if not event_id:
            continue
        if event_id in active_queue_event_ids:
            continue
        record = queue_records.get(event_id, {})
        if record.get("status") in {"completed", "failed"}:
            continue
        group_id = agent_quota_group_id(config, str(event.get("target_agent") or ""))
        if not group_id:
            continue
        counts[group_id] = counts.get(group_id, 0) + 1
    return counts


def quota_group_concurrency_limit(
    config: dict[str, Any],
    agent_id: str | None,
) -> int | None:
    _pool_id, pool = account_pool_settings(config, agent_id)
    if pool.get("max_concurrent") not in (None, ""):
        try:
            return max(0, int(pool["max_concurrent"]))
        except (TypeError, ValueError):
            return None
    return None


def agent_is_dispatch_slot(agent: dict[str, Any] | None) -> bool:
    return bool(
        isinstance(agent, dict)
        and (
            str(agent.get("dispatch_slot_for") or "").strip()
            or str(agent.get("dispatch_slot_for_pool") or "").strip()
        )
    )


def logical_worker_slot_ids(config: dict[str, Any], agent_id: str | None) -> list[str]:
    normalized = normalize_agent_id(agent_id or "")
    if not normalized:
        return []
    agents = config.get("agents", {}) or {}
    logical_agent = agents.get(normalized) or {}
    slot_ids: list[str] = []
    seen: set[str] = set()
    for raw_slot in logical_agent.get("worker_slots", []) or []:
        slot_id = normalize_agent_id(str(raw_slot or ""))
        if slot_id and slot_id in agents and slot_id not in seen:
            seen.add(slot_id)
            slot_ids.append(slot_id)
    for slot_id, slot_agent in agents.items():
        if normalize_agent_id(str((slot_agent or {}).get("dispatch_slot_for") or "")) != normalized:
            continue
        normalized_slot = normalize_agent_id(slot_id)
        if normalized_slot and normalized_slot not in seen:
            seen.add(normalized_slot)
            slot_ids.append(normalized_slot)
    account_pool = agent_account_pool_id(config, normalized)
    if account_pool:
        for slot_id, slot_agent in agents.items():
            if normalize_agent_id(str((slot_agent or {}).get("dispatch_slot_for_pool") or "")) != account_pool:
                continue
            normalized_slot = normalize_agent_id(slot_id)
            if normalized_slot and normalized_slot not in seen:
                seen.add(normalized_slot)
                slot_ids.append(normalized_slot)
    return slot_ids


def dispatch_loop_agent_ids(config: dict[str, Any]) -> list[str]:
    return [
        normalize_agent_id(agent_id)
        for agent_id, agent in (config.get("agents", {}) or {}).items()
        if normalize_agent_id(agent_id) and not agent_is_dispatch_slot(agent)
    ]


def agent_dispatch_capacity(config: dict[str, Any], agent_id: str | None) -> int:
    normalized = normalize_agent_id(agent_id or "")
    # Slots model actual processes. Aliases never create capacity.
    slot_count = len(logical_worker_slot_ids(config, normalized))
    if slot_count:
        return slot_count
    # Unpooled configurations remain safe during migration: one process per
    # logical identity. Production account pools always declare slots.
    return 1


def select_dispatch_agent_id(
    config: dict[str, Any],
    state: dict[str, Any],
    agent_id: str | None,
    active_statuses: set[str],
    provider_report: dict[str, Any] | None = None,
) -> str | None:
    normalized = normalize_agent_id(agent_id or "")
    slot_ids = logical_worker_slot_ids(config, normalized)
    if not slot_ids:
        return normalized
    active_slots = {
        normalize_agent_id(str(worker.get("agent_id") or ""))
        for worker in state.get("workers", {}).values()
        if worker.get("status") in active_statuses
    }
    for slot_id in slot_ids:
        if slot_id in active_slots:
            continue
        quota_limit = account_pool_effective_concurrency(config, state, slot_id)
        quota_group = agent_quota_group_id(config, slot_id)
        if quota_limit and quota_group:
            quota_counts = active_quota_group_counts(config, state, active_statuses)
            if quota_counts.get(quota_group, 0) >= quota_limit:
                continue
        if agent_auto_dispatch_block_reason(config, state, slot_id, provider_report):
            continue
        return slot_id
    return None


def build_request(
    config: dict[str, Any],
    event: dict[str, Any],
    *,
    agent_id_override: str | None = None,
) -> DeliveryRequest:
    logical_agent = agent_config_for(config, event["target_agent"])
    agent = agent_config_for(config, agent_id_override or event["target_agent"])
    metadata = dict(event.get("metadata", {}) or {})
    model_preference = resolve_agent_model_preference(config, agent)
    if model_preference and "model_preference" not in metadata:
        metadata["model_preference"] = model_preference
    logical_agent_id = normalize_agent_id(str(logical_agent.get("id") or event.get("target_agent") or ""))
    if logical_agent_id and "logical_agent_id" not in metadata:
        metadata["logical_agent_id"] = logical_agent_id
    if "target_display_name" not in metadata:
        metadata["target_display_name"] = event.get("target_display_name") or display_name_for(config, logical_agent_id)
    if agent_id_override:
        metadata["dispatch_slot_id"] = agent["id"]
        metadata["dispatch_slot"] = agent.get("slot_id") or agent["id"]
    context_files = event.get("context_files")
    if context_files is None:
        context_files = execution_context_files(config, event.get("task_id"))
    return DeliveryRequest(
        agent_id=agent["id"],
        provider=agent.get("provider", agent["id"]),
        delivery_mode=config.get("providers", {}).get(agent.get("provider", agent["id"]), {}).get(
            "delivery_mode", agent.get("adapter", "file_inbox")
        ),
        message=event["message"],
        task_id=event.get("task_id"),
        reason=event.get("reason"),
        context_files=context_files,
        target_files=event.get("target_files", []),
        metadata=metadata,
    )


def queue_status(state: dict[str, Any], event_id: str) -> dict[str, Any]:
    return queue_event_record(state, event_id)


def request_snapshot(request: DeliveryRequest) -> dict[str, Any]:
    return {
        "agent_id": request.agent_id,
        "provider": request.provider,
        "delivery_mode": request.delivery_mode,
        "message": request.message,
        "task_id": request.task_id,
        "reason": request.reason,
        "context_files": list(request.context_files),
        "target_files": list(request.target_files),
        "metadata": dict(request.metadata),
    }


def request_from_snapshot(snapshot: dict[str, Any]) -> DeliveryRequest:
    return DeliveryRequest(
        agent_id=snapshot["agent_id"],
        provider=snapshot["provider"],
        delivery_mode=snapshot["delivery_mode"],
        message=snapshot["message"],
        task_id=snapshot.get("task_id"),
        reason=snapshot.get("reason"),
        context_files=list(snapshot.get("context_files", []) or []),
        target_files=list(snapshot.get("target_files", []) or []),
        metadata=dict(snapshot.get("metadata", {}) or {}),
    )


WORKER_WORKTREE_EXECUTION_REASONS = [
    REASON_OWNED_READY,
    REASON_OWNED_IN_PROGRESS,
    REASON_OWNED_FINALIZE,
    REASON_REVIEW_READY,
]


def worker_worktree_settings(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("worker_worktrees")
    settings = raw if isinstance(raw, dict) else {}
    branch_workflow = config.get("branch_workflow") if isinstance(config.get("branch_workflow"), dict) else {}
    try:
        git_network_timeout_seconds = max(
            1.0,
            float(
                settings.get(
                    "git_network_timeout_seconds",
                    config.get("supervisor", {}).get("external_command_timeout_seconds", 30),
                )
            ),
        )
    except (TypeError, ValueError):
        git_network_timeout_seconds = 30.0
    return {
        "enabled": bool(settings.get("enabled", False)),
        "root": str(settings.get("root") or "/tmp/pantheon-worker-worktrees"),
        "base_ref": str(settings.get("base_ref") or f"origin/{branch_workflow.get('dev_branch') or 'dev'}"),
        "reuse_existing": bool(settings.get("reuse_existing", True)),
        "execution_reasons": list(settings.get("execution_reasons") or WORKER_WORKTREE_EXECUTION_REASONS),
        "git_network_timeout_seconds": git_network_timeout_seconds,
    }


def _task_id_slug(task_id: str | None) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(task_id or "").lower()).strip("-")
    return slug or "unknown-task"


def worker_task_branch(config: dict[str, Any], task_id: str | None) -> str:
    branch_workflow = config.get("branch_workflow") if isinstance(config.get("branch_workflow"), dict) else {}
    prefix = str(branch_workflow.get("task_branch_prefix") or "task/")
    normalized_task_id = str(task_id or "").strip()
    return f"{prefix}{normalized_task_id}" if normalized_task_id else f"{prefix}unknown-task"


def _worker_worktree_base_root(config: dict[str, Any], settings: dict[str, Any]) -> Path:
    repo_root = config_path(config, "status_file").parents[0]
    configured = Path(os.path.expanduser(str(settings.get("root") or "")))
    if not configured.is_absolute():
        configured = repo_root / configured
    return configured.resolve()


def worker_task_worktree_path(config: dict[str, Any], task_id: str | None, settings: dict[str, Any] | None = None) -> Path:
    active_settings = settings or worker_worktree_settings(config)
    repo_root = config_path(config, "status_file").parents[0]
    repo_slug = re.sub(r"[^a-z0-9]+", "-", repo_root.name.lower()).strip("-") or "repo"
    return _worker_worktree_base_root(config, active_settings) / repo_slug / _task_id_slug(task_id)


def worker_worktree_reason_enabled(reason: str | None, settings: dict[str, Any]) -> bool:
    normalized_reason = str(reason or "")
    for pattern in settings.get("execution_reasons", []):
        if fnmatch.fnmatchcase(normalized_reason, str(pattern)):
            return True
    return False


def worker_workspace_task_id(request: DeliveryRequest) -> str | None:
    metadata_task_id = str(request.metadata.get("workspace_task_id") or "").strip()
    task_id = metadata_task_id or str(request.task_id or "").strip()
    return task_id or None


def _git_worktree_records(repo_root: Path) -> list[dict[str, str]]:
    proc = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if not line.strip():
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value.strip()
    if current:
        records.append(current)
    return records


def _worktree_record_branch(record: dict[str, str]) -> str:
    branch = str(record.get("branch") or "").strip()
    if branch.startswith("refs/heads/"):
        return branch[len("refs/heads/") :]
    return branch


def _existing_worktree_for_branch(repo_root: Path, branch: str, *, exclude_root: bool) -> Path | None:
    resolved_repo_root = repo_root.resolve()
    for record in _git_worktree_records(repo_root):
        if _worktree_record_branch(record) != branch:
            continue
        path_value = record.get("worktree")
        if not path_value:
            continue
        path = Path(path_value).resolve()
        if exclude_root and path == resolved_repo_root:
            continue
        return path
    return None


def _branch_checked_out_in_root(repo_root: Path, branch: str) -> bool:
    for record in _git_worktree_records(repo_root):
        path_value = record.get("worktree")
        if not path_value:
            continue
        if Path(path_value).resolve() == repo_root.resolve():
            return _worktree_record_branch(record) == branch
    return False


def _git_ref_exists(repo_root: Path, ref: str) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        cwd=repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return proc.returncode == 0


def _create_worker_worktree(repo_root: Path, path: Path, branch: str, base_ref: str) -> tuple[bool, str | None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        return False, f"Worker worktree path already exists and is not empty: {path}"

    remote_ref = f"refs/remotes/origin/{branch}"
    if _git_ref_exists(repo_root, f"refs/heads/{branch}"):
        command = ["git", "worktree", "add", str(path), branch]
    elif _git_ref_exists(repo_root, remote_ref):
        command = ["git", "worktree", "add", "-b", branch, str(path), f"origin/{branch}"]
    else:
        command = ["git", "worktree", "add", "-b", branch, str(path), base_ref]

    proc = subprocess.run(
        command,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        details = (proc.stderr or proc.stdout or "").strip()
        return False, f"Failed to create worker worktree {path} for {branch}: {details}"
    return True, None


# Orchestrator-managed per-task scratch and context files that a worker routinely
# dirties or seeds inside its worktree. The supervisor regenerates or seeds these on
# dispatch, so a reused worktree whose ONLY dirt is here is safe to restore-and-reuse.
# Ephemeral context must not block dispatch or cause permanent lease failure.
_REUSABLE_DIRTY_PREFIXES = (
    ".orchestrator/task-briefs/",
    ".orchestrator/reviews/",
)
_REUSABLE_CONTEXT_FILES = (
    "AI_COLLABORATION_GUIDE.md",
    "ai-status.json",
    "current-work.md",
    "ai-activity-log.jsonl",
)


def _is_safe_context_destination(workspace_path: Path, rel_value: str) -> bool:
    """Validate that rel_value destination inside workspace_path is safe to write or read context.

    Returns False if:
    - rel_value is empty, absolute, or contains path traversal ('..')
    - destination or any parent directory within workspace_path is a symlink (os.path.islink)
    - destination or any parent within workspace_path exists and is a non-regular file/dir
    - destination or any parent resolves outside workspace_path
    """
    rel_clean = str(rel_value or "").replace("\\", "/").strip()
    if not rel_clean or Path(rel_clean).is_absolute() or ".." in rel_clean.split("/"):
        return False

    try:
        workspace_resolved = workspace_path.resolve()
        curr = workspace_path
        parts = Path(rel_clean).parts

        for part in parts[:-1]:
            curr = curr / part
            if os.path.islink(curr):
                return False
            if curr.exists():
                if not curr.is_dir():
                    return False
                try:
                    curr_resolved = curr.resolve()
                    if curr_resolved != workspace_resolved and workspace_resolved not in curr_resolved.parents:
                        return False
                except (OSError, RuntimeError, ValueError):
                    return False

        destination = workspace_path / rel_clean
        if os.path.islink(destination):
            return False
        if destination.exists():
            if not destination.is_file():
                return False
            try:
                dest_resolved = destination.resolve()
                if dest_resolved != workspace_resolved and workspace_resolved not in dest_resolved.parents:
                    return False
            except (OSError, RuntimeError, ValueError):
                return False

        return True
    except Exception:
        return False


def _classify_worktree_dirt(
    porcelain_status: str | bytes,
    worktree_path: Path | None = None,
) -> tuple[str, list[str]]:
    """Classify reused-worktree dirtiness from `git status --porcelain` output.

    Returns (classification, paths):
      'clean'        - no changes; paths is []
      'scratch_only' - every change is an untracked or ignored ephemeral seed
                       (see _REUSABLE_DIRTY_PREFIXES / _REUSABLE_CONTEXT_FILES); paths lists them
      'real'         - at least one change is tracked/staged or outside scratch -> must block reuse
    """
    entries: list[tuple[str, str]] = []
    if isinstance(porcelain_status, bytes):
        raw_entries = [e for e in porcelain_status.split(b"\0") if e]
        if not raw_entries:
            return "clean", []
        i = 0
        while i < len(raw_entries):
            item = raw_entries[i]
            code = item[:2].decode("utf-8", errors="replace")
            path_bytes = item[3:] if len(item) > 3 else b""
            i += 1
            if len(code) >= 2 and (code[0] in ("R", "C") or code[1] in ("R", "C")):
                if i < len(raw_entries):
                    i += 1
            rel_p = os.fsdecode(path_bytes).strip()
            if rel_p:
                entries.append((code, rel_p))
    else:
        lines = [ln for ln in porcelain_status.splitlines() if ln.strip()]
        if not lines:
            return "clean", []
        for ln in lines:
            code = ln[:2]
            body = ln[3:] if len(ln) > 3 else ln.strip()
            path = body.split(" -> ")[-1].strip().strip('"')
            if path:
                entries.append((code, path))

    if not entries:
        return "clean", []

    def _is_reusable(p: str) -> bool:
        norm = p.replace("\\", "/").strip()
        return norm.startswith(_REUSABLE_DIRTY_PREFIXES) or norm in _REUSABLE_CONTEXT_FILES

    scratch_paths: list[str] = []
    for code, path in entries:
        if code.strip() not in ("??", "!!"):
            return "real", []
        if not _is_reusable(path):
            return "real", []
        if worktree_path and not _is_safe_context_destination(worktree_path, path):
            return "real", []
        scratch_paths.append(path)

    return "scratch_only", scratch_paths


def _restore_reusable_scratch(worktree_path: Path, paths: list[str]) -> None:
    """Never checkout or destroy owner-modified content. Untracked scratch is kept untouched."""
    pass





def _git_output(cwd: Path, *args: str) -> tuple[int, str]:
    if not cwd or not Path(cwd).exists():
        return 1, ""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode, (proc.stdout or "").strip()
    except (OSError, ValueError):
        return 1, ""


def _run_git_network_command(
    cwd: Path,
    args: list[str],
    *,
    timeout_seconds: float | None,
) -> tuple[subprocess.CompletedProcess[str] | None, str | None]:
    """Run a remote git operation with a bounded wait.

    Worktree preflight runs in the supervisor's critical path.  A wedged
    HTTPS remote must fail this one lease closed, rather than consuming every
    scheduler tick and hiding useful capacity behind a stuck subprocess.
    ``None`` keeps direct unit-test callers backward compatible.
    """
    kwargs: dict[str, Any] = {
        "cwd": cwd,
        "capture_output": True,
        "text": True,
        "check": False,
    }
    if timeout_seconds is not None:
        kwargs["timeout"] = timeout_seconds
    try:
        return subprocess.run(["git", *args], **kwargs), None
    except subprocess.TimeoutExpired:
        limit = float(timeout_seconds or 0)
        return None, f"git network command timed out after {limit:g}s"
    except (OSError, ValueError) as exc:
        return None, f"git network command could not start: {type(exc).__name__}: {exc}"


def _git_commit_oid(cwd: Path, ref: str) -> str | None:
    returncode, output = _git_output(cwd, "rev-parse", "--verify", f"{ref}^{{commit}}")
    oid = output.splitlines()[0].strip() if output else ""
    return oid if returncode == 0 and re.fullmatch(r"[0-9a-fA-F]{40,64}", oid) else None


_REMOTE_HEAD_SNAPSHOTS: dict[tuple[str, str], tuple[float, dict[str, str], float]] = {}


def _clear_remote_head_snapshot_cache() -> None:
    """Clear cached remote ref heads for supervisor operations."""
    _REMOTE_HEAD_SNAPSHOTS.clear()


def _get_remote_heads_snapshot(
    cwd: Path,
    remote: str = "origin",
    *,
    network_timeout_seconds: float | None = None,
    force_refresh: bool = False,
) -> tuple[dict[str, str] | None, str]:
    """Fetch remote branch heads snapshot (mapping branch_name -> commit_sha).

    Returns (heads_dict, status_prefix).
    On success: (heads_dict, "ok").
    On failure: (None, "fetch_timed_out: ..." or "fetch_failed: ...").
    """
    now = time.monotonic()
    remotes_rc, remote_url = _git_output(cwd, "remote", "get-url", remote)
    cache_key = (remote_url.strip() if remotes_rc == 0 else str(cwd.resolve()), remote)

    cached = _REMOTE_HEAD_SNAPSHOTS.get(cache_key)
    ttl = 30.0
    max_stale = 300.0

    if not force_refresh and cached and now < cached[0]:
        return cached[1], "ok"

    remote_query, network_error = _run_git_network_command(
        cwd,
        ["ls-remote", "--heads", remote],
        timeout_seconds=network_timeout_seconds,
    )
    if network_error:
        last_success = cached[2] if cached else float("-inf")
        if cached and now - last_success < max_stale:
            return cached[1], "ok"
        return None, f"fetch_timed_out: {network_error}"

    if remote_query is None or remote_query.returncode != 0:
        last_success = cached[2] if cached else float("-inf")
        if cached and now - last_success < max_stale:
            return cached[1], "ok"
        details = (
            (remote_query.stderr or remote_query.stdout or "").strip()
            if remote_query
            else "unknown network failure"
        )
        return None, f"fetch_failed: {details}"

    heads: dict[str, str] = {}
    for line in (remote_query.stdout or "").splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        sha, ref = parts[0].strip(), parts[1].strip()
        if ref.startswith("refs/heads/") and sha:
            heads[ref.removeprefix("refs/heads/")] = sha

    _REMOTE_HEAD_SNAPSHOTS[cache_key] = (now + ttl, heads, now)
    return heads, "ok"


def _fetch_authoritative_task_head(
    repo_root: Path,
    worktree_path: Path,
    branch: str,
    *,
    network_timeout_seconds: float | None = None,
) -> tuple[str | None, str]:
    """Resolve the immutable commit used for dirty-worktree lease recovery.

    Repositories with an ``origin`` must resolve the exact remote task ref after
    fetching it.  A mutable local branch (or the dirty worktree's HEAD) is never
    allowed to win over the published task ref.  Local-only repositories retain
    the existing branch-ref behavior for tests and offline development.
    """
    remotes_rc, remotes = _git_output(worktree_path, "remote")
    has_origin = remotes_rc == 0 and "origin" in remotes.splitlines()
    if not has_origin:
        local_head = _git_commit_oid(repo_root, f"refs/heads/{branch}")
        if local_head:
            return local_head, "local_only_task_ref"
        return None, "unverifiable_refs: missing local task branch"

    heads_snapshot, snapshot_status = _get_remote_heads_snapshot(
        worktree_path,
        "origin",
        network_timeout_seconds=network_timeout_seconds,
    )
    if heads_snapshot is None:
        return None, snapshot_status

    if branch not in heads_snapshot:
        return None, "unverifiable_refs: remote task branch is missing"

    advertised_head = heads_snapshot[branch]
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", advertised_head):
        return None, "unverifiable_refs: invalid advertised remote task HEAD"

    fetch_proc, network_error = _run_git_network_command(
        worktree_path,
        [
            "fetch",
            "origin",
            "--quiet",
            f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
        ],
        timeout_seconds=network_timeout_seconds,
    )
    if network_error or fetch_proc is None:
        return None, f"fetch_timed_out: {network_error or 'unknown network failure'}"
    if fetch_proc.returncode != 0:
        details = (fetch_proc.stderr or fetch_proc.stdout or "").strip()
        return None, f"fetch_failed: {details}"
    fetched_head = _git_commit_oid(repo_root, f"refs/remotes/origin/{branch}")
    if not fetched_head or fetched_head.lower() != advertised_head.lower():
        return None, (
            "unverifiable_refs: fetched remote task HEAD does not match "
            f"advertised HEAD ({fetched_head or 'none'} != {advertised_head})"
        )
    return fetched_head, "remote_exact_task_ref"


def _git_operation_in_progress(worktree_path: Path) -> bool:
    # REBASE_HEAD records the commit currently being replayed, but Git may
    # retain it after a successful rebase has finished.  The authoritative
    # in-progress signals are rebase-merge/rebase-apply below; treating a stale
    # REBASE_HEAD as active permanently jams an otherwise reusable worktree.
    for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD"):
        returncode, _ = _git_output(worktree_path, "rev-parse", "--verify", "-q", marker)
        if returncode == 0:
            return True
    for marker in ("rebase-merge", "rebase-apply"):
        returncode, raw_path = _git_output(worktree_path, "rev-parse", "--git-path", marker)
        if returncode != 0 or not raw_path:
            return True
        marker_path = Path(raw_path)
        if not marker_path.is_absolute():
            marker_path = worktree_path / marker_path
        if marker_path.exists():
            return True
    return False


WORKTREE_LEASE_BLOCK_RETENTION_HOURS = 72


def _prune_worktree_lease_blocks(bucket: dict[str, Any]) -> None:
    """Forget streaks that nothing has touched for days.

    The counter is durable state, and `_clear_worktree_lease_block` only runs
    when a task actually leases a worktree. A task that is blocked and then
    abandoned -- finished, cancelled, renamed -- never reaches that path, so
    without an expiry its entry would sit in `state.json` forever. Supervisor
    ticks are minutes apart, so anything untouched for days is also no longer a
    *consecutive* streak; dropping it restarts the count, which is the honest
    reading.
    """

    cutoff = datetime.now(UTC) - timedelta(hours=WORKTREE_LEASE_BLOCK_RETENTION_HOURS)
    for key, entry in list(bucket.items()):
        if not isinstance(entry, dict):
            bucket.pop(key, None)
            continue
        # An unparseable timestamp is kept: expiring a streak we cannot date is
        # the failure mode this whole task exists to remove. A hand-edited entry
        # can also be naive; read it as UTC rather than raising inside dispatch.
        last_at = _parse_iso_utc(entry.get("last_at") or entry.get("first_at"))
        if last_at is None:
            continue
        if last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=UTC)
        if last_at < cutoff:
            bucket.pop(key, None)


def _record_worktree_lease_block(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    task_id: str,
    refresh_status: str,
    message: str,
) -> int:
    """Count consecutive lease blocks and escalate once they stop being noise.

    A single blocked lease is ordinary: the next tick usually clears it. What is
    not ordinary is the same block repeating unchanged forever. On 2026-08-05 ten
    tasks were blocked 1713 times over ~8h without one escalation, because each
    attempt only appended an activity record and returned. `active_workers=0`
    alongside a non-empty queue is not itself an alarm condition, so the fleet
    read as healthy the entire time.

    Returns the current consecutive count.
    """

    bucket = state.setdefault("worker_worktree_lease_blocks", {})
    _prune_worktree_lease_blocks(bucket)
    key = normalize_agent_id(task_id) or task_id
    entry = bucket.get(key)
    if not isinstance(entry, dict) or entry.get("refresh_status") != refresh_status:
        entry = {"count": 0, "first_at": utc_now(), "refresh_status": refresh_status, "escalated": False}
    entry["count"] = int(entry.get("count") or 0) + 1
    entry["last_at"] = utc_now()
    entry["message"] = message
    bucket[key] = entry

    threshold = max(2, int(worker_runtime_settings(config).get("lease_block_escalate_after", 5)))
    if entry["count"] >= threshold and not entry.get("escalated"):
        entry["escalated"] = True
        console_log(
            f"worktree lease blocked repeatedly: task={task_id} count={entry['count']} "
            f"status={refresh_status} -- dispatch for this task is stuck and needs an owner decision",
            quiet=SUPERVISOR_LOG_QUIET,
        )
        write_activity_log(
            config,
            {
                "type": "dispatch_blocked_worktree_lease_escalated",
                "task_id": task_id,
                "message": (
                    f"Worktree lease has been blocked {entry['count']} consecutive times with "
                    f"`{refresh_status}`. This will not clear on its own: {message}"
                ),
                "refresh_status": refresh_status,
                "consecutive_blocks": entry["count"],
                "first_blocked_at": entry.get("first_at"),
            },
        )
    return int(entry["count"])


def _clear_worktree_lease_block(state: dict[str, Any], task_id: str) -> None:
    bucket = state.get("worker_worktree_lease_blocks")
    if isinstance(bucket, dict):
        bucket.pop(normalize_agent_id(task_id) or task_id, None)


def _publish_unpublished_task_branch(
    worktree_path: Path,
    expected_branch: str,
) -> tuple[bool, str]:
    """Fast-forward-publish a clean task branch whose commits were never pushed.

    The fail-closed refresh policy calls a branch dispatchable only when its
    local HEAD exactly matches the remote task HEAD. A worker that commits but
    exits before pushing leaves a state that can never reach that condition on
    its own: leasing is what would run the worker that would push, and leasing
    is exactly what the policy refuses. On 2026-08-05 eight tasks sat in that
    deadlock for ~8h, each re-reported ~300 times, while the fleet ran no work
    at all.

    Publishing does not weaken the policy -- it satisfies it, by turning an
    unverifiable local state into the exact local==remote state the policy
    already accepts. It is therefore allowed only where that equivalence holds
    and nothing can be lost:

    * the worktree must be clean, so no unreviewed working-tree state is
      published as a side effect of dispatch;
    * the push must be a genuine fast-forward -- either the remote branch does
      not exist yet, or its HEAD is an ancestor of the local HEAD.

    A genuinely diverged branch (ahead *and* behind) is never published here.
    That needs a rebase decision only the task owner can make, and the caller
    escalates it instead.

    Returns (published, detail).
    """

    dirty_rc, dirty_out = _git_output(worktree_path, "status", "--porcelain")
    if dirty_rc != 0:
        return False, "cannot read worktree status"
    if dirty_out.strip():
        return False, "worktree is not clean"

    local_head = _git_commit_oid(worktree_path, "HEAD")
    if not local_head:
        return False, "missing local HEAD"

    remote_head = _git_commit_oid(worktree_path, f"origin/{expected_branch}")
    if remote_head:
        if remote_head == local_head:
            return False, "already published"
        ancestor_rc, _ = _git_output(
            worktree_path, "merge-base", "--is-ancestor", remote_head, local_head
        )
        if ancestor_rc != 0:
            # Remote holds commits the local branch does not: a real divergence.
            return False, f"diverged from remote ({remote_head[:12]})"

    push_proc = subprocess.run(
        ["git", "push", "origin", f"refs/heads/{expected_branch}:refs/heads/{expected_branch}"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if push_proc.returncode != 0:
        details = (push_proc.stderr or push_proc.stdout or "").strip().splitlines()
        return False, f"push failed: {details[0] if details else 'unknown'}"
    _clear_remote_head_snapshot_cache()
    return True, f"published {local_head[:12]}"


def _preserve_and_reset_clean_diverged_worktree(
    config: dict[str, Any],
    state: dict[str, Any],
    worktree_path: Path,
    task_id: str | None,
    expected_branch: str,
) -> tuple[bool, str]:
    """Recover a clean diverged task branch without losing its local history.

    A clean branch that is both ahead of and behind its remote cannot be
    fast-forwarded or safely pushed.  Keeping it in place permanently blocks
    the task.  When explicitly enabled, retain the complete local tip under an
    immutable timestamped preservation ref, then reset the leased branch to
    the remotely published task head.  The operator can recover the preserved
    commits later, while dispatch proceeds only from the reviewed remote head.
    """
    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    for worker in (state.get("workers", {}) or {}).values():
        if str(worker.get("status") or "") not in active_statuses:
            continue
        if str(worker.get("task_id") or "") == str(task_id or ""):
            return False, "active worker still owns this task"

    dirty_rc, dirty_out = _git_output(worktree_path, "status", "--porcelain")
    if dirty_rc != 0 or dirty_out.strip():
        return False, "worktree is no longer clean"
    local_head = _git_commit_oid(worktree_path, "HEAD")
    remote_head = _git_commit_oid(worktree_path, f"origin/{expected_branch}")
    if not local_head or not remote_head or local_head == remote_head:
        return False, "missing or unchanged task heads"
    remote_contains_local_rc, _ = _git_output(
        worktree_path, "merge-base", "--is-ancestor", local_head, remote_head
    )
    local_contains_remote_rc, _ = _git_output(
        worktree_path, "merge-base", "--is-ancestor", remote_head, local_head
    )
    # Never reset an ahead-only branch: its unpublished commits can be
    # fast-forward published safely by the caller.  Recovery is only safe for
    # a real fork, where neither tip is an ancestor of the other.
    if remote_contains_local_rc != 1 or local_contains_remote_rc != 1:
        return False, "branch is not a genuine local/remote divergence"

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    preserved_ref = f"supervisor-preserved/{_task_id_slug(task_id)}-{stamp}-{uuid.uuid4().hex[:8]}"
    preserve_proc = subprocess.run(
        ["git", "branch", preserved_ref, local_head],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if preserve_proc.returncode != 0:
        details = (preserve_proc.stderr or preserve_proc.stdout or "").strip()
        return False, f"failed to create preservation ref: {details}"
    reset_proc = subprocess.run(
        ["git", "reset", "--hard", remote_head],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if reset_proc.returncode != 0 or _git_commit_oid(worktree_path, "HEAD") != remote_head:
        details = (reset_proc.stderr or reset_proc.stdout or "").strip()
        return False, f"preserved {preserved_ref}, but reset verification failed: {details}"
    write_activity_log(
        config,
        {
            "type": "worker_worktree_clean_divergence_recovered",
            "task_id": task_id,
            "workspace_path": str(worktree_path),
            "workspace_branch": expected_branch,
            "preserved_ref": preserved_ref,
            "previous_head": local_head,
            "remote_head": remote_head,
            "message": "Clean diverged worktree reset to remote task head after preserving local history.",
        },
    )
    return True, f"clean_divergence_recovered:{preserved_ref}"


def _refresh_reused_worker_worktree(
    repo_root: Path,
    worktree_path: Path,
    base_ref: str,
    expected_branch: str,
    *,
    network_timeout_seconds: float | None = None,
) -> tuple[bool, str]:
    """Lease a clean reused worktree using a fail-closed three-way policy.

    A branch behind the current base may be fast-forwarded. A clean local task
    branch behind its freshly fetched remote task branch may also be
    fast-forwarded to that authoritative published HEAD. A branch already
    containing the base is left untouched. A genuinely diverged branch is
    dispatchable only when its local HEAD exactly matches the remote task HEAD;
    the owner then receives an explicit rebase-required prompt. Every
    unverifiable or mutable condition blocks without resetting, cleaning,
    rebasing, or otherwise discarding worker state.
    """
    base = base_ref.split("/", 1)[1] if base_ref.startswith("origin/") else base_ref
    worktree_path = worktree_path.resolve()
    repo_root = repo_root.resolve()

    top_rc, top_level = _git_output(worktree_path, "rev-parse", "--show-toplevel")
    worktree_common_rc, worktree_common = _git_output(worktree_path, "rev-parse", "--git-common-dir")
    repo_common_rc, repo_common = _git_output(repo_root, "rev-parse", "--git-common-dir")
    try:
        resolved_top = Path(top_level).resolve()
        resolved_worktree_common = (worktree_path / worktree_common).resolve()
        resolved_repo_common = (repo_root / repo_common).resolve()
    except (OSError, RuntimeError, ValueError):
        return False, "wrong_worktree: unable to resolve repository identity"
    if (
        top_rc != 0
        or worktree_common_rc != 0
        or repo_common_rc != 0
        or resolved_top != worktree_path
        or resolved_worktree_common != resolved_repo_common
    ):
        return False, "wrong_worktree: path is not the expected repository worktree"

    branch_rc, branch = _git_output(worktree_path, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch_rc == 0:
        if branch != expected_branch:
            return False, f"wrong_branch: expected {expected_branch}, found {branch}"
    else:
        current_head = _git_commit_oid(worktree_path, "HEAD")
        expected_head = (
            _git_commit_oid(repo_root, f"refs/heads/{expected_branch}")
            or _git_commit_oid(repo_root, f"origin/{expected_branch}")
            or _git_commit_oid(repo_root, expected_branch)
        )
        if not current_head or not expected_head or current_head != expected_head:
            return False, f"wrong_branch: expected {expected_branch} ({expected_head or 'none'}), found detached HEAD at {current_head or 'none'}"
    if _git_operation_in_progress(worktree_path):
        return False, "unresolved_git_operation"

    has_remote_origin = "origin" in _git_output(worktree_path, "remote")[1].splitlines()
    if has_remote_origin:
        fetch_proc, network_error = _run_git_network_command(
            worktree_path,
            ["fetch", "origin", base, "--quiet"],
            timeout_seconds=network_timeout_seconds,
        )
        if network_error or fetch_proc is None:
            return False, f"fetch_timed_out: {network_error or 'unknown network failure'}"
        if fetch_proc.returncode != 0:
            details = (fetch_proc.stderr or fetch_proc.stdout or "").strip()
            return False, f"fetch_failed: {details}"

    status_proc = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=worktree_path,
        capture_output=True,
        check=False,
    )
    if status_proc.returncode != 0:
        return False, "status_failed"
    if status_proc.stdout:
        classification, scratch_paths = _classify_worktree_dirt(status_proc.stdout, worktree_path=worktree_path)
        if classification == "scratch_only":
            _restore_reusable_scratch(worktree_path, scratch_paths)
        else:
            return False, "skipped_dirty_worktree"

    local_head = _git_commit_oid(worktree_path, "HEAD")
    if has_remote_origin:
        base_head = _git_commit_oid(worktree_path, f"origin/{base}")
    else:
        base_head = (
            _git_commit_oid(worktree_path, f"refs/heads/{base}")
            or _git_commit_oid(worktree_path, base)
            or _git_commit_oid(repo_root, base)
        )
    if not local_head or not base_head:
        return False, "unverifiable_refs: missing local HEAD or fetched base"

    remote_task_head: str | None = None
    if has_remote_origin:
        heads_snapshot, snapshot_status = _get_remote_heads_snapshot(
            worktree_path,
            "origin",
            network_timeout_seconds=network_timeout_seconds,
        )
        if heads_snapshot is None:
            return False, snapshot_status
        remote_task_exists = expected_branch in heads_snapshot
        if remote_task_exists:
            advertised_task_head = heads_snapshot[expected_branch]
            fetch_task_proc, network_error = _run_git_network_command(
                worktree_path,
                [
                    "fetch",
                    "origin",
                    "--quiet",
                    f"+refs/heads/{expected_branch}:refs/remotes/origin/{expected_branch}",
                ],
                timeout_seconds=network_timeout_seconds,
            )
            if network_error or fetch_task_proc is None:
                return False, f"fetch_timed_out: {network_error or 'unknown network failure'}"
            if fetch_task_proc.returncode != 0:
                details = (fetch_task_proc.stderr or fetch_task_proc.stdout or "").strip()
                return False, f"fetch_failed: {details}"
            remote_task_head = _git_commit_oid(worktree_path, f"origin/{expected_branch}")
            if not remote_task_head:
                return False, "unverifiable_refs: missing fetched remote task HEAD"
            if remote_task_head.lower() != advertised_task_head.lower():
                return False, (
                    "unverifiable_refs: fetched remote task HEAD does not match "
                    f"advertised HEAD ({remote_task_head} != {advertised_task_head})"
                )
            if local_head != remote_task_head:
                remote_contains_local_rc, _ = _git_output(
                    worktree_path,
                    "merge-base",
                    "--is-ancestor",
                    local_head,
                    remote_task_head,
                )
                if remote_contains_local_rc not in {0, 1}:
                    return False, "unverifiable_refs: cannot compare local and remote task HEADs"
                if remote_contains_local_rc != 0:
                    return False, f"task_head_mismatch: local={local_head} remote={remote_task_head}"
                task_ff_proc = subprocess.run(
                    ["git", "merge", "--ff-only", f"origin/{expected_branch}"],
                    cwd=worktree_path,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if task_ff_proc.returncode != 0:
                    details = (task_ff_proc.stderr or task_ff_proc.stdout or "").strip().splitlines()
                    return False, f"task_fast_forward_failed: {details[0] if details else 'unknown'}"
                local_head = _git_commit_oid(worktree_path, "HEAD")
                if local_head != remote_task_head:
                    return False, "task_fast_forward_failed: resulting HEAD did not match remote task HEAD"

    base_contains_rc, _ = _git_output(
        worktree_path, "merge-base", "--is-ancestor", local_head, base_head
    )
    if base_contains_rc not in {0, 1}:
        return False, "unverifiable_refs: cannot compare local HEAD with fetched base"
    if base_contains_rc == 0:
        merge_proc = subprocess.run(
            ["git", "merge", "--ff-only", f"origin/{base}"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if merge_proc.returncode != 0:
            details = (merge_proc.stderr or merge_proc.stdout or "").strip().splitlines()
            return False, f"fast_forward_failed: {details[0] if details else 'unknown'}"
        return True, f"ff_to_{base_head[:12]}"

    task_contains_rc, _ = _git_output(
        worktree_path, "merge-base", "--is-ancestor", base_head, local_head
    )
    if task_contains_rc not in {0, 1}:
        return False, "unverifiable_refs: cannot compare fetched base with local HEAD"
    if task_contains_rc == 0:
        return True, f"base_present_at_{local_head[:12]}"
    if not remote_task_head:
        return False, "unverifiable_refs: diverged task branch has no fetched remote task HEAD"
    return True, f"base_advance_rebase_required:local={local_head},base={base_head}"


def _task_brief_context_candidates(task_id: str | None, rel_context_path: str) -> list[str]:
    normalized = rel_context_path.replace("\\", "/").strip()
    candidates = [normalized]
    if ".orchestrator/task-briefs/" in normalized and task_id:
        hyphen_slug = _task_id_slug(task_id)
        underscore_slug = hyphen_slug.replace("-", "_")
        for slug in (underscore_slug, hyphen_slug, normalize_agent_id(task_id)):
            if slug:
                candidates.append(f".orchestrator/task-briefs/{slug}.md")
    ordered: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def _atomic_replace_context_bytes(
    destination: Path,
    payload: bytes,
    *,
    source_stat: os.stat_result | None = None,
) -> None:
    """Write context bytes without following an existing destination inode.

    In particular, replacing the directory entry prevents an untracked hard link
    at the destination from mutating a tracked file that shares the same inode.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.context-",
        dir=str(destination.parent),
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as temp_file:
            temp_file.write(payload)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        if source_stat is not None:
            os.chmod(temp_path, source_stat.st_mode & 0o777)
        os.replace(temp_path, destination)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _atomic_copy_context_file(source: Path, destination: Path) -> None:
    _atomic_replace_context_bytes(
        destination,
        source.read_bytes(),
        source_stat=source.stat(follow_symlinks=False),
    )


def _atomic_write_context_text(destination: Path, text: str) -> None:
    _atomic_replace_context_bytes(destination, text.encode("utf-8"))


def _is_tracked_in_worktree(worktree_path: Path, rel_path: str) -> bool:
    try:
        res = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", rel_path],
            cwd=str(worktree_path),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return res.returncode == 0
    except Exception:
        return False


def _file_or_dir_hash(path: Path) -> str | None:
    try:
        if not path.exists():
            return None
        if path.is_file():
            return hashlib.sha256(path.read_bytes()).hexdigest()
        if path.is_dir():
            hasher = hashlib.sha256()
            for subfile in sorted(path.rglob("*")):
                if subfile.is_file():
                    rel = str(subfile.relative_to(path))
                    hasher.update(rel.encode("utf-8"))
                    hasher.update(subfile.read_bytes())
            return hasher.hexdigest()
    except Exception:
        return None
    return None


def _is_valid_sha256(h: str | None) -> bool:
    return bool(h and isinstance(h, str) and len(h) == 64 and all(c in "0123456789abcdefABCDEF" for c in h))


def _generated_worker_task_brief(config: dict[str, Any], task_id: str | None) -> str:
    try:
        text, _, _ = generate_task_brief_content(config, str(task_id or ""))
        return text
    except ValueError as err:
        if "Archived-task ambiguity" in str(err):
            raise
        task = task_index_from_status(config, load_status(config)).get(str(task_id or ""))
        if not task:
            return "\n".join(
                [
                    f"# Task Brief: {task_id or 'unknown-task'}",
                    "",
                    "Generated in the worker workspace because the supervisor root did not have a task brief file.",
                    "",
                ]
            )
        source_docs = [str(item).strip() for item in (task.get("source_docs") or []) if str(item).strip()]
        acceptance = [str(item).strip() for item in (task.get("acceptance") or []) if str(item).strip()]
        verification = [str(item).strip() for item in (task.get("verification") or []) if str(item).strip()]
        body = [
            f"# Task Brief: {task.get('id') or task_id}",
            "",
            "Generated in the worker workspace because the supervisor root did not have a task brief file.",
            "",
            "## Task",
            f"- Title: {task.get('title') or '-'}",
            f"- Status: {task.get('status') or '-'}",
            f"- Owner: {task.get('owner') or '-'}",
            f"- Reviewer: {task.get('reviewer') or '-'}",
            f"- Next: {task.get('next') or '-'}",
            "",
            "## Summary",
            str(task.get("summary_zh") or "-"),
            "",
            "## Source Documents",
        ]
        body.extend([f"- {item}" for item in source_docs] or ["- none"])
        body.extend(["", "## Acceptance"])
        body.extend([f"- {item}" for item in acceptance] or ["- none"])
        body.extend(["", "## Verification"])
        body.extend([f"- `{item}`" for item in verification] or ["- none"])
        body.append("")
        return "\n".join(body)


# Canonical worker context that lives in the supervisor root but is gitignored, so a
# fresh or reused worktree never contains it. Always safe to (re)seed as untracked
# copies: the reuse-dirt guard runs `git status --untracked-files=no`, so untracked
# seeds never block re-dispatch.
_SEEDABLE_UNTRACKED_CONTEXT = ("ai-status.json", "current-work.md", "ai-activity-log.jsonl")


def _generated_collaboration_guide(config: dict[str, Any]) -> str:
    """Materialize AI_COLLABORATION_GUIDE.md into a worktree when the repo has none.

    The wakeup prompt tells every worker to read AI_COLLABORATION_GUIDE.md first, but
    the file is not tracked anywhere in the repo, so a worker (notably the
    Antigravity/`agy` CLI) burns its whole session hunting for it and never reaches
    the commit/closeout step. Seeding a concise, accurate guide stops the hunt.
    """
    return "\n".join(
        [
            "# AI Collaboration Guide (worker-seeded)",
            "",
            "Generated into this worktree because the supervisor root has no tracked",
            "AI_COLLABORATION_GUIDE.md. It restates the rules already in your wakeup prompt.",
            "",
            "## Workspace",
            "- You run inside an isolated per-task git worktree. It is NOT a staging area.",
            "- Confirm you are on the expected `task/<TASK-ID>` branch; use",
            "  `./delivery_toolchain/git/task_start.sh \"<TASK-ID>\"` if not.",
            "- ai-status.json / current-work.md / ai-activity-log.jsonl are seeded here",
            "  (gitignored); do not edit them by hand — use the status commands.",
            "",
            "## Commit discipline (critical — uncommitted work jams the fleet)",
            "- Commit AND push your work before you finish. A worktree left dirty blocks",
            "  the next dispatch and can deadlock the whole fleet.",
            "- Anchor-commit intermediate states per .orchestrator/skills/worker-anchor-commit.md.",
            "- Commit subject must include the Task ID; body needs LLM-Agent / Task-ID / Reviewer.",
            "- No interactive git (`git add -p/-i`, `git commit --interactive`, `git rebase -i`).",
            "",
            "## Status & closeout",
            "- Update status only via `scripts/ai-status.sh` or `python3 scripts/ai_status.py`",
            "  with your own `AI_NAME`.",
            "- For `owned_finalize_dispatch` / `review_approved`, follow",
            "  .orchestrator/skills/task-closeout-finalization.md before `... done`.",
            "",
        ]
    )


def materialize_worker_context_files(
    config: dict[str, Any],
    request: DeliveryRequest,
    workspace_path: Path,
) -> list[str]:
    """Seed the context files a worker is told to read into its isolated worktree.

    Task briefs are copied/generated as before. The other canonical references
    (ai-status.json, current-work.md, AI_COLLABORATION_GUIDE.md, ...) live in the
    supervisor root but are gitignored or untracked, so a fresh/reused worktree does
    NOT contain them. Without them the worker — notably the Antigravity/`agy` CLI —
    burns its whole session hunting for files it was instructed to read and never
    reaches the commit/closeout step, leaving uncommitted dirt that then jams the
    reuse lease. Seeding them as untracked copies is safe: the reuse-dirt guard runs
    `git status --porcelain --untracked-files=no`, so untracked seeds never block
    re-dispatch, and we never overwrite a file the branch already tracks.
    """
    if not request.context_files:
        return []
    status_root = config_path(config, "status_file").parents[0].resolve()
    materialized: list[str] = []
    manifest_entries: list[dict[str, Any]] = []

    status_data = load_status(config)
    tasks = status_data.get("tasks", []) or []
    resolver = TaskResolver(tasks)
    task = resolver.get(request.task_id)
    is_mutating_or_p0 = False
    if task:
        is_mutating_or_p0 = (
            str(task.get("priority") or "").upper() == "P0"
            or bool(task.get("mutates_canonical"))
            or str(task.get("phase") or "").strip() != "Unassigned"
        )

    for rel_context_path in request.context_files:
        rel_value = str(rel_context_path or "").strip().replace("\\", "/")
        if not rel_value or Path(rel_value).is_absolute():
            continue
        valid_dest, destination, dest_err = validate_destination_context_path(rel_value, workspace_path)
        if not valid_dest:
            if is_mutating_or_p0:
                raise ValueError(
                    f"Fail-closed on workspace materialization for task {request.task_id}: {dest_err}"
                )
            continue
        is_tracked_rc, _ = _git_output(workspace_path, "ls-files", "--error-unmatch", rel_value)
        if is_tracked_rc == 0:
            # Never clobber any destination tracked by Git; doing so when live source bytes
            # differ from the tracked baseline mutates tracked content and makes the fresh worktree dirty.
            continue
        if ".orchestrator/task-briefs/" in rel_value:
            try:
                validate_task_archive_ambiguity(config, request.task_id)
            except ValueError as err:
                if is_mutating_or_p0:
                    raise ValueError(
                        f"Fail-closed on workspace materialization for task {request.task_id}: {err}"
                    ) from err
                continue

            destination.parent.mkdir(parents=True, exist_ok=True)
            copied = False
            found_src = None
            for candidate in _task_brief_context_candidates(request.task_id, rel_value):
                source = status_root / candidate
                if not source.exists() or not source.is_file():
                    continue
                existing_text = source.read_text(encoding="utf-8")
                if task and is_task_brief_stale(existing_text, task):
                    break
                _atomic_copy_context_file(source, destination)
                copied = True
                found_src = source
                break
            if not copied:
                try:
                    text = _generated_worker_task_brief(config, request.task_id)
                except ValueError as err:
                    if is_mutating_or_p0:
                        raise ValueError(
                            f"Fail-closed on workspace materialization for task {request.task_id}: {err}"
                        ) from err
                    continue
                _atomic_write_context_text(destination, text)
                for candidate in _task_brief_context_candidates(request.task_id, rel_value):
                    canon_brief = status_root / candidate
                    try:
                        canon_brief.parent.mkdir(parents=True, exist_ok=True)
                        canon_brief.write_text(text, encoding="utf-8")
                        found_src = canon_brief
                    except OSError:
                        pass
                    break
            brief_hash = _file_or_dir_hash(destination)
            if is_mutating_or_p0 and not _is_valid_sha256(brief_hash):
                raise ValueError(
                    f"Fail-closed on workspace materialization for task {request.task_id}: unable to establish valid 64-hex SHA256 integrity hash for task brief '{rel_value}'"
                )
            if not _is_valid_sha256(brief_hash):
                continue
            materialized.append(rel_value)
            manifest_entries.append({
                "relative_path": rel_value,
                "canonical_source_path": str(found_src.resolve()) if found_src else str((status_root / rel_value).resolve()),
                "sha256": brief_hash,
            })
            continue

        source = status_root / rel_value
        valid, norm_path, err_reason = validate_source_doc_path(rel_value, status_root, task=task)
        if not valid and rel_value not in _SEEDABLE_UNTRACKED_CONTEXT and rel_value != "AI_COLLABORATION_GUIDE.md":
            if is_mutating_or_p0:
                raise ValueError(f"Fail-closed on workspace materialization for task {request.task_id}: {err_reason} for '{rel_value}'")
            continue

        always_refresh = rel_value in _SEEDABLE_UNTRACKED_CONTEXT
        if source.exists():
            if destination.exists() and not always_refresh:
                source_hash = _file_or_dir_hash(source)
                dest_hash = _file_or_dir_hash(destination)
                if is_mutating_or_p0:
                    if not _is_valid_sha256(source_hash) or not _is_valid_sha256(dest_hash):
                        raise ValueError(
                            f"Fail-closed on workspace materialization for task {request.task_id}: unable to establish valid 64-hex SHA256 integrity hash for '{rel_value}' (canonical sha {source_hash} vs destination sha {dest_hash})"
                        )
                if source_hash and dest_hash and source_hash == dest_hash:
                    materialized.append(rel_value)
                    manifest_entries.append({
                        "relative_path": rel_value,
                        "canonical_source_path": str(source.resolve()),
                        "sha256": source_hash,
                    })
                    continue
                if _is_tracked_in_worktree(workspace_path, rel_value):
                    if is_mutating_or_p0:
                        raise ValueError(
                            f"Fail-closed on workspace materialization for task {request.task_id}: tracked document '{rel_value}' hash mismatch between worktree and canonical source"
                        )
                    continue

            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                if source.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    dir_failed = False
                    resolved_status_root = status_root.resolve()
                    for src_item in source.rglob("*"):
                        try:
                            src_item.resolve().relative_to(resolved_status_root)
                        except Exception as err:
                            if is_mutating_or_p0:
                                raise ValueError(
                                    f"Fail-closed on workspace materialization for task {request.task_id}: directory child symlink '{src_item}' points outside status root"
                                ) from err
                            dir_failed = True
                            break

                        rel_child = src_item.relative_to(source)
                        child_rel_value = (Path(rel_value) / rel_child).as_posix()
                        valid_child, child_dest, child_err = validate_destination_context_path(child_rel_value, workspace_path)
                        if not valid_child:
                            if is_mutating_or_p0:
                                raise ValueError(
                                    f"Fail-closed on workspace materialization for task {request.task_id}: {child_err}"
                                )
                            dir_failed = True
                            break
                        if src_item.is_dir():
                            child_dest.mkdir(parents=True, exist_ok=True)
                        elif src_item.is_file():
                            if child_dest.exists() and not always_refresh:
                                src_h = _file_or_dir_hash(src_item)
                                dst_h = _file_or_dir_hash(child_dest)
                                if is_mutating_or_p0 and (not _is_valid_sha256(src_h) or not _is_valid_sha256(dst_h)):
                                    raise ValueError(
                                        f"Fail-closed on workspace materialization for task {request.task_id}: unable to establish valid 64-hex SHA256 integrity hash for directory item '{child_rel_value}'"
                                    )
                                if src_h and dst_h and src_h == dst_h:
                                    continue
                                if _is_tracked_in_worktree(workspace_path, child_rel_value):
                                    if is_mutating_or_p0:
                                        raise ValueError(
                                            f"Fail-closed on workspace materialization for task {request.task_id}: tracked document item '{child_rel_value}' hash mismatch between worktree and canonical source"
                                        )
                                    dir_failed = True
                                    break

                            child_dest.parent.mkdir(parents=True, exist_ok=True)
                            try:
                                _atomic_copy_context_file(src_item, child_dest)
                            except OSError as err:
                                if is_mutating_or_p0:
                                    raise ValueError(
                                        f"Fail-closed on workspace materialization for task {request.task_id}: failed to copy directory source item '{child_rel_value}': {err}"
                                    ) from err
                                dir_failed = True
                                break
                    if dir_failed:
                        continue
                else:
                    try:
                        _atomic_copy_context_file(source, destination)
                    except OSError as err:
                        if is_mutating_or_p0:
                            raise ValueError(
                                f"Fail-closed on workspace materialization for task {request.task_id}: failed to copy source document '{rel_value}': {err}"
                            ) from err
                        continue
            except OSError as err:
                if is_mutating_or_p0:
                    raise ValueError(
                        f"Fail-closed on workspace materialization for task {request.task_id}: failed to copy source document '{rel_value}': {err}"
                    ) from err
                continue

            source_hash = _file_or_dir_hash(source)
            final_dest_hash = _file_or_dir_hash(destination)
            if is_mutating_or_p0:
                if not _is_valid_sha256(source_hash) or not _is_valid_sha256(final_dest_hash):
                    raise ValueError(
                        f"Fail-closed on workspace materialization for task {request.task_id}: unable to establish valid 64-hex SHA256 integrity hash for '{rel_value}' (canonical sha {source_hash} vs destination sha {final_dest_hash})"
                    )
                if source_hash != final_dest_hash:
                    raise ValueError(
                        f"Fail-closed on workspace materialization for task {request.task_id}: final source and destination tree mismatch for '{rel_value}' (canonical sha {source_hash} vs destination sha {final_dest_hash})"
                    )
            else:
                if not _is_valid_sha256(source_hash) or not _is_valid_sha256(final_dest_hash) or source_hash != final_dest_hash:
                    continue

            materialized.append(rel_value)
            manifest_entries.append({
                "relative_path": rel_value,
                "canonical_source_path": str(source.resolve()),
                "sha256": source_hash,
            })
        elif rel_value == "AI_COLLABORATION_GUIDE.md" and not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_context_text(destination, _generated_collaboration_guide(config))
            guide_hash = _file_or_dir_hash(destination)
            if is_mutating_or_p0 and not _is_valid_sha256(guide_hash):
                raise ValueError(
                    f"Fail-closed on workspace materialization for task {request.task_id}: unable to establish valid 64-hex SHA256 integrity hash for generated '{rel_value}'"
                )
            if not _is_valid_sha256(guide_hash):
                continue
            materialized.append(rel_value)
            manifest_entries.append({
                "relative_path": rel_value,
                "canonical_source_path": str((status_root / rel_value).resolve()),
                "sha256": guide_hash,
            })

    if materialized:
        request.metadata["materialized_context_files"] = materialized
        request.metadata["materialized_source_manifest"] = manifest_entries
        request.metadata["source_manifest"] = manifest_entries

    rc, out = _git_output(workspace_path, "rev-parse", "--git-path", "info/exclude")
    if rc == 0 and out.strip():
        try:
            exclude_path = Path(out.strip())
            if not exclude_path.is_absolute():
                exclude_path = (workspace_path / exclude_path).resolve()
            exclude_path.parent.mkdir(parents=True, exist_ok=True)
            existing_exclude = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
            lines_to_add = [
                "AI_COLLABORATION_GUIDE.md",
                "ai-status.json",
                "current-work.md",
                "ai-activity-log.jsonl",
                ".orchestrator/task-briefs/",
                ".orchestrator/reviews/",
            ]
            new_lines = [line for line in lines_to_add if line not in existing_exclude.splitlines()]
            if new_lines:
                with open(exclude_path, "a", encoding="utf-8") as ef:
                    if existing_exclude and not existing_exclude.endswith("\n"):
                        ef.write("\n")
                    for line in new_lines:
                        ef.write(f"{line}\n")
        except OSError:
            pass
    return materialized


def prepare_worker_workspace(
    config: dict[str, Any],
    state: dict[str, Any],
    request: DeliveryRequest,
    *,
    queue_event_id: str | None,
    target_agent: str | None,
) -> tuple[bool, str | None]:
    settings = worker_worktree_settings(config)
    if not settings.get("enabled"):
        return True, None
    if not worker_worktree_reason_enabled(request.reason, settings):
        return True, None
    workspace_task_id = worker_workspace_task_id(request)
    if not workspace_task_id:
        return True, None
    if request.metadata.get("workspace_path"):
        return True, None

    repo_root = config_path(config, "status_file").parents[0].resolve()
    branch = worker_task_branch(config, workspace_task_id)
    worktree_path = worker_task_worktree_path(config, workspace_task_id, settings)
    reused = False

    if settings.get("reuse_existing", True):
        existing = _existing_worktree_for_branch(repo_root, branch, exclude_root=True)
        if existing:
            worktree_path = existing
            reused = True
            refresh_ok, refresh_status = _refresh_reused_worker_worktree(
                repo_root,
                worktree_path,
                str(settings.get("base_ref") or "origin/dev"),
                branch,
                network_timeout_seconds=float(settings["git_network_timeout_seconds"]),
            )
            write_activity_log(
                config,
                {
                    "type": "worker_worktree_refreshed",
                    "task_id": request.task_id,
                    "target_agent": target_agent,
                    "queue_event_id": queue_event_id,
                    "workspace_branch": branch,
                    "workspace_path": str(worktree_path),
                    "refresh_ok": refresh_ok,
                    "refresh_status": refresh_status,
                },
            )
            if not refresh_ok:
                if refresh_status == "skipped_dirty_worktree":
                    task_sha, task_sha_source = _fetch_authoritative_task_head(
                        repo_root,
                        worktree_path,
                        branch,
                        network_timeout_seconds=float(settings["git_network_timeout_seconds"]),
                    )
                    recovered = bool(task_sha) and _quarantine_and_preserve_dirty_worktree(
                        config,
                        state,
                        worktree_path,
                        workspace_task_id,
                        expected_branch=branch,
                        run_id=None,
                        trigger="lease_recovery",
                    )
                    if not task_sha:
                        refresh_status = task_sha_source
                    if recovered:
                        original_worktree_path = worktree_path
                        q_stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"_{uuid.uuid4().hex[:8]}"
                        fresh_path = worktree_path.parent / f"{worktree_path.name}.lease_{q_stamp}"
                        if task_sha:
                            fresh_path.parent.mkdir(parents=True, exist_ok=True)
                            create_proc = subprocess.run(
                                ["git", "worktree", "add", "--detach", str(fresh_path), task_sha],
                                cwd=repo_root,
                                capture_output=True,
                                text=True,
                                check=False,
                            )
                            if create_proc.returncode != 0 and not (repo_root / ".git").exists():
                                fresh_path.mkdir(parents=True, exist_ok=True)
                                create_ok = True
                            else:
                                create_ok = create_proc.returncode == 0

                            if create_ok:
                                worktree_path = fresh_path
                                fresh_head = _git_commit_oid(fresh_path, "HEAD") or task_sha
                                if fresh_head and fresh_head == task_sha:
                                    refresh_ok = True
                                    refresh_status = f"lease_recovered_exact_task_sha:{task_sha[:12]}"
                                    materialize_worker_context_files(config, request, worktree_path)
                                    write_activity_log(
                                        config,
                                        {
                                            "type": "worker_worktree_lease_recovered",
                                            "task_id": request.task_id,
                                            "target_agent": target_agent,
                                            "queue_event_id": queue_event_id,
                                            "workspace_branch": branch,
                                            "workspace_path": str(worktree_path),
                                            "quarantined_worktree_path": str(original_worktree_path),
                                            "task_sha": task_sha,
                                            "task_sha_source": task_sha_source,
                                            "leased_remote_exact": task_sha_source == "remote_exact_task_ref",
                                            "refresh_ok": refresh_ok,
                                            "refresh_status": refresh_status,
                                        },
                                    )
                                else:
                                    refresh_ok = False
                                    refresh_status = (
                                        f"recovered_task_sha_mismatch:expected={task_sha},found={fresh_head}"
                                    )
                if (
                    not refresh_ok
                    and refresh_status.startswith("task_head_mismatch:")
                    and bool(settings.get("recover_clean_diverged_worktrees", False))
                ):
                    recovered, recovery_detail = _preserve_and_reset_clean_diverged_worktree(
                        config,
                        state,
                        worktree_path,
                        workspace_task_id,
                        branch,
                    )
                    if recovered:
                        refresh_ok, refresh_status = _refresh_reused_worker_worktree(
                            repo_root,
                            worktree_path,
                            str(settings.get("base_ref") or "origin/dev"),
                            branch,
                            network_timeout_seconds=float(settings["git_network_timeout_seconds"]),
                        )
                        write_activity_log(
                            config,
                            {
                                "type": "worker_worktree_clean_divergence_recovery_verified",
                                "task_id": request.task_id,
                                "target_agent": target_agent,
                                "queue_event_id": queue_event_id,
                                "workspace_branch": branch,
                                "workspace_path": str(worktree_path),
                                "recovery_detail": recovery_detail,
                                "refresh_ok": refresh_ok,
                                "refresh_status": refresh_status,
                            },
                        )
                    else:
                        write_activity_log(
                            config,
                            {
                                "type": "worker_worktree_clean_divergence_recovery_blocked",
                                "task_id": request.task_id,
                                "target_agent": target_agent,
                                "queue_event_id": queue_event_id,
                                "workspace_branch": branch,
                                "workspace_path": str(worktree_path),
                                "recovery_detail": recovery_detail,
                                "refresh_status": refresh_status,
                            },
                        )
                if not refresh_ok and refresh_status != "skipped_dirty_worktree":
                    # The clean-but-unpublished deadlock. Publishing is only
                    # attempted for fast-forwards on a clean worktree; anything
                    # genuinely diverged falls through to the escalation below.
                    published, publish_detail = _publish_unpublished_task_branch(worktree_path, branch)
                    if published:
                        refresh_ok, refresh_status = _refresh_reused_worker_worktree(
                            repo_root,
                            worktree_path,
                            str(settings.get("base_ref") or "origin/dev"),
                            branch,
                            network_timeout_seconds=float(settings["git_network_timeout_seconds"]),
                        )
                        write_activity_log(
                            config,
                            {
                                "type": "worker_worktree_branch_published",
                                "task_id": request.task_id,
                                "target_agent": target_agent,
                                "queue_event_id": queue_event_id,
                                "workspace_branch": branch,
                                "workspace_path": str(worktree_path),
                                "publish_detail": publish_detail,
                                "refresh_ok": refresh_ok,
                                "refresh_status": refresh_status,
                            },
                        )
                        console_log(
                            f"worktree branch published: task={request.task_id} branch={branch} "
                            f"{publish_detail} refresh_ok={refresh_ok}",
                            quiet=SUPERVISOR_LOG_QUIET,
                        )

                if not refresh_ok:
                    if refresh_status == "skipped_dirty_worktree":
                        reason = (
                            "has dirty tracked or staged changes. Preserve and commit the "
                            "task-owned work before dispatch."
                        )
                    else:
                        reason = f"failed the fail-closed refresh policy ({refresh_status})."
                    message = (
                        f"Cannot lease isolated worker worktree for {workspace_task_id}: "
                        f"reused worktree {worktree_path} {reason}"
                    )
                    write_activity_log(
                        config,
                        {
                            "type": "dispatch_blocked_worktree_lease",
                            "task_id": request.task_id,
                            "workspace_task_id": workspace_task_id,
                            "target_agent": target_agent,
                            "queue_event_id": queue_event_id,
                            "message": message,
                            "workspace_branch": branch,
                            "workspace_path": str(worktree_path),
                            "refresh_status": refresh_status,
                        },
                    )
                    _record_worktree_lease_block(
                        config,
                        state,
                        task_id=str(request.task_id or workspace_task_id),
                        refresh_status=refresh_status,
                        message=message,
                    )
                    return False, message
                _clear_worktree_lease_block(state, str(request.task_id or workspace_task_id))
            if refresh_status.startswith("base_advance_rebase_required:"):
                if str(request.reason or "").strip() == "owned_finalize_dispatch":
                    # The reviewer approved an exact immutable head. A finalize
                    # worker may observe that dev advanced, but must never compose
                    # the base into the approved branch and invalidate the review.
                    request.metadata.update(
                        {
                            "approved_head_immutable": True,
                            "base_advance_deferred_to_merge_queue": True,
                            "worktree_refresh_status": refresh_status,
                        }
                    )
                else:
                    base_advance_prompt = (
                        "BASE ADVANCE REQUIRED BEFORE EDITING OR HANDOFF: this clean local task "
                        f"HEAD exactly matches origin/{branch}, but {branch} diverges from "
                        f"{settings.get('base_ref') or 'origin/dev'}. The task owner must fetch "
                        "and rebase/compose the current base in this task worktree, resolve and "
                        "verify it, then push normally. Do not reset, discard, or overwrite task "
                        "history.\n\n"
                    )
                    request.message = base_advance_prompt + request.message
                    request.metadata.update(
                        {
                            "base_advance_required": True,
                            "worktree_refresh_status": refresh_status,
                        }
                    )

    if not reused:
        if _branch_checked_out_in_root(repo_root, branch):
            message = (
                f"Cannot lease isolated worker worktree for {workspace_task_id}: "
                f"branch {branch} is currently checked out in supervisor root {repo_root}. "
                "Move the supervisor root back to dev or finish that root task branch first."
            )
            write_activity_log(
                config,
                {
                    "type": "dispatch_blocked_worktree_lease",
                    "task_id": request.task_id,
                    "workspace_task_id": workspace_task_id,
                    "target_agent": target_agent,
                    "queue_event_id": queue_event_id,
                    "message": message,
                    "workspace_branch": branch,
                    "workspace_path": str(worktree_path),
                },
            )
            return False, message
        created, error = _create_worker_worktree(repo_root, worktree_path, branch, str(settings.get("base_ref") or "origin/dev"))
        if not created:
            message = error or f"Failed to create worker worktree for {workspace_task_id}."
            write_activity_log(
                config,
                {
                    "type": "dispatch_blocked_worktree_lease",
                    "task_id": request.task_id,
                    "workspace_task_id": workspace_task_id,
                    "target_agent": target_agent,
                    "queue_event_id": queue_event_id,
                    "message": message,
                    "workspace_branch": branch,
                    "workspace_path": str(worktree_path),
                },
            )
            return False, message

    request.metadata.update(
        {
            "workspace_mode": "isolated_worktree",
            "workspace_path": str(worktree_path),
            "workspace_branch": branch,
            "status_root": str(repo_root),
        }
    )
    materialized_context_files = materialize_worker_context_files(config, request, worktree_path)
    leases = state.setdefault("worker_worktrees", {}).setdefault("leases", {})
    leases[workspace_task_id] = {
        "task_id": request.task_id,
        "workspace_task_id": workspace_task_id,
        "branch": branch,
        "path": str(worktree_path),
        "status_root": str(repo_root),
        "last_queue_event_id": queue_event_id,
        "last_target_agent": target_agent,
        "last_used_at": utc_now(),
        "materialized_context_files": materialized_context_files,
        "materialized_source_manifest": request.metadata.get("materialized_source_manifest", []),
        "source_manifest": request.metadata.get("source_manifest", []),
    }
    write_activity_log(
        config,
        {
            "type": "worker_worktree_reused" if reused else "worker_worktree_allocated",
            "task_id": request.task_id,
            "workspace_task_id": workspace_task_id,
            "target_agent": target_agent,
            "queue_event_id": queue_event_id,
            "workspace_branch": branch,
            "workspace_path": str(worktree_path),
            "status_root": str(repo_root),
        },
    )
    return True, None


def worker_tree_guard_settings(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("worker_tree_guard")
    settings = raw if isinstance(raw, dict) else {}
    blocking_globs = settings.get("blocking_globs")
    auto_restore_globs = settings.get("auto_restore_globs")
    return {
        "enabled": bool(settings.get("enabled", False)),
        "mode": str(settings.get("mode") or "warn").strip().lower(),
        "blocking_globs": list(blocking_globs)
        if isinstance(blocking_globs, list)
        else [
            ".orchestrator/supervisor.py",
            "supervisor.py",
            ".orchestrator/skills/**",
            "branch-strategy.md",
            "docs/conventions/GIT_WORKFLOW.md",
            "config*.json",
            ".orchestrator/config*.json",
            "docs/**",
        ],
        "auto_restore_globs": list(auto_restore_globs)
        if isinstance(auto_restore_globs, list)
        else [
            "ai-activity-log.jsonl",
            "ai-status.json",
            "current-work.md",
            "dashboard-bundle.json",
            "docs-site/**",
        ],
        "auto_restore_enabled": bool(settings.get("auto_restore_enabled", False)),
    }


def _git_dirty_entries(cwd: Path | None = None) -> list[dict[str, str]]:
    proc = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=cwd or THIS_DIR.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    entries: list[dict[str, str]] = []
    parts = proc.stdout.split("\0")
    index = 0
    while index < len(parts):
        raw = parts[index]
        index += 1
        if not raw:
            continue
        status = raw[:2]
        path = raw[3:] if len(raw) > 3 else ""
        if not path:
            continue
        entries.append({"status": status, "path": path.replace("\\", "/")})
        if status[:1] in {"R", "C"} and index < len(parts):
            index += 1
    return entries


def _path_matches_any_glob(path: str, patterns: list[Any]) -> bool:
    normalized = path.replace("\\", "/")
    basename = Path(normalized).name
    for raw_pattern in patterns:
        pattern = str(raw_pattern or "").strip().replace("\\", "/")
        if not pattern:
            continue
        if fnmatch.fnmatchcase(normalized, pattern):
            return True
        if "/" not in pattern and fnmatch.fnmatchcase(basename, pattern):
            return True
    return False


def check_worker_tree_clean(
    config: dict[str, Any],
    *,
    run_id: str | None,
    task_id: str | None,
    target_agent: str | None,
    queue_event_id: str | None,
    cwd: Path | None = None,
) -> tuple[bool, str | None]:
    settings = worker_tree_guard_settings(config)
    if not settings.get("enabled"):
        return True, None
    mode = str(settings.get("mode") or "warn").lower()
    if mode in {"off", "disabled", "false"}:
        return True, None

    dirty_entries = _git_dirty_entries(cwd)
    if not dirty_entries:
        return True, None

    blocking_globs = settings.get("blocking_globs") or []
    blocking_entries = [
        entry
        for entry in dirty_entries
        if _path_matches_any_glob(entry["path"], blocking_globs)
    ]
    if not blocking_entries:
        return True, None

    display_entries = [f"{entry['status']} {entry['path']}" for entry in blocking_entries[:20]]
    remaining = max(0, len(blocking_entries) - len(display_entries))
    suffix = f" (+{remaining} more)" if remaining else ""
    message = (
        "Worker tree guard found dirty high-fragility files before dispatch; "
        "anchor or close out the existing task-owned diff before yielding: "
        + "; ".join(display_entries)
        + suffix
    )
    activity_type = "dispatch_blocked_dirty_tree" if mode == "block" else "dispatch_dirty_tree_warning"
    write_activity_log(
        config,
        {
            "type": activity_type,
            "task_id": task_id,
            "target_agent": target_agent,
            "message": message,
            "queue_event_id": queue_event_id,
            "worker_run_id": run_id,
            "blocking_paths": [entry["path"] for entry in blocking_entries],
            "mode": mode,
            "workspace_path": str(cwd) if cwd else None,
        },
    )
    return mode != "block", message


def start_worker_for_request(
    config: dict[str, Any],
    state: dict[str, Any],
    provider_report: dict[str, Any],
    request: DeliveryRequest,
    *,
    queue_event_id: str | None,
    attempt_count: int,
    event_id_for_log: str | None,
    parent_run_id: str | None = None,
    delivery_mode_override: str | None = None,
    activity_type: str = "worker_started",
    activity_message: str | None = None,
) -> tuple[bool, str | None, dict[str, Any] | None]:
    agent = agent_config_for(config, request.agent_id)
    adapter_name = delivery_mode_override or agent.get("adapter", "file_inbox")
    adapter = build_adapter(adapter_name, config=config, provider_capabilities=provider_report)
    result = adapter.deliver(request)
    if not result.ok:
        failure_worker = {
            "provider": request.provider,
            "agent_id": request.agent_id,
            "task_id": request.task_id,
            "queue_event_id": event_id_for_log,
            "run_id": None,
            "log_path": result.log_path,
        }
        failure_summary = summarize_failure_reason(result.error or result.notes or "Worker delivery failed.", request.provider)
        raw_ref = write_failure_evidence(
            config,
            worker=failure_worker,
            reason=result.error or result.notes or "Worker delivery failed.",
            failure_kind=failure_summary.get("kind"),
        )
        write_activity_log(
            config,
            {
                "type": "worker_failed",
                "task_id": request.task_id,
                "target_agent": display_name_for(config, agent["id"]),
                "delivery_mode": result.mode,
                "message": failure_summary.get("summary") or "Worker delivery failed.",
                "queue_event_id": event_id_for_log,
                "parent_run_id": parent_run_id,
                "raw_ref": raw_ref,
            },
        )
        return False, failure_summary.get("summary") or result.error or result.notes or "Worker delivery failed.", None

    worker_run_id = result.run_id or new_runtime_id(request.provider)
    logical_agent_id = str(request.metadata.get("logical_agent_id") or agent["id"])
    dispatch_slot_id = str(request.metadata.get("dispatch_slot_id") or "")
    now_dt = datetime.now(UTC)
    now = _isoformat_utc(now_dt)
    result_metadata = result.metadata if isinstance(result.metadata, dict) else {}
    state.setdefault("workers", {})[worker_run_id] = {
        "run_id": worker_run_id,
        "provider": request.provider,
        "agent_id": agent["id"],
        "logical_agent_id": logical_agent_id,
        "dispatch_slot_id": dispatch_slot_id or None,
        "dispatch_slot": request.metadata.get("dispatch_slot"),
        # Keep the credential/account pool captured at dispatch time.  Looking
        # it up from `request.provider` here used to split one real account
        # across aliases and let each alias consume a separate quota budget.
        "quota_group": agent_quota_group_id(config, agent["id"]),
        "task_id": request.task_id,
        "session_id": result.session_id,
        "mode": result.mode,
        "status": "manual_pending" if result.manual_confirmation_required and not result.auto_delivered else "running",
        "last_event_at": now,
        "last_heartbeat_at": None,
        "lease_acquired_at": now,
        "lease_expires_at": worker_lease_expiry(config, now_dt),
        "deferred_action": None,
        "resume_token": result.resume_token or result.session_id,
        "pr_url": normalize_pr_url(config, result.pr_url),
        "session_url": result.session_url,
        "attempt_count": attempt_count,
        "queue_event_id": queue_event_id,
        "command": result.command,
        "log_path": result.log_path,
        "payload_path": result.payload_path,
        "workspace_mode": request.metadata.get("workspace_mode"),
        "workspace_path": request.metadata.get("workspace_path"),
        "workspace_branch": request.metadata.get("workspace_branch"),
        "status_root": request.metadata.get("status_root"),
        "pid": result.pid,
        "heartbeat_path": result_metadata.get("heartbeat_path"),
        "runner_status_path": result_metadata.get("runner_status_path"),
        # Immutable dispatch-time model pool (antigravity rotation). Quota
        # failures are recorded against this, not against the pool that happens
        # to be active when the failure is processed.
        model_rotation.WORKER_POOL_KEY: model_rotation.normalize_pool(
            result_metadata.get(model_rotation.WORKER_POOL_KEY)
        ),
        "notes": result.notes,
        "metadata": result_metadata,
        "request_snapshot": request_snapshot(request),
        "parent_run_id": parent_run_id,
        "retry_count": 0,
        "next_retry_at": None,
        "last_error": None,
    }
    record_worker_runtime_measurement(
        config,
        state,
        "worker_started",
        {
            "workers_started": 1,
            "queue_leases_started": 1 if queue_event_id else 0,
        },
        details={
            "worker_run_id": worker_run_id,
            "queue_event_id": queue_event_id,
            "task_id": request.task_id,
            "agent_id": agent["id"],
            "provider": request.provider,
            "lease_expires_at": state["workers"][worker_run_id].get("lease_expires_at"),
        },
        emit_activity=False,
    )
    # Persist immediately after launch so a supervisor crash cannot orphan
    # a live worker before the end-of-tick state save.
    save_runtime_state(config, state)
    write_activity_log(
        config,
        {
            "type": activity_type,
            "task_id": request.task_id,
            "target_agent": display_name_for(config, agent["id"]),
            "provider": request.provider,
            "delivery_mode": result.mode,
            "message": activity_message or f"Worker started via {result.adapter}: {request.reason}",
            "queue_event_id": event_id_for_log,
            "worker_run_id": worker_run_id,
            "parent_run_id": parent_run_id,
            "command": result.command,
            "log_path": result.log_path,
            "payload_path": result.payload_path,
            "workspace_mode": request.metadata.get("workspace_mode"),
            "workspace_path": request.metadata.get("workspace_path"),
            "workspace_branch": request.metadata.get("workspace_branch"),
            "status_root": request.metadata.get("status_root"),
        },
    )
    return True, worker_run_id, result.as_dict()


def process_queue(
    config: dict[str, Any],
    state: dict[str, Any],
    provider_report: dict[str, Any],
    *,
    agent_ids_override: list[str] | None = None,
    agent_override: str | None = None,
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
    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
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
        record = queue_status(state, event_id)
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
                    "provider": request.provider,
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
        try:
            workspace_ok, workspace_message = prepare_worker_workspace(
                config,
                state,
                request,
                queue_event_id=str(event_id or ""),
                target_agent=str(event.get("target_display_name") or event.get("target_agent") or ""),
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
                    "provider": request.provider,
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
                    "provider": request.provider,
                    "queue_event_id": event_id,
                    "message": record["error"],
                },
            )
            changed = True
            continue
        request_metadata = getattr(request, "metadata", {}) if hasattr(request, "metadata") else {}
        workspace_path = request_metadata.get("workspace_path") if isinstance(request_metadata, dict) else None
        guard_ok, guard_message = check_worker_tree_clean(
            config,
            run_id=str(event_id or ""),
            task_id=str(event.get("task_id") or ""),
            target_agent=str(event.get("target_display_name") or event.get("target_agent") or ""),
            queue_event_id=str(event_id or ""),
            cwd=Path(str(workspace_path)) if workspace_path else None,
        )
        if not guard_ok:
            record["status"] = "pending"
            record["last_wait_reason"] = guard_message
            record["dirty_tree_guard_at"] = utc_now()
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
                "provider": request.provider,
                "agent_id": request.agent_id,
                "logical_agent_id": (request.metadata or {}).get("logical_agent_id"),
                "task_id": request.task_id,
                "queue_event_id": event_id,
                "run_id": record.get("run_id"),
                "retry_count": max(0, int(record.get("attempt_count", 0)) - 1),
            }
            failure_reason = str(outcome or "")
            failure = classify_worker_failure(config, failure_worker, failure_reason)
            failure_summary = summarize_failure_reason(failure_reason, request.provider)
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
                    request.provider,
                    failure_reason,
                    task_id=str(request.task_id or ""),
                    failure_kind=str(failure.get("kind") or ""),
                    pause_kind=failure_kind,
                    raw_ref=raw_ref,
                    worker=failure_worker,
                )
            if is_terminal_quota_failure_kind(failure_kind):
                fence_account_pool_workers(config, state, failure_worker, failure_reason)
            if is_retryable_capacity_failure_kind(failure_kind):
                retry = worker_retry_settings(config, request.provider)
                retry_count = int(record.get("retry_count", 0))
                max_attempts = int(retry.get("max_attempts", 5))
                if retry_count < max_attempts:
                    schedule_queue_event_retry(
                        config,
                        record,
                        provider=request.provider,
                        reason=failure_summary.get("summary") or failure_reason,
                    )
                    write_activity_log(
                        config,
                        {
                            "type": "dispatch_retry_scheduled",
                            "provider": request.provider,
                            "task_id": request.task_id,
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
                and antigravity_pool_fallback_available(config, request.provider)
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
        record["lease_acquired_at"] = _isoformat_utc(queue_started_at)
        record["lease_expires_at"] = queue_lease_expiry(config, queue_started_at)
        record["processed_at"] = _isoformat_utc(queue_started_at)
        record.pop("last_wait_reason", None)
        sync_dispatched_task_status(config, event)
        changed = True
    return changed


def pid_is_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        waited_pid, _ = os.waitpid(pid, os.WNOHANG)
        if waited_pid == pid:
            return False
    except ChildProcessError:
        pass
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.exists():
        try:
            parts = proc_stat.read_text(encoding="utf-8", errors="ignore").split()
        except OSError:
            parts = []
        if len(parts) >= 3 and parts[2] == "Z":
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# Worker wakeup template always embeds `auto worker 身分是：<DisplayName>` in argv;
# scan /proc to recover the truth when state["workers"] bookkeeping drifts.
WORKER_AGENT_CMDLINE_MARKER = re.compile(r"auto worker 身分是：([A-Za-z][A-Za-z0-9_]*)")


def scan_live_worker_pids_by_agent(proc_root: Path | None = None) -> dict[str, list[int]]:
    """Return live worker PIDs grouped by agent display name parsed from /proc/*/cmdline."""
    root = proc_root if proc_root is not None else Path("/proc")
    result: dict[str, list[int]] = {}
    try:
        entries = list(root.iterdir())
    except OSError:
        return result
    self_pid = os.getpid()
    for entry in entries:
        name = entry.name
        if not name.isdigit():
            continue
        pid = int(name)
        if pid == self_pid:
            continue
        cmdline_path = entry / "cmdline"
        try:
            raw = cmdline_path.read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore")
        match = WORKER_AGENT_CMDLINE_MARKER.search(cmdline)
        if not match:
            continue
        agent = match.group(1)
        result.setdefault(agent, []).append(pid)
    return result


def active_worker_refs_for_agent_id(
    state: dict[str, Any],
    agent_id: str | None,
    active_statuses: set[str],
) -> list[str]:
    normalized_agent = normalize_agent_id(agent_id or "")
    if not normalized_agent:
        return []
    normalized_statuses = {str(status or "").strip().lower() for status in active_statuses}
    refs: list[str] = []
    for worker in (state.get("workers", {}) or {}).values():
        worker_agent_id = normalize_agent_id(str(worker.get("agent_id") or ""))
        if worker_agent_id != normalized_agent:
            continue
        worker_status = str(worker.get("status") or "").strip().lower()
        if worker_status not in normalized_statuses:
            continue
        pid = worker.get("pid")
        if pid:
            refs.append(str(pid))
            continue
        run_id = str(worker.get("run_id") or "").strip()
        if run_id:
            refs.append(run_id)
    return sorted(set(refs))


def terminate_worker_pid(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    return True


def normalize_pr_url(config: dict[str, Any], url: str | None) -> str | None:
    if not url:
        return None
    repo = (((config.get("github_bus") or {}).get("repo")) or "").strip()
    if not repo:
        return url
    expected = f"github.com/{repo}/"
    if "github.com/" in url and expected not in url:
        return None
    return url


def load_discussion_planning_state() -> dict[str, Any] | None:
    payload = load_json(PLANNING_STATE_FILE, default={}) or {}
    if not isinstance(payload, dict):
        return None
    if str(payload.get("planning_mode") or "").strip() != "discussion_planning":
        return None
    return payload


def discussion_planning_is_active(planning_state: dict[str, Any] | None) -> bool:
    if not planning_state:
        return False
    return str(planning_state.get("status") or "").strip() in {"active", "human_required"}


def discussion_planning_needs_materialization(config: dict[str, Any], planning_state: dict[str, Any] | None) -> bool:
    if not planning_state:
        return False
    if str(planning_state.get("status") or "").strip() != "accepted":
        return False
    if str(planning_state.get("human_gate_status") or "").strip() != "approved":
        return False
    if str(planning_state.get("materialized_at") or "").strip():
        return False

    proposed = [payload for payload in list(planning_state.get("proposed_execution_tasks") or []) if isinstance(payload, dict)]
    if not proposed:
        return False

    status = load_json(config_path(config, "status_file", "ai-status.json"), default={}) or {}
    schema = config.get("schema", {})
    tasks_path = str(schema.get("tasks_path", "tasks"))
    task_id_field = str(schema.get("task_id_field", "id"))
    task_map = {
        str(task.get(task_id_field) or "").strip(): task
        for task in list(status.get(tasks_path) or [])
        if isinstance(task, dict) and str(task.get(task_id_field) or "").strip()
    }
    resolver = TaskResolver(task_map)
    session_id = str(planning_state.get("session_id") or "").strip()

    for payload in proposed:
        task_id = str(payload.get("id") or "").strip()
        if not task_id:
            continue
        current = task_map.get(task_id)
        if not isinstance(current, dict):
            if resolver.snapshot(task_id) is not None:
                continue
            return True
        if str(current.get("source_plane") or "").strip().lower() != "planning":
            return True
        source_ref = current.get("source_ref") if isinstance(current.get("source_ref"), dict) else {}
        if session_id and str(source_ref.get("session_id") or "").strip() != session_id:
            return True

    return False


def auto_materialize_discussion_planning(config: dict[str, Any], planning_state: dict[str, Any] | None) -> bool:
    if not discussion_planning_needs_materialization(config, planning_state):
        return False

    status_root = config_path(config, "status_file", "ai-status.json").parent
    script = status_root / "scripts" / "planning_state.py"
    session_id = str((planning_state or {}).get("session_id") or "").strip()
    if not script.exists():
        write_activity_log(
            config,
            {
                "type": "planning_materialization_failed",
                "session_id": session_id,
                "message": f"Planning materialization script not found at {script}.",
            },
        )
        return False

    result = subprocess.run(
        [sys.executable, str(script), "materialize"],
        cwd=str(status_root),
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        write_activity_log(
            config,
            {
                "type": "planning_tasks_materialized_auto",
                "session_id": session_id,
                "message": result.stdout.strip() or "Accepted planning session auto-materialized into ai-status.json.",
            },
        )
        return True

    write_activity_log(
        config,
        {
            "type": "planning_materialization_failed",
            "session_id": session_id,
            "message": result.stderr.strip() or result.stdout.strip() or "Planning materialization failed.",
        },
    )
    return False


def discussion_planning_dir(planning_state: dict[str, Any]) -> str:
    planning_dir = str(planning_state.get("planning_dir") or "").strip()
    if planning_dir:
        return planning_dir
    return "docs/02-architecture/consensus/phase1"


def discussion_planning_artifact_path(planning_state: dict[str, Any], artifact_key: str, default_name: str) -> str:
    artifacts = planning_state.get("artifacts") if isinstance(planning_state.get("artifacts"), dict) else {}
    artifact = artifacts.get(artifact_key) if isinstance(artifacts.get(artifact_key), dict) else {}
    path = str(artifact.get("path") or "").strip()
    if path:
        return path
    return f"{discussion_planning_dir(planning_state)}/{default_name}"


def discussion_planning_readout_path(planning_state: dict[str, Any], agent_name: str) -> str:
    readouts = planning_state.get("readouts") if isinstance(planning_state.get("readouts"), dict) else {}
    readout = readouts.get(agent_name) if isinstance(readouts.get(agent_name), dict) else {}
    path = str(readout.get("path") or "").strip()
    if path:
        return path
    return f"{discussion_planning_dir(planning_state)}/{agent_name.lower()}-readout.md"


def discussion_planning_target_files(planning_state: dict[str, Any], agent_name: str) -> list[str]:
    target_files = [
        discussion_planning_artifact_path(planning_state, "planning_readme", "README.md"),
        str(planning_state.get("session_file") or "").strip() or f"{discussion_planning_dir(planning_state)}/planning-session.json",
        *[str(path).strip() for path in list(planning_state.get("brief_files") or []) if str(path).strip()],
        discussion_planning_artifact_path(planning_state, "starter_draft", "starter-draft.md"),
        discussion_planning_artifact_path(planning_state, "consensus_packet", "consensus-packet.md"),
        discussion_planning_readout_path(planning_state, agent_name),
    ]
    for output in list(planning_state.get("expected_outputs") or []):
        if not isinstance(output, dict):
            continue
        if str(output.get("owner") or "").strip() != agent_name:
            continue
        output_path = str(output.get("path") or "").strip()
        if output_path:
            target_files.append(output_path)
    ordered: list[str] = []
    seen: set[str] = set()
    for path in target_files:
        if path in seen:
            continue
        seen.add(path)
        ordered.append(path)
    return ordered


def build_discussion_planning_message(planning_state: dict[str, Any], agent_name: str, target_files: list[str]) -> str:
    session_id = str(planning_state.get("session_id") or "phase1")
    summary = str(planning_state.get("summary") or "").strip()
    objective = str(planning_state.get("objective") or "").strip()
    baton_owner = str(planning_state.get("baton_owner") or "Codex")
    next_reviewer = str(planning_state.get("next_reviewer") or "Codex2")
    current_round = int(planning_state.get("current_round") or 0)
    consensus_status = str(planning_state.get("consensus_status") or "not_started")
    readout_path = discussion_planning_readout_path(planning_state, agent_name)
    role_lines = [
        f"- 先寫你自己的 lane readout：`{readout_path}`",
        "- 只用 cited observations；不要直接改別人的 readout。",
        "- 如果你不是 baton owner，不要直接重寫 `starter-draft.md`。",
        f"- 完成 readout 後，請用 `./scripts/planning-state.sh readout {agent_name} submitted \"{agent_name} readout ready\"` 更新 planning state。",
    ]
    if agent_name == baton_owner:
        role_lines.append("- 你目前是 baton owner，除了自己的 readout，也要把 `starter-draft.md` seed 成可供 cross-review 的共享草稿。")
    if agent_name == "Claude":
        role_lines.append("- 你同時是 facilitator；目前先聚焦 readout 與 cited review，不要提早定稿 consensus packet，除非所有 readout 已齊。")
    return (
        "你被喚醒進入 discussion planning mode。\n\n"
        f"Session: {session_id}\n"
        f"Summary: {summary or 'Align architecture, delivery order, and execution slicing before implementation.'}\n"
        f"Baton owner: {baton_owner}\n"
        f"Next reviewer: {next_reviewer}\n"
        f"Current round: {current_round}\n"
        f"Consensus status: {consensus_status}\n\n"
        "請先閱讀這些 planning canonical files，並以它們作為本輪討論唯一共同真相：\n"
        + "\n".join(f"- {path}" for path in target_files)
        + "\n\n"
        + f"本輪目標：{objective or 'Align architecture, delivery order, and execution slicing before implementation.'}\n\n"
        + "\n".join(role_lines)
        + "\n"
    )


def worker_is_discussion_planning(worker: dict[str, Any]) -> bool:
    request_snapshot = worker.get("request_snapshot", {}) or {}
    metadata = request_snapshot.get("metadata", {}) or {}
    planning = metadata.get("planning")
    if isinstance(planning, dict) and planning:
        return True
    reason = str(request_snapshot.get("reason") or worker.get("reason") or "").strip()
    return reason.startswith("discussion_planning_")


def worker_is_coordination_dispatch(worker: dict[str, Any]) -> bool:
    request_snapshot = worker.get("request_snapshot", {}) or {}
    metadata = request_snapshot.get("metadata", {}) or {}
    coordination = metadata.get("coordination")
    if isinstance(coordination, dict) and coordination:
        return True
    reason = str(request_snapshot.get("reason") or worker.get("reason") or "").strip()
    return reason.startswith("coordination:")


def queue_discussion_planning_event(
    config: dict[str, Any],
    planning_state: dict[str, Any],
    *,
    agent_name: str,
    reason: str,
) -> str:
    agent = agent_config_for(config, agent_name)
    target_files = discussion_planning_target_files(planning_state, agent_name)
    queue_payload = {
        "event_id": new_runtime_id("evt"),
        "created_at": utc_now(),
        "event_key": (
            f"discussion:{planning_state.get('session_id')}:{agent_name}:{reason}:"
            f"round-{planning_state.get('current_round', 0)}:{planning_state.get('consensus_status', 'not_started')}"
        ),
        "task_id": str(planning_state.get("session_id") or "phase1"),
        "target_agent": agent["id"],
        "target_display_name": display_name_for(config, agent["id"]),
        "provider": agent.get("provider", agent["id"]),
        "reason": reason,
        "message": build_discussion_planning_message(planning_state, agent_name, target_files),
        "context_files": [relpath(path) for path in selected_shared_files(config)],
        "target_files": target_files,
        "metadata": {
            "planning": {
                "session_id": planning_state.get("session_id"),
                "mode": planning_state.get("planning_mode"),
                "baton_owner": planning_state.get("baton_owner"),
            }
        },
    }
    enqueue_event(config, queue_payload)
    write_activity_log(
        config,
        {
            "type": "planning_wake_queued",
            "task_id": queue_payload["task_id"],
            "target_agent": display_name_for(config, agent["id"]),
            "delivery_mode": config.get("providers", {}).get(agent.get("provider", agent["id"]), {}).get(
                "delivery_mode", agent.get("adapter", "file_inbox")
            ),
            "message": f"Discussion planning wake-up queued for {agent_name}: {reason}",
            "queue_event_id": queue_payload["event_id"],
        },
    )
    return queue_payload["event_key"]


def file_iso_mtime(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def update_from_log(config: dict[str, Any], worker: dict[str, Any]) -> None:
    log_path_value = worker.get("log_path")
    if not log_path_value:
        return
    log_path = Path(log_path_value)
    if not log_path.exists():
        return
    mtime = file_iso_mtime(log_path)
    if mtime and (not worker.get("last_event_at") or mtime > worker.get("last_event_at", "")):
        worker["last_event_at"] = mtime
    try:
        content = log_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not worker.get("session_id") and payload.get("session_id"):
            worker["session_id"] = payload.get("session_id")
            worker.setdefault("resume_token", worker["session_id"])
        if payload.get("type") == "result":
            if payload.get("stop_reason") == "tool_deferred":
                worker["status"] = "waiting_approval"
                worker["deferred_tool_use"] = payload.get("deferred_tool_use")
            if payload.get("pr_url") and not worker.get("pr_url"):
                worker["pr_url"] = normalize_pr_url(config, payload.get("pr_url"))
            if payload.get("session_url") and not worker.get("session_url"):
                worker["session_url"] = payload.get("session_url")
    if not worker.get("session_id"):
        for pattern in SESSION_ID_PATTERNS:
            match = pattern.search(content)
            if match:
                worker["session_id"] = match.group(1)
                worker.setdefault("resume_token", worker["session_id"])
                break
    if not worker.get("pr_url"):
        for url in URL_PATTERN.findall(content):
            if "/pull/" in url:
                worker["pr_url"] = normalize_pr_url(config, url)
                break
    worker["pr_url"] = normalize_pr_url(config, worker.get("pr_url"))
    if not worker.get("session_url"):
        for url in URL_PATTERN.findall(content):
            if "/agent" in url or "/sessions/" in url:
                worker["session_url"] = url
                break


def detect_worker_failure(worker: dict[str, Any]) -> str | None:
    log_path_value = worker.get("log_path")
    if not log_path_value:
        return None
    log_path = Path(log_path_value)
    if not log_path.exists():
        return None
    try:
        lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None

    fallback: str | None = None
    for idx in range(len(lines) - 1, -1, -1):
        line = lines[idx]
        stripped = line.strip()
        if not stripped:
            continue
        if '"ts":' in stripped and '"type":' in stripped:
            continue
        try:
            stream_payload = json.loads(stripped)
        except json.JSONDecodeError:
            stream_payload = None
        if isinstance(stream_payload, dict):
            if is_captured_orchestrator_record(stream_payload):
                continue
            if is_allowed_rate_limit_event(stream_payload):
                continue
            message = stream_payload.get("message")
            role = message.get("role") if isinstance(message, dict) else None
            if stream_payload.get("type") == "user" or role == "user":
                continue
        if SEARCH_RESULT_JSON_FIELD_PATTERN.search(stripped):
            continue
        if JSON_FIELD_LINE_PATTERN.search(stripped):
            continue
        if SEARCH_RESULT_LOG_JSON_PATTERN.search(stripped):
            continue
        if is_tool_command_output_failure_line(lines, idx):
            continue
        if any(pattern.search(stripped) for pattern in WORKER_FAILURE_FALSE_POSITIVE_PATTERNS):
            continue
        if any(pattern.search(stripped) for pattern in WORKER_FAILURE_PATTERNS):
            normalized = stripped.lower()
            if (
                "an unexpected critical error occurred" in normalized
                or "[object object]" in normalized
                or normalized.startswith("reason:")
                or normalized.startswith("retrydelayms:")
            ):
                fallback = fallback or stripped
                continue
            return stripped
    return fallback


def is_captured_orchestrator_record(payload: dict[str, Any]) -> bool:
    if payload.get("event_id") or payload.get("event_key"):
        return True
    if payload.get("queue_event_id") or payload.get("worker_run_id"):
        return True
    if payload.get("target_agent") or payload.get("target_display_name"):
        return True
    if isinstance(payload.get("metadata"), dict) and isinstance(payload.get("context_files"), list):
        return True
    return False


def is_allowed_rate_limit_event(payload: dict[str, Any]) -> bool:
    if payload.get("type") != "rate_limit_event":
        return False
    info = payload.get("rate_limit_info")
    if not isinstance(info, dict):
        return False
    return str(info.get("status") or "").strip().lower() == "allowed"


def is_tool_command_output_failure_line(lines: list[str], idx: int) -> bool:
    for prev_idx in range(idx - 1, max(idx - 5, -1), -1):
        previous = lines[prev_idx].strip()
        if not previous:
            continue
        return bool(COMMAND_OUTPUT_EXIT_LINE_PATTERN.search(previous))
    return False


# Antigravity (`agy`) per-account quota banner. Deliberately NOT a bare
# "quota reached" substring: ordinary application/test output such as
# "AssertionError: expected quota reached banner to be hidden" must stay a real
# task failure. Matching requires agy's full signature (the "Individual quota
# reached" phrase plus its upgrade/reset continuation) AND an antigravity
# provider, so only the real provider banner is treated as a quota outage.
AGY_QUOTA_SIGNATURE_PATTERN = re.compile(
    r"individual\s+quota\s+reached\b[\s.:,!-]*"
    r"(?:please\s+upgrade\s+your\s+subscription|resets?\s+in\b|try\s+again\s+(?:in|after)\b)",
    re.IGNORECASE,
)


def is_antigravity_provider(config: dict[str, Any] | None, provider: str | None) -> bool:
    """True when `provider` is served by the Antigravity (`agy`) adapter."""
    provider_id = str(provider or "").strip().lower()
    if not provider_id:
        return False
    if provider_id.startswith("antigravity"):
        return True
    providers = (config or {}).get("providers")
    entry = providers.get(provider_id) if isinstance(providers, dict) else None
    if not isinstance(entry, dict):
        return False
    if str(entry.get("adapter") or entry.get("type") or "").strip().lower() == "antigravity":
        return True
    return isinstance(entry.get("antigravity"), dict)


def is_antigravity_quota_banner(config: dict[str, Any] | None, provider: str | None, reason: str | None) -> bool:
    """True only for agy's real per-account quota banner on an agy provider."""
    if not reason or not is_antigravity_provider(config, provider):
        return False
    return bool(AGY_QUOTA_SIGNATURE_PATTERN.search(str(reason)))


# Claude CLI 5-hour session limit. The real banner is
# "You've hit your session limit · resets 5pm (UTC)", which the generic
# "hit your limit"/"hit your usage limit" markers do NOT match because of the
# extra "session" token — so it used to classify as a plain `terminal` failure,
# skipping both the provider pause path and the environmental-failure exemption
# in record_task_failure_streak (observed: a single session-limit window drove
# ODP-STORE-OPENING-001:claude to count=34).
#
# Deliberately NOT a bare "hit your session limit" substring: provider scoping
# alone cannot separate the banner from task output, because a Claude worker
# reports its own application/assertion text too. "AssertionError: expected
# You've hit your session limit banner to be hidden" is a genuine task failure
# and must stay `terminal`. Matching therefore requires the banner's reset
# continuation (observed separator: "·"; also accept the plain/dash forms and
# an explicit "resets at"), mirroring AGY_QUOTA_SIGNATURE_PATTERN above.
CLAUDE_SESSION_LIMIT_PATTERN = re.compile(
    r"hit\s+your\s+session\s+limit\b"
    r"[\s.,!·•|\-–—]*"
    r"(?:resets?\b|try\s+again\s+(?:in|at|after)\b)",
    re.IGNORECASE,
)


def is_claude_provider(config: dict[str, Any] | None, provider: str | None) -> bool:
    """True when `provider` is served by the Claude CLI adapter."""
    provider_id = str(provider or "").strip().lower()
    if not provider_id:
        return False
    if provider_id.startswith("claude"):
        return True
    providers = (config or {}).get("providers")
    entry = providers.get(provider_id) if isinstance(providers, dict) else None
    if not isinstance(entry, dict):
        return False
    return str(entry.get("adapter") or entry.get("type") or "").strip().lower() in {"claude", "claude_cli"}


def is_claude_session_limit_banner(config: dict[str, Any] | None, provider: str | None, reason: str | None) -> bool:
    """True only for the Claude CLI session-limit banner on a Claude provider."""
    if not reason or not is_claude_provider(config, provider):
        return False
    return bool(CLAUDE_SESSION_LIMIT_PATTERN.search(str(reason)))


def classify_worker_failure(config: dict[str, Any], worker: dict[str, Any], reason: str | None) -> dict[str, Any]:
    provider = str(worker.get("provider") or worker.get("agent_id") or "").strip().lower()
    normalized = str(reason or "").lower()
    retry = worker_retry_settings(config, worker.get("provider"))
    transient_patterns = [str(pattern).lower() for pattern in retry.get("transient_error_patterns", [])]

    auth_markers = {
        "status: 401",
        "unauthorized",
        "authentication",
        "not authenticated",
        "auth failed",
        "invalid api key",
        "forbidden",
        "permission denied",
    }
    terminal_quota_markers = {
        "status: 402",
        "credit balance is too low",
        "billing_error",
        "hit your limit",
        "hit your usage limit",
        "exhausted your capacity",
        "no quota",
        "you have no quota",
        "quota exceeded",
        "free daily quota has been reached",
        "free tier quota exceeded",
        "quota will reset after",
        "terminalquotaerror",
    }
    retryable_capacity_markers = {
        "status: 429",
        "retryablequotaerror",
        "quota_exhausted",
        "resource_exhausted",
        "rate limit",
        "rate limited",
        "no capacity available",
    }
    unknown_critical_markers = {
        "an unexpected critical error occurred",
        "[object object]",
    }
    provider_config_markers = {
        "error loading config.toml",
        "config.toml cannot be parsed",
        "unsupported service_tier",
        "unknown variant",
        "service_tier",
    }

    # Checked before anything else: a lane whose CLI will not start cannot
    # produce an auth, quota or config signal, and retrying it just burns the
    # queue one sub-second failure at a time.
    #
    # The named CLI must belong to this worker's provider family. A codex worker
    # that shells out to `claude` and reports Claude's launcher error says
    # nothing about the codex lane, and pausing it for 900s on that basis would
    # be a self-inflicted outage -- the same reasoning that makes
    # AGY_QUOTA_SIGNATURE_PATTERN insist on an antigravity provider.
    missing_cli = provider_launcher_missing_cli(reason)
    if missing_cli:
        provider_id = normalize_agent_id(str(provider or ""))
        expected_family = PROVIDER_CLI_FAMILY.get(missing_cli)
        if not provider_id or not expected_family or provider_id.startswith(expected_family):
            return {"kind": "provider_unavailable", "transient": False, "label": "provider CLI missing"}
    if is_github_cli_auth_failure(reason):
        return {"kind": "tool_auth", "transient": False, "label": "tool auth"}
    if "config.toml" in normalized and any(marker in normalized for marker in provider_config_markers):
        return {"kind": "provider_config", "transient": False, "label": "provider config"}
    if any(marker in normalized for marker in auth_markers):
        return {"kind": "auth", "transient": False, "label": "auth"}
    if is_antigravity_quota_banner(config, provider, reason):
        return {"kind": "quota_terminal", "transient": False, "label": "quota terminal"}
    if is_claude_session_limit_banner(config, provider, reason):
        return {"kind": "quota_terminal", "transient": False, "label": "quota terminal"}
    if any(marker in normalized for marker in terminal_quota_markers):
        return {"kind": "quota_terminal", "transient": False, "label": "quota terminal"}
    if any(marker in normalized for marker in retryable_capacity_markers):
        return {"kind": "capacity_retryable", "transient": True, "label": "capacity/429"}
    if provider.startswith("gemini") and any(marker in normalized for marker in unknown_critical_markers):
        return {"kind": "unknown_critical", "transient": False, "label": "unknown critical error"}
    if any(pattern in normalized for pattern in transient_patterns):
        return {"kind": "transient", "transient": True, "label": "transient"}
    if any(marker in normalized for marker in unknown_critical_markers):
        return {"kind": "unknown_critical", "transient": False, "label": "unknown critical error"}
    return {"kind": "terminal", "transient": False, "label": "terminal"}


def _parse_iso_utc(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _isoformat_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def worker_runtime_settings(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("worker_runtime")
    settings = dict(raw if isinstance(raw, dict) else {})
    supervisor_settings = config.get("supervisor", {}) if isinstance(config.get("supervisor"), dict) else {}
    settings.setdefault("worker_lease_seconds", supervisor_settings.get("worker_lease_seconds", 1800))
    settings.setdefault("queue_lease_seconds", supervisor_settings.get("queue_lease_seconds", 1800))
    settings.setdefault("heartbeat_stale_seconds", supervisor_settings.get("heartbeat_stale_seconds", 300))
    settings.setdefault("heartbeat_grace_seconds", supervisor_settings.get("heartbeat_grace_seconds", 60))
    settings.setdefault("runner_heartbeat_interval_seconds", 15)
    return settings


WORKER_RUNTIME_METRIC_COUNTERS = (
    "workers_started",
    "queue_leases_started",
    "marker_updates",
    "lease_refreshes",
    "missing_process_workers_failed",
    "expired_lease_workers_failed",
    "started_queue_records_requeued",
    "started_queue_records_failed",
    "stale_queue_records_completed",
    "capacity_pending_queue_events",
)


def worker_runtime_metrics_bucket(state: dict[str, Any]) -> dict[str, Any]:
    bucket = state.setdefault("worker_runtime_metrics", {})
    bucket.setdefault("version", 1)
    bucket.setdefault("updated_at", None)
    totals = bucket.setdefault("totals", {})
    for key in WORKER_RUNTIME_METRIC_COUNTERS:
        totals.setdefault(key, 0)
    bucket.setdefault("last_measurements", {})
    return bucket


def positive_runtime_counts(counts: dict[str, Any]) -> dict[str, int]:
    positive: dict[str, int] = {}
    for key, value in counts.items():
        try:
            amount = int(value)
        except (TypeError, ValueError):
            continue
        if amount > 0:
            positive[key] = amount
    return positive


def record_worker_runtime_measurement(
    config: dict[str, Any],
    state: dict[str, Any],
    measurement: str,
    counts: dict[str, Any],
    *,
    details: dict[str, Any] | None = None,
    emit_activity: bool = True,
) -> bool:
    positive = positive_runtime_counts(counts)
    if not positive and not details:
        return False
    now = utc_now()
    bucket = worker_runtime_metrics_bucket(state)
    totals = bucket.setdefault("totals", {})
    for key, amount in positive.items():
        totals[key] = int(totals.get(key, 0) or 0) + amount
    bucket["updated_at"] = now
    bucket.setdefault("last_measurements", {})[measurement] = {
        "at": now,
        "counts": positive,
        "details": details or {},
    }
    if emit_activity and positive:
        try:
            write_activity_log(
                config,
                {
                    "type": "worker_runtime_metrics",
                    "measurement": measurement,
                    "message": f"Worker runtime measurement {measurement}: {positive}",
                    "counts": positive,
                    "details": details or {},
                },
            )
        except KeyError:
            pass
    return True


def worker_lease_expiry(config: dict[str, Any], now: datetime | None = None) -> str:
    settings = worker_runtime_settings(config)
    now_dt = now or datetime.now(UTC)
    return _isoformat_utc(now_dt + timedelta(seconds=max(60, int(settings.get("worker_lease_seconds", 1800)))))


def queue_lease_expiry(config: dict[str, Any], now: datetime | None = None) -> str:
    settings = worker_runtime_settings(config)
    now_dt = now or datetime.now(UTC)
    return _isoformat_utc(now_dt + timedelta(seconds=max(60, int(settings.get("queue_lease_seconds", 1800)))))


def refresh_worker_lease(config: dict[str, Any], worker: dict[str, Any], now: datetime | None = None) -> None:
    now_dt = now or datetime.now(UTC)
    worker.setdefault("lease_acquired_at", _isoformat_utc(now_dt))
    worker["lease_expires_at"] = worker_lease_expiry(config, now_dt)


def _load_runtime_marker(path_value: Any) -> dict[str, Any] | None:
    if not path_value:
        return None
    path = Path(str(path_value))
    if not path.exists():
        return None
    try:
        payload = load_json(path, default={}) or {}
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def update_worker_runtime_markers(worker: dict[str, Any]) -> bool:
    metadata = worker.setdefault("metadata", {}) if isinstance(worker.get("metadata"), dict) else {}
    heartbeat_path = worker.get("heartbeat_path") or metadata.get("heartbeat_path")
    status_path = worker.get("runner_status_path") or metadata.get("runner_status_path")
    changed = False
    status_payload = _load_runtime_marker(status_path)
    heartbeat_payload = _load_runtime_marker(heartbeat_path)
    for payload in (status_payload, heartbeat_payload):
        if not payload:
            continue
        heartbeat_at = str(payload.get("last_heartbeat_at") or payload.get("updated_at") or "").strip()
        if heartbeat_at and heartbeat_at > str(worker.get("last_heartbeat_at") or ""):
            worker["last_heartbeat_at"] = heartbeat_at
            changed = True
        child_pid = payload.get("child_pid")
        if child_pid and worker.get("child_pid") != child_pid:
            worker["child_pid"] = child_pid
            changed = True
    if status_payload:
        runner_status = str(status_payload.get("status") or "").strip()
        if runner_status and worker.get("runner_status") != runner_status:
            worker["runner_status"] = runner_status
            changed = True
        if status_payload.get("finished_at") and worker.get("runner_finished_at") != status_payload.get("finished_at"):
            worker["runner_finished_at"] = status_payload.get("finished_at")
            changed = True
        if "exit_code" in status_payload and worker.get("exit_code") != status_payload.get("exit_code"):
            worker["exit_code"] = status_payload.get("exit_code")
            changed = True
        if status_payload.get("signal") and worker.get("runner_signal") != status_payload.get("signal"):
            worker["runner_signal"] = status_payload.get("signal")
            changed = True
    return changed


def worker_runner_succeeded(worker: dict[str, Any]) -> bool:
    runner_status = str(worker.get("runner_status") or "").strip().lower()
    if runner_status not in {"completed", "success", "succeeded"}:
        return False
    try:
        exit_code = int(worker.get("exit_code", 0))
    except (TypeError, ValueError):
        return False
    return exit_code == 0 and not worker.get("runner_signal")


def worker_heartbeat_is_stale(config: dict[str, Any], worker: dict[str, Any], now: datetime | None = None) -> bool:
    settings = worker_runtime_settings(config)
    heartbeat_dt = _parse_iso_utc(str(worker.get("last_heartbeat_at") or ""))
    if heartbeat_dt is None:
        return True
    now_dt = now or datetime.now(UTC)
    stale_after = int(settings.get("heartbeat_stale_seconds", 300)) + int(settings.get("heartbeat_grace_seconds", 60))
    return (now_dt - heartbeat_dt.astimezone(UTC)).total_seconds() > max(60, stale_after)


def worker_lease_is_expired(config: dict[str, Any], worker: dict[str, Any], now: datetime | None = None) -> bool:
    lease_expires_at = _parse_iso_utc(str(worker.get("lease_expires_at") or ""))
    if lease_expires_at is None:
        return False
    now_dt = now or datetime.now(UTC)
    return now_dt > lease_expires_at.astimezone(UTC) and worker_heartbeat_is_stale(config, worker, now_dt)


_QUOTA_RETRY_AT_PATTERN = re.compile(
    r"\btry again at\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>[ap]\.?m\.?)?",
    re.IGNORECASE,
)
_QUOTA_RETRY_AT_DATE_PATTERN = re.compile(
    r"\btry again at\s+"
    r"(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
    r"sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+"
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:,\s*|\s+)"
    r"(?P<year>\d{4})\s+"
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>[ap]\.?m\.?)?",
    re.IGNORECASE,
)
_QUOTA_RESETS_AT_PATTERN = re.compile(
    r"\bresets\s+(?:at\s+)?(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>[ap]\.?m\.?)?",
    re.IGNORECASE,
)
_MONTH_NAME_TO_NUMBER = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def parse_quota_retry_hint(reason: str | None, *, now: datetime | None = None) -> datetime | None:
    """Return the next wall-clock time at which a quota error says it will reset.

    Both Codex ("try again at 7:00 PM") and Claude ("resets 1pm (Asia/Taipei)")
    emit reset times. Bare times are interpreted in LOCAL_TZ, while explicit UTC
    hints are interpreted in UTC. Returns a UTC-aware datetime, or None if no
    hint is found.
    """
    if not reason:
        return None
    hint_tz = UTC if re.search(r"\(\s*UTC\s*\)|\bUTC\b", reason, re.IGNORECASE) else LOCAL_TZ
    date_match = _QUOTA_RETRY_AT_DATE_PATTERN.search(reason)
    if date_match:
        month = _MONTH_NAME_TO_NUMBER.get(date_match.group("month").lower())
        if not month:
            return None
        hour = int(date_match.group("hour"))
        minute = int(date_match.group("minute") or 0)
        meridiem = (date_match.group("meridiem") or "").replace(".", "").lower()
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        if not (0 <= hour < 24 and 0 <= minute < 60):
            return None
        try:
            return datetime(
                int(date_match.group("year")),
                month,
                int(date_match.group("day")),
                hour,
                minute,
                tzinfo=hint_tz,
            ).astimezone(UTC)
        except ValueError:
            return None

    match = _QUOTA_RETRY_AT_PATTERN.search(reason) or _QUOTA_RESETS_AT_PATTERN.search(reason)
    if not match:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    meridiem = (match.group("meridiem") or "").replace(".", "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    base = (now.astimezone(hint_tz) if now else datetime.now(hint_tz))
    candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= base:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


def provider_guardrail_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = dict(config.get("provider_guardrails", {}) or {})
    settings.setdefault("pause_on_capacity_failure", True)
    settings.setdefault("pause_on_auth_failure", True)
    settings.setdefault("capacity_pause_seconds", 900)
    settings.setdefault("auth_pause_seconds", int(settings.get("capacity_pause_seconds", 900)))
    settings.setdefault("provider_config_pause_seconds", int(settings.get("auth_pause_seconds", 900)))
    settings.setdefault("provider_unavailable_pause_seconds", int(settings.get("auth_pause_seconds", 900)))
    settings.setdefault("quota_terminal_pause_seconds", int(settings.get("capacity_pause_seconds", 900)))
    settings.setdefault("generic_exit_reassign_after", int(worker_reassignment_settings(config).get("after_attempts", 2)))
    return settings


def _provider_guardrail_bucket(state: dict[str, Any]) -> dict[str, Any]:
    bucket = state.setdefault("provider_guardrails", {})
    bucket.setdefault("dispatch_pauses", {})
    bucket.setdefault("task_failure_streaks", {})
    return bucket


def _dispatch_pause_bucket(state: dict[str, Any]) -> dict[str, Any]:
    return _provider_guardrail_bucket(state).setdefault("dispatch_pauses", {})


def _task_failure_streak_bucket(state: dict[str, Any]) -> dict[str, Any]:
    return _provider_guardrail_bucket(state).setdefault("task_failure_streaks", {})


def mark_account_pool_cooldown(
    config: dict[str, Any],
    state: dict[str, Any],
    worker: dict[str, Any] | None,
    reason: str,
    *,
    failure_kind: str,
    blocked_until: datetime,
) -> bool:
    """Fence one real account and remember when its authenticated canary may run.

    A quota result from any alias invalidates every slot of that account.  The
    durable state is consulted before every dispatch, including after a
    supervisor restart.
    """
    execution_id = ""
    if isinstance(worker, dict):
        execution_id = str(worker.get("logical_agent_id") or worker.get("agent_id") or worker.get("provider") or "")
    pool_id, pool = account_pool_settings(config, execution_id)
    try:
        configured_limit = int(pool.get("max_concurrent", 1) or 0)
    except (TypeError, ValueError):
        configured_limit = quota_group_concurrency_limit(config, execution_id) or 0
    if not pool_id or pool.get("enabled") is False or configured_limit == 0:
        return False
    bucket = _account_pool_runtime_bucket(state)
    previous = bucket.get(pool_id) if isinstance(bucket.get(pool_id), dict) else {}
    prior_until = _parse_iso_utc(str(previous.get("next_probe_at") or ""))
    chosen_until = max(blocked_until, prior_until) if prior_until is not None else blocked_until
    worker_run_id = str((worker or {}).get("run_id") or "")
    same_failure = (
        str(previous.get("state") or "") == "cooldown"
        and str(previous.get("last_worker_run_id") or "") == worker_run_id
        and str(previous.get("next_probe_at") or "") == chosen_until.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    entry = dict(previous)
    entry.update(
        {
            "state": "cooldown",
            "effective_concurrency": 0,
            "reason": summarize_failure_reason(reason, pool_id).get("summary") or failure_kind,
            "failure_kind": failure_kind,
            "last_failure_at": utc_now(),
            "next_probe_at": chosen_until.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "last_worker_run_id": worker_run_id or None,
        }
    )
    if not same_failure:
        entry["generation"] = int(previous.get("generation", 0) or 0) + 1
    bucket[pool_id] = entry
    if not same_failure:
        write_activity_log(
            config,
            {
                "type": "account_pool_cooldown",
                "account_pool": pool_id,
                "task_id": (worker or {}).get("task_id"),
                "worker_run_id": worker_run_id or None,
                "next_probe_at": entry["next_probe_at"],
                "generation": entry["generation"],
                "message": f"Account pool {pool_id} fenced after {failure_kind}; authenticated canary eligible at {entry['next_probe_at']}.",
            },
        )
    return not same_failure


def record_account_pool_canary_success(config: dict[str, Any], state: dict[str, Any], worker: dict[str, Any]) -> bool:
    pool_id, pool = account_pool_settings(
        config,
        str(worker.get("logical_agent_id") or worker.get("agent_id") or worker.get("provider") or ""),
    )
    entry = _account_pool_runtime_bucket(state).get(pool_id)
    if not pool_id or not isinstance(entry, dict) or str(entry.get("state") or "") != "recovering":
        return False
    try:
        configured = max(0, int(pool.get("max_concurrent")))
    except (TypeError, ValueError):
        configured = quota_group_concurrency_limit(config, str(worker.get("logical_agent_id") or worker.get("agent_id") or ""))
    entry.update(
        {
            "state": "healthy",
            "effective_concurrency": configured,
            "last_recovered_at": utc_now(),
            "last_canary_run_id": worker.get("run_id"),
            "reason": None,
        }
    )
    write_activity_log(
        config,
        {
            "type": "account_pool_recovered",
            "account_pool": pool_id,
            "task_id": worker.get("task_id"),
            "worker_run_id": worker.get("run_id"),
            "message": f"Account pool {pool_id} canary completed; restored configured concurrency.",
        },
    )
    return True


def provider_auth_identity_hash(config: dict[str, Any], provider: str | None) -> str | None:
    """Return a non-secret stable identity for the account behind a provider.

    A quota pause belongs to the account that produced it.  If an operator
    switches the local Codex profile, retaining the old account's multi-hour
    pause strands every alias in the shared quota group.
    """
    provider_id = normalize_agent_id(provider or "")
    group_id = provider_dispatch_group_id(config, provider_id) or provider_id
    if group_id != "codex" and provider_id != "codex":
        return None
    provider_cfg = provider_config_for(config, provider_id) or provider_config_for(config, "codex")
    configured_home = str(provider_cfg.get("codex_home") or "").strip()
    codex_home = Path(configured_home).expanduser() if configured_home else Path.home() / ".codex"
    auth = load_json(codex_home / "auth.json", default={})
    if not isinstance(auth, dict):
        return None
    tokens = auth.get("tokens") or {}
    account_id = str(tokens.get("account_id") or auth.get("account_id") or "").strip()
    auth_mode = str(auth.get("auth_mode") or "").strip()
    if not account_id:
        return None
    return hashlib.sha256(f"{auth_mode}:{account_id}".encode()).hexdigest()


def _failure_streak_key(task_id: str, provider: str) -> str:
    return f"{task_id}:{provider}"


def current_provider_dispatch_pause(
    state: dict[str, Any],
    provider: str | None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    provider_id = normalize_agent_id(provider or "")
    if not provider_id:
        return None
    bucket = _dispatch_pause_bucket(state)
    group_id = provider_dispatch_group_id(config, provider) if config is not None else provider_id
    for pause_id in dict.fromkeys([group_id, provider_id]):
        entry = bucket.get(pause_id)
        if not isinstance(entry, dict):
            continue
        blocked_until = _parse_iso_utc(str(entry.get("blocked_until") or ""))
        now = datetime.now(UTC)
        if blocked_until is not None and blocked_until <= now:
            bucket.pop(pause_id, None)
            continue
        return entry
    return None


def provider_dispatch_paused(config: dict[str, Any], state: dict[str, Any], provider: str | None) -> bool:
    return current_provider_dispatch_pause(state, provider, config) is not None


def agent_dispatch_paused(config: dict[str, Any], state: dict[str, Any], agent_id: str | None) -> bool:
    if not agent_id:
        return False
    if agent_dispatch_disabled(config, agent_id):
        return True
    if current_provider_dispatch_pause(state, agent_id, config) is not None:
        return True
    agent = agent_config_for(config, agent_id)
    provider_id = str(agent.get("provider") or agent.get("id") or agent_id)
    return provider_dispatch_paused(config, state, provider_id)


def is_terminal_quota_failure_kind(kind: str | None) -> bool:
    return str(kind or "").strip().lower() == "quota_terminal"


def is_retryable_capacity_failure_kind(kind: str | None) -> bool:
    return str(kind or "").strip().lower() in {"capacity", "capacity_retryable"}


def is_auth_failure_kind(kind: str | None) -> bool:
    return str(kind or "").strip().lower() == "auth"


def is_provider_config_failure_kind(kind: str | None) -> bool:
    return str(kind or "").strip().lower() == "provider_config"


def is_provider_unavailable_failure_kind(kind: str | None) -> bool:
    return str(kind or "").strip().lower() == "provider_unavailable"


def should_pause_dispatch_for_failure_kind(kind: str | None) -> bool:
    return (
        is_terminal_quota_failure_kind(kind)
        or is_retryable_capacity_failure_kind(kind)
        or is_auth_failure_kind(kind)
        or is_provider_config_failure_kind(kind)
        or is_provider_unavailable_failure_kind(kind)
    )


_TRANSIENT_INFRA_REASON_MARKERS = (
    "timeout waiting for response",
    "error: timeout",
    "request timed out",
    "read timed out",
    "deadline exceeded",
    "connection reset",
    "connection aborted",
    "socket hang up",
)


def is_transient_infra_reason(reason: str | None) -> bool:
    if not reason:
        return False
    low = str(reason).lower()
    return any(marker in low for marker in _TRANSIENT_INFRA_REASON_MARKERS)


def _lookup_worker_record(state: dict[str, Any], worker_run_id: str | None) -> dict[str, Any] | None:
    run_id = str(worker_run_id or "").strip()
    if not run_id:
        return None
    worker = (state.get("workers") or {}).get(run_id)
    return worker if isinstance(worker, dict) else None


def mark_provider_dispatch_paused(
    config: dict[str, Any],
    state: dict[str, Any],
    provider: str | None,
    reason: str,
    *,
    task_id: str | None = None,
    worker_run_id: str | None = None,
    failure_kind: str | None = None,
    pause_kind: str | None = None,
    raw_ref: str | None = None,
    worker: dict[str, Any] | None = None,
) -> bool:
    settings = provider_guardrail_settings(config)
    provider_id = normalize_agent_id(provider or "")
    if not provider_id:
        return False
    pause_provider_id = provider_dispatch_group_id(config, provider) or provider_id
    now = datetime.now(UTC)
    effective_pause_kind = str(pause_kind or failure_kind or "").strip().lower()
    if effective_pause_kind in {"auth", "provider_config", "provider_unavailable"}:
        if not settings.get("pause_on_auth_failure", True):
            return False
        # A missing CLI is an installation fault, not a capacity one, so it must
        # never feed model rotation -- there is no other pool to rotate onto. The
        # pause is deliberately finite: reinstalling the binary lets the lane
        # recover on its own, and if nobody does, it re-pauses and says so again
        # instead of failing silently forever.
        pause_seconds_key = {
            "provider_config": "provider_config_pause_seconds",
            "provider_unavailable": "provider_unavailable_pause_seconds",
        }.get(effective_pause_kind, "auth_pause_seconds")
    else:
        if not settings.get("pause_on_capacity_failure", True):
            return False
        pause_seconds_key = "quota_terminal_pause_seconds" if effective_pause_kind == "quota_terminal" else "capacity_pause_seconds"
    pause_seconds = max(60, int(settings.get(pause_seconds_key, 900)))
    processed_rotations = state.setdefault("provider_guardrails", {}).setdefault(
        "processed_model_rotation_failures", {}
    )
    rotation_run_id = str(worker_run_id or (worker or {}).get("run_id") or "").strip()
    if (
        rotation_run_id
        and model_rotation.rotation_enabled(config, provider_id)
        and rotation_run_id in processed_rotations
    ):
        return False
    # Antigravity model rotation: on a capacity/quota failure, if this provider
    # can rotate models (Gemini <-> Claude/GPT), record the exhausted pool and
    # keep dispatching on the other pool instead of hard-pausing. Only fall
    # through to a real pause when BOTH pools are exhausted.
    # `provider_unavailable` belongs with auth/provider_config, not with the
    # capacity kinds: rotation answers "this model pool is exhausted" by moving
    # to the other pool, but a missing binary means no pool is reachable at all.
    # Letting it rotate would keep dispatching into the exact sub-second failure
    # loop this kind exists to stop -- and on the rotation-enabled antigravity
    # providers that is most of the fleet.
    if effective_pause_kind not in {"auth", "provider_config", "provider_unavailable"} and model_rotation.rotation_enabled(config, provider_id):
        rotate_cooldown = min(int(pause_seconds), ROTATION_PROBE_COOLDOWN_SECONDS)
        # A real agy quota banner carries an authoritative reset countdown.
        # Persist it across task completion/review/reopen so another alias on
        # the same account does not probe Gemini prematurely.
        if effective_pause_kind == "quota_terminal":
            reset_seconds = model_rotation.parse_reset_seconds(reason)
            if reset_seconds is not None:
                rotate_cooldown = max(rotate_cooldown, reset_seconds)
        # Cool the pool this worker was DISPATCHED on, not whatever pool is
        # active now. Two concurrent Gemini workers failing on quota must cool
        # Gemini twice; without the dispatch-time binding the second one would
        # be attributed to Claude and falsely hard-pause the provider.
        dispatched_pool = model_rotation.worker_dispatched_pool(
            worker if isinstance(worker, dict) else _lookup_worker_record(state, worker_run_id)
        )
        rotate = model_rotation.record_exhaustion(
            config,
            provider_id,
            rotate_cooldown,
            reason=reason,
            pool=dispatched_pool,
        )
        if rotation_run_id:
            processed_rotations[rotation_run_id] = {
                "provider": provider_id,
                "task_id": task_id,
                "pool": rotate.get("exhausted_pool"),
                "processed_at": utc_now(),
            }
        write_activity_log(
            config,
            {
                "type": "antigravity_model_rotated",
                "provider": provider_id,
                "exhausted_pool": rotate.get("exhausted_pool"),
                "dispatched_pool": dispatched_pool,
                "pool_source": rotate.get("pool_source"),
                "next_pool": rotate.get("next_pool"),
                "both_exhausted": rotate.get("both_exhausted"),
                "message": rotate.get("message"),
            },
        )
        if not rotate.get("both_exhausted"):
            return False
    blocked_until = (now + timedelta(seconds=pause_seconds)).replace(microsecond=0)
    hinted_blocked_until: str | None = None
    hint_capped = False
    if effective_pause_kind == "quota_terminal":
        hinted = parse_quota_retry_hint(reason, now=now)
        if hinted is not None and hinted > blocked_until:
            hinted = hinted.replace(microsecond=0)
            hinted_blocked_until = hinted.isoformat().replace("+00:00", "Z")
            hint_max_seconds = int(settings.get("quota_terminal_hint_max_seconds", 0) or 0)
            if hint_max_seconds > 0:
                hint_cap = (now + timedelta(seconds=hint_max_seconds)).replace(microsecond=0)
                if hinted > hint_cap:
                    blocked_until = hint_cap
                    hint_capped = True
                else:
                    blocked_until = hinted
            else:
                blocked_until = hinted
    blocked_until_iso = blocked_until.isoformat().replace("+00:00", "Z")
    actual_pause_seconds = max(1, int((blocked_until - now).total_seconds()))
    bucket = _dispatch_pause_bucket(state)
    previous = bucket.get(pause_provider_id)
    summary = summarize_failure_reason(reason, pause_provider_id)
    changed = (
        not isinstance(previous, dict)
        or str(previous.get("blocked_until") or "") != blocked_until_iso
        or str(previous.get("summary") or "") != summary.get("summary")
        or str(previous.get("raw_ref") or "") != str(raw_ref or "")
    )
    bucket[pause_provider_id] = {
        "provider": pause_provider_id,
        "trigger_provider": provider_id,
        "paused_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "blocked_until": blocked_until_iso,
        "reason": summary.get("summary"),
        "summary": summary.get("summary"),
        "detail": summary.get("detail"),
        "failure_kind": failure_kind or summary.get("kind"),
        "pause_kind": effective_pause_kind or failure_kind or summary.get("kind"),
        "reset_after_seconds": actual_pause_seconds,
        "raw_ref": raw_ref,
        "task_id": task_id,
        "worker_run_id": worker_run_id,
    }
    auth_identity_hash = provider_auth_identity_hash(config, provider_id)
    if auth_identity_hash:
        bucket[pause_provider_id]["auth_identity_hash"] = auth_identity_hash
    if hinted_blocked_until:
        bucket[pause_provider_id]["hint_blocked_until"] = hinted_blocked_until
        bucket[pause_provider_id]["hint_capped"] = hint_capped
    if changed:
        if effective_pause_kind == "quota_terminal":
            pause_description = "terminal quota failure"
        elif effective_pause_kind == "auth":
            pause_description = "authentication failure"
        else:
            pause_description = "capacity failure"
        write_activity_log(
            config,
            {
                "type": "provider_dispatch_paused",
                "provider": pause_provider_id,
                "trigger_provider": provider_id,
                "task_id": task_id,
                "worker_run_id": worker_run_id,
                "message": (
                    f"Paused new dispatches for {pause_provider_id} until {blocked_until_iso} after {pause_description}: "
                    f"{summary.get('summary')}"
                ),
                "raw_ref": raw_ref,
            },
        )
    if effective_pause_kind in {"quota_terminal", "auth", "provider_config", "provider_unavailable"}:
        mark_account_pool_cooldown(
            config,
            state,
            worker if isinstance(worker, dict) else _lookup_worker_record(state, worker_run_id),
            reason,
            failure_kind=effective_pause_kind,
            blocked_until=blocked_until,
        )
    return changed


def antigravity_pool_fallback_available(
    config: dict[str, Any],
    provider: str | None,
) -> bool:
    """Keep the logical owner while another Antigravity model pool can run."""
    provider_id = normalize_agent_id(provider or "")
    return model_rotation.fallback_pool_available(config, provider_id)


def clear_provider_dispatch_pause(config: dict[str, Any], state: dict[str, Any], provider: str | None) -> bool:
    provider_id = normalize_agent_id(provider or "")
    if not provider_id:
        return False
    pause_provider_id = provider_dispatch_group_id(config, provider_id) or provider_id
    bucket = _dispatch_pause_bucket(state)
    removed: list[tuple[str, dict[str, Any]]] = []
    for pause_id in dict.fromkeys([pause_provider_id, provider_id]):
        entry = bucket.pop(pause_id, None)
        if isinstance(entry, dict):
            removed.append((pause_id, entry))
    for pause_id, entry in removed:
        write_activity_log(
            config,
            {
                "type": "provider_dispatch_resumed",
                "provider": pause_id,
                "task_id": entry.get("task_id"),
                "worker_run_id": entry.get("worker_run_id"),
                "message": f"Manually cleared dispatch pause for {pause_id}; dispatch is enabled again.",
                "raw_ref": entry.get("raw_ref"),
                "cleared_pause": entry,
            },
        )
    return bool(removed)


def expire_provider_dispatch_pauses(config: dict[str, Any], state: dict[str, Any]) -> bool:
    bucket = _dispatch_pause_bucket(state)
    if not bucket:
        return False
    now = datetime.now(UTC)
    expired: list[tuple[str, dict[str, Any], str]] = []
    for provider_id, entry in list(bucket.items()):
        if not isinstance(entry, dict):
            continue
        recorded_identity = str(entry.get("auth_identity_hash") or "")
        current_identity = provider_auth_identity_hash(
            config,
            str(entry.get("trigger_provider") or provider_id),
        )
        if recorded_identity and current_identity and recorded_identity != current_identity:
            expired.append((provider_id, dict(entry), "provider account identity changed"))
            bucket.pop(provider_id, None)
            continue
        blocked_until = _parse_iso_utc(str(entry.get("blocked_until") or ""))
        if blocked_until is None or blocked_until > now:
            continue
        expired.append((provider_id, dict(entry), f"pause expired at {entry.get('blocked_until')}"))
        bucket.pop(provider_id, None)

    for provider_id, entry, resume_reason in expired:
        write_activity_log(
            config,
            {
                "type": "provider_dispatch_resumed",
                "provider": provider_id,
                "task_id": entry.get("task_id"),
                "worker_run_id": entry.get("worker_run_id"),
                "message": f"Dispatch pause for {provider_id} cleared because {resume_reason}; dispatch is enabled again.",
                "raw_ref": entry.get("raw_ref"),
            },
        )
    return bool(expired)


def record_task_failure_streak(
    state: dict[str, Any],
    worker: dict[str, Any],
    reason: str,
    *,
    failure_kind: str | None = None,
) -> int:
    task_id = str(worker.get("task_id") or "").strip()
    provider_id = normalize_agent_id(str(worker.get("provider") or worker.get("agent_id") or ""))
    if not task_id or not provider_id:
        return 0
    bucket = _task_failure_streak_bucket(state)
    key = _failure_streak_key(task_id, provider_id)
    # Environmental failures (quota/capacity/auth/provider-config) are provider-level
    # outages, not evidence the agent can't do THIS task. Counting them toward the
    # per-task failure-loop streak would permanently lock a task out of dispatch
    # after a transient quota/capacity crash (the whole provider is already paused
    # separately). Transient infra timeouts (reason text) are the same class: the
    # transport failed, not the task. Record telemetry but do NOT increment the streak.
    if should_pause_dispatch_for_failure_kind(failure_kind) or is_transient_infra_reason(reason):
        existing = dict(bucket.get(key) or {})
        existing.update(
            {
                "task_id": task_id,
                "provider": provider_id,
                "last_reason": reason,
                "last_failure_at": utc_now(),
                "last_failure_kind": failure_kind or str(existing.get("last_failure_kind") or ""),
                "last_environmental_failure_at": utc_now(),
            }
        )
        if existing.get("count"):
            bucket[key] = existing
        return int(existing.get("count", 0))
    record = dict(bucket.get(key) or {})
    count = int(record.get("count", 0)) + 1
    record.update(
        {
            "task_id": task_id,
            "provider": provider_id,
            "count": count,
            "last_reason": reason,
            "last_failure_at": utc_now(),
            "last_failure_kind": failure_kind or str(record.get("last_failure_kind") or ""),
        }
    )
    bucket[key] = record
    return count


def clear_task_failure_streak(
    state: dict[str, Any],
    *,
    task_id: str | None = None,
    provider: str | None = None,
    worker: dict[str, Any] | None = None,
) -> None:
    if worker is not None:
        task_id = str(worker.get("task_id") or task_id or "")
        provider = str(worker.get("provider") or worker.get("agent_id") or provider or "")
    task_id = str(task_id or "").strip()
    provider_id = normalize_agent_id(provider or "")
    if not task_id or not provider_id:
        return
    _task_failure_streak_bucket(state).pop(_failure_streak_key(task_id, provider_id), None)


def clear_task_failure_streaks_for_task(state: dict[str, Any], task_id: str | None) -> None:
    task_id = str(task_id or "").strip()
    if not task_id:
        return
    bucket = _task_failure_streak_bucket(state)
    for key in [item for item in bucket if item.startswith(f"{task_id}:")]:
        bucket.pop(key, None)


def resolve_task_progress_head(task_id: str | None) -> str | None:
    """Resolve the task branch HEAD without making dispatch depend on git availability."""
    task_id = str(task_id or "").strip()
    if not task_id:
        return None
    try:
        head = runtime_ai_status.resolve_task_sha(task_id)
    except Exception:
        return None
    return str(head).strip() if head else None


def task_progress_snapshot(task: dict[str, Any] | None) -> dict[str, Any]:
    """Return durable task state; timestamps alone are not meaningful progress."""
    task = task if isinstance(task, dict) else {}
    head = (
        str(task.get("head") or "").strip() or None
        if "head" in task
        else resolve_task_progress_head(str(task.get("id") or ""))
    )
    return {
        "id": str(task.get("id") or "").strip(),
        "status": str(task.get("status") or "").strip().lower(),
        "owner": normalize_agent_id(str(task.get("owner") or "")),
        "reviewer": normalize_agent_id(str(task.get("reviewer") or "")),
        "next": " ".join(str(task.get("next") or "").split()),
        "head": head,
    }


def task_progress_fingerprint(task: dict[str, Any] | None) -> str:
    return json.dumps(task_progress_snapshot(task), sort_keys=True, ensure_ascii=True)


def worker_dispatch_task_snapshot(worker: dict[str, Any]) -> dict[str, Any]:
    request = worker.get("request_snapshot")
    if not isinstance(request, dict):
        return {}
    metadata = request.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    task = metadata.get("task")
    return dict(task) if isinstance(task, dict) else {}


def worker_is_review_dispatch(worker: dict[str, Any]) -> bool:
    request = worker.get("request_snapshot")
    reason = str(request.get("reason") or "") if isinstance(request, dict) else ""
    normalized = reason.strip().lower()
    return normalized == REASON_REVIEW_READY or normalized == "status:review"


READY_WITHOUT_HANDOFF_PATTERNS = (
    re.compile(r"\bready\s+(?:for|to)\s+(?:independent\s+)?(?:review|re-review)\b", re.IGNORECASE),
    re.compile(r"\bawaiting\s+(?:independent\s+)?(?:review|re-review)\b", re.IGNORECASE),
    re.compile(r"\bwaiting\s+for\s+(?:independent\s+)?(?:review|re-review)\b", re.IGNORECASE),
    re.compile(r"\bpending\s+(?:independent\s+)?(?:review|re-review)\b", re.IGNORECASE),
    re.compile(r"(?:等待|待)(?:獨立)?(?:審查|審核|複核|review)"),
    re.compile(r"(?:已可|可以|準備)(?:送|進入)?(?:獨立)?(?:審查|審核|複核|review)"),
)


def task_claims_ready_without_handoff(task: dict[str, Any]) -> bool:
    if str(task.get("status") or "").strip().lower() == "review":
        return False
    next_step = str(task.get("next") or "").strip()
    return bool(next_step and any(pattern.search(next_step) for pattern in READY_WITHOUT_HANDOFF_PATTERNS))


def successful_worker_exit_outcome(
    worker: dict[str, Any],
    current_task: dict[str, Any] | None,
    *,
    terminal_statuses: set[str],
) -> str:
    """Classify a zero-exit worker by its durable task-board postcondition."""
    task = current_task if isinstance(current_task, dict) else {}
    status = str(task.get("status") or "").strip().lower()
    terminal_statuses = {str(value).lower() for value in terminal_statuses}
    if status in terminal_statuses:
        return "lifecycle_complete"
    if worker_is_review_dispatch(worker):
        if status in {"in_progress", "todo", "blocked"}:
            return "review_decided"
        return "no_progress"
    if status == "review":
        return "lifecycle_complete"
    if task_claims_ready_without_handoff(task):
        return "no_progress"
    dispatched = worker_dispatch_task_snapshot(worker)
    if not dispatched:
        return "no_progress"
    if task_progress_fingerprint(dispatched) != task_progress_fingerprint(task):
        return "incremental_progress"
    return "no_progress"


def worker_retry_settings(config: dict[str, Any], provider: str | None) -> dict[str, Any]:
    retry = dict(config.get("worker_retry", {}) or {})
    if provider:
        retry.update(config.get("providers", {}).get(provider, {}).get("retry", {}) or {})
    retry.setdefault("enabled", True)
    retry.setdefault("max_attempts", 5)
    retry.setdefault("backoff_schedule_seconds", [5, 15, 30, 60, 120])
    retry.setdefault("jitter_seconds", 3)
    retry.setdefault(
        "transient_error_patterns",
        [
            "429",
            "resource_exhausted",
            "rate limit",
            "rate limited",
            "timed out",
            "etimedout",
            "econnreset",
            "temporarily unavailable",
            "try again later",
            "server overloaded",
            "deadline exceeded",
        ],
    )
    retry.setdefault("fallback_mode", "file_inbox")
    return retry


def worker_reassignment_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = dict(config.get("worker_reassignment", {}) or {})
    settings.setdefault("enabled", True)
    settings.setdefault("after_attempts", 2)
    settings.setdefault("reassign_on_terminal_failure", True)
    default_eligible_statuses: list[str] = []
    ready_settings = ready_dispatch_settings(config)
    for key in ("owned_statuses", "review_statuses", "finalize_statuses"):
        for value in ready_settings.get(key, []) or []:
            normalized = str(value).strip().lower()
            if normalized and normalized not in default_eligible_statuses:
                default_eligible_statuses.append(normalized)
    settings.setdefault("eligible_statuses", default_eligible_statuses or ["todo", "in_progress", "review", "review_approved"])
    default_fallbacks = {
        "Claude": ["Claude2", "Claude3", "Codex", "Codex2", "Codex6", "Antigravity", "Antigravity2", "Antigravity3"],
        "Claude2": ["Claude", "Claude3", "Codex", "Codex2", "Codex6", "Antigravity", "Antigravity2", "Antigravity3"],
        "Claude3": ["Claude2", "Claude", "Codex", "Codex2", "Codex6", "Antigravity", "Antigravity2", "Antigravity3"],
        "Antigravity": ["Antigravity2", "Antigravity3", "Antigravity4", "Antigravity5", "Antigravity6", "Antigravity7", "Codex2", "Codex", "Codex6", "Claude2", "Claude"],
        "Antigravity2": ["Antigravity", "Antigravity3", "Antigravity4", "Antigravity5", "Antigravity6", "Antigravity7", "Codex2", "Codex", "Codex6", "Claude2", "Claude"],
        "Antigravity3": ["Antigravity4", "Antigravity5", "Antigravity6", "Antigravity7", "Antigravity2", "Antigravity", "Codex6", "Codex2", "Codex", "Claude2", "Claude"],
        "Antigravity4": ["Antigravity3", "Antigravity5", "Antigravity6", "Antigravity7", "Antigravity2", "Antigravity", "Codex6", "Codex2", "Codex", "Claude2", "Claude"],
        "Antigravity5": ["Antigravity6", "Antigravity7", "Antigravity4", "Antigravity3", "Antigravity2", "Antigravity", "Codex6", "Codex2", "Codex", "Claude2", "Claude"],
        "Antigravity6": ["Antigravity5", "Antigravity7", "Antigravity4", "Antigravity3", "Antigravity2", "Antigravity", "Codex6", "Codex2", "Codex", "Claude2", "Claude"],
        "Antigravity7": ["Antigravity6", "Antigravity5", "Antigravity4", "Antigravity3", "Antigravity2", "Antigravity", "Codex6", "Codex2", "Codex", "Claude2", "Claude"],
        "Codex": ["Codex2", "Codex6", "Codex3", "Codex4", "Codex5", "Codex7", "Codex8", "Codex9", "Claude", "Claude2", "Antigravity", "Antigravity2"],
        "Codex2": ["Codex", "Codex6", "Codex3", "Codex4", "Codex5", "Codex7", "Codex8", "Codex9", "Claude", "Claude2", "Antigravity", "Antigravity2"],
        "Codex3": ["Codex2", "Codex6", "Codex", "Codex4", "Codex5", "Codex7", "Codex8", "Codex9", "Claude", "Claude2", "Antigravity"],
        "Codex4": ["Codex2", "Codex6", "Codex", "Codex3", "Codex5", "Codex7", "Codex8", "Codex9", "Claude", "Claude2", "Antigravity"],
        "Codex5": ["Codex6", "Codex2", "Codex", "Codex8", "Codex9", "Claude", "Claude2", "Antigravity3", "Antigravity4"],
        "Codex6": ["Codex2", "Codex", "Codex8", "Codex9", "Claude2", "Claude", "Antigravity3", "Antigravity7"],
        "Codex7": ["Codex6", "Codex2", "Codex", "Codex8", "Codex9", "Claude", "Claude2", "Antigravity"],
        "Codex8": ["Codex9", "Codex6", "Codex2", "Codex", "Claude2", "Claude", "Antigravity3", "Antigravity7"],
        "Codex9": ["Codex8", "Codex6", "Codex2", "Codex", "Claude2", "Claude", "Antigravity3", "Antigravity7"],
        "CodexCoordinator": ["Codex6", "Codex2", "Codex", "Codex8", "Codex9", "Claude2", "Claude", "Antigravity7"],
        "Gemini": ["Gemini2", "Codex", "Codex2", "Claude", "Claude2", "Antigravity", "Antigravity2"],
        "Gemini2": ["Gemini", "Codex", "Codex2", "Claude", "Claude2", "Antigravity", "Antigravity2"],
        "Copilot": ["Codex", "Codex2", "Claude", "Claude2", "Antigravity", "Antigravity2"],
        "Grok": ["Codex", "Codex2", "Claude"],
    }
    settings.setdefault("owner_fallbacks", default_fallbacks)
    settings.setdefault("reviewer_fallbacks", default_fallbacks)
    return settings


def is_human_gate_agent(agent_name: str | None) -> bool:
    name = str(agent_name or "").strip().casefold()
    if not name:
        return False
    return name in {"human/ops", "human", "ops"} or name.startswith("human/")


def get_agent_reassignment_candidates(
    config: dict[str, Any],
    failing_agent: str,
    role: str = "owner",
    task: dict[str, Any] | None = None,
) -> list[str]:
    mapping_key = "reviewer_fallbacks" if role == "reviewer" else "owner_fallbacks"
    configured_mapping = worker_reassignment_settings(config).get(mapping_key, {})
    explicit = normalized_mapping_values(configured_mapping, failing_agent)

    candidates: list[str] = []
    seen: set[str] = set()

    for item in explicit:
        name = str(item or "").strip()
        if name and name not in seen and not is_human_gate_agent(name):
            candidates.append(name)
            seen.add(name)

    if candidates:
        return candidates

    failing_norm = str(failing_agent or "").strip()
    if failing_norm:
        seen.add(failing_norm)

    known_names = sorted(list(known_agent_display_names(config)))
    default_pool = [
        "Antigravity", "Antigravity2", "Antigravity3", "Antigravity4", "Antigravity5", "Antigravity6", "Antigravity7",
        "Codex", "Codex2", "Codex3", "Codex4", "Codex5", "Codex6", "Codex7", "Codex8", "Codex9", "CodexCoordinator",
        "Claude", "Claude2", "Claude3", "Gemini", "Gemini2", "Copilot"
    ]
    for agent_item in default_pool:
        if agent_item not in known_names:
            known_names.append(agent_item)

    family_prefix = re.sub(r"\d+$", "", failing_norm, flags=re.IGNORECASE)
    same_family: list[str] = []
    other_family: list[str] = []

    for name in known_names:
        if name in seen or is_human_gate_agent(name):
            continue
        if family_prefix and name.casefold().startswith(family_prefix.casefold()):
            same_family.append(name)
        else:
            other_family.append(name)

    for name in same_family + other_family:
        if name not in seen:
            candidates.append(name)
            seen.add(name)

    return candidates


def normalized_mapping_values(mapping: dict[str, Any], key: str) -> list[str]:
    target = (key or "").strip().casefold()
    for candidate_key, values in mapping.items():
        if str(candidate_key).strip().casefold() != target:
            continue
        return [str(value).strip() for value in list(values or []) if str(value).strip()]
    return []


def known_agent_display_names(config: dict[str, Any]) -> set[str]:
    return {
        str(agent.get("display_name") or agent.get("name") or agent_id).strip()
        for agent_id, agent in (config.get("agents", {}) or {}).items()
        if str(agent.get("display_name") or agent.get("name") or agent_id).strip()
    }


def sidecar_only_agent_names(config: dict[str, Any]) -> set[str]:
    return {
        str(agent_name).strip()
        for agent_name in ready_dispatch_settings(config).get("sidecar_only_agents", []) or []
        if str(agent_name).strip()
    }


def disabled_dispatch_agent_keys(config: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    agents = config.get("agents", {}) or {}
    for raw_value in ready_dispatch_settings(config).get("disabled_agents", []) or []:
        raw = str(raw_value or "").strip()
        if not raw:
            continue
        keys.add(raw.casefold())
        normalized = normalize_agent_id(raw)
        if normalized:
            keys.add(normalized.casefold())
        agent = agents.get(normalized) if normalized else None
        if not isinstance(agent, dict):
            continue
        display = str(agent.get("display_name") or agent.get("name") or normalized).strip()
        provider = str(agent.get("provider") or "").strip()
        if display:
            keys.add(display.casefold())
        if provider:
            keys.add(provider.casefold())
            provider_id = normalize_agent_id(provider)
            if provider_id:
                keys.add(provider_id.casefold())
    return keys


def agent_dispatch_disabled(config: dict[str, Any], agent_name: str | None) -> bool:
    name = str(agent_name or "").strip()
    if not name:
        return False
    keys = disabled_dispatch_agent_keys(config)
    if name.casefold() in keys:
        return True
    agent_id = normalize_agent_id(name)
    if agent_id and agent_id.casefold() in keys:
        return True
    agents_cfg = config.get("agents", {}) or {}
    agent = agents_cfg.get(agent_id) or agents_cfg.get(name)
    if isinstance(agent, dict):
        if agent.get("enabled") is False or agent.get("disabled") is True or str(agent.get("status") or "").lower() in {"disabled", "unavailable"}:
            return True
        display = str(agent.get("display_name") or agent.get("name") or agent_id).strip()
        provider = str(agent.get("provider") or "").strip()
        if provider:
            prov_cfg = (config.get("providers", {}) or {}).get(provider) or (config.get("providers", {}) or {}).get(normalize_agent_id(provider))
            if isinstance(prov_cfg, dict) and (prov_cfg.get("enabled") is False or prov_cfg.get("disabled") is True):
                return True
        return bool(
            (display and display.casefold() in keys)
            or (provider and provider.casefold() in keys)
            or (provider and normalize_agent_id(provider).casefold() in keys)
        )
    return False


def agent_can_take_task(config: dict[str, Any], agent_name: str | None, task: dict[str, Any] | None) -> bool:
    name = str(agent_name or "").strip()
    if not name:
        return False
    # A task may contain historical/coordinator labels that are not worker
    # identities (for example CodexCoordinator).  Treat those labels as
    # invalid for automatic execution so normalization can select a real,
    # registered fallback instead of silently considering the task eligible.
    known_names = {item.casefold() for item in known_agent_display_names(config)}
    if name.casefold() not in known_names:
        return False
    if agent_dispatch_disabled(config, name):
        return False
    if not isinstance(task, dict):
        return True
    # This is the shared eligibility predicate for owned dispatch, helper
    # claims, and quota failover. A non-dispatchable or human-gate task must
    # never become executable merely because an automated lane is idle.
    if task_is_human_gate(task) or bool(task.get("non_dispatchable")):
        return False
    if task_is_sidecar(task):
        return True
    return name not in sidecar_only_agent_names(config)


AGENT_OPEN_TASK_STATUSES = ("todo", "in_progress", "review", "review_approved", "blocked")


def agent_open_task_counts(
    config: dict[str, Any],
    status: dict[str, Any] | None = None,
    role: str = "owner",
) -> dict[str, int]:
    """Count the open tasks each agent currently has assigned in the given role ("owner" or "reviewer").

    Reassignment picked the first name off a hardcoded pool, and "Antigravity"
    is first in that pool and is always viable, so it won. Observed on
    2026-08-08: Antigravity owned 23 open tasks while Antigravity2 through
    Antigravity7 held 3, 6, 3, 4, 3 and 4 -- six idle lanes behind one queue.
    A task has at most one active worker, so concentration translates directly
    into serialised throughput even when other account-pool slots are idle.
    """

    status = status if isinstance(status, dict) else load_status(config)
    open_statuses = {s.lower() for s in AGENT_OPEN_TASK_STATUSES}
    field = "reviewer" if str(role or "").lower() == "reviewer" else "owner"
    counts: dict[str, int] = {}
    for task in status.get("tasks", []) or []:
        if not isinstance(task, dict):
            continue
        if str(task.get("status") or "").lower() not in open_statuses:
            continue
        agent = normalize_agent_id(str(task.get(field) or ""))
        if agent:
            counts[agent] = counts.get(agent, 0) + 1
    return counts


def first_viable_agent(
    config: dict[str, Any],
    preferred: list[str],
    exclude: set[str],
    *,
    state: dict[str, Any] | None = None,
    task: dict[str, Any] | None = None,
    provider_report: dict[str, Any] | None = None,
    status: dict[str, Any] | None = None,
    balance_load: bool = True,
    role: str = "owner",
    exclude_pools: set[str] | None = None,
) -> str | None:
    known = known_agent_display_names(config)
    seen: set[str] = set()
    viable: list[str] = []
    excluded_pool_ids = {normalize_agent_id(pool) for pool in (exclude_pools or set()) if normalize_agent_id(pool)}
    for candidate in preferred:
        name = str(candidate or "").strip()
        if not name or name in seen or name in exclude:
            continue
        seen.add(name)
        if is_human_gate_agent(name):
            continue
        if name in known:
            if agent_dispatch_disabled(config, name):
                continue
            normalized = normalize_agent_id(name)
            agent_cfg = (config.get("agents", {}) or {}).get(normalized) or (config.get("agents", {}) or {}).get(name)
            provider_key = str((agent_cfg or {}).get("provider") or normalized or name)
            if provider_runtime_config_block_reason(config, provider_key):
                continue
            if state is not None:
                if agent_dispatch_paused(config, state, name):
                    continue
                block_reason = agent_auto_dispatch_block_reason(config, state, name, provider_report=provider_report)
                if block_reason:
                    continue
            if agent_account_pool_id(config, name) in excluded_pool_ids:
                continue
            if task is not None and not agent_can_take_task(config, name, task):
                continue
            viable.append(name)

    if not viable:
        return None
    if len(viable) == 1 or not balance_load:
        return viable[0]

    # Every name here already passed the same viability checks, so choosing
    # among them is free. Take the least loaded and keep the caller's ordering
    # as the tie-break, which preserves the configured preference whenever the
    # load is equal.
    counts = agent_open_task_counts(config, status, role=role)
    return min(viable, key=lambda name: (counts.get(normalize_agent_id(name), 0), viable.index(name)))



def agent_auto_dispatch_block_reason(
    config: dict[str, Any],
    state: dict[str, Any],
    agent_id: str | None,
    provider_report: dict[str, Any] | None = None,
) -> str | None:
    """Return a human-readable reason when an agent must not receive auto dispatch."""
    normalized_agent = normalize_agent_id(agent_id or "")
    if not normalized_agent:
        return "missing target agent"
    if agent_dispatch_paused(config, state, normalized_agent):
        return f"dispatch is paused or disabled for {display_name_for(config, normalized_agent) or normalized_agent}"
    pool_block_reason = account_pool_dispatch_block_reason(config, normalized_agent, runtime_state=state)
    if pool_block_reason:
        return pool_block_reason
    settings = ready_dispatch_settings(config)
    active_statuses = {str(value) for value in settings.get("active_worker_statuses", [])}
    quota_limit = account_pool_effective_concurrency(config, state, normalized_agent)
    quota_group = agent_quota_group_id(config, normalized_agent)
    if quota_limit and quota_group:
        active_quota_counts = active_quota_group_counts(config, state, active_statuses)
        active_count = active_quota_counts.get(quota_group, 0)
        if active_count >= quota_limit:
            return (
                f"quota group {quota_group} already has {active_count}/{quota_limit} "
                "active worker(s)"
            )
    agent = (config.get("agents", {}) or {}).get(normalized_agent)
    provider_key = str((agent or {}).get("provider") or normalized_agent)
    config_block_reason = provider_runtime_config_block_reason(config, provider_key)
    if config_block_reason:
        return config_block_reason
    if not provider_report:
        return None

    provider_id = normalize_agent_id(
        provider_key
    )
    agent_capability = ((provider_report.get("agent_adapters") or {}).get(normalized_agent) or {})
    provider_capability = (
        ((provider_report.get("providers") or {}).get(provider_key) or {})
        or ((provider_report.get("providers") or {}).get(provider_id) or {})
    )

    if agent_capability:
        if not agent_capability.get("supported", True):
            notes = str(agent_capability.get("notes") or "").strip()
            return notes or f"{normalized_agent} adapter is not supported"
        if agent_capability.get("can_auto_deliver") is False:
            notes = str(agent_capability.get("notes") or "").strip()
            return notes or f"{normalized_agent} cannot auto-deliver in the current workspace"

    if provider_capability:
        if provider_capability.get("local_cli_worker_supported") is False:
            return f"{provider_id} local CLI worker is not ready"
        if provider_capability.get("supports_auto_approve") is False:
            return f"{provider_id} does not currently support auto-approved dispatch"
        if provider_capability.get("config_valid") is False:
            return str(provider_capability.get("config_error") or f"{provider_id} provider config is invalid")
        if provider_capability.get("auth_ready") is False:
            return f"{provider_id} authentication is not ready"

    if settings.get("worker_os_duplicate_guard", True):
        slot_ids = logical_worker_slot_ids(config, normalized_agent)
        if slot_ids:
            occupied_slots = {
                slot_id: refs
                for slot_id in slot_ids
                if (refs := active_worker_refs_for_agent_id(state, slot_id, active_statuses))
            }
            if len(occupied_slots) >= len(slot_ids):
                slot_summary = ", ".join(
                    f"{slot_id}=PID:{'/'.join(refs)}" for slot_id, refs in sorted(occupied_slots.items())
                )
                display_name = display_name_for(config, normalized_agent) or normalized_agent
                return (
                    f"{display_name} all dispatch slots already have live worker process(es) "
                    f"{slot_summary}; skipping dispatch to avoid duplicate workers"
                )
            return None

        if agent and agent_is_dispatch_slot(agent):
            slot_refs = active_worker_refs_for_agent_id(state, normalized_agent, active_statuses)
            if slot_refs:
                display_name = display_name_for(config, normalized_agent) or normalized_agent
                return (
                    f"{display_name} slot {normalized_agent} already has live worker process(es) "
                    f"PID={','.join(slot_refs)}; skipping dispatch to avoid duplicate workers"
                )
            return None

        display_name = display_name_for(config, normalized_agent) or normalized_agent
        live_pids = scan_live_worker_pids_by_agent().get(display_name, [])
        if live_pids:
            return (
                f"{display_name} already has live worker process(es) "
                f"PID={','.join(str(p) for p in sorted(set(live_pids)))}; "
                "skipping dispatch to avoid duplicate workers"
            )

    return None


def auto_dispatch_block_is_temporary_capacity(reason: str | None) -> bool:
    normalized = str(reason or "").lower()
    return any(
        marker in normalized
        for marker in (
            "quota group",
            "already has live worker",
            "all dispatch slots",
            "slot",
        )
    )


def write_status_snapshot_if_current(config: dict[str, Any], status: dict[str, Any]) -> bool:
    """Atomically reject a stale whole-status write instead of losing a newer transition.

    Supervisor probes can spend seconds in git/GitHub calls after loading the
    board. A worker or operator may complete a canonical status command during
    that interval. Both writers share the same lock, and every successful write
    advances a UUID revision, so the old Supervisor snapshot fails closed and
    the next tick starts from canonical disk state.
    """

    status_path = config_path(config, "status_file")
    lock_path = status_path.with_name(f"{status_path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    expected_revision = status.get(STATUS_WRITE_REVISION_FIELD)
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            latest = load_json(status_path, default={}) or {}
            actual_revision = latest.get(STATUS_WRITE_REVISION_FIELD)
            if actual_revision != expected_revision:
                status.clear()
                status.update(latest)
                write_activity_log(
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
            write_json(status_path, status)
            return True
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def sync_status_pipeline(config: dict[str, Any]) -> bool:
    script = config_path(config, "status_file").parent / "scripts" / "ai_status.py"
    if not script.exists():
        write_activity_log(
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
            cwd=str(config_path(config, "status_file").parent),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        write_activity_log(
            config,
            {
                "type": "task_reassignment_sync_failed",
                "message": f"Status sync timed out after {timeout_seconds:g}s.",
            },
        )
        return False
    if result.returncode == 0:
        return True
    write_activity_log(
        config,
        {
            "type": "task_reassignment_sync_failed",
            "message": f"Status sync failed after reassignment: {result.stderr.strip() or result.stdout.strip() or 'unknown error'}",
        },
    )
    return False


def commit_canonical_task_transition(config: dict[str, Any], status: dict[str, Any]) -> bool:
    """Commit a scheduler transition through one canonical write/sync path.

    Callers may decide a transition, but they cannot independently choose a
    snapshot write versus derived-artifact sync.  This is deliberately the
    only supervisor task-transition commit point: a successful mutation is
    never allowed to leave the canonical board and its derived artifacts out
    of sync, and a rejected stale snapshot is never followed by a sync of the
    wrong state.
    """
    return write_status_snapshot_if_current(config, status) and sync_status_pipeline(config)


def sync_dispatched_task_status(config: dict[str, Any], event: dict[str, Any]) -> bool:
    reason = str(event.get("reason") or "").strip()
    action = DISPATCH_STATUS_ACTIONS.get(reason)
    if action is None:
        return False
    if not config.get("paths", {}).get("status_file"):
        return False

    script = config_path(config, "status_file").parent / "scripts" / "ai_status.py"
    if not script.exists():
        write_activity_log(
            config,
            {
                "type": "task_dispatch_sync_failed",
                "task_id": event.get("task_id"),
                "message": f"Dispatch status sync script not found at {script}.",
            },
        )
        return False

    task_id = str(event.get("task_id") or "").strip()
    target_agent = str(event.get("target_display_name") or display_name_for(config, str(event.get("target_agent") or ""))).strip()
    if not task_id or not target_agent:
        return False

    command_name, eligible_statuses = action
    task = task_index_from_status(config, load_status(config)).get(task_id)
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
    env = os.environ.copy()
    env["AI_NAME"] = target_agent
    timeout_seconds = float(config.get("supervisor", {}).get("external_command_timeout_seconds", 30))
    try:
        result = subprocess.run(
            [sys.executable, str(script), command_name, task_id, message],
            cwd=str(config_path(config, "status_file").parent),
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        write_activity_log(
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
        write_activity_log(
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

    write_activity_log(
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
    """Keep task truth aligned when a worker is superseded for higher-priority work."""
    if not config.get("paths", {}).get("status_file"):
        return False

    dispatch_reason = str(worker.get("request_snapshot", {}).get("reason") or "").strip()
    task_id = str(worker.get("task_id") or "").strip()
    target_agent = display_name_for(
        config,
        worker_logical_dispatch_agent_id(config, worker) or str(worker.get("provider") or ""),
    ).strip()
    if not task_id or not target_agent:
        return False

    status = load_status(config)
    task = task_index_from_status(config, status).get(task_id)
    if not task:
        return False
    if str(task.get("owner") or "").strip() != target_agent:
        return False

    task_status = str(task.get("status") or "").lower()
    timestamp = utc_now()
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
        write_activity_log(
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
        write_activity_log(
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


def persist_task_reassignment(
    config: dict[str, Any],
    *,
    task_id: str,
    new_owner: str,
    new_reviewer: str,
    message: str,
    new_status: str | None = None,
    new_waiting_for: str | None = None,
    handoff_to: str | None = None,
    handoff_from: str | None = None,
    resolve_open_blockers: bool = False,
) -> bool:
    status = load_status(config)
    tasks = status.get("tasks", []) or []
    timestamp = utc_now()
    task = next((item for item in tasks if item.get("id") == task_id), None)
    if task is None:
        return False

    old_owner = str(task.get("owner") or "")
    old_reviewer = str(task.get("reviewer") or "")
    task["owner"] = new_owner
    task["reviewer"] = new_reviewer
    if new_status:
        task["status"] = new_status
        if str(new_status).lower() == "todo":
            task.pop("waiting_for", None)
    if new_waiting_for:
        task["waiting_for"] = new_waiting_for
    task["last_update"] = timestamp
    task["assignment_note"] = message
    # Reassignment is coordination metadata, not resolution of an external
    # gate.  Preserve the actionable blocker text until the task is explicitly
    # moved out of blocked; otherwise dashboards claim the assignment changed
    # while hiding the dataset/approval/deployment action still required.
    if str(task.get("status") or "").lower() != "blocked" or new_status:
        task["next"] = message

    if resolve_open_blockers:
        for blocker in status.get("blockers", []) or []:
            if blocker.get("task_id") != task_id or blocker.get("status") == "resolved":
                continue
            blocker["status"] = "resolved"
            blocker["resolved_at"] = timestamp
            blocker["resolution_ref"] = f"scheduler_reassignment:{task_id}"

    for handoff in status.get("handoffs", []) or []:
        if handoff.get("task_id") != task_id or handoff.get("status") == "done":
            continue
        target = str(handoff.get("to") or "")
        if target in {old_owner, old_reviewer} and target not in {new_owner, new_reviewer}:
            handoff["status"] = "done"
            handoff["resolved_at"] = timestamp

    if handoff_to:
        status.setdefault("handoffs", []).append(
            {
                "task_id": task_id,
                "from": handoff_from or old_owner or old_reviewer or new_owner,
                "to": handoff_to,
                "message": message,
                "status": "pending",
                "created_at": timestamp,
            }
        )

    return commit_canonical_task_transition(config, status)


def maybe_reassign_task_after_worker_failure(
    config: dict[str, Any],
    state_or_worker: dict[str, Any],
    worker_or_reason: dict[str, Any] | str | None = None,
    reason: str | None = None,
    *,
    terminal: bool = False,
    force: bool = False,
    failure_count: int | None = None,
    respect_threshold: bool = False,
) -> str | None:
    if isinstance(worker_or_reason, dict):
        state = state_or_worker
        worker = worker_or_reason
    else:
        state = {}
        worker = state_or_worker
        reason = str(worker_or_reason or reason or "")
    settings = worker_reassignment_settings(config)
    if not settings.get("enabled", True):
        return None

    attempt_number = failure_count if failure_count is not None else int(worker.get("retry_count", 0)) + 1
    if not force and (not terminal or respect_threshold) and attempt_number < int(settings.get("after_attempts", 2)):
        return None
    if terminal and not settings.get("reassign_on_terminal_failure", True):
        return None

    task_id = str(worker.get("task_id") or "")
    if not task_id:
        return None
    status = load_status(config)
    task = next((item for item in status.get("tasks", []) if item.get("id") == task_id), None)
    if task is None:
        return None
    if task_is_human_gate(task) or bool(task.get("non_dispatchable")):
        return None

    task_status = str(task.get("status") or "").lower()
    if task_status not in {str(value).lower() for value in settings.get("eligible_statuses", [])}:
        return None

    dispatch_settings = ready_dispatch_settings(config)
    review_statuses = {str(value).lower() for value in dispatch_settings.get("review_statuses", ["review"])}
    finalize_statuses = {str(value).lower() for value in dispatch_settings.get("finalize_statuses", ["review_approved"])}
    owned_statuses = {str(value).lower() for value in dispatch_settings.get("owned_statuses", ["in_progress", "todo"])}

    failing_agent = display_name_for(
        config,
        worker_logical_dispatch_agent_id(config, worker) or str(worker.get("provider") or ""),
    )
    if is_human_gate_agent(failing_agent):
        return None

    failure = classify_worker_failure(config, worker, reason)
    failure_label = failure.get("label", "provider failure")
    failure_summary = summarize_failure_reason(reason, failing_agent).get("summary") or failure_label
    owner = str(task.get("owner") or "")
    reviewer = str(task.get("reviewer") or "")
    failed_pool = agent_account_pool_id(config, failing_agent)
    quota_exclusions = {failed_pool} if is_terminal_quota_failure_kind(str(failure.get("kind") or "")) and failed_pool else set()

    if task_status in review_statuses and reviewer == failing_agent:
        if is_human_gate_agent(reviewer):
            return None
        candidates = get_agent_reassignment_candidates(config, failing_agent, role="reviewer", task=task)
        new_reviewer = first_viable_agent(
            config,
            candidates,
            exclude={owner, reviewer},
            state=state,
            task=task,
            role="reviewer",
            exclude_pools=quota_exclusions | {agent_account_pool_id(config, owner)},
        )
        if not new_reviewer or is_human_gate_agent(new_reviewer):
            return None
        message = (
            f"Auto-reassigned review from {reviewer} to {new_reviewer} after repeated {failing_agent} {failure_label}: {failure_summary}"
        )
        if not persist_task_reassignment(
            config,
            task_id=task_id,
            new_owner=owner,
            new_reviewer=new_reviewer,
            message=message,
            handoff_to=new_reviewer,
            handoff_from=reviewer,
        ):
            return None
        write_activity_log(
            config,
            {
                "type": "task_reassigned",
                "task_id": task_id,
                "message": message,
                "from_reviewer": reviewer,
                "to_reviewer": new_reviewer,
                "worker_run_id": worker.get("run_id"),
            },
        )
        clear_task_failure_streaks_for_task(state, task_id)
        console_log(
            f"reassigned review: task={task_id} from={reviewer} to={new_reviewer} kind={failure_label}",
            quiet=SUPERVISOR_LOG_QUIET,
        )
        return new_reviewer

    if task_status in owned_statuses | finalize_statuses and owner == failing_agent:
        if is_human_gate_agent(owner):
            return None
        candidates = get_agent_reassignment_candidates(config, failing_agent, role="owner", task=task)
        new_owner = first_viable_agent(
            config,
            candidates,
            exclude={owner, reviewer},
            state=state,
            task=task,
            role="owner",
            exclude_pools=quota_exclusions,
        )
        if not new_owner or is_human_gate_agent(new_owner):
            return None
        # Only the owner failed. A reviewer that is still viable keeps the task:
        # load balancing is for picking a replacement, not a reason to churn a
        # healthy review assignment and lose the reviewer's accumulated context.
        new_reviewer = (
            first_viable_agent(
                config,
                [reviewer],
                exclude={new_owner},
                state=state,
                task=task,
                balance_load=False,
                exclude_pools={agent_account_pool_id(config, new_owner)},
                role="reviewer",
            )
            if reviewer
            else None
        )
        if not new_reviewer:
            reviewer_candidates = get_agent_reassignment_candidates(config, failing_agent, role="reviewer", task=task)
            reviewer_candidates.extend(get_agent_reassignment_candidates(config, failing_agent, role="owner", task=task))
            new_reviewer = first_viable_agent(
                config,
                reviewer_candidates,
                exclude={new_owner},
                state=state,
                task=task,
                role="reviewer",
                exclude_pools=quota_exclusions | {agent_account_pool_id(config, new_owner)},
            )
        if not new_reviewer or is_human_gate_agent(new_reviewer):
            return None
        requeue_for_fresh_dispatch = task_status in owned_statuses and task_status not in finalize_statuses
        message = (
            f"Auto-reassigned ownership from {owner} to {new_owner} after repeated {failing_agent} {failure_label}: {failure_summary}"
        )
        if requeue_for_fresh_dispatch:
            message = f"{message}. Task returned to todo until {new_owner} starts a fresh run."
        if not persist_task_reassignment(
            config,
            task_id=task_id,
            new_owner=new_owner,
            new_reviewer=new_reviewer,
            message=message,
            new_status="todo" if requeue_for_fresh_dispatch else None,
            handoff_to=new_owner,
            handoff_from=owner,
        ):
            return None
        write_activity_log(
            config,
            {
                "type": "task_reassigned",
                "task_id": task_id,
                "message": message,
                "from_owner": owner,
                "to_owner": new_owner,
                "from_reviewer": reviewer,
                "to_reviewer": new_reviewer,
                "worker_run_id": worker.get("run_id"),
            },
        )
        clear_task_failure_streaks_for_task(state, task_id)
        console_log(
            f"reassigned owner: task={task_id} from={owner} to={new_owner} kind={failure_label}",
            quiet=SUPERVISOR_LOG_QUIET,
        )
        return new_owner

    return None


def fence_account_pool_workers(
    config: dict[str, Any],
    state: dict[str, Any],
    triggering_worker: dict[str, Any],
    reason: str,
) -> int:
    """Stop and hand off sibling runs after a shared-account quota failure.

    Physical slots are only capacity; a quota failure belongs to the account
    pool.  Leaving sibling processes alive wastes their remaining turns and
    makes each task discover the same failure independently.  We retain each
    task worktree, create its normal durable handoff, and only select a
    replacement outside the fenced pool.
    """
    triggering_run_id = str(triggering_worker.get("run_id") or "")
    triggering_identity = worker_logical_dispatch_agent_id(config, triggering_worker)
    pool_id = agent_account_pool_id(config, triggering_identity)
    if not pool_id:
        return 0
    active_statuses = {
        str(value).lower()
        for value in ready_dispatch_settings(config).get("active_worker_statuses", [])
    }
    fenced = 0
    for sibling in list((state.get("workers", {}) or {}).values()):
        if str(sibling.get("run_id") or "") == triggering_run_id:
            continue
        if str(sibling.get("status") or "").lower() not in active_statuses:
            continue
        sibling_identity = worker_logical_dispatch_agent_id(config, sibling)
        if agent_account_pool_id(config, sibling_identity) != pool_id:
            continue
        if pid_is_alive(sibling.get("pid")):
            terminate_worker_pid(sibling.get("pid"))
        reassigned_to = maybe_reassign_task_after_worker_failure(
            config,
            state,
            sibling,
            reason,
            terminal=True,
            force=True,
        )
        sibling["status"] = "reassigned" if reassigned_to else "failed"
        sibling["reassigned_to"] = reassigned_to
        sibling["last_event_at"] = utc_now()
        sibling["last_error"] = (
            f"Account pool {pool_id} fenced after a sibling quota failure. "
            f"{reason}"
        )
        finalize_queue_event_record(
            config,
            state,
            sibling,
            "completed" if reassigned_to else "failed",
            sibling["last_error"],
        )
        write_activity_log(
            config,
            {
                "type": "account_pool_worker_fenced",
                "account_pool": pool_id,
                "task_id": sibling.get("task_id"),
                "worker_run_id": sibling.get("run_id"),
                "reassigned_to": reassigned_to,
                "message": sibling["last_error"],
            },
        )
        fenced += 1
    return fenced


def is_transient_worker_failure(config: dict[str, Any], worker: dict[str, Any], reason: str | None) -> bool:
    if not reason:
        return False
    if not worker_retry_settings(config, worker.get("provider")).get("enabled", True):
        return False
    return bool(classify_worker_failure(config, worker, reason).get("transient"))


def retry_delay_seconds(config: dict[str, Any], worker: dict[str, Any]) -> float:
    retry = worker_retry_settings(config, worker.get("provider"))
    retry_count = int(worker.get("retry_count", 0))
    schedule = list(retry.get("backoff_schedule_seconds", []) or [5, 15, 30, 60, 120])
    index = min(retry_count, len(schedule) - 1)
    base_delay = float(schedule[index])
    jitter = float(retry.get("jitter_seconds", 0) or 0)
    return base_delay + (random.uniform(0, jitter) if jitter > 0 else 0)


def schedule_queue_event_retry(config: dict[str, Any], record: dict[str, Any], *, provider: str | None, reason: str) -> None:
    delay = retry_delay_seconds(
        config,
        {
            "provider": provider,
            "retry_count": int(record.get("retry_count", 0)),
        },
    )
    retry_at = datetime.fromtimestamp(datetime.now(UTC).timestamp() + delay, tz=UTC)
    record["status"] = "retry_backoff"
    record["retry_count"] = int(record.get("retry_count", 0)) + 1
    record["next_retry_at"] = retry_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    record["error"] = reason
    record["processed_at"] = utc_now()


def request_for_worker(config: dict[str, Any], worker: dict[str, Any]) -> DeliveryRequest | None:
    snapshot = worker.get("request_snapshot")
    if isinstance(snapshot, dict) and snapshot.get("message"):
        return request_from_snapshot(snapshot)
    queue_event_id = worker.get("queue_event_id")
    if not queue_event_id:
        return None
    for event in load_event_queue(config):
        if event.get("event_id") == queue_event_id:
            return build_request(config, event)
    return None


def manual_pending_inbox_can_auto_redeliver(
    config: dict[str, Any],
    state: dict[str, Any],
    provider_report: dict[str, Any],
    worker: dict[str, Any],
) -> bool:
    if worker.get("status") != "manual_pending":
        return False
    if worker.get("mode") != "file_inbox":
        return False
    if pid_is_alive(worker.get("pid")):
        return False
    request = request_for_worker(config, worker)
    if request is None:
        return False
    if current_provider_dispatch_pause(state, request.provider, config):
        return False
    agent_capability = (provider_report or {}).get("agent_adapters", {}).get(str(request.agent_id) or "", {}) or {}
    if not agent_capability.get("can_auto_deliver"):
        return False
    return str(agent_capability.get("delivery_mode") or "") != "file_inbox"


def requeue_stale_manual_pending_worker(
    config: dict[str, Any],
    state: dict[str, Any],
    worker: dict[str, Any],
    *,
    reason: str,
) -> bool:
    run_id = str(worker.get("run_id") or "").strip()
    if not run_id:
        return False
    queue_event_id = str(worker.get("queue_event_id") or "").strip()
    state.setdefault("workers", {}).pop(run_id, None)
    if queue_event_id:
        record = queue_status(state, queue_event_id)
        record["status"] = "queued"
        record.pop("processed_at", None)
        record.pop("error", None)
        record.pop("run_id", None)
    write_activity_log(
        config,
        {
            "type": "worker_requeued",
            "provider": worker.get("provider"),
            "task_id": worker.get("task_id"),
            "worker_run_id": run_id,
            "queue_event_id": queue_event_id or None,
            "message": reason,
        },
    )
    console_log(
        f"requeued stale manual_pending worker: provider={worker.get('provider')} task={worker.get('task_id')} run={run_id}",
        quiet=SUPERVISOR_LOG_QUIET,
    )
    return True


def schedule_worker_retry(config: dict[str, Any], worker: dict[str, Any], reason: str) -> None:
    delay = retry_delay_seconds(config, worker)
    retry_at = datetime.fromtimestamp(datetime.now(UTC).timestamp() + delay, tz=UTC)
    worker["status"] = "retry_backoff"
    worker["retry_count"] = int(worker.get("retry_count", 0)) + 1
    worker["next_retry_at"] = retry_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    worker["last_error"] = reason
    worker["last_event_at"] = utc_now()


def existing_file_inbox_fallback_run_id(state: dict[str, Any], queue_event_id: str | None, exclude_run_id: str | None = None) -> str | None:
    if not queue_event_id:
        return None
    fallback_statuses = {"manual_pending", "waiting_approval", "running", "retry_backoff", "fallback", "completed"}
    for candidate in state.get("workers", {}).values():
        if candidate.get("run_id") == exclude_run_id:
            continue
        if candidate.get("queue_event_id") != queue_event_id:
            continue
        if candidate.get("mode") != "file_inbox":
            continue
        if candidate.get("status") not in fallback_statuses:
            continue
        run_id = candidate.get("run_id")
        if run_id:
            return str(run_id)
    return None


def maybe_trigger_retry_or_fallback(
    config: dict[str, Any],
    state: dict[str, Any],
    provider_report: dict[str, Any],
    worker: dict[str, Any],
    reason: str,
) -> tuple[bool, bool]:
    retry = worker_retry_settings(config, worker.get("provider"))
    failure = classify_worker_failure(config, worker, reason)
    max_attempts = int(retry.get("max_attempts", 5))
    retry_count = int(worker.get("retry_count", 0))
    request = request_for_worker(config, worker)
    if request is None:
        return False, False
    reassigned_to = maybe_reassign_task_after_worker_failure(config, state, worker, reason)
    if reassigned_to:
        worker["status"] = "reassigned"
        worker["reassigned_to"] = reassigned_to
        worker["last_error"] = reason
        worker["last_event_at"] = utc_now()
        finalize_queue_event_record(config, state, worker, "completed")
        return True, True
    if retry_count < max_attempts:
        schedule_worker_retry(config, worker, reason)
        write_activity_log(
            config,
            {
                "type": "worker_retry_scheduled",
                "provider": worker.get("provider"),
                "task_id": worker.get("task_id"),
                "message": f"Transient worker failure detected ({failure.get('label')}); retry {worker.get('retry_count')} scheduled at {worker.get('next_retry_at')}: {reason}",
                "worker_run_id": worker["run_id"],
                "next_retry_at": worker.get("next_retry_at"),
            },
        )
        console_log(
            f"retry scheduled: provider={worker.get('provider')} task={worker.get('task_id')} kind={failure.get('label')} next={worker.get('next_retry_at')}",
            quiet=SUPERVISOR_LOG_QUIET,
        )
        return True, True

    if retry.get("fallback_mode") == "file_inbox":
        existing_fallback = existing_file_inbox_fallback_run_id(
            state,
            worker.get("queue_event_id"),
            exclude_run_id=worker.get("run_id"),
        )
        if existing_fallback:
            worker["status"] = "fallback"
            worker["fallback_run_id"] = existing_fallback
            worker["last_event_at"] = utc_now()
            return True, True
        if not worker.get("fallback_run_id"):
            ok, outcome, _ = start_worker_for_request(
                config,
                state,
                provider_report,
                request,
                queue_event_id=worker.get("queue_event_id"),
                attempt_count=int(worker.get("attempt_count", 0)) + 1,
                event_id_for_log=worker.get("queue_event_id"),
                parent_run_id=worker["run_id"],
                delivery_mode_override="file_inbox",
                activity_type="worker_fallback_started",
                activity_message=f"Worker fell back to file inbox after transient failures: {reason}",
            )
            if ok:
                worker["status"] = "fallback"
                worker["fallback_run_id"] = outcome
                worker["last_event_at"] = utc_now()
                return True, True
    return False, False


def retry_due_workers(
    config: dict[str, Any],
    state: dict[str, Any],
    provider_report: dict[str, Any],
    now: datetime,
) -> bool:
    changed = False
    for worker in list(state.get("workers", {}).values()):
        if worker.get("status") != "retry_backoff":
            continue
        next_retry_at = _parse_iso_utc(worker.get("next_retry_at"))
        if next_retry_at is None or next_retry_at > now:
            continue
        request = request_for_worker(config, worker)
        if request is None:
            worker["status"] = "failed"
            worker["last_event_at"] = utc_now()
            write_activity_log(
                config,
                {
                    "type": "worker_failed",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": "Retry was due, but the original request could not be reconstructed.",
                    "worker_run_id": worker["run_id"],
                },
            )
            changed = True
            continue
        ok, outcome, _ = start_worker_for_request(
            config,
            state,
            provider_report,
            request,
            queue_event_id=worker.get("queue_event_id"),
            attempt_count=int(worker.get("attempt_count", 0)) + 1,
            event_id_for_log=worker.get("queue_event_id"),
            parent_run_id=worker["run_id"],
            activity_type="worker_retried",
            activity_message=f"Worker retry launched after backoff from {worker['run_id']}",
        )
        if ok:
            worker["status"] = "retried"
            worker["superseded_by_run_id"] = outcome
            worker["last_event_at"] = utc_now()
        else:
            worker["status"] = "failed"
            worker["last_event_at"] = utc_now()
            worker["last_error"] = outcome
        changed = True
    return changed


def _claude_resume_allowed_tools(approval: dict[str, Any] | None) -> list[str]:
    if not approval:
        return []
    candidates: list[str] = []
    for value in (
        approval.get("resume_override_rule"),
        approval.get("suggested_rule"),
    ):
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    if candidates:
        return candidates
    tool_name = approval.get("tool_name")
    if isinstance(tool_name, str) and tool_name.strip():
        return [tool_name.strip()]
    return []


def _provider_uses_claude_cli(config: dict[str, Any], provider_id: str | None) -> bool:
    normalized = normalize_agent_id(provider_id or "")
    if not normalized:
        return False
    provider = (config.get("providers", {}) or {}).get(normalized, {}) or {}
    delivery_mode = str(provider.get("delivery_mode") or "").strip()
    if delivery_mode:
        return delivery_mode == "claude_cli"
    return normalized.startswith("claude")


def _claude_runtime_env(config: dict[str, Any], provider_id: str | None) -> dict[str, str]:
    provider = (config.get("providers", {}) or {}).get(normalize_agent_id(provider_id or ""), {}) or {}
    runtime = provider.get("runtime", {}) or {}
    base_env = dict(os.environ)
    env = dict(base_env)
    home = str(runtime.get("home") or "").strip()
    if home:
        env["HOME"] = os.path.expanduser(home)
    extra_env = runtime.get("env", {}) or {}
    for key, value in extra_env.items():
        if value is None:
            continue
        env[str(key)] = os.path.expanduser(str(value))
    preserve_github_cli_auth_env(env, base_env)
    return env


def worker_supports_approval_resume(config: dict[str, Any], worker: dict[str, Any]) -> bool:
    return bool(
        _provider_uses_claude_cli(config, worker.get("provider"))
        and (worker.get("session_id") or worker.get("resume_token"))
    )


DEFERRED_TOOL_RISK_CLASS = "claude_deferred_tool"


def _deferred_tool_use_receipt(worker: dict[str, Any]) -> dict[str, Any] | None:
    """Return the worker's `stop_reason=tool_deferred` payload when it is usable."""
    payload = worker.get("deferred_tool_use")
    if not isinstance(payload, dict):
        return None
    tool_name = str(payload.get("name") or "").strip()
    if not tool_name:
        return None
    tool_input = payload.get("input")
    return {
        "tool_use_id": str(payload.get("id") or "").strip(),
        "tool_name": tool_name,
        "tool_input": tool_input if isinstance(tool_input, dict) else {},
    }


def _deferred_tool_suggested_rule(tool_name: str, tool_input: dict[str, Any]) -> str | None:
    if tool_name == "Bash":
        shell_command = tool_input.get("command") or tool_input.get("cmd") or tool_input.get("raw_command")
        if shell_command:
            return f"Bash({shell_command})"
        return None
    return tool_name or None


def _deferred_tool_broker_decision(config: dict[str, Any], tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any] | None:
    try:
        from permission_broker import evaluate_tool_request
    except Exception:  # pragma: no cover - broker is optional at runtime
        return None
    try:
        return evaluate_tool_request(tool_name, tool_input, config)
    except Exception:  # pragma: no cover - never let classification break the poll loop
        return None


def correlate_deferred_tool_approval(
    config: dict[str, Any],
    worker: dict[str, Any],
    approval_state: dict[str, Any],
) -> dict[str, Any] | None:
    """Make a Claude `tool_deferred` receipt durable in this supervisor's queue.

    The Claude CLI can exit with `stop_reason=tool_deferred` while the approval
    entry that the permission-broker hook was supposed to create never lands in
    the queue this supervisor reads (a different status root, an uninstalled
    hook, or a hook that died mid-write). The worker is then left in
    `waiting_approval` with nothing pending, trips the "approval state
    disappeared" branch, and its verified worktree gets hard-reset.

    Adopting the receipt here correlates the deferred tool with the worker run
    *before* any exit cleanup runs. It does not widen what is allowed: the
    adopted entry is created as `pending` and still needs an explicit allow, and
    a tool the broker classifies as `deny` is recorded and immediately denied so
    the worker still fails closed.

    Returns the adopted approval, or None when there is nothing to adopt.
    """
    if not _provider_uses_claude_cli(config, worker.get("provider")):
        return None
    receipt = _deferred_tool_use_receipt(worker)
    if receipt is None:
        return None
    run_id = str(worker.get("run_id") or "").strip()
    if not run_id:
        return None
    existing = find_worker_deferred_approval(
        approval_state,
        worker_run_id=run_id,
        tool_use_id=receipt["tool_use_id"] or None,
        tool_name=receipt["tool_name"],
        tool_input=receipt["tool_input"],
    )
    if existing is not None:
        return None

    tool_name = receipt["tool_name"]
    tool_input = receipt["tool_input"]
    broker_decision = _deferred_tool_broker_decision(config, tool_name, tool_input)
    approval, created = ensure_worker_deferred_approval(
        config,
        {
            "provider": worker.get("provider"),
            "agent_id": worker.get("agent_id"),
            "task_id": worker.get("task_id"),
            "worker_run_id": run_id,
            "session_id": worker.get("session_id") or worker.get("resume_token"),
            "tool_use_id": receipt["tool_use_id"] or None,
            "tool_name": tool_name,
            "tool_input": tool_input,
            "risk_class": (broker_decision or {}).get("risk_class") or DEFERRED_TOOL_RISK_CLASS,
            "suggested_rule": _deferred_tool_suggested_rule(tool_name, tool_input),
            "correlation_source": "supervisor_deferred_tool_receipt",
            "broker_decision": broker_decision,
        },
    )
    approval_id = approval.get("approval_id")
    worker["deferred_action"] = approval_id
    worker["deferred_tool_use_id"] = receipt["tool_use_id"] or None
    write_activity_log(
        config,
        {
            "type": (
                "worker_deferred_approval_recorded"
                if created
                else "worker_deferred_approval_correlated"
            ),
            "provider": worker.get("provider"),
            "task_id": worker.get("task_id"),
            "message": (
                f"Recorded deferred {tool_name} approval {approval_id} from the worker's tool_deferred receipt."
                if created
                else f"Correlated existing deferred {tool_name} approval {approval_id} with the worker receipt."
            ),
            "worker_run_id": run_id,
            "approval_id": approval_id,
            "tool_name": tool_name,
            "risk_class": approval.get("risk_class"),
        },
    )
    if (broker_decision or {}).get("decision") == "deny" and approval_id:
        try:
            return resolve_approval(
                config,
                approval_id,
                decision="deny",
                note=str(broker_decision.get("reason") or "Deferred tool is denied by orchestrator policy."),
                remember=False,
            )
        except KeyError:  # pragma: no cover - resolved concurrently
            return approval
    return approval


def resume_claude_worker(
    config: dict[str, Any],
    worker: dict[str, Any],
    provider_report: dict[str, Any],
    *,
    approval: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    session_id = worker.get("session_id") or worker.get("resume_token")
    if not session_id:
        return None
    provider_id = normalize_agent_id(worker.get("provider") or "claude")
    provider = (config.get("providers", {}) or {}).get(provider_id) or config.get("providers", {}).get("claude", {}) or {}
    runtime = provider.get("runtime", {})
    cli = command_exists(runtime.get("cli") or "claude")
    if not cli:
        return None
    command = [
        runtime.get("cli") or cli,
        "--resume",
        str(session_id),
        "--output-format",
        runtime.get("output_format", "stream-json"),
    ]
    if runtime.get("output_format", "stream-json") == "stream-json":
        command.append("--verbose")
    if runtime.get("include_hook_events", True):
        command.append("--include-hook-events")
    allowed_tools = (
        _claude_resume_allowed_tools(approval)
        if runtime.get("resume_use_allowed_tools_from_approval", True)
        else []
    )
    if allowed_tools:
        command.extend(["--allowedTools", *allowed_tools])
    provider_info = (
        (provider_report or {}).get("providers", {}).get(provider_id)
        or (provider_report or {}).get("providers", {}).get("claude", {})
    )
    resume_permission_mode = runtime.get("resume_permission_mode_after_approval", "bypassPermissions")
    if worker.get("last_approval_id"):
        command.extend(["--permission-mode", resume_permission_mode])
    elif runtime.get("enable_auto_mode_if_supported", True) and provider_info.get("supports_auto_approve"):
        command.extend(["--permission-mode", runtime.get("auto_permission_mode", "auto")])
    else:
        command.extend(["--permission-mode", runtime.get("permission_mode", "acceptEdits")])
    mcp_config = runtime.get("mcp_config")
    if mcp_config:
        command.extend(["--mcp-config", str(config_path(config, "claude_mcp_config"))])
    log_path = config_path(config, "state_file").parent / "logs" / f"{new_runtime_id(f'{provider_id}-resume')}.log"
    env = _claude_runtime_env(config, provider_id)
    repo_root = config_path(config, "status_file").parents[0]
    request_metadata = (worker.get("request_snapshot") or {}).get("metadata", {}) if isinstance(worker.get("request_snapshot"), dict) else {}
    workspace_root = Path(str(worker.get("workspace_path") or request_metadata.get("workspace_path") or repo_root)).expanduser().resolve()
    status_root = Path(str(worker.get("status_root") or request_metadata.get("status_root") or repo_root)).expanduser().resolve()
    env.update(
        {
            "ORCH_RUN_ID": worker["run_id"],
            "ORCH_TASK_ID": worker.get("task_id") or "",
            "ORCH_AGENT_ID": worker.get("agent_id") or "",
            "ORCH_PROVIDER": provider_id,
            "ORCH_SESSION_ID": str(session_id),
            "PANTHEON_WORKTREE_ROOT": str(workspace_root),
            "PANTHEON_STATUS_ROOT": str(status_root),
            "ORCH_STATUS_ROOT": str(status_root),
            "ORCH_WORKSPACE_PATH": str(workspace_root),
        }
    )
    runtime_paths = worker_runtime_paths(config, worker["run_id"])
    process, _ = spawn_background_process(
        command,
        cwd=workspace_root,
        log_path=log_path,
        env=env,
        run_id=worker["run_id"],
        heartbeat_path=runtime_paths["heartbeat_path"],
        status_path=runtime_paths["status_path"],
    )
    previous_logs = list(worker.get("previous_log_paths") or [])
    if worker.get("log_path"):
        previous_logs.append(worker["log_path"])
    now_dt = datetime.now(UTC)
    worker["previous_log_paths"] = previous_logs
    worker["pid"] = process.pid
    worker["status"] = "running"
    worker["deferred_action"] = None
    worker["deferred_tool_use"] = None
    worker["deferred_tool_use_id"] = None
    worker["last_event_at"] = _isoformat_utc(now_dt)
    worker["last_heartbeat_at"] = None
    worker["lease_acquired_at"] = _isoformat_utc(now_dt)
    worker["lease_expires_at"] = worker_lease_expiry(config, now_dt)
    worker["heartbeat_path"] = str(runtime_paths["heartbeat_path"])
    worker["runner_status_path"] = str(runtime_paths["status_path"])
    worker["log_path"] = str(log_path)
    worker["resume_count"] = int(worker.get("resume_count", 0)) + 1
    worker["last_resumed_session_id"] = str(session_id)
    worker["command"] = command
    worker.setdefault("metadata", {})["shell_command"] = shell_quote(command)
    worker["metadata"]["resume_permission_mode"] = resume_permission_mode if worker.get("last_approval_id") else None
    worker["metadata"]["resume_allowed_tools"] = allowed_tools
    worker["metadata"]["heartbeat_path"] = str(runtime_paths["heartbeat_path"])
    worker["metadata"]["runner_status_path"] = str(runtime_paths["status_path"])
    return {
        "command": command,
        "log_path": str(log_path),
        "pid": process.pid,
        "allowed_tools": allowed_tools,
    }


def poll_workers(config: dict[str, Any], state: dict[str, Any], provider_report: dict[str, Any] | None = None) -> bool:
    changed = False
    approval_state = load_approval_state(config)
    task_map = task_index_from_status(config, load_status(config))
    valid_queue_event_ids = set(state.get("queue", {}).get("events", {}))
    redispatch_statuses = redispatch_candidate_statuses(config)
    active_worker_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
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
    now = datetime.now(UTC)
    if provider_report is None:
        provider_report = load_provider_report(config)
    changed = retry_due_workers(config, state, provider_report, now) or changed
    poll_counts = {
        "marker_updates": 0,
        "lease_refreshes": 0,
        "expired_lease_workers_failed": 0,
    }
    workers = state.setdefault("workers", {})
    for run_id, worker in list(workers.items()):
        # These records already have a durable terminal disposition. Re-reading
        # their old marker/log after a later re-review or reviewer reopen must
        # never count the same run again or reassign the current lifecycle.
        if str(worker.get("status") or "").lower() in {
            "completed",
            "failed",
            "superseded",
            "reassigned",
        }:
            continue
        previous_last_event_at = worker.get("last_event_at")
        if worker.get("queue_event_id") and worker.get("queue_event_id") not in valid_queue_event_ids:
            if worker.get("status") in {"running", "waiting_approval", "retry_backoff", "manual_pending", "stalled"} and not pid_is_alive(worker.get("pid")):
                task_status = str(task_map.get(worker.get("task_id"), {}).get("status") or "").lower()
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
        if alive and worker.get("status") in active_worker_statuses and worker.get("last_heartbeat_at"):
            if not worker_heartbeat_is_stale(config, worker, now):
                refresh_worker_lease(config, worker, now)
                poll_counts["lease_refreshes"] += 1
                if worker.get("queue_event_id"):
                    record = queue_status(state, worker["queue_event_id"])
                    record["lease_owner"] = worker.get("run_id")
                    record["lease_expires_at"] = queue_lease_expiry(config, now)
        if alive and worker.get("status") in active_worker_statuses and worker_lease_is_expired(config, worker, now):
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
            and worker.get("status") in active_worker_statuses
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
                    queue_status(state, worker["queue_event_id"])["status"] = "manual_pending"
                changed = True
            continue

        if worker.get("status") in {"waiting_approval", "suspended_approval"} and resolved:
            latest = resolved[-1]
            if latest.get("approval_id") != worker.get("last_approval_id"):
                worker["last_approval_id"] = latest.get("approval_id")
                if latest.get("decision") == "allow" and _provider_uses_claude_cli(config, worker.get("provider")):
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
            if worker.get("status") == "stalled" and last_event_advanced:
                worker["status"] = "running"
                worker["last_event_at"] = worker.get("last_event_at") or utc_now()
                write_activity_log(
                    config,
                    {
                        "type": "worker_recovered",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": "Worker produced new output after being marked stalled; status restored to running.",
                        "worker_run_id": worker["run_id"],
                    },
                )
                console_log(
                    f"worker recovered: task={worker.get('task_id')} provider={worker.get('provider')} run={worker.get('run_id')}",
                    quiet=SUPERVISOR_LOG_QUIET,
                )
                changed = True
                continue
            last_event = worker.get("last_event_at")
            if last_event:
                last_dt = datetime.fromisoformat(last_event.replace("Z", "+00:00"))
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

        failure_reason = None if worker_runner_succeeded(worker) else detect_worker_failure(worker)
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


def worker_worktree_housekeeping_settings(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("worker_worktree_housekeeping")
    settings = raw if isinstance(raw, dict) else {}
    return {
        "enabled": bool(settings.get("enabled", True)),
        "tick_interval_seconds": int(settings.get("tick_interval_seconds", 600) or 0),
        "base_branches": [str(b).strip() for b in (settings.get("base_branches") or ["dev", "master", "main"]) if str(b).strip()],
        "max_removals_per_tick": int(settings.get("max_removals_per_tick", 5)),
    }


def _scan_process_paths_in_root(base_root: Path) -> set[Path]:
    """Return resolved paths under base_root mentioned in any live process cmdline."""
    base_str = str(base_root)
    referenced: set[Path] = set()
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return referenced
    self_pid = os.getpid()
    for entry in entries:
        name = entry.name
        if not name.isdigit():
            continue
        if int(name) == self_pid:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore")
        if base_str not in cmdline:
            continue
        for tok in cmdline.split(" "):
            if tok.startswith(base_str):
                try:
                    referenced.add(Path(tok).resolve())
                except OSError:
                    pass
    return referenced


def prune_orphan_worktrees(config: dict[str, Any], state: dict[str, Any]) -> bool:
    """Remove finished worker worktrees whose branches are merged and tree is clean."""
    settings = worker_worktree_housekeeping_settings(config)
    if not settings["enabled"]:
        return False

    interval = settings["tick_interval_seconds"]
    bucket = state.setdefault("worker_worktree_housekeeping", {})
    if interval > 0:
        last_at = bucket.get("last_run_at")
        last_dt = _parse_iso_utc(str(last_at or ""))
        now = datetime.now(UTC)
        if last_dt is not None and (now - last_dt).total_seconds() < interval:
            return False
    bucket["last_run_at"] = utc_now()

    worktree_settings = worker_worktree_settings(config)
    if not worktree_settings.get("enabled", False):
        return False
    base_root = _worker_worktree_base_root(config, worktree_settings)
    if not base_root.exists():
        return False
    repo_root = config_path(config, "status_file").parents[0]

    claimed_paths: set[Path] = set()
    for worker in state.get("workers", {}).values():
        wp = worker.get("workspace_path")
        if not wp:
            continue
        try:
            claimed_paths.add(Path(str(wp)).resolve())
        except OSError:
            continue

    live_paths = _scan_process_paths_in_root(base_root)

    merged_branches: set[str] = set()
    for ref in settings["base_branches"]:
        for candidate in (f"origin/{ref}", ref):
            if not _git_ref_exists(repo_root, candidate):
                continue
            proc = subprocess.run(
                ["git", "branch", "--merged", candidate, "--list", "task/*"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                continue
            for line in proc.stdout.splitlines():
                name = line.strip().lstrip("*").strip()
                if name:
                    merged_branches.add(name)
    if not merged_branches:
        return False

    max_removals = max(0, settings["max_removals_per_tick"])
    base_root_str = str(base_root)
    removed: list[str] = []
    for record in _git_worktree_records(repo_root):
        if len(removed) >= max_removals:
            break
        wt_value = record.get("worktree")
        if not wt_value or not wt_value.startswith(base_root_str):
            continue
        try:
            wt_path = Path(wt_value).resolve()
        except OSError:
            continue
        if wt_path in claimed_paths:
            continue
        if any(str(live).startswith(str(wt_path)) or str(wt_path).startswith(str(live)) for live in live_paths):
            continue
        branch = _worktree_record_branch(record)
        if not branch or branch not in merged_branches:
            continue
        status_proc = subprocess.run(
            ["git", "-C", str(wt_path), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        if status_proc.returncode != 0 or status_proc.stdout.strip():
            continue
        remove_proc = subprocess.run(
            ["git", "-C", str(repo_root), "worktree", "remove", str(wt_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if remove_proc.returncode == 0:
            removed.append(str(wt_path))

    if removed:
        write_activity_log(
            config,
            {
                "type": "worktree_pruned",
                "message": f"Pruned {len(removed)} orphan worker worktree(s): {', '.join(removed)}",
            },
        )
        return True
    return False


def auto_commit_archive_settings(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("auto_commit_archive")
    settings = raw if isinstance(raw, dict) else {}
    return {
        "enabled": bool(settings.get("enabled", True)),
        "tick_interval_seconds": int(settings.get("tick_interval_seconds", 1800) or 0),
        "script_timeout_seconds": int(settings.get("script_timeout_seconds", 180)),
    }


def maybe_auto_commit_archive(config: dict[str, Any], state: dict[str, Any]) -> bool:
    """Periodically run .orchestrator/auto_commit_archive.py so supervisor-side
    archive metadata + task briefs are not stranded as untracked files in the
    main worktree. Returns True iff the script ran AND produced a PR (so the
    caller can mark state as changed and refresh runtime artifacts)."""
    settings = auto_commit_archive_settings(config)
    if not settings["enabled"]:
        return False

    interval = settings["tick_interval_seconds"]
    bucket = state.setdefault("auto_commit_archive", {})
    if interval > 0:
        last_at = bucket.get("last_run_at")
        last_dt = _parse_iso_utc(str(last_at or ""))
        now = datetime.now(UTC)
        if last_dt is not None and (now - last_dt).total_seconds() < interval:
            return False
    bucket["last_run_at"] = utc_now()

    try:
        repo_root = config_path(config, "status_file").parents[0]
    except KeyError:
        bucket["last_error"] = "status_file path not configured"
        return False
    script = repo_root / ".orchestrator" / "auto_commit_archive.py"
    if not script.exists():
        bucket["last_error"] = "script missing"
        return False

    try:
        proc = subprocess.run(
            ["python3", str(script), "--quiet"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=settings["script_timeout_seconds"],
        )
    except subprocess.TimeoutExpired:
        bucket["last_error"] = "timeout"
        return False
    except OSError as exc:
        bucket["last_error"] = f"spawn failed: {exc}"
        return False

    bucket["last_exit"] = proc.returncode
    stdout_tail = (proc.stdout or "").strip().splitlines()[-1:] if proc.stdout else []
    stderr_tail = (proc.stderr or "").strip().splitlines()[-1:] if proc.stderr else []
    bucket["last_stdout"] = stdout_tail[0] if stdout_tail else ""
    bucket["last_stderr"] = stderr_tail[0] if stderr_tail else ""
    # Script prints "auto_commit_archive: opened PR for ..." when it actually opens one.
    return proc.returncode == 0 and "opened PR for" in (proc.stdout or "")


def trim_worker_history(state: dict[str, Any], max_entries: int) -> None:
    compact_worker_history(state, max_entries)


def finish_loop_stage_timing(state: dict[str, Any], stage: str, started_at: float) -> None:
    duration_ms = max(0, int(round((time.monotonic() - started_at) * 1000)))
    supervisor_state = state.setdefault("supervisor", {})
    timings = supervisor_state.setdefault("last_stage_timings_ms", {})
    timings[str(stage)] = duration_ms
    slowest = max(timings.items(), key=lambda item: item[1], default=(None, 0))
    supervisor_state["slowest_stage"] = slowest[0]
    supervisor_state["slowest_stage_duration_ms"] = slowest[1]


def reconcile_queue_records(config: dict[str, Any], state: dict[str, Any]) -> bool:
    changed = False
    queue_events = state.get("queue", {}).get("events", {})
    if not queue_events:
        return False
    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    for event_id, record in queue_events.items():
        workers = [worker for worker in state.get("workers", {}).values() if worker.get("queue_event_id") == event_id]
        if not workers:
            continue
        if any(worker.get("status") in active_statuses for worker in workers):
            continue
        latest = sorted(workers, key=lambda item: item.get("last_event_at") or "", reverse=True)[0]
        next_status = "failed" if any(worker.get("status") == "failed" for worker in workers) else "completed"
        if record.get("status") != next_status:
            record["status"] = next_status
            record["processed_at"] = latest.get("last_event_at") or utc_now()
            if next_status == "failed" and latest.get("last_error"):
                record["error"] = latest.get("last_error")
            changed = True
    return changed


def _reset_queue_record_for_redispatch(record: dict[str, Any], *, reason: str) -> None:
    record["status"] = "queued"
    record["requeued_at"] = utc_now()
    record["requeue_reason"] = reason
    for key in (
        "processed_at",
        "error",
        "lease_owner",
        "lease_acquired_at",
        "lease_expires_at",
        "lease_released_at",
        "last_wait_reason",
    ):
        record.pop(key, None)


def reconcile_runtime_on_boot(config: dict[str, Any], state: dict[str, Any]) -> bool:
    changed = False
    now = datetime.now(UTC)
    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    redispatch_statuses = redispatch_candidate_statuses(config)
    counts = {
        "marker_updates": 0,
        "lease_refreshes": 0,
        "missing_process_workers_failed": 0,
        "expired_lease_workers_failed": 0,
        "started_queue_records_requeued": 0,
        "started_queue_records_failed": 0,
        "stale_queue_records_completed": 0,
    }
    try:
        task_map = task_index_from_status(config, load_status(config))
    except KeyError:
        task_map = {}
    workers = state.setdefault("workers", {})

    for run_id, worker in list(workers.items()):
        if worker.get("status") not in active_statuses:
            continue
        marker_changed = update_worker_runtime_markers(worker)
        if marker_changed:
            counts["marker_updates"] += 1
            changed = True
        status_before_log_update = worker.get("status")
        if _provider_uses_claude_cli(config, worker.get("provider")):
            update_from_log(config, worker)
            if _deferred_tool_use_receipt(worker) is not None:
                try:
                    correlate_deferred_tool_approval(
                        config,
                        worker,
                        load_approval_state(config),
                    )
                except Exception as error:  # pragma: no cover - queue write failures must fail closed
                    worker["status"] = status_before_log_update
                    write_activity_log(
                        config,
                        {
                            "type": "worker_deferred_approval_failed",
                            "provider": worker.get("provider"),
                            "task_id": worker.get("task_id"),
                            "message": f"Could not record deferred tool approval for {run_id} during boot: {error}",
                            "worker_run_id": run_id,
                        },
                    )
                else:
                    # A flushed Claude result can outlive its runner process.
                    # Preserve it for the normal poll path instead of treating
                    # the missing PID as a generic worker exit.
                    changed = True
        alive = pid_is_alive(worker.get("pid"))
        missing_process = worker.get("status") in {"running", "stalled"} and not alive
        expired_lease = alive and worker_lease_is_expired(config, worker, now)
        if alive and not expired_lease and worker.get("last_heartbeat_at") and not worker_heartbeat_is_stale(config, worker, now):
            refresh_worker_lease(config, worker, now)
            counts["lease_refreshes"] += 1
            if worker.get("queue_event_id"):
                record = queue_status(state, worker["queue_event_id"])
                record["lease_owner"] = worker.get("run_id")
                record["lease_expires_at"] = queue_lease_expiry(config, now)
            changed = True
            continue
        if not missing_process and not expired_lease:
            continue
        if alive:
            terminate_worker_pid(worker.get("pid"))
        reason = (
            "Worker lease expired during supervisor boot reconciliation."
            if expired_lease
            else "Worker process missing during supervisor boot reconciliation."
        )
        runner_succeeded = worker_runner_succeeded(worker)
        if runner_succeeded and (worker_is_discussion_planning(worker) or worker_is_coordination_dispatch(worker)):
            worker["status"] = "completed"
            worker["last_event_at"] = worker.get("runner_finished_at") or utc_now()
            clear_task_failure_streak(state, worker=worker)
            finalize_queue_event_record(config, state, worker, "completed")
            write_activity_log(
                config,
                {
                    "type": "worker_completed",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": "Control worker exited successfully during supervisor boot reconciliation.",
                    "worker_run_id": run_id,
                    "pr_url": worker.get("pr_url"),
                    "session_url": worker.get("session_url"),
                },
            )
            changed = True
            continue

        task_status = str(task_map.get(str(worker.get("task_id") or ""), {}).get("status") or "").lower()
        terminal_statuses = {
            str(value).lower()
            for value in ready_dispatch_settings(config).get("worker_terminal_statuses", ["done", "review_approved"])
        }
        current_task = task_map.get(str(worker.get("task_id") or ""), {})
        success_outcome = (
            successful_worker_exit_outcome(
                worker,
                current_task,
                terminal_statuses=terminal_statuses,
            )
            if runner_succeeded
            else None
        )
        if runner_succeeded and success_outcome in {
            "lifecycle_complete",
            "review_decided",
            "incremental_progress",
        }:
            worker["status"] = "completed"
            worker["last_event_at"] = worker.get("runner_finished_at") or utc_now()
            worker["progress_outcome"] = success_outcome
            clear_task_failure_streak(state, worker=worker)
            finalize_queue_event_record(config, state, worker, "completed")
            write_activity_log(
                config,
                {
                    "type": "worker_completed",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": (
                        "Worker recorded meaningful incremental progress before supervisor boot reconciliation; task remains dispatchable."
                        if success_outcome == "incremental_progress"
                        else "Worker completed its required task lifecycle transition before supervisor boot reconciliation."
                    ),
                    "worker_run_id": run_id,
                    "pr_url": worker.get("pr_url"),
                    "session_url": worker.get("session_url"),
                    "progress_outcome": success_outcome,
                },
            )
            changed = True
            continue

        if runner_succeeded:
            reason = NO_PROGRESS_WORKER_EXIT_REASON
            failure_count = record_task_failure_streak(
                state,
                worker,
                reason,
                failure_kind="no_progress",
            )
            generic_threshold = max(1, int(provider_guardrail_settings(config).get("generic_exit_reassign_after", 2)))
            reassigned_to = None
            if task_status in redispatch_statuses and failure_count >= generic_threshold:
                reassigned_to = maybe_reassign_task_after_worker_failure(
                    config,
                    state,
                    worker,
                    reason,
                    terminal=True,
                    force=True,
                    failure_count=failure_count,
                )
            if reassigned_to:
                worker["status"] = "reassigned"
                worker["reassigned_to"] = reassigned_to
                worker["last_event_at"] = worker.get("runner_finished_at") or utc_now()
                worker["last_error"] = reason
                finalize_queue_event_record(config, state, worker, "completed")
                if expired_lease:
                    counts["expired_lease_workers_failed"] += 1
                else:
                    counts["missing_process_workers_failed"] += 1
                changed = True
                continue

        detected_reason = None if runner_succeeded else detect_worker_failure(worker)
        if detected_reason:
            failure = classify_worker_failure(config, worker, detected_reason)
            failure_summary = summarize_failure_reason(
                detected_reason,
                str(worker.get("provider") or worker.get("agent_id") or ""),
            )
            raw_ref = write_failure_evidence(
                config,
                worker=worker,
                reason=detected_reason,
                failure_kind=str(failure.get("kind") or ""),
            )
            failure_count = record_task_failure_streak(
                state,
                worker,
                detected_reason,
                failure_kind=str(failure.get("kind") or ""),
            )
            failure_kind = str(failure.get("kind") or "")
            if should_pause_dispatch_for_failure_kind(failure_kind):
                mark_provider_dispatch_paused(
                    config,
                    state,
                    str(worker.get("provider") or worker.get("agent_id") or ""),
                    detected_reason,
                    task_id=str(worker.get("task_id") or ""),
                    worker_run_id=str(worker.get("run_id") or ""),
                    failure_kind=failure_kind,
                    pause_kind=failure_kind,
                    raw_ref=raw_ref,
                    worker=worker,
                )
            if is_terminal_quota_failure_kind(failure_kind):
                fence_account_pool_workers(config, state, worker, detected_reason)
            if is_terminal_quota_failure_kind(failure_kind):
                reassigned_to = None
                if not antigravity_pool_fallback_available(
                    config, str(worker.get("provider") or worker.get("agent_id") or "")
                ):
                    reassigned_to = maybe_reassign_task_after_worker_failure(
                        config,
                        state,
                        worker,
                        failure_summary.get("summary") or detected_reason,
                        terminal=True,
                        force=True,
                        failure_count=failure_count,
                    )
                if reassigned_to:
                    worker["status"] = "reassigned"
                    worker["reassigned_to"] = reassigned_to
                    worker["last_event_at"] = utc_now()
                    worker["last_error"] = failure_summary.get("summary") or detected_reason
                    worker["last_error_raw_ref"] = raw_ref
                    finalize_queue_event_record(config, state, worker, "completed")
                    if expired_lease:
                        counts["expired_lease_workers_failed"] += 1
                    else:
                        counts["missing_process_workers_failed"] += 1
                    changed = True
                    continue
            reason = failure_summary.get("summary") or detected_reason
            worker["last_error_raw_ref"] = raw_ref
        worker["status"] = "failed"
        worker["last_event_at"] = utc_now()
        worker["last_error"] = reason
        finalize_queue_event_record(config, state, worker, "failed", reason)
        if expired_lease:
            counts["expired_lease_workers_failed"] += 1
        else:
            counts["missing_process_workers_failed"] += 1
        write_activity_log(
            config,
            {
                "type": "worker_failed",
                "provider": worker.get("provider"),
                "task_id": worker.get("task_id"),
                "message": reason,
                "worker_run_id": run_id,
            },
        )
        changed = True

    queue_records = state.setdefault("queue", {}).setdefault("events", {})
    try:
        queued_events = load_event_queue(config)
    except KeyError:
        queued_events = []
    for event in queued_events:
        event_id = str(event.get("event_id") or "")
        if not event_id:
            continue
        record = queue_records.get(event_id)
        if not isinstance(record, dict):
            continue
        if str(record.get("status") or "") not in {"started", "stalled"}:
            continue
        related_active = [
            worker
            for worker in workers.values()
            if worker.get("queue_event_id") == event_id and worker.get("status") in active_statuses
        ]
        if related_active:
            continue
        skip_message = stale_dispatch_skip_message(config, event, task_map)
        if skip_message:
            record["status"] = "completed"
            record["processed_at"] = utc_now()
            record["skip_reason"] = "stale_dispatch_event"
            record["requeue_reason"] = "started event became stale while supervisor was offline"
            counts["stale_queue_records_completed"] += 1
            changed = True
            continue
        task_status = str(task_map.get(str(event.get("task_id") or ""), {}).get("status") or "").lower()
        if task_status in redispatch_statuses:
            _reset_queue_record_for_redispatch(
                record,
                reason="started queue record had no active worker during supervisor boot reconciliation",
            )
            counts["started_queue_records_requeued"] += 1
        else:
            record["status"] = "failed"
            record["processed_at"] = utc_now()
            record["error"] = "Started queue record had no active worker and task is no longer redispatchable."
            counts["started_queue_records_failed"] += 1
        changed = True
    corrective_counts = {
        key: counts[key]
        for key in (
            "missing_process_workers_failed",
            "expired_lease_workers_failed",
            "started_queue_records_requeued",
            "started_queue_records_failed",
            "stale_queue_records_completed",
        )
    }
    record_worker_runtime_measurement(
        config,
        state,
        "boot_reconciliation",
        counts,
        emit_activity=bool(positive_runtime_counts(corrective_counts)),
    )
    return changed

def reviewer_failover_settings(config: dict[str, Any]) -> dict[str, Any]:
    """Settings for the only automatic reassignment: an unavailable reviewer."""
    settings = dict(ready_dispatch_settings(config).get("reviewer_failover", {}) or {})
    settings.setdefault("enabled", True)
    return settings


def task_is_sidecar(task: dict[str, Any]) -> bool:
    return str(task.get("task_class") or "").strip().lower() == "sidecar"


def task_is_human_gate(task: dict[str, Any]) -> bool:
    task_class = str(task.get("task_class") or "").strip().lower()
    gate_status = str(task.get("gate_status") or "").strip().lower()
    return (
        task_class == "human_gate"
        or bool(task.get("human_required_roles"))
        or gate_status.startswith("pending_human")
    )


def review_submission_is_complete(config: dict[str, Any], task: dict[str, Any]) -> bool:
    """Return whether review has immutable, task-scoped remote PR provenance."""
    submission = task.get("review_submission")
    if not isinstance(submission, dict):
        return False
    task_id = str(task.get("id") or "").strip()
    github = task.get("github") if isinstance(task.get("github"), dict) else {}
    explicit_branch = str(github.get("head_branch") or task.get("branch") or "").strip()
    expected_branch = explicit_branch or f"task/{task_id}"
    expected_base = str((config.get("branch_workflow") or {}).get("dev_branch") or "dev").strip()
    try:
        pr_number = int(submission.get("pr_number") or 0)
    except (TypeError, ValueError):
        pr_number = 0
    task_ref = task_id.lower().replace("_", "-")
    branch_ref = expected_branch.strip("/").lower().replace("_", "-")
    return bool(
        task_id
        and pr_number > 0
        and (branch_ref == task_ref or branch_ref.endswith(f"/{task_ref}"))
        and str(submission.get("branch") or "") == expected_branch
        and str(submission.get("base_branch") or "") == expected_base
        and re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", str(submission.get("remote_sha") or ""))
    )


def task_actor_assignment_block_reason(
    config: dict[str, Any],
    state: dict[str, Any],
    task: dict[str, Any],
    agent_name: str | None,
    *,
    require_dispatch_eligibility: bool = True,
) -> str | None:
    """Return a stable assignment problem, excluding momentary slot occupancy.

    Non-dispatchable and human-gate tasks still need registered actors for
    durable ownership and audit history, but their actors must not be judged
    by the dispatch predicate that deliberately rejects those task classes.
    """
    name = str(agent_name or "").strip()
    if not name:
        return "missing actor"
    if is_human_gate_agent(name):
        return None
    normalized = normalize_agent_id(name)
    agent = (config.get("agents", {}) or {}).get(normalized)
    if not isinstance(agent, dict):
        return f"unregistered actor {name}"
    if not require_dispatch_eligibility:
        return None
    if not agent_can_take_task(config, name, task):
        return f"actor {name} is disabled or not eligible for this task"
    pool_reason = account_pool_dispatch_block_reason(config, name, runtime_state=state)
    if pool_reason:
        return pool_reason
    provider = str(agent.get("provider") or normalized)
    return provider_runtime_config_block_reason(config, provider)


def task_assignment_integrity_issues(
    config: dict[str, Any],
    state: dict[str, Any],
    task: dict[str, Any],
) -> list[str]:
    """Audit the invariants shared by Supervisor dispatch and Auto Worker review."""
    if str(task.get("status") or "").strip().lower() not in AGENT_OPEN_TASK_STATUSES:
        return []
    issues: list[str] = []
    if str(task.get("priority") or "").strip().upper() not in {"P0", "P1", "P2", "P3"}:
        issues.append("priority_missing_or_invalid")
    owner = str(task.get("owner") or "").strip()
    reviewer = str(task.get("reviewer") or "").strip()
    requires_dispatch = not (task_is_human_gate(task) or bool(task.get("non_dispatchable")))
    owner_reason = task_actor_assignment_block_reason(
        config,
        state,
        task,
        owner,
        require_dispatch_eligibility=requires_dispatch,
    )
    reviewer_reason = task_actor_assignment_block_reason(
        config,
        state,
        task,
        reviewer,
        require_dispatch_eligibility=requires_dispatch,
    )
    if owner_reason:
        issues.append(f"owner_unavailable:{owner_reason}")
    if reviewer_reason:
        issues.append(f"reviewer_unavailable:{reviewer_reason}")
    if (
        owner
        and reviewer
        and not is_human_gate_agent(owner)
        and not is_human_gate_agent(reviewer)
        and not review_is_independent(config, owner, reviewer)
    ):
        issues.append("owner_reviewer_same_account_pool")
    if str(task.get("status") or "").strip().lower() == "review" and not review_submission_is_complete(config, task):
        issues.append("review_submission_missing_or_invalid")
    waiting_for = str(task.get("waiting_for") or "").strip()
    if (
        str(task.get("status") or "").strip().lower() == "blocked"
        and waiting_for
        and not is_human_gate_agent(waiting_for)
    ):
        waiting_reason = task_actor_assignment_block_reason(config, state, task, waiting_for)
        if waiting_reason:
            issues.append(f"waiting_for_unavailable:{waiting_reason}")
    return issues


def reassert_approved_review_gate_if_due(
    config: dict[str, Any],
    task: dict[str, Any],
    *,
    now_ts: float | None = None,
) -> bool:
    """Repair an overwritten/missing exact-head review gate at a bounded rate."""
    settings = ready_dispatch_settings(config)
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

    # Head equality and approved_head presence are verified immediately before
    # this helper is called. Re-emitting the same desired state is idempotent
    # and repairs a later stale writer/status post without weakening the gate.
    runtime_ai_status.emit_task_review_status_check(task, "review_approved")
    task["review_gate_reasserted_at_ts"] = current_ts
    task["review_gate_reasserted_at"] = utc_now()
    return True


def repair_open_task_metadata(config: dict[str, Any], status: dict[str, Any]) -> bool:
    """Backfill durable scheduling metadata omitted by legacy task writers."""
    paths = config.get("paths") or {}
    if not paths.get("status_file") or not paths.get("activity_log"):
        return False
    tasks = status.get("tasks", []) or []
    task_map = {str(task.get("id") or ""): task for task in tasks if task.get("id")}
    changed = False
    timestamp = utc_now()
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
        write_activity_log(
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
    """Remove legacy false-review states before reviewer dispatch.

    Human data/approval gates return to blocked.  Executable tasks return to
    their owner so task_finalize can publish and atomically submit the PR.
    """
    paths = config.get("paths") or {}
    # The repair is a canonical-state migration and must never write through
    # the repository fallback used by small unit-test configs or library-only
    # callers. Production always supplies both explicit coordination paths.
    if not paths.get("status_file") or not paths.get("activity_log"):
        return False
    changed = False
    timestamp = utc_now()
    for task in status.get("tasks", []) or []:
        if str(task.get("status") or "").strip().lower() != "review":
            continue
        if review_submission_is_complete(config, task):
            continue
        task_id = str(task.get("id") or "").strip()
        if task_is_human_gate(task):
            task["status"] = "blocked"
            task["waiting_for"] = "Human/Ops"
            message = (
                "Review state repaired: this is a Human/Ops input/approval gate, not a submitted code review. "
                "It remains blocked until the accountable human evidence is supplied."
            )
        else:
            task["status"] = "in_progress"
            task.pop("waiting_for", None)
            if task_is_sidecar(task) and task.get("depends_on"):
                # A legacy sidecar that already reached review has completed
                # its support artifact. Its parent/human-gate references are
                # context, not prerequisites for publishing that artifact.
                # Preserve them for traceability without letting the owner
                # closeout dispatch deadlock behind the parent it supports.
                task["review_submission_context_dependencies"] = list(task.get("depends_on") or [])
                task["depends_on"] = []
            message = (
                "Review state repaired: no verified remote task PR was recorded. The owner must publish via "
                "delivery_toolchain/git/task_finalize.sh before review can be dispatched."
            )
        task["last_update"] = timestamp
        task["next"] = message
        task.pop("approved_head", None)
        for handoff in status.get("handoffs", []) or []:
            if handoff.get("task_id") == task_id and handoff.get("status") != "done":
                handoff["status"] = "done"
                handoff["resolved_at"] = timestamp
        write_activity_log(
            config,
            {"type": "review_submission_repaired", "task_id": task_id, "message": message},
        )
        changed = True
    if changed:
        if not commit_canonical_task_transition(config, status):
            return False
    return changed


def normalize_task_assignment_integrity(
    config: dict[str, Any],
    state: dict[str, Any],
    status: dict[str, Any],
    task: dict[str, Any],
) -> bool:
    """Reassign unavailable or non-independent automatic task actors."""
    paths = config.get("paths") or {}
    if not paths.get("status_file") or not paths.get("activity_log"):
        return False
    issues = task_assignment_integrity_issues(config, state, task)
    assignment_issues = {
        issue.split(":", 1)[0]
        for issue in issues
        if issue.startswith("owner_unavailable:")
        or issue.startswith("reviewer_unavailable:")
        or issue == "owner_reviewer_same_account_pool"
    }
    waiting_for_unavailable = any(issue.startswith("waiting_for_unavailable:") for issue in issues)
    if not assignment_issues and not waiting_for_unavailable:
        return False

    task_id = str(task.get("id") or "").strip()
    owner = str(task.get("owner") or "").strip()
    reviewer = str(task.get("reviewer") or "").strip()
    new_owner = owner
    new_reviewer = reviewer
    new_waiting_for: str | None = None
    changes: list[str] = []

    if "owner_unavailable" in assignment_issues and not is_human_gate_agent(owner):
        owner_candidates = get_agent_reassignment_candidates(config, owner, role="owner", task=task)
        replacement = first_viable_agent(
            config,
            owner_candidates,
            exclude={owner, reviewer},
            state=state,
            task=task,
            status=status,
            role="owner",
            exclude_pools={agent_account_pool_id(config, reviewer)} if reviewer and not is_human_gate_agent(reviewer) else set(),
        )
        if replacement:
            new_owner = replacement
            changes.append(f"owner {owner} -> {new_owner}")

    reviewer_needs_replacement = (
        "reviewer_unavailable" in assignment_issues
        or "owner_reviewer_same_account_pool" in assignment_issues
        or (
            new_owner
            and new_reviewer
            and not is_human_gate_agent(new_owner)
            and not is_human_gate_agent(new_reviewer)
            and not review_is_independent(config, new_owner, new_reviewer)
        )
    )
    if reviewer_needs_replacement and not is_human_gate_agent(reviewer):
        reviewer_candidates = get_agent_reassignment_candidates(config, reviewer or owner, role="reviewer", task=task)
        replacement = first_viable_agent(
            config,
            reviewer_candidates,
            exclude={new_owner, reviewer},
            state=state,
            task=task,
            status=status,
            role="reviewer",
            exclude_pools={agent_account_pool_id(config, new_owner)},
        )
        if replacement:
            new_reviewer = replacement
            changes.append(f"reviewer {reviewer} -> {new_reviewer}")

    if waiting_for_unavailable:
        waiting_for = str(task.get("waiting_for") or "").strip()
        task_status = str(task.get("status") or "").strip().lower()
        if waiting_for == owner and new_owner:
            replacement_waiting_for = new_owner
        elif waiting_for == reviewer and new_reviewer:
            replacement_waiting_for = new_reviewer
        elif task_status == "review" and new_reviewer:
            replacement_waiting_for = new_reviewer
        else:
            replacement_waiting_for = new_owner
        if (
            replacement_waiting_for
            and not task_actor_assignment_block_reason(config, state, task, replacement_waiting_for)
        ):
            new_waiting_for = replacement_waiting_for
            changes.append(f"waiting_for {waiting_for} -> {new_waiting_for}")

    if not changes or new_owner == new_reviewer:
        return False
    if (
        not is_human_gate_agent(new_owner)
        and not is_human_gate_agent(new_reviewer)
        and not review_is_independent(config, new_owner, new_reviewer)
    ):
        return False

    message = f"Auto-reconciled task assignment integrity: {', '.join(changes)}."
    if not persist_task_reassignment(
        config,
        task_id=task_id,
        new_owner=new_owner,
        new_reviewer=new_reviewer,
        message=message,
        new_waiting_for=new_waiting_for,
        handoff_to=new_owner if new_owner != owner else new_reviewer,
        handoff_from=owner if new_owner != owner else reviewer,
    ):
        return False
    write_activity_log(
        config,
        {
            "type": "task_assignment_integrity_repaired",
            "task_id": task_id,
            "message": message,
            "issues": issues,
        },
    )
    return True


def normalized_business_priority(value: Any, default: str = "P2") -> str:
    priority = str(value or "").strip().upper()
    return priority if priority in {"P0", "P1", "P2", "P3"} else default


def blocked_task_auto_recovery_eligible(
    config: dict[str, Any],
    task: dict[str, Any],
    task_map: dict[str, dict[str, Any]] | None = None,
) -> bool:
    """Whether a blocked task is stale routing state, not a real gate.

    Human, external-data, deployment and operator gates remain fail-closed.
    A blocked task with no unresolved dependency and only routing/provider
    failure language can safely be reopened for a fresh automatic dispatch.
    """
    if str(task.get("status") or "").strip().lower() != "blocked":
        return False
    if task_is_human_gate(task) or task_is_sidecar(task) or bool(task.get("non_dispatchable")):
        return False
    if task_map is not None:
        done_statuses = {
            str(value).lower()
            for value in ready_dispatch_settings(config).get("dependency_done_statuses", ["done"])
        }
        if not dependencies_satisfied(task, task_map, done_statuses):
            return False
    context = " ".join(
        str(task.get(key) or "")
        for key in ("next", "waiting_for", "blocker", "blocked_by", "failure_reason", "last_failure_reason", "push_status")
    ).casefold()
    hard_gate_markers = (
        "human/ops", "human gate", "pending_human", "authoritative", "dataset", "attestation",
        "external-data", "mlflow", "deploy dev", "live-e2e", "production alias", "merge queue",
        "operator intervention", "manual approval", "requires operator",
    )
    if any(marker in context for marker in hard_gate_markers):
        return False
    return bool(context) and any(
        marker in context
        for marker in (
            "auto-reassigned", "sidecar-only", "quota", "auth", "credential", "worktree",
            "push failure", "dispatch", "provider", "handoff", "stale",
        )
    )


def normalize_mainline_task_assignment(
    config: dict[str, Any],
    task: dict[str, Any],
    task_map: dict[str, dict[str, Any]] | None = None,
) -> bool:
    if task_is_sidecar(task):
        return False
    settings = worker_reassignment_settings(config)
    task_id = str(task.get("id") or "").strip()
    if not task_id:
        return False
    task_status = str(task.get("status") or "").lower()
    eligible_statuses = {str(value).lower() for value in settings.get("eligible_statuses", [])}
    eligible_statuses.add("blocked")
    if task_status not in eligible_statuses:
        return False

    owner = str(task.get("owner") or "").strip()
    reviewer = str(task.get("reviewer") or "").strip()
    reopen_blocked = blocked_task_auto_recovery_eligible(config, task, task_map)
    owner_allowed = (
        task_status not in {"todo", "in_progress", "review_approved", "blocked"}
        or agent_can_take_task(config, owner, task)
    )
    # A reviewer label is only executable while the task is in review.  Older
    # task records commonly keep a coordinator/placeholder reviewer on todo
    # work; that metadata must not trigger an unnecessary owner reassignment.
    reviewer_allowed = (
        task_status != "review"
        or agent_can_take_task(config, reviewer, task)
    )
    if owner_allowed and reviewer_allowed and not reopen_blocked:
        return False

    new_owner = owner
    new_reviewer = reviewer
    changed_fields: list[str] = []

    if owner and not owner_allowed:
        if is_human_gate_agent(owner):
            return False
        owner_candidates = get_agent_reassignment_candidates(config, owner, role="owner", task=task)
        replacement_owner = first_viable_agent(config, owner_candidates, exclude={owner, reviewer}, task=task, role="owner")
        if not replacement_owner or is_human_gate_agent(replacement_owner):
            return False
        new_owner = replacement_owner
        changed_fields.append(f"owner {owner} -> {new_owner}")

    if not reviewer or not reviewer_allowed or reviewer == new_owner:
        if reviewer and is_human_gate_agent(reviewer):
            return False
        reviewer_candidates: list[str] = []
        if reviewer:
            reviewer_candidates.append(reviewer)
            reviewer_candidates.extend(get_agent_reassignment_candidates(config, reviewer, role="reviewer", task=task))
        if owner:
            reviewer_candidates.extend(get_agent_reassignment_candidates(config, owner, role="reviewer", task=task))
            reviewer_candidates.extend(get_agent_reassignment_candidates(config, owner, role="owner", task=task))
        replacement_reviewer = first_viable_agent(config, reviewer_candidates, exclude={new_owner}, task=task, role="reviewer")
        if not replacement_reviewer or is_human_gate_agent(replacement_reviewer):
            return False
        new_reviewer = replacement_reviewer
        if replacement_reviewer != reviewer:
            changed_fields.append(f"reviewer {reviewer or '(unset)'} -> {new_reviewer}")

    if new_owner == owner and new_reviewer == reviewer and not reopen_blocked:
        return False

    blocked_agents = [
        agent_name
        for agent_name in (owner, reviewer)
        if agent_name and not agent_can_take_task(config, agent_name, task)
    ]
    blocked_summary = ", ".join(dict.fromkeys(blocked_agents)) or "disallowed lane"
    if changed_fields:
        message = (
            f"Auto-reassigned {task_id} away from unavailable lane {blocked_summary}; "
            f"{', '.join(changed_fields)}."
        )
    else:
        message = f"Reopened stale blocked task {task_id} for automatic dispatch."
    if reopen_blocked:
        message = f"{message} No unresolved dependency or human/external gate remains."
    handoff_target = new_owner if new_owner != owner else (new_reviewer if new_reviewer != reviewer else None)
    handoff_source = owner if new_owner != owner else (reviewer if new_reviewer != reviewer else None)
    if not persist_task_reassignment(
        config,
        task_id=task_id,
        new_owner=new_owner,
        new_reviewer=new_reviewer,
        message=message,
        new_status="todo" if reopen_blocked else None,
        handoff_to=handoff_target,
        handoff_from=handoff_source,
        resolve_open_blockers=reopen_blocked,
    ):
        return False
    write_activity_log(
        config,
        {
            "type": "task_reassigned",
            "task_id": task_id,
            "message": message,
            "from_owner": owner,
            "to_owner": new_owner,
            "from_reviewer": reviewer,
            "to_reviewer": new_reviewer,
            "policy": "sidecar_only_agent_mainline_guard",
        },
    )
    console_log(
        f"policy reassignment: task={task_id} owner={owner}->{new_owner} reviewer={reviewer}->{new_reviewer}",
        quiet=SUPERVISOR_LOG_QUIET,
    )
    return True


def redispatch_candidate_statuses(config: dict[str, Any]) -> set[str]:
    settings = ready_dispatch_settings(config)
    statuses = set(str(value).lower() for value in settings.get("review_statuses", []))
    statuses.update(str(value).lower() for value in settings.get("finalize_statuses", []))
    statuses.update(str(value).lower() for value in settings.get("owned_statuses", []))
    return statuses


def _task_resolver(task_lookup: TaskResolver | dict[str, dict[str, Any]]) -> TaskResolver:
    if isinstance(task_lookup, TaskResolver):
        return task_lookup
    return TaskResolver(task_lookup)


def dependencies_satisfied(task: dict[str, Any], task_lookup: TaskResolver | dict[str, dict[str, Any]], done_statuses: set[str]) -> bool:
    resolver = _task_resolver(task_lookup)
    for dep_id in task.get("depends_on", []) or []:
        dep_status = resolver.dependency_status(dep_id)
        if dep_status not in done_statuses or not resolver.dependency_satisfied(dep_id):
            return False
    return True


def task_dependency_signature(task: dict[str, Any], task_lookup: TaskResolver | dict[str, dict[str, Any]]) -> str:
    resolver = _task_resolver(task_lookup)
    parts: list[str] = []
    for dep_id in task.get("depends_on", []) or []:
        dep_status = resolver.dependency_status(dep_id)
        parts.append(f"{dep_id}:{dep_status}")
    return "|".join(parts)


def active_worker_indexes(state: dict[str, Any], active_statuses: set[str]) -> tuple[set[str], set[tuple[str, str]]]:
    agents: set[str] = set()
    task_agents: set[tuple[str, str]] = set()
    for worker in state.get("workers", {}).values():
        if worker.get("status") not in active_statuses:
            continue
        agent_id = str(worker.get("agent_id") or "")
        task_id = str(worker.get("task_id") or "")
        if agent_id:
            agents.add(agent_id)
        if task_id and agent_id:
            task_agents.add((task_id, agent_id))
    return agents, task_agents


def orphaned_queue_event_grace_seconds(config: dict[str, Any]) -> int:
    value = ready_dispatch_settings(config).get("orphaned_queue_event_grace_seconds", 300)
    try:
        return max(30, int(value))
    except (TypeError, ValueError):
        return 300


def queue_event_age_seconds(event: dict[str, Any]) -> float | None:
    created_at = _parse_iso_utc(str(event.get("created_at") or ""))
    if created_at is None:
        return None
    return max(0.0, (datetime.now(UTC) - created_at.astimezone(UTC)).total_seconds())


def queue_event_is_orphaned(
    config: dict[str, Any],
    event: dict[str, Any],
    record: dict[str, Any],
    related_workers: list[dict[str, Any]],
) -> bool:
    if related_workers:
        return False
    status = str(record.get("status") or "").lower()
    if status in {"completed", "failed"}:
        return False
    age_seconds = queue_event_age_seconds(event)
    if age_seconds is None:
        return False
    return age_seconds > orphaned_queue_event_grace_seconds(config)


def outstanding_delivery_indexes(config: dict[str, Any], state: dict[str, Any]) -> tuple[set[str], set[tuple[str, str]], set[str]]:
    agents: set[str] = set()
    task_agents: set[tuple[str, str]] = set()
    event_keys: set[str] = set()
    queue_records = state.get("queue", {}).get("events", {})
    for event in load_event_queue(config):
        event_id = event.get("event_id")
        if not event_id:
            continue
        record = queue_records.get(event_id, {})
        related_workers = [
            worker for worker in state.get("workers", {}).values() if worker.get("queue_event_id") == event_id
        ]
        if record.get("status") in {"completed", "failed"}:
            continue
        if queue_event_is_orphaned(config, event, record, related_workers):
            continue
        event_key = str(event.get("event_key") or "")
        if event_key:
            event_keys.add(event_key)
        agent_id = str(event.get("target_agent") or "")
        task_id = str(event.get("task_id") or "")
        if agent_id:
            agents.add(agent_id)
        if task_id and agent_id:
            task_agents.add((task_id, agent_id))
    return agents, task_agents, event_keys


def _quarantine_and_preserve_dirty_worktree(
    config: dict[str, Any],
    state: dict[str, Any],
    worktree_path: Path | str | None,
    task_id: str | None,
    *,
    expected_branch: str | None = None,
    run_id: str | None = None,
    trigger: str = "",
) -> bool:
    """Quarantine and preserve dirty worktree state as an immutable backup without destructive reset/clean/stash or modifying the worktree.

    Returns True iff it inventoried tracked/staged/untracked dirt, wrote verified immutable
    backup to `.orchestrator/worktree-dirt-backups/`, leaving the original worktree wholly untouched.
    """
    if worktree_path is None:
        return False
    repo_root = config_path(config, "status_file").parents[0].resolve()
    try:
        worktree_path = Path(worktree_path).expanduser().resolve()
    except (OSError, TypeError, ValueError):
        return False
    if worktree_path == repo_root or not (worktree_path / ".git").exists():
        return False

    wt_git_rc, wt_git_dir = _git_output(worktree_path, "rev-parse", "--git-dir")
    repo_git_rc, _repo_git_dir = _git_output(repo_root, "rev-parse", "--git-dir")
    top_rc, top_level = _git_output(worktree_path, "rev-parse", "--show-toplevel")
    worktree_common_rc, worktree_common = _git_output(worktree_path, "rev-parse", "--git-common-dir")
    repo_common_rc, repo_common = _git_output(repo_root, "rev-parse", "--git-common-dir")
    try:
        resolved_top = Path(top_level).resolve()
        wt_gd = Path(wt_git_dir) if Path(wt_git_dir).is_absolute() else (worktree_path / wt_git_dir)
        wt_cd = Path(worktree_common) if Path(worktree_common).is_absolute() else (wt_gd / worktree_common)
        repo_cd = Path(repo_common) if Path(repo_common).is_absolute() else (repo_root / repo_common)

        resolved_worktree_common = wt_cd.resolve()
        resolved_repo_common = repo_cd.resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    if (
        top_rc != 0
        or worktree_common_rc != 0
        or repo_common_rc != 0
        or wt_git_rc != 0
        or repo_git_rc != 0
        or resolved_top != worktree_path
        or resolved_worktree_common != resolved_repo_common
    ):
        return False

    branch_rc, current_branch = _git_output(worktree_path, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch_rc != 0 or not current_branch:
        return False
    if expected_branch and current_branch != expected_branch:
        return False
    if _git_operation_in_progress(worktree_path):
        return False

    local_head = _git_commit_oid(worktree_path, "HEAD")
    if not local_head:
        return False

    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    for other in state.get("workers", {}).values():
        if not isinstance(other, dict):
            continue
        other_status = str(other.get("status") or "")
        if other_status in active_statuses:
            other_run_id = str(other.get("run_id") or "")
            if run_id and other_run_id == run_id:
                continue
            other_task_id = str(other.get("task_id") or "")
            other_path = str(other.get("workspace_path") or "")
            if (task_id and other_task_id == task_id) or (other_path and Path(other_path).resolve() == worktree_path):
                return False

    status_proc = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=worktree_path,
        capture_output=True,
        check=False,
    )
    if status_proc.returncode != 0 or not status_proc.stdout:
        return False

    raw_entries = [e for e in status_proc.stdout.split(b"\0") if e]
    if not raw_entries:
        return False

    inventory_files: list[dict[str, Any]] = []
    idx = 0
    while idx < len(raw_entries):
        item = raw_entries[idx]
        code = item[:2].decode("utf-8", errors="replace")
        path_bytes = item[3:] if len(item) > 3 else b""
        idx += 1
        orig_path_bytes = None
        if len(code) >= 2 and (code[0] in ("R", "C") or code[1] in ("R", "C")):
            if idx < len(raw_entries):
                orig_path_bytes = raw_entries[idx]
                idx += 1

        rel_path = os.fsdecode(path_bytes)
        orig_path = os.fsdecode(orig_path_bytes) if orig_path_bytes is not None else None
        if not rel_path:
            continue
        full_p = worktree_path / rel_path

        is_symlink = os.path.islink(full_p) or (hasattr(full_p, "is_symlink") and full_p.is_symlink())
        symlink_target: str | None = None
        sha256_val: str | None = None
        is_file = False
        is_dir = False

        if is_symlink:
            try:
                symlink_target = os.readlink(full_p)
            except OSError:
                symlink_target = None
        elif full_p.exists():
            if full_p.is_file():
                is_file = True
                try:
                    h = hashlib.sha256()
                    with open(full_p, "rb") as f:
                        while chunk := f.read(65536):
                            h.update(chunk)
                    sha256_val = h.hexdigest()
                except OSError:
                    sha256_val = None
            elif full_p.is_dir():
                is_dir = True

        inventory_files.append({
            "path": rel_path,
            "orig_path": orig_path,
            "status_code": code,
            "sha256": sha256_val,
            "is_symlink": is_symlink,
            "symlink_target": symlink_target,
            "is_file": is_file,
            "is_dir": is_dir,
        })

    try:
        backup_dir = repo_root / ".orchestrator" / "worktree-dirt-backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        now_str = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        stamp = f"{now_str}_{uuid.uuid4().hex[:8]}"
        task_backup_dir = backup_dir / f"{_task_id_slug(task_id)}-{stamp}"
        task_backup_dir.mkdir(parents=True, exist_ok=False)

        manifest = {
            "task_id": task_id,
            "branch": current_branch,
            "head_sha": local_head,
            "trigger": trigger,
            "run_id": run_id,
            "timestamp": now_str,
            "files": inventory_files,
        }
        manifest_path = task_backup_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        staged_proc = subprocess.run(["git", "diff", "--cached", "--binary"], cwd=worktree_path, capture_output=True, check=False)
        if staged_proc.returncode != 0:
            raise RuntimeError("failed to capture staged diff")
        (task_backup_dir / "staged.patch").write_bytes(staged_proc.stdout)

        unstaged_proc = subprocess.run(["git", "diff", "--binary"], cwd=worktree_path, capture_output=True, check=False)
        if unstaged_proc.returncode != 0:
            raise RuntimeError("failed to capture unstaged diff")
        (task_backup_dir / "unstaged.patch").write_bytes(unstaged_proc.stdout)

        untracked_base = task_backup_dir / "untracked"
        for file_entry in inventory_files:
            rel_p = file_entry["path"]
            src_path = worktree_path / rel_p
            if file_entry.get("status_code", "").startswith("?") or not src_path.exists():
                if file_entry.get("is_symlink") and file_entry.get("symlink_target"):
                    dst_path = untracked_base / rel_p
                    dst_path.parent.mkdir(parents=True, exist_ok=True)
                    if dst_path.exists() or os.path.islink(dst_path):
                        dst_path.unlink()
                    os.symlink(file_entry["symlink_target"], dst_path)
                elif file_entry.get("is_file") and file_entry.get("sha256"):
                    if src_path.exists() and src_path.is_file():
                        dst_path = untracked_base / rel_p
                        dst_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_path, dst_path)
                        h_check = hashlib.sha256()
                        with open(dst_path, "rb") as f_chk:
                            while ch := f_chk.read(65536):
                                h_check.update(ch)
                        if h_check.hexdigest() != file_entry["sha256"]:
                            raise RuntimeError(f"backup checksum mismatch for {rel_p}")

        checksums: dict[str, str] = {}
        for b_root, _, b_files in os.walk(task_backup_dir):
            for bf in b_files:
                if bf == "backup_checksums.sha256":
                    continue
                fp = Path(b_root) / bf
                rel_bp = fp.relative_to(task_backup_dir).as_posix()
                if fp.is_symlink() or os.path.islink(fp):
                    target = os.readlink(fp)
                    checksums[rel_bp] = "symlink:" + hashlib.sha256(target.encode("utf-8")).hexdigest()
                else:
                    h_b = hashlib.sha256()
                    with open(fp, "rb") as f_b:
                        while ch := f_b.read(65536):
                            h_b.update(ch)
                    checksums[rel_bp] = h_b.hexdigest()
        (task_backup_dir / "backup_checksums.sha256").write_text(json.dumps(checksums, indent=2), encoding="utf-8")

    except Exception:
        return False

    write_activity_log(
        config,
        {
            "type": "worker_worktree_preserved",
            "task_id": task_id,
            "run_id": run_id,
            "trigger": trigger,
            "workspace_path": str(worktree_path),
            "backup_dir": str(task_backup_dir),
            "head_sha": local_head,
            "message": (
                f"Quarantined dirty worktree for {task_id} ({trigger or 'cleanup'}); "
                f"backup saved to {task_backup_dir}."
            ),
        },
    )
    return True


def finalize_queue_event_record(config: dict[str, Any], state: dict[str, Any], worker: dict[str, Any], status: str, error: str | None = None) -> None:
    queue_event_id = worker.get("queue_event_id")
    if not queue_event_id:
        return
    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    for item in state.get("workers", {}).values():
        if item.get("run_id") == worker.get("run_id"):
            continue
        if item.get("queue_event_id") == queue_event_id and item.get("status") in active_statuses:
            return
    record = queue_status(state, queue_event_id)
    record["status"] = status
    record["processed_at"] = utc_now()
    record["lease_released_at"] = record["processed_at"]
    if worker.get("run_id"):
        record["lease_owner"] = worker.get("run_id")
    if error:
        record["error"] = error


def save_event_queue(config: dict[str, Any], events: list[dict[str, Any]]) -> None:
    path = config_path(config, "event_queue")
    payload = "".join(f"{json.dumps(event, ensure_ascii=False)}\n" for event in events)
    path.write_text(payload, encoding="utf-8")


def prune_event_queue(config: dict[str, Any], state: dict[str, Any]) -> bool:
    events = load_event_queue(config)
    if not events:
        return False
    task_map = task_index_from_status(config, load_status(config))
    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    redispatch_statuses = redispatch_candidate_statuses(config)
    queue_events = state.setdefault("queue", {}).setdefault("events", {})
    kept: list[dict[str, Any]] = []
    kept_ids: set[str] = set()
    changed = False

    for event in events:
        event_id = event.get("event_id")
        if not event_id:
            changed = True
            continue

        record = queue_events.get(event_id, {})
        related_workers = [worker for worker in state.get("workers", {}).values() if worker.get("queue_event_id") == event_id]
        has_active_worker = any(worker.get("status") in active_statuses for worker in related_workers)
        if queue_event_is_orphaned(config, event, record, related_workers):
            age_seconds = queue_event_age_seconds(event)
            write_activity_log(
                config,
                {
                    "type": "queue_event_pruned",
                    "task_id": event.get("task_id"),
                    "target_agent": event.get("target_display_name") or event.get("target_agent"),
                    "queue_event_id": event_id,
                    "message": (
                        f"Pruned orphaned queue event after {age_seconds:.1f}s without a live worker or queue record."
                        if age_seconds is not None
                        else "Pruned orphaned queue event without a live worker or queue record."
                    ),
                },
            )
            changed = True
            continue
        skip_message = stale_dispatch_skip_message(config, event, task_map)

        if skip_message and not has_active_worker:
            completed = queue_status(state, event_id)
            completed["status"] = "completed"
            completed["processed_at"] = completed.get("processed_at") or utc_now()
            completed["skip_reason"] = "stale_dispatch_event"
            changed = True
            continue

        if not related_workers and record.get("status") in {"started", "manual_pending", "retry_backoff", "stalled"}:
            record["status"] = "queued"
            record.pop("processed_at", None)
            record.pop("error", None)
            changed = True
            kept.append(event)
            kept_ids.add(event_id)
            continue

        current_task = task_map.get(str(event.get("task_id") or ""))
        current_status = str(current_task.get("status") or "").lower() if current_task else ""

        if record.get("status") == "failed" and not has_active_worker and current_status in redispatch_statuses:
            changed = True
            continue

        if record.get("status") in {"completed", "failed"} and not has_active_worker:
            changed = True
            continue

        kept.append(event)
        kept_ids.add(event_id)

    if not changed:
        return False

    state.setdefault("queue", {}).setdefault("events", {})
    state["queue"]["events"] = {event_id: record for event_id, record in queue_events.items() if event_id in kept_ids}
    save_event_queue(config, kept)
    return True


def task_index_from_status(config: dict[str, Any], status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    schema = config.get("schema", {})
    tasks_path = schema.get("tasks_path", "tasks")
    task_id_field = schema.get("task_id_field", "id")
    return {
        str(task.get(task_id_field)): task
        for task in status.get(tasks_path, [])
        if task.get(task_id_field)
    }


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


def worker_logical_dispatch_agent_id(config: dict[str, Any], worker: dict[str, Any]) -> str:
    explicit = normalize_agent_id(str(worker.get("logical_agent_id") or ""))
    if explicit:
        return explicit
    agent_id = normalize_agent_id(str(worker.get("agent_id") or worker.get("provider") or ""))
    agent = config.get("agents", {}).get(agent_id, {}) or {}
    return normalize_agent_id(str(agent.get("dispatch_slot_for") or agent_id))


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


def requeue_task_for_ci_repair(
    config: dict[str, Any],
    task_id: str,
    *,
    message: str,
    clear_approval: bool,
) -> bool:
    """Return a CI-stalled task to its owner for an automatic repair run."""
    status = load_status(config)
    task = next(
        (item for item in status.get("tasks", []) or [] if str(item.get("id") or "") == task_id),
        None,
    )
    # Sidecars are still real worker tasks.  They may be support-only, but a
    # failed CI check must return them to their owner just like mainline work;
    # only human gates and explicitly non-dispatchable tasks stay fail-closed.
    if not isinstance(task, dict) or task_is_human_gate(task) or bool(task.get("non_dispatchable")):
        return False
    if str(task.get("status") or "").lower() != "review_approved":
        return False
    task["status"] = "in_progress"
    task["last_update"] = utc_now()
    task["next"] = message
    task.pop("ci_pending_since_ts", None)
    task.pop("ci_pending_since", None)
    task["ci_repair_last_requeued_ts"] = datetime.now(UTC).timestamp()
    if clear_approval:
        task.pop("approved_head", None)
    if not commit_canonical_task_transition(config, status):
        return False
    write_activity_log(
        config,
        {
            "type": "ci_repair_requeued",
            "task_id": task_id,
            "message": message,
            "approval_cleared": clear_approval,
        },
    )
    return True


def queue_dispatch_event_safely(config: dict[str, Any], event: dict[str, Any]) -> bool:
    try:
        return bool(queue_delivery_event(config, event))
    except Exception as exc:
        message = f"Dispatch event failed closed: {type(exc).__name__}: {exc}"
        write_activity_log(
            config,
            {
                "type": "dispatch_event_rejected",
                "task_id": event.get("task_id"),
                "target_agent": event.get("target_agent"),
                "message": message,
            },
        )
        console_log(
            f"dispatch event rejected: task={event.get('task_id')} target={event.get('target_agent')} error={exc}",
            quiet=SUPERVISOR_LOG_QUIET,
        )
        return False


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
    seen = state.setdefault("seen_event_keys", {})
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
        seen[queued_event_key] = utc_now()
        pending_event_keys.add(queued_event_key)
        changed = True

    return changed


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
    seen = state.setdefault("seen_event_keys", {})

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
                            task["ci_repair_requeued_head"] = approved_key
                            task["ci_repair_last_requeued_ts"] = now_ts
                            status_path = config_path(config, "status_file")
                            write_json(status_path, status)
                            if requeue_task_for_ci_repair(
                                config,
                                task_id,
                                message=msg,
                                clear_approval=False,
                            ):
                                changed = True
                    if status_dirty:
                        if not commit_canonical_task_transition(config, status):
                            return changed

                    continue
                elif ci_status == "failure":
                    task.pop("ci_pending_since_ts", None)
                    msg = f"CI checks for task {task_id} failed; owner requeued to repair CI before re-review."
                    if requeue_task_for_ci_repair(
                        config,
                        task_id,
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
                seen[event["key"]] = utc_now()
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


def run_once(
    config: dict[str, Any],
    *,
    watch: bool,
    replay: bool = False,
    quiet: bool = False,
    verbose: bool = False,
    once: bool = False,
) -> bool:
    write_supervisor_pid(config)
    loop_started_at = utc_now()
    state = load_runtime_state(config)
    previous_heartbeat = state.get("supervisor", {}).get("last_heartbeat_at")
    planning_state = load_discussion_planning_state()
    stamp_supervisor_runtime_state(
        config,
        state,
        planning_state=planning_state,
        heartbeat_at=loop_started_at,
        lifecycle="running",
        loop_started_at=loop_started_at,
    )
    save_runtime_state(config, state)
    changed = False
    try:
        stage_started = time.monotonic()
        changed = reconcile_runtime_on_boot(config, state) or changed
        if changed:
            save_runtime_state(config, state)
        continue_or_skip_empty(THIS_DIR.parent)
        changed = expire_provider_dispatch_pauses(config, state) or changed
        pruned = prune_stale_approvals(config)
        if pruned:
            changed = True
        provider_report = load_provider_report(config)
        finish_loop_stage_timing(state, "boot_and_provider", stage_started)
        boot_and_provider_ms = state.get("supervisor", {}).get("last_stage_timings_ms", {}).get("boot_and_provider")

        stage_started = time.monotonic()
        # The canonical task store is the scheduler's source of truth.  The
        # legacy watcher only maintained a second task snapshot when runtime
        # delivery events were disabled, which made every loop write stale
        # state without producing work.  Do not scan unless it can enqueue an
        # event that the queue will actually consume.
        if watch and enqueue_runtime_events_enabled(config):
            changed = run_scan(config, state, replay=replay, provider_capabilities=provider_report) or changed
            state = load_runtime_state(config)
            if boot_and_provider_ms is not None:
                state.setdefault("supervisor", {}).setdefault("last_stage_timings_ms", {})["boot_and_provider"] = boot_and_provider_ms
            stamp_supervisor_runtime_state(
                config,
                state,
                planning_state=planning_state,
                heartbeat_at=loop_started_at,
                lifecycle="running",
                loop_started_at=loop_started_at,
            )
        changed = sync_coordination_files(config, state) or changed
        changed = poll_workers(config, state, provider_report=provider_report) or changed
        changed = reconcile_queue_records(config, state) or changed
        changed = prune_event_queue(config, state) or changed
        finish_loop_stage_timing(state, "scan_coordination_and_poll", stage_started)

        stage_started = time.monotonic()
        planning_state = load_discussion_planning_state()
        changed = auto_materialize_discussion_planning(config, planning_state) or changed
        planning_state = load_discussion_planning_state()
        dispatch_suppressed_by_watchdog = watchdog_safe_mode_active(state)
        if dispatch_suppressed_by_watchdog:
            changed = record_watchdog_safe_mode_observed(config, state, loop_started_at) or changed
        elif discussion_planning_is_active(planning_state):
            changed = dispatch_discussion_planning(config, state, planning_state, provider_report=provider_report) or changed
        else:
            # Work dispatch has one purpose: assign an already canonical,
            # ready task. Runtime heuristics never create or reassign tasks.
            changed = dispatch_ready_tasks(config, state, provider_report=provider_report) or changed
        if not dispatch_suppressed_by_watchdog:
            changed = process_queue(config, state, provider_report) or changed
        finish_loop_stage_timing(state, "dispatch_and_queue", stage_started)

        stage_started = time.monotonic()
        changed = poll_workers(config, state, provider_report=provider_report) or changed
        changed = reconcile_queue_records(config, state) or changed
        changed = prune_event_queue(config, state) or changed
        finish_loop_stage_timing(state, "post_dispatch_poll", stage_started)

        stage_started = time.monotonic()
        changed = sync_github_bus(config, state) or changed
        finish_loop_stage_timing(state, "github_bus", stage_started)
        # After the bus sync, so PR state is as fresh as this cycle can make it.
        stage_started = time.monotonic()
        changed = check_branch_drift(config, state) or changed
        finish_loop_stage_timing(state, "branch_drift", stage_started)

        stage_started = time.monotonic()
        trim_worker_history(state, int(config.get("supervisor", {}).get("max_worker_history", 200)))
        trim_seen_events(state, int(config.get("watcher", {}).get("max_seen_events", 2000)))
        changed = prune_orphan_worktrees(config, state) or changed
        changed = maybe_auto_commit_archive(config, state) or changed
        finish_loop_stage_timing(state, "retention_and_maintenance", stage_started)

        loop_finished_at = utc_now()
        stamp_supervisor_runtime_state(
            config,
            state,
            planning_state=planning_state,
            heartbeat_at=loop_finished_at,
            lifecycle="running",
            loop_finished_at=loop_finished_at,
            loop_error=None,
        )
        save_runtime_state(config, state)
        refresh_dashboard_runtime_artifacts(config)
        log_runtime_summary(
            state,
            safe_load_approval_state(config),
            changed=changed,
            quiet=quiet,
            verbose=verbose,
            previous_heartbeat=previous_heartbeat,
            warn_after_seconds=float(config.get("supervisor", {}).get("heartbeat_warn_after_seconds", 10.0)),
            once=once,
        )
        return changed
    except Exception as exc:
        loop_finished_at = utc_now()
        stamp_supervisor_runtime_state(
            config,
            state,
            planning_state=planning_state,
            heartbeat_at=loop_finished_at,
            lifecycle="degraded",
            loop_finished_at=loop_finished_at,
            loop_error=f"{type(exc).__name__}: {exc}",
        )
        save_runtime_state(config, state)
        refresh_dashboard_runtime_artifacts(config)
        raise


def run_supervisor_cycle(
    config: dict[str, Any],
    *,
    watch: bool,
    replay: bool = False,
    quiet: bool = False,
    verbose: bool = False,
) -> bool:
    try:
        return run_once(config, watch=watch, replay=replay, quiet=quiet, verbose=verbose, once=False)
    except Exception as exc:
        console_log(
            f"supervisor cycle failed: {type(exc).__name__}: {exc}; continuing after next poll",
            quiet=quiet,
        )
        return False


def main() -> int:
    global SUPERVISOR_LOG_QUIET
    args = parse_args()
    SUPERVISOR_LOG_QUIET = args.quiet
    config = load_config(args.config)
    if args.clear_provider_pause:
        state = load_runtime_state(config)
        changed = clear_provider_dispatch_pause(config, state, args.clear_provider_pause)
        if changed:
            save_runtime_state(config, state)
            console_log(f"cleared provider dispatch pause: {args.clear_provider_pause}", quiet=args.quiet)
        else:
            console_log(f"no provider dispatch pause found for: {args.clear_provider_pause}", quiet=args.quiet)
        return 0
    if not acquire_singleton_lock(config):
        console_log(
            "another supervisor already holds the singleton lock; exiting without "
            "touching shared state",
            quiet=args.quiet,
        )
        return 0
    terminate_other_supervisors(config)
    atexit.register(clear_supervisor_pid, config)
    write_supervisor_pid(config)
    bootstrap_supervisor_runtime_state(config, lifecycle="starting")
    poll_interval, poll_source = resolve_poll_interval(
        config,
        cli_value=args.poll_interval,
        allow_fast_poll=args.allow_fast_poll,
    )
    console_log(
        f"starting supervisor pid={os.getpid()} poll_interval={poll_interval:.1f}s "
        f"source={poll_source} config={args.config}",
        quiet=args.quiet,
    )
    if args.once:
        run_once(
            config,
            watch=not args.no_watch,
            replay=args.replay,
            quiet=args.quiet,
            verbose=args.verbose,
            once=True,
        )
        return 0
    run_supervisor_cycle(
        config,
        watch=not args.no_watch,
        replay=args.replay,
        quiet=args.quiet,
        verbose=args.verbose,
    )
    while True:
        time.sleep(poll_interval)
        run_supervisor_cycle(
            config,
            watch=not args.no_watch,
            replay=False,
            quiet=args.quiet,
            verbose=args.verbose,
        )


if __name__ == "__main__":
    raise SystemExit(main())
