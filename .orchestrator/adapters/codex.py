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

#: Reasoning levels `codex exec -c model_reasoning_effort=...` accepts, in the
#: order the CLI's own model cache lists them (codex-cli 0.147.0, verified
#: against `gpt-6-astra`). Which of them a given model offers is the model's
#: business rather than this list's; what this list is for is the spelling.
#: `-c` values are opaque config, so the CLI takes an unrecognised level without
#: complaint -- `codex -c model_reasoning_effort="maximum" features list` exits
#: 0 -- and a typo would otherwise run every dispatch at the model default while
#: the receipt still named the configured level.
CODEX_REASONING_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max", "ultra")


def _reasoning_effort_config_args(codex_settings: dict) -> list[str]:
    """`-c model_reasoning_effort=...` for this dispatch, or none.

    Reasoning effort is a Codex *config* key, not a CLI flag -- there is no
    `--effort` on `codex exec` -- so it travels the same `-c key="value"` channel
    as `ask_for_approval`. Reading it from the provider's `codex` section is what
    makes one setting cover every pool and every physical slot that resolves to
    that provider, instead of each launch path carrying its own preference.

    Unset means "send nothing", which leaves the CLI on its own default and is
    exactly the behaviour of every configuration written before this key existed.
    Any other value is rejected here rather than forwarded, because the CLI will
    not reject it for us.
    """
    effort = str(codex_settings.get("model_reasoning_effort") or "").strip().lower()
    if not effort:
        return []
    if effort not in CODEX_REASONING_EFFORT_LEVELS:
        raise ValueError(
            f"Unsupported Codex model_reasoning_effort {effort!r}; "
            f"expected one of {', '.join(CODEX_REASONING_EFFORT_LEVELS)}."
        )
    return ["-c", f'model_reasoning_effort="{effort}"']


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
        try:
            reasoning_effort_args = _reasoning_effort_config_args(codex_settings)
        except ValueError as exc:
            # A misspelled effort is a provider config error, and dispatching
            # anyway would run the review at whatever the CLI defaults to while
            # the receipt still claimed the configured level. Fail the delivery
            # with the reason instead of raising into the supervisor loop.
            return DeliveryResult(
                ok=False,
                adapter=self.name,
                mode="codex",
                target=request.agent_id,
                auto_delivered=False,
                manual_confirmation_required=True,
                error=str(exc),
                notes=str(exc),
            )
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
        command.extend(reasoning_effort_args)
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
