#!/usr/bin/env python3
from __future__ import annotations

import unittest
from unittest import mock

import watch_events


class WatcherBookkeepingTests(unittest.TestCase):
    def test_owner_wakeup_requires_official_handoff_before_exit(self) -> None:
        config = {
            "agents": {
                "antigravity4": {
                    "id": "antigravity4",
                    "display_name": "Antigravity4",
                    "wake_template": ".orchestrator/templates/wakeup.txt",
                }
            },
            "branch_workflow": {"dev_branch": "dev", "task_branch_prefix": "task/"},
        }
        event = {
            "task_id": "ODP-POSTCONDITION-001",
            "reason": "owned_in_progress_dispatch",
            "context_files": ["ai-status.json"],
            "task": {"artifacts": []},
        }

        message = watch_events.render_wakeup_message(config, event, "antigravity4")

        self.assertIn("正式 handoff／re_review", message)
        self.assertIn("no-progress failure", message)

    def test_reviewer_wakeup_requires_durable_review_decision(self) -> None:
        config = {
            "agents": {
                "codex6": {
                    "id": "codex6",
                    "display_name": "Codex6",
                    "wake_template": ".orchestrator/templates/wakeup.txt",
                }
            },
            "branch_workflow": {"dev_branch": "dev", "task_branch_prefix": "task/"},
        }
        event = {
            "task_id": "ODP-POSTCONDITION-001",
            "reason": "review_ready_dispatch",
            "context_files": ["ai-status.json"],
            "task": {"artifacts": []},
        }

        message = watch_events.render_wakeup_message(config, event, "codex6")

        self.assertIn("reviewer dispatch", message)
        self.assertIn("通過則 approve", message)
        self.assertIn("no-progress failure", message)

    def test_run_scan_updates_snapshot_without_queueing_when_runtime_enqueue_disabled(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "status_field": "status",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
                "handoffs_path": "handoffs",
            },
            "events": {
                "enqueue_runtime_events": False,
                "review_statuses": ["review"],
                "pending_handoff_statuses": ["pending"],
            },
            "watcher": {"max_seen_events": 2000},
        }
        state = {
            "initialized_at": "2026-04-06T09:00:00Z",
            "last_scan_at": "2026-04-06T09:00:00Z",
            "tasks": {
                "P3-001": {
                    "id": "P3-001",
                    "status": "in_progress",
                    "owner": "Claude",
                    "reviewer": "Codex",
                }
            },
            "pending_handoff_keys": [],
            "seen_event_keys": {},
        }
        status = {
            "tasks": [
                {
                    "id": "P3-001",
                    "status": "review",
                    "owner": "Claude",
                    "reviewer": "Codex",
                }
            ],
            "handoffs": [],
        }

        with (
            mock.patch.object(watch_events, "load_status", return_value=status),
            mock.patch.object(watch_events, "recent_terminal_summaries", return_value=[{"task_id": "OPS-001"}]),
            mock.patch.object(watch_events, "queue_delivery_event", side_effect=AssertionError("watcher should not queue runtime events")),
            mock.patch.object(watch_events, "save_runtime_state"),
        ):
            changed = watch_events.run_scan(config, state, replay=False, provider_capabilities={})

        self.assertTrue(changed)
        self.assertEqual(state["tasks"]["P3-001"]["status"], "review")
        self.assertEqual(state["recent_terminal_tasks"], [{"task_id": "OPS-001"}])
        self.assertEqual(state["pending_handoff_keys"], [])
        self.assertIsNotNone(state["last_scan_at"])


if __name__ == "__main__":
    unittest.main()
