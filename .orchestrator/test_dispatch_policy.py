import json
import subprocess
import sys
import uuid
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import dispatch_engine
import pytest
import status_transition
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
    dispatch_priority_reason,
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
    cfg["ready_dispatcher"]["reviewer_failover"] = {"enabled": False}
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


def test_merge_route_without_head_or_malformed_fails_closed() -> None:
    """Ensure is_task_review_dispatch_eligible fails closed when merge_route lacks a valid head or is malformed."""
    cfg = _base_test_config()
    new_head = "0b3e19870b3e19870b3e19870b3e19870b3e1987"
    task = {
        "id": "ODP-TEST-MALFORMED-ROUTE-001",
        "priority": "P1",
        "status": "review",
        "owner": "Claude",
        "reviewer": "Codex",
        "review_submission": {
            "pr_number": 1175,
            "branch": "task/ODP-TEST-MALFORMED-ROUTE-001",
            "base_branch": "dev",
            "remote_sha": new_head,
        },
        "depends_on": [],
        "last_update": "2026-09-05T00:53:14Z",
    }

    # Helper context manager for happy-path SHA and CI
    def _happy_path_mocks():
        return (
            mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value=new_head),
            mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", return_value=("OPEN", "success")),
        )

    # 1. No merge_route at all -> eligible
    with _happy_path_mocks()[0], _happy_path_mocks()[1]:
        assert dispatch_engine.is_task_review_dispatch_eligible(cfg, task, "Codex") is True

    # 2. Malformed / headless dicts fail closed
    headless_routes = [
        {"route": "queued"},
        {"head": "", "route": "queued"},
        {"head": "   ", "route": "queued"},
        {"head": None, "route": "queued"},
        {},
    ]
    for bad_route in headless_routes:
        task["merge_route"] = bad_route
        with _happy_path_mocks()[0], _happy_path_mocks()[1]:
            assert dispatch_engine.is_task_review_dispatch_eligible(cfg, task, "Codex") is False, f"Failed for {bad_route}"

    # 3. Malformed non-dict values fail closed
    malformed_routes = [
        "queued",
        123,
        True,
        ["queued"],
    ]
    for bad_route in malformed_routes:
        task["merge_route"] = bad_route
        with _happy_path_mocks()[0], _happy_path_mocks()[1]:
            assert dispatch_engine.is_task_review_dispatch_eligible(cfg, task, "Codex") is False, f"Failed for {bad_route}"

    # 4. Valid route for prior stale head allows review
    task["merge_route"] = {"head": "a801faf568b66e700f8691b02ceeec8358fa2aa9", "route": "queued"}
    with _happy_path_mocks()[0], _happy_path_mocks()[1]:
        assert dispatch_engine.is_task_review_dispatch_eligible(cfg, task, "Codex") is True

    # 5. Valid route for current head suppresses review
    task["merge_route"] = {"head": new_head, "route": "queued"}
    with _happy_path_mocks()[0], _happy_path_mocks()[1]:
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




# --- Conflicted review PR recovery (ODP-REVIEW-CONFLICT-CI-RECOVERY-001) -----
#
# A review PR that conflicts with its base has no merge commit, so GitHub runs
# no workflow on that head: check-runs, check-suites and Actions runs are all
# zero, `task_pr_ci_status` answers `none`, and reviewer dispatch - which
# requires terminal CI success on the exact submitted head - waits forever.
# These cover the one restricted recovery entry and, at least as importantly,
# every reading it must decline to act on.

#: The exact head #1170 sat on: OPEN, CONFLICTING, zero checks, waiting on a
#: check that could not arrive.
CONFLICT_HEAD = "847d498493343d0e8c1227f4eb3bf64a04448fa6"
CONFLICT_REPO = "alfloop-dev/odayplus"
CONFLICT_TASK_ID = "ODP-CONFLICT-001"


def _conflicted_review_task(**overrides) -> dict:
    """A review whose PR provenance is verified and whose head is unmerged."""
    task = {
        "id": CONFLICT_TASK_ID,
        "status": "review",
        "owner": "Claude",
        "reviewer": "Codex",
        "repository": CONFLICT_REPO,
        "branch": f"task/{CONFLICT_TASK_ID}",
        "pr_number": 1170,
        "depends_on": [],
        "acceptance": ["the original acceptance must survive recovery"],
        "review_reopen_count": 2,
        "review_churn_reassigned_at_count": 2,
        "human_continuation_approval_history": [
            {"approval_id": "hc-1", "status": "consumed"}
        ],
        "waiting_for": "Codex",
        "review_submission": {
            "pr_number": 1170,
            "branch": f"task/{CONFLICT_TASK_ID}",
            "base_branch": "dev",
            "remote_sha": CONFLICT_HEAD,
            "pr_url": f"https://github.com/{CONFLICT_REPO}/pull/1170",
        },
    }
    task.update(overrides)
    return task


def _pr_facts(state="OPEN", merge_state="CONFLICTING", head=CONFLICT_HEAD) -> dict:
    payload = {"state": state, "mergeStateStatus": merge_state, "headRefOid": head}
    return {key: value for key, value in payload.items() if value is not None}


def _run_recovery(
    task,
    *,
    ci=("OPEN", "none"),
    ci_error=None,
    reads=None,
    status=None,
    busy_task_ids=frozenset(),
    commit_ok=True,
    timeline=None,
):
    """Drive the recovery over one task and report what it did.

    `reads` is the sequence of `gh pr view` answers: a dict is a JSON payload,
    a str is raw stdout, and None makes `gh` fail. The last entry repeats, so a
    single-element list means both reads agree.

    `ci` and `ci_error` take the same shape: a bare answer applies to every CI
    read, and a list walks the reads in order with its last entry repeating, so
    a two-entry list is "the cached verdict said one thing and the live one says
    another". `timeline`, if given, collects `("gh", selector)` and
    `("ci", max_age_seconds)` in call order, which is how the ordering of the
    two PR reads around the authorising CI read is asserted.
    """
    import github_bus

    cfg = _base_test_config()
    if status is None:
        status = {
            "tasks": [task],
            "handoffs": [
                {
                    "task_id": task["id"],
                    "from": task.get("owner"),
                    "to": task.get("reviewer"),
                    "status": "pending",
                }
            ],
        }
    # `recover_conflicted_review_prs` resolves supervisor-owned names through
    # the module scope its entrypoint caller syncs, so sync it here too.
    dispatch_engine._sync_supervisor_scope()

    remaining = list(reads if reads is not None else [_pr_facts()])
    gh_calls: list[list[str]] = []
    events = timeline if timeline is not None else []

    def fake_run_gh(args, **_kwargs):
        gh_calls.append(list(args))
        events.append(("gh", args[2] if len(args) > 2 else ""))
        answer = remaining.pop(0) if len(remaining) > 1 else (remaining[0] if remaining else {})
        if answer is None:
            raise github_bus.GitHubBusOffline("gh could not reach api.github.com")
        stdout = answer if isinstance(answer, str) else json.dumps(answer)
        return subprocess.CompletedProcess(
            args=["gh", *args], returncode=0, stdout=stdout, stderr=""
        )

    ci_answers = list(ci) if isinstance(ci, list) else [ci]
    ci_errors = list(ci_error) if isinstance(ci_error, list) else [ci_error]

    def fake_ci(_task_id, *_args, **kwargs):
        events.append(("ci", kwargs.get("max_age_seconds")))
        error = ci_errors.pop(0) if len(ci_errors) > 1 else ci_errors[0]
        if error is not None:
            raise error
        return ci_answers.pop(0) if len(ci_answers) > 1 else ci_answers[0]

    logged: list[dict] = []
    record = lambda _cfg, event: logged.append(event)  # noqa: E731

    with (
        mock.patch.object(github_bus, "run_gh", side_effect=fake_run_gh),
        mock.patch.object(
            supervisor.runtime_ai_status, "task_pr_ci_status", side_effect=fake_ci
        ),
        mock.patch.object(
            supervisor, "commit_canonical_task_transition", return_value=commit_ok
        ),
        mock.patch.object(supervisor, "write_activity_log", side_effect=record),
        mock.patch.object(dispatch_engine, "write_activity_log", create=True, side_effect=record),
    ):
        changed = dispatch_engine.recover_conflicted_review_prs(
            cfg, status, {"review"}, busy_task_ids=set(busy_task_ids)
        )
    return changed, gh_calls, logged, status


def test_conflicted_review_with_no_ci_is_returned_to_its_owner() -> None:
    """#1170's exact shape: OPEN, conflicting, zero checks, waiting forever."""
    task = _conflicted_review_task()

    changed, gh_calls, logged, status = _run_recovery(task)

    assert changed is True
    assert task["status"] == "in_progress"
    # Handed back to the same owner; nothing about the review is rewritten.
    assert task["owner"] == "Claude"
    assert task["reviewer"] == "Codex"
    assert task["acceptance"] == ["the original acceptance must survive recovery"]
    assert task["review_reopen_count"] == 2
    assert task["review_churn_reassigned_at_count"] == 2
    assert task["human_continuation_approval_history"] == [
        {"approval_id": "hc-1", "status": "consumed"}
    ]
    assert task["review_submission"]["remote_sha"] == CONFLICT_HEAD
    assert task[dispatch_engine.REVIEW_CONFLICT_RECOVERY_HEAD_FIELD] == CONFLICT_HEAD
    # The reviewer must not keep holding work that went back to its owner.
    assert "waiting_for" not in task
    assert status["handoffs"][0]["status"] == "done"
    # `next` has to say what an owner must do, not just that something happened.
    assert "conflicts with its base" in task["next"]
    assert CONFLICT_HEAD[:8] in task["next"]
    assert "advance the base" in task["next"]
    # The transition is the canonical one, entered by its named restricted door,
    # under the reopen category that review churn deliberately does not count.
    requeued = [event for event in logged if event["type"] == "ci_repair_requeued"]
    assert len(requeued) == 1
    assert requeued[0]["entry"] == "conflicted_review"
    assert requeued[0]["category"] == "control_plane_recovery"
    assert requeued[0]["approval_cleared"] is False
    recovered = [event for event in logged if event["type"] == "review_conflict_ci_recovered"]
    assert len(recovered) == 1
    assert recovered[0]["pr_number"] == 1170
    assert recovered[0]["head"] == CONFLICT_HEAD
    # Both reads are bound to the task's own repository and PR number: asking
    # this checkout's origin about #1170 answers about an unrelated PR.
    assert len(gh_calls) == 2
    for call in gh_calls:
        assert call[:3] == ["pr", "view", "1170"]
        assert call[call.index("--repo") + 1] == CONFLICT_REPO


@pytest.mark.parametrize(
    ("label", "kwargs"),
    [
        # Every CI answer other than "GitHub has run nothing here".
        ("ci_pending", {"ci": ("OPEN", "pending")}),
        ("ci_success", {"ci": ("OPEN", "success")}),
        ("ci_failure", {"ci": ("OPEN", "failure")}),
        ("ci_unknown", {"ci": ("OPEN", "unknown")}),
        ("ci_unreadable_pr", {"ci": (None, "unknown")}),
        ("ci_probe_raises", {"ci_error": RuntimeError("gh unreachable")}),
        # The PR is no longer an open review.
        ("pr_closed_by_ci_probe", {"ci": ("CLOSED", "none")}),
        ("pr_closed", {"reads": [_pr_facts(state="CLOSED")]}),
        ("pr_merged", {"reads": [_pr_facts(state="MERGED")]}),
        # Merge states that resolve without an owner touching the branch.
        ("merge_state_clean", {"reads": [_pr_facts(merge_state="CLEAN")]}),
        ("merge_state_blocked", {"reads": [_pr_facts(merge_state="BLOCKED")]}),
        ("merge_state_behind", {"reads": [_pr_facts(merge_state="BEHIND")]}),
        ("merge_state_unknown", {"reads": [_pr_facts(merge_state="UNKNOWN")]}),
        # GitHub could not be read, or did not answer the whole question.
        ("gh_offline", {"reads": [None]}),
        ("gh_malformed_json", {"reads": ["not json"]}),
        ("gh_empty_payload", {"reads": [{}]}),
        ("missing_head", {"reads": [_pr_facts(head=None)]}),
        ("missing_merge_state", {"reads": [_pr_facts(merge_state=None)]}),
        ("missing_state", {"reads": [_pr_facts(state=None)]}),
        # The branch moved past what was submitted for review.
        ("head_drift", {"reads": [_pr_facts(head="b" * 40)]}),
    ],
)
def test_recovery_declines_every_unconfirmable_reading(label: str, kwargs: dict) -> None:
    """Only facts GitHub states outright may move a review. Otherwise, wait."""
    task = _conflicted_review_task()

    changed, _gh_calls, logged, status = _run_recovery(task, **kwargs)

    assert changed is False, label
    assert task["status"] == "review", label
    assert dispatch_engine.REVIEW_CONFLICT_RECOVERY_HEAD_FIELD not in task, label
    assert task["waiting_for"] == "Codex", label
    assert status["handoffs"][0]["status"] == "pending", label
    assert logged == [], label


def test_recovery_declines_when_the_facts_change_between_the_two_reads() -> None:
    """A head that moves, a PR that closes, or a conflict resolved mid-check."""
    for second_read in (
        _pr_facts(head="c" * 40),
        _pr_facts(state="CLOSED"),
        _pr_facts(merge_state="CLEAN"),
        None,
    ):
        task = _conflicted_review_task()

        changed, gh_calls, _logged, _status = _run_recovery(
            task, reads=[_pr_facts(), second_read]
        )

        assert changed is False
        assert task["status"] == "review"
        assert dispatch_engine.REVIEW_CONFLICT_RECOVERY_HEAD_FIELD not in task
        assert len(gh_calls) == 2


def test_the_authorising_ci_read_is_taken_fresh_and_after_the_first_pr_read() -> None:
    """The premise of this repair cannot be served from a cache.

    `task_pr_ci_status` answers from a ten-second cache by default, so the
    verdict that opened this lane may describe a moment before the PR reads
    that follow it. That first read stays -- it is the cheap way to drop the
    tasks that are not this shape -- but the transition is authorised by a
    second read taken with the cache bypassed, after the PR facts and before
    they are confirmed unchanged.
    """
    task = _conflicted_review_task()
    timeline: list[tuple[str, object]] = []

    changed, gh_calls, _logged, _status = _run_recovery(task, timeline=timeline)

    assert changed is True
    assert len(gh_calls) == 2
    assert timeline == [
        # Cheap disqualifier: whatever the reader already knows.
        ("ci", None),
        # PR facts A.
        ("gh", "1170"),
        # The read the transition actually rests on, cache bypassed.
        ("ci", 0),
        # PR facts B, confirming nothing moved while CI was asked.
        ("gh", "1170"),
    ]


@pytest.mark.parametrize(
    ("label", "kwargs"),
    [
        # The conflict was resolved a moment ago and GitHub has started the
        # checks. Requeueing now pulls a review out from under a live run.
        ("checks_started", {"ci": [("OPEN", "none"), ("OPEN", "pending")]}),
        ("checks_finished_green", {"ci": [("OPEN", "none"), ("OPEN", "success")]}),
        ("checks_finished_red", {"ci": [("OPEN", "none"), ("OPEN", "failure")]}),
        # `gh` could not answer the second time; an unreadable state is not a
        # confirmed empty one.
        ("ci_unknown", {"ci": [("OPEN", "none"), ("OPEN", "unknown")]}),
        ("pr_unreadable", {"ci": [("OPEN", "none"), (None, "unknown")]}),
        # The PR stopped being an open review between the two reads.
        ("pr_closed", {"ci": [("OPEN", "none"), ("CLOSED", "none")]}),
        ("pr_merged", {"ci": [("OPEN", "none"), ("MERGED", "none")]}),
        # The live read itself failed.
        ("fresh_read_raises", {"ci_error": [None, RuntimeError("gh unreachable")]}),
    ],
)
def test_ci_that_starts_after_the_cached_verdict_stops_the_recovery(
    label: str, kwargs: dict
) -> None:
    """The race the single cached read could not see."""
    task = _conflicted_review_task()
    timeline: list[tuple[str, object]] = []

    changed, gh_calls, logged, status = _run_recovery(task, timeline=timeline, **kwargs)

    assert changed is False, label
    assert task["status"] == "review", label
    # No marker: this head is unresolved, not recovered, and a later tick that
    # finds it genuinely checkless must still be able to act on it.
    assert dispatch_engine.REVIEW_CONFLICT_RECOVERY_HEAD_FIELD not in task, label
    assert task["waiting_for"] == "Codex", label
    assert status["handoffs"][0]["status"] == "pending", label
    assert logged == [], label
    # It stopped at the fresh read: PR facts A was taken, PR facts B never was.
    assert timeline == [("ci", None), ("gh", "1170"), ("ci", 0)], label
    assert len(gh_calls) == 1, label


@pytest.mark.parametrize(
    ("label", "task_overrides", "busy_task_ids"),
    [
        ("human_gate_class", {"task_class": "human_gate"}, frozenset()),
        ("human_required_roles", {"human_required_roles": ["ops"]}, frozenset()),
        ("pending_human_gate", {"gate_status": "pending_human_review"}, frozenset()),
        ("human_waiting_gate", {"waiting_for": "Human/Ops"}, frozenset()),
        ("human_owner", {"owner": "Human/Ops"}, frozenset()),
        ("non_dispatchable", {"non_dispatchable": True}, frozenset()),
        ("active_or_pending_worker", {}, frozenset({CONFLICT_TASK_ID})),
        (
            "live_helper_lease",
            {
                "helper_execution_lease": {
                    "claimed_by": "Antigravity7",
                    "lease_expires_at": (
                        datetime.now(UTC) + timedelta(minutes=20)
                    ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            },
            frozenset(),
        ),
        ("frozen_approved_head", {"approved_head": CONFLICT_HEAD}, frozenset()),
        (
            "queued_merge_route",
            {"merge_route": {"head": CONFLICT_HEAD, "route": "queued"}},
            frozenset(),
        ),
        ("unsubmitted_review", {"review_submission": None}, frozenset()),
        (
            "submission_head_not_a_sha",
            {
                "review_submission": {
                    "pr_number": 1170,
                    "branch": f"task/{CONFLICT_TASK_ID}",
                    "base_branch": "dev",
                    "remote_sha": "HEAD",
                }
            },
            frozenset(),
        ),
        ("already_in_progress", {"status": "in_progress"}, frozenset()),
        ("already_approved", {"status": "review_approved"}, frozenset()),
    ],
)
def test_conflicted_review_never_acts_on_a_gated_frozen_or_busy_task(
    label: str, task_overrides: dict, busy_task_ids: frozenset
) -> None:
    """These are decided locally, so GitHub is never asked about them at all."""
    original_status = str(_conflicted_review_task(**task_overrides)["status"])
    task = _conflicted_review_task(**task_overrides)

    changed, gh_calls, logged, status = _run_recovery(task, busy_task_ids=busy_task_ids)

    assert changed is False, label
    assert task["status"] == original_status, label
    assert dispatch_engine.REVIEW_CONFLICT_RECOVERY_HEAD_FIELD not in task, label
    assert status["handoffs"][0]["status"] == "pending", label
    assert gh_calls == [], label
    assert logged == [], label


def test_conflicted_review_declines_when_the_task_repository_cannot_be_resolved() -> None:
    """A PR number is only meaningful against a known repository."""
    task = _conflicted_review_task()
    task.pop("repository")

    with mock.patch.object(dispatch_engine, "_task_repository_slug", return_value=""):
        changed, gh_calls, _logged, _status = _run_recovery(task)

    assert changed is False
    assert task["status"] == "review"
    assert gh_calls == []


def test_conflicted_review_repeated_polls_and_restarts_recover_one_head_exactly_once() -> None:
    """The marker is written by the same commit that moves the status."""
    task = _conflicted_review_task()
    status = {
        "tasks": [task],
        "handoffs": [
            {"task_id": CONFLICT_TASK_ID, "from": "Claude", "to": "Codex", "status": "pending"}
        ],
    }

    first, _calls, _logged, _status = _run_recovery(task, status=status)
    assert first is True
    assert task["status"] == "in_progress"

    # Same tick, polled again: the task is no longer in review.
    second, second_calls, _logged, _status = _run_recovery(task, status=status)
    assert second is False
    assert second_calls == []

    # After a supervisor restart the owner has resubmitted the identical
    # conflicting head. Bouncing it again would be a loop, not a recovery.
    resubmitted = _conflicted_review_task(
        **{
            dispatch_engine.REVIEW_CONFLICT_RECOVERY_HEAD_FIELD: CONFLICT_HEAD,
        }
    )
    third, third_calls, third_logged, _status = _run_recovery(resubmitted)
    assert third is False
    assert resubmitted["status"] == "review"
    assert third_calls == []
    assert third_logged == []

    # A genuinely new head is a new fact and is recovered on its own merits.
    advanced_head = "d" * 40
    advanced = _conflicted_review_task(
        **{dispatch_engine.REVIEW_CONFLICT_RECOVERY_HEAD_FIELD: CONFLICT_HEAD}
    )
    advanced["review_submission"]["remote_sha"] = advanced_head
    fourth, _calls, _logged, _status = _run_recovery(
        advanced, reads=[_pr_facts(head=advanced_head)]
    )
    assert fourth is True
    assert advanced[dispatch_engine.REVIEW_CONFLICT_RECOVERY_HEAD_FIELD] == advanced_head


def test_conflicted_review_a_recovery_that_does_not_persist_leaves_the_head_recoverable() -> None:
    """Marking a head recovered on a commit that never landed would strand it."""
    task = _conflicted_review_task()

    changed, _calls, logged, _status = _run_recovery(task, commit_ok=False)

    assert changed is False
    assert dispatch_engine.REVIEW_CONFLICT_RECOVERY_HEAD_FIELD not in task
    assert logged == []


def test_canonical_ci_repair_transition_keeps_its_review_approved_guard() -> None:
    """The widened guard belongs to the named entry, not to every caller."""
    cfg = _base_test_config()

    def _requeue(task, **kwargs):
        status = {"tasks": [task], "handoffs": []}
        logged: list[dict] = []
        with (
            mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=True),
            mock.patch.object(
                supervisor, "write_activity_log", side_effect=lambda _c, e: logged.append(e)
            ),
        ):
            return supervisor.requeue_task_for_ci_repair(
                cfg, status, task, message="repair", **kwargs
            ), logged

    # A review is still refused by every existing caller.
    task = _conflicted_review_task()
    changed, logged = _requeue(task, clear_approval=True)
    assert changed is False
    assert task["status"] == "review"
    assert logged == []

    # And the queue-ejection lane is unchanged.
    approved = _conflicted_review_task(
        status="review_approved",
        approved_head=CONFLICT_HEAD,
        merge_route={"head": CONFLICT_HEAD, "route": "queued"},
    )
    changed, logged = _requeue(approved, clear_approval=True)
    assert changed is True
    assert approved["status"] == "in_progress"
    assert "approved_head" not in approved
    assert "merge_route" not in approved
    assert logged[0]["entry"] == "review_approved"


@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        ("frozen_approved_head", {"approved_head": CONFLICT_HEAD}),
        ("queued_merge_route", {"merge_route": {"head": CONFLICT_HEAD, "route": "queued"}}),
        ("unsubmitted_review", {"review_submission": None}),
        ("human_gate", {"task_class": "human_gate"}),
        ("human_waiting_gate", {"waiting_for": "Human/Ops"}),
        ("human_owner", {"owner": "Human/Ops"}),
        ("non_dispatchable", {"non_dispatchable": True}),
        ("wrong_status", {"status": "in_progress"}),
    ],
)
def test_conflicted_review_named_entry_still_refuses_invalid_review(
    label: str, overrides: dict
) -> None:
    """Even through the restricted door, the transition guards itself."""
    cfg = _base_test_config()
    task = _conflicted_review_task(**overrides)
    original_status = task["status"]
    status = {"tasks": [task], "handoffs": []}

    with (
        mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=True),
        mock.patch.object(supervisor, "write_activity_log"),
    ):
        changed = supervisor.requeue_task_for_ci_repair(
            cfg,
            status,
            task,
            message="repair",
            clear_approval=False,
            allow_conflicted_review=True,
        )

    assert changed is False, label
    assert task["status"] == original_status, label


def test_conflicted_review_dispatch_ready_tasks_recovers_without_reviewer_slot() -> None:
    """The wait is on the reviewer's slot, so the repair must not need one."""
    import github_bus

    cfg = _base_test_config()
    task = _conflicted_review_task()
    status = {
        "tasks": [task],
        "handoffs": [
            {"task_id": CONFLICT_TASK_ID, "from": "Claude", "to": "Codex", "status": "pending"}
        ],
    }
    state = {"workers": {}, "queue": {"events": {}}}
    queued_events: list[dict] = []

    def fake_run_gh(args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["gh", *args], returncode=0, stdout=json.dumps(_pr_facts()), stderr=""
        )

    with (
        mock.patch.object(github_bus, "run_gh", side_effect=fake_run_gh),
        mock.patch.object(
            supervisor.runtime_ai_status,
            "task_pr_ci_status",
            return_value=("OPEN", "none"),
        ),
        mock.patch.object(supervisor, "load_status", return_value=status),
        mock.patch.object(supervisor, "load_event_queue", return_value=[]),
        mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=True),
        mock.patch.object(supervisor, "write_activity_log"),
        mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
        mock.patch.object(
            dispatch_engine, "task_reality_reconcile_is_due", return_value=False
        ),
        mock.patch.object(
            supervisor,
            "queue_delivery_event",
            side_effect=lambda _c, evt: queued_events.append(evt) or True,
        ),
    ):
        changed = supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["antigravity7"])

    assert changed is True
    assert queued_events == []
    assert task["status"] == "in_progress"
    assert task["owner"] == "Claude"
    assert task[dispatch_engine.REVIEW_CONFLICT_RECOVERY_HEAD_FIELD] == CONFLICT_HEAD
    assert status["handoffs"][0]["status"] == "done"


# --- Review CI failure recovery -----------------------------------------------
#
# An unapproved review PR whose required CI fails is suppressed from reviewer
# dispatch, but without recovery it sits in `review` indefinitely: the reviewer
# will not review a red PR, and the owner is never re-dispatched.
# `recover_failed_ci_review_prs` recovers these back to `in_progress` under
# `control_plane_recovery`.

FAILED_CI_HEAD = "a964c83d66103444953e4ed2e8741b8e484a0f42"
FAILED_CI_REPO = "alfloop-dev/odayplus"
FAILED_CI_TASK_ID = "ODP-DATA-PLANE-DELETE-PROPAGATION-001"


def _failed_ci_review_task(**overrides) -> dict:
    """A review task whose PR CI has failed."""
    task = {
        "id": FAILED_CI_TASK_ID,
        "status": "review",
        "owner": "Antigravity",
        "reviewer": "Codex2",
        "repository": FAILED_CI_REPO,
        "branch": f"task/{FAILED_CI_TASK_ID}",
        "pr_number": 1282,
        "depends_on": [],
        "acceptance": ["the original acceptance must survive recovery"],
        "review_reopen_count": 1,
        "review_churn_reassigned_at_count": 1,
        "human_continuation_approval_history": [
            {"approval_id": "hc-1", "status": "consumed"}
        ],
        "waiting_for": "Codex2",
        "review_submission": {
            "pr_number": 1282,
            "branch": f"task/{FAILED_CI_TASK_ID}",
            "base_branch": "dev",
            "remote_sha": FAILED_CI_HEAD,
            "pr_url": f"https://github.com/{FAILED_CI_REPO}/pull/1282",
        },
    }
    task.update(overrides)
    return task


def _failed_ci_pr_facts(
    *,
    state="OPEN",
    head=FAILED_CI_HEAD,
    status_check_rollup=None,
    failed_check_name="product-e2e-gate",
    details_url="https://github.com/alfloop-dev/odayplus/actions/runs/34386285095/job/102583329286",
) -> dict:
    if status_check_rollup is not None:
        rollup = status_check_rollup
    else:
        rollup = [
            {
                "__typename": "CheckRun",
                "name": failed_check_name,
                "workflowName": "CI",
                "status": "COMPLETED",
                "conclusion": "FAILURE",
                "detailsUrl": details_url,
                "startedAt": "2026-09-09T23:00:00Z",
                "completedAt": "2026-09-09T23:05:00Z",
            },
            {
                "__typename": "CheckRun",
                "name": "product",
                "workflowName": "CI",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
                "startedAt": "2026-09-09T23:00:00Z",
                "completedAt": "2026-09-09T23:04:00Z",
            },
        ]
    return {
        "state": state,
        "headRefOid": head,
        "statusCheckRollup": rollup,
    }


def _run_ci_failure_recovery(
    task,
    *,
    ci=("OPEN", "failure"),
    ci_error=None,
    reads=None,
    status=None,
    busy_task_ids=frozenset(),
    commit_ok=True,
    timeline=None,
):
    import github_bus

    cfg = _base_test_config()
    if status is None:
        status = {
            "tasks": [task],
            "handoffs": [
                {
                    "task_id": task["id"],
                    "from": task.get("owner"),
                    "to": task.get("reviewer"),
                    "status": "pending",
                }
            ],
        }
    dispatch_engine._sync_supervisor_scope()

    head = task.get("review_submission", {}).get("remote_sha", FAILED_CI_HEAD)
    remaining = list(reads if reads is not None else [_failed_ci_pr_facts(head=head)])
    gh_calls: list[list[str]] = []
    events = timeline if timeline is not None else []

    def fake_run_gh(args, **_kwargs):
        gh_calls.append(list(args))
        events.append(("gh", args[2] if len(args) > 2 else ""))
        answer = remaining.pop(0) if len(remaining) > 1 else (remaining[0] if remaining else {})
        if answer is None:
            raise github_bus.GitHubBusOffline("gh could not reach api.github.com")
        stdout = answer if isinstance(answer, str) else json.dumps(answer)
        return subprocess.CompletedProcess(
            args=["gh", *args], returncode=0, stdout=stdout, stderr=""
        )

    ci_answers = list(ci) if isinstance(ci, list) else [ci]
    ci_errors = list(ci_error) if isinstance(ci_error, list) else [ci_error]

    def fake_ci(_task_id, *_args, **kwargs):
        events.append(("ci", kwargs.get("max_age_seconds")))
        error = ci_errors.pop(0) if len(ci_errors) > 1 else ci_errors[0]
        if error is not None:
            raise error
        return ci_answers.pop(0) if len(ci_answers) > 1 else ci_answers[0]

    logged: list[dict] = []
    record = lambda _cfg, event: logged.append(event)  # noqa: E731

    with (
        mock.patch.object(github_bus, "run_gh", side_effect=fake_run_gh),
        mock.patch.object(
            supervisor.runtime_ai_status, "task_pr_ci_status", side_effect=fake_ci
        ),
        mock.patch.object(
            supervisor, "commit_canonical_task_transition", return_value=commit_ok
        ),
        mock.patch.object(supervisor, "write_activity_log", side_effect=record),
        mock.patch.object(dispatch_engine, "write_activity_log", create=True, side_effect=record),
    ):
        changed = dispatch_engine.recover_failed_ci_review_prs(
            cfg, status, {"review"}, busy_task_ids=set(busy_task_ids)
        )
    return changed, gh_calls, logged, status


def test_ci_failure_review_is_returned_to_its_owner_with_actionable_diagnostics() -> None:
    """An unapproved review task with CI failure is returned to its owner with exact diagnostics."""
    task = _failed_ci_review_task()

    changed, gh_calls, logged, status = _run_ci_failure_recovery(task)

    assert changed is True
    assert task["status"] == "in_progress"
    assert task["owner"] == "Antigravity"
    assert task["reviewer"] == "Codex2"
    assert task["acceptance"] == ["the original acceptance must survive recovery"]
    assert task["review_reopen_count"] == 1
    assert task["review_churn_reassigned_at_count"] == 1
    assert task["human_continuation_approval_history"] == [
        {"approval_id": "hc-1", "status": "consumed"}
    ]
    assert task["review_submission"]["remote_sha"] == FAILED_CI_HEAD
    assert task[dispatch_engine.REVIEW_CI_FAILURE_RECOVERY_HEAD_FIELD] == FAILED_CI_HEAD
    assert "waiting_for" not in task
    assert status["handoffs"][0]["status"] == "done"
    assert "product-e2e-gate" in task["next"]
    assert "FAILURE" in task["next"]
    assert "actions/runs/34386285095/job/102583329286" in task["next"]
    assert "boundedly retry transient infra failures" in task["next"]
    assert FAILED_CI_HEAD[:8] in task["next"]
    assert "task_finalize.sh" in task["next"]

    requeued = [event for event in logged if event["type"] == "ci_repair_requeued"]
    assert len(requeued) == 1
    assert requeued[0]["entry"] == "review_ci_failure"
    assert requeued[0]["category"] == "control_plane_recovery"
    assert requeued[0]["approval_cleared"] is False
    recovered = [event for event in logged if event["type"] == "review_ci_failure_recovered"]
    assert len(recovered) == 1
    assert recovered[0]["pr_number"] == 1282
    assert recovered[0]["head"] == FAILED_CI_HEAD
    assert len(recovered[0]["failed_checks"]) == 1
    assert recovered[0]["failed_checks"][0]["name"] == "product-e2e-gate"
    assert recovered[0]["failed_checks"][0]["conclusion"] == "FAILURE"
    assert len(gh_calls) == 2
    for call in gh_calls:
        assert call[:3] == ["pr", "view", "1282"]
        assert call[call.index("--repo") + 1] == FAILED_CI_REPO


def test_ci_failure_recovery_declines_pending_in_progress_checks() -> None:
    """When a CI run is still in-progress (e.g. 1 failure + 1 in_progress), do not recover."""
    task = _failed_ci_review_task()
    rollup = [
        {
            "__typename": "CheckRun",
            "name": "product-e2e-gate",
            "workflowName": "CI",
            "status": "COMPLETED",
            "conclusion": "FAILURE",
            "detailsUrl": "https://github.com/alfloop-dev/odayplus/actions/runs/34386285095/job/102583329286",
        },
        {
            "__typename": "CheckRun",
            "name": "product-integration",
            "workflowName": "CI",
            "status": "IN_PROGRESS",
            "conclusion": None,
        },
    ]
    changed, _gh_calls, logged, _status = _run_ci_failure_recovery(
        task, reads=[_failed_ci_pr_facts(status_check_rollup=rollup)]
    )

    assert changed is False
    assert task["status"] == "review"
    assert dispatch_engine.REVIEW_CI_FAILURE_RECOVERY_HEAD_FIELD not in task
    assert logged == []


def test_ci_failure_recovery_declines_unknown_conclusion() -> None:
    """When a check has an unrecognized conclusion, treat as unverifiable reading."""
    task = _failed_ci_review_task()
    rollup = [
        {
            "__typename": "CheckRun",
            "name": "product-e2e-gate",
            "workflowName": "CI",
            "status": "COMPLETED",
            "conclusion": "SOME_UNKNOWN_CONCLUSION",
        }
    ]
    changed, _gh_calls, logged, _status = _run_ci_failure_recovery(
        task, reads=[_failed_ci_pr_facts(status_check_rollup=rollup)]
    )

    assert changed is False
    assert task["status"] == "review"
    assert logged == []


def test_ci_failure_recovery_declines_malformed_checks() -> None:
    """When rollup has malformed non-dict items, fail safe and decline recovery."""
    task = _failed_ci_review_task()
    rollup = ["not-a-dict-check-object"]
    changed, _gh_calls, logged, _status = _run_ci_failure_recovery(
        task, reads=[_failed_ci_pr_facts(status_check_rollup=rollup)]
    )

    assert changed is False
    assert task["status"] == "review"
    assert logged == []


def test_ci_failure_recovery_declines_human_waiting_gate() -> None:
    """When a review task is waiting for Human/Ops, recovery must not reopen or clear it."""
    task = _failed_ci_review_task(waiting_for="Human/Ops")
    changed, _gh_calls, logged, _status = _run_ci_failure_recovery(task)

    assert changed is False
    assert task["status"] == "review"
    assert task["waiting_for"] == "Human/Ops"
    assert logged == []


def test_ci_failure_recovery_declines_human_owner() -> None:
    """When a review task is owned by Human/Ops, recovery must not act on it."""
    task = _failed_ci_review_task(owner="Human/Ops")
    changed, _gh_calls, logged, _status = _run_ci_failure_recovery(task)

    assert changed is False
    assert task["status"] == "review"
    assert logged == []


@pytest.mark.parametrize(
    ("label", "kwargs"),
    [
        ("ci_pending", {"ci": ("OPEN", "pending")}),
        ("ci_success", {"ci": ("OPEN", "success")}),
        ("ci_none", {"ci": ("OPEN", "none")}),
        ("ci_unknown", {"ci": ("OPEN", "unknown")}),
        ("ci_unreadable_pr", {"ci": (None, "unknown")}),
        ("ci_probe_raises", {"ci_error": RuntimeError("gh unreachable")}),
        ("pr_closed_by_ci_probe", {"ci": ("CLOSED", "failure")}),
        ("pr_closed", {"reads": [_failed_ci_pr_facts(state="CLOSED", head=FAILED_CI_HEAD)]}),
        ("pr_merged", {"reads": [_failed_ci_pr_facts(state="MERGED", head=FAILED_CI_HEAD)]}),
        ("gh_offline", {"reads": [None]}),
        ("gh_malformed_json", {"reads": ["not json"]}),
        ("gh_empty_payload", {"reads": [{}]}),
        ("missing_head", {"reads": [_failed_ci_pr_facts(head=None)]}),
        ("missing_state", {"reads": [_failed_ci_pr_facts(state=None, head=FAILED_CI_HEAD)]}),
        ("head_drift", {"reads": [_failed_ci_pr_facts(head="b" * 40)]}),
        ("empty_rollup", {"reads": [_failed_ci_pr_facts(status_check_rollup=[])]}),
        (
            "all_green_rollup",
            {
                "reads": [
                    _failed_ci_pr_facts(
                        status_check_rollup=[
                            {
                                "__typename": "CheckRun",
                                "name": "product",
                                "status": "COMPLETED",
                                "conclusion": "SUCCESS",
                            }
                        ]
                    )
                ]
            },
        ),
    ],
)
def test_ci_failure_recovery_declines_every_unconfirmable_reading(
    label: str, kwargs: dict
) -> None:
    task = _failed_ci_review_task()
    changed, _gh_calls, logged, _status = _run_ci_failure_recovery(task, **kwargs)

    assert changed is False, label
    assert task["status"] == "review", label
    assert dispatch_engine.REVIEW_CI_FAILURE_RECOVERY_HEAD_FIELD not in task, label
    assert logged == [], label


def test_ci_failure_repeated_tick_on_same_failed_ci_head_is_idempotent() -> None:
    """Repeated ticks on the same head recover exactly once."""
    task = _failed_ci_review_task(
        **{dispatch_engine.REVIEW_CI_FAILURE_RECOVERY_HEAD_FIELD: FAILED_CI_HEAD}
    )

    changed, gh_calls, logged, _status = _run_ci_failure_recovery(task)

    assert changed is False
    assert gh_calls == []
    assert logged == []
    assert task["status"] == "review"


def test_ci_failure_resubmitted_new_head_can_recover_again() -> None:
    """When the owner pushes a new head and resubmits, a new failure can recover."""
    new_head = "e1f2a3b4" * 5
    task = _failed_ci_review_task(
        **{
            dispatch_engine.REVIEW_CI_FAILURE_RECOVERY_HEAD_FIELD: FAILED_CI_HEAD,
            "review_submission": {
                "pr_number": 1282,
                "branch": f"task/{FAILED_CI_TASK_ID}",
                "base_branch": "dev",
                "remote_sha": new_head,
                "pr_url": f"https://github.com/{FAILED_CI_REPO}/pull/1282",
            },
        }
    )

    changed, gh_calls, logged, _status = _run_ci_failure_recovery(
        task, reads=[_failed_ci_pr_facts(head=new_head)]
    )

    assert changed is True
    assert task["status"] == "in_progress"
    assert task[dispatch_engine.REVIEW_CI_FAILURE_RECOVERY_HEAD_FIELD] == new_head
    assert len(logged) == 2


def test_canonical_ci_repair_transition_allows_failed_ci_review_and_keeps_guard() -> None:
    """The canonical transition accepts allow_failed_ci_review only for valid reviews."""
    cfg = _base_test_config()

    def _requeue(task, **kwargs):
        status = {"tasks": [task], "handoffs": []}
        logged: list[dict] = []
        with (
            mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=True),
            mock.patch.object(
                supervisor, "write_activity_log", side_effect=lambda _c, e: logged.append(e)
            ),
        ):
            return supervisor.requeue_task_for_ci_repair(
                cfg, status, task, message="repair", **kwargs
            ), logged

    task = _failed_ci_review_task()
    changed, logged = _requeue(task, clear_approval=False, allow_failed_ci_review=True)
    assert changed is True
    assert task["status"] == "in_progress"
    assert logged[0]["entry"] == "review_ci_failure"
    assert logged[0]["category"] == "control_plane_recovery"


@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        ("frozen_approved_head", {"approved_head": FAILED_CI_HEAD}),
        ("queued_merge_route", {"merge_route": {"head": FAILED_CI_HEAD, "route": "queued"}}),
        ("unsubmitted_review", {"review_submission": None}),
        ("human_gate", {"task_class": "human_gate"}),
        ("human_waiting_gate", {"waiting_for": "Human/Ops"}),
        ("human_owner", {"owner": "Human/Ops"}),
        ("non_dispatchable", {"non_dispatchable": True}),
        ("wrong_status", {"status": "in_progress"}),
    ],
)
def test_canonical_ci_repair_transition_failed_ci_review_entry_still_refuses_invalid_states(
    label: str, overrides: dict
) -> None:
    cfg = _base_test_config()
    task = _failed_ci_review_task(**overrides)
    original_status = task["status"]
    status = {"tasks": [task], "handoffs": []}

    with (
        mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=True),
        mock.patch.object(supervisor, "write_activity_log"),
    ):
        changed = supervisor.requeue_task_for_ci_repair(
            cfg,
            status,
            task,
            message="repair",
            clear_approval=False,
            allow_failed_ci_review=True,
        )

    assert changed is False, label
    assert task["status"] == original_status, label


def test_ci_failure_dispatch_ready_tasks_recovers_failed_ci_review_without_reviewer_slot() -> None:
    """Review CI failure recovery runs during reconciliation without a reviewer slot."""
    import github_bus

    cfg = _base_test_config()
    task = _failed_ci_review_task()
    status = {
        "tasks": [task],
        "handoffs": [
            {"task_id": FAILED_CI_TASK_ID, "from": "Antigravity", "to": "Codex2", "status": "pending"}
        ],
    }
    state = {"workers": {}, "queue": {"events": {}}}
    queued_events: list[dict] = []

    def fake_run_gh(args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["gh", *args], returncode=0, stdout=json.dumps(_failed_ci_pr_facts(head=FAILED_CI_HEAD)), stderr=""
        )

    with (
        mock.patch.object(github_bus, "run_gh", side_effect=fake_run_gh),
        mock.patch.object(
            supervisor.runtime_ai_status,
            "task_pr_ci_status",
            return_value=("OPEN", "failure"),
        ),
        mock.patch.object(supervisor, "load_status", return_value=status),
        mock.patch.object(supervisor, "load_event_queue", return_value=[]),
        mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=True),
        mock.patch.object(supervisor, "write_activity_log"),
        mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
        mock.patch.object(
            dispatch_engine, "task_reality_reconcile_is_due", return_value=False
        ),
        mock.patch.object(
            supervisor,
            "queue_delivery_event",
            side_effect=lambda _c, evt: queued_events.append(evt) or True,
        ),
    ):
        changed = supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["antigravity7"])

    assert changed is True
    assert queued_events == []
    assert task["status"] == "in_progress"
    assert task["owner"] == "Antigravity"
    assert task[dispatch_engine.REVIEW_CI_FAILURE_RECOVERY_HEAD_FIELD] == FAILED_CI_HEAD
    assert status["handoffs"][0]["status"] == "done"


def test_ci_failure_recovery_then_success_reaches_review_dispatch() -> None:
    """When a task is resubmitted and CI succeeds, reviewer is dispatch eligible."""
    cfg = _base_test_config()
    fixed_head = "c3d4e5f6" * 5
    task = _failed_ci_review_task(
        review_submission={
            "pr_number": 1282,
            "branch": f"task/{FAILED_CI_TASK_ID}",
            "base_branch": "dev",
            "remote_sha": fixed_head,
            "pr_url": f"https://github.com/{FAILED_CI_REPO}/pull/1282",
        }
    )

    with (
        mock.patch.object(
            supervisor.runtime_ai_status,
            "resolve_task_sha",
            return_value=fixed_head,
        ),
        mock.patch.object(
            supervisor.runtime_ai_status,
            "task_pr_ci_status",
            return_value=("OPEN", "success"),
        ),
    ):
        eligible = supervisor.is_task_review_dispatch_eligible(
            cfg,
            task,
            "Codex2",
            review_statuses={"review"},
            finalize_statuses={"review_approved"},
        )

    assert eligible is True


def test_ci_failure_real_cas_race_aborts_and_does_not_dispatch_detached_in_progress(tmp_path: Path) -> None:
    """When concurrent writer approves a review task, CAS mismatch aborts and prevents detached dispatch."""
    import github_bus

    cfg = _base_test_config()
    cfg["agents"]["antigravity"] = {
        "id": "antigravity",
        "display_name": "Antigravity",
        "provider": "antigravity",
        "slot_id": "slot-antigravity-main",
    }
    cfg["agents"]["codex2"] = {
        "id": "codex2",
        "display_name": "Codex2",
        "provider": "codex",
        "slot_id": "slot-codex2",
    }
    status_file = tmp_path / "ai-status.json"
    cfg["paths"] = {
        "status_file": str(status_file),
        "activity_log": str(tmp_path / "ai-activity-log.jsonl"),
        "task_archive_dir": str(tmp_path / "ai-task-archive"),
    }

    task_id = "ODP-DATA-PLANE-DELETE-PROPAGATION-001"
    initial_task = _failed_ci_review_task(id=task_id, status="review")
    initial_status = {
        "_status_write_revision": "rev-1",
        "tasks": [initial_task],
        "handoffs": [{"task_id": task_id, "from": "Antigravity", "to": "Codex2", "status": "pending"}],
    }
    status_file.write_text(json.dumps(initial_status), encoding="utf-8")

    # Concurrent writer updates review task with revision rev-2 to disk
    concurrent_disk_task = _failed_ci_review_task(
        id=task_id,
        status="review",
    )
    concurrent_disk_task["next"] = "Review in progress by Codex2"
    disk_status = {
        "_status_write_revision": "rev-2",
        "tasks": [concurrent_disk_task],
        "handoffs": [{"task_id": task_id, "from": "Antigravity", "to": "Codex2", "status": "pending"}],
    }

    state = {"workers": {}, "queue": {"events": {}}}
    queued_events: list[dict] = []

    def fake_run_gh(args, **_kwargs):
        # When GitHub probe runs, disk status has already been advanced by concurrent writer
        status_file.write_text(json.dumps(disk_status), encoding="utf-8")
        return subprocess.CompletedProcess(
            args=["gh", *args], returncode=0, stdout=json.dumps(_failed_ci_pr_facts(head=FAILED_CI_HEAD)), stderr=""
        )

    with (
        mock.patch.object(github_bus, "run_gh", side_effect=fake_run_gh),
        mock.patch.object(
            supervisor.runtime_ai_status,
            "task_pr_ci_status",
            return_value=("OPEN", "failure"),
        ),
        mock.patch.object(
            supervisor.runtime_ai_status,
            "resolve_task_sha",
            return_value=FAILED_CI_HEAD,
        ),
        mock.patch.object(supervisor, "sync_status_pipeline", return_value=True),
        mock.patch.object(supervisor, "load_event_queue", return_value=[]),
        mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
        mock.patch.object(
            dispatch_engine, "task_reality_reconcile_is_due", return_value=False
        ),
        mock.patch.object(
            supervisor,
            "queue_delivery_event",
            side_effect=lambda _c, evt: queued_events.append(evt) or True,
        ),
    ):
        supervisor.dispatch_ready_tasks(
            cfg,
            state,
            agent_ids_override=["antigravity"],
        )

    # CAS mismatch should reject requeue_task_for_ci_repair, status reloads to disk_status (review),
    # and no owned_in_progress_dispatch is emitted!
    assert queued_events == []
    final_disk = json.loads(status_file.read_text(encoding="utf-8"))
    assert final_disk["tasks"][0]["status"] == "review"
    assert final_disk["_status_write_revision"] == "rev-2"


def test_ci_failure_full_lifecycle_dispatcher_to_resubmission_to_review(tmp_path: Path) -> None:
    """Full lifecycle: CI failure -> recovery to in_progress -> owner dispatch -> resubmission -> review dispatch."""
    import github_bus

    cfg = _base_test_config()
    cfg["agents"]["antigravity"] = {
        "id": "antigravity",
        "display_name": "Antigravity",
        "provider": "antigravity",
        "slot_id": "slot-antigravity-main",
    }
    cfg["agents"]["codex2"] = {
        "id": "codex2",
        "display_name": "Codex2",
        "provider": "codex",
        "slot_id": "slot-codex2",
    }
    status_file = tmp_path / "ai-status.json"
    cfg["paths"] = {
        "status_file": str(status_file),
        "activity_log": str(tmp_path / "ai-activity-log.jsonl"),
        "task_archive_dir": str(tmp_path / "ai-task-archive"),
    }

    task_id = "ODP-DATA-PLANE-DELETE-PROPAGATION-001"
    initial_task = _failed_ci_review_task(id=task_id, status="review", owner="Antigravity", reviewer="Codex2")
    initial_status = {
        "_status_write_revision": "rev-1",
        "tasks": [initial_task],
        "handoffs": [{"task_id": task_id, "from": "Antigravity", "to": "Codex2", "status": "pending"}],
    }
    status_file.write_text(json.dumps(initial_status), encoding="utf-8")

    state = {"workers": {}, "queue": {"events": {}}}
    queued_events: list[dict] = []

    def fake_run_gh(args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["gh", *args], returncode=0, stdout=json.dumps(_failed_ci_pr_facts(head=FAILED_CI_HEAD)), stderr=""
        )

    # Step 1: Tick 1 - reconciliation recovers task from review to in_progress
    with (
        mock.patch.object(github_bus, "run_gh", side_effect=fake_run_gh),
        mock.patch.object(
            supervisor.runtime_ai_status,
            "task_pr_ci_status",
            return_value=("OPEN", "failure"),
        ),
        mock.patch.object(supervisor, "sync_status_pipeline", return_value=True),
        mock.patch.object(supervisor, "load_event_queue", return_value=[]),
        mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
        mock.patch.object(
            dispatch_engine, "task_reality_reconcile_is_due", return_value=False
        ),
        mock.patch.object(
            supervisor,
            "queue_delivery_event",
            side_effect=lambda _c, evt: queued_events.append(evt) or True,
        ),
    ):
        changed = supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["codex2"])

    assert changed is True
    disk1 = json.loads(status_file.read_text(encoding="utf-8"))
    assert disk1["tasks"][0]["status"] == "in_progress"
    assert disk1["tasks"][0]["last_reopened_reason"] == "control_plane_recovery"
    assert disk1["handoffs"][0]["status"] == "done"

    # Step 2: Tick 2 - owner Antigravity is dispatched to repair CI
    queued_events.clear()
    with (
        mock.patch.object(supervisor, "sync_status_pipeline", return_value=True),
        mock.patch.object(supervisor, "load_event_queue", return_value=[]),
        mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
        mock.patch.object(
            dispatch_engine, "task_reality_reconcile_is_due", return_value=False
        ),
        mock.patch.object(
            supervisor,
            "queue_delivery_event",
            side_effect=lambda _c, evt: queued_events.append(evt) or True,
        ),
    ):
        changed2 = supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["antigravity"])

    assert changed2 is True
    assert len(queued_events) == 1
    assert queued_events[0]["reason"] == "owned_in_progress_dispatch"
    assert queued_events[0]["target_agent"] == "Antigravity"

    # Step 3: Owner repairs code and resubmits review at new SHA
    fixed_head = "f00d1234" * 5
    disk1["tasks"][0]["status"] = "review"
    disk1["tasks"][0]["review_submission"] = {
        "pr_number": 1282,
        "branch": f"task/{task_id}",
        "base_branch": "dev",
        "remote_sha": fixed_head,
        "pr_url": f"https://github.com/{FAILED_CI_REPO}/pull/1282",
    }
    disk1["_status_write_revision"] = "rev-3"
    status_file.write_text(json.dumps(disk1), encoding="utf-8")

    # Step 4: CI passes for new head -> reviewer Codex2 is dispatched
    queued_events.clear()
    with (
        mock.patch.object(
            supervisor.runtime_ai_status,
            "resolve_task_sha",
            return_value=fixed_head,
        ),
        mock.patch.object(
            supervisor.runtime_ai_status,
            "task_pr_ci_status",
            return_value=("OPEN", "success"),
        ),
        mock.patch.object(supervisor, "sync_status_pipeline", return_value=True),
        mock.patch.object(supervisor, "load_event_queue", return_value=[]),
        mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
        mock.patch.object(
            dispatch_engine, "task_reality_reconcile_is_due", return_value=False
        ),
        mock.patch.object(
            supervisor,
            "queue_delivery_event",
            side_effect=lambda _c, evt: queued_events.append(evt) or True,
        ),
    ):
        changed3 = supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["codex2"])

    assert changed3 is True
    assert len(queued_events) == 1
    assert queued_events[0]["reason"] == "review_ready_dispatch"
    assert queued_events[0]["target_agent"] == "Codex2"


def test_ci_failure_lane_adds_no_canonical_read_on_a_quiet_tick() -> None:
    """The CI-failure lane must cost no extra canonical read when it recovers nothing.

    Every reconciliation lane in `dispatch_ready_tasks` reloads only when its own
    step reports a change. The CI-failure lane briefly re-synced unconditionally,
    so every tick paid one extra `load_status` (3 reads on this shape instead of
    2). That surfaced as `StopIteration` in the callers that drive the dispatcher
    with a bounded `side_effect` sequence -- `ProcessQueueDispatchGuardTests` in
    test_supervisor.py supplies exactly two snapshots, which is the same
    invariant pinned numerically here.
    """
    cfg = _base_test_config()
    # Nothing is in `review`, so the CI lane inspects nothing and recovers
    # nothing; a quiet tick must not reload on its behalf.
    status = {
        "tasks": [
            {
                "id": "QUIET-001",
                "status": "in_progress",
                "owner": "Antigravity",
                "reviewer": "Codex2",
                "depends_on": [],
            }
        ],
        "handoffs": [],
    }
    state = {"workers": {}, "queue": {"events": {}}}

    with (
        mock.patch.object(
            supervisor, "load_status", return_value=status
        ) as load_status_mock,
        mock.patch.object(supervisor, "load_event_queue", return_value=[]),
        mock.patch.object(supervisor, "write_activity_log"),
        mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
        mock.patch.object(
            dispatch_engine, "task_reality_reconcile_is_due", return_value=False
        ),
        mock.patch.object(supervisor, "queue_delivery_event", return_value=True),
    ):
        supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["antigravity7"])

    assert load_status_mock.call_count == 2


def test_ci_failure_rejected_cas_rebuilds_indices_from_the_resynced_snapshot() -> None:
    """After a rejected CAS the dispatcher must select from the re-synced snapshot.

    `recover_failed_ci_review_prs` re-syncs `status` in place and still returns
    False when the canonical commit is rejected. The caller therefore has to
    rebuild `tasks`/`task_map` from that refreshed snapshot; otherwise candidate
    selection keeps scoring the detached pre-CAS objects. Here the refreshed
    snapshot carries a task the stale list never held, so dispatching it is
    possible only if the indices were genuinely rebuilt.
    """
    import github_bus

    cfg = _base_test_config()
    stale_task = _failed_ci_review_task()
    # What a concurrent writer leaves on disk once the CAS loses: the review is
    # gone and an unrelated task is ready for this agent.
    resynced = {
        "tasks": [
            {
                "id": "RESYNCED-001",
                "status": "todo",
                "owner": "Antigravity7",
                "reviewer": "Codex",
                "priority": "P2",
                "depends_on": [],
            }
        ],
        "handoffs": [],
    }
    disk = {
        "current": {
            "tasks": [stale_task],
            "handoffs": [
                {
                    "task_id": FAILED_CI_TASK_ID,
                    "from": "Antigravity",
                    "to": "Codex2",
                    "status": "pending",
                }
            ],
        }
    }
    state = {"workers": {}, "queue": {"events": {}}}
    queued_events: list[dict] = []

    def fake_run_gh(args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["gh", *args], returncode=0, stdout=json.dumps(_failed_ci_pr_facts(head=FAILED_CI_HEAD)), stderr=""
        )

    def reject_and_advance_disk(*_args, **_kwargs):
        """Lose the CAS, exactly as a concurrent canonical writer would."""
        disk["current"] = resynced
        return False

    with (
        mock.patch.object(github_bus, "run_gh", side_effect=fake_run_gh),
        mock.patch.object(
            supervisor.runtime_ai_status,
            "task_pr_ci_status",
            return_value=("OPEN", "failure"),
        ),
        mock.patch.object(
            supervisor, "load_status", side_effect=lambda *_a, **_k: disk["current"]
        ),
        mock.patch.object(supervisor, "load_event_queue", return_value=[]),
        mock.patch.object(
            supervisor,
            "commit_canonical_task_transition",
            side_effect=reject_and_advance_disk,
        ),
        mock.patch.object(supervisor, "write_activity_log"),
        mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
        mock.patch.object(
            dispatch_engine, "task_reality_reconcile_is_due", return_value=False
        ),
        mock.patch.object(
            supervisor,
            "queue_delivery_event",
            side_effect=lambda _c, evt: queued_events.append(evt) or True,
        ),
    ):
        supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["antigravity7"])

    # The rejected transition leaves the in-memory object detached and already
    # mutated -- precisely the hazard the index rebuild exists to contain.
    assert stale_task["status"] == "in_progress"
    # It must never reach dispatch on the strength of that uncommitted mutation...
    assert all(evt.get("task_id") != FAILED_CI_TASK_ID for evt in queued_events)
    # ...and selection must have run against the re-synced snapshot instead.
    assert [evt["task_id"] for evt in queued_events] == ["RESYNCED-001"]


# --- Preemption readiness ----------------------------------------------------
#
# `higher_priority_ready_task_exists` decides whether a running worker is killed
# so a better-ranked candidate can have its slot. Between 2026-09-06 11:01Z and
# 2026-09-07 04:03Z it killed one Codex2 review worker 286 consecutive times:
# its review fast path scored any review-status task naming the agent as
# reviewer as priority 0 on status and role alone, and the three P0 reviews that
# outranked it -- ODP-ROLE-PROVIDER-CODEX-LIVE-ROLLOUT-001 (`non_dispatchable`),
# ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002 and ODP-CODEX-ULTRA-DRIFT-REPAIR-001
# (both CI failure) -- could never be dispatched into the slot they emptied.
# These cover the shrunk incident, and the preemptions that must still happen.

PREEMPT_REVIEW_HEAD = "a1b2c3d4" * 5
PREEMPT_OTHER_HEAD = "f9e8d7c6" * 5


def _preemption_config(slot_count: int = 1) -> dict:
    """Codex2 with `slot_count` worker slots, reviewing an independent pool."""
    cfg = _base_test_config()
    cfg["schema"] = {
        "tasks_path": "tasks",
        "task_id_field": "id",
        "assignee_field": "owner",
        "reviewer_field": "reviewer",
    }
    slots = [f"codex2_slot_{index}" for index in range(1, slot_count + 1)]
    cfg["agents"]["codex2"] = {
        "id": "codex2",
        "display_name": "Codex2",
        "provider": "codex",
        "account_pool": "codex2",
        "worker_slots": slots,
    }
    for slot in slots:
        cfg["agents"][slot] = {
            "id": slot,
            "display_name": "Codex2",
            "provider": "codex",
            "dispatch_slot_for": "codex2",
        }
    return cfg


def _codex2_review_worker() -> dict:
    now_iso = supervisor.utc_now()
    return {
        "run_id": "run-codex2-review",
        "task_id": "P1-REVIEW-001",
        "provider": "codex",
        "agent_id": "codex2_slot_1",
        "logical_agent_id": "codex2",
        "status": "running",
        "queue_event_id": "evt-codex2-review",
        "pid": 4242,
        "last_event_at": now_iso,
        "last_heartbeat_at": now_iso,
        "request_snapshot": {"reason": REASON_REVIEW_READY},
    }


def _codex2_state(worker: dict) -> dict:
    return {
        "queue": {
            "events": {
                worker["queue_event_id"]: {
                    "status": "started",
                    "run_id": worker["run_id"],
                }
            }
        },
        "workers": {worker["run_id"]: worker},
    }


def _review_task(task_id: str, priority: str, *, head: str = PREEMPT_REVIEW_HEAD, **extra) -> dict:
    task = {
        "id": task_id,
        "status": "review",
        "owner": "Claude",
        "reviewer": "Codex2",
        "priority": priority,
        "depends_on": [],
        "review_submission": {"pr_number": 4100, "remote_sha": head},
    }
    task.update(extra)
    return task


def _readiness_probe(
    ci_by_task: dict[str, tuple[str, str]],
    sha_by_task: dict[str, str],
) -> tuple[mock.Mock, mock.Mock]:
    """Stand-ins for the two exact-head readers, recording who was asked."""

    def fake_sha(task_id, *_args, **_kwargs):
        return sha_by_task.get(str(task_id))

    def fake_ci(task_id, *_args, **_kwargs):
        return ci_by_task.get(str(task_id), ("OPEN", "unknown"))

    return mock.Mock(side_effect=fake_sha), mock.Mock(side_effect=fake_ci)


def test_dispatch_priority_reason_round_trips_every_lane() -> None:
    for reason in (
        REASON_REVIEW_READY,
        REASON_OWNED_FINALIZE,
        REASON_OWNED_IN_PROGRESS,
        REASON_OWNED_READY,
        REASON_HELPER_CLAIM,
    ):
        assert dispatch_priority_reason(dispatch_reason_priority(reason)) == reason
    assert dispatch_priority_reason(None) is None
    assert dispatch_priority_reason(99) is None
    # True == 1 in Python, and a boolean reaching here is a caller bug, not the
    # finalize lane.
    assert dispatch_priority_reason(True) is None


def test_undispatchable_p0_reviews_do_not_preempt_a_running_p1_review() -> None:
    """The 286-loop, shrunk: no P0 candidate here can take the slot it frees."""
    cfg = _preemption_config()
    worker = _codex2_review_worker()
    state = _codex2_state(worker)

    task_map = {
        "P1-REVIEW-001": _review_task("P1-REVIEW-001", "P1"),
        # ODP-ROLE-PROVIDER-CODEX-LIVE-ROLLOUT-001: parked for a human decision.
        "P0-NON-DISPATCHABLE": _review_task(
            "P0-NON-DISPATCHABLE", "P0", non_dispatchable=True
        ),
        # ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002 / ODP-CODEX-ULTRA-DRIFT-REPAIR-001.
        "P0-CI-FAILED": _review_task("P0-CI-FAILED", "P0"),
        "P0-CI-PENDING": _review_task("P0-CI-PENDING", "P0"),
        # `gh` could not answer: no evidence is not evidence of readiness.
        "P0-CI-UNKNOWN": _review_task("P0-CI-UNKNOWN", "P0"),
        # Reviewed head no longer on origin: the submission is stale.
        "P0-HEAD-DRIFTED": _review_task("P0-HEAD-DRIFTED", "P0"),
        # Already approved, so the reviewer lane is finished with it.
        "P0-ALREADY-APPROVED": _review_task(
            "P0-ALREADY-APPROVED", "P0", approved_head=PREEMPT_REVIEW_HEAD
        ),
    }
    ci_by_task = {
        "P0-CI-FAILED": ("OPEN", "failure"),
        "P0-CI-PENDING": ("OPEN", "pending"),
        "P0-CI-UNKNOWN": ("OPEN", "unknown"),
        "P0-HEAD-DRIFTED": ("OPEN", "success"),
        "P0-NON-DISPATCHABLE": ("OPEN", "success"),
        "P0-ALREADY-APPROVED": ("OPEN", "success"),
    }
    sha_by_task = dict.fromkeys(task_map, PREEMPT_REVIEW_HEAD)
    sha_by_task["P0-HEAD-DRIFTED"] = PREEMPT_OTHER_HEAD
    resolve_sha, pr_ci_status = _readiness_probe(ci_by_task, sha_by_task)

    with (
        mock.patch.object(supervisor, "load_event_queue", return_value=[]),
        mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", resolve_sha),
        mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", pr_ci_status),
    ):
        assert (
            supervisor.higher_priority_ready_task_exists(cfg, worker, task_map, state)
            is False
        )

    # A task the control plane never hands to a worker is refused before any
    # exact-head read: probing it would be a call with no reachable outcome.
    probed = {call.args[0] for call in resolve_sha.call_args_list}
    assert "P0-NON-DISPATCHABLE" not in probed
    assert "P1-REVIEW-001" not in probed


def test_dispatchable_p0_review_still_preempts_when_no_slot_is_free() -> None:
    cfg = _preemption_config()
    worker = _codex2_review_worker()
    state = _codex2_state(worker)

    task_map = {
        "P1-REVIEW-001": _review_task("P1-REVIEW-001", "P1"),
        "P0-READY": _review_task("P0-READY", "P0"),
    }
    resolve_sha, pr_ci_status = _readiness_probe(
        {"P0-READY": ("OPEN", "success")},
        dict.fromkeys(task_map, PREEMPT_REVIEW_HEAD),
    )

    with (
        mock.patch.object(supervisor, "load_event_queue", return_value=[]),
        mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", resolve_sha),
        mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", pr_ci_status),
    ):
        assert (
            supervisor.higher_priority_ready_task_exists(cfg, worker, task_map, state)
            is True
        )


def test_a_free_slot_is_used_before_a_running_worker_is_killed() -> None:
    """Capacity comes first: the same ready P0 goes to the idle slot."""
    cfg = _preemption_config(slot_count=2)
    worker = _codex2_review_worker()
    state = _codex2_state(worker)

    task_map = {
        "P1-REVIEW-001": _review_task("P1-REVIEW-001", "P1"),
        "P0-READY": _review_task("P0-READY", "P0"),
    }
    resolve_sha, pr_ci_status = _readiness_probe(
        {"P0-READY": ("OPEN", "success")},
        dict.fromkeys(task_map, PREEMPT_REVIEW_HEAD),
    )

    with (
        mock.patch.object(supervisor, "load_event_queue", return_value=[]),
        mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", resolve_sha),
        mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", pr_ci_status),
    ):
        assert (
            supervisor.higher_priority_ready_task_exists(cfg, worker, task_map, state)
            is False
        )


def test_role_policy_that_excludes_the_reviewer_lane_is_not_a_preemption_reason() -> None:
    """A P0 review this agent may not hold cannot be why its worker dies."""
    cfg = _preemption_config()
    cfg["ready_dispatcher"]["role_provider_policy"] = {
        "enabled": True,
        "rules": [{"roles": ["reviewer"], "providers": ["claude"]}],
    }
    worker = _codex2_review_worker()
    state = _codex2_state(worker)

    task_map = {
        "P1-REVIEW-001": _review_task("P1-REVIEW-001", "P1"),
        "P0-READY": _review_task("P0-READY", "P0"),
    }
    resolve_sha, pr_ci_status = _readiness_probe(
        {"P0-READY": ("OPEN", "success")},
        dict.fromkeys(task_map, PREEMPT_REVIEW_HEAD),
    )

    with (
        mock.patch.object(supervisor, "load_event_queue", return_value=[]),
        mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", resolve_sha),
        mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", pr_ci_status),
    ):
        assert (
            supervisor.higher_priority_ready_task_exists(cfg, worker, task_map, state)
            is False
        )


def test_p0_owned_work_preempts_only_once_its_dependencies_are_done() -> None:
    """Blocked and dependency-incomplete P0 work is not executable work."""
    cfg = _preemption_config()
    now_iso = supervisor.utc_now()
    worker = {
        "run_id": "run-codex2-owned",
        "task_id": "P1-OWNED-001",
        "provider": "codex",
        "agent_id": "codex2_slot_1",
        "logical_agent_id": "codex2",
        "status": "running",
        "queue_event_id": "evt-codex2-owned",
        "pid": 4243,
        "last_event_at": now_iso,
        "last_heartbeat_at": now_iso,
        "request_snapshot": {"reason": REASON_OWNED_IN_PROGRESS},
    }
    state = _codex2_state(worker)

    current = {
        "id": "P1-OWNED-001",
        "status": "in_progress",
        "owner": "Codex2",
        "reviewer": "Claude",
        "priority": "P1",
        "depends_on": [],
    }
    blocker = {
        "id": "P0-BLOCKER",
        "status": "in_progress",
        "owner": "Claude",
        "reviewer": "Codex2",
        "priority": "P0",
        "depends_on": [],
    }
    waiting = {
        "id": "P0-WAITING",
        "status": "todo",
        "owner": "Codex2",
        "reviewer": "Claude",
        "priority": "P0",
        "depends_on": ["P0-BLOCKER"],
    }
    task_map = {t["id"]: t for t in (current, blocker, waiting)}

    with mock.patch.object(supervisor, "load_event_queue", return_value=[]):
        assert (
            supervisor.higher_priority_ready_task_exists(cfg, worker, task_map, state)
            is False
        )

        # Positive control: the same P0 becomes a preemption reason the moment
        # its dependency is done and it can actually be started.
        blocker["status"] = "done"
        assert (
            supervisor.higher_priority_ready_task_exists(cfg, worker, task_map, state)
            is True
        )


# --- Diagnostic CAS resilience & review dispatch starvation tests ------------

def test_diagnostic_cas_two_pending_two_green_revision_changing_dispatch(tmp_path: Path) -> None:
    """When 2 pending CI tasks precede 2 green CI tasks, real revision-changing
    syncs during advisory writes must reload snapshot and dispatch both green tasks.
    """
    cfg = _base_test_config()
    cfg["paths"] = {
        "status_file": str(tmp_path / "ai-status.json"),
        "activity_log": str(tmp_path / "activity.jsonl"),
        "event_queue": str(tmp_path / "events.jsonl"),
    }
    status_file = Path(cfg["paths"]["status_file"])

    pending_task_1 = {
        "id": "PENDING-001",
        "status": "review",
        "owner": "Claude",
        "reviewer": "Antigravity7",
        "repository": "alfloop-dev/odayplus",
        "priority": "P1",
        "depends_on": [],
        "review_submission": {
            "pr_number": 101,
            "branch": "task/PENDING-001",
            "base_branch": "dev",
            "remote_sha": "aaaa1111" * 5,
            "pr_url": "https://github.com/alfloop-dev/odayplus/pull/101",
        },
    }
    pending_task_2 = {
        "id": "PENDING-002",
        "status": "review",
        "owner": "Claude",
        "reviewer": "Antigravity7",
        "repository": "alfloop-dev/odayplus",
        "priority": "P1",
        "depends_on": [],
        "review_submission": {
            "pr_number": 102,
            "branch": "task/PENDING-002",
            "base_branch": "dev",
            "remote_sha": "aaaa2222" * 5,
            "pr_url": "https://github.com/alfloop-dev/odayplus/pull/102",
        },
    }
    green_task_1 = {
        "id": "GREEN-001",
        "status": "review",
        "owner": "Claude",
        "reviewer": "Antigravity7",
        "repository": "alfloop-dev/odayplus",
        "priority": "P1",
        "depends_on": [],
        "review_submission": {
            "pr_number": 201,
            "branch": "task/GREEN-001",
            "base_branch": "dev",
            "remote_sha": "bbbb1111" * 5,
            "pr_url": "https://github.com/alfloop-dev/odayplus/pull/201",
        },
    }
    green_task_2 = {
        "id": "GREEN-002",
        "status": "review",
        "owner": "Claude",
        "reviewer": "Antigravity7",
        "repository": "alfloop-dev/odayplus",
        "priority": "P1",
        "depends_on": [],
        "review_submission": {
            "pr_number": 202,
            "branch": "task/GREEN-002",
            "base_branch": "dev",
            "remote_sha": "bbbb2222" * 5,
            "pr_url": "https://github.com/alfloop-dev/odayplus/pull/202",
        },
    }

    initial_revision = uuid.uuid4().hex
    status_file.write_text(
        json.dumps({
            "_status_write_revision": initial_revision,
            "tasks": [pending_task_1, pending_task_2, green_task_1, green_task_2],
            "handoffs": [],
        }),
        encoding="utf-8",
    )

    state = {"workers": {}, "queue": {"events": {}}}
    queued_events: list[dict] = []
    sync_revisions: list[str] = []

    def fake_sync_pipeline(config: dict) -> bool:
        disk_data = json.loads(status_file.read_text(encoding="utf-8"))
        new_rev = uuid.uuid4().hex
        disk_data["_status_write_revision"] = new_rev
        disk_data["synced"] = True
        status_file.write_text(json.dumps(disk_data), encoding="utf-8")
        sync_revisions.append(new_rev)
        return True

    def fake_resolve_task_sha(task_id: str, **_kwargs) -> str:
        if task_id == "PENDING-001":
            return "aaaa1111" * 5
        if task_id == "PENDING-002":
            return "aaaa2222" * 5
        if task_id == "GREEN-001":
            return "bbbb1111" * 5
        if task_id == "GREEN-002":
            return "bbbb2222" * 5
        return ""

    def fake_pr_ci_status(task_id: str, **_kwargs) -> tuple[str, str]:
        if task_id in {"PENDING-001", "PENDING-002"}:
            return "OPEN", "pending"
        if task_id in {"GREEN-001", "GREEN-002"}:
            return "OPEN", "success"
        return "OPEN", "unknown"

    with (
        mock.patch.object(supervisor, "sync_status_pipeline", side_effect=fake_sync_pipeline),
        mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", side_effect=fake_resolve_task_sha),
        mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", side_effect=fake_pr_ci_status),
        mock.patch.object(supervisor, "load_event_queue", return_value=[]),
        mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
        mock.patch.object(supervisor, "agent_dispatch_capacity", return_value=10),
        mock.patch.object(dispatch_engine, "task_reality_reconcile_is_due", return_value=False),
        mock.patch.object(
            supervisor,
            "queue_delivery_event",
            side_effect=lambda _c, evt: queued_events.append(evt) or True,
        ),
    ):
        changed = supervisor.dispatch_ready_tasks(
            cfg,
            state,
            agent_ids_override=["antigravity7"],
            max_dispatches_override=10,
        )

    assert changed is True
    assert len(sync_revisions) >= 2
    dispatched_ids = [evt.get("task_id") for evt in queued_events]
    assert "GREEN-001" in dispatched_ids
    assert "GREEN-002" in dispatched_ids
    assert "PENDING-001" not in dispatched_ids
    assert "PENDING-002" not in dispatched_ids
    assert len(dispatched_ids) == 2


def test_diagnostic_cas_canonical_sync_advancing_disk_revision_reloads_status_for_subsequent_writes(tmp_path: Path) -> None:
    """After a transition commit and canonical sync, in-memory status must reload the new disk revision.

    If commit_canonical_task_transition does not reload status from disk after
    sync_status_pipeline, the in-memory status retains the pre-sync revision, causing
    subsequent writes in the same tick to fail with stale_status_write_rejected.
    """
    status_file = tmp_path / "ai-status.json"
    activity_file = tmp_path / "ai-activity.jsonl"
    cfg = {
        "paths": {
            "status_file": str(status_file),
            "activity_log": str(activity_file),
        }
    }

    initial_revision = uuid.uuid4().hex
    task_1 = {"id": "TASK-1", "status": "todo", "priority": "P2"}
    task_2 = {"id": "TASK-2", "status": "todo", "priority": "P2"}
    status = {
        "_status_write_revision": initial_revision,
        "tasks": [task_1, task_2],
    }
    status_file.write_text(json.dumps(status), encoding="utf-8")

    sync_revision = uuid.uuid4().hex

    def fake_sync_pipeline(config: dict) -> bool:
        disk_data = json.loads(status_file.read_text(encoding="utf-8"))
        disk_data["_status_write_revision"] = sync_revision
        disk_data["synced"] = True
        status_file.write_text(json.dumps(disk_data), encoding="utf-8")
        return True

    with mock.patch("status_transition.sync_status_pipeline", side_effect=fake_sync_pipeline):
        # 1. First commit: transitions TASK-1 to in_progress
        task_1["status"] = "in_progress"
        committed_1 = status_transition.commit_canonical_task_transition(cfg, status)
        assert committed_1 is True
        assert status.get("_status_write_revision") == sync_revision
        assert status.get("synced") is True

        # 2. Second commit in the same process tick: transitions TASK-2 to in_progress
        status["tasks"][1]["status"] = "in_progress"
        committed_2 = status_transition.commit_canonical_task_transition(cfg, status)
        assert committed_2 is True
        on_disk = json.loads(status_file.read_text(encoding="utf-8"))
        assert on_disk["tasks"][0]["status"] == "in_progress"
        assert on_disk["tasks"][1]["status"] == "in_progress"


def test_diagnostic_cas_external_writer_race_preserves_data_and_dispatches_ready_tasks(tmp_path: Path) -> None:
    """When an advisory write experiences a CAS race against an external writer,
    the dispatcher resyncs from disk, preserves external updates, and dispatches newly ready tasks."""
    status_file = tmp_path / "ai-status.json"
    activity_file = tmp_path / "ai-activity.jsonl"
    event_queue = tmp_path / "events.jsonl"
    cfg = _base_test_config()
    cfg["paths"]["status_file"] = str(status_file)
    cfg["paths"]["activity_log"] = str(activity_file)
    cfg["paths"]["event_queue"] = str(event_queue)

    initial_revision = uuid.uuid4().hex
    pending_task = {
        "id": "PENDING-RACE-001",
        "status": "review",
        "owner": "Claude",
        "reviewer": "Antigravity7",
        "repository": "alfloop-dev/odayplus",
        "priority": "P1",
        "depends_on": [],
        "review_submission": {
            "pr_number": 301,
            "branch": "task/PENDING-RACE-001",
            "base_branch": "dev",
            "remote_sha": "cccc1111" * 5,
            "pr_url": "https://github.com/alfloop-dev/odayplus/pull/301",
        },
    }

    # Disk starts with only pending_task
    status_file.write_text(
        json.dumps({
            "_status_write_revision": initial_revision,
            "tasks": [pending_task],
            "handoffs": [],
        }),
        encoding="utf-8",
    )

    state = {"workers": {}, "queue": {"events": {}}}
    queued_events: list[dict] = []
    raced = False
    real_write = supervisor.write_status_snapshot_if_current

    def racing_write_snapshot(config, status_snapshot):
        nonlocal raced
        if not raced:
            raced = True
            # External writer races in and adds CONCURRENT-READY-001 with external_marker
            disk = json.loads(status_file.read_text(encoding="utf-8"))
            disk["_status_write_revision"] = uuid.uuid4().hex
            disk["external_marker"] = "preserve-external-data"
            disk["tasks"].append({
                "id": "CONCURRENT-READY-001",
                "status": "review",
                "owner": "Claude",
                "reviewer": "Antigravity7",
                "repository": "alfloop-dev/odayplus",
                "priority": "P0",
                "depends_on": [],
                "review_submission": {
                    "pr_number": 302,
                    "branch": "task/CONCURRENT-READY-001",
                    "base_branch": "dev",
                    "remote_sha": "dddd2222" * 5,
                    "pr_url": "https://github.com/alfloop-dev/odayplus/pull/302",
                },
            })
            status_file.write_text(json.dumps(disk), encoding="utf-8")
        return real_write(config, status_snapshot)

    def fake_resolve_task_sha(task_id: str, **_kwargs) -> str:
        if task_id == "PENDING-RACE-001":
            return "cccc1111" * 5
        if task_id == "CONCURRENT-READY-001":
            return "dddd2222" * 5
        return ""

    def fake_pr_ci_status(task_id: str, **_kwargs) -> tuple[str, str]:
        if task_id == "PENDING-RACE-001":
            return "OPEN", "pending"
        if task_id == "CONCURRENT-READY-001":
            return "OPEN", "success"
        return "OPEN", "unknown"

    with (
        mock.patch.object(supervisor, "write_status_snapshot_if_current", side_effect=racing_write_snapshot),
        mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", side_effect=fake_resolve_task_sha),
        mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", side_effect=fake_pr_ci_status),
        mock.patch.object(supervisor, "load_event_queue", return_value=[]),
        mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
        mock.patch.object(supervisor, "agent_dispatch_capacity", return_value=10),
        mock.patch.object(dispatch_engine, "task_reality_reconcile_is_due", return_value=False),
        mock.patch.object(
            supervisor,
            "queue_delivery_event",
            side_effect=lambda _c, evt: queued_events.append(evt) or True,
        ),
    ):
        changed = supervisor.dispatch_ready_tasks(
            cfg,
            state,
            agent_ids_override=["antigravity7"],
        )

    assert changed is True
    assert raced is True
    disk_data = json.loads(status_file.read_text(encoding="utf-8"))
    assert disk_data.get("external_marker") == "preserve-external-data"
    dispatched_ids = [evt.get("task_id") for evt in queued_events]
    assert "CONCURRENT-READY-001" in dispatched_ids
    assert "PENDING-RACE-001" not in dispatched_ids


def test_diagnostic_cas_lifecycle_sync_does_not_enqueue_reassigned_reviewer(tmp_path: Path) -> None:
    """When a lifecycle write syncs and changes a remaining candidate's reviewer on disk,
    the candidate must be evaluated from the fresh snapshot and not dispatched to the old reviewer."""
    cfg = _base_test_config()
    cfg["paths"] = {
        "status_file": str(tmp_path / "canonical.json"),
        "activity_log": str(tmp_path / "activity.jsonl"),
        "event_queue": str(tmp_path / "events.jsonl"),
    }
    cfg["ready_dispatcher"]["helper_execution_lease"]["enabled"] = False
    status_file = Path(cfg["paths"]["status_file"])
    trigger = {
        "id": "TRIGGER",
        "owner": "Antigravity7",
        "reviewer": "Codex",
        "status": "review_approved",
        "approved_head": "a" * 40,
        "depends_on": [],
    }
    victim = {
        "id": "VICTIM",
        "owner": "Claude",
        "reviewer": "Antigravity7",
        "status": "review",
        "depends_on": [],
        "review_submission": {
            "remote_sha": "c" * 40,
            "pr_number": 2,
            "branch": "task/VICTIM",
            "base_branch": "dev",
            "pr_url": "https://example.invalid/2",
        },
    }
    status_file.write_text(
        json.dumps({
            "_status_write_revision": "initial",
            "tasks": [trigger, victim],
            "handoffs": [],
        }),
        encoding="utf-8",
    )
    syncs = []
    events = []

    def sync_and_external_update(config):
        disk = json.loads(status_file.read_text(encoding="utf-8"))
        disk["_status_write_revision"] = uuid.uuid4().hex
        disk["tasks"][1]["reviewer"] = "Codex"
        disk["external_marker"] = "preserve-me"
        status_file.write_text(json.dumps(disk), encoding="utf-8")
        syncs.append(disk["_status_write_revision"])
        return True

    with ExitStack() as stack:
        for name in (
            "repair_open_task_metadata",
            "repair_unsubmitted_review_tasks",
            "normalize_task_assignment_integrity",
            "normalize_mainline_task_assignment",
            "reassign_tasks_after_review_churn",
        ):
            stack.enter_context(mock.patch.object(supervisor, name, return_value=False))
        for name in (
            "advance_approved_prs_to_merge",
            "reassign_unavailable_reviewers",
            "recover_conflicted_review_prs",
            "recover_failed_ci_review_prs",
            "task_reality_reconcile_is_due",
        ):
            stack.enter_context(mock.patch.object(dispatch_engine, name, return_value=False))
        stack.enter_context(mock.patch.object(supervisor, "sync_status_pipeline", side_effect=sync_and_external_update))
        stack.enter_context(mock.patch.object(supervisor, "load_event_queue", return_value=[]))
        stack.enter_context(mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None))
        stack.enter_context(mock.patch.object(supervisor, "agent_dispatch_capacity", return_value=2))
        stack.enter_context(mock.patch.object(supervisor.runtime_ai_status, "resolve_task_checkout_sha", return_value="b" * 40))
        stack.enter_context(mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value="c" * 40))
        stack.enter_context(mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", return_value=("OPEN", "success")))
        stack.enter_context(mock.patch.object(supervisor, "queue_delivery_event", side_effect=lambda config, event: events.append(event) or True))
        supervisor.dispatch_ready_tasks(
            cfg,
            {"workers": {}, "queue": {"events": {}}},
            agent_ids_override=["antigravity7"],
            max_dispatches_override=2,
        )

    disk = json.loads(status_file.read_text(encoding="utf-8"))
    assert len(syncs) == 1
    assert disk["tasks"][0]["status"] == "review"
    assert disk["tasks"][1]["reviewer"] == "Codex"
    assert disk["external_marker"] == "preserve-me"
    assert not events, f"Enqueued stale reviewer despite canonical reassignment: {events}"


def test_diagnostic_cas_helper_claims_persist_leases_for_all_queued_helpers(tmp_path: Path) -> None:
    """When multiple helpers are leased in the same tick, every queued event must have its lease persisted on disk."""
    cfg = _base_test_config()
    cfg["paths"] = {
        "status_file": str(tmp_path / "ai-status.json"),
        "activity_log": str(tmp_path / "activity.jsonl"),
        "event_queue": str(tmp_path / "events.jsonl"),
    }
    canonical = Path(cfg["paths"]["status_file"])
    canonical.write_text(
        json.dumps({
            "_status_write_revision": "initial-revision",
            "tasks": [
                {"id": task_id, "status": "todo", "priority": "P2", "owner": "Claude", "reviewer": "Codex", "depends_on": []}
                for task_id in ["HELPER-A", "HELPER-B"]
            ],
            "handoffs": [],
        }),
        encoding="utf-8",
    )
    events = []
    sync_receipts = []

    def revision_changing_sync(_cfg):
        board = json.loads(canonical.read_text(encoding="utf-8"))
        before = board["_status_write_revision"]
        board["_status_write_revision"] = uuid.uuid4().hex
        canonical.write_text(json.dumps(board), encoding="utf-8")
        sync_receipts.append((before, board["_status_write_revision"]))
        return True

    with ExitStack() as patches:
        for name in [
            "repair_open_task_metadata",
            "repair_unsubmitted_review_tasks",
            "reassign_tasks_after_review_churn",
            "normalize_task_assignment_integrity",
            "normalize_mainline_task_assignment",
            "reassign_unavailable_reviewers",
        ]:
            patches.enter_context(mock.patch.object(supervisor, name, return_value=False))
        patches.enter_context(mock.patch.object(dispatch_engine, "task_reality_reconcile_is_due", return_value=False))
        patches.enter_context(mock.patch.object(supervisor, "load_event_queue", return_value=[]))
        patches.enter_context(mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None))
        patches.enter_context(mock.patch.object(supervisor, "agent_dispatch_capacity", return_value=2))
        patches.enter_context(mock.patch.object(supervisor, "sync_status_pipeline", side_effect=revision_changing_sync))
        patches.enter_context(mock.patch.object(supervisor, "queue_delivery_event", side_effect=lambda _c, event: events.append(event) or True))
        supervisor.dispatch_ready_tasks(
            cfg,
            {"workers": {}, "queue": {"events": {}}},
            agent_ids_override=["antigravity7"],
            max_dispatches_override=2,
        )

    board = json.loads(canonical.read_text(encoding="utf-8"))
    disk_tasks = {task["id"]: task for task in board["tasks"]}
    assert sync_receipts and all(before != after for before, after in sync_receipts)
    assert events, "Positive control: at least the first eligible helper must be dispatched"
    for event in events:
        assert event["reason"] == "helper_claim_dispatch"
        assert disk_tasks[event["task_id"]].get("helper_execution_lease") == event["task"].get("helper_execution_lease"), (
            f"Queued {event['task_id']} without the advertised lease in canonical status"
        )


def test_diagnostic_cas_retry_exhaustion_does_not_enqueue_stale_reviewer(tmp_path: Path) -> None:
    """When advisory diagnostic writes exhaust bounded retries under repeated CAS rejections,
    the dispatcher must discard stale candidate state rather than dispatching outdated assignments."""
    cfg = _base_test_config()
    cfg["paths"] = {
        "status_file": str(tmp_path / "ai-status.json"),
        "activity_log": str(tmp_path / "activity.jsonl"),
        "event_queue": str(tmp_path / "events.jsonl"),
    }
    status_file = Path(cfg["paths"]["status_file"])
    sha = "a" * 40
    tasks = [
        {
            "id": task_id,
            "status": "review",
            "owner": "Claude",
            "reviewer": "Antigravity7",
            "priority": "P1",
            "depends_on": [],
            "repository": "alfloop-dev/odayplus",
            "review_submission": {
                "remote_sha": sha,
                "pr_number": number,
                "branch": "task/" + task_id,
                "base_branch": "dev",
                "pr_url": "https://github.com/alfloop-dev/odayplus/pull/" + str(number),
            },
        }
        for task_id, number in [("GREEN-RETRY", 901), ("PENDING-RETRY", 902)]
    ]
    status_file.write_text(
        json.dumps({
            "_status_write_revision": uuid.uuid4().hex,
            "tasks": tasks,
            "handoffs": [],
        }),
        encoding="utf-8",
    )
    state = {"workers": {}, "queue": {"events": {}}}
    queued = []
    cas_results = []
    real_write = supervisor.write_status_snapshot_if_current

    def racing_write(config, status):
        disk = json.loads(status_file.read_text(encoding="utf-8"))
        disk["_status_write_revision"] = uuid.uuid4().hex
        disk["external_writer_counter"] = len(cas_results) + 1
        if len(cas_results) == 7:
            disk["tasks"][0]["reviewer"] = "Codex"
        status_file.write_text(json.dumps(disk), encoding="utf-8")
        result = real_write(config, status)
        cas_results.append(result)
        return result

    with ExitStack() as stack:
        for mod in (supervisor, dispatch_engine):
            for name in (
                "repair_open_task_metadata",
                "repair_unsubmitted_review_tasks",
                "reassign_tasks_after_review_churn",
                "normalize_task_assignment_integrity",
                "normalize_mainline_task_assignment",
                "reassign_unavailable_reviewers",
                "advance_approved_prs_to_merge",
                "recover_conflicted_review_prs",
                "recover_failed_ci_review_prs",
                "report_narrowed_helper_claimable_statuses",
                "task_reality_reconcile_is_due",
            ):
                if hasattr(mod, name):
                    stack.enter_context(mock.patch.object(mod, name, return_value=False))
        stack.enter_context(mock.patch.object(supervisor, "load_event_queue", return_value=[]))
        stack.enter_context(mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None))
        stack.enter_context(mock.patch.object(supervisor, "agent_dispatch_capacity", return_value=10))
        stack.enter_context(mock.patch.object(supervisor, "write_status_snapshot_if_current", side_effect=racing_write))
        stack.enter_context(mock.patch.object(supervisor, "queue_delivery_event", side_effect=lambda c, e: queued.append(e) or True))
        stack.enter_context(mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value=sha))
        stack.enter_context(mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", side_effect=lambda task_id, **kw: ("OPEN", "success" if task_id == "GREEN-RETRY" else "pending")))
        supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["antigravity7"], max_dispatches_override=10)

    disk = json.loads(status_file.read_text(encoding="utf-8"))
    stale_events = [e for e in queued if e["task_id"] == "GREEN-RETRY" and e["target_agent"] == "Antigravity7"]
    assert cas_results == [False] * 8
    assert disk["tasks"][0]["reviewer"] == "Codex"
    assert disk["external_writer_counter"] == 8
    assert not stale_events


@pytest.mark.parametrize("external_mutation", ["unchanged", "competing_lease", "blocked", "dependency"])
def test_diagnostic_cas_helper_candidate_revalidated_after_real_sync(tmp_path: Path, external_mutation: str) -> None:
    """Helper candidates must be revalidated after canonical sync and never overwrite other agent's live lease."""
    cfg = _base_test_config()
    cfg["agents"]["gemini"] = {
        "id": "gemini", "display_name": "Gemini", "provider": "gemini", "slot_id": "slot-gemini",
    }
    cfg["providers"]["gemini"] = {"delivery_mode": "gemini"}
    cfg["paths"] = {
        "status_file": str(tmp_path / "ai-status.json"),
        "activity_log": str(tmp_path / "activity.jsonl"),
        "event_queue": str(tmp_path / "events.jsonl"),
    }
    canonical = Path(cfg["paths"]["status_file"])
    canonical.write_text(json.dumps({
        "_status_write_revision": "initial-revision",
        "tasks": [
            {"id": task_id, "status": "todo", "priority": "P2", "owner": "Claude", "reviewer": "Gemini", "depends_on": []}
            for task_id in ["HELPER-A", "HELPER-B"]
        ],
        "handoffs": [],
    }), encoding="utf-8")
    events = []
    sync_receipts = []
    now = datetime.now(UTC)
    competing_lease = {
        "claimed_by": "Codex",
        "original_owner": "Claude",
        "claimed_at": now.isoformat().replace("+00:00", "Z"),
        "lease_expires_at": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "reason": "external-writer-claim",
        "generation": 17,
    }

    def revision_changing_sync(_cfg):
        board = json.loads(canonical.read_text(encoding="utf-8"))
        before = board["_status_write_revision"]
        if not sync_receipts:
            victim = next(task for task in board["tasks"] if task["id"] == "HELPER-B")
            if external_mutation == "competing_lease":
                victim["helper_execution_lease"] = dict(competing_lease)
            elif external_mutation == "blocked":
                victim["status"] = "blocked"
            elif external_mutation == "dependency":
                victim["depends_on"] = ["UNFINISHED-DEPENDENCY"]
            board["external_marker"] = "must-survive"
        board["_status_write_revision"] = uuid.uuid4().hex
        canonical.write_text(json.dumps(board), encoding="utf-8")
        sync_receipts.append((before, board["_status_write_revision"]))
        return True

    with ExitStack() as patches:
        for name in [
            "repair_open_task_metadata", "repair_unsubmitted_review_tasks",
            "reassign_tasks_after_review_churn", "normalize_task_assignment_integrity",
            "normalize_mainline_task_assignment", "reassign_unavailable_reviewers",
        ]:
            patches.enter_context(mock.patch.object(supervisor, name, return_value=False))
        patches.enter_context(mock.patch.object(dispatch_engine, "task_reality_reconcile_is_due", return_value=False))
        patches.enter_context(mock.patch.object(supervisor, "load_event_queue", return_value=[]))
        patches.enter_context(mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None))
        patches.enter_context(mock.patch.object(supervisor, "agent_dispatch_capacity", return_value=2))
        patches.enter_context(mock.patch.object(supervisor, "sync_status_pipeline", side_effect=revision_changing_sync))
        patches.enter_context(mock.patch.object(supervisor, "queue_delivery_event", side_effect=lambda _c, event: events.append(event) or True))
        supervisor.dispatch_ready_tasks(
            cfg, {"workers": {}, "queue": {"events": {}}},
            agent_ids_override=["antigravity7"], max_dispatches_override=2,
        )

    board = json.loads(canonical.read_text(encoding="utf-8"))
    task_map = {task["id"]: task for task in board["tasks"]}
    queued_ids = [event["task_id"] for event in events]
    assert sync_receipts and all(before != after for before, after in sync_receipts)
    assert board["external_marker"] == "must-survive"
    assert "HELPER-A" in queued_ids, "Positive control: the first eligible helper must dispatch"
    if external_mutation == "unchanged":
        assert queued_ids == ["HELPER-A", "HELPER-B"]
        for event in events:
            assert task_map[event["task_id"]]["helper_execution_lease"] == event["task"]["helper_execution_lease"]
    else:
        if external_mutation == "competing_lease":
            assert task_map["HELPER-B"].get("helper_execution_lease") == competing_lease, (
                "Canonical refresh must not authorize overwriting another agent's live lease",
                task_map["HELPER-B"], queued_ids,
            )
        assert "HELPER-B" not in queued_ids, (
            "Candidate became ineligible during the first helper's canonical sync",
            external_mutation, task_map["HELPER-B"], queued_ids,
        )


@pytest.mark.parametrize("failure_mode", ["ok", "sync_failure", "reload_failure"])
def test_diagnostic_cas_advisory_resync_failure_never_queues_superseded_reviewer(tmp_path: Path, failure_mode: str) -> None:
    """Advisory sync and reload failures must suppress dispatch rather than enqueuing superseded assignments from stale snapshot."""
    cfg = _base_test_config()
    cfg["paths"] = {
        "status_file": str(tmp_path / "ai-status.json"),
        "activity_log": str(tmp_path / "activity.jsonl"),
        "event_queue": str(tmp_path / "events.jsonl"),
    }
    cfg["ready_dispatcher"]["helper_execution_lease"]["enabled"] = False
    canonical = Path(cfg["paths"]["status_file"])
    head = "a" * 40
    tasks = [
        {
            "id": task_id,
            "status": "review",
            "owner": "Claude",
            "reviewer": "Antigravity7",
            "priority": "P1",
            "repository": "alfloop-dev/odayplus",
            "depends_on": [],
            "review_submission": {
                "pr_number": pr_number,
                "branch": "task/" + task_id,
                "base_branch": "dev",
                "remote_sha": head,
                "pr_url": f"https://github.com/alfloop-dev/odayplus/pull/{pr_number}",
            },
        }
        for task_id, pr_number in [("PENDING-RESYNC", 9901), ("GREEN-RESYNC", 9902)]
    ]
    canonical.write_text(json.dumps({
        "_status_write_revision": "initial-revision",
        "tasks": tasks,
        "handoffs": [],
    }), encoding="utf-8")

    real_load = supervisor.load_status
    sync_receipts = []
    failed_loads = []
    queued_events = []

    def revision_changing_sync(config):
        board = json.loads(canonical.read_text(encoding="utf-8"))
        before = board["_status_write_revision"]
        assert before != "initial-revision"
        board["_status_write_revision"] = uuid.uuid4().hex
        board["tasks"][1]["reviewer"] = "Codex"
        board["external_marker"] = "preserve-this-update"
        canonical.write_text(json.dumps(board), encoding="utf-8")
        sync_receipts.append((before, board["_status_write_revision"]))
        return failure_mode != "sync_failure"

    def controlled_load(config):
        if failure_mode == "reload_failure" and sync_receipts and not failed_loads:
            failed_loads.append("post-sync read failed once")
            raise OSError("review repro: transient canonical read failure")
        return real_load(config)

    def ci_status(task_id, **kwargs):
        return "OPEN", "pending" if task_id == "PENDING-RESYNC" else "success"

    with ExitStack() as patches:
        for name in (
            "repair_open_task_metadata",
            "repair_unsubmitted_review_tasks",
            "reassign_tasks_after_review_churn",
            "normalize_task_assignment_integrity",
            "normalize_mainline_task_assignment",
            "reassign_unavailable_reviewers",
        ):
            patches.enter_context(mock.patch.object(supervisor, name, return_value=False))
        for name in ("recover_conflicted_review_prs", "recover_failed_ci_review_prs"):
            patches.enter_context(mock.patch.object(dispatch_engine, name, return_value=False))
        patches.enter_context(mock.patch.object(dispatch_engine, "task_reality_reconcile_is_due", return_value=False))
        patches.enter_context(mock.patch.object(supervisor, "load_status", side_effect=controlled_load))
        patches.enter_context(mock.patch.object(supervisor, "sync_status_pipeline", side_effect=revision_changing_sync))
        patches.enter_context(mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value=head))
        patches.enter_context(mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", side_effect=ci_status))
        patches.enter_context(mock.patch.object(supervisor, "load_event_queue", return_value=[]))
        patches.enter_context(mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None))
        patches.enter_context(mock.patch.object(supervisor, "agent_dispatch_capacity", return_value=2))
        patches.enter_context(mock.patch.object(supervisor, "queue_delivery_event", side_effect=lambda _cfg, event: queued_events.append(event) or True))
        supervisor.dispatch_ready_tasks(
            cfg,
            {"workers": {}, "queue": {"events": {}}},
            agent_ids_override=["antigravity7"],
            max_dispatches_override=2,
        )

    board = json.loads(canonical.read_text(encoding="utf-8"))
    assert sync_receipts and all(before != after for before, after in sync_receipts)
    assert bool(failed_loads) == (failure_mode == "reload_failure")
    assert board["external_marker"] == "preserve-this-update"
    assert board["tasks"][1]["reviewer"] == "Codex"
    assert not queued_events, (
        f"{failure_mode}: queued stale events after canonical reviewer changed to Codex: "
        f"{[(event['task_id'], event['target_agent'], event['task'].get('reviewer')) for event in queued_events]}"
    )


@pytest.mark.parametrize("mutation", ["unchanged", "blocked", "dependency", "reviewer"])
def test_diagnostic_cas_current_helper_revalidated_after_own_sync(tmp_path: Path, mutation: str) -> None:
    """The helper candidate whose own lease sync changes eligibility must be fully revalidated."""
    cfg = _base_test_config()
    cfg["agents"]["gemini"] = {"id": "gemini", "display_name": "Gemini", "provider": "gemini", "slot_id": "slot-gemini"}
    cfg["providers"]["gemini"] = {"delivery_mode": "gemini"}
    cfg["paths"] = {
        "status_file": str(tmp_path / "ai-status.json"),
        "activity_log": str(tmp_path / "activity.jsonl"),
        "event_queue": str(tmp_path / "events.jsonl"),
    }
    canonical = Path(cfg["paths"]["status_file"])
    canonical.write_text(json.dumps({
        "_status_write_revision": "initial",
        "tasks": [
            {"id": task_id, "status": "todo", "priority": "P2", "owner": "Claude", "reviewer": "Gemini", "depends_on": []}
            for task_id in ["HELPER-A", "HELPER-B"]
        ],
        "handoffs": [],
    }), encoding="utf-8")
    events, syncs = [], []

    def sync(_cfg):
        board = json.loads(canonical.read_text(encoding="utf-8"))
        before = board["_status_write_revision"]
        assert before != "initial", "Actual CAS must precede sync"
        if not syncs:
            victim = board["tasks"][0]
            assert victim["helper_execution_lease"]["claimed_by"] == "Antigravity7"
            if mutation == "blocked":
                victim["status"] = "blocked"
            elif mutation == "dependency":
                victim["depends_on"] = ["UNFINISHED-DEPENDENCY"]
            elif mutation == "reviewer":
                victim["reviewer"] = "Antigravity7"
            board["external_marker"] = "must-survive"
        board["_status_write_revision"] = uuid.uuid4().hex
        canonical.write_text(json.dumps(board), encoding="utf-8")
        syncs.append((before, board["_status_write_revision"]))
        return True

    with ExitStack() as patches:
        for name in [
            "repair_open_task_metadata", "repair_unsubmitted_review_tasks",
            "reassign_tasks_after_review_churn", "normalize_task_assignment_integrity",
            "normalize_mainline_task_assignment", "reassign_unavailable_reviewers",
        ]:
            patches.enter_context(mock.patch.object(supervisor, name, return_value=False))
        patches.enter_context(mock.patch.object(dispatch_engine, "task_reality_reconcile_is_due", return_value=False))
        patches.enter_context(mock.patch.object(supervisor, "load_event_queue", return_value=[]))
        patches.enter_context(mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None))
        patches.enter_context(mock.patch.object(supervisor, "agent_dispatch_capacity", return_value=2))
        patches.enter_context(mock.patch.object(supervisor, "sync_status_pipeline", side_effect=sync))
        patches.enter_context(mock.patch.object(supervisor, "queue_delivery_event", side_effect=lambda c, e: events.append(e) or True))
        supervisor.dispatch_ready_tasks(
            cfg, {"workers": {}, "queue": {"events": {}}},
            agent_ids_override=["antigravity7"], max_dispatches_override=2,
        )

        board = json.loads(canonical.read_text(encoding="utf-8"))
        task_map = {t["id"]: t for t in board["tasks"]}
        helper_a_events = [e for e in events if e["task_id"] == "HELPER-A"]
        guard_messages = [supervisor.stale_dispatch_skip_message(cfg, e, task_map) for e in helper_a_events]

    assert syncs and all(a != b for a, b in syncs)
    assert board["external_marker"] == "must-survive"
    queued_ids = [e["task_id"] for e in events]
    if mutation == "unchanged":
        assert queued_ids == ["HELPER-A", "HELPER-B"]
        for event in events:
            assert task_map[event["task_id"]]["helper_execution_lease"] == event["task"]["helper_execution_lease"]
    else:
        assert not helper_a_events, (mutation, task_map["HELPER-A"], queued_ids, guard_messages)


@pytest.mark.parametrize("mode", ["unchanged", "mutation_readable", "mutation_unreadable"])
def test_diagnostic_cas_cross_agent_after_advisory_snapshot_refresh(tmp_path: Path, mode: str) -> None:
    """Unconfirmed snapshot freshness must not reach subsequent agent iterations."""
    cfg = _base_test_config()
    cfg["ready_dispatcher"]["helper_execution_lease"]["enabled"] = False
    cfg["agents"]["gemini"] = {
        "id": "gemini", "display_name": "Gemini", "provider": "gemini", "slot_id": "slot-gemini",
    }
    cfg["providers"]["gemini"] = {"delivery_mode": "gemini"}
    cfg["paths"] = {
        "status_file": str(tmp_path / "canonical.json"),
        "activity_log": str(tmp_path / "activity.jsonl"),
        "event_queue": str(tmp_path / "events.jsonl"),
    }
    canonical = Path(cfg["paths"]["status_file"])
    head = "a" * 40
    tasks = []
    for task_id, reviewer, pr_number in [
        ("PENDING-FIRST-AGENT", "Antigravity7", 9901),
        ("GREEN-SECOND-AGENT", "Codex", 9902),
    ]:
        tasks.append({
            "id": task_id, "status": "review", "owner": "Claude", "reviewer": reviewer,
            "priority": "P1", "repository": "alfloop-dev/odayplus", "depends_on": [],
            "review_submission": {
                "pr_number": pr_number, "branch": "task/" + task_id, "base_branch": "dev",
                "remote_sha": head, "pr_url": f"https://github.com/alfloop-dev/odayplus/pull/{pr_number}",
            },
        })
    canonical.write_text(json.dumps({"_status_write_revision": "initial", "tasks": tasks, "handoffs": []}), encoding="utf-8")
    real_load = supervisor.load_status
    syncs = []
    failed_reads = []
    events = []

    def sync_with_external_writer(_cfg):
        board = json.loads(canonical.read_text(encoding="utf-8"))
        before = board["_status_write_revision"]
        assert before != "initial"
        board["_status_write_revision"] = uuid.uuid4().hex
        board["external_marker"] = "preserved"
        if mode != "unchanged":
            board["tasks"][1]["reviewer"] = "Gemini"
        canonical.write_text(json.dumps(board), encoding="utf-8")
        syncs.append((before, board["_status_write_revision"]))
        return True

    def controlled_load(config):
        if syncs and mode == "mutation_unreadable":
            failed_reads.append("canonical read failed after sync")
            raise OSError("review probe: persistent post-sync read failure")
        return real_load(config)

    with ExitStack() as patches:
        for name in [
            "repair_open_task_metadata", "repair_unsubmitted_review_tasks", "reassign_tasks_after_review_churn",
            "normalize_task_assignment_integrity", "normalize_mainline_task_assignment", "reassign_unavailable_reviewers",
        ]:
            patches.enter_context(mock.patch.object(supervisor, name, return_value=False))
        for name in ["recover_conflicted_review_prs", "recover_failed_ci_review_prs", "task_reality_reconcile_is_due"]:
            patches.enter_context(mock.patch.object(dispatch_engine, name, return_value=False))
        patches.enter_context(mock.patch.object(supervisor, "load_status", side_effect=controlled_load))
        patches.enter_context(mock.patch.object(supervisor, "sync_status_pipeline", side_effect=sync_with_external_writer))
        patches.enter_context(mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value=head))
        patches.enter_context(mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", side_effect=lambda task_id, **kwargs: ("OPEN", "pending" if task_id == "PENDING-FIRST-AGENT" else "success")))
        patches.enter_context(mock.patch.object(supervisor, "load_event_queue", return_value=[]))
        patches.enter_context(mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None))
        patches.enter_context(mock.patch.object(supervisor, "agent_dispatch_capacity", return_value=2))
        patches.enter_context(mock.patch.object(supervisor, "queue_delivery_event", side_effect=lambda config, event: events.append(event) or True))
        supervisor.dispatch_ready_tasks(
            cfg, {"workers": {}, "queue": {"events": {}}},
            agent_ids_override=["antigravity7", "codex"], max_dispatches_override=2,
        )

    disk = json.loads(canonical.read_text(encoding="utf-8"))
    assert syncs and all(before != after for before, after in syncs)
    assert disk["external_marker"] == "preserved"
    event_records = [(event["task_id"], event["target_agent"], event["task"]["reviewer"]) for event in events]
    if mode == "unchanged":
        assert event_records == [("GREEN-SECOND-AGENT", "Codex", "codex")]
    else:
        assert disk["tasks"][1]["reviewer"] == "Gemini"
        if mode == "mutation_unreadable":
            assert len(failed_reads) >= 2
        assert not event_records, f"Second agent queued stale reviewer despite failed freshness: {event_records}"


@pytest.mark.parametrize("budget", ["available", "zero", "consumed"])
def test_exhausted_helper_budget_does_not_starve_exact_head_green_review(tmp_path: Path, budget: str) -> None:
    """Helper budget exhaustion must leave reviewer capacity usable."""
    cfg = _base_test_config()
    cfg["paths"] = {
        "status_file": str(tmp_path / "ai-status.json"),
        "activity_log": str(tmp_path / "activity.jsonl"),
        "event_queue": str(tmp_path / "events.jsonl"),
    }
    helper_settings = cfg["ready_dispatcher"]["helper_execution_lease"]
    helper_settings["require_owner_saturated"] = False
    helper_settings["max_claims_per_tick"] = 0 if budget == "zero" else 1
    sha = "a" * 40
    status = {
        "_status_write_revision": "initial",
        "tasks": [
            {"id": "P0-HELPER", "priority": "P0", "status": "todo", "owner": "Claude", "reviewer": "Codex", "depends_on": []},
            {"id": "P1-GREEN-REVIEW", "priority": "P1", "status": "review", "owner": "Claude", "reviewer": "Antigravity7", "depends_on": [],
             "review_submission": {"remote_sha": sha, "pr_number": 999, "branch": "task/P1-GREEN-REVIEW", "base_branch": "dev"},
             "repository": "alfloop-dev/odayplus"},
        ],
        "handoffs": [],
    }
    canonical = Path(cfg["paths"]["status_file"])
    canonical.write_text(json.dumps(status))
    events: list[dict] = []
    state = {"workers": {}, "queue": {"events": {}}, "ready_dispatcher": {"helper_dispatches_this_tick": 1 if budget == "consumed" else 0}}
    with ExitStack() as patches:
        for name in [
            "repair_open_task_metadata", "repair_unsubmitted_review_tasks", "reassign_tasks_after_review_churn",
            "normalize_task_assignment_integrity", "normalize_mainline_task_assignment", "reassign_unavailable_reviewers",
        ]:
            patches.enter_context(mock.patch.object(supervisor, name, return_value=False))
        for name in ["advance_approved_prs_to_merge", "recover_conflicted_review_prs", "recover_failed_ci_review_prs"]:
            patches.enter_context(mock.patch.object(dispatch_engine, name, return_value=False))
        patches.enter_context(mock.patch.object(dispatch_engine, "task_reality_reconcile_is_due", return_value=False))
        patches.enter_context(mock.patch.object(supervisor, "load_event_queue", return_value=[]))
        patches.enter_context(mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None))
        patches.enter_context(mock.patch.object(supervisor, "agent_dispatch_capacity", return_value=2))
        patches.enter_context(mock.patch.object(supervisor, "sync_status_pipeline", return_value=True))
        patches.enter_context(mock.patch.object(supervisor.runtime_ai_status, "resolve_task_sha", return_value=sha))
        patches.enter_context(mock.patch.object(supervisor.runtime_ai_status, "task_pr_ci_status", return_value=("OPEN", "success")))
        patches.enter_context(mock.patch.object(supervisor, "queue_delivery_event", side_effect=lambda c, e: events.append(e) or True))
        supervisor.dispatch_ready_tasks(cfg, state, agent_ids_override=["antigravity7"], max_dispatches_override=2)
    assert [e["task_id"] for e in events if e["reason"] == "review_ready_dispatch"] == ["P1-GREEN-REVIEW"], (budget, events)
    if budget == "available":
        assert [e["task_id"] for e in events] == ["P0-HELPER", "P1-GREEN-REVIEW"]
    else:
        assert all(e["reason"] != "helper_claim_dispatch" for e in events)
