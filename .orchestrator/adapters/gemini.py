from __future__ import annotations

import os
from pathlib import Path

from common import (
    agent_config_for,
    delivery_workspace_root,
)
from provider_runtime import (
    configured_provider_binary,
    gemini_runtime_env,
    gemini_settings,
    inbox_fallback_enabled,
    provider_env,
    provider_key,
    provider_settings,
)
from provider_runtime import (
    gemini_auth_ready as shared_gemini_auth_ready,
)
from provider_runtime import (
    gemini_home as _gemini_home,
)
from provider_runtime import (
    gemini_oauth_creds_path as _gemini_oauth_creds_path,
)

from adapters.base import BaseAdapter, DeliveryCapability, DeliveryRequest, DeliveryResult


def _provider_key(config: dict | None, agent_id: str | None = None, provider_id: str | None = None) -> str:
    return provider_key(config, default="gemini", agent_id=agent_id, provider_id=provider_id)


def _provider_settings(config: dict | None = None, provider_id: str | None = None) -> dict:
    return provider_settings(config, default="gemini", provider_id=provider_id)


def _provider_env(config: dict | None = None, provider_id: str | None = None) -> dict[str, str]:
    return provider_env(
        config,
        default="gemini",
        provider_id=provider_id,
        blocks=("runtime", "gemini"),
        defaults={"GEMINI_CLI_TRUST_WORKSPACE": "true"},
    )


def _configured_gemini_cli(config: dict | None = None, provider_id: str | None = None) -> str | None:
    return configured_provider_binary(
        config,
        provider_id=provider_id or "gemini",
        section="gemini",
        default="gemini",
    )


def _allow_inbox_fallback(config: dict | None = None, provider_id: str | None = None) -> bool:
    return inbox_fallback_enabled(config, default="gemini", provider_id=provider_id)


def _gemini_auth_ready(config: dict | None = None, provider_id: str | None = None) -> bool:
    key = provider_id or "gemini"
    env = gemini_runtime_env(config, key)
    return shared_gemini_auth_ready(
        gemini_settings(config, key),
        oauth_creds_path=_gemini_oauth_creds_path(config, key),
        env=env,
    )


class GeminiAdapter(BaseAdapter):
    name = "gemini"

    def capability(self, agent_id: str) -> DeliveryCapability:
        provider_id = _provider_key(self.config, agent_id=agent_id)
        allow_inbox_fallback = _allow_inbox_fallback(self.config, provider_id)
        cli = _configured_gemini_cli(self.config, provider_id)
        auth_ready = _gemini_auth_ready(self.config, provider_id)
        supported = bool(cli and auth_ready)
        if cli and auth_ready:
            notes = "Uses the verified Gemini CLI `--prompt`, local auth config, and approval mode settings."
        elif cli:
            notes = "Gemini CLI is installed but not authenticated for non-interactive use."
        else:
            notes = "Gemini CLI is not installed."
        if not supported and not allow_inbox_fallback:
            notes = f"{notes} Inbox fallback is disabled for this provider."
        return DeliveryCapability(
            adapter=self.name,
            supported=bool(cli),
            requires_manual_confirmation=bool(not supported and allow_inbox_fallback),
            can_auto_deliver=supported,
            can_auto_approve_edits=supported,
            delivery_mode="gemini" if (supported or not allow_inbox_fallback) else "file_inbox",
            verified="verified" if supported else ("partial" if cli else "unavailable"),
            host="Gemini CLI" if (cli or not allow_inbox_fallback) else "Gemini CLI + inbox fallback",
            notes=notes,
        )

    def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        provider_id = _provider_key(self.config, agent_id=request.agent_id, provider_id=request.provider)
        capability = self.capability(request.agent_id)
        if not capability.supported or not capability.can_auto_deliver:
            return self.unavailable_or_inbox(
                request,
                capability,
                mode="gemini",
                target=agent_config_for(self.config, request.agent_id).get(
                    "display_name", request.agent_id
                ),
                allow_inbox_fallback=_allow_inbox_fallback(self.config, provider_id),
            )

        provider = _provider_settings(self.config, provider_id)
        gemini_settings = provider.get("gemini", {})
        approval = provider.get("approval", {})
        cli = _configured_gemini_cli(self.config, provider_id) or gemini_settings.get("cli") or "gemini"
        agent_cfg = agent_config_for(self.config, request.agent_id)
        display_name = str(agent_cfg.get("display_name") or request.agent_id)
        workspace_root = delivery_workspace_root(self.config, request.metadata)
        command = [cli]
        model = str(gemini_settings.get("model") or "").strip()
        if model:
            command.extend(["--model", model])
        output_format = str(gemini_settings.get("output_format") or "").strip()
        if output_format:
            command.extend(["--output-format", output_format])
        command.extend(["--prompt", request.message])
        approval_mode = approval.get("default_approval_mode")
        if approval_mode:
            command.extend(["--approval-mode", approval_mode])
        include_directories = gemini_settings.get("include_directories")
        if include_directories:
            root = workspace_root
            paths = [str(root)] if include_directories is True else include_directories
            if isinstance(paths, (str, os.PathLike)):
                paths = [paths]
            for path in paths:
                expanded = Path(os.path.expanduser(str(path)))
                command.extend(["--include-directories", str(expanded if expanded.is_absolute() else root / expanded)])

        env_overrides = _provider_env(self.config, provider_id)
        gemini_home = _gemini_home(self.config, provider_id)
        if gemini_home != Path.home():
            env_overrides["GEMINI_CLI_HOME"] = str(gemini_home)

        return self.spawn_cli_delivery(
            request,
            provider_id=provider_id,
            mode="gemini",
            display_name=display_name,
            command=command,
            notes="Gemini CLI wake-up started in the background.",
            workspace_root=workspace_root,
            env_overrides=env_overrides,
        )
