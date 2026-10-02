from __future__ import annotations

import json
import os
from pathlib import Path

from common import (
    agent_config_for,
    command_exists,
    delivery_workspace_root,
)
from provider_runtime import provider_key, provider_section

from adapters.base import BaseAdapter, DeliveryCapability, DeliveryRequest, DeliveryResult

PI_DEFAULT_LLM_PROVIDER = "openai-codex"
PI_DEFAULT_AGENT_DIR = "~/.pi/agent"

#: Levels `pi --thinking` accepts (pi-coding-agent 1.0.0 `pi --help`). Like
#: Codex's reasoning effort, a misspelling must fail the delivery here rather
#: than run at some other level while the receipt names the configured one.
PI_THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")

#: pi refuses `--provider` without `--model` ("--provider requires --model"), so
#: an empty `model` resolves through this mirror of pi's own
#: `defaultModelPerProvider` instead of being left to pi. Leaving both flags off
#: would let pi pick whichever logged-in provider it lists first.
PI_DEFAULT_MODELS = {
    "openai-codex": "gpt-6.1-sol",
}

PI_INHERITED_SESSION_ENV = (
    "PI_CODING_AGENT_DIR",
    "PI_CODING_AGENT_SESSION_DIR",
)

PI_CAPABILITY_NOTE = (
    "pi has no sandbox or approval layer: every orchestrated run executes its "
    "read/bash/edit/write tools unconfirmed inside the task worktree. "
    "Project-local .pi resources stay untrusted because print mode has no UI."
)


def _pi_settings(config: dict, provider_id: str) -> dict:
    return provider_section(config, provider_id=provider_id, section="pi", default="pi")


def _llm_provider(pi_settings: dict) -> str:
    return str(pi_settings.get("llm_provider") or "").strip() or PI_DEFAULT_LLM_PROVIDER


def _agent_dir(pi_settings: dict) -> Path:
    return Path(os.path.expanduser(str(pi_settings.get("agent_dir") or "").strip() or PI_DEFAULT_AGENT_DIR))


def _auth_problem(agent_dir: Path, llm_provider: str) -> str | None:
    """Why `<agent_dir>/auth.json` cannot serve `llm_provider`, or None.

    pi writes `{}` to auth.json before anyone logs in, so the file existing is not
    enough: the provider id has to be a key in it.
    """
    auth_path = agent_dir / "auth.json"
    login_hint = f"run `PI_CODING_AGENT_DIR={agent_dir} pi` and /login to {llm_provider} first."
    if not auth_path.is_file():
        return f"pi auth file {auth_path} is missing; {login_hint}"
    try:
        credentials = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"pi auth file {auth_path} is unreadable ({exc}); {login_hint}"
    if not isinstance(credentials, dict) or llm_provider not in credentials:
        return f"pi auth file {auth_path} has no {llm_provider} credential; {login_hint}"
    return None


def _thinking_args(pi_settings: dict) -> list[str]:
    thinking = str(pi_settings.get("thinking") or "").strip().lower()
    if not thinking:
        return []
    if thinking not in PI_THINKING_LEVELS:
        raise ValueError(
            f"Unsupported pi thinking {thinking!r}; expected one of {', '.join(PI_THINKING_LEVELS)}."
        )
    return ["--thinking", thinking]


def _model(pi_settings: dict, llm_provider: str) -> str:
    model = str(pi_settings.get("model") or "").strip() or PI_DEFAULT_MODELS.get(llm_provider, "")
    if not model:
        raise ValueError(
            f"pi provider {llm_provider!r} has no known default model; set pi.model explicitly."
        )
    return model


class PiAdapter(BaseAdapter):
    name = "pi"

    def capability(self, agent_id: str) -> DeliveryCapability:
        provider_id = provider_key(self.config, default="pi", agent_id=agent_id)
        pi_settings = _pi_settings(self.config, provider_id)
        cli = command_exists(pi_settings.get("cli") or "pi")
        if not cli:
            problem = "pi CLI is not installed."
        else:
            problem = _auth_problem(_agent_dir(pi_settings), _llm_provider(pi_settings))
        supported = problem is None
        return DeliveryCapability(
            adapter=self.name,
            supported=supported,
            requires_manual_confirmation=not supported,
            can_auto_deliver=supported,
            can_auto_approve_edits=supported,
            delivery_mode="pi",
            verified="verified" if supported else "unavailable",
            host="pi coding agent CLI",
            notes=PI_CAPABILITY_NOTE if supported else f"{problem} {PI_CAPABILITY_NOTE}",
        )

    def _failed(self, request: DeliveryRequest, reason: str) -> DeliveryResult:
        return DeliveryResult(
            ok=False,
            adapter=self.name,
            mode="pi",
            target=request.agent_id,
            auto_delivered=False,
            manual_confirmation_required=True,
            error=reason,
            notes=reason,
        )

    def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        capability = self.capability(request.agent_id)
        if not capability.supported:
            return self._failed(request, capability.notes)

        provider_id = provider_key(
            self.config,
            default="pi",
            agent_id=request.agent_id,
            provider_id=request.provider,
        )
        pi_settings = _pi_settings(self.config, provider_id)
        agent_cfg = agent_config_for(self.config, request.agent_id)
        display_name = str(agent_cfg.get("display_name") or request.agent_id)
        llm_provider = _llm_provider(pi_settings)
        try:
            # Same contract as Codex reasoning effort: a provider config error
            # fails this delivery with its reason instead of being forwarded.
            thinking_args = _thinking_args(pi_settings)
            model = _model(pi_settings, llm_provider)
        except ValueError as exc:
            return self._failed(request, str(exc))

        # The resolved absolute path: the process runs with cwd set to the task
        # worktree, where a relative `cli` such as .orchestrator/bin/pi would
        # resolve against the wrong tree.
        cli = command_exists(pi_settings.get("cli") or "pi") or "pi"
        command = [
            cli,
            "-p",
            "--provider",
            llm_provider,
            "--model",
            model,
        ]
        command.extend(thinking_args)
        command.append(request.message)

        return self.spawn_cli_delivery(
            request,
            provider_id=provider_id,
            runtime_provider_id="pi",
            mode="pi",
            display_name=display_name,
            command=command,
            notes="pi CLI wake-up started in the background.",
            workspace_root=delivery_workspace_root(self.config, request.metadata),
            env_overrides={"PI_CODING_AGENT_DIR": str(_agent_dir(pi_settings))},
            remove_env=PI_INHERITED_SESSION_ENV,
        )
