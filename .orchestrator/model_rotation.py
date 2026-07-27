"""Antigravity model rotation: cycle a provider between its Gemini quota and its
Claude/GPT quota so the fleet keeps running (and consumes both pools) instead of
idling when one 5-hour limit is hit.

Self-contained and feature-flagged: only providers whose `antigravity` settings
carry `model_rotation.enabled = true` are affected. Everything else is a no-op.

State lives in `.orchestrator/runtime/antigravity_model_cooldown.json`:
    { "<provider_id>": {"gemini_until": "<iso>|null", "claude_until": "<iso>|null"} }

Both the adapter (model selection at dispatch) and the supervisor (recording an
exhausted pool on capacity/quota failure) call into this one module so their view
of "which pool is active" can never diverge.
"""
from __future__ import annotations

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
    entry = _entry(_load(), str(provider_id or ""))
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


def resolve_active_model(config: dict[str, Any] | None, provider_id: str | None, settings: dict[str, Any] | None = None, now: datetime | None = None) -> str:
    """Model string for `agy --model`. '' means agy default (Gemini).

    When rotation is disabled, preserve legacy behaviour: return the static
    `model` setting (if any)."""
    settings = settings if isinstance(settings, dict) else _provider_antigravity_settings(config, provider_id)
    if not rotation_enabled(config, provider_id):
        return str(settings.get("model") or "").strip()
    pool = active_pool(config, str(provider_id or ""), now=now)
    if pool == "claude":
        return _fallback_model(config, provider_id)
    # 'gemini' or None -> primary (empty == agy default Gemini)
    return _primary_model(config, provider_id)


def record_exhaustion(config: dict[str, Any] | None, provider_id: str | None, cooldown_seconds: int, *, reason: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Mark the CURRENTLY-ACTIVE pool as exhausted for `cooldown_seconds`.

    Returns {exhausted_pool, next_pool, both_exhausted, message}. When both pools
    are cooling the caller should fall back to a real dispatch pause."""
    now = _now(now)
    pid = str(provider_id or "")
    pool = active_pool(config, pid, now=now)  # the pool that was in use when it failed
    if pool is None:
        return {"exhausted_pool": None, "next_pool": None, "both_exhausted": True,
                "message": f"{pid}: both Gemini and Claude/GPT pools already cooling."}
    until = (now.replace(microsecond=0)).timestamp() + max(60, int(cooldown_seconds or 900))
    until_iso = datetime.fromtimestamp(until, tz=UTC).isoformat().replace("+00:00", "Z")
    state = _load()
    entry = dict(_entry(state, pid))
    if pool == "gemini":
        entry["gemini_until"] = until_iso
    else:
        entry["claude_until"] = until_iso
    entry["last_reason"] = (str(reason or "").strip()[:200]) or entry.get("last_reason")
    entry["updated_at"] = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    state[pid] = entry
    _save(state)
    next_pool = active_pool(config, pid, now=now)
    both = next_pool is None
    return {
        "exhausted_pool": pool,
        "next_pool": next_pool,
        "both_exhausted": both,
        "message": (
            f"{pid}: {pool} pool exhausted until {until_iso}; "
            + ("both pools now cooling -> real pause." if both else f"rotating to {next_pool}.")
        ),
    }


def status(provider_id: str | None = None) -> dict[str, Any]:
    """Introspection helper for humans/tests."""
    state = _load()
    if provider_id:
        return {str(provider_id): _entry(state, str(provider_id))}
    return state
