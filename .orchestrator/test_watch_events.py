#!/usr/bin/env python3
from __future__ import annotations

import unittest
from unittest import mock

import watch_events


class WatcherBookkeepingTests(unittest.TestCase):
    def test_execution_prompts_require_durable_owner_and_reviewer_transitions(self) -> None:
        base = {
            "schema": {},
            "branch_workflow": {"dev_branch": "dev", "task_branch_prefix": "task/"},
            "agents": {
                "antigravity4": {"id": "antigravity4", "display_name": "Antigravity4", "wake_template": ".orchestrator/templates/wakeup.txt"},
                "codex6": {"id": "codex6", "display_name": "Codex6", "wake_template": ".orchestrator/templates/wakeup.txt"},
            },
        }
        owner_event = {
            "task_id": "ODP-PROMPT-001",
            "reason": "owned_in_progress_dispatch",
            "context_files": ["AI_COLLABORATION_GUIDE.md"],
            "task": {"artifacts": []},
        }
        reviewer_event = {
            "task_id": "ODP-PROMPT-002",
            "reason": "review_ready_dispatch",
            "context_files": ["AI_COLLABORATION_GUIDE.md"],
            "task": {"artifacts": []},
        }
        finalize_event = {
            "task_id": "ODP-PROMPT-003",
            "reason": "owned_finalize_dispatch",
            "context_files": ["AI_COLLABORATION_GUIDE.md"],
            "task": {"artifacts": [], "status": "review_approved"},
        }
        owner_message = watch_events.render_wakeup_message(base, owner_event, "antigravity4")
        reviewer_message = watch_events.render_wakeup_message(base, reviewer_event, "codex6")
        finalize_message = watch_events.render_wakeup_message(base, finalize_event, "antigravity4")
        self.assertIn("delivery_toolchain/git/task_finalize.sh", owner_message)
        self.assertIn("不得直接 handoff／re_review", owner_message)
        self.assertIn("no-progress failure", owner_message)
        self.assertIn("必須做出可稽核的 review 決定", reviewer_message)
        self.assertIn("讓 task 留在 review", reviewer_message)
        self.assertIn("immutable finalize dispatch", finalize_message)
        self.assertIn("不可 merge、rebase", finalize_message)
        self.assertIn("PR 尚未 merge 就保持 review_approved", finalize_message)
        self.assertNotIn("delivery_toolchain/git/task_finalize.sh 推送", finalize_message)

    def test_run_scan_is_noop_when_runtime_enqueue_disabled(self) -> None:
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

        self.assertFalse(changed)
        self.assertEqual(state["tasks"]["P3-001"]["status"], "in_progress")
        self.assertNotIn("recent_terminal_tasks", state)
        self.assertEqual(state["pending_handoff_keys"], [])
        self.assertEqual(state["last_scan_at"], "2026-04-06T09:00:00Z")


if __name__ == "__main__":
    unittest.main()
