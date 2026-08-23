from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from common import (
    delivery_runtime_env,
    delivery_workspace_root,
    new_runtime_id,
    runtime_log_path,
    spawn_background_process,
    worker_runtime_paths,
)


@dataclass
class DeliveryCapability:
    adapter: str
    supported: bool
    requires_manual_confirmation: bool
    can_auto_deliver: bool
    can_auto_approve_edits: bool
    delivery_mode: str
    verified: str
    notes: str = ""
    host: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DeliveryRequest:
    agent_id: str
    provider: str
    delivery_mode: str
    message: str
    task_id: str | None = None
    reason: str | None = None
    context_files: list[str] = field(default_factory=list)
    target_files: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeliveryResult:
    ok: bool
    adapter: str
    mode: str
    target: str
    auto_delivered: bool
    manual_confirmation_required: bool
    notes: str = ""
    command: list[str] = field(default_factory=list)
    payload_path: str | None = None
    log_path: str | None = None
    pid: int | None = None
    run_id: str | None = None
    session_id: str | None = None
    resume_token: str | None = None
    session_url: str | None = None
    pr_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class BaseAdapter:
    name = "base"

    def __init__(self, *, config: dict[str, Any], provider_capabilities: dict[str, Any]) -> None:
        self.config = config
        self.provider_capabilities = provider_capabilities

    def capability(self, agent_id: str) -> DeliveryCapability:
        raise NotImplementedError

    def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        raise NotImplementedError

    def unavailable_or_inbox(
        self,
        request: DeliveryRequest,
        capability: DeliveryCapability,
        *,
        mode: str,
        target: str,
        allow_inbox_fallback: bool,
    ) -> DeliveryResult:
        """Return one canonical unavailable result or delegate to the inbox."""

        if not allow_inbox_fallback:
            reason = capability.notes or f"{mode} auto-delivery is unavailable."
            return DeliveryResult(
                ok=False,
                adapter=self.name,
                mode=mode,
                target=target,
                auto_delivered=False,
                manual_confirmation_required=False,
                error=reason,
                notes=reason,
            )

        # Local import avoids a base -> concrete adapter import cycle.
        from adapters.file_inbox import FileInboxAdapter

        result = FileInboxAdapter(
            config=self.config,
            provider_capabilities=self.provider_capabilities,
        ).deliver(request)
        result.adapter = self.name
        result.mode = "file_inbox"
        result.notes = f"{result.notes}. {capability.notes}"
        if not capability.supported:
            result.error = capability.notes
        return result

    def spawn_cli_delivery(
        self,
        request: DeliveryRequest,
        *,
        provider_id: str,
        runtime_provider_id: str | None = None,
        mode: str,
        display_name: str,
        command: list[str],
        notes: str,
        workspace_root: Path | None = None,
        env_overrides: dict[str, str] | None = None,
        remove_env: tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> DeliveryResult:
        """Launch a provider CLI with the shared worker runtime contract."""

        root = workspace_root or delivery_workspace_root(self.config, request.metadata)
        runtime_key = runtime_provider_id or provider_id
        run_id = new_runtime_id(runtime_key)
        log_path = runtime_log_path(runtime_key, request.agent_id)
        runtime_paths = worker_runtime_paths(self.config, run_id)
        scratch_dir = runtime_paths["status_path"].parents[1] / "scratch" / run_id
        scratch_dir.mkdir(parents=True, exist_ok=True)

        env = dict(os.environ)
        env.update(delivery_runtime_env(self.config, request.metadata))
        for key in remove_env:
            env.pop(key, None)
        env.update(env_overrides or {})
        env.update(
            {
                "AI_NAME": display_name,
                "ORCH_AGENT_ID": request.agent_id,
                "ORCH_PROVIDER": provider_id,
                "ORCH_RUN_ID": run_id,
                # One-shot patchers and probe scripts belong outside the task
                # checkout.  The handoff seal remains authoritative, but this
                # gives every provider a safe default that prevents the common
                # untracked-root-file failure before it happens.
                "ORCH_SCRATCH_DIR": str(scratch_dir),
                "PANTHEON_WORKER_SCRATCH_DIR": str(scratch_dir),
            }
        )
        if request.task_id:
            env["ORCH_TASK_ID"] = request.task_id
        if request.reason:
            env["ORCH_REASON"] = request.reason

        process, _ = spawn_background_process(
            command,
            cwd=root,
            log_path=log_path,
            env=env,
            run_id=run_id,
            heartbeat_path=runtime_paths["heartbeat_path"],
            status_path=runtime_paths["status_path"],
        )
        result_metadata = {
            "heartbeat_path": str(runtime_paths["heartbeat_path"]),
            "runner_status_path": str(runtime_paths["status_path"]),
        }
        result_metadata.update(metadata or {})
        return DeliveryResult(
            ok=True,
            adapter=self.name,
            mode=mode,
            target=display_name,
            auto_delivered=True,
            manual_confirmation_required=False,
            notes=notes,
            command=command,
            log_path=str(log_path),
            pid=process.pid,
            run_id=run_id,
            metadata=result_metadata,
        )
