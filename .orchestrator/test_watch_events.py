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
        self.assertIn("明確禁止執行 pytest、npm test、build、lint、security scan 與 E2E 等驗證命令", finalize_message)
        self.assertIn("僅讀取 exact approved head 的 PR、CI 與 receipt 證據，不得重跑測試", finalize_message)
        self.assertNotIn("delivery_toolchain/git/task_finalize.sh 推送", finalize_message)

    def test_nonmutating_owner_dispatch_prohibits_empty_pr_and_recommends_supersede(self) -> None:
        base = {
            "schema": {},
            "branch_workflow": {"dev_branch": "dev", "task_branch_prefix": "task/"},
            "agents": {
                "antigravity4": {"id": "antigravity4", "display_name": "Antigravity4", "wake_template": ".orchestrator/templates/wakeup.txt"},
            },
        }
        for flag in (False, "false"):
            with self.subTest(mutates_canonical=flag):
                nonmutating_event = {
                    "task_id": "ODP-NONMUTATING-001",
                    "reason": "owned_ready_dispatch",
                    "context_files": ["AI_COLLABORATION_GUIDE.md"],
                    "task": {"artifacts": [], "mutates_canonical": flag},
                }
                message = watch_events.render_wakeup_message(base, nonmutating_event, "antigravity4")
                self.assertIn("non-mutating", message)
                self.assertIn("mutates_canonical=false", message)
                self.assertIn("明確禁止建立空 PR", message)
                self.assertIn("commit", message)
                self.assertIn("task_finalize.sh", message)
                self.assertIn("不必要的測試", message)
                self.assertIn("supersede", message)
                self.assertIn('supersede ODP-NONMUTATING-001 "<checkpoint message>"', message)
                self.assertIn("不執行 `./delivery_toolchain/git/task_start.sh`", message)
                self.assertNotIn("delivery_toolchain/git/task_finalize.sh 推送", message)
                self.assertNotIn("不得直接 handoff／re_review", message)
                self.assertNotIn("優先使用 `./delivery_toolchain/git/task_start.sh", message)

        in_progress_nonmutating_event = {
            "task_id": "ODP-NONMUTATING-002",
            "reason": "owned_in_progress_dispatch",
            "context_files": ["AI_COLLABORATION_GUIDE.md"],
            "task": {"artifacts": [], "mutates_canonical": False},
        }
        in_progress_message = watch_events.render_wakeup_message(base, in_progress_nonmutating_event, "antigravity4")
        self.assertIn("non-mutating", in_progress_message)
        self.assertIn("明確禁止建立空 PR", in_progress_message)
        self.assertIn("supersede", in_progress_message)
        self.assertNotIn("delivery_toolchain/git/task_finalize.sh 推送", in_progress_message)

        mutating_event = {
            "task_id": "ODP-MUTATING-001",
            "reason": "owned_ready_dispatch",
            "context_files": ["AI_COLLABORATION_GUIDE.md"],
            "task": {"artifacts": [], "mutates_canonical": True},
        }
        mutating_message = watch_events.render_wakeup_message(base, mutating_event, "antigravity4")
        self.assertIn("delivery_toolchain/git/task_finalize.sh 推送", mutating_message)
        self.assertIn("./delivery_toolchain/git/task_start.sh", mutating_message)
        self.assertIn("不得直接 handoff／re_review", mutating_message)
        self.assertNotIn("mutates_canonical=false", mutating_message)
        self.assertNotIn("supersede", mutating_message)

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
            changed = watch_events.run_scan(config, state, replay=False)

        self.assertFalse(changed)
        self.assertEqual(state["tasks"]["P3-001"]["status"], "in_progress")
        self.assertNotIn("recent_terminal_tasks", state)
        self.assertEqual(state["pending_handoff_keys"], [])
        self.assertEqual(state["last_scan_at"], "2026-04-06T09:00:00Z")

    def test_wakeup_prompt_includes_test_completion_guidance_for_all_providers(self) -> None:
        base = {
            "schema": {},
            "branch_workflow": {"dev_branch": "dev", "task_branch_prefix": "task/"},
            "agents": {
                "antigravity4": {"id": "antigravity4", "display_name": "Antigravity4", "wake_template": ".orchestrator/templates/wakeup.txt"},
                "claude2": {"id": "claude2", "display_name": "Claude2", "wake_template": ".orchestrator/templates/wakeup.txt"},
                "codex6": {"id": "codex6", "display_name": "Codex6", "wake_template": ".orchestrator/templates/wakeup.txt"},
            },
        }
        for agent_id in ("antigravity4", "claude2", "codex6"):
            for reason in ("owned_ready_dispatch", "owned_in_progress_dispatch"):
                with self.subTest(agent=agent_id, reason=reason):
                    event = {
                        "task_id": "ODP-TEST-GUIDE-001",
                        "reason": reason,
                        "context_files": ["AI_COLLABORATION_GUIDE.md"],
                        "task": {"artifacts": []},
                    }
                    message = watch_events.render_wakeup_message(base, event, agent_id)
                    self.assertIn("測試執行與完成判定規範（僅在任務允許驗證時啟動測試）", message)
                    self.assertIn("原工具 terminal status 與 exit code", message)
                    self.assertIn("原背景 job handle 完成收據", message)
                    self.assertIn("同一個 shell child 可使用啟動時捕捉的 PID 執行 `wait` 並保存 exit code", message)
                    self.assertIn("禁止用等待 passed 摘要的 grep 迴圈", message)
                    self.assertIn("pgrep -f", message)
                    self.assertIn("kill -0", message)
                    self.assertIn("自身 wait shell", message)
                    self.assertIn("區分程序結束與 exit 成功", message)
                    self.assertIn("缺少摘要或測試 count 不代表程序仍在執行", message)
                    self.assertIn("讀取既有 log 或 JUnit", message)
                    self.assertIn("不得只為統計 count 重跑測試", message)
                    self.assertIn("退出收據不足或丟失時應回報狀態未知", message)
                    self.assertIn("不得冒充成功", message)
                    self.assertIn("不得無限等待", message)
                    self.assertIn("合理且單次的 diagnostic grep 或 process 狀態查詢不受此限", message)

        finalize_event = {
            "task_id": "ODP-FINALIZE-001",
            "reason": "owned_finalize_dispatch",
            "context_files": ["AI_COLLABORATION_GUIDE.md"],
            "task": {"artifacts": [], "status": "review_approved"},
        }
        finalize_msg = watch_events.render_wakeup_message(base, finalize_event, "antigravity4")
        self.assertIn("明確禁止執行 pytest、npm test、build、lint、security scan 與 E2E 等驗證命令", finalize_msg)
        self.assertIn("僅讀取 exact approved head 的 PR、CI 與 receipt 證據，不得重跑測試", finalize_msg)
        self.assertIn("測試執行與完成判定規範（僅在任務允許驗證時啟動測試）", finalize_msg)

        nonmutating_event = {
            "task_id": "ODP-NONMUTATING-003",
            "reason": "owned_ready_dispatch",
            "context_files": ["AI_COLLABORATION_GUIDE.md"],
            "task": {"artifacts": [], "mutates_canonical": False},
        }
        nonmutating_msg = watch_events.render_wakeup_message(base, nonmutating_event, "antigravity4")
        self.assertIn("明確禁止執行 pytest、npm test、build、lint、security scan 與 E2E 等不必要的測試或驗證命令", nonmutating_msg)
        self.assertIn("測試執行與完成判定規範（僅在任務允許驗證時啟動測試）", nonmutating_msg)


if __name__ == "__main__":
    unittest.main()
