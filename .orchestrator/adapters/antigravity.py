from __future__ import annotations

import os
from pathlib import Path

import model_rotation
from common import (
    agent_config_for,
    delivery_workspace_root,
)
from provider_runtime import (
    configured_provider_binary,
    inbox_fallback_enabled,
    provider_config,
    provider_env,
    provider_key,
    provider_section,
)

from adapters.base import BaseAdapter, DeliveryCapability, DeliveryRequest, DeliveryResult

# Antigravity CLI (`agy`) is the successor to the Gemini CLI; Google stops
# serving the legacy Gemini CLI for consumer tiers on 2026-06-18. The OAuth
# token lives under ~/.gemini/antigravity-cli/ (relative to HOME), separate
# from the legacy Gemini CLI oauth_creds.json.
ANTIGRAVITY_OAUTH_TOKEN_REL = Path(".gemini") / "antigravity-cli" / "antigravity-oauth-token"

# `agy --print-timeout` is an absolute wall-clock limit; it does not observe
# worker process-tree activity.  The supervisor already owns activity-aware
# stall detection and terminates genuinely inactive workers, so keep the CLI
# timeout only as a last-resort runaway guard.  A week is deliberately beyond
# the normal task lifetime without pretending the CLI can reset this timer.
DEFAULT_HARD_PRINT_TIMEOUT = "168h"


def _antigravity_home(config: dict | None = None, provider_id: str | None = None) -> Path:
    runtime = provider_section(config, provider_id=provider_id, section="antigravity", default="antigravity")
    home = str(runtime.get("config_home") or runtime.get("home") or "").strip()
    return Path(os.path.expanduser(home)) if home else Path.home()


def _oauth_token_path(config: dict | None = None, provider_id: str | None = None) -> Path:
    return _antigravity_home(config, provider_id) / ANTIGRAVITY_OAUTH_TOKEN_REL


def _auth_ready(config: dict | None = None, provider_id: str | None = None) -> bool:
    env = {
        **os.environ,
        **provider_env(
            config,
            default="antigravity",
            provider_id=provider_id,
            blocks=("runtime", "antigravity"),
        ),
    }
    if env.get("GEMINI_API_KEY"):
        return True
    return _oauth_token_path(config, provider_id).exists()


class AntigravityAdapter(BaseAdapter):
    name = "antigravity"

    def capability(self, agent_id: str) -> DeliveryCapability:
        provider_id = provider_key(self.config, default="antigravity", agent_id=agent_id)
        allow_inbox_fallback = inbox_fallback_enabled(
            self.config, default="antigravity", provider_id=provider_id
        )
        cli = configured_provider_binary(
            self.config, provider_id=provider_id, section="antigravity", default="agy"
        )
        auth_ready = _auth_ready(self.config, provider_id)
        supported = bool(cli and auth_ready)
        if cli and auth_ready:
            notes = "Uses the verified Antigravity CLI `agy --prompt`, local OAuth/API-key auth, and auto-approval mode."
        elif cli:
            notes = "Antigravity CLI (agy) is installed but not authenticated for non-interactive use."
        else:
            notes = "Antigravity CLI (agy) is not installed."
        if not supported and not allow_inbox_fallback:
            notes = f"{notes} Inbox fallback is disabled for this provider."
        return DeliveryCapability(
            adapter=self.name,
            supported=bool(cli),
            requires_manual_confirmation=bool(not supported and allow_inbox_fallback),
            can_auto_deliver=supported,
            can_auto_approve_edits=supported,
            delivery_mode="antigravity" if (supported or not allow_inbox_fallback) else "file_inbox",
            verified="verified" if supported else ("partial" if cli else "unavailable"),
            host="Antigravity CLI" if (cli or not allow_inbox_fallback) else "Antigravity CLI + inbox fallback",
            notes=notes,
        )

    def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        provider_id = provider_key(
            self.config, default="antigravity", agent_id=request.agent_id, provider_id=request.provider
        )
        capability = self.capability(request.agent_id)
        if not capability.supported or not capability.can_auto_deliver:
            return self.unavailable_or_inbox(
                request,
                capability,
                mode="antigravity",
                target=agent_config_for(self.config, request.agent_id).get(
                    "display_name", request.agent_id
                ),
                allow_inbox_fallback=inbox_fallback_enabled(
                    self.config, default="antigravity", provider_id=provider_id
                ),
            )

        provider = provider_config(self.config, provider_id, default="antigravity")
        settings = provider_section(
            self.config, provider_id=provider_id, section="antigravity", default="antigravity"
        )
        approval = provider.get("approval", {})
        cli = configured_provider_binary(
            self.config, provider_id=provider_id, section="antigravity", default="agy"
        ) or settings.get("cli") or "agy"
        agent_cfg = agent_config_for(self.config, request.agent_id)
        display_name = str(agent_cfg.get("display_name") or request.agent_id)
        workspace_root = delivery_workspace_root(self.config, request.metadata)

        command = [cli]
        # Model rotation: cycle Gemini <-> Claude/GPT per the provider's quota
        # cooldown state (falls back to the static `model` setting when rotation
        # is disabled). '' means let agy use its default (Gemini) model.
        selection = model_rotation.resolve_active_selection(
            self.config,
            provider_id,
            settings,
            task=request.metadata.get("task") if isinstance(request.metadata, dict) else None,
            reason=request.reason,
        )
        model = str(selection.get("model") or "").strip()
        dispatched_pool = model_rotation.normalize_pool(selection.get("pool"))
        if model:
            # Structured argv: the model string is one argument, never shell text.
            command.extend(["--model", model])
        hard_print_timeout = str(
            settings.get("hard_print_timeout")
            or settings.get("print_timeout")  # backward-compatible legacy key
            or DEFAULT_HARD_PRINT_TIMEOUT
        ).strip()
        if hard_print_timeout:
            command.extend(["--print-timeout", hard_print_timeout])
        # Auto-approve tool/edit permissions for non-interactive worker runs.
        if approval.get("dangerously_skip_permissions", True):
            command.append("--dangerously-skip-permissions")
        include_directories = settings.get("include_directories")
        if include_directories:
            root = workspace_root
            paths = [str(root)] if include_directories is True else include_directories
            if isinstance(paths, (str, os.PathLike)):
                paths = [paths]
            for path in paths:
                expanded = Path(os.path.expanduser(str(path)))
                command.extend(["--add-dir", str(expanded if expanded.is_absolute() else root / expanded)])
        command.extend(["--prompt", request.message])

        env_overrides = provider_env(
            self.config, default="antigravity", provider_id=provider_id, blocks=("runtime", "antigravity")
        )
        home = _antigravity_home(self.config, provider_id)
        if home != Path.home():
            env_overrides["HOME"] = str(home)

        return self.spawn_cli_delivery(
            request,
            provider_id=provider_id,
            mode="antigravity",
            display_name=display_name,
            command=command,
            notes="Antigravity CLI wake-up started in the background.",
            workspace_root=workspace_root,
            env_overrides=env_overrides,
            metadata={
                # Pool/model this worker was ACTUALLY launched on. A later quota
                # failure is attributed to this immutable value, so a stale
                # worker can never cool a pool it never ran on.
                model_rotation.WORKER_POOL_KEY: dispatched_pool,
                model_rotation.WORKER_MODEL_KEY: model,
                model_rotation.WORKER_MODEL_RISK_TIER_KEY: selection.get("risk_tier"),
                model_rotation.WORKER_MODEL_REASON_KEY: selection.get("selection_reason"),
            },
        )
