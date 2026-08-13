"""Shared provider configuration resolution for delivery adapters."""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

from common import agent_config_for


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
