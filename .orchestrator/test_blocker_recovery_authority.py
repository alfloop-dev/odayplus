"""Canonical blocker authority regressions through the production dispatcher.

All board writes are confined to tmp_path; network/provider/process I/O is
mocked. Recovery eligibility, persistence, canonical loads and CAS are real.
"""
from __future__ import annotations

import json
import sys
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from pathlib import Path
from unittest import mock

import pytest

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR.parent / "scripts"))
import ai_status
import dispatch_engine
import supervisor


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setenv("PANTHEON_STATUS_ROOT", str(tmp_path))
    monkeypatch.setenv("ORCH_STATUS_ROOT", str(tmp_path))
    config = {
        "paths": {key: str(tmp_path / key) for key in ("status_file", "activity_log", "event_queue")},
        "schema": {"tasks_path": "tasks", "task_id_field": "id", "assignee_field": "owner", "reviewer_field": "reviewer"},
        "agents": {
            "codex": {"id": "codex", "display_name": "Codex", "provider": "codex", "account_pool": "owner"},
            "claude": {"id": "claude", "display_name": "Claude", "provider": "claude", "account_pool": "reviewer"},
        },
        "providers": {"codex": {}, "claude": {}},
        "ready_dispatcher": {"helper_execution_lease": {"enabled": False}},
    }
    task = {"id": "AUTHORITY-001", "status": "blocked", "owner": "Codex", "reviewer": "Claude", "depends_on": [], "next": "dataset still missing"}
    status = {"tasks": [task], "blockers": [], "handoffs": [], "_status_write_revision": "initial"}
    path = Path(config["paths"]["status_file"])
    Path(config["paths"]["event_queue"]).write_text("")
    return config, status, path


def save(path, status):
    path.write_text(json.dumps(status), encoding="utf-8")


def hard_blocker(**updates):
    return {"task_id": "AUTHORITY-001", "owner": "Codex", "waiting_for": "Claude", "status": "open", "message": "External-data/dataset gate: A1 raw/masked and A2/A3 live still missing", **updates}


def ordinary_note(status, path, message):
    """Use the real note mutation, not a hand-built task.next approximation."""
    with (
        mock.patch.object(ai_status, "current_actor_validated", return_value="Codex"),
        mock.patch.object(ai_status, "LOG_FILE", path.parent / "note.jsonl"),
    ):
        ai_status.command_note(status, ["AUTHORITY-001", message])
    save(path, status)


@contextmanager
def dispatch_boundary(config):
    """Leave recovery and its full snapshot/persistence wiring unmocked."""
    events = []
    with ExitStack() as stack:
        for name in (
            "repair_open_task_metadata", "repair_unsubmitted_review_tasks",
            "reassign_tasks_after_review_churn", "normalize_task_assignment_integrity",
        ):
            stack.enter_context(mock.patch.object(supervisor, name, return_value=False))
        for name in ("reassign_unavailable_reviewers", "advance_approved_prs_to_merge", "recover_conflicted_review_prs", "recover_failed_ci_review_prs", "task_reality_reconcile_is_due"):
            stack.enter_context(mock.patch.object(dispatch_engine, name, return_value=False))
        stack.enter_context(mock.patch.object(supervisor, "sync_status_pipeline", return_value=True))
        stack.enter_context(mock.patch.object(supervisor, "provider_runtime_config_block_reason", return_value=None))
        stack.enter_context(mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None))
        stack.enter_context(mock.patch.object(supervisor, "resolve_task_progress_head", return_value=None))
        stack.enter_context(mock.patch.object(supervisor, "queue_delivery_event", side_effect=lambda _config, event: events.append(event) or True))
        audit = stack.enter_context(mock.patch.object(supervisor, "write_activity_log"))
        launch = stack.enter_context(mock.patch.object(supervisor, "start_worker_for_request", side_effect=AssertionError("no real launch")))
        yield events, audit, launch


def dispatch(config):
    return supervisor.dispatch_ready_tasks(config, {"workers": {}, "queue": {"events": {}}}, {}, agent_ids_override=["codex"])


@pytest.mark.parametrize("message", [
    "Ordinary note: status corrected; provider handoff is stale",
    "owned_paths notification: provider routing and handoff unchanged",
    "handoff metadata correction only",
    "stale provider notification only",
])
def test_note_cannot_release_canonical_external_blocker(board, message):
    config, status, path = board
    status["blockers"] = [hard_blocker()]
    ordinary_note(status, path, message)
    before = deepcopy(status)
    with dispatch_boundary(config) as (events, audit, launch):
        dispatch(config)
    assert json.loads(path.read_text()) == before
    assert events == []
    launch.assert_not_called()
    assert not any("No unresolved dependency" in str(call) for call in audit.call_args_list)
