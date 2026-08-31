"""Focused regression tests for canonical dependency updates and launch gates."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

THIS_DIR = Path(__file__).resolve().parent
ROOT_DIR = THIS_DIR.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

os.environ.setdefault("PANTHEON_STATUS_ROOT", tempfile.mkdtemp(prefix="pantheon-dependency-gate-status-"))
os.environ.setdefault("ORCH_STATUS_ROOT", os.environ["PANTHEON_STATUS_ROOT"])

import ai_status
import supervisor


class DependencyGraphValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = {
            "id": "GATE-TARGET-001",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": [],
        }
        self.upstream = {
            "id": "GATE-UPSTREAM-001",
            "status": "in_progress",
            "owner": "Claude",
            "reviewer": "Codex",
            "depends_on": [],
        }

    def test_update_is_audited_after_graph_validation(self) -> None:
        state = {"tasks": [deepcopy(self.target), deepcopy(self.upstream)]}
        with (
            tempfile.TemporaryDirectory(prefix="pantheon-dependency-audit-") as tmpdir,
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(ai_status, "load_archived_snapshot", return_value=None),
            mock.patch.object(ai_status, "LOG_FILE", Path(tmpdir) / "activity.jsonl"),
        ):
            ai_status.command_set_dependencies(
                state,
                ["GATE-TARGET-001", "GATE-UPSTREAM-001", "等待上游完成"],
            )
            audit = json.loads((Path(tmpdir) / "activity.jsonl").read_text(encoding="utf-8"))

        self.assertEqual(state["tasks"][0]["depends_on"], ["GATE-UPSTREAM-001"])
        self.assertEqual(audit["type"], "dependency_update")
        self.assertEqual(audit["old_dependencies"], [])
        self.assertEqual(audit["new_dependencies"], ["GATE-UPSTREAM-001"])
        self.assertEqual(audit["source"], "canonical_cli")

    def test_invalid_dependency_update_does_not_mutate_state(self) -> None:
        state = {"tasks": [deepcopy(self.target), deepcopy(self.upstream)]}
        before = deepcopy(state)
        for dependencies in (
            ["GATE-TARGET-001"],
            ["GATE-MISSING-001"],
            ["GATE-UPSTREAM-001", "GATE-UPSTREAM-001"],
        ):
            with self.subTest(dependencies=dependencies), mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False), mock.patch.object(ai_status, "load_archived_snapshot", return_value=None):
                with self.assertRaises(SystemExit):
                    ai_status.command_set_dependencies(
                        state,
                        ["GATE-TARGET-001", ",".join(dependencies), "invalid graph"],
                    )
            self.assertEqual(state, before)

    def test_reachable_cycle_and_live_archive_duplicate_are_rejected(self) -> None:
        cycle_state = {
            "tasks": [
                {**deepcopy(self.target), "depends_on": []},
                {**deepcopy(self.upstream), "id": "GATE-UPSTREAM-001", "depends_on": ["GATE-TARGET-001"]},
            ]
        }
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(ai_status, "load_archived_snapshot", return_value=None),
        ):
            with self.assertRaisesRegex(SystemExit, "cycle"):
                ai_status.command_set_dependencies(
                    cycle_state,
                    ["GATE-TARGET-001", "GATE-UPSTREAM-001", "cycle test"],
                )

        duplicate_state = {"tasks": [deepcopy(self.target), deepcopy(self.upstream)]}
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(
                ai_status,
                "load_archived_snapshot",
                return_value={"task_id": "GATE-UPSTREAM-001", "terminal_status": "done"},
            ),
        ):
            with self.assertRaisesRegex(SystemExit, "BOTH"):
                ai_status.command_set_dependencies(
                    duplicate_state,
                    ["GATE-TARGET-001", "GATE-UPSTREAM-001", "duplicate lifecycle"],
                )


class DependencyDispatchGateTests(unittest.TestCase):
    def _config(self) -> dict:
        return {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {"dependency_done_statuses": ["done"]},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
            },
            "providers": {"codex": {}},
        }

    def test_stale_in_progress_special_case_still_obeys_dependency_gate(self) -> None:
        task = {
            "id": "GATE-STALE-001",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": ["GATE-UPSTREAM-001"],
        }
        upstream = {"id": "GATE-UPSTREAM-001", "status": "in_progress", "depends_on": []}
        event = {
            "event_key": "old-dispatch-key",
            "task_id": task["id"],
            "target_agent": "codex",
            "target_display_name": "Codex",
            "reason": "owned_ready_dispatch",
        }

        message = supervisor.stale_dispatch_skip_message(
            self._config(), event, {task["id"]: task, upstream["id"]: upstream}
        )

        self.assertIsNotNone(message)
        self.assertIn("dependency gate", message or "")

    def test_blocked_recovery_requires_a_task_snapshot_when_dependencies_exist(self) -> None:
        task = {
            "id": "GATE-BLOCKED-001",
            "status": "blocked",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": ["GATE-UPSTREAM-001"],
            "next": "stale dispatch provider failure; retry",
        }

        self.assertFalse(
            supervisor.blocked_task_auto_recovery_eligible(self._config(), task, None)
        )

    def test_queue_revalidates_canonical_dependencies_before_start(self) -> None:
        task = {
            "id": "GATE-TOCTOU-001",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": ["GATE-UPSTREAM-001"],
            "last_update": "2026-08-31T00:00:00Z",
        }
        ready_upstream = {"id": "GATE-UPSTREAM-001", "status": "done", "depends_on": []}
        blocked_upstream = {"id": "GATE-UPSTREAM-001", "status": "blocked", "depends_on": []}
        config = self._config()
        event = supervisor.build_dispatch_event(
            task,
            "Codex",
            "owned_in_progress_dispatch",
            {task["id"]: task, ready_upstream["id"]: ready_upstream},
        )
        event.update(
            {
                "event_id": "evt-gate-toctou",
                "event_key": event["key"],
                "target_agent": "codex",
                "target_display_name": "Codex",
            }
        )
        initial_status = {"tasks": [task, ready_upstream]}
        latest_status = {"tasks": [task, blocked_upstream]}
        state = {"queue": {"events": {}}, "workers": {}}

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[event]),
            mock.patch.object(supervisor, "load_status", side_effect=[initial_status, latest_status]),
            mock.patch.object(supervisor, "build_request", return_value=SimpleNamespace(
                provider="codex", agent_id="codex", task_id=task["id"], metadata={}
            )),
            mock.patch.object(supervisor, "prepare_worker_workspace", return_value=(True, None)),
            mock.patch.object(supervisor, "start_worker_for_request", side_effect=AssertionError("gate must stop launch")),
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.process_queue(config, state, provider_report={})

        self.assertTrue(changed)
        self.assertEqual(state["queue"]["events"][event["event_id"]]["skip_reason"], "stale_dispatch_event")


if __name__ == "__main__":
    unittest.main()
