from __future__ import annotations

from datetime import UTC, datetime, timedelta

import capacity_controller
import supervisor

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def config(slot_count: int = 8) -> dict:
    return {
        "agents": {
            "Claude": {"provider": "claude", "slot_id": "slot-0"},
            "Codex": {"provider": "codex", "slot_id": "slot-1"},
            "Claude2": {"provider": "claude", "slot_id": "slot-2"},
            "Antigravity2": {"provider": "antigravity", "slot_id": "slot-3"},
            "Antigravity5": {"provider": "antigravity", "slot_id": "slot-4"},
            "Antigravity7": {"provider": "antigravity", "slot_id": "slot-5"},
            "Codex2": {"provider": "codex", "slot_id": "slot-6"},
            "Human/Ops": {"provider": "human_gate"},
            **{
                f"slot-{index}": {"slot_id": f"slot-{index}"}
                for index in range(7, slot_count)
            },
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


def canonical_runnable_ids(cfg: dict, tasks: list[dict]) -> set[str]:
    return supervisor.canonical_dispatchable_task_ids(cfg, tasks)


def test_chair_never_approves_sidecars_while_canonical_work_is_runnable() -> None:
    runtime_state = {
        "capacity_controller": {
            "underutilization_since": "2026-08-20T11:40:00Z",
        }
    }
    tasks = [{"id": "CANON-001", "status": "todo", "owner": "Claude"}]

    controller, changed = capacity_controller.evaluate_chair(
        config(), runtime_state, tasks, runnable_tasks=canonical_runnable_ids(config(), tasks), now=NOW
    )

    assert changed is True
    assert controller["chair_decision"]["approve_helper_wave"] is True
    assert controller["chair_decision"]["sidecar_wave"]["approved"] is False
    assert capacity_controller.sidecar_candidates(config(), runtime_state, tasks, runnable_tasks=canonical_runnable_ids(config(), tasks), now=NOW) == []


def test_chair_approves_bounded_sidecars_after_sustained_idle_capacity() -> None:
    runtime_state = {
        "capacity_controller": {
            "underutilization_since": "2026-08-20T11:40:00Z",
        }
    }
    tasks = [
        {"id": f"BLOCKED-{index}", "status": "blocked", "blocked_reason": "upstream API defect", "owner": "Claude"}
        for index in range(4)
    ]
    tasks.append(
        {"id": "HARD-GATE", "status": "blocked", "blocked_reason": "manual approval required", "owner": "Human/Ops"}
    )

    capacity_controller.evaluate_chair(config(), runtime_state, tasks, runnable_tasks=canonical_runnable_ids(config(), tasks), now=NOW)
    candidates = capacity_controller.sidecar_candidates(config(), runtime_state, tasks, runnable_tasks=canonical_runnable_ids(config(), tasks), now=NOW)

    # Eight slots at a 25% cap permits two sidecars, even though the wave cap is three.
    assert len(candidates) == 2
    assert {candidate["helper_parent"] for candidate in candidates} == {"BLOCKED-0", "BLOCKED-1"}
    assert all(candidate["task_class"] == "sidecar" for candidate in candidates)
    assert all(candidate["mutates_canonical"] is False for candidate in candidates)
    assert all(candidate["owner"] == "AUTO_ASSIGN" for candidate in candidates)
    assert all(candidate["expires_at"] == "2026-08-20T14:00:00Z" for candidate in candidates)


def test_sidecar_wave_requires_a_current_chair_decision() -> None:
    tasks = [{"id": "BLOCKED-1", "status": "blocked", "blocked_reason": "transient defect", "owner": "Claude"}]
    expired_state = {
        "capacity_controller": {
            "chair_decision": {
                "valid_until": "2026-08-20T11:59:59Z",
                "sidecar_wave": {"approved": True},
            }
        }
    }

    assert capacity_controller.sidecar_candidates(config(), expired_state, tasks, runnable_tasks=canonical_runnable_ids(config(), tasks), now=NOW) == []


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
        {"id": "NON-DISP-001", "status": "todo", "owner": "Claude", "non_dispatchable": True},
        {"id": "BLOCKED-001", "status": "blocked", "owner": "Claude", "blocked_reason": "waiting for external fix"},
        {"id": "REVIEW-001", "status": "review", "owner": "Claude", "reviewer": "Codex"},
        {"id": "APPROVED-001", "status": "review_approved", "owner": "Claude", "reviewer": "Codex"},
        {"id": "UNSAT-DEP-001", "status": "todo", "owner": "Claude", "depends_on": ["NON-EXISTENT-DEP"]},
    ]
    snapshot = capacity_controller.capacity_snapshot(config(), {}, tasks, runnable_tasks=canonical_runnable_ids(config(), tasks))
    assert snapshot["runnable_tasks"] == 0

    runtime_state: dict = {}
    controller, changed = capacity_controller.evaluate_chair(
        config(), runtime_state, tasks, runnable_tasks=canonical_runnable_ids(config(), tasks), now=NOW
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

    snapshot = capacity_controller.capacity_snapshot(config(), {}, tasks, runnable_tasks=canonical_runnable_ids(config(), tasks))
    assert snapshot["runnable_tasks"] == 0, f"Expected 0 runnable tasks, got {snapshot['runnable_tasks']}"

    runtime_state = {
        "capacity_controller": {
            "underutilization_since": "2026-08-20T11:40:00Z",
        }
    }
    controller, changed = capacity_controller.evaluate_chair(
        config(), runtime_state, tasks, runnable_tasks=canonical_runnable_ids(config(), tasks), now=NOW
    )
    decision = controller.get("chair_decision") or {}
    assert decision["sidecar_wave"]["approved"] is True
    assert "runnable_work_without_active_workers" not in decision.get("reasons", [])


def test_runnable_todo_task_approves_helper_wave_when_slots_available() -> None:
    tasks = [
        {"id": "DEP-DONE", "status": "done"},
        {"id": "CANON-RUNNABLE-001", "status": "todo", "owner": "Claude", "depends_on": ["DEP-DONE"]},
    ]
    snapshot = capacity_controller.capacity_snapshot(config(), {}, tasks, runnable_tasks=canonical_runnable_ids(config(), tasks))
    assert snapshot["runnable_tasks"] == 1

    runtime_state = {
        "capacity_controller": {
            "underutilization_since": "2026-08-20T11:40:00Z",
        }
    }
    controller, changed = capacity_controller.evaluate_chair(
        config(), runtime_state, tasks, runnable_tasks=canonical_runnable_ids(config(), tasks), now=NOW
    )
    decision = controller.get("chair_decision") or {}
    assert decision["approve_helper_wave"] is True
    assert decision["sidecar_wave"]["approved"] is False


def test_capacity_snapshot_with_custom_schema_and_invalid_task_id_or_owner() -> None:
    cfg = config()
    cfg["schema"] = {
        "tasks_path": "items",
        "task_id_field": "taskId",
        "assignee_field": "assignee",
    }
    tasks = [
        # Valid runnable task
        {"taskId": "T-1", "status": "todo", "assignee": "Claude"},
        # Missing taskId
        {"status": "todo", "assignee": "Claude"},
        # Empty taskId
        {"taskId": "", "status": "todo", "assignee": "Claude"},
        # Unknown owner
        {"taskId": "T-2", "status": "todo", "assignee": "NonExistentAgent"},
        # Human/Ops assignee
        {"taskId": "T-3", "status": "todo", "assignee": "Human/Ops"},
        # Missing assignee
        {"taskId": "T-4", "status": "todo"},
    ]
    snapshot = capacity_controller.capacity_snapshot(cfg, {}, tasks, runnable_tasks=canonical_runnable_ids(cfg, tasks))
    assert snapshot["runnable_tasks"] == 1


def test_capacity_and_supervisor_dispatch_share_identical_runnable_truth() -> None:
    cfg = config()
    tasks = [
        {"id": "T-VALID-TODO", "status": "todo", "owner": "Claude"},
        {"id": "T-VALID-INPROG", "status": "in_progress", "owner": "Codex"},
        {"id": "T-HUMAN-GATE", "status": "todo", "owner": "Human/Ops", "task_class": "human_gate"},
        {"id": "T-SIDECAR", "status": "todo", "owner": "Claude", "task_class": "sidecar"},
        {"id": "T-BLOCKED", "status": "blocked", "owner": "Claude"},
        {"id": "T-REVIEW", "status": "review", "owner": "Claude", "reviewer": "Codex"},
        {"id": "T-APPROVED", "status": "review_approved", "owner": "Claude", "reviewer": "Codex"},
        {"id": "T-DONE", "status": "done", "owner": "Claude"},
        {"id": "T-UNSAT-DEP", "status": "todo", "owner": "Claude", "depends_on": ["MISSING-DEP"]},
        {"id": "", "status": "todo", "owner": "Claude"},
        {"id": "T-UNKNOWN-OWNER", "status": "todo", "owner": "MysteryAgent"},
    ]
    runnable_ids = canonical_runnable_ids(cfg, tasks)
    assert runnable_ids == {"T-VALID-TODO", "T-VALID-INPROG"}

    # Snapshot consumes the exact Dispatcher-backed task-id set.
    snapshot = capacity_controller.capacity_snapshot(cfg, {}, tasks, runnable_tasks=runnable_ids)
    assert snapshot["runnable_tasks"] == 2


def test_custom_schema_dependency_requires_canonical_dependency_id() -> None:
    cfg = config()
    cfg["schema"] = {
        "tasks_path": "items",
        "task_id_field": "taskId",
        "assignee_field": "assignee",
    }
    runnable = {
        "taskId": "RUN",
        "status": "todo",
        "assignee": "Claude",
        "depends_on": ["DEP"],
    }
    # The legacy id is deliberately present but is not the configured task ID.
    legacy_dependency = {"id": "DEP", "status": "done"}
    tasks = [runnable, legacy_dependency]

    assert canonical_runnable_ids(cfg, tasks) == set()
    snapshot = capacity_controller.capacity_snapshot(
        cfg,
        {},
        tasks,
        runnable_tasks=canonical_runnable_ids(cfg, tasks),
    )
    assert snapshot["runnable_tasks"] == 0

    # When dependency has the canonical taskId field, it is runnable.
    canonical_dependency = {"taskId": "DEP", "status": "done"}
    canonical_tasks = [runnable, canonical_dependency]
    assert canonical_runnable_ids(cfg, canonical_tasks) == {"RUN"}
    snapshot_ok = capacity_controller.capacity_snapshot(
        cfg,
        {},
        canonical_tasks,
        runnable_tasks=canonical_runnable_ids(cfg, canonical_tasks),
    )
    assert snapshot_ok["runnable_tasks"] == 1


def test_custom_schema_sidecar_parent_requires_canonical_task_id() -> None:
    cfg = config()
    cfg["schema"] = {"tasks_path": "items", "task_id_field": "taskId"}
    tasks = [
        {
            "id": "LEGACY-PARENT",
            "status": "blocked",
            "blocked_reason": "upstream API defect",
        }
    ]
    runtime_state = {
        "capacity_controller": {
            "chair_decision": {
                "valid_until": "2026-08-20T12:30:00Z",
                "sidecar_wave": {"approved": True},
            }
        }
    }

    assert (
        capacity_controller.sidecar_candidates(
            cfg, runtime_state, tasks, runnable_tasks=set(), now=NOW
        )
        == []
    )


def test_sidecar_candidates_fail_closed_when_runnable_work_appears_during_valid_approval() -> None:
    """Regression test: sidecar_candidates must fail closed when canonical runnable work exists,

    even if chair_decision was previously approved and is still within valid_until.
    """
    runtime_state = {
        "capacity_controller": {
            "chair_decision": {
                "issued_at": "2026-08-20T11:50:00Z",
                "valid_until": "2026-08-20T12:30:00Z",
                "sidecar_wave": {"approved": True},
            }
        }
    }
    tasks = [
        {"id": "BLOCKED-1", "status": "blocked", "blocked_reason": "upstream API defect", "owner": "Claude"},
        {"id": "RUN-1", "status": "todo", "owner": "Claude"},
    ]
    cfg = config()
    runnable_ids = canonical_runnable_ids(cfg, tasks)
    assert runnable_ids == {"RUN-1"}

    candidates = capacity_controller.sidecar_candidates(
        cfg,
        runtime_state,
        tasks,
        runnable_tasks=runnable_ids,
        now=NOW,
    )
    assert candidates == []


def test_reviewer_active_does_not_offset_unrelated_owner_runnable_task() -> None:
    """Regression test: 1 reviewer active + 1 unrelated owner runnable must approve helper wave.

    Slot accounting keeps counting the reviewer towards available_slots and
    utilization_ratio, but helper backlog ignores the reviewer since its task_id
    is not in the canonical runnable set.
    """
    cfg = config(slot_count=8)
    tasks = [
        {"id": "TASK-REVIEW-001", "status": "review", "owner": "Claude", "reviewer": "Codex"},
        {"id": "TASK-RUNNABLE-002", "status": "todo", "owner": "Claude2"},
    ]
    runtime_state = {
        "workers": {
            "run-reviewer": {
                "task_id": "TASK-REVIEW-001",
                "status": "running",
                "agent_id": "Codex",
            }
        }
    }
    runnable_ids = canonical_runnable_ids(cfg, tasks)
    assert runnable_ids == {"TASK-RUNNABLE-002"}

    snapshot = capacity_controller.capacity_snapshot(
        cfg, runtime_state, tasks, runnable_tasks=runnable_ids
    )
    assert snapshot["slot_total"] == 8
    assert snapshot["active_workers"] == 1
    assert snapshot["active_runnable_workers"] == 0
    assert snapshot["available_slots"] == 7
    assert snapshot["runnable_tasks"] == 1
    assert snapshot["utilization_ratio"] == 0.125

    controller, changed = capacity_controller.evaluate_chair(
        cfg, runtime_state, tasks, runnable_tasks=runnable_ids, now=NOW
    )
    assert changed is True
    decision = controller["chair_decision"]
    assert decision["approve_helper_wave"] is True
    assert decision["max_helper_claims"] == 4


def test_finalizer_active_does_not_offset_unrelated_owner_runnable_task() -> None:
    """A finalizer active on a review_approved task must not offset an unrelated owner runnable."""
    cfg = config(slot_count=8)
    tasks = [
        {"id": "TASK-APPROVED-001", "status": "review_approved", "owner": "Claude", "reviewer": "Codex"},
        {"id": "TASK-RUNNABLE-002", "status": "todo", "owner": "Claude2"},
    ]
    runtime_state = {
        "workers": {
            "run-finalizer": {
                "task_id": "TASK-APPROVED-001",
                "status": "running",
                "agent_id": "Claude",
            }
        }
    }
    runnable_ids = canonical_runnable_ids(cfg, tasks)
    assert runnable_ids == {"TASK-RUNNABLE-002"}

    snapshot = capacity_controller.capacity_snapshot(
        cfg, runtime_state, tasks, runnable_tasks=runnable_ids
    )
    assert snapshot["active_workers"] == 1
    assert snapshot["active_runnable_workers"] == 0
    assert snapshot["available_slots"] == 7

    controller, _ = capacity_controller.evaluate_chair(
        cfg, runtime_state, tasks, runnable_tasks=runnable_ids, now=NOW
    )
    assert controller["chair_decision"]["approve_helper_wave"] is True


def test_active_worker_on_runnable_task_suppresses_helper_wave() -> None:
    """An active worker executing the only runnable task satisfies backlog and suppresses helper wave."""
    cfg = config(slot_count=8)
    tasks = [
        {"id": "TASK-RUNNABLE-001", "status": "in_progress", "owner": "Claude"},
    ]
    runtime_state = {
        "workers": {
            "run-executing": {
                "task_id": "TASK-RUNNABLE-001",
                "status": "running",
                "agent_id": "Claude",
            }
        }
    }
    runnable_ids = canonical_runnable_ids(cfg, tasks)
    assert runnable_ids == {"TASK-RUNNABLE-001"}

    snapshot = capacity_controller.capacity_snapshot(
        cfg, runtime_state, tasks, runnable_tasks=runnable_ids
    )
    assert snapshot["active_workers"] == 1
    assert snapshot["active_runnable_workers"] == 1
    assert snapshot["runnable_tasks"] == 1

    controller, _ = capacity_controller.evaluate_chair(
        cfg, runtime_state, tasks, runnable_tasks=runnable_ids, now=NOW
    )
    assert controller["chair_decision"]["approve_helper_wave"] is False


def test_int_only_legacy_runnable_tasks_conservative_fallback() -> None:
    """When runnable_tasks is passed as int, conservative fallback assumes all active workers are executing."""
    cfg = config(slot_count=8)
    runtime_state = {
        "workers": {
            "run-1": {"task_id": "TASK-A", "status": "running"},
            "run-2": {"task_id": "TASK-B", "status": "running"},
        }
    }
    # runnable_tasks=2 with 2 active workers: conservative fallback sets active_runnable_workers=2
    snapshot = capacity_controller.capacity_snapshot(
        cfg, runtime_state, [], runnable_tasks=2
    )
    assert snapshot["runnable_tasks"] == 2
    assert snapshot["active_workers"] == 2
    assert snapshot["active_runnable_workers"] == 2

    controller, _ = capacity_controller.evaluate_chair(
        cfg, runtime_state, [], runnable_tasks=2, now=NOW
    )
    assert controller["chair_decision"]["approve_helper_wave"] is False

    # runnable_tasks=3 with 2 active workers: 3 > 2 approves helper wave
    runtime_state3 = {
        "workers": {
            "run-1": {"task_id": "TASK-A", "status": "running"},
            "run-2": {"task_id": "TASK-B", "status": "running"},
        }
    }
    controller3, _ = capacity_controller.evaluate_chair(
        cfg, runtime_state3, [], runnable_tasks=3, now=NOW
    )
    assert controller3["chair_decision"]["approve_helper_wave"] is True


def test_custom_schema_active_worker_task_id_matching() -> None:
    """Active worker task ID extraction respects custom schema and matches against runnable set."""
    cfg = config(slot_count=8)
    cfg["schema"] = {
        "tasks_path": "items",
        "task_id_field": "taskId",
        "assignee_field": "assignee",
    }
    tasks = [
        {"taskId": "T-REV", "status": "review", "assignee": "Claude", "reviewer": "Codex"},
        {"taskId": "T-RUN", "status": "todo", "assignee": "Claude2"},
    ]
    runtime_state = {
        "workers": {
            "run-rev": {"taskId": "T-REV", "status": "running"},
            "run-run": {"taskId": "T-RUN", "status": "running"},
        }
    }
    runnable_ids = canonical_runnable_ids(cfg, tasks)
    assert runnable_ids == {"T-RUN"}

    snapshot = capacity_controller.capacity_snapshot(
        cfg, runtime_state, tasks, runnable_tasks=runnable_ids
    )
    assert snapshot["active_workers"] == 2
    assert snapshot["runnable_tasks"] == 1
    assert snapshot["active_runnable_workers"] == 1

    controller, _ = capacity_controller.evaluate_chair(
        cfg, runtime_state, tasks, runnable_tasks=runnable_ids, now=NOW
    )
    assert controller["chair_decision"]["approve_helper_wave"] is False


def test_sidecar_task_id_short_ids_remain_unchanged() -> None:
    """Short sidecar IDs (<= 48 chars) must remain identical to the legacy format."""
    # 42 chars
    short_id = capacity_controller.build_sidecar_task_id("BLOCKED-0", "blocked_task_diagnostics")
    assert short_id == "BLOCKED-0-SIDECAR-BLOCKED-TASK-DIAGNOSTICS"
    assert len(short_id) == 42

    # 34 chars
    bff_id = capacity_controller.build_sidecar_task_id("APP-001", "bff_handoff_packet")
    assert bff_id == "APP-001-SIDECAR-BFF-HANDOFF-PACKET"
    assert len(bff_id) == 34

    # Exact boundary test: 48 chars
    # "P" * 15 (15) + "-SIDECAR-" (9) + "K" * 24 (24) = 48
    parent_exact = "P" * 15
    kind_exact = "k" * 24
    exact_id = capacity_controller.build_sidecar_task_id(parent_exact, kind_exact)
    assert exact_id == f"{parent_exact}-SIDECAR-{kind_exact.upper()}"
    assert len(exact_id) == 48


def test_sidecar_task_id_bounded_and_deterministic_for_long_parent_and_kind() -> None:
    """Long sidecar IDs (> 48 chars) must be deterministic, <= 48 chars, and contain -SIDECAR-."""
    long_parent = "HUMAN-OSS-LEGAL-APPROVAL-001"
    kind = "blocked_task_diagnostics"

    # Determinism
    id1 = capacity_controller.build_sidecar_task_id(long_parent, kind)
    id2 = capacity_controller.build_sidecar_task_id(long_parent, kind)
    assert id1 == id2
    assert len(id1) <= 48
    assert "-SIDECAR-" in id1
    assert id1.startswith("HUMAN-OSS-LEGAL-APPROVAL-001-SIDECAR-")

    # Long parent + long kind
    very_long_parent = "ODP-CI-DEV-MERGE-RELEASE-NOGO-DEADLOCK-001"
    very_long_kind = "deep_diagnostic_remediation_investigation_packet"
    long_id = capacity_controller.build_sidecar_task_id(very_long_parent, very_long_kind)
    assert len(long_id) <= 48
    assert "-SIDECAR-" in long_id

    # 100+ character inputs
    huge_parent = "X" * 120
    huge_kind = "y" * 120
    huge_id = capacity_controller.build_sidecar_task_id(huge_parent, huge_kind)
    assert len(huge_id) <= 48
    assert "-SIDECAR-" in huge_id


def test_sidecar_task_id_collision_resistance_for_long_parents_and_kinds() -> None:
    """Collision-resistant digest ensures distinct task IDs even when parent prefixes match."""
    # Two distinct long parents sharing the exact same 31-character prefix
    prefix = "ODP-VERY-LONG-TASK-PREFIX-NAME-"
    parent_a = f"{prefix}ALPHA-001"
    parent_b = f"{prefix}BETA-002"
    kind = "blocked_task_diagnostics"

    id_a = capacity_controller.build_sidecar_task_id(parent_a, kind)
    id_b = capacity_controller.build_sidecar_task_id(parent_b, kind)

    assert id_a != id_b
    assert len(id_a) <= 48
    assert len(id_b) <= 48

    # Same long parent with two distinct long kinds
    parent = "ODP-LONG-PARENT-SHARED-ACROSS-KINDS-001"
    kind_a = "deep_diagnostic_analysis_and_triage_packet_v1"
    kind_b = "deep_diagnostic_analysis_and_triage_packet_v2"

    id_kind_a = capacity_controller.build_sidecar_task_id(parent, kind_a)
    id_kind_b = capacity_controller.build_sidecar_task_id(parent, kind_b)

    assert id_kind_a != id_kind_b
    assert len(id_kind_a) <= 48
    assert len(id_kind_b) <= 48

    # Pairwise uniqueness across a matrix of 10 long parents and 10 long kinds
    parents = [f"LONG-PARENT-ENTITY-SPECIFIER-{i:03d}" for i in range(10)]
    kinds = [f"long_diagnostic_investigation_kind_{j:03d}" for j in range(10)]
    generated_ids = {
        capacity_controller.build_sidecar_task_id(p, k)
        for p in parents
        for k in kinds
    }
    assert len(generated_ids) == 100
    assert all(len(sid) <= 48 for sid in generated_ids)
    assert all("-SIDECAR-" in sid for sid in generated_ids)


def test_sidecar_candidates_with_long_parent_preserves_authoritative_fields_and_artifacts() -> None:
    """sidecar_candidates retains full helper_parent/helper_kind while generating bounded task ID and correct artifact paths."""
    runtime_state = {
        "capacity_controller": {
            "underutilization_since": "2026-08-20T11:40:00Z",
        }
    }
    tasks = [
        {
            "id": "HUMAN-OSS-LEGAL-APPROVAL-001",
            "status": "blocked",
            "blocked_reason": "upstream API defect",
            "owner": "Claude",
        },
        {
            "id": "ODP-EPHEMERAL-STAGING-ROLLOUT-001",
            "status": "blocked",
            "blocked_reason": "upstream API defect",
            "owner": "Claude",
        },
    ]

    capacity_controller.evaluate_chair(
        config(), runtime_state, tasks, runnable_tasks=canonical_runnable_ids(config(), tasks), now=NOW
    )
    candidates = capacity_controller.sidecar_candidates(
        config(), runtime_state, tasks, runnable_tasks=canonical_runnable_ids(config(), tasks), now=NOW
    )

    assert len(candidates) == 2
    for candidate in candidates:
        sid = candidate["id"]
        parent_id = candidate["helper_parent"]
        kind = candidate["helper_kind"]

        assert len(sid) <= 48
        assert "-SIDECAR-" in sid
        assert parent_id in {"HUMAN-OSS-LEGAL-APPROVAL-001", "ODP-EPHEMERAL-STAGING-ROLLOUT-001"}
        assert kind == "blocked_task_diagnostics"
        assert candidate["task_class"] == "sidecar"
        assert candidate["artifacts"] == [f"support/sidecars/{parent_id}/{sid}.md"]
        assert parent_id in candidate["title"]
        assert parent_id in candidate["summary_zh"]


def test_capacity_snapshot_excludes_unauthenticated_or_inbox_fallback_slots_from_effective_capacity() -> None:
    """When a provider's auth is false or fallback to inbox, its slots are excluded from effective slot_total and available_slots."""
    cfg = config(slot_count=8)
    provider_report = {
        "providers": {
            "claude": {
                "auth_ready": False,
                "local_cli_worker_supported": False,
                "supports_auto_approve": False,
            },
            "antigravity": {
                "auth_ready": True,
                "local_cli_worker_supported": True,
                "supports_auto_approve": True,
            },
            "codex": {
                "auth_ready": True,
                "local_cli_worker_supported": True,
                "supports_auto_approve": True,
            },
        },
        "agent_adapters": {
            "Claude": {"can_auto_deliver": False, "supported": True, "notes": "Claude CLI is installed but not authenticated, so delivery falls back to the workspace inbox path."},
            "Claude2": {"can_auto_deliver": False, "supported": True, "notes": "Claude CLI is installed but not authenticated, so delivery falls back to the workspace inbox path."},
        },
    }
    # 8 configured slots in config(): Claude (slot-0), Codex (slot-1), Claude2 (slot-2), Antigravity2 (slot-3),
    # Antigravity5 (slot-4), Antigravity7 (slot-5), Codex2 (slot-6), slot-7.
    # Claude and Claude2 are unauthenticated (2 slots), so effective slot_total is 6.
    tasks: list[dict] = []
    snapshot = capacity_controller.capacity_snapshot(
        cfg,
        {},
        tasks,
        runnable_tasks=canonical_runnable_ids(cfg, tasks),
        provider_report=provider_report,
    )
    assert snapshot["configured_slot_total"] == 8
    assert snapshot["slot_total"] == 6
    assert snapshot["available_slots"] == 6
    assert snapshot["active_workers"] == 0
    assert snapshot["utilization_ratio"] == 0.0


def test_capacity_snapshot_and_evaluate_chair_no_false_underutilization_when_provider_auth_false() -> None:
    """When all configured slots belong to an unauthenticated provider, effective capacity is 0 and does not trigger false underutilization."""
    cfg = {
        "agents": {
            "claude_slot_1": {"provider": "claude", "slot_id": "claude_slot_1", "account_pool": "claude_main"},
            "claude_slot_2": {"provider": "claude", "slot_id": "claude_slot_2", "account_pool": "claude_main"},
        },
        "account_pools": {
            "claude_main": {"max_concurrent": 2, "state": "healthy"},
        },
        "capacity_controller": {
            "underutilization_threshold_ratio": 0.5,
            "underutilization_window_seconds": 600,
            "stall_window_seconds": 300,
            "chair_interval_seconds": 1800,
            "coordination_reserved_slots": 1,
            "sidecars": {"max_new_per_wave": 3, "max_active": 4, "max_capacity_ratio": 0.25, "ttl_seconds": 7200},
        },
    }
    provider_report = {
        "providers": {
            "claude": {
                "auth_ready": False,
                "local_cli_worker_supported": False,
                "supports_auto_approve": False,
            }
        },
        "agent_adapters": {
            "claude_slot_1": {"can_auto_deliver": False, "notes": "inbox fallback"},
            "claude_slot_2": {"can_auto_deliver": False, "notes": "inbox fallback"},
        },
    }
    tasks = [{"id": "BLOCKED-1", "status": "blocked", "blocked_reason": "upstream bug", "owner": "claude_slot_1"}]
    runtime_state = {
        "capacity_controller": {
            "underutilization_since": "2026-08-20T11:40:00Z",
        }
    }

    snapshot = capacity_controller.capacity_snapshot(
        cfg,
        runtime_state,
        tasks,
        runnable_tasks=set(),
        provider_report=provider_report,
    )
    assert snapshot["configured_slot_total"] == 2
    assert snapshot["slot_total"] == 0
    assert snapshot["available_slots"] == 0
    assert snapshot["utilization_ratio"] == 0.0

    controller, changed = capacity_controller.evaluate_chair(
        cfg,
        runtime_state,
        tasks,
        runnable_tasks=set(),
        provider_report=provider_report,
        now=NOW,
    )
    decision = controller.get("chair_decision") or {}
    # With effective slot_total == 0, underutilization is not true, so sidecars must NOT be approved
    assert decision.get("sidecar_wave", {}).get("approved") is False
    assert capacity_controller.sidecar_candidates(
        cfg,
        runtime_state,
        tasks,
        runnable_tasks=set(),
        provider_report=provider_report,
        now=NOW,
    ) == []


def test_capacity_automatically_restores_next_tick_on_relogin_without_clearing_state() -> None:
    """When provider report updates to authenticated on next tick, capacity snapshot restores automatically without manual state clearing."""
    cfg = config(slot_count=8)
    report_unauth = {
        "providers": {"claude": {"auth_ready": False, "local_cli_worker_supported": False}},
        "agent_adapters": {
            "Claude": {"can_auto_deliver": False},
            "Claude2": {"can_auto_deliver": False},
        },
    }
    report_auth = {
        "providers": {"claude": {"auth_ready": True, "local_cli_worker_supported": True, "supports_auto_approve": True}},
        "agent_adapters": {
            "Claude": {"can_auto_deliver": True, "supported": True},
            "Claude2": {"can_auto_deliver": True, "supported": True},
        },
    }
    runtime_state = {"capacity_controller": {}}

    # Tick 1: unauthenticated
    snap1 = capacity_controller.capacity_snapshot(
        cfg, runtime_state, [], runnable_tasks=set(), provider_report=report_unauth
    )
    assert snap1["configured_slot_total"] == 8
    assert snap1["slot_total"] == 6

    # Tick 2: user logs in and provider report refreshed without changing runtime_state
    snap2 = capacity_controller.capacity_snapshot(
        cfg, runtime_state, [], runnable_tasks=set(), provider_report=report_auth
    )
    assert snap2["configured_slot_total"] == 8
    assert snap2["slot_total"] == 8
    assert snap2["available_slots"] == 8


def test_claude_auth_status_logged_in_but_oauth_inference_expired_regression() -> None:
    """Regression test: claude auth status says loggedIn=true, but OAuth inference/expiry makes auth_ready=False.

    Ensures capacity snapshot excludes Claude slots from effective capacity while keeping configured_slot_total.
    """
    cfg = config(slot_count=8)
    # Simulates provider_capabilities output when claude auth status returned loggedIn=true
    # but token expiration check determined OAuth is expired / inference failed.
    provider_report = {
        "providers": {
            "claude": {
                "auth_ready": False,
                "local_cli_worker_supported": False,
                "supports_auto_approve": False,
                "notes": [
                    "Claude CLI is installed but not authenticated; inbox fallback is disabled for this provider.",
                ],
            }
        },
        "agent_adapters": {
            "Claude": {
                "can_auto_deliver": False,
                "supported": True,
                "delivery_mode": "file_inbox",
                "notes": "Claude CLI is installed but not authenticated, so delivery falls back to the workspace inbox path.",
            },
            "Claude2": {
                "can_auto_deliver": False,
                "supported": True,
                "delivery_mode": "file_inbox",
                "notes": "Claude CLI is installed but not authenticated, so delivery falls back to the workspace inbox path.",
            },
        },
    }
    tasks = [{"id": "T-1", "status": "todo", "owner": "Claude"}]
    snapshot = capacity_controller.capacity_snapshot(
        cfg,
        {},
        tasks,
        runnable_tasks={"T-1"},
        provider_report=provider_report,
    )
    # 2 Claude slots excluded from effective slot_total (6 instead of 8)
    assert snapshot["configured_slot_total"] == 8
    assert snapshot["slot_total"] == 6
    assert snapshot["available_slots"] == 6

