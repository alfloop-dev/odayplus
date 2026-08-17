from __future__ import annotations

import os

from common import (
    agent_config_for,
    command_exists,
    delivery_workspace_root,
)
from provider_runtime import provider_key, provider_section

from adapters.base import BaseAdapter, DeliveryCapability, DeliveryRequest, DeliveryResult

CODEX_INHERITED_SESSION_ENV = (
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
    "CODEX_CONVERSATION_ID",
    "CODEX_PARENT_THREAD_ID",
)


def _normalize_codex_model_name(model: str) -> str:
    """Map historical aliases to supported Codex model identifiers."""
    value = str(model or "").strip()
    normalized = value.lower()
    if normalized in {"codex-5.3-spark", "5.3-spark", "codex-5.3"}:
        return "gpt-5.3-codex-spark"
    return value


class CodexAdapter(BaseAdapter):
    name = "codex"

    def capability(self, agent_id: str) -> DeliveryCapability:
        provider_id = provider_key(self.config, default="codex", agent_id=agent_id)
        codex_settings = provider_section(
            self.config, provider_id=provider_id, section="codex", default="codex"
        )
        configured_cli = codex_settings.get("cli") or "codex"
        cli = command_exists(configured_cli) or command_exists("codex")
        supported = bool(cli)
        return DeliveryCapability(
            adapter=self.name,
            supported=supported,
            requires_manual_confirmation=not supported,
            can_auto_deliver=supported,
            can_auto_approve_edits=supported,
            delivery_mode="codex",
            verified="verified" if supported else "unavailable",
            host="Codex CLI",
            notes="Uses verified Codex CLI approval flags for orchestrated runs." if supported else "Codex CLI is not installed.",
        )

    def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        capability = self.capability(request.agent_id)
        if not capability.supported:
            return DeliveryResult(
                ok=False,
                adapter=self.name,
                mode="codex",
                target=request.agent_id,
                auto_delivered=False,
                manual_confirmation_required=True,
                error=capability.notes,
                notes=capability.notes,
            )

        provider_id = provider_key(
            self.config,
            default="codex",
            agent_id=request.agent_id,
            provider_id=request.provider,
        )
        codex_settings = provider_section(
            self.config, provider_id=provider_id, section="codex", default="codex"
        )
        agent_cfg = agent_config_for(self.config, request.agent_id)
        display_name = str(agent_cfg.get("display_name") or request.agent_id)
        cli = codex_settings.get("cli") or "codex"
        workspace_root = delivery_workspace_root(self.config, request.metadata)
        command = [
            cli,
            "exec",
            "-C",
            str(workspace_root),
            "-c",
            f'ask_for_approval="{codex_settings.get("ask_for_approval", "never")}"',
            "-s",
            codex_settings.get("sandbox_mode", "workspace-write"),
            "--skip-git-repo-check",
        ]
        model = _normalize_codex_model_name(codex_settings.get("model"))
        if model:
            command.extend(["--model", model])
        if codex_settings.get("dangerously_bypass"):
            command.append("--dangerously-bypass-approvals-and-sandbox")
        command.append(request.message)

        # Build env: inherit current environment, then apply overrides.
        env_overrides: dict[str, str] = {}

        api_key_env = codex_settings.get("api_key_env", "").strip()
        codex_home = codex_settings.get("codex_home", "").strip()

        if api_key_env:
            if api_key_env != "OPENAI_API_KEY":
                api_key_value = os.environ.get(api_key_env, "")
                if api_key_value:
                    env_overrides["OPENAI_API_KEY"] = api_key_value
        if codex_home:
            env_overrides["CODEX_HOME"] = os.path.expanduser(codex_home)

        remove_env = CODEX_INHERITED_SESSION_ENV + (() if api_key_env else ("OPENAI_API_KEY",))
        return self.spawn_cli_delivery(
            request,
            provider_id=provider_id,
            runtime_provider_id="codex",
            mode="codex",
            display_name=display_name,
            command=command,
            notes="Codex CLI wake-up started in the background.",
            workspace_root=workspace_root,
            env_overrides=env_overrides,
            remove_env=remove_env,
        )
