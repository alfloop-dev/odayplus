"""Antigravity model rotation: cycle a provider between its Gemini quota and its
Claude/GPT quota so the fleet keeps running (and consumes both pools) instead of
idling when one 5-hour limit is hit.

Self-contained and feature-flagged: only providers whose `antigravity` settings
carry `model_rotation.enabled = true` are affected. Everything else is a no-op.

State lives in the canonical status root's
`.orchestrator/runtime/antigravity_model_cooldown.json`, keyed by the shared
account/profile rather than a worker alias. A legacy checkout-local state file
is read once, under the canonical lock, and merged into that authority.

Both the adapter (model selection at dispatch) and the supervisor (recording an
exhausted pool on capacity/quota failure) call into this one module so their view
of "which pool is active" can never diverge.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Antigravity/agy reset hint, e.g. "Resets in 2h21m32s." or "refresh in 40 minutes".
_RESET_HMS = re.compile(
    r"resets?\s+in\s+(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?", re.IGNORECASE
)
_RESET_MINUTES = re.compile(r"(?:refresh|resets?)\s+in\s+(\d+)\s*minutes?", re.IGNORECASE)


def parse_reset_seconds(text: str | None) -> int | None:
    """Best-effort seconds-until-reset from an agy quota message. None if absent."""
    if not text:
        return None
    m = _RESET_MINUTES.search(text)
    if m:
        return int(m.group(1)) * 60
    m = _RESET_HMS.search(text)
    if m and any(m.groups()):
        h, mi, s = (int(g) if g else 0 for g in m.groups())
        total = h * 3600 + mi * 60 + s
        return total or None
    return None


UTC = UTC
_STATE_FILENAME = "antigravity_model_cooldown.json"
_LEGACY_STATE_PATH = Path(__file__).resolve().parent / "runtime" / _STATE_FILENAME


def _canonical_state_path() -> Path:
    """Return the cooldown authority for this process.

    Supervisor-launched workers receive the canonical status root in their
    environment. Keep the checkout-local path only as the no-environment
    fallback for standalone development and as the one-time migration source.
    ``ORCH_STATUS_ROOT`` has the same precedence as the rest of the
    orchestrator status system; ``PANTHEON_STATUS_ROOT`` remains supported for
    the existing worker contract.
    """
    raw_root = str(
        os.environ.get("ORCH_STATUS_ROOT") or os.environ.get("PANTHEON_STATUS_ROOT") or ""
    ).strip()
    if not raw_root:
        return _LEGACY_STATE_PATH
    return (
        Path(os.path.expanduser(raw_root)).resolve() / ".orchestrator" / "runtime" / _STATE_FILENAME
    )


# This remains a module-level compatibility seam for the existing supervisor
# tests, which replace it with a per-test file. In production it stays equal to
# the import-time canonical path while `_state_path()` also tracks a status-root
# environment selected after module import.
_DEFAULT_STATE_PATH = _canonical_state_path()
_STATE_PATH = _DEFAULT_STATE_PATH
_MIGRATION_MARKER_SUFFIX = ".legacy-migrated"
# `agy --model` takes the model ID from `agy models`, not its display label.
# This was "Claude Sonnet 4.6 (Thinking)" -- the label -- so every quota
# fallback dispatched an unresolvable model and the claude pool was dead.
DEFAULT_FALLBACK_MODEL = "claude-sonnet-4-6"

POOLS = ("gemini", "claude")
# Worker metadata keys: the pool/model a worker was ACTUALLY launched on. Quota
# failures must be recorded against this immutable dispatch-time value, never
# against `active_pool()` re-read at failure-processing time (see
# `record_exhaustion`).
WORKER_POOL_KEY = "antigravity_model_pool"
WORKER_MODEL_KEY = "antigravity_model"
WORKER_MODEL_RISK_TIER_KEY = "antigravity_model_risk_tier"
WORKER_MODEL_REASON_KEY = "antigravity_model_reason"

DEFAULT_STANDARD_MODEL = "gemini-3.7-flash-high"
# NOT a gemini pro id. `agy --model gemini-3.1-pro-high` is not honoured: the
# CLI logs "Model ID gemini-3.1-pro-high not in local config, defaulting to
# CCPA" and the trajectory records `gemini-pro-default` (MODEL_PLACEHOLDER_M16)
# instead of the requested model -- i.e. every P0/P1 dispatch silently ran on
# agy's own default rather than the pro tier we asked for. Measured
# 2026-08-23 across 19 of 23 runs. `claude-opus-4-6-thinking` and
# `gemini-3.7-flash-high` both round-trip intact (M26 / M298), so high-risk
# work goes to the strongest id the account actually resolves.
DEFAULT_HIGH_RISK_MODEL = "claude-opus-4-6-thinking"
DEFAULT_HIGH_RISK_PRIORITIES = ("P0", "P1")
DEFAULT_HIGH_RISK_KEYWORDS = (
    "core/",
    "domain/",
    "persistence",
    "repository",
    "database",
    "migration",
    "rbac",
    "permission",
    "authorization",
    "finance",
    "billing",
    "payment",
    "timekeeping",
    "timezone",
    "temporal",
    "contract",
    "schema",
    "workflow",
    "iac",
)
DEFAULT_STANDARD_KEYWORDS = (
    "sidecar",
    "documentation",
    "docs/",
    "lint",
    "formatting",
    "finalize",
    "closeout",
)


def normalize_pool(pool: Any) -> str | None:
    """Return 'gemini'/'claude' for a recognised pool name, else None."""
    value = str(pool or "").strip().lower()
    return value if value in POOLS else None


def pool_for_model(model: Any, default: str = "gemini") -> str:
    """Quota pool a model id actually bills against.

    The risk policy and the quota-rotation pool are separate axes: a
    high-risk dispatch can select a `claude-*` id while the rotation pool is
    still 'gemini'. Attributing that run to 'gemini' would cool the wrong pool
    on a quota failure, so the pool always follows the id being launched.
    """
    value = str(model or "").strip().lower()
    if value.startswith("claude-"):
        return "claude"
    return default


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(UTC)


def _parse(ts: Any) -> datetime | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    # A hand-edited or old naive timestamp must not make a dispatch crash when
    # compared with the aware UTC clock used by the supervisor.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _provider_antigravity_settings(
    config: dict[str, Any] | None, provider_id: str | None
) -> dict[str, Any]:
    providers = (config or {}).get("providers", {}) or {}
    key = str(provider_id or "").strip() or "antigravity"
    provider = providers.get(key) or providers.get("antigravity") or {}
    settings = provider.get("antigravity") if isinstance(provider, dict) else None
    return settings if isinstance(settings, dict) else {}


def cooldown_scope(config: dict[str, Any] | None, provider_id: str | None) -> str:
    """Stable state key for the account used by an Antigravity provider alias."""
    providers = (config or {}).get("providers", {}) or {}
    pid = str(provider_id or "").strip()
    provider = providers.get(pid) if isinstance(providers.get(pid), dict) else {}
    settings = _provider_antigravity_settings(config, pid)
    explicit = (
        provider.get("quota_group")
        or provider.get("account_group")
        or settings.get("account_group")
    )
    if explicit:
        return f"account:{str(explicit).strip().lower()}"
    profile = str(settings.get("config_home") or settings.get("home") or "").strip()
    if profile:
        digest = hashlib.sha256(str(Path(profile).expanduser()).encode()).hexdigest()[:16]
        return f"profile:{digest}"
    return "account:antigravity-default" if pid.lower().startswith("antigravity") else pid


def rotation_config(config: dict[str, Any] | None, provider_id: str | None) -> dict[str, Any]:
    settings = _provider_antigravity_settings(config, provider_id)
    rc = settings.get("model_rotation")
    return rc if isinstance(rc, dict) else {}


def rotation_enabled(config: dict[str, Any] | None, provider_id: str | None) -> bool:
    return bool(rotation_config(config, provider_id).get("enabled"))


def model_policy_config(config: dict[str, Any] | None, provider_id: str | None) -> dict[str, Any]:
    """Return the Antigravity task-risk policy with safe operational defaults.

    A provider that explicitly pins the legacy ``model`` remains static unless
    it opts in. Providers without a static model use the risk policy by default,
    which prevents agy's own low-reasoning default from silently handling P0/P1
    or repeatedly reopened work.
    """
    settings = _provider_antigravity_settings(config, provider_id)
    raw = settings.get("model_policy")
    policy = dict(raw) if isinstance(raw, dict) else {}
    policy.setdefault("enabled", not bool(str(settings.get("model") or "").strip()))
    policy.setdefault("standard_model", DEFAULT_STANDARD_MODEL)
    policy.setdefault("high_risk_model", DEFAULT_HIGH_RISK_MODEL)
    policy.setdefault("high_risk_priorities", list(DEFAULT_HIGH_RISK_PRIORITIES))
    policy.setdefault("high_risk_keywords", list(DEFAULT_HIGH_RISK_KEYWORDS))
    policy.setdefault("standard_task_keywords", list(DEFAULT_STANDARD_KEYWORDS))
    policy.setdefault("upgrade_after_review_reopens", 1)
    return policy


def task_model_decision(
    config: dict[str, Any] | None,
    provider_id: str | None,
    task: dict[str, Any] | None = None,
    reason: str | None = None,
) -> dict[str, str]:
    """Choose the Gemini model for one dispatch from durable task risk signals."""
    policy = model_policy_config(config, provider_id)
    if not policy.get("enabled", True):
        return {"model": "", "risk_tier": "static", "reason": "model_policy_disabled"}

    task = task if isinstance(task, dict) else {}
    dispatch_reason = str(reason or "").strip().lower()
    task_class = str(task.get("task_class") or "").strip().lower()
    corpus_parts = [
        str(task.get(key) or "")
        for key in ("id", "title", "summary", "summary_zh", "scope", "next")
    ]
    corpus_parts.extend(str(item or "") for item in (task.get("artifacts") or []))
    corpus = " ".join(corpus_parts).casefold().replace("\\", "/")

    # Structural sidecars and the post-merge finalize lane are bounded work and
    # stay on Flash even when the parent task has historical review churn.
    if task_class == "sidecar" or "sidecar" in dispatch_reason or "finalize" in dispatch_reason:
        return {
            "model": str(policy.get("standard_model") or DEFAULT_STANDARD_MODEL).strip(),
            "risk_tier": "standard",
            "reason": "bounded_sidecar_or_finalize",
        }

    try:
        reopen_count = max(0, int(task.get("review_reopen_count", 0) or 0))
    except (TypeError, ValueError):
        reopen_count = 0
    try:
        reopen_threshold = max(1, int(policy.get("upgrade_after_review_reopens", 1) or 1))
    except (TypeError, ValueError):
        reopen_threshold = 1
    if reopen_count >= reopen_threshold:
        return {
            "model": str(policy.get("high_risk_model") or DEFAULT_HIGH_RISK_MODEL).strip(),
            "risk_tier": "high",
            "reason": f"review_reopened_{reopen_count}_time(s)",
        }

    priority = str(task.get("priority") or "").strip().upper()
    high_priorities = {str(item).strip().upper() for item in policy.get("high_risk_priorities", [])}
    if priority and priority in high_priorities:
        return {
            "model": str(policy.get("high_risk_model") or DEFAULT_HIGH_RISK_MODEL).strip(),
            "risk_tier": "high",
            "reason": f"business_priority_{priority}",
        }

    high_keywords = [
        str(item).casefold() for item in policy.get("high_risk_keywords", []) if str(item)
    ]
    matched_keyword = next((keyword for keyword in high_keywords if keyword in corpus), None)
    if matched_keyword:
        return {
            "model": str(policy.get("high_risk_model") or DEFAULT_HIGH_RISK_MODEL).strip(),
            "risk_tier": "high",
            "reason": f"sensitive_scope:{matched_keyword}",
        }

    standard_keywords = [
        str(item).casefold() for item in policy.get("standard_task_keywords", []) if str(item)
    ]
    if any(keyword in corpus for keyword in standard_keywords):
        return {
            "model": str(policy.get("standard_model") or DEFAULT_STANDARD_MODEL).strip(),
            "risk_tier": "standard",
            "reason": "bounded_docs_or_lint",
        }

    return {
        "model": str(policy.get("standard_model") or DEFAULT_STANDARD_MODEL).strip(),
        "risk_tier": "standard",
        "reason": "ordinary_single_module_or_unclassified",
    }


def _primary_model(config: dict[str, Any] | None, provider_id: str | None) -> str:
    # Empty string == let `agy` use its default (Gemini) model.
    return str(rotation_config(config, provider_id).get("primary_model") or "").strip()


def _fallback_model(config: dict[str, Any] | None, provider_id: str | None) -> str:
    return str(
        rotation_config(config, provider_id).get("fallback_model") or DEFAULT_FALLBACK_MODEL
    ).strip()


def _state_path() -> Path:
    """Resolve the canonical path, retaining `_STATE_PATH` test overrides."""
    configured = Path(_STATE_PATH)
    if configured != _DEFAULT_STATE_PATH:
        return configured
    return _canonical_state_path()


def canonical_state_path() -> Path:
    """Public introspection helper for the durable cooldown authority."""
    return _state_path()


def _migration_marker_path(state_path: Path) -> Path:
    return state_path.with_name(state_path.name + _MIGRATION_MARKER_SUFFIX)


@contextmanager
def _state_lock(state_path: Path) -> Iterator[None]:
    """Lock the canonical state for a complete read/merge/write transaction."""
    lock_path = state_path.with_name(f"{state_path.name}.lock")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _read_state_file(path: Path) -> tuple[bool, bool, dict[str, Any]]:
    """Read one state file, distinguishing absent from malformed state."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False, True, {}
    except (OSError, UnicodeError):
        return True, False, {}
    if not raw.strip():
        return True, False, {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return True, False, {}
    if not isinstance(data, dict):
        return True, False, {}
    return True, True, data


def _write_state_unlocked(state_path: Path, state: dict[str, Any]) -> None:
    """Atomically replace state; caller must hold `_state_lock`."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=state_path.parent, delete=False
        ) as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, state_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _write_migration_marker_unlocked(state_path: Path) -> None:
    """Atomically seal the one-way legacy read after canonical state exists."""
    marker_path = _migration_marker_path(state_path)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=marker_path.parent, delete=False
        ) as handle:
            handle.write("canonical cooldown state owns migration\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, marker_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _merge_legacy_state(canonical: dict[str, Any], legacy: dict[str, Any]) -> dict[str, Any]:
    """Merge legacy entries while retaining the later expiry per pool."""
    merged = dict(canonical)
    for scope, legacy_entry in legacy.items():
        if not isinstance(legacy_entry, dict):
            continue
        current_entry = merged.get(scope)
        if not isinstance(current_entry, dict):
            merged[scope] = dict(legacy_entry)
            continue

        combined = dict(current_entry)
        for pool in POOLS:
            field = f"{pool}_until"
            legacy_until = _parse(legacy_entry.get(field))
            current_until = _parse(current_entry.get(field))
            if legacy_until is not None and (current_until is None or legacy_until > current_until):
                combined[field] = legacy_entry[field]
        for field in ("scope", "trigger_provider", "last_reason", "updated_at"):
            if field not in combined and field in legacy_entry:
                combined[field] = legacy_entry[field]
        merged[scope] = combined
    return merged


def _load_unlocked(state_path: Path) -> dict[str, Any]:
    """Load canonical state and perform the one-time legacy migration."""
    canonical_exists, canonical_valid, canonical = _read_state_file(state_path)
    # A malformed canonical file is fail-safe: do not resurrect a possibly
    # stale checkout copy, and do not overwrite the evidence during a read.
    if canonical_exists and not canonical_valid:
        return {}

    marker_path = _migration_marker_path(state_path)
    if marker_path.exists():
        # A marker is durable proof that the old checkout is no longer an
        # authority. If the canonical payload was removed, fail safe to an
        # empty state rather than resurrecting the old runtime copy.
        return canonical

    legacy_path = Path(_LEGACY_STATE_PATH)
    legacy_exists = False
    legacy_valid = True
    legacy: dict[str, Any] = {}
    if legacy_path.resolve() != state_path.resolve():
        legacy_exists, legacy_valid, legacy = _read_state_file(legacy_path)

    if not canonical_exists:
        # Creating an empty canonical file seals migration even when no usable
        # legacy state exists. A later-created checkout file must never become
        # a second read authority.
        canonical = legacy if legacy_exists and legacy_valid else {}
    elif legacy_exists and legacy_valid:
        canonical = _merge_legacy_state(canonical, legacy)

    try:
        _write_state_unlocked(state_path, canonical)
        _write_migration_marker_unlocked(state_path)
    except OSError:
        # Keep reads fail-safe if a status root becomes temporarily unavailable;
        # record_exhaustion will surface a write error rather than losing a
        # requested cooldown silently.
        pass
    return canonical


def _load() -> dict[str, Any]:
    state_path = _state_path()
    try:
        with _state_lock(state_path):
            return _load_unlocked(state_path)
    except OSError:
        return {}


def _save(state: dict[str, Any]) -> None:
    state_path = _state_path()
    with _state_lock(state_path):
        _write_state_unlocked(state_path, state)
        _write_migration_marker_unlocked(state_path)


def _entry(state: dict[str, Any], provider_id: str) -> dict[str, Any]:
    entry = state.get(provider_id)
    return entry if isinstance(entry, dict) else {}


def pool_cooling(
    config: dict[str, Any] | None, provider_id: str, pool: str, now: datetime | None = None
) -> bool:
    """Whether one pool is still inside its quota cooldown window."""
    until = _parse(_entry(_load(), cooldown_scope(config, provider_id)).get(f"{pool}_until"))
    return until is not None and _now(now) < until


def active_pool(
    config: dict[str, Any] | None, provider_id: str, now: datetime | None = None
) -> str | None:
    """Return 'gemini', 'claude', or None (both pools currently cooling down)."""
    now = _now(now)
    entry = _entry(_load(), cooldown_scope(config, provider_id))
    gemini_until = _parse(entry.get("gemini_until"))
    claude_until = _parse(entry.get("claude_until"))
    gemini_cooling = gemini_until is not None and now < gemini_until
    claude_cooling = claude_until is not None and now < claude_until
    if gemini_cooling and claude_cooling:
        return None
    if gemini_cooling:
        return "claude"
    if claude_cooling:
        return "gemini"
    return "gemini"


def resolve_active_selection(
    config: dict[str, Any] | None,
    provider_id: str | None,
    settings: dict[str, Any] | None = None,
    now: datetime | None = None,
    *,
    task: dict[str, Any] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Pool + model to launch this worker on.

    Returns {"pool": 'gemini'|'claude'|None, "model": str, "rotating": bool}.
    `pool` is None when rotation is disabled for the provider (legacy static
    model). Callers must persist `pool` on the worker record so a later quota
    failure is attributed to the pool the worker actually ran on."""
    settings = (
        settings
        if isinstance(settings, dict)
        else _provider_antigravity_settings(config, provider_id)
    )
    decision = task_model_decision(config, provider_id, task=task, reason=reason)
    policy_model = str(decision.get("model") or "").strip()
    if not rotation_enabled(config, provider_id):
        static_model = str(settings.get("model") or "").strip()
        return {
            "pool": None,
            "model": policy_model or static_model,
            "rotating": False,
            "risk_tier": decision.get("risk_tier"),
            "selection_reason": decision.get("reason"),
        }
    pool = active_pool(config, str(provider_id or ""), now=now)
    if pool == "claude":
        return {
            "pool": "claude",
            "model": _fallback_model(config, provider_id),
            "rotating": True,
            "risk_tier": decision.get("risk_tier"),
            "selection_reason": f"quota_pool_fallback:{decision.get('reason')}",
        }
    # 'gemini' or None (both cooling; the probe still goes out on primary) ->
    # primary model (empty string == agy default Gemini).
    selected_model = policy_model or _primary_model(config, provider_id)
    # The risk policy names the strongest id the account resolves, which for
    # high-risk work lives in the claude pool. Reaching here means rotation did
    # NOT grant claude -- either it is cooling, or both pools are -- so billing
    # that id now would launch straight into the exhausted pool. Drop to the
    # standard gemini id instead of the tier the policy asked for.
    if pool_for_model(selected_model) == "claude" and pool_cooling(
        config, str(provider_id or ""), "claude", now
    ):
        selected_model = str(
            model_policy_config(config, provider_id).get("standard_model") or DEFAULT_STANDARD_MODEL
        ).strip()
    return {
        "pool": pool_for_model(selected_model),
        "model": selected_model,
        "rotating": True,
        "risk_tier": decision.get("risk_tier"),
        "selection_reason": decision.get("reason"),
    }


def resolve_active_model(
    config: dict[str, Any] | None,
    provider_id: str | None,
    settings: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> str:
    """Model string for `agy --model`. '' means agy default (Gemini).

    When rotation is disabled, preserve legacy behaviour: return the static
    `model` setting (if any)."""
    return str(resolve_active_selection(config, provider_id, settings, now=now).get("model") or "")


def fallback_pool_available(
    config: dict[str, Any] | None, provider_id: str | None, now: datetime | None = None
) -> bool:
    """Whether a rotating provider still has a pool available for redispatch."""
    return bool(
        rotation_enabled(config, provider_id)
        and active_pool(config, str(provider_id or ""), now=now) is not None
    )


def worker_dispatched_pool(worker: dict[str, Any] | None) -> str | None:
    """Pool a worker record was actually launched on, or None if unknown.

    Looks at the worker's adapter metadata first, then a top-level mirror, so
    both freshly-written and legacy worker records resolve."""
    if not isinstance(worker, dict):
        return None
    metadata = worker.get("metadata")
    if isinstance(metadata, dict):
        pool = normalize_pool(metadata.get(WORKER_POOL_KEY))
        if pool:
            return pool
    return normalize_pool(worker.get(WORKER_POOL_KEY))


def record_exhaustion(
    config: dict[str, Any] | None,
    provider_id: str | None,
    cooldown_seconds: int,
    *,
    reason: str | None = None,
    pool: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Mark the pool a failed worker was DISPATCHED ON as exhausted for `cooldown_seconds`.

    `pool` must be the immutable dispatch-time pool from the worker record.
    Inferring it from `active_pool()` here is unsafe under concurrency: once
    worker A cools Gemini, a stale worker B that also ran on Gemini would be
    attributed to Claude and falsely exhaust both pools. Only fall back to
    `active_pool()` when the dispatched pool is genuinely unknown (e.g. a
    delivery that failed before a worker record existed).

    Returns {exhausted_pool, next_pool, both_exhausted, message}. When both pools
    are cooling the caller should fall back to a real dispatch pause."""
    now = _now(now)
    pid = str(provider_id or "")
    scope = cooldown_scope(config, pid)
    pool = normalize_pool(pool)
    inferred = pool is None
    if inferred:
        pool = active_pool(config, pid, now=now)
    if pool is None:
        return {
            "exhausted_pool": None,
            "next_pool": None,
            "both_exhausted": True,
            "message": f"{pid}: both Gemini and Claude/GPT pools already cooling.",
        }
    until = (now.replace(microsecond=0)).timestamp() + max(60, int(cooldown_seconds or 900))
    until_iso = datetime.fromtimestamp(until, tz=UTC).isoformat().replace("+00:00", "Z")
    state_path = _state_path()
    with _state_lock(state_path):
        # Reload while holding the canonical lock so concurrent quota failures
        # update different pool/scope fields instead of losing one another.
        state = _load_unlocked(state_path)
        entry = dict(_entry(state, scope))
        if pool == "gemini":
            entry["gemini_until"] = until_iso
        else:
            entry["claude_until"] = until_iso
        entry["last_reason"] = (str(reason or "").strip()[:200]) or entry.get("last_reason")
        entry["updated_at"] = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        entry["scope"] = scope
        entry["trigger_provider"] = pid
        state[scope] = entry
        _write_state_unlocked(state_path, state)
        _write_migration_marker_unlocked(state_path)
    next_pool = active_pool(config, pid, now=now)
    both = next_pool is None
    return {
        "exhausted_pool": pool,
        "next_pool": next_pool,
        "both_exhausted": both,
        "pool_source": "inferred" if inferred else "dispatched",
        "message": (
            f"{pid}: {pool} pool exhausted until {until_iso}; "
            + ("both pools now cooling -> real pause." if both else f"rotating to {next_pool}.")
        ),
    }


def status(provider_id: str | None = None) -> dict[str, Any]:
    """Introspection helper for humans/tests."""
    state = _load()
    if provider_id:
        pid = str(provider_id)
        direct = _entry(state, pid)
        if direct:
            return {pid: direct}
        matches = [
            entry
            for entry in state.values()
            if isinstance(entry, dict) and str(entry.get("trigger_provider") or "") == pid
        ]
        return {pid: matches[-1] if matches else {}}
    return state
