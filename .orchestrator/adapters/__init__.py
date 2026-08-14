from __future__ import annotations

from adapters.antigravity import AntigravityAdapter
from adapters.base import BaseAdapter
from adapters.claude_cli import ClaudeCLIAdapter
from adapters.codex import CodexAdapter
from adapters.file_inbox import FileInboxAdapter

ADAPTERS: dict[str, type[BaseAdapter]] = {
    "file_inbox": FileInboxAdapter,
    "claude_cli": ClaudeCLIAdapter,
    "antigravity": AntigravityAdapter,
    "codex": CodexAdapter,
}


def build_adapter(name: str, config: dict, provider_capabilities: dict | None = None) -> BaseAdapter:
    adapter_cls = ADAPTERS.get(name)
    if adapter_cls is None:
        raise KeyError(f"Unknown adapter: {name}")
    return adapter_cls(config=config, provider_capabilities=provider_capabilities or {})
