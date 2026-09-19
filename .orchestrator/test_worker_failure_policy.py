from __future__ import annotations

import ctypes
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

THIS_DIR = Path(__file__).resolve().parent
ROOT_DIR = THIS_DIR.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import runtime_state
import supervisor
import worker_failure_policy
import worker_runner
import worker_workspace
import worktree_cleanliness
from adapters.base import DeliveryRequest
from worktree_cleanliness import inspect_worktree


class WorkerFailurePolicyAuthorityTests(unittest.TestCase):
    """Tests verifying structured failure authority and quota detection rules."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config: dict[str, Any] = {
            "paths": {
                "activity_log": str(Path(self.tmpdir.name) / "activity.jsonl"),
            },
            "worker_retry": {
                "max_attempts": 3,
                "transient_error_patterns": ["retryablequotaerror", "resource_exhausted"],
            },
            "provider_guardrails": {
                "pause_on_capacity_failure": True,
                "pause_on_auth_failure": True,
                "capacity_pause_seconds": 900,
                "quota_terminal_pause_seconds": 900,
            },
            "providers": {
                "codex": {"dispatch_group": "codex"},
                "codex2": {"dispatch_group": "codex"},
            },
            "agents": {
                "codex": {"provider": "codex", "account_pool": "codex"},
                "codex2": {"provider": "codex2", "account_pool": "codex"},
            },
            "account_pools": {
                "codex": {
                    "enabled": True,
                    "max_concurrent": 2,
                    "providers": ["codex", "codex2", "codex3"],
                }
            },
        }

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _make_worker_log(self, text: str) -> tuple[tempfile.TemporaryDirectory, dict[str, Any]]:
        tmpdir = tempfile.TemporaryDirectory()
        log_path = Path(tmpdir.name) / "worker.log"
        log_path.write_text(text, encoding="utf-8")
        worker = {
            "run_id": "run-test-001",
            "task_id": "ORCH-PROVIDER-QUOTA-SIGNAL-AUTHORITY-001",
            "provider": "codex",
            "agent_id": "codex",
            "log_path": str(log_path),
            "pid": 999999,
        }
        return tmpdir, worker

    def test_completed_worker_detect_failure_returns_none(self) -> None:
        """A worker marked completed must not scan log tail or return failure reason."""
        tmpdir, worker = self._make_worker_log("ERROR: You've hit your usage limit. Try again at 7:00 PM.\n")
        try:
            worker["status"] = "completed"
            self.assertIsNone(worker_failure_policy.detect_worker_failure(worker))
        finally:
            tmpdir.cleanup()

    def test_zero_exit_worker_detect_failure_returns_none_even_with_error_in_log(self) -> None:
        """A worker that exited with code 0 must not return failure reason even if log contains error text."""
        tmpdir, worker = self._make_worker_log(
            "Cloud Run API quota exceeded\n"
            "ERROR: You've hit your usage limit. Try again at 7:00 PM.\n"
            "Completed task successfully.\n"
        )
        try:
            worker["exit_code"] = 0
            worker["runner_status"] = "completed"
            self.assertIsNone(worker_failure_policy.detect_worker_failure(worker))
        finally:
            tmpdir.cleanup()

    def test_completed_worker_mark_provider_dispatch_paused_returns_false(self) -> None:
        """A completed worker must never establish a provider pause."""
        state: dict[str, Any] = {}
        worker = {
            "run_id": "run-1",
            "task_id": "TASK-1",
            "provider": "codex",
            "status": "completed",
            "exit_code": 0,
        }
        paused = worker_failure_policy.mark_provider_dispatch_paused(
            self.config,
            state,
            "codex",
            "ERROR: You've hit your usage limit. Try again at 7:00 PM.",
            failure_kind="quota_terminal",
            pause_kind="quota_terminal",
            worker=worker,
        )
        self.assertFalse(paused)
        self.assertNotIn("codex", state.get("provider_guardrails", {}).get("dispatch_pauses", {}))

    def test_zero_exit_worker_mark_provider_dispatch_paused_returns_false(self) -> None:
        """A zero-exit worker must never establish a provider pause."""
        state: dict[str, Any] = {}
        worker = {
            "run_id": "run-2",
            "task_id": "TASK-2",
            "provider": "codex",
            "status": "running",
            "exit_code": 0,
            "runner_status": "completed",
        }
        paused = worker_failure_policy.mark_provider_dispatch_paused(
            self.config,
            state,
            "codex",
            "402 You have no quota",
            failure_kind="quota_terminal",
            pause_kind="quota_terminal",
            worker=worker,
        )
        self.assertFalse(paused)
        self.assertNotIn("codex", state.get("provider_guardrails", {}).get("dispatch_pauses", {}))

    def test_signal_termination_skips_log_scan_but_is_not_success(self) -> None:
        """A signal is structured termination: skip stale logs, but do not call it success."""
        for runner_status, exit_code, signal_value in (
            ("completed", 0, 15),
            ("failed", -15, 15),
        ):
            with self.subTest(runner_status=runner_status, exit_code=exit_code):
                tmpdir, worker = self._make_worker_log("ERROR: You've hit your usage limit.\n")
                try:
                    worker.update(
                        {
                            "status": "running",
                            "runner_status": runner_status,
                            "exit_code": exit_code,
                            "runner_signal": signal_value,
                        }
                    )
                    self.assertTrue(worker_failure_policy.worker_was_terminated(worker))
                    self.assertTrue(worker_failure_policy.worker_log_scan_should_be_skipped(worker))
                    self.assertFalse(worker_failure_policy.is_structured_successful_worker(worker))
                    self.assertIsNone(worker_failure_policy.detect_worker_failure(worker))

                    state: dict[str, Any] = {}
                    paused = worker_failure_policy.mark_provider_dispatch_paused(
                        self.config,
                        state,
                        "codex",
                        "402 You have no quota",
                        failure_kind="quota_terminal",
                        pause_kind="quota_terminal",
                        worker=worker,
                    )
                    self.assertFalse(paused)
                    self.assertNotIn("codex", state.get("provider_guardrails", {}).get("dispatch_pauses", {}))
                finally:
                    tmpdir.cleanup()

    def test_completed_lifecycle_status_skips_scan_without_proving_success(self) -> None:
        """Lifecycle completion suppresses stale log parsing, even without runner proof."""
        tmpdir, worker = self._make_worker_log("ERROR: You've hit your usage limit.\n")
        try:
            worker["status"] = "completed"
            self.assertTrue(worker_failure_policy.worker_log_scan_should_be_skipped(worker))
            self.assertFalse(worker_failure_policy.is_structured_successful_worker(worker))
            self.assertIsNone(worker_failure_policy.detect_worker_failure(worker))
        finally:
            tmpdir.cleanup()

    def test_nonzero_exit_real_cli_quota_still_detects_and_pauses(self) -> None:
        """A failed nonzero runner must still surface a real CLI quota signal."""
        tmpdir, worker = self._make_worker_log(
            "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage.\n"
        )
        try:
            worker.update({"status": "running", "runner_status": "failed", "exit_code": 1})
            reason = worker_failure_policy.detect_worker_failure(worker)
            self.assertIsNotNone(reason)
            failure = worker_failure_policy.classify_worker_failure(self.config, worker, reason)
            self.assertEqual(failure.get("kind"), "quota_terminal")
            state: dict[str, Any] = {}
            self.assertTrue(
                worker_failure_policy.mark_provider_dispatch_paused(
                    self.config,
                    state,
                    "codex",
                    reason,
                    failure_kind=str(failure["kind"]),
                    pause_kind=str(failure["kind"]),
                    worker=worker,
                )
            )
            self.assertIn("codex", state["provider_guardrails"]["dispatch_pauses"])
            self.assertEqual(state["account_pool_runtime"]["codex"]["state"], "cooldown")
        finally:
            tmpdir.cleanup()

    def test_completed_with_failed_nonzero_exit_real_cli_quota_detects_and_fences(self) -> None:
        """Worker with lifecycle status completed but explicit failed/exit1 must still detect real CLI quota and fence pool."""
        tmpdir, worker = self._make_worker_log(
            "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage.\n"
        )
        try:
            worker.update(
                {
                    "run_id": "run-comp-failed",
                    "task_id": "TASK-COMP-FAIL",
                    "provider": "codex",
                    "status": "completed",
                    "runner_status": "failed",
                    "exit_code": 1,
                }
            )
            self.assertFalse(worker_failure_policy.worker_log_scan_should_be_skipped(worker))
            self.assertFalse(worker_failure_policy.is_structured_successful_worker(worker))
            reason = worker_failure_policy.detect_worker_failure(worker)
            self.assertIsNotNone(reason)
            failure = worker_failure_policy.classify_worker_failure(self.config, worker, reason)
            self.assertEqual(failure.get("kind"), "quota_terminal")
            state: dict[str, Any] = {}
            paused = worker_failure_policy.mark_provider_dispatch_paused(
                self.config,
                state,
                "codex",
                reason,
                failure_kind=str(failure["kind"]),
                pause_kind=str(failure["kind"]),
                worker=worker,
            )
            self.assertTrue(paused)
            self.assertIn("codex", state["provider_guardrails"]["dispatch_pauses"])
            self.assertEqual(state["account_pool_runtime"]["codex"]["state"], "cooldown")
        finally:
            tmpdir.cleanup()

    def test_status_file_failed_nonzero_exit_real_cli_quota_detects_and_fences(self) -> None:
        """Worker with status file indicating failed exit 1 must detect real CLI quota and fence pool."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "runner_status.json"
            status_path.write_text('{"status": "failed", "exit_code": 1}', encoding="utf-8")
            log_path = Path(tmpdir) / "worker.log"
            log_path.write_text(
                "API Error: quota exceeded for current billing cycle\n",
                encoding="utf-8",
            )
            worker = {
                "run_id": "run-stat-file-failed",
                "task_id": "TASK-STAT-FAIL",
                "provider": "codex",
                "status": "completed",
                "runner_status_path": str(status_path),
                "log_path": str(log_path),
            }
            self.assertFalse(worker_failure_policy.worker_log_scan_should_be_skipped(worker))
            self.assertFalse(worker_failure_policy.is_structured_successful_worker(worker))
            reason = worker_failure_policy.detect_worker_failure(worker)
            self.assertIsNotNone(reason)
            failure = worker_failure_policy.classify_worker_failure(self.config, worker, reason)
            self.assertEqual(failure.get("kind"), "quota_terminal")
            state: dict[str, Any] = {}
            paused = worker_failure_policy.mark_provider_dispatch_paused(
                self.config,
                state,
                "codex",
                reason,
                failure_kind=str(failure["kind"]),
                pause_kind=str(failure["kind"]),
                worker=worker,
            )
            self.assertTrue(paused)
            self.assertIn("codex", state["provider_guardrails"]["dispatch_pauses"])
            self.assertEqual(state["account_pool_runtime"]["codex"]["state"], "cooldown")

    def test_cloud_run_quota_exceeded_not_detected_as_worker_failure(self) -> None:
        """Task log containing Cloud Run API quota exceeded string must not be detected as worker failure."""
        for text in (
            "Cloud Run API quota exceeded\n",
            "google.api_core.exceptions.ResourceExhausted: 429 Quota exceeded for quota metric 'Cloud Run API quota exceeded'\n",
            "+ raise RuntimeError('Cloud Run API quota exceeded')\n",
            "assert 'Cloud Run API quota exceeded' in str(exc)\n",
        ):
            tmpdir, worker = self._make_worker_log(text)
            try:
                self.assertIsNone(worker_failure_policy.detect_worker_failure(worker), f"Failed for: {text}")
            finally:
                tmpdir.cleanup()

    def test_cloud_run_quota_exceeded_classified_as_terminal_not_quota_terminal(self) -> None:
        """Cloud Run API quota exceeded must classify as terminal, never quota_terminal."""
        worker = {"provider": "codex"}
        for phrase in (
            "Cloud Run API quota exceeded",
            "google.api_core.exceptions.ResourceExhausted: Quota exceeded for quota metric 'Cloud Run API quota exceeded'",
            "RuntimeError: Cloud Run API quota exceeded",
        ):
            res = worker_failure_policy.classify_worker_failure(self.config, worker, phrase)
            self.assertNotEqual(res.get("kind"), "quota_terminal", f"Misclassified as quota_terminal: {phrase}")
            self.assertFalse(worker_failure_policy.should_pause_dispatch_for_failure_kind(res.get("kind")))

    def test_cloud_run_quota_exceeded_never_fences_account_pool(self) -> None:
        """Cloud Run API quota exceeded must never fence account pool or pause dispatch."""
        worker = {"provider": "codex", "task_id": "TASK-1", "run_id": "run-1"}
        res = worker_failure_policy.classify_worker_failure(self.config, worker, "Cloud Run API quota exceeded")
        kind = res.get("kind")
        self.assertFalse(worker_failure_policy.is_terminal_quota_failure_kind(kind))
        self.assertFalse(worker_failure_policy.should_pause_dispatch_for_failure_kind(kind))

    def test_real_codex_usage_limit_classified_as_quota_terminal(self) -> None:
        """Real Codex CLI usage limit error must classify as quota_terminal."""
        worker = {"provider": "codex"}
        reason = "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 7:00 PM."
        res = worker_failure_policy.classify_worker_failure(self.config, worker, reason)
        self.assertEqual(res.get("kind"), "quota_terminal")
        self.assertTrue(worker_failure_policy.should_pause_dispatch_for_failure_kind(res.get("kind")))

    def test_real_antigravity_quota_classified_as_quota_terminal(self) -> None:
        """Real Antigravity agy quota error must classify as quota_terminal."""
        worker = {"provider": "antigravity3"}
        reason = "Error: Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 2h21m32s."
        res = worker_failure_policy.classify_worker_failure(self.config, worker, reason)
        self.assertEqual(res.get("kind"), "quota_terminal")
        self.assertTrue(worker_failure_policy.should_pause_dispatch_for_failure_kind(res.get("kind")))

    def test_real_claude_session_limit_classified_as_quota_terminal(self) -> None:
        """Real Claude session limit must classify as quota_terminal."""
        worker = {"provider": "claude"}
        reason = "You've hit your session limit · resets 5pm (UTC)"
        res = worker_failure_policy.classify_worker_failure(self.config, worker, reason)
        self.assertEqual(res.get("kind"), "quota_terminal")
        self.assertTrue(worker_failure_policy.should_pause_dispatch_for_failure_kind(res.get("kind")))

    def test_real_copilot_no_quota_classified_as_quota_terminal(self) -> None:
        """Real Copilot no quota error must classify as quota_terminal."""
        worker = {"provider": "copilot"}
        res = worker_failure_policy.classify_worker_failure(self.config, worker, "402 You have no quota")
        self.assertEqual(res.get("kind"), "quota_terminal")
        self.assertTrue(worker_failure_policy.should_pause_dispatch_for_failure_kind(res.get("kind")))

    def test_real_helper_free_tier_quota_classified_as_quota_terminal(self) -> None:
        """Real Helper OAuth free tier quota exceeded must classify as quota_terminal."""
        worker = {"provider": "helper"}
        res = worker_failure_policy.classify_worker_failure(
            self.config, worker, "[API Error: Helper OAuth free tier quota exceeded.]"
        )
        self.assertEqual(res.get("kind"), "quota_terminal")
        self.assertTrue(worker_failure_policy.should_pause_dispatch_for_failure_kind(res.get("kind")))

    def test_generic_provider_quota_exceeded_still_pauses_dispatch(self) -> None:
        """Generic provider 'quota exceeded' must classify as quota_terminal and pause dispatch."""
        worker = {"provider": "claude", "task_id": "TASK-GENERIC-1", "run_id": "run-gen-1"}
        reason = "API Error: quota exceeded for current billing cycle"
        res = worker_failure_policy.classify_worker_failure(self.config, worker, reason)
        self.assertEqual(res.get("kind"), "quota_terminal")
        self.assertTrue(worker_failure_policy.should_pause_dispatch_for_failure_kind(res.get("kind")))

        state: dict[str, Any] = {}
        paused = worker_failure_policy.mark_provider_dispatch_paused(
            self.config,
            state,
            "claude",
            reason,
            failure_kind="quota_terminal",
            pause_kind="quota_terminal",
            worker=worker,
        )
        self.assertTrue(paused)
        self.assertIn("claude", state.get("provider_guardrails", {}).get("dispatch_pauses", {}))

    def test_cloud_run_source_diff_and_quota_error_does_not_pause_dispatch(self) -> None:
        """Cloud Run quota error in diff or source must classify as terminal, not pause dispatch."""
        for phrase in (
            "Cloud Run API quota exceeded",
            "429 Quota exceeded for quota metric 'Cloud Run API quota exceeded'",
            "ResourceExhausted: 429 Quota exceeded for quota metric 'run.googleapis.com/requests'",
        ):
            worker = {"provider": "codex", "task_id": "TASK-CR-1", "run_id": "run-cr-1"}
            res = worker_failure_policy.classify_worker_failure(self.config, worker, phrase)
            self.assertEqual(res.get("kind"), "terminal", f"Failed for phrase: {phrase}")
            self.assertFalse(worker_failure_policy.should_pause_dispatch_for_failure_kind(res.get("kind")))

            state: dict[str, Any] = {}
            paused = worker_failure_policy.mark_provider_dispatch_paused(
                self.config,
                state,
                "codex",
                phrase,
                failure_kind="terminal",
                pause_kind="terminal",
                worker=worker,
            )
            self.assertFalse(paused)
            self.assertNotIn("codex", state.get("provider_guardrails", {}).get("dispatch_pauses", {}))

    def test_runner_status_file_completed_while_state_worker_running_does_not_pause_or_fail(self) -> None:
        """Worker with on-disk runner status completed must not detect failure or pause dispatch even if log has quota."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "runner_status.json"
            status_path.write_text('{"status": "completed", "exit_code": 0}', encoding="utf-8")
            log_path = Path(tmpdir) / "worker.log"
            log_path.write_text("ERROR: You've hit your usage limit. Try again at 7:00 PM.\n", encoding="utf-8")

            worker = {
                "run_id": "run-runner-snap",
                "task_id": "TASK-SNAP",
                "provider": "codex",
                "status": "running",
                "runner_status_path": str(status_path),
                "log_path": str(log_path),
            }
            self.assertTrue(worker_failure_policy.is_structured_successful_worker(worker))
            self.assertIsNone(worker_failure_policy.detect_worker_failure(worker))

            state: dict[str, Any] = {}
            paused = worker_failure_policy.mark_provider_dispatch_paused(
                self.config,
                state,
                "codex",
                "402 You have no quota",
                failure_kind="quota_terminal",
                pause_kind="quota_terminal",
                worker=worker,
            )
            self.assertFalse(paused)
            self.assertNotIn("codex", state.get("provider_guardrails", {}).get("dispatch_pauses", {}))

    def test_real_cli_quota_fences_account_pool(self) -> None:
        """Real CLI quota error must fence the account pool and establish a cooldown."""
        state: dict[str, Any] = {
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "provider": "codex",
                    "agent_id": "codex",
                    "task_id": "TASK-1",
                    "status": "running",
                    "pid": 999999,
                },
                "run-2": {
                    "run_id": "run-2",
                    "provider": "codex2",
                    "agent_id": "codex2",
                    "task_id": "TASK-2",
                    "status": "running",
                    "pid": 999998,
                },
            }
        }
        triggering_worker = state["workers"]["run-1"]
        reason = "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 7:00 PM."

        paused = worker_failure_policy.mark_provider_dispatch_paused(
            self.config,
            state,
            "codex",
            reason,
            task_id="TASK-1",
            worker_run_id="run-1",
            failure_kind="quota_terminal",
            pause_kind="quota_terminal",
            worker=triggering_worker,
        )
        self.assertTrue(paused)
        self.assertIn("codex", state.get("account_pool_runtime", {}))
        self.assertEqual(state["account_pool_runtime"]["codex"]["state"], "cooldown")
        self.assertEqual(state["account_pool_runtime"]["codex"]["effective_concurrency"], 0)

        fenced = worker_failure_policy.fence_account_pool_workers(self.config, state, triggering_worker, reason)
        self.assertEqual(fenced, 1)
        sibling = state["workers"]["run-2"]
        self.assertIn(sibling["status"], {"reassigned", "failed"})
        self.assertIn("codex", str(sibling.get("last_error") or ""))

    def test_real_google_api_core_resource_exhausted_still_classified_as_capacity(self) -> None:
        """Generic ResourceExhausted (non-Cloud Run) must be classified as capacity_retryable, not swallowed."""
        worker = {"provider": "gemini"}
        reason = "google.api_core.exceptions.ResourceExhausted: 429 Resource has been exhausted (e.g. check quota)."
        res = worker_failure_policy.classify_worker_failure(self.config, worker, reason)
        self.assertEqual(res.get("kind"), "capacity_retryable")
        self.assertTrue(worker_failure_policy.should_pause_dispatch_for_failure_kind(res.get("kind")))


class OwnerProviderPreferenceTests(unittest.TestCase):
    """The owner lane has to be able to prefer a provider, not just the idlest one.

    Load balancing answers "who is least busy"; it cannot answer "who should
    implement". On the live board the two disagree constantly: an Antigravity
    lane holding nine open tasks with an idle worker slot always sorted behind a
    Codex lane holding one, so implementation work kept landing on the lane that
    is meant to integrate and deploy it. Reordering `owner_fallbacks` cannot fix
    that either -- order is only consulted after load, so it decides ties and
    nothing else.
    """

    PREFERENCE = {
        "enabled": True,
        "preferred_providers": ["antigravity", "claude"],
        "task_classes": ["implementation", "remediation", "documentation"],
    }
    POOL = ["Codex", "Antigravity", "Claude"]

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        # `agent_dispatch_loads` reads the real event queue; an empty one keeps
        # "pending delivery" a measured zero rather than an unreadable path.
        (self.root / "event_queue.jsonl").write_text("", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _config(self, preference: dict[str, Any] | None = "default") -> dict[str, Any]:
        config: dict[str, Any] = {
            "paths": {
                "event_queue": str(self.root / "event_queue.jsonl"),
                "status_file": str(self.root / "ai-status.json"),
                "activity_log": str(self.root / "activity.jsonl"),
            },
            "providers": {
                "antigravity": {"delivery_mode": "antigravity"},
                # The alias the live fleet actually runs: a distinct provider key
                # whose delivery mode is still the agy adapter.
                "antigravity2": {"delivery_mode": "antigravity"},
                "claude": {"delivery_mode": "claude_cli"},
                "codex": {
                    "delivery_mode": "codex",
                    "codex": {"codex_home": str(self.root / "codex-home")},
                },
            },
            "agents": {
                "antigravity": {
                    "display_name": "Antigravity",
                    "provider": "antigravity",
                    "adapter": "antigravity",
                    "account_pool": "antigravity_main",
                },
                "antigravity2": {
                    "display_name": "Antigravity2",
                    "provider": "antigravity2",
                    "adapter": "antigravity",
                    "account_pool": "antigravity_main",
                },
                "claude": {
                    "display_name": "Claude",
                    "provider": "claude",
                    "adapter": "claude_cli",
                    "account_pool": "claude_main",
                },
                "codex": {
                    "display_name": "Codex",
                    "provider": "codex",
                    "adapter": "codex",
                    "account_pool": "codex_bjoe",
                },
                "antigravity_slot_1": {
                    "display_name": "antigravity_slot_1",
                    "provider": "antigravity",
                    "adapter": "antigravity",
                    "account_pool": "antigravity_main",
                    "dispatch_slot_for_pool": "antigravity_main",
                },
                "antigravity_slot_2": {
                    "display_name": "antigravity_slot_2",
                    "provider": "antigravity",
                    "adapter": "antigravity",
                    "account_pool": "antigravity_main",
                    "dispatch_slot_for_pool": "antigravity_main",
                },
                "claude_slot_1": {
                    "display_name": "claude_slot_1",
                    "provider": "claude",
                    "adapter": "claude_cli",
                    "account_pool": "claude_main",
                    "dispatch_slot_for_pool": "claude_main",
                },
                "codex_slot_1": {
                    "display_name": "codex_slot_1",
                    "provider": "codex",
                    "adapter": "codex",
                    "account_pool": "codex_bjoe",
                    "dispatch_slot_for_pool": "codex_bjoe",
                },
            },
            "account_pools": {
                "antigravity_main": {"enabled": True, "max_concurrent": 2},
                "claude_main": {"enabled": True, "max_concurrent": 1},
                "codex_bjoe": {"enabled": True, "max_concurrent": 1},
            },
            "ready_dispatcher": {},
        }
        if preference == "default":
            preference = dict(self.PREFERENCE)
        if preference is not None:
            config["ready_dispatcher"]["owner_provider_preference"] = preference
        return config

    @staticmethod
    def _status(counts: dict[str, int]) -> dict[str, Any]:
        tasks: list[dict[str, Any]] = []
        for owner, count in counts.items():
            tasks.extend(
                {"id": f"T-{owner}-{index}", "status": "in_progress", "owner": owner}
                for index in range(count)
            )
        return {"tasks": tasks}

    #: The account pool each fixture slot belongs to. A live worker record
    #: captures this at dispatch time (`supervisor.record_worker` stores
    #: `agent_quota_group_id`), and pool accounting reads it, so a fixture that
    #: omitted it would under-report every shared pool.
    POOL_OF_SLOT = {
        "antigravity_slot_1": "antigravity_main",
        "antigravity_slot_2": "antigravity_main",
        "claude_slot_1": "claude_main",
        "codex_slot_1": "codex_bjoe",
    }

    @classmethod
    def _state(cls, busy_slots: dict[str, str] | None = None) -> dict[str, Any]:
        workers = {
            f"run-{index}": {
                "status": "running",
                "agent_id": slot_id,
                "logical_agent_id": logical_id,
                "quota_group": cls.POOL_OF_SLOT.get(slot_id, ""),
                "task_id": f"T-BUSY-{index}",
                "request_snapshot": {"reason": "owned_ready_dispatch"},
            }
            for index, (slot_id, logical_id) in enumerate(sorted((busy_slots or {}).items()))
        }
        return {"workers": workers, "queue": {"events": {}}, "provider_guardrails": {"dispatch_pauses": {}}}

    # A task the preference is allowed to act on, and a load picture where load
    # balancing on its own would pick Codex.
    TASK = {"id": "T-1", "status": "todo", "task_class": "implementation"}
    LOAD = {"Antigravity": 9, "Claude": 7, "Codex": 1}

    def _select(self, config: dict[str, Any], **kwargs: Any) -> str | None:
        params: dict[str, Any] = {
            "exclude": set(),
            "state": self._state(),
            "task": dict(self.TASK),
            "status": self._status(self.LOAD),
            "role": "owner",
        }
        params.update(kwargs)
        pool = params.pop("pool", self.POOL)
        return worker_failure_policy.first_viable_agent(config, pool, **params)

    def test_busier_preferred_owner_with_a_free_slot_beats_idle_codex(self) -> None:
        """The whole point: open task count is not capacity."""
        self.assertEqual(self._select(self._config()), "Claude")

    def test_preferred_group_keeps_its_own_load_balancing(self) -> None:
        chosen = self._select(
            self._config(),
            pool=["Antigravity", "Claude"],
            status=self._status({"Antigravity": 9, "Claude": 2}),
        )
        self.assertEqual(chosen, "Claude")

    def test_saturated_preferred_group_falls_back_to_codex(self) -> None:
        """Every preferred slot is running, so the preference has nothing to offer."""
        state = self._state(
            {
                "antigravity_slot_1": "antigravity",
                "antigravity_slot_2": "antigravity",
                "claude_slot_1": "claude",
            }
        )
        self.assertEqual(self._select(self._config(), state=state), "Codex")

    def test_partially_loaded_preferred_lane_is_still_preferred(self) -> None:
        """One of two Antigravity slots is busy; the other can start work now."""
        state = self._state({"antigravity_slot_1": "antigravity", "claude_slot_1": "claude"})
        self.assertEqual(self._select(self._config(), state=state), "Antigravity")

    def test_provider_alias_is_resolved_through_config_not_agent_names(self) -> None:
        """Antigravity2 runs on the `antigravity2` provider key, not `antigravity`."""
        chosen = self._select(
            self._config(),
            pool=["Codex", "Antigravity2"],
            status=self._status({"Antigravity2": 9, "Codex": 1}),
        )
        self.assertEqual(chosen, "Antigravity2")

    def test_reviewer_selection_is_untouched(self) -> None:
        self.assertEqual(self._select(self._config(), role="reviewer"), "Codex")

    def test_deployment_task_classes_keep_their_owner_rules(self) -> None:
        for task_class in ("runtime_release", "rollout", "sidecar"):
            with self.subTest(task_class=task_class):
                task = {"id": "T-2", "status": "todo", "task_class": task_class}
                self.assertEqual(self._select(self._config(), task=task), "Codex")

    def test_task_without_a_class_is_not_assumed_to_be_implementation(self) -> None:
        self.assertEqual(self._select(self._config(), task={"id": "T-3", "status": "todo"}), "Codex")

    def test_human_gate_and_non_dispatchable_tasks_are_out_of_scope(self) -> None:
        for task in (
            {"id": "T-4", "status": "blocked", "task_class": "human_gate"},
            {"id": "T-5", "status": "todo", "task_class": "implementation", "non_dispatchable": True},
        ):
            with self.subTest(task=task["id"]):
                # A human gate is never dispatchable at all, so no owner is viable.
                self.assertIn(self._select(self._config(), task=task), {None, "Codex"})

    def test_frozen_closeout_ordering_is_completely_unchanged(self) -> None:
        """An approved head is not an implementation-lane question.

        The task class is still eligible and the preferred lane still has a free
        slot -- the only difference is that this owner already wrote the commit
        under review. Preferring a different lane here would hand somebody
        else's frozen head to an agent that must not touch it, so the answer
        must be exactly what the selector produced before the preference
        existed: the least loaded viable candidate.
        """
        for label, overrides in (
            ("review_approved", {"status": "review_approved"}),
            ("approved_head", {"status": "in_progress", "approved_head": "a" * 40}),
            ("merge_route", {"status": "in_progress", "merge_route": {"route": "queued"}}),
        ):
            with self.subTest(label=label):
                task = dict(self.TASK, **overrides)
                self.assertEqual(self._select(self._config(), task=task), "Codex")
                self.assertEqual(
                    self._select(self._config(preference=None), task=task), "Codex"
                )

    def test_frozen_closeout_detection_reads_the_configured_finalize_statuses(self) -> None:
        config = self._config()
        config["ready_dispatcher"]["finalize_statuses"] = ["ready_to_merge"]
        frozen = worker_failure_policy.task_closeout_owner_is_frozen
        self.assertTrue(frozen(config, {"status": "ready_to_merge"}))
        # Always frozen, however the fleet spells its own finalize statuses.
        self.assertTrue(frozen(config, {"status": "review_approved"}))
        self.assertFalse(frozen(config, {"status": "in_progress"}))
        self.assertFalse(frozen(config, {"status": "todo"}))
        self.assertFalse(frozen(config, {}))

    def test_ordinary_owned_work_is_not_frozen_by_the_new_guard(self) -> None:
        """The guard must not freeze the normal, un-approved owner fallback."""
        applies = worker_failure_policy.owner_preference_applies_to_task
        for task_status in ("todo", "in_progress", "blocked", "review"):
            with self.subTest(status=task_status):
                self.assertTrue(applies(self._config(), dict(self.TASK, status=task_status), "owner"))
        for task_status in ("todo", "in_progress"):
            with self.subTest(select=task_status):
                task = dict(self.TASK, status=task_status)
                self.assertEqual(self._select(self._config(), task=task), "Claude")

    def test_unconfigured_preference_reproduces_load_balancing(self) -> None:
        self.assertEqual(self._select(self._config(preference=None)), "Codex")

    def test_disabled_preference_reproduces_load_balancing(self) -> None:
        preference = dict(self.PREFERENCE, enabled=False)
        self.assertEqual(self._select(self._config(preference=preference)), "Codex")

    def test_missing_runtime_state_never_claims_capacity(self) -> None:
        """No state means no capacity evidence, so the preference stays out of it."""
        self.assertEqual(self._select(self._config(), state=None), "Codex")

    def test_unreadable_event_queue_never_claims_capacity(self) -> None:
        config = self._config()
        config["paths"].pop("event_queue")
        self.assertEqual(self._select(config), "Codex")

    def test_paused_preferred_agents_are_excluded_before_the_preference(self) -> None:
        state = self._state()
        state["provider_guardrails"]["dispatch_pauses"] = {
            provider: {
                "provider": provider,
                "blocked_until": "2099-01-01T00:00:00Z",
                "reason": f"{provider} usage limit reached",
            }
            for provider in ("antigravity", "claude")
        }
        self.assertEqual(self._select(self._config(), state=state), "Codex")

    def test_excluded_owners_stay_excluded_however_preferred(self) -> None:
        chosen = self._select(self._config(), exclude={"Antigravity", "Claude"})
        self.assertEqual(chosen, "Codex")

    def test_account_pool_exclusion_still_wins_over_the_preference(self) -> None:
        chosen = self._select(self._config(), exclude_pools={"antigravity_main", "claude_main"})
        self.assertEqual(chosen, "Codex")

    def test_single_candidate_checks_still_skip_load_and_preference(self) -> None:
        """A one-name list asks "can this agent take it?" and must stay that cheap."""
        config = self._config()
        with mock.patch.object(
            worker_failure_policy,
            "dispatch_slot_loads",
            side_effect=AssertionError("must not probe capacity"),
        ):
            self.assertEqual(self._select(config, pool=["Codex"]), "Codex")
            self.assertEqual(self._select(config, pool=self.POOL, balance_load=False), "Codex")

    def test_settings_defaults_leave_the_preference_inert(self) -> None:
        settings = worker_failure_policy.owner_provider_preference_settings({})
        self.assertEqual(settings["preferred_providers"], [])
        self.assertEqual(
            settings["task_classes"],
            ["implementation", "remediation", "documentation"],
        )
        self.assertIs(settings["enabled"], True)
        self.assertEqual(worker_failure_policy.preferred_owner_provider_ids({}), set())

    def test_provider_identity_covers_provider_key_and_adapter(self) -> None:
        config = self._config()
        self.assertEqual(
            worker_failure_policy.agent_provider_identity_ids(config, "Antigravity2"),
            {"antigravity", "antigravity2"},
        )
        self.assertEqual(
            worker_failure_policy.agent_provider_identity_ids(config, "Claude"),
            {"claude", "claude_cli"},
        )
        self.assertEqual(
            worker_failure_policy.agent_provider_identity_ids(config, "Codex"),
            {"codex"},
        )


class OwnerPreferenceSharedPoolCapacityTests(unittest.TestCase):
    """A logical agent's own load does not describe the account behind it.

    Antigravity, Antigravity2 and Antigravity3 are three ownership roles on one
    real account with one five-slot budget. The preference asked only
    `agent_dispatch_loads[<this agent>] < agent_dispatch_capacity(<this
    agent>)`, and neither side of that comparison can see the other two
    aliases: with all five slots claimed by undelivered Antigravity2 events,
    Antigravity and Antigravity3 each measured a load of zero, took rank 0, and
    outranked a candidate that genuinely had somewhere to run. The result is a
    queue that no one can start, in front of a lane that was idle.
    """

    PREFERENCE = {
        "enabled": True,
        "preferred_providers": ["antigravity"],
        "task_classes": ["implementation", "remediation", "documentation"],
    }
    POOL = ["Antigravity", "Antigravity3", "Codex"]
    TASK = {"id": "T-1", "status": "todo", "task_class": "implementation"}
    #: Load balancing alone would answer Codex, so any other answer is the
    #: preference speaking.
    LOAD = {"Antigravity": 9, "Antigravity3": 9, "Codex": 1}
    SLOT_LIMIT = 5

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.queue_path = self.root / "event_queue.jsonl"
        self.queue_path.write_text("", encoding="utf-8")
        # Supervisor-owned names reach this module through the scope its
        # entrypoints sync; tests that read them directly must sync too.
        worker_failure_policy._sync_supervisor_scope()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _config(self, *, max_concurrent: int | None = SLOT_LIMIT) -> dict[str, Any]:
        agents: dict[str, Any] = {
            "codex": {
                "display_name": "Codex",
                "provider": "codex",
                "adapter": "codex",
                "account_pool": "codex_bjoe",
            },
            "codex_slot_1": {
                "display_name": "codex_slot_1",
                "provider": "codex",
                "adapter": "codex",
                "account_pool": "codex_bjoe",
                "dispatch_slot_for_pool": "codex_bjoe",
            },
        }
        # Three ownership roles, one account, one shared slot set.
        for index, alias in enumerate(("antigravity", "antigravity2", "antigravity3"), start=1):
            agents[alias] = {
                "display_name": f"Antigravity{index if index > 1 else ''}",
                "provider": alias,
                "adapter": "antigravity",
                "account_pool": "agy_main",
            }
        for slot in range(1, self.SLOT_LIMIT + 1):
            agents[f"agy_slot_{slot}"] = {
                "display_name": f"agy_slot_{slot}",
                "provider": "antigravity",
                "adapter": "antigravity",
                "account_pool": "agy_main",
                "dispatch_slot_for_pool": "agy_main",
            }
        pool: dict[str, Any] = {"enabled": True}
        if max_concurrent is not None:
            pool["max_concurrent"] = max_concurrent
        return {
            "paths": {
                "event_queue": str(self.queue_path),
                "status_file": str(self.root / "ai-status.json"),
                "activity_log": str(self.root / "activity.jsonl"),
            },
            "providers": {
                "antigravity": {"delivery_mode": "antigravity"},
                "antigravity2": {"delivery_mode": "antigravity"},
                "antigravity3": {"delivery_mode": "antigravity"},
                "codex": {
                    "delivery_mode": "codex",
                    "codex": {"codex_home": str(self.root / "codex-home")},
                },
            },
            "agents": agents,
            "account_pools": {
                "agy_main": pool,
                "codex_bjoe": {"enabled": True, "max_concurrent": 1},
            },
            "ready_dispatcher": {"owner_provider_preference": dict(self.PREFERENCE)},
        }

    def _queue(self, events: list[dict[str, Any]]) -> None:
        self.queue_path.write_text(
            "".join(f"{json.dumps(event)}\n" for event in events), encoding="utf-8"
        )

    @staticmethod
    def _pending(event_id: str, target: str = "Antigravity2") -> dict[str, Any]:
        return {
            "event_id": event_id,
            "target_agent": target,
            "target_display_name": target,
            "provider": "antigravity",
            "reason": "owned_ready_dispatch",
            "message": "queued but not delivered",
        }

    @staticmethod
    def _worker(index: int, *, queue_event_id: str = "") -> dict[str, Any]:
        return {
            "run_id": f"run-{index}",
            "status": "running",
            "agent_id": f"agy_slot_{index}",
            "logical_agent_id": "antigravity2",
            "quota_group": "agy_main",
            "queue_event_id": queue_event_id,
            "task_id": f"T-BUSY-{index}",
            "request_snapshot": {"reason": "owned_ready_dispatch"},
        }

    def _state(self, workers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return {
            "workers": {worker["run_id"]: worker for worker in (workers or [])},
            "queue": {"events": {}},
            "provider_guardrails": {"dispatch_pauses": {}},
        }

    def _status(self) -> dict[str, Any]:
        tasks: list[dict[str, Any]] = []
        for owner, count in self.LOAD.items():
            tasks.extend(
                {"id": f"T-{owner}-{index}", "status": "in_progress", "owner": owner}
                for index in range(count)
            )
        return {"tasks": tasks}

    def _select(self, config: dict[str, Any], state: dict[str, Any]) -> str | None:
        return worker_failure_policy.first_viable_agent(
            config,
            list(self.POOL),
            set(),
            state=state,
            task=dict(self.TASK),
            status=self._status(),
            role="owner",
        )

    def test_pool_saturated_by_another_alias_pending_falls_back_to_a_free_lane(self) -> None:
        """The defect, stated as capacity: five queued, zero deliverable."""
        self._queue([self._pending(f"evt-{index}") for index in range(self.SLOT_LIMIT)])
        config = self._config()
        state = self._state()

        # Neither preferred alias has run anything, so per-agent accounting on
        # its own still reports plenty of room ...
        loads = worker_failure_policy.dispatch_slot_loads(config, state)
        self.assertEqual(loads.get("Antigravity"), None)
        self.assertEqual(len(loads.get("Antigravity2") or []), self.SLOT_LIMIT)
        # ... while the account they all share has nothing left.
        usage = worker_failure_policy.dispatch_pool_usage(config, state)
        self.assertEqual(usage.get("agy_main"), self.SLOT_LIMIT)
        for alias in ("Antigravity", "Antigravity3"):
            with self.subTest(alias=alias):
                self.assertFalse(
                    worker_failure_policy.agent_has_free_dispatch_slot(
                        config, alias, loads, state=state, pool_usage=usage
                    )
                )

        self.assertEqual(self._select(config, state), "Codex")

    def test_one_free_slot_in_the_shared_pool_is_still_preferred(self) -> None:
        """The guard withholds the preference on saturation, not in general."""
        self._queue([self._pending(f"evt-{index}") for index in range(self.SLOT_LIMIT - 1)])
        config = self._config()
        state = self._state()

        usage = worker_failure_policy.dispatch_pool_usage(config, state)
        self.assertEqual(usage.get("agy_main"), self.SLOT_LIMIT - 1)
        # Antigravity is the busier of the two preferred aliases by open task
        # count only, which is exactly the bookkeeping the preference overrides.
        self.assertEqual(self._select(config, state), "Antigravity")

    def test_mixed_active_and_pending_never_charge_one_dispatch_twice(self) -> None:
        """A delivered event is still in the queue file while its worker runs.

        Three running workers plus two queued events, one of which is the event
        that started the third worker, is four claims on the pool -- not five.
        Counting it twice would report the pool full and silently disable the
        preference one dispatch early.
        """
        self._queue([self._pending("evt-delivered"), self._pending("evt-waiting")])
        config = self._config()
        state = self._state(
            [
                self._worker(1),
                self._worker(2),
                self._worker(3, queue_event_id="evt-delivered"),
            ]
        )

        usage = worker_failure_policy.dispatch_pool_usage(config, state)
        self.assertEqual(usage.get("agy_main"), 4)
        self.assertEqual(self._select(config, state), "Antigravity")

        # The fifth claim is the one that closes the pool.
        self._queue(
            [self._pending("evt-delivered"), self._pending("evt-waiting"), self._pending("evt-last")]
        )
        self.assertEqual(
            worker_failure_policy.dispatch_pool_usage(config, state).get("agy_main"), 5
        )
        self.assertEqual(self._select(config, state), "Codex")

    def test_the_lower_of_the_two_ceilings_decides(self) -> None:
        """A recovering pool has five slots and permission to use one of them.

        Nothing is running, so the active-only quota check that guards dispatch
        lets every alias through; the single undelivered event is what actually
        consumes the runtime budget.
        """
        self._queue([self._pending("evt-0")])
        config = self._config()
        state = self._state()
        state["account_pool_runtime"] = {
            "agy_main": {"state": "recovering", "effective_concurrency": 1, "generation": 3}
        }

        # Five configured slots, none of them busy: the per-agent ceiling alone
        # would call this lane wide open.
        self.assertEqual(worker_failure_policy.agent_dispatch_capacity(config, "antigravity"), 5)
        self.assertIsNone(
            worker_failure_policy.agent_auto_dispatch_block_reason(config, state, "antigravity")
        )
        self.assertEqual(
            worker_failure_policy.account_pool_effective_concurrency(config, state, "antigravity"), 1
        )
        self.assertEqual(
            worker_failure_policy.dispatch_pool_usage(config, state).get("agy_main"), 1
        )
        self.assertEqual(self._select(config, state), "Codex")

    def test_an_effective_limit_of_zero_is_a_limit_not_an_absence(self) -> None:
        """`if limit and used >= limit` would read 0 as "no ceiling"."""
        import supervisor

        config = self._config()
        state = self._state()
        usage = worker_failure_policy.dispatch_pool_usage(config, state)
        self.assertEqual(usage, {})
        original_limit = supervisor.account_pool_effective_concurrency

        def account_limit(config, state, agent_id, provider_report=None):
            if supervisor.agent_quota_group_id(config, agent_id) == "agy_main":
                return 0
            return original_limit(config, state, agent_id, provider_report)

        with mock.patch.object(
            supervisor, "account_pool_effective_concurrency", side_effect=account_limit
        ):
            self.assertFalse(
                worker_failure_policy.account_pool_has_free_dispatch_slot(
                    config, state, "antigravity", usage
                )
            )
            # Only the recovering account has zero capacity, not every account.
            self.assertEqual(self._select(config, state), "Codex")

    def test_a_pool_with_no_declared_ceiling_keeps_the_previous_behaviour(self) -> None:
        """An unpooled or uncapped configuration is bounded by slots alone."""
        config = self._config(max_concurrent=None)
        state = self._state()

        self.assertIsNone(
            worker_failure_policy.account_pool_effective_concurrency(config, state, "antigravity")
        )
        self.assertEqual(self._select(config, state), "Antigravity")

    def test_unmeasurable_pool_usage_never_claims_capacity(self) -> None:
        """No evidence about the shared account is not evidence of an idle one."""
        config = self._config()
        self.assertIsNone(worker_failure_policy.dispatch_pool_usage(config, None))
        self.assertFalse(
            worker_failure_policy.account_pool_has_free_dispatch_slot(
                config, self._state(), "antigravity", None
            )
        )
        unreadable = self._config()
        unreadable["paths"].pop("event_queue")
        self.assertIsNone(worker_failure_policy.dispatch_pool_usage(unreadable, self._state()))
        self.assertEqual(self._select(unreadable, self._state()), "Codex")

    def test_a_configured_queue_path_pointing_at_nothing_is_not_an_empty_queue(self) -> None:
        """A set path is not a read queue.

        `load_jsonl` returns [] for a file that does not exist, so resolving the
        configured path proves only that an operator wrote it down. A pool whose
        pending events cannot be counted has to rank as unmeasured; reporting
        zero would hand rank 0 to whichever lane the queue was hiding.
        """
        config = self._config()
        state = self._state()
        self.queue_path.unlink()

        # The path is still configured and still resolvable ...
        self.assertEqual(
            worker_failure_policy.config_path(config, "event_queue"), self.queue_path
        )
        # ... and the queue reader is happy to call that zero pending events.
        import supervisor

        self.assertEqual(supervisor.queued_quota_group_counts(config, state), {})
        # The preference must not be built on that zero.
        self.assertIsNone(worker_failure_policy.dispatch_pool_usage(config, state))
        self.assertEqual(self._select(config, state), "Codex")

    def test_a_queue_that_cannot_be_read_is_not_an_empty_queue(self) -> None:
        """The same conclusion when the path exists but the bytes are refused."""
        config = self._config()
        state = self._state()

        # A path that is not a file at all: deterministic for every uid.
        self.queue_path.unlink()
        self.queue_path.mkdir()
        self.assertIsNone(worker_failure_policy.dispatch_pool_usage(config, state))
        self.assertEqual(self._select(config, state), "Codex")
        self.queue_path.rmdir()

        # A real permission denial, asserted only where this process can
        # actually be denied -- running as root would make the mode a no-op and
        # the assertion a decoration.
        self.queue_path.write_text("", encoding="utf-8")
        self.queue_path.chmod(0o000)
        try:
            self.queue_path.read_text(encoding="utf-8")
        except PermissionError:
            self.assertIsNone(worker_failure_policy.dispatch_pool_usage(config, state))
            self.assertEqual(self._select(config, state), "Codex")
        finally:
            self.queue_path.chmod(0o600)

    def test_a_pool_without_a_quota_ceiling_is_still_bounded_by_its_slots(self) -> None:
        """An absent quota is not an absent pool.

        With no `max_concurrent` the effective limit is None, which used to be
        read as "unbounded" and returned True on the spot. The five processes
        behind the account do not disappear because nobody wrote a number: all
        five are spoken for here, and the two idle aliases still have nowhere
        to run.
        """
        self._queue([self._pending(f"evt-{index}") for index in range(self.SLOT_LIMIT)])
        config = self._config(max_concurrent=None)
        state = self._state()

        self.assertIsNone(
            worker_failure_policy.account_pool_effective_concurrency(config, state, "antigravity")
        )
        self.assertEqual(
            worker_failure_policy.account_pool_physical_capacity(config, "antigravity"),
            self.SLOT_LIMIT,
        )
        self.assertEqual(
            worker_failure_policy.dispatch_pool_usage(config, state).get("agy_main"),
            self.SLOT_LIMIT,
        )
        for alias in ("Antigravity", "Antigravity3"):
            with self.subTest(alias=alias):
                self.assertFalse(
                    worker_failure_policy.account_pool_has_free_dispatch_slot(
                        config, state, alias, worker_failure_policy.dispatch_pool_usage(config, state)
                    )
                )
        self.assertEqual(self._select(config, state), "Codex")

    def test_a_quota_ceiling_above_the_slot_count_does_not_create_slots(self) -> None:
        """Permission to run ten is not ten processes.

        Comparing usage against the quota alone would find five of ten used and
        call the account free, when the account has exactly five slots and every
        one of them is claimed. The lower of the two ceilings is the real one.
        """
        self._queue([self._pending(f"evt-{index}") for index in range(self.SLOT_LIMIT)])
        config = self._config(max_concurrent=10)
        state = self._state()

        self.assertEqual(
            worker_failure_policy.account_pool_effective_concurrency(config, state, "antigravity"),
            10,
        )
        self.assertEqual(
            worker_failure_policy.account_pool_physical_capacity(config, "antigravity"),
            self.SLOT_LIMIT,
        )
        # Each alias reports the shared five as if the five were its own, which
        # is why summing the per-agent capacities would answer fifteen.
        for alias in ("antigravity", "antigravity2", "antigravity3"):
            self.assertEqual(worker_failure_policy.agent_dispatch_capacity(config, alias), 5)

        self.assertEqual(self._select(config, state), "Codex")

    def test_room_under_both_ceilings_keeps_the_preference(self) -> None:
        """Partial capacity is capacity; the guard withholds only on saturation."""
        self._queue([self._pending(f"evt-{index}") for index in range(3)])
        config = self._config(max_concurrent=10)
        state = self._state()

        self.assertEqual(
            worker_failure_policy.dispatch_pool_usage(config, state).get("agy_main"), 3
        )
        self.assertTrue(
            worker_failure_policy.account_pool_has_free_dispatch_slot(
                config, state, "Antigravity", worker_failure_policy.dispatch_pool_usage(config, state)
            )
        )
        self.assertEqual(self._select(config, state), "Antigravity")

    def test_a_configured_ceiling_of_zero_is_a_ceiling(self) -> None:
        """The same rule as the runtime zero, stated in configuration.

        Taking the lower of two ceilings must not let a configured zero be
        rescued by a slot count of five.
        """
        config = self._config(max_concurrent=0)
        state = self._state()

        self.assertEqual(
            worker_failure_policy.account_pool_effective_concurrency(config, state, "antigravity"), 0
        )
        self.assertEqual(worker_failure_policy.dispatch_pool_usage(config, state), {})
        self.assertFalse(
            worker_failure_policy.account_pool_has_free_dispatch_slot(
                config, state, "Antigravity", {}
            )
        )
        self.assertEqual(self._select(config, state), "Codex")


def _git_run(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


class QuotaSiblingFencingDirtyHandoffTests(unittest.TestCase):
    """End-to-end regression tests for sibling quota fencing and dirty worktree handoff recovery."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        _git_run(self.repo, "init", "--quiet")
        _git_run(self.repo, "config", "user.email", "test@pantheon.local")
        _git_run(self.repo, "config", "user.name", "Test Runner")
        (self.repo / "README.md").write_text("base repository content\n", encoding="utf-8")
        _git_run(self.repo, "add", "README.md")
        _git_run(self.repo, "commit", "--quiet", "-m", "initial base commit")
        _git_run(self.repo, "branch", "-M", "dev")

        self.remote = self.root / "origin.git"
        self.remote.mkdir(parents=True, exist_ok=True)
        _git_run(self.remote, "init", "--bare", "--quiet")
        _git_run(self.repo, "remote", "add", "origin", str(self.remote))
        _git_run(self.repo, "push", "--quiet", "-u", "origin", "dev")

        self.worktree = self.root / "worktrees" / "pantheon" / "task-sibling-001"
        self.worktree.parent.mkdir(parents=True, exist_ok=True)
        _git_run(self.repo, "worktree", "add", "-b", "task/TASK-SIBLING-001", str(self.worktree), "dev")
        _git_run(self.repo, "push", "--quiet", "-u", "origin", "task/TASK-SIBLING-001")
        self.head_sha = _git_run(self.worktree, "rev-parse", "HEAD")

        self.status_file = self.root / "ai-status.json"
        self.activity_log = self.root / "activity.jsonl"
        self.event_queue = self.root / "event_queue.jsonl"
        self.event_queue.write_text("", encoding="utf-8")

        self.status_data: dict[str, Any] = {
            "project": "pantheon",
            "agents": [
                {"name": "Antigravity", "status": "running"},
                {"name": "Antigravity2", "status": "running"},
                {"name": "Codex", "status": "idle"},
                {"name": "Codex2", "status": "idle"},
                {"name": "Claude", "status": "idle"},
            ],
            "tasks": [
                {
                    "id": "TASK-SIBLING-001",
                    "title": "Sibling Task",
                    "owner": "Antigravity2",
                    "reviewer": "Codex2",
                    "status": "todo",
                    "repository": "pantheon",
                },
                {
                    "id": "TASK-TRIGGER-001",
                    "title": "Triggering Task",
                    "owner": "Antigravity",
                    "reviewer": "Codex",
                    "status": "todo",
                    "repository": "pantheon",
                },
                {
                    "id": "TASK-INDEPENDENT-001",
                    "title": "Independent Task",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "status": "todo",
                    "repository": "pantheon",
                },
            ],
            "handoffs": [],
            "blockers": [],
        }
        self.status_file.write_text(json.dumps(self.status_data, indent=2), encoding="utf-8")
        (self.root / "approval_queue.json").write_text(json.dumps({"pending": [], "history": []}), encoding="utf-8")

        self.config: dict[str, Any] = {
            "paths": {
                "status_file": str(self.status_file),
                "state_file": str(self.root / "runtime-state.json"),
                "activity_log": str(self.activity_log),
                "event_queue": str(self.event_queue),
                "approval_queue": str(self.root / "approval_queue.json"),
                "approval_history": str(self.root / "approval_history.json"),
                "delivery_queue": str(self.root / "delivery_queue.jsonl"),
            },
            "schema": {"assignee_field": "owner"},
            "coordination": {
                "repositories": {
                    "pantheon": {
                        "repo": None,
                        "local_path": str(self.repo),
                        "default_branch": "dev",
                    }
                }
            },
            "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
            "worker_worktrees": {"root": str(self.root / "worktrees")},
            "worker_retry": {
                "enabled": True,
                "max_attempts": 3,
                "transient_error_patterns": ["retryablequotaerror", "resource_exhausted"],
            },
            "worker_reassignment": {
                "enabled": True,
                "after_attempts": 1,
                "reassign_on_terminal_failure": True,
                "eligible_statuses": ["todo", "in_progress", "review"],
                "owner_fallbacks": {
                    "antigravity": ["Codex", "Claude"],
                    "antigravity2": ["Codex", "Claude"],
                    "Antigravity": ["Codex", "Claude"],
                    "Antigravity2": ["Codex", "Claude"],
                },
                "reviewer_fallbacks": {
                    "codex": ["Claude"],
                    "codex2": ["Claude"],
                    "Codex": ["Claude"],
                    "Codex2": ["Claude"],
                },
            },
            "provider_guardrails": {
                "pause_on_capacity_failure": True,
                "pause_on_auth_failure": True,
                "capacity_pause_seconds": 900,
                "quota_terminal_pause_seconds": 900,
            },
            "providers": {
                "antigravity": {"dispatch_group": "antigravity", "delivery_mode": "antigravity"},
                "antigravity2": {"dispatch_group": "antigravity", "delivery_mode": "antigravity"},
                "codex": {"dispatch_group": "codex", "delivery_mode": "codex"},
                "codex2": {"dispatch_group": "codex", "delivery_mode": "codex"},
                "claude": {"dispatch_group": "claude", "delivery_mode": "claude_cli"},
            },
            "agents": {
                "antigravity": {
                    "display_name": "Antigravity",
                    "provider": "antigravity",
                    "adapter": "antigravity",
                    "account_pool": "antigravity_main",
                },
                "antigravity2": {
                    "display_name": "Antigravity2",
                    "provider": "antigravity2",
                    "adapter": "antigravity",
                    "account_pool": "antigravity_main",
                },
                "codex": {
                    "display_name": "Codex",
                    "provider": "codex",
                    "adapter": "codex",
                    "account_pool": "codex_main",
                },
                "codex2": {
                    "display_name": "Codex2",
                    "provider": "codex2",
                    "adapter": "codex",
                    "account_pool": "codex_main",
                },
                "claude": {
                    "display_name": "Claude",
                    "provider": "claude",
                    "adapter": "claude_cli",
                    "account_pool": "claude_main",
                },
            },
            "account_pools": {
                "antigravity_main": {
                    "enabled": True,
                    "max_concurrent": 2,
                    "providers": ["antigravity", "antigravity2"],
                },
                "codex_main": {
                    "enabled": True,
                    "max_concurrent": 2,
                    "providers": ["codex", "codex2"],
                },
                "claude_main": {
                    "enabled": True,
                    "max_concurrent": 2,
                    "providers": ["claude"],
                },
            },
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_sibling_quota_fence_preserves_dirty_worktree_and_authorizes_successor_lease_continuation(self) -> None:
        """E2E: Sibling worker dirty changes (staged, unstaged, untracked) are backed up, sealed, and handed off to authorized successor via prepare_worker_workspace."""
        # Create uncommitted staged, unstaged, and untracked work in the sibling's isolated worktree
        staged_file = self.worktree / "staged_impl.py"
        staged_file.write_text("print('staged work')\n", encoding="utf-8")
        _git_run(self.worktree, "add", "staged_impl.py")

        unstaged_file = self.worktree / "README.md"
        unstaged_file.write_text("modified readme unstaged work\n", encoding="utf-8")

        untracked_file = self.worktree / "untracked_impl.py"
        untracked_file.write_text("print('untracked work')\n", encoding="utf-8")

        inspection = inspect_worktree(self.worktree)
        self.assertEqual(inspection.kind, "owner_dirty")

        state: dict[str, Any] = {
            "workers": {
                "run-trigger": {
                    "run_id": "run-trigger",
                    "provider": "antigravity",
                    "agent_id": "antigravity",
                    "task_id": "TASK-TRIGGER-001",
                    "status": "running",
                    "pid": 999999,
                    "queue_event_id": "evt-trigger-1",
                },
                "run-sibling": {
                    "run_id": "run-sibling",
                    "provider": "antigravity2",
                    "agent_id": "antigravity2",
                    "task_id": "TASK-SIBLING-001",
                    "workspace_path": str(self.worktree.resolve()),
                    "workspace_branch": "task/TASK-SIBLING-001",
                    "workspace_mode": "isolated_worktree",
                    "reason": "owned_ready_dispatch",
                    "status": "running",
                    "pid": 999998,
                    "queue_event_id": "evt-sibling-1",
                },
                "run-independent": {
                    "run_id": "run-independent",
                    "provider": "claude",
                    "agent_id": "claude",
                    "task_id": "TASK-INDEPENDENT-001",
                    "status": "running",
                    "pid": 999997,
                    "queue_event_id": "evt-indep-1",
                },
            },
            "queue": {
                "events": {
                    "evt-trigger-1": {"status": "started", "task_id": "TASK-TRIGGER-001"},
                    "evt-sibling-1": {"status": "started", "task_id": "TASK-SIBLING-001"},
                    "evt-indep-1": {"status": "started", "task_id": "TASK-INDEPENDENT-001"},
                }
            },
            "worker_worktrees": {"handoff_blocks": {}},
        }
        triggering_worker = state["workers"]["run-trigger"]
        quota_reason = "ERROR: You've hit your usage limit. Visit https://antigravity.google.com to upgrade or try again at 7:00 PM."

        with mock.patch.object(supervisor, "pid_is_alive", return_value=False), \
             mock.patch.object(supervisor, "terminate_worker_pid", return_value=True), \
             mock.patch.object(supervisor, "sync_status_pipeline", return_value=True), \
             mock.patch("status_transition.sync_status_pipeline", return_value=True):
            fenced = worker_failure_policy.fence_account_pool_workers(
                self.config, state, triggering_worker, quota_reason
            )

        self.assertEqual(fenced, 1)

        # Verify physical backup manifest, patches, and untracked files
        backup_root = self.root / ".orchestrator" / "worktree-dirt-backups"
        self.assertTrue(backup_root.exists(), "Backup directory must exist")
        backup_dirs = list(backup_root.glob("task-sibling-001-*"))
        self.assertEqual(len(backup_dirs), 1, "Exactly one backup dir created for sibling task")
        task_backup_dir = backup_dirs[0]

        manifest_data = json.loads((task_backup_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest_data.get("task_id"), "TASK-SIBLING-001")
        self.assertEqual(manifest_data.get("branch"), "task/TASK-SIBLING-001")
        self.assertEqual(manifest_data.get("head_sha"), self.head_sha)
        self.assertEqual(manifest_data.get("trigger"), "sibling_fenced")

        staged_patch = (task_backup_dir / "staged.patch").read_bytes()
        self.assertIn(b"staged_impl.py", staged_patch)
        self.assertIn(b"staged work", staged_patch)

        unstaged_patch = (task_backup_dir / "unstaged.patch").read_bytes()
        self.assertIn(b"modified readme unstaged work", unstaged_patch)

        untracked_backup = task_backup_dir / "untracked" / "untracked_impl.py"
        self.assertTrue(untracked_backup.exists())
        self.assertEqual(untracked_backup.read_text(encoding="utf-8"), "print('untracked work')\n")

        checksums_file = task_backup_dir / "backup_checksums.sha256"
        self.assertTrue(checksums_file.exists())

        # Sibling was reassigned to Codex outside antigravity_main pool
        sibling = state["workers"]["run-sibling"]
        self.assertEqual(sibling["status"], "reassigned")
        self.assertEqual(sibling["reassigned_to"], "Codex")

        # Independent worker in claude_main pool was NOT touched
        independent = state["workers"]["run-independent"]
        self.assertEqual(independent["status"], "running")

        # Task in canonical status was reassigned to Codex
        updated_task = worker_failure_policy.canonical_task_record(self.config, "TASK-SIBLING-001")
        self.assertIsNotNone(updated_task)
        self.assertEqual(updated_task.get("owner"), "Codex")

        # Handoff block recorded with exact provenance and transferred to authorized successor
        handoff_block = state["worker_worktrees"]["handoff_blocks"].get("TASK-SIBLING-001")
        self.assertIsNotNone(handoff_block)
        self.assertEqual(handoff_block.get("owner"), "Codex")
        self.assertEqual(handoff_block.get("authorized_successor"), "Codex")
        self.assertEqual(handoff_block.get("original_owner"), "Antigravity2")
        self.assertEqual(handoff_block.get("transferred_from"), "Antigravity2")
        self.assertEqual(handoff_block.get("transferred_to"), "Codex")
        self.assertEqual(handoff_block.get("dirt_fingerprint"), inspection.fingerprint)
        self.assertEqual(handoff_block.get("head_sha"), self.head_sha)
        self.assertEqual(handoff_block.get("workspace_branch"), "task/TASK-SIBLING-001")
        self.assertEqual(handoff_block.get("workspace_path"), str(self.worktree.resolve()))

        # Authorized successor (Codex) enters actual prepare_worker_workspace lease entry
        successor_request = DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="resume work on existing worktree",
            task_id="TASK-SIBLING-001",
            reason="owned_ready_dispatch",
        )
        lease_ok, lease_err = worker_workspace.prepare_worker_workspace(
            self.config,
            state,
            successor_request,
            queue_event_id="evt-sibling-1",
            target_agent="Codex",
        )
        self.assertTrue(lease_ok, f"prepare_worker_workspace must succeed: {lease_err}")
        self.assertEqual(successor_request.metadata.get("worktree_continuation"), "sealed_owner_dirt")
        self.assertEqual(successor_request.metadata.get("workspace_mode"), "isolated_worktree")
        self.assertEqual(successor_request.metadata.get("workspace_path"), str(self.worktree.resolve()))

        # Successor completes work, commits, and leaves clean repo
        _git_run(self.worktree, "add", "-A")
        _git_run(self.worktree, "commit", "--quiet", "-m", "TASK-SIBLING-001: complete task implementation")
        clean_inspection = inspect_worktree(self.worktree)
        self.assertEqual(clean_inspection.kind, "clean")

    def test_sibling_quota_fence_alive_terminating_dead_ordering(self) -> None:
        """Ordering: Alive -> terminating (pending fence) -> dead ordering verifies deferred reassignment and settlement."""
        dirty_file = self.worktree / "in_progress.py"
        dirty_file.write_text("print('pending dirty')\n", encoding="utf-8")

        state: dict[str, Any] = {
            "workers": {
                "run-trigger": {
                    "run_id": "run-trigger",
                    "provider": "antigravity",
                    "agent_id": "antigravity",
                    "task_id": "TASK-TRIGGER-001",
                    "status": "running",
                    "pid": 999999,
                    "queue_event_id": "evt-trigger-1",
                },
                "run-sibling": {
                    "run_id": "run-sibling",
                    "provider": "antigravity2",
                    "agent_id": "antigravity2",
                    "task_id": "TASK-SIBLING-001",
                    "workspace_path": str(self.worktree.resolve()),
                    "workspace_branch": "task/TASK-SIBLING-001",
                    "workspace_mode": "isolated_worktree",
                    "reason": "owned_ready_dispatch",
                    "status": "running",
                    "pid": 999998,
                    "queue_event_id": "evt-sibling-1",
                },
            },
            "queue": {
                "events": {
                    "evt-trigger-1": {"status": "started", "task_id": "TASK-TRIGGER-001"},
                    "evt-sibling-1": {"status": "started", "task_id": "TASK-SIBLING-001"},
                }
            },
            "worker_worktrees": {"handoff_blocks": {}},
        }
        quota_reason = "ERROR: Free daily quota has been reached."

        # Step 1: fence_account_pool_workers runs while sibling process is still alive (shutdown in progress)
        with mock.patch.object(supervisor, "pid_is_alive", return_value=True), \
             mock.patch.object(supervisor, "terminate_worker_pid", return_value=True) as mock_term:
            fenced = worker_failure_policy.fence_account_pool_workers(
                self.config, state, state["workers"]["run-trigger"], quota_reason
            )

        self.assertEqual(fenced, 1)
        mock_term.assert_called_once_with(999998)

        sibling = state["workers"]["run-sibling"]
        # Process is alive: pending fence recorded, but status remains running (active)
        self.assertIsNotNone(sibling.get("pending_fence"))
        self.assertEqual(sibling["pending_fence"]["pool_id"], "antigravity_main")
        self.assertEqual(sibling["status"], "running")
        self.assertIsNone(sibling.get("reassigned_to"))

        # Task owner in canonical status is UNCHANGED (Antigravity2)
        task_initial = worker_failure_policy.canonical_task_record(self.config, "TASK-SIBLING-001")
        self.assertEqual(task_initial.get("owner"), "Antigravity2")

        # Queue event remains started (unfinalized)
        self.assertEqual(state["queue"]["events"]["evt-sibling-1"]["status"], "started")
        # No handoff block created yet
        self.assertNotIn("TASK-SIBLING-001", state.get("worker_worktrees", {}).get("handoff_blocks", {}))

        # Step 2: poll_workers runs while process is STILL alive
        with mock.patch.object(supervisor, "pid_is_alive", return_value=True), \
             mock.patch.object(supervisor, "terminate_worker_pid", return_value=True) as mock_term2:
            supervisor.poll_workers(self.config, state)

        mock_term2.assert_called_once_with(999998)
        self.assertEqual(sibling["status"], "running")
        self.assertIsNotNone(sibling.get("pending_fence"))
        task_still_same = worker_failure_policy.canonical_task_record(self.config, "TASK-SIBLING-001")
        self.assertEqual(task_still_same.get("owner"), "Antigravity2")

        # Step 3: poll_workers runs after process is confirmed dead
        with mock.patch.object(supervisor, "pid_is_alive", return_value=False), \
             mock.patch.object(supervisor, "sync_status_pipeline", return_value=True), \
             mock.patch("status_transition.sync_status_pipeline", return_value=True):
            changed = supervisor.poll_workers(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(sibling["status"], "reassigned")
        self.assertEqual(sibling["reassigned_to"], "Codex")
        self.assertIsNone(sibling.get("pending_fence"))

        # Task in canonical status now reassigned to Codex
        task_after_death = worker_failure_policy.canonical_task_record(self.config, "TASK-SIBLING-001")
        self.assertEqual(task_after_death.get("owner"), "Codex")

        # Handoff block created and transferred to Codex
        handoff_block = state["worker_worktrees"]["handoff_blocks"].get("TASK-SIBLING-001")
        self.assertIsNotNone(handoff_block)
        self.assertEqual(handoff_block.get("owner"), "Codex")
        self.assertEqual(handoff_block.get("authorized_successor"), "Codex")

        # Queue event finalized
        self.assertEqual(state["queue"]["events"]["evt-sibling-1"]["status"], "completed")

        # Successor Codex can now lease via prepare_worker_workspace
        successor_request = DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="resume work",
            task_id="TASK-SIBLING-001",
            reason="owned_ready_dispatch",
        )
        lease_ok, lease_err = worker_workspace.prepare_worker_workspace(
            self.config,
            state,
            successor_request,
            queue_event_id="evt-sibling-1",
            target_agent="Codex",
        )
        self.assertTrue(lease_ok, f"prepare_worker_workspace must succeed: {lease_err}")

    def test_sibling_quota_fence_failed_termination_preserves_active_state_without_reassign(self) -> None:
        """Failed termination: If SIGTERM fails to stop the worker process, status and ownership remain untouched."""
        (self.worktree / "uncommitted.py").write_text("print('writer active')\n", encoding="utf-8")
        state: dict[str, Any] = {
            "workers": {
                "run-trigger": {
                    "run_id": "run-trigger",
                    "provider": "antigravity",
                    "agent_id": "antigravity",
                    "task_id": "TASK-TRIGGER-001",
                    "status": "running",
                    "pid": 999999,
                    "queue_event_id": "evt-trigger-1",
                },
                "run-sibling": {
                    "run_id": "run-sibling",
                    "provider": "antigravity2",
                    "agent_id": "antigravity2",
                    "task_id": "TASK-SIBLING-001",
                    "workspace_path": str(self.worktree.resolve()),
                    "workspace_branch": "task/TASK-SIBLING-001",
                    "workspace_mode": "isolated_worktree",
                    "reason": "owned_ready_dispatch",
                    "status": "running",
                    "pid": 999998,
                    "queue_event_id": "evt-sibling-1",
                },
            },
            "queue": {
                "events": {
                    "evt-trigger-1": {"status": "started", "task_id": "TASK-TRIGGER-001"},
                    "evt-sibling-1": {"status": "started", "task_id": "TASK-SIBLING-001"},
                }
            },
            "worker_worktrees": {"handoff_blocks": {}},
        }
        quota_reason = "ERROR: Free daily quota has been reached."

        # Process is alive and cannot be terminated
        with mock.patch.object(supervisor, "pid_is_alive", return_value=True), \
             mock.patch.object(supervisor, "terminate_worker_pid", return_value=False):
            fenced = worker_failure_policy.fence_account_pool_workers(
                self.config, state, state["workers"]["run-trigger"], quota_reason
            )

        self.assertEqual(fenced, 1)

        sibling = state["workers"]["run-sibling"]
        # Pending fence recorded, status remains running (active)
        self.assertEqual(sibling["status"], "running")
        self.assertIsNotNone(sibling.get("pending_fence"))

        # Canonical task owner is UNCHANGED (Antigravity2)
        task = worker_failure_policy.canonical_task_record(self.config, "TASK-SIBLING-001")
        self.assertEqual(task.get("owner"), "Antigravity2")

        # Queue event remains started (active)
        self.assertEqual(state["queue"]["events"]["evt-sibling-1"]["status"], "started")

        # No handoff block created because active writer was present
        self.assertNotIn("TASK-SIBLING-001", state.get("worker_worktrees", {}).get("handoff_blocks", {}))

    def test_sibling_quota_fence_backup_failure_refuses_reassignment_and_leaves_owner_unchanged(self) -> None:
        """Backup failure guard: If worktree preservation fails on dirty work, pending_fence remains retryable without dropping state, and recovers upon repair."""
        (self.worktree / "dirty.py").write_text("print('valuable dirty content')\n", encoding="utf-8")

        state: dict[str, Any] = {
            "workers": {
                "run-trigger": {
                    "run_id": "run-trigger",
                    "provider": "antigravity",
                    "agent_id": "antigravity",
                    "task_id": "TASK-TRIGGER-001",
                    "status": "running",
                    "pid": 999999,
                    "queue_event_id": "evt-trigger-1",
                },
                "run-sibling": {
                    "run_id": "run-sibling",
                    "provider": "antigravity2",
                    "agent_id": "antigravity2",
                    "task_id": "TASK-SIBLING-001",
                    "workspace_path": str(self.worktree.resolve()),
                    "workspace_branch": "task/TASK-SIBLING-001",
                    "workspace_mode": "isolated_worktree",
                    "reason": "owned_ready_dispatch",
                    "status": "running",
                    "pid": 999998,
                    "queue_event_id": "evt-sibling-1",
                },
            },
            "queue": {
                "events": {
                    "evt-trigger-1": {"status": "started", "task_id": "TASK-TRIGGER-001"},
                    "evt-sibling-1": {"status": "started", "task_id": "TASK-SIBLING-001"},
                }
            },
            "worker_worktrees": {"handoff_blocks": {}},
        }
        quota_reason = "ERROR: Free daily quota has been reached."

        # Simulate backup write failure on dead worker
        with mock.patch.object(supervisor, "pid_is_alive", return_value=False), \
             mock.patch.object(supervisor, "terminate_worker_pid", return_value=True), \
             mock.patch.object(
                 supervisor,
                 "preserve_dead_worker_worktree",
                 return_value=worker_workspace.QuarantineOutcome(False, "backup_write_failed", "disk full during copy"),
             ), \
             mock.patch.object(
                 worker_workspace,
                 "preserve_dead_worker_worktree",
                 return_value=worker_workspace.QuarantineOutcome(False, "backup_write_failed", "disk full during copy"),
             ):
            fenced = worker_failure_policy.fence_account_pool_workers(
                self.config, state, state["workers"]["run-trigger"], quota_reason
            )

        self.assertEqual(fenced, 1)

        sibling = state["workers"]["run-sibling"]
        # Pending fence preserved for retry (NOT dropped as terminal failure)
        self.assertIsNotNone(sibling.get("pending_fence"))
        self.assertTrue(sibling.get("pending_fence", {}).get("preservation_failed"))
        self.assertIsNone(sibling.get("reassigned_to"))
        self.assertIn("backup_write_failed", str(sibling.get("last_error") or ""))

        # Canonical task owner is UNCHANGED (Antigravity2)
        task = worker_failure_policy.canonical_task_record(self.config, "TASK-SIBLING-001")
        self.assertEqual(task.get("owner"), "Antigravity2")

        # Queue event remains started (pending settlement)
        self.assertEqual(state["queue"]["events"]["evt-sibling-1"]["status"], "started")

        # No handoff block created for successor yet
        self.assertNotIn("TASK-SIBLING-001", state.get("worker_worktrees", {}).get("handoff_blocks", {}))

        # Attempted lease entry by successor Codex MUST be blocked
        successor_request = DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="attempt lease",
            task_id="TASK-SIBLING-001",
            reason="owned_ready_dispatch",
        )
        lease_ok, lease_err = worker_workspace.prepare_worker_workspace(
            self.config,
            state,
            successor_request,
            queue_event_id="evt-codex-1",
            target_agent="Codex",
        )
        self.assertFalse(lease_ok, "Successor lease must be refused when dirty worktree preservation failed")
        self.assertIn("Cannot lease isolated worker worktree", str(lease_err or ""))

        # Repair: now simulate filesystem repair, next poll_workers cycle retries preservation and succeeds
        with mock.patch.object(supervisor, "pid_is_alive", return_value=False), \
             mock.patch.object(supervisor, "sync_status_pipeline", return_value=True), \
             mock.patch("status_transition.sync_status_pipeline", return_value=True):
            changed = supervisor.poll_workers(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(sibling["status"], "reassigned")
        self.assertEqual(sibling["reassigned_to"], "Codex")
        self.assertIsNone(sibling.get("pending_fence"))

        # Handoff block now exists and is transferred to Codex
        handoff_block = state["worker_worktrees"]["handoff_blocks"].get("TASK-SIBLING-001")
        self.assertIsNotNone(handoff_block)
        self.assertEqual(handoff_block.get("owner"), "Codex")
        self.assertEqual(handoff_block.get("authorized_successor"), "Codex")

        # Successor Codex can now lease successfully
        lease_ok, lease_err = worker_workspace.prepare_worker_workspace(
            self.config,
            state,
            successor_request,
            queue_event_id="evt-codex-2",
            target_agent="Codex",
        )
        self.assertTrue(lease_ok, f"Successor lease must succeed after repair: {lease_err}")
        self.assertEqual(successor_request.metadata.get("worktree_continuation"), "sealed_owner_dirt")

    def test_authorized_successor_refuses_foreign_agent_or_unauthorized_owner(self) -> None:
        """Negative: Foreign agent cannot claim an authorized successor dirty worktree lease."""
        (self.worktree / "uncommitted.py").write_text("print('dirt')\n", encoding="utf-8")
        inspection = inspect_worktree(self.worktree)
        state: dict[str, Any] = {
            "worker_worktrees": {
                "handoff_blocks": {
                    "TASK-SIBLING-001": {
                        "owner": "Codex",
                        "authorized_successor": "Codex",
                        "original_owner": "Antigravity2",
                        "workspace_path": str(self.worktree.resolve()),
                        "workspace_branch": "task/TASK-SIBLING-001",
                        "head_sha": self.head_sha,
                        "dirt_fingerprint": inspection.fingerprint,
                        "detail": inspection.detail,
                    }
                }
            }
        }
        task = {"id": "TASK-SIBLING-001", "owner": "Codex"}
        foreign_request = DeliveryRequest(
            agent_id="claude",
            provider="claude",
            delivery_mode="claude_cli",
            message="attempt claim by third agent",
            task_id="TASK-SIBLING-001",
            reason="owned_ready_dispatch",
        )
        allowed, reason = worker_workspace.sealed_owner_continuation_allowed(
            self.config,
            state,
            foreign_request,
            task,
            target_agent="claude",
            worktree_path=self.worktree,
            branch="task/TASK-SIBLING-001",
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "not_same_owner")

        # Actual prepare_worker_workspace also refuses lease
        lease_ok, lease_err = worker_workspace.prepare_worker_workspace(
            self.config,
            state,
            foreign_request,
            queue_event_id="evt-foreign-1",
            target_agent="Claude",
        )
        self.assertFalse(lease_ok)
        self.assertIn("Cannot lease isolated worker worktree", str(lease_err or ""))

    def test_authorized_successor_refuses_reviewer_guard(self) -> None:
        """Reviewer guard: Reviewers must never inherit uncommitted dirty worktrees."""
        (self.worktree / "uncommitted.py").write_text("print('dirt')\n", encoding="utf-8")
        inspection = inspect_worktree(self.worktree)
        state: dict[str, Any] = {
            "worker_worktrees": {
                "handoff_blocks": {
                    "TASK-SIBLING-001": {
                        "owner": "Codex",
                        "authorized_successor": "Codex",
                        "workspace_path": str(self.worktree.resolve()),
                        "workspace_branch": "task/TASK-SIBLING-001",
                        "head_sha": self.head_sha,
                        "dirt_fingerprint": inspection.fingerprint,
                        "detail": inspection.detail,
                    }
                }
            }
        }
        task = {"id": "TASK-SIBLING-001", "owner": "Codex", "reviewer": "Codex2"}
        reviewer_request = DeliveryRequest(
            agent_id="codex2",
            provider="codex2",
            delivery_mode="codex",
            message="review request",
            task_id="TASK-SIBLING-001",
            reason="review_ready_dispatch",
        )
        allowed, reason = worker_workspace.sealed_owner_continuation_allowed(
            self.config,
            state,
            reviewer_request,
            task,
            target_agent="codex2",
            worktree_path=self.worktree,
            branch="task/TASK-SIBLING-001",
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "not_owner_execution")

        lease_ok, lease_err = worker_workspace.prepare_worker_workspace(
            self.config,
            state,
            reviewer_request,
            queue_event_id="evt-reviewer-1",
            target_agent="Codex2",
        )
        self.assertFalse(lease_ok)
        self.assertIn("Cannot lease isolated worker worktree", str(lease_err or ""))

    def test_authorized_successor_refuses_helper_guard(self) -> None:
        """Helper guard: Helper claims must never inherit uncommitted dirty worktrees."""
        (self.worktree / "uncommitted.py").write_text("print('dirt')\n", encoding="utf-8")
        inspection = inspect_worktree(self.worktree)
        state: dict[str, Any] = {
            "worker_worktrees": {
                "handoff_blocks": {
                    "TASK-SIBLING-001": {
                        "owner": "Codex",
                        "authorized_successor": "Codex",
                        "workspace_path": str(self.worktree.resolve()),
                        "workspace_branch": "task/TASK-SIBLING-001",
                        "head_sha": self.head_sha,
                        "dirt_fingerprint": inspection.fingerprint,
                        "detail": inspection.detail,
                    }
                }
            }
        }
        task = {"id": "TASK-SIBLING-001", "owner": "Codex"}
        helper_request = DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="helper claim",
            task_id="TASK-SIBLING-001",
            reason="helper_claim",
        )
        allowed, reason = worker_workspace.sealed_owner_continuation_allowed(
            self.config,
            state,
            helper_request,
            task,
            target_agent="codex",
            worktree_path=self.worktree,
            branch="task/TASK-SIBLING-001",
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "not_owner_execution")

        lease_ok, lease_err = worker_workspace.prepare_worker_workspace(
            self.config,
            state,
            helper_request,
            queue_event_id="evt-helper-1",
            target_agent="Codex",
        )
        self.assertFalse(lease_ok)
        self.assertIn("Cannot lease isolated worker worktree", str(lease_err or ""))

    def test_authorized_successor_refuses_when_head_sha_drifted(self) -> None:
        """Negative: Lease continuation fails if HEAD SHA drifted since sealing."""
        (self.worktree / "uncommitted.py").write_text("print('dirt')\n", encoding="utf-8")
        inspection = inspect_worktree(self.worktree)
        state: dict[str, Any] = {
            "worker_worktrees": {
                "handoff_blocks": {
                    "TASK-SIBLING-001": {
                        "owner": "Codex",
                        "authorized_successor": "Codex",
                        "workspace_path": str(self.worktree.resolve()),
                        "workspace_branch": "task/TASK-SIBLING-001",
                        "head_sha": self.head_sha,
                        "dirt_fingerprint": inspection.fingerprint,
                        "detail": inspection.detail,
                    }
                }
            }
        }
        task = {"id": "TASK-SIBLING-001", "owner": "Codex"}
        successor_request = DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="resume",
            task_id="TASK-SIBLING-001",
            reason="owned_ready_dispatch",
        )

        # Advance HEAD unexpectedly
        _git_run(self.worktree, "commit", "--allow-empty", "-m", "unexpected HEAD advance")

        allowed, reason = worker_workspace.sealed_owner_continuation_allowed(
            self.config,
            state,
            successor_request,
            task,
            target_agent="codex",
            worktree_path=self.worktree,
            branch="task/TASK-SIBLING-001",
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "head_changed")

        lease_ok, lease_err = worker_workspace.prepare_worker_workspace(
            self.config,
            state,
            successor_request,
            queue_event_id="evt-codex-1",
            target_agent="Codex",
        )
        self.assertFalse(lease_ok)
        self.assertIn("Cannot lease isolated worker worktree", str(lease_err or ""))

    def test_authorized_successor_refuses_when_dirt_fingerprint_drifted(self) -> None:
        """Negative: Lease continuation fails if dirty bytes or files drifted since sealing."""
        (self.worktree / "uncommitted.py").write_text("print('dirt v1')\n", encoding="utf-8")
        inspection = inspect_worktree(self.worktree)
        state: dict[str, Any] = {
            "worker_worktrees": {
                "handoff_blocks": {
                    "TASK-SIBLING-001": {
                        "owner": "Codex",
                        "authorized_successor": "Codex",
                        "workspace_path": str(self.worktree.resolve()),
                        "workspace_branch": "task/TASK-SIBLING-001",
                        "head_sha": self.head_sha,
                        "dirt_fingerprint": inspection.fingerprint,
                        "detail": inspection.detail,
                    }
                }
            }
        }
        task = {"id": "TASK-SIBLING-001", "owner": "Codex"}
        successor_request = DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="resume",
            task_id="TASK-SIBLING-001",
            reason="owned_ready_dispatch",
        )

        # Alter dirty content
        (self.worktree / "uncommitted.py").write_text("print('dirt tampered')\n", encoding="utf-8")

        allowed, reason = worker_workspace.sealed_owner_continuation_allowed(
            self.config,
            state,
            successor_request,
            task,
            target_agent="codex",
            worktree_path=self.worktree,
            branch="task/TASK-SIBLING-001",
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "dirt_changed")

        lease_ok, lease_err = worker_workspace.prepare_worker_workspace(
            self.config,
            state,
            successor_request,
            queue_event_id="evt-codex-1",
            target_agent="Codex",
        )
        self.assertFalse(lease_ok)
        self.assertIn("Cannot lease isolated worker worktree", str(lease_err or ""))

    def test_authorized_successor_refuses_when_branch_mismatched(self) -> None:
        """Negative: Lease continuation fails if branch does not match sealed workspace branch."""
        (self.worktree / "uncommitted.py").write_text("print('dirt')\n", encoding="utf-8")
        inspection = inspect_worktree(self.worktree)
        state: dict[str, Any] = {
            "worker_worktrees": {
                "handoff_blocks": {
                    "TASK-SIBLING-001": {
                        "owner": "Codex",
                        "authorized_successor": "Codex",
                        "workspace_path": str(self.worktree.resolve()),
                        "workspace_branch": "task/TASK-SIBLING-001",
                        "head_sha": self.head_sha,
                        "dirt_fingerprint": inspection.fingerprint,
                        "detail": inspection.detail,
                    }
                }
            }
        }
        task = {"id": "TASK-SIBLING-001", "owner": "Codex"}
        successor_request = DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="resume",
            task_id="TASK-SIBLING-001",
            reason="owned_ready_dispatch",
        )
        allowed, reason = worker_workspace.sealed_owner_continuation_allowed(
            self.config,
            state,
            successor_request,
            task,
            target_agent="codex",
            worktree_path=self.worktree,
            branch="task/FOREIGN-BRANCH",
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "workspace_changed")

    def test_sibling_quota_fence_without_reassignment_allows_same_owner_continuation(self) -> None:
        """Same-owner: If no successor is reassigned, original owner retains sealed handoff block for later recovery."""
        (self.worktree / "same_owner_dirt.py").write_text("print('same owner dirt')\n", encoding="utf-8")
        inspection = inspect_worktree(self.worktree)

        state: dict[str, Any] = {
            "workers": {
                "run-trigger": {
                    "run_id": "run-trigger",
                    "provider": "antigravity",
                    "agent_id": "antigravity",
                    "task_id": "TASK-TRIGGER-001",
                    "status": "running",
                    "pid": 999999,
                    "queue_event_id": "evt-trigger-1",
                },
                "run-sibling": {
                    "run_id": "run-sibling",
                    "provider": "antigravity2",
                    "agent_id": "antigravity2",
                    "task_id": "TASK-SIBLING-001",
                    "workspace_path": str(self.worktree.resolve()),
                    "workspace_branch": "task/TASK-SIBLING-001",
                    "workspace_mode": "isolated_worktree",
                    "reason": "owned_ready_dispatch",
                    "status": "running",
                    "pid": 999998,
                    "queue_event_id": "evt-sibling-1",
                },
            },
            "queue": {
                "events": {
                    "evt-trigger-1": {"status": "started", "task_id": "TASK-TRIGGER-001"},
                    "evt-sibling-1": {"status": "started", "task_id": "TASK-SIBLING-001"},
                }
            },
            "worker_worktrees": {"handoff_blocks": {}},
        }
        quota_reason = "ERROR: Free daily quota has been reached."

        with mock.patch.object(supervisor, "pid_is_alive", return_value=False), \
             mock.patch.object(supervisor, "terminate_worker_pid", return_value=True), \
             mock.patch.object(supervisor, "maybe_reassign_task_after_worker_failure", return_value=None):
            fenced = worker_failure_policy.fence_account_pool_workers(
                self.config, state, state["workers"]["run-trigger"], quota_reason
            )

        self.assertEqual(fenced, 1)
        sibling = state["workers"]["run-sibling"]
        self.assertEqual(sibling["status"], "failed")

        # Handoff block retained for original owner Antigravity2
        handoff_block = state["worker_worktrees"]["handoff_blocks"].get("TASK-SIBLING-001")
        self.assertIsNotNone(handoff_block)
        self.assertEqual(handoff_block.get("owner"), "Antigravity2")
        self.assertEqual(inspection.kind, "owner_dirty")
        self.assertEqual(handoff_block.get("dirt_fingerprint"), inspection.fingerprint)

        # Task owner remains Antigravity2
        task = worker_failure_policy.canonical_task_record(self.config, "TASK-SIBLING-001")
        self.assertEqual(task.get("owner"), "Antigravity2")

        # Original owner dispatched later is allowed continuation
        orig_request = DeliveryRequest(
            agent_id="antigravity2",
            provider="antigravity2",
            delivery_mode="antigravity",
            message="resume after cooldown",
            task_id="TASK-SIBLING-001",
            reason="owned_ready_dispatch",
        )
        allowed, detail = worker_workspace.sealed_owner_continuation_allowed(
            self.config,
            state,
            orig_request,
            task,
            target_agent="antigravity2",
            worktree_path=self.worktree,
            branch="task/TASK-SIBLING-001",
        )
        self.assertTrue(allowed, f"Original owner must be allowed continuation: {detail}")

        lease_ok, lease_err = worker_workspace.prepare_worker_workspace(
            self.config,
            state,
            orig_request,
            queue_event_id="evt-orig-1",
            target_agent="Antigravity2",
        )
        self.assertTrue(lease_ok, f"Original owner prepare_worker_workspace must succeed: {lease_err}")

    def test_sibling_quota_fence_clean_worktree_reassigns_without_handoff_seal(self) -> None:
        """Clean worktree: Sibling worker with clean worktree reassigns normally without creating a handoff seal."""
        clean_inspection = inspect_worktree(self.worktree)
        self.assertEqual(clean_inspection.kind, "clean")

        state: dict[str, Any] = {
            "workers": {
                "run-trigger": {
                    "run_id": "run-trigger",
                    "provider": "antigravity",
                    "agent_id": "antigravity",
                    "task_id": "TASK-TRIGGER-001",
                    "status": "running",
                    "pid": 999999,
                    "queue_event_id": "evt-trigger-1",
                },
                "run-sibling": {
                    "run_id": "run-sibling",
                    "provider": "antigravity2",
                    "agent_id": "antigravity2",
                    "task_id": "TASK-SIBLING-001",
                    "workspace_path": str(self.worktree.resolve()),
                    "workspace_branch": "task/TASK-SIBLING-001",
                    "workspace_mode": "isolated_worktree",
                    "reason": "owned_ready_dispatch",
                    "status": "running",
                    "pid": 999998,
                    "queue_event_id": "evt-sibling-1",
                },
            },
            "queue": {
                "events": {
                    "evt-trigger-1": {"status": "started", "task_id": "TASK-TRIGGER-001"},
                    "evt-sibling-1": {"status": "started", "task_id": "TASK-SIBLING-001"},
                }
            },
            "worker_worktrees": {"handoff_blocks": {}},
        }
        quota_reason = "ERROR: Free daily quota has been reached."

        with mock.patch.object(supervisor, "pid_is_alive", return_value=False), \
             mock.patch.object(supervisor, "terminate_worker_pid", return_value=True), \
             mock.patch.object(supervisor, "sync_status_pipeline", return_value=True), \
             mock.patch("status_transition.sync_status_pipeline", return_value=True):
            fenced = worker_failure_policy.fence_account_pool_workers(
                self.config, state, state["workers"]["run-trigger"], quota_reason
            )

        self.assertEqual(fenced, 1)
        sibling = state["workers"]["run-sibling"]
        self.assertEqual(sibling["status"], "reassigned")
        self.assertEqual(sibling["reassigned_to"], "Codex")

        # No handoff block created because worktree was clean
        self.assertNotIn("TASK-SIBLING-001", state.get("worker_worktrees", {}).get("handoff_blocks", {}))

        successor_request = DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="resume on clean worktree",
            task_id="TASK-SIBLING-001",
            reason="owned_ready_dispatch",
        )
        lease_ok, lease_err = worker_workspace.prepare_worker_workspace(
            self.config,
            state,
            successor_request,
            queue_event_id="evt-clean-1",
            target_agent="Codex",
        )
        self.assertTrue(lease_ok, f"Clean worktree prepare_worker_workspace must succeed: {lease_err}")

    def test_unrelated_or_noworkspace_failure_does_not_transfer_foreign_handoff_block(self) -> None:
        """Negative: An unrelated dispatch failure without matching workspace/run does not rewrite a prior owner's handoff seal."""
        foreign_content = "print('uncommitted work from prior owner Claude')\n"
        (self.worktree / "foreign_owner_dirt.py").write_text(foreign_content, encoding="utf-8")
        state: dict[str, Any] = {"workers": {}, "worker_worktrees": {"handoff_blocks": {}}}
        canonical_before = supervisor.canonical_task_record(self.config, "TASK-SIBLING-001")
        self.assertEqual(canonical_before["owner"], "Antigravity2")

        prior_owner_task = dict(canonical_before, owner="Claude")
        prior_worker = {
            "run_id": "prior-claude-run",
            "provider": "claude",
            "agent_id": "claude",
            "task_id": "TASK-SIBLING-001",
            "status": "failed",
            "workspace_path": str(self.worktree.resolve()),
            "workspace_branch": "task/TASK-SIBLING-001",
            "workspace_mode": "isolated_worktree",
            "reason": "owned_ready_dispatch",
        }
        preservation = worker_workspace.preserve_dead_worker_worktree(
            self.config,
            state,
            prior_worker,
            task=prior_owner_task,
            trigger="prior_owner_test",
        )
        self.assertTrue(preservation.preserved)
        prior_block = dict(state["worker_worktrees"]["handoff_blocks"]["TASK-SIBLING-001"])
        self.assertEqual(prior_block["owner"], "Claude")
        self.assertEqual(prior_block["source_run_id"], "prior-claude-run")

        # Now canonical owner Antigravity2 experiences a dispatch failure without a workspace
        failure_worker = {
            "provider": "antigravity2",
            "agent_id": "antigravity2",
            "task_id": "TASK-SIBLING-001",
            "queue_event_id": "evt-failed-dispatch",
            "run_id": None,
            "retry_count": 0,
        }
        with mock.patch.object(supervisor, "sync_status_pipeline", return_value=True), \
             mock.patch("status_transition.sync_status_pipeline", return_value=True):
            successor = worker_failure_policy.maybe_reassign_task_after_worker_failure(
                self.config,
                state,
                failure_worker,
                "ERROR: Free daily quota has been reached.",
                terminal=True,
                force=True,
            )

        self.assertEqual(successor, "Codex")
        after_block = state["worker_worktrees"]["handoff_blocks"]["TASK-SIBLING-001"]
        # Crucial check: Handoff block owner MUST NOT be rewritten to Codex
        self.assertEqual(after_block["owner"], "Claude")
        self.assertNotEqual(after_block.get("authorized_successor"), "Codex")

        # Codex attempting lease entry must be blocked (cannot adopt foreign/stale dirt)
        codex_request = DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="attempt lease after dispatch failure",
            task_id="TASK-SIBLING-001",
            reason="owned_ready_dispatch",
        )
        lease_ok, lease_err = worker_workspace.prepare_worker_workspace(
            self.config,
            state,
            codex_request,
            queue_event_id="evt-codex-lease",
            target_agent="Codex",
        )
        self.assertFalse(lease_ok, "Successor must not lease foreign dirt after unauthenticated dispatch failure")
        self.assertIn("Cannot lease isolated worker worktree", str(lease_err or ""))

    def test_sibling_quota_fence_writer_process_tree_descendants_prevent_premature_settlement(self) -> None:
        """Process tree shutdown: Active descendant writer process with cwd outside worktree prevents settlement."""
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            libc.prctl(36, 1, 0, 0, 0)  # PR_SET_CHILD_SUBREAPER
        except Exception:
            pass

        readme = self.worktree / "README.md"
        readme.write_text("uncommitted original owner work\n", encoding="utf-8")

        parent_sock, child_sock = socket.socketpair()
        parent_sock.settimeout(8)
        runner = None
        writer_pid = None
        cli_pid = None
        writer_reaped = False

        runner_source = r'''
import json, os, signal, socket, sys
s = socket.socket(fileno=int(sys.argv[1]))
cli_pid = os.fork()
if cli_pid == 0:
    writer_pid = os.fork()
    if writer_pid == 0:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        target = os.open(sys.argv[2], os.O_WRONLY | os.O_APPEND)
        os.chdir(sys.argv[3])
        s.sendall((json.dumps({'runner': os.getppid(), 'writer_pid': os.getpid(), 'cli_pid': os.getppid()}) + '\n').encode())
        if s.recv(1) == b'W':
            os.write(target, b'late original writer change after successor lease\n')
            os.fsync(target)
            s.sendall(b'written\n')
        os.close(target)
        os._exit(0)
    while True:
        signal.pause()
while True:
    signal.pause()
'''
        try:
            runner = subprocess.Popen(
                [sys.executable, "-c", runner_source, str(child_sock.fileno()), str(readme), str(self.root)],
                cwd=self.worktree,
                pass_fds=(child_sock.fileno(),),
                start_new_session=True,
            )
            child_sock.close()
            peer = parent_sock.makefile("rb")
            ready = json.loads(peer.readline())
            writer_pid = ready["writer_pid"]
            cli_pid = ready["cli_pid"]

            sibling = {
                "run_id": "run-sibling",
                "provider": "antigravity2",
                "agent_id": "antigravity2",
                "task_id": "TASK-SIBLING-001",
                "workspace_path": str(self.worktree.resolve()),
                "workspace_branch": "task/TASK-SIBLING-001",
                "workspace_mode": "isolated_worktree",
                "reason": "owned_ready_dispatch",
                "status": "running",
                "pid": runner.pid,
                "child_pid": cli_pid,
                "queue_event_id": "evt-sibling-1",
            }
            trigger = {
                "run_id": "run-trigger",
                "provider": "antigravity",
                "agent_id": "antigravity",
                "task_id": "TASK-TRIGGER-001",
                "status": "failed",
            }
            state = {
                "workers": {"run-sibling": sibling},
                "queue": {"events": {"evt-sibling-1": {"status": "started", "task_id": "TASK-SIBLING-001"}}},
                "worker_worktrees": {"handoff_blocks": {}},
            }

            detected_before = sorted(worker_failure_policy.worker_writer_pids(sibling))
            self.assertIn(writer_pid, detected_before)

            with mock.patch.object(supervisor, "sync_status_pipeline", return_value=True), \
                 mock.patch("status_transition.sync_status_pipeline", return_value=True):
                fenced = worker_failure_policy.fence_account_pool_workers(
                    self.config, state, trigger, "ERROR: Free daily quota has been reached."
                )
                self.assertEqual(fenced, 1)

                runner.wait(timeout=5)
                try:
                    os.waitpid(cli_pid, 0)
                except ChildProcessError:
                    pass

                # Runner and CLI exited, but grandchild writer ignored SIGTERM and is still alive
                self.assertTrue(supervisor.pid_is_alive(writer_pid))
                detected_after = sorted(worker_failure_policy.worker_writer_pids(sibling))
                self.assertIn(writer_pid, detected_after)
                self.assertTrue(worker_failure_policy.worker_writers_are_alive(sibling))

                # Poll must not settle sibling while writer is alive
                changed = supervisor.poll_workers(self.config, state)
                self.assertFalse(changed)
                self.assertEqual(sibling["status"], "running")
                self.assertIsNotNone(sibling.get("pending_fence"))
                self.assertNotIn("TASK-SIBLING-001", state.get("worker_worktrees", {}).get("handoff_blocks", {}))

                # Release writer to complete its write and exit
                parent_sock.sendall(b"W")
                self.assertEqual(peer.readline().decode().strip(), "written")
                waited_pid, _ = os.waitpid(writer_pid, 0)
                writer_reaped = True

                # Now writer is dead, poll_workers should settle the sibling
                changed2 = supervisor.poll_workers(self.config, state)
                self.assertTrue(changed2)
                self.assertEqual(sibling["status"], "reassigned")
                self.assertEqual(sibling["reassigned_to"], "Codex")
                self.assertIsNone(sibling.get("pending_fence"))
                self.assertIn("TASK-SIBLING-001", state.get("worker_worktrees", {}).get("handoff_blocks", {}))

                # Successor Codex can now lease workspace
                successor_request = DeliveryRequest(
                    agent_id="codex",
                    provider="codex",
                    delivery_mode="codex",
                    message="resume after writer stopped",
                    task_id="TASK-SIBLING-001",
                    reason="owned_ready_dispatch",
                )
                lease_ok, lease_err = worker_workspace.prepare_worker_workspace(
                    self.config,
                    state,
                    successor_request,
                    queue_event_id="evt-codex-lease",
                    target_agent="Codex",
                )
                self.assertTrue(lease_ok, f"Successor lease must succeed: {lease_err}")
                self.assertEqual(successor_request.metadata.get("worktree_continuation"), "sealed_owner_dirt")

                content = readme.read_text(encoding="utf-8")
                self.assertIn("uncommitted original owner work", content)
                self.assertIn("late original writer change after successor lease", content)
        finally:
            if writer_pid and not writer_reaped:
                try:
                    os.kill(writer_pid, signal.SIGKILL)
                    os.waitpid(writer_pid, 0)
                except (ProcessLookupError, ChildProcessError):
                    pass
            if runner and runner.poll() is None:
                runner.kill()
                runner.wait(timeout=5)
            parent_sock.close()
            child_sock.close()

    def test_sibling_quota_fence_boot_reconciliation_pending_preservation_recovery(self) -> None:
        """Boot reconciliation: Pending fence survives boot under backup failure and recovers after repair."""
        dirty = self.worktree / "boot-probe-dirty.txt"
        dirty.write_text("original owner valuable content\n", encoding="utf-8")
        backup_root = self.root / ".orchestrator" / "worktree-dirt-backups"
        backup_root.parent.mkdir(parents=True, exist_ok=True)
        backup_root.write_text("obstruction: regular file prevents backup directory creation\n")

        sibling = {
            "run_id": "boot-probe-sibling",
            "provider": "antigravity2",
            "agent_id": "antigravity2",
            "task_id": "TASK-SIBLING-001",
            "workspace_path": str(self.worktree.resolve()),
            "workspace_branch": "task/TASK-SIBLING-001",
            "workspace_mode": "isolated_worktree",
            "reason": "owned_ready_dispatch",
            "status": "running",
            "pid": None,
            "queue_event_id": "evt-boot-probe-sibling",
        }
        trigger = {
            "run_id": "boot-probe-trigger",
            "provider": "antigravity",
            "agent_id": "antigravity",
            "task_id": "TASK-TRIGGER-001",
            "status": "failed",
        }
        state = {
            "workers": {sibling["run_id"]: sibling},
            "queue": {"events": {sibling["queue_event_id"]: {"status": "started", "task_id": sibling["task_id"]}}},
            "worker_worktrees": {"handoff_blocks": {}},
        }

        with mock.patch.object(supervisor, "sync_status_pipeline", return_value=True), \
             mock.patch("status_transition.sync_status_pipeline", return_value=True):
            fenced = worker_failure_policy.fence_account_pool_workers(
                self.config, state, trigger, "ERROR: Free daily quota has been reached."
            )
            self.assertEqual(fenced, 1)
            self.assertEqual(sibling["status"], "running")
            self.assertEqual(sibling["pending_fence"]["preservation_reason"], "backup_write_failed")

            # Persisted/reloaded state simulation
            state = json.loads(json.dumps(state))
            sibling = state["workers"]["boot-probe-sibling"]
            supervisor.reconcile_runtime_on_boot(self.config, state)
            self.assertEqual(sibling["status"], "running")
            self.assertTrue(sibling["pending_fence"]["preservation_failed"])

            # Remove obstruction
            backup_root.unlink()

            # First poll after repair
            changed = supervisor.poll_workers(self.config, state, provider_report={})
            self.assertTrue(changed)
            self.assertEqual(sibling["status"], "reassigned")
            self.assertIsNone(sibling.get("pending_fence"))
            self.assertIsNotNone(state["worker_worktrees"]["handoff_blocks"].get(sibling["task_id"]))

            owner_request = DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="resume preserved task",
                task_id=sibling["task_id"],
                reason="owned_ready_dispatch",
            )
            lease_ok, lease_error = worker_workspace.prepare_worker_workspace(
                self.config, state, owner_request, queue_event_id="evt-boot-probe-owner", target_agent="Codex"
            )
            self.assertTrue(lease_ok, f"Lease failed: {lease_error}")
            self.assertEqual(owner_request.metadata.get("worktree_continuation"), "sealed_owner_dirt")

    def test_cleared_fence_survives_save_load_merge_and_successor_continues(self) -> None:
        """Regression: pending_fence cleared by settlement must not be resurrected
        by the production save/load merge.  After save→reload, the successor must
        still obtain a valid sealed_owner_dirt lease without the fence being
        re-applied.
        """
        staged = self.worktree / "staged_fence_regression.py"
        staged.write_text("print('staged content for fence regression')\n", encoding="utf-8")
        _git_run(self.worktree, "add", "staged_fence_regression.py")
        inspection = inspect_worktree(self.worktree)
        self.assertNotEqual(inspection.kind, "clean")

        sibling = {
            "run_id": "run-fence-regression-sibling",
            "provider": "antigravity2",
            "agent_id": "antigravity2",
            "task_id": "TASK-SIBLING-001",
            "workspace_path": str(self.worktree.resolve()),
            "workspace_branch": "task/TASK-SIBLING-001",
            "workspace_mode": "isolated_worktree",
            "reason": "owned_ready_dispatch",
            "status": "running",
            "pid": None,
            "queue_event_id": "evt-fence-regression-1",
        }
        trigger = {
            "run_id": "run-fence-regression-trigger",
            "provider": "antigravity",
            "agent_id": "antigravity",
            "task_id": "TASK-TRIGGER-001",
            "status": "running",
            "pid": 999999,
            "queue_event_id": "evt-fence-regression-trigger",
        }
        state: dict[str, Any] = {
            "workers": {
                sibling["run_id"]: sibling,
                trigger["run_id"]: trigger,
            },
            "queue": {
                "events": {
                    sibling["queue_event_id"]: {"status": "started", "task_id": sibling["task_id"]},
                    trigger["queue_event_id"]: {"status": "started", "task_id": trigger["task_id"]},
                }
            },
            "worker_worktrees": {"handoff_blocks": {}},
        }

        with mock.patch.object(supervisor, "pid_is_alive", return_value=False), \
             mock.patch.object(supervisor, "terminate_worker_pid", return_value=True), \
             mock.patch.object(supervisor, "sync_status_pipeline", return_value=True), \
             mock.patch("status_transition.sync_status_pipeline", return_value=True):
            fenced = worker_failure_policy.fence_account_pool_workers(
                self.config, state, trigger, "ERROR: Free daily quota has been reached."
            )
        self.assertEqual(fenced, 1)
        self.assertEqual(sibling["status"], "reassigned")
        # pending_fence must be cleared (None sentinel, not absent)
        self.assertIsNone(sibling.get("pending_fence"))
        self.assertIn("pending_fence", sibling, "pending_fence must be present as None sentinel, not popped")

        # Simulate a disk snapshot taken BEFORE the fence was cleared.
        # This is the race: disk has pending_fence={...}, memory has pending_fence=None.
        disk_snapshot = json.loads(json.dumps(sibling))
        disk_snapshot["pending_fence"] = {"pool_id": "antigravity_main", "reason": "stale disk snapshot"}

        # Exercise the production merge path.
        merged = runtime_state._merge_worker_record(disk_snapshot, sibling)

        # The merged record must NOT have a truthy pending_fence.
        self.assertFalse(
            isinstance(merged.get("pending_fence"), dict),
            f"Cleared pending_fence was resurrected by merge: {merged.get('pending_fence')}"
        )

        # Full save/load cycle: verify the merged state file content directly.
        # (After reload, the terminal worker is correctly pruned by
        # prune_worker_records since its queue event is completed. We verify
        # the merge produced the correct disk content before pruning.)
        state_path = Path(self.config["paths"]["state_file"])
        # Write queue events to JSONL for save_runtime_state.
        eq_path = Path(self.config["paths"]["event_queue"])
        eq_entries = [
            json.dumps({"event_id": sibling["queue_event_id"], "task_id": sibling["task_id"]}),
            json.dumps({"event_id": trigger["queue_event_id"], "task_id": trigger["task_id"]}),
        ]
        eq_path.write_text("\n".join(eq_entries) + "\n", encoding="utf-8")
        # Write a disk state with the stale pending_fence.
        stale_disk_state = {
            "workers": {sibling["run_id"]: disk_snapshot},
            "queue": state.get("queue", {}),
        }
        state_path.write_text(json.dumps(stale_disk_state, default=str), encoding="utf-8")

        # Save the in-memory state (with pending_fence=None) over it.
        runtime_state.save_runtime_state(self.config, state)

        # Read the raw state file to verify the merge wrote pending_fence=None
        # (not the stale dict from disk).
        raw_saved = json.loads(state_path.read_text(encoding="utf-8"))
        saved_sibling = raw_saved.get("workers", {}).get(sibling["run_id"])
        self.assertIsNotNone(saved_sibling, "Sibling must be present in saved state file")
        self.assertFalse(
            isinstance(saved_sibling.get("pending_fence"), dict),
            f"After save, pending_fence was resurrected in state file: {saved_sibling.get('pending_fence')}"
        )

        # Successor lease must succeed using the settled in-memory state.
        successor_request = DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="resume after fence cleared and reloaded",
            task_id=sibling["task_id"],
            reason="owned_ready_dispatch",
        )
        lease_ok, lease_err = worker_workspace.prepare_worker_workspace(
            self.config, state, successor_request,
            queue_event_id="evt-fence-regression-successor", target_agent="Codex"
        )
        self.assertTrue(lease_ok, f"Successor lease failed after fence clearance: {lease_err}")
        self.assertEqual(successor_request.metadata.get("worktree_continuation"), "sealed_owner_dirt")

    def test_seal_transfer_refuses_while_writers_alive(self) -> None:
        """Regression: `maybe_reassign_...` must not move the seal with live writers.

        This covers the seal-transfer guard inside `maybe_reassign_task_after_worker_failure`
        for any caller that reaches it with writers still running. It deliberately
        does NOT describe the quota-trigger path: on that path the canonical owner
        must not move at all, which
        `test_quota_trigger_fences_itself_while_its_own_writers_are_alive` asserts
        against the real `poll_workers` branch. Reading the assertion below as the
        quota-path invariant is what let the defect survive six review rounds --
        reassignment happening here is this helper's behaviour, not a statement
        that quota handling may reassign.
        """
        dirty = self.worktree / "trigger_writer_probe.py"
        dirty.write_text("print('original writer still running')\n", encoding="utf-8")
        _git_run(self.worktree, "add", "trigger_writer_probe.py")

        # Set TASK-SIBLING-001 owner to Antigravity so the trigger worker owns the task.
        status_data = json.loads(self.status_file.read_text(encoding="utf-8"))
        for t in status_data.get("tasks", []):
            if t["id"] == "TASK-SIBLING-001":
                t["owner"] = "Antigravity"
                t["status"] = "in_progress"
        self.status_file.write_text(json.dumps(status_data, indent=2), encoding="utf-8")

        trigger = {
            "run_id": "run-writer-alive-trigger",
            "provider": "antigravity",
            "agent_id": "antigravity",
            "task_id": "TASK-SIBLING-001",
            "workspace_path": str(self.worktree.resolve()),
            "workspace_branch": "task/TASK-SIBLING-001",
            "workspace_mode": "isolated_worktree",
            "reason": "owned_ready_dispatch",
            "status": "running",
            "pid": None,
            "queue_event_id": "evt-writer-alive-trigger",
        }
        state: dict[str, Any] = {
            "workers": {trigger["run_id"]: trigger},
            "queue": {"events": {trigger["queue_event_id"]: {"status": "started", "task_id": trigger["task_id"]}}},
            "worker_worktrees": {
                "handoff_blocks": {
                    "TASK-SIBLING-001": {
                        "owner": "Antigravity",
                        "source_run_id": trigger["run_id"],
                        "workspace_path": str(self.worktree.resolve()),
                        "workspace_branch": "task/TASK-SIBLING-001",
                        "head_sha": self.head_sha,
                        "dirt_fingerprint": "test-fingerprint",
                    }
                }
            },
        }

        # Simulate writers that refuse to die: writer_writers_are_alive always True.
        with mock.patch.object(
            worker_failure_policy, "worker_writers_are_alive", return_value=True
        ), mock.patch.object(
            worker_failure_policy, "terminate_worker_writers", return_value=False
        ), mock.patch.object(
            supervisor, "sync_status_pipeline", return_value=True
        ), mock.patch(
            "status_transition.sync_status_pipeline", return_value=True
        ):
            reassigned_to = worker_failure_policy.maybe_reassign_task_after_worker_failure(
                self.config,
                state,
                trigger,
                "ERROR: quota exhausted",
                terminal=True,
                force=True,
            )

        self.assertIsNotNone(
            reassigned_to,
            "This helper still selects a successor; deferring the canonical change "
            "is the caller's job on the quota path, not this function's",
        )
        handoff_block = state["worker_worktrees"]["handoff_blocks"].get("TASK-SIBLING-001")
        self.assertIsNotNone(handoff_block)
        # Seal must NOT be transferred to the new owner when writers are alive.
        self.assertNotEqual(
            handoff_block.get("authorized_successor"), reassigned_to,
            "Seal must not be transferred while original writers are alive"
        )
        self.assertTrue(
            handoff_block.get("handoff_deferred_writer_alive"),
            "Handoff block must record that transfer was deferred due to alive writers"
        )
        # Original owner must still hold the seal.
        self.assertEqual(handoff_block.get("owner"), "Antigravity")

    def test_quota_trigger_fences_itself_while_its_own_writers_are_alive(self) -> None:
        """Regression: the quota-triggering run must fence itself, not hand off.

        `fence_account_pool_workers` skips the triggering run, so it was the one
        worker in the fenced pool whose canonical owner changed while its own
        writers were still mutating the worktree. The successor then received a
        lease whose dirt fingerprint no longer matched, and completing the queue
        event stopped any later poll from coming back for it. This exercises the
        real `poll_workers` quota branch rather than `maybe_reassign_...`
        directly, because that is the path the defect lived on.
        """
        # The trigger must still own its task, otherwise poll_workers reclaims it
        # through the "responsibility moved" path and never reaches the quota branch.
        status_data = json.loads(self.status_file.read_text(encoding="utf-8"))
        for task in status_data.get("tasks", []):
            if task["id"] == "TASK-SIBLING-001":
                task["owner"] = "Antigravity"
                task["status"] = "in_progress"
        self.status_file.write_text(json.dumps(status_data, indent=2), encoding="utf-8")

        trigger = {
            "run_id": "run-quota-trigger-self",
            "provider": "antigravity",
            "agent_id": "antigravity",
            "task_id": "TASK-SIBLING-001",
            "workspace_path": str(self.worktree.resolve()),
            "workspace_branch": "task/TASK-SIBLING-001",
            "workspace_mode": "isolated_worktree",
            "reason": "owned_ready_dispatch",
            "status": "running",
            "pid": None,
            "queue_event_id": "evt-quota-trigger-self",
        }
        state: dict[str, Any] = {
            "workers": {trigger["run_id"]: trigger},
            "queue": {
                "events": {
                    trigger["queue_event_id"]: {
                        "status": "started",
                        "task_id": trigger["task_id"],
                    }
                }
            },
            "worker_worktrees": {"handoff_blocks": {}},
        }

        with mock.patch.object(
            supervisor, "detect_worker_failure", return_value="ERROR: You've hit your usage limit."
        ), mock.patch.object(
            supervisor, "worker_writers_are_alive", return_value=True
        ), mock.patch.object(
            supervisor, "terminate_worker_writers", return_value=False
        ), mock.patch.object(
            worker_failure_policy, "worker_writers_are_alive", return_value=True
        ), mock.patch.object(
            worker_failure_policy, "terminate_worker_writers", return_value=False
        ), mock.patch.object(
            supervisor, "sync_status_pipeline", return_value=True
        ), mock.patch(
            "status_transition.sync_status_pipeline", return_value=True
        ):
            supervisor.poll_workers(self.config, state, provider_report={})

        self.assertIsNotNone(
            trigger.get("pending_fence"),
            "Quota-triggering run must fence itself while its writers are alive",
        )
        self.assertNotEqual(
            trigger.get("status"),
            "reassigned",
            "Canonical responsibility must not move while the writers are alive",
        )
        self.assertNotEqual(
            state["queue"]["events"][trigger["queue_event_id"]].get("status"),
            "completed",
            "Completing the queue event strands the run: no later poll recovers it",
        )
        self.assertNotIn(
            "TASK-SIBLING-001",
            state["worker_worktrees"]["handoff_blocks"],
            "No seal may be granted to a successor while the original writers run",
        )

    def test_seal_transfer_succeeds_when_writers_verified_stopped(self) -> None:
        """Regression: seal transfer proceeds normally when all writers are confirmed dead."""
        dirty = self.worktree / "writer_stopped_probe.py"
        dirty.write_text("print('writer will be stopped')\n", encoding="utf-8")
        _git_run(self.worktree, "add", "writer_stopped_probe.py")

        # Set TASK-SIBLING-001 owner to Antigravity so the trigger worker owns the task.
        status_data = json.loads(self.status_file.read_text(encoding="utf-8"))
        for t in status_data.get("tasks", []):
            if t["id"] == "TASK-SIBLING-001":
                t["owner"] = "Antigravity"
                t["status"] = "in_progress"
        self.status_file.write_text(json.dumps(status_data, indent=2), encoding="utf-8")

        trigger = {
            "run_id": "run-writer-stopped-trigger",
            "provider": "antigravity",
            "agent_id": "antigravity",
            "task_id": "TASK-SIBLING-001",
            "workspace_path": str(self.worktree.resolve()),
            "workspace_branch": "task/TASK-SIBLING-001",
            "workspace_mode": "isolated_worktree",
            "reason": "owned_ready_dispatch",
            "status": "running",
            "pid": None,
            "queue_event_id": "evt-writer-stopped-trigger",
        }
        state: dict[str, Any] = {
            "workers": {trigger["run_id"]: trigger},
            "queue": {"events": {trigger["queue_event_id"]: {"status": "started", "task_id": trigger["task_id"]}}},
            "worker_worktrees": {
                "handoff_blocks": {
                    "TASK-SIBLING-001": {
                        "owner": "Antigravity",
                        "source_run_id": trigger["run_id"],
                        "workspace_path": str(self.worktree.resolve()),
                        "workspace_branch": "task/TASK-SIBLING-001",
                        "head_sha": self.head_sha,
                        "dirt_fingerprint": "test-fingerprint",
                    }
                }
            },
        }

        with mock.patch.object(
            worker_failure_policy, "worker_writers_are_alive", return_value=False
        ), mock.patch.object(
            supervisor, "sync_status_pipeline", return_value=True
        ), mock.patch(
            "status_transition.sync_status_pipeline", return_value=True
        ):
            reassigned_to = worker_failure_policy.maybe_reassign_task_after_worker_failure(
                self.config,
                state,
                trigger,
                "ERROR: quota exhausted",
                terminal=True,
                force=True,
            )

        self.assertIsNotNone(reassigned_to)
        handoff_block = state["worker_worktrees"]["handoff_blocks"].get("TASK-SIBLING-001")
        self.assertIsNotNone(handoff_block)
        # Seal MUST be transferred when writers are confirmed dead.
        self.assertEqual(handoff_block.get("authorized_successor"), reassigned_to)
        self.assertEqual(handoff_block.get("owner"), reassigned_to)
        self.assertTrue(handoff_block.get("writers_verified_stopped"))
        self.assertIsNone(handoff_block.get("handoff_deferred_writer_alive"))

    def test_handoff_authorization_persists_atomically_with_actor_change(self) -> None:
        """Regression: handoff authorization must be committed atomically with
        the canonical actor change.  After save/load, the authorization record
        must be recoverable from the task record even if in-memory seal was lost.
        """
        dirty = self.worktree / "crash_handoff_probe.py"
        dirty.write_text("print('crash probe')\n", encoding="utf-8")
        _git_run(self.worktree, "add", "crash_handoff_probe.py")

        # Set TASK-SIBLING-001 owner to Antigravity so the trigger worker owns the task.
        status_data = json.loads(self.status_file.read_text(encoding="utf-8"))
        for t in status_data.get("tasks", []):
            if t["id"] == "TASK-SIBLING-001":
                t["owner"] = "Antigravity"
                t["status"] = "in_progress"
        self.status_file.write_text(json.dumps(status_data, indent=2), encoding="utf-8")

        trigger = {
            "run_id": "run-crash-handoff-trigger",
            "provider": "antigravity",
            "agent_id": "antigravity",
            "task_id": "TASK-SIBLING-001",
            "workspace_path": str(self.worktree.resolve()),
            "workspace_branch": "task/TASK-SIBLING-001",
            "workspace_mode": "isolated_worktree",
            "reason": "owned_ready_dispatch",
            "status": "running",
            "pid": None,
            "queue_event_id": "evt-crash-handoff-trigger",
        }
        state: dict[str, Any] = {
            "workers": {trigger["run_id"]: trigger},
            "queue": {"events": {trigger["queue_event_id"]: {"status": "started", "task_id": trigger["task_id"]}}},
            "worker_worktrees": {
                "handoff_blocks": {
                    "TASK-SIBLING-001": {
                        "owner": "Antigravity",
                        "source_run_id": trigger["run_id"],
                        "workspace_path": str(self.worktree.resolve()),
                        "workspace_branch": "task/TASK-SIBLING-001",
                        "head_sha": self.head_sha,
                        "dirt_fingerprint": "test-fingerprint",
                    }
                }
            },
        }

        with mock.patch.object(
            worker_failure_policy, "worker_writers_are_alive", return_value=False
        ), mock.patch.object(
            supervisor, "sync_status_pipeline", return_value=True
        ), mock.patch(
            "status_transition.sync_status_pipeline", return_value=True
        ):
            reassigned_to = worker_failure_policy.maybe_reassign_task_after_worker_failure(
                self.config,
                state,
                trigger,
                "ERROR: quota exhausted",
                terminal=True,
                force=True,
            )

        self.assertIsNotNone(reassigned_to)

        # Read the canonical task record after the reassignment was persisted.
        canonical = worker_failure_policy.canonical_task_record(self.config, "TASK-SIBLING-001")
        self.assertIsNotNone(canonical)
        self.assertEqual(canonical.get("owner"), reassigned_to)

        # handoff_authorization must be present in the persisted task record
        # because it was written atomically with the actor change.
        auth = canonical.get("handoff_authorization")
        self.assertIsNotNone(
            auth,
            "handoff_authorization must be persisted alongside the canonical actor change"
        )
        self.assertEqual(auth.get("authorized_successor"), reassigned_to)
        self.assertEqual(auth.get("transferred_from"), "Antigravity")
        self.assertEqual(auth.get("source_run_id"), trigger["run_id"])

        # Simulate crash recovery: reload the canonical status from disk and
        # verify the authorization is recoverable without in-memory seal.
        reloaded_status = json.loads(self.status_file.read_text(encoding="utf-8"))
        reloaded_task = next(
            (t for t in reloaded_status.get("tasks", []) if t.get("id") == "TASK-SIBLING-001"),
            None,
        )
        self.assertIsNotNone(reloaded_task)
        self.assertEqual(reloaded_task.get("owner"), reassigned_to)
        reloaded_auth = reloaded_task.get("handoff_authorization")
        self.assertIsNotNone(
            reloaded_auth,
            "After crash + reload, handoff_authorization must be present in the task record"
        )
        self.assertEqual(reloaded_auth.get("authorized_successor"), reassigned_to)


class AgyBackgroundExitRecoveryTests(unittest.TestCase):
    """Regression tests for agy background task termination, snapshot stability, and failure classification."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.status_file = Path(self.tmpdir.name) / "ai-status.json"
        self.status_file.write_text(json.dumps({"tasks": []}), encoding="utf-8")
        self.config: dict[str, Any] = {
            "paths": {
                "status_file": str(self.status_file),
                "activity_log": str(Path(self.tmpdir.name) / "activity.jsonl"),
            },
            "worker_retry": {
                "max_attempts": 3,
                "transient_error_patterns": ["retryablequotaerror", "resource_exhausted"],
            },
            "provider_guardrails": {
                "pause_on_capacity_failure": True,
                "pause_on_auth_failure": True,
                "generic_exit_reassign_after": 2,
            },
            "providers": {
                "antigravity": {"dispatch_group": "antigravity"},
                "codex": {"dispatch_group": "codex"},
                "claude": {"dispatch_group": "claude"},
            },
            "agents": {
                "antigravity": {"display_name": "Antigravity", "provider": "antigravity", "account_pool": "antigravity"},
                "antigravity2": {"display_name": "Antigravity2", "provider": "antigravity", "account_pool": "antigravity"},
                "antigravity3": {"display_name": "Antigravity3", "provider": "antigravity", "account_pool": "antigravity"},
                "antigravity7": {"display_name": "Antigravity7", "provider": "antigravity", "account_pool": "antigravity"},
                "codex": {"display_name": "Codex", "provider": "codex", "account_pool": "codex"},
                "codex2": {"display_name": "Codex2", "provider": "codex", "account_pool": "codex"},
                "claude": {"display_name": "Claude", "provider": "claude", "account_pool": "claude"},
            },
            "account_pools": {
                "antigravity": {
                    "enabled": True,
                    "max_concurrent": 2,
                    "providers": ["antigravity"],
                },
                "codex": {
                    "enabled": True,
                    "max_concurrent": 2,
                    "providers": ["codex"],
                },
            },
            "worker_reassignment": {
                "enabled": True,
                "after_attempts": 2,
                "reassign_on_terminal_failure": True,
                "eligible_statuses": ["todo", "in_progress", "review", "review_approved"],
            },
        }

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _make_worker_log(self, text: str) -> tuple[tempfile.TemporaryDirectory, dict[str, Any]]:
        tmpdir = tempfile.TemporaryDirectory()
        log_path = Path(tmpdir.name) / "worker.log"
        log_path.write_text(text, encoding="utf-8")
        worker = {
            "run_id": "run-agy-test-001",
            "task_id": "ODP-AGY-BACKGROUND-EXIT-RECOVERY-001",
            "provider": "antigravity",
            "agent_id": "Antigravity7",
            "log_path": str(log_path),
            "pid": 999999,
        }
        return tmpdir, worker

    def test_worker_with_terminated_background_tasks_is_interrupted_not_success(self) -> None:
        """A worker exiting 0 whose log contains background task termination must be detected as interrupted failure."""
        log_text = (
            "I have launched the test command and am waiting for it to complete.\n"
            "root agent idle; waiting up to 5s for 1 background task(s)\n"
            "terminating 1 background task(s) on exit\n"
        )
        tmpdir, worker = self._make_worker_log(log_text)
        try:
            worker["exit_code"] = 0
            worker["runner_status"] = "completed"
            self.assertTrue(worker_failure_policy.worker_has_terminated_background_tasks(worker))
            self.assertFalse(worker_failure_policy.is_structured_successful_worker(worker))
            self.assertFalse(worker_failure_policy.worker_log_scan_should_be_skipped(worker))

            reason = worker_failure_policy.detect_worker_failure(worker)
            self.assertIsNotNone(reason)
            self.assertIn("agy background lifecycle interrupted:", reason)

            failure = worker_failure_policy.classify_worker_failure(self.config, worker, reason)
            self.assertEqual(failure.get("kind"), "interrupted")
            self.assertTrue(failure.get("transient"))
            self.assertTrue(worker_failure_policy.is_interrupted_failure_kind(failure.get("kind")))
            self.assertFalse(worker_failure_policy.should_pause_dispatch_for_failure_kind(failure.get("kind")))
        finally:
            tmpdir.cleanup()

    def test_stream_session_errors_are_authoritative_but_tool_quotes_are_not(self) -> None:
        for error in ("authentication failed", "quota exceeded", "You have exhausted your capacity on this model."):
            with self.subTest(error=error):
                tmpdir, worker = self._make_worker_log(json.dumps({
                    "event": "result", "result": {"status": "ERROR", "error": error},
                }) + "\n")
                try:
                    worker.update(runner_status="failed", exit_code=1)
                    reason = worker_failure_policy.detect_worker_failure(worker)
                    self.assertEqual(reason, "Error: " + error)
                finally:
                    tmpdir.cleanup()
        for event in (
            {"event": "step_update", "step_update": {"tool_info": {"output": "quota exceeded"}}},
            {"event": "result", "result": {"status": "SUCCESS", "response": "Earlier log: quota exceeded"}},
        ):
            tmpdir, worker = self._make_worker_log(json.dumps(event) + "\n")
            try:
                worker.update(runner_status="failed", exit_code=1)
                self.assertIsNone(worker_failure_policy.detect_worker_failure(worker))
            finally:
                tmpdir.cleanup()

    def test_stream_receipt_overrides_prose_but_not_other_providers(self) -> None:
        marker = Path(self.tmpdir.name) / "runner.json"
        session = Path(str(marker) + ".agy.json")
        session.write_text(json.dumps({"transport": "agy_stream_json", "status": "interrupted"}))
        worker = {"provider": "antigravity", "runner_status_path": str(marker),
                  "runner_status": "completed", "exit_code": 0}
        self.assertFalse(worker_failure_policy.is_structured_successful_worker(worker))
        reason = worker_failure_policy.detect_worker_failure(worker)
        self.assertEqual(worker_failure_policy.classify_worker_failure(self.config, worker, reason)["kind"], "interrupted")
        worker["provider"] = "codex"
        self.assertTrue(worker_failure_policy.is_structured_successful_worker(worker))

    def test_other_provider_quoting_plain_agy_marker_is_success(self) -> None:
        tmpdir, worker = self._make_worker_log("terminating 1 background task(s) on exit\n")
        try:
            worker.update(provider="codex", runner_status="completed", exit_code=0)
            self.assertTrue(worker_failure_policy.is_structured_successful_worker(worker))
            self.assertIsNone(worker_failure_policy.detect_worker_failure(worker))
        finally:
            tmpdir.cleanup()

    def test_dirty_handoff_budget_survives_owner_alias_change(self) -> None:
        state = {}
        worker = {"task_id": "TASK-ALIAS-DIRT", "run_id": "first"}
        task = {"owner": "Antigravity"}
        seal = worker_workspace.WorkerHandoffSeal(
            accepted=False, reason="owner_dirty", detail="unchanged file",
            head_sha="a" * 40, dirt_fingerprint="same-dirt",
        )
        for i, owner in enumerate(("Antigravity", "Antigravity2", "Antigravity3"), 1):
            task["owner"] = owner
            worker_workspace.record_unsealed_worker_handoff(self.config, state, worker, task, seal)
            self.assertEqual(state["worker_worktrees"]["handoff_blocks"]["TASK-ALIAS-DIRT"]["rejection_count"], i)
        changed = seal._replace(head_sha="b" * 40)
        worker_workspace.record_unsealed_worker_handoff(self.config, state, worker, task, changed)
        self.assertEqual(state["worker_worktrees"]["handoff_blocks"]["TASK-ALIAS-DIRT"]["rejection_count"], 1)

    def test_task_progress_snapshot_ignores_ephemeral_fields_and_notes(self) -> None:
        """task_progress_snapshot must only track durable progress (head, pr_url, artifacts), not notes or assignments."""
        task_dispatched = {
            "id": "TASK-TEST-001",
            "head": "a" * 40,
            "status": "todo",
            "owner": "Antigravity7",
            "reviewer": "Codex2",
            "priority": "P1",
            "title": "Old title",
            "task_class": "ops",
            "review_reopen_count": 0,
            "review_churn_reassigned_at_count": 0,
            "next": "Waiting for worker to start.",
        }
        task_autostarted_with_note = {
            "id": "TASK-TEST-001",
            "head": "a" * 40,
            "status": "in_progress",
            "owner": "Antigravity2",
            "reviewer": "Codex",
            "priority": "P2",
            "title": "New title",
            "task_class": "feature",
            "review_reopen_count": 1,
            "review_churn_reassigned_at_count": 1,
            "next": "Supervisor auto-started TASK-TEST-001 after successful dispatch.",
        }
        snap_disp = worker_failure_policy.task_progress_snapshot(task_dispatched)
        snap_curr = worker_failure_policy.task_progress_snapshot(task_autostarted_with_note)
        self.assertEqual(snap_disp, snap_curr)
        self.assertEqual(
            worker_failure_policy.task_progress_fingerprint(task_dispatched),
            worker_failure_policy.task_progress_fingerprint(task_autostarted_with_note),
        )

        worker = {
            "request_snapshot": {
                "reason": "owned_in_progress_dispatch",
                "metadata": {"task": task_dispatched},
            }
        }
        outcome = worker_failure_policy.successful_worker_exit_outcome(
            worker,
            task_autostarted_with_note,
            terminal_statuses={"done", "review_approved"},
        )
        self.assertEqual(outcome, "no_progress")

    def test_task_progress_snapshot_detects_head_pr_and_artifact_advancement(self) -> None:
        """Durable changes like new git HEAD, PR URL, or artifacts must yield incremental_progress."""
        task_dispatched = {
            "id": "TASK-TEST-001",
            "head": "a" * 40,
            "status": "in_progress",
        }
        worker = {
            "request_snapshot": {
                "reason": "owned_in_progress_dispatch",
                "metadata": {"task": task_dispatched},
            }
        }

        # Case 1: New commit HEAD (e.g. anchor commit)
        task_new_head = {"id": "TASK-TEST-001", "head": "b" * 40, "status": "in_progress"}
        self.assertEqual(
            worker_failure_policy.successful_worker_exit_outcome(
                worker, task_new_head, terminal_statuses={"done", "review_approved"}
            ),
            "incremental_progress",
        )

        # Case 2: New PR URL
        task_new_pr = {
            "id": "TASK-TEST-001",
            "head": "a" * 40,
            "status": "in_progress",
            "pr_url": "https://github.com/alfloop-dev/odayplus/pull/999",
        }
        self.assertEqual(
            worker_failure_policy.successful_worker_exit_outcome(
                worker, task_new_pr, terminal_statuses={"done", "review_approved"}
            ),
            "incremental_progress",
        )

        # Case 3: New artifact
        task_new_artifact = {
            "id": "TASK-TEST-001",
            "head": "a" * 40,
            "status": "in_progress",
            "artifacts": ["services/new_feature.py"],
        }
        self.assertEqual(
            worker_failure_policy.successful_worker_exit_outcome(
                worker, task_new_artifact, terminal_statuses={"done", "review_approved"}
            ),
            "incremental_progress",
        )

    def test_subcommand_outcomes_and_classifications(self) -> None:
        """Verify short success, >5s success, nonzero exit, and cancellation classification."""
        # 1. Short command success
        short_success_log = "Running quick check...\nCheck completed successfully in 0.4s.\n"
        tmpdir, worker = self._make_worker_log(short_success_log)
        try:
            worker["exit_code"] = 0
            worker["runner_status"] = "completed"
            self.assertFalse(worker_failure_policy.worker_has_terminated_background_tasks(worker))
            self.assertTrue(worker_failure_policy.is_structured_successful_worker(worker))
            self.assertIsNone(worker_failure_policy.detect_worker_failure(worker))
        finally:
            tmpdir.cleanup()

        # 2. >5s command success (e.g. 25s test suite)
        long_success_log = (
            "Running full test suite...\n"
            "manage_task(Action=status) -> RUNNING\n"
            "manage_task(Action=status) -> DONE\n"
            "77 passed, 20 subtests passed in 25.23s\n"
        )
        tmpdir, worker = self._make_worker_log(long_success_log)
        try:
            worker["exit_code"] = 0
            worker["runner_status"] = "completed"
            self.assertFalse(worker_failure_policy.worker_has_terminated_background_tasks(worker))
            self.assertTrue(worker_failure_policy.is_structured_successful_worker(worker))
            self.assertIsNone(worker_failure_policy.detect_worker_failure(worker))
        finally:
            tmpdir.cleanup()

        # 3. Nonzero exit
        nonzero_log = "Running test suite...\nError: test execution failed with exit code 1\n"
        tmpdir, worker = self._make_worker_log(nonzero_log)
        try:
            worker["exit_code"] = 1
            worker["runner_status"] = "failed"
            self.assertFalse(worker_failure_policy.is_structured_successful_worker(worker))
            reason = worker_failure_policy.detect_worker_failure(worker)
            self.assertIsNotNone(reason)
            self.assertIn("Error: test execution failed", reason)
        finally:
            tmpdir.cleanup()

        # 4. Context canceled / background task terminated on exit
        canceled_log = (
            "Running long command...\n"
            "root agent idle; waiting up to 5s for 1 background task(s)\n"
            "terminating 1 background task(s) on exit\n"
        )
        tmpdir, worker = self._make_worker_log(canceled_log)
        try:
            worker["exit_code"] = 0
            worker["runner_status"] = "completed"
            self.assertTrue(worker_failure_policy.worker_has_terminated_background_tasks(worker))
            self.assertFalse(worker_failure_policy.is_structured_successful_worker(worker))
            reason = worker_failure_policy.detect_worker_failure(worker)
            self.assertIsNotNone(reason)
            failure = worker_failure_policy.classify_worker_failure(self.config, worker, reason)
            self.assertEqual(failure.get("kind"), "interrupted")
            self.assertTrue(failure.get("transient"))
        finally:
            tmpdir.cleanup()

    def test_successful_worker_exit_outcome_review_and_lifecycle_decisions(self) -> None:
        """Verify review decisions, owner-to-review transitions, and terminal lifecycle completions."""
        review_worker = {
            "request_snapshot": {
                "reason": "status:review",
                "metadata": {"task": {"id": "TASK-REV-001", "status": "review"}},
            }
        }
        # Reviewer reopens to in_progress -> review_decided
        self.assertEqual(
            worker_failure_policy.successful_worker_exit_outcome(
                review_worker,
                {"id": "TASK-REV-001", "status": "in_progress"},
                terminal_statuses={"done", "review_approved"},
            ),
            "review_decided",
        )
        # Reviewer blocks task -> review_decided
        self.assertEqual(
            worker_failure_policy.successful_worker_exit_outcome(
                review_worker,
                {"id": "TASK-REV-001", "status": "blocked"},
                terminal_statuses={"done", "review_approved"},
            ),
            "review_decided",
        )
        # Reviewer leaves task in review (no decision) -> no_progress
        self.assertEqual(
            worker_failure_policy.successful_worker_exit_outcome(
                review_worker,
                {"id": "TASK-REV-001", "status": "review"},
                terminal_statuses={"done", "review_approved"},
            ),
            "no_progress",
        )

        owner_worker = {
            "request_snapshot": {
                "reason": "owned_in_progress_dispatch",
                "metadata": {"task": {"id": "TASK-OWN-001", "status": "in_progress"}},
            }
        }
        # Owner submits to review -> lifecycle_complete
        self.assertEqual(
            worker_failure_policy.successful_worker_exit_outcome(
                owner_worker,
                {"id": "TASK-OWN-001", "status": "review"},
                terminal_statuses={"done", "review_approved"},
            ),
            "lifecycle_complete",
        )
        # Owner reaches review_approved -> lifecycle_complete
        self.assertEqual(
            worker_failure_policy.successful_worker_exit_outcome(
                owner_worker,
                {"id": "TASK-OWN-001", "status": "review_approved"},
                terminal_statuses={"done", "review_approved"},
            ),
            "lifecycle_complete",
        )
        # Owner reaches done -> lifecycle_complete
        self.assertEqual(
            worker_failure_policy.successful_worker_exit_outcome(
                owner_worker,
                {"id": "TASK-OWN-001", "status": "done"},
                terminal_statuses={"done", "review_approved"},
            ),
            "lifecycle_complete",
        )

    def test_real_subcommand_lifecycle_and_duration_receipts(self) -> None:
        """Run real subprocess commands via worker_runner to verify exit code, duration, and terminal receipts."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            heartbeat_path = temp_path / "heartbeat.json"
            status_path = temp_path / "status.json"

            # 1. Real short command success
            cmd_short = [sys.executable, "-c", "import sys, time; time.sleep(0.05); sys.exit(0)"]
            ret = worker_runner.main([
                "--run-id", "test-run-short",
                "--heartbeat-path", str(heartbeat_path),
                "--status-path", str(status_path),
                "--heartbeat-interval-seconds", "1.0",
                "--", *cmd_short,
            ])
            self.assertEqual(ret, 0)
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(status.get("status"), "completed")
            self.assertEqual(status.get("exit_code"), 0)
            self.assertIsNotNone(status.get("duration_seconds"))
            self.assertGreaterEqual(status.get("duration_seconds", 0), 0.04)

            # 2. Real >5s command success
            cmd_long = [sys.executable, "-c", "import sys, time; time.sleep(5.1); sys.exit(0)"]
            ret = worker_runner.main([
                "--run-id", "test-run-long",
                "--heartbeat-path", str(heartbeat_path),
                "--status-path", str(status_path),
                "--heartbeat-interval-seconds", "1.0",
                "--", *cmd_long,
            ])
            self.assertEqual(ret, 0)
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(status.get("status"), "completed")
            self.assertEqual(status.get("exit_code"), 0)
            self.assertGreaterEqual(status.get("duration_seconds", 0), 5.0)

            # 3. Real nonzero exit command
            cmd_fail = [sys.executable, "-c", "import sys, time; time.sleep(0.05); sys.exit(42)"]
            ret = worker_runner.main([
                "--run-id", "test-run-fail",
                "--heartbeat-path", str(heartbeat_path),
                "--status-path", str(status_path),
                "--heartbeat-interval-seconds", "1.0",
                "--", *cmd_fail,
            ])
            self.assertEqual(ret, 42)
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(status.get("status"), "failed")
            self.assertEqual(status.get("exit_code"), 42)

    def test_log_quotation_provenance_filtering(self) -> None:
        """Verify user/tool/fixture quotations of termination markers do not override real structured success."""
        # 1. Log with user JSON record quoting background task termination
        user_quoted_log = (
            '{"type": "user", "message": {"role": "user", "content": "Fix: terminating 1 background task(s) on exit"}}\n'
            '{"type": "assistant", "message": {"role": "assistant", "content": "I fixed the issue."}}\n'
        )
        tmpdir, worker = self._make_worker_log(user_quoted_log)
        try:
            worker["exit_code"] = 0
            worker["runner_status"] = "completed"
            # Provenance filtering ignores user quotation
            self.assertFalse(worker_failure_policy.worker_has_terminated_background_tasks(worker))
            self.assertTrue(worker_failure_policy.is_structured_successful_worker(worker))
            self.assertIsNone(worker_failure_policy.detect_worker_failure(worker))
        finally:
            tmpdir.cleanup()

        # 2. Log with tool command output quoting termination marker
        tool_quoted_log = (
            "exited 0 in 1.2s:\n"
            "stdout: log shows terminating 2 background task(s) on exit in old test run\n"
        )
        tmpdir, worker = self._make_worker_log(tool_quoted_log)
        try:
            worker["exit_code"] = 0
            worker["runner_status"] = "completed"
            self.assertFalse(worker_failure_policy.worker_has_terminated_background_tasks(worker))
            self.assertTrue(worker_failure_policy.is_structured_successful_worker(worker))
            self.assertIsNone(worker_failure_policy.detect_worker_failure(worker))
        finally:
            tmpdir.cleanup()

        # 3. Real authoritative CLI background task termination line
        real_termination_log = (
            "root agent idle; waiting up to 5s for 1 background task(s)\n"
            "terminating 1 background task(s) on exit\n"
        )
        tmpdir, worker = self._make_worker_log(real_termination_log)
        try:
            worker["exit_code"] = 0
            worker["runner_status"] = "completed"
            self.assertTrue(worker_failure_policy.worker_has_terminated_background_tasks(worker))
            self.assertFalse(worker_failure_policy.is_structured_successful_worker(worker))
            reason = worker_failure_policy.detect_worker_failure(worker)
            self.assertIsNotNone(reason)
            failure = worker_failure_policy.classify_worker_failure(self.config, worker, reason)
            self.assertEqual(failure.get("kind"), "interrupted")
            self.assertTrue(failure.get("transient"))
        finally:
            tmpdir.cleanup()

    def test_two_due_retries_persist_handoffs_before_another_tick(self) -> None:
        from datetime import UTC, datetime

        from adapters.base import DeliveryResult

        self.config['paths'].update(state_file=str(Path(self.tmpdir.name) / 'state.json'),
                                    event_queue=str(Path(self.tmpdir.name) / 'queue.jsonl'))
        state = {'workers': {}}
        for index in range(2):
            request = DeliveryRequest(agent_id='antigravity', provider='antigravity',
                                      delivery_mode='antigravity', message='fixture',
                                      task_id=f'TASK-RETRY-{index}', reason='owned_in_progress_dispatch')
            event_id = f'event-{index}'
            runtime_state.enqueue_event(self.config, {
                'event_id': event_id, 'target_agent': 'antigravity', 'provider': 'antigravity',
                'task_id': request.task_id, 'reason': request.reason, 'message': request.message})
            state['workers'][f'parent-{index}'] = {
                'run_id': f'parent-{index}', 'provider': 'antigravity',
                'task_id': request.task_id, 'queue_event_id': event_id,
                'request_snapshot': supervisor.request_snapshot(request),
                'status': 'retry_backoff', 'retry_count': 1, 'attempt_count': 1,
                'next_retry_at': '2000-01-01T00:00:00Z',
            }
        with (
            mock.patch.object(supervisor, 'build_adapter') as adapter,
            mock.patch.object(supervisor, 'provider_auth_identity_hash', return_value='fixture'),
            mock.patch.object(supervisor, 'save_runtime_state', side_effect=runtime_state.save_runtime_state),
            mock.patch.object(supervisor, 'write_activity_log'),
            mock.patch.object(supervisor, 'record_worker_runtime_measurement'),
        ):
            adapter.return_value.deliver.side_effect = [
                DeliveryResult(ok=True, adapter='antigravity', mode='antigravity', target='fixture',
                               auto_delivered=True, manual_confirmation_required=False,
                               run_id=f'child-{index}') for index in range(2)]
            self.assertTrue(supervisor.retry_due_workers(self.config, state, {}, datetime.now(UTC)))
            # Read the file written during child launch, before any outer tick save.
            persisted = json.loads(Path(self.config['paths']['state_file']).read_text())
            for index in range(2):
                parent = persisted['workers'][f'parent-{index}']
                self.assertEqual(parent['status'], 'retried')
                self.assertEqual(parent['superseded_by_run_id'], f'child-{index}')
                self.assertIsNone(parent['next_retry_at'])
                self.assertEqual(persisted['workers'][f'child-{index}']['retry_count'], 1)
            state = runtime_state.load_runtime_state(self.config)
            for _ in range(3):
                self.assertFalse(supervisor.retry_due_workers(self.config, state, {}, datetime.now(UTC)))
            self.assertEqual(adapter.return_value.deliver.call_count, 2)

    def test_real_retry_replacements_exhaust_across_aliases_and_restart(self) -> None:
        from datetime import UTC, datetime, timedelta
        from types import SimpleNamespace

        from adapters.base import DeliveryResult

        task = {'id': 'TASK-RETRY-CHAIN', 'owner': 'Antigravity',
                'reviewer': 'Codex2', 'status': 'in_progress'}
        aliases = ['antigravity', 'antigravity2', 'antigravity3']
        self.config['worker_retry'].update(max_attempts=2, fallback_mode='file_inbox')
        self.config['paths'].update(state_file=str(Path(self.tmpdir.name) / 'state.json'),
                                    event_queue=str(Path(self.tmpdir.name) / 'queue.jsonl'))
        for agent in self.config['agents'].values():
            agent['adapter'] = 'antigravity'
        for alias in aliases:
            runtime_state.enqueue_event(self.config, {
                'event_id': f'event-{alias}', 'target_agent': alias, 'provider': 'antigravity',
                'task_id': task['id'], 'reason': 'owned_in_progress_dispatch', 'message': 'fixture'})
        state = {'workers': {}}
        reason = 'agy background lifecycle interrupted: command_exit_unknown'
        deliveries = []

        def deliver(request, mode):
            deliveries.append(request.agent_id)
            return DeliveryResult(ok=True, adapter=mode, mode=mode,
                                  target='fixture', auto_delivered=mode != 'file_inbox',
                                  manual_confirmation_required=mode == 'file_inbox',
                                  run_id=f'run-{len(deliveries)}')

        def persist(_config, **kwargs):
            task.update(owner=kwargs['new_owner'], reviewer=kwargs['new_reviewer'],
                        status=kwargs.get('new_status') or task['status'])
            return True

        with (
            mock.patch.object(supervisor, 'build_adapter', side_effect=lambda mode, **kw:
                              SimpleNamespace(deliver=lambda req: deliver(req, mode))),
            mock.patch.object(supervisor, 'provider_auth_identity_hash', return_value='fixture'),
            mock.patch.object(supervisor, 'save_runtime_state', side_effect=runtime_state.save_runtime_state),
            mock.patch.object(supervisor, 'write_activity_log'),
            mock.patch.object(supervisor, 'record_worker_runtime_measurement'),
            mock.patch.object(supervisor, 'load_status', return_value={'tasks': [task]}),
            mock.patch.object(supervisor, 'persist_task_reassignment', side_effect=persist),
            mock.patch.object(supervisor, 'get_agent_reassignment_candidates',
                              return_value=['Antigravity', 'Antigravity2', 'Antigravity3']),
        ):
            for alias in aliases:
                self.assertEqual(task['owner'].lower(), alias)
                task['status'] = 'in_progress'
                request = DeliveryRequest(agent_id=alias, provider='antigravity',
                                          delivery_mode='antigravity', message='fixture',
                                          task_id=task['id'], reason='owned_in_progress_dispatch')
                ok, run_id, _ = supervisor.start_worker_for_request(
                    self.config, state, {}, request, queue_event_id=f'event-{alias}',
                    attempt_count=1, event_id_for_log=None)
                self.assertTrue(ok)
                for generation in range(3):
                    worker = state['workers'][run_id]
                    self.assertEqual(worker['retry_count'], generation)
                    worker_failure_policy.record_task_failure_streak(
                        state, worker, reason, failure_kind='interrupted')
                    outcome = supervisor.maybe_trigger_retry_or_fallback(
                        self.config, state, {}, worker, reason)
                    worker = state['workers'][run_id]
                    if generation < 2:
                        self.assertEqual(outcome, (True, True))
                        self.assertEqual(worker['status'], 'retry_backoff')
                        # A supervisor restart must not give the next run a fresh budget.
                        state = json.loads(json.dumps(state))
                        self.assertTrue(supervisor.retry_due_workers(
                            self.config, state, {}, datetime.now(UTC) + timedelta(days=1)))
                        parent = state['workers'][run_id]
                        self.assertEqual(parent['status'], 'retried')
                        run_id = parent['superseded_by_run_id']
                    elif alias != aliases[-1]:
                        self.assertEqual(outcome, (True, True))
                        self.assertEqual(worker['status'], 'reassigned')
                    else:
                        self.assertEqual(outcome, (True, True))
                        self.assertEqual(worker['status'], 'fallback')
                        fallback_id = worker['fallback_run_id']
                        self.assertEqual(state['workers'][fallback_id]['retry_count'], 2)
                        self.assertEqual(state['workers'][fallback_id]['status'], 'manual_pending')
                self.assertEqual(len(deliveries), 3 * (aliases.index(alias) + 1) + (alias == aliases[-1]))
            self.assertFalse(supervisor.retry_due_workers(
                self.config, state, {}, datetime.now(UTC) + timedelta(days=1)))
            report = {'agent_adapters': {alias: {'can_auto_deliver': True,
                                                'delivery_mode': 'antigravity'} for alias in aliases}}
            fresh_inbox = {**state['workers'][fallback_id], 'retry_count': 0}
            self.assertTrue(supervisor.manual_pending_inbox_can_auto_redeliver(
                self.config, state, report, fresh_inbox))
            persisted = json.loads(Path(self.config['paths']['state_file']).read_text())
            self.assertEqual(persisted['workers'][run_id]['status'], 'fallback')
            self.assertEqual(persisted['workers'][run_id]['fallback_run_id'], fallback_id)
            # Exercise deployed poll -> inbox recovery -> queue repeatedly with
            # actual save/reload (which replaces nested state dictionaries).
            with mock.patch.object(supervisor, 'load_approval_state', return_value={'pending': [], 'history': []}):
                for _ in range(3):
                    supervisor.poll_workers(self.config, state, report)
                    supervisor.process_queue(self.config, state, report)
                    runtime_state.save_runtime_state(self.config, state)
                    state = runtime_state.load_runtime_state(self.config)
                    self.assertEqual(state['workers'][fallback_id]['status'], 'manual_pending')
                    self.assertEqual(state['workers'][run_id]['status'], 'fallback')
                    self.assertEqual(len(deliveries), 10)
        self.assertEqual(deliveries, [alias for alias in aliases for _ in range(3)] + [aliases[-1]])
        streaks = state['provider_guardrails']['task_failure_streaks']
        self.assertEqual([streaks[f"{task['id']}:{alias}"]['count'] for alias in aliases], [3, 3, 3])

    def test_cross_alias_repeated_failure_exhaustion(self) -> None:
        """Verify cross-alias failures accumulate durable task streaks and cleanly exhaust fallback candidates."""
        state: dict[str, Any] = {"provider_guardrails": {"task_failure_streaks": {}, "dispatch_pauses": {}}}
        task_id = "TASK-ALIAS-001"
        task = {
            "id": task_id,
            "status": "in_progress",
            "owner": "Antigravity",
            "reviewer": "Codex2",
        }
        status_data = {"tasks": [task]}
        self.status_file.write_text(json.dumps(status_data), encoding="utf-8")

        # 1. Antigravity fails
        w1 = {"task_id": task_id, "provider": "antigravity", "logical_agent_id": "antigravity", "run_id": "run-1"}
        worker_failure_policy.record_task_failure_streak(state, w1, "terminating 1 background task(s) on exit", failure_kind="interrupted")
        worker_failure_policy.record_task_failure_streak(state, w1, "terminating 1 background task(s) on exit", failure_kind="interrupted")
        self.assertEqual(state["provider_guardrails"]["task_failure_streaks"][f"{task_id}:antigravity"]["count"], 2)

        with (
            mock.patch.object(supervisor, "sync_status_pipeline", return_value=True),
            mock.patch("status_transition.sync_status_pipeline", return_value=True),
        ):
            # Reassignment triggers from Antigravity -> Antigravity2
            reassigned_owner = worker_failure_policy.maybe_reassign_task_after_worker_failure(
                self.config, state, w1, "terminating 1 background task(s) on exit", terminal=True
            )
            self.assertEqual(reassigned_owner, "Antigravity2")
            # Streak for antigravity is preserved (not cleared)
            self.assertIn(f"{task_id}:antigravity", state["provider_guardrails"]["task_failure_streaks"])

            # 2. Antigravity2 fails on same unprogressed task
            task["owner"] = "Antigravity2"
            self.status_file.write_text(json.dumps({"tasks": [task]}), encoding="utf-8")
            w2 = {"task_id": task_id, "provider": "antigravity", "logical_agent_id": "antigravity2", "run_id": "run-2"}
            worker_failure_policy.record_task_failure_streak(state, w2, "terminating 1 background task(s) on exit", failure_kind="interrupted")
            worker_failure_policy.record_task_failure_streak(state, w2, "terminating 1 background task(s) on exit", failure_kind="interrupted")
            self.assertEqual(state["provider_guardrails"]["task_failure_streaks"][f"{task_id}:antigravity2"]["count"], 2)

            # Reassignment from Antigravity2: Antigravity is excluded because it already failed
            reassigned_owner_2 = worker_failure_policy.maybe_reassign_task_after_worker_failure(
                self.config, state, w2, "terminating 1 background task(s) on exit", terminal=True
            )
            self.assertNotIn(reassigned_owner_2, {"Antigravity", "Antigravity2"})
            self.assertEqual(reassigned_owner_2, "Antigravity3")

    def test_same_dirty_fingerprint_redispatch_and_handoff_bounds(self) -> None:
        """Verify unsealed handoff recording, same dirty fingerprint redispatch, and rejection boundaries."""
        state: dict[str, Any] = {"worker_worktrees": {"handoff_blocks": {}}}
        worker = {
            "run_id": "run-dirty-001",
            "task_id": "TASK-DIRTY-001",
            "workspace_path": "/tmp/test-worktree",
            "workspace_branch": "task/TASK-DIRTY-001",
        }
        task = {"id": "TASK-DIRTY-001", "owner": "Antigravity7", "status": "in_progress"}
        seal = worker_workspace.WorkerHandoffSeal(
            accepted=False,
            reason="owner_dirty",
            detail="1 dirty change: modified_file.py",
            head_sha="a" * 40,
            dirt_fingerprint="fingerprint-xyz-123",
        )
        worker_workspace.record_unsealed_worker_handoff(self.config, state, worker, task, seal)
        self.assertIn("TASK-DIRTY-001", state["worker_worktrees"]["handoff_blocks"])
        block = state["worker_worktrees"]["handoff_blocks"]["TASK-DIRTY-001"]
        self.assertEqual(block["dirt_fingerprint"], "fingerprint-xyz-123")
        self.assertEqual(block["head_sha"], "a" * 40)
        self.assertEqual(block["owner"], "Antigravity7")

        req_same_owner = DeliveryRequest(
            task_id="TASK-DIRTY-001",
            agent_id="Antigravity7",
            provider="antigravity",
            delivery_mode="antigravity",
            message="wake",
            reason="owned_in_progress_dispatch",
        )
        fake_path = Path("/tmp/test-worktree")
        mock_insp = mock.Mock(kind="owner_dirty", fingerprint="fingerprint-xyz-123")
        with (
            mock.patch.object(worktree_cleanliness, "inspect_worktree", return_value=mock_insp),
            mock.patch.object(worker_workspace, "inspect_worktree", return_value=mock_insp),
            mock.patch.object(worker_workspace, "_git_commit_oid", return_value="a" * 40),
            mock.patch.object(supervisor, "_git_commit_oid", return_value="a" * 40),
        ):
            # 1. Same owner, matching dirty fingerprint & HEAD -> allowed
            allowed, detail = worker_workspace.sealed_owner_continuation_allowed(
                self.config,
                state,
                req_same_owner,
                task,
                target_agent="Antigravity7",
                worktree_path=fake_path,
                branch="task/TASK-DIRTY-001",
            )
            self.assertTrue(allowed, f"Expected allowed, got detail: {detail}")
            self.assertEqual(detail, "1 dirty change: modified_file.py")

            # 2. Different target agent (alias or another agent) -> rejected
            allowed, detail = worker_workspace.sealed_owner_continuation_allowed(
                self.config,
                state,
                req_same_owner,
                task,
                target_agent="Codex2",
                worktree_path=fake_path,
                branch="task/TASK-DIRTY-001",
            )
            self.assertFalse(allowed)
            self.assertEqual(detail, "not_same_owner")

        # 3. Changed dirt fingerprint -> rejected
        mock_diff_insp = mock.Mock(kind="owner_dirty", fingerprint="different-fingerprint")
        with (
            mock.patch.object(worktree_cleanliness, "inspect_worktree", return_value=mock_diff_insp),
            mock.patch.object(worker_workspace, "inspect_worktree", return_value=mock_diff_insp),
            mock.patch.object(worker_workspace, "_git_commit_oid", return_value="a" * 40),
            mock.patch.object(supervisor, "_git_commit_oid", return_value="a" * 40),
        ):
            allowed, detail = worker_workspace.sealed_owner_continuation_allowed(
                self.config,
                state,
                req_same_owner,
                task,
                target_agent="Antigravity7",
                worktree_path=fake_path,
                branch="task/TASK-DIRTY-001",
            )
            self.assertFalse(allowed)
            self.assertEqual(detail, "dirt_changed")

    def test_repeated_unsealed_handoff_rejection_exhaustion(self) -> None:
        """Verify repeated unsealed handoffs increment rejection_count and halt when exceeding bounds."""
        state: dict[str, Any] = {"worker_worktrees": {"handoff_blocks": {}}}
        worker = {
            "run_id": "run-dirty-001",
            "task_id": "TASK-DIRTY-BOUND-001",
            "workspace_path": "/tmp/test-worktree",
            "workspace_branch": "task/TASK-DIRTY-BOUND-001",
        }
        task = {"id": "TASK-DIRTY-BOUND-001", "owner": "Antigravity7", "status": "in_progress"}
        seal = worker_workspace.WorkerHandoffSeal(
            accepted=False,
            reason="owner_dirty",
            detail="1 dirty change: modified_file.py",
            head_sha="b" * 40,
            dirt_fingerprint="fingerprint-abc-456",
        )

        # 1st rejection
        worker_workspace.record_unsealed_worker_handoff(self.config, state, worker, task, seal)
        self.assertEqual(state["worker_worktrees"]["handoff_blocks"]["TASK-DIRTY-BOUND-001"]["rejection_count"], 1)

        req = DeliveryRequest(
            task_id="TASK-DIRTY-BOUND-001",
            agent_id="Antigravity7",
            provider="antigravity",
            delivery_mode="antigravity",
            message="wake",
            reason="owned_in_progress_dispatch",
        )
        fake_path = Path("/tmp/test-worktree")
        mock_insp = mock.Mock(kind="owner_dirty", fingerprint="fingerprint-abc-456")
        with (
            mock.patch.object(worktree_cleanliness, "inspect_worktree", return_value=mock_insp),
            mock.patch.object(worker_workspace, "inspect_worktree", return_value=mock_insp),
            mock.patch.object(worker_workspace, "_git_commit_oid", return_value="b" * 40),
            mock.patch.object(supervisor, "_git_commit_oid", return_value="b" * 40),
        ):
            # Continuation allowed on 1st rejection
            allowed, _ = worker_workspace.sealed_owner_continuation_allowed(
                self.config, state, req, task, target_agent="Antigravity7", worktree_path=fake_path, branch="task/TASK-DIRTY-BOUND-001"
            )
            self.assertTrue(allowed)

            # 2nd rejection (same dirt fingerprint & head)
            worker_workspace.record_unsealed_worker_handoff(self.config, state, worker, task, seal)
            self.assertEqual(state["worker_worktrees"]["handoff_blocks"]["TASK-DIRTY-BOUND-001"]["rejection_count"], 2)
            allowed, _ = worker_workspace.sealed_owner_continuation_allowed(
                self.config, state, req, task, target_agent="Antigravity7", worktree_path=fake_path, branch="task/TASK-DIRTY-BOUND-001"
            )
            self.assertTrue(allowed)

            # 3rd rejection -> exceeds max_unsealed_handoff_attempts (default 2)
            worker_workspace.record_unsealed_worker_handoff(self.config, state, worker, task, seal)
            self.assertEqual(state["worker_worktrees"]["handoff_blocks"]["TASK-DIRTY-BOUND-001"]["rejection_count"], 3)
            allowed, detail = worker_workspace.sealed_owner_continuation_allowed(
                self.config, state, req, task, target_agent="Antigravity7", worktree_path=fake_path, branch="task/TASK-DIRTY-BOUND-001"
            )
            self.assertFalse(allowed)
            self.assertIn("unsealed_handoff_limit_exceeded", detail)


if __name__ == "__main__":
    unittest.main()
