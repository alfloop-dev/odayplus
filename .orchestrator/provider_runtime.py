"""Shared provider configuration resolution for delivery adapters."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback.
    tomllib = None  # type: ignore[assignment]

from common import (
    agent_config_for,
    apply_claude_oauth_token_file,
    command_exists,
    load_json,
    normalize_agent_id,
    preserve_github_cli_auth_env,
    run_command,
)

# Keep this aligned with the installed Codex config schema.  `priority` is a
# valid tier in current Codex CLI releases; treating it as invalid silently
# removes otherwise healthy Codex lanes from supervisor dispatch.
CODEX_ALLOWED_SERVICE_TIERS = ("fast", "flex", "priority")


def provider_config_entry(
    config: dict[str, Any] | None,
    provider_id: str | None,
    *,
    default: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return the canonical provider key and settings from one resolver.

    Provider ids appear as display names, normalized ids, and historical
    hyphen/underscore variants. Every consumer must resolve those aliases in
    the same order. ``default`` opts adapters into their historical fallback;
    callers that validate an explicit provider omit it and fail closed.
    """
    providers = (config or {}).get("providers", {}) or {}
    requested = str(provider_id or "").strip()

    def candidates(value: str) -> list[str]:
        normalized = normalize_agent_id(value)
        return list(
            dict.fromkeys(
                candidate
                for candidate in (
                    value,
                    normalized,
                    value.replace("_", "-"),
                    value.replace("-", "_"),
                    normalized.replace("_", "-"),
                )
                if candidate
            )
        )

    for candidate in candidates(requested):
        entry = providers.get(candidate)
        if isinstance(entry, dict):
            return candidate, entry

    fallback = str(default or "").strip()
    if fallback and fallback != requested:
        for candidate in candidates(fallback):
            entry = providers.get(candidate)
            if isinstance(entry, dict):
                return candidate, entry

    return normalize_agent_id(requested or fallback), {}


def provider_key(
    config: dict[str, Any] | None,
    *,
    default: str,
    agent_id: str | None = None,
    provider_id: str | None = None,
) -> str:
    requested = ""
    if provider_id:
        requested = str(provider_id).strip()
    elif agent_id:
        agent = agent_config_for(config or {}, agent_id)
        requested = str(agent.get("provider") or agent.get("id") or agent_id).strip()
    return provider_config_entry(config, requested, default=default)[0] or default


def provider_config(
    config: dict[str, Any] | None,
    provider_id: str | None,
    *,
    default: str | None = None,
) -> dict[str, Any]:
    return provider_config_entry(config, provider_id, default=default)[1]


def provider_section(
    config: dict[str, Any] | None,
    *,
    provider_id: str | None,
    section: str,
    default: str | None = None,
) -> dict[str, Any]:
    """Return one provider subsection through the canonical resolver."""
    value = provider_config(config, provider_id, default=default).get(section, {}) or {}
    return value if isinstance(value, dict) else {}


def provider_uses_claude_cli(
    config: dict[str, Any] | None,
    provider_id: str | None,
) -> bool:
    """Return whether a provider belongs to the Claude approval/runtime lane."""
    normalized = normalize_agent_id(provider_id or "")
    if not normalized:
        return False
    delivery_mode = str(provider_config(config, provider_id).get("delivery_mode") or "").strip()
    return delivery_mode == "claude_cli" if delivery_mode else normalized.startswith("claude")


def claude_approval_provider(config: dict[str, Any] | None) -> str:
    """Resolve the provider identity used by Claude hooks and approval records."""
    provider_id = normalize_agent_id(os.environ.get("ORCH_PROVIDER") or "claude") or "claude"
    provider = provider_config(config, provider_id)
    delivery_mode = str(provider.get("delivery_mode") or "").strip()
    if delivery_mode and delivery_mode != "claude_cli":
        return "claude"
    if provider or provider_id.startswith("claude"):
        return provider_id
    return "claude"


def provider_env(
    config: dict[str, Any] | None,
    *,
    default: str,
    provider_id: str | None = None,
    blocks: Iterable[str] = ("runtime",),
    defaults: dict[str, str] | None = None,
) -> dict[str, str]:
    provider = provider_config(config, provider_id, default=default)
    env: dict[str, str] = dict(defaults or {})
    for block_name in blocks:
        block = provider.get(block_name, {}) or {}
        for key, value in (block.get("env", {}) or {}).items():
            if value is not None:
                env[str(key)] = os.path.expanduser(str(value))
    return env


def inbox_fallback_enabled(
    config: dict[str, Any] | None,
    *,
    default: str,
    provider_id: str | None = None,
) -> bool:
    return bool(provider_config(config, provider_id, default=default).get("allow_inbox_fallback", True))


def configured_provider_binary(
    config: dict[str, Any] | None,
    *,
    provider_id: str,
    section: str,
    default: str,
) -> str | None:
    """Resolve an executable from one provider configuration section."""
    runtime = provider_section(config, provider_id=provider_id, section=section)
    return command_exists(runtime.get("cli") or default)


def claude_runtime_env(
    config: dict[str, Any] | None,
    provider_id: str = "claude",
) -> dict[str, str]:
    """Build the one process environment used by Claude probes and workers."""
    base_env = dict(os.environ)
    env = dict(base_env)
    runtime = provider_section(config, provider_id=provider_id, section="runtime", default="claude")
    home = str(runtime.get("home") or "").strip()
    if home:
        env["HOME"] = os.path.expanduser(home)
    for key, value in (runtime.get("env", {}) or {}).items():
        if value is not None:
            env[str(key)] = os.path.expanduser(str(value))
    preserve_github_cli_auth_env(env, base_env)
    apply_claude_oauth_token_file(env, runtime)
    return env


def codex_home(config: dict[str, Any] | None = None, provider_id: str = "codex") -> Path:
    provider = provider_config(config, provider_id, default="codex")
    profile = provider_section(config, provider_id=provider_id, section="codex", default="codex")
    home = str(
        profile.get("codex_home")
        or profile.get("config_home")
        or provider.get("codex_home")
        or ""
    ).strip()
    return Path(os.path.expanduser(home)) if home else Path.home() / ".codex"


def codex_config_path(config: dict[str, Any] | None = None, provider_id: str = "codex") -> Path:
    return codex_home(config, provider_id) / "config.toml"


def codex_config_health(config: dict[str, Any] | None = None, provider_id: str = "codex") -> dict[str, Any]:
    """Validate dispatch-critical Codex config without probing capabilities."""
    path = codex_config_path(config, provider_id)
    result: dict[str, Any] = {
        "valid": True,
        "path": str(path),
        "checks": {"service_tier": None},
        "allowed_service_tiers": list(CODEX_ALLOWED_SERVICE_TIERS),
    }
    if not path.exists():
        result["notes"] = "Codex config file is absent; CLI defaults apply."
        return result
    if tomllib is None:
        result["notes"] = "Python tomllib is unavailable; Codex config schema preflight skipped."
        return result
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        result.update({"valid": False, "error": f"Codex config {path} cannot be parsed: {exc}"})
        return result

    service_tier = payload.get("service_tier")
    result["checks"]["service_tier"] = service_tier
    if service_tier in (None, ""):
        return result
    if not isinstance(service_tier, str):
        result.update(
            {
                "valid": False,
                "error": (
                    f"Codex config {path} has non-string service_tier={service_tier!r}; "
                    f"installed Codex CLI accepts {', '.join(CODEX_ALLOWED_SERVICE_TIERS)}."
                ),
            }
        )
        return result
    if service_tier.strip().lower() not in CODEX_ALLOWED_SERVICE_TIERS:
        result.update(
            {
                "valid": False,
                "error": (
                    f"Codex config {path} has unsupported service_tier={service_tier!r}; "
                    f"installed Codex CLI accepts {', '.join(CODEX_ALLOWED_SERVICE_TIERS)}."
                ),
            }
        )
    return result


def github_auth_token(binary: str | None) -> str | None:
    """Return the token exposed by an authenticated GitHub CLI."""
    if not binary:
        return None
    result = run_command([binary, "auth", "token"])
    token = (result.stdout or "").strip()
    return token or None


def gemini_home(config: dict[str, Any] | None = None, provider_id: str = "gemini") -> Path:
    runtime = provider_section(config, provider_id=provider_id, section="gemini", default="gemini")
    home = str(runtime.get("config_home") or runtime.get("home") or "").strip()
    return Path(os.path.expanduser(home)) if home else Path.home()


def gemini_settings_path(config: dict[str, Any] | None = None, provider_id: str = "gemini") -> Path:
    return gemini_home(config, provider_id) / ".gemini" / "settings.json"


def gemini_oauth_creds_path(config: dict[str, Any] | None = None, provider_id: str = "gemini") -> Path:
    return gemini_home(config, provider_id) / ".gemini" / "oauth_creds.json"


def gemini_runtime_env(
    config: dict[str, Any] | None = None,
    provider_id: str = "gemini",
) -> dict[str, str]:
    return {
        **os.environ,
        **provider_env(
            config,
            default="gemini",
            provider_id=provider_id,
            blocks=("runtime", "gemini"),
            defaults={"GEMINI_CLI_TRUST_WORKSPACE": "true"},
        ),
    }


def gemini_settings(
    config: dict[str, Any] | None = None,
    provider_id: str = "gemini",
) -> dict[str, Any]:
    return load_json(gemini_settings_path(config, provider_id), default={}) or {}


def _truthy_env(name: str, env: dict[str, str]) -> bool:
    return env.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def gemini_selected_auth_type(
    settings: dict[str, Any],
    *,
    oauth_creds_path: Path,
    env: dict[str, str],
) -> str | None:
    if _truthy_env("GOOGLE_GENAI_USE_GCA", env):
        return "oauth-personal"
    if _truthy_env("GEMINI_CLI_USE_COMPUTE_ADC", env):
        return "compute-default-credentials"
    if _truthy_env("GOOGLE_GENAI_USE_VERTEXAI", env):
        return "vertex-ai"
    if env.get("GEMINI_API_KEY"):
        return "gemini-api-key"
    return settings.get("security", {}).get("auth", {}).get("selectedType") or (
        "oauth-personal" if oauth_creds_path.exists() else None
    )


def gemini_auth_ready(
    settings: dict[str, Any],
    *,
    oauth_creds_path: Path,
    env: dict[str, str],
) -> bool:
    auth_type = gemini_selected_auth_type(
        settings,
        oauth_creds_path=oauth_creds_path,
        env=env,
    )
    if auth_type == "oauth-personal":
        return oauth_creds_path.exists()
    if auth_type == "gemini-api-key":
        return bool(env.get("GEMINI_API_KEY"))
    if auth_type == "vertex-ai":
        return bool(
            env.get("GOOGLE_API_KEY")
            or (env.get("GOOGLE_CLOUD_PROJECT") and env.get("GOOGLE_CLOUD_LOCATION"))
        )
    if auth_type == "compute-default-credentials":
        if env.get("GOOGLE_APPLICATION_CREDENTIALS"):
            return True
        gcloud = command_exists("gcloud")
        return bool(gcloud) and run_command(
            [gcloud, "auth", "application-default", "print-access-token"]
        ).returncode == 0
    return False
