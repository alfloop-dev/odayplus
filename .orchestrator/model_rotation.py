"""Antigravity model rotation: cycle a provider between its Gemini quota and its
Claude/GPT quota so the fleet keeps running (and consumes both pools) instead of
idling when one 5-hour limit is hit.

Self-contained and feature-flagged: only providers whose `antigravity` settings
carry `model_rotation.enabled = true` are affected. Everything else is a no-op.

State lives in `.orchestrator/runtime/antigravity_model_cooldown.json`, keyed by
the shared account/profile rather than a worker alias.

Both the adapter (model selection at dispatch) and the supervisor (recording an
exhausted pool on capacity/quota failure) call into this one module so their view
of "which pool is active" can never diverge.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Antigravity/agy reset hint, e.g. "Resets in 2h21m32s." or "refresh in 40 minutes".
_RESET_HMS = re.compile(r"resets?\s+in\s+(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?", re.IGNORECASE)
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
_STATE_PATH = Path(__file__).resolve().parent / "runtime" / "antigravity_model_cooldown.json"
DEFAULT_FALLBACK_MODEL = "Claude Sonnet 4.6 (Thinking)"

POOLS = ("gemini", "claude")
# Worker metadata keys: the pool/model a worker was ACTUALLY launched on. Quota
# failures must be recorded against this immutable dispatch-time value, never
# against `active_pool()` re-read at failure-processing time (see
# `record_exhaustion`).
WORKER_POOL_KEY = "antigravity_model_pool"
WORKER_MODEL_KEY = "antigravity_model"


def normalize_pool(pool: Any) -> str | None:
    """Return 'gemini'/'claude' for a recognised pool name, else None."""
    value = str(pool or "").strip().lower()
    return value if value in POOLS else None


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(UTC)


def _parse(ts: Any) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _provider_antigravity_settings(config: dict[str, Any] | None, provider_id: str | None) -> dict[str, Any]:
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
    explicit = provider.get("quota_group") or provider.get("account_group") or settings.get("account_group")
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


def _primary_model(config: dict[str, Any] | None, provider_id: str | None) -> str:
    # Empty string == let `agy` use its default (Gemini) model.
    return str(rotation_config(config, provider_id).get("primary_model") or "").strip()


def _fallback_model(config: dict[str, Any] | None, provider_id: str | None) -> str:
    return str(rotation_config(config, provider_id).get("fallback_model") or DEFAULT_FALLBACK_MODEL).strip()


def _load() -> dict[str, Any]:
    try:
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save(state: dict[str, Any]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(_STATE_PATH)


def _entry(state: dict[str, Any], provider_id: str) -> dict[str, Any]:
    entry = state.get(provider_id)
    return entry if isinstance(entry, dict) else {}


def active_pool(config: dict[str, Any] | None, provider_id: str, now: datetime | None = None) -> str | None:
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


def resolve_active_selection(config: dict[str, Any] | None, provider_id: str | None, settings: dict[str, Any] | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Pool + model to launch this worker on.

    Returns {"pool": 'gemini'|'claude'|None, "model": str, "rotating": bool}.
    `pool` is None when rotation is disabled for the provider (legacy static
    model). Callers must persist `pool` on the worker record so a later quota
    failure is attributed to the pool the worker actually ran on."""
    settings = settings if isinstance(settings, dict) else _provider_antigravity_settings(config, provider_id)
    if not rotation_enabled(config, provider_id):
        return {"pool": None, "model": str(settings.get("model") or "").strip(), "rotating": False}
    pool = active_pool(config, str(provider_id or ""), now=now)
    if pool == "claude":
        return {"pool": "claude", "model": _fallback_model(config, provider_id), "rotating": True}
    # 'gemini' or None (both cooling; the probe still goes out on primary) ->
    # primary model (empty string == agy default Gemini).
    return {"pool": "gemini", "model": _primary_model(config, provider_id), "rotating": True}


def resolve_active_model(config: dict[str, Any] | None, provider_id: str | None, settings: dict[str, Any] | None = None, now: datetime | None = None) -> str:
    """Model string for `agy --model`. '' means agy default (Gemini).

    When rotation is disabled, preserve legacy behaviour: return the static
    `model` setting (if any)."""
    return str(resolve_active_selection(config, provider_id, settings, now=now).get("model") or "")


def fallback_pool_available(config: dict[str, Any] | None, provider_id: str | None, now: datetime | None = None) -> bool:
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


def record_exhaustion(config: dict[str, Any] | None, provider_id: str | None, cooldown_seconds: int, *, reason: str | None = None, pool: str | None = None, now: datetime | None = None) -> dict[str, Any]:
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
        return {"exhausted_pool": None, "next_pool": None, "both_exhausted": True,
                "message": f"{pid}: both Gemini and Claude/GPT pools already cooling."}
    until = (now.replace(microsecond=0)).timestamp() + max(60, int(cooldown_seconds or 900))
    until_iso = datetime.fromtimestamp(until, tz=UTC).isoformat().replace("+00:00", "Z")
    state = _load()
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
    _save(state)
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
            entry for entry in state.values()
            if isinstance(entry, dict) and str(entry.get("trigger_provider") or "") == pid
        ]
        return {pid: matches[-1] if matches else {}}
    return state
