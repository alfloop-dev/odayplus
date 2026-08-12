#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).with_name("configure_account_pools.py")
SPEC = importlib.util.spec_from_file_location("configure_account_pools", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_configure_removes_competing_dispatch_and_retired_scheduler_paths() -> None:
    config = {
        "agents": {
            "antigravity": {"provider": "antigravity"},
            "antigravity2": {"provider": "antigravity2"},
            "codex": {"provider": "codex1", "adapter": "codex"},
            "codex2": {"provider": "codex2"},
        },
        "ready_dispatcher": {
            "helper_claim": {"enabled": True},
            "worker_self_claim": {"enabled": True},
            "max_tasks_per_agent_by_agent": {"Antigravity": 99},
            "target_workload": {"Antigravity": 99},
            "max_concurrent_per_quota_group": {"antigravity": 99},
            "active_worker_statuses": ["running", "manual_pending"],
        },
        "underutilization_dispatch": {"enabled": True},
        "chair_review": {"enabled": False},
    }

    updated = MODULE.configure(config)

    ready = updated["ready_dispatcher"]
    for retired_key in (
        "helper_claim",
        "worker_self_claim",
        "max_tasks_per_agent_by_agent",
        "target_workload",
        "max_concurrent_per_quota_group",
    ):
        assert retired_key not in ready
    assert ready["reviewer_failover"] == {"enabled": True}
    assert ready["active_worker_statuses"] == ["running"]
    assert "underutilization_dispatch" not in updated
    assert "chair_review" not in updated
    assert updated["account_pools"]["codex_bjoe"]["max_concurrent"] == 3
    assert updated["agents"]["antigravity2"]["account_pool"] == "antigravity_main"
    assert updated["agents"]["antigravity_slot_1"]["dispatch_slot_for_pool"] == "antigravity_main"
    assert updated["agents"]["codex_bjoe_slot_1"]["provider"] == "codex1"
