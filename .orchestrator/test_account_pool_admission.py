"""Recovery provenance must be captured before adapter delivery."""
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import supervisor
from adapters.base import DeliveryRequest, DeliveryResult


@pytest.fixture(autouse=True)
def isolated_status_root(tmp_path, monkeypatch):
    monkeypatch.setenv("PANTHEON_STATUS_ROOT", str(tmp_path))
    monkeypatch.setenv("ORCH_STATUS_ROOT", str(tmp_path))


def fixture(tmp_path):
    config = {
        "paths": {"activity_log": str(Path(tmp_path) / "activity.jsonl")},
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
        "provider": "codex", "trigger_provider": "codex",
        "paused_at": "2026-09-11T01:00:00Z", "worker_run_id": "failed-a",
        "blocked_until": "2026-09-11T03:00:00Z",
        "auth_identity_hash": "auth-a", "failure_kind": "quota_terminal",
    }
    state = {
        "workers": {},
        "provider_guardrails": {"dispatch_pauses": {"codex": pause}},
        "account_pool_runtime": {
            "pool_a": {
                "state": "cooldown", "effective_concurrency": 0, "generation": 1,
                "last_failure_at": pause["paused_at"], "last_worker_run_id": "failed-a",
                "auth_identity_hash": "auth-a", "failure_kind": "quota_terminal",
                "next_probe_at": pause["blocked_until"],
            },
            "pool_b": {"state": "healthy", "effective_concurrency": 2, "auth_identity_hash": "auth-a"},
        },
    }
    return config, state


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 9, 11, 4, 0, tzinfo=UTC)


def test_clear_during_adapter_delivery_cannot_relabel_preclear_worker(tmp_path):
    config, state = fixture(tmp_path)
    pause = state["provider_guardrails"]["dispatch_pauses"].pop("codex")
    cooldown = state["account_pool_runtime"].pop("pool_a")
    request = DeliveryRequest(agent_id="codex2", provider="codex2", delivery_mode="codex", message="fixture", task_id="fixture-task", reason="review_ready_dispatch")
    result = DeliveryResult(ok=True, adapter="codex", mode="codex", target="fixture", auto_delivered=True, manual_confirmation_required=False, run_id="started-before-clear")

    def deliver(_request):
        # The adapter has started the worker. A concurrent clear completes
        # before deliver() returns and the parent records the worker.
        state["provider_guardrails"]["dispatch_pauses"]["codex"] = pause
        state["account_pool_runtime"]["pool_a"] = cooldown
        assert supervisor.clear_provider_dispatch_pause(config, state, "codex")
        return result

    with (
        mock.patch.object(supervisor, "build_adapter") as adapter,
        mock.patch.object(supervisor, "datetime", Clock),
        mock.patch.object(supervisor, "utc_now", return_value="2026-09-11T04:00:00Z"),
        mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="auth-a"),
        mock.patch.object(supervisor, "write_activity_log"),
        mock.patch.object(supervisor, "record_worker_runtime_measurement"),
        mock.patch.object(supervisor, "save_runtime_state"),
    ):
        adapter.return_value.deliver.side_effect = deliver
        ok, run_id, _ = supervisor.start_worker_for_request(config, state, {}, request, queue_event_id="event", attempt_count=1, event_id_for_log="event")
        assert ok
        worker = state["workers"][run_id]
        worker.update(status="completed", runner_status="completed", exit_code=0)
        assert not supervisor.record_account_pool_canary_success(config, state, worker)
        assert state["account_pool_runtime"]["pool_a"]["state"] == "recovering"


def test_dispatch_auth_snapshot_precedes_adapter_delivery(tmp_path):
    config, state = fixture(tmp_path)
    request = DeliveryRequest(agent_id="codex", provider="codex", delivery_mode="codex", message="fixture", task_id="fixture-task", reason="review_ready_dispatch")
    result = DeliveryResult(ok=True, adapter="codex", mode="codex", target="fixture", auto_delivered=True, manual_confirmation_required=False, run_id="auth-a-worker")
    identity = ["auth-a"]

    def deliver(_request):
        assert identity[0] == "auth-a"  # the adapter launches with A
        identity[0] = "auth-b"  # credentials rotate before the parent resumes
        return result

    with (
        mock.patch.object(supervisor, "build_adapter") as adapter,
        mock.patch.object(supervisor, "datetime", Clock),
        mock.patch.object(supervisor, "provider_auth_identity_hash", side_effect=lambda *_args, **_kwargs: identity[0]),
        mock.patch.object(supervisor, "write_activity_log"),
        mock.patch.object(supervisor, "record_worker_runtime_measurement"),
        mock.patch.object(supervisor, "save_runtime_state"),
    ):
        adapter.return_value.deliver.side_effect = deliver
        ok, run_id, _ = supervisor.start_worker_for_request(config, state, {}, request, queue_event_id="event", attempt_count=1, event_id_for_log="event")
        assert ok
        assert state["workers"][run_id]["auth_identity_hash"] == "auth-a"


def test_canary_cannot_promote_sibling_recovery_created_after_dispatch(tmp_path):
    config, state = fixture(tmp_path)
    request = DeliveryRequest(agent_id="codex", provider="codex", delivery_mode="codex", message="fixture", task_id="fixture-task", reason="review_ready_dispatch")
    result = DeliveryResult(ok=True, adapter="codex", mode="codex", target="fixture", auto_delivered=True, manual_confirmation_required=False, run_id="canary-before-new-sibling-epoch")
    with (
        mock.patch.object(supervisor, "build_adapter") as adapter,
        mock.patch.object(supervisor, "datetime", Clock),
        mock.patch.object(supervisor, "utc_now", return_value="2026-09-11T04:00:00Z"),
        mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="auth-a"),
        mock.patch.object(supervisor, "write_activity_log"),
        mock.patch.object(supervisor, "record_worker_runtime_measurement"),
        mock.patch.object(supervisor, "save_runtime_state"),
    ):
        assert supervisor.clear_provider_dispatch_pause(config, state, "codex")
        adapter.return_value.deliver.return_value = result
        ok, run_id, _ = supervisor.start_worker_for_request(config, state, {}, request, queue_event_id="event", attempt_count=1, event_id_for_log="event")
        assert ok
        # A refreshed runtime contains a new same-second sibling recovery.
        # The canary's own pool still belongs to its original admission.
        sibling = state["account_pool_runtime"]["pool_b"]
        sibling["generation"] += 1
        worker = state["workers"][run_id]
        worker.update(status="completed", runner_status="completed", exit_code=0)
        assert supervisor.record_account_pool_canary_success(config, state, worker)
        assert state["account_pool_runtime"]["pool_a"]["state"] == "healthy"
        assert sibling["state"] == "recovering"


@pytest.mark.parametrize("worker_auth,admitted,expected", [
    (None, False, False),
    (None, True, False),
    ("auth-a", True, False),
    ("auth-b", False, False),
    ("auth-b", True, True),
])
def test_missing_worker_auth_cannot_be_inferred_from_current_credentials(tmp_path, worker_auth, admitted, expected):
    config, _ = fixture(tmp_path)
    entry = {
        "state": "recovering", "effective_concurrency": 1, "generation": 3,
        "auth_identity_hash": "auth-b", "last_probe_at": "2026-09-11T04:00:00Z",
    }
    worker = {
        "run_id": "legacy-or-canary", "logical_agent_id": "codex", "provider": "codex",
        "status": "completed", "runner_status": "completed", "exit_code": 0,
        "lease_acquired_at": "2026-09-11T04:00:00Z",
    }
    if worker_auth is not None:
        worker["auth_identity_hash"] = worker_auth
    if admitted:
        worker.update({
            "dispatched_pool_state": "recovering", "recovery_generation": 3,
            "dispatched_recovery_epochs": {"pool_a": {key: entry.get(key) for key in (
                "auth_identity_hash", "generation", "last_probe_at", "last_failure_at", "last_worker_run_id"
            )}},
        })
    state = {"workers": {worker["run_id"]: worker}, "account_pool_runtime": {"pool_a": entry}}
    with (
        mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="auth-b"),
        mock.patch.object(supervisor, "write_activity_log"),
    ):
        assert supervisor.record_account_pool_canary_success(config, state, worker) is expected
    assert entry["state"] == ("healthy" if expected else "recovering")
