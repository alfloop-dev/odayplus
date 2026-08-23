#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / "scripts", ROOT / ".orchestrator"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import ai_status
import multi_repo_registry
import task_archive


PARENT_HEAD = "a" * 40
CHILD_HEAD = "c" * 40
MERGE_COMMIT = "d" * 40


def config_for(root: Path) -> dict[str, object]:
    return {
        "paths": {"status_file": str(root / "ai-status.json")},
        "events": {"enqueue_runtime_events": False},
        "coordination": {
            "repositories": {
                "odayplus": {"local_path": str(root / "odayplus")},
                "oday_data_platform": {"local_path": str(root / "oday-data-platform")},
            }
        },
    }


def task_with_delivery() -> dict[str, object]:
    return {
        "id": "XR-CUTOVER-001",
        "title": "EMGI cutover",
        "owner": "Codex",
        "reviewer": "Claude",
        "status": "review_approved",
        "approved_head": PARENT_HEAD,
        "repository": "alfloop-dev/oday-data-platform",
        "pr_number": 45,
        "required_deliveries": [
            {
                "id": "consumer-cutover",
                "repository": "alfloop-dev/odayplus",
                "pr_number": 970,
                "head_branch": "task/XR-CUTOVER-001-CONSUMER",
                "approved_head": CHILD_HEAD,
                "base_branch": "dev",
            }
        ],
    }


def red_open_child_pr() -> dict[str, object]:
    return {
        "number": 970,
        "state": "OPEN",
        "mergeStateStatus": "UNKNOWN",
        "mergedAt": None,
        "mergeCommit": None,
        "headRefOid": CHILD_HEAD,
        "headRefName": "task/XR-CUTOVER-001-CONSUMER",
        "baseRefName": "dev",
        "statusCheckRollup": [
            {
                "__typename": "CheckRun",
                "name": "consumer-cutover",
                "status": "COMPLETED",
                "conclusion": "FAILURE",
            }
        ],
    }


def green_merged_child_pr() -> dict[str, object]:
    payload = red_open_child_pr()
    payload.update(
        {
            "state": "MERGED",
            "mergeStateStatus": "UNKNOWN",
            "mergedAt": "2026-08-23T04:00:00Z",
            "mergeCommit": {"oid": MERGE_COMMIT},
            "statusCheckRollup": [
                {
                    "__typename": "CheckRun",
                    "name": "consumer-cutover",
                    "status": "COMPLETED",
                    "conclusion": "SUCCESS",
                }
            ],
        }
    )
    return payload


def green_mergeable_child_pr() -> dict[str, object]:
    payload = green_merged_child_pr()
    payload.update(
        {
            "state": "OPEN",
            "mergeStateStatus": "CLEAN",
            "mergedAt": None,
            "mergeCommit": None,
        }
    )
    return payload


class CrossRepoTerminalGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="cross-repo-terminal-gate-")
        self.root = Path(self.tmp.name)
        (self.root / "odayplus").mkdir()
        (self.root / "oday-data-platform").mkdir()
        self.config = config_for(self.root)
        self.state = {"tasks": [], "blockers": [], "handoffs": []}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _status_config(self):
        return mock.patch.object(ai_status, "status_runtime_config", return_value=self.config)

    def _actor(self):
        return mock.patch.object(
            ai_status,
            "registered_agent_names",
            return_value={"Codex", "Claude", "Orchestrator", "Human/Ops"},
        )

    def test_delivery_requirements_are_explicit_and_do_not_copy_parent_pr(self) -> None:
        parent = {
            "id": "PARENT",
            "repository": "alfloop-dev/oday-data-platform",
            "pr_number": 45,
        }
        self.assertEqual(
            multi_repo_registry.task_delivery_requirements(self.config, parent), []
        )

        requirements = multi_repo_registry.task_delivery_requirements(
            self.config, task_with_delivery()
        )
        self.assertEqual(len(requirements), 1)
        self.assertEqual(requirements[0]["repository"], "alfloop-dev/odayplus")
        self.assertEqual(requirements[0]["pr_number"], 970)
        self.assertEqual(requirements[0]["approved_head"], CHILD_HEAD)

    def test_false_zero_board_blocks_parent_when_consumer_pr_is_open_and_red(self) -> None:
        task = task_with_delivery()
        calls: list[list[str]] = []

        def fake_gh(args: list[str], *, cwd: Path | None = None):
            calls.append(args)
            return red_open_child_pr()

        with self._status_config(), mock.patch.object(
            ai_status, "run_gh_json_command", side_effect=fake_gh
        ):
            gate = ai_status.evaluate_cross_repo_delivery_gate(task, self.state)

        self.assertEqual(gate["status"], "blocked")
        self.assertEqual(len(gate["blockers"]), 1)
        message = gate["blockers"][0]["message"]
        self.assertIn("#970", message)
        self.assertIn("red", message)
        self.assertFalse(any("45" in arg for call in calls for arg in call))

    def test_done_rejects_and_records_active_child_blocker(self) -> None:
        task = task_with_delivery()
        self.state["tasks"] = [task]
        with (
            self._status_config(),
            self._actor(),
            mock.patch.dict("os.environ", {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(ai_status, "run_gh_json_command", return_value=red_open_child_pr()),
            mock.patch.object(
                ai_status,
                "collect_done_delivery_metadata",
                side_effect=AssertionError("parent delivery must not be collected"),
            ),
        ):
            with self.assertRaises(ai_status.CrossRepoDeliveryBlocked):
                ai_status.command_done(self.state, [task["id"], "close"])

        self.assertEqual(task["status"], "review_approved")
        active = [item for item in self.state["blockers"] if item["status"] == "open"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["pr_number"], 970)
        self.assertEqual(task["cross_repo_delivery_gate"]["status"], "blocked")

    def test_merged_green_child_resolves_blocker_and_wakes_supervisor(self) -> None:
        task = task_with_delivery()
        self.state["tasks"] = [task]
        with self._status_config(), mock.patch.object(
            ai_status, "run_gh_json_command", return_value=red_open_child_pr()
        ):
            first = ai_status.refresh_cross_repo_delivery_gate(self.state, task)
        self.assertEqual(first["status"], "blocked")

        with self._status_config(), mock.patch.object(
            ai_status, "run_gh_json_command", return_value=green_merged_child_pr()
        ):
            second = ai_status.refresh_cross_repo_delivery_gate(self.state, task)
        self.assertEqual(second["status"], "ready")
        self.assertEqual(
            [item["status"] for item in self.state["blockers"]], ["resolved"]
        )
        self.assertEqual(len(self.state["supervisor_wakeups"]), 1)
        self.assertEqual(self.state["supervisor_wakeups"][0]["task_id"], task["id"])

    def test_green_mergeable_child_wakes_supervisor_before_merge(self) -> None:
        task = task_with_delivery()
        self.state["tasks"] = [task]
        with self._status_config(), mock.patch.object(
            ai_status, "run_gh_json_command", return_value=red_open_child_pr()
        ):
            ai_status.refresh_cross_repo_delivery_gate(self.state, task)

        with self._status_config(), mock.patch.object(
            ai_status, "run_gh_json_command", return_value=green_mergeable_child_pr()
        ):
            gate = ai_status.refresh_cross_repo_delivery_gate(self.state, task)

        self.assertEqual(gate["status"], "blocked")
        self.assertEqual(gate["blockers"][0]["code"], "mergeable")
        self.assertEqual(len(self.state["supervisor_wakeups"]), 1)

    def test_archive_refuses_carried_unresolved_delivery_and_keeps_existing_snapshot(self) -> None:
        blocked = task_with_delivery()
        blocked["status"] = "done"
        blocked["cross_repo_delivery_gate"] = {
            "status": "blocked",
            "blockers": [{"message": "consumer PR #970 is still open"}],
        }
        with self.assertRaisesRegex(ValueError, "Cannot archive task"):
            task_archive.archive_task_snapshot(blocked)

        with tempfile.TemporaryDirectory(prefix="archive-immutability-") as archive_root:
            original_dir = task_archive.ARCHIVE_TASKS_DIR
            original_index = task_archive.ARCHIVE_INDEX_FILE
            original_archive = task_archive.ARCHIVE_DIR
            try:
                task_archive.ARCHIVE_DIR = Path(archive_root)
                task_archive.ARCHIVE_TASKS_DIR = Path(archive_root) / "tasks"
                task_archive.ARCHIVE_INDEX_FILE = Path(archive_root) / "index.json"
                first = {"id": "ARCHIVE-1", "status": "done", "title": "original"}
                task_archive.archive_task_snapshot(first)
                task_archive.archive_task_snapshot(
                    {"id": "ARCHIVE-1", "status": "done", "title": "replacement"}
                )
                snapshot = task_archive.load_archived_snapshot("ARCHIVE-1")
                self.assertEqual(snapshot["task"]["title"], "original")
            finally:
                task_archive.ARCHIVE_DIR = original_archive
                task_archive.ARCHIVE_TASKS_DIR = original_dir
                task_archive.ARCHIVE_INDEX_FILE = original_index


if __name__ == "__main__":
    unittest.main()
