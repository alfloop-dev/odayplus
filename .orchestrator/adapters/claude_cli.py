from __future__ import annotations

import os

from common import (
    apply_claude_oauth_token_file,
    command_exists,
    config_path,
    delivery_workspace_root,
    preserve_github_cli_auth_env,
    shell_quote,
)
from common import (
    claude_auth_ready as shared_claude_auth_ready,
)
from provider_runtime import inbox_fallback_enabled, provider_key, provider_settings

from adapters.base import DeliveryCapability, DeliveryRequest, DeliveryResult
from adapters.claude_code import ClaudeCodeAdapter


def _provider_key(config: dict | None, agent_id: str | None = None, provider_id: str | None = None) -> str:
    return provider_key(config, default="claude", agent_id=agent_id, provider_id=provider_id)


def _provider_settings(config: dict | None = None, provider_id: str | None = None) -> dict:
    return provider_settings(config, default="claude", provider_id=provider_id)


def _runtime_settings(config: dict | None = None, provider_id: str | None = None) -> dict:
    return _provider_settings(config, provider_id).get("runtime", {}) or {}


def _spawn_env(config: dict | None = None, provider_id: str | None = None) -> dict[str, str]:
    base_env = dict(os.environ)
    env = dict(base_env)
    runtime = _runtime_settings(config, provider_id)
    home = str(runtime.get("home") or "").strip()
    if home:
        env["HOME"] = os.path.expanduser(home)
    extra_env = runtime.get("env", {}) or {}
    for key, value in extra_env.items():
        if value is None:
            continue
        env[str(key)] = os.path.expanduser(str(value))
    preserve_github_cli_auth_env(env, base_env)
    apply_claude_oauth_token_file(env, runtime)
    return env


def _claude_auth_ready(
    cli: str | None,
    *,
    env: dict[str, str] | None = None,
    refresh_if_needed: bool = True,
) -> bool:
    return shared_claude_auth_ready(cli, env=env, refresh_if_needed=refresh_if_needed)


def _configured_claude_cli(config: dict | None = None, provider_id: str | None = None) -> str | None:
    runtime = _runtime_settings(config, provider_id)
    return command_exists(runtime.get("cli") or "claude")


def _allow_inbox_fallback(config: dict | None = None, provider_id: str | None = None) -> bool:
    return inbox_fallback_enabled(config, default="claude", provider_id=provider_id)


class ClaudeCLIAdapter(ClaudeCodeAdapter):
    name = "claude_cli"

    def capability(self, agent_id: str) -> DeliveryCapability:
        provider_id = _provider_key(self.config, agent_id=agent_id)
        cli = _configured_claude_cli(self.config, provider_id)
        auth_ready = _claude_auth_ready(cli, env=_spawn_env(self.config, provider_id), refresh_if_needed=False)
        if cli and auth_ready:
            return DeliveryCapability(
                adapter=self.name,
                supported=True,
                requires_manual_confirmation=False,
                can_auto_deliver=True,
                can_auto_approve_edits=True,
                delivery_mode="claude_cli",
                verified="verified",
                host="Claude Code CLI",
                notes="Uses non-interactive Claude CLI sessions with the local approval broker hooks.",
            )
        missing_reason = "Claude CLI is not installed" if not cli else "Claude CLI is installed but not authenticated"
        if not _allow_inbox_fallback(self.config, provider_id):
            return DeliveryCapability(
                adapter=self.name,
                supported=bool(cli),
                requires_manual_confirmation=False,
                can_auto_deliver=False,
                can_auto_approve_edits=False,
                delivery_mode="claude_cli",
                verified="partial" if cli else "unavailable",
                host="Claude Code CLI",
                notes=f"{missing_reason}; inbox fallback is disabled for this provider.",
            )
        fallback = super().capability(agent_id)
        return DeliveryCapability(
            adapter=self.name,
            supported=fallback.supported,
            requires_manual_confirmation=True,
            can_auto_deliver=False,
            can_auto_approve_edits=fallback.can_auto_approve_edits,
            delivery_mode="file_inbox",
            verified="partial",
            host="Claude Code CLI + inbox fallback",
            notes=f"{missing_reason}, so delivery falls back to the workspace inbox path.",
        )

    def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        provider_id = _provider_key(self.config, agent_id=request.agent_id, provider_id=request.provider)
        cli = _configured_claude_cli(self.config, provider_id)
        env = _spawn_env(self.config, provider_id)
        auth_ready = _claude_auth_ready(cli, env=env)
        if not cli or not auth_ready:
            if not _allow_inbox_fallback(self.config, provider_id):
                reason = (
                    "Claude CLI is unavailable; inbox fallback is disabled for this provider."
                    if not cli
                    else "Claude CLI is not authenticated; inbox fallback is disabled for this provider."
                )
                return DeliveryResult(
                    ok=False,
                    adapter=self.name,
                    mode="claude_cli",
                    target=request.agent_id,
                    auto_delivered=False,
                    manual_confirmation_required=False,
                    error=reason,
                    notes=reason,
                )
            result = super().deliver(request)
            result.adapter = self.name
            result.mode = "file_inbox"
            if not cli:
                result.notes = f"{result.notes}. Claude CLI is unavailable, so inbox fallback was used."
            else:
                result.notes = f"{result.notes}. Claude CLI is not authenticated, so inbox fallback was used."
            return result

        provider = _provider_settings(self.config, provider_id)
        runtime = provider.get("runtime", {})
        workspace_root = delivery_workspace_root(self.config, request.metadata)
        output_format = runtime.get("output_format", "stream-json")
        command = [
            runtime.get("cli") or cli,
            "-p",
            request.message,
            "--output-format",
            output_format,
        ]
        if output_format == "stream-json":
            command.append("--verbose")
        if runtime.get("include_hook_events", True):
            command.append("--include-hook-events")

        provider_info = (
            (self.provider_capabilities or {}).get("providers", {}).get(provider_id)
            or (self.provider_capabilities or {}).get("providers", {}).get("claude", {})
        )
        if runtime.get("enable_auto_mode_if_supported", True) and provider_info.get("supports_auto_approve"):
            command.extend(["--permission-mode", runtime.get("auto_permission_mode", "auto")])
        else:
            command.extend(["--permission-mode", runtime.get("permission_mode", "acceptEdits")])

        mcp_config = runtime.get("mcp_config")
        if mcp_config:
            command.extend(["--mcp-config", str(config_path(self.config, "claude_mcp_config"))])

        env.update(
            {
                "ORCH_CONTEXT_FILES": "\n".join(request.context_files),
                "ORCH_TARGET_FILES": "\n".join(request.target_files),
            }
        )
        return self.spawn_cli_delivery(
            request,
            provider_id=provider_id,
            mode="claude_cli",
            display_name=request.agent_id,
            command=command,
            notes="Claude CLI wake-up started in the background.",
            workspace_root=workspace_root,
            env_overrides=env,
            metadata={
                "shell_command": shell_quote(command),
            },
        )
