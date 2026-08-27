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


def test_capacity_snapshot_excludes_human_gate_non_dispatchable_review_and_blocked() -> None:
    tasks = [
        {"id": "HG-001", "status": "todo", "owner": "Human/Ops", "task_class": "human_gate"},
        {"id": "NON-DISP-001", "status": "todo", "non_dispatchable": True},
        {"id": "BLOCKED-001", "status": "blocked", "blocked_reason": "waiting for external fix"},
        {"id": "REVIEW-001", "status": "review", "owner": "Claude", "reviewer": "Codex"},
        {"id": "APPROVED-001", "status": "review_approved", "owner": "Claude", "reviewer": "Codex"},
        {"id": "UNSAT-DEP-001", "status": "todo", "depends_on": ["NON-EXISTENT-DEP"]},
    ]
    snapshot = capacity_controller.capacity_snapshot(config(), {}, tasks)
    assert snapshot["runnable_tasks"] == 0

    runtime_state: dict = {}
    controller, changed = capacity_controller.evaluate_chair(
        config(), runtime_state, tasks, now=NOW
    )
    decision = controller.get("chair_decision") or {}
    assert "runnable_work_without_active_workers" not in decision.get("reasons", [])
    assert controller.get("stall_since") is None


def test_current_active_tasks_regression_fixture_runnable_is_zero() -> None:
    """Regression fixture with the 5 active tasks from live board.

    Proves runnable_tasks=0 instead of false positive 4.
    """
    tasks = [
        {
            "id": "HUMAN-OSS-LEGAL-APPROVAL-001",
            "title": "逐一決定每個資料來源與 OSS 的許可及義務",
            "phase": "OSS production compliance",
            "owner": "Human/Ops",
            "reviewer": "Claude2",
            "status": "todo",
            "depends_on": ["XR-EXT-OSS-FINAL-AUDIT-001"],
            "task_class": "human_gate",
        },
        {
            "id": "XR-SOURCE-APPROVAL-ACTIVATION-001",
            "title": "依逐來源許可決定啟用自動更新，未核准來源保持關閉",
            "phase": "Unassigned",
            "owner": "Claude",
            "reviewer": "Antigravity5",
            "status": "todo",
            "depends_on": ["HUMAN-OSS-LEGAL-APPROVAL-001"],
            "task_class": "implementation",
        },
        {
            "id": "ODP-EPHEMERAL-STAGING-ROLLOUT-001",
            "title": "建立 ephemeral staging 並完成全套 release rehearsal",
            "phase": "Wave 3 - Staging Rollout",
            "owner": "Codex",
            "reviewer": "Claude",
            "status": "blocked",
            "depends_on": ["ODP-EPHEMERAL-STAGING-IAC-001", "ODP-DEV-ROLLOUT-001"],
            "task_class": "rollout",
            "waiting_for": "Human/Ops",
        },
        {
            "id": "ODP-PROD-BLUEGREEN-ROLLOUT-001",
            "title": "執行 production 0% green 驗證與 100% blue-green 切換",
            "phase": "Wave 4 - Production Rollout",
            "owner": "Antigravity7",
            "reviewer": "Claude2",
            "status": "todo",
            "depends_on": [
                "ODP-EPHEMERAL-STAGING-ROLLOUT-001",
                "ODP-GITHUB-GCP-ENV-BOOTSTRAP-001",
            ],
            "task_class": "rollout",
        },
        {
            "id": "ODP-POSTDEPLOY-WATCH-CLOSEOUT-001",
            "title": "完成 production watch、staging cleanup 與 release archive",
            "phase": "Wave 4 - Release Closeout",
            "owner": "Codex2",
            "reviewer": "Antigravity2",
            "status": "todo",
            "depends_on": ["ODP-PROD-BLUEGREEN-ROLLOUT-001"],
            "task_class": "rollout",
        },
    ]

    snapshot = capacity_controller.capacity_snapshot(config(), {}, tasks)
    assert snapshot["runnable_tasks"] == 0, f"Expected 0 runnable tasks, got {snapshot['runnable_tasks']}"

    runtime_state = {
        "capacity_controller": {
            "underutilization_since": "2026-08-20T11:40:00Z",
        }
    }
    controller, changed = capacity_controller.evaluate_chair(
        config(), runtime_state, tasks, now=NOW
    )
    decision = controller.get("chair_decision") or {}
    assert decision["sidecar_wave"]["approved"] is True
    assert "runnable_work_without_active_workers" not in decision.get("reasons", [])


def test_runnable_todo_task_approves_helper_wave_when_slots_available() -> None:
    tasks = [
        {"id": "DEP-DONE", "status": "done"},
        {"id": "CANON-RUNNABLE-001", "status": "todo", "owner": "Claude", "depends_on": ["DEP-DONE"]},
    ]
    snapshot = capacity_controller.capacity_snapshot(config(), {}, tasks)
    assert snapshot["runnable_tasks"] == 1

    runtime_state = {
        "capacity_controller": {
            "underutilization_since": "2026-08-20T11:40:00Z",
        }
    }
    controller, changed = capacity_controller.evaluate_chair(
        config(), runtime_state, tasks, now=NOW
    )
    decision = controller.get("chair_decision") or {}
    assert decision["approve_helper_wave"] is True
    assert decision["sidecar_wave"]["approved"] is False
