from __future__ import annotations

from datetime import UTC, datetime, timedelta

import capacity_controller

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def config(slot_count: int = 8) -> dict:
    return {
        "agents": {
            f"slot-{index}": {"slot_id": f"slot-{index}"}
            for index in range(slot_count)
        },
        "capacity_controller": {
            "chair_interval_seconds": 1800,
            "underutilization_window_seconds": 600,
            "coordination_reserved_slots": 1,
            "sidecars": {
                "max_new_per_wave": 3,
                "max_active": 4,
                "max_capacity_ratio": 0.25,
                "ttl_seconds": 7200,
            },
        },
    }


def test_chair_never_approves_sidecars_while_canonical_work_is_runnable() -> None:
    runtime_state = {
        "capacity_controller": {
            "underutilization_since": "2026-08-20T11:40:00Z",
        }
    }
    tasks = [{"id": "CANON-001", "status": "todo"}]

    controller, changed = capacity_controller.evaluate_chair(
        config(), runtime_state, tasks, now=NOW
    )

    assert changed is True
    assert controller["chair_decision"]["approve_helper_wave"] is True
    assert controller["chair_decision"]["sidecar_wave"]["approved"] is False
    assert capacity_controller.sidecar_candidates(config(), runtime_state, tasks, now=NOW) == []


def test_chair_approves_bounded_sidecars_after_sustained_idle_capacity() -> None:
    runtime_state = {
        "capacity_controller": {
            "underutilization_since": "2026-08-20T11:40:00Z",
        }
    }
    tasks = [
        {"id": f"BLOCKED-{index}", "status": "blocked", "blocked_reason": "upstream API defect"}
        for index in range(4)
    ]
    tasks.append(
        {"id": "HARD-GATE", "status": "blocked", "blocked_reason": "manual approval required"}
    )

    capacity_controller.evaluate_chair(config(), runtime_state, tasks, now=NOW)
    candidates = capacity_controller.sidecar_candidates(config(), runtime_state, tasks, now=NOW)

    # Eight slots at a 25% cap permits two sidecars, even though the wave cap is three.
    assert len(candidates) == 2
    assert {candidate["helper_parent"] for candidate in candidates} == {"BLOCKED-0", "BLOCKED-1"}
    assert all(candidate["task_class"] == "sidecar" for candidate in candidates)
    assert all(candidate["mutates_canonical"] is False for candidate in candidates)
    assert all(candidate["owner"] == "AUTO_ASSIGN" for candidate in candidates)
    assert all(candidate["expires_at"] == "2026-08-20T14:00:00Z" for candidate in candidates)


def test_sidecar_wave_requires_a_current_chair_decision() -> None:
    tasks = [{"id": "BLOCKED-1", "status": "blocked", "blocked_reason": "transient defect"}]
    expired_state = {
        "capacity_controller": {
            "chair_decision": {
                "valid_until": "2026-08-20T11:59:59Z",
                "sidecar_wave": {"approved": True},
            }
        }
    }

    assert capacity_controller.sidecar_candidates(config(), expired_state, tasks, now=NOW) == []


def test_expired_helper_execution_leases_are_reported_without_changing_owner() -> None:
    task = {
        "id": "CANON-LEASE-001",
        "status": "in_progress",
        "owner": "Claude",
        "helper_execution_lease": {
            "claimed_by": "Codex",
            "lease_expires_at": "2026-08-20T11:59:59Z",
        },
    }

    assert capacity_controller.expired_helper_claim_task_ids([task], now=NOW) == ["CANON-LEASE-001"]
    assert task["owner"] == "Claude"
    task["helper_execution_lease"]["lease_expires_at"] = capacity_controller._iso(
        NOW + timedelta(seconds=1)
    )
    assert capacity_controller.expired_helper_claim_task_ids([task], now=NOW) == []
