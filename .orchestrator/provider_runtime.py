"""Shared provider configuration resolution for delivery adapters."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from common import agent_config_for, command_exists, load_json, run_command


def provider_key(
    config: dict[str, Any] | None,
    *,
    default: str,
    agent_id: str | None = None,
    provider_id: str | None = None,
) -> str:
    if provider_id:
        return str(provider_id).strip() or default
    if agent_id:
        agent = agent_config_for(config or {}, agent_id)
        return str(agent.get("provider") or agent.get("id") or agent_id).strip() or default
    return default


def provider_settings(
    config: dict[str, Any] | None,
    *,
    default: str,
    provider_id: str | None = None,
) -> dict[str, Any]:
    providers = (config or {}).get("providers", {}) or {}
    key = provider_key(config, default=default, provider_id=provider_id)
    return providers.get(key) or providers.get(default) or {}


def provider_env(
    config: dict[str, Any] | None,
    *,
    default: str,
    provider_id: str | None = None,
    blocks: Iterable[str] = ("runtime",),
    defaults: dict[str, str] | None = None,
) -> dict[str, str]:
    provider = provider_settings(config, default=default, provider_id=provider_id)
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
    return bool(provider_settings(config, default=default, provider_id=provider_id).get("allow_inbox_fallback", True))


def configured_provider_binary(
    config: dict[str, Any] | None,
    *,
    provider_id: str,
    section: str,
    default: str,
) -> str | None:
    """Resolve an executable from one provider configuration section."""
    provider = ((config or {}).get("providers", {}).get(provider_id, {}) or {})
    runtime = provider.get(section, {}) or {}
    return command_exists(runtime.get("cli") or default)


def github_auth_token(binary: str | None) -> str | None:
    """Return the token exposed by an authenticated GitHub CLI."""
    if not binary:
        return None
    result = run_command([binary, "auth", "token"])
    token = (result.stdout or "").strip()
    return token or None


def gemini_home(config: dict[str, Any] | None = None, provider_id: str = "gemini") -> Path:
    runtime = provider_settings(config, default="gemini", provider_id=provider_id).get("gemini", {}) or {}
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
