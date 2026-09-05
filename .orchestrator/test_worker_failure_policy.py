from __future__ import annotations

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

import worker_failure_policy


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

    @staticmethod
    def _state(busy_slots: dict[str, str] | None = None) -> dict[str, Any]:
        workers = {
            f"run-{index}": {
                "status": "running",
                "agent_id": slot_id,
                "logical_agent_id": logical_id,
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


if __name__ == "__main__":
    unittest.main()
