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
        changed = supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["antigravity7", "claude"])

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
        assert supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["antigravity7", "claude"]) is False

    assert len(queued_events) == 0
    assert "helper_execution_lease" not in task


def test_orphaned_in_progress_task_redispatched_to_owner_when_owner_idle_even_if_sla_exceeded() -> None:
    cfg = _base_test_config()
    task = {
        "id": "TASK-ORPHANED-IDLE-001",
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
        mock.patch.object(dispatch_engine, "commit_canonical_task_transition", create=True, return_value=True),
        mock.patch.object(supervisor, "write_activity_log"),
        mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
        mock.patch.object(
            supervisor, "queue_delivery_event", side_effect=lambda _c, evt: queued_events.append(evt) or True
        ),
    ):
        changed = supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["antigravity7", "claude"])

    assert changed is True
    assert len(queued_events) == 1
    assert queued_events[0]["task_id"] == "TASK-ORPHANED-IDLE-001"
    assert queued_events[0]["target_agent"] == "Claude"
    assert queued_events[0]["reason"] == "owned_in_progress_dispatch"
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
        assert supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["antigravity7", "claude"]) is False

    assert len(queued_events) == 0


def test_review_blocked_human_gate_and_dependency_tasks_never_claimed() -> None:
    cfg = _base_test_config()
    tasks = [
        {
            "id": "T-REV",
            "priority": "P2",
            "status": "review",
            "owner": "Claude",
            "reviewer": "Codex",
            "last_update": "2026-08-20T00:00:00Z",
            "review_submission": {
                "pr_number": 101,
                "branch": "task/T-REV",
                "base_branch": "dev",
                "remote_sha": "a" * 40,
            },
        },
        {"id": "T-BLOCK", "priority": "P2", "status": "blocked", "owner": "Claude", "reviewer": "Codex", "waiting_for": "Human/Ops", "last_update": "2026-08-20T00:00:00Z"},
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
        assert supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["antigravity7"]) is False

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


def test_dead_helper_lease_released_sets_changed_true_even_when_no_dispatches_queued() -> None:
    cfg = _base_test_config()
    task = {
        "id": "TASK-DEAD-LEASE-BLOCKED-001",
        "priority": "P2",
        "status": "in_progress",
        "owner": "Claude",
        "reviewer": "Codex",
        "non_dispatchable": True,
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
                "task_id": "TASK-DEAD-LEASE-BLOCKED-001",
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
    assert len(queued_events) == 0


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


def test_helper_owner_is_saturated_unit_cases() -> None:
    cfg = _base_test_config()
    helper_cfg = {"dispatch_sla_seconds": 600, "require_owner_saturated": True}
    task = {
        "id": "T-1",
        "owner": "Claude",
        "reviewer": "Codex",
        "last_update": "2026-08-20T10:00:00Z",
    }
    now = datetime(2026, 8, 20, 11, 0, 0, tzinfo=UTC)

    # 1. Idle owner with full agents list -> False (not saturated)
    assert dispatch_engine.helper_owner_is_saturated(
        cfg, task, {"Claude": []}, helper_cfg, now=now
    ) is False

    # 2. Busy owner (load 1 >= capacity 1), SLA exceeded -> True
    assert dispatch_engine.helper_owner_is_saturated(
        cfg, task, {"Claude": [1234]}, helper_cfg, now=now
    ) is True

    # 3. Paused owner, SLA exceeded -> True
    state_paused = {
        "provider_guardrails": {
            "dispatch_pauses": {"claude": {"blocked_until": "2099-01-01T00:00:00Z"}}
        }
    }
    assert dispatch_engine.helper_owner_is_saturated(
        cfg, task, {"Claude": []}, helper_cfg, state=state_paused, now=now
    ) is True

    # 4. Fresh task (SLA not exceeded), busy owner -> False (require_owner_saturated is True, but SLA not exceeded)
    fresh_task = {
        "id": "T-2",
        "owner": "Claude",
        "reviewer": "Codex",
        "last_update": "2026-08-20T10:55:00Z",
    }
    assert dispatch_engine.helper_owner_is_saturated(
        cfg, fresh_task, {"Claude": [1234]}, helper_cfg, now=now
    ) is False

    # 5. Non-existent owner -> True
    missing_owner_task = {
        "id": "T-3",
        "owner": "NonExistentAgent",
        "reviewer": "Codex",
        "last_update": "2026-08-20T10:00:00Z",
    }
    assert dispatch_engine.helper_owner_is_saturated(
        cfg, missing_owner_task, {}, helper_cfg, now=now
    ) is True



def _promotion_event_and_task() -> tuple[dict, dict, dict[str, dict]]:
    """A wake queued while the task was `todo`, consumed after the owner started it."""
    task = {
        "id": "TASK-PROMOTION-001",
        "status": "todo",
        "owner": "Claude",
        "reviewer": "Codex",
        "depends_on": [],
    }
    task_map = {task["id"]: task}
    event = supervisor.build_dispatch_event(task, "Claude", REASON_OWNED_READY, task_map)
    event["event_key"] = event["key"]
    event["target_display_name"] = "Claude"
    task["status"] = "in_progress"
    return event, task, task_map


def test_owner_starting_task_after_wake_is_queued_is_not_stale() -> None:
    cfg = _base_test_config()
    with mock.patch.object(supervisor, "resolve_task_progress_head", return_value=None):
        event, _task, task_map = _promotion_event_and_task()
        assert supervisor.stale_dispatch_skip_message(cfg, event, task_map) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reviewer", "Antigravity7"),
        ("owner", "Antigravity7"),
        ("depends_on", ["TASK-PROMOTION-DEP-001"]),
    ],
)
def test_authority_change_during_status_promotion_still_stales_wake(field: str, value: object) -> None:
    """R8/R12: only `status` may drift; owner/reviewer/dependency edges may not.

    Re-deriving eligibility inside the exemption would exempt every signature
    component at once, so a wake queued before a reviewer swap or a `depends_on`
    rewrite would still fire under the pre-change authority snapshot.
    """
    cfg = _base_test_config()
    with mock.patch.object(supervisor, "resolve_task_progress_head", return_value=None):
        event, task, task_map = _promotion_event_and_task()
        task_map["TASK-PROMOTION-DEP-001"] = {
            "id": "TASK-PROMOTION-DEP-001",
            "status": "done",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": [],
        }
        task[field] = value
        message = supervisor.stale_dispatch_skip_message(cfg, event, task_map) or ""

    assert "no longer eligible" in message or "task state changed" in message, message


def test_status_demotion_after_wake_is_queued_is_stale() -> None:
    """`in_progress -> todo` is a reset, not a promotion, and must not be exempt."""
    cfg = _base_test_config()
    task = {
        "id": "TASK-DEMOTION-001",
        "status": "in_progress",
        "owner": "Claude",
        "reviewer": "Codex",
        "depends_on": [],
    }
    task_map = {task["id"]: task}
    with mock.patch.object(supervisor, "resolve_task_progress_head", return_value=None):
        event = supervisor.build_dispatch_event(task, "Claude", REASON_OWNED_IN_PROGRESS, task_map)
        event["event_key"] = event["key"]
        event["target_display_name"] = "Claude"
        task["status"] = "todo"
        message = supervisor.stale_dispatch_skip_message(cfg, event, task_map) or ""

    assert "no longer eligible" in message or "task state changed" in message, message


def _dead_lease_fixture() -> tuple[dict, dict, dict]:
    task = {
        "id": "TASK-DEAD-LEASE-ABORT-001",
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
                "task_id": task["id"],
                "status": "failed",
                "request_snapshot": {"reason": "helper_claim_dispatch"},
            }
        },
        "queue": {"events": {}},
    }
    return task, status, state


def test_release_dead_helper_claims_reports_commit_failure_separately_from_no_op() -> None:
    """One boolean cannot say whether nothing needed releasing or the write failed."""
    cfg = _base_test_config()
    _task, status, state = _dead_lease_fixture()

    with (
        mock.patch.object(supervisor, "write_activity_log"),
        mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=False),
    ):
        assert supervisor.release_dead_helper_claims(cfg, state, status) == (False, False)

    clean_status = {"tasks": [{"id": "TASK-NO-LEASE-001", "status": "todo", "owner": "Claude"}]}
    with (
        mock.patch.object(supervisor, "write_activity_log"),
        mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=True),
    ):
        assert supervisor.release_dead_helper_claims(cfg, {"workers": {}}, clean_status) == (False, True)


def test_dispatch_aborts_when_dead_lease_release_fails_to_commit() -> None:
    """The leases are already popped in memory; dispatching on that view leaks slots."""
    cfg = _base_test_config()
    _task, status, state = _dead_lease_fixture()
    queued_events: list[dict] = []

    with (
        mock.patch.object(supervisor, "load_status", return_value=status),
        mock.patch.object(supervisor, "load_event_queue", return_value=[]),
        mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=False),
        mock.patch.object(supervisor, "write_activity_log"),
        mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
        mock.patch.object(
            supervisor, "queue_delivery_event", side_effect=lambda _c, evt: queued_events.append(evt) or True
        ),
    ):
        changed = supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["antigravity7", "claude"])

    assert changed is False
    assert queued_events == []


def test_capacity_reconcile_aborts_when_dead_lease_release_fails_to_commit() -> None:
    """Pre-refactor behaviour: the Chair must not size capacity off an uncommitted release."""
    cfg = _base_test_config()
    _task, status, state = _dead_lease_fixture()

    with (
        mock.patch.object(supervisor, "load_status", return_value=status),
        mock.patch.object(supervisor, "load_provider_report", return_value={}),
        mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=False),
        mock.patch.object(supervisor, "write_activity_log"),
        mock.patch.object(supervisor.capacity_controller, "evaluate_chair") as evaluate_chair,
    ):
        assert supervisor.reconcile_capacity_controller(cfg, state) is False

    evaluate_chair.assert_not_called()


def test_narrowed_claimable_statuses_are_reported_once_per_config_change() -> None:
    """A default the live config overrides must not fail silently.

    `ready_dispatch_settings` seeds `claimable_statuses` with `setdefault`, so an
    explicit value in the control plane's gitignored `config.json` wins and the
    whole helper-claim path for `in_progress` never executes -- with every test
    still green. This is the signal that tells those two states apart.
    """
    cfg = _base_test_config()
    cfg["ready_dispatcher"]["helper_execution_lease"]["claimable_statuses"] = ["todo"]
    state: dict = {}
    entries: list[dict] = []

    with mock.patch.object(
        supervisor, "write_activity_log", side_effect=lambda _c, entry: entries.append(entry)
    ):
        assert dispatch_engine.report_narrowed_helper_claimable_statuses(cfg, state) is True
        # Debounced: an unchanged configuration is not re-reported every tick.
        assert dispatch_engine.report_narrowed_helper_claimable_statuses(cfg, state) is False

    assert len(entries) == 1
    assert entries[0]["type"] == "helper_claim_statuses_narrowed"
    assert entries[0]["detail"]["missing"] == ["in_progress"]

    cfg["ready_dispatcher"]["helper_execution_lease"]["claimable_statuses"] = ["todo", "in_progress"]
    with mock.patch.object(
        supervisor, "write_activity_log", side_effect=lambda _c, entry: entries.append(entry)
    ):
        assert dispatch_engine.report_narrowed_helper_claimable_statuses(cfg, state) is True

    assert entries[-1]["type"] == "helper_claim_statuses_narrowed_cleared"


def test_default_claimable_statuses_are_not_reported_as_narrowed() -> None:
    cfg = _base_test_config()
    state: dict = {}
    with mock.patch.object(supervisor, "write_activity_log") as write_log:
        assert dispatch_engine.report_narrowed_helper_claimable_statuses(cfg, state) is False
    write_log.assert_not_called()


def test_empty_agent_override_does_not_make_every_owner_undispatchable() -> None:
    """`[]` means "no subset given", the same as it does for the rotation list.

    Reading it as "no agent is dispatchable" made `helper_owner_is_saturated`
    return True for every healthy owner, handing idle owners' tasks to helpers.
    """
    cfg = _base_test_config()
    task = {
        "id": "TASK-EMPTY-OVERRIDE-001",
        "priority": "P2",
        "status": "in_progress",
        "owner": "Claude",
        "reviewer": "Codex",
        "depends_on": [],
        "last_update": "2026-08-20T10:00:00Z",
    }
    status = {"tasks": [task]}
    state = {"workers": {}, "queue": {"events": {}}}
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
        supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=[])

    assert [evt["reason"] for evt in queued_events] == ["owned_in_progress_dispatch"]
    assert queued_events[0]["target_agent"] == "Claude"
    assert "helper_execution_lease" not in task


def test_review_approved_task_not_dispatched_for_review_on_poll_or_restart() -> None:
    cfg = _base_test_config()
    task = {
        "id": "TASK-APPROVED-001",
        "priority": "P1",
        "status": "review_approved",
        "owner": "Claude",
        "reviewer": "Codex",
        "approved_head": "1111111122222222333333334444444455555555",
        "depends_on": [],
        "last_update": "2026-08-20T10:00:00Z",
    }
    status = {"tasks": [task]}
    state = {"workers": {}, "queue": {"events": {}}}
    queued_events: list[dict] = []

    assert dispatch_engine.is_task_review_dispatch_eligible(cfg, task, "Codex") is False
    assert supervisor.is_task_review_dispatch_eligible(cfg, task, "Codex") is False
    assert supervisor.dispatch_priority_for_task(cfg, task, "Codex", task_map={"TASK-APPROVED-001": task}) is None

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
        supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["codex"])

    assert len(queued_events) == 0


def test_merge_routed_queued_task_not_dispatched_for_review_on_poll_or_restart() -> None:
    cfg = _base_test_config()
    task = {
        "id": "TASK-QUEUED-001",
        "priority": "P1",
        "status": "review",
        "owner": "Claude",
        "reviewer": "Codex",
        "merge_route": {
            "head": "1111111122222222333333334444444455555555",
            "route": "queued",
            "pr_number": 101,
            "at": "2026-08-20T10:00:00Z",
            "attempts": 1,
        },
        "depends_on": [],
        "last_update": "2026-08-20T10:00:00Z",
    }
    status = {"tasks": [task]}
    state = {"workers": {}, "queue": {"events": {}}}
    queued_events: list[dict] = []

    assert dispatch_engine.is_task_review_dispatch_eligible(cfg, task, "Codex") is False
    assert supervisor.is_task_review_dispatch_eligible(cfg, task, "Codex") is False
    assert supervisor.dispatch_priority_for_task(cfg, task, "Codex", task_map={"TASK-QUEUED-001": task}) is None

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
        supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["codex"])

    assert len(queued_events) == 0


def test_stale_wake_for_review_ready_skipped_when_task_approved_or_merge_routed() -> None:
    cfg = _base_test_config()
    task = {
        "id": "TASK-WAKE-001",
        "status": "review",
        "owner": "Claude",
        "reviewer": "Codex",
        "review_submission": {
            "pr_number": 101,
            "branch": "task/TASK-WAKE-001",
            "base_branch": "dev",
            "remote_sha": "a" * 40,
        },
        "depends_on": [],
    }
    task_map = {task["id"]: task}

    # Positive: while in review with CI success and without approval or merge_route, wake is not stale
    with (
        mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value="a" * 40),
        mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", return_value=("OPEN", "success")),
    ):
        event = supervisor.build_dispatch_event(task, "Codex", REASON_REVIEW_READY, task_map)
        event["event_key"] = event["key"]
        event["target_display_name"] = "Codex"
        assert supervisor.stale_dispatch_skip_message(cfg, event, task_map) is None

    # Case 1: Task becomes review_approved
    task["status"] = "review_approved"
    task["approved_head"] = "1111111122222222333333334444444455555555"
    with (
        mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value="a" * 40),
        mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", return_value=("OPEN", "success")),
    ):
        assert supervisor.current_dispatch_event_key(cfg, event, task_map) is None
        skip_msg = supervisor.stale_dispatch_skip_message(cfg, event, task_map)
        assert skip_msg is not None
        assert "no longer eligible" in skip_msg

    # Case 2: Task has merge_route=queued matching current submitted head
    task["status"] = "review"
    task.pop("approved_head", None)
    task["merge_route"] = {"head": "a" * 40, "route": "queued"}
    with (
        mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value="a" * 40),
        mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", return_value=("OPEN", "success")),
    ):
        assert supervisor.current_dispatch_event_key(cfg, event, task_map) is None
        skip_msg = supervisor.stale_dispatch_skip_message(cfg, event, task_map)
        assert skip_msg is not None
        assert "no longer eligible" in skip_msg

    # Case 3: Task has stale merge_route for a prior head (PR#1175 pattern)
    # The wake is NOT stale and the task IS eligible for review on the new head
    task["merge_route"] = {"head": "b" * 40, "route": "queued"}
    with (
        mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value="a" * 40),
        mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", return_value=("OPEN", "success")),
    ):
        assert supervisor.current_dispatch_event_key(cfg, event, task_map) == event["key"]
        assert supervisor.stale_dispatch_skip_message(cfg, event, task_map) is None


def test_pr1175_reproduction_prior_queued_route_does_not_block_repaired_new_head_review() -> None:
    """PR#1175: prior queued route head a801faf5经owner CI repair后current submitted head 0b3e1987,

    CI success, mergeQueueEntry null. The stale prior merge_route must not suppress reviewer dispatch.
    """
    cfg = _base_test_config()
    stale_head = "a801faf568b66e700f8691b02ceeec8358fa2aa9"
    new_head = "0b3e19870b3e19870b3e19870b3e19870b3e1987"
    task = {
        "id": "ODP-TEST-PR1175-001",
        "priority": "P1",
        "status": "review",
        "owner": "Claude",
        "reviewer": "Codex",
        "review_submission": {
            "pr_number": 1175,
            "branch": "task/ODP-TEST-PR1175-001",
            "base_branch": "dev",
            "remote_sha": new_head,
        },
        "merge_route": {
            "head": stale_head,
            "route": "queued",
            "pr_number": 1175,
            "at": "2026-09-05T00:10:56Z",
            "attempts": 1,
        },
        "depends_on": [],
        "last_update": "2026-09-05T00:53:14Z",
    }
    status = {"tasks": [task]}
    state = {"workers": {}, "queue": {"events": {}}}
    queued_events: list[dict] = []

    # 1. Verification: is_task_review_dispatch_eligible is True on new head despite stale merge_route
    with (
        mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value=new_head),
        mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", return_value=("OPEN", "success")),
    ):
        assert dispatch_engine.is_task_review_dispatch_eligible(cfg, task, "Codex") is True
        assert supervisor.is_task_review_dispatch_eligible(cfg, task, "Codex") is True
        assert supervisor.dispatch_priority_for_task(cfg, task, "Codex", task_map={"ODP-TEST-PR1175-001": task}) == 0

    # 2. Verification: dispatch_ready_tasks dispatches reviewer Codex
    with (
        mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value=new_head),
        mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", return_value=("OPEN", "success")),
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
        changed = supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["codex"])

    assert changed is True
    assert len(queued_events) == 1
    assert queued_events[0]["task_id"] == "ODP-TEST-PR1175-001"
    assert queued_events[0]["target_agent"] == "Codex"
    assert queued_events[0]["reason"] == REASON_REVIEW_READY

    # 3. Fail-closed verification: failing CI, pending CI, missing CI, and SHA drift fail closed
    for bad_ci in ["failure", "pending", "none", "unknown", "error"]:
        with (
            mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value=new_head),
            mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", return_value=("OPEN", bad_ci)),
        ):
            assert dispatch_engine.is_task_review_dispatch_eligible(cfg, task, "Codex") is False

    # SHA drift check: remote SHA != submitted SHA
    with (
        mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value="c" * 40),
        mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", return_value=("OPEN", "success")),
    ):
        assert dispatch_engine.is_task_review_dispatch_eligible(cfg, task, "Codex") is False

    # 4. Currently queued head remains immutable: matching head merge_route suppresses review dispatch
    task["merge_route"]["head"] = new_head
    with (
        mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value=new_head),
        mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", return_value=("OPEN", "success")),
    ):
        assert dispatch_engine.is_task_review_dispatch_eligible(cfg, task, "Codex") is False


def test_ci_repair_requeue_and_transition_clears_stale_merge_route() -> None:
    """Queue ejection / CI repair requeue and submit_review cleanly clear merge_route."""
    cfg = _base_test_config()
    task = {
        "id": "ODP-TRANSITION-001",
        "status": "review_approved",
        "owner": "Claude",
        "reviewer": "Codex",
        "approved_head": "a" * 40,
        "merge_route": {
            "head": "a" * 40,
            "route": "queued",
            "pr_number": 123,
            "at": "2026-09-05T00:00:00Z",
            "attempts": 1,
        },
        "depends_on": [],
    }
    status = {"tasks": [task]}

    with (
        mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=True),
        mock.patch.object(supervisor, "write_activity_log"),
    ):
        # CI repair requeue with clear_approval=True
        changed = supervisor.requeue_task_for_ci_repair(
            cfg,
            status,
            task,
            message="CI failed, owner repair required.",
            clear_approval=True,
        )
        assert changed is True
        assert task["status"] == "in_progress"
        assert "approved_head" not in task
        assert "merge_route" not in task


def test_reconcile_runtime_on_boot_completes_stale_review_event_when_approved_or_merge_routed() -> None:
    cfg = _base_test_config()
    task = {
        "id": "TASK-BOOT-001",
        "priority": "P1",
        "status": "review_approved",
        "owner": "Claude",
        "reviewer": "Codex",
        "approved_head": "1111111122222222333333334444444455555555",
        "merge_route": {"head": "1111111122222222333333334444444455555555", "route": "queued"},
        "depends_on": [],
        "last_update": "2026-08-20T10:00:00Z",
    }
    status = {"tasks": [task]}
    event = supervisor.build_dispatch_event(
        {"id": "TASK-BOOT-001", "status": "review", "owner": "Claude", "reviewer": "Codex", "depends_on": []},
        "Codex",
        REASON_REVIEW_READY,
        {"TASK-BOOT-001": task},
    )
    event["event_id"] = "evt-review-boot-1"
    event["event_key"] = event["key"]
    event["target_display_name"] = "Codex"

    state = {
        "workers": {},
        "queue": {
            "events": {
                "evt-review-boot-1": {
                    "status": "started",
                    "lease_owner": "run-codex-dead",
                }
            }
        },
    }

    with (
        mock.patch.object(supervisor, "load_status", return_value=status),
        mock.patch.object(supervisor, "load_event_queue", return_value=[event]),
        mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=True),
        mock.patch.object(supervisor, "write_activity_log"),
    ):
        changed = supervisor.reconcile_runtime_on_boot(cfg, state)

    assert changed is True
    record = state["queue"]["events"]["evt-review-boot-1"]
    assert record["status"] == "completed"
    assert record["skip_reason"] == "stale_dispatch_event"


def test_genuine_review_state_dispatches_reviewer() -> None:
    cfg = _base_test_config()
    task = {
        "id": "TASK-GENUINE-REV-001",
        "priority": "P1",
        "status": "review",
        "owner": "Claude",
        "reviewer": "Codex",
        "review_submission": {
            "pr_number": 101,
            "branch": "task/TASK-GENUINE-REV-001",
            "base_branch": "dev",
            "remote_sha": "a" * 40,
        },
        "depends_on": [],
        "last_update": "2026-08-20T10:00:00Z",
    }
    status = {"tasks": [task]}
    state = {"workers": {}, "queue": {"events": {}}}
    queued_events: list[dict] = []

    with (
        mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value="a" * 40),
        mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", return_value=("OPEN", "success")),
    ):
        assert dispatch_engine.is_task_review_dispatch_eligible(cfg, task, "Codex") is True
        assert supervisor.is_task_review_dispatch_eligible(cfg, task, "Codex") is True
        assert supervisor.dispatch_priority_for_task(cfg, task, "Codex", task_map={"TASK-GENUINE-REV-001": task}) == 0

    with (
        mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value="a" * 40),
        mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", return_value=("OPEN", "success")),
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
        changed = supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["codex"])

    assert changed is True
    assert len(queued_events) == 1
    assert queued_events[0]["task_id"] == "TASK-GENUINE-REV-001"
    assert queued_events[0]["target_agent"] == "Codex"
    assert queued_events[0]["reason"] == "review_ready_dispatch"


def test_review_dispatch_suppressed_when_ci_is_pending() -> None:
    cfg = _base_test_config()
    task = {
        "id": "TASK-CI-PENDING-001",
        "priority": "P1",
        "status": "review",
        "owner": "Claude",
        "reviewer": "Codex",
        "review_submission": {
            "pr_number": 102,
            "branch": "task/TASK-CI-PENDING-001",
            "base_branch": "dev",
            "remote_sha": "b" * 40,
        },
        "depends_on": [],
        "last_update": "2026-08-20T10:00:00Z",
    }
    status = {"tasks": [task]}
    state = {"workers": {}, "queue": {"events": {}}}
    queued_events: list[dict] = []

    with (
        mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value="b" * 40),
        mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", return_value=("OPEN", "pending")),
    ):
        assert dispatch_engine.is_task_review_dispatch_eligible(cfg, task, "Codex") is False
        assert supervisor.is_task_review_dispatch_eligible(cfg, task, "Codex") is False
        assert supervisor.dispatch_priority_for_task(cfg, task, "Codex", task_map={"TASK-CI-PENDING-001": task}) is None

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
            supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["codex"])

    assert len(queued_events) == 0
    assert task["status"] == "review"
    assert "CI checks pending" in task.get("next", "")


def test_review_dispatch_suppressed_when_ci_is_failure() -> None:
    cfg = _base_test_config()
    task = {
        "id": "TASK-CI-FAIL-001",
        "priority": "P1",
        "status": "review",
        "owner": "Claude",
        "reviewer": "Codex",
        "review_submission": {
            "pr_number": 103,
            "branch": "task/TASK-CI-FAIL-001",
            "base_branch": "dev",
            "remote_sha": "c" * 40,
        },
        "depends_on": [],
        "last_update": "2026-08-20T10:00:00Z",
    }
    status = {"tasks": [task]}
    state = {"workers": {}, "queue": {"events": {}}}
    queued_events: list[dict] = []

    with (
        mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value="c" * 40),
        mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", return_value=("OPEN", "failure")),
    ):
        assert dispatch_engine.is_task_review_dispatch_eligible(cfg, task, "Codex") is False
        assert supervisor.is_task_review_dispatch_eligible(cfg, task, "Codex") is False
        assert supervisor.dispatch_priority_for_task(cfg, task, "Codex", task_map={"TASK-CI-FAIL-001": task}) is None

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
            supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["codex"])

    assert len(queued_events) == 0
    assert task["status"] == "review"
    assert "CI failure" in task.get("next", "")


def test_review_dispatch_suppressed_when_head_has_drifted() -> None:
    cfg = _base_test_config()
    task = {
        "id": "TASK-DRIFT-001",
        "priority": "P1",
        "status": "review",
        "owner": "Claude",
        "reviewer": "Codex",
        "review_submission": {
            "pr_number": 104,
            "branch": "task/TASK-DRIFT-001",
            "base_branch": "dev",
            "remote_sha": "d" * 40,
        },
        "depends_on": [],
        "last_update": "2026-08-20T10:00:00Z",
    }
    status = {"tasks": [task]}
    state = {"workers": {}, "queue": {"events": {}}}
    queued_events: list[dict] = []

    with (
        mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value="e" * 40),
        mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", return_value=("OPEN", "success")),
    ):
        assert dispatch_engine.is_task_review_dispatch_eligible(cfg, task, "Codex") is False
        assert supervisor.is_task_review_dispatch_eligible(cfg, task, "Codex") is False
        assert supervisor.dispatch_priority_for_task(cfg, task, "Codex", task_map={"TASK-DRIFT-001": task}) is None

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
            supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["codex"])

    assert len(queued_events) == 0
    assert task["status"] == "review"
    assert "drifted" in task.get("next", "")


def test_review_dispatch_suppressed_when_remote_head_is_missing() -> None:
    cfg = _base_test_config()
    task = {
        "id": "TASK-MISSING-001",
        "priority": "P1",
        "status": "review",
        "owner": "Claude",
        "reviewer": "Codex",
        "review_submission": {
            "pr_number": 105,
            "branch": "task/TASK-MISSING-001",
            "base_branch": "dev",
            "remote_sha": "f" * 40,
        },
        "depends_on": [],
        "last_update": "2026-08-20T10:00:00Z",
    }
    status = {"tasks": [task]}
    state = {"workers": {}, "queue": {"events": {}}}
    queued_events: list[dict] = []

    with (
        mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value=None),
        mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", return_value=("OPEN", "success")),
    ):
        assert dispatch_engine.is_task_review_dispatch_eligible(cfg, task, "Codex") is False
        assert supervisor.is_task_review_dispatch_eligible(cfg, task, "Codex") is False
        assert supervisor.dispatch_priority_for_task(cfg, task, "Codex", task_map={"TASK-MISSING-001": task}) is None

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
            supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["codex"])

    assert len(queued_events) == 0
    assert task["status"] == "review"
    assert "Cannot verify branch HEAD" in task.get("next", "")


def test_review_dispatch_suppressed_when_merge_group_failed_retains_reason() -> None:
    cfg = _base_test_config()
    task = {
        "id": "TASK-MG-FAIL-001",
        "priority": "P1",
        "status": "review",
        "owner": "Claude",
        "reviewer": "Codex",
        "next": "Merge group run 999 failed on gh-readonly-queue/dev/pr-106-abc; reviewer recovery handoff dispatched to Codex.",
        "review_submission": {
            "pr_number": 106,
            "branch": "task/TASK-MG-FAIL-001",
            "base_branch": "dev",
            "remote_sha": "9" * 40,
        },
        "depends_on": [],
        "last_update": "2026-08-20T10:00:00Z",
    }
    status = {"tasks": [task]}
    state = {"workers": {}, "queue": {"events": {}}}
    queued_events: list[dict] = []

    with (
        mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value="9" * 40),
        mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", return_value=("OPEN", "failure")),
    ):
        assert dispatch_engine.is_task_review_dispatch_eligible(cfg, task, "Codex") is False
        assert supervisor.is_task_review_dispatch_eligible(cfg, task, "Codex") is False
        assert supervisor.dispatch_priority_for_task(cfg, task, "Codex", task_map={"TASK-MG-FAIL-001": task}) is None

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
            supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["codex"])

    assert len(queued_events) == 0
    assert task["status"] == "review"
    assert "Merge group run 999 failed" in task["next"]


def test_stale_wake_for_review_ready_skipped_when_ci_is_pending_or_failed() -> None:
    cfg = _base_test_config()
    task = {
        "id": "TASK-WAKE-CI-001",
        "status": "review",
        "owner": "Claude",
        "reviewer": "Codex",
        "review_submission": {
            "pr_number": 107,
            "branch": "task/TASK-WAKE-CI-001",
            "base_branch": "dev",
            "remote_sha": "8" * 40,
        },
        "depends_on": [],
    }
    task_map = {task["id"]: task}

    # Positive: while CI is success, wake is not stale
    with (
        mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value="8" * 40),
        mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", return_value=("OPEN", "success")),
    ):
        event = supervisor.build_dispatch_event(task, "Codex", REASON_REVIEW_READY, task_map)
        event["event_key"] = event["key"]
        event["target_display_name"] = "Codex"
        assert supervisor.stale_dispatch_skip_message(cfg, event, task_map) is None

    # Case 1: CI becomes pending
    with (
        mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value="8" * 40),
        mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", return_value=("OPEN", "pending")),
    ):
        assert supervisor.current_dispatch_event_key(cfg, event, task_map) is None
        skip_msg = supervisor.stale_dispatch_skip_message(cfg, event, task_map)
        assert skip_msg is not None
        assert "no longer eligible" in skip_msg

    # Case 2: CI becomes failure
    with (
        mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value="8" * 40),
        mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", return_value=("OPEN", "failure")),
    ):
        assert supervisor.current_dispatch_event_key(cfg, event, task_map) is None
        skip_msg = supervisor.stale_dispatch_skip_message(cfg, event, task_map)
        assert skip_msg is not None
        assert "no longer eligible" in skip_msg


def test_review_dispatch_eligible_when_task_review_gate_pending_but_other_ci_green() -> None:
    cfg = _base_test_config()
    task = {
        "id": "TASK-REV-GATE-PENDING-001",
        "priority": "P1",
        "status": "review",
        "owner": "Claude",
        "reviewer": "Codex",
        "review_submission": {
            "pr_number": 108,
            "branch": "task/TASK-REV-GATE-PENDING-001",
            "base_branch": "dev",
            "remote_sha": "7" * 40,
        },
        "depends_on": [],
        "last_update": "2026-08-20T10:00:00Z",
    }
    status = {"tasks": [task]}
    state = {"workers": {}, "queue": {"events": {}}}
    queued_events: list[dict] = []

    def fake_gh_json(args, *, cwd=None):
        return {
            "state": "OPEN",
            "statusCheckRollup": [
                {
                    "__typename": "CheckRun",
                    "name": "orchestrator",
                    "workflowName": "orchestrator",
                    "status": "COMPLETED",
                    "conclusion": "SUCCESS",
                },
                {
                    "__typename": "CheckRun",
                    "name": "product",
                    "workflowName": "product",
                    "status": "COMPLETED",
                    "conclusion": "SUCCESS",
                },
                {
                    "__typename": "StatusContext",
                    "context": "task-review-gate",
                    "state": "PENDING",
                },
            ],
        }

    supervisor.runtime_ai_status._CI_STATUS_CACHE.clear()
    with (
        mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value="7" * 40),
        mock.patch.object(supervisor.runtime_ai_status, "run_gh_json_command", side_effect=fake_gh_json),
        mock.patch.object(supervisor.runtime_ai_status, "task_pr_lookup_scope", return_value=(Path("/"), [], 108)),
    ):
        assert dispatch_engine.is_task_review_dispatch_eligible(cfg, task, "Codex") is True
        assert supervisor.is_task_review_dispatch_eligible(cfg, task, "Codex") is True
        assert supervisor.dispatch_priority_for_task(cfg, task, "Codex", task_map={"TASK-REV-GATE-PENDING-001": task}) == 0

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
            changed = supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["codex"])

    assert changed is True
    assert len(queued_events) == 1
    assert queued_events[0]["task_id"] == "TASK-REV-GATE-PENDING-001"
    assert queued_events[0]["target_agent"] == "Codex"
    assert queued_events[0]["reason"] == "review_ready_dispatch"


