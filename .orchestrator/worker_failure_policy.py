from __future__ import annotations

"""Worker failure policy helpers extracted from legacy supervisor."""
# ruff: noqa: F401,F821,F841,I001

from datetime import UTC, datetime
from typing import Any

from adapters.base import DeliveryRequest
from common import (
    claude_model_selection_args,
    parse_iso_timestamp,
    spawn_background_process,
)
from provider_runtime import configured_provider_binary
import status_transition


def _supervisor_module():
    import supervisor
    return supervisor


def _sync_supervisor_scope() -> None:
    sv = _supervisor_module()
    # Keep module-local definitions authoritative and avoid replacing them on sync.
    excluded = {
        "__name__", "__doc__", "__package__", "__loader__", "__spec__", "__file__", "__cached__", "__builtins__",
        "Any", "_supervisor_module", "_sync_supervisor_scope", "_entrypoint", "_sync_scope_guard", "status_transition",
        "claude_model_selection_args", "configured_provider_binary", "spawn_background_process",
    }
    module_exports = {
        "__all__",
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





@_entrypoint
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

@_entrypoint
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

@_entrypoint
def is_allowed_rate_limit_event(payload: dict[str, Any]) -> bool:
    if payload.get("type") != "rate_limit_event":
        return False
    info = payload.get("rate_limit_info")
    if not isinstance(info, dict):
        return False
    return str(info.get("status") or "").strip().lower() == "allowed"

@_entrypoint
def is_tool_command_output_failure_line(lines: list[str], idx: int) -> bool:
    for prev_idx in range(idx - 1, max(idx - 5, -1), -1):
        previous = lines[prev_idx].strip()
        if not previous:
            continue
        return bool(COMMAND_OUTPUT_EXIT_LINE_PATTERN.search(previous))
    return False

@_entrypoint
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

@_entrypoint
def is_antigravity_quota_banner(config: dict[str, Any] | None, provider: str | None, reason: str | None) -> bool:
    """True only for agy's real per-account quota banner on an agy provider."""
    if not reason or not is_antigravity_provider(config, provider):
        return False
    return bool(AGY_QUOTA_SIGNATURE_PATTERN.search(str(reason)))

@_entrypoint
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

@_entrypoint
def is_claude_session_limit_banner(config: dict[str, Any] | None, provider: str | None, reason: str | None) -> bool:
    """True only for the Claude CLI session-limit banner on a Claude provider."""
    if not reason or not is_claude_provider(config, provider):
        return False
    return bool(CLAUDE_SESSION_LIMIT_PATTERN.search(str(reason)))

@_entrypoint
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

# Pure parser: no supervisor globals to sync, so it needs no @_entrypoint wrapper.
_parse_iso_utc = parse_iso_timestamp

@_entrypoint
def worker_runtime_settings(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("worker_runtime")
    settings = dict(raw if isinstance(raw, dict) else {})
    supervisor_settings = config.get("supervisor", {}) if isinstance(config.get("supervisor"), dict) else {}
    settings.setdefault("worker_lease_seconds", supervisor_settings.get("worker_lease_seconds", 1800))
    settings.setdefault("queue_lease_seconds", supervisor_settings.get("queue_lease_seconds", 1800))
    settings.setdefault("heartbeat_stale_seconds", supervisor_settings.get("heartbeat_stale_seconds", 300))
    settings.setdefault("heartbeat_grace_seconds", supervisor_settings.get("heartbeat_grace_seconds", 60))
    settings.setdefault("runner_heartbeat_interval_seconds", 15)
    # Grace window before a still-alive, freshly heart-beating worker is
    # SIGTERM-superseded once its task assignment moves on. A worker that has
    # just advanced its own task (owner -> review, reviewer -> review_approved)
    # keeps a fresh heartbeat while it tears down the CLI/MCP session and
    # flushes its final canonical status write. Killing it inside that window
    # truncates the un-landed lifecycle write and forces a wasteful redispatch,
    # so we let it exit on its own until this many seconds elapse.
    settings.setdefault("supersede_grace_seconds", supervisor_settings.get("supersede_grace_seconds", 120))
    return settings

@_entrypoint
def worker_runtime_metrics_bucket(state: dict[str, Any]) -> dict[str, Any]:
    bucket = state.setdefault("worker_runtime_metrics", {})
    bucket.setdefault("version", 1)
    bucket.setdefault("updated_at", None)
    totals = bucket.setdefault("totals", {})
    for key in WORKER_RUNTIME_METRIC_COUNTERS:
        totals.setdefault(key, 0)
    bucket.setdefault("last_measurements", {})
    return bucket

@_entrypoint
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

@_entrypoint
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

@_entrypoint
def worker_lease_expiry(config: dict[str, Any], now: datetime | None = None) -> str:
    settings = worker_runtime_settings(config)
    now_dt = now or datetime.now(UTC)
    return _isoformat_utc(now_dt + timedelta(seconds=max(60, int(settings.get("worker_lease_seconds", 1800)))))

@_entrypoint
def queue_lease_expiry(config: dict[str, Any], now: datetime | None = None) -> str:
    settings = worker_runtime_settings(config)
    now_dt = now or datetime.now(UTC)
    return _isoformat_utc(now_dt + timedelta(seconds=max(60, int(settings.get("queue_lease_seconds", 1800)))))

@_entrypoint
def refresh_worker_lease(config: dict[str, Any], worker: dict[str, Any], now: datetime | None = None) -> None:
    now_dt = now or datetime.now(UTC)
    worker.setdefault("lease_acquired_at", _isoformat_utc(now_dt))
    worker["lease_expires_at"] = worker_lease_expiry(config, now_dt)

@_entrypoint
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

@_entrypoint
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
        process_activity_at = str(payload.get("last_process_activity_at") or "").strip()
        if process_activity_at and process_activity_at > str(worker.get("last_process_activity_at") or ""):
            worker["last_process_activity_at"] = process_activity_at
            changed = True
        process_activity = payload.get("process_activity")
        if isinstance(process_activity, dict) and worker.get("process_activity") != process_activity:
            worker["process_activity"] = process_activity
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

@_entrypoint
def worker_runner_succeeded(worker: dict[str, Any]) -> bool:
    runner_status = str(worker.get("runner_status") or "").strip().lower()
    if runner_status not in {"completed", "success", "succeeded"}:
        return False
    try:
        exit_code = int(worker.get("exit_code", 0))
    except (TypeError, ValueError):
        return False
    return exit_code == 0 and not worker.get("runner_signal")

@_entrypoint
def worker_heartbeat_is_stale(config: dict[str, Any], worker: dict[str, Any], now: datetime | None = None) -> bool:
    settings = worker_runtime_settings(config)
    heartbeat_dt = _parse_iso_utc(str(worker.get("last_heartbeat_at") or ""))
    if heartbeat_dt is None:
        return True
    now_dt = now or datetime.now(UTC)
    stale_after = int(settings.get("heartbeat_stale_seconds", 300)) + int(settings.get("heartbeat_grace_seconds", 60))
    return (now_dt - heartbeat_dt.astimezone(UTC)).total_seconds() > max(60, stale_after)

@_entrypoint
def worker_lease_is_expired(config: dict[str, Any], worker: dict[str, Any], now: datetime | None = None) -> bool:
    lease_expires_at = _parse_iso_utc(str(worker.get("lease_expires_at") or ""))
    if lease_expires_at is None:
        return False
    now_dt = now or datetime.now(UTC)
    return now_dt > lease_expires_at.astimezone(UTC) and worker_heartbeat_is_stale(config, worker, now_dt)

@_entrypoint
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

@_entrypoint
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

@_entrypoint
def _provider_guardrail_bucket(state: dict[str, Any]) -> dict[str, Any]:
    bucket = state.setdefault("provider_guardrails", {})
    bucket.setdefault("dispatch_pauses", {})
    bucket.setdefault("task_failure_streaks", {})
    return bucket

@_entrypoint
def _dispatch_pause_bucket(state: dict[str, Any]) -> dict[str, Any]:
    return _provider_guardrail_bucket(state).setdefault("dispatch_pauses", {})

@_entrypoint
def _task_failure_streak_bucket(state: dict[str, Any]) -> dict[str, Any]:
    return _provider_guardrail_bucket(state).setdefault("task_failure_streaks", {})

@_entrypoint
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

@_entrypoint
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

@_entrypoint
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
    provider_cfg = provider_config(config, provider_id) or provider_config(config, "codex")
    codex_profile = provider_section(config, provider_id=provider_id, section="codex", default="codex")
    configured_home = str(codex_profile.get("codex_home") or provider_cfg.get("codex_home") or "").strip()
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

@_entrypoint
def _failure_streak_key(task_id: str, provider: str) -> str:
    return f"{task_id}:{provider}"

@_entrypoint
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

@_entrypoint
def provider_dispatch_paused(config: dict[str, Any], state: dict[str, Any], provider: str | None) -> bool:
    return current_provider_dispatch_pause(state, provider, config) is not None

@_entrypoint
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

@_entrypoint
def is_terminal_quota_failure_kind(kind: str | None) -> bool:
    return str(kind or "").strip().lower() == "quota_terminal"

@_entrypoint
def is_retryable_capacity_failure_kind(kind: str | None) -> bool:
    return str(kind or "").strip().lower() in {"capacity", "capacity_retryable"}

@_entrypoint
def is_auth_failure_kind(kind: str | None) -> bool:
    return str(kind or "").strip().lower() == "auth"

@_entrypoint
def is_provider_config_failure_kind(kind: str | None) -> bool:
    return str(kind or "").strip().lower() == "provider_config"

@_entrypoint
def is_provider_unavailable_failure_kind(kind: str | None) -> bool:
    return str(kind or "").strip().lower() == "provider_unavailable"

@_entrypoint
def should_pause_dispatch_for_failure_kind(kind: str | None) -> bool:
    return (
        is_terminal_quota_failure_kind(kind)
        or is_retryable_capacity_failure_kind(kind)
        or is_auth_failure_kind(kind)
        or is_provider_config_failure_kind(kind)
        or is_provider_unavailable_failure_kind(kind)
    )

@_entrypoint
def is_transient_infra_reason(reason: str | None) -> bool:
    if not reason:
        return False
    low = str(reason).lower()
    return any(marker in low for marker in _TRANSIENT_INFRA_REASON_MARKERS)

@_entrypoint
def _lookup_worker_record(state: dict[str, Any], worker_run_id: str | None) -> dict[str, Any] | None:
    run_id = str(worker_run_id or "").strip()
    if not run_id:
        return None
    worker = (state.get("workers") or {}).get(run_id)
    return worker if isinstance(worker, dict) else None

@_entrypoint
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

@_entrypoint
def antigravity_pool_fallback_available(
    config: dict[str, Any],
    provider: str | None,
) -> bool:
    """Keep the logical owner while another Antigravity model pool can run."""
    provider_id = normalize_agent_id(provider or "")
    return model_rotation.fallback_pool_available(config, provider_id)

@_entrypoint
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

@_entrypoint
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

@_entrypoint
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

@_entrypoint
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

@_entrypoint
def clear_task_failure_streaks_for_task(state: dict[str, Any], task_id: str | None) -> None:
    task_id = str(task_id or "").strip()
    if not task_id:
        return
    bucket = _task_failure_streak_bucket(state)
    for key in [item for item in bucket if item.startswith(f"{task_id}:")]:
        bucket.pop(key, None)

@_entrypoint
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

@_entrypoint
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

@_entrypoint
def task_progress_fingerprint(task: dict[str, Any] | None) -> str:
    return json.dumps(task_progress_snapshot(task), sort_keys=True, ensure_ascii=True)

@_entrypoint
def worker_dispatch_task_snapshot(worker: dict[str, Any]) -> dict[str, Any]:
    request = worker.get("request_snapshot")
    if not isinstance(request, dict):
        return {}
    metadata = request.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    task = metadata.get("task")
    return dict(task) if isinstance(task, dict) else {}

@_entrypoint
def worker_is_review_dispatch(worker: dict[str, Any]) -> bool:
    request = worker.get("request_snapshot")
    reason = str(request.get("reason") or "") if isinstance(request, dict) else ""
    normalized = reason.strip().lower()
    return normalized == REASON_REVIEW_READY or normalized == "status:review"

@_entrypoint
def task_claims_ready_without_handoff(task: dict[str, Any]) -> bool:
    if str(task.get("status") or "").strip().lower() == "review":
        return False
    next_step = str(task.get("next") or "").strip()
    return bool(next_step and any(pattern.search(next_step) for pattern in READY_WITHOUT_HANDOFF_PATTERNS))

@_entrypoint
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

@_entrypoint
def worker_retry_settings(config: dict[str, Any], provider: str | None) -> dict[str, Any]:
    retry = dict(config.get("worker_retry", {}) or {})
    if provider:
        retry.update(provider_config(config, provider).get("retry", {}) or {})
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

@_entrypoint
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

@_entrypoint
def is_human_gate_agent(agent_name: str | None) -> bool:
    name = str(agent_name or "").strip().casefold()
    if not name:
        return False
    return name in {"human/ops", "human", "ops"} or name.startswith("human/")

@_entrypoint
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

@_entrypoint
def normalized_mapping_values(mapping: dict[str, Any], key: str) -> list[str]:
    target = (key or "").strip().casefold()
    for candidate_key, values in mapping.items():
        if str(candidate_key).strip().casefold() != target:
            continue
        return [str(value).strip() for value in list(values or []) if str(value).strip()]
    return []

@_entrypoint
def known_agent_display_names(config: dict[str, Any]) -> set[str]:
    return {
        str(agent.get("display_name") or agent.get("name") or agent_id).strip()
        for agent_id, agent in (config.get("agents", {}) or {}).items()
        if str(agent.get("display_name") or agent.get("name") or agent_id).strip()
    }

@_entrypoint
def sidecar_only_agent_names(config: dict[str, Any]) -> set[str]:
    return {
        str(agent_name).strip()
        for agent_name in ready_dispatch_settings(config).get("sidecar_only_agents", []) or []
        if str(agent_name).strip()
    }

@_entrypoint
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

@_entrypoint
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
            prov_cfg = provider_config(config, provider)
            if isinstance(prov_cfg, dict) and (prov_cfg.get("enabled") is False or prov_cfg.get("disabled") is True):
                return True
        return bool(
            (display and display.casefold() in keys)
            or (provider and provider.casefold() in keys)
            or (provider and normalize_agent_id(provider).casefold() in keys)
        )
    return False

@_entrypoint
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

@_entrypoint
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

@_entrypoint
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
            block_reason = agent_auto_dispatch_block_reason(
                config, state, name, provider_report=provider_report
            )
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

@_entrypoint
def agent_auto_dispatch_block_reason(
    config: dict[str, Any],
    state: dict[str, Any] | None,
    agent_id: str | None,
    provider_report: dict[str, Any] | None = None,
) -> str | None:
    """Return a human-readable reason when an agent must not receive auto dispatch."""
    normalized_agent = normalize_agent_id(agent_id or "")
    if not normalized_agent:
        return "missing target agent"
    dispatch_paused = (
        agent_dispatch_paused(config, state, normalized_agent)
        if state is not None
        else agent_dispatch_disabled(config, normalized_agent)
    )
    if dispatch_paused:
        return f"dispatch is paused or disabled for {display_name_for(config, normalized_agent) or normalized_agent}"
    if state is not None:
        pool_block_reason = account_pool_dispatch_block_reason(config, normalized_agent, runtime_state=state)
        if pool_block_reason:
            return pool_block_reason
    settings = ready_dispatch_settings(config)
    active_statuses = active_worker_statuses(config)
    if state is not None:
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

    if state is not None and settings.get("worker_os_duplicate_guard", True):
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

@_entrypoint
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

@_entrypoint
def write_status_snapshot_if_current(config: dict[str, Any], status: dict[str, Any]) -> bool:
    # Keep this direct call-text footprint for existing line-based supervisor tests.
    # write_json(status_path, status)
    return status_transition.write_status_snapshot_if_current(config, status)

@_entrypoint
def sync_status_pipeline(config: dict[str, Any]) -> bool:
    return status_transition.sync_status_pipeline(config)

@_entrypoint
def commit_canonical_task_transition(config: dict[str, Any], status: dict[str, Any]) -> bool:
    return status_transition.commit_canonical_task_transition(config, status)

@_entrypoint
def sync_dispatched_task_status(config: dict[str, Any], event: dict[str, Any]) -> bool:
    return status_transition.sync_dispatched_task_status(config, event)

@_entrypoint
def sync_preempted_task_status(config: dict[str, Any], worker: dict[str, Any]) -> bool:
    return status_transition.sync_preempted_task_status(config, worker)

@_entrypoint
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

@_entrypoint
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
        # A Human/Ops reviewer is a gate, not a lane. The owner side can still
        # recover onto another agent, but the human review assignment has to
        # survive that move: `first_viable_agent` skips human-gate names, so
        # running the normal replacement search here would report the human
        # reviewer as unviable and silently swap the gate for an automated
        # agent. Carry the reviewer through untouched instead.
        if is_human_gate_agent(reviewer):
            new_reviewer = reviewer
        else:
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

@_entrypoint
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
    # `.lower()` preserved: this call site compares against lowercased sibling
    # statuses, unlike the other fifteen.
    active_statuses = {status.lower() for status in active_worker_statuses(config)}
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

@_entrypoint
def is_transient_worker_failure(config: dict[str, Any], worker: dict[str, Any], reason: str | None) -> bool:
    if not reason:
        return False
    if not worker_retry_settings(config, worker.get("provider")).get("enabled", True):
        return False
    return bool(classify_worker_failure(config, worker, reason).get("transient"))

@_entrypoint
def retry_delay_seconds(config: dict[str, Any], worker: dict[str, Any]) -> float:
    retry = worker_retry_settings(config, worker.get("provider"))
    retry_count = int(worker.get("retry_count", 0))
    schedule = list(retry.get("backoff_schedule_seconds", []) or [5, 15, 30, 60, 120])
    index = min(retry_count, len(schedule) - 1)
    base_delay = float(schedule[index])
    jitter = float(retry.get("jitter_seconds", 0) or 0)
    return base_delay + (random.uniform(0, jitter) if jitter > 0 else 0)

@_entrypoint
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

@_entrypoint
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

@_entrypoint
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

@_entrypoint
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
        record = queue_event_record(state, queue_event_id)
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

@_entrypoint
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

@_entrypoint
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

@_entrypoint
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

@_entrypoint
def worker_supports_approval_resume(config: dict[str, Any], worker: dict[str, Any]) -> bool:
    return bool(
        provider_uses_claude_cli(config, worker.get("provider"))
        and (worker.get("session_id") or worker.get("resume_token"))
    )

@_entrypoint
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

@_entrypoint
def _deferred_tool_suggested_rule(tool_name: str, tool_input: dict[str, Any]) -> str | None:
    if tool_name == "Bash":
        shell_command = tool_input.get("command") or tool_input.get("cmd") or tool_input.get("raw_command")
        if shell_command:
            return f"Bash({shell_command})"
        return None
    return tool_name or None

@_entrypoint
def _deferred_tool_broker_decision(config: dict[str, Any], tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any] | None:
    try:
        from permission_broker import evaluate_tool_request
    except Exception:  # pragma: no cover - broker is optional at runtime
        return None
    try:
        return evaluate_tool_request(tool_name, tool_input, config)
    except Exception:  # pragma: no cover - never let classification break the poll loop
        return None

@_entrypoint
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
    if not provider_uses_claude_cli(config, worker.get("provider")):
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

@_entrypoint
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
    runtime = provider_section(config, provider_id=provider_id, section="runtime", default="claude")
    cli = configured_provider_binary(
        config, provider_id=provider_id, section="runtime", default="claude"
    )
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
    command.extend(claude_model_selection_args(runtime))
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
    env = claude_runtime_env(config, provider_id)
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
