"""Independent bounded admission repro. All auth, launch and external I/O mocked."""
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import supervisor
from adapters.base import DeliveryRequest


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PANTHEON_STATUS_ROOT", str(tmp_path))
    monkeypatch.setenv("ORCH_STATUS_ROOT", str(tmp_path))


def fixture():
    config = {
        "account_pools": {
            "pool_a": {"max_concurrent": 2, "enabled": True},
            "pool_b": {"max_concurrent": 2, "enabled": True},
        },
        "agents": {
            "codex": {"id": "codex", "provider": "codex", "adapter": "codex", "account_pool": "pool_a"},
            "codex2": {"id": "codex2", "provider": "codex2", "adapter": "codex", "account_pool": "pool_b"},
        },
        "providers": {
            "codex": {"delivery_mode": "codex", "quota_group": "codex"},
            "codex2": {"delivery_mode": "codex", "quota_group": "codex"},
        },
    }
    pause = {
        "provider": "codex", "trigger_provider": "codex", "paused_at": "2026-09-11T01:00:00Z",
        "blocked_until": "2099-09-11T03:00:00Z", "worker_run_id": "failed-a",
        "auth_identity_hash": "auth-a", "failure_kind": "quota_terminal",
    }
    state = {
        "workers": {}, "queue": {"events": {}},
        "provider_guardrails": {"dispatch_pauses": {"codex": pause}},
        "account_pool_runtime": {
            "pool_a": {
                "state": "cooldown", "effective_concurrency": 0, "generation": 1,
                "last_failure_at": pause["paused_at"], "last_worker_run_id": "failed-a",
                "next_probe_at": pause["blocked_until"], "auth_identity_hash": "auth-a",
                "failure_kind": "quota_terminal",
            },
            "pool_b": {"state": "healthy", "effective_concurrency": 2, "auth_identity_hash": "auth-a"},
        },
    }
    return config, state


@pytest.mark.parametrize("scenario", ["zero_sibling", "admitted_canary", "independent_auth", "occupied_canary"])
def test_shared_canary_actual_process_queue_launch(scenario):
    config, state = fixture()
    target = "codex" if scenario in {"admitted_canary", "occupied_canary"} else "codex2"
    expected_launches = 1 if scenario in {"admitted_canary", "independent_auth"} else 0
    identity = {"codex": "auth-a", "codex2": "auth-b" if scenario == "independent_auth" else "auth-a"}
    state["account_pool_runtime"]["pool_b"]["auth_identity_hash"] = identity["codex2"]
    if scenario in {"zero_sibling", "occupied_canary"}:
        state["workers"]["active-canary"] = {
            "run_id": "active-canary", "status": "running", "agent_id": "codex",
            "logical_agent_id": "codex", "provider": "codex", "quota_group": "pool_a",
            "task_id": "another-task", "queue_event_id": "another-event", "auth_identity_hash": "auth-a",
        }
    event = {"event_id": "fixture-event", "target_agent": target, "task_id": "fixture-task", "provider": target, "reason": "review_ready_dispatch"}
    request = DeliveryRequest(agent_id=target, provider=target, delivery_mode="codex", message="fixture", task_id="fixture-task", reason="review_ready_dispatch")
    with (
        mock.patch.object(supervisor, "provider_auth_identity_hash", side_effect=lambda _config, provider: identity.get(provider)),
        mock.patch.object(supervisor, "write_activity_log"),
        mock.patch.object(supervisor, "record_worker_runtime_measurement"),
        mock.patch.object(supervisor, "provider_runtime_config_block_reason", return_value=None),
        mock.patch.object(supervisor, "load_status", return_value={}),
        mock.patch.object(supervisor, "task_index_from_status", return_value={}),
        mock.patch.object(supervisor, "load_event_queue", return_value=[event]),
        mock.patch.object(supervisor, "queue_event_is_orphaned", return_value=False),
        mock.patch.object(supervisor, "stale_dispatch_skip_message", return_value=None),
        mock.patch.object(supervisor, "build_request", return_value=request),
        mock.patch.object(supervisor, "prepare_worker_workspace", return_value=(True, None)),
        mock.patch.object(supervisor, "sync_dispatched_task_status"),
        mock.patch.object(supervisor, "start_worker_for_request", return_value=(True, "fixture-launch", {})) as launch,
    ):
        assert supervisor.clear_provider_dispatch_pause(config, state, "codex")
        capacity = supervisor.account_pool_effective_concurrency(config, state, target)
        assert capacity == ({"zero_sibling": 0, "admitted_canary": 1, "occupied_canary": 1, "independent_auth": 2}[scenario])
        supervisor.process_queue(config, state, {})
        assert launch.call_count == expected_launches, f"{scenario}: capacity={capacity}, queue={state['queue']}"


@pytest.mark.parametrize("rotated_identity", ["auth-a", "auth-b"])
def test_healthy_pool_rotating_into_recovering_identity_respects_shared_budget(rotated_identity):
    config, state = fixture()
    state["account_pool_runtime"]["pool_b"]["auth_identity_hash"] = "auth-b"
    identity = {"codex": "auth-a", "codex2": "auth-b"}
    with (
        mock.patch.object(supervisor, "provider_auth_identity_hash", side_effect=lambda _config, provider: identity.get(provider)),
        mock.patch.object(supervisor, "write_activity_log"),
    ):
        assert supervisor.clear_provider_dispatch_pause(config, state, "codex")
        assert state["account_pool_runtime"]["pool_a"]["state"] == "recovering"
        assert state["account_pool_runtime"]["pool_b"]["state"] == "healthy"
        identity["codex2"] = rotated_identity
        limits = {agent: supervisor.account_pool_effective_concurrency(config, state, agent) for agent in ("codex", "codex2")}
        if rotated_identity == "auth-a":
            assert sum(limits.values()) <= 1, f"same auth exposed {limits} before canary success"
        else:
            assert limits == {"codex": 1, "codex2": 2}


@pytest.mark.parametrize("scenario", ["zero_sibling", "admitted_canary", "independent_auth", "unspecified_limit"])
@pytest.mark.parametrize("general_guard", [True, False])
def test_shared_canary_actual_ready_dispatch(scenario, general_guard, tmp_path):
    from contextlib import ExitStack

    import dispatch_engine

    config, state = fixture()
    config["paths"] = {key: str(tmp_path / f"{key}.json") for key in ("status_file", "activity_log", "event_queue")}
    config["ready_dispatcher"] = {"helper_execution_lease": {"enabled": False}}
    target = "codex" if scenario == "admitted_canary" else "codex2"
    identity = {"codex": "auth-a", "codex2": "auth-b" if scenario in {"independent_auth", "unspecified_limit"} else "auth-a"}
    state["account_pool_runtime"]["pool_b"]["auth_identity_hash"] = identity["codex2"]
    if scenario == "unspecified_limit":
        config["account_pools"]["pool_b"]["max_concurrent"] = None
    task = {"id": "ready-fixture", "owner": target, "reviewer": "codex" if target == "codex2" else "codex2", "status": "in_progress", "depends_on": []}
    board = {"tasks": [task]}
    events = []
    with ExitStack() as patches:
        patches.enter_context(mock.patch.object(supervisor, "provider_auth_identity_hash", side_effect=lambda _config, provider: identity.get(provider)))
        patches.enter_context(mock.patch.object(supervisor, "write_activity_log"))
        assert supervisor.clear_provider_dispatch_pause(config, state, "codex")
        for name in ("repair_open_task_metadata", "repair_unsubmitted_review_tasks", "reassign_tasks_after_review_churn", "normalize_task_assignment_integrity", "normalize_mainline_task_assignment", "reassign_unavailable_reviewers"):
            patches.enter_context(mock.patch.object(supervisor, name, return_value=False))
        patches.enter_context(mock.patch.object(dispatch_engine, "task_reality_reconcile_is_due", return_value=False))
        patches.enter_context(mock.patch.object(supervisor, "provider_runtime_config_block_reason", return_value=None))
        patches.enter_context(mock.patch.object(supervisor, "load_status", return_value=board))
        patches.enter_context(mock.patch.object(supervisor, "load_event_queue", return_value=[]))
        patches.enter_context(mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=True))
        patches.enter_context(mock.patch.object(supervisor, "queue_delivery_event", side_effect=lambda _config, event: events.append(event) or True))
        if not general_guard:
            # Also verify the dispatcher's own limit checks, independently of
            # the shared front-door guard; None must remain unlimited, zero not.
            patches.enter_context(mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None))
        supervisor.dispatch_ready_tasks(config, state, {}, agent_ids_override=[target])
    assert len(events) == (0 if scenario == "zero_sibling" else 1)


def test_healthy_auth_join_recovery_fence_survives_stale_disk_writer(tmp_path):
    import runtime_state

    config, state = fixture()
    config["paths"] = {"state_file": str(tmp_path / "state.json"), "event_queue": str(tmp_path / "queue.jsonl")}
    Path(config["paths"]["event_queue"]).write_text("")
    state["account_pool_runtime"]["pool_b"]["auth_identity_hash"] = "auth-b"
    identity = {"codex": "auth-a", "codex2": "auth-b"}
    with (
        mock.patch.object(supervisor, "provider_auth_identity_hash", side_effect=lambda _config, provider: identity.get(provider)),
        mock.patch.object(supervisor, "write_activity_log"),
    ):
        assert supervisor.clear_provider_dispatch_pause(config, state, "codex")
        runtime_state.save_runtime_state(config, state)
        stale = runtime_state.load_runtime_state(config)
        fresh = runtime_state.load_runtime_state(config)
        identity["codex2"] = "auth-a"
        assert supervisor.account_pool_effective_concurrency(config, fresh, "codex2") == 0
        runtime_state.save_runtime_state(config, fresh)
        runtime_state.save_runtime_state(config, stale)
        saved = runtime_state.load_runtime_state(config)
        assert saved["account_pool_runtime"]["pool_b"]["auth_identity_hash"] == "auth-a"
        assert saved["account_pool_runtime"]["pool_b"]["state"] == "recovering"
        assert supervisor.account_pool_effective_concurrency(config, saved, "codex2") == 0
        assert supervisor.account_pool_effective_concurrency(config, saved, "codex") == 1
