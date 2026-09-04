import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import dispatch_engine
import pytest
import supervisor
import worker_workspace
from adapters.base import DeliveryRequest
from dispatch_policy import (
    DEFAULT_ACTIVE_WORKER_STATUSES,
    DEFAULT_ORPHANED_QUEUE_EVENT_GRACE_SECONDS,
    REASON_HELPER_CLAIM,
    REASON_OWNED_FINALIZE,
    REASON_OWNED_IN_PROGRESS,
    REASON_OWNED_READY,
    REASON_REVIEW_READY,
    dispatch_reason_priority,
    is_execution_dispatch_reason,
    normalized_status_set,
    ready_dispatch_settings,
)


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (REASON_REVIEW_READY, 0),
        (REASON_OWNED_FINALIZE, 1),
        (REASON_OWNED_IN_PROGRESS, 2),
        (REASON_OWNED_READY, 3),
        (REASON_HELPER_CLAIM, 4),
        ("discussion_planning_readout_dispatch", None),
        (None, None),
    ],
)
def test_dispatch_reason_priority_cases(reason: str | None, expected: int | None) -> None:
    assert dispatch_reason_priority(reason) == expected


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (REASON_REVIEW_READY, True),
        (REASON_OWNED_FINALIZE, True),
        (REASON_OWNED_IN_PROGRESS, True),
        (REASON_OWNED_READY, True),
        (REASON_HELPER_CLAIM, True),
        ("discussion_planning_baton_dispatch", False),
        ("", False),
        (None, False),
    ],
)
def test_is_execution_dispatch_reason_cases(reason: str | None, expected: bool) -> None:
    assert is_execution_dispatch_reason(reason) is expected


@pytest.mark.parametrize(
    ("values", "default", "expected"),
    [
        (None, ["Done"], {"done"}),
        (["Review", "DONE"], ["todo"], {"review", "done"}),
        (("Blocked", 1), ["todo"], {"blocked", "1"}),
        ("Review_Approved", ["todo"], {"review_approved"}),
        ([], ["todo"], set()),
        ([None, ""], ["todo"], {"none", ""}),
    ],
)
def test_normalized_status_set_cases(
    values: object, default: list[str], expected: set[str]
) -> None:
    assert normalized_status_set(values, default) == expected


def test_ready_dispatch_settings_current_defaults() -> None:
    settings = ready_dispatch_settings({})

    assert settings["enabled"] is True
    assert settings["review_statuses"] == ["review"]
    assert settings["finalize_statuses"] == ["review_approved"]
    assert settings["owned_statuses"] == ["in_progress", "todo"]
    assert settings["dependency_done_statuses"] == ["done"]
    assert settings["worker_terminal_statuses"] == ["review", "done", "review_approved"]
    assert settings["active_worker_statuses"] == DEFAULT_ACTIVE_WORKER_STATUSES
    assert "max_dispatches_per_tick" not in settings
    assert (
        settings["orphaned_queue_event_grace_seconds"] == DEFAULT_ORPHANED_QUEUE_EVENT_GRACE_SECONDS
    )
    assert settings["helper_execution_lease"]["enabled"] is True
    assert settings["helper_execution_lease"]["claimable_statuses"] == ["todo", "in_progress"]
    assert settings["helper_execution_lease"]["require_owner_saturated"] is True


def test_ready_dispatch_settings_treats_missing_ready_dispatcher_as_defaults() -> None:
    assert ready_dispatch_settings({"ready_dispatcher": None})["review_statuses"] == ["review"]


def test_ready_dispatch_settings_preserves_configured_values() -> None:
    settings = ready_dispatch_settings(
        {
            "ready_dispatcher": {
                "review_statuses": ["needs_review"],
                "finalize_statuses": ["approved"],
                "owned_statuses": ["queued"],
                "max_dispatches_per_tick": 8,
            }
        }
    )

    assert settings["review_statuses"] == ["needs_review"]
    assert settings["finalize_statuses"] == ["approved"]
    assert settings["owned_statuses"] == ["queued"]
    assert settings["max_dispatches_per_tick"] == 8


def test_ready_dispatch_settings_uses_done_statuses_for_legacy_terminal_default() -> None:
    settings = ready_dispatch_settings({"ready_dispatcher": {"done_statuses": ["done"]}})

    assert settings["worker_terminal_statuses"] == ["done"]


def test_ready_dispatch_settings_explicit_worker_terminal_statuses_win() -> None:
    settings = ready_dispatch_settings(
        {
            "ready_dispatcher": {
                "done_statuses": ["done"],
                "worker_terminal_statuses": ["complete", "review_approved"],
            }
        }
    )

    assert settings["worker_terminal_statuses"] == ["complete", "review_approved"]


def test_ready_dispatch_settings_preserves_current_sidecar_and_queue_knobs() -> None:
    settings = ready_dispatch_settings(
        {
            "ready_dispatcher": {
                "sidecar_only_agents": ["Copilot"],
                "disabled_agents": ["Gemini"],
                "orphaned_queue_event_grace_seconds": 90,
            }
        }
    )

    assert settings["sidecar_only_agents"] == ["Copilot"]
    assert settings["disabled_agents"] == ["Gemini"]
    assert settings["orphaned_queue_event_grace_seconds"] == 90


def _init_test_git_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-b", "task/TEST-RETRY-001"], cwd=path, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test Runner"], cwd=path, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        capture_output=True,
        check=True,
    )
    tracked_file = path / "README.md"
    tracked_file.write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"], cwd=path, capture_output=True, check=True
    )
    head_proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, check=True
    )
    return head_proc.stdout.strip()


def test_worktree_lease_block_with_zero_byte_ai_status_lock_suppresses_and_clearing_recovers_immediately(
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "worktree"
    _init_test_git_repo(repo_path)

    lock_file = repo_path / "ai-status.json.lock"
    lock_file.write_bytes(b"")

    task = {
        "id": "ORCH-DISPATCH-RETRY-AUTHORITY-001",
        "status": "in_progress",
        "owner": "Antigravity6",
        "reviewer": "Codex2",
        "depends_on": [],
    }
    task_map = {task["id"]: task}
    reason = "owned_in_progress_dispatch"

    config: dict = {"worker_runtime": {"lease_block_escalate_after": 5}}
    state: dict = {}

    count = worker_workspace._record_worktree_lease_block(
        config,
        state,
        task_id=task["id"],
        refresh_status="skipped_dirty_worktree: 1 dirty change (1 untracked): ai-status.json.lock",
        message="Cannot lease isolated worker worktree: dirty changes",
        worktree_path=repo_path,
    )
    assert count == 1
    key = supervisor.normalize_agent_id(task["id"])
    entry = state["worker_worktree_lease_blocks"][key]
    entry["dispatch_signature"] = dispatch_engine.ready_dispatch_signature(task, reason, task_map)
    entry["last_at"] = (datetime.now(UTC) - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. While ai-status.json.lock is present, worktree_block_still_matches_dispatch returns True (suppressed)
    assert (
        dispatch_engine.worktree_block_still_matches_dispatch(
            state,
            task,
            reason,
            task_map,
            retry_after_seconds=1800.0,
        )
        is True
    )

    # 2. When operator clears the 0-byte ai-status.json.lock, it recovers eligibility on next tick without waiting 1800s
    lock_file.unlink()
    assert (
        dispatch_engine.worktree_block_still_matches_dispatch(
            state,
            task,
            reason,
            task_map,
            retry_after_seconds=1800.0,
        )
        is False
    )


def test_worktree_lease_block_records_auditable_secret_free_state_identity(tmp_path: Path) -> None:
    repo_path = tmp_path / "worktree_identity"
    head_sha = _init_test_git_repo(repo_path)

    # Clean state
    clean_identity = worker_workspace.compute_worktree_state_identity(repo_path)
    assert clean_identity == f"clean:{head_sha}"

    # Dirty state (0-byte lock)
    lock_file = repo_path / "ai-status.json.lock"
    lock_file.write_bytes(b"")
    dirty_identity = worker_workspace.compute_worktree_state_identity(repo_path)
    assert dirty_identity.startswith("owner_dirty:")
    assert dirty_identity.endswith(f":{head_sha}")
    assert ":" in dirty_identity

    state: dict = {}
    config: dict = {}
    worker_workspace._record_worktree_lease_block(
        config,
        state,
        task_id="TASK-AUDIT-001",
        refresh_status="skipped_dirty_worktree: 1 dirty change",
        message="Dirty worktree refusal",
        worktree_path=repo_path,
    )
    key = supervisor.normalize_agent_id("TASK-AUDIT-001")
    entry = state["worker_worktree_lease_blocks"][key]
    assert entry["worktree_path"] == str(repo_path.resolve())
    assert entry["worktree_state_identity"] == dirty_identity


def test_first_dirty_block_fails_closed_and_records_state_without_provider_slot(
    tmp_path: Path,
) -> None:
    root_path = tmp_path / "worktrees_root"
    root_path.mkdir(parents=True, exist_ok=True)
    task_id = "TASK-PREFLIGHT-001"
    repo_worktree = root_path / "odayplus" / "task-preflight-001"
    head_sha = _init_test_git_repo(repo_worktree)

    # Put a 0-byte ai-status.json.lock in the worktree
    (repo_worktree / "ai-status.json.lock").write_bytes(b"")

    config = {
        "paths": {
            "status_file": str(repo_worktree / "ai-status.json"),
            "activity_log": str(root_path / "ai-activity-log.jsonl"),
        },
        "worker_worktrees": {
            "root": str(root_path),
        },
        "worker_runtime": {
            "lease_block_escalate_after": 5,
        },
    }
    state: dict = {}
    request = DeliveryRequest(
        agent_id="Antigravity6",
        provider="cli",
        delivery_mode="background",
        task_id=task_id,
        message="Please implement the feature.",
        reason="owned_ready_dispatch",
        metadata={
            "workspace_task_id": task_id,
            "task": {
                "id": task_id,
                "status": "todo",
                "owner": "Antigravity6",
                "reviewer": "Codex2",
                "branch": "task/TEST-RETRY-001",
            },
        },
    )

    # Preflight fails closed
    with (
        mock.patch.object(
            supervisor,
            "resolve_worker_base",
            return_value=(
                worker_workspace.WorkerBaseResolution("odayplus", "dev", head_sha, "origin/dev"),
                None,
            ),
        ),
        mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=repo_worktree),
    ):
        ok, message = worker_workspace.prepare_worker_workspace(
            config,
            state,
            request,
            queue_event_id="q-1",
            target_agent="Antigravity6",
        )
    assert ok is False
    assert "ai-status.json.lock" in (message or "")

    # Block entry is recorded with state identity and path
    key = supervisor.normalize_agent_id(task_id)
    entry = state["worker_worktree_lease_blocks"][key]
    assert entry["count"] == 1
    assert "skipped_dirty_worktree" in entry["refresh_status"]
    assert "ai-status.json.lock" in entry["refresh_status"]
    assert entry["worktree_path"] == str(repo_worktree.resolve())
    assert entry["worktree_state_identity"].startswith("owner_dirty:")


def test_dirty_worktree_committed_locally_recovers_immediately_without_remote_push(
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "worktree_commit"
    _init_test_git_repo(repo_path)

    task_file = repo_path / "task_work.py"
    task_file.write_text("x = 1\n", encoding="utf-8")

    task = {
        "id": "TASK-LOCAL-COMMIT-001",
        "status": "in_progress",
        "owner": "Antigravity6",
        "reviewer": "Codex2",
        "depends_on": [],
    }
    task_map = {task["id"]: task}
    reason = "owned_in_progress_dispatch"
    config: dict = {}
    state: dict = {}

    worker_workspace._record_worktree_lease_block(
        config,
        state,
        task_id=task["id"],
        refresh_status="skipped_dirty_worktree: 1 untracked",
        message="Dirty worktree refusal",
        worktree_path=repo_path,
    )
    key = supervisor.normalize_agent_id(task["id"])
    entry = state["worker_worktree_lease_blocks"][key]
    entry["dispatch_signature"] = dispatch_engine.ready_dispatch_signature(task, reason, task_map)
    entry["last_at"] = (datetime.now(UTC) - timedelta(seconds=15)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Still dirty
    assert (
        dispatch_engine.worktree_block_still_matches_dispatch(state, task, reason, task_map) is True
    )

    # Operator commits task-owned dirt locally
    subprocess.run(["git", "add", "task_work.py"], cwd=repo_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "TASK-LOCAL-COMMIT-001: save work"],
        cwd=repo_path,
        capture_output=True,
        check=True,
    )

    # Now clean -> immediately eligible
    assert (
        dispatch_engine.worktree_block_still_matches_dispatch(state, task, reason, task_map)
        is False
    )


def test_unresolved_git_operation_suppresses_and_finishing_recovers_immediately(
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "worktree_git_op"
    head_sha = _init_test_git_repo(repo_path)

    merge_head = repo_path / ".git" / "MERGE_HEAD"
    merge_head.write_text(head_sha + "\n", encoding="utf-8")

    task = {
        "id": "TASK-GIT-OP-001",
        "status": "in_progress",
        "owner": "Antigravity6",
        "reviewer": "Codex2",
        "depends_on": [],
    }
    task_map = {task["id"]: task}
    reason = "owned_in_progress_dispatch"
    config: dict = {}
    state: dict = {}

    worker_workspace._record_worktree_lease_block(
        config,
        state,
        task_id=task["id"],
        refresh_status="unresolved_git_operation",
        message="Git operation in progress",
        worktree_path=repo_path,
    )
    key = supervisor.normalize_agent_id(task["id"])
    entry = state["worker_worktree_lease_blocks"][key]
    entry["dispatch_signature"] = dispatch_engine.ready_dispatch_signature(task, reason, task_map)
    entry["last_at"] = (datetime.now(UTC) - timedelta(seconds=15)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # In-progress git operation -> suppressed
    assert (
        dispatch_engine.worktree_block_still_matches_dispatch(state, task, reason, task_map) is True
    )

    # Git operation finishes (e.g. merge completed / MERGE_HEAD cleared)
    merge_head.unlink()

    # Now clean and no git op -> immediately eligible
    assert (
        dispatch_engine.worktree_block_still_matches_dispatch(state, task, reason, task_map)
        is False
    )


def test_unrepaired_dirty_worktree_maintains_backoff_until_expiry(tmp_path: Path) -> None:
    repo_path = tmp_path / "worktree_backoff"
    _init_test_git_repo(repo_path)

    dirty_file = repo_path / "unrepaired.txt"
    dirty_file.write_text("still dirty\n", encoding="utf-8")

    task = {
        "id": "TASK-BACKOFF-001",
        "status": "in_progress",
        "owner": "Antigravity6",
        "reviewer": "Codex2",
        "depends_on": [],
    }
    task_map = {task["id"]: task}
    reason = "owned_in_progress_dispatch"
    config: dict = {}
    state: dict = {}

    worker_workspace._record_worktree_lease_block(
        config,
        state,
        task_id=task["id"],
        refresh_status="skipped_dirty_worktree: 1 untracked",
        message="Dirty worktree refusal",
        worktree_path=repo_path,
    )
    key = supervisor.normalize_agent_id(task["id"])
    entry = state["worker_worktree_lease_blocks"][key]
    entry["dispatch_signature"] = dispatch_engine.ready_dispatch_signature(task, reason, task_map)

    # Within retry window (100 seconds ago < 1800.0) -> suppressed
    entry["last_at"] = (datetime.now(UTC) - timedelta(seconds=100)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert (
        dispatch_engine.worktree_block_still_matches_dispatch(
            state, task, reason, task_map, retry_after_seconds=1800.0
        )
        is True
    )

    # After retry window expired (2000 seconds ago > 1800.0) -> eligible for periodic retry
    entry["last_at"] = (datetime.now(UTC) - timedelta(seconds=2000)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert (
        dispatch_engine.worktree_block_still_matches_dispatch(
            state, task, reason, task_map, retry_after_seconds=1800.0
        )
        is False
    )


def test_legacy_lease_block_without_worktree_path_maintains_suppression() -> None:
    task = {
        "id": "TASK-LEGACY-ENTRY-001",
        "status": "in_progress",
        "owner": "Antigravity6",
        "reviewer": "Codex2",
        "depends_on": [],
    }
    task_map = {task["id"]: task}
    reason = "owned_in_progress_dispatch"
    config: dict = {}
    state: dict = {}

    worker_workspace._record_worktree_lease_block(
        config,
        state,
        task_id=task["id"],
        refresh_status="skipped_dirty_worktree: legacy block",
        message="Legacy block without recorded path",
    )
    key = supervisor.normalize_agent_id(task["id"])
    entry = state["worker_worktree_lease_blocks"][key]
    entry["dispatch_signature"] = dispatch_engine.ready_dispatch_signature(task, reason, task_map)
    entry["last_at"] = (datetime.now(UTC) - timedelta(seconds=100)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Without worktree_path, entry cannot verify cleanliness -> maintains suppression
    assert "worktree_path" not in entry
    assert (
        dispatch_engine.worktree_block_still_matches_dispatch(
            state, task, reason, task_map, retry_after_seconds=1800.0
        )
        is True
    )


def test_missing_or_deleted_worktree_path_maintains_suppression(tmp_path: Path) -> None:
    deleted_path = tmp_path / "non_existent_worktree"
    task = {
        "id": "TASK-MISSING-WT-001",
        "status": "in_progress",
        "owner": "Antigravity6",
        "reviewer": "Codex2",
        "depends_on": [],
    }
    task_map = {task["id"]: task}
    reason = "owned_in_progress_dispatch"
    config: dict = {}
    state: dict = {}

    worker_workspace._record_worktree_lease_block(
        config,
        state,
        task_id=task["id"],
        refresh_status="skipped_dirty_worktree: missing worktree",
        message="Worktree deleted",
        worktree_path=deleted_path,
    )
    key = supervisor.normalize_agent_id(task["id"])
    entry = state["worker_worktree_lease_blocks"][key]
    entry["dispatch_signature"] = dispatch_engine.ready_dispatch_signature(task, reason, task_map)
    entry["last_at"] = (datetime.now(UTC) - timedelta(seconds=100)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Missing worktree path -> maintains suppression
    assert (
        dispatch_engine.worktree_block_still_matches_dispatch(
            state, task, reason, task_map, retry_after_seconds=1800.0
        )
        is True
    )


def test_worktree_with_materialized_context_seed_recovers_immediately(tmp_path: Path) -> None:
    repo_path = tmp_path / "worktree_mat_context"
    head_sha = _init_test_git_repo(repo_path)

    # Materialized seed file allowed by orchestrator
    guide_file = repo_path / "AI_COLLABORATION_GUIDE.md"
    guide_file.write_text("# Guide\n", encoding="utf-8")
    mat_paths = ["AI_COLLABORATION_GUIDE.md"]

    task = {
        "id": "TASK-MAT-SEED-001",
        "status": "in_progress",
        "owner": "Antigravity6",
        "reviewer": "Codex2",
        "depends_on": [],
    }
    task_map = {task["id"]: task}
    reason = "owned_in_progress_dispatch"
    config: dict = {}
    state: dict = {}

    worker_workspace._record_worktree_lease_block(
        config,
        state,
        task_id=task["id"],
        refresh_status="skipped_dirty_worktree: materialized seed",
        message="Materialized seed",
        worktree_path=repo_path,
        materialized_paths=mat_paths,
    )
    key = supervisor.normalize_agent_id(task["id"])
    entry = state["worker_worktree_lease_blocks"][key]
    entry["dispatch_signature"] = dispatch_engine.ready_dispatch_signature(task, reason, task_map)
    entry["last_at"] = (datetime.now(UTC) - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Orchestrator seed only -> recognized as handoff clean -> recovers immediately
    identity = worker_workspace.compute_worktree_state_identity(
        repo_path, materialized_paths=mat_paths
    )
    assert identity.startswith("orchestrator_seed_only:")
    assert identity.endswith(f":{head_sha}")
    assert (
        dispatch_engine.worktree_block_still_matches_dispatch(state, task, reason, task_map)
        is False
    )


def _base_test_config() -> dict:
    return {
        "paths": {
            "status_file": "/tmp/status.json",
            "activity_log": "/tmp/activity.jsonl",
            "event_queue": "/tmp/events.jsonl",
        },
        "agents": {
            "claude": {
                "id": "claude",
                "display_name": "Claude",
                "provider": "claude",
                "slot_id": "slot-claude",
            },
            "antigravity7": {
                "id": "antigravity7",
                "display_name": "Antigravity7",
                "provider": "antigravity",
                "slot_id": "slot-antigravity",
            },
            "codex": {
                "id": "codex",
                "display_name": "Codex",
                "provider": "codex",
                "slot_id": "slot-codex",
            },
        },
        "ready_dispatcher": {
            "enabled": True,
            "helper_execution_lease": {
                "enabled": True,
                "claimable_statuses": ["todo", "in_progress"],
                "require_owner_saturated": True,
                "dispatch_sla_seconds": 600,
                "lease_seconds": 1800,
                "max_claims_per_tick": 4,
                "max_claims_per_agent": 2,
            },
        },
        "worker_runtime": {
            "heartbeat_stale_seconds": 300,
            "heartbeat_grace_seconds": 60,
        },
        "providers": {
            "claude": {"delivery_mode": "claude"},
            "antigravity": {"delivery_mode": "antigravity"},
            "codex": {"delivery_mode": "codex"},
        },
    }


def test_orphaned_in_progress_task_redispatched_to_available_owner() -> None:
    cfg = _base_test_config()
    task = {
        "id": "TASK-ORPHANED-001",
        "priority": "P2",
        "status": "in_progress",
        "owner": "Claude",
        "reviewer": "Codex",
        "depends_on": [],
        "last_update": "2026-08-20T10:00:00Z",
    }
    status = {"tasks": [task]}
    state = {
        "workers": {},
        "queue": {"events": {}},
    }
    queued_events: list[dict] = []

    with (
        mock.patch.object(supervisor, "load_status", return_value=status),
        mock.patch.object(supervisor, "load_event_queue", return_value=[]),
        mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=True),
        mock.patch.object(supervisor, "write_activity_log"),
        mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
        mock.patch.object(
            supervisor, "queue_delivery_event", side_effect=lambda _c, evt: queued_events.append(evt) or True
        ),
    ):
        changed = supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["antigravity7", "claude"])

    assert changed is True
    assert len(queued_events) == 1
    assert queued_events[0]["task_id"] == "TASK-ORPHANED-001"
    assert queued_events[0]["target_agent"] == "Claude"
    assert queued_events[0]["reason"] == "owned_in_progress_dispatch"
    assert "helper_execution_lease" not in task


def test_orphaned_in_progress_task_claimed_by_helper_when_owner_busy_and_sla_exceeded() -> None:
    cfg = _base_test_config()
    task = {
        "id": "TASK-ORPHANED-002",
        "priority": "P2",
        "status": "in_progress",
        "owner": "Claude",
        "reviewer": "Codex",
        "depends_on": [],
        "last_update": "2026-08-20T10:00:00Z",
    }
    busy_task = {
        "id": "TASK-BUSY-001",
        "priority": "P2",
        "status": "in_progress",
        "owner": "Claude",
        "reviewer": "Codex",
    }
    status = {"tasks": [task, busy_task]}
    state = {
        "workers": {
            "run-claude-busy": {
                "run_id": "run-claude-busy",
                "task_id": "TASK-BUSY-001",
                "logical_agent_id": "claude",
                "agent_id": "claude",
                "status": "running",
                "pid": 12345,
                "last_heartbeat_at": "2026-08-20T12:00:00Z",
                "request_snapshot": {"reason": "owned_in_progress_dispatch"},
            }
        },
        "queue": {"events": {}},
    }
    queued_events: list[dict] = []

    with (
        mock.patch.object(supervisor, "load_status", return_value=status),
        mock.patch.object(supervisor, "load_event_queue", return_value=[]),
        mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=True),
        mock.patch.object(dispatch_engine, "commit_canonical_task_transition", create=True, return_value=True),
        mock.patch.object(supervisor, "write_activity_log"),
        mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
        mock.patch.object(supervisor, "pid_is_alive", return_value=True),
        mock.patch.object(
            supervisor, "queue_delivery_event", side_effect=lambda _c, evt: queued_events.append(evt) or True
        ),
    ):
        changed = supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["antigravity7"])

    assert changed is True
    assert len(queued_events) == 1
    assert queued_events[0]["task_id"] == "TASK-ORPHANED-002"
    assert queued_events[0]["target_agent"] == "Antigravity7"
    assert queued_events[0]["reason"] == "helper_claim_dispatch"
    assert task["helper_execution_lease"]["claimed_by"] == "Antigravity7"
    assert task["helper_execution_lease"]["original_owner"] == "Claude"
    assert task["owner"] == "Claude"


def test_orphaned_in_progress_task_not_claimed_when_sla_not_exceeded() -> None:
    cfg = _base_test_config()
    now_iso = (datetime.now(UTC) - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    task = {
        "id": "TASK-ORPHANED-003",
        "priority": "P2",
        "status": "in_progress",
        "owner": "Claude",
        "reviewer": "Codex",
        "depends_on": [],
        "last_update": now_iso,
    }
    status = {"tasks": [task]}
    state = {
        "workers": {
            "run-claude-busy": {
                "run_id": "run-claude-busy",
                "task_id": "TASK-BUSY-001",
                "logical_agent_id": "claude",
                "agent_id": "claude",
                "status": "running",
                "pid": 12345,
                "last_heartbeat_at": "2026-08-20T12:00:00Z",
                "request_snapshot": {"reason": "owned_in_progress_dispatch"},
            }
        },
        "queue": {"events": {}},
    }
    queued_events: list[dict] = []

    with (
        mock.patch.object(supervisor, "load_status", return_value=status),
        mock.patch.object(supervisor, "load_event_queue", return_value=[]),
        mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=True),
        mock.patch.object(dispatch_engine, "commit_canonical_task_transition", create=True, return_value=True),
        mock.patch.object(supervisor, "write_activity_log"),
        mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
        mock.patch.object(supervisor, "pid_is_alive", return_value=True),
        mock.patch.object(
            supervisor, "queue_delivery_event", side_effect=lambda _c, evt: queued_events.append(evt) or True
        ),
    ):
        changed = supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["antigravity7"])

    assert len(queued_events) == 0
    assert "helper_execution_lease" not in task


def test_orphaned_in_progress_task_claimed_when_owner_paused_and_sla_exceeded() -> None:
    cfg = _base_test_config()
    task = {
        "id": "TASK-ORPHANED-004",
        "priority": "P2",
        "status": "in_progress",
        "owner": "Claude",
        "reviewer": "Codex",
        "depends_on": [],
        "last_update": "2026-08-20T10:00:00Z",
    }
    status = {"tasks": [task]}
    state = {
        "workers": {},
        "queue": {"events": {}},
        "paused_agents": {"claude": "maintenance"},
    }
    queued_events: list[dict] = []

    def fake_block_reason(_cfg, _state, agent_id, _report=None):
        if agent_id == "claude":
            return "claude is paused"
        return None

    with (
        mock.patch.object(supervisor, "load_status", return_value=status),
        mock.patch.object(supervisor, "load_event_queue", return_value=[]),
        mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=True),
        mock.patch.object(dispatch_engine, "commit_canonical_task_transition", create=True, return_value=True),
        mock.patch.object(supervisor, "write_activity_log"),
        mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", side_effect=fake_block_reason),
        mock.patch.object(
            supervisor, "queue_delivery_event", side_effect=lambda _c, evt: queued_events.append(evt) or True
        ),
    ):
        changed = supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["antigravity7", "claude"])

    assert changed is True
    assert len(queued_events) == 1
    assert queued_events[0]["target_agent"] == "Antigravity7"
    assert queued_events[0]["reason"] == "helper_claim_dispatch"


def test_active_runner_prevents_duplicate_owner_dispatch_and_helper_claim() -> None:
    cfg = _base_test_config()
    task = {
        "id": "TASK-ACTIVE-001",
        "priority": "P2",
        "status": "in_progress",
        "owner": "Claude",
        "reviewer": "Codex",
        "depends_on": [],
        "last_update": "2026-08-20T10:00:00Z",
    }
    status = {"tasks": [task]}
    state = {
        "workers": {
            "run-claude-active": {
                "run_id": "run-claude-active",
                "task_id": "TASK-ACTIVE-001",
                "logical_agent_id": "claude",
                "agent_id": "claude",
                "status": "running",
                "pid": 54321,
                "last_heartbeat_at": (datetime.now(UTC) - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "request_snapshot": {"reason": "owned_in_progress_dispatch"},
            }
        },
        "queue": {"events": {}},
    }
    queued_events: list[dict] = []

    with (
        mock.patch.object(supervisor, "load_status", return_value=status),
        mock.patch.object(supervisor, "load_event_queue", return_value=[]),
        mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=True),
        mock.patch.object(dispatch_engine, "commit_canonical_task_transition", create=True, return_value=True),
        mock.patch.object(supervisor, "write_activity_log"),
        mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
        mock.patch.object(supervisor, "pid_is_alive", return_value=True),
        mock.patch.object(
            supervisor, "queue_delivery_event", side_effect=lambda _c, evt: queued_events.append(evt) or True
        ),
    ):
        changed = supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["antigravity7", "claude"])

    assert len(queued_events) == 0


def test_review_blocked_human_gate_and_dependency_tasks_never_claimed() -> None:
    cfg = _base_test_config()
    tasks = [
        {"id": "T-REV", "priority": "P2", "status": "review", "owner": "Claude", "reviewer": "Codex", "last_update": "2026-08-20T00:00:00Z"},
        {"id": "T-BLOCK", "priority": "P2", "status": "blocked", "owner": "Claude", "reviewer": "Codex", "last_update": "2026-08-20T00:00:00Z"},
        {"id": "T-HG", "priority": "P2", "status": "todo", "owner": "Claude", "reviewer": "Codex", "task_class": "human_gate", "last_update": "2026-08-20T00:00:00Z"},
        {"id": "T-NONDISP", "priority": "P2", "status": "in_progress", "owner": "Claude", "reviewer": "Codex", "non_dispatchable": True, "last_update": "2026-08-20T00:00:00Z"},
        {"id": "T-UNSAT", "priority": "P2", "status": "in_progress", "owner": "Claude", "reviewer": "Codex", "depends_on": ["NON-EXISTENT"], "last_update": "2026-08-20T00:00:00Z"},
    ]
    status = {"tasks": tasks}
    state = {"workers": {}, "queue": {"events": {}}}
    queued_events: list[dict] = []

    with (
        mock.patch.object(supervisor, "load_status", return_value=status),
        mock.patch.object(supervisor, "load_event_queue", return_value=[]),
        mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=True),
        mock.patch.object(dispatch_engine, "commit_canonical_task_transition", create=True, return_value=True),
        mock.patch.object(supervisor, "write_activity_log"),
        mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
        mock.patch.object(
            supervisor, "queue_delivery_event", side_effect=lambda _c, evt: queued_events.append(evt) or True
        ),
    ):
        changed = supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["antigravity7"])

    assert len(queued_events) == 0
    assert all("helper_execution_lease" not in t for t in tasks)


def test_dead_helper_lease_released_and_recovered_for_owner_redispatch() -> None:
    cfg = _base_test_config()
    task = {
        "id": "TASK-DEAD-LEASE-001",
        "priority": "P2",
        "status": "in_progress",
        "owner": "Claude",
        "reviewer": "Codex",
        "depends_on": [],
        "last_update": "2026-08-20T10:00:00Z",
        "helper_execution_lease": {
            "claimed_by": "Codex",
            "original_owner": "Claude",
            "run_id": "run-codex-dead",
            "generation": 1,
            "lease_expires_at": (datetime.now(UTC) + timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }
    status = {"tasks": [task]}
    state = {
        "workers": {
            "run-codex-dead": {
                "run_id": "run-codex-dead",
                "task_id": "TASK-DEAD-LEASE-001",
                "status": "failed",
                "request_snapshot": {"reason": "helper_claim_dispatch"},
            }
        },
        "queue": {"events": {}},
    }
    queued_events: list[dict] = []

    with (
        mock.patch.object(supervisor, "load_status", return_value=status),
        mock.patch.object(supervisor, "load_event_queue", return_value=[]),
        mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=True),
        mock.patch.object(dispatch_engine, "commit_canonical_task_transition", create=True, return_value=True),
        mock.patch.object(supervisor, "write_activity_log"),
        mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
        mock.patch.object(
            supervisor, "queue_delivery_event", side_effect=lambda _c, evt: queued_events.append(evt) or True
        ),
    ):
        changed = supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["antigravity7", "claude"])

    assert changed is True
    assert "helper_execution_lease" not in task
    assert len(queued_events) == 1
    assert queued_events[0]["target_agent"] == "Claude"
    assert queued_events[0]["reason"] == "owned_in_progress_dispatch"


def test_active_helper_continues_executing_valid_lease() -> None:
    cfg = _base_test_config()
    expires = (datetime.now(UTC) + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    task = {
        "id": "TASK-ACTIVE-LEASE-001",
        "priority": "P2",
        "status": "in_progress",
        "owner": "Claude",
        "reviewer": "Codex",
        "depends_on": [],
        "last_update": "2026-08-20T10:00:00Z",
        "helper_execution_lease": {
            "claimed_by": "Antigravity7",
            "original_owner": "Claude",
            "generation": 1,
            "lease_expires_at": expires,
        },
    }
    status = {"tasks": [task]}
    state = {
        "workers": {},
        "queue": {"events": {}},
    }
    queued_events: list[dict] = []

    with (
        mock.patch.object(supervisor, "load_status", return_value=status),
        mock.patch.object(supervisor, "load_event_queue", return_value=[]),
        mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=True),
        mock.patch.object(dispatch_engine, "commit_canonical_task_transition", create=True, return_value=True),
        mock.patch.object(supervisor, "write_activity_log"),
        mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
        mock.patch.object(
            supervisor, "queue_delivery_event", side_effect=lambda _c, evt: queued_events.append(evt) or True
        ),
    ):
        changed = supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["antigravity7"])

    assert changed is True
    assert len(queued_events) == 1
    assert queued_events[0]["target_agent"] == "Antigravity7"
    assert queued_events[0]["reason"] == "helper_claim_dispatch"
    assert task["helper_execution_lease"]["generation"] == 1


