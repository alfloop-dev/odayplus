#!/usr/bin/env python3
"""Fail-closed live probes for B23, B24, N3 review-head freeze controls.

Runs against an isolated temporary PANTHEON_STATUS_ROOT copied from live status root.
Does NOT mutate live status or dashboard artifacts.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

WORKTREE = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(WORKTREE / ".orchestrator"))
sys.path.insert(0, str(WORKTREE / "scripts"))

import ai_status
import supervisor


class LiveFreezeProbes(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="freeze-probe-root-")
        self.status_root = Path(self.tmp_dir)

        live_root = Path("/home/lupin/oday-plus-supervisor-live")
        shutil.copy(live_root / "ai-status.json", self.status_root / "ai-status.json")
        (self.status_root / ".orchestrator").mkdir(parents=True, exist_ok=True)
        if (live_root / ".orchestrator" / "config.json").exists():
            shutil.copy(
                live_root / ".orchestrator" / "config.json",
                self.status_root / ".orchestrator" / "config.json",
            )

        self.orig_env = os.environ.get("PANTHEON_STATUS_ROOT")
        os.environ["PANTHEON_STATUS_ROOT"] = str(self.status_root)
        ai_status.STATUS_ROOT = self.status_root
        ai_status.STATUS_FILE = self.status_root / "ai-status.json"

    def tearDown(self):
        if self.orig_env is not None:
            os.environ["PANTHEON_STATUS_ROOT"] = self.orig_env
        else:
            os.environ.pop("PANTHEON_STATUS_ROOT", None)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_b23_probe_restore_approved_refuses_reopened_or_moved_head(self):
        """B23 Probe: restore_approved refuses when reviewer reopened work."""
        state = ai_status.load_state()
        test_task = {
            "id": "PROBE-TASK-B23",
            "title": "Probe task for B23",
            "owner": "Antigravity2",
            "reviewer": "Antigravity5",
            "status": "in_progress",
            "last_approved_head": "1111222233334444555566667777888899990000",
            "last_reopened_by": "Antigravity5",
            "review_notes_zh": "Reopened by reviewer",
        }
        state["tasks"].append(test_task)
        ai_status.save_state(state)

        with mock.patch.dict(os.environ, {"AI_NAME": "Antigravity2", "AI_STATUS_EXTRA_AGENTS": "Antigravity2"}):
            with self.assertRaises(SystemExit) as cm:
                ai_status.command_restore_approved(state, ["PROBE-TASK-B23", "Restoring approved status"])
            self.assertIn("reopened by the reviewer", str(cm.exception).lower())

    def test_b24_probe_higher_priority_ready_task_skips_missing_approved_head(self):
        """B24 Probe: higher_priority_ready_task_exists skips tasks without approved_head."""
        config = supervisor.load_config()

        task_unapproved = {
            "id": "PROBE-B24-UNAPPROVED",
            "owner": "Codex5",
            "reviewer": "Antigravity5",
            "status": "review_approved",
            "priority": "P0",
            # No approved_head set
        }
        worker = {
            "task_id": "CURRENT-TASK",
            "agent_id": "Codex5",
            "request_snapshot": {"reason": "owned_in_progress_dispatch"},
        }
        task_map = {
            "CURRENT-TASK": {"id": "CURRENT-TASK", "owner": "Codex5", "status": "in_progress", "priority": "P2"},
            "PROBE-B24-UNAPPROVED": task_unapproved,
        }
        state = {
            "tasks": [task_unapproved],
            "agents": [],
        }

        result = supervisor.higher_priority_ready_task_exists(
            config=config,
            worker=worker,
            task_map=task_map,
            state=state,
        )
        self.assertFalse(result, "Unapproved task missing approved_head must not trigger preemption")

    def test_n3_probe_restore_approved_head_check_emission(self):
        """N3 Probe: restore_approved_head repairs missing-head shape, asserts task-review-gate emission, and fails closed on sha mismatch."""
        state_before = ai_status.load_state()
        state = copy.deepcopy(state_before)
        task = {
            "id": "PROBE-TASK-N3",
            "owner": "Antigravity2",
            "reviewer": "Antigravity5",
            "status": "review_approved",
            # No approved_head set (missing-head shape)
            "review_notes_zh": "Approved in prior round",
        }
        state["tasks"].append(task)
        ai_status.save_state(state)

        with mock.patch.object(ai_status, "resolve_task_sha", return_value="aaaa1111bbbb2222cccc3333dddd4444eeee5555"), \
             mock.patch.object(ai_status, "task_pr_head_and_merge_commit", return_value=("aaaa1111bbbb2222cccc3333dddd4444eeee5555", None)), \
             mock.patch.dict(os.environ, {"AI_NAME": "Antigravity5", "AI_STATUS_EXTRA_AGENTS": "Antigravity5"}), \
             mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")

            ai_status.command_restore_approved_head(state, ["PROBE-TASK-N3", "aaaa1111bbbb2222cccc3333dddd4444eeee5555", "Restoring head"])

            updated = [t for t in state["tasks"] if t["id"] == "PROBE-TASK-N3"][0]
            self.assertEqual(updated["status"], "review_approved")
            self.assertEqual(updated["approved_head"], "aaaa1111bbbb2222cccc3333dddd4444eeee5555")
            self.assertEqual(updated["last_approved_head"], "aaaa1111bbbb2222cccc3333dddd4444eeee5555")

            # N3 status check emission assertion: emit_status_checks_for_changed_tasks must emit task-review-gate=success
            ai_status.emit_status_checks_for_changed_tasks(state_before, state, "restore_approved_head", ["PROBE-TASK-N3", "aaaa1111bbbb2222cccc3333dddd4444eeee5555", "Restoring head"])
            self.assertTrue(mock_run.called, "emit_status_checks_for_changed_tasks must call subprocess.run to post GitHub status check")
            cmd_args = mock_run.call_args[0][0]
            self.assertIn("context=task-review-gate", cmd_args)
            self.assertIn("state=success", cmd_args)
            self.assertIn("statuses/aaaa1111bbbb2222cccc3333dddd4444eeee5555", cmd_args[4])

            # Pending/Failure gate assertion: when task status is not review_approved or done, gate emits failure
            mock_run.reset_mock()
            in_progress_task = dict(updated)
            in_progress_task["status"] = "in_progress"
            ai_status.emit_task_review_status_check(in_progress_task, "in_progress")
            self.assertTrue(mock_run.called)
            cmd_args_pending = mock_run.call_args[0][0]
            self.assertIn("context=task-review-gate", cmd_args_pending)
            self.assertIn("state=failure", cmd_args_pending)

            # Mismatch probe: must fail closed when requested sha != current branch head
            with self.assertRaises(SystemExit) as cm:
                ai_status.command_restore_approved_head(state, ["PROBE-TASK-N3", "ffff1111bbbb2222cccc3333dddd4444eeee5555", "Wrong sha"])
            self.assertIn("already carries one", str(cm.exception).lower())


if __name__ == "__main__":
    unittest.main()
