#!/usr/bin/env python3
from __future__ import annotations
# ruff: noqa: F401,F821,I001,F841

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
    CONFIG_PATH_ENV_VAR,
    PROVIDER_CLI_FAMILY,
    PROVIDER_LAUNCHER_MISSING_PATTERN,
    agent_config_for,
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
    parse_iso_timestamp,
    pid_is_alive,
    provider_launcher_missing_cli,
    relpath,
    resolve_path,
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
from github_reconciliation import (
    CI_FAILURE,
    CI_PENDING,
    CI_UNRESOLVED,
    HEAD_MISMATCH,
    HEAD_UNRESOLVED,
    MISSING_APPROVED_HEAD,
    PR_NOT_MERGED,
    READY,
    evaluate_finalize_gate,
)
import status_transition
import dispatch as dispatch_ops
import worker_lifecycle

import dispatch_engine
import worker_workspace
import worker_failure_policy

_WORKSPACE_HELPER_FUNCTIONS = [
"_atomic_copy_context_file",
"_atomic_replace_context_bytes",
"_atomic_write_context_text",
"_blocking_dirt_entries",
"_classify_worktree_dirt",
"_clear_remote_head_snapshot_cache",
"_clear_worktree_lease_block",
"_create_worker_worktree",
"_describe_dirt_entries",
"_detached_head_is_merged",
"_dirty_worktree_detail",
"_create_worker_worktree_fallback",
"_existing_worktree_for_branch",
"_fetch_authoritative_task_head",
"_file_or_dir_hash",
"_fresh_lease_path",
"_generated_collaboration_guide",
"_generated_worker_task_brief",
"_get_remote_heads_snapshot",
"_git_commit_oid",
"_git_dirty_entries",
"_git_operation_in_progress",
"_git_output",
"_git_worktree_records",
"_is_reusable_dirt_entry",
"_is_skipped_dirty_worktree",
"_is_tracked_in_worktree",
"_is_valid_sha256",
"_lease_status_kind",
"_normalize_materialized_paths",
"_orchestrator_materialized_paths",
"_parse_porcelain_entries",
"_path_matches_any_glob",
"_preserve_and_reset_clean_diverged_worktree",
"_prune_worktree_lease_blocks",
"_publish_unpublished_task_branch",
"_quarantine_and_preserve_dirty_worktree",
"_record_worktree_lease_block",
"_refresh_reused_worker_worktree",
"_restore_reusable_scratch",
"_run_git_network_command",
"_scan_process_paths_in_root",
"_task_brief_context_candidates",
"_task_id_slug",
"_worker_worktree_base_root",
"_worktree_record_branch",
"check_worker_tree_clean",
"materialize_worker_context_files",
"prepare_worker_workspace",
"prune_orphan_worktrees",
"branch_name_is_usable",
"canonical_task_record",
"worker_task_branch",
"worker_task_repo_root",
"worker_task_worktree_path",
"worker_tree_guard_settings",
"worker_worktree_housekeeping_settings",
"worker_worktree_reason_enabled",
"worker_worktree_settings",
]
_FAILURE_HELPER_FUNCTIONS = [
"_deferred_tool_broker_decision",
"_deferred_tool_suggested_rule",
"_deferred_tool_use_receipt",
"_dispatch_pause_bucket",
"_failure_streak_key",
"_load_runtime_marker",
"_lookup_worker_record",
"_parse_iso_utc",
"_provider_guardrail_bucket",
"_task_failure_streak_bucket",
"agent_auto_dispatch_block_reason",
"agent_can_take_task",
"agent_dispatch_disabled",
"agent_dispatch_paused",
"agent_open_task_counts",
"antigravity_pool_fallback_available",
"auto_dispatch_block_is_temporary_capacity",
"classify_worker_failure",
"clear_provider_dispatch_pause",
"clear_task_failure_streak",
"clear_task_failure_streaks_for_task",
"commit_canonical_task_transition",
"correlate_deferred_tool_approval",
"current_provider_dispatch_pause",
"detect_worker_failure",
"disabled_dispatch_agent_keys",
"existing_file_inbox_fallback_run_id",
"expire_provider_dispatch_pauses",
"fence_account_pool_workers",
"first_viable_agent",
"get_agent_reassignment_candidates",
"is_allowed_rate_limit_event",
"is_antigravity_provider",
"is_antigravity_quota_banner",
"is_auth_failure_kind",
"is_captured_orchestrator_record",
"is_claude_provider",
"is_claude_session_limit_banner",
"is_human_gate_agent",
"is_provider_config_failure_kind",
"is_provider_unavailable_failure_kind",
"is_retryable_capacity_failure_kind",
"is_terminal_quota_failure_kind",
"is_tool_command_output_failure_line",
"is_transient_infra_reason",
"is_transient_worker_failure",
"known_agent_display_names",
"manual_pending_inbox_can_auto_redeliver",
"mark_account_pool_cooldown",
"mark_provider_dispatch_paused",
"maybe_reassign_task_after_worker_failure",
"maybe_trigger_retry_or_fallback",
"normalized_mapping_values",
"parse_quota_retry_hint",
"persist_task_reassignment",
"positive_runtime_counts",
"provider_auth_identity_hash",
"provider_dispatch_paused",
"provider_guardrail_settings",
"queue_lease_expiry",
"record_account_pool_canary_success",
"record_task_failure_streak",
"record_worker_runtime_measurement",
"refresh_worker_lease",
"request_for_worker",
"requeue_stale_manual_pending_worker",
"resolve_task_progress_head",
"resume_claude_worker",
"retry_delay_seconds",
"retry_due_workers",
"schedule_queue_event_retry",
"should_pause_dispatch_for_failure_kind",
"sidecar_only_agent_names",
"successful_worker_exit_outcome",
"sync_dispatched_task_status",
"sync_preempted_task_status",
"sync_status_pipeline",
"task_claims_ready_without_handoff",
"task_progress_fingerprint",
"task_progress_snapshot",
"update_worker_runtime_markers",
"worker_dispatch_task_snapshot",
"worker_heartbeat_is_stale",
"worker_is_review_dispatch",
"worker_lease_expiry",
"worker_lease_is_expired",
"worker_reassignment_settings",
"worker_retry_settings",
"worker_runner_succeeded",
"worker_runtime_metrics_bucket",
"worker_runtime_settings",
"worker_supports_approval_resume",
"write_status_snapshot_if_current",
]

for _workspace_name in _WORKSPACE_HELPER_FUNCTIONS:
    if hasattr(worker_workspace, _workspace_name):
        globals()[_workspace_name] = getattr(worker_workspace, _workspace_name)

for _failure_name in _FAILURE_HELPER_FUNCTIONS:
    if hasattr(worker_failure_policy, _failure_name):
        globals()[_failure_name] = getattr(worker_failure_policy, _failure_name)

# Keep the canonical status-write boundary text in this file for existing
# source-based supervisor invariants.
def write_status_snapshot_if_current(config: dict[str, Any], status: dict[str, Any]) -> bool:
    status_path = config_path(config, "status_file")
    lock_path = status_path.with_name(f"{status_path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    expected_revision = status.get(STATUS_WRITE_REVISION_FIELD)

    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            latest = load_json(status_path, default={}) or {}
            actual_revision = latest.get(STATUS_WRITE_REVISION_FIELD)
            if (
                expected_revision is not None
                and actual_revision is not None
                and actual_revision != expected_revision
            ):
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
    return status_transition.sync_status_pipeline(config)


def sync_dispatched_task_status(config: dict[str, Any], event: dict[str, Any]) -> bool:
    return status_transition.sync_dispatched_task_status(config, event)


def sync_preempted_task_status(config: dict[str, Any], worker: dict[str, Any]) -> bool:
    return status_transition.sync_preempted_task_status(config, worker)


def commit_canonical_task_transition(config: dict[str, Any], status: dict[str, Any]) -> bool:
    return write_status_snapshot_if_current(config, status) and sync_status_pipeline(config)


from github_bus import sync_github_bus
from provider_permissions import (
    provider_capabilities as build_provider_capabilities,
)
from provider_permissions import write_provider_capabilities
from provider_runtime import (
    claude_runtime_env,
    codex_config_health,
    configured_provider_binary,
    provider_config,
    provider_config_entry,
    provider_section,
    provider_uses_claude_cli,
)
from rebase_helper import continue_or_skip_empty
from runtime_state import (
    ACTIVE_WORKER_STATUSES,
    active_worker_statuses,
    compact_worker_history,
    enqueue_event,
    load_approval_state,
    load_event_queue,
    load_runtime_state,
    queue_event_record,
    replace_event_queue,
    save_runtime_state,
)
from task_archive import TaskResolver
from watch_events import (
    enqueue_runtime_events_enabled,
    queue_delivery_event,
    run_scan,
    trim_seen_events,
)

# Set once the boot reconciliation pass has run in THIS process. Deliberately a
# process global rather than runtime state: every new supervisor process needs its
# own boot pass, so this must reset on restart and must not persist to state.json.
_BOOT_RECONCILED = False

SIDECAR_READY_PRIORITY_OFFSET = 10
STATUS_WRITE_REVISION_FIELD = "_status_write_revision"
# Max time the antigravity model-rotation will treat a pool as exhausted before
# re-probing it. Kept SHORT because Gemini's 5-hour limit is a rolling window
# that recovers within minutes — a longer cooldown (e.g. trusting the error's
# "Resets in Xh" hint) falsely locks an already-recovered pool for hours.
ROTATION_PROBE_COOLDOWN_SECONDS = 1800
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


parse_runtime_timestamp = parse_iso_timestamp


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
    active_workers = [
        {
            "run_id": run_id,
            "task_id": worker.get("task_id"),
            "agent_id": worker.get("agent_id"),
            "provider": worker.get("provider"),
            "status": worker.get("status"),
        }
        for run_id, worker in workers.items()
        if worker.get("status") in ACTIVE_WORKER_STATUSES
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
    # Deliberately NOT `active_worker_statuses(config)`. That floor exists so a
    # live worker is never mistaken for a finished one -- a safety question. This
    # is not one: occupancy decides whether the execution lane looks busy enough
    # to keep focus off planning, and a manual-inbox record parked with no PID is
    # not executing anything. Widening it here would hold focus on execution for
    # work nobody is doing (see
    # SupervisorRuntimeFocusTests.test_discussion_planning_focus_overrides_execution_draining).
    settings = ready_dispatch_settings(config)
    active_statuses = {str(value) for value in settings.get("active_worker_statuses", [])}
    active_statuses.update({"started", "suspended_approval", "fallback"})
    pending_worker_statuses = {"waiting_approval", "manual_pending", "suspended_approval", "retry_backoff"}
    active_event_ids: set[str] = set()

    for worker in state.get("workers", {}).values():
        status = str(worker.get("status") or "")
        if status not in active_statuses:
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


_LOADED_PROVENANCE: dict[str, str | None] | None = None
_TREE_PROVENANCE_CACHE: tuple[float, dict[str, str | None]] | None = None
#: The tree is polled for drift, not for every heartbeat. Drift matters within
#: minutes, and a `git rev-parse` per heartbeat is a fork per tick for a value
#: that changes when a person moves the checkout.
TREE_PROVENANCE_MAX_AGE_SECONDS = 300.0


def runtime_provenance() -> dict[str, str | None]:
    """What this process actually loaded, so nobody has to infer it.

    A supervisor loads its code once at import and its config once at startup,
    while the checkout underneath it keeps moving. "Is the running supervisor
    the one with the fix?" was answered wrongly twice on 2026-08-20 - once from
    a process start time that turned out to predate a fast-forward by 36
    seconds, once from a `pgrep` that matched the observer's own command line.
    Both were reasoning about the artifact instead of asking the process.

    Recorded once per heartbeat: the commit the loaded code came from, and a
    digest of the config document in force. Neither is a guess.
    """
    global _TREE_PROVENANCE_CACHE
    import subprocess
    import time as _time

    from common import ROOT as _root

    cached = _TREE_PROVENANCE_CACHE
    if cached is not None and (_time.time() - cached[0]) < TREE_PROVENANCE_MAX_AGE_SECONDS:
        return dict(cached[1])

    code_sha: str | None = None
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if proc.returncode == 0:
            candidate = (proc.stdout or "").strip()
            # Only a real object name is evidence. Recording whatever came back
            # would let a failed or substituted probe be cached as provenance,
            # which is worse than recording nothing.
            if len(candidate) == 40 and all(c in "0123456789abcdef" for c in candidate):
                code_sha = candidate
    except (OSError, subprocess.SubprocessError):
        code_sha = None

    config_digest: str | None = None
    config_path = os.environ.get(CONFIG_PATH_ENV_VAR)
    if config_path:
        try:
            config_digest = hashlib.sha256(
                Path(config_path).read_bytes()
            ).hexdigest()[:16]
        except OSError:
            config_digest = None

    result = {"code_sha": code_sha, "config_digest": config_digest}
    _TREE_PROVENANCE_CACHE = (_time.time(), dict(result))
    return result


def loaded_runtime_provenance() -> dict[str, str | None]:
    """What this process loaded, computed once. It cannot change while it runs."""
    global _LOADED_PROVENANCE
    if _LOADED_PROVENANCE is None:
        _LOADED_PROVENANCE = runtime_provenance()
    return dict(_LOADED_PROVENANCE)


def runtime_is_stale(supervisor_state: dict[str, Any]) -> str | None:
    """Why the loaded runtime no longer matches the tree, or None.

    Code is imported once and config is read once at startup, so a checkout
    that moves underneath a running supervisor leaves it executing a version
    that no longer exists on disk with nothing saying so. Comparing the values
    stamped at load against the tree right now turns that into an observation
    instead of an inference.
    """
    loaded_code = str(supervisor_state.get("loaded_code_sha") or "")
    loaded_config = str(supervisor_state.get("loaded_config_digest") or "")
    current = runtime_provenance()
    reasons: list[str] = []
    if loaded_code and current.get("code_sha") and current["code_sha"] != loaded_code:
        reasons.append(
            f"code moved from {loaded_code[:8]} to {str(current['code_sha'])[:8]}"
        )
    if (
        loaded_config
        and current.get("config_digest")
        and current["config_digest"] != loaded_config
    ):
        reasons.append("config document changed")
    if not reasons:
        return None
    return (
        "Running supervisor no longer matches the tree it was started from: "
        + "; ".join(reasons)
        + ". It will keep executing the loaded version until it is restarted."
    )


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
    provenance = loaded_runtime_provenance()
    supervisor_state.update(provenance)
    # Stamped once, at the first heartbeat of this pid: what this process
    # actually loaded. Everything after compares against it rather than
    # overwriting it, which is what makes drift observable at all.
    if previous_pid != current_pid or not supervisor_state.get("loaded_code_sha"):
        supervisor_state["loaded_code_sha"] = provenance.get("code_sha")
        supervisor_state["loaded_config_digest"] = provenance.get("config_digest")
        supervisor_state.pop("runtime_stale_reported", None)

    stale_reason = runtime_is_stale(supervisor_state)
    if stale_reason:
        if supervisor_state.get("runtime_stale_reported") != stale_reason:
            supervisor_state["runtime_stale_reported"] = stale_reason
            write_activity_log(
                config, {"type": "supervisor_runtime_stale", "message": stale_reason}
            )
    else:
        supervisor_state.pop("runtime_stale_reported", None)
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
    provider = provider_config(config, provider_id)
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


def provider_runtime_config_block_reason(config: dict[str, Any], provider: str | None) -> str | None:
    provider_key, provider_cfg = provider_config_entry(config, provider)
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
    provider_cfg = provider_config(config, provider)
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
    active_statuses = active_worker_statuses(config)
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
    entries: list[tuple[int, int, str]] = []
    for index, (agent_id, agent) in enumerate((config.get("agents", {}) or {}).items()):
        normalized = normalize_agent_id(agent_id)
        if not normalized or agent_is_dispatch_slot(agent):
            continue
        entries.append((agent_dispatch_preference_rank(config, normalized), index, normalized))
    entries.sort(key=lambda item: (item[0], item[1]))
    return [normalized for _, _, normalized in entries]


def agent_dispatch_preference_rank(config: dict[str, Any], agent_id: str | None) -> int:
    normalized = normalize_agent_id(agent_id or "")
    if not normalized:
        return 99
    agent = (config.get("agents", {}) or {}).get(normalized, {})
    provider = str((agent or {}).get("provider") or "").strip().lower()
    if provider.startswith("antigravity"):
        return 0
    if provider.startswith("claude"):
        return 1
    if provider.startswith("codex"):
        return 2
    return 99


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
        delivery_mode=provider_config(
            config, agent.get("provider", agent["id"])
        ).get("delivery_mode", agent.get("adapter", "file_inbox")),
        message=event["message"],
        task_id=event.get("task_id"),
        reason=event.get("reason"),
        context_files=context_files,
        target_files=event.get("target_files", []),
        metadata=metadata,
    )


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














def worker_workspace_task_id(request: DeliveryRequest) -> str | None:
    metadata_task_id = str(request.metadata.get("workspace_task_id") or "").strip()
    task_id = metadata_task_id or str(request.task_id or "").strip()
    return task_id or None








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




# Orchestrator-managed per-task scratch and context files that a worker routinely
# dirties or seeds inside its worktree. The supervisor regenerates or seeds these on
# dispatch, so a reused worktree whose ONLY dirt is here is safe to restore-and-reuse.
# Ephemeral context must not block dispatch or cause permanent lease failure.
_REUSABLE_DIRTY_PREFIXES = (
    ".orchestrator/task-briefs/",
    ".orchestrator/reviews/",
    # Orchestrator-owned reference material. `worker_tree_guard.blocking_globs`
    # already forbids a worker from modifying `.orchestrator/skills/**`, so an
    # untracked copy of it is never deliverable work. A repository that does not
    # track these files gets one per worker that follows its brief, and treating
    # them as real dirt blocked the lease permanently: DPF-GOV-001 was refused on
    # a worktree already sitting at its exact reviewer-approved head.
    ".orchestrator/skills/",
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















_REMOTE_HEAD_SNAPSHOTS: dict[tuple[str, str], tuple[float, dict[str, str], float]] = {}










WORKTREE_LEASE_BLOCK_RETENTION_HOURS = 72






























# Canonical worker context that lives in the supervisor root but is gitignored, so a
# fresh or reused worktree never contains it. Always safe to (re)seed as untracked
# copies: the reuse-dirt guard runs `git status --untracked-files=no`, so untracked
# seeds never block re-dispatch.
_SEEDABLE_UNTRACKED_CONTEXT = ("ai-status.json", "current-work.md", "ai-activity-log.jsonl")
















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
    return worker_lifecycle.process_queue(
        config,
        state,
        provider_report,
        agent_ids_override=agent_ids_override,
        agent_override=agent_override,
    )


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
    queue_payload = enqueue_event(config, queue_payload)
    write_activity_log(
        config,
        {
            "type": "planning_wake_queued",
            "task_id": queue_payload["task_id"],
            "target_agent": display_name_for(config, agent["id"]),
            "delivery_mode": provider_config(
                config, agent.get("provider", agent["id"])
            ).get("delivery_mode", agent.get("adapter", "file_inbox")),
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
                # The whole log is re-read on every poll, so an OLD deferral line
                # is seen again on each pass.  Only a worker that is still in an
                # active state may be moved into waiting_approval by it: a parent
                # that has since been retried, handed off, or settled would
                # otherwise be dragged back out of its own terminal disposition
                # -- a `retry_backoff` record flipped this way stops matching
                # `retry_due_workers` and its retry never fires again.
                if str(worker.get("status") or "") in ACTIVE_WORKER_STATUSES:
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










def _isoformat_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")




WORKER_RUNTIME_METRIC_COUNTERS = (
    "workers_started",
    "queue_leases_started",
    "marker_updates",
    "lease_refreshes",
    "missing_process_workers_failed",
    "expired_lease_workers_failed",
    "supersede_deferrals",
    "started_queue_records_requeued",
    "started_queue_records_failed",
    "stale_queue_records_completed",
    "capacity_pending_queue_events",
)
























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






























READY_WITHOUT_HANDOFF_PATTERNS = (
    re.compile(r"\bready\s+(?:for|to)\s+(?:independent\s+)?(?:review|re-review)\b", re.IGNORECASE),
    re.compile(r"\bawaiting\s+(?:independent\s+)?(?:review|re-review)\b", re.IGNORECASE),
    re.compile(r"\bwaiting\s+for\s+(?:independent\s+)?(?:review|re-review)\b", re.IGNORECASE),
    re.compile(r"\bpending\s+(?:independent\s+)?(?:review|re-review)\b", re.IGNORECASE),
    re.compile(r"(?:等待|待)(?:獨立)?(?:審查|審核|複核|review)"),
    re.compile(r"(?:已可|可以|準備)(?:送|進入)?(?:獨立)?(?:審查|審核|複核|review)"),
)


























AGENT_OPEN_TASK_STATUSES = ("todo", "in_progress", "review", "review_approved", "blocked")







































def schedule_worker_retry(config: dict[str, Any], worker: dict[str, Any], reason: str) -> None:
    delay = retry_delay_seconds(config, worker)
    retry_at = datetime.fromtimestamp(datetime.now(UTC).timestamp() + delay, tz=UTC)
    worker["status"] = "retry_backoff"
    worker["retry_count"] = int(worker.get("retry_count", 0)) + 1
    worker["next_retry_at"] = retry_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    worker["last_error"] = reason
    worker["last_event_at"] = utc_now()








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




DEFERRED_TOOL_RISK_CLASS = "claude_deferred_tool"












def poll_workers(config: dict[str, Any], state: dict[str, Any], provider_report: dict[str, Any] | None = None) -> bool:
    return worker_lifecycle.poll_workers(config, state, provider_report=provider_report)








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
    active_statuses = active_worker_statuses(config)
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
    active_statuses = active_worker_statuses(config)
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
        if provider_uses_claude_cli(config, worker.get("provider")):
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
                record = queue_event_record(state, worker["queue_event_id"])
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
    elif str(task.get("status") or "").strip().lower() == "blocked" and not waiting_for:
        issues.append("waiting_for_missing")
    return issues


def reassert_approved_review_gate_if_due(
    config: dict[str, Any],
    task: dict[str, Any],
    *,
    now_ts: float | None = None,
) -> bool:
    return status_transition.reassert_approved_review_gate_if_due(config, task, now_ts=now_ts)


def repair_open_task_metadata(config: dict[str, Any], status: dict[str, Any]) -> bool:
    return status_transition.repair_open_task_metadata(config, status)


def repair_unsubmitted_review_tasks(config: dict[str, Any], status: dict[str, Any]) -> bool:
    """Remove legacy false-review states before reviewer dispatch.

    Human data/approval gates return to blocked.  Executable tasks return to
    their owner so task_finalize can publish and atomically submit the PR.
    """
    return status_transition.repair_unsubmitted_review_tasks(config, status)


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
    waiting_for_missing = "waiting_for_missing" in issues
    waiting_for_unavailable = any(issue.startswith("waiting_for_unavailable:") for issue in issues)
    if not assignment_issues and not waiting_for_unavailable and not waiting_for_missing:
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
    elif waiting_for_missing:
        replacement_waiting_for = new_owner
        if (
            replacement_waiting_for
            and not task_actor_assignment_block_reason(config, state, task, replacement_waiting_for)
        ):
            new_waiting_for = replacement_waiting_for
            changes.append(f"waiting_for missing -> {new_waiting_for}")

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
    return status_transition.normalized_business_priority(value, default=default)


def blocked_task_prose_context(task: dict[str, Any]) -> str:
    """Free-text blocker context with declared task IDs removed.

    The gate keywords below are matched as substrings, so any task ID that
    happens to contain one poisons every task that depends on it: a task
    waiting on ``DPF-KRN-DATASET-001`` reads as an external-data gate purely
    because its dependency list mentions "dataset". Dependency identity is
    already carried structurally in ``depends_on``; strip those tokens before
    keyword matching so only genuine prose is classified.
    """
    identifiers = [str(task.get("id") or "")]
    identifiers.extend(str(dep) for dep in (task.get("depends_on") or []))
    context = " ".join(
        str(task.get(key) or "")
        for key in ("next", "waiting_for", "blocker", "blocked_by", "failure_reason", "last_failure_reason", "push_status")
    ).casefold()
    for identifier in identifiers:
        token = identifier.strip().casefold()
        if token:
            context = context.replace(token, " ")
    return context


def blocked_task_auto_recovery_eligible(
    config: dict[str, Any],
    task: dict[str, Any],
    task_map: dict[str, dict[str, Any]] | None = None,
) -> bool:
    """Whether a blocked task is a released gate, not a live one.

    Human, external-data, deployment and operator gates remain fail-closed.
    Two distinct blocks are recoverable: a dependency gate whose dependencies
    have since completed, and stale routing/provider failure state.

    The dependency case must be decided from ``depends_on`` via the resolver,
    never from the blocker prose. A catalog registers a staged wave as
    ``blocked`` with "waiting for dependencies: X" and nothing rewrites that
    sentence when X completes, so a prose-only rule leaves every dependent
    task blocked forever behind a dependency the resolver already reports as
    satisfied -- a deadlock only a human can clear.
    """
    if str(task.get("status") or "").strip().lower() != "blocked":
        return False
    if task_is_human_gate(task) or task_is_sidecar(task) or bool(task.get("non_dispatchable")):
        return False
    declared_dependencies = [str(dep).strip() for dep in (task.get("depends_on") or []) if str(dep).strip()]
    dependency_gate_released = False
    if task_map is not None:
        done_statuses = {
            str(value).lower()
            for value in ready_dispatch_settings(config).get("dependency_done_statuses", ["done"])
        }
        if not dependencies_satisfied(task, task_map, done_statuses):
            return False
        dependency_gate_released = bool(declared_dependencies)
    context = blocked_task_prose_context(task)
    hard_gate_markers = (
        "human/ops", "human gate", "pending_human", "authoritative", "dataset", "attestation",
        "external-data", "mlflow", "deploy dev", "live-e2e", "production alias", "merge queue",
        "operator intervention", "manual approval", "requires operator",
    )
    if any(marker in context for marker in hard_gate_markers):
        return False
    if dependency_gate_released:
        return True
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




def finalize_queue_event_record(config: dict[str, Any], state: dict[str, Any], worker: dict[str, Any], status: str, error: str | None = None) -> None:
    queue_event_id = worker.get("queue_event_id")
    if not queue_event_id:
        return
    active_statuses = active_worker_statuses(config)
    for item in state.get("workers", {}).values():
        if item.get("run_id") == worker.get("run_id"):
            continue
        if item.get("queue_event_id") == queue_event_id and item.get("status") in active_statuses:
            return
    record = queue_event_record(state, queue_event_id)
    record["status"] = status
    record["processed_at"] = utc_now()
    record["lease_released_at"] = record["processed_at"]
    if worker.get("run_id"):
        record["lease_owner"] = worker.get("run_id")
    if error:
        record["error"] = error


def prune_event_queue(config: dict[str, Any], state: dict[str, Any]) -> bool:
    events = load_event_queue(config)
    if not events:
        return False
    task_map = task_index_from_status(config, load_status(config))
    active_statuses = active_worker_statuses(config)
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
            completed = queue_event_record(state, event_id)
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
    replace_event_queue(config, original_events=events, retained_events=kept)
    return True


def task_index_from_status(config: dict[str, Any], status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return dispatch_ops.task_index_from_status(config, status)


def current_dispatch_event_key(config: dict[str, Any], event: dict[str, Any], task_map: dict[str, dict[str, Any]]) -> str | None:
    return dispatch_ops.current_dispatch_event_key(config, event, task_map)

def dispatch_priority_for_task(
    config: dict[str, Any],
    task: dict[str, Any],
    agent_name: str,
    *,
    task_map: dict[str, dict[str, Any]] | None = None,
    dependencies_done_statuses: set[str] | None = None,
) -> int | None:
    return dispatch_ops.dispatch_priority_for_task(
        config,
        task,
        agent_name,
        task_map=task_map,
        dependencies_done_statuses=dependencies_done_statuses,
    )



def agent_dispatch_loads(config: dict[str, Any], state: dict[str, Any], active_statuses: set[str]) -> dict[str, list[int]]:
    return dispatch_ops.agent_dispatch_loads(config, state, active_statuses)


def reassign_unavailable_reviewers(
    config: dict[str, Any],
    state: dict[str, Any],
    status: dict[str, Any],
    *,
    provider_report: dict[str, Any] | None = None,
) -> bool:
    return dispatch_ops.reassign_unavailable_reviewers(config, state, status, provider_report=provider_report)


def is_sidecar_review_of_current_parent(
    candidate_task: dict[str, Any],
    current_task: dict[str, Any] | None,
    *,
    agent_name: str,
    review_statuses: set[str],
    owner_field: str,
    reviewer_field: str,
) -> bool:
    return dispatch_ops.is_sidecar_review_of_current_parent(
        candidate_task,
        current_task,
        agent_name=agent_name,
        review_statuses=review_statuses,
        owner_field=owner_field,
        reviewer_field=reviewer_field,
    )


def worker_logical_dispatch_agent_id(config: dict[str, Any], worker: dict[str, Any]) -> str:
    return dispatch_ops.worker_logical_dispatch_agent_id(config, worker)


def higher_priority_ready_task_exists(
    config: dict[str, Any],
    worker: dict[str, Any],
    task_map: dict[str, dict[str, Any]],
    state: dict[str, Any] | None = None,
) -> bool:
    return dispatch_ops.higher_priority_ready_task_exists(config, worker, task_map, state=state)


def worker_matches_current_assignment(
    config: dict[str, Any],
    worker: dict[str, Any],
    task_map: dict[str, dict[str, Any]],
) -> bool:
    return dispatch_ops.worker_matches_current_assignment(config, worker, task_map)


def stale_dispatch_skip_message(
    config: dict[str, Any],
    event: dict[str, Any],
    task_map: dict[str, dict[str, Any]],
) -> str | None:
    return dispatch_ops.stale_dispatch_skip_message(config, event, task_map)


def ready_dispatch_signature(task: dict[str, Any], reason: str, task_map: dict[str, dict[str, Any]]) -> str:
    return dispatch_ops.ready_dispatch_signature(task, reason, task_map)


def worktree_block_still_matches_dispatch(
    state: dict[str, Any],
    task: dict[str, Any],
    reason: str,
    task_map: dict[str, dict[str, Any]],
    *,
    retry_after_seconds: float | None = None,
) -> bool:
    if retry_after_seconds is None:
        return dispatch_ops.worktree_block_still_matches_dispatch(state, task, reason, task_map)
    return dispatch_ops.worktree_block_still_matches_dispatch(
        state,
        task,
        reason,
        task_map,
        retry_after_seconds=retry_after_seconds,
    )


def build_dispatch_event(task: dict[str, Any], target_agent: str, reason: str, task_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return dispatch_ops.build_dispatch_event(task, target_agent, reason, task_map)


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
    return status_transition.requeue_task_for_ci_repair(
        config,
        status,
        task,
        message=message,
        clear_approval=clear_approval,
        requeued_head=requeued_head,
        now_ts=now_ts,
    )


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
    return dispatch_ops.dispatch_discussion_planning(config, state, planning_state=planning_state, provider_report=provider_report)


def dispatch_ready_tasks(
    config: dict[str, Any],
    state: dict[str, Any],
    provider_report: dict[str, Any] | None = None,
    agent_ids_override: list[str] | None = None,
    max_dispatches_override: int | None = None,
) -> bool:
    return dispatch_ops.dispatch_ready_tasks(
        config,
        state,
        provider_report=provider_report,
        agent_ids_override=agent_ids_override,
        max_dispatches_override=max_dispatches_override,
    )


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
        # Boot reconciliation, as the name says, settles what the PREVIOUS
        # supervisor process left behind. Running it on every loop made it a
        # second, permanent worker-settlement path that always ran BEFORE
        # poll_workers -- and it deliberately has no retry/fallback branch, so it
        # hard-failed every dead `running`/`stalled` worker before poll_workers
        # could offer one. That silently disabled the whole `worker_retry` config
        # (max_attempts, backoff_schedule_seconds, fallback_mode) for the most
        # common failure there is.
        #
        # The two paths are intentionally NOT identical -- see
        # test_boot_reconciliation_correlates_flushed_receipt_before_missing_process_failure
        # versus its poll-path sibling, which pin different outcomes on purpose --
        # so this is a scheduling fix, not a de-duplication.
        global _BOOT_RECONCILED
        if not _BOOT_RECONCILED:
            changed = reconcile_runtime_on_boot(config, state) or changed
            _BOOT_RECONCILED = True
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
            changed = run_scan(config, state, replay=replay) or changed
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


def install_termination_logging(config: dict[str, Any]) -> None:
    """Make the supervisor announce its own death.

    Without this the process has no way to record being terminated. `atexit` does
    NOT run on SIGTERM -- Python's default disposition kills the process outright --
    and never on SIGKILL, so a signalled supervisor simply stops mid-loop and the
    log's last line is an ordinary tick. Four terminations inside one hour were
    diagnosable only by elimination (not OOM, no traceback, no clean-exit message,
    no `supervisor_replaced` event) because nothing recorded what ended them.

    Raising SystemExit unwinds the main thread normally, which also lets the
    registered atexit hook clear `supervisor.pid` instead of leaving a stale one
    for the watchdog to read as a live supervisor.

    SIGKILL cannot be caught; that case stays silent by construction.
    """

    def _handle(signum: int, _frame: Any) -> None:
        try:
            name = signal.Signals(signum).name
        except ValueError:  # pragma: no cover - platform-specific signal numbers
            name = str(signum)
        message = f"Supervisor received {name}; shutting down."
        # Both sinks are best-effort: a handler that raises on its way out would
        # replace the diagnostic it exists to record.
        try:
            console_log(f"{message} pid={os.getpid()}", quiet=SUPERVISOR_LOG_QUIET)
        except Exception:
            pass
        try:
            write_activity_log(
                config,
                {
                    "type": "supervisor_terminated",
                    "message": message,
                    "signal": name,
                    "pid": os.getpid(),
                },
            )
        except Exception:
            pass
        raise SystemExit(128 + signum)

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            signal.signal(signum, _handle)
        except (OSError, ValueError):  # pragma: no cover - not installable everywhere
            continue


def main() -> int:
    global SUPERVISOR_LOG_QUIET
    args = parse_args()
    SUPERVISOR_LOG_QUIET = args.quiet
    selected_config_path = resolve_path(args.config)
    if selected_config_path is None:
        raise RuntimeError(f"Unable to resolve orchestrator config path: {args.config}")
    os.environ[CONFIG_PATH_ENV_VAR] = str(selected_config_path)
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
    install_termination_logging(config)
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
