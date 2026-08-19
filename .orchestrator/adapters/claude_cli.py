from __future__ import annotations

from common import (
    claude_auth_ready,
    claude_model_selection_args,
    config_path,
    delivery_workspace_root,
    shell_quote,
)
from provider_runtime import (
    claude_runtime_env,
    configured_provider_binary,
    inbox_fallback_enabled,
    provider_key,
    provider_section,
)

from adapters.base import DeliveryCapability, DeliveryRequest, DeliveryResult
from adapters.file_inbox import FileInboxAdapter


class ClaudeCLIAdapter(FileInboxAdapter):
    name = "claude_cli"

    def capability(self, agent_id: str) -> DeliveryCapability:
        provider_id = provider_key(self.config, default="claude", agent_id=agent_id)
        cli = configured_provider_binary(
            self.config, provider_id=provider_id, section="runtime", default="claude"
        )
        auth_ready = claude_auth_ready(
            cli, env=claude_runtime_env(self.config, provider_id), refresh_if_needed=False
        )
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
        if not inbox_fallback_enabled(self.config, default="claude", provider_id=provider_id):
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
        provider_id = provider_key(
            self.config, default="claude", agent_id=request.agent_id, provider_id=request.provider
        )
        cli = configured_provider_binary(
            self.config, provider_id=provider_id, section="runtime", default="claude"
        )
        env = claude_runtime_env(self.config, provider_id)
        auth_ready = claude_auth_ready(cli, env=env)
        if not cli or not auth_ready:
            if not inbox_fallback_enabled(self.config, default="claude", provider_id=provider_id):
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

        runtime = provider_section(
            self.config, provider_id=provider_id, section="runtime", default="claude"
        )
        workspace_root = delivery_workspace_root(self.config, request.metadata)
        output_format = runtime.get("output_format", "stream-json")
        command = [
            # `cli` is this same `runtime.cli` value already resolved to an
            # absolute path by command_exists; preferring the raw config string
            # threw that resolution away. The worker is spawned with cwd set to
            # its workspace, so a relative argv[0] like `.orchestrator/bin/claude`
            # resolved only while that workspace happened to be the pantheon
            # checkout. Every cross-repository worker died on it -- observed
            # 2026-08-19T07:35:38Z finalizing DPF-GOV-001 from the
            # oday-data-platform worktree: FileNotFoundError on
            # '.orchestrator/bin/claude'. `deliver` has already returned the
            # inbox fallback when `cli` is falsy, so it is set by this point.
            cli,
            "-p",
            request.message,
            "--output-format",
            output_format,
        ]
        if output_format == "stream-json":
            command.append("--verbose")
        if runtime.get("include_hook_events", True):
            command.append("--include-hook-events")
        command.extend(claude_model_selection_args(runtime))

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
