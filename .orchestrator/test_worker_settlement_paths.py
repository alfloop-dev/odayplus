#!/usr/bin/env python3
"""Pin the deliberate divergence between the two worker-settlement paths.

`reconcile_runtime_on_boot` and `poll_workers` call almost the same helpers --
39 of 40 overlap -- which makes them look like a duplicate awaiting removal. They
are not. Measured across representative worker states, five of eight outcomes
differ, and the differences are structural rather than drift:

  * `reconcile_runtime_on_boot` gates on
    `missing_process = status in {"running", "stalled"} and not alive`, so it
    does not touch a worker parked in `waiting_approval` or `retry_backoff`. It
    leaves those for the normal poll, which can correlate approvals first.
  * It contains none of `is_transient_worker_failure`,
    `maybe_trigger_retry_or_fallback`, `higher_priority_ready_task_exists`,
    `worker_supports_approval_resume` or `resume_claude_worker`. A worker left
    behind by a supervisor that is no longer running is settled conservatively --
    failed, and re-dispatched by the normal scheduler -- rather than resumed
    against retry/backoff state whose timestamps predate the restart.

Merging the two behind an `at_boot` flag would thread these five decisions
through a 758-line function; two narrow functions state the intent better. What
was missing was any statement of WHICH outcomes are supposed to differ, so this
file is that statement. A change that accidentally aligns the paths fails here
and has to argue its case.

The counterpart guard is `test_boot_reconciliation_runs_once_per_process`: boot
settlement must run once per process, so these conservative outcomes apply to the
restart seam only and never to steady state.
"""
from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import supervisor
import worker_lifecycle

TASK = {"id": "T-1", "status": "in_progress", "owner": "Claude", "reviewer": "Codex"}
STATUS = {"tasks": [TASK]}
REQUEST_SNAPSHOT = {
    "message": "go",
    "provider": "claude",
    "agent_id": "claude",
    "task_id": "T-1",
    "target_agent": "Claude",
    "delivery_mode": "claude_cli",
    "context_files": [],
    "target_files": [],
    "metadata": {},
    "reason": "task_in_progress",
}


class WorkerSettlementPathTests(unittest.TestCase):
    """Outcome-level contract for the boot path versus the steady-state path."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.logs = Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _log(self, name: str, text: str) -> str:
        path = self.logs / f"{name}.log"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def _worker(self, **overrides) -> dict:
        worker = {
            "run_id": "run-1",
            "status": "running",
            "task_id": "T-1",
            "provider": "claude",
            "agent_id": "claude",
            "target_agent": "Claude",
            "queue_event_id": "evt-1",
            "pid": 999999,  # not a live process
            "retry_count": 0,
            "attempt_count": 1,
            "reason": "task_in_progress",
            "request_snapshot": dict(REQUEST_SNAPSHOT),
        }
        worker.update(overrides)
        return worker

    def _settle(self, path, worker: dict) -> tuple[str, str]:
        """Run one settlement path over one worker; return (worker status, queue status)."""
        state = {
            "workers": {"run-1": copy.deepcopy(worker)},
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "provider_guardrails": {"dispatch_pauses": {}, "task_failure_streaks": {}},
        }
        config = supervisor.load_config(".orchestrator/config.json")
        patches = [
            mock.patch.object(supervisor, "load_status", return_value=STATUS),
            mock.patch.object(worker_lifecycle, "load_status", return_value=STATUS, create=True),
            mock.patch.object(supervisor, "worker_matches_current_assignment", return_value=True),
            mock.patch.object(supervisor, "higher_priority_ready_task_exists", return_value=False),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "console_log"),
            mock.patch.object(supervisor, "start_worker_for_request", return_value=(True, "run-2", None)),
        ]
        for patch in patches:
            patch.start()
        try:
            path(config, state)
        finally:
            for patch in patches:
                patch.stop()
        settled = state["workers"].get("run-1", {})
        return str(settled.get("status")), str(state["queue"]["events"]["evt-1"].get("status"))

    def test_boot_does_not_retry_a_transient_failure(self) -> None:
        """Retry/backoff timestamps predate the restart, so boot settles instead."""
        worker = self._worker(log_path=self._log("t", "Error: 429 rate limit exceeded, try again later\n"))
        self.assertEqual(self._settle(supervisor.reconcile_runtime_on_boot, worker)[0], "failed")
        self.assertEqual(self._settle(supervisor.poll_workers, worker)[0], "retry_backoff")

    def test_boot_leaves_an_approval_parked_worker_alone(self) -> None:
        """`missing_process` covers only running/stalled; the poll correlates approvals."""
        worker = self._worker(status="waiting_approval", log_path=self._log("w", ""))
        self.assertEqual(self._settle(supervisor.reconcile_runtime_on_boot, worker)[0], "waiting_approval")
        self.assertEqual(self._settle(supervisor.poll_workers, worker)[0], "failed")

    def test_boot_leaves_a_backoff_worker_alone(self) -> None:
        worker = self._worker(status="retry_backoff", log_path=self._log("b", ""))
        self.assertEqual(self._settle(supervisor.reconcile_runtime_on_boot, worker)[0], "retry_backoff")
        self.assertEqual(self._settle(supervisor.poll_workers, worker)[0], "failed")

    def test_both_paths_agree_on_a_terminal_quota_failure(self) -> None:
        """Where the outcome should NOT differ, it does not."""
        worker = self._worker(log_path=self._log("q", "Error: you have exhausted your capacity for model\n"))
        self.assertEqual(
            self._settle(supervisor.reconcile_runtime_on_boot, worker),
            self._settle(supervisor.poll_workers, worker),
        )

    def test_both_paths_agree_on_a_silent_exit(self) -> None:
        worker = self._worker(log_path=self._log("e", ""))
        self.assertEqual(
            self._settle(supervisor.reconcile_runtime_on_boot, worker),
            self._settle(supervisor.poll_workers, worker),
        )

    def test_boot_path_has_no_retry_resume_or_preemption_branch(self) -> None:
        """Structural guard: the divergence above comes from these being absent."""
        import ast

        source = Path(supervisor.__file__).read_text(encoding="utf-8")
        for node in ast.parse(source).body:
            if getattr(node, "name", "") == "reconcile_runtime_on_boot":
                worker_half = ast.unparse(node).split("queue_records = state.setdefault")[0]
                break
        else:  # pragma: no cover - the function must exist
            self.fail("reconcile_runtime_on_boot not found")
        for absent in (
            "is_transient_worker_failure",
            "maybe_trigger_retry_or_fallback",
            "higher_priority_ready_task_exists",
            "worker_supports_approval_resume",
            "resume_claude_worker",
        ):
            with self.subTest(branch=absent):
                # assertNotIn would dump the whole 258-line function into the
                # failure output; assertFalse keeps the message readable.
                self.assertFalse(
                    absent in worker_half,
                    f"boot settlement gained a `{absent}` branch. The outcomes pinned "
                    "above depend on its absence, so if that is intended they need to "
                    "change with it -- this is a decision, not a lint failure.",
                )


if __name__ == "__main__":
    unittest.main()
