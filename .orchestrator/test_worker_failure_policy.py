from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

THIS_DIR = Path(__file__).resolve().parent
ROOT_DIR = THIS_DIR.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import worker_failure_policy


class WorkerFailurePolicyAuthorityTests(unittest.TestCase):
    """Tests verifying structured failure authority and quota detection rules."""

    def setUp(self) -> None:
        self.config: dict[str, Any] = {
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
            "account_pools": {
                "codex-pool": {
                    "enabled": True,
                    "max_concurrent": 2,
                    "providers": ["codex", "codex2", "codex3"],
                }
            },
        }

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

    def test_real_gemini_terminal_quota_classified_as_quota_terminal(self) -> None:
        """Real Gemini API TerminalQuotaError must classify as quota_terminal."""
        worker = {"provider": "gemini"}
        reason = "Error when talking to Gemini API TerminalQuotaError: You have exhausted your capacity on this model."
        res = worker_failure_policy.classify_worker_failure(self.config, worker, reason)
        self.assertEqual(res.get("kind"), "quota_terminal")
        self.assertTrue(worker_failure_policy.should_pause_dispatch_for_failure_kind(res.get("kind")))


if __name__ == "__main__":
    unittest.main()
