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


@pytest.mark.parametrize("completed_dependency", [False, True])
@pytest.mark.parametrize("blocker_message", [
    "External-data/dataset. A1 raw/masked and A2/A3 live still missing; provider handoff pending",
    "External-data/dataset gate: provider handoff pending; A1 raw/masked and A2/A3 live still missing",
    "Human approval pending; provider retry still blocked",
    "Human/Ops gate: provider handoff pending; manual approval still missing",
    "Unclassified business gate: provider handoff pending; input still missing",
    "dependency gate: UPSTREAM-DATASET-001; unrelated business gate: provider handoff pending",
    "waiting for dependencies: UPSTREAM-DATASET-001; awaiting human approval",
    "waiting for dependencies: UPSTREAM-DATASET-001; provider failed; requires operator sign-off",
])
def test_mixed_gate_prose_is_not_erased_as_a_path(board, completed_dependency, blocker_message):
    config, status, path = board
    if completed_dependency:
        status["tasks"][0]["depends_on"] = ["UPSTREAM-DATASET-001"]
        status["tasks"].append({"id": "UPSTREAM-DATASET-001", "status": "done", "depends_on": []})
    status["blockers"] = [hard_blocker(message=blocker_message)]
    ordinary_note(status, path, "Ordinary note: status corrected; provider handoff is stale")
    before = deepcopy(status)
    with dispatch_boundary(config) as (events, audit, launch):
        dispatch(config)
    assert json.loads(path.read_text()) == before
    assert events == []
    launch.assert_not_called()
    assert not any("No unresolved dependency" in str(call) for call in audit.call_args_list)


@pytest.mark.parametrize("gate", [
    {"requires_human_approval": True}, {"human_required_roles": ["ops"]},
    {"credentials_gate": True}, {"credential_gate": True},
    {"deployment_gate": True}, {"production_gate": True},
    {"external_data_gate": True}, {"human_gate": {"status": "pending"}},
    {"gate_status": "pending_human_signoff"}, {"waiting_for": "Human/Ops"},
    {"non_dispatchable": True}, {"task_class": "human_gate"},
    {"blocked_reason": "dataset still missing"},
    {"blocked_reason": "External-data/dataset gate: stale provider handoff pending"},
    {"blocked_reason": "External-data/dataset. A1 raw/masked and A2/A3 live still missing; provider handoff pending"},
    {"blocked_reason": "Human approval pending; provider retry still blocked"},
])
def test_structured_gate_survives_note(board, gate):
    config, status, path = board
    status["tasks"][0].update(gate)
    ordinary_note(status, path, "provider handoff stale notification")
    with dispatch_boundary(config) as (events, audit, launch):
        dispatch(config)
    assert json.loads(path.read_text()) == status
    assert events == []
    launch.assert_not_called()


@pytest.mark.parametrize("blocker", [
    hard_blocker(message="provider failed; requires operator sign-off"),
    hard_blocker(message="Human approval pending; provider retry still blocked"),
    hard_blocker(message="approval required after stale handoff"),
    hard_blocker(message="unrecorded business input still missing"),
    hard_blocker(message="provider error", waiting_for="Human/Ops"),
    hard_blocker(message="provider error", external_data_gate=True),
    hard_blocker(message="provider handoff notification", kind="external_data"),
    hard_blocker(message="stale worktree notification", kind="human_gate"),
    hard_blocker(message="provider notification", kind="cross_repo_delivery"),
    hard_blocker(message="stale provider", status="unknown"),
])
def test_completed_dependency_cannot_release_an_independent_blocker(board, blocker):
    config, status, path = board
    status["tasks"][0]["depends_on"] = ["UPSTREAM-DATASET-001"]
    status["tasks"].append({"id": "UPSTREAM-DATASET-001", "status": "done", "depends_on": []})
    status["blockers"] = [hard_blocker(kind="dependency", message="waiting for dependencies: UPSTREAM-DATASET-001"), blocker]
    ordinary_note(status, path, "provider stale handoff notification")
    with dispatch_boundary(config) as (events, audit, launch):
        dispatch(config)
    assert json.loads(path.read_text()) == status
    assert events == []
    launch.assert_not_called()


@pytest.mark.parametrize("scenario", ["routing", "resolved", "dependency", "unrelated", "references"])
def test_released_scheduler_owned_gate_still_recovers_and_enqueues(board, scenario):
    config, status, path = board
    task = status["tasks"][0]
    task["next"] = "stale provider/worktree failure; retry dispatch"
    if scenario == "routing":
        status["blockers"] = [hard_blocker(message="provider quota/worktree failure")]
    elif scenario == "resolved":
        status["blockers"] = [hard_blocker(status="resolved", resolved_at="2026-10-02T00:00:00Z")]
    elif scenario == "references":
        task.update(depends_on=["UPSTREAM-DATASET-001"], next="stale provider failure in scripts/deployment.py; retry dispatch")
        status["tasks"].append({"id": "UPSTREAM-DATASET-001", "status": "done", "depends_on": []})
        status["blockers"] = [hard_blocker(message="provider failure in scripts/deployment.py; refs=docs/dataset.json; `docs/dataset`; /tmp/dataset; ./docs/dataset; `external_data_gate`; UPSTREAM-DATASET-001")]
    elif scenario == "dependency":
        task.update(depends_on=["UPSTREAM-DATASET-001"], next="waiting for dependencies: UPSTREAM-DATASET-001")
        status["tasks"].append({"id": "UPSTREAM-DATASET-001", "status": "done", "depends_on": []})
        status["blockers"] = [hard_blocker(kind="dependency", message=task["next"])]
    else:
        status["blockers"] = [hard_blocker(task_id="OTHER-TASK")]
    save(path, status)
    with dispatch_boundary(config) as (events, audit, launch):
        dispatch(config)
    latest = json.loads(path.read_text())
    assert latest["tasks"][0]["status"] == "todo"
    assert len(events) == 1
    assert events[0]["task_id"] == "AUTHORITY-001"
    launch.assert_not_called()
    for blocker in latest["blockers"]:
        assert blocker["status"] == ("open" if scenario == "unrelated" else "resolved")
    if scenario == "resolved":
        assert latest["blockers"] == status["blockers"]  # historical resolution untouched


@pytest.mark.parametrize("missing", ["blockers", "revision"])
def test_incomplete_canonical_snapshot_fails_closed(board, missing):
    config, status, path = board
    status.pop("blockers" if missing == "blockers" else "_status_write_revision")
    ordinary_note(status, path, "provider handoff stale notification")
    with dispatch_boundary(config) as (events, audit, launch):
        dispatch(config)
    assert json.loads(path.read_text()) == status
    assert events == []


def test_task_only_map_is_not_authority_even_without_dependencies(board):
    config, status, path = board
    task = status["tasks"][0]
    task["next"] = "stale provider handoff"
    assert not supervisor.blocked_task_auto_recovery_eligible(config, task, {task["id"]: task})
    assert not supervisor.normalize_mainline_task_assignment(config, task, {task["id"]: task})


def test_task_must_match_the_authorizing_snapshot(board):
    config, status, path = board
    task = deepcopy(status["tasks"][0])
    task["next"] = "stale provider failure"
    assert not supervisor.blocked_task_auto_recovery_eligible(config, task, status_snapshot=status)


def test_snapshot_dependency_truth_overrides_optimistic_task_map(board):
    config, status, path = board
    task = status["tasks"][0]
    task.update(depends_on=["UPSTREAM-001"], next="stale provider")
    upstream = {"id": "UPSTREAM-001", "status": "blocked", "depends_on": []}
    status["tasks"].append(upstream)
    optimistic = {task["id"]: task, upstream["id"]: {**upstream, "status": "done"}}
    assert not supervisor.blocked_task_auto_recovery_eligible(config, task, optimistic, status_snapshot=status)
    save(path, status)
    with dispatch_boundary(config) as (events, audit, launch):
        dispatch(config)
    assert json.loads(path.read_text()) == status
    assert events == []


@pytest.mark.parametrize("insertion_point", ["before_persist", "before_cas"])
def test_parallel_human_blocker_is_never_resolved_or_dispatched(board, insertion_point):
    config, status, path = board
    status["tasks"][0]["next"] = "stale provider failure; retry dispatch"
    save(path, status)
    real_persist = supervisor.persist_task_reassignment
    real_cas = supervisor.write_status_snapshot_if_current

    def add_human_blocker():
        latest = json.loads(path.read_text())
        latest["blockers"].append(hard_blocker(waiting_for="Human/Ops", message="manual approval"))
        latest["_status_write_revision"] = "parallel-canonical-write"
        save(path, latest)

    def persist_after_add(*args, **kwargs):
        add_human_blocker()
        return real_persist(*args, **kwargs)

    def cas_after_add(*args, **kwargs):
        add_human_blocker()
        return real_cas(*args, **kwargs)

    target = "persist_task_reassignment" if insertion_point == "before_persist" else "write_status_snapshot_if_current"
    callback = persist_after_add if insertion_point == "before_persist" else cas_after_add
    with dispatch_boundary(config) as (events, audit, launch), mock.patch.object(supervisor, target, side_effect=callback):
        dispatch(config)
    latest = json.loads(path.read_text())
    assert latest["tasks"][0]["status"] == "blocked"
    assert latest["blockers"][0]["status"] == "open"
    assert latest["_status_write_revision"] == "parallel-canonical-write"
    assert events == []
    launch.assert_not_called()
    assert any(call.args[1]["type"] == "stale_status_write_rejected" for call in audit.call_args_list)
    assert not any("No unresolved dependency" in str(call) for call in audit.call_args_list)
