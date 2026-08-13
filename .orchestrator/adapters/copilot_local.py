from __future__ import annotations

import json
import os
from pathlib import Path

from common import (
    delivery_workspace_root,
    shell_quote,
)
from provider_runtime import configured_provider_binary, github_auth_token

from adapters.base import BaseAdapter, DeliveryCapability, DeliveryRequest, DeliveryResult

COPILOT_CONFIG_DIR = Path.home() / ".copilot"
COPILOT_CONFIG_PATH = COPILOT_CONFIG_DIR / "config.json"


def _configured_copilot_cli(config: dict | None = None) -> str | None:
    return configured_provider_binary(
        config,
        provider_id="copilot",
        section="local",
        default="copilot",
    )


def _configured_gh_cli(config: dict | None = None) -> str | None:
    return configured_provider_binary(
        config,
        provider_id="copilot",
        section="cloud",
        default="gh",
    )


def _allow_inbox_fallback(config: dict | None = None) -> bool:
    provider = ((config or {}).get("providers", {}).get("copilot", {}) or {})
    return bool(provider.get("allow_inbox_fallback", True))


def _gh_auth_token(config: dict | None = None) -> str | None:
    return github_auth_token(_configured_gh_cli(config))


def _copilot_config_auth_ready() -> bool:
    if not COPILOT_CONFIG_PATH.exists():
        return False
    for candidate in ("oauth.json", "auth.json", "credentials.json", "hosts.json"):
        if (COPILOT_CONFIG_DIR / candidate).exists():
            return True
    try:
        payload = json.loads(COPILOT_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return any(key != "firstLaunchAt" and value not in (None, "", {}, []) for key, value in payload.items())


def _copilot_auth_ready(config: dict | None = None) -> bool:
    for env_name in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(env_name):
            return True
    return bool(_gh_auth_token(config)) or _copilot_config_auth_ready()


class CopilotLocalAdapter(BaseAdapter):
    name = "copilot_local"

    def capability(self, agent_id: str) -> DeliveryCapability:
        cli = _configured_copilot_cli(self.config)
        if cli and _copilot_auth_ready(self.config):
            return DeliveryCapability(
                adapter=self.name,
                supported=True,
                requires_manual_confirmation=False,
                can_auto_deliver=True,
                can_auto_approve_edits=True,
                delivery_mode="copilot_local",
                verified="verified",
                host="Copilot CLI + VS Code workspace link",
                notes="Uses Copilot CLI autopilot in the current WSL workspace.",
            )
        missing_reason = "Copilot CLI is not installed" if not cli else "Copilot CLI is installed but not authenticated"
        if not _allow_inbox_fallback(self.config):
            return DeliveryCapability(
                adapter=self.name,
                supported=bool(cli),
                requires_manual_confirmation=False,
                can_auto_deliver=False,
                can_auto_approve_edits=False,
                delivery_mode="copilot_local",
                verified="partial" if cli else "unavailable",
                host="Copilot CLI",
                notes=f"{missing_reason}; inbox fallback is disabled for this provider.",
            )
        return DeliveryCapability(
            adapter=self.name,
            supported=True,
            requires_manual_confirmation=True,
            can_auto_deliver=False,
            can_auto_approve_edits=False,
            delivery_mode="file_inbox",
            verified="partial",
            host="Copilot CLI + inbox fallback",
            notes=f"{missing_reason}, so delivery falls back to a workspace inbox file.",
        )

    def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        cli = _configured_copilot_cli(self.config)
        auth_ready = _copilot_auth_ready(self.config)
        if not cli or not auth_ready:
            return self.unavailable_or_inbox(
                request,
                self.capability(request.agent_id),
                mode="copilot_local",
                target=request.agent_id,
                allow_inbox_fallback=_allow_inbox_fallback(self.config),
            )

        provider = self.config.get("providers", {}).get("copilot", {})
        local = provider.get("local", {})
        workspace_root = delivery_workspace_root(self.config, request.metadata)
        command = [local.get("cli") or cli]
        if local.get("autopilot", True):
            command.append("--autopilot")
        command.extend(["-p", request.message])
        max_autopilot = local.get("max_autopilot_continues")
        if max_autopilot:
            command.extend(["--max-autopilot-continues", str(max_autopilot)])
        if local.get("allow_all_tools", False):
            command.append("--allow-all-tools")
        if local.get("add_workspace_dir", True):
            command.extend(["--add-dir", str(workspace_root)])
        if local.get("no_ask_user", True):
            command.append("--no-ask-user")
        for tool in local.get("allow_tools", []) or []:
            command.extend(["--allow-tool", tool])
        for tool in local.get("deny_tools", []) or []:
            command.extend(["--deny-tool", tool])
        model_preference = request.metadata.get("model_preference")
        if model_preference:
            command.extend(["--model", str(model_preference)])
        for extra_arg in local.get("extra_args", []) or []:
            command.append(str(extra_arg))

        env: dict[str, str] = {}
        if not any(
            os.environ.get(name)
            for name in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")
        ):
            gh_token = _gh_auth_token(self.config)
            if gh_token:
                env["GH_TOKEN"] = gh_token
        return self.spawn_cli_delivery(
            request,
            provider_id="copilot",
            mode="copilot_local",
            display_name=request.agent_id,
            command=command,
            notes="Copilot CLI autopilot wake-up started in the background.",
            workspace_root=workspace_root,
            env_overrides=env,
            metadata={
                "shell_command": shell_quote(command),
                "model_preference": model_preference,
            },
        )
