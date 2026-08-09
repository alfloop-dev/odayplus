#!/usr/bin/env python3
"""Migrate a Supervisor runtime config from alias-based to account-pool scheduling.

The roster reflects the operational inventory: all Antigravity aliases share
one account; Codex is split by the configured credential homes.  The script is
idempotent and writes atomically, so it is safe to use during a controlled
runtime rollout.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

POOL_CONFIG: dict[str, dict[str, Any]] = {
    "antigravity_main": {"state": "healthy", "max_concurrent": 3},
    "claude_main": {"state": "healthy", "max_concurrent": 2},
    "codex_bjoe": {"state": "healthy", "max_concurrent": 3},
    "codex_lupin": {"state": "healthy", "max_concurrent": 2},
    "codex_ajoe": {"state": "disabled", "max_concurrent": 0},
}


def configure(config: dict[str, Any]) -> dict[str, Any]:
    pools = config.setdefault("account_pools", {})
    pools.update(POOL_CONFIG)
    agents = config.setdefault("agents", {})

    for agent_id in ("antigravity", "antigravity2", "antigravity3", "antigravity4", "antigravity5", "antigravity6", "antigravity7"):
        if agent_id in agents:
            agents[agent_id]["account_pool"] = "antigravity_main"
    for agent_id in ("claude", "claude2", "claude3"):
        if agent_id in agents:
            agents[agent_id]["account_pool"] = "claude_main"
    if "codex" in agents:
        agents["codex"]["account_pool"] = "codex_bjoe"
    if "codex2" in agents:
        agents["codex2"]["account_pool"] = "codex_lupin"
    for agent_id in ("codex3", "codex4", "codex5", "codex6", "codex7", "codex8", "codex9"):
        if agent_id in agents:
            agents[agent_id]["account_pool"] = "codex_ajoe"

    # Dedicated executable slots. Logical agent names remain ownership roles;
    # slots are the only records allowed to consume pool concurrency.
    slots = {
        "antigravity_slot_1": ("antigravity", "antigravity_main"),
        "antigravity_slot_2": ("antigravity2", "antigravity_main"),
        "antigravity_slot_3": ("antigravity3", "antigravity_main"),
        "claude_slot_1": ("claude", "claude_main"),
        "claude_slot_2": ("claude", "claude_main"),
        "codex_lupin_slot_1": ("codex2", "codex_lupin"),
        "codex_lupin_slot_2": ("codex2", "codex_lupin"),
        "codex_bjoe_slot_1": ("codex", "codex_bjoe"),
        "codex_bjoe_slot_2": ("codex", "codex_bjoe"),
        "codex_bjoe_slot_3": ("codex", "codex_bjoe"),
    }
    for slot_id, (provider, pool) in slots.items():
        adapter = "claude_cli" if provider == "claude" else ("codex" if provider.startswith("codex") else "antigravity")
        agents[slot_id] = {
            "id": slot_id,
            "display_name": slot_id,
            "provider": provider,
            "adapter": adapter,
            "account_pool": pool,
            "dispatch_slot_for_pool": pool,
            "slot_id": slot_id,
        }
    for slot_id in ("codex_ajoe_slot_1", "codex_ajoe_slot_2", "codex_ajoe_slot_3"):
        agents.pop(slot_id, None)

    ready = config.setdefault("ready_dispatcher", {})
    active = ready.get("active_worker_statuses", [])
    ready["active_worker_statuses"] = [status for status in active if status != "manual_pending"]
    ready["max_concurrent_per_quota_group"] = {
        "antigravity_main": 3,
        "claude_main": 2,
        "codex_bjoe": 3,
        "codex_lupin": 2,
        "codex_ajoe": 0,
    }
    ready["max_concurrent_workers"] = 10
    ready["max_dispatches_per_tick"] = min(10, max(1, int(ready.get("max_dispatches_per_tick", 10))))
    config.setdefault("worker_worktrees", {})["recover_clean_diverged_worktrees"] = True
    return config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--check", action="store_true", help="report whether the migration would change the file")
    args = parser.parse_args()
    original = args.config.read_text(encoding="utf-8")
    data = json.loads(original)
    updated = json.dumps(configure(data), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        print("changed" if updated != original else "unchanged")
        return 0
    if updated == original:
        print("unchanged")
        return 0
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=args.config.parent, delete=False) as tmp:
        tmp.write(updated)
        temp_name = tmp.name
    os.replace(temp_name, args.config)
    print(f"updated {args.config}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
