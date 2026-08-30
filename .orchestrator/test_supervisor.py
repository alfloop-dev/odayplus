#!/usr/bin/env python3
from __future__ import annotations

import ast
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

THIS_DIR = Path(__file__).resolve().parent
ROOT_DIR = THIS_DIR.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Never let orchestration tests inherit a worker's live coordination root.
# ai_status binds its output paths at import time, so isolation must happen
# before importing it.
_ORIGINAL_STATUS_ROOT = os.environ.get("PANTHEON_STATUS_ROOT")
_ORIGINAL_ORCH_STATUS_ROOT = os.environ.get("ORCH_STATUS_ROOT")
_TEST_STATUS_ROOT_HANDLE = tempfile.TemporaryDirectory(prefix="pantheon-supervisor-tests-")
_TEST_STATUS_ROOT = Path(_TEST_STATUS_ROOT_HANDLE.name).resolve()
os.environ["PANTHEON_STATUS_ROOT"] = str(_TEST_STATUS_ROOT)
os.environ["ORCH_STATUS_ROOT"] = str(_TEST_STATUS_ROOT)

import ai_status
import runtime_state
import supervisor
import watch_events
import worker_failure_policy


def tearDownModule() -> None:
    if _ORIGINAL_STATUS_ROOT is None:
        os.environ.pop("PANTHEON_STATUS_ROOT", None)
    else:
        os.environ["PANTHEON_STATUS_ROOT"] = _ORIGINAL_STATUS_ROOT
    if _ORIGINAL_ORCH_STATUS_ROOT is None:
        os.environ.pop("ORCH_STATUS_ROOT", None)
    else:
        os.environ["ORCH_STATUS_ROOT"] = _ORIGINAL_ORCH_STATUS_ROOT
    _TEST_STATUS_ROOT_HANDLE.cleanup()


def load_test_config() -> dict[str, Any]:
    # The committed example is the fixture. config.json is gitignored and holds
    # whatever roster the machine currently runs, so reading it would make these
    # assertions depend on the box rather than on the code: a deployment that
    # trims agents out of owner_fallbacks turns reassignment tests red locally
    # while CI -- which has no config.json and bootstraps from the example --
    # stays green, and a machine with a laxer config hides real failures the
    # same way. Point PANTHEON_TEST_CONFIG at a file to opt into another one.
    override = os.environ.get("PANTHEON_TEST_CONFIG", "").strip()
    config_file = Path(override) if override else Path(__file__).with_name("config.example.json")
    config = json.loads(config_file.read_text(encoding="utf-8"))

    # A test config must never retain repository-relative coordination paths:
    # common.config_path() resolves them against the checked-out code root, not
    # PANTHEON_STATUS_ROOT. Rewrite the complete coordination path table to the
    # module-scoped temporary root before any Supervisor helper can persist.
    config.setdefault("ready_dispatcher", {})["disabled_agents"] = []

    isolated_paths: dict[str, str] = {}
    for key, value in (config.get("paths") or {}).items():
        raw_path = Path(str(value))
        relative_path = Path(raw_path.name) if raw_path.is_absolute() else raw_path
        isolated_paths[key] = str((_TEST_STATUS_ROOT / relative_path).resolve())
    config["paths"] = isolated_paths

    watchdog = config.setdefault("watchdog", {})
    watchdog["state_file"] = str((_TEST_STATUS_ROOT / ".orchestrator/watchdog-state.json").resolve())
    watchdog["metrics_file"] = str(
        (_TEST_STATUS_ROOT / ".orchestrator/metrics/supervisor-watchdog.jsonl").resolve()
    )
    config.setdefault("worker_worktrees", {})["root"] = str(
        (_TEST_STATUS_ROOT / "worker-worktrees").resolve()
    )
    config.setdefault("permission_broker", {})["allowed_workspace_roots"] = [
        str((_TEST_STATUS_ROOT / "workspace").resolve())
    ]
    return config


class TestConfigFixtureTests(unittest.TestCase):
    """The fixture must be the committed example, never the machine's config.

    config.json is gitignored and tracks whatever roster is deployed, so reading
    it makes these tests assert on the box instead of the code -- green on CI,
    red on a machine whose owner_fallbacks were trimmed, and silently permissive
    on a machine whose config is laxer than the example.
    """

    def _example(self) -> dict[str, Any]:
        return json.loads(
            (Path(supervisor.__file__).with_name("config.example.json")).read_text(encoding="utf-8")
        )

    def test_fixture_roster_matches_committed_example(self) -> None:
        config = load_test_config()
        example = self._example()

        self.assertEqual(sorted(config.get("agents") or {}), sorted(example.get("agents") or {}))
        for table in ("owner_fallbacks", "reviewer_fallbacks"):
            with self.subTest(table=table):
                self.assertEqual(
                    (config.get("worker_reassignment") or {}).get(table),
                    (example.get("worker_reassignment") or {}).get(table),
                )

    def test_explicit_override_is_honoured(self) -> None:
        example = self._example()
        example["agents"] = {"solo": example["agents"][next(iter(example["agents"]))]}
        with tempfile.TemporaryDirectory(prefix="pantheon-test-config-") as tmp:
            override = Path(tmp) / "config.json"
            override.write_text(json.dumps(example), encoding="utf-8")
            with mock.patch.dict(os.environ, {"PANTHEON_TEST_CONFIG": str(override)}):
                config = load_test_config()

        self.assertEqual(sorted(config.get("agents") or {}), ["solo"])


class StatusWriteConcurrencyTests(unittest.TestCase):
    def test_supervisor_has_one_task_transition_commit_boundary(self) -> None:
        source = Path(supervisor.__file__).read_text(encoding="utf-8")
        # Definition plus the canonical boundary only.  Dispatch, repair,
        # preemption, and reassignment must not regain a direct snapshot write
        # that can skip derived-state synchronization.
        self.assertEqual(source.count("write_status_snapshot_if_current("), 2)
        # The one physical status write belongs inside the CAS writer. CI
        # repair previously added a second write before its canonical commit,
        # publishing marker-only partial transitions.
        self.assertEqual(source.count("write_json(status_path, status)"), 1)

    def test_stale_supervisor_snapshot_cannot_overwrite_newer_cli_revision(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pantheon-status-cas-") as tmp:
            root = Path(tmp)
            status_path = root / "ai-status.json"
            config = {
                "paths": {
                    "status_file": str(status_path),
                    "activity_log": str(root / "ai-activity-log.jsonl"),
                }
            }
            stale = {
                "_status_write_revision": "old-revision",
                "tasks": [{"id": "TASK-001", "status": "review", "review_submission": {"remote_sha": "old"}}],
            }
            latest = {
                "_status_write_revision": "new-revision",
                "tasks": [{"id": "TASK-001", "status": "review", "review_submission": {"remote_sha": "new"}}],
            }
            status_path.write_text(json.dumps(latest), encoding="utf-8")

            self.assertFalse(supervisor.write_status_snapshot_if_current(config, stale))
            self.assertEqual(stale, latest)
            self.assertEqual(json.loads(status_path.read_text(encoding="utf-8")), latest)

    def test_current_supervisor_snapshot_advances_revision_atomically(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pantheon-status-cas-") as tmp:
            root = Path(tmp)
            status_path = root / "ai-status.json"
            config = {
                "paths": {
                    "status_file": str(status_path),
                    "activity_log": str(root / "ai-activity-log.jsonl"),
                }
            }
            status = {
                "_status_write_revision": "current-revision",
                "tasks": [{"id": "TASK-001", "status": "review", "next": "updated"}],
            }
            status_path.write_text(json.dumps(status), encoding="utf-8")

            self.assertTrue(supervisor.write_status_snapshot_if_current(config, status))
            self.assertNotEqual(status["_status_write_revision"], "current-revision")
            self.assertEqual(
                json.loads(status_path.read_text(encoding="utf-8")),
                status,
            )


class RuntimeConfigTests(unittest.TestCase):
    def test_supervisor_pins_ai_status_to_immutable_runtime(self) -> None:
        self.assertEqual(
            Path(supervisor.runtime_ai_status.__file__).resolve(),
            (SCRIPTS_DIR / "ai_status.py").resolve(),
        )

    def test_dashboard_refresh_does_not_prepend_status_root_scripts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pantheon-stale-status-root-") as tmp:
            status_root = Path(tmp)
            stale_scripts = status_root / "scripts"
            stale_scripts.mkdir()
            (stale_scripts / "ai_status.py").write_text(
                "raise RuntimeError('stale status-root ai_status imported')\n",
                encoding="utf-8",
            )
            config = {"paths": {"status_file": str(status_root / "ai-status.json")}}

            with mock.patch.object(supervisor.runtime_ai_status, "load_state", return_value={}), \
                 mock.patch.object(supervisor.runtime_ai_status, "write_dashboard_bundle"), \
                 mock.patch.object(supervisor.runtime_ai_status, "sync_docs_site"):
                supervisor.refresh_dashboard_runtime_artifacts(config)

            self.assertNotIn(str(stale_scripts), sys.path)

    def test_task_head_resolution_ignores_poisoned_ai_status_module(self) -> None:
        stale_module = mock.Mock()
        stale_module.resolve_task_sha.side_effect = TypeError(
            "resolve_task_sha() got an unexpected keyword argument 'force_refresh'"
        )
        with mock.patch.dict(sys.modules, {"ai_status": stale_module}), \
             mock.patch.object(
                 supervisor.runtime_ai_status,
                 "resolve_task_sha",
                 return_value="1111111122222222333333334444444455555555",
             ) as pinned_resolver, \
             mock.patch.object(
                 supervisor.runtime_ai_status,
                 "task_pr_ci_status",
                 return_value=("MERGED", "success"),
             ):
            self.assertEqual(
                supervisor.resolve_task_progress_head("PINNED-RUNTIME-001"),
                "1111111122222222333333334444444455555555",
            )
            self.assertEqual(
                supervisor.dispatch_priority_for_task(
                    load_test_config(),
                    {
                        "id": "PINNED-RUNTIME-001",
                        "owner": "Codex2",
                        "reviewer": "Codex",
                        "status": "review_approved",
                        "approved_head": "1111111122222222333333334444444455555555",
                    },
                    "Codex2",
                ),
                1,
            )

        self.assertEqual(
            pinned_resolver.call_args_list,
            [
                mock.call("PINNED-RUNTIME-001"),
                mock.call("PINNED-RUNTIME-001", force_refresh=True),
            ],
        )
        stale_module.resolve_task_sha.assert_not_called()

    def test_test_config_coordination_paths_are_temporary_and_absolute(self) -> None:
        config = load_test_config()

        for key, value in config["paths"].items():
            with self.subTest(path=key):
                path = Path(value)
                self.assertTrue(path.is_absolute())
                self.assertTrue(path.is_relative_to(_TEST_STATUS_ROOT))
        self.assertTrue(Path(config["watchdog"]["state_file"]).is_relative_to(_TEST_STATUS_ROOT))
        self.assertTrue(Path(config["watchdog"]["metrics_file"]).is_relative_to(_TEST_STATUS_ROOT))
        self.assertTrue(Path(config["worker_worktrees"]["root"]).is_relative_to(_TEST_STATUS_ROOT))

    def test_example_config_has_no_legacy_capacity_model(self) -> None:
        config = load_test_config()

        ready_dispatcher = config["ready_dispatcher"]
        for key in (
            "helper_claim",
            "worker_self_claim",
            "max_tasks_per_agent",
            "max_tasks_per_agent_by_agent",
            "target_workload",
            "agent_workload_weights",
            "max_concurrent_per_quota_group",
            "max_concurrent_workers",
        ):
            self.assertNotIn(key, ready_dispatcher)


class AccountPoolSchedulingTests(unittest.TestCase):
    def _config(self) -> dict[str, Any]:
        return {
            "account_pools": {
                "antigravity_main": {"max_concurrent": 2, "state": "healthy"},
                "codex_exhausted": {"enabled": False, "reason": "known exhausted quota"},
            },
            "agents": {
                "antigravity": {
                    "id": "antigravity", "display_name": "Antigravity", "provider": "antigravity",
                    "account_pool": "antigravity_main",
                },
                "antigravity2": {
                    "id": "antigravity2", "display_name": "Antigravity2", "provider": "antigravity",
                    "account_pool": "antigravity_main",
                },
                "ag_slot_1": {
                    "id": "ag_slot_1", "display_name": "Antigravity", "provider": "antigravity",
                    "account_pool": "antigravity_main", "dispatch_slot_for_pool": "antigravity_main",
                },
                "ag_slot_2": {
                    "id": "ag_slot_2", "display_name": "Antigravity", "provider": "antigravity",
                    "account_pool": "antigravity_main", "dispatch_slot_for_pool": "antigravity_main",
                },
                "codex": {
                    "id": "codex", "display_name": "Codex", "provider": "codex",
                    "account_pool": "codex_exhausted",
                },
            },
            "providers": {"antigravity": {}, "codex": {}},
            "ready_dispatcher": {"active_worker_statuses": ["running"]},
        }

    def test_aliases_share_slots_quota_and_review_independence(self) -> None:
        config = self._config()
        self.assertEqual(supervisor.logical_worker_slot_ids(config, "antigravity"), ["ag_slot_1", "ag_slot_2"])
        self.assertEqual(supervisor.logical_worker_slot_ids(config, "antigravity2"), ["ag_slot_1", "ag_slot_2"])
        self.assertEqual(supervisor.agent_dispatch_capacity(config, "antigravity2"), 2)
        self.assertEqual(supervisor.quota_group_concurrency_limit(config, "antigravity"), 2)
        self.assertFalse(supervisor.review_is_independent(config, "Antigravity", "Antigravity2"))
        self.assertIn(
            "disabled",
            supervisor.agent_auto_dispatch_block_reason(config, {"workers": {}}, "codex", {}) or "",
        )

    def test_declared_slots_override_legacy_per_alias_capacity(self) -> None:
        config = self._config()
        config["ready_dispatcher"]["max_tasks_per_agent_by_agent"] = {"Antigravity": 99}

        # Aliases describe who owns/reviews a task. They are not 99 processes:
        # this account has exactly the two executable slots declared above.
        self.assertEqual(supervisor.agent_dispatch_capacity(config, "antigravity"), 2)
        self.assertEqual(supervisor.agent_dispatch_capacity(config, "antigravity2"), 2)

    def test_priority_precedes_lifecycle_and_board_order(self) -> None:
        config = {
            "schema": {"tasks_path": "tasks", "task_id_field": "id", "assignee_field": "owner", "reviewer_field": "reviewer"},
            "ready_dispatcher": {"enabled": True, "max_dispatches_per_tick": 1, "helper_claim": {"enabled": False}},
            "agents": {"codex": {"id": "codex", "display_name": "Codex", "provider": "codex"}},
            "providers": {"codex": {}},
        }
        status = {"tasks": [
            {"id": "LOW-FIRST", "status": "todo", "priority": "P3", "owner": "Codex", "reviewer": "Reviewer", "depends_on": []},
            {"id": "HIGH-SECOND", "status": "todo", "priority": "P0", "owner": "Codex", "reviewer": "Reviewer", "depends_on": []},
        ]}
        queued: list[dict[str, Any]] = []
        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", side_effect=lambda _config, event: queued.append(event) or True),
        ):
            self.assertTrue(supervisor.dispatch_ready_tasks(config, {"queue": {"events": {}}, "workers": {}}, provider_report={}))
        self.assertEqual([event["task_id"] for event in queued], ["HIGH-SECOND"])

    def test_slot_worker_matches_its_logical_owner_not_slot_display_name(self) -> None:
        config = self._config()
        config["agents"]["ag_slot_1"]["display_name"] = "ag_slot_1"
        task_map = {"TASK-1": {"id": "TASK-1", "status": "todo", "owner": "Antigravity", "reviewer": "Codex"}}
        worker = {
            "task_id": "TASK-1",
            "agent_id": "ag_slot_1",
            "logical_agent_id": "antigravity",
            "status": "running",
        }
        self.assertTrue(supervisor.worker_matches_current_assignment(config, worker, task_map))

    def test_quota_cooldown_recovers_through_one_canary_then_full_capacity(self) -> None:
        config = self._config()
        state: dict[str, Any] = {}
        worker = {"run_id": "run-1", "task_id": "TASK-1", "logical_agent_id": "antigravity"}
        past = datetime.now(UTC) - timedelta(seconds=1)
        with mock.patch.object(supervisor, "write_activity_log"):
            self.assertTrue(
                supervisor.mark_account_pool_cooldown(
                    config,
                    state,
                    worker,
                    "quota exhausted",
                    failure_kind="quota_terminal",
                    blocked_until=past,
                )
            )
            self.assertEqual(supervisor.account_pool_effective_concurrency(config, state, "antigravity"), 1)
            self.assertEqual(state["account_pool_runtime"]["antigravity_main"]["state"], "recovering")
            self.assertTrue(supervisor.record_account_pool_canary_success(config, state, worker))
            self.assertEqual(supervisor.account_pool_effective_concurrency(config, state, "antigravity"), 2)
            self.assertEqual(state["account_pool_runtime"]["antigravity_main"]["state"], "healthy")

    def test_account_pool_runtime_state_and_concurrency_when_provider_auth_false(self) -> None:
        config = self._config()
        config["account_pools"]["claude_main"] = {"max_concurrent": 2, "state": "healthy"}
        config["agents"]["claude"] = {
            "id": "claude", "display_name": "Claude", "provider": "claude", "account_pool": "claude_main"
        }
        config["agents"]["claude_slot_1"] = {
            "id": "claude_slot_1", "display_name": "claude_slot_1", "provider": "claude",
            "account_pool": "claude_main", "dispatch_slot_for_pool": "claude_main", "slot_id": "claude_slot_1"
        }
        config["agents"]["claude_slot_2"] = {
            "id": "claude_slot_2", "display_name": "claude_slot_2", "provider": "claude",
            "account_pool": "claude_main", "dispatch_slot_for_pool": "claude_main", "slot_id": "claude_slot_2"
        }
        unauth_report = {
            "providers": {"claude": {"auth_ready": False, "local_cli_worker_supported": False, "supports_auto_approve": False}},
            "agent_adapters": {
                "claude": {"can_auto_deliver": False, "notes": "Claude CLI is installed but not authenticated, so delivery falls back to the workspace inbox path."},
                "claude_slot_1": {"can_auto_deliver": False, "notes": "Claude CLI is installed but not authenticated, so delivery falls back to the workspace inbox path."},
                "claude_slot_2": {"can_auto_deliver": False, "notes": "Claude CLI is installed but not authenticated, so delivery falls back to the workspace inbox path."},
            },
        }
        state: dict[str, Any] = {"workers": {}}
        lifecycle, entry = supervisor.account_pool_runtime_state(config, state, "claude", provider_report=unauth_report)
        self.assertIn(lifecycle, {"auth_blocked", "inbox_fallback", "unavailable"})
        self.assertEqual(entry.get("effective_concurrency"), 0)
        self.assertEqual(supervisor.account_pool_effective_concurrency(config, state, "claude", provider_report=unauth_report), 0)
        block_reason = supervisor.account_pool_dispatch_block_reason(config, "claude", runtime_state=state, provider_report=unauth_report)
        self.assertIsNotNone(block_reason)

        # When provider report becomes healthy, pool recovers automatically
        auth_report = {
            "providers": {"claude": {"auth_ready": True, "local_cli_worker_supported": True, "supports_auto_approve": True}},
            "agent_adapters": {
                "claude": {"can_auto_deliver": True, "supported": True},
                "claude_slot_1": {"can_auto_deliver": True, "supported": True},
                "claude_slot_2": {"can_auto_deliver": True, "supported": True},
            },
        }
        lifecycle_ok, entry_ok = supervisor.account_pool_runtime_state(config, state, "claude", provider_report=auth_report)
        self.assertEqual(lifecycle_ok, "healthy")
        self.assertEqual(entry_ok.get("effective_concurrency"), 2)
        self.assertEqual(supervisor.account_pool_effective_concurrency(config, state, "claude", provider_report=auth_report), 2)

    def test_reviewer_failover_excludes_owner_and_exhausted_pool(self) -> None:
        config = self._config()
        config["account_pools"]["codex_pool"] = {"max_concurrent": 1, "state": "healthy"}
        config["agents"]["codex_reviewer"] = {
            "id": "codex_reviewer", "display_name": "CodexReviewer", "provider": "codex", "account_pool": "codex_pool"
        }
        selected = supervisor.first_viable_agent(
            config,
            ["Antigravity2", "CodexReviewer"],
            exclude={"Antigravity"},
            state={"workers": {}},
            task={"id": "TASK-1", "task_class": "implementation"},
            role="reviewer",
            exclude_pools={"antigravity_main"},
        )
        self.assertEqual(selected, "CodexReviewer")

    def test_task_assignment_audit_catches_shared_pool_disabled_actor_and_false_review(self) -> None:
        config = self._config()
        task = {
            "id": "TASK-INTEGRITY-1",
            "status": "review",
            "owner": "Codex",
            "reviewer": "Antigravity2",
            "waiting_for": "Codex",
        }
        issues = supervisor.task_assignment_integrity_issues(config, {"workers": {}}, task)
        self.assertTrue(any(issue.startswith("owner_unavailable:") for issue in issues))
        self.assertIn("review_submission_missing_or_invalid", issues)

        task.update(owner="Antigravity", reviewer="Antigravity2", status="todo", waiting_for=None)
        issues = supervisor.task_assignment_integrity_issues(config, {"workers": {}}, task)
        self.assertIn("owner_reviewer_same_account_pool", issues)

    def test_review_submission_accepts_task_scoped_explicit_branch(self) -> None:
        task = {
            "id": "HEATZONE-LATENCY-FIXTURE-FIX",
            "branch": "agent/heatzone-latency-fixture-fix",
            "review_submission": {
                "pr_number": 826,
                "branch": "agent/heatzone-latency-fixture-fix",
                "base_branch": "dev",
                "remote_sha": "a" * 40,
            },
        }
        self.assertTrue(supervisor.review_submission_is_complete({"branch_workflow": {"dev_branch": "dev"}}, task))

    def test_assignment_integrity_audits_non_dispatchable_actor_identity_without_dispatch_eligibility(self) -> None:
        config = self._config()
        task = {
            "id": "OPERATOR-ONLY",
            "status": "todo",
            "priority": "P0",
            "owner": "Antigravity",
            "reviewer": "Codex",
            "non_dispatchable": True,
        }

        self.assertEqual(
            supervisor.task_assignment_integrity_issues(config, {"workers": {}}, task),
            [],
        )

        task["reviewer"] = "UnknownReviewer"
        self.assertEqual(
            supervisor.task_assignment_integrity_issues(config, {"workers": {}}, task),
            ["reviewer_unavailable:unregistered actor UnknownReviewer"],
        )

    def test_assignment_integrity_does_not_reassign_human_gate(self) -> None:
        config = self._config()
        task = {
            "id": "HUMAN-GATE",
            "status": "blocked",
            "priority": "P1",
            "owner": "Human/Ops",
            "reviewer": "Antigravity",
            "waiting_for": "Human/Ops",
            "task_class": "human_gate",
            "non_dispatchable": True,
        }
        status = {"tasks": [task]}

        with mock.patch.object(supervisor, "persist_task_reassignment") as persist:
            self.assertFalse(
                supervisor.normalize_task_assignment_integrity(config, {"workers": {}}, status, task)
            )
        persist.assert_not_called()

    def test_assignment_integrity_reassigns_reviewer_to_independent_healthy_pool(self) -> None:
        config = self._config()
        config["paths"] = {"status_file": "/tmp/status.json", "activity_log": "/tmp/activity.jsonl"}
        config["account_pools"]["claude_main"] = {"max_concurrent": 1, "state": "healthy"}
        config["agents"]["claude"] = {
            "id": "claude", "display_name": "Claude", "provider": "claude", "account_pool": "claude_main",
        }
        config["providers"]["claude"] = {}
        task = {"id": "TASK-INTEGRITY-2", "status": "todo", "owner": "Antigravity", "reviewer": "Antigravity2"}
        status = {"tasks": [task]}
        with (
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            self.assertTrue(
                supervisor.normalize_task_assignment_integrity(config, {"workers": {}}, status, task)
            )
        self.assertEqual(persist.call_args.kwargs["new_owner"], "Antigravity")
        self.assertEqual(persist.call_args.kwargs["new_reviewer"], "Claude")

    def test_assignment_integrity_retargets_stale_blocked_waiting_actor(self) -> None:
        config = self._config()
        config["paths"] = {"status_file": "/tmp/status.json", "activity_log": "/tmp/activity.jsonl"}
        config["account_pools"]["claude_main"] = {"max_concurrent": 1, "state": "healthy"}
        config["agents"]["claude"] = {
            "id": "claude", "display_name": "Claude", "provider": "claude", "account_pool": "claude_main",
        }
        config["providers"]["claude"] = {}
        task = {
            "id": "TASK-INTEGRITY-3",
            "status": "blocked",
            "owner": "Antigravity",
            "reviewer": "Claude",
            "waiting_for": "Codex",
        }
        status = {"tasks": [task]}
        with (
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            self.assertTrue(
                supervisor.normalize_task_assignment_integrity(config, {"workers": {}}, status, task)
            )
        self.assertEqual(persist.call_args.kwargs["new_waiting_for"], "Antigravity")



    def test_quota_failure_fences_sibling_slots_and_hands_off(self) -> None:
        config = self._config()
        triggering = {
            "run_id": "run-1", "task_id": "TASK-1", "logical_agent_id": "antigravity", "status": "running",
        }
        sibling = {
            "run_id": "run-2", "task_id": "TASK-2", "logical_agent_id": "antigravity2", "status": "running",
        }
        state = {"workers": {"run-1": triggering, "run-2": sibling}, "queue": {"events": {}}}
        with (
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "maybe_reassign_task_after_worker_failure", return_value="CodexReviewer") as reassign,
            mock.patch.object(supervisor, "finalize_queue_event_record"),
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            self.assertEqual(
                supervisor.fence_account_pool_workers(config, state, triggering, "quota exhausted"),
                1,
            )
        self.assertEqual(sibling["status"], "reassigned")
        self.assertEqual(sibling["reassigned_to"], "CodexReviewer")
        reassign.assert_called_once()


class WorkerWorkspaceNetworkTests(unittest.TestCase):
    def test_git_network_timeout_is_bounded_and_reported(self) -> None:
        with mock.patch.object(
            supervisor.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["git", "ls-remote"], timeout=7),
        ):
            result, error = supervisor._run_git_network_command(
                Path("/tmp"),
                ["ls-remote", "origin"],
                timeout_seconds=7,
            )
        self.assertIsNone(result)
        self.assertEqual(error, "git network command timed out after 7s")

class DetectWorkerFailureTests(unittest.TestCase):
    def _worker_for_log(self, content: str) -> dict[str, str]:
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
        handle.write(content)
        handle.flush()
        handle.close()
        self.addCleanup(Path(handle.name).unlink, missing_ok=True)
        return {"log_path": handle.name}

    def test_ignores_error_markers_inside_captured_log_output(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "codex",
                    "I am reading ai-activity-log.jsonl for context.",
                    '262-{"ts": "2026-04-05T13:36:01Z", "message": "Error: Model \\"grok-code-fast-1\\" from --model flag is not available."}',
                    'worker_retry_scheduled: {"message": "Transient worker failure detected; retry 1 scheduled at 2026-04-05T13:48:48Z: reason: \\"QUOTA_EXHAUSTED\\""}',
                    "No local failure happened in this session.",
                ]
            )
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_detects_missing_provider_cli_from_wrapper_message(self) -> None:
        """The 2026-08-05 Codex outage: this exact line sat in 194 worker logs.

        It matched no failure pattern, so `detect_worker_failure` returned None
        and every one of those dispatches was recorded as an unexplained exit
        with nothing printed. The lane was dead for six hours and the console
        showed only task reassignments.
        """

        worker = self._worker_for_log(
            "Codex CLI binary not found at /home/lupin/.npm-global/bin/codex or on PATH.\n"
        )

        self.assertEqual(
            supervisor.detect_worker_failure(worker),
            "Codex CLI binary not found at /home/lupin/.npm-global/bin/codex or on PATH.",
        )

    def test_detects_missing_cli_for_every_provider_wrapper(self) -> None:
        for message in (
            "Codex CLI binary not found at /home/lupin/.npm-global/bin/codex or on PATH.",
            "Antigravity CLI (agy) binary not found under ~/.local/bin or PATH.",
            "Claude CLI binary not found under ~/.vscode-server/extensions.",
            "Copilot CLI binary not found under ~/.local/share/pantheon-orchestrator-tools.",
            "GitHub CLI binary not found under ~/.local/share/pantheon-orchestrator-tools.",
        ):
            with self.subTest(message=message):
                worker = self._worker_for_log(message + "\n")
                self.assertEqual(supervisor.detect_worker_failure(worker), message)

    def test_a_non_provider_binary_not_found_line_is_not_a_lane_failure(self) -> None:
        """Ordinary build output must not pause a healthy lane for 900s.

        An earlier pattern matched any line-initial "<token> binary not found",
        so a toolchain message like "protoc binary not found" read as a dead
        provider CLI.
        """

        for line in (
            "protoc binary not found in PATH",
            "ffmpeg binary not found",
            "terraform binary not found under /usr/local/bin",
        ):
            with self.subTest(line=line):
                worker = self._worker_for_log(line + "\n")
                self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_another_providers_launcher_error_does_not_kill_this_lane(self) -> None:
        """A codex worker reporting Claude's launcher error says nothing about codex."""

        reason = "Claude CLI binary not found under ~/.vscode-server/extensions."

        own = supervisor.classify_worker_failure({}, {"provider": "claude2"}, reason)
        other = supervisor.classify_worker_failure({}, {"provider": "codex3"}, reason)

        self.assertEqual(own["kind"], "provider_unavailable")
        self.assertNotEqual(other["kind"], "provider_unavailable")

    def test_gemini_launcher_error_maps_to_the_antigravity_family(self) -> None:
        failure = supervisor.classify_worker_failure(
            {},
            {"provider": "antigravity5"},
            "Antigravity CLI (agy) binary not found under ~/.local/bin or PATH.",
        )
        self.assertEqual(failure["kind"], "provider_unavailable")

    def test_missing_cli_wording_inside_task_output_is_not_a_lane_failure(self) -> None:
        """Ordinary work that mentions the wording must not pause a live lane."""

        worker = self._worker_for_log(
            "\n".join(
                [
                    "codex",
                    '+    raise RuntimeError("Codex CLI binary not found")',
                    'assert "CLI binary not found" in caplog.text',
                    "All tests passed.",
                ]
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_detects_real_model_availability_failure(self) -> None:
        worker = self._worker_for_log('Error: Model "grok-code-fast-1" from --model flag is not available.\n')

        self.assertEqual(
            supervisor.detect_worker_failure(worker),
            'Error: Model "grok-code-fast-1" from --model flag is not available.',
        )

    def test_detects_real_gemini_quota_failure(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "Error when talking to Gemini API Full report available at: /tmp/gemini-client-error.json TerminalQuotaError: You have exhausted your capacity on this model.",
                    "retryDelayMs: 1807388.816191,",
                    "reason: 'QUOTA_EXHAUSTED'",
                    "An unexpected critical error occurred:[object Object]",
                ]
            )
            + "\n"
        )

        self.assertEqual(
            supervisor.detect_worker_failure(worker),
            "Error when talking to Gemini API Full report available at: /tmp/gemini-client-error.json TerminalQuotaError: You have exhausted your capacity on this model.",
        )

    def test_detects_claude_auth_failure_from_cli_log(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    '{"type":"system","subtype":"api_retry","attempt":1,"max_retries":10,"retry_delay_ms":590.5,"error_status":401,"error":"authentication_failed"}',
                    '{"type":"assistant","message":{"content":[{"type":"text","text":"Failed to authenticate. API Error: 401 {\\"type\\":\\"error\\",\\"error\\":{\\"type\\":\\"authentication_error\\",\\"message\\":\\"Invalid authentication credentials\\"}}"}]}}',
                ]
            )
            + "\n"
        )

        self.assertEqual(
            supervisor.detect_worker_failure(worker),
            '{"type":"assistant","message":{"content":[{"type":"text","text":"Failed to authenticate. API Error: 401 {\\"type\\":\\"error\\",\\"error\\":{\\"type\\":\\"authentication_error\\",\\"message\\":\\"Invalid authentication credentials\\"}}"}]}}',
        )

    def test_ignores_auth_text_inside_tool_result_user_message(self) -> None:
        worker = self._worker_for_log(
            '{"type":"user","message":{"role":"user","content":[{"type":"tool_result","content":"prior state said not authenticated, but this is just captured inspection output"}]}}\n'
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_ignores_transcribed_limit_error_inside_review_notes(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "Reviewer note:",
                    'Auto-reassigned ownership from Claude to Copilot after repeated provider failure: {"type":"result","result":"You\'ve hit your limit · resets 12am (Asia/Taipei)","worker_run_id":"claude-123"}',
                    "No local failure happened in this session.",
                ]
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_ignores_search_result_json_field_that_mentions_quota(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "exec",
                    '718:      "next": "Auto-reassigned ownership from Copilot to Codex after repeated Copilot capacity/429: 402 You have no quota",',
                    "No local failure happened in this session.",
                ]
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_ignores_activity_log_bullet_that_mentions_prior_quota_reassignment(self) -> None:
        worker = self._worker_for_log(
            "- 2026-05-09T07:29:01Z · Orchestrator · task_reassigned · Auto-reassigned review from Copilot to Codex2 after repeated Copilot quota terminal: 402 You have no quota\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_ignores_captured_queue_event_json_that_mentions_prior_quota_reassignment(self) -> None:
        worker = self._worker_for_log(
            json.dumps(
                {
                    "event_id": "evt-1",
                    "event_key": "dispatcher:Codex2:BFF-LUV-SEM-001",
                    "target_agent": "codex2",
                    "message": "Wake-up queued for supervisor: review_ready_dispatch",
                    "metadata": {
                        "task": {
                            "next": "Auto-reassigned review from Copilot to Codex2 after repeated Copilot quota terminal: 402 You have no quota"
                        }
                    },
                }
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_ignores_allowed_rate_limit_event(self) -> None:
        worker = self._worker_for_log(
            json.dumps(
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {
                        "status": "allowed",
                        "resetsAt": 1778324400,
                        "rateLimitType": "five_hour",
                        "overageStatus": "rejected",
                        "overageDisabledReason": "org_level_disabled",
                        "isUsingOverage": False,
                    },
                }
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_detects_non_allowed_rate_limit_event(self) -> None:
        line = json.dumps(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {
                    "status": "rate_limited",
                    "rateLimitType": "five_hour",
                },
            }
        )
        worker = self._worker_for_log(line + "\n")

        self.assertEqual(supervisor.detect_worker_failure(worker), line)

    def test_detects_real_no_quota_line(self) -> None:
        worker = self._worker_for_log("402 You have no quota\n")

        self.assertEqual(supervisor.detect_worker_failure(worker), "402 You have no quota")

    def test_ignores_git_fatal_from_tool_command_output(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "exec",
                    "/bin/bash -lc 'git show abc:missing.md' in /repo",
                    " exited 128 in 0ms:",
                    "fatal: path 'missing.md' does not exist in 'abc'",
                    "worker continued reviewing after this probe.",
                ]
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_detects_standalone_fatal_line(self) -> None:
        worker = self._worker_for_log("fatal: provider process crashed\n")

        self.assertEqual(supervisor.detect_worker_failure(worker), "fatal: provider process crashed")

    def test_ignores_log_search_result_json_that_mentions_quota(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "exec",
                    '.orchestrator/logs/20260417T134622225365Z-claude.log:24:{"type":"user","message":{"content":"402 You have no quota"}}',
                    "No local failure happened in this session.",
                ]
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_ignores_pretty_json_field_that_mentions_auth_failure(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "succeeded in 252ms:",
                    '"next": "Auto-reassigned ownership from Gemini2 after repeated Gemini2 auth: not authenticated",',
                    "No local failure happened in this session.",
                ]
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_ignores_diff_assignment_that_quotes_auth_failure(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "**Blocker**",
                    '+ completed.stderr = b"Error: not authenticated, please login first"',
                    "The quoted failure came from a reviewed diff, not this worker process.",
                ]
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_auto_reassigned_log_summary_quoted_auth_ignored(self) -> None:
        """Verify auto-reassigned notes quoting auth or permission errors are not misclassified as live worker failures."""
        worker = self._worker_for_log(
            "next: Auto-reassigned ownership from Gemini2 after repeated Gemini2 auth: not authenticated\n"
        )
        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_python_source_assignment_quoted_auth_ignored(self) -> None:
        worker = self._worker_for_log(
            'reason = f"Principal {attempt.actor_id!r} is not authenticated"\n'
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_cloud_run_api_quota_exceeded_in_source_or_diff_ignored(self) -> None:
        """Regression test: Cloud Run API quota exceeded from test/source/diff must not be detected as worker failure."""
        for text in (
            "Cloud Run API quota exceeded\n",
            "+ raise Exception('Cloud Run API quota exceeded')\n",
            "assert 'Cloud Run API quota exceeded' in str(exc)\n",
            "google.api_core.exceptions.ResourceExhausted: 429 Quota exceeded for quota metric 'Cloud Run API quota exceeded'\n",
        ):
            worker = self._worker_for_log(text)
            self.assertIsNone(supervisor.detect_worker_failure(worker), f"Failed for: {text}")

    def test_completed_and_zero_exit_worker_ignores_log_tail(self) -> None:
        """Completed or exit_code 0 worker must return None and never establish provider pause."""
        worker = self._worker_for_log("402 You have no quota\nERROR: You've hit your usage limit.\n")
        worker["status"] = "completed"
        self.assertIsNone(supervisor.detect_worker_failure(worker))

        worker["status"] = "running"
        worker["exit_code"] = 0
        worker["runner_status"] = "completed"
        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_classifies_cloud_run_quota_exceeded_as_terminal_not_provider_quota(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "codex"}
        res = supervisor.classify_worker_failure(config, worker, "Cloud Run API quota exceeded")
        self.assertEqual(res["kind"], "terminal")
        self.assertFalse(supervisor.should_pause_dispatch_for_failure_kind(res["kind"]))

    def test_classifies_gemini_capacity_failure(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "gemini"}

        result = supervisor.classify_worker_failure(config, worker, "status: 429 RESOURCE_EXHAUSTED")

        self.assertEqual(result["kind"], "capacity_retryable")
        self.assertTrue(result["transient"])

    def test_classifies_gemini_terminal_quota_failure(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "gemini"}

        result = supervisor.classify_worker_failure(
            config,
            worker,
            "Error when talking to Gemini API Full report available at: /tmp/gemini-client-error.json TerminalQuotaError: You have exhausted your capacity on this model.",
        )

        self.assertEqual(result["kind"], "quota_terminal")
        self.assertFalse(result["transient"])

    def test_classifies_copilot_no_quota_failure_as_terminal(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "copilot"}

        result = supervisor.classify_worker_failure(config, worker, "402 You have no quota")

        self.assertEqual(result["kind"], "quota_terminal")
        self.assertFalse(result["transient"])

    def test_classifies_claude_credit_balance_failure_as_terminal(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "claude"}

        result = supervisor.classify_worker_failure(config, worker, "Credit balance is too low")

        self.assertEqual(result["kind"], "quota_terminal")
        self.assertFalse(result["transient"])

    def test_classifies_helper_free_tier_quota_failure_as_terminal(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "helper"}

        result = supervisor.classify_worker_failure(config, worker, "[API Error: Helper OAuth free tier quota exceeded.]")

        self.assertEqual(result["kind"], "quota_terminal")
        self.assertFalse(result["transient"])

    def test_classifies_codex_usage_limit_failure_as_terminal_quota(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "codex"}

        result = supervisor.classify_worker_failure(
            config,
            worker,
            "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 7:00 PM.",
        )

        self.assertEqual(result["kind"], "quota_terminal")
        self.assertFalse(result["transient"])

    def test_detects_codex_usage_limit_line_as_worker_failure(self) -> None:
        worker = self._worker_for_log(
            "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 7:00 PM.\n"
        )

        self.assertEqual(
            supervisor.detect_worker_failure(worker),
            "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 7:00 PM.",
        )

    def test_detects_codex_config_parse_failure_as_worker_failure(self) -> None:
        worker = self._worker_for_log(
            "Error loading config.toml: unknown variant `priority`, expected `fast` or `flex` in `service_tier`\n"
        )

        self.assertEqual(
            supervisor.detect_worker_failure(worker),
            "Error loading config.toml: unknown variant `priority`, expected `fast` or `flex` in `service_tier`",
        )

    def test_classifies_gemini_auth_failure(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "gemini"}

        result = supervisor.classify_worker_failure(config, worker, "status: 401 unauthorized")

        self.assertEqual(result["kind"], "auth")
        self.assertFalse(result["transient"])

    def test_classifies_not_authenticated_failure_as_auth(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "claude2"}

        result = supervisor.classify_worker_failure(config, worker, "Claude CLI is not authenticated; inbox fallback is disabled.")

        self.assertEqual(result["kind"], "auth")
        self.assertFalse(result["transient"])

    def test_classifies_github_cli_auth_failure_as_tool_auth(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "claude2"}

        result = supervisor.classify_worker_failure(config, worker, "GitHub CLI is not authenticated. Run gh auth login.")

        self.assertEqual(result["kind"], "tool_auth")
        self.assertFalse(result["transient"])

    def test_classifies_codex_config_parse_failure_as_provider_config(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "codex1-1"}

        result = supervisor.classify_worker_failure(
            config,
            worker,
            "Error loading config.toml: unknown variant `priority`, expected `fast` or `flex` in `service_tier`",
        )

        self.assertEqual(result["kind"], "provider_config")
        self.assertFalse(result["transient"])

    def test_auth_failures_pause_provider_dispatch(self) -> None:
        self.assertTrue(supervisor.should_pause_dispatch_for_failure_kind("auth"))

    def test_provider_config_failures_pause_provider_dispatch(self) -> None:
        self.assertTrue(supervisor.should_pause_dispatch_for_failure_kind("provider_config"))

    def test_tool_auth_failures_do_not_pause_provider_dispatch(self) -> None:
        self.assertFalse(supervisor.should_pause_dispatch_for_failure_kind("tool_auth"))

    def test_classifies_gemini_unknown_critical_failure(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "gemini"}

        result = supervisor.classify_worker_failure(config, worker, "An unexpected critical error occurred:[object Object]")

        self.assertEqual(result["kind"], "unknown_critical")
        self.assertFalse(result["transient"])

    def test_formats_runtime_timestamp_in_taipei_time(self) -> None:
        self.assertEqual(
            supervisor.format_runtime_timestamp_local("2026-04-06T14:35:42Z"),
            "2026-04-06 22:35:42",
        )

    @mock.patch("supervisor.os.kill")
    @mock.patch("supervisor.os.waitpid", return_value=(43210, 0))
    def test_pid_is_alive_treats_reaped_child_as_dead(self, _waitpid: mock.Mock, _kill: mock.Mock) -> None:
        self.assertFalse(supervisor.pid_is_alive(43210))

    def test_parse_quota_retry_hint_codex_pm(self) -> None:
        from datetime import datetime

        # 03:05Z on 2026-04-28 = 11:05 LOCAL (Asia/Taipei). "7:00 PM" in local
        # time = 19:00 LOCAL = 11:00 UTC same day.
        now = datetime(2026, 4, 28, 3, 5, 0, tzinfo=UTC)
        hint = supervisor.parse_quota_retry_hint(
            "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 7:00 PM.",
            now=now,
        )

        self.assertEqual(hint, datetime(2026, 4, 28, 11, 0, 0, tzinfo=UTC))

    def test_parse_quota_retry_hint_rolls_to_next_day_when_past(self) -> None:
        from datetime import datetime

        # 06:00Z on 2026-04-28 = 14:00 LOCAL same day (Asia/Taipei). "1pm" = 13:00
        # LOCAL is already past, so the hint should roll forward to the next day:
        # 2026-04-29 13:00 LOCAL = 2026-04-29 05:00 UTC.
        now = datetime(2026, 4, 28, 6, 0, 0, tzinfo=UTC)
        hint = supervisor.parse_quota_retry_hint(
            "You've hit your limit · resets 1pm (Asia/Taipei)",
            now=now,
        )

        self.assertEqual(hint, datetime(2026, 4, 29, 5, 0, 0, tzinfo=UTC))

    def test_parse_quota_retry_hint_honors_explicit_utc(self) -> None:
        from datetime import datetime

        now = datetime(2026, 5, 8, 16, 53, 27, tzinfo=UTC)
        hint = supervisor.parse_quota_retry_hint(
            "You've hit your limit · resets 8:40pm (UTC)",
            now=now,
        )

        self.assertEqual(hint, datetime(2026, 5, 8, 20, 40, 0, tzinfo=UTC))

    def test_parse_quota_retry_hint_codex_full_date(self) -> None:
        from datetime import datetime

        now = datetime(2026, 5, 16, 10, 5, 36, tzinfo=UTC)
        hint = supervisor.parse_quota_retry_hint(
            "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage "
            "to purchase more credits or try again at May 19th, 2026 12:40 AM.",
            now=now,
        )

        self.assertEqual(hint, datetime(2026, 5, 18, 16, 40, 0, tzinfo=UTC))

    def test_parse_quota_retry_hint_returns_none_when_absent(self) -> None:
        self.assertIsNone(supervisor.parse_quota_retry_hint("Credit balance is too low"))
        self.assertIsNone(supervisor.parse_quota_retry_hint(None))

    def test_missing_provider_cli_classifies_as_provider_unavailable(self) -> None:
        failure = supervisor.classify_worker_failure(
            {},
            {"provider": "codex"},
            "Codex CLI binary not found at /home/lupin/.npm-global/bin/codex or on PATH.",
        )

        self.assertEqual(failure["kind"], "provider_unavailable")
        self.assertFalse(failure["transient"])
        self.assertTrue(supervisor.should_pause_dispatch_for_failure_kind(failure["kind"]))

    # Rotation-enabled, as the seven antigravity providers in the live config
    # are. Asserting no-rotation against a provider that cannot rotate passes
    # whatever the code does, which is how the first version of this test missed
    # the defect it was written to catch.
    ROTATING_CONFIG = {
        "provider_guardrails": {
            "capacity_pause_seconds": 900,
            "quota_terminal_pause_seconds": 900,
        },
        "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
        "providers": {
            "antigravity5": {
                "antigravity": {
                    "model_rotation": {
                        "enabled": True,
                        "primary_model": "",
                        "fallback_model": "claude-sonnet-4-6",
                    }
                }
            }
        },
    }

    def test_missing_provider_cli_pauses_dispatch_without_rotating_models(self) -> None:
        """A dead binary has no second model pool to fall back onto.

        Rotation answers "this model pool is exhausted" by dispatching on the
        other pool. When the binary itself is gone, neither pool is reachable,
        so rotating just resumes the sub-second failure loop.
        """

        state: dict = {}
        self.assertTrue(
            supervisor.model_rotation.rotation_enabled(self.ROTATING_CONFIG, "antigravity5"),
            "fixture must have rotation enabled or this test proves nothing",
        )

        with (
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor.model_rotation, "record_exhaustion") as rotate,
        ):
            paused = supervisor.mark_provider_dispatch_paused(
                self.ROTATING_CONFIG,
                state,
                "antigravity5",
                "Antigravity CLI (agy) binary not found under ~/.local/bin or PATH.",
                task_id="ODP-ORCH-EXAMPLE-001",
                worker_run_id="agy-run-1",
                failure_kind="provider_unavailable",
                pause_kind="provider_unavailable",
            )

        self.assertTrue(paused)
        rotate.assert_not_called()
        entry = state["provider_guardrails"]["dispatch_pauses"]["antigravity5"]
        self.assertEqual(entry["pause_kind"], "provider_unavailable")
        # Finite, so reinstalling the CLI brings the lane back without manual
        # intervention -- and so a lane nobody fixes keeps re-announcing itself.
        self.assertGreaterEqual(entry["reset_after_seconds"], 60)
        self.assertTrue(entry["blocked_until"])

    def test_quota_on_the_same_provider_still_rotates(self) -> None:
        """Guard the other direction: the exclusion must not disable rotation."""

        state: dict = {}
        with (
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor.model_rotation, "record_exhaustion", return_value={"exhausted_pool": "gemini"}) as rotate,
        ):
            supervisor.mark_provider_dispatch_paused(
                self.ROTATING_CONFIG,
                state,
                "antigravity5",
                "Error: Individual quota reached. Please upgrade your subscription. Resets in 2h21m32s.",
                task_id="ODP-ORCH-EXAMPLE-001",
                worker_run_id="agy-run-2",
                failure_kind="quota_terminal",
                pause_kind="quota_terminal",
            )

        rotate.assert_called_once()

    def test_provider_unavailable_pause_seconds_has_a_default(self) -> None:
        settings = supervisor.provider_guardrail_settings({})
        self.assertGreaterEqual(int(settings["provider_unavailable_pause_seconds"]), 60)

    def test_mark_provider_dispatch_paused_honors_codex_retry_at(self) -> None:
        from datetime import datetime

        config = {
            "provider_guardrails": {"capacity_pause_seconds": 900, "quota_terminal_pause_seconds": 900},
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
        }
        state: dict = {}

        fake_now = datetime(2026, 4, 28, 3, 5, 0, tzinfo=UTC)
        with (
            mock.patch.object(supervisor, "datetime") as datetime_mock,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            datetime_mock.now.return_value = fake_now
            datetime_mock.side_effect = lambda *a, **kw: datetime(*a, **kw)
            supervisor.mark_provider_dispatch_paused(
                config,
                state,
                "codex",
                "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 7:00 PM.",
                task_id="SD-FND-003",
                worker_run_id="codex-run-1",
                failure_kind="quota_terminal",
                pause_kind="quota_terminal",
            )

        entry = state["provider_guardrails"]["dispatch_pauses"]["codex"]
        # 7pm Asia/Taipei = 11:00 UTC same day, far longer than the default 900s
        self.assertEqual(entry["blocked_until"], "2026-04-28T11:00:00Z")
        self.assertEqual(entry["pause_kind"], "quota_terminal")
        # reset_after_seconds should reflect the actual hint window, not the default
        self.assertGreater(entry["reset_after_seconds"], 900)
        self.assertEqual(entry["reset_after_seconds"], int((11 - 3) * 3600 - 5 * 60))

    def test_mark_provider_dispatch_paused_honors_codex_full_date_retry_at(self) -> None:
        from datetime import datetime

        config = {
            "provider_guardrails": {"capacity_pause_seconds": 900, "quota_terminal_pause_seconds": 900},
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
            "providers": {"codex2-3": {"quota_group": "codex2"}},
        }
        state: dict = {}

        fake_now = datetime(2026, 5, 16, 10, 5, 36, tzinfo=UTC)
        with (
            mock.patch.object(supervisor, "datetime") as datetime_mock,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            datetime_mock.now.return_value = fake_now
            datetime_mock.side_effect = lambda *a, **kw: datetime(*a, **kw)
            supervisor.mark_provider_dispatch_paused(
                config,
                state,
                "codex2-3",
                "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage "
                "to purchase more credits or try again at May 19th, 2026 12:40 AM.",
                task_id="TRN-002",
                worker_run_id="codex-run-1",
                failure_kind="quota_terminal",
                pause_kind="quota_terminal",
            )

        entry = state["provider_guardrails"]["dispatch_pauses"]["codex2"]
        self.assertEqual(entry["trigger_provider"], "codex2_3")
        self.assertEqual(entry["blocked_until"], "2026-05-18T16:40:00Z")
        self.assertEqual(entry["reset_after_seconds"], 196464)

    def test_mark_provider_dispatch_paused_caps_codex_retry_hint_when_configured(self) -> None:
        from datetime import datetime

        config = {
            "provider_guardrails": {
                "capacity_pause_seconds": 900,
                "quota_terminal_pause_seconds": 900,
                "quota_terminal_hint_max_seconds": 3600,
            },
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
            "providers": {"codex2-3": {"quota_group": "codex2"}},
        }
        state: dict = {}

        fake_now = datetime(2026, 5, 17, 20, 2, 2, tzinfo=UTC)
        with (
            mock.patch.object(supervisor, "datetime") as datetime_mock,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            datetime_mock.now.return_value = fake_now
            datetime_mock.side_effect = lambda *a, **kw: datetime(*a, **kw)
            supervisor.mark_provider_dispatch_paused(
                config,
                state,
                "codex2-3",
                "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage "
                "to purchase more credits or try again at May 19th, 2026 12:40 AM.",
                task_id="OODA-E2E-005",
                worker_run_id="codex-run-1",
                failure_kind="quota_terminal",
                pause_kind="quota_terminal",
            )

        entry = state["provider_guardrails"]["dispatch_pauses"]["codex2"]
        self.assertEqual(entry["blocked_until"], "2026-05-17T21:02:02Z")
        self.assertEqual(entry["hint_blocked_until"], "2026-05-18T16:40:00Z")
        self.assertTrue(entry["hint_capped"])
        self.assertEqual(entry["reset_after_seconds"], 3600)

    def test_mark_provider_dispatch_paused_uses_default_when_no_hint(self) -> None:
        from datetime import datetime

        config = {
            "provider_guardrails": {"capacity_pause_seconds": 900, "quota_terminal_pause_seconds": 900},
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
        }
        state: dict = {}

        fake_now = datetime(2026, 4, 28, 3, 5, 0, tzinfo=UTC)
        with (
            mock.patch.object(supervisor, "datetime") as datetime_mock,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            datetime_mock.now.return_value = fake_now
            datetime_mock.side_effect = lambda *a, **kw: datetime(*a, **kw)
            supervisor.mark_provider_dispatch_paused(
                config,
                state,
                "claude",
                "Credit balance is too low",
                failure_kind="quota_terminal",
                pause_kind="quota_terminal",
            )

        entry = state["provider_guardrails"]["dispatch_pauses"]["claude"]
        # 03:05Z + 900s = 03:20Z
        self.assertEqual(entry["blocked_until"], "2026-04-28T03:20:00Z")
        self.assertEqual(entry["reset_after_seconds"], 900)

    def test_codex_slot_pause_uses_shared_quota_group(self) -> None:
        config = {
            "provider_guardrails": {"capacity_pause_seconds": 900, "quota_terminal_pause_seconds": 900},
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
            "providers": {
                "codex1-1": {"delivery_mode": "codex", "quota_group": "codex1"},
                "codex1-2": {"delivery_mode": "codex", "quota_group": "codex1"},
            },
        }
        state: dict = {}

        with mock.patch.object(supervisor, "write_activity_log"):
            supervisor.mark_provider_dispatch_paused(
                config,
                state,
                "codex1-1",
                "status: 429 RESOURCE_EXHAUSTED",
                failure_kind="capacity_retryable",
                pause_kind="capacity_retryable",
            )

        pauses = state["provider_guardrails"]["dispatch_pauses"]
        self.assertIn("codex1", pauses)
        self.assertNotIn("codex1_1", pauses)
        self.assertEqual(pauses["codex1"]["trigger_provider"], "codex1_1")
        self.assertIs(supervisor.current_provider_dispatch_pause(state, "codex1-2", config), pauses["codex1"])

    def test_expire_provider_dispatch_pauses_removes_expired_entry(self) -> None:
        config = {
            "provider_guardrails": {"capacity_pause_seconds": 900, "quota_terminal_pause_seconds": 900},
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
        }
        state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "copilot": {
                        "provider": "copilot",
                        "blocked_until": "2026-04-06T12:00:00Z",
                        "pause_kind": "quota_terminal",
                        "task_id": "PKT-001",
                        "worker_run_id": "copilot-run",
                        "raw_ref": ".orchestrator/evidence/copilot.json",
                    }
                }
            }
        }

        with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
            changed = supervisor.expire_provider_dispatch_pauses(config, state)

        self.assertTrue(changed)
        self.assertEqual(state["provider_guardrails"]["dispatch_pauses"], {})
        write_activity_log.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "provider_dispatch_resumed")

    def test_expire_provider_dispatch_pauses_clears_quota_pause_after_account_switch(self) -> None:
        config = {
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
            "providers": {"codex": {"quota_group": "codex"}},
        }
        state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "codex": {
                        "provider": "codex",
                        "trigger_provider": "codex",
                        "blocked_until": "2999-01-01T00:00:00Z",
                        "pause_kind": "quota_terminal",
                        "auth_identity_hash": "old-account",
                    }
                }
            }
        }

        with (
            mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="new-account"),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.expire_provider_dispatch_pauses(config, state)

        self.assertTrue(changed)
        self.assertEqual(state["provider_guardrails"]["dispatch_pauses"], {})
        message = write_activity_log.call_args.args[1]["message"]
        self.assertIn("account identity changed", message)

    def test_clear_provider_dispatch_pause_removes_group_pause(self) -> None:
        config = {
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
            "providers": {"codex2-3": {"delivery_mode": "codex", "quota_group": "codex2"}},
        }
        state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "codex2": {
                        "task_id": "OODA-E2E-005",
                        "worker_run_id": "codex-run-1",
                        "raw_ref": ".orchestrator/evidence/codex.json",
                    }
                }
            }
        }

        with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
            changed = supervisor.clear_provider_dispatch_pause(config, state, "codex2-3")

        self.assertTrue(changed)
        self.assertEqual(state["provider_guardrails"]["dispatch_pauses"], {})
        write_activity_log.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "provider_dispatch_resumed")
        self.assertEqual(write_activity_log.call_args.args[1]["provider"], "codex2")


class ProcessQueueDispatchGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "status_field": "status",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {},
            "agents": {
                "codex": {
                    "id": "codex",
                    "name": "Codex",
                    "display_name": "Codex",
                    "provider": "codex",
                    "adapter": "codex",
                }
            },
            "providers": {
                "codex": {
                    "delivery_mode": "codex",
                }
            },
        }
        self.provider_report: dict[str, object] = {}
        # These tests exercise queue/lease guards, not network discovery. Keep
        # their synthetic repositories offline while still making the base an
        # explicit immutable SHA rather than restoring a mutable base_ref.
        self._base_patcher = mock.patch.object(
            supervisor,
            "resolve_worker_base",
            return_value=(mock.Mock(sha="a" * 40, remote_ref="origin/dev"), None),
        )
        self._base_patcher.start()
        self.addCleanup(self._base_patcher.stop)

    def test_prepare_worker_workspace_allocates_task_worktree_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            worktree_root = Path(tmpdir) / "workers"
            config = {
                **self.config,
                "paths": {"status_file": str(repo_root / "ai-status.json")},
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(worktree_root),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            state: dict[str, object] = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="OPS-WORKTREE-001",
                reason="owned_in_progress_dispatch",
            )

            with (
                mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=None),
                mock.patch.object(supervisor, "_branch_checked_out_in_root", return_value=False),
                mock.patch.object(supervisor, "_create_worker_worktree", return_value=(True, None)) as create_worktree,
                mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-1",
                    target_agent="Codex",
                )

        expected_path = worktree_root / "pantheon" / "ops-worktree-001"
        self.assertTrue(ok)
        self.assertIsNone(message)
        self.assertEqual(request.metadata["workspace_mode"], "isolated_worktree")
        self.assertEqual(request.metadata["workspace_path"], str(expected_path))
        self.assertEqual(request.metadata["workspace_branch"], "task/OPS-WORKTREE-001")
        self.assertEqual(request.metadata["status_root"], str(repo_root.resolve()))
        self.assertEqual(state["worker_worktrees"]["leases"]["OPS-WORKTREE-001"]["path"], str(expected_path))
        create_worktree.assert_called_once_with(repo_root.resolve(), expected_path, "task/OPS-WORKTREE-001", "a" * 40)
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_worktree_allocated")

    @staticmethod
    def _init_repo_with_origin(root: Path, origin_url: str) -> None:
        subprocess.run(["git", "init", str(root)], capture_output=True, check=True)
        subprocess.run(["git", "remote", "add", "origin", origin_url], cwd=root, check=True)

    def _external_repo_config(
        self,
        repo_root: Path,
        worktree_root: Path,
        local_path: Path,
        repo_id: str = "oday_data_platform",
        slug: str = "alfloop-dev/oday-data-platform",
    ) -> dict[str, object]:
        return {
            **self.config,
            "paths": {"status_file": str(repo_root / "ai-status.json")},
            "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
            "worker_worktrees": {
                "enabled": True,
                "root": str(worktree_root),
                "base_ref": "origin/dev",
                "reuse_existing": True,
            },
            "coordination": {
                "repositories": {repo_id: {"repo": slug, "local_path": str(local_path)}}
            },
        }

    def _external_repo_request(self, slug: str = "alfloop-dev/oday-data-platform"):
        request = supervisor.DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="wake",
            task_id="DPF-KRN-MEAS-001",
            reason="owned_in_progress_dispatch",
        )
        request.metadata["task"] = {"id": "DPF-KRN-MEAS-001", "repository": slug}
        return request

    def test_prepare_worker_workspace_routes_external_repository_task_to_its_checkout(self) -> None:
        # A data-platform task must never materialize its worktree (and therefore
        # its task branch) inside the supervisor repo: the branch would be pushed
        # to the wrong origin and every later ref check would fail closed. Those
        # assertions are load-bearing and unchanged.
        #
        # The `status_root` assertions below were changed, and separately: the
        # code repository and the board are two different things, and only the
        # worktree follows the code. Do not read the whole test as stale because
        # that line moved.
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            data_platform_root = Path(tmpdir) / "oday-data-platform-supervisor"
            worktree_root = Path(tmpdir) / "workers"
            repo_root.mkdir()
            self._init_repo_with_origin(repo_root, "https://github.com/alfloop-dev/odayplus.git")
            self._init_repo_with_origin(
                data_platform_root, "https://github.com/alfloop-dev/oday-data-platform.git"
            )

            config = self._external_repo_config(repo_root, worktree_root, data_platform_root)
            state: dict[str, object] = {}
            request = self._external_repo_request()

            with (
                mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=None),
                mock.patch.object(supervisor, "_branch_checked_out_in_root", return_value=False),
                mock.patch.object(supervisor, "_create_worker_worktree", return_value=(True, None)) as create_worktree,
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-1",
                    target_agent="Codex",
                )

        expected_path = worktree_root / "oday-data-platform" / "dpf-krn-meas-001"
        self.assertTrue(ok, message)
        self.assertIsNone(message)
        self.assertEqual(request.metadata["workspace_path"], str(expected_path))
        # The worktree goes to the task's repository; the status root does not.
        # This assertion previously pinned status_root to the data-platform
        # checkout, which has no ai-status.json and no scripts/ai-status.sh --
        # DPF tasks are recorded on the pantheon board. The wake prompt tells
        # workers to run `$PANTHEON_STATUS_ROOT/scripts/ai-status.sh`, so
        # pointing it at the code repository made every cross-repository worker
        # unable to report status.
        fleet_root = Path(config["paths"]["status_file"]).parents[0]
        self.assertEqual(request.metadata["status_root"], str(fleet_root))
        self.assertNotEqual(request.metadata["status_root"], str(data_platform_root.resolve()))
        lease = state["worker_worktrees"]["leases"]["DPF-KRN-MEAS-001"]
        self.assertEqual(lease["status_root"], str(fleet_root))
        self.assertEqual(lease["repo_root_source"], "repository:oday_data_platform")
        create_worktree.assert_called_once_with(
            data_platform_root.resolve(), expected_path, "task/DPF-KRN-MEAS-001", "a" * 40
        )

    def test_prepare_worker_workspace_blocks_when_external_checkout_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            self._init_repo_with_origin(repo_root, "https://github.com/alfloop-dev/odayplus.git")
            worktree_root = Path(tmpdir) / "workers"
            # A registry id without a hard-coded sibling fallback, so "missing"
            # cannot be silently satisfied by a checkout elsewhere on this host.
            config = self._external_repo_config(
                repo_root,
                worktree_root,
                Path(tmpdir) / "does-not-exist",
                repo_id="acme_widgets",
                slug="acme/widgets",
            )
            state: dict[str, object] = {}
            request = self._external_repo_request(slug="acme/widgets")

            with (
                mock.patch.object(supervisor, "_create_worker_worktree") as create_worktree,
                mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-1",
                    target_agent="Codex",
                )

        self.assertFalse(ok)
        self.assertIn("repository_checkout_unavailable", message)
        create_worktree.assert_not_called()
        self.assertEqual(
            write_activity_log.call_args.args[1]["type"], "dispatch_blocked_worktree_lease"
        )

    def test_worker_task_repo_root_rejects_checkout_pointing_at_another_origin(self) -> None:
        # The exact DPF-GOV-001 failure: a registry entry that resolves to the
        # supervisor's own checkout must fail closed, not silently win.
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            wrong_root = Path(tmpdir) / "wrong-checkout"
            repo_root.mkdir()
            self._init_repo_with_origin(repo_root, "https://github.com/alfloop-dev/odayplus.git")
            self._init_repo_with_origin(wrong_root, "https://github.com/alfloop-dev/odayplus.git")

            config = self._external_repo_config(
                repo_root,
                Path(tmpdir) / "workers",
                wrong_root,
                repo_id="acme_widgets",
                slug="acme/widgets",
            )
            resolved, source = supervisor.worker_task_repo_root(config, {"repository": "acme/widgets"})

        self.assertIsNone(resolved)
        self.assertIn("repository_checkout_mismatch", source)

    def test_worker_task_repo_root_keeps_supervisor_repo_for_its_own_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            self._init_repo_with_origin(repo_root, "git@github.com:alfloop-dev/odayplus.git")
            config = self._external_repo_config(
                repo_root, Path(tmpdir) / "workers", Path(tmpdir) / "unused"
            )

            resolved, source = supervisor.worker_task_repo_root(
                config, {"repository": "alfloop-dev/odayplus"}
            )
            fallback, fallback_source = supervisor.worker_task_repo_root(config, {})

        # The fleet's own repo resolves through the same registry as any other,
        # and `local_path: "."` anchors on the fleet root rather than whichever
        # rollout directory the code happens to be running from.
        self.assertEqual(resolved, repo_root.resolve())
        self.assertEqual(source, "repository:odayplus")
        self.assertEqual(fallback, repo_root.resolve())
        self.assertEqual(fallback_source, "repository:pantheon")

    def test_create_worker_worktree_refuses_to_delete_stale_foreign_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "repo"
            foreign_root = Path(tmpdir) / "foreign"
            stale_path = Path(tmpdir) / "workers" / "stale"
            repo_root.mkdir()
            foreign_root.mkdir()

            subprocess.run(["git", "init", str(repo_root)], capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "Supervisor Test"], cwd=repo_root, check=True)
            subprocess.run(["git", "config", "user.email", "test@supervisor.invalid"], cwd=repo_root, check=True)
            (repo_root / "README.md").write_text("base\\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "-b", "dev"], cwd=repo_root, check=True)

            subprocess.run(["git", "init", str(foreign_root)], capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "Supervisor Test"], cwd=foreign_root, check=True)
            subprocess.run(["git", "config", "user.email", "test@supervisor.invalid"], cwd=foreign_root, check=True)
            (foreign_root / "README.md").write_text("foreign\\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=foreign_root, check=True)
            subprocess.run(["git", "commit", "-m", "foreign base"], cwd=foreign_root, check=True)

            shutil.copytree(foreign_root, stale_path)

            ok, error = supervisor._create_worker_worktree(repo_root.resolve(), stale_path, "task/OPS-WORKTREE-001", "dev")
            self.assertFalse(ok)
            self.assertIn("not a registered worktree", error or "")
            self.assertTrue((stale_path / "README.md").exists())

    def test_worktree_clone_fallback_is_removed(self) -> None:
        self.assertFalse(hasattr(supervisor, "_create_worker_worktree_fallback"))

    def test_prepare_worker_workspace_skips_reused_worktree_with_mismatched_git_common_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            foreign_root = Path(tmpdir) / "foreign"
            foreign_root.mkdir()

            subprocess.run(["git", "init", str(repo_root)], capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "Supervisor Test"], cwd=repo_root, check=True)
            subprocess.run(["git", "config", "user.email", "test@supervisor.invalid"], cwd=repo_root, check=True)
            (repo_root / "README.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "-b", "dev"], cwd=repo_root, check=True)

            subprocess.run(["git", "init", str(foreign_root)], capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "Supervisor Test"], cwd=foreign_root, check=True)
            subprocess.run(["git", "config", "user.email", "test@supervisor.invalid"], cwd=foreign_root, check=True)
            (foreign_root / "README.md").write_text("foreign\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=foreign_root, check=True)
            subprocess.run(["git", "commit", "-m", "foreign base"], cwd=foreign_root, check=True)

            worktree_root = Path(tmpdir) / "workers"
            config = {
                **self.config,
                "paths": {"status_file": str(repo_root / "ai-status.json")},
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(worktree_root),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            state: dict[str, object] = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="OPS-WORKTREE-001",
                reason="owned_in_progress_dispatch",
            )

            with (
                mock.patch.object(
                    supervisor,
                    "_git_worktree_records",
                    return_value=[{"worktree": str(foreign_root), "branch": "task/OPS-WORKTREE-001"}],
                ),
                mock.patch.object(supervisor, "_branch_checked_out_in_root", return_value=False),
                mock.patch.object(supervisor, "_create_worker_worktree", return_value=(True, None)) as create_worktree,
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-1",
                    target_agent="Codex",
                )

        expected_path = worktree_root / "pantheon" / "ops-worktree-001"
        self.assertTrue(ok)
        self.assertIsNone(message)
        self.assertEqual(request.metadata["workspace_mode"], "isolated_worktree")
        self.assertEqual(request.metadata["workspace_path"], str(expected_path))
        create_worktree.assert_called_once_with(repo_root.resolve(), expected_path, "task/OPS-WORKTREE-001", "a" * 40)

    def test_review_dispatch_uses_task_branch_not_mainline_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            worktree_root = Path(tmpdir) / "workers"
            config = {
                **self.config,
                "paths": {"status_file": str(repo_root / "ai-status.json")},
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(worktree_root),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            state: dict[str, object] = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="review exact task head",
                task_id="ODP-PLAN-REVIEW-001",
                reason="review_ready_dispatch",
            )

            with (
                mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=None),
                mock.patch.object(supervisor, "_branch_checked_out_in_root", return_value=False),
                mock.patch.object(
                    supervisor,
                    "_create_worker_worktree",
                    return_value=(True, None),
                ) as create_worktree,
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-review",
                    target_agent="Codex",
                )

        expected_path = worktree_root / "pantheon" / "odp-plan-review-001"
        self.assertTrue(ok)
        self.assertIsNone(message)
        self.assertEqual(request.metadata["workspace_branch"], "task/ODP-PLAN-REVIEW-001")
        self.assertNotEqual(request.metadata["workspace_branch"], "dev")
        self.assertNotEqual(request.metadata["workspace_branch"], "main")
        create_worktree.assert_called_once_with(
            repo_root.resolve(),
            expected_path,
            "task/ODP-PLAN-REVIEW-001",
            "a" * 40,
        )


    def test_prepare_worker_workspace_materializes_task_brief_into_isolated_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            source_brief = repo_root / ".orchestrator" / "task-briefs" / "ops_brief_001.md"
            source_brief.parent.mkdir(parents=True)
            source_brief.write_text("# Source brief\n", encoding="utf-8")
            worktree_root = Path(tmpdir) / "workers"
            config = {
                **self.config,
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "activity-log.jsonl"),
                },
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(worktree_root),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            state: dict[str, object] = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="OPS-BRIEF-001",
                reason="owned_in_progress_dispatch",
                context_files=[".orchestrator/task-briefs/ops_brief_001.md"],
            )

            with (
                mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=None),
                mock.patch.object(supervisor, "_branch_checked_out_in_root", return_value=False),
                mock.patch.object(supervisor, "_create_worker_worktree", return_value=(True, None)),
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-brief",
                    target_agent="Codex",
                )

            self.assertTrue(ok)
            self.assertIsNone(message)
            copied_brief = Path(request.metadata["workspace_path"]) / ".orchestrator" / "task-briefs" / "ops_brief_001.md"
            self.assertEqual(copied_brief.read_text(encoding="utf-8"), "# Source brief\n")
            self.assertEqual(request.metadata["materialized_context_files"], [".orchestrator/task-briefs/ops_brief_001.md"])

    def test_refused_dirty_lease_writes_a_backup_once(self) -> None:
        """A refused lease must leave a backup, and only one per dirt-state.

        Preservation otherwise runs only from the paths that declare a worker
        dead, so dirt from a worker that exited cleanly -- or that predates the
        lease entirely -- was never backed up at all. On 2026-08-23 four
        worktrees sat blocked for four to seven hours holding 3608, 336, 44 and
        22 uncommitted files, and `worktree-dirt-backups/` had an entry for none
        of them.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root = tmp_p / "pantheon"
            repo_root.mkdir()
            (repo_root / "ai-status.json").write_text("{}", encoding="utf-8")
            for cmd in (
                ["git", "init", "-b", "dev"],
                ["git", "config", "user.name", "TestUser"],
                ["git", "config", "user.email", "test@example.com"],
            ):
                subprocess.run(cmd, cwd=repo_root, capture_output=True, check=True)
            (repo_root / "README.md").write_text("main\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo_root, capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_root, capture_output=True, check=True)
            subprocess.run(["git", "branch", "task/OPS-WORKTREE-001"], cwd=repo_root, capture_output=True, check=True)

            worktree_path = tmp_p / "workers" / "pantheon" / "ops-worktree-001"
            worktree_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "worktree", "add", str(worktree_path), "task/OPS-WORKTREE-001"],
                cwd=repo_root, capture_output=True, check=True,
            )
            (worktree_path / "unfinished.py").write_text("half a feature\n", encoding="utf-8")

            config = {
                **self.config,
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(tmp_p / "workers"),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            state: dict[str, object] = {}
            dirty = "skipped_dirty_worktree: 1 dirty change (1 untracked): unfinished.py"
            backups_dir = repo_root / ".orchestrator" / "worktree-dirt-backups"

            def lease():
                request = supervisor.DeliveryRequest(
                    agent_id="codex",
                    provider="codex",
                    delivery_mode="codex",
                    message="wake",
                    task_id="OPS-WORKTREE-001",
                    reason="owned_in_progress_dispatch",
                )
                with (
                    mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=worktree_path),
                    mock.patch.object(supervisor, "_refresh_reused_worker_worktree", return_value=(False, dirty)),
                ):
                    return supervisor.prepare_worker_workspace(
                        config, state, request, queue_event_id="evt-dirty", target_agent="Codex",
                    )

            ok, _ = lease()
            self.assertFalse(ok)
            self.assertEqual(
                1, len(list(backups_dir.glob("ops-worktree-001-*"))),
                "a refused dirty lease must leave exactly one backup",
            )
            # The dirt itself stays put, as it does at worker death.
            self.assertTrue((worktree_path / "unfinished.py").exists())

            ok_again, _ = lease()
            self.assertFalse(ok_again)
            self.assertEqual(
                1, len(list(backups_dir.glob("ops-worktree-001-*"))),
                "an unchanged dirt-state must not be backed up again on every retry",
            )

    def test_prepare_worker_workspace_blocks_dirty_reused_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            worktree_path = Path(tmpdir) / "workers" / "pantheon" / "ops-worktree-001"
            config = {
                **self.config,
                "paths": {"status_file": str(repo_root / "ai-status.json")},
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(Path(tmpdir) / "workers"),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            state: dict[str, object] = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="OPS-WORKTREE-001",
                reason="owned_in_progress_dispatch",
            )

            with (
                mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=worktree_path),
                mock.patch.object(
                    supervisor,
                    "_refresh_reused_worker_worktree",
                    return_value=(False, "skipped_dirty_worktree: 1 dirty change (1 unstaged tracked): services/app.py"),
                ) as refresh_worktree,
                mock.patch.object(supervisor, "_create_worker_worktree") as create_worktree,
                mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-dirty",
                    target_agent="Codex",
                )

        self.assertFalse(ok)
        assert message is not None
        self.assertIn("1 dirty change (1 unstaged tracked): services/app.py", message)
        self.assertNotIn("dirty tracked or staged changes", message)
        self.assertNotIn("workspace_path", request.metadata)
        self.assertNotIn("worker_worktrees", state)
        refresh_worktree.assert_called_once()
        create_worktree.assert_not_called()
        self.assertEqual(
            [call.args[1]["type"] for call in write_activity_log.call_args_list],
            ["worker_worktree_refreshed", "dispatch_blocked_worktree_lease"],
        )
        self.assertEqual(write_activity_log.call_args_list[-1].args[1]["refresh_status"], "skipped_dirty_worktree: 1 dirty change (1 unstaged tracked): services/app.py")

    def test_prepare_worker_workspace_preserves_dirty_worktree_and_blocks_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            worktree_path = Path(tmpdir) / "workers" / "pantheon" / "ops-worktree-001"
            config = {
                **self.config,
                "paths": {"status_file": str(repo_root / "ai-status.json")},
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(Path(tmpdir) / "workers"),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            state: dict = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="OPS-WORKTREE-RECOVER-001",
                reason="owned_in_progress_dispatch",
            )

            with (
                mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=worktree_path),
                mock.patch.object(
                    supervisor,
                    "_refresh_reused_worker_worktree",
                    return_value=(False, "skipped_dirty_worktree"),
                ) as refresh_worktree,
                mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-recover",
                    target_agent="Codex",
                )

        self.assertFalse(ok)
        self.assertIn("Preserve and commit", message or "")
        self.assertEqual(refresh_worktree.call_count, 1)
        self.assertEqual(
            [call.args[1]["type"] for call in write_activity_log.call_args_list],
            ["worker_worktree_refreshed", "dispatch_blocked_worktree_lease"],
        )


    def test_prepare_worker_workspace_blocks_interrupted_merge_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            worktree_path = Path(tmpdir) / "workers" / "pantheon" / "ops-worktree-merge-001"
            config = {
                **self.config,
                "paths": {"status_file": str(repo_root / "ai-status.json")},
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(Path(tmpdir) / "workers"),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            state: dict = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="OPS-WORKTREE-MERGE-001",
                reason="owned_in_progress_dispatch",
            )

            with (
                mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=worktree_path),
                mock.patch.object(
                    supervisor,
                    "_refresh_reused_worker_worktree",
                    return_value=(False, "unresolved_git_operation"),
                ) as refresh_worktree,
                mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-merge-jam",
                    target_agent="Codex",
                )

        self.assertFalse(ok)
        self.assertIn("unresolved_git_operation", message or "")
        self.assertEqual(refresh_worktree.call_count, 1)
        self.assertEqual(
            [call.args[1]["type"] for call in write_activity_log.call_args_list],
            ["worker_worktree_refreshed", "dispatch_blocked_worktree_lease"],
        )

    def test_prepare_worker_workspace_blocks_every_other_unsafe_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            worktree_path = Path(tmpdir) / "workers" / "pantheon" / "ops-worktree-unsafe-001"
            config = {
                **self.config,
                "paths": {"status_file": str(repo_root / "ai-status.json")},
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(Path(tmpdir) / "workers"),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="OPS-WORKTREE-UNSAFE-001",
                reason="owned_in_progress_dispatch",
            )

            with (
                mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=worktree_path),
                mock.patch.object(
                    supervisor,
                    "_refresh_reused_worker_worktree",
                    return_value=(False, "task_head_mismatch: local=a remote=b"),
                ),
                mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    {},
                    request,
                    queue_event_id="evt-unsafe",
                    target_agent="Codex",
                )

        self.assertFalse(ok)
        self.assertIn("fail-closed refresh policy", message or "")
        self.assertEqual(write_activity_log.call_args_list[-1].args[1]["type"], "dispatch_blocked_worktree_lease")

    def test_prepare_worker_workspace_prompts_owner_to_advance_diverged_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            worktree_path = Path(tmpdir) / "workers" / "pantheon" / "ops-worktree-rebase-001"
            config = {
                **self.config,
                "paths": {"status_file": str(repo_root / "ai-status.json")},
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(Path(tmpdir) / "workers"),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            state: dict[str, object] = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="original owner dispatch",
                task_id="OPS-WORKTREE-REBASE-001",
                reason="owned_in_progress_dispatch",
            )
            refresh_status = "base_advance_rebase_required:local=" + "a" * 40 + ",base=" + "b" * 40

            with (
                mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=worktree_path),
                mock.patch.object(
                    supervisor,
                    "_refresh_reused_worker_worktree",
                    return_value=(True, refresh_status),
                ),
                mock.patch.object(supervisor, "materialize_worker_context_files", return_value=[]),
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-rebase",
                    target_agent="Codex",
                )

        self.assertTrue(ok)
        self.assertIsNone(message)
        self.assertTrue(request.metadata["base_advance_required"])
        self.assertEqual(request.metadata["worktree_refresh_status"], refresh_status)
        self.assertIn("BASE ADVANCE REQUIRED BEFORE REVIEW OR MERGE", request.message)
        self.assertIn("Compose the current base", request.message)
        self.assertTrue(request.message.endswith("original owner dispatch"))

    def test_prepare_finalize_workspace_never_prompts_to_mutate_approved_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            worktree_path = Path(tmpdir) / "workers" / "pantheon" / "finalize-001"
            config = {
                **self.config,
                "paths": {"status_file": str(repo_root / "ai-status.json")},
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(Path(tmpdir) / "workers"),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="immutable finalize",
                task_id="FINALIZE-001",
                reason="owned_finalize_dispatch",
            )
            refresh_status = "base_advance_rebase_required:local=" + "a" * 40 + ",base=" + "b" * 40

            with (
                mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=worktree_path),
                mock.patch.object(supervisor, "_refresh_reused_worker_worktree", return_value=(True, refresh_status)),
                mock.patch.object(supervisor, "materialize_worker_context_files", return_value=[]),
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    {},
                    request,
                    queue_event_id="evt-finalize",
                    target_agent="Codex",
                )

        self.assertTrue(ok)
        self.assertIsNone(message)
        self.assertEqual(request.message, "immutable finalize")
        self.assertNotIn("base_advance_required", request.metadata)
        self.assertTrue(request.metadata["approved_head_immutable"])
        self.assertTrue(request.metadata["base_advance_deferred_to_merge_queue"])

    def test_process_queue_checks_worker_guard_inside_isolated_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            workspace = Path(tmpdir) / "workers" / "pantheon" / "bus-val-004"
            repo_root.mkdir()
            workspace.mkdir(parents=True)
            config = {
                **self.config,
                "paths": {"status_file": str(repo_root / "ai-status.json")},
                "worker_worktrees": {"enabled": True, "root": str(workspace.parent.parent)},
            }
            current_task = {
                "id": "BUS-VAL-004",
                "status": "in_progress",
                "owner": "Codex",
                "reviewer": "Gemini",
                "depends_on": [],
                "last_update": "2026-04-05T14:54:01Z",
            }
            queue_payload = {
                "event_id": "evt-current",
                "task_id": "BUS-VAL-004",
                "target_agent": "codex",
                "target_display_name": "Codex",
                "provider": "codex",
                "reason": "owned_in_progress_dispatch",
                "message": "wake",
            }
            state = {"queue": {"events": {}}, "workers": {}}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="BUS-VAL-004",
                reason="owned_in_progress_dispatch",
            )

            def prepare_workspace(_config, _state, prepared_request, **_kwargs):
                prepared_request.metadata.update(
                    {
                        "workspace_path": str(workspace),
                        "workspace_branch": "task/BUS-VAL-004",
                        "workspace_mode": "isolated_worktree",
                        "status_root": str(repo_root.resolve()),
                    }
                )
                return True, None

            with (
                mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
                mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
                mock.patch.object(supervisor, "build_request", return_value=request),
                mock.patch.object(supervisor, "prepare_worker_workspace", side_effect=prepare_workspace),
                mock.patch.object(supervisor, "start_worker_for_request", return_value=(True, "run-123", {"manual_confirmation_required": False, "auto_delivered": True})),
                mock.patch.object(supervisor, "sync_dispatched_task_status", return_value=True),
            ):
                changed = supervisor.process_queue(config, state, self.provider_report)

        self.assertTrue(changed)

    def test_process_queue_isolates_workspace_exception_and_starts_next_event(self) -> None:
        tasks = [
            {
                "id": task_id,
                "status": "in_progress",
                "owner": "Codex",
                "reviewer": "Reviewer",
                "depends_on": [],
            }
            for task_id in ("BAD-001", "GOOD-001")
        ]
        events = [
            {
                "event_id": event_id,
                "task_id": task_id,
                "target_agent": "codex",
                "target_display_name": "Codex",
                "provider": "codex",
                "reason": "owned_in_progress_dispatch",
                "message": "wake",
            }
            for event_id, task_id in (("evt-bad", "BAD-001"), ("evt-good", "GOOD-001"))
        ]
        state = {"queue": {"events": {}}, "workers": {}}

        def prepare_workspace(_config, _state, request, **_kwargs):
            if request.task_id == "BAD-001":
                raise ValueError("missing required source document")
            request.metadata["workspace_path"] = "/tmp/good-workspace"
            return True, None

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=events),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": tasks}),
            mock.patch.object(supervisor, "select_dispatch_agent_id", return_value="codex"),
            mock.patch.object(supervisor, "prepare_worker_workspace", side_effect=prepare_workspace),
            mock.patch.object(
                supervisor,
                "start_worker_for_request",
                return_value=(True, "run-good", {"manual_confirmation_required": False, "auto_delivered": True}),
            ) as start_worker,
            mock.patch.object(supervisor, "sync_dispatched_task_status", return_value=True),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        self.assertEqual(state["queue"]["events"]["evt-bad"]["status"], "failed")
        self.assertIn("missing required source document", state["queue"]["events"]["evt-bad"]["error"])
        self.assertEqual(state["queue"]["events"]["evt-good"]["status"], "started")
        start_worker.assert_called_once()
        failure_events = [
            call.args[1]
            for call in write_activity_log.call_args_list
            if call.args[1].get("type") == "wake_failed"
        ]
        self.assertEqual(failure_events[0]["task_id"], "BAD-001")

    def test_process_queue_preflight_failure_releases_queue_for_next_task(self) -> None:
        events = [
            {
                "event_id": event_id,
                "task_id": task_id,
                "target_agent": "codex",
                "target_display_name": "Codex",
                "provider": "codex",
                "reason": "owned_in_progress_dispatch",
                "message": "wake",
            }
            for event_id, task_id in (("evt-blocked", "BLOCKED-001"), ("evt-ready", "READY-001"))
        ]
        tasks = [
            {"id": event["task_id"], "status": "in_progress", "owner": "Codex", "reviewer": "Reviewer", "depends_on": []}
            for event in events
        ]
        state = {"queue": {"events": {}}, "workers": {}}

        def prepare_workspace(_config, _state, request, **_kwargs):
            if request.task_id == "BLOCKED-001":
                return False, "task_head_mismatch: local and remote histories diverged"
            return True, None

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=events),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": tasks}),
            mock.patch.object(supervisor, "select_dispatch_agent_id", return_value="codex"),
            mock.patch.object(supervisor, "prepare_worker_workspace", side_effect=prepare_workspace),
            mock.patch.object(
                supervisor,
                "start_worker_for_request",
                return_value=(True, "run-ready", {"manual_confirmation_required": False, "auto_delivered": True}),
            ) as start_worker,
            mock.patch.object(supervisor, "sync_dispatched_task_status", return_value=True),
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            self.assertTrue(supervisor.process_queue(self.config, state, self.provider_report))

        self.assertEqual(state["queue"]["events"]["evt-blocked"]["status"], "failed")
        self.assertIn("preflight blocked", state["queue"]["events"]["evt-blocked"]["error"])
        self.assertEqual(state["queue"]["events"]["evt-ready"]["status"], "started")
        start_worker.assert_called_once()

    def test_process_queue_isolates_request_construction_exception(self) -> None:
        events = [
            {
                "event_id": event_id,
                "task_id": task_id,
                "target_agent": "codex",
                "target_display_name": "Codex",
                "provider": "codex",
                "reason": "owned_in_progress_dispatch",
                "message": "wake",
            }
            for event_id, task_id in (("evt-bad", "BAD-REQUEST"), ("evt-good", "GOOD-REQUEST"))
        ]
        tasks = [
            {
                "id": event["task_id"],
                "status": "in_progress",
                "owner": "Codex",
                "reviewer": "Reviewer",
            }
            for event in events
        ]

        def build_request(_config, event, **_kwargs):
            if event["task_id"] == "BAD-REQUEST":
                raise ValueError("invalid task source metadata")
            return supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="GOOD-REQUEST",
                reason="owned_in_progress_dispatch",
            )

        state = {"queue": {"events": {}}, "workers": {}}
        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=events),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": tasks}),
            mock.patch.object(supervisor, "build_request", side_effect=build_request),
            mock.patch.object(supervisor, "select_dispatch_agent_id", return_value="codex"),
            mock.patch.object(supervisor, "prepare_worker_workspace", return_value=(True, None)),
            mock.patch.object(
                supervisor,
                "start_worker_for_request",
                return_value=(True, "run-good", {"manual_confirmation_required": False, "auto_delivered": True}),
            ) as start_worker,
            mock.patch.object(supervisor, "sync_dispatched_task_status", return_value=True),
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        self.assertEqual(state["queue"]["events"]["evt-bad"]["status"], "failed")
        self.assertIn("invalid task source metadata", state["queue"]["events"]["evt-bad"]["error"])
        self.assertEqual(state["queue"]["events"]["evt-good"]["status"], "started")
        start_worker.assert_called_once()

    def test_queue_dispatch_event_safely_contains_source_metadata_error(self) -> None:
        event = {"task_id": "BAD-EVENT", "target_agent": "Antigravity"}
        with (
            mock.patch.object(
                supervisor,
                "queue_delivery_event",
                side_effect=ValueError("missing source document"),
            ),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            queued = supervisor.queue_dispatch_event_safely(self.config, event)

        self.assertFalse(queued)
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "dispatch_event_rejected")
        self.assertIn("missing source document", write_activity_log.call_args.args[1]["message"])

    def test_build_request_uses_provider_model_preference_for_helper_agent(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "agents": {
                "helper": {
                    "id": "helper",
                    "display_name": "Helper",
                    "provider": "helper",
                    "adapter": "helper",
                }
            },
            "providers": {
                "helper": {
                    "delivery_mode": "helper",
                    "model_preference": {
                        "helper": "helper3-coder-plus",
                    },
                }
            },
        }

        request = supervisor.build_request(
            config,
            {
                "target_agent": "helper",
                "message": "wake",
            },
        )

        self.assertEqual(request.agent_id, "helper")
        self.assertEqual(request.provider, "helper")
        self.assertEqual(request.metadata["model_preference"], "helper3-coder-plus")

    def test_build_request_skips_default_model_for_primary_copilot_agent(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "agents": {
                "copilot": {
                    "id": "copilot",
                    "display_name": "Copilot",
                    "provider": "copilot",
                    "adapter": "copilot_local",
                }
            },
            "providers": {
                "copilot": {
                    "delivery_mode": "copilot_local",
                    "model_preference": {
                        "default": None,
                        "grok": "grok-code-fast-1",
                    },
                }
            },
        }

        request = supervisor.build_request(
            config,
            {
                "target_agent": "copilot",
                "message": "wake",
            },
        )

        self.assertEqual(request.agent_id, "copilot")
        self.assertEqual(request.provider, "copilot")
        self.assertNotIn("model_preference", request.metadata)

    def test_build_request_keeps_agent_specific_model_for_copilot_alias(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "agents": {
                "grok": {
                    "id": "grok",
                    "display_name": "Copilot (legacy alias)",
                    "provider": "copilot",
                    "adapter": "copilot_local",
                }
            },
            "providers": {
                "copilot": {
                    "delivery_mode": "copilot_local",
                    "model_preference": {
                        "default": None,
                        "grok": "grok-code-fast-1",
                    },
                }
            },
        }

        request = supervisor.build_request(
            config,
            {
                "target_agent": "grok",
                "message": "wake",
            },
        )

        self.assertEqual(request.agent_id, "grok")
        self.assertEqual(request.provider, "copilot")
        self.assertEqual(request.metadata["model_preference"], "grok-code-fast-1")
        self.assertEqual(request.metadata["logical_agent_id"], "grok")
        self.assertEqual(request.metadata["target_display_name"], "Copilot (legacy alias)")

    def test_build_request_can_target_codex_worker_slot_with_logical_identity(self) -> None:
        config = {
            "agents": {
                "codex": {
                    "id": "codex",
                    "display_name": "Codex",
                    "provider": "codex",
                    "adapter": "codex",
                    "worker_slots": ["codex1_1", "codex1_2"],
                },
                "codex1_1": {
                    "id": "codex1_1",
                    "display_name": "Codex",
                    "provider": "codex1-1",
                    "adapter": "codex",
                    "dispatch_slot_for": "codex",
                    "slot_id": "codex1-1",
                },
                "codex1_2": {
                    "id": "codex1_2",
                    "display_name": "Codex",
                    "provider": "codex1-2",
                    "adapter": "codex",
                    "dispatch_slot_for": "codex",
                    "slot_id": "codex1-2",
                },
            },
            "providers": {
                "codex": {"delivery_mode": "codex", "quota_group": "codex1"},
                "codex1-1": {"delivery_mode": "codex", "quota_group": "codex1"},
                "codex1-2": {"delivery_mode": "codex", "quota_group": "codex1"},
            },
        }

        request = supervisor.build_request(
            config,
            {
                "target_agent": "codex",
                "target_display_name": "Codex",
                "message": "wake",
                "task_id": "BFF-CONSOL-011",
                "context_files": [],
            },
            agent_id_override="codex1_2",
        )

        self.assertEqual(request.agent_id, "codex1_2")
        # The slot identifies process capacity; the logical target owns the
        # provider identity used by the adapter.
        self.assertEqual(request.provider, "codex")
        self.assertEqual(request.metadata["logical_agent_id"], "codex")
        self.assertEqual(request.metadata["dispatch_slot_id"], "codex1_2")
        self.assertEqual(request.metadata["dispatch_slot"], "codex1-2")
        self.assertEqual(request.metadata["target_display_name"], "Codex")

    def test_antigravity_logical_provider_survives_shared_slot_adapter_launch(self) -> None:
        from adapters.antigravity import AntigravityAdapter

        with tempfile.TemporaryDirectory(prefix="pantheon-agy-provider-authority-") as tmpdir:
            root = Path(tmpdir)
            logical_home = root / "ag2-home"
            slot_home = root / "ag-home"
            workspace = root / "workspace"
            config = {
                "agents": {
                    "antigravity2": {
                        "id": "antigravity2",
                        "display_name": "Antigravity2",
                        "provider": "antigravity2",
                        "adapter": "antigravity",
                        "account_pool": "antigravity_main",
                        "worker_slots": ["antigravity_slot_1"],
                    },
                    "antigravity_slot_1": {
                        "id": "antigravity_slot_1",
                        "display_name": "antigravity_slot_1",
                        "provider": "antigravity",
                        "adapter": "antigravity",
                        "account_pool": "antigravity_main",
                        "dispatch_slot_for_pool": "antigravity_main",
                        "slot_id": "antigravity_slot_1",
                    },
                },
                "providers": {
                    "antigravity": {
                        "delivery_mode": "antigravity",
                        "antigravity": {
                            "cli": "agy",
                            "config_home": str(slot_home),
                            "model_rotation": {
                                "enabled": True,
                                "primary_model": "gemini-slot-model",
                                "fallback_model": "claude-slot-model",
                            },
                        },
                    },
                    "antigravity2": {
                        "delivery_mode": "antigravity",
                        "antigravity": {
                            "cli": "agy",
                            "config_home": str(logical_home),
                            "model_rotation": {
                                "enabled": True,
                                "primary_model": "gemini-logical-model",
                                "fallback_model": "claude-logical-model",
                            },
                            "model_policy": {
                                "enabled": True,
                                "standard_model": "gemini-logical-model",
                            },
                        },
                    },
                },
            }
            event = {
                "target_agent": "antigravity2",
                "target_display_name": "Antigravity2",
                "message": "wake",
                "task_id": "ORCH-AGY-LOGICAL-PROVIDER-AUTHORITY-TEST",
                "reason": "owned_ready_dispatch",
                "context_files": [],
            }
            request = supervisor.build_request(
                config,
                event,
                agent_id_override="antigravity_slot_1",
            )

            self.assertEqual(request.agent_id, "antigravity_slot_1")
            self.assertEqual(request.provider, "antigravity2")
            self.assertEqual(request.delivery_mode, "antigravity")
            self.assertEqual(request.metadata["logical_agent_id"], "antigravity2")

            process = mock.Mock(pid=4321)
            previous_state_path = supervisor.model_rotation._STATE_PATH
            supervisor.model_rotation._STATE_PATH = root / "model-cooldown.json"
            try:
                with (
                    mock.patch(
                        "adapters.antigravity.configured_provider_binary",
                        return_value="/usr/bin/agy",
                    ),
                    mock.patch("adapters.antigravity._auth_ready", return_value=True) as auth_ready,
                    mock.patch(
                        "adapters.antigravity.delivery_workspace_root",
                        return_value=workspace,
                    ),
                    mock.patch(
                        "adapters.base.runtime_log_path",
                        return_value=root / "agy.log",
                    ),
                    mock.patch(
                        "adapters.base.new_runtime_id",
                        return_value="antigravity2-test",
                    ),
                    mock.patch(
                        "adapters.base.worker_runtime_paths",
                        return_value={
                            "heartbeat_path": root / "heartbeat.json",
                            "status_path": root / "runner.json",
                        },
                    ),
                    mock.patch(
                        "adapters.base.spawn_background_process",
                        return_value=(process, root / "agy.log"),
                    ) as spawn,
                ):
                    adapter = AntigravityAdapter(config=config, provider_capabilities={})
                    first = adapter.deliver(request)

                    self.assertTrue(first.ok)
                    self.assertEqual(first.metadata[supervisor.model_rotation.WORKER_POOL_KEY], "gemini")
                    self.assertEqual(first.metadata[supervisor.model_rotation.WORKER_MODEL_KEY], "gemini-logical-model")
                    self.assertEqual(auth_ready.call_args.args, (config, "antigravity2"))
                    first_env = spawn.call_args.kwargs["env"]
                    self.assertEqual(first_env["HOME"], str(logical_home))
                    self.assertEqual(first_env["ORCH_PROVIDER"], "antigravity2")
                    self.assertEqual(first_env["ORCH_AGENT_ID"], "antigravity_slot_1")

                    supervisor.model_rotation.record_exhaustion(
                        config,
                        request.provider,
                        900,
                        pool="gemini",
                    )
                    scope = supervisor.model_rotation.cooldown_scope(config, request.provider)
                    slot_scope = supervisor.model_rotation.cooldown_scope(config, "antigravity")
                    self.assertNotEqual(scope, slot_scope)
                    cooldown = json.loads((root / "model-cooldown.json").read_text(encoding="utf-8"))
                    self.assertIn(scope, cooldown)
                    self.assertNotIn(slot_scope, cooldown)

                    second = adapter.deliver(request)
                    self.assertTrue(second.ok)
                    self.assertEqual(second.metadata[supervisor.model_rotation.WORKER_POOL_KEY], "claude")
                    self.assertEqual(second.metadata[supervisor.model_rotation.WORKER_MODEL_KEY], "claude-logical-model")
            finally:
                supervisor.model_rotation._STATE_PATH = previous_state_path

    def test_start_worker_record_keeps_logical_provider_and_model_metadata(self) -> None:
        from adapters.base import DeliveryResult

        config = {
            "agents": {
                "antigravity2": {
                    "id": "antigravity2",
                    "provider": "antigravity2",
                    "adapter": "antigravity",
                    "account_pool": "antigravity_main",
                },
                "antigravity_slot_1": {
                    "id": "antigravity_slot_1",
                    "provider": "antigravity",
                    "adapter": "antigravity",
                    "account_pool": "antigravity_main",
                },
            },
            "providers": {
                "antigravity2": {"delivery_mode": "antigravity"},
                "antigravity": {"delivery_mode": "antigravity"},
            },
        }
        request = supervisor.build_request(
            config,
            {
                "target_agent": "antigravity2",
                "message": "wake",
                "task_id": "ORCH-AGY-LOGICAL-PROVIDER-AUTHORITY-RECORD-TEST",
                "context_files": [],
            },
            agent_id_override="antigravity_slot_1",
        )
        result = DeliveryResult(
            ok=True,
            adapter="antigravity",
            mode="antigravity",
            target="Antigravity2",
            auto_delivered=True,
            manual_confirmation_required=False,
            run_id="agy-logical-record-test",
            metadata={
                supervisor.model_rotation.WORKER_POOL_KEY: "gemini",
                supervisor.model_rotation.WORKER_MODEL_KEY: "gemini-logical-model",
            },
        )
        state: dict[str, Any] = {"workers": {}}
        adapter = mock.Mock()
        adapter.deliver.return_value = result
        with (
            mock.patch.object(supervisor, "build_adapter", return_value=adapter),
            mock.patch.object(supervisor, "save_runtime_state"),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "record_worker_runtime_measurement"),
        ):
            ok, run_id, _ = supervisor.start_worker_for_request(
                config,
                state,
                {},
                request,
                queue_event_id="evt-logical-record",
                attempt_count=1,
                event_id_for_log="evt-logical-record",
            )

        self.assertTrue(ok)
        self.assertEqual(run_id, "agy-logical-record-test")
        worker = state["workers"][run_id]
        self.assertEqual(worker["agent_id"], "antigravity_slot_1")
        self.assertEqual(worker["logical_agent_id"], "antigravity2")
        self.assertEqual(worker["provider"], "antigravity2")
        self.assertEqual(worker["quota_group"], "antigravity_main")
        self.assertEqual(worker["metadata"][supervisor.model_rotation.WORKER_MODEL_KEY], "gemini-logical-model")

    def test_select_dispatch_agent_id_chooses_free_codex_slot(self) -> None:
        config = {
            "agents": {
                "codex": {
                    "id": "codex",
                    "display_name": "Codex",
                    "provider": "codex",
                    "adapter": "codex",
                    "worker_slots": ["codex1_1", "codex1_2"],
                },
                "codex1_1": {
                    "id": "codex1_1",
                    "display_name": "Codex",
                    "provider": "codex1-1",
                    "adapter": "codex",
                    "dispatch_slot_for": "codex",
                },
                "codex1_2": {
                    "id": "codex1_2",
                    "display_name": "Codex",
                    "provider": "codex1-2",
                    "adapter": "codex",
                    "dispatch_slot_for": "codex",
                },
            },
            "providers": {
                "codex1-1": {"delivery_mode": "codex", "quota_group": "codex1"},
                "codex1-2": {"delivery_mode": "codex", "quota_group": "codex1"},
            },
        }
        state = {
            "workers": {
                "run-1": {
                    "agent_id": "codex1_1",
                    "provider": "codex1-1",
                    "status": "running",
                }
            }
        }

        selected = supervisor.select_dispatch_agent_id(config, state, "codex", {"running"})

        self.assertEqual(selected, "codex1_2")

    def test_skips_stale_owned_dispatch_event_after_task_completion(self) -> None:
        queued_task = {
            "id": "BUS-VAL-001",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-05T11:45:16Z",
        }
        queued_event = supervisor.build_dispatch_event(
            queued_task,
            "Codex",
            "owned_in_progress_dispatch",
            {"BUS-VAL-001": queued_task},
        )
        queue_payload = {
            "event_id": "evt-stale",
            "event_key": queued_event["key"],
            "task_id": "BUS-VAL-001",
            "target_agent": "codex",
            "target_display_name": "Codex",
            "reason": "owned_in_progress_dispatch",
            "message": "wake",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        current_status = {
            "tasks": [
                {
                    **queued_task,
                    "status": "done",
                    "last_update": "2026-04-05T12:00:00Z",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value=current_status),
            mock.patch.object(supervisor, "start_worker_for_request", side_effect=AssertionError("stale event should not start a worker")),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-stale"]
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["skip_reason"], "stale_dispatch_event")
        self.assertIn("processed_at", record)
        write_activity_log.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "wake_skipped")

    def test_starts_current_owned_dispatch_event(self) -> None:
        current_task = {
            "id": "BUS-VAL-004",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-05T14:54:01Z",
        }
        current_event = supervisor.build_dispatch_event(
            current_task,
            "Codex",
            "owned_in_progress_dispatch",
            {"BUS-VAL-004": current_task},
        )
        queue_payload = {
            "event_id": "evt-current",
            "event_key": current_event["key"],
            "task_id": "BUS-VAL-004",
            "target_agent": "codex",
            "target_display_name": "Codex",
            "reason": "owned_in_progress_dispatch",
            "message": "wake",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        request = object()
        delivery = {"manual_confirmation_required": False, "auto_delivered": True}

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
            mock.patch.object(supervisor, "build_request", return_value=request) as build_request,
            mock.patch.object(supervisor, "prepare_worker_workspace", return_value=(True, None)),
            mock.patch.object(supervisor, "start_worker_for_request", return_value=(True, "run-123", delivery)) as start_worker,
            mock.patch.object(supervisor, "sync_dispatched_task_status", return_value=True) as sync_dispatched_task_status,
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-current"]
        self.assertEqual(record["status"], "started")
        self.assertEqual(record["run_id"], "run-123")
        build_request.assert_called_once_with(self.config, queue_payload)
        start_worker.assert_called_once()
        sync_dispatched_task_status.assert_called_once_with(self.config, queue_payload)

    def test_failed_auto_lane_dispatch_does_not_create_manual_pending_worker(self) -> None:
        current_task = {
            "id": "BUS-VAL-005",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-13T14:20:00Z",
        }
        queue_payload = {
            "event_id": "evt-failed-auto",
            "task_id": "BUS-VAL-005",
            "target_agent": "codex",
            "target_display_name": "Codex",
            "provider": "codex",
            "reason": "owned_in_progress_dispatch",
            "message": "wake",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        request = supervisor.DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="wake",
            task_id="BUS-VAL-005",
            reason="owned_in_progress_dispatch",
        )

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
            mock.patch.object(supervisor, "build_request", return_value=request),
            mock.patch.object(supervisor, "prepare_worker_workspace", return_value=(True, None)),
            mock.patch.object(supervisor, "start_worker_for_request", return_value=(False, "CLI auth unavailable", None)),
            mock.patch.object(supervisor, "classify_worker_failure", return_value={"kind": "auth", "label": "authentication"}),
            mock.patch.object(supervisor, "summarize_failure_reason", return_value={"summary": "CLI auth unavailable", "kind": "auth"}),
            mock.patch.object(supervisor, "write_failure_evidence", return_value=None),
            mock.patch.object(supervisor, "record_task_failure_streak", return_value=1),
            mock.patch.object(supervisor, "mark_provider_dispatch_paused", return_value=True) as mark_provider_dispatch_paused,
            mock.patch.object(supervisor, "maybe_reassign_task_after_worker_failure", return_value=None),
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-failed-auto"]
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["error"], "CLI auth unavailable")
        self.assertEqual(state["workers"], {})
        mark_provider_dispatch_paused.assert_called_once()

    def test_process_queue_skips_not_auto_ready_provider_without_starting_worker(self) -> None:
        current_task = {
            "id": "BUS-VAL-005B",
            "status": "review",
            "owner": "Codex",
            "reviewer": "Claude2",
            "depends_on": [],
            "last_update": "2026-04-13T14:20:00Z",
        }
        current_event = supervisor.build_dispatch_event(
            current_task,
            "Claude2",
            "review_ready_dispatch",
            {"BUS-VAL-005B": current_task},
        )
        queue_payload = {
            "event_id": "evt-not-ready",
            "event_key": current_event["key"],
            "task_id": "BUS-VAL-005B",
            "target_agent": "claude2",
            "target_display_name": "Claude2",
            "provider": "claude2",
            "reason": "review_ready_dispatch",
            "message": "wake",
            "context_files": [],
        }
        provider_report = {
            "agent_adapters": {
                "claude2": {
                    "supported": True,
                    "can_auto_deliver": False,
                    "notes": "Claude CLI is installed but not authenticated.",
                }
            },
            "providers": {"claude2": {"auth_ready": False}},
        }
        state = {"queue": {"events": {}}, "workers": {}}

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
            mock.patch.object(supervisor, "start_worker_for_request", side_effect=AssertionError("not-ready provider should not start")),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.process_queue(self.config, state, provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-not-ready"]
        self.assertEqual(record["status"], "failed")
        self.assertIn("Auto dispatch unavailable for claude2", record["error"])
        self.assertEqual(state["workers"], {})
        write_activity_log.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "wake_skipped")

    def test_process_queue_records_capacity_wait_metrics(self) -> None:
        current_task = {
            "id": "BUS-VAL-CAP",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-13T14:20:00Z",
        }
        queue_payload = {
            "event_id": "evt-capacity-wait",
            "task_id": "BUS-VAL-CAP",
            "target_agent": "codex",
            "target_display_name": "Codex",
            "provider": "codex",
            "reason": "owned_in_progress_dispatch",
            "message": "wake",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        request = supervisor.DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="wake",
            task_id="BUS-VAL-CAP",
            reason="owned_in_progress_dispatch",
        )

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
            mock.patch.object(supervisor, "build_request", return_value=request),
            mock.patch.object(
                supervisor,
                "agent_auto_dispatch_block_reason",
                return_value="quota group codex1 already has 1/1 active worker(s)",
            ),
            mock.patch.object(supervisor, "start_worker_for_request", side_effect=AssertionError("capacity wait should not start")),
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-capacity-wait"]
        self.assertEqual(record["status"], "pending")
        self.assertEqual(record["capacity_wait_count"], 1)
        metrics = state["worker_runtime_metrics"]
        self.assertEqual(metrics["totals"]["capacity_pending_queue_events"], 1)
        self.assertEqual(
            metrics["last_measurements"]["dispatch_capacity_wait"]["details"]["queue_event_id"],
            "evt-capacity-wait",
        )

    def test_retryable_capacity_start_failure_schedules_queue_retry(self) -> None:
        current_task = {
            "id": "BUS-VAL-006",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-13T14:20:00Z",
        }
        queue_payload = {
            "event_id": "evt-retryable-capacity",
            "task_id": "BUS-VAL-006",
            "target_agent": "codex",
            "target_display_name": "Codex",
            "provider": "codex",
            "reason": "owned_in_progress_dispatch",
            "message": "wake",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        request = supervisor.DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="wake",
            task_id="BUS-VAL-006",
            reason="owned_in_progress_dispatch",
        )

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
            mock.patch.object(supervisor, "build_request", return_value=request),
            mock.patch.object(supervisor, "prepare_worker_workspace", return_value=(True, None)),
            mock.patch.object(supervisor, "start_worker_for_request", return_value=(False, "status: 429 RESOURCE_EXHAUSTED", None)),
            mock.patch.object(
                supervisor,
                "classify_worker_failure",
                return_value={"kind": "capacity_retryable", "label": "capacity/429", "transient": True},
            ),
            mock.patch.object(supervisor, "summarize_failure_reason", return_value={"summary": "Rate limited", "kind": "capacity_retryable"}),
            mock.patch.object(supervisor, "write_failure_evidence", return_value=None),
            mock.patch.object(supervisor, "record_task_failure_streak", return_value=1),
            mock.patch.object(supervisor, "mark_provider_dispatch_paused", return_value=True),
            mock.patch.object(supervisor, "maybe_reassign_task_after_worker_failure") as maybe_reassign_task_after_worker_failure,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-retryable-capacity"]
        self.assertEqual(record["status"], "retry_backoff")
        self.assertEqual(record["error"], "Rate limited")
        self.assertEqual(record["retry_count"], 1)
        self.assertIsNotNone(record["next_retry_at"])
        maybe_reassign_task_after_worker_failure.assert_not_called()
        self.assertEqual(state["workers"], {})

    def test_dispatcher_can_requeue_same_task_after_previous_failure(self) -> None:
        current_task = {
            "id": "REG-002",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": [],
            "last_update": "2026-04-06T09:00:00Z",
            "artifacts": ["services/registry/promotion/"],
            "next": "continue",
        }
        state = {
            "queue": {
                "events": {
                    "evt-old": {
                        "status": "failed",
                        "run_id": "old-run",
                    }
                }
            },
            "workers": {
                "old-run": {
                    "run_id": "old-run",
                    "queue_event_id": "evt-old",
                    "task_id": "REG-002",
                    "agent_id": "codex",
                    "status": "failed",
                }
            },
            "seen_event_keys": {"dispatcher:Codex:REG-002:owned_in_progress_dispatch:stale-signature": "2026-04-06T08:59:00Z"},
        }
        status = {"tasks": [current_task]}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(self.config, state)

        self.assertTrue(changed)
        queue_delivery_event.assert_called_once()
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "REG-002")
        self.assertEqual(queued_event["target_agent"], "Codex")
        self.assertEqual(queued_event["reason"], "owned_in_progress_dispatch")

    def test_idle_worker_leases_sla_overdue_task_without_reassigning_owner(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["agents"].update(
            {
                "claude": {
                    "id": "claude",
                    "display_name": "Claude",
                    "provider": "claude",
                    "adapter": "claude",
                },
                "gemini": {
                    "id": "gemini",
                    "display_name": "Gemini",
                    "provider": "gemini",
                    "adapter": "gemini",
                },
            }
        )
        config["providers"].update(
            {
                "claude": {"delivery_mode": "claude"},
                "gemini": {"delivery_mode": "gemini"},
            }
        )
        config["ready_dispatcher"]["helper_execution_lease"] = {
            "enabled": True,
            "claimable_statuses": ["todo"],
            "require_owner_saturated": True,
            "dispatch_sla_seconds": 1,
            "lease_seconds": 1800,
        }
        task = {
            "id": "HELPER-LEASE-001",
            "status": "todo",
            "owner": "Claude",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-08-20T00:00:00Z",
        }
        status = {"tasks": [task]}
        state = {
            "queue": {"events": {}},
            "workers": {},
            "capacity_controller": {"chair_decision": {"max_helper_claims": 1}},
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=True),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queued,
        ):
            changed = supervisor.dispatch_ready_tasks(
                config,
                state,
                agent_ids_override=["codex"],
            )

        self.assertTrue(changed)
        self.assertEqual(task["owner"], "Claude")
        self.assertEqual(task["reviewer"], "Gemini")
        self.assertEqual(task["helper_execution_lease"]["claimed_by"], "Codex")
        queued.assert_called_once()
        event = queued.call_args.args[1]
        self.assertEqual(event["target_agent"], "Codex")
        self.assertEqual(event["reason"], "helper_claim_dispatch")

    def test_an_escalated_lease_block_is_reported_on_the_task_record(self) -> None:
        """Stopping dispatch silently is the 2026-08-17 jam; retrying forever is
        the 2026-08-19 livelock. An escalated block has to do both - stop, and
        say so where an owner looks - or it repeats one of the two.

        `last_at` is recent because the stop is now bounded by the escalated
        retry window: inside it the block stops dispatch and reports, and once
        it expires the block is retried instead. A fixed date here would drift
        out of the window and silently stop testing the reporting path."""
        import dispatch_engine

        task = {
            "id": "REG-BLOCKED-001",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": [],
            "last_update": "2026-08-20T09:00:00Z",
            "next": "continue",
        }
        status = {"tasks": [task]}
        reason = "owned_in_progress_dispatch"
        signature = dispatch_engine.ready_dispatch_signature(task, reason, {task["id"]: task})
        state = {
            "queue": {"events": {}},
            "workers": {},
            "worker_worktree_lease_blocks": {
                supervisor.normalize_agent_id(task["id"]): {
                    "dispatch_signature": signature,
                    "last_at": (datetime.now(UTC) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "refresh_status": "unverifiable_refs: remote task branch is missing",
                    "escalated": True,
                    "count": 29,
                }
            },
        }
        logged: list[dict] = []

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=True),
            mock.patch.object(
                supervisor, "write_activity_log", side_effect=lambda _c, e: logged.append(e)
            ),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queued,
        ):
            supervisor.dispatch_ready_tasks(self.config, state)

        queued.assert_not_called()
        self.assertIn("is stopped", task["next"])
        self.assertIn("29 consecutive times", task["next"])
        self.assertIn("remote task branch is missing", task["next"])
        self.assertEqual(
            [e["type"] for e in logged if e.get("type") == "dispatch_stopped_worktree_lease"],
            ["dispatch_stopped_worktree_lease"],
        )

        # Steady state: the same stopped block must not rewrite the board.
        logged.clear()
        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=True),
            mock.patch.object(
                supervisor, "write_activity_log", side_effect=lambda _c, e: logged.append(e)
            ),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queued2,
        ):
            supervisor.dispatch_ready_tasks(self.config, state)

        queued2.assert_not_called()
        self.assertEqual([e for e in logged if e.get("type") == "dispatch_stopped_worktree_lease"], [])

    def test_dispatcher_queues_multiple_codex_tasks_up_to_worker_slot_capacity(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["agents"]["codex"]["worker_slots"] = ["codex1_1", "codex1_2", "codex1_3", "codex1_4"]
        for index in range(1, 5):
            config["agents"][f"codex1_{index}"] = {
                "id": f"codex1_{index}",
                "display_name": "Codex",
                "provider": f"codex1-{index}",
                "adapter": "codex",
                "dispatch_slot_for": "codex",
            }
            config["providers"][f"codex1-{index}"] = {
                "delivery_mode": "codex",
                "quota_group": "codex1",
            }
        status = {
            "tasks": [
                {
                    "id": f"BFF-CONSOL-0{index}",
                    "status": "todo",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "depends_on": [],
                    "last_update": f"2026-05-13T04:0{index}:00Z",
                }
                for index in range(1, 5)
            ]
        }
        state = {"queue": {"events": {}}, "workers": {}}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        queued_task_ids = [call.args[1]["task_id"] for call in queue_delivery_event.call_args_list]
        self.assertEqual(queued_task_ids, ["BFF-CONSOL-01", "BFF-CONSOL-02", "BFF-CONSOL-03", "BFF-CONSOL-04"])
        self.assertTrue(all(call.args[1]["target_agent"] == "Codex" for call in queue_delivery_event.call_args_list))


    def test_persisted_dispatch_cursor_prevents_review_starvation(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "enabled": True,
                "review_statuses": ["review"],
                "owned_statuses": ["in_progress", "todo"],
                "active_worker_statuses": ["running"],
                "max_dispatches_per_tick": 1,
            },
            "agents": {
                "antigravity": {
                    "id": "antigravity",
                    "display_name": "Antigravity",
                    "provider": "antigravity",
                },
                "codex": {
                    "id": "codex",
                    "display_name": "Codex",
                    "provider": "codex",
                },
            },
            "providers": {},
        }
        status = {
            "tasks": [
                {
                    "id": "OWNER-FIRST",
                    "status": "todo",
                    "owner": "Antigravity",
                    "reviewer": "Codex",
                    "depends_on": [],
                },
                {
                    "id": "REVIEW-LATER",
                    "status": "review",
                    "owner": "Antigravity",
                    "reviewer": "Codex",
                    "depends_on": [],
                },
            ]
        }
        state = runtime_state.default_state()
        first_round: list[dict[str, Any]] = []
        second_round: list[dict[str, Any]] = []

        common_patches = (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(
                supervisor,
                "outstanding_delivery_indexes",
                return_value=(set(), set(), set()),
            ),
            mock.patch.object(supervisor, "scan_live_worker_pids_by_agent", return_value={}),
            mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
            mock.patch.object(supervisor, "normalize_mainline_task_assignment", return_value=False),
            mock.patch.object(
                supervisor,
                "utc_now",
                return_value="2026-07-31T12:00:00Z",
            ),
        )
        with contextlib.ExitStack() as stack:
            for patcher in common_patches:
                stack.enter_context(patcher)
            stack.enter_context(
                mock.patch.object(
                    supervisor,
                    "queue_delivery_event",
                    side_effect=lambda _config, event: first_round.append(event) or True,
                )
            )
            self.assertTrue(supervisor.dispatch_ready_tasks(config, state, provider_report={}))

        self.assertEqual(first_round[0]["task_id"], "OWNER-FIRST")
        self.assertEqual(state["ready_dispatcher"]["dispatch_cursor"], 1)
        self.assertEqual(state["ready_dispatcher"]["dispatch_cursor_revision"], 1)

        # Simulate the end-of-loop save/reload boundary that previously erased
        # the cursor and restarted every round from Antigravity.
        state = runtime_state.migrate_state(deepcopy(state))
        with contextlib.ExitStack() as stack:
            for patcher in (
                mock.patch.object(supervisor, "load_status", return_value=status),
                mock.patch.object(supervisor, "load_event_queue", return_value=[]),
                mock.patch.object(
                    supervisor,
                    "outstanding_delivery_indexes",
                    return_value=(set(), set(), set()),
                ),
                mock.patch.object(supervisor, "scan_live_worker_pids_by_agent", return_value={}),
                mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
                mock.patch.object(supervisor, "normalize_mainline_task_assignment", return_value=False),
                mock.patch.object(
                    supervisor,
                    "utc_now",
                    return_value="2026-07-31T12:00:00Z",
                ),
                mock.patch.object(
                    supervisor,
                    "queue_delivery_event",
                    side_effect=lambda _config, event: second_round.append(event) or True,
                ),
            ):
                stack.enter_context(patcher)
            self.assertTrue(supervisor.dispatch_ready_tasks(config, state, provider_report={}))

        self.assertEqual(second_round[0]["task_id"], "REVIEW-LATER")
        self.assertEqual(second_round[0]["target_agent"], "Codex")
        self.assertEqual(second_round[0]["reason"], "review_ready_dispatch")
        self.assertEqual(state["ready_dispatcher"]["dispatch_cursor"], 0)
        self.assertEqual(state["ready_dispatcher"]["dispatch_cursor_revision"], 2)
        self.assertEqual(
            state["ready_dispatcher"]["dispatch_cursor_updated_at"],
            "2026-07-31T12:00:00Z",
        )

    def test_dispatcher_fails_closed_on_owner_self_review(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "enabled": True,
                "review_statuses": ["review"],
                "active_worker_statuses": ["running"],
                "helper_claim": {"enabled": False},
            },
            "agents": {
                "codex": {
                    "id": "codex",
                    "display_name": "Codex",
                    "provider": "codex",
                }
            },
            "providers": {},
        }
        status = {
            "tasks": [
                {
                    "id": "INVALID-SELF-REVIEW",
                    "status": "review",
                    "priority": "P1",
                    "owner": "Codex",
                    "reviewer": "Codex",
                    "depends_on": [],
                }
            ]
        }
        state = runtime_state.default_state()

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(
                supervisor,
                "outstanding_delivery_indexes",
                return_value=(set(), set(), set()),
            ),
            mock.patch.object(supervisor, "scan_live_worker_pids_by_agent", return_value={}),
            mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
            mock.patch.object(supervisor, "normalize_mainline_task_assignment", return_value=False),
            mock.patch.object(supervisor, "queue_delivery_event") as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(
                config,
                state,
                provider_report={},
                agent_ids_override=["codex"],
            )

        self.assertFalse(changed)
        queue_delivery_event.assert_not_called()

    def test_dispatcher_queues_owner_finalize_after_review_approved(self) -> None:
        current_task = {
            "id": "REG-002",
            "status": "review_approved",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": ["REG-001"],
            "last_update": "2026-04-06T15:00:00Z",
            "approved_head": "1111111122222222333333334444444455555555",
        }
        dependency = {
            "id": "REG-001",
            "status": "done",
            "owner": "Codex",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-06T14:00:00Z",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        status = {"tasks": [dependency, current_task]}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch("ai_status.resolve_task_sha", return_value="1111111122222222333333334444444455555555"),
            mock.patch("ai_status.task_pr_ci_status", return_value=("MERGED", "success")),
        ):
            changed = supervisor.dispatch_ready_tasks(self.config, state)

        self.assertTrue(changed)
        queue_delivery_event.assert_called_once()
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "REG-002")
        self.assertEqual(queued_event["target_agent"], "Codex")
        self.assertEqual(queued_event["reason"], "owned_finalize_dispatch")

    def test_dispatcher_waits_for_done_not_review_approved_dependencies(self) -> None:
        current_task = {
            "id": "FB-003",
            "status": "todo",
            "owner": "Claude",
            "reviewer": "Codex",
            "depends_on": ["REG-002"],
            "last_update": "2026-04-06T15:00:00Z",
        }
        dependency = {
            "id": "REG-002",
            "status": "review_approved",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": ["REG-001"],
            "last_update": "2026-04-06T14:00:00Z",
            "approved_head": "1111111122222222333333334444444455555555",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        status = {"tasks": [dependency, current_task]}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch("ai_status.resolve_task_sha", return_value="1111111122222222333333334444444455555555"),
            mock.patch("ai_status.task_pr_ci_status", return_value=("MERGED", "success")),
        ):
            changed = supervisor.dispatch_ready_tasks(self.config, state)

        self.assertTrue(changed)
        queued_task_ids = [call.args[1]["task_id"] for call in queue_delivery_event.call_args_list]
        self.assertNotIn("FB-003", queued_task_ids)

    def test_dispatcher_accepts_archived_done_dependency(self) -> None:
        current_task = {
            "id": "FB-004",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": ["REG-100"],
            "last_update": "2026-04-06T15:00:00Z",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        status = {"tasks": [current_task]}

        class FakeResolver:
            def __init__(self, task_lookup):
                self.task_lookup = task_lookup

            def dependency_status(self, task_id):
                if task_id == "REG-100":
                    return "done"
                task = self.task_lookup.get(task_id) or {}
                return str(task.get("status") or "missing")

            def dependency_satisfied(self, task_id):
                return task_id == "REG-100"

        with (
            mock.patch.object(supervisor, "TaskResolver", FakeResolver),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(self.config, state)

        self.assertTrue(changed)
        queued_task_ids = [call.args[1]["task_id"] for call in queue_delivery_event.call_args_list]
        self.assertIn("FB-004", queued_task_ids)

    def test_dispatcher_rejects_archived_superseded_dependency(self) -> None:
        current_task = {
            "id": "FB-005",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": ["REG-200"],
            "last_update": "2026-04-06T15:00:00Z",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        status = {"tasks": [current_task]}

        class FakeResolver:
            def __init__(self, task_lookup):
                self.task_lookup = task_lookup

            def dependency_status(self, task_id):
                if task_id == "REG-200":
                    return "superseded"
                task = self.task_lookup.get(task_id) or {}
                return str(task.get("status") or "missing")

            def dependency_satisfied(self, task_id):
                return False

        with (
            mock.patch.object(supervisor, "TaskResolver", FakeResolver),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(self.config, state)

        self.assertFalse(changed)
        queued_task_ids = [call.args[1]["task_id"] for call in queue_delivery_event.call_args_list]
        self.assertNotIn("FB-005", queued_task_ids)

    def test_discussion_planning_materialization_treats_archived_task_as_already_materialized(self) -> None:
        planning_state = {
            "status": "accepted",
            "human_gate_status": "approved",
            "session_id": "phase3-2026-04-14-pantheon-console-loop",
            "proposed_execution_tasks": [{"id": "LOOP-001"}],
        }

        class FakeResolver:
            def __init__(self, _task_lookup):
                pass

            def snapshot(self, task_id):
                if task_id == "LOOP-001":
                    return {"task_id": "LOOP-001"}
                return None

        with (
            mock.patch.object(supervisor, "load_json", return_value={"tasks": []}),
            mock.patch.object(supervisor, "config_path", return_value=Path("/tmp/ai-status.json")),
            mock.patch.object(supervisor, "TaskResolver", FakeResolver),
        ):
            needs_materialization = supervisor.discussion_planning_needs_materialization(self.config, planning_state)

        self.assertFalse(needs_materialization)

    def test_agent_cannot_take_non_dispatchable_or_human_gate_task(self) -> None:
        config = {
            "agents": {
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }

        self.assertFalse(
            supervisor.agent_can_take_task(
                config,
                "Claude",
                {"id": "OPERATOR-ONLY", "non_dispatchable": True},
            )
        )
        self.assertFalse(
            supervisor.agent_can_take_task(
                config,
                "Claude",
                {"id": "HUMAN-GATE", "task_class": "human_gate"},
            )
        )






    def test_dispatcher_does_not_queue_backlog_for_single_process_agent(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "active_worker_statuses": ["running"],
                "max_tasks_per_agent_by_agent": {"Copilot": 4},
                "helper_claim": {"enabled": False},
            },
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
            },
            "providers": {},
        }
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-copilot": {
                    "run_id": "run-copilot",
                    "task_id": "BUSY-001",
                    "provider": "copilot",
                    "agent_id": "copilot",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_in_progress_dispatch"},
                }
            },
        }
        status = {
            "tasks": [
                {
                    "id": "NEXT-001",
                    "status": "todo",
                    "owner": "Copilot",
                    "reviewer": "Reviewer",
                    "depends_on": [],
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertFalse(changed)
        queue_delivery_event.assert_not_called()

    def test_dispatcher_spreads_paused_review_to_registered_idle_reviewer(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "active_worker_statuses": ["running"],
                "reviewer_failover": {
                    "enabled": True,
                },
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "antigravity": {
                    "id": "antigravity",
                    "display_name": "Antigravity",
                    "provider": "antigravity",
                },
                "antigravity2": {
                    "id": "antigravity2",
                    "display_name": "Antigravity2",
                    "provider": "antigravity2",
                },
            },
            "providers": {},
        }
        state = {
            "queue": {"events": {}},
            "workers": {},
            "provider_guardrails": {
                "dispatch_pauses": {
                    "codex": {
                        "provider": "codex",
                        "blocked_until": "2999-01-01T00:00:00Z",
                        "summary": "Codex usage limit reached",
                    }
                }
            },
        }
        initial_status = {
            "tasks": [
                {
                    "id": "REVIEW-002",
                    "status": "review",
                    "owner": "Antigravity",
                    "reviewer": "Codex",
                    "depends_on": [],
                }
            ]
        }
        persisted_status = {
            "tasks": [
                {
                    "id": "REVIEW-002",
                    "status": "review",
                    "owner": "Antigravity",
                    "reviewer": "Antigravity2",
                    "depends_on": [],
                    "last_update": "2026-08-02T14:05:00Z",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[initial_status, persisted_status]),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        self.assertEqual(persist.call_args.kwargs["new_owner"], "Antigravity")
        self.assertEqual(persist.call_args.kwargs["new_reviewer"], "Antigravity2")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "REVIEW-002")
        self.assertEqual(queued_event["target_agent"], "Antigravity2")
        self.assertEqual(queued_event["reason"], "review_ready_dispatch")










    def test_dispatcher_reassigns_mainline_helper_owner_before_dispatch(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "sidecar_only_agents": ["Helper"],
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Helper": ["Codex", "Claude", "Copilot"],
                },
                "reviewer_fallbacks": {
                    "Helper": ["Codex", "Claude", "Copilot"],
                },
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "helper": {"id": "helper", "display_name": "Helper", "provider": "helper"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        initial_status = {
            "tasks": [
                {"id": "WB-011", "status": "todo", "owner": "Helper", "reviewer": "Claude", "depends_on": []},
            ]
        }
        normalized_status = {
            "tasks": [
                {"id": "WB-011", "status": "todo", "owner": "Codex", "reviewer": "Claude", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[initial_status, normalized_status]),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, {"queue": {"events": {}}, "workers": {}})

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "WB-011")
        self.assertEqual(kwargs["new_owner"], "Codex")
        self.assertEqual(kwargs["new_reviewer"], "Claude")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "WB-011")
        self.assertEqual(queued_event["target_agent"], "Codex")
        self.assertEqual(queued_event["reason"], "owned_ready_dispatch")

    def test_dispatcher_reassigns_mainline_helper_reviewer_before_dispatch(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "sidecar_only_agents": ["Helper"],
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Helper": ["Codex", "Claude", "Copilot"],
                },
                "reviewer_fallbacks": {
                    "Helper": ["Codex", "Claude", "Copilot"],
                },
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "helper": {"id": "helper", "display_name": "Helper", "provider": "helper"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        initial_status = {
            "tasks": [
                {"id": "WB-012", "status": "review", "owner": "Claude", "reviewer": "Helper", "depends_on": []},
            ]
        }
        normalized_status = {
            "tasks": [
                {"id": "WB-012", "status": "review", "owner": "Claude", "reviewer": "Codex", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[initial_status, normalized_status]),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, {"queue": {"events": {}}, "workers": {}})

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "WB-012")
        self.assertEqual(kwargs["new_owner"], "Claude")
        self.assertEqual(kwargs["new_reviewer"], "Codex")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "WB-012")
        self.assertEqual(queued_event["target_agent"], "Codex")
        self.assertEqual(queued_event["reason"], "review_ready_dispatch")





    def test_dispatcher_skips_agent_when_provider_report_says_not_auto_ready(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "agents": {
                "claude2": {"id": "claude2", "display_name": "Claude2", "provider": "claude2"},
            },
            "providers": {},
        }
        status = {
            "tasks": [
                {"id": "AUTO-READY-001", "status": "review", "owner": "Codex", "reviewer": "Claude2", "depends_on": []},
            ]
        }
        provider_report = {
            "agent_adapters": {
                "claude2": {
                    "supported": True,
                    "can_auto_deliver": False,
                    "notes": "Claude CLI is installed but not authenticated.",
                }
            },
            "providers": {
                "claude2": {
                    "local_cli_worker_supported": False,
                    "supports_auto_approve": False,
                    "auth_ready": False,
                }
            },
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event") as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(
                config,
                {"queue": {"events": {}}, "workers": {}},
                provider_report=provider_report,
            )

        self.assertFalse(changed)
        queue_delivery_event.assert_not_called()

    def test_skips_duplicate_start_when_active_worker_already_exists(self) -> None:
        current_task = {
            "id": "P3-001",
            "status": "review",
            "owner": "Claude",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-06T05:30:43Z",
        }
        current_event = supervisor.build_dispatch_event(
            current_task,
            "Gemini",
            "review_ready_dispatch",
            {"P3-001": current_task},
        )
        queue_payload = {
            "event_id": "evt-current",
            "event_key": current_event["key"],
            "task_id": "P3-001",
            "target_agent": "gemini",
            "target_display_name": "Gemini",
            "reason": "review_ready_dispatch",
            "message": "wake",
        }
        state = {
            "queue": {"events": {}},
            "workers": {
                "gemini-run-1": {
                    "run_id": "gemini-run-1",
                    "queue_event_id": "evt-current",
                    "status": "running",
                }
            },
        }

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
            mock.patch.object(supervisor, "start_worker_for_request", side_effect=AssertionError("duplicate queue event should not start another worker")),
            mock.patch.object(supervisor, "sync_dispatched_task_status", return_value=True) as sync_dispatched_task_status,
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-current"]
        self.assertEqual(record["status"], "started")
        self.assertEqual(record["run_id"], "gemini-run-1")
        sync_dispatched_task_status.assert_called_once_with(self.config, queue_payload)


class DispatchStatusSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        (self.root / "scripts").mkdir(parents=True, exist_ok=True)
        (self.root / "scripts" / "ai_status.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        (self.root / "activity-log.jsonl").write_text("", encoding="utf-8")
        self.status_path = self.root / "ai-status.json"
        self.status_path.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "APP-002-W1-FRONT-HANDOFF",
                            "status": "todo",
                            "owner": "Copilot",
                            "reviewer": "Codex",
                            "depends_on": [],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "status_field": "status",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "paths": {
                "status_file": str(self.status_path),
                "activity_log": str(self.root / "activity-log.jsonl"),
            },
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
            },
        }

    def test_sync_dispatched_task_status_starts_owned_todo_task(self) -> None:
        event = {
            "task_id": "APP-002-W1-FRONT-HANDOFF",
            "target_agent": "copilot",
            "target_display_name": "Copilot",
            "reason": "owned_ready_dispatch",
        }

        with mock.patch.object(supervisor.subprocess, "run", return_value=mock.Mock(returncode=0, stderr="", stdout="")) as run_mock:
            changed = supervisor.sync_dispatched_task_status(self.config, event)

        self.assertTrue(changed)
        command = run_mock.call_args.args[0]
        self.assertEqual(command[2], "start")
        self.assertEqual(command[3], "APP-002-W1-FRONT-HANDOFF")
        self.assertIn("Supervisor auto-started", command[4])
        self.assertEqual(run_mock.call_args.kwargs["env"]["AI_NAME"], "Copilot")

    def test_sync_dispatched_task_status_skips_review_dispatch(self) -> None:
        event = {
            "task_id": "APP-002-W1-FRONT-HANDOFF",
            "target_agent": "codex",
            "target_display_name": "Codex",
            "reason": "review_ready_dispatch",
        }

        with mock.patch.object(supervisor.subprocess, "run") as run_mock:
            changed = supervisor.sync_dispatched_task_status(self.config, event)

        self.assertFalse(changed)
        run_mock.assert_not_called()


class RunOnceSupervisorStateTests(unittest.TestCase):
    def test_discussion_planning_needs_materialization_for_accepted_approved_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_file = root / "ai-status.json"
            status_file.write_text(json.dumps({"tasks": []}), encoding="utf-8")
            config = {
                "paths": {"status_file": str(status_file)},
                "schema": {"tasks_path": "tasks", "task_id_field": "id"},
            }
            planning_state = {
                "status": "accepted",
                "human_gate_status": "approved",
                "session_id": "phase3-session",
                "proposed_execution_tasks": [
                    {
                        "id": "LOOP-001",
                        "source_plane": "planning",
                        "source_ref": {"session_id": "phase3-session"},
                    }
                ],
            }

            class FakeResolver:
                def __init__(self, _task_lookup):
                    pass

                def snapshot(self, _task_id):
                    return None

            with mock.patch.object(supervisor, "TaskResolver", FakeResolver):
                self.assertTrue(supervisor.discussion_planning_needs_materialization(config, planning_state))

    def test_discussion_planning_skips_materialization_when_current_session_tasks_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_file = root / "ai-status.json"
            status_file.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "LOOP-001",
                                "source_plane": "planning",
                                "source_ref": {"session_id": "phase3-session"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "paths": {"status_file": str(status_file)},
                "schema": {"tasks_path": "tasks", "task_id_field": "id"},
            }
            planning_state = {
                "status": "accepted",
                "human_gate_status": "approved",
                "session_id": "phase3-session",
                "proposed_execution_tasks": [
                    {
                        "id": "LOOP-001",
                        "source_plane": "planning",
                        "source_ref": {"session_id": "phase3-session"},
                    }
                ],
            }

            self.assertFalse(supervisor.discussion_planning_needs_materialization(config, planning_state))

    def test_discussion_planning_skips_materialization_when_session_already_stamped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_file = root / "ai-status.json"
            status_file.write_text(json.dumps({"tasks": []}), encoding="utf-8")
            config = {
                "paths": {"status_file": str(status_file)},
                "schema": {"tasks_path": "tasks", "task_id_field": "id"},
            }
            planning_state = {
                "status": "accepted",
                "human_gate_status": "approved",
                "materialized_at": "2026-04-19T03:40:25Z",
                "session_id": "phase7-session",
                "proposed_execution_tasks": [{"id": "OSS-004A"}],
            }

            self.assertFalse(supervisor.discussion_planning_needs_materialization(config, planning_state))

    def test_heartbeat_lag_seconds_reports_gap(self) -> None:
        lag = supervisor.heartbeat_lag_seconds(
            "2026-04-06T12:00:00Z",
            "2026-04-06T12:00:12Z",
        )

        self.assertEqual(lag, 12.0)

    def test_run_once_re_stamps_current_pid_after_watch_reload(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {},
            "watcher": {},
            "ready_dispatcher": {},
            "providers": {},
            "agents": {},
        }
        initial_state = {
            "queue": {"events": {}},
            "workers": {},
            "approvals": {},
            "supervisor": {
                "pid": 61209,
                "started_at": "2026-04-05T12:44:57Z",
                "last_heartbeat_at": "2026-04-06T04:17:26Z",
            },
        }
        saved_state: dict[str, object] = {}

        def capture_save(_config: dict[str, object], state: dict[str, object]) -> None:
            saved_state.clear()
            saved_state.update(state)

        with (
            mock.patch.object(supervisor, "write_supervisor_pid"),
            mock.patch.object(supervisor, "load_runtime_state", side_effect=[dict(initial_state), dict(initial_state)]),
            mock.patch.object(supervisor, "prune_stale_approvals", return_value=False),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "run_scan", return_value=False),
            mock.patch.object(supervisor, "poll_workers", return_value=False),
            mock.patch.object(supervisor, "reconcile_queue_records", return_value=False),
            mock.patch.object(supervisor, "prune_event_queue", return_value=False),
            mock.patch.object(supervisor, "load_discussion_planning_state", return_value=None),
            mock.patch.object(supervisor, "dispatch_ready_tasks", return_value=False),
            mock.patch.object(supervisor, "process_queue", return_value=False),
            mock.patch.object(supervisor, "sync_github_bus", return_value=False),
            mock.patch.object(supervisor, "utc_now", return_value="2026-06-30T04:30:09Z"),
            mock.patch.object(supervisor, "trim_worker_history"),
            mock.patch.object(supervisor, "trim_seen_events"),
            mock.patch.object(supervisor, "save_runtime_state", side_effect=capture_save),
        ):
            supervisor.run_once(config, watch=True, replay=False)

        self.assertEqual(saved_state["supervisor"]["pid"], os.getpid())
        self.assertIsNotNone(saved_state["supervisor"]["last_heartbeat_at"])
        self.assertEqual(saved_state["supervisor"]["started_at"], saved_state["supervisor"]["last_heartbeat_at"])

    def test_run_once_prioritizes_discussion_planning_dispatch(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {},
            "watcher": {},
            "ready_dispatcher": {},
            "providers": {},
            "agents": {},
        }
        initial_state = {
            "queue": {"events": {}},
            "workers": {},
            "approvals": {},
            "supervisor": {
                "pid": 61209,
                "started_at": "2026-04-05T12:44:57Z",
                "last_heartbeat_at": "2026-04-06T04:17:26Z",
            },
        }

        with (
            mock.patch.object(supervisor, "write_supervisor_pid"),
            mock.patch.object(supervisor, "load_runtime_state", side_effect=[dict(initial_state), dict(initial_state)]),
            mock.patch.object(supervisor, "prune_stale_approvals", return_value=False),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "run_scan", return_value=False),
            mock.patch.object(supervisor, "sync_coordination_files", return_value=False),
            mock.patch.object(supervisor, "poll_workers", return_value=False),
            mock.patch.object(supervisor, "reconcile_queue_records", return_value=False),
            mock.patch.object(supervisor, "prune_event_queue", return_value=False),
            mock.patch.object(supervisor, "load_discussion_planning_state", return_value={"status": "active", "planning_mode": "discussion_planning", "readouts": {}}),
            mock.patch.object(supervisor, "dispatch_discussion_planning", return_value=True) as dispatch_discussion_planning,
            mock.patch.object(supervisor, "dispatch_ready_tasks", return_value=False) as dispatch_ready_tasks,
            mock.patch.object(supervisor, "process_queue", return_value=False),
            mock.patch.object(supervisor, "sync_github_bus", return_value=False),
            mock.patch.object(supervisor, "trim_worker_history"),
            mock.patch.object(supervisor, "trim_seen_events"),
            mock.patch.object(supervisor, "save_runtime_state"),
        ):
            supervisor.run_once(config, watch=True, replay=False)

        dispatch_discussion_planning.assert_called_once()
        dispatch_ready_tasks.assert_not_called()


    def test_boot_reconciliation_runs_once_per_process(self) -> None:
        """`reconcile_runtime_on_boot` settles what the PREVIOUS process left behind.

        It used to run on every loop, always before `poll_workers`, and it has no
        retry/fallback branch on purpose -- so it hard-failed every dead
        `running`/`stalled` worker before `poll_workers` could schedule a retry,
        silently disabling the entire `worker_retry` config. The two settlement
        paths are deliberately different (see
        test_boot_reconciliation_correlates_flushed_receipt_before_missing_process_failure
        and its poll-path sibling), so the fix is scheduling, not de-duplication.
        """
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {},
            "watcher": {},
            "ready_dispatcher": {},
            "providers": {},
            "agents": {},
        }
        state = {"queue": {"events": {}}, "workers": {}, "approvals": {}}

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(supervisor, "_BOOT_RECONCILED", False))
            reconcile = stack.enter_context(
                mock.patch.object(supervisor, "reconcile_runtime_on_boot", return_value=False)
            )
            for name in (
                "continue_or_skip_empty",
                "expire_provider_dispatch_pauses",
                "prune_stale_approvals",
                "load_provider_report",
                "sync_coordination_files",
                "poll_workers",
                "reconcile_queue_records",
                "prune_event_queue",
                "refresh_chair_review_state",
                "auto_materialize_discussion_planning",
                "dispatch_ready_tasks",
                "dispatch_chair_review",
                "dispatch_underutilization_sidecars",
                "process_queue",
                "sync_github_bus",
                "check_branch_drift",
                "trim_worker_history",
                "trim_seen_events",
                "prune_orphan_worktrees",
                "maybe_auto_commit_archive",
                "refresh_dashboard_runtime_artifacts",
                "log_runtime_summary",
                "save_runtime_state",
                "write_activity_log",
                "stamp_supervisor_runtime_state",
                "compact_worker_history",
            ):
                if hasattr(supervisor, name):
                    stack.enter_context(mock.patch.object(supervisor, name, return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "load_runtime_state", return_value=state))
            for name, value in (
                ("load_discussion_planning_state", None),
                ("watchdog_safe_mode_active", False),
                ("discussion_planning_is_active", False),
                ("chair_review_failure_loop_details", []),
                ("write_supervisor_pid", None),
            ):
                if hasattr(supervisor, name):
                    stack.enter_context(mock.patch.object(supervisor, name, return_value=value))

            supervisor.run_once(config, watch=False, replay=False)
            supervisor.run_once(config, watch=False, replay=False)
            supervisor.run_once(config, watch=False, replay=False)

        # Boot pass on the first loop of this process, never again.
        reconcile.assert_called_once()

    def test_run_once_watchdog_safe_mode_suppresses_new_dispatch(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {},
            "watcher": {},
            "ready_dispatcher": {},
            "providers": {},
            "agents": {},
        }
        initial_state = {
            "queue": {"events": {}},
            "workers": {},
            "approvals": {},
            "watchdog": {
                "safe_mode_until": "2999-01-01T00:00:00Z",
                "safe_mode_reason": "stale_heartbeat",
            },
            "supervisor": {
                "pid": 61209,
                "started_at": "2026-04-05T12:44:57Z",
                "last_heartbeat_at": "2026-04-06T04:17:26Z",
            },
        }

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(supervisor, "write_supervisor_pid"))
            stack.enter_context(mock.patch.object(supervisor, "load_runtime_state", return_value=dict(initial_state)))
            stack.enter_context(mock.patch.object(supervisor, "continue_or_skip_empty"))
            stack.enter_context(mock.patch.object(supervisor, "expire_provider_dispatch_pauses", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "prune_stale_approvals", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "load_provider_report", return_value={}))
            stack.enter_context(mock.patch.object(supervisor, "sync_coordination_files", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "poll_workers", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "reconcile_queue_records", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "prune_event_queue", return_value=False))
            stack.enter_context(
                mock.patch.object(
                    supervisor,
                    "load_discussion_planning_state",
                    return_value={"status": "active", "planning_mode": "discussion_planning"},
                )
            )
            stack.enter_context(mock.patch.object(supervisor, "auto_materialize_discussion_planning", return_value=False))
            dispatch_discussion_planning = stack.enter_context(
                mock.patch.object(supervisor, "dispatch_discussion_planning", return_value=True)
            )
            dispatch_ready_tasks = stack.enter_context(mock.patch.object(supervisor, "dispatch_ready_tasks", return_value=True))
            process_queue = stack.enter_context(mock.patch.object(supervisor, "process_queue", return_value=True))
            stack.enter_context(mock.patch.object(supervisor, "sync_github_bus", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "check_branch_drift", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "trim_worker_history"))
            stack.enter_context(mock.patch.object(supervisor, "trim_seen_events"))
            stack.enter_context(mock.patch.object(supervisor, "prune_orphan_worktrees", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "refresh_dashboard_runtime_artifacts"))
            stack.enter_context(mock.patch.object(supervisor, "log_runtime_summary"))
            stack.enter_context(mock.patch.object(supervisor, "save_runtime_state"))
            write_activity_log = stack.enter_context(mock.patch.object(supervisor, "write_activity_log"))
            changed = supervisor.run_once(config, watch=False, replay=False)

        self.assertTrue(changed)
        dispatch_discussion_planning.assert_not_called()
        dispatch_ready_tasks.assert_not_called()
        process_queue.assert_not_called()
        write_activity_log.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "watchdog_safe_mode_dispatch_suppressed")

    def test_run_supervisor_cycle_logs_and_continues_after_error(self) -> None:
        config = {"supervisor": {}}

        with (
            mock.patch.object(supervisor, "run_once", side_effect=RuntimeError("boom")) as run_once,
            mock.patch.object(supervisor, "console_log") as console_log,
        ):
            changed = supervisor.run_supervisor_cycle(config, watch=True, replay=True, quiet=True, verbose=False)

        self.assertFalse(changed)
        run_once.assert_called_once_with(
            config,
            watch=True,
            replay=True,
            quiet=True,
            verbose=False,
            once=False,
        )
        self.assertIn("RuntimeError: boom", console_log.call_args.args[0])
        self.assertTrue(console_log.call_args.kwargs["quiet"])

    def test_run_once_auto_materializes_accepted_session_before_execution_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_file = root / "ai-status.json"
            status_file.write_text(json.dumps({"tasks": []}), encoding="utf-8")
            script_dir = root / "scripts"
            script_dir.mkdir(parents=True, exist_ok=True)
            (script_dir / "planning_state.py").write_text("# test stub\n", encoding="utf-8")

            config = {
                "paths": {
                    "status_file": str(status_file),
                    "activity_log": str(root / "activity-log.jsonl"),
                },
                "schema": {
                    "tasks_path": "tasks",
                    "task_id_field": "id",
                    "assignee_field": "owner",
                    "reviewer_field": "reviewer",
                },
                "supervisor": {},
                "watcher": {},
                "ready_dispatcher": {},
                "providers": {},
                "agents": {},
            }
            initial_state = {
                "queue": {"events": {}},
                "workers": {},
                "approvals": {},
                "supervisor": {
                    "pid": 61209,
                    "started_at": "2026-04-05T12:44:57Z",
                    "last_heartbeat_at": "2026-04-06T04:17:26Z",
                },
            }
            planning_state = {
                "status": "accepted",
                "planning_mode": "discussion_planning",
                "human_gate_status": "approved",
                "session_id": "phase3-session",
                "proposed_execution_tasks": [
                    {
                        "id": "LOOP-001",
                        "source_plane": "planning",
                        "source_ref": {"session_id": "phase3-session"},
                    }
                ],
            }

            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(supervisor, "write_supervisor_pid"))
                stack.enter_context(mock.patch.object(supervisor, "load_runtime_state", return_value=dict(initial_state)))
                stack.enter_context(mock.patch.object(supervisor, "continue_or_skip_empty"))
                stack.enter_context(mock.patch.object(supervisor, "prune_stale_approvals", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "load_provider_report", return_value={}))
                stack.enter_context(mock.patch.object(supervisor, "sync_coordination_files", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "poll_workers", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "reconcile_queue_records", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "prune_event_queue", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "load_discussion_planning_state", return_value=planning_state))
                dispatch_discussion_planning = stack.enter_context(
                    mock.patch.object(supervisor, "dispatch_discussion_planning", return_value=False)
                )
                dispatch_ready_tasks = stack.enter_context(
                    mock.patch.object(supervisor, "dispatch_ready_tasks", return_value=True)
                )
                stack.enter_context(mock.patch.object(supervisor, "process_queue", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "sync_github_bus", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "check_branch_drift", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "trim_worker_history"))
                stack.enter_context(mock.patch.object(supervisor, "trim_seen_events"))
                stack.enter_context(mock.patch.object(supervisor, "refresh_dashboard_runtime_artifacts"))
                stack.enter_context(mock.patch.object(supervisor, "log_runtime_summary"))
                stack.enter_context(mock.patch.object(supervisor, "save_runtime_state"))
                stack.enter_context(
                    mock.patch.object(
                        supervisor,
                        "TaskResolver",
                        type(
                            "FakeResolver",
                            (),
                            {
                                "__init__": lambda self, _task_lookup: None,
                                "snapshot": lambda self, _task_id: None,
                            },
                        ),
                    )
                )
                run_mock = stack.enter_context(
                    mock.patch.object(
                        supervisor.subprocess,
                        "run",
                        return_value=subprocess.CompletedProcess(
                            args=["python3", str(script_dir / "planning_state.py"), "materialize"],
                            returncode=0,
                            stdout="materialized",
                            stderr="",
                        ),
                    )
                )
                changed = supervisor.run_once(config, watch=False, replay=False)

            self.assertTrue(changed)
            dispatch_discussion_planning.assert_not_called()
            dispatch_ready_tasks.assert_called_once()
            materialize_calls = [
                call
                for call in run_mock.call_args_list
                if any(
                    "planning_state.py" in str(arg)
                    for arg in (call.args[0] if call.args else [])
                )
            ]
            self.assertEqual(len(materialize_calls), 1)
            self.assertEqual(materialize_calls[0].args[0][-1], "materialize")


class SupervisorRuntimeFocusTests(unittest.TestCase):
    def test_discussion_planning_focus_overrides_execution_draining(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "event-queue.jsonl").write_text("", encoding="utf-8")
            config = {
                "paths": {
                    "event_queue": str(root / "event-queue.jsonl"),
                    "status_file": str(root / "ai-status.json"),
                },
                "schema": {
                    "tasks_path": "tasks",
                    "task_id_field": "id",
                    "assignee_field": "owner",
                    "reviewer_field": "reviewer",
                },
                "ready_dispatcher": {},
            }
            state = {
                "queue": {"events": {}},
                "workers": {
                    "exec-worker": {
                        "status": "manual_pending",
                        "reason": "owned_dispatch",
                    },
                    "planning-worker": {
                        "status": "started",
                        "reason": "discussion_planning_baton_dispatch",
                        "request_snapshot": {
                            "reason": "discussion_planning_baton_dispatch",
                            "metadata": {
                                "planning": {
                                    "session_id": "phase7-2026-04-18-ep4-ep5-execution-proof",
                                    "mode": "discussion_planning",
                                }
                            },
                        },
                    },
                },
                "supervisor": {
                    "pid": 61209,
                    "focus_mode": "execution",
                    "mode_status": "active",
                },
            }
            planning_state = {
                "status": "active",
                "planning_mode": "discussion_planning",
                "session_id": "phase7-2026-04-18-ep4-ep5-execution-proof",
            }

            supervisor.stamp_supervisor_runtime_state(
                config,
                state,
                planning_state=planning_state,
                heartbeat_at="2026-04-18T14:40:00Z",
                lifecycle="running",
            )

            supervisor_state = state["supervisor"]
            self.assertEqual(supervisor_state["focus_mode"], "planning")
            self.assertEqual(supervisor_state["mode_status"], "active")
            self.assertIsNone(supervisor_state["mode_switch_requested"])
            self.assertEqual(supervisor_state["last_mode_switch_at"], "2026-04-18T14:40:00Z")
            self.assertEqual(supervisor_state["mode_occupancy"]["planning"]["running"], 1)
            # A manual inbox record with no PID is not an executing worker and
            # must not make the runtime look occupied.
            self.assertEqual(supervisor_state["mode_occupancy"]["execution"]["pending"], 0)


class DiscussionPlanningDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        (self.root / "activity-log.jsonl").write_text("", encoding="utf-8")
        self.config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "status_field": "status",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "paths": {
                "event_queue": str(self.root / "event-queue.jsonl"),
                "activity_log": str(self.root / "activity-log.jsonl"),
            },
            "ready_dispatcher": {
                "active_worker_statuses": [
                    "running",
                    "started",
                    "waiting_approval",
                    "manual_pending",
                    "retry_backoff",
                    "suspended_approval",
                    "stalled",
                    "fallback",
                ],
            },
            "agents": {
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
                "gemini": {"id": "gemini", "display_name": "Gemini", "provider": "gemini"},
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
                "helper": {"id": "helper", "display_name": "Helper", "provider": "helper"},
            },
            "providers": {
                "claude": {"delivery_mode": "claude_cli"},
                "gemini": {"delivery_mode": "gemini"},
                "codex": {"delivery_mode": "codex"},
                "copilot": {"delivery_mode": "copilot_local"},
                "helper": {"delivery_mode": "helper"},
            },
        }

    def test_dispatch_discussion_planning_queues_pending_readouts(self) -> None:
        planning_state = {
            "session_id": "phase1-2026-04-11",
            "status": "active",
            "planning_mode": "discussion_planning",
            "summary": "Plan the ODay Plus backend completion wave.",
            "baton_owner": "Codex",
            "next_reviewer": "Helper",
            "current_round": 0,
            "consensus_status": "draft",
            "readouts": {
                "Claude": {"status": "pending"},
                "Codex": {"status": "pending"},
                "Gemini": {"status": "pending"},
                "Helper": {"status": "pending"},
                "Copilot": {"status": "pending"},
            },
        }
        state = {"queue": {"events": {}}, "workers": {}, "seen_event_keys": {}}

        with mock.patch.object(supervisor, "selected_shared_files", return_value=[self.root / "shared.md"]):
            changed = supervisor.dispatch_discussion_planning(self.config, state, planning_state)

        self.assertTrue(changed)
        rows = [
            json.loads(line)
            for line in (self.root / "event-queue.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(rows), 5)
        codex_event = next(row for row in rows if row["target_display_name"] == "Codex")
        self.assertEqual(codex_event["reason"], "discussion_planning_baton_dispatch")
        self.assertIn("starter-draft.md", "\n".join(codex_event["target_files"]))
        claude_event = next(row for row in rows if row["target_display_name"] == "Claude")
        self.assertIn("consensus-packet.md", "\n".join(claude_event["target_files"]))

    def test_dispatch_discussion_planning_uses_active_session_paths_and_owned_outputs(self) -> None:
        planning_dir = "docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop"
        planning_state = {
            "session_id": "phase3-2026-04-14-pantheon-console-loop",
            "planning_dir": planning_dir,
            "session_file": f"{planning_dir}/planning-session.json",
            "status": "active",
            "planning_mode": "discussion_planning",
            "summary": "Formalize the ODay Plus Console closed loop.",
            "objective": "Define the canonical closed-loop coordination protocol and execution backlog for all 8 workbenches.",
            "baton_owner": "Codex",
            "next_reviewer": "Helper",
            "current_round": 0,
            "consensus_status": "draft",
            "brief_files": [
                "Pantheon_總索引版系統分析文件.md",
                ".coordination/README.md",
            ],
            "artifacts": {
                "planning_readme": {"path": f"{planning_dir}/README.md"},
                "starter_draft": {"path": f"{planning_dir}/starter-draft.md"},
                "consensus_packet": {"path": f"{planning_dir}/consensus-packet.md"},
            },
            "expected_outputs": [
                {
                    "id": "coordination_loop_spec",
                    "path": f"{planning_dir}/coordination-loop-spec.md",
                    "owner": "Codex",
                }
            ],
            "readouts": {
                "Claude": {"status": "pending", "path": f"{planning_dir}/claude-readout.md"},
                "Codex": {"status": "pending", "path": f"{planning_dir}/codex-readout.md"},
                "Gemini": {"status": "pending", "path": f"{planning_dir}/gemini-readout.md"},
                "Helper": {"status": "pending", "path": f"{planning_dir}/helper-readout.md"},
                "Copilot": {"status": "pending", "path": f"{planning_dir}/copilot-readout.md"},
            },
        }
        state = {"queue": {"events": {}}, "workers": {}, "seen_event_keys": {}}

        with mock.patch.object(supervisor, "selected_shared_files", return_value=[self.root / "shared.md"]):
            changed = supervisor.dispatch_discussion_planning(self.config, state, planning_state)

        self.assertTrue(changed)
        rows = [
            json.loads(line)
            for line in (self.root / "event-queue.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        codex_event = next(row for row in rows if row["target_display_name"] == "Codex")
        self.assertIn(f"{planning_dir}/README.md", codex_event["target_files"])
        self.assertIn(f"{planning_dir}/planning-session.json", codex_event["target_files"])
        self.assertIn(f"{planning_dir}/codex-readout.md", codex_event["target_files"])
        self.assertIn(f"{planning_dir}/coordination-loop-spec.md", codex_event["target_files"])
        self.assertIn("本輪目標：Define the canonical closed-loop coordination protocol", codex_event["message"])

    def test_planning_worker_matches_assignment_without_taskboard_entry(self) -> None:
        worker = {
            "task_id": "phase1-2026-04-11-backend-completion",
            "agent_id": "codex",
            "request_snapshot": {
                "reason": "discussion_planning_baton_dispatch",
                "metadata": {
                    "planning": {
                        "session_id": "phase1-2026-04-11-backend-completion",
                        "mode": "discussion_planning",
                    }
                },
            },
        }

        self.assertTrue(supervisor.worker_matches_current_assignment(self.config, worker, {}))
        self.assertFalse(supervisor.higher_priority_ready_task_exists(self.config, worker, {}))

    def test_coordination_worker_matches_assignment_without_taskboard_entry(self) -> None:
        worker = {
            "task_id": "F-042",
            "agent_id": "codex",
            "request_snapshot": {
                "reason": "coordination:ui-done",
                "metadata": {
                    "coordination": {
                        "feature_id": "F-042",
                        "worker_kind": "front-sync-worker",
                        "payload_type": "ui-done",
                    }
                },
            },
        }

        self.assertTrue(supervisor.worker_matches_current_assignment(self.config, worker, {}))
        self.assertFalse(supervisor.higher_priority_ready_task_exists(self.config, worker, {}))

    def test_detect_worker_failure_ignores_code_snippet_error_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "worker.log"
            log_path.write_text(
                "\n".join(
                    [
                        "class CommandStatusResponse(BaseModel):",
                        "    result: Optional[Dict[str, Any]] = None",
                        "    error: Optional[Dict[str, Any]] = None,",
                        "    audit: Optional[Dict[str, Any]] = None",
                        "class BffErrorEnvelope(BaseModel):",
                        "    error: BffErrorPayload",
                        "class ErrorResponse(BffErrorEnvelope):",
                        "    error: BFFError",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            worker = {"log_path": str(log_path)}
            self.assertIsNone(supervisor.detect_worker_failure(worker))


    def test_priority_preemption_respects_logical_agent_slot_capacity(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["agents"]["codex"]["worker_slots"] = ["codex1_1", "codex1_2", "codex1_3", "codex1_4"]
        for slot_id in config["agents"]["codex"]["worker_slots"]:
            config["agents"][slot_id] = {
                "id": slot_id,
                "display_name": "Codex",
                "dispatch_slot_for": "codex",
                "provider": slot_id.replace("_", "-"),
            }
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-high": {
                    "run_id": "run-high",
                    "task_id": "BFF-CONSOL-016",
                    "agent_id": "codex1_1",
                    "logical_agent_id": "codex",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_in_progress_dispatch"},
                },
                "run-low": {
                    "run_id": "run-low",
                    "task_id": "BFF-CONSOL-017",
                    "agent_id": "codex1_2",
                    "logical_agent_id": "codex",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_ready_dispatch"},
                },
            },
        }
        task_map = {
            "BFF-CONSOL-016": {
                "id": "BFF-CONSOL-016",
                "status": "in_progress",
                "owner": "Codex",
                "reviewer": "Codex2",
                "depends_on": [],
            },
            "BFF-CONSOL-017": {
                "id": "BFF-CONSOL-017",
                "status": "todo",
                "owner": "Codex",
                "reviewer": "Codex2",
                "depends_on": [],
            },
        }

        self.assertFalse(
            supervisor.higher_priority_ready_task_exists(
                config,
                state["workers"]["run-low"],
                task_map,
                state,
            )
        )

    def test_slotted_worker_is_not_preempted_for_non_urgent_owned_backlog(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["agents"]["codex"]["worker_slots"] = ["codex1_1", "codex1_2", "codex1_3", "codex1_4"]
        for slot_id in config["agents"]["codex"]["worker_slots"]:
            config["agents"][slot_id] = {
                "id": slot_id,
                "display_name": "Codex",
                "dispatch_slot_for": "codex",
                "provider": slot_id.replace("_", "-"),
            }
        state = {
            "queue": {"events": {}},
            "workers": {
                f"run-low-{index}": {
                    "run_id": f"run-low-{index}",
                    "task_id": f"BFF-CONSOL-0{20 + index}",
                    "agent_id": f"codex1_{index}",
                    "logical_agent_id": "codex",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_ready_dispatch"},
                }
                for index in range(1, 5)
            },
        }
        task_map = {
            f"BFF-CONSOL-0{20 + index}": {
                "id": f"BFF-CONSOL-0{20 + index}",
                "status": "todo",
                "owner": "Codex",
                "reviewer": "Claude",
                "depends_on": [],
            }
            for index in range(1, 5)
        }
        task_map["BFF-CONSOL-099"] = {
            "id": "BFF-CONSOL-099",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": [],
        }

        with mock.patch.object(supervisor, "load_event_queue", return_value=[]):
            self.assertFalse(
                supervisor.higher_priority_ready_task_exists(
                    config,
                    state["workers"]["run-low-1"],
                    task_map,
                    state,
                )
            )

    def test_dead_coordination_worker_is_completed_without_taskboard_entry(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {},
            "providers": {},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "F-042",
                    "provider": "codex",
                    "agent_id": "codex",
                    "status": "running",
                    "queue_event_id": "evt-1",
                    "pid": 999999,
                    "last_event_at": "2026-04-06T09:00:00Z",
                    "request_snapshot": {
                        "reason": "coordination:ui-done",
                        "metadata": {
                            "coordination": {
                                "feature_id": "F-042",
                                "worker_kind": "front-sync-worker",
                                "payload_type": "ui-done",
                            }
                        },
                    },
                }
            },
        }
        status = {"tasks": []}

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "detect_worker_failure", return_value=None),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "completed")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "completed")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_completed")


class QuotaPlanningAndCoordinationPollOrderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        (self.root / "ai-status.json").write_text('{"tasks": []}\n', encoding="utf-8")
        (self.root / "activity-log.jsonl").write_text("", encoding="utf-8")
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        self.config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "status_field": "status",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "paths": {
                "status_file": str(self.root / "ai-status.json"),
                "activity_log": str(self.root / "activity-log.jsonl"),
                "event_queue": str(self.root / "event-queue.jsonl"),
            },
            "account_pools": {
                "antigravity_main": {"max_concurrent": 2, "state": "healthy"},
            },
            "agents": {
                "antigravity": {
                    "id": "antigravity",
                    "display_name": "Antigravity",
                    "provider": "antigravity",
                    "account_pool": "antigravity_main",
                },
                "antigravity2": {
                    "id": "antigravity2",
                    "display_name": "Antigravity2",
                    "provider": "antigravity",
                    "account_pool": "antigravity_main",
                },
            },
            "providers": {"antigravity": {}},
            "ready_dispatcher": {
                "active_worker_statuses": ["running", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "stalled"],
                "review_statuses": ["review"],
                "owned_statuses": ["in_progress", "todo"],
                "done_statuses": ["done", "review_approved"],
            },
            "provider_guardrails": {
                "pause_on_capacity_failure": True,
                "quota_terminal_pause_seconds": 900,
            },
        }

    def _write_log(self, content: str) -> str:
        log_file = self.root / f"log-{uuid.uuid4().hex}.log"
        log_file.write_text(content, encoding="utf-8")
        return str(log_file)

    def test_failed_quota_planning_worker_with_exit_code_1_fences_account_pool_and_fails(self) -> None:
        log_path = self._write_log("free tier quota exceeded\n")
        state = {
            "queue": {"events": {"evt-plan-1": {"status": "started"}}},
            "workers": {
                "run-plan-1": {
                    "run_id": "run-plan-1",
                    "task_id": None,
                    "provider": "antigravity",
                    "agent_id": "antigravity",
                    "logical_agent_id": "antigravity",
                    "status": "running",
                    "queue_event_id": "evt-plan-1",
                    "pid": 999999,
                    "exit_code": 1,
                    "log_path": log_path,
                    "last_event_at": "2026-08-27T09:00:00Z",
                    "request_snapshot": {
                        "reason": "discussion_planning_baton_dispatch",
                        "metadata": {
                            "planning": {
                                "session_id": "sess-123",
                            },
                        },
                    },
                }
            },
            "account_pool_runtime": {},
            "provider_guardrails": {"dispatch_pauses": {}, "task_failure_streaks": {}},
        }
        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": []}),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "record_account_pool_canary_success") as canary_success,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.poll_workers(self.config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-plan-1"]
        self.assertEqual(worker["status"], "failed")
        self.assertTrue(worker.get("last_error"))
        pool_entry = state.get("account_pool_runtime", {}).get("antigravity_main", {})
        self.assertEqual(pool_entry.get("state"), "cooldown")
        canary_success.assert_not_called()

    def test_failed_quota_coordination_worker_with_exit_code_1_fences_account_pool_and_fails(self) -> None:
        log_path = self._write_log("402 You have no quota\n")
        state = {
            "queue": {"events": {"evt-coord-1": {"status": "started"}}},
            "workers": {
                "run-coord-1": {
                    "run_id": "run-coord-1",
                    "task_id": "F-042",
                    "provider": "antigravity",
                    "agent_id": "antigravity",
                    "logical_agent_id": "antigravity",
                    "status": "running",
                    "queue_event_id": "evt-coord-1",
                    "pid": 999999,
                    "exit_code": 1,
                    "log_path": log_path,
                    "last_event_at": "2026-08-27T09:00:00Z",
                    "request_snapshot": {
                        "reason": "coordination:ui-done",
                        "metadata": {
                            "coordination": {
                                "feature_id": "F-042",
                                "worker_kind": "front-sync-worker",
                                "payload_type": "ui-done",
                            },
                        },
                    },
                }
            },
            "account_pool_runtime": {},
            "provider_guardrails": {"dispatch_pauses": {}, "task_failure_streaks": {}},
        }
        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": []}),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.poll_workers(self.config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-coord-1"]
        self.assertEqual(worker["status"], "failed")
        pool_entry = state.get("account_pool_runtime", {}).get("antigravity_main", {})
        self.assertEqual(pool_entry.get("state"), "cooldown")

    def test_exit_code_0_planning_and_coordination_workers_succeed(self) -> None:
        log_path = self._write_log("Normal execution log with no error.\n")
        state = {
            "queue": {
                "events": {
                    "evt-plan": {"status": "started"},
                    "evt-coord": {"status": "started"},
                }
            },
            "workers": {
                "run-plan": {
                    "run_id": "run-plan",
                    "task_id": None,
                    "provider": "antigravity",
                    "agent_id": "antigravity",
                    "status": "running",
                    "queue_event_id": "evt-plan",
                    "pid": 999991,
                    "exit_code": 0,
                    "log_path": log_path,
                    "last_event_at": "2026-08-27T09:00:00Z",
                    "request_snapshot": {
                        "reason": "discussion_planning_baton_dispatch",
                        "metadata": {"planning": {"session_id": "sess-1"}},
                    },
                },
                "run-coord": {
                    "run_id": "run-coord",
                    "task_id": "F-001",
                    "provider": "antigravity",
                    "agent_id": "antigravity",
                    "status": "running",
                    "queue_event_id": "evt-coord",
                    "pid": 999992,
                    "exit_code": 0,
                    "log_path": log_path,
                    "last_event_at": "2026-08-27T09:00:00Z",
                    "request_snapshot": {
                        "reason": "coordination:sync",
                        "metadata": {"coordination": {"feature_id": "F-001"}},
                    },
                },
            },
        }
        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": []}),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "record_account_pool_canary_success") as canary_success,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.poll_workers(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-plan"]["status"], "completed")
        self.assertEqual(state["queue"]["events"]["evt-plan"]["status"], "completed")
        self.assertEqual(state["workers"]["run-coord"]["status"], "completed")
        self.assertEqual(state["queue"]["events"]["evt-coord"]["status"], "completed")
        canary_success.assert_called_once()

    def test_signal_terminated_planning_and_coordination_workers_do_not_succeed(self) -> None:
        log_path = self._write_log("status: 429 You have exceeded your quota\n")
        state = {
            "queue": {
                "events": {
                    "evt-plan": {"status": "started"},
                    "evt-coord": {"status": "started"},
                }
            },
            "workers": {
                "run-plan": {
                    "run_id": "run-plan",
                    "task_id": None,
                    "provider": "antigravity",
                    "agent_id": "antigravity",
                    "status": "running",
                    "queue_event_id": "evt-plan",
                    "pid": 999991,
                    "runner_signal": 15,
                    "exit_code": 0,
                    "log_path": log_path,
                    "last_event_at": "2026-08-27T09:00:00Z",
                    "request_snapshot": {
                        "reason": "discussion_planning_baton_dispatch",
                        "metadata": {"planning": {"session_id": "sess-1"}},
                    },
                },
                "run-coord": {
                    "run_id": "run-coord",
                    "task_id": "F-001",
                    "provider": "antigravity",
                    "agent_id": "antigravity",
                    "status": "running",
                    "queue_event_id": "evt-coord",
                    "pid": 999992,
                    "runner_signal": 9,
                    "exit_code": 0,
                    "log_path": log_path,
                    "last_event_at": "2026-08-27T09:00:00Z",
                    "request_snapshot": {
                        "reason": "coordination:sync",
                        "metadata": {"coordination": {"feature_id": "F-001"}},
                    },
                },
            },
        }
        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": []}),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "detect_worker_failure", wraps=supervisor.detect_worker_failure) as detect_failure,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.poll_workers(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-plan"]["status"], "failed")
        self.assertEqual(state["workers"]["run-coord"]["status"], "failed")
        self.assertEqual(detect_failure.call_count, 2)

    def test_exit_code_1_without_log_failure_does_not_succeed(self) -> None:
        log_path = self._write_log("No recognized error strings here.\n")
        state = {
            "queue": {
                "events": {
                    "evt-plan": {"status": "started"},
                    "evt-coord": {"status": "started"},
                }
            },
            "workers": {
                "run-plan": {
                    "run_id": "run-plan",
                    "task_id": None,
                    "provider": "antigravity",
                    "agent_id": "antigravity",
                    "status": "running",
                    "queue_event_id": "evt-plan",
                    "pid": 999991,
                    "exit_code": 1,
                    "log_path": log_path,
                    "last_event_at": "2026-08-27T09:00:00Z",
                    "request_snapshot": {
                        "reason": "discussion_planning_baton_dispatch",
                        "metadata": {"planning": {"session_id": "sess-1"}},
                    },
                },
                "run-coord": {
                    "run_id": "run-coord",
                    "task_id": "F-001",
                    "provider": "antigravity",
                    "agent_id": "antigravity",
                    "status": "running",
                    "queue_event_id": "evt-coord",
                    "pid": 999992,
                    "exit_code": 1,
                    "log_path": log_path,
                    "last_event_at": "2026-08-27T09:00:00Z",
                    "request_snapshot": {
                        "reason": "coordination:sync",
                        "metadata": {"coordination": {"feature_id": "F-001"}},
                    },
                },
            },
        }
        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": []}),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.poll_workers(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-plan"]["status"], "failed")
        self.assertEqual(state["workers"]["run-coord"]["status"], "failed")

    def test_only_structured_success_with_valid_outcome_short_circuits_failure_detection(self) -> None:
        task = {
            "id": "TASK-100",
            "status": "in_progress",
            "owner": "Antigravity",
            "reviewer": "Codex",
            "next": "Implement",
            "depends_on": [],
        }
        dispatch_task = dict(task, head="a" * 40)
        task_with_new_head = dict(task, head="b" * 40)
        log_path = self._write_log("Normal log\n")
        worker = {
            "run_id": "run-task-1",
            "task_id": "TASK-100",
            "provider": "antigravity",
            "agent_id": "antigravity",
            "status": "running",
            "queue_event_id": "evt-task-1",
            "pid": 999991,
            "runner_status": "completed",
            "exit_code": 0,
            "log_path": log_path,
            "last_event_at": "2026-08-27T09:00:00Z",
            "request_snapshot": {
                "reason": "owned_in_progress_dispatch",
                "metadata": {
                    "logical_agent_id": "antigravity",
                    "task": dispatch_task,
                },
            },
        }
        state = {
            "queue": {"events": {"evt-task-1": {"status": "started"}}},
            "workers": {"run-task-1": worker},
        }
        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task_with_new_head]}),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "resolve_task_progress_head", return_value="b" * 40),
            mock.patch.object(
                supervisor,
                "detect_worker_failure",
                side_effect=AssertionError("structured successful worker with valid outcome must short-circuit"),
            ),
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.poll_workers(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(worker["status"], "completed")
        self.assertEqual(worker["progress_outcome"], "incremental_progress")


class OrphanedQueueEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        (self.root / "ai-status.json").write_text('{"tasks": []}\n', encoding="utf-8")
        (self.root / "activity-log.jsonl").write_text("", encoding="utf-8")
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        self.config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "status_field": "status",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "paths": {
                "status_file": str(self.root / "ai-status.json"),
                "activity_log": str(self.root / "activity-log.jsonl"),
                "event_queue": str(self.root / "event-queue.jsonl"),
            },
            "ready_dispatcher": {
                "active_worker_statuses": [
                    "running",
                    "started",
                    "waiting_approval",
                    "suspended_approval",
                    "manual_pending",
                    "retry_backoff",
                    "stalled",
                ],
                "orphaned_queue_event_grace_seconds": 300,
            },
            "providers": {},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }

    def _write_event(self, payload: dict[str, object]) -> None:
        (self.root / "event-queue.jsonl").write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

    def test_outstanding_delivery_indexes_ignore_stale_orphan_event(self) -> None:
        self._write_event(
            {
                "event_id": "coord-old",
                "created_at": "2000-01-01T00:00:00Z",
                "event_key": "coordination:front-sync-worker:RW-05-artifact-compare:ui-done:old",
                "task_id": "RW-05-artifact-compare",
                "target_agent": "codex",
                "target_display_name": "Codex",
                "provider": "codex",
                "reason": "coordination:ui-done",
                "message": "stale event",
            }
        )
        state = {"queue": {"events": {}}, "workers": {}}

        agents, task_agents, event_keys = supervisor.outstanding_delivery_indexes(self.config, state)

        self.assertEqual(agents, set())
        self.assertEqual(task_agents, set())
        self.assertEqual(event_keys, set())

    def test_process_queue_skips_stale_orphan_event(self) -> None:
        self._write_event(
            {
                "event_id": "coord-old",
                "created_at": "2000-01-01T00:00:00Z",
                "event_key": "coordination:front-sync-worker:RW-05-artifact-compare:ui-done:old",
                "task_id": "RW-05-artifact-compare",
                "target_agent": "codex",
                "target_display_name": "Codex",
                "provider": "codex",
                "reason": "coordination:ui-done",
                "message": "stale event",
            }
        )
        state = {"queue": {"events": {}}, "workers": {}}

        with mock.patch.object(supervisor, "start_worker_for_request") as start_worker:
            changed = supervisor.process_queue(self.config, state, provider_report={})

        self.assertFalse(changed)
        start_worker.assert_not_called()
        self.assertEqual(state["queue"]["events"], {})

    def test_prune_event_queue_drops_stale_orphan_event(self) -> None:
        self._write_event(
            {
                "event_id": "coord-old",
                "created_at": "2000-01-01T00:00:00Z",
                "event_key": "coordination:front-sync-worker:RW-05-artifact-compare:ui-done:old",
                "task_id": "RW-05-artifact-compare",
                "target_agent": "codex",
                "target_display_name": "Codex",
                "provider": "codex",
                "reason": "coordination:ui-done",
                "message": "stale event",
            }
        )
        state = {"queue": {"events": {}}, "workers": {}}

        with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
            changed = supervisor.prune_event_queue(self.config, state)

        self.assertTrue(changed)
        self.assertEqual((self.root / "event-queue.jsonl").read_text(encoding="utf-8"), "")
        self.assertEqual(state["queue"]["events"], {})
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "queue_event_pruned")


class DeferredApprovalCorrelationTests(unittest.TestCase):
    task_id = "ODP-DEPLOY-JOB-SECRET-BINDING-SELECTION-001"
    command = "git commit -F /tmp/odp-secret-schema-msg.txt 2>&1 | tail -20"

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        self.config = {
            "paths": {
                "approval_queue": str(self.root / "approval-queue.json"),
                "state_file": str(self.root / "state.json"),
                "event_queue": str(self.root / "event-queue.jsonl"),
                "activity_log": str(self.root / "activity-log.jsonl"),
                "evidence_dir": str(self.root / "evidence"),
                "status_file": str(self.root / "ai-status.json"),
            },
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "active_worker_statuses": [
                    "running",
                    "waiting_approval",
                    "suspended_approval",
                    "manual_pending",
                ]
            },
            "providers": {"claude2": {"delivery_mode": "claude_cli"}},
            "agents": {
                "claude2": {"id": "claude2", "display_name": "Claude2"},
                "codex6": {"id": "codex6", "display_name": "Codex6"},
            },
        }
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        (self.root / "approval-queue.json").write_text(
            json.dumps({"pending": [], "history": []}),
            encoding="utf-8",
        )
        self.status = {
            "tasks": [
                {
                    "id": self.task_id,
                    "status": "in_progress",
                    "owner": "Claude2",
                    "reviewer": "Codex6",
                }
            ]
        }

    def _write_deferred_log(self) -> Path:
        log_path = self.root / "claude2.log"
        receipt = {
            "is_error": False,
            "stop_reason": "tool_deferred",
            "session_id": "7d919ae4-893a-400b-b13f-b45e5115184b",
            "terminal_reason": "tool_deferred",
            "deferred_tool_use": {
                "id": "toolu_01YZMtMekPkN7JQUG6AdSo1f",
                "name": "Bash",
                "input": {
                    "command": self.command,
                    "description": "Create task commit",
                },
            },
            "type": "result",
        }
        log_path.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
        return log_path

    def _state_for_deferred_log(self) -> dict[str, object]:
        return {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": self.task_id,
                    "provider": "claude2",
                    "agent_id": "claude2",
                    "status": "running",
                    "queue_event_id": "evt-1",
                    "pid": 999999,
                    "log_path": str(self._write_deferred_log()),
                    "last_event_at": "2026-07-28T16:48:00Z",
                }
            },
        }

    def _poll(self, state: dict[str, object], broker_decision: dict[str, str]) -> bool:
        with (
            mock.patch.object(
                supervisor,
                "load_approval_state",
                return_value={"pending": [], "history": []},
            ),
            mock.patch.object(supervisor, "load_status", return_value=self.status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(
                supervisor,
                "_deferred_tool_broker_decision",
                return_value=broker_decision,
            ),
        ):
            return supervisor.poll_workers(self.config, state)

    def test_normal_poll_makes_claude2_receipt_durable_before_dead_worker_cleanup(self) -> None:
        state = self._state_for_deferred_log()

        changed = self._poll(
            state,
            {"decision": "defer", "risk_class": "git_write"},
        )

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "suspended_approval")
        self.assertEqual(worker["session_id"], "7d919ae4-893a-400b-b13f-b45e5115184b")
        self.assertEqual(worker["deferred_tool_use"]["input"]["command"], self.command)
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "manual_pending")
        approval_state = supervisor.load_approval_state(self.config)
        self.assertEqual(len(approval_state["pending"]), 1)
        approval = approval_state["pending"][0]
        self.assertEqual(approval["worker_run_id"], "run-1")
        self.assertEqual(approval["tool_use_id"], "toolu_01YZMtMekPkN7JQUG6AdSo1f")
        self.assertEqual(approval["suggested_rule"], f"Bash({self.command})")
        self.assertEqual(worker["deferred_action"], approval["approval_id"])

    def test_boot_reconciliation_correlates_flushed_receipt_before_missing_process_failure(self) -> None:
        state = self._state_for_deferred_log()

        with (
            mock.patch.object(supervisor, "load_status", return_value=self.status),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(
                supervisor,
                "_deferred_tool_broker_decision",
                return_value={"decision": "defer", "risk_class": "git_write"},
            ),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.reconcile_runtime_on_boot(self.config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "waiting_approval")
        self.assertEqual(worker["session_id"], "7d919ae4-893a-400b-b13f-b45e5115184b")
        self.assertEqual(worker["deferred_tool_use"]["id"], "toolu_01YZMtMekPkN7JQUG6AdSo1f")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "started")
        approval_state = supervisor.load_approval_state(self.config)
        self.assertEqual(len(approval_state["pending"]), 1)
        self.assertEqual(approval_state["pending"][0]["worker_run_id"], "run-1")
        activity_types = [call.args[1]["type"] for call in write_activity_log.call_args_list]
        self.assertNotIn("worker_failed", activity_types)
        metrics = state.get("worker_runtime_metrics", {})
        self.assertEqual(metrics.get("totals", {}).get("missing_process_workers_failed", 0), 0)

    def test_boot_reconciliation_fails_closed_when_receipt_cannot_be_persisted(self) -> None:
        state = self._state_for_deferred_log()

        with (
            mock.patch.object(supervisor, "load_status", return_value=self.status),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(
                supervisor,
                "correlate_deferred_tool_approval",
                side_effect=OSError("approval queue unavailable"),
            ),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.reconcile_runtime_on_boot(self.config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "failed")
        self.assertIn("process missing", worker["last_error"])
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "failed")
        self.assertEqual(supervisor.load_approval_state(self.config)["pending"], [])
        activity_types = [call.args[1]["type"] for call in write_activity_log.call_args_list]
        self.assertIn("worker_deferred_approval_failed", activity_types)
        self.assertIn("worker_failed", activity_types)
        metrics = state["worker_runtime_metrics"]
        self.assertEqual(metrics["totals"]["missing_process_workers_failed"], 1)

    def test_allowed_scoped_commit_resumes_without_broad_bash(self) -> None:
        state = self._state_for_deferred_log()
        self._poll(state, {"decision": "defer", "risk_class": "git_write"})
        pending = supervisor.load_approval_state(self.config)["pending"][0]
        resolved = supervisor.resolve_approval(
            self.config,
            pending["approval_id"],
            decision="allow",
            note="Allow only the staged task commit.",
            remember=False,
        )
        resolved_state = supervisor.load_approval_state(self.config)

        def resume(
            _config: dict[str, object],
            worker: dict[str, object],
            _provider_report: dict[str, object],
            *,
            approval: dict[str, object],
        ) -> dict[str, object]:
            worker["status"] = "running"
            worker["deferred_tool_use"] = None
            return {
                "command": ["claude", "--resume", str(worker["session_id"])],
                "allowed_tools": supervisor._claude_resume_allowed_tools(approval),
            }

        with (
            mock.patch.object(
                supervisor,
                "load_approval_state",
                return_value=resolved_state,
            ),
            mock.patch.object(supervisor, "load_status", return_value=self.status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(
                supervisor,
                "resume_claude_worker",
                side_effect=resume,
            ) as resume_claude_worker,
        ):
            changed = supervisor.poll_workers(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-1"]["status"], "running")
        resume_claude_worker.assert_called_once()
        resumed_approval = resume_claude_worker.call_args.kwargs["approval"]
        self.assertEqual(resumed_approval["approval_id"], resolved["approval_id"])
        self.assertEqual(
            supervisor._claude_resume_allowed_tools(resumed_approval),
            [f"Bash({self.command})"],
        )
        self.assertNotIn("Bash", supervisor._claude_resume_allowed_tools(resumed_approval))

    def test_broker_denied_deferred_tool_fails_closed(self) -> None:
        state = self._state_for_deferred_log()

        changed = self._poll(
            state,
            {
                "decision": "deny",
                "risk_class": "forbidden",
                "reason": "Command is forbidden by policy.",
            },
        )

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "failed")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "failed")
        approval_state = supervisor.load_approval_state(self.config)
        self.assertEqual(approval_state["pending"], [])
        self.assertEqual(approval_state["history"][0]["decision"], "deny")
        self.assertEqual(approval_state["history"][0]["note"], "Command is forbidden by policy.")

    def test_missing_deferred_receipt_still_fails_closed(self) -> None:
        state = {
            "queue": {"events": {"evt-1": {"status": "manual_pending"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": self.task_id,
                    "provider": "claude2",
                    "agent_id": "claude2",
                    "status": "suspended_approval",
                    "queue_event_id": "evt-1",
                    "pid": 999999,
                    "session_id": "session-without-receipt",
                    "last_event_at": "2026-07-28T16:48:00Z",
                }
            },
        }

        changed = self._poll(
            state,
            {"decision": "defer", "risk_class": "git_write"},
        )

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "failed")
        self.assertEqual(
            worker["last_error"],
            "Approval state disappeared before the suspended worker could resume.",
        )
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "failed")


class PollWorkersRecoveryTests(unittest.TestCase):

    @staticmethod
    def _helper_lease_fixture(
        *,
        current_claim: dict[str, Any] | None = None,
        fresh_heartbeat: bool = True,
        fresh_process_activity: bool = False,
        supersede_deferred_since: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        now = datetime.now(UTC).replace(microsecond=0)
        now_text = now.isoformat().replace("+00:00", "Z")
        expired_claim = {
            "claimed_by": "Codex",
            "generation": 7,
            "lease_expires_at": (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        }
        dispatched_task = {
            "id": "HELPER-LEASE-001",
            "status": "todo",
            "owner": "Claude",
            "reviewer": "Gemini",
            "helper_execution_lease": expired_claim,
        }
        worker = {
            "run_id": "helper-run-1",
            "task_id": dispatched_task["id"],
            "provider": "codex",
            "agent_id": "codex",
            "logical_agent_id": "codex",
            "status": "running",
            "queue_event_id": "evt-helper-1",
            "pid": 4321,
            "last_event_at": now_text,
            "last_heartbeat_at": now_text if fresh_heartbeat else None,
            "last_process_activity_at": now_text if fresh_process_activity else None,
            "lease_expires_at": (now + timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
            "request_snapshot": {
                "reason": "helper_claim_dispatch",
                "metadata": {
                    "logical_agent_id": "codex",
                    "task": dispatched_task,
                },
            },
        }
        if supersede_deferred_since is not None:
            worker["supersede_deferred_since"] = supersede_deferred_since
        task = {
            "id": dispatched_task["id"],
            "status": "todo",
            "owner": "Claude",
            "reviewer": "Gemini",
            "depends_on": [],
        }
        if current_claim is not None:
            task["helper_execution_lease"] = current_claim
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "worker_runtime": {
                "worker_lease_seconds": 1800,
                "heartbeat_stale_seconds": 300,
                "heartbeat_grace_seconds": 60,
                "supersede_grace_seconds": 120,
            },
            "ready_dispatcher": {
                "owned_statuses": ["todo", "in_progress"],
                "active_worker_statuses": ["running", "waiting_approval", "manual_pending", "stalled"],
            },
            "providers": {},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex"},
                "claude": {"id": "claude", "display_name": "Claude"},
                "gemini": {"id": "gemini", "display_name": "Gemini"},
            },
        }
        state = {
            "queue": {"events": {"evt-helper-1": {"status": "started"}}},
            "workers": {worker["run_id"]: worker},
        }
        return config, state, {"tasks": [task]}

    @staticmethod
    def _poll_helper_worker(config: dict[str, Any], state: dict[str, Any], status: dict[str, Any]) -> mock.Mock:
        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "higher_priority_ready_task_exists", return_value=False),
            mock.patch.object(supervisor, "detect_worker_failure", return_value=None),
            mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            supervisor.poll_workers(config, state)
        return terminate_worker_pid

    def test_expired_helper_claim_does_not_kill_live_worker_with_fresh_heartbeat(self) -> None:
        config, state, status = self._helper_lease_fixture()

        terminate_worker_pid = self._poll_helper_worker(config, state, status)

        worker = state["workers"]["helper-run-1"]
        self.assertEqual(worker["status"], "running")
        self.assertNotIn("supersede_deferred_since", worker)
        terminate_worker_pid.assert_not_called()

    def test_expired_helper_claim_accepts_fresh_process_activity_without_heartbeat(self) -> None:
        config, state, status = self._helper_lease_fixture(
            fresh_heartbeat=False,
            fresh_process_activity=True,
        )

        terminate_worker_pid = self._poll_helper_worker(config, state, status)

        self.assertEqual(state["workers"]["helper-run-1"]["status"], "running")
        terminate_worker_pid.assert_not_called()

    def test_expired_same_generation_claim_retained_on_task_does_not_kill_live_worker(self) -> None:
        now = datetime.now(UTC).replace(microsecond=0)
        current_claim = {
            "claimed_by": "Codex",
            "generation": 7,
            "lease_expires_at": (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        }
        config, state, status = self._helper_lease_fixture(current_claim=current_claim)

        terminate_worker_pid = self._poll_helper_worker(config, state, status)

        worker = state["workers"]["helper-run-1"]
        self.assertEqual(worker["status"], "running")
        self.assertNotIn("supersede_deferred_since", worker)
        terminate_worker_pid.assert_not_called()

    def test_changed_helper_claim_generation_still_uses_bounded_supersede(self) -> None:
        now = datetime.now(UTC).replace(microsecond=0)
        current_claim = {
            "claimed_by": "Gemini",
            "generation": 8,
            "lease_expires_at": (now + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
        }
        deferred_since = (now - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
        config, state, status = self._helper_lease_fixture(
            current_claim=current_claim,
            supersede_deferred_since=deferred_since,
        )

        terminate_worker_pid = self._poll_helper_worker(config, state, status)

        worker = state["workers"]["helper-run-1"]
        self.assertEqual(worker["status"], "superseded")
        self.assertEqual(state["queue"]["events"]["evt-helper-1"]["status"], "completed")
        terminate_worker_pid.assert_called_once_with(4321)

    def test_owner_or_reviewer_change_still_uses_bounded_supersede(self) -> None:
        now = datetime.now(UTC).replace(microsecond=0)
        current_claim = {
            "claimed_by": "Codex",
            "generation": 7,
            "lease_expires_at": (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        }
        deferred_since = (now - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")

        for changed_field, changed_identity in (("owner", "Gemini"), ("reviewer", "Claude")):
            with self.subTest(changed_field=changed_field):
                config, state, status = self._helper_lease_fixture(
                    current_claim=current_claim,
                    supersede_deferred_since=deferred_since,
                )
                status["tasks"][0][changed_field] = changed_identity

                terminate_worker_pid = self._poll_helper_worker(config, state, status)

                worker = state["workers"]["helper-run-1"]
                self.assertEqual(worker["status"], "superseded")
                self.assertEqual(state["queue"]["events"]["evt-helper-1"]["status"], "completed")
                terminate_worker_pid.assert_called_once_with(4321)

    def test_lower_priority_worker_is_superseded_when_finalize_backlog_exists(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "active_worker_statuses": ["running", "started", "waiting_approval", "manual_pending", "retry_backoff", "suspended_approval", "stalled", "fallback"],
                "finalize_statuses": ["review_approved"],
                "dependency_done_statuses": ["done"],
            },
            "providers": {},
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot"},
                "codex": {"id": "codex", "display_name": "Codex"},
                "claude": {"id": "claude", "display_name": "Claude"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "FB-003",
                    "provider": "copilot",
                    "agent_id": "copilot",
                    "status": "running",
                    "queue_event_id": "evt-1",
                    "pid": 12345,
                    "last_event_at": "2026-04-06T09:00:00Z",
                    "request_snapshot": {"reason": "owned_ready_dispatch"},
                }
            },
        }
        status = {
            "tasks": [
                {"id": "FB-003", "status": "todo", "owner": "Copilot", "reviewer": "Codex", "depends_on": []},
                {"id": "EX-001", "status": "review_approved", "approved_head": "1111111122222222333333334444444455555555", "owner": "Copilot", "reviewer": "Claude", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
            mock.patch.object(supervisor, "detect_worker_failure", return_value=None),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch("ai_status.resolve_task_sha", return_value="1111111122222222333333334444444455555555"),
            mock.patch("ai_status.task_pr_ci_status", return_value=("MERGED", "success")),
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "superseded")
        self.assertIn("prioritize higher-priority review/finalize work", worker["last_error"])
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "completed")
        terminate_worker_pid.assert_called_once_with(12345)
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_superseded")


    def test_dead_worker_for_open_task_is_marked_failed_not_completed(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {},
            "providers": {},
            "agents": {
                "claude": {"id": "claude", "display_name": "Claude"},
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "EX-001",
                    "provider": "codex",
                    "agent_id": "codex",
                    "status": "running",
                    "queue_event_id": "evt-1",
                    "pid": 999999,
                    "last_event_at": "2026-04-06T09:00:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "EX-001", "status": "in_progress", "owner": "Codex", "reviewer": "Claude"}]}

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "detect_worker_failure", return_value=None),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "failed")
        self.assertEqual(worker["last_error"], supervisor.NO_PROGRESS_WORKER_EXIT_REASON)
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "failed")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_failed")

    def test_dead_waiting_approval_worker_is_failed_and_approval_is_resolved(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {},
            "providers": {},
            "agents": {
                "claude": {"id": "claude", "display_name": "Claude"},
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "manual_pending"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "OC-002",
                    "provider": "claude",
                    "agent_id": "claude",
                    "status": "waiting_approval",
                    "queue_event_id": "evt-1",
                    "pid": 999999,
                    "last_event_at": "2026-04-06T09:00:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "OC-002", "status": "review", "owner": "Codex", "reviewer": "Claude"}]}
        approval_state = {
            "pending": [
                {
                    "approval_id": "apr-1",
                    "worker_run_id": "run-1",
                    "task_id": "OC-002",
                    "provider": "claude",
                    "tool_name": "Bash",
                    "created_at": "2026-04-06T09:01:00Z",
                }
            ],
            "history": [],
        }

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value=approval_state),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "resolve_approval") as resolve_approval,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "failed")
        self.assertEqual(worker["last_error"], "Worker exited while waiting for approval.")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "failed")
        resolve_approval.assert_called_once_with(
            config,
            "apr-1",
            decision="deny",
            note="Auto-denied because the worker exited before approval could be applied.",
            remember=False,
        )
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_failed")

    def test_dead_claude_waiting_approval_worker_with_session_is_suspended(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "active_worker_statuses": [
                    "running",
                    "waiting_approval",
                    "suspended_approval",
                    "manual_pending",
                ]
            },
            "providers": {},
            "agents": {
                "claude": {"id": "claude", "display_name": "Claude"},
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "manual_pending"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "LP-004",
                    "provider": "claude",
                    "agent_id": "claude",
                    "status": "waiting_approval",
                    "queue_event_id": "evt-1",
                    "pid": 999999,
                    "session_id": "sess-123",
                    "resume_token": "sess-123",
                    "last_event_at": "2026-04-06T09:00:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "LP-004", "status": "in_progress", "owner": "Claude", "reviewer": "Codex"}]}
        approval_state = {
            "pending": [
                {
                    "approval_id": "apr-1",
                    "worker_run_id": "run-1",
                    "task_id": "LP-004",
                    "provider": "claude",
                    "tool_name": "ToolSearch",
                    "created_at": "2026-04-06T09:01:00Z",
                }
            ],
            "history": [],
        }

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value=approval_state),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "resolve_approval") as resolve_approval,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "suspended_approval")
        self.assertEqual(worker["deferred_action"], "apr-1")
        self.assertEqual(worker["last_event_at"], "2026-04-06T09:01:00Z")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "manual_pending")
        resolve_approval.assert_not_called()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_waiting_approval")

    def test_dead_claude2_waiting_approval_worker_with_session_is_suspended(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "active_worker_statuses": [
                    "running",
                    "waiting_approval",
                    "suspended_approval",
                    "manual_pending",
                ]
            },
            "providers": {"claude2": {"delivery_mode": "claude_cli"}},
            "agents": {
                "claude2": {"id": "claude2", "display_name": "Claude2"},
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "manual_pending"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "LP-005",
                    "provider": "claude2",
                    "agent_id": "claude2",
                    "status": "waiting_approval",
                    "queue_event_id": "evt-1",
                    "pid": 999999,
                    "session_id": "sess-456",
                    "resume_token": "sess-456",
                    "last_event_at": "2026-04-06T09:00:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "LP-005", "status": "in_progress", "owner": "Claude2", "reviewer": "Codex"}]}
        approval_state = {
            "pending": [
                {
                    "approval_id": "apr-2",
                    "worker_run_id": "run-1",
                    "task_id": "LP-005",
                    "provider": "claude2",
                    "tool_name": "ToolSearch",
                    "created_at": "2026-04-06T09:01:00Z",
                }
            ],
            "history": [],
        }

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value=approval_state),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "resolve_approval") as resolve_approval,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "suspended_approval")
        self.assertEqual(worker["deferred_action"], "apr-2")
        self.assertEqual(worker["last_event_at"], "2026-04-06T09:01:00Z")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "manual_pending")
        resolve_approval.assert_not_called()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_waiting_approval")

    def test_dead_stale_worker_is_reaped_when_task_assignment_moved(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "owned_statuses": ["in_progress", "todo"],
                "done_statuses": ["done", "review_approved"],
                "active_worker_statuses": ["running", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "stalled"],
            },
            "providers": {},
            "agents": {
                "codex": {"id": "codex", "name": "Codex"},
                "claude": {"id": "claude", "name": "Claude"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "manual_pending"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "EX-001",
                    "provider": "codex",
                    "agent_id": "codex",
                    "status": "manual_pending",
                    "queue_event_id": "evt-1",
                    "pid": None,
                    "last_event_at": "2026-04-06T09:00:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "EX-001", "status": "review", "owner": "Grok", "reviewer": "Claude"}]}

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-1"]["status"], "superseded")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "completed")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_superseded")

    def test_stalled_worker_returns_to_running_after_new_log_activity(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "owned_statuses": ["in_progress", "todo"],
                "done_statuses": ["done", "review_approved"],
                "active_worker_statuses": ["running", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "stalled"],
            },
            "providers": {},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "LP-002",
                    "provider": "codex",
                    "agent_id": "codex",
                    "status": "stalled",
                    "queue_event_id": "evt-1",
                    "pid": 1234,
                    "last_event_at": "2026-04-06T14:20:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "LP-002", "status": "in_progress", "owner": "Codex", "reviewer": "Copilot"}]}

        def bump_log_activity(_config, worker):
            worker["last_event_at"] = "2026-04-06T14:31:28Z"

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "update_from_log", side_effect=bump_log_activity),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-1"]["status"], "running")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_recovered")

    def test_stalled_worker_is_terminated_after_extended_stall(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "owned_statuses": ["todo", "in_progress"],
                "active_worker_statuses": ["running", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "stalled"],
            },
            "providers": {},
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "FB-003",
                    "provider": "copilot",
                    "agent_id": "copilot",
                    "status": "stalled",
                    "queue_event_id": "evt-1",
                    "pid": 1234,
                    "last_event_at": "2026-04-06T14:00:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "FB-003", "status": "todo", "owner": "Copilot", "reviewer": "Codex"}]}

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "update_from_log", side_effect=lambda *_args, **_kwargs: None),
            mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-1"]["status"], "failed")
        terminate_worker_pid.assert_called_once_with(1234)
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "failed")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_failed")

    def test_alive_worker_is_superseded_after_reassignment(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "owned_statuses": ["in_progress", "todo"],
                "done_statuses": ["done", "review_approved"],
                "active_worker_statuses": ["running", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "stalled"],
            },
            "providers": {},
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot"},
                "gemini": {"id": "gemini", "display_name": "Gemini"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "REG-002",
                    "provider": "copilot",
                    "agent_id": "copilot",
                    "status": "stalled",
                    "queue_event_id": "evt-1",
                    "pid": 2222,
                    "last_event_at": "2026-04-06T14:19:47Z",
                }
            },
        }
        status = {"tasks": [{"id": "REG-002", "status": "review", "owner": "Codex", "reviewer": "Gemini"}]}

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "terminate_worker_pid", return_value=True) as terminate_worker_pid,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-1"]["status"], "superseded")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "completed")
        terminate_worker_pid.assert_called_once_with(2222)
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_superseded")

    def _reassigned_supersede_fixture(self, worker_overrides: dict) -> tuple[dict, dict, dict]:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "owned_statuses": ["in_progress", "todo"],
                "done_statuses": ["done", "review_approved"],
                "active_worker_statuses": ["running", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "stalled"],
            },
            "providers": {},
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot"},
                "gemini": {"id": "gemini", "display_name": "Gemini"},
            },
        }
        worker = {
            "run_id": "run-1",
            "task_id": "REG-002",
            "provider": "copilot",
            "agent_id": "copilot",
            "status": "running",
            "queue_event_id": "evt-1",
            "pid": 2222,
            "last_event_at": "2026-04-06T14:19:47Z",
        }
        worker.update(worker_overrides)
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {"run-1": worker},
        }
        status = {"tasks": [{"id": "REG-002", "status": "review", "owner": "Codex", "reviewer": "Gemini"}]}
        return config, state, status

    def test_fresh_alive_worker_supersede_is_deferred_within_grace(self) -> None:
        # A worker that just handed its task off keeps a fresh heartbeat while it
        # tears down and flushes its final status write. It must NOT be killed
        # inside the grace window (that truncates the write and causes churn).
        config, state, status = self._reassigned_supersede_fixture(
            {"last_heartbeat_at": supervisor.utc_now()}
        )

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "terminate_worker_pid", return_value=True) as terminate_worker_pid,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "running")
        self.assertNotEqual(worker["status"], "superseded")
        self.assertIsNotNone(worker.get("supersede_deferred_since"))
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "started")
        terminate_worker_pid.assert_not_called()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_supersede_deferred")

    def test_fresh_alive_worker_is_superseded_after_grace_exhausted(self) -> None:
        # Once the grace window elapses and the worker still has not exited, the
        # supervisor reclaims it exactly as before.
        deferred_since = (datetime.now(UTC) - timedelta(seconds=600)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        config, state, status = self._reassigned_supersede_fixture(
            {
                "last_heartbeat_at": supervisor.utc_now(),
                "supersede_deferred_since": deferred_since,
            }
        )

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "terminate_worker_pid", return_value=True) as terminate_worker_pid,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "superseded")
        self.assertNotIn("supersede_deferred_since", worker)
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "completed")
        terminate_worker_pid.assert_called_once_with(2222)
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_superseded")


class SingleSupervisorGuardTests(unittest.TestCase):
    def test_cmdline_match_requires_supervisor_as_executable_or_python_script(self) -> None:
        script = str(Path(supervisor.__file__).resolve())

        self.assertTrue(supervisor.cmdline_is_supervisor_process(["python3", ".orchestrator/supervisor.py", "--verbose"]))
        self.assertTrue(supervisor.cmdline_is_supervisor_process(["python3", script, "--poll-interval", "15"]))
        self.assertTrue(supervisor.cmdline_is_supervisor_process([".orchestrator/supervisor.py", "--once"]))

    def test_cmdline_match_ignores_wrapper_processes(self) -> None:
        self.assertFalse(
            supervisor.cmdline_is_supervisor_process(["timeout", "20s", "python3", ".orchestrator/supervisor.py", "--once"])
        )
        self.assertFalse(
            supervisor.cmdline_is_supervisor_process(["bash", "-lc", "python3 .orchestrator/supervisor.py --verbose"])
        )

    def test_terminate_other_supervisors_kills_all_matching_except_self(self) -> None:
        # Singleton semantics: the flock winner terminates every other matching
        # supervisor regardless of PID ordering. 404 > 202 must still be killed
        # (PID wraparound previously let a higher-PID older supervisor survive).
        config = {"activity_log": "/tmp/fake-log.jsonl"}
        killed: list[tuple[int, int]] = []
        alive = {101: True, 202: True, 404: True}

        def fake_kill(pid: int, sig: int) -> None:
            killed.append((pid, sig))
            if sig in {supervisor.signal.SIGTERM, supervisor.signal.SIGKILL}:
                alive[pid] = False

        with (
            mock.patch.object(supervisor, "iter_matching_supervisor_pids", return_value=[101, 202, 404]),
            mock.patch.object(supervisor, "pid_is_alive", side_effect=lambda pid: alive.get(pid, False)),
            mock.patch.object(supervisor.os, "getpid", return_value=202),
            mock.patch.object(supervisor.os, "kill", side_effect=fake_kill),
            mock.patch.object(supervisor.time, "sleep"),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            supervisor.terminate_other_supervisors(config)

        self.assertEqual(
            killed,
            [(101, supervisor.signal.SIGTERM), (404, supervisor.signal.SIGTERM)],
        )
        self.assertEqual(write_activity_log.call_count, 2)
        terminated_pids = {
            call.args[1]["old_pid"] for call in write_activity_log.call_args_list
        }
        self.assertEqual(terminated_pids, {101, 404})
        for call in write_activity_log.call_args_list:
            self.assertEqual(call.args[1]["type"], "supervisor_replaced")
            self.assertEqual(call.args[1]["new_pid"], 202)

    def test_singleton_lock_is_exclusive_and_released_on_close(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            config = {"paths": {"state_file": str(Path(tmp) / "runtime-state.json")}}

            # First acquirer wins.
            self.assertTrue(supervisor.acquire_singleton_lock(config))
            first_handle = supervisor._SINGLETON_LOCK_HANDLE
            self.assertIsNotNone(first_handle)
            # pid file content reflects the owner.
            self.assertEqual(
                supervisor.supervisor_lock_path(config).read_text(encoding="utf-8").strip(),
                str(supervisor.os.getpid()),
            )

            # A concurrent acquirer (separate fd) is refused while the lock is held.
            import fcntl as _fcntl

            contender = open(supervisor.supervisor_lock_path(config), "a+", encoding="utf-8")
            try:
                with self.assertRaises(OSError):
                    _fcntl.flock(
                        contender.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB
                    )
            finally:
                contender.close()

            # Releasing (process exit simulated by closing the fd) frees the lock.
            first_handle.close()
            regained = open(supervisor.supervisor_lock_path(config), "a+", encoding="utf-8")
            try:
                _fcntl.flock(regained.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            finally:
                _fcntl.flock(regained.fileno(), _fcntl.LOCK_UN)
                regained.close()


class WorktreeDirtClassificationTests(unittest.TestCase):
    def test_clean_status(self) -> None:
        self.assertEqual(supervisor._classify_worktree_dirt(""), ("clean", []))
        self.assertEqual(supervisor._classify_worktree_dirt("\n  \n"), ("clean", []))

    def test_recorded_seed_paths_are_reusable(self) -> None:
        # Only Supervisor-recorded context paths are classified as scratch_only.
        status = (
            "?? .orchestrator/task-briefs/mgmt_ai_persist_p1_attach_007.md\n"
            "?? .orchestrator/reviews/mgmt_ai_persist_p1_attach_007_review.md\n"
        )
        kind, paths = supervisor._classify_worktree_dirt(
            status,
            materialized_paths=[
                ".orchestrator/task-briefs/mgmt_ai_persist_p1_attach_007.md",
                ".orchestrator/reviews/mgmt_ai_persist_p1_attach_007_review.md",
            ],
        )
        self.assertEqual(kind, "scratch_only")
        self.assertEqual(
            set(paths),
            {
                ".orchestrator/task-briefs/mgmt_ai_persist_p1_attach_007.md",
                ".orchestrator/reviews/mgmt_ai_persist_p1_attach_007_review.md",
            },
        )

    def test_tracked_or_staged_context_dirt_classified_as_real(self) -> None:
        # Tracked/staged modifications under context or scratch paths are real dirt.
        status = (
            "MM .orchestrator/task-briefs/mgmt_ai_persist_p1_attach_007.md\n"
            " M ai-status.json\n"
        )
        kind, paths = supervisor._classify_worktree_dirt(status)
        self.assertEqual(kind, "real")
        self.assertEqual(paths, [])

    def test_real_product_dirt_still_blocks(self) -> None:
        status = (
            " M .orchestrator/task-briefs/asst_integ_004.md\n"
            " M services/control-plane/bff/main.py\n"
        )
        kind, paths = supervisor._classify_worktree_dirt(status)
        self.assertEqual(kind, "real")
        self.assertEqual(paths, [])

    def test_rename_uses_new_path(self) -> None:
        status = "R  old/file.py -> services/new/file.py\n"
        kind, _ = supervisor._classify_worktree_dirt(status)
        self.assertEqual(kind, "real")

    def test_skills_seeded_by_the_orchestrator_are_not_owner_dirt(self) -> None:
        # A repository that does not version-control .orchestrator/ sees the skill files
        # the supervisor materialized as untracked; they must not deny the lease.
        status = (
            "?? .orchestrator/skills/worker-anchor-commit.md\n"
            "?? .orchestrator/skills/task-closeout-finalization.md\n"
            "?? .orchestrator/task-briefs/dpf_gov_001.md\n"
        )
        kind, paths = supervisor._classify_worktree_dirt(
            status,
            materialized_paths=[
                ".orchestrator/skills/worker-anchor-commit.md",
                ".orchestrator/skills/task-closeout-finalization.md",
                ".orchestrator/task-briefs/dpf_gov_001.md",
            ],
        )
        self.assertEqual(kind, "scratch_only")
        self.assertEqual(len(paths), 3)

    def test_materialized_context_outside_orchestrator_is_allowlisted(self) -> None:
        status = "?? docs/source/spec.md\n"
        self.assertEqual(supervisor._classify_worktree_dirt(status)[0], "real")
        kind, paths = supervisor._classify_worktree_dirt(
            status,
            materialized_paths=["docs/source/spec.md"],
        )
        self.assertEqual(kind, "scratch_only")
        self.assertEqual(paths, ["docs/source/spec.md"])

    def test_materialized_directory_covers_its_children(self) -> None:
        status = "?? docs/source/nested/spec.md\n"
        kind, _ = supervisor._classify_worktree_dirt(
            status,
            materialized_paths=["docs/source"],
        )
        self.assertEqual(kind, "scratch_only")

    def test_allowlist_does_not_excuse_tracked_edits_or_other_paths(self) -> None:
        allowed = [".orchestrator/skills/worker-anchor-commit.md"]
        tracked_edit = " M .orchestrator/skills/worker-anchor-commit.md\n"
        self.assertEqual(
            supervisor._classify_worktree_dirt(tracked_edit, materialized_paths=allowed)[0],
            "real",
        )
        owner_untracked = "?? services/control-plane/new_module.py\n"
        self.assertEqual(
            supervisor._classify_worktree_dirt(owner_untracked, materialized_paths=allowed)[0],
            "real",
        )

    def test_allowlist_rejects_traversal_and_absolute_entries(self) -> None:
        status = "?? escaped.py\n"
        kind, _ = supervisor._classify_worktree_dirt(
            status,
            materialized_paths=["../escaped.py", "/etc/escaped.py", "", "   "],
        )
        self.assertEqual(kind, "real")

    def test_blocking_entries_exclude_orchestrator_seeds(self) -> None:
        entries = supervisor._parse_porcelain_entries(
            "?? .orchestrator/skills/worker-anchor-commit.md\n"
            "?? owner_notes.md\n"
        )
        blocking = supervisor._blocking_dirt_entries(
            entries,
            materialized_paths=[".orchestrator/skills/worker-anchor-commit.md"],
        )
        self.assertEqual(blocking, [("??", "owner_notes.md")])


class DirtDescriptionTests(unittest.TestCase):
    """A lease block must name what git actually reported, not a fixed guess."""

    def test_untracked_only_is_not_described_as_tracked_or_staged(self) -> None:
        entries = supervisor._parse_porcelain_entries(
            "?? .orchestrator/skills/worker-anchor-commit.md\n"
            "?? .orchestrator/task-briefs/dpf_gov_001.md\n"
        )
        detail = supervisor._describe_dirt_entries(entries)
        self.assertIn("2 dirty changes", detail)
        self.assertIn("2 untracked", detail)
        self.assertNotIn("staged", detail)
        self.assertIn(".orchestrator/task-briefs/dpf_gov_001.md", detail)

    def test_mixed_states_are_counted_separately(self) -> None:
        entries = supervisor._parse_porcelain_entries(
            "M  staged.py\n"
            " M unstaged.py\n"
            "?? new.py\n"
        )
        detail = supervisor._describe_dirt_entries(entries)
        self.assertIn("3 dirty changes", detail)
        self.assertIn("1 staged", detail)
        self.assertIn("1 unstaged tracked", detail)
        self.assertIn("1 untracked", detail)

    def test_long_lists_are_truncated(self) -> None:
        entries = [("??", f"file{index}.py") for index in range(9)]
        detail = supervisor._describe_dirt_entries(entries)
        self.assertIn("9 dirty changes", detail)
        self.assertIn("+4 more", detail)

    def test_detail_round_trips_through_the_refresh_status_token(self) -> None:
        status = "skipped_dirty_worktree: 1 dirty change (1 untracked): new.py"
        self.assertEqual(supervisor._lease_status_kind(status), "skipped_dirty_worktree")
        self.assertTrue(supervisor._is_skipped_dirty_worktree(status))
        self.assertTrue(supervisor._is_skipped_dirty_worktree("skipped_dirty_worktree"))
        self.assertFalse(supervisor._is_skipped_dirty_worktree("wrong_branch: x"))
        self.assertEqual(
            supervisor._dirty_worktree_detail(status),
            "1 dirty change (1 untracked): new.py",
        )
        self.assertEqual(supervisor._dirty_worktree_detail("skipped_dirty_worktree"), "dirty changes")

class UnversionedOrchestratorWorkspaceLeaseTests(unittest.TestCase):
    """Regression for OPS-MATERIALIZED-CONTEXT-DIRT-001.

    A repository that does not version-control `.orchestrator/` receives the worker
    context files as untracked writes from the supervisor itself. Read as owner dirt,
    those writes denied every later lease of the very workspace the orchestrator had
    just seeded, so the task could never be dispatched into it again.
    """

    task_id = "DPF-GOV-001"
    branch = "task/DPF-GOV-001"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)

        self.remote = root / "remote.git"
        self._git(root, "init", "--bare", str(self.remote))

        self.repo_root = root / "data_platform"
        self._git(root, "clone", str(self.remote), str(self.repo_root))
        self._git(self.repo_root, "config", "user.name", "Supervisor Test")
        self._git(self.repo_root, "config", "user.email", "supervisor-test@example.invalid")
        self._git(self.repo_root, "checkout", "-b", "dev")
        # The whole point: this repository version-controls product files only. It has
        # no .orchestrator/ directory under version control.
        (self.repo_root / "README.md").write_text("data platform\n", encoding="utf-8")
        self._git(self.repo_root, "add", "README.md")
        self._git(self.repo_root, "commit", "-m", "initial")
        self._git(self.repo_root, "push", "-u", "origin", "dev")
        self._git(self.repo_root, "checkout", "-b", self.branch)
        self._git(self.repo_root, "push", "-u", "origin", self.branch)
        self._git(self.repo_root, "checkout", "dev")

        (self.repo_root / "ai-status.json").write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": self.task_id,
                            "title": "Cross repository governance",
                            "status": "in_progress",
                            "owner": "Claude",
                            "reviewer": "Antigravity",
                            "phase": "Unassigned",
                            "branch": self.branch,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        skills_dir = self.repo_root / ".orchestrator" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "worker-anchor-commit.md").write_text("# anchor\n", encoding="utf-8")
        (skills_dir / "task-closeout-finalization.md").write_text("# closeout\n", encoding="utf-8")

        self.worktree = root / "workers" / supervisor._task_id_slug(self.task_id)
        self.worktree.parent.mkdir(parents=True, exist_ok=True)
        self._git(self.repo_root, "worktree", "add", str(self.worktree), self.branch)
        self._git(self.worktree, "config", "user.name", "Supervisor Test")
        self._git(self.worktree, "config", "user.email", "supervisor-test@example.invalid")

        self.config = {
            "paths": {
                "status_file": str(self.repo_root / "ai-status.json"),
                "activity_log": str(self.repo_root / "ai-activity-log.jsonl"),
            },
            "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
            "worker_worktrees": {
                "enabled": True,
                "root": str(root / "workers"),
                "base_ref": "origin/dev",
                "reuse_existing": True,
            },
        }
        self.context_files = [
            ".orchestrator/skills/worker-anchor-commit.md",
            ".orchestrator/skills/task-closeout-finalization.md",
        ]

    def _git(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)

    def _dispatch(self, state: dict[str, Any]) -> tuple[bool, str | None, Any]:
        request = supervisor.DeliveryRequest(
            agent_id="claude",
            provider="claude",
            delivery_mode="claude",
            message="wake",
            task_id=self.task_id,
            reason="owned_in_progress_dispatch",
            context_files=list(self.context_files),
        )
        ok, message = supervisor.prepare_worker_workspace(
            self.config,
            state,
            request,
            queue_event_id="evt-unversioned",
            target_agent="Claude",
        )
        return ok, message, request

    def _refresh(self) -> tuple[bool, str]:
        return supervisor._refresh_reused_worker_worktree(
            self.repo_root,
            self.worktree,
            "origin/dev",
            self.branch,
            materialized_paths=self.context_files,
        )

    def test_repeated_dispatch_never_accumulates_a_materialized_context_block(self) -> None:
        state: dict[str, Any] = {}

        ok, message, request = self._dispatch(state)
        self.assertTrue(ok, message)
        self.assertEqual(Path(request.metadata["workspace_path"]), self.worktree)
        for rel in self.context_files:
            self.assertTrue((self.worktree / rel).exists(), rel)

        # The supervisor's own writes are untracked in this repository.
        untracked = self._git(
            self.worktree, "status", "--porcelain=v1", "--untracked-files=all"
        ).stdout
        self.assertIn(".orchestrator/skills/worker-anchor-commit.md", untracked)

        # Every later dispatch must still lease the same worktree.
        for _ in range(3):
            ok, message, request = self._dispatch(state)
            self.assertTrue(ok, message)
            self.assertEqual(Path(request.metadata["workspace_path"]), self.worktree)

    def test_materialized_context_alone_does_not_deny_the_lease(self) -> None:
        state: dict[str, Any] = {}
        ok, message, _ = self._dispatch(state)
        self.assertTrue(ok, message)

        ok, status = self._refresh()

        self.assertTrue(ok, status)
        self.assertFalse(supervisor._is_skipped_dirty_worktree(status), status)

    def test_genuine_tracked_owner_dirt_still_denies_the_lease(self) -> None:
        state: dict[str, Any] = {}
        ok, message, _ = self._dispatch(state)
        self.assertTrue(ok, message)

        (self.worktree / "README.md").write_text("owner edit in progress\n", encoding="utf-8")

        ok, status = self._refresh()

        self.assertFalse(ok)
        self.assertTrue(supervisor._is_skipped_dirty_worktree(status), status)
        self.assertIn("1 unstaged tracked", status)
        self.assertIn("README.md", status)
        self.assertEqual(
            (self.worktree / "README.md").read_text(encoding="utf-8"),
            "owner edit in progress\n",
        )

    def test_untracked_owner_file_still_denies_the_lease_and_is_named_untracked(self) -> None:
        state: dict[str, Any] = {}
        ok, message, _ = self._dispatch(state)
        self.assertTrue(ok, message)

        (self.worktree / "scratch_notes.md").write_text("owner scratch\n", encoding="utf-8")

        ok, status = self._refresh()

        self.assertFalse(ok)
        self.assertTrue(supervisor._is_skipped_dirty_worktree(status), status)
        self.assertIn("1 untracked", status)
        self.assertIn("scratch_notes.md", status)
        self.assertNotIn("staged", status)

    def test_owner_dirt_is_reported_and_preserved_by_the_dispatch_path(self) -> None:
        state: dict[str, Any] = {}
        ok, message, _ = self._dispatch(state)
        self.assertTrue(ok, message)

        (self.worktree / "README.md").write_text("owner edit in progress\n", encoding="utf-8")

        logged: list[dict[str, Any]] = []
        with mock.patch.object(
            supervisor, "write_activity_log", side_effect=lambda _cfg, entry: logged.append(entry)
        ):
            self._dispatch(state)

        refresh_entry = next(entry for entry in logged if entry["type"] == "worker_worktree_refreshed")
        self.assertFalse(refresh_entry["refresh_ok"])
        self.assertTrue(
            supervisor._is_skipped_dirty_worktree(refresh_entry["refresh_status"]),
            refresh_entry["refresh_status"],
        )
        self.assertIn("README.md", refresh_entry["refresh_status"])
        self.assertNotIn("tracked or staged changes", refresh_entry["refresh_status"])
        self.assertEqual(
            (self.worktree / "README.md").read_text(encoding="utf-8"),
            "owner edit in progress\n",
        )


class IsolatedWorktreePolicyTests(unittest.TestCase):
    def test_settings_expose_only_storage_and_network_controls(self) -> None:
        settings = supervisor.worker_worktree_settings({"worker_worktrees": {"root": "/tmp/worktrees"}})
        self.assertEqual(settings["root"], "/tmp/worktrees")
        self.assertNotIn("enabled", settings)
        self.assertNotIn("base_ref", settings)
        self.assertNotIn("reuse_existing", settings)
        self.assertNotIn("execution_reasons", settings)

    def test_shipped_config_has_no_shared_checkout_fallback_switches(self) -> None:
        shipped = json.loads((Path(supervisor.__file__).parent / "config.example.json").read_text(encoding="utf-8"))
        configured = shipped["worker_worktrees"]
        self.assertEqual(set(configured), {"root", "git_network_timeout_seconds"})


class WorktreeLeaseBlockEscalationTests(unittest.TestCase):
    """A block that repeats unchanged forever has to stop reading as noise."""

    def _record(self, config: dict, state: dict, events: list, *, n: int, status: str = "task_head_mismatch: local=a remote=b") -> int:
        count = 0
        with mock.patch.object(supervisor, "write_activity_log", side_effect=lambda _c, e: events.append(e)):
            for _ in range(n):
                count = supervisor._record_worktree_lease_block(
                    config,
                    state,
                    task_id="ODP-ORCH-EXAMPLE-001",
                    refresh_status=status,
                    message="reused worktree ... failed the fail-closed refresh policy",
                )
        return count

    def test_repeated_identical_blocks_escalate_exactly_once(self) -> None:
        config: dict = {"worker_runtime": {"lease_block_escalate_after": 3}}
        state: dict = {}
        events: list = []

        count = self._record(config, state, events, n=10)

        self.assertEqual(count, 10)
        escalations = [e for e in events if e["type"] == "dispatch_blocked_worktree_lease_escalated"]
        # Once, not ten times: the point is to surface the stall, not to become
        # a second copy of the noise it is reporting.
        self.assertEqual(len(escalations), 1)
        self.assertEqual(escalations[0]["consecutive_blocks"], 3)
        self.assertEqual(escalations[0]["task_id"], "ODP-ORCH-EXAMPLE-001")

    def test_blocks_below_the_threshold_stay_quiet(self) -> None:
        config: dict = {"worker_runtime": {"lease_block_escalate_after": 5}}
        state: dict = {}
        events: list = []

        self._record(config, state, events, n=4)

        self.assertEqual([e for e in events if e["type"].endswith("_escalated")], [])

    def test_a_different_block_reason_restarts_the_count(self) -> None:
        config: dict = {"worker_runtime": {"lease_block_escalate_after": 3}}
        state: dict = {}
        events: list = []

        self._record(config, state, events, n=2, status="task_head_mismatch: local=a remote=b")
        count = self._record(config, state, events, n=1, status="unverifiable_refs: remote task branch is missing")

        self.assertEqual(count, 1)
        self.assertEqual([e for e in events if e["type"].endswith("_escalated")], [])

    def test_a_successful_lease_clears_the_streak(self) -> None:
        config: dict = {"worker_runtime": {"lease_block_escalate_after": 3}}
        state: dict = {}
        events: list = []

        self._record(config, state, events, n=2)
        supervisor._clear_worktree_lease_block(state, "ODP-ORCH-EXAMPLE-001")
        count = self._record(config, state, events, n=1)

        self.assertEqual(count, 1)
        self.assertEqual([e for e in events if e["type"].endswith("_escalated")], [])

    def test_stale_streaks_expire_instead_of_accumulating_forever(self) -> None:
        # The bucket is durable now. `_clear_worktree_lease_block` only runs on a
        # successful lease, so a task blocked and then abandoned would otherwise
        # keep its entry in state.json permanently.
        stale = datetime.now(UTC) - timedelta(
            hours=supervisor.WORKTREE_LEASE_BLOCK_RETENTION_HOURS + 1
        )
        fresh = datetime.now(UTC) - timedelta(minutes=5)
        bucket = {
            "odp-orch-abandoned-001": {"count": 9, "last_at": supervisor.isoformat_utc(stale)},
            "odp-orch-live-001": {"count": 2, "last_at": supervisor.isoformat_utc(fresh)},
            "odp-orch-undated-001": {"count": 1},
            "odp-orch-garbage-001": "not-a-mapping",
        }

        supervisor._prune_worktree_lease_blocks(bucket)

        # An entry we cannot date is kept: expiring an undatable streak would
        # recreate the silent-loss failure this whole guard exists to remove.
        self.assertEqual(
            sorted(bucket), ["odp-orch-live-001", "odp-orch-undated-001"]
        )

    def test_streak_actually_escalates_across_save_and_reload_cycles(self) -> None:
        # End-to-end proof that the escalation can fire at all. Every previous
        # tick round-tripped its state through `save_runtime_state`, which
        # discarded the counter, so the count reset to 1 forever: 372
        # consecutive blocks over 23h produced zero escalations.
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        root = Path(tmpdir.name)
        (root / "event-queue.jsonl").write_text("", encoding="utf-8")
        config: dict = {
            "worker_runtime": {"lease_block_escalate_after": 3},
            "paths": {
                "state_file": str(root / "state.json"),
                "event_queue": str(root / "event-queue.jsonl"),
            },
        }
        events: list = []

        counts = []
        for _ in range(5):
            state = runtime_state.load_runtime_state(config)
            counts.append(self._record(config, state, events, n=1))
            runtime_state.save_runtime_state(config, state)

        self.assertEqual(counts, [1, 2, 3, 4, 5])
        escalations = [e for e in events if e["type"] == "dispatch_blocked_worktree_lease_escalated"]
        self.assertEqual(len(escalations), 1)
        self.assertEqual(escalations[0]["consecutive_blocks"], 3)
        # `escalated` must persist too, or every later tick re-alarms.
        persisted = json.loads((root / "state.json").read_text(encoding="utf-8"))
        self.assertTrue(
            persisted["worker_worktree_lease_blocks"]["odp_orch_example_001"]["escalated"]
        )


class ReusedWorkerWorktreeBaseAdvanceTests(unittest.TestCase):
    """Regression matrix for the clean divergence topology observed on PR #562."""

    task_branch = "task/ODP-CI-DEV-MERGE-RELEASE-NOGO-DEADLOCK-001"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.origin = root / "origin.git"
        seed = root / "seed"
        self.repo_root = root / "supervisor"
        self.worktree = root / "worker"

        self._git(root, "init", "--bare", str(self.origin))
        self._git(root, "init", "-b", "dev", str(seed))
        self._git(seed, "config", "user.name", "Supervisor Test")
        self._git(seed, "config", "user.email", "supervisor-test@example.invalid")
        (seed / "tracked.txt").write_text("initial\n", encoding="utf-8")
        self._git(seed, "add", "tracked.txt")
        self._git(seed, "commit", "-m", "initial")
        self.initial_head = self._git(seed, "rev-parse", "HEAD").stdout.strip()
        self._git(seed, "remote", "add", "origin", str(self.origin))
        self._git(seed, "push", "-u", "origin", "dev")
        self._git(self.origin, "symbolic-ref", "HEAD", "refs/heads/dev")

        self._git(seed, "switch", "-c", self.task_branch)
        (seed / "task.txt").write_text("task commit\n", encoding="utf-8")
        self._git(seed, "add", "task.txt")
        self._git(seed, "commit", "-m", "task change")
        self._git(seed, "push", "-u", "origin", self.task_branch)
        self.task_head = self._git(seed, "rev-parse", "HEAD").stdout.strip()

        self._git(seed, "switch", "dev")
        (seed / "base.txt").write_text("advanced dev\n", encoding="utf-8")
        self._git(seed, "add", "base.txt")
        self._git(seed, "commit", "-m", "advance dev")
        self._git(seed, "push", "origin", "dev")
        self.base_head = self._git(seed, "rev-parse", "HEAD").stdout.strip()

        self._git(root, "clone", "--branch", "dev", str(self.origin), str(self.repo_root))
        self._git(
            self.repo_root,
            "worktree",
            "add",
            "-b",
            self.task_branch,
            str(self.worktree),
            f"origin/{self.task_branch}",
        )
        self._git(self.worktree, "config", "user.name", "Supervisor Test")
        self._git(self.worktree, "config", "user.email", "supervisor-test@example.invalid")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _git(self, cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=check,
        )

    def _refresh(self) -> tuple[bool, str]:
        return supervisor._refresh_reused_worker_worktree(
            self.repo_root,
            self.worktree,
            "origin/dev",
            self.task_branch,
        )

    def test_clean_matching_task_head_diverged_from_dev_dispatches_for_owner_rebase(self) -> None:
        before = self._git(self.worktree, "rev-parse", "HEAD").stdout.strip()

        ok, status = self._refresh()

        self.assertTrue(ok)
        self.assertTrue(status.startswith("base_advance_rebase_required:"), status)
        self.assertEqual(self._git(self.worktree, "rev-parse", "HEAD").stdout.strip(), before)
        self.assertEqual(before, self.task_head)

    def test_task_containing_current_base_is_left_untouched(self) -> None:
        self._git(self.worktree, "merge", "--no-edit", "origin/dev")
        self._git(self.worktree, "push", "origin", f"HEAD:{self.task_branch}")
        composed_head = self._git(self.worktree, "rev-parse", "HEAD").stdout.strip()

        ok, status = self._refresh()

        self.assertTrue(ok)
        self.assertTrue(status.startswith("base_present_at_"), status)
        self.assertEqual(self._git(self.worktree, "rev-parse", "HEAD").stdout.strip(), composed_head)

    def test_task_behind_current_base_fast_forwards(self) -> None:
        self._git(self.worktree, "reset", "--hard", self.initial_head)
        self._git(self.repo_root, "push", "origin", "--delete", self.task_branch)

        ok, status = self._refresh()

        self.assertTrue(ok)
        self.assertEqual(status, f"ff_to_{self.base_head[:12]}")
        self.assertEqual(self._git(self.worktree, "rev-parse", "HEAD").stdout.strip(), self.base_head)

    def test_dirty_tracked_state_blocks_without_discarding_it(self) -> None:
        dirty_path = self.worktree / "task.txt"
        dirty_path.write_text("uncommitted owner work\n", encoding="utf-8")
        before_head = self._git(self.worktree, "rev-parse", "HEAD").stdout.strip()

        ok, status = self._refresh()

        self.assertFalse(ok)
        self.assertTrue(status.startswith("skipped_dirty_worktree:"), status)
        self.assertIn("1 unstaged tracked", status)
        self.assertIn("task.txt", status)
        self.assertEqual(dirty_path.read_text(encoding="utf-8"), "uncommitted owner work\n")
        self.assertEqual(self._git(self.worktree, "rev-parse", "HEAD").stdout.strip(), before_head)

    def test_dirty_tracked_orchestrator_scratch_also_blocks(self) -> None:
        scratch = self.worktree / ".orchestrator" / "config.json"
        scratch.parent.mkdir(parents=True, exist_ok=True)
        scratch.write_text("tracked context\n", encoding="utf-8")
        self._git(self.worktree, "add", str(scratch.relative_to(self.worktree)))
        self._git(self.worktree, "commit", "-m", "track task context")
        self._git(self.worktree, "push", "origin", f"HEAD:{self.task_branch}")
        scratch.write_text("owner annotation\n", encoding="utf-8")

        ok, status = self._refresh()

        self.assertFalse(ok)
        self.assertTrue(status.startswith("skipped_dirty_worktree:"), status)
        self.assertIn("1 unstaged tracked", status)
        self.assertIn(".orchestrator/config.json", status)
        self.assertEqual(scratch.read_text(encoding="utf-8"), "owner annotation\n")

    def test_local_task_head_is_not_compared_to_moving_remote_task_ref(self) -> None:
        (self.worktree / "local-only.txt").write_text("local\n", encoding="utf-8")
        self._git(self.worktree, "add", "local-only.txt")
        self._git(self.worktree, "commit", "-m", "local only")

        ok, status = self._refresh()

        self.assertTrue(ok, status)
        self.assertFalse(status.startswith("task_head_mismatch:"), status)

    def test_local_task_branch_behind_remote_is_left_untouched_without_remote_refresh(self) -> None:
        self._git(self.repo_root, "fetch", "origin", self.task_branch)
        self._git(self.repo_root, "switch", "--detach", f"origin/{self.task_branch}")
        self._git(self.repo_root, "config", "user.name", "Supervisor Test")
        self._git(self.repo_root, "config", "user.email", "supervisor-test@example.invalid")
        (self.repo_root / "published.txt").write_text("published task update\n", encoding="utf-8")
        self._git(self.repo_root, "add", "published.txt")
        self._git(self.repo_root, "commit", "-m", "publish task update")
        self._git(self.repo_root, "push", "origin", f"HEAD:{self.task_branch}")
        published_head = self._git(self.repo_root, "rev-parse", "HEAD").stdout.strip()

        ok, status = self._refresh()

        self.assertTrue(ok, status)
        self.assertFalse(status.startswith("task_head_mismatch:"), status)
        self.assertNotEqual(self._git(self.worktree, "rev-parse", "HEAD").stdout.strip(), published_head)

    def test_wrong_branch_blocks(self) -> None:
        self._git(self.worktree, "switch", "-c", "task/WRONG-BRANCH")

        ok, status = self._refresh()

        self.assertFalse(ok)
        self.assertTrue(status.startswith("wrong_branch:"), status)

    def test_wrong_repository_worktree_blocks(self) -> None:
        other = Path(self.tmp.name) / "other"
        self._git(Path(self.tmp.name), "init", "-b", "dev", str(other))

        ok, status = supervisor._refresh_reused_worker_worktree(
            self.repo_root,
            other,
            "origin/dev",
            self.task_branch,
        )

        self.assertFalse(ok)
        self.assertTrue(status.startswith("wrong_worktree:"), status)

    def test_task_remote_failure_does_not_affect_local_exact_base_relation(self) -> None:
        self._git(self.worktree, "remote", "set-url", "origin", str(Path(self.tmp.name) / "missing.git"))

        ok, status = self._refresh()

        self.assertTrue(ok, status)
        self.assertFalse(status.startswith("fetch_failed:"), status)

    def test_unresolved_git_operation_blocks(self) -> None:
        marker = Path(self._git(self.worktree, "rev-parse", "--git-path", "MERGE_HEAD").stdout.strip())
        marker.write_text(self.base_head + "\n", encoding="utf-8")

        ok, status = self._refresh()

        self.assertFalse(ok)
        self.assertEqual(status, "unresolved_git_operation")

    def test_unresolved_rebase_blocks(self) -> None:
        raw_rebase_head = self._git(
            self.worktree, "rev-parse", "--git-path", "REBASE_HEAD"
        ).stdout.strip()
        rebase_head = Path(raw_rebase_head)
        if not rebase_head.is_absolute():
            rebase_head = self.worktree / rebase_head
        rebase_head.write_text(self.initial_head + "\n", encoding="utf-8")

        for marker_name in ("rebase-merge", "rebase-apply"):
            with self.subTest(marker=marker_name):
                raw_marker = self._git(
                    self.worktree, "rev-parse", "--git-path", marker_name
                ).stdout.strip()
                marker = Path(raw_marker)
                if not marker.is_absolute():
                    marker = self.worktree / marker
                marker.mkdir(parents=True)

                ok, status = self._refresh()

                self.assertFalse(ok)
                self.assertEqual(status, "unresolved_git_operation")
                marker.rmdir()

    def test_stale_rebase_head_after_completed_rebase_does_not_block(self) -> None:
        raw_marker = self._git(self.worktree, "rev-parse", "--git-path", "REBASE_HEAD").stdout.strip()
        marker = Path(raw_marker)
        if not marker.is_absolute():
            marker = self.worktree / marker
        marker.write_text(self.initial_head + "\n", encoding="utf-8")

        ok, status = self._refresh()

        self.assertTrue(ok)
        self.assertTrue(status.startswith("base_advance_rebase_required:"), status)
        self.assertEqual(marker.read_text(encoding="utf-8").strip(), self.initial_head)

    def test_unverifiable_fetched_base_blocks(self) -> None:
        with mock.patch.object(supervisor, "_git_commit_oid", side_effect=[self.task_head, None]):
            ok, status = self._refresh()

        self.assertFalse(ok)
        self.assertTrue(status.startswith("unverifiable_refs:"), status)


class WorkerReassignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "worker_reassignment": {
                "enabled": True,
                "after_attempts": 2,
                "reassign_on_terminal_failure": True,
                "owner_fallbacks": {
                    "Gemini": ["Codex", "Claude", "Grok"],
                },
                "reviewer_fallbacks": {
                    "Gemini": ["Codex", "Claude", "Grok"],
                },
            },
            "agents": {
                "claude": {"display_name": "Claude"},
                "gemini": {"display_name": "Gemini"},
                "codex": {"display_name": "Codex"},
                "grok": {"display_name": "Grok"},
            },
        }

    def test_reassigns_review_task_to_new_reviewer_after_repeated_failure(self) -> None:
        worker = {
            "task_id": "P3-001",
            "agent_id": "gemini",
            "retry_count": 1,
            "run_id": "gemini-run-1",
        }
        status = {
            "tasks": [
                {
                    "id": "P3-001",
                    "status": "review",
                    "owner": "Claude",
                    "reviewer": "Gemini",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            reassigned_to = supervisor.maybe_reassign_task_after_worker_failure(
                self.config,
                worker,
                "status: 429",
            )

        self.assertEqual(reassigned_to, "Codex")
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "P3-001")
        self.assertEqual(kwargs["new_owner"], "Claude")
        self.assertEqual(kwargs["new_reviewer"], "Codex")
        self.assertEqual(kwargs["handoff_to"], "Codex")
        write_activity_log.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "task_reassigned")

    def test_review_churn_second_review_reopen_reassigns_owner_to_different_account_pool(self) -> None:
        config = {
            "worker_reassignment": {
                "enabled": True,
                "review_churn": {
                    "enabled": True,
                    "reassign_after_reopens": 2,
                    "require_different_account_pool": True,
                },
                "owner_fallbacks": {
                    "Antigravity": ["Antigravity2", "Codex"],
                },
            },
            "agents": {
                "antigravity": {
                    "display_name": "Antigravity",
                    "provider": "antigravity",
                    "account_pool": "agy-shared",
                },
                "antigravity2": {
                    "display_name": "Antigravity2",
                    "provider": "antigravity2",
                    "account_pool": "agy-shared",
                },
                "codex": {
                    "display_name": "Codex",
                    "provider": "codex",
                    "account_pool": "codex-main",
                },
                "claude": {
                    "display_name": "Claude",
                    "provider": "claude",
                    "account_pool": "claude-main",
                },
            },
        }
        status = {
            "tasks": [
                {
                    "id": "P3-CHURN",
                    "status": "in_progress",
                    "owner": "Antigravity",
                    "reviewer": "Claude",
                    "review_reopen_count": 2,
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.reassign_tasks_after_review_churn(config, {}, status)

        self.assertTrue(changed)
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["new_owner"], "Codex")
        self.assertEqual(kwargs["new_reviewer"], "Claude")
        self.assertEqual(kwargs["new_status"], "todo")
        self.assertEqual(kwargs["task_updates"]["review_churn_reassigned_at_count"], 2)
        event = write_activity_log.call_args.args[1]
        self.assertEqual(event["type"], "review_churn_reassigned")
        self.assertNotEqual(event["from_owner_pool"], event["to_owner_pool"])

    def _escalation_config(self, **churn_overrides) -> dict:
        churn = {
            "enabled": True,
            "reassign_after_reopens": 2,
            "require_different_account_pool": False,
        }
        churn.update(churn_overrides)
        return {
            "worker_reassignment": {
                "enabled": True,
                "review_churn": churn,
                "owner_fallbacks": {"Antigravity": ["Codex"]},
            },
            "agents": {
                "antigravity": {"display_name": "Antigravity", "provider": "antigravity"},
                "codex": {"display_name": "Codex", "provider": "codex"},
                "claude": {"display_name": "Claude", "provider": "claude"},
            },
        }

    def _churn_status(self, reopen_count: int) -> dict:
        return {
            "tasks": [
                {
                    "id": "P3-ESCALATE",
                    "status": "in_progress",
                    "owner": "Antigravity",
                    "reviewer": "Claude",
                    "review_reopen_count": reopen_count,
                }
            ]
        }

    def test_repeated_reopens_escalate_to_a_human_instead_of_rotating_again(self) -> None:
        """Owner rotation has to terminate.

        Rotating answers "this agent could not pass review"; it cannot answer
        "no agent can pass review from here". A live task reached 8 reopens and
        20 dispatches cycling owners against a gate that needed an authorized
        human action, because nothing in the loop ever stopped rotating.
        """
        with (
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.reassign_tasks_after_review_churn(
                self._escalation_config(escalate_to_human_after_reopens=6), {}, self._churn_status(6)
            )

        self.assertTrue(changed)
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["new_status"], "blocked")
        self.assertEqual(kwargs["new_waiting_for"], "Human/Ops")
        # The owner is kept: escalation is a handoff to a human, not one more
        # rotation, and a reader needs to see who last held it.
        self.assertEqual(kwargs["new_owner"], "Antigravity")
        self.assertEqual(kwargs["task_updates"]["review_churn_escalated_at_count"], 6)
        self.assertEqual(
            write_activity_log.call_args.args[1]["type"], "review_churn_escalated_to_human"
        )

    def test_below_the_escalation_threshold_still_rotates_owners(self) -> None:
        with (
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            supervisor.reassign_tasks_after_review_churn(
                self._escalation_config(escalate_to_human_after_reopens=6), {}, self._churn_status(2)
            )

        self.assertEqual(persist.call_args.kwargs["new_status"], "todo")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "review_churn_reassigned")

    def test_escalation_can_be_disabled_with_zero(self) -> None:
        """Zero opts a deployment out; rotation then keeps its old behaviour."""
        with (
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            supervisor.reassign_tasks_after_review_churn(
                self._escalation_config(escalate_to_human_after_reopens=0), {}, self._churn_status(99)
            )

        self.assertEqual(persist.call_args.kwargs["new_status"], "todo")

    def test_review_churn_reassignment_is_idempotent_until_two_more_reopens(self) -> None:
        status = {
            "tasks": [
                {
                    "id": "P3-CHURN-DONE",
                    "status": "in_progress",
                    "owner": "Gemini",
                    "reviewer": "Claude",
                    "review_reopen_count": 3,
                    "review_churn_reassigned_at_count": 2,
                }
            ]
        }
        with mock.patch.object(supervisor, "persist_task_reassignment") as persist:
            changed = supervisor.reassign_tasks_after_review_churn(self.config, {}, status)
        self.assertFalse(changed)
        persist.assert_not_called()

    def test_review_churn_does_not_bounce_back_to_failed_owner_in_same_epoch(self) -> None:
        config = {
            "worker_reassignment": {
                "enabled": True,
                "review_churn": {
                    "enabled": True,
                    "reassign_after_reopens": 2,
                    "require_different_account_pool": True,
                },
                "owner_fallbacks": {
                    "Antigravity": ["Codex2"],
                    "Codex2": ["Antigravity", "Claude2"],
                },
            },
            "agents": {
                "antigravity": {
                    "display_name": "Antigravity",
                    "provider": "antigravity",
                    "account_pool": "agy-shared",
                },
                "codex2": {
                    "display_name": "Codex2",
                    "provider": "codex2",
                    "account_pool": "codex-main",
                },
                "claude": {
                    "display_name": "Claude",
                    "provider": "claude",
                    "account_pool": "claude-main",
                },
                "claude2": {
                    "display_name": "Claude2",
                    "provider": "claude2",
                    "account_pool": "claude-alt",
                },
            },
        }
        # Codex2 currently owns task after Antigravity failed earlier in this churn epoch (reopen 1 & 2).
        # Codex2 now gets 2 more reopens (reopen 3 & 4), reaching reopen_count = 4.
        status = {
            "tasks": [
                {
                    "id": "OPS-REVIEW-CHURN-TEST-001",
                    "status": "in_progress",
                    "owner": "Codex2",
                    "reviewer": "Claude",
                    "review_reopen_count": 4,
                    "review_churn_reassigned_at_count": 2,
                    "review_churn_previous_owner": "Antigravity",
                    "review_churn_epoch_failed_owners": ["Antigravity"],
                    "review_reopen_history": [
                        {"count": 1, "at": "2026-08-27T01:00:00Z", "by": "Claude", "owner": "Antigravity"},
                        {"count": 2, "at": "2026-08-27T01:30:00Z", "by": "Claude", "owner": "Antigravity"},
                        {"count": 3, "at": "2026-08-27T02:00:00Z", "by": "Claude", "owner": "Codex2"},
                        {"count": 4, "at": "2026-08-27T02:30:00Z", "by": "Claude", "owner": "Codex2"},
                    ],
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.reassign_tasks_after_review_churn(config, {}, status)

        self.assertTrue(changed)
        kwargs = persist.call_args.kwargs
        # Must not bounce back to Antigravity; must advance to Claude2
        self.assertEqual(kwargs["new_owner"], "Claude2")
        self.assertEqual(kwargs["new_reviewer"], "Claude")
        self.assertEqual(kwargs["new_status"], "todo")
        self.assertEqual(kwargs["task_updates"]["review_churn_reassigned_at_count"], 4)
        self.assertEqual(kwargs["task_updates"]["review_churn_previous_owner"], "Codex2")
        self.assertEqual(
            kwargs["task_updates"]["review_churn_epoch_failed_owners"],
            ["Antigravity", "Codex2"],
        )
        event = write_activity_log.call_args.args[1]
        self.assertEqual(event["type"], "review_churn_reassigned")
        self.assertEqual(event["to_owner"], "Claude2")
        self.assertEqual(event["epoch_failed_owners"], ["Antigravity", "Codex2"])

    def test_review_churn_fails_closed_when_no_other_healthy_owner_available(self) -> None:
        config = {
            "worker_reassignment": {
                "enabled": True,
                "review_churn": {
                    "enabled": True,
                    "reassign_after_reopens": 2,
                    "require_different_account_pool": True,
                },
                "owner_fallbacks": {
                    "Antigravity": ["Codex2"],
                    "Codex2": ["Antigravity"],
                },
            },
            "agents": {
                "antigravity": {
                    "display_name": "Antigravity",
                    "provider": "antigravity",
                    "account_pool": "agy-shared",
                },
                "codex2": {
                    "display_name": "Codex2",
                    "provider": "codex2",
                    "account_pool": "codex-main",
                },
                "claude": {
                    "display_name": "Claude",
                    "provider": "claude",
                    "account_pool": "claude-main",
                },
            },
        }
        status = {
            "tasks": [
                {
                    "id": "OPS-REVIEW-CHURN-TEST-002",
                    "status": "in_progress",
                    "owner": "Codex2",
                    "reviewer": "Claude",
                    "review_reopen_count": 4,
                    "review_churn_reassigned_at_count": 2,
                    "review_churn_previous_owner": "Antigravity",
                    "review_churn_epoch_failed_owners": ["Antigravity"],
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.reassign_tasks_after_review_churn(config, {}, status)

        self.assertTrue(changed)
        kwargs = persist.call_args.kwargs
        # Fails closed to blocked with Human/Ops
        self.assertEqual(kwargs["new_status"], "blocked")
        self.assertEqual(kwargs["new_waiting_for"], "Human/Ops")
        self.assertIn("Review churn fail-closed", kwargs["message"])
        self.assertIn("Antigravity, Codex2", kwargs["message"])
        self.assertEqual(
            kwargs["task_updates"]["review_churn_epoch_failed_owners"],
            ["Antigravity", "Codex2"],
        )
        event = write_activity_log.call_args.args[1]
        self.assertEqual(event["type"], "review_churn_blocked")
        self.assertEqual(event["failed_owners"], ["Antigravity", "Codex2"])

    def test_review_churn_epoch_history_cleared_immediately_on_reset_count_zero(self) -> None:
        config = {
            "worker_reassignment": {
                "enabled": True,
                "review_churn": {
                    "enabled": True,
                    "reassign_after_reopens": 2,
                    "require_different_account_pool": True,
                },
                "owner_fallbacks": {
                    "Codex2": ["Antigravity"],
                },
            },
            "agents": {
                "antigravity": {
                    "display_name": "Antigravity",
                    "provider": "antigravity",
                    "account_pool": "agy-shared",
                },
                "codex2": {
                    "display_name": "Codex2",
                    "provider": "codex2",
                    "account_pool": "codex-main",
                },
                "claude": {
                    "display_name": "Claude",
                    "provider": "claude",
                    "account_pool": "claude-main",
                },
            },
        }
        status = {
            "tasks": [
                {
                    "id": "OPS-REVIEW-CHURN-TEST-RESET-0",
                    "status": "in_progress",
                    "owner": "Codex2",
                    "reviewer": "Claude",
                    "next": "Fixing implementation",
                    "review_reopen_count": 0,
                    "review_churn_reassigned_at_count": 4,
                    "review_churn_previous_owner": "Antigravity",
                    "review_churn_last_reassigned_at": "2026-08-27T02:00:00Z",
                    "review_churn_epoch_failed_owners": ["Antigravity", "Codex2"],
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.reassign_tasks_after_review_churn(config, {}, status)

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["new_owner"], "Codex2")
        self.assertEqual(kwargs["new_reviewer"], "Claude")
        self.assertEqual(kwargs["task_updates"]["review_churn_reassigned_at_count"], 0)
        self.assertIsNone(kwargs["task_updates"]["review_churn_previous_owner"])
        self.assertIsNone(kwargs["task_updates"]["review_churn_last_reassigned_at"])
        self.assertEqual(kwargs["task_updates"]["review_churn_epoch_failed_owners"], [])
        write_activity_log.assert_not_called()

    def test_review_churn_epoch_history_is_cleared_before_next_epoch(self) -> None:
        config = {
            "worker_reassignment": {
                "enabled": True,
                "review_churn": {
                    "enabled": True,
                    "reassign_after_reopens": 2,
                    "require_different_account_pool": True,
                },
                "owner_fallbacks": {
                    "Codex2": ["Antigravity"],
                },
            },
            "agents": {
                "antigravity": {
                    "display_name": "Antigravity",
                    "provider": "antigravity",
                    "account_pool": "agy-shared",
                },
                "codex2": {
                    "display_name": "Codex2",
                    "provider": "codex2",
                    "account_pool": "codex-main",
                },
                "claude": {
                    "display_name": "Claude",
                    "provider": "claude",
                    "account_pool": "claude-main",
                },
            },
        }
        # First call observes an explicit reset and must persist the cleared epoch.
        # The second call is a new epoch that retains old audit history; that
        # history must not make Antigravity ineligible again.
        status = {
            "tasks": [
                {
                    "id": "OPS-REVIEW-CHURN-TEST-003",
                    "status": "in_progress",
                    "owner": "Codex2",
                    "reviewer": "Claude",
                    "review_reopen_count": 0,
                    "review_churn_reassigned_at_count": 4,
                    "review_churn_previous_owner": "Antigravity",
                    "review_churn_last_reassigned_at": "2026-08-27T02:00:00Z",
                    "review_churn_epoch_failed_owners": ["Antigravity", "Codex2"],
                    "review_reopen_history": [
                        {"count": 1, "at": "2026-08-27T01:00:00Z", "by": "Claude", "owner": "Antigravity"},
                        {"count": 2, "at": "2026-08-27T01:30:00Z", "by": "Claude", "owner": "Antigravity"},
                    ],
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.reassign_tasks_after_review_churn(config, {}, status)

        self.assertTrue(changed)
        reset_kwargs = persist.call_args.kwargs
        self.assertEqual(reset_kwargs["new_owner"], "Codex2")
        self.assertEqual(reset_kwargs["task_updates"]["review_churn_reassigned_at_count"], 0)
        self.assertIsNone(reset_kwargs["task_updates"]["review_churn_previous_owner"])
        self.assertIsNone(reset_kwargs["task_updates"]["review_churn_last_reassigned_at"])
        self.assertEqual(reset_kwargs["task_updates"]["review_churn_epoch_failed_owners"], [])
        write_activity_log.assert_not_called()

        task = status["tasks"][0]
        task.update(reset_kwargs["task_updates"])
        task["review_reopen_count"] = 2
        with (
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as second_persist,
            mock.patch.object(supervisor, "write_activity_log") as second_write_activity_log,
        ):
            changed = supervisor.reassign_tasks_after_review_churn(config, {}, status)

        self.assertTrue(changed)
        second_persist.assert_called_once()
        kwargs = second_persist.call_args.kwargs
        # In a new epoch after reset, Antigravity is eligible again even though
        # the old reopen audit entries are still present.
        self.assertEqual(kwargs["new_owner"], "Antigravity")
        self.assertEqual(kwargs["new_status"], "todo")
        self.assertEqual(kwargs["task_updates"]["review_churn_epoch_failed_owners"], ["Codex2"])
        event = second_write_activity_log.call_args.args[1]
        self.assertEqual(event["type"], "review_churn_reassigned")
        self.assertEqual(event["epoch_failed_owners"], ["Codex2"])

    def test_review_churn_antigravity5_deferred_when_codex_pool_saturated_and_recovers(self) -> None:
        config = {
            "account_pools": {
                "agy_shared": {"max_concurrent": 2, "state": "healthy"},
                "codex_main": {"max_concurrent": 1, "state": "healthy"},
                "claude_main": {"max_concurrent": 1, "state": "healthy"},
            },
            "worker_reassignment": {
                "enabled": True,
                "review_churn": {
                    "enabled": True,
                    "reassign_after_reopens": 2,
                    "require_different_account_pool": True,
                },
                "owner_fallbacks": {
                    "Antigravity5": ["Codex"],
                },
            },
            "agents": {
                "antigravity5": {
                    "display_name": "Antigravity5",
                    "provider": "antigravity",
                    "account_pool": "agy_shared",
                },
                "codex": {
                    "display_name": "Codex",
                    "provider": "codex",
                    "account_pool": "codex_main",
                },
                "claude": {
                    "display_name": "Claude",
                    "provider": "claude",
                    "account_pool": "claude_main",
                },
            },
        }
        status = {
            "tasks": [
                {
                    "id": "ODP-ANTIGRAVITY5-CHURN",
                    "status": "in_progress",
                    "owner": "Antigravity5",
                    "reviewer": "Claude",
                    "review_reopen_count": 2,
                }
            ]
        }
        # Tick 1: Codex pool is temporarily saturated (1 active worker in codex_main).
        state_saturated = {
            "workers": {
                "w1": {
                    "run_id": "r1",
                    "task_id": "OTHER-TASK-001",
                    "agent_id": "codex",
                    "quota_group": "codex_main",
                    "status": "running",
                }
            }
        }
        with (
            mock.patch.object(supervisor, "persist_task_reassignment") as persist,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.reassign_tasks_after_review_churn(config, state_saturated, status)

        # Must not fail closed to Human/Ops blocked; should defer to subsequent tick.
        self.assertFalse(changed)
        persist.assert_not_called()
        write_activity_log.assert_not_called()
        self.assertEqual(status["tasks"][0]["status"], "in_progress")
        self.assertEqual(status["tasks"][0]["owner"], "Antigravity5")

        # Tick 2: Codex pool capacity recovers (no active workers).
        state_healthy = {"workers": {}}
        with (
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.reassign_tasks_after_review_churn(config, state_healthy, status)

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["new_owner"], "Codex")
        self.assertEqual(kwargs["new_reviewer"], "Claude")
        self.assertEqual(kwargs["new_status"], "todo")
        self.assertEqual(kwargs["task_updates"]["review_churn_reassigned_at_count"], 2)
        self.assertEqual(kwargs["task_updates"]["review_churn_previous_owner"], "Antigravity5")
        self.assertEqual(kwargs["task_updates"]["review_churn_epoch_failed_owners"], ["Antigravity5"])
        event = write_activity_log.call_args.args[1]
        self.assertEqual(event["type"], "review_churn_reassigned")
        self.assertEqual(event["from_owner"], "Antigravity5")
        self.assertEqual(event["to_owner"], "Codex")
        self.assertEqual(event["epoch_failed_owners"], ["Antigravity5"])

    def test_review_churn_defers_when_candidate_in_cooldown_and_recovers(self) -> None:
        config = {
            "account_pools": {
                "agy_shared": {"max_concurrent": 1, "state": "healthy"},
                "codex_main": {"max_concurrent": 1, "state": "healthy"},
                "claude_main": {"max_concurrent": 1, "state": "healthy"},
            },
            "worker_reassignment": {
                "enabled": True,
                "review_churn": {
                    "enabled": True,
                    "reassign_after_reopens": 2,
                    "require_different_account_pool": True,
                },
                "owner_fallbacks": {
                    "Antigravity": ["Codex"],
                },
            },
            "agents": {
                "antigravity": {
                    "display_name": "Antigravity",
                    "provider": "antigravity",
                    "account_pool": "agy_shared",
                },
                "codex": {
                    "display_name": "Codex",
                    "provider": "codex",
                    "account_pool": "codex_main",
                },
                "claude": {
                    "display_name": "Claude",
                    "provider": "claude",
                    "account_pool": "claude_main",
                },
            },
        }
        status = {
            "tasks": [
                {
                    "id": "ODP-COOLDOWN-CHURN",
                    "status": "in_progress",
                    "owner": "Antigravity",
                    "reviewer": "Claude",
                    "review_reopen_count": 2,
                }
            ]
        }
        # Tick 1: codex_main is in cooldown.
        state_cooldown = {
            "account_pool_runtime": {
                "codex_main": {
                    "state": "cooldown",
                    "effective_concurrency": 0,
                    "next_probe_at": "2099-01-01T00:00:00Z",
                }
            }
        }
        with (
            mock.patch.object(supervisor, "persist_task_reassignment") as persist,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.reassign_tasks_after_review_churn(config, state_cooldown, status)

        self.assertFalse(changed)
        persist.assert_not_called()
        write_activity_log.assert_not_called()

        # Tick 2: codex_main pool is healthy again.
        state_healthy = {
            "account_pool_runtime": {
                "codex_main": {
                    "state": "healthy",
                    "effective_concurrency": 1,
                }
            }
        }
        with (
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.reassign_tasks_after_review_churn(config, state_healthy, status)

        self.assertTrue(changed)
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["new_owner"], "Codex")
        self.assertEqual(kwargs["new_status"], "todo")

    def test_has_configured_reassignment_candidates(self) -> None:
        config = {
            "agents": {
                "antigravity": {"display_name": "Antigravity", "provider": "antigravity", "account_pool": "agy_shared"},
                "antigravity2": {"display_name": "Antigravity2", "provider": "antigravity", "account_pool": "agy_shared"},
                "codex": {"display_name": "Codex", "provider": "codex", "account_pool": "codex_main"},
                "disabled_agent": {"display_name": "DisabledAgent", "provider": "other", "enabled": False},
            },
        }
        # Codex is viable across account pools
        self.assertTrue(
            worker_failure_policy.has_configured_reassignment_candidates(
                config,
                ["Antigravity2", "Codex"],
                exclude={"Antigravity"},
                exclude_pools={"agy_shared"},
            )
        )
        # Only candidates in excluded pool or exclude set -> returns False
        self.assertFalse(
            worker_failure_policy.has_configured_reassignment_candidates(
                config,
                ["Antigravity2", "Codex"],
                exclude={"Antigravity", "Codex"},
                exclude_pools={"agy_shared"},
            )
        )
        # Only disabled or human gate agents -> returns False
        self.assertFalse(
            worker_failure_policy.has_configured_reassignment_candidates(
                config,
                ["Human/Ops", "DisabledAgent"],
                exclude=set(),
            )
        )

    def test_review_churn_defers_when_reviewer_candidate_temporarily_unavailable_and_recovers(self) -> None:
        config = {
            "account_pools": {
                "agy_shared": {"max_concurrent": 1, "state": "healthy"},
                "codex_main": {"max_concurrent": 1, "state": "healthy"},
                "gemini_main": {"max_concurrent": 1, "state": "healthy"},
            },
            "worker_reassignment": {
                "enabled": True,
                "review_churn": {
                    "enabled": True,
                    "reassign_after_reopens": 2,
                    "require_different_account_pool": True,
                },
                "owner_fallbacks": {
                    "Antigravity": ["Codex"],
                },
                "reviewer_fallbacks": {
                    "Antigravity": ["Gemini"],
                },
            },
            "agents": {
                "antigravity": {
                    "display_name": "Antigravity",
                    "provider": "antigravity",
                    "account_pool": "agy_shared",
                },
                "codex": {
                    "display_name": "Codex",
                    "provider": "codex",
                    "account_pool": "codex_main",
                },
                "gemini": {
                    "display_name": "Gemini",
                    "provider": "gemini",
                    "account_pool": "gemini_main",
                },
            },
        }
        status = {
            "tasks": [
                {
                    "id": "ODP-REVIEWER-CHURN",
                    "status": "in_progress",
                    "owner": "Antigravity",
                    "reviewer": "",
                    "review_reopen_count": 2,
                }
            ]
        }
        # Tick 1: Gemini pool is temporarily saturated
        state_saturated = {
            "workers": {
                "w1": {
                    "run_id": "r1",
                    "task_id": "OTHER-TASK-002",
                    "agent_id": "gemini",
                    "quota_group": "gemini_main",
                    "status": "running",
                }
            }
        }
        with (
            mock.patch.object(supervisor, "persist_task_reassignment") as persist,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.reassign_tasks_after_review_churn(config, state_saturated, status)

        self.assertFalse(changed)
        persist.assert_not_called()
        write_activity_log.assert_not_called()

        # Tick 2: Gemini pool recovers
        state_healthy = {"workers": {}}
        with (
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.reassign_tasks_after_review_churn(config, state_healthy, status)

        self.assertTrue(changed)
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["new_owner"], "Codex")
        self.assertEqual(kwargs["new_reviewer"], "Gemini")
        self.assertEqual(kwargs["new_status"], "todo")

    def test_reassign_review_skips_paused_reviewer_candidates(self) -> None:
        config = {
            "worker_reassignment": {
                "enabled": True,
                "after_attempts": 2,
                "reassign_on_terminal_failure": True,
                "reviewer_fallbacks": {
                    "Claude": ["Codex", "Helper", "Copilot", "Gemini"],
                },
            },
            "agents": {
                "claude": {"display_name": "Claude", "provider": "claude"},
                "helper": {"display_name": "Helper", "provider": "helper"},
                "codex": {"display_name": "Codex", "provider": "codex"},
                "copilot": {"display_name": "Copilot", "provider": "copilot"},
                "gemini": {"display_name": "Gemini", "provider": "gemini"},
            },
        }
        state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "helper": {
                        "provider": "helper",
                        "blocked_until": "2099-01-01T00:00:00Z",
                    }
                }
            }
        }
        worker = {
            "task_id": "P3-002",
            "agent_id": "claude",
            "retry_count": 1,
            "run_id": "claude-run-2",
        }
        status = {
            "tasks": [
                {
                    "id": "P3-002",
                    "status": "review",
                    "owner": "Codex",
                    "reviewer": "Claude",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            reassigned_to = supervisor.maybe_reassign_task_after_worker_failure(
                config,
                state,
                worker,
                "status: 401 unauthorized",
                terminal=True,
            )

        self.assertEqual(reassigned_to, "Copilot")
        self.assertEqual(persist.call_args.kwargs["new_reviewer"], "Copilot")

    def test_reassign_review_can_fall_back_to_codex2_when_codex_is_owner(self) -> None:
        config = {
            "worker_reassignment": {
                "enabled": True,
                "after_attempts": 2,
                "reassign_on_terminal_failure": True,
                "reviewer_fallbacks": {
                    "Claude": ["Codex", "Codex2", "Helper", "Copilot", "Gemini"],
                },
            },
            "agents": {
                "claude": {"display_name": "Claude", "provider": "claude"},
                "helper": {"display_name": "Helper", "provider": "helper"},
                "codex": {"display_name": "Codex", "provider": "codex"},
                "codex2": {"display_name": "Codex2", "provider": "codex2"},
                "copilot": {"display_name": "Copilot", "provider": "copilot"},
                "gemini": {"display_name": "Gemini", "provider": "gemini"},
            },
        }
        worker = {
            "task_id": "P3-003",
            "agent_id": "claude",
            "retry_count": 1,
            "run_id": "claude-run-3",
        }
        status = {
            "tasks": [
                {
                    "id": "P3-003",
                    "status": "review",
                    "owner": "Codex",
                    "reviewer": "Claude",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            reassigned_to = supervisor.maybe_reassign_task_after_worker_failure(
                config,
                worker,
                "Credit balance is too low",
                terminal=True,
            )

        self.assertEqual(reassigned_to, "Codex2")
        self.assertEqual(persist.call_args.kwargs["new_reviewer"], "Codex2")

    def test_owner_failure_keeps_a_viable_reviewer_however_loaded(self) -> None:
        """Only the owner failed, so the reviewer must not be rebalanced away.

        Reviewer Claude holds four open reviews and Grok holds none, so
        reviewer-load balancing would prefer Grok. Claude is still viable and
        did not fail, so it keeps the review and its accumulated context.
        """
        worker = {
            "task_id": "LP-004",
            "agent_id": "gemini",
            "retry_count": 1,
            "run_id": "gemini-run-3",
        }
        status = {
            "tasks": [
                {
                    "id": "LP-004",
                    "status": "in_progress",
                    "owner": "Gemini",
                    "reviewer": "Claude",
                },
                *(
                    {"id": f"LOAD-{i}", "status": "review", "owner": "Codex", "reviewer": "Claude"}
                    for i in range(4)
                ),
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            supervisor.maybe_reassign_task_after_worker_failure(self.config, worker, "status: 429")

        self.assertEqual(persist.call_args.kwargs["new_reviewer"], "Claude")

    def test_reviewer_replacement_still_balances_reviewer_load(self) -> None:
        """Preservation applies only while the reviewer is viable.

        Claude is dispatch-disabled, so it cannot keep the review. The
        replacement is then picked by reviewer load: Codex already holds three
        open reviews and Codex2 none, so Codex2 takes it despite Codex sorting
        first in the fallback list.
        """
        # setUp rebuilds self.config per test, so narrowing it here is local.
        self.config["agents"]["codex2"] = {"display_name": "Codex2"}
        self.config["agents"]["claude"]["enabled"] = False
        self.config["worker_reassignment"]["owner_fallbacks"]["Gemini"] = ["Grok"]
        self.config["worker_reassignment"]["reviewer_fallbacks"]["Gemini"] = ["Codex", "Codex2"]
        worker = {
            "task_id": "LP-005",
            "agent_id": "gemini",
            "retry_count": 1,
            "run_id": "gemini-run-4",
        }
        status = {
            "tasks": [
                {
                    "id": "LP-005",
                    "status": "in_progress",
                    "owner": "Gemini",
                    "reviewer": "Claude",
                },
                *(
                    {"id": f"REV-{i}", "status": "review", "owner": "Grok", "reviewer": "Codex"}
                    for i in range(3)
                ),
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            reassigned_to = supervisor.maybe_reassign_task_after_worker_failure(self.config, worker, "status: 429")

        self.assertEqual(reassigned_to, "Grok")
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["new_owner"], "Grok")
        self.assertEqual(kwargs["new_reviewer"], "Codex2")

    def test_reassigns_owned_task_to_new_owner_after_repeated_failure(self) -> None:
        worker = {
            "task_id": "LP-003",
            "agent_id": "gemini",
            "retry_count": 1,
            "run_id": "gemini-run-2",
        }
        status = {
            "tasks": [
                {
                    "id": "LP-003",
                    "status": "in_progress",
                    "owner": "Gemini",
                    "reviewer": "Claude",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            reassigned_to = supervisor.maybe_reassign_task_after_worker_failure(
                self.config,
                worker,
                "status: 429",
            )

        self.assertEqual(reassigned_to, "Codex")
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "LP-003")
        self.assertEqual(kwargs["new_owner"], "Codex")
        self.assertEqual(kwargs["new_reviewer"], "Claude")
        self.assertEqual(kwargs["new_status"], "todo")
        self.assertIn("Task returned to todo until Codex starts a fresh run.", kwargs["message"])


class HumanContinuationApprovalSupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "schema": {"tasks_path": "tasks"},
            "paths": {
                "status_file": "ai-status.json",
                "activity_log": "ai-activity-log.jsonl",
            },
        }

    @staticmethod
    def _approval(**updates: Any) -> dict[str, Any]:
        approval: dict[str, Any] = {
            "approval_id": "apr-continuation-1",
            "approval_type": "human_ops_continuation",
            "status": "issued",
            "task_id": "ODP-CONTINUATION-001",
            "task_scope": "ODP-CONTINUATION-001",
            "scope": {"task_id": "ODP-CONTINUATION-001"},
            "reason": "Human reviewed the churn evidence and authorized one continuation.",
            "issued_by": "Human/Ops",
            "issued_at": "2026-08-30T00:00:00Z",
            "expires_at": "2099-01-01T00:00:00Z",
            "nonce": "nonce-supervisor-1",
            "consumed_at": None,
            "consumed_by": None,
            "consumption_reason": None,
        }
        approval.update(updates)
        return approval

    def _task(self, approval: dict[str, Any] | None = None, **updates: Any) -> dict[str, Any]:
        task: dict[str, Any] = {
            "id": "ODP-CONTINUATION-001",
            "owner": "Codex2",
            "reviewer": "Claude2",
            "status": "blocked",
            "waiting_for": "Human/Ops",
            "review_reopen_count": 6,
            "review_churn_escalated_at_count": 6,
            "review_churn_escalated_at": "2026-08-30T00:00:00Z",
            "next": "Escalated to Human/Ops after repeated review churn.",
        }
        if approval is not None:
            task["human_continuation_approval"] = approval
        task.update(updates)
        return task

    def test_consumes_valid_approval_once_and_returns_task_to_todo(self) -> None:
        status = {"tasks": [self._task(self._approval())], "blockers": []}
        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=True) as commit,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.consume_human_continuation_approvals(self.config, {})

        self.assertTrue(changed)
        task = status["tasks"][0]
        self.assertEqual(task["status"], "todo")
        self.assertNotIn("waiting_for", task)
        self.assertNotIn("human_continuation_approval", task)
        consumed = task["human_continuation_approval_history"][-1]
        self.assertEqual(consumed["status"], "consumed")
        self.assertEqual(consumed["consumed_by"], "Orchestrator")
        self.assertEqual(task["review_churn_reassigned_at_count"], 6)
        commit.assert_called_once_with(self.config, status)
        self.assertEqual(
            write_activity_log.call_args.args[1]["type"],
            "human_continuation_approval_consumed",
        )

        with mock.patch.object(supervisor, "load_status", return_value=status):
            self.assertFalse(supervisor.consume_human_continuation_approvals(self.config, {}))

    def test_expired_scope_mismatch_and_replay_fail_closed(self) -> None:
        expired_task = self._task(self._approval(expires_at="2020-01-01T00:00:00Z"))
        expired_status = {"tasks": [expired_task], "blockers": []}
        with (
            mock.patch.object(supervisor, "load_status", return_value=expired_status),
            mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=True) as commit,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            self.assertTrue(supervisor.consume_human_continuation_approvals(self.config, {}))
        self.assertEqual(expired_task["status"], "blocked")
        self.assertEqual(expired_task["human_continuation_approval_history"][-1]["status"], "expired")
        commit.assert_called_once()

        cases = [
            self._approval(task_id="OTHER-TASK", task_scope="OTHER-TASK", scope={"task_id": "OTHER-TASK"}),
            self._approval(issued_by="Codex2"),
            self._approval(nonce="nonce-used"),
        ]
        for approval in cases:
            with self.subTest(approval=approval):
                task = self._task(approval)
                if approval["nonce"] == "nonce-used":
                    task["human_continuation_approval_history"] = [
                        self._approval(approval_id="apr-old", nonce="nonce-used", status="consumed")
                    ]
                status = {"tasks": [task], "blockers": []}
                with (
                    mock.patch.object(supervisor, "load_status", return_value=status),
                    mock.patch.object(supervisor, "commit_canonical_task_transition") as commit,
                ):
                    self.assertFalse(supervisor.consume_human_continuation_approvals(self.config, {}))
                self.assertEqual(task["status"], "blocked")
                commit.assert_not_called()

    def test_independent_production_gate_is_not_released_by_continuation_approval(self) -> None:
        task = self._task(
            self._approval(),
            next="Review churn is also blocked pending production deployment approval.",
        )
        status = {"tasks": [task], "blockers": []}
        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "commit_canonical_task_transition") as commit,
        ):
            self.assertFalse(supervisor.consume_human_continuation_approvals(self.config, {}))
        self.assertEqual(task["status"], "blocked")
        commit.assert_not_called()

    def test_production_deployment_title_does_not_block_review_churn_consumption(self) -> None:
        task = self._task(
            self._approval(),
            title="Production deployment continuation",
            summary="Deployment recovery for the production lane",
            summary_zh="production deployment 修復",
            blocker="Review churn only after repeated reviewer reopenings.",
        )
        status = {"tasks": [task], "blockers": []}
        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=True),
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            self.assertTrue(supervisor.consume_human_continuation_approvals(self.config, {}))

        self.assertEqual(task["status"], "todo")
        self.assertEqual(task["human_continuation_approval_history"][-1]["status"], "consumed")


class AutomaticRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {"dependency_done_statuses": ["done"]},
            "agents": {
                "antigravity": {"display_name": "Antigravity", "provider": "antigravity"},
                "claude": {"display_name": "Claude", "provider": "claude"},
                "codex": {"display_name": "Codex", "provider": "codex"},
            },
            "worker_reassignment": {
                "owner_fallbacks": {"CodexCoordinator": ["Codex", "Claude"]},
                "reviewer_fallbacks": {"CodexCoordinator": ["Codex", "Claude"]},
            },
        }

    def test_stale_blocked_mainline_task_reopens_for_dispatch(self) -> None:
        task = {
            "id": "AUTO-REOPEN-001",
            "status": "blocked",
            "owner": "Antigravity",
            "reviewer": "Claude",
            "depends_on": [],
            "next": "Auto-reassigned away from sidecar-only lane Codex; owner Codex -> Antigravity.",
        }
        with (
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.normalize_mainline_task_assignment(
                self.config,
                task,
                {task["id"]: task},
            )

        self.assertTrue(changed)
        self.assertEqual(persist.call_args.kwargs["new_status"], "todo")
        self.assertTrue(persist.call_args.kwargs["resolve_open_blockers"])
        self.assertIsNone(persist.call_args.kwargs["handoff_to"])

    @staticmethod
    def _dependency_gated_task(task_id: str, depends_on: list[str]) -> dict[str, object]:
        # Exactly the shape a staged-wave catalog registers: a static `blocked`
        # status whose prose is never rewritten when the dependency completes.
        reason = "waiting for dependencies: " + ", ".join(depends_on)
        return {
            "id": task_id,
            "status": "blocked",
            "owner": "Antigravity",
            "reviewer": "Claude",
            "depends_on": list(depends_on),
            "blocked_reason": reason,
            "next": reason,
            "waiting_for": "Antigravity",
        }

    def test_released_dependency_gate_reopens_without_routing_failure_prose(self) -> None:
        gate = {"id": "WAVE-GOV-001", "status": "done", "depends_on": []}
        task = self._dependency_gated_task("WAVE-KRN-001", ["WAVE-GOV-001"])
        task_map = {gate["id"]: gate, task["id"]: task}

        self.assertTrue(supervisor.blocked_task_auto_recovery_eligible(self.config, task, task_map))

        with (
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.normalize_mainline_task_assignment(self.config, task, task_map)

        self.assertTrue(changed)
        self.assertEqual(persist.call_args.kwargs["new_status"], "todo")
        self.assertTrue(persist.call_args.kwargs["resolve_open_blockers"])

    def test_unsatisfied_dependency_gate_stays_blocked(self) -> None:
        gate = {"id": "WAVE-GOV-001", "status": "in_progress", "depends_on": []}
        task = self._dependency_gated_task("WAVE-KRN-001", ["WAVE-GOV-001"])
        task_map = {gate["id"]: gate, task["id"]: task}

        self.assertFalse(supervisor.blocked_task_auto_recovery_eligible(self.config, task, task_map))

    def test_dependency_ids_do_not_masquerade_as_hard_gates(self) -> None:
        # "WAVE-KRN-DATASET-001" contains the external-data gate keyword
        # "dataset"; a dependent task must not inherit that classification.
        gate = {"id": "WAVE-KRN-DATASET-001", "status": "done", "depends_on": []}
        task = self._dependency_gated_task("WAVE-KRN-SCHEMA-001", ["WAVE-KRN-DATASET-001"])
        task_map = {gate["id"]: gate, task["id"]: task}

        self.assertNotIn("dataset", supervisor.blocked_task_prose_context(task))
        self.assertTrue(supervisor.blocked_task_auto_recovery_eligible(self.config, task, task_map))

    def test_released_dependency_gate_still_fails_closed_on_human_gate_prose(self) -> None:
        gate = {"id": "WAVE-GOV-001", "status": "done", "depends_on": []}
        task = self._dependency_gated_task("WAVE-KRN-001", ["WAVE-GOV-001"])
        task["blocker"] = "requires operator sign-off before dispatch"
        task_map = {gate["id"]: gate, task["id"]: task}

        self.assertFalse(supervisor.blocked_task_auto_recovery_eligible(self.config, task, task_map))

    def test_dependency_free_blocked_task_still_needs_routing_failure_prose(self) -> None:
        task = {
            "id": "WAVE-MISC-001",
            "status": "blocked",
            "owner": "Antigravity",
            "reviewer": "Claude",
            "depends_on": [],
            "next": "waiting on an unrelated business decision",
        }

        self.assertFalse(
            supervisor.blocked_task_auto_recovery_eligible(self.config, task, {task["id"]: task})
        )

    def test_unregistered_coordinator_reviewer_is_reassigned(self) -> None:
        task = {
            "id": "AUTO-REVIEW-001",
            "status": "review",
            "owner": "Antigravity",
            "reviewer": "CodexCoordinator",
            "depends_on": [],
        }
        with (
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.normalize_mainline_task_assignment(
                self.config,
                task,
                {task["id"]: task},
            )

        self.assertTrue(changed)
        self.assertIn(persist.call_args.kwargs["new_reviewer"], {"Codex", "Claude"})
        self.assertNotEqual(persist.call_args.kwargs["new_reviewer"], "CodexCoordinator")

    def test_ci_failure_requeues_owner_and_clears_stale_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "ai-status.json"
            status = {
                "tasks": [
                    {
                        "id": "AUTO-CI-001",
                        "status": "review_approved",
                        "owner": "Antigravity",
                        "reviewer": "Claude",
                        "approved_head": "a" * 40,
                    }
                ]
            }
            status_path.write_text(json.dumps(status), encoding="utf-8")
            config = dict(self.config)
            config["paths"] = {"status_file": str(status_path)}
            with (
                mock.patch.object(supervisor, "sync_status_pipeline", return_value=True),
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                self.assertTrue(
                    supervisor.requeue_task_for_ci_repair(
                        config,
                        status,
                        status["tasks"][0],
                        message="CI failed; repair queued.",
                        clear_approval=True,
                    )
                )
            saved = json.loads(status_path.read_text(encoding="utf-8"))["tasks"][0]
            self.assertEqual(saved["status"], "in_progress")
            self.assertNotIn("approved_head", saved)

    def test_ci_pending_requeue_commits_markers_and_lifecycle_once(self) -> None:
        now_ts = 1_786_665_600.0
        approved_head = "a" * 40
        task = {
            "id": "AUTO-CI-ATOMIC-001",
            "status": "review_approved",
            "owner": "Antigravity",
            "reviewer": "Claude",
            "approved_head": approved_head,
            "ci_pending_since_ts": now_ts - 2000,
            "ci_pending_since": "2026-08-14T00:00:00Z",
        }
        status = {"tasks": [task]}
        with (
            mock.patch.object(
                supervisor,
                "commit_canonical_task_transition",
                return_value=True,
            ) as commit,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.requeue_task_for_ci_repair(
                self.config,
                status,
                task,
                message="CI pending; repair queued.",
                clear_approval=False,
                requeued_head=approved_head,
                now_ts=now_ts,
            )

        self.assertTrue(changed)
        commit.assert_called_once_with(self.config, status)
        self.assertIs(status["tasks"][0], task)
        self.assertEqual(task["status"], "in_progress")
        self.assertEqual(task["ci_repair_requeued_head"], approved_head)
        self.assertEqual(task["ci_repair_last_requeued_ts"], now_ts)
        self.assertNotIn("ci_pending_since_ts", task)
        self.assertNotIn("ci_pending_since", task)

    def test_ci_requeue_rejects_task_outside_canonical_snapshot(self) -> None:
        task = {"id": "AUTO-CI-DETACHED-001", "status": "review_approved"}
        status = {"tasks": []}
        with mock.patch.object(
            supervisor,
            "commit_canonical_task_transition",
        ) as commit:
            changed = supervisor.requeue_task_for_ci_repair(
                self.config,
                status,
                task,
                message="Must not commit a detached task object.",
                clear_approval=False,
            )

        self.assertFalse(changed)
        commit.assert_not_called()

    def test_ci_failure_requeues_sidecar_owner_too(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "ai-status.json"
            status = {
                "tasks": [
                    {
                        "id": "AUTO-CI-SIDECAR-001",
                        "status": "review_approved",
                        "owner": "Antigravity",
                        "reviewer": "Claude",
                        "task_class": "sidecar",
                        "approved_head": "b" * 40,
                    }
                ]
            }
            status_path.write_text(json.dumps(status), encoding="utf-8")
            config = dict(self.config)
            config["paths"] = {"status_file": str(status_path)}
            with (
                mock.patch.object(supervisor, "sync_status_pipeline", return_value=True),
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                self.assertTrue(
                    supervisor.requeue_task_for_ci_repair(
                        config,
                        status,
                        status["tasks"][0],
                        message="CI failed; sidecar owner repair queued.",
                        clear_approval=True,
                    )
                )
            saved = json.loads(status_path.read_text(encoding="utf-8"))["tasks"][0]
            self.assertEqual(saved["status"], "in_progress")
            self.assertNotIn("approved_head", saved)

    def test_ci_failure_requeue_fails_closed_on_stale_status_snapshot(self) -> None:
        status = {
            "tasks": [
                {
                    "id": "AUTO-CI-STALE-001",
                    "status": "review_approved",
                    "owner": "Antigravity",
                    "reviewer": "Claude",
                }
            ]
        }
        with (
            mock.patch.object(
                supervisor,
                "commit_canonical_task_transition",
                return_value=False,
            ) as commit,
            mock.patch.object(supervisor, "write_activity_log") as activity_log,
        ):
            changed = supervisor.requeue_task_for_ci_repair(
                self.config,
                status,
                status["tasks"][0],
                message="CI failed; repair queued.",
                clear_approval=True,
            )

        self.assertFalse(changed)
        commit.assert_called_once_with(self.config, status)
        activity_log.assert_not_called()


class WorkerPreemptionSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "worker_reassignment": {
                "enabled": True,
                "after_attempts": 2,
                "reassign_on_terminal_failure": True,
                "owner_fallbacks": {
                    "Gemini": ["Codex", "Claude", "Grok"],
                },
                "reviewer_fallbacks": {
                    "Gemini": ["Codex", "Claude", "Grok"],
                },
            },
            "agents": {
                "claude": {"display_name": "Claude"},
                "gemini": {"display_name": "Gemini"},
                "codex": {"display_name": "Codex"},
                "grok": {"display_name": "Grok"},
            },
        }

    def test_reassignment_preserves_blocked_reason_as_next(self) -> None:
        config = {**self.config, "paths": {"status_file": "ai-status.json"}}
        status = {
            "tasks": [
                {
                    "id": "BLOCKED-001",
                    "status": "blocked",
                    "owner": "Gemini",
                    "reviewer": "Claude",
                    "waiting_for": "Human/Ops",
                    "next": "Await authoritative Human/Ops dataset and attestation.",
                }
            ],
            "handoffs": [],
            "blockers": [],
        }
        message = "Auto-reassigned owner from Gemini to Codex."

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "write_json"),
            mock.patch.object(supervisor, "sync_status_pipeline", return_value=True),
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.persist_task_reassignment(
                config,
                task_id="BLOCKED-001",
                new_owner="Codex",
                new_reviewer="Claude",
                message=message,
            )

        self.assertTrue(changed)
        task = status["tasks"][0]
        self.assertEqual(task["next"], "Await authoritative Human/Ops dataset and attestation.")
        self.assertEqual(task["assignment_note"], message)
        self.assertEqual(task["waiting_for"], "Human/Ops")

    def test_sync_preempted_owned_task_returns_in_progress_task_to_todo(self) -> None:
        config = {
            "paths": {"status_file": "ai-status.json"},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        worker = {
            "task_id": "BP5-CICD-001",
            "agent_id": "codex",
            "provider": "codex",
            "request_snapshot": {"reason": "owned_ready_dispatch"},
        }
        status = {
            "tasks": [
                {
                    "id": "BP5-CICD-001",
                    "status": "in_progress",
                    "owner": "Codex",
                    "reviewer": "Gemini",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "write_json") as write_json,
            mock.patch.object(supervisor, "sync_status_pipeline", return_value=True),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-15T16:09:52Z"),
        ):
            synced = supervisor.sync_preempted_task_status(config, worker)

        self.assertTrue(synced)
        task = status["tasks"][0]
        self.assertEqual(task["status"], "todo")
        self.assertEqual(task["last_update"], "2026-04-15T16:09:52Z")
        self.assertIn("returned to todo until a fresh run restarts it", task["next"])
        write_json.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "task_preempted_synced")

    def test_sync_preempted_finalize_task_keeps_review_approved(self) -> None:
        config = {
            "paths": {"status_file": "ai-status.json"},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        worker = {
            "task_id": "BP5-SVC-001",
            "agent_id": "codex",
            "provider": "codex",
            "request_snapshot": {"reason": "owned_finalize_dispatch"},
        }
        status = {
            "tasks": [
                {
                    "id": "BP5-SVC-001",
                    "status": "review_approved",
                    "owner": "Codex",
                    "reviewer": "Helper",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "write_json") as write_json,
            mock.patch.object(supervisor, "sync_status_pipeline", return_value=True),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-15T16:09:52Z"),
        ):
            synced = supervisor.sync_preempted_task_status(config, worker)

        self.assertTrue(synced)
        task = status["tasks"][0]
        self.assertEqual(task["status"], "review_approved")
        self.assertEqual(task["last_update"], "2026-04-15T16:09:52Z")
        self.assertIn("task remains review_approved", task["next"])
        write_json.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "task_preempted_synced")

    def test_reassigns_finalize_task_to_new_owner_after_repeated_failure(self) -> None:
        config = {
            **self.config,
            "ready_dispatcher": {
                "sidecar_only_agents": ["Helper"],
            },
            "worker_reassignment": {
                **self.config["worker_reassignment"],
                "owner_fallbacks": {
                    **self.config["worker_reassignment"]["owner_fallbacks"],
                    "Claude": ["Helper", "Grok", "Gemini"],
                },
                "reviewer_fallbacks": {
                    **self.config["worker_reassignment"]["reviewer_fallbacks"],
                    "Claude": ["Helper", "Grok", "Gemini"],
                },
            },
            "agents": {
                **self.config["agents"],
                "helper": {"display_name": "Helper"},
            },
        }
        worker = {
            "task_id": "RUN-001",
            "agent_id": "claude",
            "retry_count": 5,
            "run_id": "claude-run-9",
        }
        status = {
            "tasks": [
                {
                    "id": "RUN-001",
                    "status": "review_approved",
                    "owner": "Claude",
                    "reviewer": "Codex",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            reassigned_to = supervisor.maybe_reassign_task_after_worker_failure(
                config,
                worker,
                "You've hit your limit · resets 1pm (Asia/Taipei)",
                terminal=True,
            )

        self.assertEqual(reassigned_to, "Grok")
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "RUN-001")
        self.assertEqual(kwargs["new_owner"], "Grok")
        self.assertEqual(kwargs["new_reviewer"], "Codex")
        self.assertIsNone(kwargs["new_status"])


class WorkerPreemptionSafeBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "paths": {"status_file": "ai-status.json"},
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "owned_statuses": ["in_progress", "todo"],
                "finalize_statuses": ["review_approved"],
                "active_worker_statuses": ["running", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "stalled"],
            },
            "agents": {
                "antigravity": {
                    "id": "antigravity",
                    "display_name": "Antigravity",
                    "worker_slots": ["antigravity_slot_1", "antigravity_slot_2"],
                },
                "antigravity_slot_1": {
                    "id": "antigravity_slot_1",
                    "display_name": "Antigravity",
                    "dispatch_slot_for": "antigravity",
                    "provider": "antigravity-1",
                },
                "antigravity_slot_2": {
                    "id": "antigravity_slot_2",
                    "display_name": "Antigravity",
                    "dispatch_slot_for": "antigravity",
                    "provider": "antigravity-2",
                },
                "codex": {
                    "id": "codex",
                    "display_name": "Codex",
                },
            },
        }

    def test_healthy_foundation_owner_not_preempted_when_alternate_free_lane_exists(self) -> None:
        """Regression test: healthy foundation owner is not preempted when alternate free lane exists for review."""
        config = json.loads(json.dumps(self.config))
        now_iso = supervisor.utc_now()
        worker = {
            "run_id": "run-owner-1",
            "task_id": "FOUNDATION-001",
            "provider": "antigravity-1",
            "agent_id": "antigravity_slot_1",
            "logical_agent_id": "antigravity",
            "status": "running",
            "queue_event_id": "evt-owner-1",
            "pid": 5555,
            "last_event_at": now_iso,
            "last_heartbeat_at": now_iso,
            "request_snapshot": {"reason": "owned_in_progress_dispatch"},
        }
        state = {
            "queue": {"events": {"evt-owner-1": {"status": "started", "run_id": "run-owner-1"}}},
            "workers": {"run-owner-1": worker},
        }
        task_map = {
            "FOUNDATION-001": {
                "id": "FOUNDATION-001",
                "status": "in_progress",
                "owner": "Antigravity",
                "reviewer": "Codex",
                "depends_on": [],
            },
            "REVIEW-001": {
                "id": "REVIEW-001",
                "status": "review",
                "owner": "Codex",
                "reviewer": "Antigravity",
                "depends_on": [],
            },
        }
        status = {"tasks": list(task_map.values())}

        with mock.patch.object(supervisor, "pid_is_alive", return_value=True):
            # 1. higher_priority_ready_task_exists returns False because antigravity_slot_2 is free
            self.assertFalse(
                supervisor.higher_priority_ready_task_exists(config, worker, task_map, state)
            )

            # 2. worker_can_be_preempted returns False for healthy in_progress owner
            self.assertFalse(
                supervisor.worker_can_be_preempted(config, worker, task_map, state)
            )

        # 3. poll_workers does not terminate or supersede the healthy owner
        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertFalse(changed)
        self.assertEqual(worker["status"], "running")
        terminate_worker_pid.assert_not_called()

    def test_healthy_foundation_owner_not_preempted_when_no_free_lane_and_review_deferred(self) -> None:
        """When review has no free lane, healthy in_progress owner is not preempted and review defers without Human blocker."""
        config = json.loads(json.dumps(self.config))
        config["agents"]["antigravity"]["worker_slots"] = ["antigravity_slot_1"]
        config["agents"].pop("antigravity_slot_2", None)

        now_iso = supervisor.utc_now()
        worker = {
            "run_id": "run-owner-1",
            "task_id": "FOUNDATION-001",
            "provider": "antigravity-1",
            "agent_id": "antigravity_slot_1",
            "logical_agent_id": "antigravity",
            "status": "running",
            "queue_event_id": "evt-owner-1",
            "pid": 5555,
            "last_event_at": now_iso,
            "last_heartbeat_at": now_iso,
            "request_snapshot": {"reason": "owned_in_progress_dispatch"},
        }
        state = {
            "queue": {"events": {"evt-owner-1": {"status": "started", "run_id": "run-owner-1"}}},
            "workers": {"run-owner-1": worker},
        }
        task_map = {
            "FOUNDATION-001": {
                "id": "FOUNDATION-001",
                "status": "in_progress",
                "owner": "Antigravity",
                "reviewer": "Codex",
                "depends_on": [],
            },
            "REVIEW-001": {
                "id": "REVIEW-001",
                "status": "review",
                "owner": "Codex",
                "reviewer": "Antigravity",
                "depends_on": [],
            },
        }
        status = {"tasks": list(task_map.values())}

        with mock.patch.object(supervisor, "pid_is_alive", return_value=True):
            # worker_can_be_preempted protects the healthy active owner
            self.assertFalse(
                supervisor.worker_can_be_preempted(config, worker, task_map, state)
            )

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertFalse(changed)
        self.assertEqual(worker["status"], "running")
        terminate_worker_pid.assert_not_called()
        self.assertEqual(task_map["REVIEW-001"]["status"], "review")
        self.assertNotIn("waiting_for", task_map["REVIEW-001"])

    def test_dirty_worktree_fails_closed_and_preserves_receipt_on_forced_preemption(self) -> None:
        """When preemption is forced (e.g. operator emergency), uncommitted dirt is preserved with a receipt."""
        config = json.loads(json.dumps(self.config))
        config["agents"]["antigravity"]["worker_slots"] = ["antigravity_slot_1"]
        config["agents"].pop("antigravity_slot_2", None)
        worker = {
            "run_id": "run-emergency-1",
            "task_id": "FOUNDATION-001",
            "provider": "antigravity-1",
            "agent_id": "antigravity_slot_1",
            "logical_agent_id": "antigravity",
            "status": "running",
            "queue_event_id": "evt-1",
            "pid": 5555,
            "operator_emergency": True,
            "workspace_path": "/tmp/test-workspace-foundation",
            "request_snapshot": {"reason": "owned_in_progress_dispatch"},
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started", "run_id": "run-emergency-1"}}},
            "workers": {"run-emergency-1": worker},
        }
        task_map = {
            "FOUNDATION-001": {
                "id": "FOUNDATION-001",
                "status": "in_progress",
                "owner": "Antigravity",
                "reviewer": "Codex",
                "depends_on": [],
            },
            "REV-001": {
                "id": "REV-001",
                "status": "review",
                "owner": "Codex",
                "reviewer": "Antigravity",
                "depends_on": [],
            },
        }
        status = {"tasks": list(task_map.values())}

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "terminate_worker_pid", return_value=True) as terminate_worker_pid,
            mock.patch.object(supervisor, "preserve_dead_worker_worktree") as preserve_worktree,
            mock.patch.object(supervisor, "sync_preempted_task_status", return_value=True),
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        self.assertEqual(worker["status"], "superseded")
        terminate_worker_pid.assert_called_once_with(5555)
        preserve_worktree.assert_called_once_with(
            config,
            state,
            worker,
            task=task_map["FOUNDATION-001"],
            trigger="preempted_priority_escalation",
        )

    def test_clean_finalize_worker_can_be_preempted_and_preserves_review_approved(self) -> None:
        """Finalize workers on immutable approved heads are safe to preempt and remain review_approved."""
        config = json.loads(json.dumps(self.config))
        config["agents"]["antigravity"]["worker_slots"] = ["antigravity_slot_1"]
        config["agents"].pop("antigravity_slot_2", None)
        worker = {
            "run_id": "run-finalize-1",
            "task_id": "FINAL-001",
            "provider": "antigravity-1",
            "agent_id": "antigravity_slot_1",
            "logical_agent_id": "antigravity",
            "status": "running",
            "queue_event_id": "evt-final-1",
            "pid": 5555,
            "request_snapshot": {"reason": supervisor.REASON_OWNED_FINALIZE},
        }
        state = {
            "queue": {"events": {"evt-final-1": {"status": "started", "run_id": "run-finalize-1"}}},
            "workers": {"run-finalize-1": worker},
        }
        task_map = {
            "FINAL-001": {
                "id": "FINAL-001",
                "status": "review_approved",
                "owner": "Antigravity",
                "reviewer": "Codex",
                "depends_on": [],
            },
            "REV-001": {
                "id": "REV-001",
                "status": "review",
                "owner": "Codex",
                "reviewer": "Antigravity",
                "depends_on": [],
            },
        }
        status = {"tasks": list(task_map.values())}

        with mock.patch.object(supervisor, "pid_is_alive", return_value=True):
            self.assertTrue(
                supervisor.worker_can_be_preempted(config, worker, task_map, state)
            )

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "terminate_worker_pid", return_value=True) as terminate_worker_pid,
            mock.patch.object(supervisor, "preserve_dead_worker_worktree"),
            mock.patch.object(supervisor, "sync_preempted_task_status", return_value=True) as sync_preempted,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        self.assertEqual(worker["status"], "superseded")
        terminate_worker_pid.assert_called_once_with(5555)
        sync_preempted.assert_called_once_with(config, worker)

    def test_healthy_owned_ready_todo_worker_cannot_be_preempted_even_when_clean(self) -> None:
        """Healthy active owned_ready worker on a todo task cannot be preempted even when its worktree is clean."""
        config = json.loads(json.dumps(self.config))
        config["agents"]["antigravity"]["worker_slots"] = ["antigravity_slot_1"]
        config["agents"].pop("antigravity_slot_2", None)

        now_iso = supervisor.utc_now()
        worker = {
            "run_id": "run-ready-1",
            "task_id": "TODO-001",
            "provider": "antigravity-1",
            "agent_id": "antigravity_slot_1",
            "logical_agent_id": "antigravity",
            "status": "running",
            "queue_event_id": "evt-ready-1",
            "pid": 5555,
            "last_event_at": now_iso,
            "last_heartbeat_at": now_iso,
            "request_snapshot": {"reason": supervisor.REASON_OWNED_READY},
        }
        state = {
            "queue": {"events": {"evt-ready-1": {"status": "started", "run_id": "run-ready-1"}}},
            "workers": {"run-ready-1": worker},
        }
        task_map = {
            "TODO-001": {
                "id": "TODO-001",
                "status": "todo",
                "owner": "Antigravity",
                "reviewer": "Codex",
                "depends_on": [],
            },
            "REV-001": {
                "id": "REV-001",
                "status": "review",
                "owner": "Codex",
                "reviewer": "Antigravity",
                "depends_on": [],
            },
        }
        status = {"tasks": list(task_map.values())}

        with (
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "worker_worktree_is_clean", return_value=True),
        ):
            self.assertFalse(
                supervisor.worker_can_be_preempted(config, worker, task_map, state)
            )

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "worker_worktree_is_clean", return_value=True),
            mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertFalse(changed)
        self.assertEqual(worker["status"], "running")
        terminate_worker_pid.assert_not_called()

    def test_healthy_helper_claim_worker_cannot_be_preempted_even_when_clean(self) -> None:
        """Healthy active helper_claim worker cannot be preempted even when clean."""
        config = json.loads(json.dumps(self.config))
        config["agents"]["antigravity"]["worker_slots"] = ["antigravity_slot_1"]
        config["agents"].pop("antigravity_slot_2", None)

        now_iso = supervisor.utc_now()
        worker = {
            "run_id": "run-helper-1",
            "task_id": "HELP-001",
            "provider": "antigravity-1",
            "agent_id": "antigravity_slot_1",
            "logical_agent_id": "antigravity",
            "status": "running",
            "queue_event_id": "evt-helper-1",
            "pid": 5555,
            "last_event_at": now_iso,
            "last_heartbeat_at": now_iso,
            "request_snapshot": {"reason": supervisor.REASON_HELPER_CLAIM},
        }
        state = {
            "queue": {"events": {"evt-helper-1": {"status": "started", "run_id": "run-helper-1"}}},
            "workers": {"run-helper-1": worker},
        }
        task_map = {
            "HELP-001": {
                "id": "HELP-001",
                "status": "todo",
                "owner": "Antigravity",
                "reviewer": "Codex",
                "depends_on": [],
            },
            "REV-001": {
                "id": "REV-001",
                "status": "review",
                "owner": "Codex",
                "reviewer": "Antigravity",
                "depends_on": [],
            },
        }
        status = {"tasks": list(task_map.values())}

        with (
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "worker_worktree_is_clean", return_value=True),
        ):
            self.assertFalse(
                supervisor.worker_can_be_preempted(config, worker, task_map, state)
            )

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "worker_worktree_is_clean", return_value=True),
            mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertFalse(changed)
        self.assertEqual(worker["status"], "running")
        terminate_worker_pid.assert_not_called()

    def test_stalled_active_worker_can_be_preempted(self) -> None:
        """Stalled active worker with expired heartbeat and no fresh activity can be preempted."""
        config = json.loads(json.dumps(self.config))
        stale_iso = "2026-08-27T10:00:00Z"
        worker = {
            "run_id": "run-stalled-1",
            "task_id": "TODO-001",
            "provider": "antigravity-1",
            "agent_id": "antigravity_slot_1",
            "logical_agent_id": "antigravity",
            "status": "running",
            "queue_event_id": "evt-stalled-1",
            "pid": 5555,
            "last_event_at": stale_iso,
            "last_heartbeat_at": stale_iso,
            "last_process_activity_at": stale_iso,
            "request_snapshot": {"reason": supervisor.REASON_OWNED_READY},
        }
        task_map = {
            "TODO-001": {
                "id": "TODO-001",
                "status": "todo",
                "owner": "Antigravity",
                "reviewer": "Codex",
                "depends_on": [],
            },
        }
        now_dt = datetime.fromisoformat("2026-08-27T11:00:00+00:00")
        with mock.patch.object(supervisor, "pid_is_alive", return_value=True):
            self.assertTrue(
                supervisor.worker_can_be_preempted(config, worker, task_map, now=now_dt)
            )


class WorkerOsDuplicateGuardTests(unittest.TestCase):
    def _make_fake_proc(self, entries: dict[int, str | None]) -> Path:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(self._cleanup_proc, root)
        for pid, cmdline in entries.items():
            pid_dir = root / str(pid)
            pid_dir.mkdir()
            if cmdline is not None:
                (pid_dir / "cmdline").write_bytes(cmdline.replace(" ", "\x00").encode("utf-8"))
        return root

    @staticmethod
    def _cleanup_proc(root: Path) -> None:
        for child in root.glob("**/*"):
            if child.is_file():
                child.unlink()
        for child in sorted(root.glob("**/*"), reverse=True):
            if child.is_dir():
                child.rmdir()
        root.rmdir()

    def test_scan_groups_pids_by_agent_marker(self) -> None:
        proc = self._make_fake_proc(
            {
                111: "codex exec -C /tmp/wt 你的 auto worker 身分是：Codex 。 Task ID: T1",
                222: "codex exec -C /tmp/wt2 你的 auto worker 身分是：Codex2 。 Task ID: T2",
                333: "codex exec -C /tmp/wt3 你的 auto worker 身分是：Codex 。 Task ID: T3",
                444: "vim",
                555: None,
            }
        )
        result = supervisor.scan_live_worker_pids_by_agent(proc_root=proc)
        self.assertEqual(sorted(result["Codex"]), [111, 333])
        self.assertEqual(result["Codex2"], [222])
        self.assertNotIn("vim", result)

    def test_scan_skips_self_pid(self) -> None:
        proc = self._make_fake_proc(
            {os.getpid(): "auto worker 身分是：Codex"}
        )
        self.assertEqual(supervisor.scan_live_worker_pids_by_agent(proc_root=proc), {})

    def test_block_reason_flags_live_duplicate(self) -> None:
        config = {
            "agents": {"codex": {"provider": "codex"}},
            "ready_dispatcher": {"worker_os_duplicate_guard": True},
        }
        state: dict = {}
        provider_report = {"providers": {"codex": {"auth_ready": True}}}
        with (
            mock.patch.object(supervisor, "display_name_for", return_value="Codex"),
            mock.patch.object(supervisor, "agent_dispatch_paused", return_value=False),
            mock.patch.object(
                supervisor, "scan_live_worker_pids_by_agent",
                return_value={"Codex": [42, 99]},
            ),
        ):
            reason = supervisor.agent_auto_dispatch_block_reason(
                config, state, "codex", provider_report
            )
        self.assertIsNotNone(reason)
        assert reason is not None
        self.assertIn("Codex", reason)
        self.assertIn("42", reason)
        self.assertIn("99", reason)

    def test_block_reason_passes_when_guard_disabled(self) -> None:
        config = {
            "agents": {"codex": {"provider": "codex"}},
            "ready_dispatcher": {"worker_os_duplicate_guard": False},
        }
        provider_report = {"providers": {"codex": {"auth_ready": True}}}
        with (
            mock.patch.object(supervisor, "display_name_for", return_value="Codex"),
            mock.patch.object(supervisor, "agent_dispatch_paused", return_value=False),
            mock.patch.object(
                supervisor, "scan_live_worker_pids_by_agent",
                return_value={"Codex": [42]},
            ) as scan,
        ):
            reason = supervisor.agent_auto_dispatch_block_reason(
                config, {}, "codex", provider_report
            )
        self.assertIsNone(reason)
        scan.assert_not_called()

    def test_block_reason_rejects_invalid_codex_service_tier(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            (codex_home / "config.toml").write_text('service_tier = "turbo"\n', encoding="utf-8")
            config = {
                "agents": {"codex": {"provider": "codex"}},
                "providers": {
                    "codex": {
                        "delivery_mode": "codex",
                        "codex": {"codex_home": str(codex_home)},
                    }
                },
                "ready_dispatcher": {"worker_os_duplicate_guard": False},
            }
            provider_report = {"providers": {"codex": {"local_cli_worker_supported": True, "supports_auto_approve": True}}}

            reason = supervisor.agent_auto_dispatch_block_reason(config, {}, "codex", provider_report)

        self.assertIsNotNone(reason)
        assert reason is not None
        self.assertIn("unsupported service_tier", reason)
        self.assertIn("turbo", reason)

    def test_block_reason_uses_hyphenated_provider_key_for_codex_slot_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            (codex_home / "config.toml").write_text('service_tier = "fast"\n', encoding="utf-8")
            config = {
                "agents": {
                    "codex1_1": {
                        "id": "codex1_1",
                        "provider": "codex1-1",
                        "display_name": "Codex",
                        "dispatch_slot_for": "codex",
                    }
                },
                "providers": {
                    "codex1-1": {
                        "delivery_mode": "codex",
                        "codex": {"codex_home": str(codex_home)},
                    }
                },
                "ready_dispatcher": {"worker_os_duplicate_guard": False},
            }
            provider_report = {"providers": {"codex1-1": {"local_cli_worker_supported": True, "supports_auto_approve": True}}}

            reason = supervisor.agent_auto_dispatch_block_reason(config, {}, "codex1_1", provider_report)

        self.assertIsNone(reason)

    def test_block_reason_ignores_other_agents_processes(self) -> None:
        config = {
            "agents": {"codex": {"provider": "codex"}},
            "ready_dispatcher": {"worker_os_duplicate_guard": True},
        }
        provider_report = {"providers": {"codex": {"auth_ready": True}}}
        with (
            mock.patch.object(supervisor, "display_name_for", return_value="Codex"),
            mock.patch.object(supervisor, "agent_dispatch_paused", return_value=False),
            mock.patch.object(
                supervisor, "scan_live_worker_pids_by_agent",
                return_value={"Claude": [42], "Codex2": [99]},
            ),
        ):
            reason = supervisor.agent_auto_dispatch_block_reason(
                config, {}, "codex", provider_report
            )
        self.assertIsNone(reason)

    def test_block_reason_allows_slotted_logical_agent_with_free_slot(self) -> None:
        config = {
            "agents": {
                "codex": {
                    "provider": "codex",
                    "display_name": "Codex",
                    "worker_slots": ["codex1_1", "codex1_2"],
                },
                "codex1_1": {
                    "id": "codex1_1",
                    "provider": "codex1-1",
                    "display_name": "Codex",
                    "dispatch_slot_for": "codex",
                },
                "codex1_2": {
                    "id": "codex1_2",
                    "provider": "codex1-2",
                    "display_name": "Codex",
                    "dispatch_slot_for": "codex",
                },
            },
            "ready_dispatcher": {"worker_os_duplicate_guard": True},
        }
        state = {
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "agent_id": "codex1_1",
                    "status": "running",
                    "pid": 42,
                }
            }
        }
        provider_report = {"providers": {"codex": {"auth_ready": True}}}
        with mock.patch.object(
            supervisor,
            "scan_live_worker_pids_by_agent",
            return_value={"Codex": [42]},
        ) as scan:
            reason = supervisor.agent_auto_dispatch_block_reason(
                config, state, "codex", provider_report
            )
        self.assertIsNone(reason)
        scan.assert_not_called()

    def test_block_reason_blocks_exact_slot_with_active_worker(self) -> None:
        config = {
            "agents": {
                "codex": {
                    "provider": "codex",
                    "display_name": "Codex",
                    "worker_slots": ["codex1_1", "codex1_2"],
                },
                "codex1_1": {
                    "id": "codex1_1",
                    "provider": "codex1-1",
                    "display_name": "Codex",
                    "dispatch_slot_for": "codex",
                },
                "codex1_2": {
                    "id": "codex1_2",
                    "provider": "codex1-2",
                    "display_name": "Codex",
                    "dispatch_slot_for": "codex",
                },
            },
            "ready_dispatcher": {"worker_os_duplicate_guard": True},
        }
        state = {
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "agent_id": "codex1_1",
                    "status": "running",
                    "pid": 42,
                }
            }
        }
        provider_report = {"providers": {"codex1-1": {"auth_ready": True}, "codex1-2": {"auth_ready": True}}}
        with mock.patch.object(supervisor, "scan_live_worker_pids_by_agent") as scan:
            blocked = supervisor.agent_auto_dispatch_block_reason(
                config, state, "codex1_1", provider_report
            )
            available = supervisor.agent_auto_dispatch_block_reason(
                config, state, "codex1_2", provider_report
            )
        self.assertIsNotNone(blocked)
        assert blocked is not None
        self.assertIn("codex1_1", blocked)
        self.assertIn("42", blocked)
        self.assertIsNone(available)
        scan.assert_not_called()

    def test_block_reason_blocks_slotted_logical_agent_when_all_slots_busy(self) -> None:
        config = {
            "agents": {
                "codex": {
                    "provider": "codex",
                    "display_name": "Codex",
                    "worker_slots": ["codex1_1", "codex1_2"],
                },
                "codex1_1": {
                    "id": "codex1_1",
                    "provider": "codex1-1",
                    "display_name": "Codex",
                    "dispatch_slot_for": "codex",
                },
                "codex1_2": {
                    "id": "codex1_2",
                    "provider": "codex1-2",
                    "display_name": "Codex",
                    "dispatch_slot_for": "codex",
                },
            },
            "ready_dispatcher": {"worker_os_duplicate_guard": True},
        }
        state = {
            "workers": {
                "run-1": {"run_id": "run-1", "agent_id": "codex1_1", "status": "running", "pid": 42},
                "run-2": {"run_id": "run-2", "agent_id": "codex1_2", "status": "running", "pid": 99},
            }
        }
        provider_report = {"providers": {"codex": {"auth_ready": True}}}
        with mock.patch.object(supervisor, "scan_live_worker_pids_by_agent") as scan:
            reason = supervisor.agent_auto_dispatch_block_reason(
                config, state, "codex", provider_report
            )
        self.assertIsNotNone(reason)
        assert reason is not None
        self.assertIn("all dispatch slots", reason)
        self.assertIn("codex1_1", reason)
        self.assertIn("codex1_2", reason)
        scan.assert_not_called()


class RuntimeLeaseReconciliationTests(unittest.TestCase):
    def _config(self, root: Path) -> dict:
        return {
            "paths": {
                "status_file": str(root / "ai-status.json"),
                "activity_log": str(root / "activity-log.jsonl"),
                "event_queue": str(root / "event-queue.jsonl"),
            },
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "status_field": "status",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {},
            "providers": {"codex": {"delivery_mode": "codex", "quota_group": "codex1"}},
            "agents": {"codex": {"id": "codex", "display_name": "Codex", "provider": "codex"}},
        }

    def test_reconcile_runtime_requeues_started_event_without_active_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = self._config(root)
            (root / "ai-status.json").write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "OPS-LEASE-001",
                                "status": "in_progress",
                                "owner": "Codex",
                                "reviewer": "Claude",
                                "depends_on": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "event-queue.jsonl").write_text(
                json.dumps(
                    {
                        "event_id": "evt-lease",
                        "task_id": "OPS-LEASE-001",
                        "target_agent": "codex",
                        "target_display_name": "Codex",
                        "reason": "owned_in_progress_dispatch",
                        "message": "wake",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            state = {
                "queue": {
                    "events": {
                        "evt-lease": {
                            "status": "started",
                            "run_id": "codex-run-missing",
                            "lease_owner": "codex-run-missing",
                        }
                    }
                },
                "workers": {},
            }

            changed = supervisor.reconcile_runtime_on_boot(config, state)

            self.assertTrue(changed)
            record = state["queue"]["events"]["evt-lease"]
            self.assertEqual(record["status"], "queued")
            self.assertEqual(
                record["requeue_reason"],
                "started queue record had no active worker during supervisor boot reconciliation",
            )
            self.assertNotIn("lease_owner", record)
            metrics = state["worker_runtime_metrics"]
            self.assertEqual(metrics["totals"]["started_queue_records_requeued"], 1)
            self.assertEqual(
                metrics["last_measurements"]["boot_reconciliation"]["counts"]["started_queue_records_requeued"],
                1,
            )

    def test_restart_preserves_one_live_claude_worker_without_redispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = self._config(root)
            config["providers"]["claude"] = {"delivery_mode": "claude_cli", "quota_group": "claude"}
            config["agents"]["claude"] = {
                "id": "claude",
                "display_name": "Claude",
                "provider": "claude",
            }
            config["ready_dispatcher"]["agent_order"] = ["claude"]
            task = {
                "id": "ODP-TASKOUTPUT-LIVE",
                "status": "in_progress",
                "priority": "P1",
                "owner": "Claude",
                "reviewer": "Codex",
                "depends_on": [],
            }
            (root / "ai-status.json").write_text(json.dumps({"tasks": [task]}), encoding="utf-8")
            (root / "event-queue.jsonl").write_text(
                json.dumps(
                    {
                        "event_id": "evt-claude",
                        "task_id": task["id"],
                        "target_agent": "claude",
                        "target_display_name": "Claude",
                        "reason": "owned_in_progress_dispatch",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            state = {
                "queue": {
                    "events": {
                        "evt-claude": {
                            "status": "started",
                            "run_id": "claude-run-live",
                        }
                    }
                },
                "workers": {
                    "claude-run-live": {
                        "run_id": "claude-run-live",
                        "status": "running",
                        "provider": "claude",
                        "agent_id": "claude",
                        "task_id": task["id"],
                        "queue_event_id": "evt-claude",
                        "pid": 4242,
                    }
                },
            }

            with (
                mock.patch.object(supervisor, "pid_is_alive", return_value=True),
                mock.patch.object(supervisor, "queue_delivery_event") as queue_delivery_event,
                mock.patch.object(supervisor, "scan_live_worker_pids_by_agent", return_value={"Claude": [4242]}),
            ):
                supervisor.reconcile_runtime_on_boot(config, state)
                dispatched = supervisor.dispatch_ready_tasks(
                    config,
                    state,
                    provider_report={"providers": {"claude": {"auth_ready": True}}},
                )

            active_for_task = [
                worker
                for worker in state["workers"].values()
                if worker.get("task_id") == task["id"] and worker.get("status") == "running"
            ]
            self.assertFalse(dispatched)
            self.assertEqual(len(active_for_task), 1)
            self.assertEqual(active_for_task[0]["run_id"], "claude-run-live")
            queue_delivery_event.assert_not_called()

    def test_reconcile_runtime_fails_running_worker_when_pid_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = self._config(root)
            (root / "ai-status.json").write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "OPS-LEASE-002",
                                "status": "in_progress",
                                "owner": "Codex",
                                "reviewer": "Claude",
                                "depends_on": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "event-queue.jsonl").write_text(
                json.dumps({"event_id": "evt-worker", "task_id": "OPS-LEASE-002", "target_agent": "codex"})
                + "\n",
                encoding="utf-8",
            )
            state = {
                "queue": {"events": {"evt-worker": {"status": "started", "run_id": "codex-run-dead"}}},
                "workers": {
                    "codex-run-dead": {
                        "run_id": "codex-run-dead",
                        "status": "running",
                        "provider": "codex",
                        "agent_id": "codex",
                        "task_id": "OPS-LEASE-002",
                        "queue_event_id": "evt-worker",
                        "pid": 987654,
                    }
                },
            }

            with (
                mock.patch.object(supervisor, "pid_is_alive", return_value=False),
                mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            ):
                changed = supervisor.reconcile_runtime_on_boot(config, state)

            self.assertTrue(changed)
            worker = state["workers"]["codex-run-dead"]
            self.assertEqual(worker["status"], "failed")
            self.assertEqual(state["queue"]["events"]["evt-worker"]["status"], "failed")
            self.assertIn("process missing", worker["last_error"])
            activity_types = [call.args[1]["type"] for call in write_activity_log.call_args_list]
            self.assertEqual(activity_types, ["worker_failed", "worker_runtime_metrics"])
            metrics = state["worker_runtime_metrics"]
            self.assertEqual(metrics["totals"]["missing_process_workers_failed"], 1)
            self.assertEqual(
                metrics["last_measurements"]["boot_reconciliation"]["counts"]["missing_process_workers_failed"],
                1,
            )

    def test_reconcile_runtime_does_not_scan_successful_missing_worker_log_for_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = self._config(root)
            (root / "ai-status.json").write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "OPS-LEASE-003",
                                "status": "review",
                                "owner": "Claude",
                                "reviewer": "Codex",
                                "depends_on": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "event-queue.jsonl").write_text(
                json.dumps({"event_id": "evt-worker", "task_id": "OPS-LEASE-003", "target_agent": "codex"})
                + "\n",
                encoding="utf-8",
            )
            log_path = root / "codex-review.log"
            log_path.write_text(
                "\n".join(
                    [
                        "**Blocker**",
                        '+ completed.stderr = b"Error: not authenticated, please login first"',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            status_path = root / "runner-status.json"
            status_path.write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "exit_code": 0,
                        "finished_at": "2026-06-01T13:07:54Z",
                    }
                ),
                encoding="utf-8",
            )
            state = {
                "queue": {"events": {"evt-worker": {"status": "started", "run_id": "codex-run-done"}}},
                "provider_guardrails": {"dispatch_pauses": {}},
                "workers": {
                    "codex-run-done": {
                        "run_id": "codex-run-done",
                        "status": "running",
                        "provider": "codex",
                        "agent_id": "codex",
                        "task_id": "OPS-LEASE-003",
                        "queue_event_id": "evt-worker",
                        "pid": 987654,
                        "log_path": str(log_path),
                        "runner_status_path": str(status_path),
                    }
                },
            }

            with (
                mock.patch.object(supervisor, "pid_is_alive", return_value=False),
                mock.patch.object(supervisor, "write_failure_evidence") as write_failure_evidence,
                mock.patch.object(supervisor, "mark_provider_dispatch_paused") as mark_provider_dispatch_paused,
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                changed = supervisor.reconcile_runtime_on_boot(config, state)

            self.assertTrue(changed)
            worker = state["workers"]["codex-run-done"]
            self.assertEqual(worker["status"], "completed")
            self.assertNotIn("last_error", worker)
            self.assertEqual(worker["runner_status"], "completed")
            self.assertEqual(worker["exit_code"], 0)
            self.assertEqual(state["queue"]["events"]["evt-worker"]["status"], "completed")
            self.assertEqual(state["provider_guardrails"]["dispatch_pauses"], {})
            write_failure_evidence.assert_not_called()
            mark_provider_dispatch_paused.assert_not_called()

    def test_reconcile_runtime_signal_termination_skips_log_without_marking_success(self) -> None:
        """A SIGTERM marker suppresses stale quota text but cannot complete the worker."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = self._config(root)
            task = {
                "id": "OPS-LEASE-SIGTERM",
                "status": "in_progress",
                "owner": "Codex",
                "reviewer": "Claude",
                "depends_on": [],
            }
            (root / "ai-status.json").write_text(json.dumps({"tasks": [task]}), encoding="utf-8")
            (root / "event-queue.jsonl").write_text(
                json.dumps({"event_id": "evt-sigterm", "task_id": task["id"], "target_agent": "codex"}) + "\n",
                encoding="utf-8",
            )
            log_path = root / "codex.log"
            log_path.write_text("ERROR: You've hit your usage limit.\n", encoding="utf-8")
            state = {
                "queue": {"events": {"evt-sigterm": {"status": "started", "run_id": "codex-run-sigterm"}}},
                "provider_guardrails": {"dispatch_pauses": {}},
                "workers": {
                    "codex-run-sigterm": {
                        "run_id": "codex-run-sigterm",
                        "status": "running",
                        "provider": "codex",
                        "agent_id": "codex",
                        "task_id": task["id"],
                        "queue_event_id": "evt-sigterm",
                        "pid": 987654,
                        "log_path": str(log_path),
                        "runner_status": "completed",
                        "exit_code": 0,
                        "runner_signal": 15,
                    }
                },
            }

            with (
                mock.patch.object(supervisor, "pid_is_alive", return_value=False),
                mock.patch.object(supervisor, "write_failure_evidence") as write_failure_evidence,
                mock.patch.object(supervisor, "mark_provider_dispatch_paused") as mark_provider_dispatch_paused,
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                changed = supervisor.reconcile_runtime_on_boot(config, state)

            self.assertTrue(changed)
            worker = state["workers"]["codex-run-sigterm"]
            self.assertEqual(worker["status"], "failed")
            self.assertNotEqual(worker["status"], "completed")
            self.assertEqual(state["queue"]["events"]["evt-sigterm"]["status"], "failed")
            self.assertEqual(state["provider_guardrails"]["dispatch_pauses"], {})
            write_failure_evidence.assert_not_called()
            mark_provider_dispatch_paused.assert_not_called()

    def test_reconcile_runtime_uses_log_failure_for_missing_process_quota(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = self._config(root)
            config["providers"]["gemini"] = {"delivery_mode": "gemini"}
            config["agents"]["gemini"] = {"id": "gemini", "display_name": "Gemini", "provider": "gemini"}
            (root / "ai-status.json").write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "OPS-LEASE-003",
                                "status": "in_progress",
                                "owner": "Gemini",
                                "reviewer": "Claude",
                                "depends_on": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "event-queue.jsonl").write_text(
                json.dumps({"event_id": "evt-gemini", "task_id": "OPS-LEASE-003", "target_agent": "gemini"})
                + "\n",
                encoding="utf-8",
            )
            log_path = root / "gemini-quota.log"
            log_path.write_text(
                "\n".join(
                    [
                        "Error when talking to Gemini API Full report available at: /tmp/gemini-client-error.json TerminalQuotaError: You have exhausted your capacity on this model.",
                        "reason: 'QUOTA_EXHAUSTED'",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            state = {
                "queue": {"events": {"evt-gemini": {"status": "started", "run_id": "gemini-run-dead"}}},
                "provider_guardrails": {"dispatch_pauses": {}},
                "workers": {
                    "gemini-run-dead": {
                        "run_id": "gemini-run-dead",
                        "status": "running",
                        "provider": "gemini",
                        "agent_id": "gemini",
                        "task_id": "OPS-LEASE-003",
                        "queue_event_id": "evt-gemini",
                        "pid": 987654,
                        "log_path": str(log_path),
                    }
                },
            }

            with (
                mock.patch.object(supervisor, "pid_is_alive", return_value=False),
                mock.patch.object(supervisor, "write_failure_evidence", return_value="evidence/gemini.json"),
                mock.patch.object(supervisor, "maybe_reassign_task_after_worker_failure", return_value="Codex"),
            ):
                changed = supervisor.reconcile_runtime_on_boot(config, state)

            self.assertTrue(changed)
            worker = state["workers"]["gemini-run-dead"]
            self.assertEqual(worker["status"], "reassigned")
            self.assertEqual(worker["reassigned_to"], "Codex")
            self.assertEqual(state["queue"]["events"]["evt-gemini"]["status"], "completed")
            pause = state["provider_guardrails"]["dispatch_pauses"]["gemini"]
            self.assertEqual(pause["pause_kind"], "quota_terminal")
            self.assertEqual(pause["worker_run_id"], "gemini-run-dead")
            # Provider quota is environmental, not evidence that this task is in
            # a logic failure loop; provider pause carries the recovery state.
            self.assertNotIn(
                "OPS-LEASE-003:gemini",
                state["provider_guardrails"]["task_failure_streaks"],
            )
            self.assertIn("capacity", worker["last_error"].lower())

    def test_antigravity_boot_reconciliation_preserves_owner_for_claude_pool(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = self._config(root)
            config["providers"]["antigravity5"] = {
                "delivery_mode": "antigravity",
                "antigravity": {
                    "model_rotation": {
                        "enabled": True,
                        "primary_model": "",
                        "fallback_model": "claude-sonnet-4-6",
                    }
                },
            }
            config["agents"]["antigravity5"] = {
                "id": "antigravity5",
                "display_name": "Antigravity5",
                "provider": "antigravity5",
            }
            (root / "ai-status.json").write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "ODP-AGY-LIVE",
                                "status": "in_progress",
                                "owner": "Antigravity5",
                                "reviewer": "Claude",
                                "depends_on": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "event-queue.jsonl").write_text(
                json.dumps(
                    {
                        "event_id": "evt-agy",
                        "task_id": "ODP-AGY-LIVE",
                        "target_agent": "antigravity5",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            log_path = root / "agy-quota.log"
            log_path.write_text(
                "Error: Individual quota reached. Please upgrade your subscription "
                "to increase your limits. Resets in 2h21m32s.\n",
                encoding="utf-8",
            )
            worker = {
                "run_id": "agy-run-dead",
                "status": "running",
                "provider": "antigravity5",
                "agent_id": "antigravity5",
                "task_id": "ODP-AGY-LIVE",
                "queue_event_id": "evt-agy",
                "pid": 987654,
                "log_path": str(log_path),
                "antigravity_model_pool": "gemini",
                "metadata": {"antigravity_model_pool": "gemini"},
            }
            state = {
                "queue": {"events": {"evt-agy": {"status": "started", "run_id": "agy-run-dead"}}},
                "workers": {"agy-run-dead": worker},
            }

            previous_state_path = supervisor.model_rotation._STATE_PATH
            supervisor.model_rotation._STATE_PATH = root / "model-cooldown.json"
            try:
                with (
                    mock.patch.object(supervisor, "pid_is_alive", return_value=False),
                    mock.patch.object(supervisor, "write_failure_evidence", return_value="evidence/agy.json"),
                    mock.patch.object(supervisor, "maybe_reassign_task_after_worker_failure") as reassign,
                ):
                    self.assertTrue(supervisor.reconcile_runtime_on_boot(config, state))
                    self.assertFalse(supervisor.reconcile_runtime_on_boot(config, state))
                    self.assertEqual(
                        supervisor.model_rotation.resolve_active_selection(config, "antigravity5")["pool"],
                        "claude",
                    )
            finally:
                supervisor.model_rotation._STATE_PATH = previous_state_path

            reassign.assert_not_called()
            self.assertEqual(worker["status"], "failed")
            self.assertEqual(state["queue"]["events"]["evt-agy"]["status"], "failed")
            processed = state["provider_guardrails"]["processed_model_rotation_failures"]
            self.assertEqual(list(processed), ["agy-run-dead"])

    def test_account_pool_cap_blocks_second_slot(self) -> None:
        config = {
            "account_pools": {"codex_main": {"max_concurrent": 1}},
            "agents": {
                "codex1_1": {"id": "codex1_1", "display_name": "Codex", "provider": "codex1-1", "account_pool": "codex_main"},
                "codex1_2": {"id": "codex1_2", "display_name": "Codex", "provider": "codex1-2", "account_pool": "codex_main"},
            },
            "providers": {
                "codex1-1": {"quota_group": "codex1"},
                "codex1-2": {"quota_group": "codex1"},
            },
        }
        state = {
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "status": "running",
                    "agent_id": "codex1_1",
                    "provider": "codex1-1",
                    "quota_group": "codex_main",
                }
            }
        }

        reason = supervisor.agent_auto_dispatch_block_reason(config, state, "codex1_2", provider_report={})

        self.assertIsNotNone(reason)
        self.assertIn("quota group codex_main", reason or "")




class PruneOrphanWorktreesTests(unittest.TestCase):
    def setUp(self) -> None:
        # Pruning receives its merge authority solely from the registry base
        # resolver.  These tests isolate the cleanup policy from network I/O.
        self._registry_patcher = mock.patch(
            "multi_repo_registry.iter_local_repositories",
            return_value=[
                {
                    "id": "pantheon",
                    "resolved_local_path": Path("/repo"),
                    "default_branch": "dev",
                }
            ],
        )
        self._base_patcher = mock.patch.object(
            supervisor,
            "resolve_worker_base",
            return_value=(mock.Mock(sha="a" * 40, remote_ref="origin/dev"), None),
        )
        self._registry_patcher.start()
        self._base_patcher.start()
        self.addCleanup(self._registry_patcher.stop)
        self.addCleanup(self._base_patcher.stop)

    def _stub_subprocess_run(self, results):
        def fake_run(cmd, *args, **kwargs):
            cmd_tuple = tuple(str(c) for c in cmd)
            for key, value in results.items():
                if cmd_tuple[: len(key)] == key:
                    return value
            raise AssertionError(f"unexpected subprocess.run call: {cmd_tuple}")
        return fake_run

    def test_returns_false_when_disabled(self) -> None:
        config = {"worker_worktree_housekeeping": {"enabled": False}}
        state: dict = {}
        self.assertFalse(supervisor.prune_orphan_worktrees(config, state))

    def test_throttled_within_interval(self) -> None:
        from datetime import datetime as _dt
        from datetime import timedelta as _td
        recent_ts = (_dt.now(UTC) - _td(seconds=30)).isoformat().replace("+00:00", "Z")
        config = {"worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 600}}
        state = {"worker_worktree_housekeeping": {"last_run_at": recent_ts}}
        with mock.patch.object(supervisor, "worker_worktree_settings") as ws:
            result = supervisor.prune_orphan_worktrees(config, state)
        self.assertFalse(result)
        ws.assert_not_called()

    def test_skips_when_no_merged_branches(self) -> None:
        config = {"worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 0}}
        state: dict = {}
        with (
            mock.patch.object(supervisor, "worker_worktree_settings", return_value={"enabled": True, "root": "/tmp/wt", "root_configured": True, "git_network_timeout_seconds": 30}),
            mock.patch.object(supervisor, "_worker_worktree_base_root", return_value=Path("/tmp/wt")),
            mock.patch.object(supervisor, "config_path", return_value=Path("/repo/ai-status.json")),
            mock.patch.object(supervisor, "_scan_process_paths_in_root", return_value=set()),
            mock.patch.object(supervisor, "_git_ref_exists", return_value=False),
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(
                supervisor.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            ),
        ):
            result = supervisor.prune_orphan_worktrees(config, state)
        self.assertFalse(result)

    def test_removes_clean_merged_orphan(self) -> None:
        base = Path("/tmp/wt").resolve()
        record_path = str(base / "task-x")
        records = [
            {"worktree": record_path, "branch": "refs/heads/task/X"},
            {"worktree": "/repo", "branch": "refs/heads/main"},
        ]
        merged_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="  task/X\n", stderr="")
        clean_status = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        remove_ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        runs = {
            ("git", "branch", "--merged"): merged_proc,
            ("git", "-C", record_path, "status", "--porcelain"): clean_status,
            ("git", "-C", "/repo", "worktree", "remove", record_path): remove_ok,
        }
        config = {"worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 0}}
        state: dict = {}
        with (
            mock.patch.object(supervisor, "worker_worktree_settings", return_value={"enabled": True, "root_configured": True, "git_network_timeout_seconds": 30}),
            mock.patch.object(supervisor, "_worker_worktree_base_root", return_value=base),
            mock.patch.object(supervisor, "config_path", return_value=Path("/repo/ai-status.json")),
            mock.patch.object(supervisor, "_scan_process_paths_in_root", return_value=set()),
            mock.patch.object(supervisor, "_git_ref_exists", side_effect=lambda _root, ref: ref == "origin/dev"),
            mock.patch.object(supervisor, "_git_worktree_records", return_value=records),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(supervisor.subprocess, "run", side_effect=self._stub_subprocess_run(runs)),
        ):
            result = supervisor.prune_orphan_worktrees(config, state)
        self.assertTrue(result)

    def test_skips_dirty_worktree(self) -> None:
        base = Path("/tmp/wt").resolve()
        record_path = str(base / "task-x")
        records = [{"worktree": record_path, "branch": "refs/heads/task/X"}]
        merged_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="task/X\n", stderr="")
        dirty_status = subprocess.CompletedProcess(args=[], returncode=0, stdout=" M foo.py\n", stderr="")
        runs = {
            ("git", "branch", "--merged"): merged_proc,
            ("git", "-C", record_path, "status", "--porcelain"): dirty_status,
        }
        config = {"worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 0}}
        state: dict = {}
        with (
            mock.patch.object(supervisor, "worker_worktree_settings", return_value={"enabled": True, "root_configured": True, "git_network_timeout_seconds": 30}),
            mock.patch.object(supervisor, "_worker_worktree_base_root", return_value=base),
            mock.patch.object(supervisor, "config_path", return_value=Path("/repo/ai-status.json")),
            mock.patch.object(supervisor, "_scan_process_paths_in_root", return_value=set()),
            mock.patch.object(supervisor, "_git_ref_exists", side_effect=lambda _root, ref: ref == "origin/dev"),
            mock.patch.object(supervisor, "_git_worktree_records", return_value=records),
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(supervisor.subprocess, "run", side_effect=self._stub_subprocess_run(runs)),
        ):
            result = supervisor.prune_orphan_worktrees(config, state)
        self.assertFalse(result)

    def _detached_case(self, *, ancestor: bool, clean: bool = True):
        """A lease-recovery worktree: created with `--detach`, so it has no branch."""
        base = Path("/tmp/wt").resolve()
        record_path = str(base / "task-x.lease_20260819T000000Z_deadbeef")
        records = [{"worktree": record_path, "branch": ""}]
        # No task branch is merged: the branch path could never admit anything here.
        merged_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        status_proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="" if clean else " M foo.py\n", stderr=""
        )
        remove_ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        runs = {
            ("git", "branch", "--merged"): merged_proc,
            ("git", "-C", record_path, "status", "--porcelain"): status_proc,
            ("git", "-C", "/repo", "worktree", "remove", record_path): remove_ok,
        }
        config = {"worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 0}}
        state: dict = {}
        ctx = [
            mock.patch.object(supervisor, "worker_worktree_settings", return_value={"enabled": True, "root_configured": True, "git_network_timeout_seconds": 30}),
            mock.patch.object(supervisor, "_worker_worktree_base_root", return_value=base),
            mock.patch.object(supervisor, "config_path", return_value=Path("/repo/ai-status.json")),
            mock.patch.object(supervisor, "_scan_process_paths_in_root", return_value=set()),
            mock.patch.object(supervisor, "_git_ref_exists", side_effect=lambda _root, ref: ref == "origin/dev"),
            mock.patch.object(supervisor, "_git_worktree_records", return_value=records),
            mock.patch.object(supervisor, "_detached_head_is_merged", return_value=ancestor),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(supervisor.subprocess, "run", side_effect=self._stub_subprocess_run(runs)),
        ]
        return config, state, ctx

    def test_removes_detached_lease_worktree_already_in_base(self) -> None:
        """Lease worktrees are `--detach`, so the branch test could never admit them.

        Every recovery worktree the orchestrator has ever created was structurally
        unprunable: `_worktree_record_branch` returns "" for a detached HEAD and the
        guard rejected it before any other check ran. Ancestry answers the same
        question -- is this content already in a base? -- for a worktree with no name.
        """
        config, state, ctx = self._detached_case(ancestor=True)
        with contextlib.ExitStack() as stack:
            for c in ctx:
                stack.enter_context(c)
            self.assertTrue(supervisor.prune_orphan_worktrees(config, state))

    def test_keeps_detached_worktree_not_yet_in_base(self) -> None:
        """The safety property is unchanged: unmerged work is never deleted."""
        config, state, ctx = self._detached_case(ancestor=False)
        with contextlib.ExitStack() as stack:
            for c in ctx:
                stack.enter_context(c)
            self.assertFalse(supervisor.prune_orphan_worktrees(config, state))

    def test_keeps_dirty_detached_worktree_even_when_merged(self) -> None:
        """Ancestry does not override the dirty-tree guard."""
        config, state, ctx = self._detached_case(ancestor=True, clean=False)
        with contextlib.ExitStack() as stack:
            for c in ctx:
                stack.enter_context(c)
            self.assertFalse(supervisor.prune_orphan_worktrees(config, state))

    def test_detached_head_is_merged_fails_closed(self) -> None:
        """An unreadable HEAD or unanswerable ancestry keeps the worktree."""
        repo = Path("/repo")
        wt = Path("/tmp/wt/task-x.lease_1")
        self.assertFalse(supervisor._detached_head_is_merged(repo, wt, []))
        with mock.patch.object(supervisor, "_git_commit_oid", return_value=None):
            self.assertFalse(supervisor._detached_head_is_merged(repo, wt, ["origin/dev"]))
        with (
            mock.patch.object(supervisor, "_git_commit_oid", return_value="abc123"),
            mock.patch.object(supervisor, "_git_output", return_value=(128, "")),
        ):
            self.assertFalse(supervisor._detached_head_is_merged(repo, wt, ["origin/dev"]))
        with (
            mock.patch.object(supervisor, "_git_commit_oid", return_value="abc123"),
            mock.patch.object(supervisor, "_git_output", return_value=(0, "")),
        ):
            self.assertTrue(supervisor._detached_head_is_merged(repo, wt, ["origin/dev"]))

    def _claim_case(self, worker: dict, *, clean: bool = True):
        base = Path("/tmp/wt").resolve()
        record_path = str(base / "task-x")
        records = [{"worktree": record_path, "branch": "refs/heads/task/X"}]
        merged_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="task/X\n", stderr="")
        status_proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="" if clean else " M f.py\n", stderr=""
        )
        remove_ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        runs = {
            ("git", "branch", "--merged"): merged_proc,
            ("git", "-C", record_path, "status", "--porcelain"): status_proc,
            ("git", "-C", "/repo", "worktree", "remove", record_path): remove_ok,
        }
        config = {"worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 0}}
        state = {"workers": {"r-1": {**worker, "workspace_path": record_path}}}
        ctx = [
            mock.patch.object(supervisor, "worker_worktree_settings", return_value={"enabled": True, "root_configured": True, "git_network_timeout_seconds": 30}),
            mock.patch.object(supervisor, "_worker_worktree_base_root", return_value=base),
            mock.patch.object(supervisor, "config_path", return_value=Path("/repo/ai-status.json")),
            mock.patch.object(supervisor, "_scan_process_paths_in_root", return_value=set()),
            mock.patch.object(supervisor, "_git_ref_exists", side_effect=lambda _root, ref: ref == "origin/dev"),
            mock.patch.object(supervisor, "_git_worktree_records", return_value=records),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(supervisor.subprocess, "run", side_effect=self._stub_subprocess_run(runs)),
        ]
        return config, state, ctx

    def test_finished_worker_does_not_hold_its_worktree(self) -> None:
        """A record that can never run again must not reserve a checkout.

        Every worker record used to claim its workspace regardless of status, so a
        `completed` or `failed` worker kept its worktree until `max_worker_history`
        evicted the record. Observed live: 200 records (142 completed, 45 failed,
        12 superseded) holding every reclaimable worktree, with the pruner running
        on schedule and removing nothing.
        """
        for status in ("completed", "failed", "superseded", "reassigned", "retried"):
            with self.subTest(status=status):
                config, state, ctx = self._claim_case({"status": status})
                with contextlib.ExitStack() as stack:
                    for c in ctx:
                        stack.enter_context(c)
                    self.assertTrue(supervisor.prune_orphan_worktrees(config, state))

    def test_active_worker_still_holds_its_worktree(self) -> None:
        for status in sorted(runtime_state.ACTIVE_WORKER_STATUSES):
            with self.subTest(status=status):
                config, state, ctx = self._claim_case({"status": status})
                with contextlib.ExitStack() as stack:
                    for c in ctx:
                        stack.enter_context(c)
                    self.assertFalse(supervisor.prune_orphan_worktrees(config, state))

    def test_dropping_the_claim_does_not_bypass_the_dirty_guard(self) -> None:
        config, state, ctx = self._claim_case({"status": "completed"}, clean=False)
        with contextlib.ExitStack() as stack:
            for c in ctx:
                stack.enter_context(c)
            self.assertFalse(supervisor.prune_orphan_worktrees(config, state))

    def test_skips_worktree_claimed_by_active_worker(self) -> None:
        base = Path("/tmp/wt").resolve()
        record_path = str(base / "task-x")
        records = [{"worktree": record_path, "branch": "refs/heads/task/X"}]
        merged_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="task/X\n", stderr="")
        runs = {
            ("git", "branch", "--merged"): merged_proc,
        }
        config = {"worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 0}}
        # Status matters now: without one this fixture could not tell "any worker
        # claims" from "an active worker claims", which is how the gap survived.
        state = {"workers": {"r-1": {"status": "running", "workspace_path": record_path}}}
        with (
            mock.patch.object(supervisor, "worker_worktree_settings", return_value={"enabled": True, "root_configured": True, "git_network_timeout_seconds": 30}),
            mock.patch.object(supervisor, "_worker_worktree_base_root", return_value=base),
            mock.patch.object(supervisor, "config_path", return_value=Path("/repo/ai-status.json")),
            mock.patch.object(supervisor, "_scan_process_paths_in_root", return_value=set()),
            mock.patch.object(supervisor, "_git_ref_exists", side_effect=lambda _root, ref: ref == "origin/dev"),
            mock.patch.object(supervisor, "_git_worktree_records", return_value=records),
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(supervisor.subprocess, "run", side_effect=self._stub_subprocess_run(runs)),
        ):
            result = supervisor.prune_orphan_worktrees(config, state)
        self.assertFalse(result)


class PruneOrphanWorktreeAccountingTests(PruneOrphanWorktreesTests):
    """A refused reclaim must say why it refused.

    Every guard in `prune_orphan_worktrees` ends in a bare `continue`, and the
    only log it emitted was the success one, so a pruner that examined 69
    worktrees and reclaimed none logged exactly what an idle pruner logged. On
    a live host that silence lasted until the disk hit 99%.

    Subclasses the guard tests to inherit their registry/base isolation; the
    inherited cases re-run here, which is cheap and keeps the two in step.
    """

    MANAGED_ROOT = Path("/tmp/wt").resolve()

    def _scan(
        self,
        state: dict,
        records: list[dict],
        runs: dict,
        *,
        base: Path | None = None,
        housekeeping: dict | None = None,
        live_paths: set | None = None,
        classify_result=None,
    ):
        """Run one prune tick over `records` and hand back its return value.

        `runs` is exhaustive: `_stub_subprocess_run` raises on any command it
        was not given, so a test that omits a command is asserting that command
        never runs.
        """
        base = base if base is not None else self.MANAGED_ROOT
        settings = {"enabled": True, "tick_interval_seconds": 0}
        settings.update(housekeeping or {})
        config = {"worker_worktree_housekeeping": settings}
        classify = (
            mock.patch.object(supervisor, "_classify_processes_in_worktree", return_value=classify_result)
            if classify_result is not None
            else contextlib.nullcontext()
        )
        with (
            mock.patch.object(supervisor, "worker_worktree_settings", return_value={"enabled": True, "root_configured": True, "git_network_timeout_seconds": 30}),
            mock.patch.object(supervisor, "_worker_worktree_base_root", return_value=base),
            mock.patch.object(supervisor, "config_path", return_value=Path("/repo/ai-status.json")),
            mock.patch.object(supervisor, "_scan_process_paths_in_root", return_value=live_paths or set()),
            classify,
            mock.patch.object(supervisor, "_git_ref_exists", side_effect=lambda _root, ref: ref == "origin/dev"),
            mock.patch.object(supervisor, "_git_worktree_records", return_value=records),
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(supervisor.subprocess, "run", side_effect=self._stub_subprocess_run(runs)),
        ):
            return supervisor.prune_orphan_worktrees(config, state)

    @staticmethod
    def _completed(stdout: str = "", returncode: int = 0):
        return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")

    @staticmethod
    def _last_scan(state: dict) -> dict:
        return state["worker_worktree_housekeeping"]["last_scan"]

    def _dirty_case(self, state: dict):
        record_path = str(self.MANAGED_ROOT / "task-x")
        return self._scan(
            state,
            [{"worktree": record_path, "branch": "refs/heads/task/X"}],
            {
                ("git", "branch", "--merged"): self._completed("task/X\n"),
                ("git", "-C", record_path, "status", "--porcelain"): self._completed(" M foo.py\n"),
            },
        )

    def _live_process_case(self, state: dict, classify_result):
        """A worktree nothing active claims, but something inside is running."""
        record_path = str(self.MANAGED_ROOT / "task-x")
        return self._scan(
            state,
            [{"worktree": record_path, "branch": "refs/heads/task/X"}],
            {("git", "branch", "--merged"): self._completed("task/X\n")},
            live_paths={Path(record_path)},
            classify_result=classify_result,
        )

    def test_a_worktree_held_only_by_orphans_is_reported_as_reclaimable(self) -> None:
        """An init-reparented holder means the owning worker is already gone.

        Twelve pgserver instances outlived their workers by up to three days
        here. Each had called `setsid()`, so no process group reached them, and
        the pruner reported them the same way it reported genuinely busy trees.
        """
        state: dict = {}
        self.assertFalse(self._live_process_case(state, ([4242], 0)))
        scan = self._last_scan(state)
        self.assertEqual(scan["skipped"], {"live_process_orphaned": 1})
        self.assertEqual(
            scan["orphaned_worktrees"], [{"path": str(Path("/tmp/wt").resolve() / "task-x"), "pids": [4242]}]
        )

    def test_a_still_parented_holder_is_a_real_tenant_and_stays_opaque(self) -> None:
        state: dict = {}
        self.assertFalse(self._live_process_case(state, ([], 1)))
        scan = self._last_scan(state)
        self.assertEqual(scan["skipped"], {"live_process": 1})
        self.assertEqual(scan["orphaned_worktrees"], [])

    def test_one_living_parent_disqualifies_the_whole_tree(self) -> None:
        """Orphans alongside a live tenant must not be called reclaimable."""
        state: dict = {}
        self._live_process_case(state, ([4242], 1))
        scan = self._last_scan(state)
        self.assertEqual(scan["skipped"], {"live_process": 1})
        self.assertEqual(scan["orphaned_worktrees"], [])

    def test_worktrees_outside_the_managed_root_are_counted_not_ignored(self) -> None:
        """A repo's worktrees elsewhere are not reclaimable, but they are real.

        68 of 178 registered worktrees on the live host sat outside the managed
        root and held 8.3G. The skip happened before any counter, so they were
        invisible to every reclaim report -- not even counted as refused.
        """
        base = Path("/tmp/wt").resolve()
        outside = "/tmp/somewhere-else/task-y"
        records = [
            {"worktree": str(base / "task-x"), "branch": "refs/heads/task/X"},
            {"worktree": outside, "branch": "refs/heads/task/Y"},
        ]
        merged_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="task/X\n", stderr="")
        dirty_status = subprocess.CompletedProcess(args=[], returncode=0, stdout=" M foo.py\n", stderr="")
        runs = {
            ("git", "branch", "--merged"): merged_proc,
            ("git", "-C", str(base / "task-x"), "status", "--porcelain"): dirty_status,
        }
        config = {"worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 0}}
        state: dict = {}
        with (
            mock.patch.object(supervisor, "worker_worktree_settings", return_value={"enabled": True, "root_configured": True, "git_network_timeout_seconds": 30}),
            mock.patch.object(supervisor, "_worker_worktree_base_root", return_value=base),
            mock.patch.object(supervisor, "config_path", return_value=Path("/repo/ai-status.json")),
            mock.patch.object(supervisor, "_scan_process_paths_in_root", return_value=set()),
            mock.patch.object(supervisor, "_git_ref_exists", side_effect=lambda _root, ref: ref == "origin/dev"),
            mock.patch.object(supervisor, "_git_worktree_records", return_value=records),
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(supervisor.subprocess, "run", side_effect=self._stub_subprocess_run(runs)),
        ):
            supervisor.prune_orphan_worktrees(config, state)

        scan = self._last_scan(state)
        self.assertEqual(scan["unmanaged"], 1)
        self.assertEqual(scan["unmanaged_sample"], [outside])
        # The outside one is not a reclaim candidate, so it must not inflate
        # `examined` -- that number is the denominator for "reclaimed 0 of N".
        self.assertEqual(scan["examined"], 1)
        self.assertEqual(scan["skipped"], {"dirty": 1})

    def test_records_refusal_reasons_when_nothing_is_reclaimed(self) -> None:
        state: dict = {}
        self.assertFalse(self._dirty_case(state))
        scan = self._last_scan(state)
        self.assertEqual(scan["examined"], 1)
        self.assertEqual(scan["removed"], 0)
        self.assertEqual(scan["skipped"], {"dirty": 1})

    def test_stall_marker_starts_on_the_first_fully_refused_tick(self) -> None:
        state: dict = {}
        self._dirty_case(state)
        bucket = state["worker_worktree_housekeeping"]
        self.assertTrue(bucket.get("stall_since"))
        self.assertEqual(bucket.get("last_stall_signature"), "dirty=1")

    def test_a_missing_activity_log_sink_does_not_break_the_reclaim(self) -> None:
        """The counts live in state; the log is a convenience on top of them."""
        state: dict = {}
        with mock.patch.object(
            supervisor, "write_activity_log", side_effect=KeyError("activity_log")
        ):
            self.assertFalse(self._dirty_case(state))
        self.assertEqual(self._last_scan(state)["skipped"], {"dirty": 1})

    def test_a_sibling_root_sharing_the_name_prefix_is_not_managed(self) -> None:
        """`<root>-archive` is a different directory, not a member of `<root>`.

        Membership used to be `str.startswith` on the raw record, so every
        sibling whose name merely began with the root's name was pulled into
        the reclaim candidate set. `runs` carries no `git status` entry for the
        sibling, so if it is ever treated as managed the stub raises.
        """
        sibling = f"{self.MANAGED_ROOT}-archive/task-y"
        state: dict = {}
        self.assertFalse(
            self._scan(
                state,
                [{"worktree": sibling, "branch": "refs/heads/task/Y"}],
                {("git", "branch", "--merged"): self._completed("task/Y\n")},
            )
        )
        scan = self._last_scan(state)
        self.assertEqual(scan["examined"], 0)
        self.assertEqual(scan["unmanaged"], 1)
        self.assertEqual(scan["unmanaged_sample"], [sibling])

    def test_a_symlink_pointing_out_of_the_managed_root_is_not_managed(self) -> None:
        """The record's spelling is not evidence of where the tree lives.

        A link sitting under the root satisfies any string-prefix test while
        its content is somewhere the pruner has no authority over, which is the
        direction of this bug that actually destroys data.
        """
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw).resolve()
            root = tmp / "root"
            outside = tmp / "outside"
            root.mkdir()
            outside.mkdir()
            link = root / "task-z"
            link.symlink_to(outside)
            state: dict = {}
            self.assertFalse(
                self._scan(
                    state,
                    [{"worktree": str(link), "branch": "refs/heads/task/Z"}],
                    {("git", "branch", "--merged"): self._completed("task/Z\n")},
                    base=root,
                )
            )
        scan = self._last_scan(state)
        self.assertEqual(scan["examined"], 0)
        self.assertEqual(scan["unmanaged"], 1)

    def test_a_capped_tick_still_counts_the_worktrees_it_did_not_reach(self) -> None:
        """The cap bounds removals, not the accounting.

        It used to `break` out of both loops, so everything after the cap fell
        out of `examined` and `unmanaged` while `unmanaged` was still published
        as the exact total -- under-reporting of exactly the kind this record
        exists to end.
        """
        first = str(self.MANAGED_ROOT / "task-a")
        second = str(self.MANAGED_ROOT / "task-b")
        outside = f"{self.MANAGED_ROOT}-archive/task-c"
        state: dict = {}
        # `second` has no `git status` entry and no `worktree remove` entry: the
        # cap must stop it from being evaluated at all, not merely from being
        # removed.
        with mock.patch.object(supervisor, "write_activity_log"):
            reclaimed = self._scan(
                state,
                [
                    {"worktree": first, "branch": "refs/heads/task/A"},
                    {"worktree": second, "branch": "refs/heads/task/B"},
                    {"worktree": outside, "branch": "refs/heads/task/C"},
                ],
                {
                    ("git", "branch", "--merged"): self._completed("task/A\ntask/B\ntask/C\n"),
                    ("git", "-C", first, "status", "--porcelain"): self._completed(),
                    ("git", "-C", "/repo", "worktree", "remove", first): self._completed(),
                },
                housekeeping={"max_removals_per_tick": 1},
            )
        self.assertTrue(reclaimed)
        scan = self._last_scan(state)
        self.assertEqual(scan["removed"], 1)
        self.assertTrue(scan["capped_at_max_removals"])
        self.assertEqual(scan["examined"], 2)
        self.assertEqual(scan["skipped"], {"deferred_removal_cap": 1})
        # The one that matters: discovered after the cap, still counted.
        self.assertEqual(scan["unmanaged"], 1)
        self.assertEqual(scan["unmanaged_sample"], [outside])

    def test_a_zero_cap_counts_without_resolving_a_base_or_touching_git(self) -> None:
        """Counting is cheap; the work the cap exists to bound is not.

        An empty `runs` makes every subprocess an error, so this also pins that
        a capped tick skips base resolution and per-worktree status queries
        rather than merely skipping the removal.
        """
        state: dict = {}
        self.assertFalse(
            self._scan(
                state,
                [
                    {"worktree": str(self.MANAGED_ROOT / "task-a"), "branch": "refs/heads/task/A"},
                    {"worktree": f"{self.MANAGED_ROOT}-archive/task-c", "branch": "refs/heads/task/C"},
                ],
                {},
                housekeeping={"max_removals_per_tick": 0},
            )
        )
        scan = self._last_scan(state)
        self.assertEqual(scan["examined"], 1)
        self.assertEqual(scan["unmanaged"], 1)
        self.assertEqual(scan["skipped"], {"deferred_removal_cap": 1})
        self.assertTrue(scan["capped_at_max_removals"])

    def test_a_process_in_a_prefix_sibling_does_not_pin_a_worktree(self) -> None:
        """`/tmp/wt/task-x-old` is not inside `/tmp/wt/task-x`.

        Both the live-path guard and the orphan classifier compared paths as
        strings, so a neighbour whose name merely extended this one read as a
        live tenant. Here that mislabels a clean, merged worktree as busy and
        leaves the disk unreclaimed.
        """
        record_path = str(self.MANAGED_ROOT / "task-x")
        state: dict = {}
        with mock.patch.object(supervisor, "write_activity_log"):
            reclaimed = self._scan(
                state,
                [{"worktree": record_path, "branch": "refs/heads/task/X"}],
                {
                    ("git", "branch", "--merged"): self._completed("task/X\n"),
                    ("git", "-C", record_path, "status", "--porcelain"): self._completed(),
                    ("git", "-C", "/repo", "worktree", "remove", record_path): self._completed(),
                },
                live_paths={Path(f"{record_path}-old")},
            )
        self.assertTrue(reclaimed)
        scan = self._last_scan(state)
        self.assertEqual(scan["removed"], 1)
        self.assertEqual(scan["skipped"], {})


class RuntimeSettingsAreReachableFromConfigTests(unittest.TestCase):
    """A knob the schema rejects is a knob that does not exist.

    `config.schema.json` is enforced on every config load and these objects are
    `additionalProperties: false`, so a setting the code reads but the schema
    never declares cannot be set -- and, worse, cannot be turned off.
    `escalate_to_human_after_reopens` shipped that way: the code documented 0 as
    the opt-out and the schema made writing 0 a hard ConfigError, leaving a
    fleet-wide escalation threshold with no way to change or disable it.
    """

    SCHEMA = json.loads((THIS_DIR / "config.schema.json").read_text(encoding="utf-8"))

    def _validate(self, config: dict) -> None:
        from common import validate_config

        validate_config(config, source="<test>")

    def test_review_churn_escalation_threshold_is_settable_and_disablable(self) -> None:
        for value in (0, 6, 12):
            with self.subTest(value=value):
                self._validate(
                    {"worker_reassignment": {"review_churn": {"escalate_to_human_after_reopens": value}}}
                )

    def test_worktree_housekeeping_is_settable(self) -> None:
        self._validate(
            {
                "worker_worktree_housekeeping": {
                    "enabled": True,
                    "tick_interval_seconds": 600,
                    "max_removals_per_tick": 5,
                    "stall_log_interval_seconds": 3600,
                }
            }
        )

    def test_every_default_these_readers_apply_is_declared_in_the_schema(self) -> None:
        """Covers the next knob added to either block, not just today's.

        Each reader's defaulted key set is the contract it offers operators;
        anything in it the schema omits is unreachable at runtime.
        """
        cases = {
            ("worker_reassignment", "review_churn"): supervisor.review_churn_settings({}),
            ("worker_worktree_housekeeping",): supervisor.worker_worktree_housekeeping_settings({}),
        }
        for path, settings in cases.items():
            with self.subTest(path=".".join(path)):
                node = self.SCHEMA
                for part in path:
                    node = node["properties"][part]
                self.assertEqual(set(settings), set(node["properties"]))



class ResolvePollIntervalTests(unittest.TestCase):
    def test_default_uses_config_value(self) -> None:
        config = {"supervisor": {"poll_interval_seconds": 300}}
        value, source = supervisor.resolve_poll_interval(
            config, cli_value=None, allow_fast_poll=False
        )
        self.assertEqual(value, 300.0)
        self.assertEqual(source, "config")

    def test_cli_value_at_or_above_config_does_not_require_authorization(self) -> None:
        config = {"supervisor": {"poll_interval_seconds": 300}}
        value, source = supervisor.resolve_poll_interval(
            config, cli_value=600.0, allow_fast_poll=False
        )
        self.assertEqual(value, 600.0)
        self.assertEqual(source, "cli")

    def test_cli_value_below_config_requires_allow_fast_poll(self) -> None:
        config = {"supervisor": {"poll_interval_seconds": 300}}
        with self.assertRaises(SystemExit) as ctx:
            supervisor.resolve_poll_interval(
                config, cli_value=60.0, allow_fast_poll=False
            )
        self.assertIn("--allow-fast-poll", str(ctx.exception))

    def test_cli_value_below_config_allowed_when_authorized(self) -> None:
        config = {"supervisor": {"poll_interval_seconds": 300}}
        value, source = supervisor.resolve_poll_interval(
            config, cli_value=60.0, allow_fast_poll=True
        )
        self.assertEqual(value, 60.0)
        self.assertEqual(source, "cli")

    def test_zero_or_negative_cli_value_rejected(self) -> None:
        config = {"supervisor": {"poll_interval_seconds": 300}}
        with self.assertRaises(SystemExit):
            supervisor.resolve_poll_interval(
                config, cli_value=0.0, allow_fast_poll=True
            )
        with self.assertRaises(SystemExit):
            supervisor.resolve_poll_interval(
                config, cli_value=-5.0, allow_fast_poll=True
            )

    def test_missing_config_falls_back_to_default(self) -> None:
        value, source = supervisor.resolve_poll_interval(
            {}, cli_value=None, allow_fast_poll=False
        )
        self.assertEqual(value, supervisor.CONFIG_DEFAULT_POLL_INTERVAL_SECONDS)
        self.assertEqual(source, "config")


class RunSupervisorShellGuardTests(unittest.TestCase):
    def _script(self) -> Path:
        return Path(supervisor.__file__).resolve().parent.parent / "scripts" / "run-supervisor.sh"

    def _run(self, args: list[str], stub_body: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "python3"
            stub.write_text(stub_body)
            stub.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{tmp}:{env.get('PATH', '')}"
            return subprocess.run(
                ["bash", str(self._script()), *args],
                env=env,
                capture_output=True,
                text=True,
            )

    def test_poll_interval_without_allow_fast_poll_is_rejected(self) -> None:
        script = self._script()
        if not script.exists():
            self.skipTest("run-supervisor.sh not present")
        proc = self._run(["--poll-interval", "60"], "#!/bin/sh\necho 'should not run' >&2\nexit 99\n")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("--allow-fast-poll", proc.stderr)

    def test_poll_interval_equals_form_also_rejected(self) -> None:
        script = self._script()
        if not script.exists():
            self.skipTest("run-supervisor.sh not present")
        proc = self._run(["--poll-interval=60"], "#!/bin/sh\necho 'should not run' >&2\nexit 99\n")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("--allow-fast-poll", proc.stderr)

    def test_poll_interval_with_allow_fast_poll_passes_through(self) -> None:
        script = self._script()
        if not script.exists():
            self.skipTest("run-supervisor.sh not present")
        proc = self._run(
            ["--poll-interval", "60", "--allow-fast-poll"], '#!/bin/sh\nexit 7\n'
        )
        self.assertEqual(proc.returncode, 7, proc.stderr)

    def test_no_poll_interval_passes_through(self) -> None:
        script = self._script()
        if not script.exists():
            self.skipTest("run-supervisor.sh not present")
        proc = self._run(["--verbose"], '#!/bin/sh\nexit 11\n')
        self.assertEqual(proc.returncode, 11, proc.stderr)



class ReviewHeadFreezeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.test_dir.name)
        ai_status.STATUS_FILE = self.tmp_path / "ai-status.json"
        ai_status.LOG_FILE = self.tmp_path / "ai-activity-log.jsonl"
        ai_status.CURRENT_WORK_FILE = self.tmp_path / "current-work.md"
        ai_status.STATUS_FILE.write_text("{}", encoding="utf-8")
        ai_status.LOG_FILE.write_text("", encoding="utf-8")
        ai_status.clear_ai_status_caches()

    def tearDown(self) -> None:
        ai_status.clear_ai_status_caches()
        self.test_dir.cleanup()

    def _build_freeze_test_config(self) -> dict[str, Any]:
        config = load_test_config()
        ready_disp = config.setdefault("ready_dispatcher", {})
        ready_disp["enabled"] = True
        ready_disp["disabled_agents"] = []
        ready_disp["review_statuses"] = ["review"]
        ready_disp["finalize_statuses"] = ["review_approved"]
        ready_disp["owned_statuses"] = ["in_progress", "todo"]
        ready_disp["active_worker_statuses"] = ["running", "waiting_approval"]
        max_by_agent = ready_disp.setdefault("max_tasks_per_agent_by_agent", {})
        max_by_agent["Antigravity4"] = 10
        max_by_agent["antigravity4"] = 10
        quota_groups = ready_disp.setdefault("max_concurrent_per_quota_group", {})
        quota_groups["antigravity4"] = 10
        quota_groups["antigravity"] = 10
        agents = config.setdefault("agents", {})
        agents["antigravity4"] = {
            "display_name": "Antigravity4",
            "provider": "antigravity",
            "adapter": "antigravity",
        }
        return config

    def test_approve_saves_approved_head_and_rejects_same_owner_reviewer(self) -> None:
        state = {
            "tasks": [
                {
                    "id": "FREEZE-TEST-001",
                    "owner": "Antigravity4",
                    "reviewer": "Claude",
                    "status": "review",
                    "review_submission": {"remote_sha": "1111111122222222333333334444444455555555"},
                },
                {
                    "id": "FREEZE-TEST-002",
                    "owner": "Claude",
                    "reviewer": "Claude",
                    "status": "review",
                },
            ]
        }
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Claude"):
            with unittest.mock.patch("ai_status.resolve_task_sha", return_value="1111111122222222333333334444444455555555"):
                with unittest.mock.patch("ai_status.sync_all"):
                    with self.assertRaises(SystemExit) as cm:
                        ai_status.command_approve(state, ["FREEZE-TEST-002", "Approve self"])
                    self.assertIn("must be separate identities", str(cm.exception))

                    ai_status.command_approve(state, ["FREEZE-TEST-001", "Approve valid"])
                    task = ai_status.get_task(state, "FREEZE-TEST-001")
                    self.assertEqual(task["status"], "review_approved")
                    self.assertEqual(task["approved_head"], "1111111122222222333333334444444455555555")

    def test_command_done_rejects_mutated_head(self) -> None:
        state = {
            "tasks": [
                {
                    "id": "FREEZE-TEST-003",
                    "owner": "Antigravity4",
                    "reviewer": "Claude",
                    "status": "review_approved",
                    "approved_head": "1111111122222222333333334444444455555555",
                }
            ]
        }
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"):
            with unittest.mock.patch(
                "ai_status.collect_done_delivery_metadata",
                return_value={
                    "verified_head": "9999999922222222333333334444444455555555",
                    "pull_request": {
                        "head_sha": "1111111122222222333333334444444455555555",
                        "merge_commit": "aaaaaaaa22222222333333334444444455555555",
                    },
                },
            ):
                with self.assertRaises(SystemExit) as cm:
                    ai_status.command_done(state, ["FREEZE-TEST-003", "Finalize done"])
                self.assertIn("differs from reviewer-approved head", str(cm.exception))

    def test_command_done_fails_closed_when_delivery_checkout_sha_unresolved_or_collector_raises(self) -> None:
        state = {
            "tasks": [
                {
                    "id": "FREEZE-TEST-003B",
                    "owner": "Antigravity4",
                    "reviewer": "Claude",
                    "status": "review_approved",
                    "approved_head": "1111111122222222333333334444444455555555",
                }
            ]
        }
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"):
            with unittest.mock.patch(
                "ai_status.collect_done_delivery_metadata",
                return_value={
                    "verified_head": None,
                    "pull_request": {
                        "head_sha": "1111111122222222333333334444444455555555",
                        "merge_commit": "aaaaaaaa22222222333333334444444455555555",
                    },
                },
            ):
                with self.assertRaises(SystemExit) as cm:
                    ai_status.command_done(state, ["FREEZE-TEST-003B", "Finalize done"])
                self.assertIn("differs from reviewer-approved head", str(cm.exception))

            with unittest.mock.patch(
                "ai_status.collect_done_delivery_metadata",
                side_effect=SystemExit("Cannot finalize task: task-owned checkout HEAD is unavailable."),
            ):
                with self.assertRaises(SystemExit) as cm:
                    ai_status.command_done(state, ["FREEZE-TEST-003B", "Finalize done"])
                self.assertIn("task-owned checkout HEAD is unavailable", str(cm.exception))

    def test_supervisor_reverts_mutated_approved_head_to_review_on_disk(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            status_file = tmppath / "ai-status.json"
            initial_status = {
                "tasks": [
                    {
                        "id": "FREEZE-TEST-004",
                        "owner": "Antigravity4",
                        "reviewer": "Claude",
                        "status": "review_approved",
                        "approved_head": "1111111122222222333333334444444455555555",
                    }
                ]
            }
            status_file.write_text(json.dumps(initial_status), encoding="utf-8")

            config = self._build_freeze_test_config()
            config["paths"]["status_file"] = str(status_file)
            state = {
                "seen_event_keys": {},
                "ready_dispatcher": {"dispatch_cursor": 0},
                "status": initial_status,
            }

            ai_status.clear_ai_status_caches()
            with unittest.mock.patch("supervisor.scan_live_worker_pids_by_agent", return_value={}), \
                 unittest.mock.patch("supervisor.outstanding_delivery_indexes", return_value=(set(), set(), set())), \
                 unittest.mock.patch("ai_status.resolve_task_sha", return_value="8888888822222222333333334444444455555555"), \
                 unittest.mock.patch("supervisor.agent_auto_dispatch_block_reason", return_value=None), \
                 unittest.mock.patch("supervisor.sync_status_pipeline", return_value=True), \
                 unittest.mock.patch("supervisor.write_activity_log"):
                supervisor.dispatch_ready_tasks(
                    config,
                    state,
                    agent_ids_override=["antigravity4"],
                )
                disk_status = json.loads(status_file.read_text(encoding="utf-8"))
                disk_task = disk_status["tasks"][0]
                self.assertEqual(disk_task["status"], "review")
                self.assertIn("re-review required", disk_task["next"])
                self.assertNotIn("approved_head", disk_task)

    def test_task_pr_ci_status_handles_checkrun_completed_failure(self) -> None:
        ai_status.clear_ai_status_caches()
        fake_payload = {
            "state": "OPEN",
            "statusCheckRollup": [
                {
                    "__typename": "CheckRun",
                    "name": "build",
                    "status": "COMPLETED",
                    "conclusion": "FAILURE",
                }
            ],
        }
        with unittest.mock.patch("ai_status.run_gh_json_command", return_value=fake_payload):
            pr_state, ci_status = ai_status.task_pr_ci_status("TEST-CI-001")
            self.assertEqual(pr_state, "OPEN")
            self.assertEqual(ci_status, "failure")

    def test_dispatch_priority_for_task_and_agent_primary_work(self) -> None:
        config = self._build_freeze_test_config()
        task_mutated = {
            "id": "FREEZE-TEST-007",
            "owner": "Antigravity4",
            "reviewer": "Claude",
            "status": "review_approved",
            "approved_head": "1111111122222222333333334444444455555555",
        }
        task_map = {"FREEZE-TEST-007": task_mutated}

        ai_status.clear_ai_status_caches()
        with unittest.mock.patch("ai_status.resolve_task_sha", return_value="9999999922222222333333334444444455555555"):
            prio = supervisor.dispatch_priority_for_task(config, task_mutated, "Antigravity4", task_map=task_map)
            self.assertIsNone(prio)

    def test_dispatch_priority_fails_closed_on_unresolved_head_or_unknown_ci(self) -> None:
        config = self._build_freeze_test_config()
        task = {
            "id": "FREEZE-TEST-007B",
            "owner": "Antigravity4",
            "reviewer": "Claude",
            "status": "review_approved",
            "approved_head": "1111111122222222333333334444444455555555",
        }
        task_map = {"FREEZE-TEST-007B": task}

        ai_status.clear_ai_status_caches()
        # Positive control: matching head + green CI MUST yield the finalize priority.
        # Without this, a config/schema regression would make every negative
        # sub-case below pass vacuously (the B13 failure mode).
        with unittest.mock.patch("ai_status.resolve_task_sha", return_value="1111111122222222333333334444444455555555"), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("MERGED", "success")):
            self.assertEqual(
                supervisor.dispatch_priority_for_task(config, task, "Antigravity4", task_map=task_map),
                1,
            )

        # Head sub-cases pin CI to a *passing* probe so the head gate is the only
        # thing that can produce None. Leaving task_pr_ci_status unpatched here
        # lets control fall through to the CI gate, where the real (unmocked)
        # probe shells out to `gh`, returns ("unknown"), and produces the None
        # the assertion is checking -- which is how B14/B17/B18 stayed vacuous.
        with unittest.mock.patch("ai_status.resolve_task_sha", return_value=None), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("MERGED", "success")):
            self.assertIsNone(supervisor.dispatch_priority_for_task(config, task, "Antigravity4", task_map=task_map))

        with unittest.mock.patch("ai_status.resolve_task_sha", side_effect=RuntimeError("git error")), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("MERGED", "success")):
            self.assertIsNone(supervisor.dispatch_priority_for_task(config, task, "Antigravity4", task_map=task_map))

        # Head drifted off the approved head, CI green: still must not dispatch.
        with unittest.mock.patch("ai_status.resolve_task_sha", return_value="9999999922222222333333334444444455555555"), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("MERGED", "success")):
            self.assertIsNone(supervisor.dispatch_priority_for_task(config, task, "Antigravity4", task_map=task_map))

        with unittest.mock.patch("ai_status.resolve_task_sha", return_value="1111111122222222333333334444444455555555"), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", "unknown")):
            self.assertIsNone(supervisor.dispatch_priority_for_task(config, task, "Antigravity4", task_map=task_map))

        with unittest.mock.patch("ai_status.resolve_task_sha", return_value="1111111122222222333333334444444455555555"), \
             unittest.mock.patch("ai_status.task_pr_ci_status", side_effect=RuntimeError("gh error")):
            self.assertIsNone(supervisor.dispatch_priority_for_task(config, task, "Antigravity4", task_map=task_map))

    def test_approve_fails_closed_when_approved_head_cannot_be_resolved(self) -> None:
        """B20: approving without freezing a head silently disables the freeze.

        command_done and both supervisor dispatch gates are guarded by
        `if approved_head:`. Recording no head therefore does not fail closed --
        it opts the task out of the integrity check entirely. Approval must
        abort instead, leaving the task in `review`.
        """
        def _fresh_state() -> dict[str, Any]:
            return {
                "tasks": [
                    {
                        "id": "FREEZE-TEST-020A",
                        "owner": "Antigravity4",
                        "reviewer": "Claude",
                        "status": "review",
                        "review_submission": {"remote_sha": "1111111122222222333333334444444455555555"},
                    }
                ]
            }

        # Positive control: a resolvable head still approves and freezes.
        state = _fresh_state()
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Claude"), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value="1111111122222222333333334444444455555555"), \
             unittest.mock.patch("ai_status.append_log"), \
             unittest.mock.patch("ai_status.sync_all"):
            ai_status.command_approve(state, ["FREEZE-TEST-020A", "Approve valid"])
        task = ai_status.get_task(state, "FREEZE-TEST-020A")
        self.assertEqual(task["status"], "review_approved")
        self.assertEqual(task["approved_head"], "1111111122222222333333334444444455555555")

        # Unresolvable head -> abort, and leave no half-applied approval behind.
        state = _fresh_state()
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Claude"), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value=None), \
             unittest.mock.patch("ai_status.sync_all"):
            with self.assertRaises(SystemExit) as cm:
                ai_status.command_approve(state, ["FREEZE-TEST-020A", "Approve valid"])
        self.assertIn("could not be resolved", str(cm.exception))
        task = ai_status.get_task(state, "FREEZE-TEST-020A")
        self.assertEqual(task["status"], "review")
        self.assertNotIn("approved_head", task)

        # A raising probe must fail closed too, not escape as a traceback.
        state = _fresh_state()
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Claude"), \
             unittest.mock.patch("ai_status.resolve_task_sha", side_effect=RuntimeError("gh down")), \
             unittest.mock.patch("ai_status.sync_all"):
            with self.assertRaises(SystemExit) as cm:
                ai_status.command_approve(state, ["FREEZE-TEST-020A", "Approve valid"])
        self.assertIn("Integrity gate failed closed", str(cm.exception))
        task = ai_status.get_task(state, "FREEZE-TEST-020A")
        self.assertEqual(task["status"], "review")
        self.assertNotIn("approved_head", task)

    def test_approve_refuses_to_overwrite_uncleared_approved_head(self) -> None:
        """B20: approved_head is immutable for the lifetime of one approval.

        Every transition back to `review` pops approved_head, so a task in
        `review` still carrying one is inconsistent state. Silently overwriting
        it would re-freeze on a head the reviewer never signed off.
        """
        old_head = "1111111122222222333333334444444455555555"
        new_head = "9999999922222222333333334444444455555555"
        state = {
            "tasks": [
                {
                    "id": "FREEZE-TEST-020B",
                    "owner": "Antigravity4",
                    "reviewer": "Claude",
                    "status": "review",
                    "approved_head": old_head,
                    "review_submission": {"remote_sha": new_head},
                }
            ]
        }
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Claude"), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value=new_head), \
             unittest.mock.patch("ai_status.sync_all"):
            with self.assertRaises(SystemExit) as cm:
                ai_status.command_approve(state, ["FREEZE-TEST-020B", "Approve drifted"])
        self.assertIn("uncleared approved head", str(cm.exception))
        task = ai_status.get_task(state, "FREEZE-TEST-020B")
        self.assertEqual(task["status"], "review")
        self.assertEqual(task["approved_head"], old_head)

        # Positive control: re-approving at the *same* head is not a conflict,
        # so the guard cannot be satisfied by rejecting every stale-head task.
        state["tasks"][0]["review_submission"]["remote_sha"] = old_head
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Claude"), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value=old_head), \
             unittest.mock.patch("ai_status.append_log"), \
             unittest.mock.patch("ai_status.sync_all"):
            ai_status.command_approve(state, ["FREEZE-TEST-020B", "Approve same head"])
        task = ai_status.get_task(state, "FREEZE-TEST-020B")
        self.assertEqual(task["status"], "review_approved")
        self.assertEqual(task["approved_head"], old_head)

    def _run_finalize_dispatch_capturing_signals(
        self,
        config: dict[str, Any],
        task: dict[str, Any],
        *,
        head: Any,
        ci: Any,
        pr_status: str = "MERGED",
    ) -> tuple[bool, list[dict[str, Any]], unittest.mock.MagicMock]:
        """Drive dispatch_ready_tasks once and collect the operator signals."""
        state = {
            "seen_event_keys": {},
            "ready_dispatcher": {"dispatch_cursor": 0},
            "status": {"tasks": [task]},
        }
        status = {"tasks": [task]}
        logged: list[dict[str, Any]] = []

        ai_status.clear_ai_status_caches()
        head_patch = (
            unittest.mock.patch("ai_status.resolve_task_sha", side_effect=head)
            if isinstance(head, Exception)
            else unittest.mock.patch("ai_status.resolve_task_sha", return_value=head)
        )
        with head_patch, \
             unittest.mock.patch("supervisor.scan_live_worker_pids_by_agent", return_value={}), \
             unittest.mock.patch("supervisor.outstanding_delivery_indexes", return_value=(set(), set(), set())), \
             unittest.mock.patch("supervisor.agent_dispatch_loads", return_value={}), \
             unittest.mock.patch("supervisor.load_status", return_value=status), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=(pr_status, ci)), \
             unittest.mock.patch("supervisor.reassert_approved_review_gate_if_due", return_value=False), \
             unittest.mock.patch("supervisor.agent_auto_dispatch_block_reason", return_value=None), \
             unittest.mock.patch("supervisor.sync_status_pipeline"), \
             unittest.mock.patch("supervisor.write_json"), \
             unittest.mock.patch("supervisor.write_activity_log", side_effect=lambda _c, e: logged.append(e)), \
             unittest.mock.patch("supervisor.queue_delivery_event", return_value=True) as mock_queue:
            dispatched = supervisor.dispatch_ready_tasks(
                config,
                state,
                agent_ids_override=["antigravity4"],
            )
        return dispatched, logged, mock_queue

    def test_green_open_pr_waits_for_merge_before_finalize_dispatch(self) -> None:
        approved = "1111111122222222333333334444444455555555"
        config = self._build_freeze_test_config()
        task = {
            "id": "FREEZE-TEST-WAIT-MERGE",
            "owner": "Antigravity4",
            "reviewer": "Claude",
            "status": "review_approved",
            "priority": "P1",
            "approved_head": approved,
        }

        dispatched, _, queue = self._run_finalize_dispatch_capturing_signals(
            config,
            task,
            head=approved,
            ci="success",
            pr_status="OPEN",
        )

        # No finalize worker: composing the base would rewrite the frozen head.
        queue.assert_not_called()
        self.assertIn("awaiting merge queue", task["next"])
        # The board was written, so the tick reports a change. It used to write
        # `next` and still report none, which left the caller unaware of it.
        self.assertTrue(dispatched)

        # Steady state: the same wait must not rewrite the board every tick.
        repeat_dispatched, _, repeat_queue = self._run_finalize_dispatch_capturing_signals(
            config,
            task,
            head=approved,
            ci="success",
            pr_status="OPEN",
        )
        repeat_queue.assert_not_called()
        self.assertFalse(repeat_dispatched)

    def test_supervisor_emits_operator_signal_for_silent_finalize_suppression(self) -> None:
        """B20: suppressing finalize dispatch must not be silent.

        The unresolved-head path and the catch-all unresolved-CI path both used
        a bare `continue`, unlike the pending/failure branches, so a task could
        sit in review_approved indefinitely with no `next` and no activity-log
        entry explaining why nothing was happening.
        """
        approved = "1111111122222222333333334444444455555555"
        config = self._build_freeze_test_config()

        def _task() -> dict[str, Any]:
            return {
                "id": "FREEZE-TEST-020C",
                "owner": "Antigravity4",
                "reviewer": "Claude",
                "status": "review_approved",
                "priority": "P1",
                "approved_head": approved,
            }

        # Positive control: head matches and CI is green -> dispatch, no signal.
        task = _task()
        dispatched, logged, mock_queue = self._run_finalize_dispatch_capturing_signals(
            config, task, head=approved, ci="success"
        )
        self.assertTrue(dispatched)
        mock_queue.assert_called_once()
        self.assertEqual([], [e["type"] for e in logged])

        # Head unresolvable: suppressed, task stays review_approved, signal emitted.
        task = _task()
        dispatched, logged, mock_queue = self._run_finalize_dispatch_capturing_signals(
            config, task, head=None, ci="success"
        )
        self.assertFalse(dispatched)
        mock_queue.assert_not_called()
        self.assertEqual(task["status"], "review_approved")
        self.assertIn("approved_head_unresolved", [e["type"] for e in logged])
        self.assertIn("Cannot verify branch HEAD", task["next"])

        # Head resolution raising is the same suppression path.
        task = _task()
        dispatched, logged, _ = self._run_finalize_dispatch_capturing_signals(
            config, task, head=RuntimeError("git down"), ci="success"
        )
        self.assertFalse(dispatched)
        self.assertIn("approved_head_unresolved", [e["type"] for e in logged])

        # CI probe inconclusive: suppressed with its own distinct signal.
        task = _task()
        dispatched, logged, mock_queue = self._run_finalize_dispatch_capturing_signals(
            config, task, head=approved, ci="unknown"
        )
        self.assertFalse(dispatched)
        mock_queue.assert_not_called()
        self.assertEqual(task["status"], "review_approved")
        self.assertIn("ci_status_unresolved", [e["type"] for e in logged])
        self.assertIn("is unresolved (unknown)", task["next"])

        # Signals are emitted once, not re-logged every supervisor cycle.
        dispatched, logged, _ = self._run_finalize_dispatch_capturing_signals(
            config, task, head=approved, ci="unknown"
        )
        self.assertFalse(dispatched)
        self.assertEqual([], [e["type"] for e in logged])

    def test_ci_pending_requeues_owner_after_timeout(self) -> None:
        """A long-pending CI result gets an automatic owner refresh run."""
        approved = "1111111122222222333333334444444455555555"
        config = self._build_freeze_test_config()

        def _task(**extra: Any) -> dict[str, Any]:
            task = {
                "id": "FREEZE-TEST-020D",
                "owner": "Antigravity4",
                "reviewer": "Claude",
                "status": "review_approved",
                "priority": "P1",
                "approved_head": approved,
            }
            task.update(extra)
            return task

        # First pending cycle: start the clock, do not escalate yet.
        task = _task()
        dispatched, logged, _ = self._run_finalize_dispatch_capturing_signals(
            config, task, head=approved, ci="pending"
        )
        self.assertFalse(dispatched)
        self.assertIn("ci_pending_since_ts", task)
        self.assertNotIn("ci_pending_timeout", [e["type"] for e in logged])

        # Still inside the 30-minute window: still no escalation.
        task = _task(ci_pending_since_ts=datetime.now(UTC).timestamp() - 60)
        _, logged, _ = self._run_finalize_dispatch_capturing_signals(
            config, task, head=approved, ci="pending"
        )
        self.assertNotIn("ci_pending_timeout", [e["type"] for e in logged])

        # Past 1800s: requeue the owner exactly once for a CI refresh.
        task = _task(ci_pending_since_ts=datetime.now(UTC).timestamp() - 2000)
        dispatched, logged, mock_queue = self._run_finalize_dispatch_capturing_signals(
            config, task, head=approved, ci="pending"
        )
        self.assertTrue(dispatched)
        mock_queue.assert_not_called()
        self.assertIn("ci_repair_requeued", [e["type"] for e in logged])
        self.assertEqual(task["status"], "in_progress")
        self.assertIn("owner requeued", task["next"])

        dispatched, logged, mock_queue = self._run_finalize_dispatch_capturing_signals(
            config, task, head=approved, ci="pending"
        )
        self.assertTrue(dispatched)
        mock_queue.assert_called_once()
        self.assertEqual([], [e["type"] for e in logged])

        # Recovery: a green probe clears the pending bookkeeping and dispatches.
        dispatched, _, mock_queue = self._run_finalize_dispatch_capturing_signals(
            config, task, head=approved, ci="success"
        )
        self.assertTrue(dispatched)
        mock_queue.assert_called_once()
        self.assertNotIn("ci_pending_since_ts", task)

    def test_pending_ci_reasserts_exact_approved_review_gate_at_bounded_rate(self) -> None:
        config = self._build_freeze_test_config()
        config["ready_dispatcher"]["review_gate_reassert_seconds"] = 300
        task = {
            "id": "FREEZE-TEST-GATE-REASSERT",
            "status": "review_approved",
            "approved_head": "1" * 40,
        }

        with unittest.mock.patch("ai_status.emit_task_review_status_check") as emit:
            self.assertTrue(
                supervisor.reassert_approved_review_gate_if_due(config, task, now_ts=1_000)
            )
            self.assertFalse(
                supervisor.reassert_approved_review_gate_if_due(config, task, now_ts=1_299)
            )
            self.assertTrue(
                supervisor.reassert_approved_review_gate_if_due(config, task, now_ts=1_300)
            )

        self.assertEqual(emit.call_count, 2)
        emit.assert_called_with(task, "review_approved")
        self.assertEqual(task["review_gate_reasserted_at_ts"], 1_300)

    def test_explicit_re_review_command(self) -> None:
        state = {
            "tasks": [
                {
                    "id": "FREEZE-TEST-008",
                    "owner": "Antigravity4",
                    "reviewer": "Claude",
                    "status": "review_approved",
                    "approved_head": "1111111122222222333333334444444455555555",
                }
            ]
        }
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"), \
             unittest.mock.patch("ai_status.load_state", return_value=state), \
             unittest.mock.patch("ai_status.save_state"), \
             unittest.mock.patch("ai_status.sync_all"), \
             unittest.mock.patch("ai_status.emit_status_checks_for_changed_tasks"):
            ai_status.main(["ai_status.py", "re_review", "FREEZE-TEST-008", "Updated branch with strict merge"])
            task = ai_status.get_task(state, "FREEZE-TEST-008")
            self.assertEqual(task["status"], "review")
            self.assertNotIn("approved_head", task)
            self.assertEqual(task["next"], "Updated branch with strict merge")

            task["status"] = "review_approved"
            task["approved_head"] = "1111111122222222333333334444444455555555"
            ai_status.main(["ai_status.py", "re-review", "FREEZE-TEST-008", "Alias re-review check"])
            self.assertEqual(task["status"], "review")
            self.assertNotIn("approved_head", task)

    def test_higher_priority_ready_task_exists_refuses_undispatchable_finalize_task(self) -> None:
        """B24: higher_priority_ready_task_exists must fail closed on undispatchable finalize tasks.

        A review_approved task with missing approved_head, head mismatch, or pending CI
        cannot be dispatched by dispatch_ready_tasks. It MUST NOT cause
        higher_priority_ready_task_exists to return True and terminate running workers.
        """
        approved_head = "1111111122222222333333334444444455555555"
        config = self._build_freeze_test_config()
        config["ready_dispatcher"]["max_tasks_per_agent_by_agent"]["Antigravity4"] = 1
        config["ready_dispatcher"]["max_concurrent_per_quota_group"]["antigravity4"] = 1

        worker = {
            "run_id": "run-001",
            "task_id": "INPROG-001",
            "agent_id": "antigravity4",
            "status": "running",
            "request_snapshot": {"reason": supervisor.REASON_OWNED_IN_PROGRESS},
        }

        task_map = {
            "INPROG-001": {
                "id": "INPROG-001",
                "owner": "Antigravity4",
                "reviewer": "Claude",
                "status": "in_progress",
            },
            "FINAL-001": {
                "id": "FINAL-001",
                "owner": "Antigravity4",
                "reviewer": "Claude",
                "status": "review_approved",
            },
        }

        # No approved_head -> must return False (does not preempt)
        self.assertFalse(supervisor.higher_priority_ready_task_exists(config, worker, task_map))

        # Head mismatch -> must return False
        task_map["FINAL-001"]["approved_head"] = approved_head
        with unittest.mock.patch("ai_status.resolve_task_sha", return_value="9999999922222222333333334444444455555555"), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("MERGED", "success")):
            self.assertFalse(supervisor.higher_priority_ready_task_exists(config, worker, task_map))

        # CI pending -> must return False
        with unittest.mock.patch("ai_status.resolve_task_sha", return_value=approved_head), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("MERGED", "pending")):
            self.assertFalse(supervisor.higher_priority_ready_task_exists(config, worker, task_map))

        # Positive control: matching head + green CI -> returns True (preempts)
        with unittest.mock.patch("ai_status.resolve_task_sha", return_value=approved_head), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("MERGED", "success")), \
             unittest.mock.patch("supervisor.agent_dispatch_capacity", return_value=1):
            self.assertTrue(supervisor.higher_priority_ready_task_exists(config, worker, task_map))

    def test_supervisor_suppresses_finalize_dispatch_on_pending_ci(self) -> None:
        config = self._build_freeze_test_config()
        task_worktree_status_path = ROOT_DIR / "ai-status.json"
        task_worktree_status_before = (
            task_worktree_status_path.read_bytes()
            if task_worktree_status_path.exists()
            else None
        )
        task = {
            "id": "FREEZE-TEST-005",
            "owner": "Antigravity4",
            "reviewer": "Claude",
            "status": "review_approved",
            "approved_head": "1111111122222222333333334444444455555555",
        }
        state = {
            "seen_event_keys": {},
            "ready_dispatcher": {"dispatch_cursor": 0},
            "status": {"tasks": [task]},
        }
        status = {"tasks": [task]}

        ai_status.clear_ai_status_caches()
        # Positive Control: ci_status = "success" MUST dispatch
        with unittest.mock.patch("supervisor.scan_live_worker_pids_by_agent", return_value={}), \
             unittest.mock.patch("supervisor.outstanding_delivery_indexes", return_value=(set(), set(), set())), \
             unittest.mock.patch("supervisor.agent_dispatch_loads", return_value={}), \
             unittest.mock.patch("supervisor.repair_open_task_metadata", return_value=False), \
             unittest.mock.patch("supervisor.repair_unsubmitted_review_tasks", return_value=False), \
             unittest.mock.patch("supervisor.load_status", return_value=status), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value="1111111122222222333333334444444455555555"), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("MERGED", "success")), \
             unittest.mock.patch("supervisor.agent_auto_dispatch_block_reason", return_value=None), \
             unittest.mock.patch("supervisor.queue_delivery_event", return_value=True) as mock_queue:
            dispatched = supervisor.dispatch_ready_tasks(
                config,
                state,
                agent_ids_override=["antigravity4"],
            )
            self.assertTrue(dispatched)
            mock_queue.assert_called_once()

        # Test suppression: ci_status = "pending" MUST NOT dispatch
        with unittest.mock.patch("supervisor.scan_live_worker_pids_by_agent", return_value={}), \
             unittest.mock.patch("supervisor.outstanding_delivery_indexes", return_value=(set(), set(), set())), \
             unittest.mock.patch("supervisor.agent_dispatch_loads", return_value={}), \
             unittest.mock.patch("supervisor.repair_open_task_metadata", return_value=False), \
             unittest.mock.patch("supervisor.repair_unsubmitted_review_tasks", return_value=False), \
             unittest.mock.patch("supervisor.load_status", return_value=status), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value="1111111122222222333333334444444455555555"), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", "pending")), \
             unittest.mock.patch("supervisor.agent_auto_dispatch_block_reason", return_value=None), \
             unittest.mock.patch("supervisor.queue_delivery_event", return_value=True) as mock_queue:
            dispatched = supervisor.dispatch_ready_tasks(
                config,
                state,
                agent_ids_override=["antigravity4"],
            )
            self.assertFalse(dispatched)
            mock_queue.assert_not_called()
            self.assertEqual(task["status"], "review_approved")
        task_worktree_status_after = (
            task_worktree_status_path.read_bytes()
            if task_worktree_status_path.exists()
            else None
        )
        self.assertEqual(task_worktree_status_before, task_worktree_status_after)

    def test_supervisor_suppresses_finalize_dispatch_on_unresolved_head_or_unknown_ci(self) -> None:
        config = self._build_freeze_test_config()
        task = {
            "id": "FREEZE-TEST-005B",
            "owner": "Antigravity4",
            "reviewer": "Claude",
            "status": "review_approved",
            "approved_head": "1111111122222222333333334444444455555555",
        }
        state = {
            "seen_event_keys": {},
            "ready_dispatcher": {"dispatch_cursor": 0},
            "status": {"tasks": [task]},
        }
        status = {"tasks": [task]}
        APPROVED = "1111111122222222333333334444444455555555"

        ai_status.clear_ai_status_caches()

        # Sub-case 1: resolve_task_sha returns None — head cannot be resolved, must suppress.
        # Positive control: matching head + ci=success MUST dispatch.
        with unittest.mock.patch("supervisor.scan_live_worker_pids_by_agent", return_value={}), \
             unittest.mock.patch("supervisor.outstanding_delivery_indexes", return_value=(set(), set(), set())), \
             unittest.mock.patch("supervisor.agent_dispatch_loads", return_value={}), \
             unittest.mock.patch("supervisor.repair_open_task_metadata", return_value=False), \
             unittest.mock.patch("supervisor.repair_unsubmitted_review_tasks", return_value=False), \
             unittest.mock.patch("supervisor.load_status", return_value=status), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value=APPROVED), \
             unittest.mock.patch("supervisor.runtime_ai_status.resolve_task_checkout_sha", return_value=APPROVED), \
             unittest.mock.patch("ai_status.is_approved_head_satisfied", return_value=True), \
             unittest.mock.patch("supervisor.runtime_ai_status.is_approved_head_satisfied", return_value=True), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("MERGED", "success")), \
             unittest.mock.patch("supervisor.runtime_ai_status.task_pr_ci_status", return_value=("MERGED", "success")), \
             unittest.mock.patch("supervisor.agent_auto_dispatch_block_reason", return_value=None), \
             unittest.mock.patch("supervisor.agent_dispatch_capacity", return_value=10), \
             unittest.mock.patch("supervisor.agent_can_take_task", return_value=True), \
             unittest.mock.patch("supervisor.worktree_block_still_matches_dispatch", return_value=False), \
             unittest.mock.patch("supervisor.queue_delivery_event", return_value=True) as mock_queue:
            dispatched = supervisor.dispatch_ready_tasks(
                config,
                state,
                agent_ids_override=["antigravity4"],
            )
            self.assertTrue(dispatched)
            mock_queue.assert_called_once()

        # Negative: resolve_task_sha returns None — must NOT dispatch.
        state["seen_event_keys"] = {}
        task["status"] = "review_approved"
        with unittest.mock.patch("supervisor.scan_live_worker_pids_by_agent", return_value={}), \
             unittest.mock.patch("supervisor.outstanding_delivery_indexes", return_value=(set(), set(), set())), \
             unittest.mock.patch("supervisor.agent_dispatch_loads", return_value={}), \
             unittest.mock.patch("supervisor.repair_open_task_metadata", return_value=False), \
             unittest.mock.patch("supervisor.repair_unsubmitted_review_tasks", return_value=False), \
             unittest.mock.patch("supervisor.load_status", return_value=status), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value=None), \
             unittest.mock.patch("supervisor.runtime_ai_status.resolve_task_checkout_sha", return_value=None), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("MERGED", "success")), \
             unittest.mock.patch("supervisor.runtime_ai_status.task_pr_ci_status", return_value=("MERGED", "success")), \
             unittest.mock.patch("supervisor.agent_auto_dispatch_block_reason", return_value=None), \
             unittest.mock.patch("supervisor.write_activity_log"), \
             unittest.mock.patch("supervisor.write_json"), \
             unittest.mock.patch("supervisor.commit_canonical_task_transition", return_value=True), \
             unittest.mock.patch("supervisor.sync_status_pipeline", return_value=True), \
             unittest.mock.patch("supervisor.queue_delivery_event", return_value=True) as mock_queue:
            dispatched = supervisor.dispatch_ready_tasks(
                config,
                state,
                agent_ids_override=["antigravity4"],
            )
            self.assertFalse(dispatched)
            mock_queue.assert_not_called()
            self.assertEqual(task["status"], "review_approved")

        # Sub-case 2: ci_status = "unknown" — must suppress.
        # Positive control: matching head + ci=success MUST dispatch.
        state["seen_event_keys"] = {}
        task["status"] = "review_approved"
        with unittest.mock.patch("supervisor.scan_live_worker_pids_by_agent", return_value={}), \
             unittest.mock.patch("supervisor.outstanding_delivery_indexes", return_value=(set(), set(), set())), \
             unittest.mock.patch("supervisor.agent_dispatch_loads", return_value={}), \
             unittest.mock.patch("supervisor.repair_open_task_metadata", return_value=False), \
             unittest.mock.patch("supervisor.repair_unsubmitted_review_tasks", return_value=False), \
             unittest.mock.patch("supervisor.load_status", return_value=status), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value=APPROVED), \
             unittest.mock.patch("supervisor.runtime_ai_status.resolve_task_checkout_sha", return_value=APPROVED), \
             unittest.mock.patch("ai_status.is_approved_head_satisfied", return_value=True), \
             unittest.mock.patch("supervisor.runtime_ai_status.is_approved_head_satisfied", return_value=True), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("MERGED", "success")), \
             unittest.mock.patch("supervisor.runtime_ai_status.task_pr_ci_status", return_value=("MERGED", "success")), \
             unittest.mock.patch("supervisor.agent_auto_dispatch_block_reason", return_value=None), \
             unittest.mock.patch("supervisor.agent_dispatch_capacity", return_value=10), \
             unittest.mock.patch("supervisor.agent_can_take_task", return_value=True), \
             unittest.mock.patch("supervisor.worktree_block_still_matches_dispatch", return_value=False), \
             unittest.mock.patch("supervisor.queue_delivery_event", return_value=True) as mock_queue:
            dispatched = supervisor.dispatch_ready_tasks(
                config,
                state,
                agent_ids_override=["antigravity4"],
            )
            self.assertTrue(dispatched)
            mock_queue.assert_called_once()

        # Negative: ci_status = "unknown" — must NOT dispatch.
        state["seen_event_keys"] = {}
        task["status"] = "review_approved"
        with unittest.mock.patch("supervisor.scan_live_worker_pids_by_agent", return_value={}), \
             unittest.mock.patch("supervisor.outstanding_delivery_indexes", return_value=(set(), set(), set())), \
             unittest.mock.patch("supervisor.agent_dispatch_loads", return_value={}), \
             unittest.mock.patch("supervisor.repair_open_task_metadata", return_value=False), \
             unittest.mock.patch("supervisor.repair_unsubmitted_review_tasks", return_value=False), \
             unittest.mock.patch("supervisor.load_status", return_value=status), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value=APPROVED), \
             unittest.mock.patch("supervisor.runtime_ai_status.resolve_task_checkout_sha", return_value=APPROVED), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", "unknown")), \
             unittest.mock.patch("supervisor.runtime_ai_status.task_pr_ci_status", return_value=("OPEN", "unknown")), \
             unittest.mock.patch("supervisor.agent_auto_dispatch_block_reason", return_value=None), \
             unittest.mock.patch("supervisor.write_activity_log"), \
             unittest.mock.patch("supervisor.write_json"), \
             unittest.mock.patch("supervisor.commit_canonical_task_transition", return_value=True), \
             unittest.mock.patch("supervisor.sync_status_pipeline", return_value=True), \
             unittest.mock.patch("supervisor.queue_delivery_event", return_value=True) as mock_queue:
            dispatched = supervisor.dispatch_ready_tasks(
                config,
                state,
                agent_ids_override=["antigravity4"],
            )
            self.assertFalse(dispatched)
            mock_queue.assert_not_called()
            self.assertEqual(task["status"], "review_approved")

        # Sub-case 3: task_pr_ci_status raises — must suppress (fail closed on error).
        # Positive control: matching head + ci=success MUST dispatch.
        state["seen_event_keys"] = {}
        task["status"] = "review_approved"
        with unittest.mock.patch("supervisor.scan_live_worker_pids_by_agent", return_value={}), \
             unittest.mock.patch("supervisor.outstanding_delivery_indexes", return_value=(set(), set(), set())), \
             unittest.mock.patch("supervisor.agent_dispatch_loads", return_value={}), \
             unittest.mock.patch("supervisor.repair_open_task_metadata", return_value=False), \
             unittest.mock.patch("supervisor.repair_unsubmitted_review_tasks", return_value=False), \
             unittest.mock.patch("supervisor.load_status", return_value=status), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value=APPROVED), \
             unittest.mock.patch("supervisor.runtime_ai_status.resolve_task_checkout_sha", return_value=APPROVED), \
             unittest.mock.patch("supervisor.runtime_ai_status.is_approved_head_satisfied", return_value=True), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("MERGED", "success")), \
             unittest.mock.patch("supervisor.runtime_ai_status.task_pr_ci_status", return_value=("MERGED", "success")), \
             unittest.mock.patch("supervisor.agent_auto_dispatch_block_reason", return_value=None), \
             unittest.mock.patch("supervisor.agent_dispatch_capacity", return_value=10), \
             unittest.mock.patch("supervisor.agent_can_take_task", return_value=True), \
             unittest.mock.patch("supervisor.worktree_block_still_matches_dispatch", return_value=False), \
             unittest.mock.patch("supervisor.queue_delivery_event", return_value=True) as mock_queue:
            dispatched = supervisor.dispatch_ready_tasks(
                config,
                state,
                agent_ids_override=["antigravity4"],
            )
            self.assertTrue(dispatched)
            mock_queue.assert_called_once()

        # Negative: task_pr_ci_status raises RuntimeError — must NOT dispatch.
        state["seen_event_keys"] = {}
        task["status"] = "review_approved"
        with unittest.mock.patch("supervisor.scan_live_worker_pids_by_agent", return_value={}), \
             unittest.mock.patch("supervisor.outstanding_delivery_indexes", return_value=(set(), set(), set())), \
             unittest.mock.patch("supervisor.agent_dispatch_loads", return_value={}), \
             unittest.mock.patch("supervisor.repair_open_task_metadata", return_value=False), \
             unittest.mock.patch("supervisor.repair_unsubmitted_review_tasks", return_value=False), \
             unittest.mock.patch("supervisor.load_status", return_value=status), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value=APPROVED), \
             unittest.mock.patch("supervisor.runtime_ai_status.resolve_task_checkout_sha", return_value=APPROVED), \
             unittest.mock.patch("ai_status.task_pr_ci_status", side_effect=RuntimeError("gh error")), \
             unittest.mock.patch("supervisor.runtime_ai_status.task_pr_ci_status", side_effect=RuntimeError("gh error")), \
             unittest.mock.patch("supervisor.agent_auto_dispatch_block_reason", return_value=None), \
             unittest.mock.patch("supervisor.write_activity_log"), \
             unittest.mock.patch("supervisor.write_json"), \
             unittest.mock.patch("supervisor.commit_canonical_task_transition", return_value=True), \
             unittest.mock.patch("supervisor.sync_status_pipeline", return_value=True), \
             unittest.mock.patch("supervisor.queue_delivery_event", return_value=True) as mock_queue:
            dispatched = supervisor.dispatch_ready_tasks(
                config,
                state,
                agent_ids_override=["antigravity4"],
            )
            self.assertFalse(dispatched)
            mock_queue.assert_not_called()
            self.assertEqual(task["status"], "review_approved")

    def test_supervisor_finalize_dispatch_forces_fresh_sha_resolution(self) -> None:
        """Verify supervisor evaluate_dispatch and finalize dispatch gate pass force_refresh=True to resolve_task_sha."""
        config = self._build_freeze_test_config()
        task = {
            "id": "FREEZE-TEST-FRESH-001",
            "owner": "Antigravity4",
            "reviewer": "Claude",
            "status": "review_approved",
            "approved_head": "1111111122222222333333334444444455555555",
        }
        with unittest.mock.patch("ai_status.resolve_task_sha", return_value="1111111122222222333333334444444455555555") as mock_resolve, \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("MERGED", "success")):
            prio = supervisor.dispatch_priority_for_task(config, task, "Antigravity4")
            self.assertIsNotNone(prio)
            mock_resolve.assert_called_with("FREEZE-TEST-FRESH-001", force_refresh=True)

    def test_task_review_gate_status_check_pending_on_head_mismatch(self) -> None:
        task = {
            "id": "FREEZE-TEST-006",
            "reviewer": "Claude",
            "approved_head": "1111111122222222333333334444444455555555",
        }
        ai_status.clear_ai_status_caches()
        with unittest.mock.patch("ai_status.resolve_task_sha", return_value="7777777722222222333333334444444455555555"), \
             unittest.mock.patch("ai_status.task_repository_slug_safe", return_value="alfloop-dev/odayplus"), \
             unittest.mock.patch("ai_status.get_repository_slug_safe", return_value="alfloop-dev/odayplus"):
                with unittest.mock.patch("subprocess.run") as mock_run:
                    mock_run.return_value.returncode = 0
                    ai_status.emit_task_review_status_check(task, "review_approved")
                    mock_run.assert_called_once()
                    cmd = mock_run.call_args[0][0]
                    self.assertIn("state=pending", cmd)
                    self.assertIn("re-review required", "".join(cmd))

    # ------------------------------------------------------------------
    # B22 -- `review_approved` with the approved_head key *absent*.
    #
    # Round 8 rejected the freeze because every consumer was written as
    # `if approved_head:`, so the missing-key shape opted a task out of the
    # control entirely. Mutation testing could not find it: a mutant only dies
    # inside a path some test reaches, and all 32 approved_head references in
    # the suite *set* the key. These tests construct the shape directly.
    # ------------------------------------------------------------------

    MISSING_HEAD_APPROVED = "3333333322222222333333334444444455555555"

    def _missing_head_task(self, task_id: str) -> dict[str, Any]:
        """The live ODP-DEPLOY-STAGING-JOB-RECEIPT-UPLOAD-001 shape, verbatim:
        review_approved, owned, and simply carrying no `approved_head` key."""
        return {
            "id": task_id,
            "owner": "Antigravity4",
            "reviewer": "Claude",
            "status": "review_approved",
            "priority": "P1",
        }

    def test_dispatch_priority_fails_closed_when_approved_head_is_absent(self) -> None:
        config = self._build_freeze_test_config()
        task = self._missing_head_task("FREEZE-TEST-022A")
        task_map = {task["id"]: task}
        ai_status.clear_ai_status_caches()

        # The gate must not consult the head/CI probes at all -- there is
        # nothing to compare against -- so both are pinned green. If the
        # suppression came from the CI probe instead, this would pass vacuously.
        with unittest.mock.patch("ai_status.resolve_task_sha", return_value=self.MISSING_HEAD_APPROVED), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("MERGED", "success")):
            self.assertIsNone(
                supervisor.dispatch_priority_for_task(config, task, "Antigravity4", task_map=task_map)
            )

            # Control: the identical task with the key present and matching is
            # dispatched, so the assertion above is the gate firing, not a
            # broken fixture.
            control = dict(task, approved_head=self.MISSING_HEAD_APPROVED)
            self.assertEqual(
                supervisor.dispatch_priority_for_task(
                    config, control, "Antigravity4", task_map={control["id"]: control}
                ),
                1,
            )

    def test_dispatch_ready_suppresses_and_signals_when_approved_head_is_absent(self) -> None:
        config = self._build_freeze_test_config()
        task = self._missing_head_task("FREEZE-TEST-022B")

        dispatched, logged, mock_queue = self._run_finalize_dispatch_capturing_signals(
            config, task, head=self.MISSING_HEAD_APPROVED, ci="success"
        )
        self.assertFalse(dispatched)
        mock_queue.assert_not_called()
        self.assertEqual(task["status"], "review_approved")
        self.assertIn("approved_head_missing", [e["type"] for e in logged])
        self.assertIn("no reviewer-approved head", task["next"])
        self.assertIn("restore_approved_head", task["next"])

        # Emitted once, not every supervisor cycle.
        dispatched, logged, _ = self._run_finalize_dispatch_capturing_signals(
            config, task, head=self.MISSING_HEAD_APPROVED, ci="success"
        )
        self.assertFalse(dispatched)
        self.assertEqual([], [e["type"] for e in logged])

        # Control: the same task with the head recorded does dispatch.
        control = self._missing_head_task("FREEZE-TEST-022B")
        control["approved_head"] = self.MISSING_HEAD_APPROVED
        dispatched, _, mock_queue = self._run_finalize_dispatch_capturing_signals(
            config, control, head=self.MISSING_HEAD_APPROVED, ci="success"
        )
        self.assertTrue(dispatched)
        mock_queue.assert_called_once()

    def test_command_done_rejects_absent_head_before_delivery_metadata(self) -> None:
        """The freeze gate must reject, not merely be followed by gates that do.

        Round 8's reproduction reached the commit-convention and require_merged_pr
        messages, which proved the freeze had never fired. Those gates check
        merge hygiene, not "is this the commit a reviewer read", so this asserts
        collect_done_delivery_metadata is never entered.
        """
        state = {"tasks": [self._missing_head_task("FREEZE-TEST-022C")]}
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value=self.MISSING_HEAD_APPROVED), \
             unittest.mock.patch("ai_status.collect_done_delivery_metadata") as collect, \
             unittest.mock.patch("ai_status.append_log"):
            with self.assertRaises(SystemExit) as cm:
                ai_status.command_done(state, ["FREEZE-TEST-022C", "Finalize"])
        self.assertIn("no reviewer-approved head", str(cm.exception))
        collect.assert_not_called()
        self.assertEqual(ai_status.get_task(state, "FREEZE-TEST-022C")["status"], "review_approved")

    def test_task_review_gate_pending_when_approved_head_is_absent(self) -> None:
        task = {"id": "FREEZE-TEST-022D", "reviewer": "Claude"}
        ai_status.clear_ai_status_caches()
        with unittest.mock.patch("ai_status.resolve_task_sha", return_value=self.MISSING_HEAD_APPROVED), \
             unittest.mock.patch("ai_status.get_repository_slug_safe", return_value="alfloop-dev/odayplus"), \
             unittest.mock.patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            ai_status.emit_task_review_status_check(task, "review_approved")
            cmd = mock_run.call_args[0][0]
            self.assertIn("state=pending", cmd)
            self.assertIn("no reviewer-approved head is recorded", "".join(cmd))

    def test_staging_shape_closes_only_after_explicit_head_restoration(self) -> None:
        """End-to-end on the exact live shape: head e0147acb, merge 4b329493.

        Also pins B22-3: the freeze anchors on the immutable PR head, and the
        merge commit GitHub created is recorded as separate delivery evidence
        rather than being accepted as the reviewed commit.
        """
        head = "e0147acb22222222333333334444444455555555"
        merge = "4b32949322222222333333334444444455555555"
        task_id = "ODP-DEPLOY-STAGING-JOB-RECEIPT-UPLOAD-001"
        state = {"tasks": [self._missing_head_task(task_id)]}
        task = ai_status.get_task(state, task_id)
        config = self._build_freeze_test_config()
        ai_status.clear_ai_status_caches()

        resolve = unittest.mock.patch("ai_status.resolve_task_sha", return_value=head)
        pr_info = unittest.mock.patch(
            "ai_status.task_pr_head_and_merge_commit", return_value=(head, merge)
        )

        # 1. Nothing dispatches and nothing finalizes while the head is missing.
        with resolve, pr_info, unittest.mock.patch("ai_status.append_log"):
            self.assertIsNone(
                supervisor.dispatch_priority_for_task(config, task, "Antigravity4", task_map={task_id: task})
            )
            with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"), \
                 unittest.mock.patch("ai_status.collect_done_delivery_metadata") as collect:
                with self.assertRaises(SystemExit):
                    ai_status.command_done(state, [task_id, "Finalize"])
                collect.assert_not_called()

            # 2. The owner cannot attest their own head.
            with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"):
                with self.assertRaises(SystemExit) as cm:
                    ai_status.command_restore_approved_head(state, [task_id, head, "self-attest"])
                self.assertIn("Only the reviewer", str(cm.exception))
            self.assertNotIn("approved_head", task)

            # 3. The reviewer cannot attest the merge commit, an abbreviation,
            #    or any sha other than the immutable PR head.
            with unittest.mock.patch("ai_status.current_actor_validated", return_value="Claude"):
                with self.assertRaises(SystemExit) as cm:
                    ai_status.command_restore_approved_head(state, [task_id, merge, "merge commit"])
                self.assertIn("merge commit", str(cm.exception))

                with self.assertRaises(SystemExit) as cm:
                    ai_status.command_restore_approved_head(state, [task_id, head[:8], "abbrev"])
                self.assertIn("40-character commit sha", str(cm.exception))

                with self.assertRaises(SystemExit) as cm:
                    ai_status.command_restore_approved_head(
                        state, [task_id, "9999999922222222333333334444444455555555", "other head"]
                    )
                self.assertIn("does not match the task branch head", str(cm.exception))
                self.assertNotIn("approved_head", task)

                # 4. Attesting the exact reviewed head restores the freeze.
                ai_status.command_restore_approved_head(state, [task_id, head, "attest reviewed head"])
            self.assertEqual(task["approved_head"], head)

            # 5. Only now does the task dispatch and finalize, and the merge
            #    commit is recorded as a distinct fact from the approved head.
            with unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("MERGED", "success")):
                self.assertEqual(
                    supervisor.dispatch_priority_for_task(config, task, "Antigravity4", task_map={task_id: task}),
                    1,
                )
            with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"), \
                 unittest.mock.patch(
                     "ai_status.collect_done_delivery_metadata",
                     return_value={
                         "verified_head": head,
                         "pull_request": {"head_sha": head, "merge_commit": merge},
                     },
                 ), \
                 unittest.mock.patch("ai_status.archive_terminal_task_from_state"):
                ai_status.command_done(state, [task_id, "Finalize"])

        self.assertEqual(task["status"], "done")
        self.assertEqual(task["delivery"]["approved_head"], head)
        self.assertEqual(task["delivery"]["verified_head"], head)
        self.assertEqual(task["delivery"]["pr_merge_commit"], merge)
        self.assertNotEqual(task["delivery"]["pr_merge_commit"], task["delivery"]["approved_head"])

    def test_done_refuses_when_resolved_head_is_the_merge_commit(self) -> None:
        """If a task branch resolves to the merge commit, the frozen head must
        not be satisfiable by it -- the merge commit was never reviewed."""
        merge = "4b32949322222222333333334444444455555555"
        state = {
            "tasks": [
                {
                    "id": "FREEZE-TEST-022E",
                    "owner": "Antigravity4",
                    "reviewer": "Claude",
                    "status": "review_approved",
                    "approved_head": merge,
                }
            ]
        }
        ai_status.clear_ai_status_caches()
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"), \
             unittest.mock.patch(
                 "ai_status.collect_done_delivery_metadata",
                 return_value={
                     "verified_head": merge,
                     "pull_request": {
                         "head_sha": "e0147acb22222222333333334444444455555555",
                         "merge_commit": merge,
                     },
                 },
             ) as collect, \
             unittest.mock.patch("ai_status.append_log"):
            with self.assertRaises(SystemExit) as cm:
                ai_status.command_done(state, ["FREEZE-TEST-022E", "Finalize"])
        self.assertIn("differs from reviewer-approved head", str(cm.exception))
        collect.assert_called_once()

    # ------------------------------------------------------------------
    # B21 -- `restore_approved` is the second producer of `review_approved`.
    # ------------------------------------------------------------------

    def _restore_state(self, **task_extra: Any) -> dict[str, Any]:
        task = {
            "id": "FREEZE-TEST-021",
            "owner": "Antigravity4",
            "reviewer": "Claude",
            "status": "in_progress",
            "review_notes_zh": "已審核通過",
        }
        task.update(task_extra)
        return {"tasks": [task]}

    def test_restore_approved_refuses_without_a_durable_approved_head(self) -> None:
        """The round-8 reproduction: approve -> reopen -> restore -> done at an
        unreviewed head. `reopen` pops approved_head but keeps review_notes_zh,
        which is exactly restore_approved's precondition."""
        approved = "1111111122222222333333334444444455555555"
        state = {
            "tasks": [
                {
                    "id": "FREEZE-TEST-021A",
                    "owner": "Antigravity4",
                    "reviewer": "Claude",
                    "status": "review",
                    "review_notes_zh": "已審核通過",
                    "review_submission": {"remote_sha": approved},
                }
            ]
        }
        task = ai_status.get_task(state, "FREEZE-TEST-021A")
        ai_status.clear_ai_status_caches()
        with unittest.mock.patch("ai_status.append_log"), unittest.mock.patch("ai_status.sync_all"):
            with unittest.mock.patch("ai_status.current_actor_validated", return_value="Claude"), \
                 unittest.mock.patch("ai_status.resolve_task_sha", return_value=approved):
                ai_status.command_approve(state, ["FREEZE-TEST-021A", "Approved"])
            self.assertEqual(task["last_approved_head"], approved)

            with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"):
                ai_status.command_reopen(state, ["FREEZE-TEST-021A", "More work"])
                self.assertNotIn("approved_head", task)
                # B21's precondition: the durable record survives the reopen,
                # so restore can re-freeze it rather than producing no freeze.
                self.assertEqual(task["last_approved_head"], approved)

                # Owner pushes an unreviewed commit, then tries to restore.
                drifted = "bbbbbbbb22222222333333334444444455555555"
                with unittest.mock.patch("ai_status.resolve_task_sha", return_value=drifted):
                    with self.assertRaises(SystemExit) as cm:
                        ai_status.command_restore_approved(state, ["FREEZE-TEST-021A", "Restore"])
                self.assertIn("moved to bbbbbbbb", str(cm.exception))
                self.assertEqual(task["status"], "in_progress")

            # A task that never had a durable head cannot be restored at all.
            legacy = self._restore_state(id="FREEZE-TEST-021B")
            with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"), \
                 unittest.mock.patch("ai_status.resolve_task_sha", return_value=approved):
                with self.assertRaises(SystemExit) as cm:
                    ai_status.command_restore_approved(legacy, ["FREEZE-TEST-021B", "Restore"])
            self.assertIn("no durable reviewer-approved head", str(cm.exception))
            self.assertEqual(ai_status.get_task(legacy, "FREEZE-TEST-021B")["status"], "in_progress")

    def test_restore_approved_refreezes_the_reviewed_head_when_branch_unmoved(self) -> None:
        """Positive control for the two refusals above: a genuinely spurious
        downgrade still recovers, and it recovers *with* the freeze intact."""
        approved = "1111111122222222333333334444444455555555"
        state = self._restore_state(id="FREEZE-TEST-021C", last_approved_head=approved)
        task = ai_status.get_task(state, "FREEZE-TEST-021C")
        ai_status.clear_ai_status_caches()
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value=approved), \
             unittest.mock.patch("ai_status.append_log"):
            ai_status.command_restore_approved(state, ["FREEZE-TEST-021C", "Spurious downgrade"])
        self.assertEqual(task["status"], "review_approved")
        self.assertEqual(task["approved_head"], approved)

        # And the restored task is frozen: a later push is still refused.
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"), \
             unittest.mock.patch(
                 "ai_status.collect_done_delivery_metadata",
                 return_value={
                     "verified_head": "bbbbbbbb22222222333333334444444455555555",
                     "pull_request": {
                         "head_sha": approved,
                         "merge_commit": "aaaaaaaa22222222333333334444444455555555",
                     },
                 },
             ) as collect, \
             unittest.mock.patch("ai_status.append_log"):
            with self.assertRaises(SystemExit) as cm:
                ai_status.command_done(state, ["FREEZE-TEST-021C", "Finalize"])
        self.assertIn("differs from reviewer-approved head", str(cm.exception))
        collect.assert_called_once()

    def test_restore_approved_fails_closed_when_head_unresolvable(self) -> None:
        approved = "1111111122222222333333334444444455555555"
        for head, expected in (
            (None, "branch HEAD could not be resolved"),
            (RuntimeError("git down"), "unable to resolve the current branch HEAD"),
        ):
            with self.subTest(head=str(head)):
                state = self._restore_state(id="FREEZE-TEST-021D", last_approved_head=approved)
                ai_status.clear_ai_status_caches()
                patch = (
                    unittest.mock.patch("ai_status.resolve_task_sha", side_effect=head)
                    if isinstance(head, Exception)
                    else unittest.mock.patch("ai_status.resolve_task_sha", return_value=head)
                )
                with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"), \
                     patch, unittest.mock.patch("ai_status.append_log"):
                    with self.assertRaises(SystemExit) as cm:
                        ai_status.command_restore_approved(state, ["FREEZE-TEST-021D", "Restore"])
                self.assertIn(expected, str(cm.exception))
                self.assertEqual(
                    ai_status.get_task(state, "FREEZE-TEST-021D")["status"], "in_progress"
                )

    def test_restore_approved_head_requires_the_missing_head_shape(self) -> None:
        """It repairs the B22 shape only; it is not a second approve path."""
        head = "1111111122222222333333334444444455555555"
        ai_status.clear_ai_status_caches()
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Claude"), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value=head), \
             unittest.mock.patch("ai_status.task_pr_head_and_merge_commit", return_value=(head, None)), \
             unittest.mock.patch("ai_status.append_log"):
            in_progress = self._restore_state(id="FREEZE-TEST-022F")
            with self.assertRaises(SystemExit) as cm:
                ai_status.command_restore_approved_head(in_progress, ["FREEZE-TEST-022F", head, "attest"])
            self.assertIn("only valid when status is review_approved", str(cm.exception))

            already = {"tasks": [self._missing_head_task("FREEZE-TEST-022G")]}
            ai_status.get_task(already, "FREEZE-TEST-022G")["approved_head"] = head
            with self.assertRaises(SystemExit) as cm:
                ai_status.command_restore_approved_head(already, ["FREEZE-TEST-022G", head, "attest"])
            self.assertIn("already carries one", str(cm.exception))

            same_identity = {"tasks": [self._missing_head_task("FREEZE-TEST-022H")]}
            ai_status.get_task(same_identity, "FREEZE-TEST-022H")["owner"] = "Claude"
            with self.assertRaises(SystemExit) as cm:
                ai_status.command_restore_approved_head(same_identity, ["FREEZE-TEST-022H", head, "attest"])
            self.assertIn("must be separate identities", str(cm.exception))

    # ------------------------------------------------------------------
    # Round-8 non-blocking findings N1/N2, now covered.
    # ------------------------------------------------------------------

    def test_return_to_review_transitions_clear_and_preserve_the_right_heads(self) -> None:
        """N2: `reopen`/`handoff` popping approved_head is load-bearing.

        After B20-b a leftover approved_head makes `command_approve` refuse
        outright, so dropping either pop would wedge the whole
        reject -> rework -> re-approve cycle with the suite still green. The
        durable `last_approved_head` must survive all three, or B21's fix
        loses its input.
        """
        approved = "1111111122222222333333334444444455555555"

        def _state() -> dict[str, Any]:
            return {
                "tasks": [
                    {
                        "id": "FREEZE-TEST-023",
                        "owner": "Antigravity4",
                        "reviewer": "Claude",
                        "status": "review_approved",
                        "review_submission": {
                            "pr_number": 123,
                            "remote_sha": approved,
                            "branch": "task/FREEZE-TEST-023",
                            "base_branch": "dev",
                        },
                        "approved_head": approved,
                        "last_approved_head": approved,
                    }
                ],
                "handoffs": [],
            }

        cases = (
            ("reopen", ai_status.command_reopen, ["FREEZE-TEST-023", "rework"], "Antigravity4"),
            ("handoff", ai_status.command_handoff, ["FREEZE-TEST-023", "Claude", "review"], "Antigravity4"),
            ("re_review", ai_status.command_re_review, ["FREEZE-TEST-023", "re-review"], "Antigravity4"),
        )
        for label, command, args, actor in cases:
            with self.subTest(command=label):
                state = _state()
                with unittest.mock.patch("ai_status.current_actor_validated", return_value=actor), \
                     unittest.mock.patch("ai_status.resolve_actor_reference", side_effect=lambda v, **_: v), \
                     unittest.mock.patch("ai_status.append_log"):
                    command(state, args)
                task = ai_status.get_task(state, "FREEZE-TEST-023")
                self.assertNotIn("approved_head", task)
                self.assertEqual(task["last_approved_head"], approved)

    def test_ci_failure_branch_signals_and_clears_the_pending_timer(self) -> None:
        """A deterministic CI failure returns the task for owner repair."""
        approved = "1111111122222222333333334444444455555555"
        config = self._build_freeze_test_config()
        task = {
            "id": "FREEZE-TEST-024",
            "owner": "Antigravity4",
            "reviewer": "Claude",
            "status": "review_approved",
            "priority": "P1",
            "approved_head": approved,
            "ci_pending_since_ts": datetime.now(UTC).timestamp() - 4000,
        }

        dispatched, logged, mock_queue = self._run_finalize_dispatch_capturing_signals(
            config, task, head=approved, ci="failure"
        )
        self.assertTrue(dispatched)
        mock_queue.assert_not_called()
        self.assertIn("ci_repair_requeued", [e["type"] for e in logged])
        self.assertNotIn("ci_pending_timeout", [e["type"] for e in logged])
        self.assertEqual(task["status"], "in_progress")
        self.assertNotIn("ci_pending_since_ts", task)
        self.assertNotIn("approved_head", task)
        self.assertIn("owner requeued", task["next"])


class SuccessfulWorkerPostconditionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_test_config()
        self.config.setdefault("provider_guardrails", {})["generic_exit_reassign_after"] = 2

    @staticmethod
    def _task(*, status: str = "in_progress", next_step: str = "Implement packet") -> dict[str, Any]:
        return {
            "id": "ODP-POSTCONDITION-001",
            "status": status,
            "owner": "Antigravity4",
            "reviewer": "Codex6",
            "next": next_step,
            "depends_on": [],
        }

    @staticmethod
    def _worker(
        task: dict[str, Any],
        *,
        run_id: str = "antigravity4-postcondition-1",
        reason: str = "owned_in_progress_dispatch",
        agent_id: str = "antigravity4",
        dispatch_head: str = "a" * 40,
    ) -> dict[str, Any]:
        dispatch_task = {
            "id": task["id"],
            "status": task["status"],
            "owner": task["owner"],
            "reviewer": task["reviewer"],
            "next": task["next"],
            "head": dispatch_head,
        }
        return {
            "run_id": run_id,
            "task_id": task["id"],
            "provider": agent_id,
            "agent_id": agent_id,
            "status": "running",
            "queue_event_id": f"evt-{run_id}",
            "pid": 999999,
            "runner_status": "completed",
            "exit_code": 0,
            "last_event_at": "2026-07-31T15:00:00Z",
            "request_snapshot": {
                "reason": reason,
                "metadata": {
                    "logical_agent_id": agent_id,
                    "task": dispatch_task,
                },
            },
        }

    def _poll(self, state: dict[str, Any], task: dict[str, Any], *, current_head: str) -> bool:
        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "resolve_task_progress_head", return_value=current_head),
            mock.patch.object(supervisor, "detect_worker_failure", side_effect=AssertionError("zero-exit worker must use postcondition path")),
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            return supervisor.poll_workers(self.config, state)

    def test_poll_accepts_new_head_as_incremental_progress_and_clears_failure_streak(self) -> None:
        task = self._task()
        worker = self._worker(task)
        streak_key = f"{task['id']}:antigravity4"
        state = {
            "queue": {"events": {worker["queue_event_id"]: {"status": "started"}}},
            "workers": {worker["run_id"]: worker},
            "provider_guardrails": {"task_failure_streaks": {streak_key: {"count": 1}}},
        }
        self.assertTrue(self._poll(state, task, current_head="b" * 40))
        self.assertEqual(worker["status"], "completed")
        self.assertEqual(worker["progress_outcome"], "incremental_progress")
        self.assertEqual(state["queue"]["events"][worker["queue_event_id"]]["status"], "completed")
        self.assertNotIn(streak_key, state["provider_guardrails"]["task_failure_streaks"])

    def test_poll_reassigns_after_repeated_zero_exit_without_progress(self) -> None:
        task = self._task()
        worker = self._worker(task)
        streak_key = f"{task['id']}:antigravity4"
        state = {
            "queue": {"events": {worker["queue_event_id"]: {"status": "started"}}},
            "workers": {worker["run_id"]: worker},
            "provider_guardrails": {"task_failure_streaks": {streak_key: {"count": 1}}},
        }
        with mock.patch.object(supervisor, "maybe_reassign_task_after_worker_failure", return_value="Antigravity5") as reassign:
            self.assertTrue(self._poll(state, task, current_head="a" * 40))
        self.assertEqual(worker["status"], "reassigned")
        self.assertEqual(worker["reassigned_to"], "Antigravity5")
        self.assertEqual(state["provider_guardrails"]["task_failure_streaks"][streak_key]["count"], 2)
        reassign.assert_called_once()

    def test_poll_signal_termination_skips_log_without_marking_success(self) -> None:
        task = self._task()
        worker = self._worker(task)
        worker.update({"runner_status": "completed", "exit_code": 0, "runner_signal": 15})
        state = {
            "queue": {"events": {worker["queue_event_id"]: {"status": "started"}}},
            "workers": {worker["run_id"]: worker},
            "provider_guardrails": {"dispatch_pauses": {}},
        }
        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "detect_worker_failure", wraps=supervisor.detect_worker_failure) as detect_worker_failure,
            mock.patch.object(supervisor, "mark_provider_dispatch_paused") as mark_provider_dispatch_paused,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.poll_workers(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(worker["status"], "failed")
        self.assertNotEqual(worker["status"], "completed")
        detect_worker_failure.assert_called_once()
        mark_provider_dispatch_paused.assert_not_called()

    def test_owner_ready_note_without_handoff_is_no_progress_even_with_new_head(self) -> None:
        task = self._task(next_step="Implementation complete; ready for independent review")
        worker = self._worker(self._task())
        outcome = supervisor.successful_worker_exit_outcome(
            worker,
            task,
            terminal_statuses={"done", "review_approved"},
        )
        self.assertEqual(outcome, "no_progress")

    def test_poll_accepts_reviewer_reopen_as_durable_review_decision(self) -> None:
        dispatch_task = self._task(status="review", next_step="Independent review required")
        worker = self._worker(dispatch_task, reason="review_ready_dispatch", agent_id="codex6")
        current_task = self._task(status="in_progress", next_step="Fix review finding B1")
        state = {
            "queue": {"events": {worker["queue_event_id"]: {"status": "started"}}},
            "workers": {worker["run_id"]: worker},
        }
        with mock.patch.object(supervisor, "seal_worker_handoff") as seal_worker_handoff:
            self.assertTrue(self._poll(state, current_task, current_head="a" * 40))
        self.assertEqual(worker["status"], "completed")
        self.assertEqual(worker["progress_outcome"], "review_decided")
        seal_worker_handoff.assert_not_called()

    def test_owner_review_handoff_is_revoked_when_the_exit_seal_rejects_it(self) -> None:
        task = self._task(status="review", next_step="Independent review required")
        worker = self._worker(task, reason="owned_ready_dispatch")
        worker.update(
            {
                "workspace_mode": "isolated_worktree",
                "workspace_path": "/tmp/owner-worktree",
                "workspace_branch": "task/ODP-POSTCONDITION-001",
            }
        )
        state = {
            "queue": {"events": {worker["queue_event_id"]: {"status": "started"}}},
            "workers": {worker["run_id"]: worker},
        }
        rejected = supervisor.WorkerHandoffSeal(
            False,
            "owner_dirty",
            "1 dirty change (1 untracked): fix_probe.py",
            "a" * 40,
            "dirt-fingerprint",
        )
        with (
            mock.patch.object(supervisor, "seal_worker_handoff", return_value=rejected),
            mock.patch.object(
                supervisor.status_transition,
                "reject_unsealed_worker_handoff",
                return_value=True,
            ) as reject_handoff,
            mock.patch.object(supervisor, "record_unsealed_worker_handoff") as record_handoff,
        ):
            self.assertTrue(self._poll(state, task, current_head="a" * 40))

        self.assertEqual(worker["status"], "completed")
        self.assertEqual(worker["progress_outcome"], "handoff_seal_rejected")
        self.assertEqual(state["queue"]["events"][worker["queue_event_id"]]["status"], "completed")
        reject_handoff.assert_called_once()
        record_handoff.assert_called_once()

    def test_poll_never_recounts_historical_terminal_run_after_reopen(self) -> None:
        task = self._task(status="review", next_step="Fresh exact-head review required")
        for historical_status in ("completed", "failed", "superseded", "reassigned"):
            with self.subTest(historical_status=historical_status):
                worker = self._worker(
                    self._task(status="review", next_step="Old review"),
                    reason="review_ready_dispatch",
                    agent_id="codex6",
                )
                worker.update(
                    {
                        "status": historical_status,
                        "runner_status": "failed",
                        "exit_code": -15,
                        "runner_signal": 15,
                    }
                )
                state = {
                    "queue": {"events": {worker["queue_event_id"]: {"status": "completed"}}},
                    "workers": {worker["run_id"]: worker},
                    "provider_guardrails": {"task_failure_streaks": {}},
                }
                with mock.patch.object(
                    supervisor,
                    "maybe_reassign_task_after_worker_failure",
                    side_effect=AssertionError("historical run must not be re-counted"),
                ):
                    self.assertFalse(self._poll(state, task, current_head="b" * 40))
                self.assertEqual(worker["status"], historical_status)
                self.assertEqual(state["provider_guardrails"]["task_failure_streaks"], {})

    def test_boot_reconciliation_applies_same_no_progress_threshold(self) -> None:
        task = self._task()
        worker = self._worker(task)
        streak_key = f"{task['id']}:antigravity4"
        state = {
            "queue": {"events": {worker["queue_event_id"]: {"status": "started"}}},
            "workers": {worker["run_id"]: worker},
            "provider_guardrails": {"task_failure_streaks": {streak_key: {"count": 1}}},
        }
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "resolve_task_progress_head", return_value="a" * 40),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "maybe_reassign_task_after_worker_failure", return_value="Antigravity5") as reassign,
        ):
            changed = supervisor.reconcile_runtime_on_boot(self.config, state)
        self.assertTrue(changed)
        self.assertEqual(worker["status"], "reassigned")
        self.assertEqual(worker["reassigned_to"], "Antigravity5")
        self.assertEqual(state["provider_guardrails"]["task_failure_streaks"][streak_key]["count"], 2)
        reassign.assert_called_once()


class SupervisorFailureLoopCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_test_config()

    def test_full_agent_matrix_coverage_all_configured_agents(self) -> None:
        all_agents = [
            "Antigravity", "Antigravity2", "Antigravity3", "Antigravity4", "Antigravity5", "Antigravity6", "Antigravity7",
            "Claude", "Claude2", "Claude3",
            "Codex", "Codex2", "Codex3", "Codex4", "Codex5", "Codex6", "Codex7", "Codex8", "Codex9", "CodexCoordinator",
            "Gemini", "Gemini2", "Copilot"
        ]
        for agent in all_agents:
            with self.subTest(agent=agent, role="owner"):
                fallbacks = supervisor.get_agent_reassignment_candidates(self.config, agent, role="owner")
                self.assertTrue(len(fallbacks) > 0, f"Owner fallbacks for {agent} should not be empty")
                self.assertNotIn(agent, fallbacks)
                self.assertNotIn("Human/Ops", fallbacks)

            with self.subTest(agent=agent, role="reviewer"):
                fallbacks = supervisor.get_agent_reassignment_candidates(self.config, agent, role="reviewer")
                self.assertTrue(len(fallbacks) > 0, f"Reviewer fallbacks for {agent} should not be empty")
                self.assertNotIn(agent, fallbacks)
                self.assertNotIn("Human/Ops", fallbacks)

    def test_dynamic_fallback_derivation_when_agent_missing_from_config(self) -> None:
        lean_config = {
            "worker_reassignment": {
                "enabled": True,
                "after_attempts": 2,
                "reassign_on_terminal_failure": True,
                "owner_fallbacks": {},
                "reviewer_fallbacks": {},
            },
            "agents": {
                "antigravity4": {"display_name": "Antigravity4"},
                "antigravity3": {"display_name": "Antigravity3"},
                "codex6": {"display_name": "Codex6"},
            },
        }
        fallbacks = supervisor.get_agent_reassignment_candidates(lean_config, "Antigravity4", role="owner")
        self.assertIn("Antigravity3", fallbacks)
        self.assertIn("Codex6", fallbacks)
        self.assertNotIn("Antigravity4", fallbacks)

    def test_failure_loop_reassignment_drill_owner(self) -> None:
        worker = {
            "task_id": "ODP-PLAN-SITESCORE-OUTCOME-001",
            "agent_id": "antigravity4",
            "retry_count": 2,
            "run_id": "antigravity4-run-1",
        }
        status = {
            "tasks": [
                {
                    "id": "ODP-PLAN-SITESCORE-OUTCOME-001",
                    "status": "in_progress",
                    "owner": "Antigravity4",
                    "reviewer": "Codex6",
                }
            ]
        }
        state = {
            "provider_guardrails": {
                "task_failure_streaks": {
                    "ODP-PLAN-SITESCORE-OUTCOME-001:antigravity4": {
                        "task_id": "ODP-PLAN-SITESCORE-OUTCOME-001",
                        "provider": "antigravity4",
                        "count": 2,
                    }
                }
            }
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            reassigned_to = supervisor.maybe_reassign_task_after_worker_failure(
                self.config,
                state,
                worker,
                "Terminal model quota exhausted",
                terminal=True,
            )

        self.assertIsNotNone(reassigned_to)
        self.assertNotEqual(reassigned_to, "Antigravity4")
        self.assertNotEqual(reassigned_to, "Codex6")
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "ODP-PLAN-SITESCORE-OUTCOME-001")
        self.assertEqual(kwargs["new_owner"], reassigned_to)
        self.assertEqual(kwargs["new_status"], "todo")
        self.assertNotIn("ODP-PLAN-SITESCORE-OUTCOME-001:antigravity4", state["provider_guardrails"]["task_failure_streaks"])

    def test_failure_loop_reassignment_drill_reviewer(self) -> None:
        worker = {
            "task_id": "ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001",
            "agent_id": "antigravity7",
            "retry_count": 2,
            "run_id": "antigravity7-run-1",
        }
        status = {
            "tasks": [
                {
                    "id": "ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001",
                    "status": "review",
                    "owner": "Codex6",
                    "reviewer": "Antigravity7",
                }
            ]
        }
        state = {
            "provider_guardrails": {
                "task_failure_streaks": {
                    "ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001:antigravity7": {
                        "task_id": "ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001",
                        "provider": "antigravity7",
                        "count": 2,
                    }
                }
            }
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            reassigned_to = supervisor.maybe_reassign_task_after_worker_failure(
                self.config,
                state,
                worker,
                "Quota limit reached",
                terminal=True,
            )

        self.assertIsNotNone(reassigned_to)
        self.assertNotEqual(reassigned_to, "Antigravity7")
        self.assertNotEqual(reassigned_to, "Codex6")
        self.assertEqual(persist.call_args.kwargs["new_reviewer"], reassigned_to)

    def test_fail_closed_human_ops_gate_never_auto_reassigned(self) -> None:
        worker = {
            "task_id": "ODP-PLAN-OSS-LEGAL-POLICY-001",
            "agent_id": "Human/Ops",
            "retry_count": 5,
            "run_id": "human-run-1",
        }
        status = {
            "tasks": [
                {
                    "id": "ODP-PLAN-OSS-LEGAL-POLICY-001",
                    "status": "in_progress",
                    "owner": "Human/Ops",
                    "reviewer": "CodexCoordinator",
                }
            ]
        }
        state = {}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment") as persist,
        ):
            reassigned_to = supervisor.maybe_reassign_task_after_worker_failure(
                self.config,
                state,
                worker,
                "Human gate pending",
                terminal=True,
            )

        self.assertIsNone(reassigned_to)
        persist.assert_not_called()

    def test_owner_reassignment_preserves_human_gate_reviewer(self) -> None:
        """A Human/Ops reviewer survives owner reassignment instead of being swapped for an agent.

        `first_viable_agent` deliberately skips human-gate names, so the reviewer
        replacement search reports the existing human reviewer as unviable and
        would otherwise hand the review gate to whichever automated lane is
        least loaded, silently dropping the human approval requirement.
        """
        worker = {
            "task_id": "T-HUMAN-REVIEWER",
            "agent_id": "antigravity4",
            "retry_count": 2,
            "run_id": "antigravity4-run-1",
        }
        status = {
            "tasks": [
                {
                    "id": "T-HUMAN-REVIEWER",
                    "status": "in_progress",
                    "owner": "Antigravity4",
                    "reviewer": "Human/Ops",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            new_owner = supervisor.maybe_reassign_task_after_worker_failure(
                self.config,
                {},
                worker,
                "Terminal provider failure",
                terminal=True,
            )

        self.assertIsNotNone(new_owner, "the failing automated owner lane should still recover")
        self.assertNotEqual(new_owner, "Antigravity4")
        self.assertFalse(supervisor.is_human_gate_agent(new_owner))
        persist.assert_called_once()
        self.assertEqual(persist.call_args.kwargs["new_owner"], new_owner)
        self.assertEqual(persist.call_args.kwargs["new_reviewer"], "Human/Ops")

    def test_fail_closed_never_reassigns_to_human_ops(self) -> None:
        config_with_human = dict(self.config)
        config_with_human["worker_reassignment"] = {
            "enabled": True,
            "after_attempts": 1,
            "reassign_on_terminal_failure": True,
            "owner_fallbacks": {
                "Antigravity4": ["Human/Ops", "Codex6"],
            },
        }
        worker = {
            "task_id": "T-TEST",
            "agent_id": "antigravity4",
            "retry_count": 2,
        }
        status = {
            "tasks": [
                {
                    "id": "T-TEST",
                    "status": "in_progress",
                    "owner": "Antigravity4",
                    "reviewer": "Claude",
                }
            ]
        }
        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment") as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            reassigned = supervisor.maybe_reassign_task_after_worker_failure(
                config_with_human,
                {},
                worker,
                "failure",
                terminal=True,
            )

        self.assertNotEqual(reassigned, "Human/Ops")
        if persist.called:
            self.assertNotEqual(persist.call_args.kwargs["new_owner"], "Human/Ops")
            self.assertNotEqual(persist.call_args.kwargs["new_reviewer"], "Human/Ops")

    def test_fail_closed_skips_paused_agents(self) -> None:
        state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "antigravity3": {"provider": "antigravity3", "blocked_until": "2099-01-01T00:00:00Z"},
                    "antigravity5": {"provider": "antigravity5", "blocked_until": "2099-01-01T00:00:00Z"},
                }
            }
        }
        worker = {
            "task_id": "T-PAUSE-TEST",
            "agent_id": "antigravity4",
            "retry_count": 2,
        }
        status = {
            "tasks": [
                {
                    "id": "T-PAUSE-TEST",
                    "status": "in_progress",
                    "owner": "Antigravity4",
                    "reviewer": "Codex6",
                }
            ]
        }
        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment") as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            reassigned = supervisor.maybe_reassign_task_after_worker_failure(
                self.config,
                state,
                worker,
                "failure",
                terminal=True,
            )

        self.assertNotIn(reassigned, ["Antigravity3", "Antigravity5", "Antigravity4", "Codex6"])
        if reassigned is None:
            persist.assert_not_called()

    def test_concurrent_claim_main_loop_state_preservation(self) -> None:
        """R1: Prove concurrent claim/main-loop state save preserves live worker, reconciles event, and avoids double-dispatch."""
        import tempfile
        from pathlib import Path
        ai_status.clear_ai_status_caches()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            cfg = deepcopy(self.config)
            ready_disp = cfg.setdefault("ready_dispatcher", {})
            ready_disp["enabled"] = True
            ready_disp["disabled_agents"] = []
            ready_disp.setdefault("max_tasks_per_agent_by_agent", {})["Antigravity4"] = 5
            ready_disp.setdefault("max_concurrent_per_quota_group", {})["antigravity"] = 5
            cfg.setdefault("agents", {})["antigravity4"] = {
                "display_name": "Antigravity4",
                "provider": "antigravity",
                "adapter": "antigravity",
            }
            live_activity_path = ROOT_DIR / "ai-activity-log.jsonl"
            live_activity_before = (
                live_activity_path.read_bytes()
                if live_activity_path.exists()
                else None
            )
            isolated_paths = {
                "status_file": tmp_root / "ai-status.json",
                "activity_log": tmp_root / "ai-activity-log.jsonl",
                "current_work": tmp_root / "current-work.md",
                "dashboard": tmp_root / "docs-site" / "index.html",
                "state_file": tmp_root / ".orchestrator" / "state.json",
                "event_queue": tmp_root / ".orchestrator" / "event-queue.jsonl",
                "approval_queue": tmp_root / ".orchestrator" / "approval-queue.json",
                "sidecar_catalog": tmp_root / ".orchestrator" / "sidecar_catalog.json",
                "github_bus_state": tmp_root / ".orchestrator" / "github-bus-state.json",
                "github_relay_state": tmp_root / ".orchestrator" / "github-relay-state.json",
                "provider_capabilities": tmp_root / ".orchestrator" / "provider_capabilities.json",
                "claude_mcp_config": tmp_root / ".orchestrator" / "claude-approval-broker.mcp.json",
            }
            (tmp_root / ".orchestrator").mkdir(parents=True, exist_ok=True)
            (tmp_root / ".orchestrator" / "event-queue.jsonl").write_text("", encoding="utf-8")
            (tmp_root / ".orchestrator" / "state.json").write_text("{}", encoding="utf-8")
            cfg["paths"] = {key: str(value) for key, value in isolated_paths.items()}
            cfg.setdefault("worker_worktrees", {})["root"] = str(tmp_root / "worker-worktrees")
            cfg.setdefault("permission_broker", {})["allowed_workspace_roots"] = [str(tmp_root)]
            cfg.setdefault("watchdog", {})["state_file"] = str(tmp_root / ".orchestrator" / "watchdog-state.json")
            cfg.setdefault("watchdog", {})["metrics_file"] = str(tmp_root / ".orchestrator" / "watchdog-metrics.jsonl")

            status_todo = {
                "agents": [
                    {
                        "name": "Antigravity4",
                        "status": "idle",
                        "current_task_ids": [],
                    }
                ],
                "tasks": [
                    {
                        "id": "ODP-CONC-001",
                        "status": "todo",
                        "priority": "P1",
                        "owner": "Antigravity4",
                        "reviewer": "Codex",
                    }
                ]
            }

            ai_status.clear_ai_status_caches()
            initial_state = supervisor.load_runtime_state(cfg)
            initial_state["seen_event_keys"] = {}
            with (
                mock.patch.object(supervisor, "load_status", return_value=status_todo),
                mock.patch.object(supervisor, "scan_live_worker_pids_by_agent", return_value={}),
                mock.patch.object(supervisor, "outstanding_delivery_indexes", return_value=(set(), set(), set())),
                mock.patch.object(supervisor, "agent_dispatch_loads", return_value={}),
            ):
                supervisor.dispatch_ready_tasks(cfg, initial_state, {}, agent_ids_override=["antigravity4"])
            supervisor.save_runtime_state(cfg, initial_state)

            main_loop_state = supervisor.load_runtime_state(cfg)
            evt_id = list(main_loop_state.get("queue", {}).get("events", {}).keys())[0]

            concurrent_worker = {
                "run_id": "antigravity4-conc-run-1",
                "agent_id": "antigravity4",
                "task_id": "ODP-CONC-001",
                "queue_event_id": evt_id,
                "pid": os.getpid(),
                "status": "running",
                "started_at": supervisor.utc_now(),
                "last_heartbeat_at": supervisor.utc_now(),
            }
            disk_state = supervisor.load_runtime_state(cfg)
            disk_state["workers"]["antigravity4-conc-run-1"] = concurrent_worker
            disk_state["queue"]["events"][evt_id]["status"] = "started"
            disk_state["queue"]["events"][evt_id]["run_id"] = "antigravity4-conc-run-1"
            supervisor.save_runtime_state(cfg, disk_state)
            # The main loop crosses the real locked save boundary before it may
            # process its stale queue snapshot. The concurrent run and started
            # event must be merged into its live view.
            supervisor.save_runtime_state(cfg, main_loop_state)
            main_loop_state = supervisor.load_runtime_state(cfg)

            status_in_prog = {
                "tasks": [
                    {
                        "id": "ODP-CONC-001",
                        "status": "in_progress",
                        "priority": "P1",
                        "owner": "Antigravity4",
                        "reviewer": "Codex6",
                    }
                ]
            }

            with (
                mock.patch.object(supervisor, "load_status", return_value=status_in_prog),
                mock.patch.object(supervisor, "prepare_worker_workspace", return_value=(True, "isolated")),
                mock.patch.object(supervisor, "start_worker_for_request") as start_worker,
                mock.patch.object(supervisor, "sync_dispatched_task_status", return_value=True),
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                supervisor.process_queue(cfg, main_loop_state, {})
            start_worker.assert_not_called()

            supervisor.save_runtime_state(cfg, main_loop_state)
            saved_disk = supervisor.load_runtime_state(cfg)

            self.assertIn("antigravity4-conc-run-1", saved_disk["workers"])
            self.assertEqual(saved_disk["workers"]["antigravity4-conc-run-1"]["pid"], os.getpid())

            evt_rec = saved_disk["queue"]["events"].get(evt_id, {})
            self.assertIn(evt_rec.get("status"), {"started", "manual_pending"})
            self.assertNotEqual(evt_rec.get("skip_reason"), "stale_dispatch_event")
            self.assertEqual(evt_rec.get("run_id"), "antigravity4-conc-run-1")

            with (
                mock.patch.object(supervisor, "load_status", return_value=status_in_prog),
                mock.patch.object(supervisor, "scan_live_worker_pids_by_agent", return_value={}),
            ):
                dispatched = supervisor.dispatch_ready_tasks(cfg, saved_disk, {})
                self.assertFalse(dispatched)

            self.assertTrue(Path(cfg["paths"]["state_file"]).is_relative_to(tmp_root))
            self.assertTrue(Path(cfg["paths"]["event_queue"]).is_relative_to(tmp_root))
            self.assertTrue(Path(cfg["worker_worktrees"]["root"]).is_relative_to(tmp_root))
            live_activity_after = (
                live_activity_path.read_bytes()
                if live_activity_path.exists()
                else None
            )
            self.assertEqual(live_activity_after, live_activity_before)

    def test_runtime_state_save_preserves_terminal_worker_and_queue_transitions(self) -> None:
        """R9: A stale active disk snapshot cannot revive a terminal run or queue record."""
        for terminal_status in ("completed", "failed"):
            with self.subTest(terminal_status=terminal_status), tempfile.TemporaryDirectory() as tmpdir:
                state_path = Path(tmpdir) / "state.json"
                cfg = deepcopy(self.config)
                cfg["paths"]["state_file"] = str(state_path)
                cfg["paths"]["event_queue"] = str(Path(tmpdir) / "event-queue.jsonl")

                run_id = f"run-{terminal_status}"
                event_id = f"event-{terminal_status}"
                Path(cfg["paths"]["event_queue"]).write_text(
                    json.dumps(
                        {
                            "event_id": event_id,
                            "task_id": "ODP-TERMINAL-001",
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                disk_state = supervisor.load_runtime_state(cfg)
                disk_state["workers"][run_id] = {
                    "run_id": run_id,
                    "task_id": "ODP-TERMINAL-001",
                    "queue_event_id": event_id,
                    "pid": os.getpid(),
                    "status": "running",
                    "last_heartbeat_at": "2026-07-31T08:00:00Z",
                }
                disk_state["queue"]["events"][event_id] = {
                    "status": "started",
                    "run_id": run_id,
                }
                supervisor.save_runtime_state(cfg, disk_state)

                terminal_state = deepcopy(disk_state)
                terminal_state["workers"][run_id]["status"] = terminal_status
                terminal_state["workers"][run_id]["finished_at"] = "2026-07-31T08:01:00Z"
                terminal_state["queue"]["events"][event_id]["status"] = terminal_status
                terminal_state["queue"]["events"][event_id]["processed_at"] = "2026-07-31T08:01:00Z"
                supervisor.save_runtime_state(cfg, terminal_state)

                persisted = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(persisted["workers"][run_id]["status"], terminal_status)
                self.assertEqual(persisted["queue"]["events"][event_id]["status"], terminal_status)

    def test_runtime_state_save_serializes_two_real_writers(self) -> None:
        """R10: Two processes that loaded the same snapshot both survive the locked transaction."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            state_path = tmp_root / "state.json"
            cfg = deepcopy(self.config)
            cfg["paths"]["state_file"] = str(state_path)
            cfg["paths"]["event_queue"] = str(tmp_root / "event-queue.jsonl")
            supervisor.save_runtime_state(cfg, supervisor.load_runtime_state(cfg))

            ready_read, ready_write = os.pipe()
            start_read, start_write = os.pipe()
            child_pids: list[int] = []
            for index in (1, 2):
                pid = os.fork()
                if pid == 0:
                    os.close(ready_read)
                    os.close(start_write)
                    try:
                        child_state = supervisor.load_runtime_state(cfg)
                        run_id = f"run-{index}"
                        child_state.setdefault("workers", {})[run_id] = {
                            "run_id": run_id,
                            "task_id": f"ODP-CONCURRENT-{index}",
                            "pid": os.getpid(),
                            "status": "running",
                            "last_heartbeat_at": "2026-07-31T08:00:00Z",
                        }
                        os.write(ready_write, b"1")
                        os.read(start_read, 1)
                        supervisor.save_runtime_state(cfg, child_state)
                    except BaseException:
                        os._exit(1)
                    os._exit(0)
                child_pids.append(pid)

            os.close(ready_write)
            os.close(start_read)
            try:
                ready = b""
                while len(ready) < 2:
                    ready += os.read(ready_read, 2 - len(ready))
                self.assertEqual(ready, b"11")
                os.write(start_write, b"12")
                exit_codes = []
                for pid in child_pids:
                    _, wait_status = os.waitpid(pid, 0)
                    exit_codes.append(os.waitstatus_to_exitcode(wait_status))
            finally:
                os.close(ready_read)
                os.close(start_write)

            self.assertEqual(exit_codes, [0, 0])
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(sorted(persisted["workers"]), ["run-1", "run-2"])

    def test_fail_closed_human_gate_task_metadata_and_non_dispatchable(self) -> None:
        """R2: Prove human-gate metadata and non_dispatchable tasks fail-closed and return None without calling persist."""
        worker = {
            "task_id": "ODP-HG-TEST",
            "agent_id": "antigravity4",
            "retry_count": 3,
            "run_id": "ag4-run-hg",
        }
        cases = [
            {"id": "ODP-HG-TEST", "status": "in_progress", "owner": "Antigravity4", "reviewer": "Codex6", "task_class": "human_gate"},
            {"id": "ODP-HG-TEST", "status": "in_progress", "owner": "Antigravity4", "reviewer": "Codex6", "human_required_roles": ["security_reviewer"]},
            {"id": "ODP-HG-TEST", "status": "in_progress", "owner": "Antigravity4", "reviewer": "Codex6", "gate_status": "pending_human_signoff"},
            {"id": "ODP-HG-TEST", "status": "in_progress", "owner": "Antigravity4", "reviewer": "Codex6", "non_dispatchable": True},
        ]
        for task_dict in cases:
            with self.subTest(case=task_dict):
                status = {"tasks": [task_dict]}
                with mock.patch.object(supervisor, "load_status", return_value=status), \
                     mock.patch.object(supervisor, "persist_task_reassignment") as persist:
                    res = supervisor.maybe_reassign_task_after_worker_failure(
                        self.config,
                        {},
                        worker,
                        "failure threshold exceeded",
                        terminal=True,
                    )
                    self.assertIsNone(res)
                    persist.assert_not_called()

    def test_full_agent_matrix_and_negative_viability_coverage(self) -> None:
        """R3: Prove enabled agent matrix coverage and negative viability checks."""
        enabled_agents = [
            "Antigravity", "Antigravity2", "Antigravity3", "Antigravity4", "Antigravity5", "Antigravity6", "Antigravity7",
            "Claude", "Claude2", "Claude3",
            "Codex", "Codex2", "Codex3", "Codex4", "Codex5", "Codex6", "Codex7", "Codex8", "Codex9", "CodexCoordinator",
            "Gemini", "Gemini2", "Copilot"
        ]
        for agent in enabled_agents:
            with self.subTest(agent=agent, role="owner"):
                candidates = supervisor.get_agent_reassignment_candidates(self.config, agent, role="owner")
                viable = supervisor.first_viable_agent(self.config, candidates, exclude={agent})
                self.assertIsNotNone(viable, f"Owner fallback for {agent} should return a viable candidate")
                self.assertNotEqual(viable, agent)
                self.assertNotEqual(viable, "Human/Ops")

            with self.subTest(agent=agent, role="reviewer"):
                candidates = supervisor.get_agent_reassignment_candidates(self.config, agent, role="reviewer")
                viable = supervisor.first_viable_agent(self.config, candidates, exclude={agent})
                self.assertIsNotNone(viable, f"Reviewer fallback for {agent} should return a viable candidate")
                self.assertNotEqual(viable, agent)
                self.assertNotEqual(viable, "Human/Ops")

        disabled_config = deepcopy(self.config)
        disabled_config.setdefault("agents", {})["antigravity3"] = {"display_name": "Antigravity3", "enabled": False}
        viable_dis = supervisor.first_viable_agent(disabled_config, ["Antigravity3"], exclude=set())
        self.assertIsNone(viable_dis)

        paused_state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "antigravity3": {"provider": "antigravity3", "blocked_until": "2099-01-01T00:00:00Z"}
                }
            }
        }
        viable_pause = supervisor.first_viable_agent(self.config, ["Antigravity3"], exclude=set(), state=paused_state)
        self.assertIsNone(viable_pause)

        viable_human = supervisor.first_viable_agent(self.config, ["Human/Ops"], exclude=set())
        self.assertIsNone(viable_human)

    def test_status_check_http_422_failure_injection_outbox_transactional(self) -> None:
        """R13: Real isolated main retries the exact 422 payload without rolling back state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            status_file = tmp_root / "ai-status.json"
            activity_file = tmp_root / "ai-activity-log.jsonl"
            runtime_file = tmp_root / ".orchestrator" / "state.json"
            runtime_file.parent.mkdir(parents=True, exist_ok=True)
            backup_dir = tmp_root / ".orchestrator" / "worktree-dirt-backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_file = backup_dir / "odp-plan-acceptance-real-exec-001-lease_blocked.patch"
            backup_file.write_text("patch content", encoding="utf-8")
            runtime_payload = {
                "workers": {
                    "run-422": {
                        "run_id": "run-422",
                        "task_id": "ODP-422-TEST-001",
                        "status": "running",
                        "queue_event_id": "event-422",
                    }
                },
                "queue": {
                    "events": {
                        "event-422": {
                            "status": "started",
                            "run_id": "run-422",
                        }
                    }
                },
            }
            runtime_file.write_text(
                json.dumps(runtime_payload, indent=2) + "\n",
                encoding="utf-8",
            )
            runtime_before = runtime_file.read_bytes()
            state = {
                "agents": [
                    {
                        "name": "CodexCoordinator",
                        "capability_lane": [],
                        "status": "idle",
                        "current_task_ids": [],
                        "branch": "",
                        "next": "",
                        "last_update": None,
                    },
                    {
                        "name": "Codex2",
                        "capability_lane": [],
                        "status": "idle",
                        "current_task_ids": [],
                        "branch": "",
                        "next": "",
                        "last_update": None,
                    },
                ],
                "tasks": [
                    {
                        "id": "ODP-422-TEST-001",
                        "title": "Outbox transaction",
                        "phase": "execution-control",
                        "owner": "CodexCoordinator",
                        "reviewer": "Codex2",
                        "status": "review",
                        "depends_on": [],
                        "artifacts": [],
                        "acceptance": [],
                        "next": "Awaiting review",
                        "last_update": "2026-07-31T08:00:00Z",
                    }
                ],
                "handoffs": [
                    {
                        "task_id": "ODP-422-TEST-001",
                        "from": "CodexCoordinator",
                        "to": "Codex2",
                        "message": "Review exact head",
                        "status": "pending",
                        "created_at": "2026-07-31T08:00:00Z",
                    }
                ],
                "blockers": [],
                "workload": {},
                "workload_summary": {},
            }
            status_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            sha = "f1078e8b" + ("0" * 32)
            observed_status_at_post: list[str] = []
            post_results = [
                mock.MagicMock(
                    returncode=1,
                    stderr=f"HTTP 422 Unprocessable Entity: No commit found for SHA {sha}",
                    stdout="",
                ),
                mock.MagicMock(returncode=0, stderr="", stdout="created"),
            ]

            def fake_post(*_args: Any, **_kwargs: Any) -> Any:
                persisted = json.loads(status_file.read_text(encoding="utf-8"))
                observed_status_at_post.append(persisted["tasks"][0]["status"])
                return post_results.pop(0)

            live_activity = ROOT_DIR / "ai-activity-log.jsonl"
            live_activity_before = live_activity.read_bytes() if live_activity.exists() else None
            with (
                mock.patch.dict(os.environ, {"AI_NAME": "Codex2"}, clear=False),
                mock.patch.object(ai_status, "STATUS_ROOT", tmp_root),
                mock.patch.object(ai_status, "STATUS_FILE", status_file),
                mock.patch.object(ai_status, "LOG_FILE", activity_file),
                mock.patch.object(ai_status, "CURRENT_WORK_FILE", tmp_root / "current-work.md"),
                mock.patch.object(ai_status, "DOCS_SITE_DIR", tmp_root / "docs-site"),
                mock.patch.object(ai_status, "ORCHESTRATOR_STATE_FILE", runtime_file),
                mock.patch.object(ai_status, "DASHBOARD_BUNDLE_FILE", tmp_root / "dashboard-bundle.json"),
                mock.patch.object(ai_status, "resolve_task_sha", return_value=sha),
                mock.patch.object(ai_status, "task_repository_slug_safe", return_value="alfloop-dev/odayplus"),
                mock.patch.object(ai_status, "get_repository_slug_safe", return_value="alfloop-dev/odayplus"),
                mock.patch.object(ai_status, "sync_all", side_effect=lambda state_arg: ai_status.save_state(state_arg)),
                mock.patch.object(ai_status.subprocess, "run", side_effect=fake_post),
            ):
                self.assertEqual(
                    ai_status.main(
                        [
                            "ai_status.py",
                            "reopen",
                            "ODP-422-TEST-001",
                            "Exact-head review rejected",
                        ]
                    ),
                    0,
                )
                failed_state = json.loads(status_file.read_text(encoding="utf-8"))
                failed_task = failed_state["tasks"][0]
                self.assertEqual(failed_task["status"], "in_progress")
                self.assertEqual(len(failed_task["status_check_outbox"]), 1)
                failed_payload = failed_task["status_check_outbox"][0]
                self.assertEqual(failed_payload["sha"], sha)
                self.assertEqual(failed_payload["state"], "failure")
                self.assertEqual(failed_payload["context"], "task-review-gate")
                self.assertIn("Review rejected or reopened", failed_payload["description"])
                self.assertIn("HTTP 422", failed_payload["last_error"])

                self.assertEqual(ai_status.main(["ai_status.py", "sync"]), 0)

            reconciled_state = json.loads(status_file.read_text(encoding="utf-8"))
            reconciled_task = reconciled_state["tasks"][0]
            self.assertNotIn("status_check_outbox", reconciled_task)
            history = reconciled_task["status_check_delivery_history"]
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["sha"], sha)
            self.assertEqual(history[0]["state"], "failure")
            self.assertEqual(observed_status_at_post, ["in_progress", "in_progress"])
            self.assertEqual(runtime_file.read_bytes(), runtime_before)
            self.assertTrue(backup_file.exists())
            self.assertEqual(backup_file.read_text(encoding="utf-8"), "patch content")
            live_activity_after = live_activity.read_bytes() if live_activity.exists() else None
            self.assertEqual(live_activity_after, live_activity_before)

    def test_last_update_only_change_does_not_stale_eligible_wake(self) -> None:
        """R8: operational notes must not invalidate an otherwise eligible wake."""
        task = {
            "id": "ODP-WAKE-STABLE-001",
            "status": "in_progress",
            "owner": "Antigravity7",
            "reviewer": "CodexCoordinator",
            "last_update": "2026-07-31T08:28:23Z",
            "depends_on": [],
        }
        task_map = {task["id"]: task}
        event = supervisor.build_dispatch_event(
            task,
            "Antigravity7",
            supervisor.REASON_OWNED_IN_PROGRESS,
            task_map,
        )
        event["event_key"] = event["key"]
        event["target_display_name"] = "Antigravity7"

        task["last_update"] = "2026-07-31T08:29:00Z"
        task["next"] = "Coordinator note added after wake queueing"

        self.assertIsNone(
            supervisor.stale_dispatch_skip_message(self.config, event, task_map)
        )

    def test_assignment_or_status_change_still_stales_wake(self) -> None:
        """R8/R12 negative matrix: every authority or dependency change invalidates."""
        base_task = {
            "id": "ODP-WAKE-STABLE-002",
            "status": "in_progress",
            "owner": "Antigravity7",
            "reviewer": "CodexCoordinator",
            "depends_on": [],
        }
        dependency = {
            "id": "ODP-WAKE-DEPENDENCY-001",
            "status": "done",
            "owner": "Codex",
            "reviewer": "Codex2",
            "depends_on": [],
        }
        mutations = {
            "owner": lambda task, task_map: task.update(owner="Antigravity4"),
            "reviewer": lambda task, task_map: task.update(reviewer="Codex6"),
            "status": lambda task, task_map: task.update(status="review"),
            "dependency_list": lambda task, task_map: task.update(depends_on=[dependency["id"]]),
            "dependency_state": lambda task, task_map: task_map[dependency["id"]].update(status="in_progress"),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                task = deepcopy(base_task)
                if name == "dependency_state":
                    task["depends_on"] = [dependency["id"]]
                task_map = {
                    task["id"]: task,
                    dependency["id"]: deepcopy(dependency),
                }
                event = supervisor.build_dispatch_event(
                    task,
                    "Antigravity7",
                    supervisor.REASON_OWNED_IN_PROGRESS,
                    task_map,
                )
                event["event_key"] = event["key"]
                event["target_display_name"] = "Antigravity7"

                mutate(task, task_map)
                message = supervisor.stale_dispatch_skip_message(
                    self.config,
                    event,
                    task_map,
                ) or ""
                self.assertTrue(
                    "no longer eligible" in message or "task state changed" in message,
                    message,
                )

    def test_resolve_task_sha_precedence_over_merged_pr(self) -> None:
        """Verify pushed task branch beats both stale PR and unpushed local HEAD."""
        task_id = "TEST-BRANCH-PRECEDENCE-001"
        ai_status.clear_ai_status_caches()

        def fake_run(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "ls-remote --heads origin" in cmd_str:
                remote_ref = f"refs/heads/task/{task_id}"
                return unittest.mock.Mock(
                    returncode=0,
                    stdout=f"{'2' * 40}\t{remote_ref}\n",
                )
            if "branch --show-current" in cmd_str:
                return unittest.mock.Mock(returncode=0, stdout=f"task/{task_id}\n")
            if "rev-parse HEAD" in cmd_str:
                return unittest.mock.Mock(returncode=0, stdout=f"{'3' * 40}\n")
            if "pr view" in cmd_str:
                return unittest.mock.Mock(
                    returncode=0,
                    stdout=json.dumps({"headRefOid": "1" * 40}),
                )
            return unittest.mock.Mock(returncode=1, stdout="")

        with unittest.mock.patch("subprocess.run", side_effect=fake_run):
            resolved = ai_status.resolve_task_sha(task_id)
            self.assertEqual(resolved, "2" * 40)

    def test_resolve_task_sha_exact_length_validation(self) -> None:
        """Verify resolve_task_sha accepts exactly 40 or 64 hex chars and rejects 41, 63, or non-hex."""
        task_id = "TEST-SHA-LENGTH-VAL-001"

        sha_candidates = [
            ("a" * 40, True),   # 40 hex (SHA-1) -> valid
            ("f" * 64, True),   # 64 hex (SHA-256) -> valid
            ("b" * 41, False),  # 41 hex -> invalid
            ("c" * 63, False),  # 63 hex -> invalid
            ("g" * 40, False),  # 40 non-hex -> invalid
        ]

        for sha, is_valid in sha_candidates:
            ai_status.clear_ai_status_caches()

            def fake_ls_remote(cmd, current_sha=sha, **kwargs):
                cmd_str = " ".join(cmd)
                if "ls-remote --heads origin" in cmd_str:
                    remote_ref = f"refs/heads/task/{task_id}"
                    return unittest.mock.Mock(
                        returncode=0,
                        stdout=f"{current_sha}\t{remote_ref}\n",
                    )
                return unittest.mock.Mock(returncode=1, stdout="")

            with unittest.mock.patch("subprocess.run", side_effect=fake_ls_remote):
                resolved = ai_status.resolve_task_sha(task_id, force_refresh=True)
                if is_valid:
                    self.assertEqual(resolved, sha)
                else:
                    self.assertIsNone(resolved)





class ProcessQueueAgentOverrideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_test_config()

    def test_process_queue_with_agent_override_only_processes_matching_agent_queue_events(self) -> None:
        events = [
            {
                "event_id": "evt-codex6-1",
                "task_id": "TASK-CODEX6",
                "target_agent": "Codex6",
                "key": "evt-codex6-1",
                "message": "test message codex6",
            },
            {
                "event_id": "evt-antigravity6-1",
                "task_id": "TASK-ANTIGRAVITY6",
                "target_agent": "Antigravity6",
                "key": "evt-antigravity6-1",
                "message": "test message antigravity6",
            },
            {
                "event_id": "evt-empty-target",
                "task_id": "TASK-EMPTY",
                "target_agent": "",
                "key": "evt-empty-target",
            },
            {
                "event_id": "evt-malformed-target",
                "task_id": "TASK-MALFORMED",
                "target_agent": "!!!###",
                "key": "evt-malformed-target",
            },
            {
                "event_id": "evt-unknown-target",
                "task_id": "TASK-UNKNOWN",
                "target_agent": "NonExistentAgent999",
                "key": "evt-unknown-target",
            },
        ]
        status = {
            "tasks": [
                {"id": "TASK-CODEX6", "status": "in_progress", "owner": "Codex6", "reviewer": "Claude"},
                {"id": "TASK-ANTIGRAVITY6", "status": "in_progress", "owner": "Antigravity6", "reviewer": "Claude"},
                {"id": "TASK-EMPTY", "status": "in_progress", "owner": "Codex6", "reviewer": "Claude"},
                {"id": "TASK-MALFORMED", "status": "in_progress", "owner": "Codex6", "reviewer": "Claude"},
                {"id": "TASK-UNKNOWN", "status": "in_progress", "owner": "Codex6", "reviewer": "Claude"},
            ]
        }
        state = {
            "queue": {
                "events": {
                    "evt-codex6-1": {"status": "pending"},
                    "evt-antigravity6-1": {"status": "pending"},
                    "evt-empty-target": {"status": "pending"},
                    "evt-malformed-target": {"status": "pending"},
                    "evt-unknown-target": {"status": "pending"},
                }
            }
        }
        with mock.patch.object(supervisor, "load_event_queue", return_value=events), \
             mock.patch.object(supervisor, "load_status", return_value=status), \
             mock.patch.object(supervisor, "current_provider_dispatch_pause", return_value=None), \
             mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None), \
             mock.patch.object(supervisor, "prepare_worker_workspace", return_value=(True, "ok")), \
             mock.patch.object(supervisor, "start_worker_for_request", return_value=(True, "run-codex6-1", {})) as start_worker, \
             mock.patch.object(supervisor, "write_activity_log"):
            changed = supervisor.process_queue(
                self.config,
                state,
                provider_report={},
                agent_ids_override=["codex6"],
            )

        self.assertTrue(changed)
        self.assertEqual(state["queue"]["events"]["evt-codex6-1"]["status"], "started")
        self.assertEqual(state["queue"]["events"]["evt-antigravity6-1"]["status"], "pending")
        self.assertEqual(state["queue"]["events"]["evt-empty-target"]["status"], "pending")
        self.assertEqual(state["queue"]["events"]["evt-malformed-target"]["status"], "pending")
        self.assertEqual(state["queue"]["events"]["evt-unknown-target"]["status"], "pending")
        start_worker.assert_called_once()

    def test_process_queue_with_agent_override_skips_targetless_events_without_inference(self) -> None:
        events = [
            {
                "event_id": "evt-targetless-complete",
                "task_id": "TASK-TARGETLESS",
                "key": "evt-targetless-complete",
                "message": "targetless complete message",
            },
        ]
        status = {
            "tasks": [
                {"id": "TASK-TARGETLESS", "status": "in_progress", "owner": "Codex6", "reviewer": "Claude"},
            ]
        }
        state = {
            "queue": {
                "events": {
                    "evt-targetless-complete": {"status": "pending"},
                }
            }
        }
        with mock.patch.object(supervisor, "load_event_queue", return_value=events), \
             mock.patch.object(supervisor, "load_status", return_value=status), \
             mock.patch.object(supervisor, "build_request") as mock_build_req:
            changed = supervisor.process_queue(
                self.config,
                state,
                provider_report={},
                agent_ids_override=["codex6"],
            )

        self.assertFalse(changed)
        self.assertEqual(state["queue"]["events"]["evt-targetless-complete"]["status"], "pending")
        mock_build_req.assert_not_called()

    def test_post_merge_deleted_remote_branch_finalize_dispatch(self) -> None:
        """B1 regression: when origin branch task/<id> is deleted on PR merge,
        resolve_task_checkout_sha resolves the post-merge checkout HEAD, enabling
        supervisor priority dispatch and reconciliation gates to issue owned_finalize_dispatch.
        Composed and verified on dev base d37e6e5cfae0a4c936b121b363906a17739d293c."""
        approved_head = "b664a8ea9fed476c6224a339994fa66163c574fa"
        checkout_head = "80ba278631111111222222223333333344444444"
        task_id = "B1-TEST-POST-MERGE-001"
        task_item = {
            "id": task_id,
            "status": "review_approved",
            "owner": "Antigravity",
            "reviewer": "Antigravity4",
            "approved_head": approved_head,
        }
        task_map = {task_id: task_item}

        with mock.patch("ai_status.resolve_task_sha", return_value=None), \
             mock.patch("ai_status.is_approved_head_satisfied", return_value=True), \
             mock.patch("ai_status.run_git_command") as mock_git, \
             mock.patch("ai_status.task_pr_ci_status", return_value=("MERGED", "success")):
            def git_side_effect(cmd, **kwargs):
                if cmd[0] == "rev-parse" and cmd[1] == "HEAD":
                    return checkout_head
                if cmd[0] == "remote":
                    return "origin\n"
                if cmd[0] == "rev-parse" and cmd[1] == "--verify":
                    return checkout_head
                return ""
            mock_git.side_effect = git_side_effect

            # 1. Verify resolve_task_checkout_sha returns checkout_head despite resolve_task_sha returning None
            resolved = ai_status.resolve_task_checkout_sha(task_item, force_refresh=True)
            self.assertEqual(resolved, checkout_head)

            # 2. Verify dispatch_priority_for_task returns 1 (owned_finalize_dispatch priority)
            config = {"status_file": "/tmp/fake_status.json", "branch_workflow": {"dev_branch": "dev"}}
            prio = supervisor.dispatch_priority_for_task(config, task_item, "Antigravity", task_map=task_map)
            self.assertEqual(prio, 1)

            # 3. Verify dispatch_ready_tasks retains review_approved status and does not set approved_head_unresolved
            status = {"tasks": [task_item]}
            with mock.patch("supervisor.load_status", return_value=status), \
                 mock.patch("supervisor.load_event_queue", return_value=[]), \
                 mock.patch("supervisor.queue_delivery_event", return_value=False), \
                 mock.patch("supervisor.config_path", return_value="/tmp/fake_status.json"), \
                 mock.patch("supervisor.write_json"), \
                 mock.patch("supervisor.sync_status_pipeline"), \
                 mock.patch("supervisor.write_activity_log"):
                supervisor.dispatch_ready_tasks(config, {}, agent_ids_override=["antigravity"])
            self.assertEqual(task_item["status"], "review_approved")
            self.assertNotIn("Cannot verify branch HEAD", task_item.get("next", ""))
class QuarantineAndPreserveDirtyWorktreeTests(unittest.TestCase):

    def _create_git_repo_and_worktree(self, tmpdir_path: Path, task_id: str = "TASK-001") -> tuple[Path, Path, str]:
        repo_root = tmpdir_path / "main_repo"
        repo_root.mkdir()
        (repo_root / "ai-status.json").write_text("{}", encoding="utf-8")
        subprocess.run(["git", "init", "-b", "dev"], cwd=repo_root, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
        (repo_root / "README.md").write_text("main\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo_root, check=True)

        branch_name = f"task/{task_id}"
        subprocess.run(["git", "branch", branch_name], cwd=repo_root, check=True)

        wt_path = tmpdir_path / "workers" / supervisor._task_id_slug(task_id)
        wt_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "worktree", "add", str(wt_path), branch_name], cwd=repo_root, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=wt_path, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=wt_path, check=True)
        return repo_root, wt_path, branch_name

    def test_untracked_preservation_and_staged_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root, wt_path, branch_name = self._create_git_repo_and_worktree(tmp_p, "TASK-REAL-001")

            staged_file = wt_path / "staged_doc.txt"
            staged_file.write_text("staged content", encoding="utf-8")
            subprocess.run(["git", "add", "staged_doc.txt"], cwd=wt_path, check=True)

            unstaged_file = wt_path / "README.md"
            unstaged_file.write_text("modified main\n", encoding="utf-8")

            untracked_file = wt_path / "untracked_output.log"
            untracked_file.write_text("untracked context data", encoding="utf-8")

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
            }
            state: dict = {}

            ok = supervisor._quarantine_and_preserve_dirty_worktree(
                config,
                state,
                wt_path,
                "TASK-REAL-001",
                expected_branch=branch_name,
                run_id=None,
                trigger="unit_test",
            )

            self.assertTrue(ok)

            st_proc = subprocess.run(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=wt_path, capture_output=True, text=True, check=True)
            self.assertIn("A  staged_doc.txt", st_proc.stdout)
            self.assertIn("?? untracked_output.log", st_proc.stdout)

            self.assertTrue(staged_file.exists())
            self.assertEqual("staged content", staged_file.read_text(encoding="utf-8"))
            self.assertTrue(untracked_file.exists())
            self.assertEqual("untracked context data", untracked_file.read_text(encoding="utf-8"))

            backups_dir = repo_root / ".orchestrator" / "worktree-dirt-backups"
            self.assertTrue(backups_dir.exists())
            task_backups = list(backups_dir.glob("task-real-001-*"))
            self.assertEqual(1, len(task_backups))
            b_dir = task_backups[0]
            manifest_file = b_dir / "manifest.json"
            self.assertTrue(manifest_file.exists())
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            self.assertEqual("TASK-REAL-001", manifest["task_id"])
            paths_in_manifest = [f["path"] for f in manifest["files"]]
            self.assertIn("staged_doc.txt", paths_in_manifest)
            self.assertIn("untracked_output.log", paths_in_manifest)
            self.assertTrue((b_dir / "untracked" / "untracked_output.log").exists())
            self.assertTrue((b_dir / "backup_checksums.sha256").exists())

    def test_backup_failure_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root, wt_path, branch_name = self._create_git_repo_and_worktree(tmp_p, "TASK-FAIL-001")

            untracked_file = wt_path / "untracked_data.txt"
            untracked_file.write_text("important data", encoding="utf-8")

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
            }
            state: dict = {}

            with mock.patch("shutil.copy2", side_effect=OSError("Disk full")):
                ok = supervisor._quarantine_and_preserve_dirty_worktree(
                    config,
                    state,
                    wt_path,
                    "TASK-FAIL-001",
                    expected_branch=branch_name,
                    run_id=None,
                    trigger="unit_test",
                )

            self.assertFalse(ok)
            st_proc = subprocess.run(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=wt_path, capture_output=True, text=True, check=True)
            self.assertIn("untracked_data.txt", st_proc.stdout)
            self.assertTrue(untracked_file.exists())

    def test_ref_and_branch_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root, wt_path, branch_name = self._create_git_repo_and_worktree(tmp_p, "TASK-MISMATCH-001")

            (wt_path / "file.txt").write_text("dirt", encoding="utf-8")

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
            }
            state: dict = {}

            ok = supervisor._quarantine_and_preserve_dirty_worktree(
                config,
                state,
                wt_path,
                "TASK-MISMATCH-001",
                expected_branch="task/OTHER-BRANCH",
                run_id=None,
                trigger="unit_test",
            )
            self.assertFalse(ok)

    def test_active_run_exclusion_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root, wt_path, branch_name = self._create_git_repo_and_worktree(tmp_p, "TASK-ACTIVE-001")

            (wt_path / "file.txt").write_text("dirt", encoding="utf-8")

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
            }
            state = {
                "workers": {
                    "w1": {
                        "run_id": "run-active-123",
                        "task_id": "TASK-ACTIVE-001",
                        "status": "running",
                        "workspace_path": str(wt_path),
                    }
                }
            }

            ok = supervisor._quarantine_and_preserve_dirty_worktree(
                config,
                state,
                wt_path,
                "TASK-ACTIVE-001",
                expected_branch=branch_name,
                run_id=None,
                trigger="lease_recovery",
            )
            self.assertFalse(ok)

            ok_self = supervisor._quarantine_and_preserve_dirty_worktree(
                config,
                state,
                wt_path,
                "TASK-ACTIVE-001",
                expected_branch=branch_name,
                run_id="run-active-123",
                trigger="worker_failed",
            )
            self.assertTrue(ok_self)



    @unittest.skip("dirty worktrees now block dispatch; the fresh recovery lease was intentionally removed")
    def test_prepare_worker_workspace_recovers_dirty_worktree_lease_with_real_bare_remote(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            remote_root = tmp_p / "remote.git"
            subprocess.run(["git", "init", "--bare", str(remote_root)], capture_output=True, check=True)

            repo_root = tmp_p / "main_repo"
            subprocess.run(["git", "clone", str(remote_root), str(repo_root)], capture_output=True, check=True)
            (repo_root / "ai-status.json").write_text("{}", encoding="utf-8")
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "-b", "dev"], cwd=repo_root, check=True)
            (repo_root / "README.md").write_text("main\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo_root, check=True)
            subprocess.run(["git", "push", "origin", "dev"], cwd=repo_root, check=True)

            task_id = "TASK-REMOTE-BARE-001"
            branch_name = f"task/{task_id}"
            subprocess.run(["git", "checkout", "-b", branch_name], cwd=repo_root, check=True)
            subprocess.run(["git", "push", "origin", branch_name], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "dev"], cwd=repo_root, check=True)

            wt_path = tmp_p / "workers" / supervisor._task_id_slug(task_id)
            wt_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "worktree", "add", str(wt_path), branch_name], capture_output=True, check=True, cwd=repo_root)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=wt_path, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=wt_path, check=True)

            # Create an unpushed local commit. Recovery must lease the immutable
            # remote task head, never this newer mutable local branch ref.
            (wt_path / "local_only.txt").write_text("unpushed local commit\n", encoding="utf-8")
            subprocess.run(["git", "add", "local_only.txt"], cwd=wt_path, check=True)
            subprocess.run(["git", "commit", "-m", "local unpushed task commit"], cwd=wt_path, check=True)
            local_unpushed_head = supervisor._git_commit_oid(wt_path, "HEAD")

            # Introduce uncommitted dirty changes into the reused worker worktree
            dirty_file = wt_path / "dirty_work.txt"
            dirty_file.write_text("uncommitted progress\n", encoding="utf-8")
            subprocess.run(["git", "add", "dirty_work.txt"], cwd=wt_path, check=True)

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(tmp_p / "workers"),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            state: dict = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id=task_id,
                reason="owned_in_progress_dispatch",
            )

            # Execute real prepare_worker_workspace without mocking refresh or quarantine
            ok, message = supervisor.prepare_worker_workspace(
                config,
                state,
                request,
                queue_event_id="evt-remote-recover",
                target_agent="Codex",
            )

            self.assertTrue(ok)
            self.assertIsNone(message)

            leased_path = Path(request.metadata["workspace_path"])
            self.assertNotEqual(leased_path, wt_path)

            # Confirm original worktree wt_path is untouched and still dirty
            self.assertTrue(dirty_file.exists())
            st_orig = subprocess.run(["git", "status", "--porcelain=v1"], cwd=wt_path, capture_output=True, text=True, check=True)
            self.assertTrue(st_orig.stdout.strip())

            # Confirm leased fresh worktree is clean
            st_leased = subprocess.run(["git", "status", "--porcelain=v1"], cwd=leased_path, capture_output=True, text=True, check=True)
            self.assertEqual("", st_leased.stdout.strip())

            # Confirm bare remote task branch was NOT polluted with dirty commits
            remote_task_head = subprocess.run(
                ["git", "rev-parse", f"refs/heads/{branch_name}"],
                cwd=remote_root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            leased_head = supervisor._git_commit_oid(leased_path, "HEAD")
            self.assertEqual(leased_head, remote_task_head)
            self.assertNotEqual(leased_head, local_unpushed_head)

            # Confirm backup manifest exists
            backups_dir = repo_root / ".orchestrator" / "worktree-dirt-backups"
            self.assertTrue(backups_dir.exists())
            self.assertGreater(len(list(backups_dir.glob("task-remote-bare-001-*"))), 0)

    @unittest.skip("dirty worktrees now block dispatch; the fresh recovery lease was intentionally removed")
    def test_rejected_push_has_no_head_mismatch_and_no_unknown_publication(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            remote_root = tmp_p / "remote.git"
            subprocess.run(["git", "init", "--bare", str(remote_root)], capture_output=True, check=True)

            repo_root = tmp_p / "main_repo"
            subprocess.run(["git", "clone", str(remote_root), str(repo_root)], capture_output=True, check=True)
            (repo_root / "ai-status.json").write_text("{}", encoding="utf-8")
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "-b", "dev"], cwd=repo_root, check=True)
            (repo_root / "README.md").write_text("main\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo_root, check=True)
            subprocess.run(["git", "push", "origin", "dev"], cwd=repo_root, check=True)

            task_id = "TASK-PUSH-REJECT-001"
            branch_name = f"task/{task_id}"
            subprocess.run(["git", "checkout", "-b", branch_name], cwd=repo_root, check=True)
            subprocess.run(["git", "push", "origin", branch_name], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "dev"], cwd=repo_root, check=True)

            # Configure pre-receive hook to reject all subsequent pushes
            hook_file = remote_root / "hooks" / "pre-receive"
            hook_file.write_text("#!/bin/sh\necho 'Push rejected' >&2\nexit 1\n", encoding="utf-8")
            hook_file.chmod(0o755)

            wt_path = tmp_p / "workers" / supervisor._task_id_slug(task_id)
            wt_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "worktree", "add", str(wt_path), branch_name], capture_output=True, check=True, cwd=repo_root)

            # Add dirt to worktree
            (wt_path / "secret.txt").write_text("abandoned dirty secret\n", encoding="utf-8")

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(tmp_p / "workers"),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            state: dict = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id=task_id,
                reason="owned_in_progress_dispatch",
            )

            ok, message = supervisor.prepare_worker_workspace(
                config,
                state,
                request,
                queue_event_id="evt-push-reject",
                target_agent="Codex",
            )
            self.assertTrue(ok)
            self.assertIsNone(message)

            leased_path = Path(request.metadata["workspace_path"])
            st_proc = subprocess.run(["git", "status", "--porcelain=v1"], cwd=leased_path, capture_output=True, text=True, check=True)
            self.assertEqual("", st_proc.stdout.strip())

            ref_ok, ref_status = supervisor._refresh_reused_worker_worktree(repo_root, leased_path, "origin/dev", branch_name)
            self.assertTrue(ref_ok)
            self.assertNotEqual("task_head_mismatch", ref_status)



    def test_context_materialization_ephemeral_and_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root = tmp_p / "repo"
            subprocess.run(["git", "init", str(repo_root)], capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
            (repo_root / "ai-status.json").write_text("{}", encoding="utf-8")
            (repo_root / "README.md").write_text("hello\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "-b", "dev"], cwd=repo_root, check=True)
            wt_path = tmp_p / "wt"
            subprocess.run(["git", "worktree", "add", "-b", "task/EPHEM-001", str(wt_path), "dev"], cwd=repo_root, capture_output=True, check=True)

            config = {"paths": {"status_file": str(repo_root / "ai-status.json")}}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="EPHEM-001",
                reason="owned_in_progress_dispatch",
                context_files=["AI_COLLABORATION_GUIDE.md", ".orchestrator/task-briefs/ephem_001.md"],
            )

            materialized = supervisor.materialize_worker_context_files(config, request, wt_path)
            self.assertIn("AI_COLLABORATION_GUIDE.md", materialized)
            self.assertIn(".orchestrator/task-briefs/ephem_001.md", materialized)

            # Confirm git exclude path was updated via rev-parse
            rc, out = supervisor._git_output(wt_path, "rev-parse", "--git-path", "info/exclude")
            self.assertEqual(rc, 0)
            ex_path = Path(out.strip())
            if not ex_path.is_absolute():
                ex_path = (wt_path / ex_path).resolve()
            ex_content = ex_path.read_text(encoding="utf-8")
            self.assertIn("AI_COLLABORATION_GUIDE.md", ex_content)
            self.assertIn(".orchestrator/task-briefs/", ex_content)

            # Confirm dirt classification treats context files as clean or scratch_only
            st_proc = subprocess.run(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=wt_path, capture_output=True, check=True)
            classification, _paths = supervisor._classify_worktree_dirt(st_proc.stdout)
            self.assertIn(classification, ("clean", "scratch_only"))

            # Add an unknown user file in task-briefs directory
            unknown_user_file = wt_path / ".orchestrator" / "task-briefs" / "my_custom_notes.txt"
            unknown_user_file.write_text("custom user notes\n", encoding="utf-8")

            # The shared policy is observational: it must never delete an
            # unknown owner file while classifying a reusable workspace.
            self.assertTrue(unknown_user_file.exists())
            self.assertEqual(unknown_user_file.read_text(encoding="utf-8"), "custom user notes\n")

    @unittest.skip("dirty worktrees now block dispatch; the fresh recovery lease was intentionally removed")
    def test_original_worktree_byte_branch_gitdir_identity_on_lease_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root = tmp_p / "repo"
            subprocess.run(["git", "init", str(repo_root)], capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
            (repo_root / "ai-status.json").write_text("{}", encoding="utf-8")
            (repo_root / "README.md").write_text("base content\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_root, check=True)

            task_id = "TASK-IDENTITY-001"
            branch_name = f"task/{task_id}"
            subprocess.run(["git", "checkout", "-b", branch_name], cwd=repo_root, check=True)
            (repo_root / "task_code.py").write_text("print('task')\n", encoding="utf-8")
            subprocess.run(["git", "add", "task_code.py"], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "task commit"], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "master"], cwd=repo_root, check=True)

            wt_path = tmp_p / "workers" / supervisor._task_id_slug(task_id)
            wt_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "worktree", "add", str(wt_path), branch_name], capture_output=True, check=True, cwd=repo_root)

            # Introduce dirty changes into wt_path
            dirty_f = wt_path / "dirty_progress.py"
            dirty_f.write_text("import sys\n# work in progress\n", encoding="utf-8")
            subprocess.run(["git", "add", "dirty_progress.py"], cwd=wt_path, check=True)

            # Snapshot original bytes, branch, .git file, gitdir before recovery
            orig_git_file_content = (wt_path / ".git").read_text(encoding="utf-8")
            orig_dirty_content = dirty_f.read_text(encoding="utf-8")
            orig_branch = supervisor._git_output(wt_path, "symbolic-ref", "--quiet", "--short", "HEAD")[1]

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "master"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(tmp_p / "workers"),
                    "base_ref": "master",
                    "reuse_existing": True,
                },
            }
            state: dict = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id=task_id,
                reason="owned_in_progress_dispatch",
            )

            ok, message = supervisor.prepare_worker_workspace(
                config,
                state,
                request,
                queue_event_id="evt-identity",
                target_agent="Codex",
            )

            self.assertTrue(ok)
            self.assertIsNone(message)

            leased_path = Path(request.metadata["workspace_path"])
            self.assertNotEqual(leased_path, wt_path)

            # Verify original wt_path bytes, branch, .git file content are 100% byte-identical
            self.assertEqual((wt_path / ".git").read_text(encoding="utf-8"), orig_git_file_content)
            self.assertEqual(dirty_f.read_text(encoding="utf-8"), orig_dirty_content)
            self.assertEqual(supervisor._git_output(wt_path, "symbolic-ref", "--quiet", "--short", "HEAD")[1], orig_branch)

            # Verify leased_path is clean and at exact task HEAD
            st_leased = subprocess.run(["git", "status", "--porcelain=v1"], cwd=leased_path, capture_output=True, text=True, check=True)
            self.assertEqual("", st_leased.stdout.strip())
            self.assertEqual(supervisor._git_commit_oid(leased_path, "HEAD"), supervisor._git_commit_oid(repo_root, branch_name))

    @unittest.skip("dirty worktrees now block dispatch; the fresh recovery lease was intentionally removed")
    def test_dirty_lease_recovery_preserves_exact_task_sha_without_dev_merge(self) -> None:
        """B1 regression test: lease recovery preserves exact task SHA without merging origin/dev base."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root = tmp_p / "repo"
            subprocess.run(["git", "init", str(repo_root)], capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
            (repo_root / "ai-status.json").write_text("{}", encoding="utf-8")
            (repo_root / "base.txt").write_text("initial base\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "-b", "dev"], cwd=repo_root, check=True)

            task_id = "TASK-B1-001"
            branch_name = f"task/{task_id}"
            subprocess.run(["git", "checkout", "-b", branch_name], cwd=repo_root, check=True)
            (repo_root / "task.txt").write_text("task work\n", encoding="utf-8")
            subprocess.run(["git", "add", "task.txt"], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "task commit"], cwd=repo_root, check=True)
            task_sha = supervisor._git_commit_oid(repo_root, branch_name)
            self.assertIsNotNone(task_sha)

            # Advance dev branch with new commits so task_sha becomes an ancestor of dev
            subprocess.run(["git", "checkout", "dev"], cwd=repo_root, check=True)
            (repo_root / "dev_advance.txt").write_text("dev advanced\n", encoding="utf-8")
            subprocess.run(["git", "add", "dev_advance.txt"], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "dev advance commit"], cwd=repo_root, check=True)
            dev_sha = supervisor._git_commit_oid(repo_root, "dev")
            self.assertNotEqual(task_sha, dev_sha)

            # Create existing dirty worktree for task branch
            wt_path = tmp_p / "workers" / supervisor._task_id_slug(task_id)
            wt_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "worktree", "add", str(wt_path), branch_name], capture_output=True, check=True, cwd=repo_root)
            (wt_path / "dirty.txt").write_text("dirty uncommitted work\n", encoding="utf-8")

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(tmp_p / "workers"),
                    "base_ref": "dev",
                    "reuse_existing": True,
                },
            }
            state: dict = {}
            request = supervisor.DeliveryRequest(
                agent_id="antigravity",
                provider="antigravity",
                delivery_mode="antigravity",
                message="wake",
                task_id=task_id,
                reason="owned_in_progress_dispatch",
            )

            ok, message = supervisor.prepare_worker_workspace(
                config, state, request, queue_event_id="evt-b1", target_agent="Antigravity"
            )
            self.assertTrue(ok)
            leased_path = Path(request.metadata["workspace_path"])
            leased_sha = supervisor._git_commit_oid(leased_path, "HEAD")

            # B1 check: fresh HEAD must be exact task_sha, NOT dev_sha
            self.assertEqual(leased_sha, task_sha)
            self.assertNotEqual(leased_sha, dev_sha)

    def test_materialized_context_written_to_actual_git_exclude_path(self) -> None:
        """B2 regression test: context exclusions written to git rev-parse --git-path info/exclude."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root = tmp_p / "repo"
            subprocess.run(["git", "init", str(repo_root)], capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
            (repo_root / "ai-status.json").write_text("{}", encoding="utf-8")
            (repo_root / "README.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "-b", "dev"], cwd=repo_root, check=True)

            wt_path = tmp_p / "wt"
            subprocess.run(["git", "worktree", "add", "-b", "task/EPHEM-B2", str(wt_path), "dev"], capture_output=True, check=True, cwd=repo_root)

            config = {"paths": {"status_file": str(repo_root / "ai-status.json")}}
            request = supervisor.DeliveryRequest(
                agent_id="antigravity",
                provider="antigravity",
                delivery_mode="antigravity",
                message="wake",
                task_id="EPHEM-B2",
                reason="owned_in_progress_dispatch",
                context_files=["AI_COLLABORATION_GUIDE.md", ".orchestrator/task-briefs/ephem_b2.md"],
            )

            supervisor.materialize_worker_context_files(config, request, wt_path)

            # B2 check: git check-ignore must return 0 (ignored) for materialized context
            chk_proc = subprocess.run(["git", "check-ignore", "AI_COLLABORATION_GUIDE.md"], cwd=wt_path, capture_output=True, check=False)
            self.assertEqual(chk_proc.returncode, 0)

            # Confirm git status does NOT report AI_COLLABORATION_GUIDE.md as untracked
            st_proc = subprocess.run(["git", "status", "--porcelain=v1"], cwd=wt_path, capture_output=True, text=True, check=True)
            self.assertNotIn("AI_COLLABORATION_GUIDE.md", st_proc.stdout)

    @unittest.skip("dirty worktrees now block dispatch; the fresh recovery lease was intentionally removed")
    def test_tracked_owner_content_classified_real_dirt_and_preserved(self) -> None:
        """B3 regression test: modified tracked context files classified as real dirt, quarantined, and recovered cleanly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root = tmp_p / "repo"
            subprocess.run(["git", "init", str(repo_root)], capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
            (repo_root / "ai-status.json").write_text("{}", encoding="utf-8")
            (repo_root / "ai-activity-log.jsonl").write_text('{"event":"baseline"}\n', encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "-b", "dev"], cwd=repo_root, check=True)

            task_id = "B3-001"
            branch_name = f"task/{task_id}"
            wt_path = tmp_p / "workers" / supervisor._task_id_slug(task_id)
            wt_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "worktree", "add", "-b", branch_name, str(wt_path), "dev"], capture_output=True, check=True, cwd=repo_root)

            # Modify tracked ai-activity-log.jsonl (simulating owner progress)
            owner_progress_text = '{"event":"baseline"}\n{"event":"owner_progress"}\n'
            (wt_path / "ai-activity-log.jsonl").write_text(owner_progress_text, encoding="utf-8")

            # B3 check 1: dirt classification for tracked context file MUST be real
            st_proc = subprocess.run(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=wt_path, capture_output=True, check=True)
            classification, paths = supervisor._classify_worktree_dirt(st_proc.stdout)
            self.assertEqual(classification, "real")
            self.assertEqual(paths, [])

            # B3 check 2: end-to-end prepare_worker_workspace interaction
            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(tmp_p / "workers"),
                    "base_ref": "dev",
                    "reuse_existing": True,
                },
            }
            state: dict = {}
            request = supervisor.DeliveryRequest(
                agent_id="antigravity",
                provider="antigravity",
                delivery_mode="antigravity",
                message="wake",
                task_id=task_id,
                reason="owned_in_progress_dispatch",
                context_files=["ai-status.json", "ai-activity-log.jsonl"],
            )

            ok, message = supervisor.prepare_worker_workspace(
                config, state, request, queue_event_id="evt-b3", target_agent="Antigravity"
            )
            self.assertTrue(ok)
            self.assertIsNone(message)

            leased_path = Path(request.metadata["workspace_path"])
            # Fresh clean workspace must be allocated at a distinct path
            self.assertNotEqual(leased_path, wt_path)

            # Original dirty worktree bytes must remain 100% byte-identical and untouched
            self.assertEqual((wt_path / "ai-activity-log.jsonl").read_text(encoding="utf-8"), owner_progress_text)

    def test_materialization_refuses_to_overwrite_tracked_files_with_differing_source_bytes(self) -> None:
        """B4 regression test: materialization refuses to overwrite any Git-tracked destination even when live source differs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root = tmp_p / "repo"
            subprocess.run(["git", "init", str(repo_root)], capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)

            # Create tracked files on dev: ai-status.json, ai-activity-log.jsonl, and a task brief
            (repo_root / "ai-status.json").write_text('{"project":"tracked_baseline"}\n', encoding="utf-8")
            (repo_root / "ai-activity-log.jsonl").write_text('{"event":"tracked_log_baseline"}\n', encoding="utf-8")
            tb_dir = repo_root / ".orchestrator" / "task-briefs"
            tb_dir.mkdir(parents=True, exist_ok=True)
            tracked_brief = tb_dir / "b4_task_001.md"
            tracked_brief.write_text("# Tracked Brief Baseline\n", encoding="utf-8")
            (repo_root / "README.md").write_text("base readme\n", encoding="utf-8")

            subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "initial tracked baseline"], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "-b", "dev"], cwd=repo_root, check=True)

            task_id = "B4-TASK-001"
            branch_name = f"task/{task_id}"
            subprocess.run(["git", "checkout", "-b", branch_name], cwd=repo_root, check=True)

            # Now modify live status_root files (repo_root) to differ from the tracked baseline
            (repo_root / "ai-activity-log.jsonl").write_text('{"event":"live_canonical_log_differs"}\n', encoding="utf-8")
            tracked_brief.write_text("# Live Brief Modified Bytes\n", encoding="utf-8")
            (repo_root / "AI_COLLABORATION_GUIDE.md").write_text("# Live Collaboration Guide\n", encoding="utf-8")

            # Checkout dev in repo_root so task branch can be checked out in worktree
            subprocess.run(["git", "checkout", "dev"], cwd=repo_root, check=True)

            wt_path = tmp_p / "wt_b4"
            subprocess.run(["git", "worktree", "add", str(wt_path), branch_name], capture_output=True, check=True, cwd=repo_root)

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
            }
            request = supervisor.DeliveryRequest(
                agent_id="antigravity",
                provider="antigravity",
                delivery_mode="antigravity",
                message="wake",
                task_id=task_id,
                reason="owned_in_progress_dispatch",
                context_files=[
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-activity-log.jsonl",
                    ".orchestrator/task-briefs/b4_task_001.md",
                    "ai-status.json",
                ],
            )

            materialized = supervisor.materialize_worker_context_files(config, request, wt_path)

            # 1. Untracked file AI_COLLABORATION_GUIDE.md was materialized
            self.assertIn("AI_COLLABORATION_GUIDE.md", materialized)

            # 2. Tracked files MUST NOT be in materialized list, and MUST NOT be overwritten
            self.assertNotIn("ai-activity-log.jsonl", materialized)
            self.assertNotIn(".orchestrator/task-briefs/b4_task_001.md", materialized)
            self.assertNotIn("ai-status.json", materialized)

            self.assertEqual((wt_path / "ai-activity-log.jsonl").read_text(encoding="utf-8"), '{"event":"tracked_log_baseline"}\n')
            self.assertEqual((wt_path / ".orchestrator" / "task-briefs" / "b4_task_001.md").read_text(encoding="utf-8"), "# Tracked Brief Baseline\n")
            self.assertEqual((wt_path / "ai-status.json").read_text(encoding="utf-8"), '{"project":"tracked_baseline"}\n')

            # 3. git status MUST remain 100% clean after materialization
            st_proc = subprocess.run(["git", "status", "--porcelain=v1"], cwd=wt_path, capture_output=True, text=True, check=True)
            self.assertEqual("", st_proc.stdout.strip())

    def test_materialize_worker_context_files_skips_symlinks_and_unsafe_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root = tmp_p / "main_repo"
            repo_root.mkdir()
            (repo_root / "ai-status.json").write_text('{"project":"canonical"}', encoding="utf-8")

            wt_path = tmp_p / "wt_b5"
            wt_path.mkdir()

            # Create target file README.md
            readme = wt_path / "README.md"
            readme.write_text("original owner readme content\n", encoding="utf-8")

            # Create untracked symlink ai-status.json -> README.md
            symlink = wt_path / "ai-status.json"
            symlink.symlink_to("README.md")

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
            }
            request = supervisor.DeliveryRequest(
                agent_id="antigravity",
                provider="antigravity",
                delivery_mode="antigravity",
                message="wake",
                task_id="TASK-B5-001",
                reason="owned_in_progress_dispatch",
                context_files=["ai-status.json"],
            )

            materialized = supervisor.materialize_worker_context_files(config, request, wt_path)

            self.assertNotIn("ai-status.json", materialized)
            self.assertEqual(readme.read_text(encoding="utf-8"), "original owner readme content\n")
            self.assertTrue(os.path.islink(symlink))

    def test_materialize_worker_context_files_replaces_hardlink_without_mutating_tracked_inode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            status_root = tmp_p / "status_root"
            status_root.mkdir()
            canonical = b'{"project":"canonical"}\n'
            (status_root / "ai-status.json").write_bytes(canonical)

            wt_path = tmp_p / "worker"
            wt_path.mkdir()
            subprocess.run(["git", "init"], cwd=wt_path, capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=wt_path, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=wt_path, check=True)
            readme = wt_path / "README.md"
            owner_bytes = b"tracked owner bytes\n"
            readme.write_bytes(owner_bytes)
            subprocess.run(["git", "add", "README.md"], cwd=wt_path, check=True)
            subprocess.run(["git", "commit", "-m", "tracked baseline"], cwd=wt_path, capture_output=True, check=True)

            destination = wt_path / "ai-status.json"
            os.link(readme, destination)
            self.assertEqual(readme.stat().st_ino, destination.stat().st_ino)

            config = {
                "paths": {
                    "status_file": str(status_root / "ai-status.json"),
                    "activity_log": str(status_root / "ai-activity-log.jsonl"),
                },
            }
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="TASK-HARDLINK-001",
                reason="owned_in_progress_dispatch",
                context_files=["ai-status.json"],
            )

            materialized = supervisor.materialize_worker_context_files(config, request, wt_path)

            self.assertIn("ai-status.json", materialized)
            self.assertEqual(readme.read_bytes(), owner_bytes)
            self.assertEqual(destination.read_bytes(), canonical)
            self.assertNotEqual(readme.stat().st_ino, destination.stat().st_ino)
            diff = subprocess.run(
                ["git", "diff", "--exit-code", "--", "README.md"],
                cwd=wt_path,
                capture_output=True,
                check=False,
            )
            self.assertEqual(diff.returncode, 0, diff.stdout.decode(errors="replace"))

    @unittest.skip("dirty worktrees now block dispatch; the fresh recovery lease was intentionally removed")
    def test_prepare_worker_workspace_recovers_dirty_worktree_lease_with_untracked_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            remote_root = tmp_p / "remote.git"
            subprocess.run(["git", "init", "--bare", str(remote_root)], capture_output=True, check=True)

            repo_root = tmp_p / "main_repo"
            subprocess.run(["git", "clone", str(remote_root), str(repo_root)], capture_output=True, check=True)
            (repo_root / "ai-status.json").write_text('{"project":"canonical"}', encoding="utf-8")
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "-b", "dev"], cwd=repo_root, check=True)
            (repo_root / "README.md").write_text("main owner content\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo_root, check=True)
            subprocess.run(["git", "push", "origin", "dev"], cwd=repo_root, check=True)

            task_id = "TASK-B5-LEASE-001"
            branch_name = f"task/{task_id}"
            subprocess.run(["git", "checkout", "-b", branch_name], cwd=repo_root, check=True)
            subprocess.run(["git", "push", "origin", branch_name], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "dev"], cwd=repo_root, check=True)

            wt_path = tmp_p / "workers" / supervisor._task_id_slug(task_id)
            wt_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "worktree", "add", str(wt_path), branch_name], capture_output=True, check=True, cwd=repo_root)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=wt_path, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=wt_path, check=True)

            # Introduce an untracked symlink ai-status.json -> README.md into the reused worktree
            symlink = wt_path / "ai-status.json"
            symlink.symlink_to("README.md")

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(tmp_p / "workers"),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            state: dict = {}
            request = supervisor.DeliveryRequest(
                agent_id="antigravity",
                provider="antigravity",
                delivery_mode="antigravity",
                message="wake",
                task_id=task_id,
                reason="owned_in_progress_dispatch",
                context_files=["ai-status.json"],
            )

            ok, message = supervisor.prepare_worker_workspace(
                config,
                state,
                request,
                queue_event_id="evt-b5-recover",
                target_agent="antigravity",
            )
            self.assertTrue(ok)
            self.assertIsNone(message)

            leased_path = Path(request.metadata["workspace_path"])
            # Fresh clean workspace allocated at a distinct path
            self.assertNotEqual(leased_path, wt_path)

            # Original dirty worktree at wt_path remains 100% byte-identical and untouched
            self.assertEqual((wt_path / "README.md").read_text(encoding="utf-8"), "main owner content\n")
            self.assertTrue(os.path.islink(wt_path / "ai-status.json"))

            # Fresh recovery workspace has regular materialized ai-status.json
            self.assertTrue(leased_path.exists())
            self.assertFalse(os.path.islink(leased_path / "ai-status.json"))
            self.assertEqual((leased_path / "ai-status.json").read_text(encoding="utf-8"), '{"project":"canonical"}')

    def test_a_clean_worktree_is_reported_as_clean_not_unreadable(self) -> None:
        """`git status` succeeding with no output means nothing to preserve.

        That took the same branch as `git status` failing, and both reported
        `status_unreadable`. The one time the event fired, two sessions read
        "No uncommitted work preserved ... status_unreadable" as lost work and
        reported it as such. The routine outcome has to be distinguishable
        from the failure it is not.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root, wt_path, branch_name = self._create_git_repo_and_worktree(tmp_p, "TASK-CLEAN-001")

            status = subprocess.run(
                ["git", "status", "--porcelain=v1", "--untracked-files=all"],
                cwd=wt_path,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(status.stdout.strip(), "", "the fixture worktree must start clean")

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
            }

            outcome = supervisor._quarantine_and_preserve_dirty_worktree(
                config,
                {},
                wt_path,
                "TASK-CLEAN-001",
                expected_branch=branch_name,
                run_id=None,
                trigger="unit_test",
            )

            self.assertFalse(outcome, "a clean worktree preserves nothing")
            self.assertEqual(getattr(outcome, "reason", ""), "worktree_clean")
class BlockedTaskRoleReassignmentTests(unittest.TestCase):
    """A blocked owner strands a task the same way a blocked reviewer does.

    Only the reviewer half was covered, so a task whose owner had run out of
    quota just waited: nothing reassigns an owner. Observed on
    ODP-ORCH-BRANCH-DRIFT-ALARMS-001, stuck 9 hours at review_approved.
    """

    def _config(self) -> dict:
        return {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "reviewer_failover": {"enabled": True},
                "review_statuses": ["review"],
                "finalize_statuses": ["review_approved"],
                "owned_statuses": ["in_progress", "todo"],
                "active_worker_statuses": ["running"],
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "antigravity": {
                    "id": "antigravity",
                    "display_name": "Antigravity",
                    "provider": "antigravity",
                },
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }

    def _state_with_codex_quota_paused(self) -> dict:
        return {
            "queue": {"events": {}},
            "workers": {},
            "provider_guardrails": {
                "dispatch_pauses": {
                    "codex": {
                        "provider": "codex",
                        "blocked_until": "2099-01-01T00:00:00Z",
                        "reason": "Codex usage limit reached",
                    }
                }
            },
        }

    def _run(self, status: dict, config: dict | None = None) -> mock.Mock:
        cfg = config or self._config()
        with (
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(
                supervisor, "outstanding_delivery_indexes", return_value=(set(), set(), set())
            ),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "console_log"),
        ):
            supervisor.reassign_unavailable_reviewers(
                cfg, self._state_with_codex_quota_paused(), status
            )
        return persist

    def test_blocked_owner_at_finalize_is_reassigned(self) -> None:
        status = {
            "tasks": [
                {"id": "T-1", "status": "review_approved", "owner": "Codex", "reviewer": "Antigravity"}
            ]
        }

        persist = self._run(status)

        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "T-1")
        self.assertEqual(kwargs["new_owner"], "Claude")
        self.assertEqual(kwargs["new_reviewer"], "Antigravity")
        self.assertEqual(kwargs["handoff_from"], "Codex")

    def test_blocked_owner_in_progress_is_reassigned(self) -> None:
        status = {
            "tasks": [{"id": "T-2", "status": "in_progress", "owner": "Codex", "reviewer": "Antigravity"}]
        }

        persist = self._run(status)

        persist.assert_called_once()
        self.assertEqual(persist.call_args.kwargs["new_owner"], "Claude")

    def test_blocked_reviewer_at_review_still_reassigns_the_reviewer(self) -> None:
        status = {
            "tasks": [{"id": "T-3", "status": "review", "owner": "Antigravity", "reviewer": "Codex"}]
        }

        persist = self._run(status)

        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "T-3")
        self.assertEqual(kwargs["new_owner"], "Antigravity")
        self.assertEqual(kwargs["new_reviewer"], "Claude")
        self.assertEqual(kwargs["handoff_from"], "Codex")

    def test_healthy_owner_is_left_alone(self) -> None:
        status = {
            "tasks": [
                {"id": "T-4", "status": "review_approved", "owner": "Antigravity", "reviewer": "Claude"}
            ]
        }

        self._run(status).assert_not_called()

    def test_blocked_owner_at_review_is_not_reassigned(self) -> None:
        """At review the reviewer owes the work, so a blocked owner is not the blocker."""
        status = {
            "tasks": [{"id": "T-5", "status": "review", "owner": "Codex", "reviewer": "Antigravity"}]
        }

        self._run(status).assert_not_called()

    def test_failover_disabled_leaves_tasks_alone(self) -> None:
        config = self._config()
        config["ready_dispatcher"]["reviewer_failover"]["enabled"] = False
        status = {
            "tasks": [
                {"id": "T-6", "status": "review_approved", "owner": "Codex", "reviewer": "Antigravity"}
            ]
        }

        self._run(status, config=config).assert_not_called()

    def test_shared_pool_reviewer_is_reassigned_to_independent_reviewer(self) -> None:
        config = self._config()
        config["agents"]["antigravity2"] = {
            "id": "antigravity2",
            "display_name": "Antigravity2",
            "provider": "antigravity",
        }
        status = {
            "tasks": [
                {"id": "T-7", "status": "review", "owner": "Antigravity", "reviewer": "Antigravity2"}
            ]
        }

        persist = self._run(status, config=config)

        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "T-7")
        self.assertEqual(kwargs["new_owner"], "Antigravity")
        self.assertEqual(kwargs["new_reviewer"], "Claude")
        self.assertEqual(kwargs["handoff_from"], "Antigravity2")

    def test_blocked_owner_reassigned_to_independent_owner(self) -> None:
        config = self._config()
        config["agents"]["claude2"] = {
            "id": "claude2",
            "display_name": "Claude2",
            "provider": "claude",
        }
        status = {
            "tasks": [
                {"id": "T-8", "status": "review_approved", "owner": "Codex", "reviewer": "Claude"}
            ]
        }

        persist = self._run(status, config=config)

        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "T-8")
        self.assertEqual(kwargs["new_owner"], "Antigravity")
        self.assertEqual(kwargs["new_reviewer"], "Claude")

    def test_transient_quota_saturated_owner_is_not_reassigned(self) -> None:
        """A healthy owner whose quota group is temporarily at capacity (active workers == limit)

        must NOT have in_progress/todo/review_approved tasks durably reassigned away.
        """
        config = self._config()
        config["agents"]["antigravity2"] = {
            "id": "antigravity2",
            "display_name": "Antigravity2",
            "provider": "antigravity",
        }
        config["account_pools"] = {
            "antigravity": {
                "id": "antigravity",
                "max_concurrency": 1,
            }
        }
        # Antigravity's quota group has 1/1 active workers running on an unrelated task
        state = {
            "queue": {"events": {}},
            "workers": {
                "w-1": {
                    "status": "running",
                    "agent": "antigravity",
                    "task_id": "T-OTHER",
                }
            },
            "provider_guardrails": {"dispatch_pauses": {}},
        }
        status = {
            "tasks": [
                {"id": "T-9", "status": "review_approved", "owner": "Antigravity2", "reviewer": "Claude"},
                {"id": "T-10", "status": "in_progress", "owner": "Antigravity2", "reviewer": "Claude"},
                {"id": "T-11", "status": "todo", "owner": "Antigravity2", "reviewer": "Claude"},
            ]
        }
        with (
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(
                supervisor, "outstanding_delivery_indexes", return_value=(set(), set(), set())
            ),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "console_log"),
        ):
            supervisor.reassign_unavailable_reviewers(config, state, status)

        persist.assert_not_called()

    def test_account_pool_exhausted_owner_is_reassigned(self) -> None:
        config = self._config()
        config["account_pools"] = {
            "codex": {
                "id": "codex",
                "state": "exhausted",
                "reason": "monthly quota limit reached",
            }
        }
        state = {
            "queue": {"events": {}},
            "workers": {},
            "provider_guardrails": {"dispatch_pauses": {}},
        }
        status = {
            "tasks": [
                {"id": "T-12", "status": "review_approved", "owner": "Codex", "reviewer": "Claude"}
            ]
        }
        with (
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(
                supervisor, "outstanding_delivery_indexes", return_value=(set(), set(), set())
            ),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "console_log"),
        ):
            supervisor.reassign_unavailable_reviewers(config, state, status)

        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "T-12")
        self.assertEqual(kwargs["new_owner"], "Antigravity")
        self.assertEqual(kwargs["new_reviewer"], "Claude")
        self.assertEqual(kwargs["handoff_from"], "Codex")
        self.assertIn("account pool codex is exhausted", kwargs["message"])



class AgentLoadBalancingTests(unittest.TestCase):
    """Reassignment used to hand every task to whoever sorted first.

    `first_viable_agent` returned the first name that passed its checks, and
    the default candidate pool is a hardcoded list beginning with
    "Antigravity". That name is always viable, so it always won. Measured on
    2026-08-08: Antigravity owned 23 open tasks while Antigravity2..7 held
    3, 6, 3, 4, 3 and 4. Because an agent runs one worker at a time, those 23
    were a single queue with six idle lanes beside it.
    """

    CONFIG = {
        "agents": {
            "antigravity": {"display_name": "Antigravity", "provider": "antigravity"},
            "antigravity2": {"display_name": "Antigravity2", "provider": "antigravity2"},
            "antigravity3": {"display_name": "Antigravity3", "provider": "antigravity3"},
        }
    }
    POOL = ["Antigravity", "Antigravity2", "Antigravity3"]

    @staticmethod
    def _status(counts: dict[str, int]) -> dict:
        tasks = []
        for owner, n in counts.items():
            tasks.extend({"id": f"T-{owner}-{i}", "status": "in_progress", "owner": owner} for i in range(n))
        return {"tasks": tasks}

    def test_picks_the_least_loaded_viable_agent(self) -> None:
        status = self._status({"Antigravity": 23, "Antigravity2": 3, "Antigravity3": 6})

        chosen = supervisor.first_viable_agent(
            self.CONFIG, self.POOL, exclude=set(), status=status
        )

        self.assertEqual(chosen, "Antigravity2")

    def test_preference_order_still_breaks_ties(self) -> None:
        """Equal load must keep the configured ordering, not shuffle it."""

        status = self._status({"Antigravity": 4, "Antigravity2": 4, "Antigravity3": 4})

        chosen = supervisor.first_viable_agent(
            self.CONFIG, self.POOL, exclude=set(), status=status
        )

        self.assertEqual(chosen, "Antigravity")

    def test_excluded_agents_are_never_chosen_however_idle(self) -> None:
        status = self._status({"Antigravity": 23, "Antigravity2": 0, "Antigravity3": 6})

        chosen = supervisor.first_viable_agent(
            self.CONFIG, self.POOL, exclude={"Antigravity2"}, status=status
        )

        self.assertEqual(chosen, "Antigravity3")

    def test_single_candidate_viability_check_is_unchanged(self) -> None:
        """Callers use a one-name list to ask "can this agent take it?".

        That question must not consult load, and must not read the board.
        """

        with mock.patch.object(supervisor, "load_status", side_effect=AssertionError("must not read the board")):
            self.assertEqual(
                supervisor.first_viable_agent(self.CONFIG, ["Antigravity"], exclude=set()),
                "Antigravity",
            )
            self.assertIsNone(
                supervisor.first_viable_agent(self.CONFIG, ["Antigravity"], exclude={"Antigravity"})
            )

    def test_viability_uses_capability_report_without_runtime_state(self) -> None:
        report = {
            "providers": {
                "antigravity": {
                    "local_cli_worker_supported": False,
                }
            }
        }

        chosen = supervisor.first_viable_agent(
            self.CONFIG,
            ["Antigravity"],
            exclude=set(),
            provider_report=report,
        )

        self.assertIsNone(chosen)

    def test_open_task_counts_ignore_finished_work(self) -> None:
        status = {
            "tasks": [
                {"id": "a", "status": "in_progress", "owner": "Antigravity"},
                {"id": "b", "status": "review", "owner": "Antigravity"},
                {"id": "c", "status": "done", "owner": "Antigravity"},
                {"id": "d", "status": "archived", "owner": "Antigravity"},
            ]
        }

        counts = supervisor.agent_open_task_counts(self.CONFIG, status)

        self.assertEqual(counts.get("antigravity"), 2)

    def test_open_task_counts_supports_reviewer_role(self) -> None:
        status = {
            "tasks": [
                {"id": "a", "status": "review", "owner": "Claude", "reviewer": "Antigravity2"},
                {"id": "b", "status": "review", "owner": "Claude", "reviewer": "Antigravity2"},
                {"id": "c", "status": "done", "owner": "Claude", "reviewer": "Antigravity2"},
            ]
        }

        owner_counts = supervisor.agent_open_task_counts(self.CONFIG, status, role="owner")
        reviewer_counts = supervisor.agent_open_task_counts(self.CONFIG, status, role="reviewer")

        self.assertEqual(owner_counts.get("claude"), 2)
        self.assertEqual(reviewer_counts.get("antigravity2"), 2)
        self.assertNotIn("antigravity3", reviewer_counts)

    def test_reviewer_path_balances_reviewer_load(self) -> None:
        """Reviewer reassignment must count reviewer load, not owner load.

        Reproducer: two review tasks assigned to reviewer Antigravity2 and none to
        Antigravity3. Owner count for both candidate reviewers is 0. With
        role="reviewer", first_viable_agent chooses Antigravity3.
        """
        status = {
            "tasks": [
                {"id": "a", "status": "review", "owner": "Claude", "reviewer": "Antigravity2"},
                {"id": "b", "status": "review", "owner": "Claude", "reviewer": "Antigravity2"},
            ]
        }
        candidates = ["Antigravity2", "Antigravity3"]

        chosen = supervisor.first_viable_agent(
            self.CONFIG, candidates, exclude={"Claude"}, status=status, role="reviewer"
        )

        self.assertEqual(chosen, "Antigravity3")


class ClaudeResumeModelSelectionTests(unittest.TestCase):
    """A resumed worker used to fall back to the interactive model setting.

    `resume_claude_worker` builds its own command line, separate from the
    adapter's. Without `--model`/`--effort` the resumed process reads
    ~/.claude/settings.json instead, so a worker that started on the
    configured model could silently finish on a different one — and the
    prompt cache, which is model-scoped, would be thrown away mid-run.
    """

    def _resume(self, runtime_extra: dict[str, Any]) -> list[str]:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = {
                "paths": {
                    "state_file": str(root / "state.json"),
                    "status_file": str(root / "ai-status.json"),
                },
                "providers": {
                    "claude": {
                        "runtime": {
                            "cli": ".orchestrator/bin/claude",
                            "output_format": "stream-json",
                            "include_hook_events": True,
                            **runtime_extra,
                        }
                    }
                },
            }
            worker = {"run_id": "run-1", "session_id": "sess-1", "agent_id": "claude"}
            fake_process = mock.Mock(pid=4321)

            with (
                mock.patch.object(
                    worker_failure_policy,
                    "configured_provider_binary",
                    return_value=".orchestrator/bin/claude",
                ),
                mock.patch.object(
                    worker_failure_policy,
                    "spawn_background_process",
                    return_value=(fake_process, root / "claude.log"),
                ) as spawn,
            ):
                result = worker_failure_policy.resume_claude_worker(config, worker, {})

        self.assertIsNotNone(result)
        self.assertTrue(spawn.called)
        self.assertEqual(spawn.call_count, 1)
        return list(result["command"])

    def test_resume_carries_configured_model_and_effort(self) -> None:
        command = self._resume({"model": "sonnet", "effort": "medium"})

        self.assertEqual(command[command.index("--model") + 1], "sonnet")
        self.assertEqual(command[command.index("--effort") + 1], "medium")

    def test_resume_omits_flags_when_unset(self) -> None:
        command = self._resume({})

        self.assertNotIn("--model", command)
        self.assertNotIn("--effort", command)


class ApprovedPrMergeRoutingTests(unittest.TestCase):
    """A reviewed, CI-green PR must be enqueued for merge.

    GitHub's merge queue only holds what is explicitly enqueued, so approved
    work used to stall indefinitely with nothing driving it to a merge.
    """

    HEAD = "1111111122222222333333334444444455555555"

    def _route(self, task, *, scope, merge_state="CLEAN", gh_side_effect=None):
        import dispatch_engine

        calls: list[list[str]] = []

        def fake_run_gh(args, **kwargs):
            calls.append(list(args))
            if gh_side_effect is not None:
                raise gh_side_effect
            return unittest.mock.Mock(stdout="{}")

        with unittest.mock.patch.object(
            dispatch_engine, "approved_pr_change_scope", return_value=scope
        ), unittest.mock.patch.object(
            dispatch_engine, "_pr_merge_state", return_value=merge_state
        ), unittest.mock.patch("github_bus.run_gh", side_effect=fake_run_gh), \
                unittest.mock.patch.object(dispatch_engine, "write_activity_log", create=True), \
                unittest.mock.patch.object(dispatch_engine, "utc_now", create=True, return_value="T"):
            route, detail = dispatch_engine.route_approved_pr_to_merge({}, task)
        return route, detail, calls

    def test_every_scope_takes_the_same_queue_path(self) -> None:
        """`dev` requires the merge queue, so scope cannot select a route.

        Branching here and taking `--admin` for tooling was rejected by the
        repository ruleset on every attempt.
        """
        for scope in ("development_tooling", "product_or_mixed", None):
            with self.subTest(scope=scope):
                task = {"id": "T-1", "pr_number": 42, "approved_head": self.HEAD}

                route, _, calls = self._route(task, scope=scope)

                self.assertEqual(route, "queued")
                self.assertEqual(calls, [["pr", "merge", "42"]])

    def test_scope_is_recorded_but_never_gates_the_enqueue(self) -> None:
        task = {"id": "T-1b", "pr_number": 46, "approved_head": self.HEAD}

        route, _, calls = self._route(task, scope="development_tooling")

        self.assertEqual(route, "queued")
        self.assertEqual(task["merge_route"]["scope"], "development_tooling")
        self.assertEqual(calls, [["pr", "merge", "46"]])

    def test_unclassifiable_diff_is_still_enqueued(self) -> None:
        """An unreadable diff is a reason to say so, not to strand a reviewed
        PR the queue would have accepted."""
        task = {"id": "T-2", "pr_number": 43, "approved_head": self.HEAD}

        route, _, calls = self._route(task, scope=None)

        self.assertEqual(route, "queued")
        self.assertEqual(task["merge_route"]["scope"], "unknown")
        self.assertEqual(calls, [["pr", "merge", "43"]])

    def _routed_at(self, seconds_ago: float) -> str:
        stamp = datetime.now(UTC) - timedelta(seconds=seconds_ago)
        return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_same_reviewed_head_is_not_routed_twice_within_the_window(self) -> None:
        task = {
            "id": "T-3",
            "pr_number": 44,
            "approved_head": self.HEAD,
            "merge_route": {
                "head": self.HEAD,
                "route": "queued",
                "at": self._routed_at(60),
                "attempts": 1,
            },
        }

        route, _, calls = self._route(task, scope="product_or_mixed")

        self.assertEqual(route, "waiting")
        self.assertEqual(calls, [])

    def test_a_recorded_enqueue_is_retried_once_the_window_elapses(self) -> None:
        """The record proves an enqueue was issued, not that the PR is queued.

        On 2026-08-19 four reviewed, CI-green PRs each carried `route: queued`
        while the dev queue was empty, and this guard suppressed every retry
        until an operator cleared the field by hand.
        """
        task = {
            "id": "T-5",
            "pr_number": 46,
            "approved_head": self.HEAD,
            "merge_route": {
                "head": self.HEAD,
                "route": "queued",
                "at": self._routed_at(7200),
                "attempts": 1,
            },
        }

        route, _, calls = self._route(task, scope="product_or_mixed")

        self.assertEqual(route, "queued")
        self.assertEqual(calls, [["pr", "merge", "46"]])
        self.assertEqual(task["merge_route"]["attempts"], 2)

    def test_repeated_enqueues_stop_and_report_rather_than_continue(self) -> None:
        """Retrying forever is the other failure. Past the cap the missing step
        is not another `gh pr merge`, so say so instead of issuing a fifth."""
        task = {
            "id": "T-6",
            "pr_number": 47,
            "approved_head": self.HEAD,
            "merge_route": {
                "head": self.HEAD,
                "route": "queued",
                "at": self._routed_at(7200),
                "attempts": 4,
            },
        }

        route, detail, calls = self._route(task, scope="product_or_mixed")

        self.assertEqual(route, "stalled")
        self.assertIn("4 times", detail)
        self.assertEqual(calls, [])

    def test_a_record_without_a_timestamp_self_heals(self) -> None:
        """A malformed record is retried once, which stamps a real `at`, and the
        attempt cap still bounds it."""
        task = {
            "id": "T-7",
            "pr_number": 48,
            "approved_head": self.HEAD,
            "merge_route": {"head": self.HEAD, "route": "queued"},
        }

        route, _, calls = self._route(task, scope="product_or_mixed")

        self.assertEqual(route, "queued")
        self.assertEqual(calls, [["pr", "merge", "48"]])
        self.assertTrue(task["merge_route"]["at"])

    def test_queue_ejection_is_reported_rather_than_waited_on(self) -> None:
        """An entry dropped for conflicting with a newly merged base is never
        re-added by the queue, so waiting on it strands the task forever."""
        task = {
            "id": "T-3b",
            "pr_number": 47,
            "approved_head": self.HEAD,
            "merge_route": {"head": self.HEAD, "route": "queued"},
        }

        route, detail, calls = self._route(
            task, scope="product_or_mixed", merge_state="DIRTY"
        )

        self.assertEqual(route, "ejected")
        self.assertIn("conflicts with base", detail)
        self.assertEqual(calls, [])

    def test_a_refused_enqueue_is_reported_and_not_recorded(self) -> None:
        """When GitHub refuses the enqueue - a ruleset violation, an offline
        host - say why and leave `merge_route` unset so the next tick retries.
        """
        import github_bus

        task = {"id": "T-4", "pr_number": 45, "approved_head": self.HEAD}
        refusal = github_bus.GitHubBusError(
            "GraphQL: Repository rule violations found (mergePullRequest)"
        )

        route, detail, calls = self._route(
            task, scope="development_tooling", gh_side_effect=refusal
        )

        self.assertEqual(route, "blocked")
        self.assertIn("rule violations", detail)
        self.assertEqual(calls, [["pr", "merge", "45"]])
        self.assertNotIn("merge_route", task)

    def test_task_without_pr_number_is_left_alone(self) -> None:
        task = {"id": "T-5", "approved_head": self.HEAD}

        route, _, calls = self._route(task, scope="development_tooling")

        self.assertEqual(route, "waiting")
        self.assertEqual(calls, [])


class ApprovedPrMergeAdvanceTests(unittest.TestCase):
    """Routing an approved PR, and explaining the wait, belong in one place.

    Both used to happen twice per tick - once here and once in the finalize
    lane of `dispatch_ready_tasks` - so a PR GitHub had just refused was
    retried immediately under a second, different message.
    """

    HEAD = "1111111122222222333333334444444455555555"
    FINALIZE = {"review_approved"}

    def _advance(self, task, *, route, detail=""):
        import dispatch_engine

        status = {"tasks": [task]}
        logged: list[dict] = []
        with unittest.mock.patch.object(
            dispatch_engine.runtime_ai_status,
            "task_pr_ci_status",
            return_value=("OPEN", "success"),
        ), unittest.mock.patch.object(
            dispatch_engine, "route_approved_pr_to_merge", return_value=(route, detail)
        ), unittest.mock.patch.object(
            dispatch_engine, "commit_canonical_task_transition", return_value=True
        ), unittest.mock.patch.object(
            dispatch_engine,
            "write_activity_log",
            create=True,
            side_effect=lambda _c, event: logged.append(event),
        ):
            changed = dispatch_engine.advance_approved_prs_to_merge(
                {}, status, self.FINALIZE
            )
        return changed, logged

    def _task(self):
        return {
            "id": "T-9",
            "status": "review_approved",
            "approved_head": self.HEAD,
            "pr_number": 99,
        }

    def test_a_refused_enqueue_is_reported_once(self) -> None:
        """A PR GitHub will not take is parked, so say why - but only once."""
        task = self._task()

        changed, logged = self._advance(task, route="blocked", detail="rule violations")

        self.assertTrue(changed)
        self.assertIn("could not be routed", task["next"])
        self.assertIn("rule violations", task["next"])
        self.assertEqual([event["type"] for event in logged], ["merge_route_blocked"])

        repeat_changed, repeat_logged = self._advance(
            task, route="blocked", detail="rule violations"
        )

        self.assertFalse(repeat_changed)
        self.assertEqual(repeat_logged, [])

    def test_a_stalled_enqueue_is_reported_once_and_not_retried(self) -> None:
        """Same shape as an escalated worktree lease: stop, and say so where an
        owner looks. Reporting without stopping is what left four PRs cycling."""
        task = self._task()

        changed, logged = self._advance(task, route="stalled", detail="enqueued 4 times")

        self.assertTrue(changed)
        self.assertIn("has not merged after being enqueued repeatedly", task["next"])
        self.assertIn("enqueued 4 times", task["next"])
        self.assertEqual([e["type"] for e in logged], ["merge_route_stalled"])

        repeat_changed, repeat_logged = self._advance(
            task, route="stalled", detail="enqueued 4 times"
        )

        self.assertFalse(repeat_changed)
        self.assertEqual(repeat_logged, [])

    def test_waiting_explains_the_queue_wait_without_rewriting_the_board(self) -> None:
        task = self._task()

        changed, _ = self._advance(task, route="waiting", detail="already routed")

        self.assertTrue(changed)
        self.assertIn("awaiting merge queue", task["next"])

        repeat_changed, _ = self._advance(task, route="waiting", detail="already routed")

        self.assertFalse(repeat_changed)

    def test_the_finalize_lane_no_longer_routes(self) -> None:
        """One caller only. A second one meant two enqueue attempts per tick."""
        import inspect

        import dispatch_engine

        source = inspect.getsource(dispatch_engine)
        self.assertEqual(source.count("route_approved_pr_to_merge(config, task)"), 1)


class FleetDispatchLivelockTests(unittest.TestCase):
    """The three faults that left the fleet idle for ten hours on 2026-08-17.

    Each one is individually fail-closed and defensible; together they meant no
    task could ever be dispatched again without a human touching the state file.
    """

    def _task(self, task_id="ODP-LIVELOCK-001"):
        return {
            "id": task_id,
            "status": "in_progress",
            "owner": "Antigravity2",
            "reviewer": "Claude",
            "depends_on": [],
        }

    def _blocked_state(self, task, *, blocked_at, reason="owned_in_progress_dispatch"):
        import dispatch_engine

        signature = dispatch_engine.ready_dispatch_signature(task, reason, {task["id"]: task})
        return {
            "worker_worktree_lease_blocks": {
                supervisor.normalize_agent_id(task["id"]): {
                    "dispatch_signature": signature,
                    "last_at": blocked_at,
                    "refresh_status": "unresolved_git_operation",
                }
            }
        }

    def test_a_fresh_block_still_suppresses_the_identical_wake(self) -> None:
        import dispatch_engine

        task = self._task()
        recent = (datetime.now(UTC) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state = self._blocked_state(task, blocked_at=recent)

        self.assertTrue(
            dispatch_engine.worktree_block_still_matches_dispatch(
                state,
                task,
                "owned_in_progress_dispatch",
                {task["id"]: task},
                retry_after_seconds=1800.0,
            )
        )

    def test_an_escalated_block_still_suppresses_inside_its_longer_window(self) -> None:
        """Escalation buys a much longer window, not silence.

        Retrying an escalated block on the ordinary window is what produced 341
        blocked dispatches over two days, so the ordinary window must not apply
        here. 3000s is past the ordinary 1800s window and still inside the
        escalated one, which is exactly the gap this branch exists to cover."""
        import dispatch_engine

        task = self._task()
        stale = (datetime.now(UTC) - timedelta(seconds=3000)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state = self._blocked_state(task, blocked_at=stale)
        entry = state["worker_worktree_lease_blocks"][supervisor.normalize_agent_id(task["id"])]
        entry.update({"escalated": True, "count": 7})

        self.assertTrue(
            dispatch_engine.worktree_block_still_matches_dispatch(
                state,
                task,
                "owned_in_progress_dispatch",
                {task["id"]: task},
                retry_after_seconds=1800.0,
            )
        )
        self.assertIs(dispatch_engine.escalated_lease_block(state, task), entry)

    def test_an_escalated_block_is_retried_once_its_longer_window_expires(self) -> None:
        """A repaired worktree has to be able to come back on its own.

        Suppressing escalated blocks forever made the documented escape hatch
        unreachable: the entry "resets whenever `refresh_status` differs", but
        `refresh_status` is only rewritten by a lease attempt, and suppression
        is precisely what prevents the lease attempt. On 2026-08-22 three tasks
        sat escalated for four to seven hours with eleven idle slots, and
        cleaning one worktree by hand changed nothing because no attempt
        followed. Retrying once per escalated window costs one dispatch per task
        per hour -- against a 180s tick, twenty times cheaper than the ordinary
        window -- and is the only thing that makes "repair the worktree"
        actionable."""
        import dispatch_engine

        task = self._task()
        stale = (
            datetime.now(UTC)
            - timedelta(seconds=dispatch_engine.ESCALATED_LEASE_BLOCK_RETRY_AFTER_SECONDS + 60)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        state = self._blocked_state(task, blocked_at=stale)
        entry = state["worker_worktree_lease_blocks"][supervisor.normalize_agent_id(task["id"])]
        entry.update({"escalated": True, "count": 7})

        self.assertFalse(
            dispatch_engine.worktree_block_still_matches_dispatch(
                state,
                task,
                "owned_in_progress_dispatch",
                {task["id"]: task},
                retry_after_seconds=1800.0,
            )
        )

    def test_a_block_below_the_threshold_still_expires(self) -> None:
        """Stopping every block would re-create the 2026-08-17 jam. Only an
        escalated one is terminal; a repairable worktree still comes back."""
        import dispatch_engine

        task = self._task()
        stale = (datetime.now(UTC) - timedelta(seconds=3600)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state = self._blocked_state(task, blocked_at=stale)

        self.assertFalse(
            dispatch_engine.worktree_block_still_matches_dispatch(
                state,
                task,
                "owned_in_progress_dispatch",
                {task["id"]: task},
                retry_after_seconds=1800.0,
            )
        )
        self.assertIsNone(dispatch_engine.escalated_lease_block(state, task))

    def test_an_expired_block_lets_the_task_be_retried(self) -> None:
        """A task parked in `in_progress` never changes dispatch signature, so
        without an expiry the block suppressed the only thing that could clear
        it and the task waited forever."""
        import dispatch_engine

        task = self._task()
        stale = (datetime.now(UTC) - timedelta(seconds=3600)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state = self._blocked_state(task, blocked_at=stale)

        self.assertFalse(
            dispatch_engine.worktree_block_still_matches_dispatch(
                state,
                task,
                "owned_in_progress_dispatch",
                {task["id"]: task},
                retry_after_seconds=1800.0,
            )
        )

    def test_a_block_with_no_timestamp_still_suppresses(self) -> None:
        import dispatch_engine

        task = self._task()
        state = self._blocked_state(task, blocked_at="")

        self.assertTrue(
            dispatch_engine.worktree_block_still_matches_dispatch(
                state,
                task,
                "owned_in_progress_dispatch",
                {task["id"]: task},
                retry_after_seconds=1800.0,
            )
        )

    def test_retry_window_comes_from_worker_runtime_settings(self) -> None:
        import dispatch_engine

        self.assertEqual(
            dispatch_engine.lease_block_retry_after_seconds(
                {"worker_runtime": {"lease_block_retry_after_seconds": 90}}
            ),
            90.0,
        )
        self.assertEqual(
            dispatch_engine.lease_block_retry_after_seconds({}),
            dispatch_engine.LEASE_BLOCK_RETRY_AFTER_SECONDS,
        )
        self.assertEqual(
            dispatch_engine.lease_block_retry_after_seconds(
                {"worker_runtime": {"lease_block_retry_after_seconds": "not-a-number"}}
            ),
            dispatch_engine.LEASE_BLOCK_RETRY_AFTER_SECONDS,
        )

    def test_a_pr_that_changes_nothing_is_classified_not_unknown(self) -> None:
        """GitHub answering "no files" is an answer. Collapsing it into None
        made a zero-diff PR indistinguishable from an unreadable diff, so three
        reviewed PRs sat unroutable with `change scope could not be classified`."""
        import dispatch_engine

        with unittest.mock.patch.object(
            dispatch_engine, "_pr_changed_paths", return_value=[]
        ):
            self.assertEqual(dispatch_engine.approved_pr_change_scope(461), "product_or_mixed")

    def test_an_unreadable_diff_is_still_unknown(self) -> None:
        import dispatch_engine

        with unittest.mock.patch.object(
            dispatch_engine, "_pr_changed_paths", return_value=None
        ):
            self.assertIsNone(dispatch_engine.approved_pr_change_scope(461))

    def test_changed_paths_reports_an_empty_pr_as_empty(self) -> None:
        import dispatch_engine

        proc = unittest.mock.Mock(stdout='{"files": []}')
        with unittest.mock.patch("github_bus.run_gh", return_value=proc):
            self.assertEqual(dispatch_engine._pr_changed_paths(461), [])

class WorktreeLeaseBlockTests(unittest.TestCase):
    """Lease blocks must invalidate when the task branch head changes.

    On 2026-08-23 a worktree was cleaned and a new commit pushed, but the
    dispatch signature did not include the branch head. The old lease block's
    signature still matched, so the task sat blocked for the full debounce
    window (30 min / 1 h escalated) instead of recovering immediately.

    Fix: ``ready_dispatch_signature`` includes ``branch_head`` so that any
    push to the task branch produces a different signature and invalidates
    the old block instantly.
    """

    TASK_ID = "ODP-LEASE-HEAD-001"
    OLD_HEAD = "a" * 40
    NEW_HEAD = "b" * 40

    def _task(self):
        return {
            "id": self.TASK_ID,
            "status": "in_progress",
            "owner": "Antigravity2",
            "reviewer": "Claude",
            "depends_on": [],
        }

    def _blocked_state(self, task, *, blocked_at, head_sha, reason="owned_in_progress_dispatch"):
        """Build a state with a lease block whose signature was computed under ``head_sha``."""
        import dispatch_engine

        with mock.patch.object(
            supervisor, "resolve_task_progress_head", return_value=head_sha,
        ):
            signature = dispatch_engine.ready_dispatch_signature(
                task, reason, {task["id"]: task},
            )
        return {
            "worker_worktree_lease_blocks": {
                supervisor.normalize_agent_id(task["id"]): {
                    "dispatch_signature": signature,
                    "last_at": blocked_at,
                    "refresh_status": "skipped_dirty_worktree: M .orchestrator/state.json",
                    "count": 3,
                }
            }
        }

    def test_block_invalidates_when_branch_head_changes(self) -> None:
        """A new push to the task branch must clear the block immediately."""
        import dispatch_engine

        task = self._task()
        recent = (datetime.now(UTC) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Block was recorded with OLD_HEAD
        state = self._blocked_state(task, blocked_at=recent, head_sha=self.OLD_HEAD)

        # Now the head has changed to NEW_HEAD — block must not match
        with mock.patch.object(
            supervisor, "resolve_task_progress_head", return_value=self.NEW_HEAD,
        ):
            self.assertFalse(
                dispatch_engine.worktree_block_still_matches_dispatch(
                    state,
                    task,
                    "owned_in_progress_dispatch",
                    {task["id"]: task},
                    retry_after_seconds=1800.0,
                )
            )

    def test_block_remains_when_head_unchanged(self) -> None:
        """When the head has not changed, the debounce window still applies."""
        import dispatch_engine

        task = self._task()
        recent = (datetime.now(UTC) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state = self._blocked_state(task, blocked_at=recent, head_sha=self.OLD_HEAD)

        with mock.patch.object(
            supervisor, "resolve_task_progress_head", return_value=self.OLD_HEAD,
        ):
            self.assertTrue(
                dispatch_engine.worktree_block_still_matches_dispatch(
                    state,
                    task,
                    "owned_in_progress_dispatch",
                    {task["id"]: task},
                    retry_after_seconds=1800.0,
                )
            )

    def test_escalated_block_invalidates_on_head_change(self) -> None:
        """Even an escalated block must yield to a new branch head."""
        import dispatch_engine

        task = self._task()
        recent = (datetime.now(UTC) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state = self._blocked_state(task, blocked_at=recent, head_sha=self.OLD_HEAD)
        entry = state["worker_worktree_lease_blocks"][supervisor.normalize_agent_id(task["id"])]
        entry.update({"escalated": True, "count": 12})

        with mock.patch.object(
            supervisor, "resolve_task_progress_head", return_value=self.NEW_HEAD,
        ):
            self.assertFalse(
                dispatch_engine.worktree_block_still_matches_dispatch(
                    state,
                    task,
                    "owned_in_progress_dispatch",
                    {task["id"]: task},
                    retry_after_seconds=1800.0,
                )
            )

    def test_unresolvable_head_matches_unresolvable_head(self) -> None:
        """When git is unreachable, both block and check get None — block holds."""
        import dispatch_engine

        task = self._task()
        recent = (datetime.now(UTC) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state = self._blocked_state(task, blocked_at=recent, head_sha=None)

        with mock.patch.object(
            supervisor, "resolve_task_progress_head", return_value=None,
        ):
            self.assertTrue(
                dispatch_engine.worktree_block_still_matches_dispatch(
                    state,
                    task,
                    "owned_in_progress_dispatch",
                    {task["id"]: task},
                    retry_after_seconds=1800.0,
                )
            )

    def test_head_becoming_resolvable_invalidates_block(self) -> None:
        """Block recorded while git was down; git comes back with a head."""
        import dispatch_engine

        task = self._task()
        recent = (datetime.now(UTC) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state = self._blocked_state(task, blocked_at=recent, head_sha=None)

        with mock.patch.object(
            supervisor, "resolve_task_progress_head", return_value=self.NEW_HEAD,
        ):
            self.assertFalse(
                dispatch_engine.worktree_block_still_matches_dispatch(
                    state,
                    task,
                    "owned_in_progress_dispatch",
                    {task["id"]: task},
                    retry_after_seconds=1800.0,
                )
            )

    def test_signature_includes_branch_head(self) -> None:
        """The dispatch signature JSON must contain a ``branch_head`` key."""
        import dispatch_engine

        task = self._task()
        with mock.patch.object(
            supervisor, "resolve_task_progress_head", return_value=self.OLD_HEAD,
        ):
            sig = dispatch_engine.ready_dispatch_signature(
                task, "owned_in_progress_dispatch", {task["id"]: task},
            )
        import json
        parsed = json.loads(sig)
        self.assertIn("branch_head", parsed)
        self.assertEqual(parsed["branch_head"], self.OLD_HEAD)

    def test_different_heads_produce_different_signatures(self) -> None:
        """Two heads must produce different signatures for the same task."""
        import dispatch_engine

        task = self._task()
        with mock.patch.object(
            supervisor, "resolve_task_progress_head", return_value=self.OLD_HEAD,
        ):
            sig_old = dispatch_engine.ready_dispatch_signature(
                task, "owned_in_progress_dispatch", {task["id"]: task},
            )
        with mock.patch.object(
            supervisor, "resolve_task_progress_head", return_value=self.NEW_HEAD,
        ):
            sig_new = dispatch_engine.ready_dispatch_signature(
                task, "owned_in_progress_dispatch", {task["id"]: task},
            )
        self.assertNotEqual(sig_old, sig_new)


class WorkerTaskBranchFromRecordTests(unittest.TestCase):
    """A worker must check out the branch the task record names.

    Deriving `task/<id>` unconditionally invented a branch for every task
    reimported from an existing GitHub PR. The refresh policy then reported the
    invented name as missing from a remote that never had it, which no retry can
    clear. SINGLE-RUNTIME-RELEASE-0D1603CF sat in that deadlock: its work is on
    `single-runtime-release-0d1603cf` (PR #822) while the leased worktree held an
    empty `task/SINGLE-RUNTIME-RELEASE-0D1603CF`.
    """

    CONFIG = {"branch_workflow": {"task_branch_prefix": "task/"}}

    def test_the_recorded_branch_wins_over_the_derived_name(self) -> None:
        self.assertEqual(
            supervisor.worker_task_branch(
                self.CONFIG,
                "SINGLE-RUNTIME-RELEASE-0D1603CF",
                {"branch": "single-runtime-release-0d1603cf"},
            ),
            "single-runtime-release-0d1603cf",
        )

    def test_a_record_naming_no_branch_still_derives(self) -> None:
        for task in ({}, {"branch": ""}, {"branch": "   "}, None):
            with self.subTest(task=task):
                self.assertEqual(
                    supervisor.worker_task_branch(self.CONFIG, "ODP-CONC-001", task),
                    "task/ODP-CONC-001",
                )

    def test_the_derived_name_is_unchanged_without_a_task_argument(self) -> None:
        self.assertEqual(
            supervisor.worker_task_branch(self.CONFIG, "ODP-CONC-001"), "task/ODP-CONC-001"
        )

    def test_a_malformed_recorded_branch_is_refused_not_passed_to_git(self) -> None:
        for bad in ("has space", "tilde~1", "caret^", "colon:x", "star*", "q?", "br[x", "back\\slash",
                    "-leading", "/leading", "trailing/", "x.lock", "dot..dot", "at@{0}"):
            with self.subTest(branch=bad):
                self.assertFalse(supervisor.branch_name_is_usable(bad))
                self.assertEqual(
                    supervisor.worker_task_branch(self.CONFIG, "T-1", {"branch": bad}),
                    "task/T-1",
                )

    def test_ordinary_branch_names_are_usable(self) -> None:
        for good in ("dev", "task/ODP-CONC-001", "single-runtime-release-0d1603cf", "feature/a_b.c"):
            with self.subTest(branch=good):
                self.assertTrue(supervisor.branch_name_is_usable(good))

    def test_the_canonical_record_beats_the_dispatch_snapshot(self) -> None:
        """The dispatch event carries a progress snapshot with no `branch` and no
        `repository`, so reading either from it silently degraded to a derived
        name and the default repository."""
        canonical = {"id": "T-9", "branch": "imported-branch", "repository": "owner/other"}
        snapshot = {"id": "T-9", "status": "in_progress", "owner": "Claude"}

        with mock.patch.object(supervisor, "load_status", return_value={}), \
                mock.patch.object(supervisor, "task_index_from_status", return_value={"T-9": canonical}):
            self.assertEqual(supervisor.canonical_task_record({}, "T-9", snapshot), canonical)

    def test_an_unreadable_status_file_falls_back_to_the_snapshot(self) -> None:
        snapshot = {"id": "T-9", "status": "in_progress"}

        with mock.patch.object(supervisor, "load_status", side_effect=OSError("gone")):
            self.assertEqual(supervisor.canonical_task_record({}, "T-9", snapshot), snapshot)

    def test_an_unknown_task_falls_back_to_the_snapshot(self) -> None:
        snapshot = {"id": "T-9"}

        with mock.patch.object(supervisor, "load_status", return_value={}), \
                mock.patch.object(supervisor, "task_index_from_status", return_value={}):
            self.assertEqual(supervisor.canonical_task_record({}, "T-9", snapshot), snapshot)


class OrchestratorSkillsAreScratchTests(unittest.TestCase):
    """Orchestrator-owned reference material must not block a lease.

    The Supervisor materializes these files into each isolated worktree. They
    are not worker deliverables, so counting them as owner dirt would refuse a
    lease on a worktree already at its exact reviewer-approved head.
    """

    def test_seeded_skill_files_classify_as_scratch(self) -> None:
        status = (
            b"?? .orchestrator/skills/task-closeout-finalization.md\0"
            b"?? .orchestrator/skills/worker-anchor-commit.md\0"
        )

        classification, paths = supervisor._classify_worktree_dirt(
            status,
            materialized_paths=[
                ".orchestrator/skills/task-closeout-finalization.md",
                ".orchestrator/skills/worker-anchor-commit.md",
            ],
        )

        self.assertEqual(classification, "scratch_only")
        self.assertEqual(len(paths), 2)

    def test_they_mix_with_the_other_scratch_prefixes(self) -> None:
        status = (
            b"?? .orchestrator/skills/worker-anchor-commit.md\0"
            b"?? .orchestrator/task-briefs/T-1.md\0"
            b"?? ai-status.json\0"
        )

        classification, _paths = supervisor._classify_worktree_dirt(
            status,
            materialized_paths=[
                ".orchestrator/skills/worker-anchor-commit.md",
                ".orchestrator/task-briefs/T-1.md",
                "ai-status.json",
            ],
        )

        self.assertEqual(classification, "scratch_only")

    def test_real_work_is_still_real(self) -> None:
        for status in (
            b"?? src/feature.py\0",
            b" M .orchestrator/skills/worker-anchor-commit.md\0",
            b"?? .orchestrator/skills/worker-anchor-commit.md\0?? src/feature.py\0",
        ):
            with self.subTest(status=status):
                classification, _paths = supervisor._classify_worktree_dirt(status)
                self.assertEqual(classification, "real")


class CrossRepositoryWorkerStartupTests(unittest.TestCase):
    """A worker whose workspace is in a sibling repository must still start.

    Observed 2026-08-19T07:35:38Z finalizing DPF-GOV-001 from the
    oday-data-platform worktree: `FileNotFoundError: [Errno 2] No such file or
    directory: '.orchestrator/bin/claude'`. Both halves of that failure are
    path resolution that only worked while every workspace happened to be the
    pantheon checkout.
    """

    RELATIVE_CLI = ".orchestrator/bin/claude"
    RESOLVED_CLI = "/home/lupin/odayplus/.orchestrator/bin/claude"

    def test_the_launcher_is_spawned_by_absolute_path(self) -> None:
        """`runtime.cli` is a relative config value and the worker is spawned
        with cwd set to its workspace, so argv[0] must be the resolved form."""
        from adapters.base import DeliveryRequest as AdapterRequest
        from adapters.claude_cli import ClaudeCLIAdapter

        config = {"providers": {"claude": {"runtime": {"cli": self.RELATIVE_CLI}}}}
        request = AdapterRequest(
            agent_id="claude", provider="claude", delivery_mode="claude_cli", message="wake"
        )
        adapter = ClaudeCLIAdapter(config=config, provider_capabilities={})
        captured: dict = {}

        def fake_spawn(_request, **kwargs):
            captured.update(kwargs)
            return mock.Mock(ok=True)

        with (
            mock.patch("adapters.claude_cli.configured_provider_binary", return_value=self.RESOLVED_CLI),
            mock.patch("adapters.claude_cli.claude_auth_ready", return_value=True),
            mock.patch.object(ClaudeCLIAdapter, "spawn_cli_delivery", side_effect=fake_spawn),
        ):
            adapter.deliver(request)

        argv0 = captured["command"][0]
        self.assertEqual(argv0, self.RESOLVED_CLI)
        self.assertTrue(Path(argv0).is_absolute(), argv0)
        self.assertNotEqual(argv0, self.RELATIVE_CLI)

    def test_status_root_is_the_fleet_root_not_the_task_repository(self) -> None:
        """The wake prompt tells workers to run
        `$PANTHEON_STATUS_ROOT/scripts/ai-status.sh`; a sibling repository has
        no `scripts/`, so the status root must stay with ai-status.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fleet_root = Path(tmpdir) / "pantheon"
            fleet_root.mkdir()
            other_repo = Path(tmpdir) / "oday-data-platform"
            other_repo.mkdir()
            config = {
                "paths": {"status_file": str(fleet_root / "ai-status.json")},
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "root": str(Path(tmpdir) / "workers"),
                },
            }
            state: dict = {}
            request = supervisor.DeliveryRequest(
                agent_id="claude",
                provider="claude",
                delivery_mode="claude_cli",
                message="wake",
                task_id="DPF-GOV-001",
                reason="owned_finalize_dispatch",
            )

            with (
                mock.patch.object(
                    supervisor,
                    "worker_task_repository_binding",
                    return_value=mock.Mock(
                        resolved=True,
                        root=other_repo,
                        source="repository:oday_data_platform",
                        repo_id="oday_data_platform",
                        default_branch="dev",
                    ),
                ),
                mock.patch.object(
                    supervisor,
                    "resolve_worker_base",
                    return_value=(mock.Mock(sha="a" * 40, remote_ref="origin/dev"), None),
                ),
                mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=None),
                mock.patch.object(supervisor, "_create_worker_worktree", return_value=(True, None)),
                mock.patch.object(supervisor, "materialize_worker_context_files", return_value=[]),
                mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-cross-repo",
                    target_agent="Claude",
                )

        self.assertTrue(ok, message)
        # The workspace is leased per repository under the worktree root...
        self.assertIn("oday-data-platform", request.metadata["workspace_path"])
        # ...but the status root follows ai-status.json, not the code.
        self.assertEqual(request.metadata["status_root"], str(fleet_root))
        self.assertNotEqual(request.metadata["status_root"], str(other_repo))
        lease = state["worker_worktrees"]["leases"]["DPF-GOV-001"]
        self.assertEqual(lease["status_root"], str(fleet_root))
        self.assertEqual(write_activity_log.call_args_list[-1].args[1]["status_root"], str(fleet_root))


class SupervisorTerminationLoggingTests(unittest.TestCase):
    """A signalled supervisor must not die silently.

    `atexit` does not run on SIGTERM and never on SIGKILL, so before this the
    process had no way to record being terminated: the log's last line was an
    ordinary tick and `supervisor.pid` was left stale for the watchdog to read.
    Four terminations in one hour were diagnosable only by elimination.
    """

    def _install_and_capture(self):
        installed: dict[int, object] = {}
        with mock.patch.object(supervisor.signal, "signal", side_effect=lambda n, h: installed.__setitem__(n, h)):
            supervisor.install_termination_logging({"paths": {}})
        return installed

    def test_installs_handlers_for_catchable_termination_signals(self) -> None:
        installed = self._install_and_capture()
        self.assertEqual(
            set(installed),
            {supervisor.signal.SIGTERM, supervisor.signal.SIGINT, supervisor.signal.SIGHUP},
        )

    def test_handler_records_the_signal_and_exits(self) -> None:
        installed = self._install_and_capture()
        handler = installed[supervisor.signal.SIGTERM]
        with (
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "console_log"),
        ):
            with self.assertRaises(SystemExit) as raised:
                handler(supervisor.signal.SIGTERM, None)
        # SystemExit rather than os._exit: it unwinds normally so the registered
        # atexit hook still clears supervisor.pid.
        self.assertEqual(raised.exception.code, 128 + int(supervisor.signal.SIGTERM))
        payload = write_activity_log.call_args.args[1]
        self.assertEqual(payload["type"], "supervisor_terminated")
        self.assertEqual(payload["signal"], "SIGTERM")
        self.assertEqual(payload["pid"], os.getpid())

    def test_handler_still_exits_when_both_log_sinks_fail(self) -> None:
        """A handler that raises on its way out would destroy the diagnostic."""
        installed = self._install_and_capture()
        handler = installed[supervisor.signal.SIGINT]
        with (
            mock.patch.object(supervisor, "console_log", side_effect=OSError("no tty")),
            mock.patch.object(supervisor, "write_activity_log", side_effect=OSError("read-only fs")),
        ):
            with self.assertRaises(SystemExit) as raised:
                handler(supervisor.signal.SIGINT, None)
        self.assertEqual(raised.exception.code, 128 + int(supervisor.signal.SIGINT))

    def test_main_installs_the_handler_before_writing_the_pid_file(self) -> None:
        """Ordering matters: a supervisor that owns the pid file must be able to clear it."""
        source = Path(supervisor.__file__).read_text(encoding="utf-8")
        install_at = source.index("    install_termination_logging(config)")
        write_at = source.index("    write_supervisor_pid(config)\n    bootstrap_supervisor_runtime_state")
        self.assertLess(install_at, write_at)


class HandedOffChildProtectsItsQueueEventTests(unittest.TestCase):
    """A queue event must not settle while a worker on it is still running.

    `finalize_queue_event_record` refuses when another worker on the same event is
    active -- but it asked the raw `ready_dispatcher.active_worker_statuses`, which
    shipped without `fallback`. So a parent that had handed off to a file_inbox
    child settled the event as `failed` underneath the child. Reproduced against
    the real function before the floor was introduced.
    """

    def _state(self, child_status: str) -> dict:
        return {
            "workers": {
                "parent": {"run_id": "parent", "status": "failed", "queue_event_id": "evt-1", "task_id": "T-1"},
                "child": {"run_id": "child", "status": child_status, "queue_event_id": "evt-1", "task_id": "T-1"},
            },
            "queue": {"events": {"evt-1": {"status": "started"}}},
        }

    def test_a_running_fallback_child_blocks_finalization(self) -> None:
        config = {"ready_dispatcher": {"active_worker_statuses": ["running", "waiting_approval"]}}
        state = self._state("fallback")
        supervisor.finalize_queue_event_record(config, state, state["workers"]["parent"], "failed", "parent died")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "started")

    def test_a_started_child_blocks_finalization(self) -> None:
        config = {"ready_dispatcher": {"active_worker_statuses": []}}
        state = self._state("started")
        supervisor.finalize_queue_event_record(config, state, state["workers"]["parent"], "failed", "parent died")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "started")

    def test_a_finished_child_does_not_block_finalization(self) -> None:
        """The floor widens what counts as live; it does not stop settlement."""
        config = {"ready_dispatcher": {"active_worker_statuses": []}}
        state = self._state("completed")
        supervisor.finalize_queue_event_record(config, state, state["workers"]["parent"], "failed", "parent died")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "failed")


class MergeRouteWithoutQueueTests(unittest.TestCase):
    """A repository without a merge queue must be merged, not enqueued.

    Routing was written against ODay Plus, whose `dev` requires a queue.
    `oday-data-platform` is private on a plan without branch protection, so it
    has no queue and a bare `gh pr merge` there enqueues nothing. On 2026-08-20
    four reviewed, CI-green PRs sat until they were reported `stalled` and
    merged by hand, and every DPF task reaching review_approved would repeat it.
    """

    HEAD = "1111111122222222333333334444444455555555"

    def _route(self, *, has_queue, repository="alfloop-dev/oday-data-platform"):
        import dispatch_engine

        calls = []

        def fake_run_gh(args, **kwargs):
            calls.append(list(args))
            return unittest.mock.Mock(stdout="{}")

        task = {"id": "T-1", "pr_number": 42, "approved_head": self.HEAD,
                "repository": repository, "base_branch": "dev"}
        with unittest.mock.patch.object(dispatch_engine, "repository_has_merge_queue", return_value=has_queue), \
                unittest.mock.patch.object(dispatch_engine, "approved_pr_change_scope", return_value="product_or_mixed"), \
                unittest.mock.patch.object(dispatch_engine, "_pr_merge_state", return_value="CLEAN"), \
                unittest.mock.patch("github_bus.run_gh", side_effect=fake_run_gh), \
                unittest.mock.patch.object(dispatch_engine, "write_activity_log", create=True), \
                unittest.mock.patch.object(dispatch_engine, "utc_now", create=True, return_value="T"):
            route, detail = dispatch_engine.route_approved_pr_to_merge({}, task)
        return route, detail, calls, task

    def test_a_repository_without_a_queue_is_merged_directly(self) -> None:
        route, _d, calls, task = self._route(has_queue=False)

        self.assertEqual(route, "merged")
        self.assertEqual(
            calls,
            [["pr", "merge", "42", "--merge", "--repo", "alfloop-dev/oday-data-platform"]],
        )
        self.assertEqual(task["merge_route"]["route"], "merged")

    def test_a_repository_with_a_queue_is_still_enqueued(self) -> None:
        route, _d, calls, task = self._route(has_queue=True, repository="alfloop-dev/odayplus")

        self.assertEqual(route, "queued")
        self.assertEqual(calls, [["pr", "merge", "42", "--repo", "alfloop-dev/odayplus"]])
        self.assertEqual(task["merge_route"]["route"], "queued")

    def test_an_unreadable_answer_enqueues_rather_than_merging(self) -> None:
        """None means unknown. A direct merge on a queue-protected repository
        would be exactly the bypass this routing exists to prevent."""
        route, _d, calls, _task = self._route(has_queue=None)

        self.assertEqual(route, "queued")
        self.assertNotIn("--merge", calls[0])

    def test_detection_is_cached_per_repository(self) -> None:
        import dispatch_engine

        calls = []

        def fake_run_gh(args, **kwargs):
            calls.append(list(args))
            return unittest.mock.Mock(
                stdout='{"data":{"repository":{"mergeQueue":null}}}'
            )

        dispatch_engine._MERGE_QUEUE_BY_REPO.pop("owner/repo", None)
        with unittest.mock.patch("github_bus.run_gh", side_effect=fake_run_gh):
            first = dispatch_engine.repository_has_merge_queue("owner/repo", "dev")
            second = dispatch_engine.repository_has_merge_queue("owner/repo", "dev")

        self.assertFalse(first)
        self.assertFalse(second)
        self.assertEqual(len(calls), 1, "second lookup should come from the cache")
        dispatch_engine._MERGE_QUEUE_BY_REPO.pop("owner/repo", None)

    def test_a_present_queue_reads_as_true(self) -> None:
        import dispatch_engine

        dispatch_engine._MERGE_QUEUE_BY_REPO.pop("owner/queued", None)
        with unittest.mock.patch(
            "github_bus.run_gh",
            return_value=unittest.mock.Mock(stdout='{"data":{"repository":{"mergeQueue":{"id":"MQ_x"}}}}'),
        ):
            self.assertTrue(dispatch_engine.repository_has_merge_queue("owner/queued", "dev"))
        dispatch_engine._MERGE_QUEUE_BY_REPO.pop("owner/queued", None)

    def test_a_malformed_slug_is_not_queried(self) -> None:
        import dispatch_engine

        with unittest.mock.patch("github_bus.run_gh") as run_gh:
            self.assertIsNone(dispatch_engine.repository_has_merge_queue("", "dev"))
            self.assertIsNone(dispatch_engine.repository_has_merge_queue("noslash", "dev"))
        run_gh.assert_not_called()


class QuarantineCrossRepositoryTests(unittest.TestCase):
    """Quarantine must judge a worktree against its own repository.

    It derived the repository from the status file's directory, which is always
    the Pantheon checkout, then compared git identities and refused anything
    that did not match. Every oday-data-platform worktree failed that check
    silently, so the recovery that exists for `skipped_dirty_worktree` could
    never run there -- DPF-SRC-RIS-NLSC-001 and DPF-SRC-OSM-TDX-001 each blocked
    five times and escalated with no backup ever written.
    """

    def _repo(self, root: Path, name: str) -> Path:
        repo = root / name
        repo.mkdir(parents=True)
        for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "t"]):
            subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
        (repo / "seed.txt").write_text("seed\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True, capture_output=True)
        return repo

    def _dirty_worktree(self, repo: Path, path: Path, branch: str) -> Path:
        subprocess.run(["git", "worktree", "add", "-q", "-b", branch, str(path)],
                       cwd=repo, check=True, capture_output=True)
        (path / "seed.txt").write_text("edited\n")
        return path

    def test_a_sibling_repository_worktree_is_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fleet = self._repo(root, "pantheon")
            sibling = self._repo(root, "data-platform")
            wt = self._dirty_worktree(sibling, root / "wt", "task/T-1")
            config = {"paths": {
                "status_file": str(fleet / "ai-status.json"),
                "activity_log": str(fleet / "ai-activity-log.jsonl"),
                "state_file": str(fleet / ".orchestrator" / "state.json"),
            }}

            # Without the owning repository it compares against Pantheon and refuses.
            self.assertFalse(
                supervisor._quarantine_and_preserve_dirty_worktree(
                    config, {}, wt, "T-1", expected_branch="task/T-1", trigger="t"
                )
            )
            # With it, the worktree is judged against the repository it belongs to.
            self.assertTrue(
                supervisor._quarantine_and_preserve_dirty_worktree(
                    config, {}, wt, "T-1", expected_branch="task/T-1", trigger="t",
                    owning_repo_root=sibling,
                )
            )

    def test_the_backup_stays_in_the_fleet_root(self) -> None:
        """The orchestrator's state lives in Pantheon regardless of whose code
        is being preserved."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fleet = self._repo(root, "pantheon")
            sibling = self._repo(root, "data-platform")
            wt = self._dirty_worktree(sibling, root / "wt", "task/T-2")
            config = {"paths": {
                "status_file": str(fleet / "ai-status.json"),
                "activity_log": str(fleet / "ai-activity-log.jsonl"),
                "state_file": str(fleet / ".orchestrator" / "state.json"),
            }}

            self.assertTrue(
                supervisor._quarantine_and_preserve_dirty_worktree(
                    config, {}, wt, "T-2", expected_branch="task/T-2", trigger="t",
                    owning_repo_root=sibling,
                )
            )

            backups = fleet / ".orchestrator" / "worktree-dirt-backups"
            written = sorted(backups.glob("t-2-*"))
            self.assertEqual(len(written), 1, f"expected one backup, found {written}")
            self.assertFalse((sibling / ".orchestrator").exists(),
                             "backups must not be written into the task's repository")
            manifest = json.loads((written[0] / "manifest.json").read_text())
            self.assertEqual(manifest["task_id"], "T-2")
            self.assertEqual([f["path"] for f in manifest["files"]], ["seed.txt"])

    def test_the_same_repository_still_works_without_the_argument(self) -> None:
        """Pantheon's own worktrees must behave exactly as before."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fleet = self._repo(root, "pantheon")
            wt = self._dirty_worktree(fleet, root / "wt", "task/T-3")
            config = {"paths": {
                "status_file": str(fleet / "ai-status.json"),
                "activity_log": str(fleet / "ai-activity-log.jsonl"),
                "state_file": str(fleet / ".orchestrator" / "state.json"),
            }}

            self.assertTrue(
                supervisor._quarantine_and_preserve_dirty_worktree(
                    config, {}, wt, "T-3", expected_branch="task/T-3", trigger="t"
                )
            )

    def test_a_worktree_of_neither_repository_is_still_refused(self) -> None:
        """The guard must keep refusing a path that is not a worktree of the
        repository it is claimed to belong to."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fleet = self._repo(root, "pantheon")
            sibling = self._repo(root, "data-platform")
            stranger = self._repo(root, "stranger")
            wt = self._dirty_worktree(stranger, root / "wt", "task/T-4")
            config = {"paths": {
                "status_file": str(fleet / "ai-status.json"),
                "activity_log": str(fleet / "ai-activity-log.jsonl"),
                "state_file": str(fleet / ".orchestrator" / "state.json"),
            }}

            self.assertFalse(
                supervisor._quarantine_and_preserve_dirty_worktree(
                    config, {}, wt, "T-4", expected_branch="task/T-4", trigger="t",
                    owning_repo_root=sibling,
                )
            )


class QuarantineRefusalIsLegibleTests(unittest.TestCase):
    """Thirteen ways to decline used to be one indistinguishable `False`."""

    def test_refusals_name_themselves_and_stay_falsy(self) -> None:
        outcome = supervisor._quarantine_refused("branch_mismatch", "wanted task/A")
        self.assertFalse(outcome)
        self.assertFalse(bool(outcome))
        self.assertEqual(outcome.reason, "branch_mismatch")
        self.assertEqual(outcome.detail, "wanted task/A")

    def test_a_missing_path_and_a_foreign_repo_are_different_answers(self) -> None:
        """The two refusals a caller most needs to tell apart.

        `no_worktree_path` is routine. `worktree_belongs_to_another_repository`
        is the misrouting that silently disabled quarantine for every
        oday-data-platform task until 2026-08-20.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"paths": {"status_file": str(Path(tmpdir) / "ai-status.json")}}
            absent = supervisor._quarantine_and_preserve_dirty_worktree(config, {}, None, "T-1")
            self.assertFalse(absent)
            self.assertEqual(absent.reason, "no_worktree_path")

            not_a_worktree = supervisor._quarantine_and_preserve_dirty_worktree(
                config, {}, Path(tmpdir) / "nowhere", "T-1"
            )
            self.assertFalse(not_a_worktree)
            self.assertEqual(not_a_worktree.reason, "not_a_worktree")
            self.assertNotEqual(absent.reason, not_a_worktree.reason)

    def test_a_success_is_still_truthy(self) -> None:
        self.assertTrue(supervisor.QuarantineOutcome(True, "preserved"))



class PreserveOnWorkerDeathTests(unittest.TestCase):
    """Uncommitted work is saved when the worker dies, not when the next lease tries.

    The stranded-work loop: a worker dies before committing, so no branch is
    published; the fail-closed refresh then refuses the lease because the branch
    is missing from the remote; and the quarantine that would have preserved the
    work sits behind that refusal. The only worker that could have committed is
    the one refused entry. Recovering it by hand cost 4459+ lines across four to
    five occurrences.
    """

    def _config(self, tmpdir: str) -> dict:
        return {"paths": {"status_file": str(Path(tmpdir) / "ai-status.json")}}

    def test_it_names_the_dying_worker_so_it_is_not_read_as_a_live_owner(self) -> None:
        """The dead worker is still in `state` with an active status.

        Without its run id, quarantine sees an active worker holding that path
        and refuses -- the record of the death would block the response to it.
        """
        worker = {
            "run_id": "run-1",
            "task_id": "T-1",
            "status": "running",
            "workspace_path": "/tmp/does-not-matter",
        }
        state = {"workers": {"run-1": worker}}
        with tempfile.TemporaryDirectory() as tmpdir:
            config = self._config(tmpdir)
            with (
                mock.patch.object(
                    supervisor,
                    "_quarantine_and_preserve_dirty_worktree",
                    return_value=supervisor._quarantine_refused("nothing_to_preserve"),
                ) as quarantine,
                mock.patch.object(supervisor, "write_activity_log") as log,
            ):
                outcome = supervisor.preserve_dead_worker_worktree(
                    config, state, worker, task={"id": "T-1"}, trigger="missing_process"
                )
        self.assertFalse(outcome)
        self.assertEqual(quarantine.call_args.kwargs["run_id"], "run-1")
        self.assertEqual(quarantine.call_args.kwargs["trigger"], "missing_process")
        self.assertEqual(log.call_args.args[1]["type"], "worker_death_worktree_not_preserved")
        self.assertEqual(log.call_args.args[1]["reason"], "nothing_to_preserve")

    def test_a_worker_with_no_workspace_is_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            outcome = supervisor.preserve_dead_worker_worktree(
                self._config(tmpdir), {"workers": {}}, {"run_id": "r", "task_id": "T"}
            )
        self.assertFalse(outcome)
        self.assertEqual(outcome.reason, "worker_had_no_workspace")

    def test_settlement_is_never_blocked_by_a_failure_to_preserve(self) -> None:
        """A worker must still settle if backing up its scratch space throws."""
        worker = {"run_id": "r", "task_id": "T", "status": "running", "workspace_path": "/tmp/x"}
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                mock.patch.object(
                    supervisor,
                    "_quarantine_and_preserve_dirty_worktree",
                    side_effect=OSError("disk full"),
                ),
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                outcome = supervisor.preserve_dead_worker_worktree(
                    self._config(tmpdir), {"workers": {}}, worker
                )
        self.assertFalse(outcome)
        self.assertEqual(outcome.reason, "preserve_raised")

    def test_the_orphan_path_preserves_before_the_record_disappears(self) -> None:
        """Ordering, checked at the source.

        `workers.pop` is the last moment anything knows where this worker's
        workspace was, so preservation has to precede it. Nothing at runtime
        can observe that ordering after the fact, which is why it is pinned
        here rather than behaviourally.
        """
        source = (Path(__file__).resolve().parent / "worker_lifecycle.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        pop_lines, preserve_lines = [], []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "pop" and isinstance(func.value, ast.Name) and func.value.id == "workers":
                pop_lines.append(node.lineno)
            if isinstance(func, ast.Name) and func.id == "preserve_dead_worker_worktree":
                preserve_lines.append(node.lineno)
        self.assertTrue(pop_lines, "expected the orphan-drop site to still exist")
        self.assertTrue(preserve_lines, "worker death must preserve the worktree")
        for pop_line in pop_lines:
            self.assertTrue(
                any(0 < pop_line - preserve_line < 15 for preserve_line in preserve_lines),
                f"workers.pop at line {pop_line} drops a worker record without preserving its worktree first",
            )


class CapacityControllerReconciliationTests(unittest.TestCase):
    def _config(self) -> dict[str, Any]:
        return {
            "schema": {"tasks_path": "tasks"},
            "agents": {
                "claude": {"slot_id": "slot-0"},
                "codex": {"slot_id": "slot-1"},
            },
            "capacity_controller": {
                "enabled": True,
                "chair_interval_seconds": 1800,
                "stall_window_seconds": 300,
                "underutilization_window_seconds": 600,
                "coordination_reserved_slots": 1,
                "sidecars": {
                    "enabled": True,
                    "max_new_per_wave": 3,
                    "max_active": 4,
                    "max_capacity_ratio": 0.25,
                    "ttl_seconds": 7200,
                },
            },
        }

    def test_capacity_reconcile_with_blocked_and_human_gate_tasks_does_not_flag_stalled_runnable_work(self) -> None:
        tasks = [
            {"id": "HG-001", "status": "todo", "owner": "Human/Ops", "task_class": "human_gate"},
            {"id": "BLOCKED-001", "status": "blocked", "owner": "codex", "waiting_for": "Human/Ops"},
            {"id": "DEP-BLOCKED-001", "status": "todo", "owner": "claude", "depends_on": ["BLOCKED-001"]},
        ]
        config = self._config()
        state: dict[str, Any] = {"workers": {}}

        with mock.patch.object(supervisor, "load_status", return_value={"tasks": tasks}):
            changed = supervisor.reconcile_capacity_controller(config, state)
            self.assertTrue(changed)
            controller = state.get("capacity_controller", {})
            snapshot = controller.get("snapshot", {})
            self.assertEqual(snapshot.get("runnable_tasks"), 0)
            self.assertIsNone(controller.get("stall_since"))
            decision = controller.get("chair_decision", {})
            self.assertNotIn("runnable_work_without_active_workers", decision.get("reasons", []))

    def test_capacity_reconcile_with_runnable_task_updates_snapshot_truth(self) -> None:
        tasks = [
            {"id": "DEP-001", "status": "done"},
            {"id": "RUNNABLE-001", "status": "todo", "owner": "claude", "depends_on": ["DEP-001"]},
        ]
        config = self._config()
        state: dict[str, Any] = {"workers": {}}

        with mock.patch.object(supervisor, "load_status", return_value={"tasks": tasks}):
            changed = supervisor.reconcile_capacity_controller(config, state)
            self.assertTrue(changed)
            controller = state.get("capacity_controller", {})
            snapshot = controller.get("snapshot", {})
            self.assertEqual(snapshot.get("runnable_tasks"), 1)
            decision = controller.get("chair_decision", {})
            self.assertTrue(decision.get("approve_helper_wave"))

    def test_canonical_dispatchable_task_ids_respects_custom_schema_and_excludes_invalid_id_or_owner(self) -> None:
        config = self._config()
        config["schema"] = {
            "tasks_path": "items",
            "task_id_field": "taskId",
            "assignee_field": "assignee",
        }
        # Valid task
        task_valid = {"taskId": "T-001", "status": "todo", "assignee": "claude"}
        self.assertEqual(supervisor.canonical_dispatchable_task_ids(config, [task_valid]), {"T-001"})

        # Missing or empty task ID
        self.assertEqual(supervisor.canonical_dispatchable_task_ids(config, [{"status": "todo", "assignee": "claude"}]), set())
        self.assertEqual(supervisor.canonical_dispatchable_task_ids(config, [{"taskId": "", "status": "todo", "assignee": "claude"}]), set())

        # Human/Ops assignee
        self.assertEqual(supervisor.canonical_dispatchable_task_ids(config, [{"taskId": "T-002", "status": "todo", "assignee": "Human/Ops"}]), set())

        # Unknown assignee not in agents
        self.assertEqual(supervisor.canonical_dispatchable_task_ids(config, [{"taskId": "T-003", "status": "todo", "assignee": "UnknownWorker"}]), set())

        # Missing assignee
        self.assertEqual(supervisor.canonical_dispatchable_task_ids(config, [{"taskId": "T-004", "status": "todo"}]), set())

    def test_canonical_dispatchable_task_ids_excludes_non_execution_states_and_unsatisfied_dependencies(self) -> None:
        config = self._config()
        dep_done = {"id": "DEP-DONE", "status": "done"}
        dep_todo = {"id": "DEP-TODO", "status": "todo", "owner": "claude"}

        # Non-dispatchable
        self.assertEqual(supervisor.canonical_dispatchable_task_ids(config, [{"id": "ND-1", "status": "todo", "owner": "claude", "non_dispatchable": True}]), set())

        # Human gate and sidecars
        self.assertEqual(supervisor.canonical_dispatchable_task_ids(config, [{"id": "HG-1", "status": "todo", "owner": "claude", "task_class": "human_gate"}]), set())
        self.assertEqual(supervisor.canonical_dispatchable_task_ids(config, [{"id": "SC-1", "status": "todo", "owner": "claude", "task_class": "sidecar"}]), set())

        # Review and review_approved states
        self.assertEqual(supervisor.canonical_dispatchable_task_ids(config, [{"id": "REV-1", "status": "review", "owner": "claude", "reviewer": "codex"}]), set())
        self.assertEqual(supervisor.canonical_dispatchable_task_ids(config, [{"id": "APP-1", "status": "review_approved", "owner": "claude"}]), set())

        # Blocked state
        self.assertEqual(supervisor.canonical_dispatchable_task_ids(config, [{"id": "BLK-1", "status": "blocked", "owner": "claude"}]), set())

        # Dependency satisfaction
        task_with_done_dep = {"id": "T-1", "status": "todo", "owner": "claude", "depends_on": ["DEP-DONE"]}
        self.assertEqual(supervisor.canonical_dispatchable_task_ids(config, [task_with_done_dep, dep_done]), {"T-1"})

        task_with_pending_dep = {"id": "T-2", "status": "todo", "owner": "claude", "depends_on": ["DEP-TODO"]}
        self.assertEqual(supervisor.canonical_dispatchable_task_ids(config, [task_with_pending_dep, dep_todo]), {"DEP-TODO"})

    def test_capacity_reconcile_does_not_create_sidecars_when_runnable_task_appears_during_active_approval(self) -> None:
        tasks = [
            {"id": "BLOCKED-001", "status": "blocked", "owner": "claude", "blocked_reason": "upstream defect"},
            {"id": "RUN-001", "status": "todo", "owner": "claude"},
        ]
        config = self._config()
        state: dict[str, Any] = {
            "workers": {},
            "capacity_controller": {
                "chair_decision": {
                    "issued_at": "2026-08-20T11:50:00Z",
                    "valid_until": "2026-08-20T12:30:00Z",
                    "sidecar_wave": {"approved": True},
                }
            },
        }

        with mock.patch.object(supervisor, "load_status", return_value={"tasks": tasks}):
            supervisor.reconcile_capacity_controller(config, state)
            sidecar_tasks = [t for t in tasks if t.get("task_class") == "sidecar"]
            self.assertEqual(len(sidecar_tasks), 0)

    def test_reconcile_capacity_controller_injects_provider_report_into_capacity_truth(self) -> None:
        tasks = [
            {"id": "DEP-001", "status": "done"},
            {"id": "RUNNABLE-001", "status": "todo", "owner": "claude", "depends_on": ["DEP-001"]},
        ]
        config = self._config()
        state: dict[str, Any] = {"workers": {}}
        provider_report = {
            "providers": {
                "claude": {"auth_ready": False, "local_cli_worker_supported": False},
                "codex": {"auth_ready": True, "local_cli_worker_supported": True},
            },
            "agent_adapters": {
                "claude": {"can_auto_deliver": False},
                "codex": {"can_auto_deliver": True, "supported": True},
            },
        }

        with mock.patch.object(supervisor, "load_status", return_value={"tasks": tasks}):
            changed = supervisor.reconcile_capacity_controller(config, state, provider_report=provider_report)
            self.assertTrue(changed)
            controller = state.get("capacity_controller", {})
            snapshot = controller.get("snapshot", {})
            # 2 slots configured (claude slot-0, codex slot-1), claude unauth -> effective slot_total is 1
            self.assertEqual(snapshot.get("configured_slot_total"), 2)
            self.assertEqual(snapshot.get("slot_total"), 1)
            self.assertEqual(snapshot.get("available_slots"), 1)


class WorkerPromptContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_test_config()

    def test_worker_prompt_forbids_root_wide_scan_and_requires_declared_verification(self) -> None:
        event = {
            "task_id": "OPS-WORKER-NO-ROOT-SCAN-001",
            "reason": "owned_in_progress_dispatch",
            "context_files": ["AI_COLLABORATION_GUIDE.md"],
            "task": {
                "artifacts": [".orchestrator/templates/wakeup.txt"],
                "verification": ["python3 -m pytest .orchestrator/test_supervisor.py -k worker_prompt -q"],
            },
        }
        rendered = watch_events.render_wakeup_message(self.config, event, "antigravity4")
        self.assertIn("find /", rendered)
        self.assertIn("rg /", rendered)
        self.assertIn("全系統", rendered)
        self.assertIn("root-wide", rendered)
        self.assertIn("uv run", rendered)
        self.assertIn("verification", rendered)
        self.assertIn("不自行掃描主機", rendered)

    def test_worker_prompt_root_scan_prohibition_applies_across_dispatch_reasons(self) -> None:
        dispatch_reasons = [
            "owned_ready_dispatch",
            "owned_in_progress_dispatch",
            "review_ready_dispatch",
            "owned_finalize_dispatch",
            "helper_claim_dispatch",
        ]
        for reason in dispatch_reasons:
            with self.subTest(reason=reason):
                event = {
                    "task_id": "OPS-WORKER-NO-ROOT-SCAN-001",
                    "reason": reason,
                    "context_files": ["AI_COLLABORATION_GUIDE.md"],
                    "task": {"artifacts": [".orchestrator/templates/wakeup.txt"]},
                }
                rendered = watch_events.render_wakeup_message(self.config, event, "antigravity4")
                self.assertIn("find /", rendered)
                self.assertIn("rg /", rendered)
                self.assertIn("uv run", rendered)
                self.assertIn("不自行掃描主機", rendered)


if __name__ == "__main__":
    unittest.main()
