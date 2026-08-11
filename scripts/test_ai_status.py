#!/usr/bin/env python3
from __future__ import annotations

import ast
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

_TEST_STATUS_ROOT_HANDLE = tempfile.TemporaryDirectory(prefix="pantheon-ai-status-tests-")
_TEST_STATUS_ROOT = Path(_TEST_STATUS_ROOT_HANDLE.name).resolve()

# Pytest imports every test module before it runs any module-level teardown.
# test_supervisor may therefore have imported ai_status against its own
# temporary status root already.  Only set the environment for a first import;
# setUpModule below rebinds the shared module for this module's actual lifetime.
_IMPORT_STATUS_ROOT = os.environ.get("PANTHEON_STATUS_ROOT")
_IMPORT_ORCH_STATUS_ROOT = os.environ.get("ORCH_STATUS_ROOT")
if "ai_status" not in sys.modules:
    os.environ["PANTHEON_STATUS_ROOT"] = str(_TEST_STATUS_ROOT)
    os.environ["ORCH_STATUS_ROOT"] = str(_TEST_STATUS_ROOT)
try:
    import ai_status
    import task_archive
finally:
    if _IMPORT_STATUS_ROOT is None:
        os.environ.pop("PANTHEON_STATUS_ROOT", None)
    else:
        os.environ["PANTHEON_STATUS_ROOT"] = _IMPORT_STATUS_ROOT
    if _IMPORT_ORCH_STATUS_ROOT is None:
        os.environ.pop("ORCH_STATUS_ROOT", None)
    else:
        os.environ["ORCH_STATUS_ROOT"] = _IMPORT_ORCH_STATUS_ROOT


_AI_STATUS_ROOT_ATTRIBUTES = (
    "STATUS_ROOT",
    "STATUS_FILE",
    "LOG_FILE",
    "CURRENT_WORK_FILE",
    "DOCS_SITE_DIR",
    "STATUS_ROOT_CONFIG_LOCAL_FILE",
    "PLANNING_STATE_FILE",
    "ORCHESTRATOR_STATE_FILE",
    "APPROVAL_QUEUE_FILE",
    "DASHBOARD_BUNDLE_FILE",
    "ARCHIVE_TASKS_DIR",
)
_TASK_ARCHIVE_ROOT_ATTRIBUTES = (
    "STATUS_ROOT",
    "ARCHIVE_DIR",
    "ARCHIVE_TASKS_DIR",
    "ARCHIVE_INDEX_FILE",
)
_RUNTIME_STATUS_ROOT: str | None = None
_RUNTIME_ORCH_STATUS_ROOT: str | None = None
_ORIGINAL_AI_STATUS_PATHS: dict[str, Path] = {}
_ORIGINAL_TASK_ARCHIVE_PATHS: dict[str, Path] = {}


def setUpModule() -> None:
    global _RUNTIME_STATUS_ROOT, _RUNTIME_ORCH_STATUS_ROOT
    global _ORIGINAL_AI_STATUS_PATHS, _ORIGINAL_TASK_ARCHIVE_PATHS

    _RUNTIME_STATUS_ROOT = os.environ.get("PANTHEON_STATUS_ROOT")
    _RUNTIME_ORCH_STATUS_ROOT = os.environ.get("ORCH_STATUS_ROOT")
    _ORIGINAL_AI_STATUS_PATHS = {
        name: getattr(ai_status, name) for name in _AI_STATUS_ROOT_ATTRIBUTES
    }
    _ORIGINAL_TASK_ARCHIVE_PATHS = {
        name: getattr(task_archive, name) for name in _TASK_ARCHIVE_ROOT_ATTRIBUTES
    }

    os.environ["PANTHEON_STATUS_ROOT"] = str(_TEST_STATUS_ROOT)
    os.environ["ORCH_STATUS_ROOT"] = str(_TEST_STATUS_ROOT)
    ai_status.STATUS_ROOT = _TEST_STATUS_ROOT
    ai_status.STATUS_FILE = _TEST_STATUS_ROOT / "ai-status.json"
    ai_status.LOG_FILE = _TEST_STATUS_ROOT / "ai-activity-log.jsonl"
    ai_status.CURRENT_WORK_FILE = _TEST_STATUS_ROOT / "current-work.md"
    ai_status.DOCS_SITE_DIR = _TEST_STATUS_ROOT / "docs-site"
    ai_status.STATUS_ROOT_CONFIG_LOCAL_FILE = _TEST_STATUS_ROOT / ".orchestrator" / "config.local.json"
    ai_status.PLANNING_STATE_FILE = _TEST_STATUS_ROOT / ".orchestrator" / "planning-state.json"
    ai_status.ORCHESTRATOR_STATE_FILE = _TEST_STATUS_ROOT / ".orchestrator" / "state.json"
    ai_status.APPROVAL_QUEUE_FILE = _TEST_STATUS_ROOT / ".orchestrator" / "approval-queue.json"
    ai_status.DASHBOARD_BUNDLE_FILE = _TEST_STATUS_ROOT / "dashboard-bundle.json"

    task_archive.STATUS_ROOT = _TEST_STATUS_ROOT
    task_archive.ARCHIVE_DIR = _TEST_STATUS_ROOT / "ai-task-archive"
    task_archive.ARCHIVE_TASKS_DIR = task_archive.ARCHIVE_DIR / "tasks"
    task_archive.ARCHIVE_INDEX_FILE = task_archive.ARCHIVE_DIR / "index.json"
    ai_status.ARCHIVE_TASKS_DIR = task_archive.ARCHIVE_TASKS_DIR


def tearDownModule() -> None:
    for name, value in _ORIGINAL_AI_STATUS_PATHS.items():
        setattr(ai_status, name, value)
    for name, value in _ORIGINAL_TASK_ARCHIVE_PATHS.items():
        setattr(task_archive, name, value)
    if _RUNTIME_STATUS_ROOT is None:
        os.environ.pop("PANTHEON_STATUS_ROOT", None)
    else:
        os.environ["PANTHEON_STATUS_ROOT"] = _RUNTIME_STATUS_ROOT
    if _RUNTIME_ORCH_STATUS_ROOT is None:
        os.environ.pop("ORCH_STATUS_ROOT", None)
    else:
        os.environ["ORCH_STATUS_ROOT"] = _RUNTIME_ORCH_STATUS_ROOT
    _TEST_STATUS_ROOT_HANDLE.cleanup()


class StatusRootRoutingTests(unittest.TestCase):
    def test_orchestrator_status_root_wins_over_worker_shadow_override(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ai-status-canonical-root-") as temp_dir:
            root = Path(temp_dir)
            canonical = root / "supervisor-live"
            shadow = root / "task-worktree"
            env = {
                "ORCH_STATUS_ROOT": str(canonical),
                "PANTHEON_STATUS_ROOT": str(shadow),
            }
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(ai_status.resolve_status_root(), canonical.resolve())
                self.assertEqual(task_archive.status_root(), canonical.resolve())

    def test_load_local_coordination_payload_tolerates_missing_yaml(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ai-status-no-yaml-") as temp_dir:
            root = Path(temp_dir)
            (root / "payload.yaml").write_text("status: done\n", encoding="utf-8")
            (root / "payload.json").write_text('{"status": "done"}\n', encoding="utf-8")

            with (
                mock.patch.object(ai_status, "ROOT", root),
                mock.patch.object(ai_status, "yaml", None),
                mock.patch.object(ai_status, "YAML_ERROR_TYPES", ()),
            ):
                self.assertIsNone(ai_status.load_local_coordination_payload("payload.yaml"))
                self.assertEqual(
                    {"status": "done"},
                    ai_status.load_local_coordination_payload("payload.json"),
                )

    def test_load_config_routes_runtime_paths_to_status_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ai-status-routing-") as temp_dir:
            root = Path(temp_dir)
            code_root = root / "code"
            status_root = root / "status"
            config_file = code_root / ".orchestrator" / "config.json"
            config_file.parent.mkdir(parents=True)
            config_file.write_text(
                json.dumps(
                    {
                        "paths": {
                            "status_file": "ai-status.json",
                            "activity_log": "ai-activity-log.jsonl",
                            "state_file": ".orchestrator/state.json",
                        }
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.object(ai_status, "CONFIG_FILE", config_file),
                mock.patch.object(ai_status, "STATUS_ROOT", status_root),
                mock.patch.object(ai_status, "STATUS_FILE", status_root / "ai-status.json"),
                mock.patch.object(ai_status, "LOG_FILE", status_root / "ai-activity-log.jsonl"),
                mock.patch.object(ai_status, "CURRENT_WORK_FILE", status_root / "current-work.md"),
                mock.patch.object(ai_status, "DOCS_SITE_DIR", status_root / "docs-site"),
                mock.patch.object(ai_status, "ORCHESTRATOR_STATE_FILE", status_root / ".orchestrator" / "state.json"),
                mock.patch.object(ai_status, "APPROVAL_QUEUE_FILE", status_root / ".orchestrator" / "approval-queue.json"),
            ):
                config = ai_status.load_config()

        self.assertEqual(config["paths"]["status_file"], str(status_root / "ai-status.json"))
        self.assertEqual(config["paths"]["activity_log"], str(status_root / "ai-activity-log.jsonl"))
        self.assertEqual(config["paths"]["state_file"], str(status_root / ".orchestrator" / "state.json"))
        self.assertEqual(config["paths"]["event_queue"], str(status_root / ".orchestrator" / "event-queue.jsonl"))


class ReviewApprovedWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = {
            "agents": [
                {"name": "Codex", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Claude", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Gemini", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Copilot", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
            ],
            "tasks": [
                {
                    "id": "REG-002",
                    "title": "Promotion gate",
                    "phase": "Epic C",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "review",
                    "review_submission": {
                        "pr_number": 123,
                        "remote_sha": "1111111122222222333333334444444455555555",
                        "branch": "task/REG-002",
                        "base_branch": "dev",
                    },
                    "depends_on": [],
                    "artifacts": [],
                    "acceptance": [],
                    "next": "Awaiting review",
                    "last_update": "2026-04-06T15:00:00Z",
                }
            ],
            "handoffs": [
                {
                    "task_id": "REG-002",
                    "from": "Codex",
                    "to": "Claude",
                    "message": "Please review the promotion gate.",
                    "status": "pending",
                    "created_at": "2026-04-06T15:00:00Z",
                }
            ],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }

    def test_approve_creates_owner_finalize_handoff(self) -> None:
        # resolve_task_sha is pinned so the approval freezes a known head:
        # command_approve now fails closed when it cannot resolve one, and an
        # unpatched probe would shell out to `gh`/`git` for a task id that has
        # no branch, making this unit test environment-dependent.
        with mock.patch.dict(os.environ, {"AI_NAME": "Claude", "REVIEW_NOTES_ZH": "審查通過||交回 owner 收尾"}, clear=False), \
             mock.patch.object(ai_status, "resolve_task_sha", return_value="1111111122222222333333334444444455555555"):
            ai_status.command_approve(self.state, ["REG-002", "Review passed. Owner should finalize."])

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "review_approved")
        self.assertEqual(task["approved_head"], "1111111122222222333333334444444455555555")
        self.assertEqual(task["review_notes_zh"], ["審查通過", "交回 owner 收尾"])

        pending = [handoff for handoff in self.state["handoffs"] if handoff["status"] != "done"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["from"], "Claude")
        self.assertEqual(pending[0]["to"], "Codex")
        self.assertIn("finalize", pending[0]["message"].lower())

    def test_done_requires_owner_and_review_approved(self) -> None:
        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
            with self.assertRaises(SystemExit):
                ai_status.command_done(self.state, ["REG-002", "Attempted direct completion"])

        self.state["tasks"][0]["status"] = "review_approved"
        approved_head = "1111111122222222333333334444444455555555"
        self.state["tasks"][0]["approved_head"] = approved_head

        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False):
            with self.assertRaises(SystemExit):
                ai_status.command_done(self.state, ["REG-002", "Reviewer cannot finalize"])

        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(
                ai_status,
                "collect_done_delivery_metadata",
                return_value={
                    "verified_head": approved_head,
                    "pull_request": {"head_sha": approved_head, "merge_commit": "merge-sha"},
                },
            ),
            mock.patch.object(ai_status, "archive_task_snapshot", return_value={"task_id": "REG-002"}) as archive_task_snapshot,
        ):
            ai_status.command_done(self.state, ["REG-002", "Owner finalized approved task"])

        self.assertIsNone(ai_status.get_task(self.state, "REG-002"))
        self.assertEqual(self.state["handoffs"], [])
        archive_task = archive_task_snapshot.call_args.args[0]
        self.assertEqual(archive_task["status"], "done")
        self.assertEqual(archive_task["terminal_outcome"], "completed")

    def test_handoff_must_go_from_owner_to_reviewer(self) -> None:
        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False):
            with self.assertRaises(SystemExit):
                ai_status.command_handoff(self.state, ["REG-002", "Claude", "Wrong actor"])

        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
            with self.assertRaises(SystemExit):
                ai_status.command_handoff(self.state, ["REG-002", "Gemini", "Wrong reviewer"])

        self.state["tasks"][0].pop("review_submission", None)
        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
            with self.assertRaises(SystemExit):
                ai_status.command_handoff(self.state, ["REG-002", "Claude", "Ready for review"])

        self.state["tasks"][0]["review_submission"] = {
            "pr_number": 123,
            "remote_sha": "1111111122222222333333334444444455555555",
        }
        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
            ai_status.command_handoff(self.state, ["REG-002", "Claude", "Ready for review"])

        self.assertEqual(self.state["tasks"][0]["status"], "review")

    def test_submit_review_requires_verified_remote_pr_before_review_transition(self) -> None:
        evidence = {
            "pr_number": 123,
            "pr_url": "https://github.com/example/repo/pull/123",
            "branch": "task/REG-002",
            "remote_sha": "1111111122222222333333334444444455555555",
            "base_branch": "dev",
            "verified_at": "2026-08-11T00:00:00Z",
        }
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(ai_status, "review_submission_for_task", return_value=evidence),
        ):
            ai_status.command_submit_review(self.state, ["REG-002", "123", "Ready for review"])

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "review")
        self.assertEqual(task["review_submission"], evidence)
        pending = [handoff for handoff in self.state["handoffs"] if handoff["status"] != "done"]
        self.assertEqual(pending[0]["to"], "Claude")

    def test_reviewer_reopen_creates_handoff_back_to_owner(self) -> None:
        self.state["tasks"][0]["status"] = "review"
        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False):
            ai_status.command_reopen(self.state, ["REG-002", "Please address the requested changes"])

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "in_progress")
        pending = [handoff for handoff in self.state["handoffs"] if handoff["status"] != "done"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["from"], "Claude")
        self.assertEqual(pending[0]["to"], "Codex")

    def test_restore_approved_refuses_when_reviewer_reopened(self) -> None:
        """B23: restore_approved must refuse when the downgrade was a reviewer rejection."""
        self.state["tasks"][0]["status"] = "review"
        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False), \
             mock.patch.object(ai_status, "resolve_task_sha", return_value="1111111122222222333333334444444455555555"):
            ai_status.command_approve(self.state, ["REG-002", "Approve first"])

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "review_approved")
        self.assertEqual(task["last_approved_head"], "1111111122222222333333334444444455555555")

        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False):
            ai_status.command_reopen(self.state, ["REG-002", "Please address changes"])

        self.assertEqual(task["status"], "in_progress")
        self.assertEqual(task["last_approved_head"], "1111111122222222333333334444444455555555")

        task["review_notes_zh"] = ["prior note"]
        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False), \
             mock.patch.object(ai_status, "resolve_task_sha", return_value="1111111122222222333333334444444455555555"):
            with self.assertRaises(SystemExit) as cm:
                ai_status.command_restore_approved(self.state, ["REG-002", "Attempt restore"])
            self.assertIn("reopened by the reviewer", str(cm.exception))

    def test_restore_approved_head_emits_status_check(self) -> None:
        """N3: restore_approved_head must trigger status check emission."""
        state_before = {
            "tasks": [{"id": "REG-002", "status": "review_approved", "owner": "Codex", "reviewer": "Claude"}]
        }
        state_after = {
            "tasks": [{"id": "REG-002", "status": "review_approved", "approved_head": "1111111122222222333333334444444455555555", "owner": "Codex", "reviewer": "Claude"}]
        }
        with mock.patch.object(ai_status, "emit_task_review_status_check") as mock_emit:
            ai_status.emit_status_checks_for_changed_tasks(
                state_before, state_after, "restore_approved_head", ["REG-002", "1111111122222222333333334444444455555555", "restore"]
            )
            mock_emit.assert_called_once()

    def test_normalize_handoffs_adds_finalize_handoff_for_approved_task(self) -> None:
        self.state["tasks"][0]["status"] = "review_approved"
        self.state["handoffs"] = []

        ai_status.normalize_handoffs(self.state)

        pending = [handoff for handoff in self.state["handoffs"] if handoff["status"] != "done"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["to"], "Codex")
        self.assertEqual(pending[0]["from"], "Claude")

    def test_supersede_closes_legacy_blocker_and_resolves_blocker_entries(self) -> None:
        self.state["tasks"][0]["status"] = "blocked"
        self.state["tasks"][0]["waiting_for"] = "Gemini"
        self.state["blockers"] = [
            {
                "task_id": "REG-002",
                "owner": "Codex",
                "waiting_for": "Gemini",
                "message": "Legacy lane replaced by newer execution slice",
                "status": "open",
                "created_at": "2026-04-06T15:05:00Z",
            }
        ]

        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(ai_status, "archive_task_snapshot", return_value={"task_id": "REG-002"}) as archive_task_snapshot,
        ):
            ai_status.command_supersede(self.state, ["REG-002", "Superseded by REG-010 after accepted consensus.", "REG-010"])

        self.assertIsNone(ai_status.get_task(self.state, "REG-002"))
        self.assertEqual(self.state["blockers"], [])
        archive_task = archive_task_snapshot.call_args.args[0]
        self.assertEqual(archive_task["status"], "done")
        self.assertEqual(archive_task["terminal_outcome"], "superseded")
        self.assertEqual(archive_task["superseded_by"], "REG-010")
        self.assertNotIn("waiting_for", archive_task)


class DeliveryMetadataValidationTests(unittest.TestCase):
    def test_collect_done_delivery_metadata_reports_all_missing_trailers_at_once(self) -> None:
        responses = iter(
            [
                "task/BG-006",
                "abc123",
                "BG-006 finalize operator acceptance matrix",
                "Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>",
                "Claude",
                "noreply@anthropic.com",
            ]
        )
        task = {
            "id": "BG-006",
            "owner": "Claude",
            "reviewer": "Codex",
            "status": "review_approved",
            "approved_head": "abc123",
        }

        with mock.patch.object(ai_status, "run_git_command", side_effect=lambda *args, **kwargs: next(responses)):
            with self.assertRaises(SystemExit) as exc_info:
                ai_status.collect_done_delivery_metadata(task, "Claude")

        message = str(exc_info.exception)
        self.assertIn("`LLM-Agent: ...`", message)
        self.assertIn("`Task-ID: ...`", message)
        self.assertIn("`Reviewer: ...`", message)

    def test_collect_done_delivery_metadata_uses_execute_plans_artifact_repo(self) -> None:
        responses = iter(
            [
                "task/FE-INT-GATE-DUMMY",
                "abc123",
                "FE-INT-GATE-DUMMY finalize execute-plans artifact",
                "LLM-Agent: Codex2\nTask-ID: FE-INT-GATE-DUMMY\nReviewer: Claude\n",
                "Codex2",
                "codex2@example.com",
                "",
                "",
            ]
        )
        calls: list[tuple[list[str], Path | None]] = []

        def fake_run_git_command(args: list[str], **kwargs: object) -> str:
            calls.append((args, kwargs.get("cwd") if isinstance(kwargs.get("cwd"), Path) else None))
            return next(responses)

        task = {
            "id": "FE-INT-GATE-DUMMY",
            "owner": "Codex2",
            "reviewer": "Claude",
            "status": "review_approved",
            "approved_head": "abc123",
            "artifacts": ["execute-plans/e2e/dummy.spec.ts"],
        }
        with (
            mock.patch.object(ai_status, "run_git_command", side_effect=fake_run_git_command),
            mock.patch.object(ai_status, "enforce_delivery_merged_gate"),
            mock.patch.object(ai_status, "git_remote_repository_slug", return_value="ajoe734/execute-plans"),
        ):
            delivery = ai_status.collect_done_delivery_metadata(task, "Codex2")

        execute_plans_root = Path(delivery["repository_path"])
        self.assertEqual(delivery["repository_id"], "execute_plans")
        self.assertEqual(delivery["repository_path"], str(execute_plans_root))
        self.assertEqual(delivery["repository_slug"], "ajoe734/execute-plans")
        self.assertEqual(delivery["branch"], "task/FE-INT-GATE-DUMMY")
        self.assertTrue(calls)
        self.assertTrue(all(cwd == execute_plans_root for _, cwd in calls))

    def test_collect_done_delivery_metadata_falls_back_to_pantheon_for_missing_mixed_repo(self) -> None:
        responses = iter(
            [
                "task/BFF-PM12-002",
                "abc123",
                "BFF-PM12-002: refresh closeout gate",
                "LLM-Agent: Codex2\nTask-ID: BFF-PM12-002\nReviewer: Claude2\n",
                "Codex2",
                "codex2@example.com",
                "",
                "",
            ]
        )
        calls: list[tuple[list[str], Path | None]] = []

        def fake_run_git_command(args: list[str], **kwargs: object) -> str:
            calls.append((args, kwargs.get("cwd") if isinstance(kwargs.get("cwd"), Path) else None))
            return next(responses)

        task = {
            "id": "BFF-PM12-002",
            "owner": "Codex2",
            "reviewer": "Claude2",
            "status": "review_approved",
            "approved_head": "abc123",
            "artifacts": [
                "execute-plans/src/lib/bff-v1/management.ts",
                "services/control-plane/bff/main.py",
            ],
        }
        pantheon_root = Path("/tmp/pantheon-task-worktree")
        missing_execute_plans_root = Path("/tmp/pantheon-worker-worktrees/pantheon/execute-plans")

        def fake_repository_local_path(_config: dict[str, object], repo_id: str | None) -> Path | None:
            if repo_id == "execute_plans":
                return missing_execute_plans_root
            if repo_id == "pantheon":
                return pantheon_root
            return None

        with (
            mock.patch.object(ai_status, "run_git_command", side_effect=fake_run_git_command),
            mock.patch.object(ai_status, "repository_local_path", side_effect=fake_repository_local_path),
            mock.patch.object(ai_status, "enforce_delivery_merged_gate"),
            mock.patch.object(ai_status, "git_remote_repository_slug", return_value="alfloop-dev/odayplus"),
        ):
            delivery = ai_status.collect_done_delivery_metadata(task, "Codex2")

        self.assertEqual(delivery["repository_id"], "pantheon")
        self.assertEqual(delivery["repository_path"], str(pantheon_root))
        self.assertEqual(delivery["branch"], "task/BFF-PM12-002")
        self.assertEqual(delivery["repository_fallback"]["from_repository_id"], "execute_plans")
        self.assertEqual(delivery["repository_fallback"]["missing_repository_path"], str(missing_execute_plans_root))
        self.assertTrue(calls)
        self.assertTrue(all(cwd == pantheon_root for _, cwd in calls))

    def test_collect_done_delivery_metadata_blocks_unmerged_task_pr(self) -> None:
        task = {
            "id": "REG-002",
            "owner": "Codex",
            "reviewer": "Claude",
            "status": "review_approved",
            "approved_head": "abc123",
            "artifacts": [],
        }

        def fake_run_git_command(args: list[str], **kwargs: object) -> str:
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return "task/REG-002"
            if args == ["rev-parse", "HEAD"]:
                return "abc123"
            if args == ["show", "-s", "--format=%s", "abc123"]:
                return "REG-002 finalize"
            if args == ["show", "-s", "--format=%b", "abc123"]:
                return "LLM-Agent: Codex\nTask-ID: REG-002\nReviewer: Claude\n"
            if args == ["show", "-s", "--format=%an", "abc123"]:
                return "Codex"
            if args == ["show", "-s", "--format=%ae", "abc123"]:
                return "codex@example.com"
            if args == ["status", "--porcelain", "--untracked-files=all"]:
                return ""
            if args == ["remote"]:
                return "origin"
            if args == ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"]:
                return "origin/task/REG-002"
            if args == ["rev-list", "--left-right", "--count", "origin/task/REG-002...HEAD"]:
                return "0 0"
            if args == ["fetch", "origin", "dev"]:
                return ""
            if args == ["rev-parse", "--verify", "origin/dev"]:
                return "devsha"
            raise AssertionError(f"unexpected git command: {args}")

        with (
            mock.patch.object(ai_status, "run_git_command", side_effect=fake_run_git_command),
            mock.patch.object(ai_status, "git_command_succeeds", return_value=False),
            mock.patch.object(
                ai_status,
                "pull_request_status_for_branch",
                return_value={
                    "number": 152,
                    "state": "OPEN",
                    "mergeStateStatus": "BEHIND",
                    "autoMergeRequest": {"mergeMethod": "MERGE"},
                    "url": "https://github.com/ajoe734/pantheon/pull/152",
                },
            ),
            mock.patch.object(ai_status, "repository_slug", return_value="alfloop-dev/odayplus"),
            mock.patch.object(ai_status, "git_remote_repository_slug", return_value="alfloop-dev/odayplus"),
        ):
            with self.assertRaises(SystemExit) as exc_info:
                ai_status.collect_done_delivery_metadata(task, "Codex")

        message = str(exc_info.exception)
        self.assertIn("immutable approved-head PR provenance", message)
        self.assertIn("PR #152", message)
        self.assertIn("mergeState=BEHIND", message)
        self.assertIn("matching approved PR head", message)

    def test_collect_done_delivery_metadata_allows_head_merged_to_dev(self) -> None:
        task = {
            "id": "REG-002",
            "owner": "Codex",
            "reviewer": "Claude",
            "status": "review_approved",
            "approved_head": "abc123",
            "artifacts": [],
        }

        def fake_run_git_command(args: list[str], **kwargs: object) -> str:
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return "task/REG-002"
            if args == ["rev-parse", "HEAD"]:
                return "abc123"
            if args == ["show", "-s", "--format=%s", "abc123"]:
                return "REG-002 finalize"
            if args == ["show", "-s", "--format=%b", "abc123"]:
                return "LLM-Agent: Codex\nTask-ID: REG-002\nReviewer: Claude\n"
            if args == ["show", "-s", "--format=%an", "abc123"]:
                return "Codex"
            if args == ["show", "-s", "--format=%ae", "abc123"]:
                return "codex@example.com"
            if args == ["status", "--porcelain", "--untracked-files=all"]:
                return ""
            if args == ["remote"]:
                return "origin"
            if args == ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"]:
                return "origin/task/REG-002"
            if args == ["rev-list", "--left-right", "--count", "origin/task/REG-002...HEAD"]:
                return "0 0"
            if args == ["fetch", "origin", "dev"]:
                return ""
            if args == ["rev-parse", "--verify", "origin/dev"]:
                return "devsha"
            raise AssertionError(f"unexpected git command: {args}")

        merged_pr = {
            "number": 152,
            "state": "MERGED",
            "headRefOid": "abc123",
            "headRefName": "task/REG-002",
            "baseRefName": "dev",
            "mergedAt": "2026-08-02T00:55:48Z",
            "mergeCommit": {"oid": "merge123"},
            "statusCheckRollup": [
                {"__typename": "CheckRun", "name": "orchestrator", "status": "COMPLETED", "conclusion": "SUCCESS"}
            ],
        }
        with (
            mock.patch.object(ai_status, "run_git_command", side_effect=fake_run_git_command),
            mock.patch.object(ai_status, "git_command_succeeds", return_value=True),
            mock.patch.object(ai_status, "pull_request_status_for_branch", return_value=merged_pr),
            mock.patch.object(ai_status, "repository_slug", return_value="alfloop-dev/odayplus"),
            mock.patch.object(ai_status, "git_remote_repository_slug", return_value="alfloop-dev/odayplus"),
        ):
            delivery = ai_status.collect_done_delivery_metadata(task, "Codex")

        self.assertEqual(delivery["merge_target_branch"], "dev")
        self.assertEqual(delivery["merge_target_ref"], "origin/dev")
        self.assertEqual(delivery["merge_target_sha"], "devsha")
        self.assertTrue(delivery["head_merged_to_target"])

    def test_collect_done_delivery_metadata_allows_verified_dev_squash_merge(self) -> None:
        task = {
            "id": "REG-002",
            "owner": "Codex",
            "reviewer": "Claude",
            "status": "review_approved",
            "approved_head": "sourcehead",
            "artifacts": [],
        }

        def fake_run_git_command(args: list[str], **kwargs: object) -> str:
            responses = {
                ("rev-parse", "--abbrev-ref", "HEAD"): "task/REG-002",
                ("rev-parse", "HEAD"): "sourcehead",
                ("show", "-s", "--format=%s", "sourcehead"): "REG-002 finalize",
                ("show", "-s", "--format=%b", "sourcehead"): "LLM-Agent: Codex\nTask-ID: REG-002\nReviewer: Claude\n",
                ("show", "-s", "--format=%an", "sourcehead"): "Codex",
                ("show", "-s", "--format=%ae", "sourcehead"): "codex@example.com",
                ("status", "--porcelain", "--untracked-files=all"): "",
                ("remote",): "origin",
                ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"): "origin/task/REG-002",
                ("rev-list", "--left-right", "--count", "origin/task/REG-002...HEAD"): "0 0",
                ("fetch", "origin", "dev"): "",
                ("rev-parse", "--verify", "origin/dev"): "devtip",
            }
            key = tuple(args)
            if key not in responses:
                raise AssertionError(f"unexpected git command: {args}")
            return responses[key]

        def fake_git_succeeds(args: list[str], **kwargs: object) -> bool:
            if args == ["merge-base", "--is-ancestor", "sourcehead", "origin/dev"]:
                return False
            if args == ["merge-base", "--is-ancestor", "squashmerge", "origin/dev"]:
                return True
            raise AssertionError(f"unexpected git check: {args}")

        with (
            mock.patch.object(ai_status, "run_git_command", side_effect=fake_run_git_command),
            mock.patch.object(ai_status, "git_command_succeeds", side_effect=fake_git_succeeds),
            mock.patch.object(
                ai_status,
                "pull_request_status_for_branch",
                return_value={
                    "number": 533,
                    "state": "MERGED",
                    "headRefOid": "sourcehead",
                    "headRefName": "task/REG-002",
                    "baseRefName": "dev",
                    "mergedAt": "2026-07-31T08:08:36Z",
                    "mergeCommit": {"oid": "squashmerge"},
                    "url": "https://github.com/example/repo/pull/533",
                    "statusCheckRollup": [
                        {"__typename": "CheckRun", "name": "orchestrator", "status": "COMPLETED", "conclusion": "SUCCESS"},
                        {"__typename": "StatusContext", "context": "task-review-gate", "state": "SUCCESS"},
                    ],
                },
            ),
            mock.patch.object(ai_status, "repository_slug", return_value="alfloop-dev/odayplus"),
            mock.patch.object(ai_status, "git_remote_repository_slug", return_value="alfloop-dev/odayplus"),
        ):
            delivery = ai_status.collect_done_delivery_metadata(task, "Codex")

        self.assertFalse(delivery["head_merged_to_target"])
        self.assertTrue(delivery["merge_verified_via_pr"])
        self.assertEqual(delivery["pull_request"]["head_sha"], "sourcehead")
        self.assertEqual(delivery["pull_request"]["base_branch"], "dev")
        self.assertEqual(delivery["pull_request"]["merge_commit"], "squashmerge")


class DoneDeliveryProvenanceRegressionTests(unittest.TestCase):
    TASK_ID = "ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001"
    APPROVED_HEAD = "ca262d1737fcb8d9fc077eca13efa803bc56d0bc"
    MERGE_COMMIT = "8757c2d10fdfd6c972d586b0e6b7ac21712088c6"
    REPOSITORY = "alfloop-dev/odayplus"

    def pr_552(self) -> dict[str, object]:
        return {
            "number": 552,
            "state": "MERGED",
            "headRefOid": self.APPROVED_HEAD,
            "headRefName": f"task/{self.TASK_ID}",
            "baseRefName": "dev",
            "mergedAt": "2026-08-02T00:55:48Z",
            "mergeCommit": {"oid": self.MERGE_COMMIT},
            "url": "https://github.com/alfloop-dev/odayplus/pull/552",
            "statusCheckRollup": [
                {
                    "__typename": "CheckRun",
                    "name": "orchestrator",
                    "status": "COMPLETED",
                    "conclusion": "SUCCESS",
                },
                {
                    "__typename": "CheckRun",
                    "name": "product",
                    "status": "COMPLETED",
                    "conclusion": "SUCCESS",
                },
                {
                    "__typename": "StatusContext",
                    "context": "task-review-gate",
                    "state": "SUCCESS",
                },
            ],
        }

    def enforce_pr(self, pr_status: dict[str, object] | None, *, merge_on_target: bool = True) -> dict[str, object]:
        delivery: dict[str, object] = {}

        def fake_git(args: list[str], **kwargs: object) -> str:
            if args == ["fetch", "origin", "dev"]:
                return ""
            if args == ["rev-parse", "--verify", "origin/dev"]:
                return "dev-tip"
            raise AssertionError(f"unexpected git command: {args}")

        def fake_succeeds(args: list[str], **kwargs: object) -> bool:
            if args == ["merge-base", "--is-ancestor", self.APPROVED_HEAD, "origin/dev"]:
                return False
            if args == ["merge-base", "--is-ancestor", self.MERGE_COMMIT, "origin/dev"]:
                return merge_on_target
            raise AssertionError(f"unexpected git check: {args}")

        with (
            mock.patch.object(ai_status, "run_git_command", side_effect=fake_git),
            mock.patch.object(ai_status, "git_command_succeeds", side_effect=fake_succeeds),
            mock.patch.object(ai_status, "pull_request_status_for_branch", return_value=pr_status),
        ):
            ai_status.enforce_delivery_merged_gate(
                {"branch_workflow": {"dev_branch": "dev"}},
                delivery,
                repository_root=Path("/task-checkout"),
                repository_id="pantheon",
                branch=f"task/{self.TASK_ID}",
                remote_names=["origin"],
                approved_head=self.APPROVED_HEAD,
                repository_slug_value=self.REPOSITORY,
            )
        return delivery

    def test_pr_552_squash_topology_records_immutable_delivery_and_green_checks(self) -> None:
        delivery = self.enforce_pr(self.pr_552())

        self.assertFalse(delivery["head_merged_to_target"])
        self.assertTrue(delivery["merge_verified_via_pr"])
        self.assertEqual(delivery["pull_request"]["number"], 552)
        self.assertEqual(delivery["pull_request"]["head_sha"], self.APPROVED_HEAD)
        self.assertEqual(delivery["pull_request"]["merge_commit"], self.MERGE_COMMIT)
        self.assertEqual(delivery["ci_status"], "success")
        self.assertEqual([check["name"] for check in delivery["ci_checks"]], ["orchestrator", "product", "task-review-gate"])

    def test_pr_provenance_rejects_unmerged_closed_moved_wrong_task_and_wrong_base(self) -> None:
        cases = {
            "open": {"state": "OPEN"},
            "closed": {"state": "CLOSED"},
            "moved-head": {"headRefOid": "b" * 40},
            "wrong-task": {"headRefName": "task/OTHER-001"},
            "wrong-base": {"baseRefName": "main"},
            "missing-merge-time": {"mergedAt": ""},
            "missing-merge-commit": {"mergeCommit": None},
        }
        for label, mutation in cases.items():
            with self.subTest(label=label):
                pr_status = self.pr_552()
                pr_status.update(mutation)
                with self.assertRaisesRegex(SystemExit, "immutable approved-head PR provenance"):
                    self.enforce_pr(pr_status)

    def test_pr_provenance_rejects_unverifiable_network_and_merge_commit(self) -> None:
        with self.assertRaisesRegex(SystemExit, "Network and repository provenance fail closed"):
            self.enforce_pr(None)
        with self.assertRaisesRegex(SystemExit, "immutable approved-head PR provenance"):
            self.enforce_pr(self.pr_552(), merge_on_target=False)

    def test_pr_checks_reject_empty_red_pending_and_unknown_shapes(self) -> None:
        cases = {
            "empty": [],
            "red": [{"__typename": "CheckRun", "name": "product", "status": "COMPLETED", "conclusion": "FAILURE"}],
            "pending": [{"__typename": "CheckRun", "name": "product", "status": "IN_PROGRESS", "conclusion": ""}],
            "unknown": [{"__typename": "Mystery", "name": "product", "state": "SUCCESS"}],
        }
        for label, checks in cases.items():
            with self.subTest(label=label):
                pr_status = self.pr_552()
                pr_status["statusCheckRollup"] = checks
                with self.assertRaises(SystemExit):
                    self.enforce_pr(pr_status)

    def test_task_checkout_resolution_ignores_unrelated_canonical_writer_head(self) -> None:
        task_path = Path("/tmp/task-owned-checkout")
        worktrees = (
            "worktree /home/lupin/oday-plus-supervisor-live\n"
            "HEAD e496be62c47c45d758681b8a4d3abfae16f1c96d\n"
            "branch refs/heads/dev\n\n"
            f"worktree {task_path}\n"
            f"HEAD {self.APPROVED_HEAD}\n"
            f"branch refs/heads/task/{self.TASK_ID}\n\n"
        )

        def fake_git(args: list[str], **kwargs: object) -> str:
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return "dev"
            if args == ["worktree", "list", "--porcelain"]:
                return worktrees
            raise AssertionError(f"unexpected git command: {args}")

        with mock.patch.object(ai_status, "run_git_command", side_effect=fake_git):
            checkout, branch = ai_status.task_delivery_checkout(Path("/home/lupin/oday-plus-supervisor-live"), self.TASK_ID)

        self.assertEqual(checkout, task_path)
        self.assertEqual(branch, f"task/{self.TASK_ID}")

    def test_done_finalizes_from_merged_pr_despite_post_merge_checkout_advance(self) -> None:
        task = {
            "id": self.TASK_ID,
            "owner": "Antigravity4",
            "reviewer": "Codex4",
            "status": "review_approved",
            "approved_head": self.APPROVED_HEAD,
            "artifacts": [],
        }

        POST_MERGE_DEV_HEAD = "80ba278623b8d4ad4ce81ea749a5aee030e5c18d"

        def fake_git(args: list[str], **kwargs: object) -> str:
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return f"task/{self.TASK_ID}"
            if args == ["rev-parse", "HEAD"]:
                return POST_MERGE_DEV_HEAD
            if args == ["show", "-s", "--format=%s", self.APPROVED_HEAD]:
                return f"{self.TASK_ID}: seal done provenance"
            if args == ["show", "-s", "--format=%b", self.APPROVED_HEAD]:
                return f"LLM-Agent: Antigravity4\nTask-ID: {self.TASK_ID}\nReviewer: Codex4\n"
            if args == ["show", "-s", "--format=%an", self.APPROVED_HEAD]:
                return "Antigravity4"
            if args == ["show", "-s", "--format=%ae", self.APPROVED_HEAD]:
                return "antigravity4@example.com"
            if args == ["status", "--porcelain", "--untracked-files=all"]:
                return ""
            if args == ["remote"]:
                return "origin"
            if args == ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"]:
                return f"origin/task/{self.TASK_ID}"
            if args == ["rev-list", "--left-right", "--count", f"origin/task/{self.TASK_ID}...HEAD"]:
                return "0 0"
            if args == ["fetch", "origin", "dev"]:
                return ""
            if args == ["rev-parse", "--verify", "origin/dev"]:
                return POST_MERGE_DEV_HEAD
            raise AssertionError(f"unexpected git command: {args}")

        def fake_succeeds(args: list[str], **kwargs: object) -> bool:
            if args == ["merge-base", "--is-ancestor", self.APPROVED_HEAD, "origin/dev"]:
                return False
            if args == ["merge-base", "--is-ancestor", self.MERGE_COMMIT, "origin/dev"]:
                return True
            if args == ["merge-base", "--is-ancestor", self.MERGE_COMMIT, POST_MERGE_DEV_HEAD]:
                return True
            if args == ["merge-base", "--is-ancestor", self.APPROVED_HEAD, POST_MERGE_DEV_HEAD]:
                return True
            if args == ["merge-base", "--is-ancestor", POST_MERGE_DEV_HEAD, "origin/dev"]:
                return True
            raise AssertionError(f"unexpected git check: {args}")

        pr_status = self.pr_552()

        with (
            mock.patch.object(ai_status, "run_git_command", side_effect=fake_git),
            mock.patch.object(ai_status, "git_command_succeeds", side_effect=fake_succeeds),
            mock.patch.object(ai_status, "pull_request_status_for_branch", return_value=pr_status),
            mock.patch.object(ai_status, "repository_slug", return_value=self.REPOSITORY),
            mock.patch.object(ai_status, "git_remote_repository_slug", return_value=self.REPOSITORY),
        ):
            delivery = ai_status.collect_done_delivery_metadata(task, "Antigravity4", approved_head=self.APPROVED_HEAD)

        self.assertTrue(delivery["merge_verified_via_pr"])
        self.assertTrue(delivery["post_merge_checkout_advanced"])
        self.assertEqual(delivery["verified_head"], POST_MERGE_DEV_HEAD)
        self.assertEqual(delivery["approved_head"], self.APPROVED_HEAD)

    def test_git_clean_gate_ignores_only_exact_worker_seed_context(self) -> None:
        entries = [
            "?? AI_COLLABORATION_GUIDE.md",
            "?? ai-status.json",
            "?? .orchestrator/task-briefs/odp_operator_live_provenance_health_001.md",
            "?? .orchestrator/task-briefs/other-task.md",
            " M scripts/ai_status.py",
        ]

        owned, ignored = ai_status.split_task_owned_dirty_entries(entries, self.TASK_ID)

        self.assertEqual(ignored, entries[:3])
        self.assertEqual(owned, entries[3:])

    def test_collector_rejects_moved_or_dirty_task_checkout_even_if_env_disables_gates(self) -> None:
        task = {
            "id": self.TASK_ID,
            "owner": "Codex",
            "reviewer": "Codex8",
            "status": "review_approved",
            "approved_head": self.APPROVED_HEAD,
            "artifacts": [],
        }
        with (
            mock.patch.object(
                ai_status,
                "resolve_task_delivery_checkout",
                return_value={"checkout": Path("/task"), "branch": f"task/{self.TASK_ID}", "present": True},
            ),
            mock.patch.object(ai_status, "run_git_command", return_value="b" * 40),
        ):
            with self.assertRaisesRegex(SystemExit, "task-owned checkout HEAD"):
                ai_status.collect_done_delivery_metadata(task, "Codex")

        def fake_git(args: list[str], **kwargs: object) -> str:
            responses = {
                ("rev-parse", "HEAD"): self.APPROVED_HEAD,
                ("show", "-s", "--format=%s", self.APPROVED_HEAD): f"{self.TASK_ID}: bind E2E evidence",
                ("show", "-s", "--format=%b", self.APPROVED_HEAD): f"LLM-Agent: Codex\nTask-ID: {self.TASK_ID}\nReviewer: Codex8\n",
                ("show", "-s", "--format=%an", self.APPROVED_HEAD): "Codex",
                ("show", "-s", "--format=%ae", self.APPROVED_HEAD): "codex@example.com",
                ("status", "--porcelain", "--untracked-files=all"): " M scripts/ai_status.py",
            }
            key = tuple(args)
            if key not in responses:
                raise AssertionError(f"unexpected git command: {args}")
            return responses[key]

        disabled = {
            "TASK_REQUIRE_COMMIT_HASH": "false",
            "TASK_REQUIRE_GIT_CLEAN": "false",
            "TASK_REQUIRE_MERGED_PR": "false",
            "TASK_REQUIRE_SUBJECT_TASK_ID": "false",
            "TASK_COMMIT_REQUIRED_FIELDS": "Verified",
        }
        with (
            mock.patch.dict(os.environ, disabled, clear=False),
            mock.patch.object(
                ai_status,
                "resolve_task_delivery_checkout",
                return_value={"checkout": Path("/task"), "branch": f"task/{self.TASK_ID}", "present": True},
            ),
            mock.patch.object(ai_status, "run_git_command", side_effect=fake_git),
        ):
            with self.assertRaisesRegex(SystemExit, "task-owned git working tree is dirty"):
                ai_status.collect_done_delivery_metadata(task, "Codex")

    def test_collector_rejects_checkout_from_wrong_repository(self) -> None:
        task = {
            "id": self.TASK_ID,
            "owner": "Codex",
            "reviewer": "Codex8",
            "status": "review_approved",
            "approved_head": self.APPROVED_HEAD,
            "artifacts": [],
        }

        def fake_git(args: list[str], **kwargs: object) -> str:
            responses = {
                ("rev-parse", "HEAD"): self.APPROVED_HEAD,
                ("show", "-s", "--format=%s", self.APPROVED_HEAD): f"{self.TASK_ID}: bind E2E evidence",
                ("show", "-s", "--format=%b", self.APPROVED_HEAD): f"LLM-Agent: Codex\nTask-ID: {self.TASK_ID}\nReviewer: Codex8\n",
                ("show", "-s", "--format=%an", self.APPROVED_HEAD): "Codex",
                ("show", "-s", "--format=%ae", self.APPROVED_HEAD): "codex@example.com",
                ("status", "--porcelain", "--untracked-files=all"): "",
                ("remote",): "origin",
            }
            key = tuple(args)
            if key not in responses:
                raise AssertionError(f"unexpected git command: {args}")
            return responses[key]

        with (
            mock.patch.object(
                ai_status,
                "resolve_task_delivery_checkout",
                return_value={"checkout": Path("/task"), "branch": f"task/{self.TASK_ID}", "present": True},
            ),
            mock.patch.object(ai_status, "run_git_command", side_effect=fake_git),
            mock.patch.object(ai_status, "repository_slug", return_value=self.REPOSITORY),
            mock.patch.object(ai_status, "git_remote_repository_slug", return_value="attacker/wrong-repo"),
        ):
            with self.assertRaisesRegex(SystemExit, "does not match configured repository"):
                ai_status.collect_done_delivery_metadata(task, "Codex")

    def done_state(self) -> dict[str, object]:
        return {
            "agents": [],
            "tasks": [
                {
                    "id": self.TASK_ID,
                    "owner": "Codex7",
                    "reviewer": "Codex8",
                    "status": "review_approved",
                    "approved_head": self.APPROVED_HEAD,
                }
            ],
            "handoffs": [],
            "blockers": [],
        }

    def done_delivery(self, *, checkout_head: str | None = None, pr_head: str | None = None) -> dict[str, object]:
        return {
            "verified_head": checkout_head or self.APPROVED_HEAD,
            "pull_request": {
                "head_sha": pr_head or self.APPROVED_HEAD,
                "merge_commit": self.MERGE_COMMIT,
            },
        }

    def test_command_done_allows_deleted_remote_ref_after_merged_pr_provenance(self) -> None:
        state = self.done_state()
        registered_actor = {"AI_NAME": "Codex7", "AI_STATUS_EXTRA_AGENTS": "Codex7,Codex8"}
        with (
            mock.patch.dict(os.environ, registered_actor, clear=False),
            mock.patch.object(
                ai_status,
                "resolve_task_sha",
                side_effect=AssertionError("done must not require an ephemeral remote task ref"),
            ),
            mock.patch.object(ai_status, "collect_done_delivery_metadata", return_value=self.done_delivery()),
            mock.patch.object(ai_status, "archive_task_snapshot", return_value={"task_id": self.TASK_ID}),
            mock.patch.object(ai_status, "append_log"),
        ):
            ai_status.command_done(state, [self.TASK_ID, "done"])

        self.assertEqual(state["tasks"], [])

    def test_command_done_rejects_moved_checkout_or_pr_head(self) -> None:
        cases = {
            "checkout-moved": self.done_delivery(checkout_head="b" * 40),
            "pr-head-moved": self.done_delivery(pr_head="b" * 40),
            "pr-head-missing": {
                "verified_head": self.APPROVED_HEAD,
                "pull_request": {"merge_commit": self.MERGE_COMMIT},
            },
        }
        registered_actor = {"AI_NAME": "Codex7", "AI_STATUS_EXTRA_AGENTS": "Codex7,Codex8"}
        for label, delivery in cases.items():
            with self.subTest(label=label):
                with (
                    mock.patch.dict(os.environ, registered_actor, clear=False),
                    mock.patch.object(ai_status, "collect_done_delivery_metadata", return_value=delivery),
                ):
                    with self.assertRaisesRegex(SystemExit, "differs from reviewer-approved head"):
                        ai_status.command_done(self.done_state(), [self.TASK_ID, "done"])


class ArchiveWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = {
            "agents": [
                {"name": "Codex", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Claude", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
            ],
            "tasks": [
                {
                    "id": "REG-100",
                    "title": "Archived completion candidate",
                    "phase": "Epic X",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "done",
                    "terminal_outcome": "completed",
                    "depends_on": [],
                    "artifacts": [],
                    "acceptance": [],
                    "next": "Completed",
                    "last_update": "2026-04-14T02:00:00Z",
                },
                {
                    "id": "REG-101",
                    "title": "Still active",
                    "phase": "Epic X",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "todo",
                    "depends_on": ["REG-100"],
                    "artifacts": [],
                    "acceptance": [],
                    "next": "Waiting on archived dependency",
                    "last_update": "2026-04-14T02:00:00Z",
                },
            ],
            "handoffs": [
                {
                    "task_id": "REG-100",
                    "from": "Claude",
                    "to": "Codex",
                    "message": "Finalize complete",
                    "status": "done",
                    "created_at": "2026-04-14T01:50:00Z",
                }
            ],
            "blockers": [
                {
                    "task_id": "REG-100",
                    "owner": "Codex",
                    "waiting_for": "Claude",
                    "message": "Resolved blocker snapshot",
                    "status": "resolved",
                    "created_at": "2026-04-14T01:45:00Z",
                }
            ],
            "workload": {},
            "workload_summary": {},
        }

    def test_archive_migrate_moves_terminal_tasks_out_of_active_state(self) -> None:
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(ai_status, "archive_task_snapshot", return_value={"task_id": "REG-100"}) as archive_task_snapshot,
            mock.patch.object(ai_status, "rebuild_archive_index") as rebuild_archive_index,
        ):
            ai_status.command_archive_migrate(self.state, [])

        self.assertEqual([task["id"] for task in self.state["tasks"]], ["REG-101"])
        self.assertEqual(self.state["handoffs"], [])
        self.assertEqual(self.state["blockers"], [])
        archive_task = archive_task_snapshot.call_args.args[0]
        self.assertEqual(archive_task["id"], "REG-100")
        rebuild_archive_index.assert_called_once()

    def test_prune_archived_active_tasks_removes_duplicate_active_rows(self) -> None:
        def fake_archived_snapshot(task_id: str):
            return {"task_id": task_id} if task_id == "REG-100" else None

        with mock.patch.object(ai_status, "archived_task_snapshot", side_effect=fake_archived_snapshot):
            pruned = ai_status.prune_archived_active_tasks(self.state)

        self.assertEqual(pruned, ["REG-100"])
        self.assertEqual([task["id"] for task in self.state["tasks"]], ["REG-101"])
        self.assertEqual(self.state["handoffs"], [])
        self.assertEqual(self.state["blockers"], [])

    def test_reopen_rejects_archived_task(self) -> None:
        self.state["tasks"] = []
        with mock.patch.object(ai_status, "archived_task_snapshot", return_value={"task_id": "REG-100"}):
            with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
                with self.assertRaises(SystemExit) as exc_info:
                    ai_status.command_reopen(self.state, ["REG-100", "Resume work"])

        self.assertIn("archived", str(exc_info.exception))
        self.assertIn("follow-up", str(exc_info.exception))

    def test_show_reads_archive_snapshot(self) -> None:
        self.state["tasks"] = []
        snapshot = {
            "task_id": "REG-100",
            "archived_at": "2026-04-14T02:00:00Z",
            "terminal_outcome": "completed",
            "task": {
                "id": "REG-100",
                "status": "done",
                "title": "Archived completion candidate",
            },
        }
        with (
            mock.patch.object(ai_status, "archived_task_snapshot", return_value=snapshot),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            ai_status.command_show(self.state, ["REG-100"])

        rendered = stdout.getvalue()
        self.assertIn('"source": "archive"', rendered)
        self.assertIn('"task_id": "REG-100"', rendered)
        self.assertIn("ai-task-archive/tasks", rendered)


class SidecarTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = {
            "agents": [
                {"name": "Codex", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Claude", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Gemini", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Copilot", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Qwen", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
            ],
            "tasks": [],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }

    def test_assign_supports_sidecar_metadata_from_env(self) -> None:
        env = {
            "AI_NAME": "Codex",
            "TASK_PHASE": "Phase 5: Persona and Application Surfaces",
            "TASK_TITLE": "Prepare APP-001 BFF handoff packet",
            "TASK_SUMMARY_ZH": "平行支援 APP-001，整理 BFF handoff materials。",
            "TASK_DEPENDS_ON": "PER-001",
            "TASK_ARTIFACTS": "support/sidecars/APP-001/APP-001-SIDECAR-BFF-HANDOFF.md",
            "TASK_CLASS": "sidecar",
            "TASK_HELPER_PARENT": "APP-001",
            "TASK_HELPER_KIND": "bff_handoff_packet",
            "TASK_AUTO_GENERATED": "true",
            "TASK_MUTATES_CANONICAL": "false",
            "TASK_AUTO_CREATED_BY": "supervisor-underutilization",
            "TASK_PRIORITY": "P1",
        }
        with (
            mock.patch.dict(os.environ, env, clear=False),
            mock.patch.object(
                ai_status,
                "configured_agent_names",
                return_value={"Codex", "Claude2", "Antigravity3"},
            ),
        ):
            # Owner/reviewer must be configured workers: Gemini and Copilot are
            # KNOWN_AGENTS leftovers the Supervisor cannot dispatch.
            ai_status.command_assign(self.state, ["APP-001-SIDECAR-BFF-HANDOFF", "Claude2", "Antigravity3"])

        task = ai_status.get_task(self.state, "APP-001-SIDECAR-BFF-HANDOFF")
        self.assertIsNotNone(task)
        self.assertEqual(task["task_class"], "sidecar")
        self.assertTrue(task["auto_generated"])
        self.assertEqual(task["helper_parent"], "APP-001")
        self.assertEqual(task["helper_kind"], "bff_handoff_packet")
        self.assertFalse(task["mutates_canonical"])
        self.assertEqual(task["auto_created_by"], "supervisor-underutilization")
        self.assertEqual(task["priority"], "P1")
        self.assertEqual(task["depends_on"], ["PER-001"])

    def test_display_task_title_marks_sidecar_parent(self) -> None:
        title = ai_status.display_task_title(
            {
                "title": "Prepare APP-001 BFF handoff packet",
                "task_class": "sidecar",
                "auto_generated": True,
                "helper_parent": "APP-001",
            }
        )

        self.assertEqual(title, "[Sidecar] [Auto] [Parent APP-001] Prepare APP-001 BFF handoff packet")


class HumanOpsAgentTests(unittest.TestCase):
    def test_human_gate_can_belong_to_human_ops_without_blocking_worker(self) -> None:
        state = {
            "agents": [
                {"name": "Claude", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Codex", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
            ],
            "tasks": [
                {
                    "id": "PROD-WRITES-001-V2",
                    "title": "Enable production real writes",
                    "phase": "Phase 8 / EPIC-LIVE-GATE",
                    "owner": "human/ops",
                    "reviewer": "Codex",
                    "status": "blocked",
                    "waiting_for": "ops",
                    "depends_on": [],
                    "artifacts": [],
                    "acceptance": ["Human risk-owner + operator signoff"],
                    "next": "Awaiting risk-owner and operator signoff",
                    "last_update": "2026-06-01T00:00:00Z",
                    "task_class": "human_gate",
                    "non_dispatchable": True,
                    "allowed_workers": [],
                }
            ],
            "handoffs": [],
            "blockers": [
                {
                    "task_id": "PROD-WRITES-001-V2",
                    "owner": "human/ops",
                    "waiting_for": "ops",
                    "message": "Awaiting risk-owner and operator signoff",
                    "status": "open",
                    "created_at": "2026-06-01T00:00:00Z",
                }
            ],
            "workload": {},
            "workload_summary": {},
        }

        ai_status.validate_state(state)
        ai_status.recompute_agents(state)
        ai_status.recompute_workload(state)

        task = ai_status.get_task(state, "PROD-WRITES-001-V2")
        self.assertEqual(task["owner"], "Human/Ops")
        self.assertEqual(task["waiting_for"], "Human/Ops")
        self.assertEqual(state["blockers"][0]["owner"], "Human/Ops")
        self.assertEqual(state["blockers"][0]["waiting_for"], "Human/Ops")

        human_ops = ai_status.get_agent(state, "Human/Ops")
        self.assertEqual(human_ops["status"], "blocked")
        self.assertEqual(human_ops["current_task_ids"], ["PROD-WRITES-001-V2"])
        self.assertEqual(ai_status.get_agent(state, "Claude")["status"], "idle")
        self.assertEqual(state["workload"]["Human/Ops"], 0)
        self.assertEqual(state["workload_summary"]["Human/Ops"]["blocked"], 1)


class RuntimeWorkerLivenessTests(unittest.TestCase):
    def test_pid_is_alive_rejects_zombie_processes(self) -> None:
        with mock.patch.object(ai_status, "proc_pid_state", return_value="Z"):
            self.assertFalse(ai_status.pid_is_alive(1234))

    def test_normalize_runtime_workers_marks_zombie_running_worker_stale(self) -> None:
        state = {
            "tasks": [
                {
                    "id": "TASK-001",
                    "title": "Review stale runtime",
                    "summary_zh": "確認 zombie worker 不會被 dashboard 當成 live。",
                    "owner": "Codex",
                    "reviewer": "Gemini2",
                    "status": "review_approved",
                    "depends_on": [],
                    "next": "Owner finalize",
                    "last_update": "2026-05-17T11:00:00Z",
                }
            ]
        }
        orchestrator_state = {
            "workers": {
                "gemini2-run": {
                    "task_id": "TASK-001",
                    "provider": "gemini2",
                    "logical_agent_id": "gemini2",
                    "status": "running",
                    "pid": 1234,
                    "last_event_at": "2026-05-17T11:03:15Z",
                    "request_snapshot": {"reason": "review_ready_dispatch"},
                }
            }
        }

        with mock.patch.object(ai_status, "proc_pid_state", return_value="Z"):
            workers = ai_status.normalize_runtime_workers(state, orchestrator_state)

        self.assertEqual(workers[0]["bucket"], "stale")
        self.assertFalse(workers[0]["is_live_runtime"])
        self.assertFalse(workers[0]["pid_alive"])
        self.assertEqual(workers[0]["pid_state"], "Z")


class PortableStateRenderingTests(unittest.TestCase):
    def test_default_canonical_document_layers_exclude_review_and_session_records(self) -> None:
        layers = ai_status.default_canonical_document_layers()
        flattened = ai_status.flatten_canonical_document_layers(layers)

        self.assertIn("DOCUMENT_AUTHORITY_AND_RECORD_BOUNDARY.md", flattened)
        self.assertIn("WORKBENCH_DELIVERY_BACKLOG.md", flattened)
        self.assertIn("DELIVERY_CLOSURE_AND_LOOP_STATES.md", flattened)
        self.assertIn("EXECUTION_PROOF_AND_MATURITY_LEVELS.md", flattened)
        self.assertNotIn("docs/reviews/2026-04-17-next-wave-implementation-plan.md", flattened)
        self.assertNotIn(
            "docs/02-architecture/consensus/sessions/phase6-2026-04-16-oss-ecosystem-closure/README.md",
            flattened,
        )

    def test_sync_canonical_document_metadata_migrates_current_work_to_derived_layer(self) -> None:
        state = {
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                    "current-work.md",
                ],
                "L1 Runtime & Dashboard": [
                    "docs-site/index.html",
                ],
            }
        }

        ai_status.sync_canonical_document_metadata(state)

        self.assertEqual(
            state["canonical_document_layers"]["L0 Collaboration & State"],
            [
                "AI_COLLABORATION_GUIDE.md",
                "ai-status.json",
                "ai-activity-log.jsonl",
            ],
        )
        self.assertEqual(
            state["canonical_document_layers"]["L0.5 Derived Narrative"],
            ["current-work.md"],
        )

    def test_sync_canonical_document_metadata_backfills_new_default_l2_documents(self) -> None:
        state = {
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                ],
                "L0.5 Derived Narrative": [
                    "current-work.md",
                ],
                "L2 Planning & Execution": [
                    "CANONICAL_DOCUMENT_MAP.md",
                    "DOCUMENT_AUTHORITY_AND_RECORD_BOUNDARY.md",
                    "ROADMAP.md",
                    "DEVELOPMENT_WORKBREAKDOWN.md",
                    "OSS_INTEGRATION_CHECKLIST.md",
                ],
            }
        }

        ai_status.sync_canonical_document_metadata(state)

        self.assertIn(
            "WORKBENCH_DELIVERY_BACKLOG.md",
            state["canonical_document_layers"]["L2 Planning & Execution"],
        )
        self.assertIn(
            "DELIVERY_CLOSURE_AND_LOOP_STATES.md",
            state["canonical_document_layers"]["L2 Planning & Execution"],
        )
        self.assertIn(
            "EXECUTION_PROOF_AND_MATURITY_LEVELS.md",
            state["canonical_document_layers"]["L2 Planning & Execution"],
        )

    def test_build_onboarding_prompt_follows_state_canonical_files(self) -> None:
        state = {
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                ],
                "L0.5 Derived Narrative": [
                    "current-work.md",
                ],
                "L1 Runtime & Dashboard": [
                    "docs-site/index.html",
                ],
            }
        }

        prompt = ai_status.build_onboarding_prompt(state)

        self.assertIn("Read AI_COLLABORATION_GUIDE.md, ai-status.json", prompt)
        self.assertIn("Use current-work.md as a human summary only", prompt)
        self.assertIn("Use ai-activity-log.jsonl only when you need targeted recent history.", prompt)
        self.assertIn("TARGET_ARCHITECTURE.md", prompt)

    def test_write_current_work_uses_generic_delivery_sections(self) -> None:
        state = {
            "updated_at": "2026-04-10T00:00:00Z",
            "objective": "Stand up a portable delivery system.",
            "sprint": "2026-04-10-bootstrap",
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                ],
                "L0.5 Derived Narrative": [
                    "current-work.md",
                ],
                "L1 Runtime & Dashboard": [
                    "docs-site/index.html",
                ],
            },
            "agents": [
                {"name": "Codex", "capability_lane": ["integration"], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Claude", "capability_lane": ["review"], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
            ],
            "tasks": [
                {
                    "id": "DEMO-001",
                    "title": "First migrated task",
                    "summary_zh": "建立第一個遷移任務。",
                    "phase": "Foundation",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "in_progress",
                    "depends_on": [],
                    "next": "Implement foundation task",
                    "last_update": "2026-04-10T00:00:00Z",
                },
                {
                    "id": "OSS-001",
                    "title": "Verify external integration",
                    "summary_zh": "驗證外部整合。",
                    "phase": "Integration",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "status": "todo",
                    "depends_on": [],
                    "next": "Queue external validation",
                    "last_update": "2026-04-10T00:00:00Z",
                },
                {
                    "id": "P2-OSS-ACTIVATE-001",
                    "title": "Research OSS production activation after fail-closed gates",
                    "summary_zh": "確認外部資料串接 activation gate。",
                    "phase": "P2 Wave 7",
                    "owner": "Codex",
                    "reviewer": "Copilot",
                    "status": "todo",
                    "depends_on": ["P0-CI-BOUNDED-001"],
                    "artifacts": ["services/source_ingestion", "services/search"],
                    "next": "Assignment created",
                    "last_update": "2026-04-10T00:00:00Z",
                },
            ],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }

        with tempfile.TemporaryDirectory(prefix="ai-status-current-work-") as temp_dir:
            output_path = Path(temp_dir) / "current-work.md"
            with (
                mock.patch.object(ai_status, "CURRENT_WORK_FILE", output_path),
                mock.patch.object(
                    ai_status,
                    "load_archive_index",
                    return_value={
                        "updated_at": "2026-04-10T01:00:00Z",
                        "counts": {"total": 1, "completed": 1, "superseded": 0},
                        "recent_terminal_ids": ["DONE-001"],
                    },
                ),
                mock.patch.object(
                    ai_status,
                    "recent_terminal_summaries",
                    return_value=[
                        {
                            "task_id": "DONE-001",
                            "title": "Executed task",
                            "phase": "Archive",
                            "owner": "Codex",
                            "terminal_outcome": "completed",
                            "archived_at": "2026-04-10T01:00:00Z",
                            "snapshot_path": "ai-task-archive/tasks/DONE-001.json",
                        }
                    ],
                ),
            ):
                ai_status.write_current_work(state, [])

            content = output_path.read_text(encoding="utf-8")

        self.assertIn("### Primary Project Work", content)
        self.assertIn("### External / Upstream Integration Work", content)
        self.assertIn("`P2-OSS-ACTIVATE-001`", content)
        self.assertIn("## Recently Executed Tasks", content)
        self.assertIn("`DONE-001`", content)
        self.assertIn("`ai-task-archive/tasks/DONE-001.json`", content)
        self.assertNotIn("### Pantheon Product Work", content)
        self.assertIn("Canonical map", content)
        self.assertIn("Workbench backlog", content)
        self.assertIn("Loop closure", content)
        self.assertIn("Execution proof", content)
        self.assertIn("- Canonical tiers: `L0 Collaboration & State`, `L0.5 Derived Narrative`, `L1 Runtime & Dashboard`", content)

    def test_write_current_work_formats_absolute_times_in_taiwan_time(self) -> None:
        state = {
            "updated_at": "2026-04-10T00:00:00Z",
            "objective": "Track the queue and resume work before 2026-04-10T01:30:00Z.",
            "sprint": "2026-04-10-bootstrap",
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                ],
                "L0.5 Derived Narrative": [
                    "current-work.md",
                ],
            },
            "agents": [
                {"name": "Codex", "capability_lane": ["integration"], "status": "idle", "current_task_ids": [], "branch": "", "next": "Resume at 2026-04-10T01:45:00Z.", "last_update": None},
            ],
            "tasks": [
                {
                    "id": "DEMO-002",
                    "title": "Timezone rendering",
                    "summary_zh": "確認人類可讀時間會轉成台灣時間。",
                    "phase": "Foundation",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "review",
                    "depends_on": [],
                    "next": "Waiting until 2026-04-10T02:15:00Z.",
                    "last_update": "2026-04-10T02:00:00Z",
                    "review_notes_zh": ["Reviewer checked the handoff at 2026-04-10T02:30:00Z."],
                    "review_file": "reviews/demo-002.md",
                },
            ],
            "handoffs": [
                {
                    "task_id": "DEMO-002",
                    "from": "Codex",
                    "to": "Claude",
                    "message": "Please review before 2026-04-10T02:20:00Z.",
                    "status": "pending",
                    "created_at": "2026-04-10T02:05:00Z",
                }
            ],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }
        logs = [
            {
                "ts": "2026-04-10T02:10:00Z",
                "agent": "Codex",
                "task_id": "DEMO-002",
                "message": "Paused until 2026-04-10T02:40:00Z.",
            },
            {
                "ts": "2026-04-10T02:11:00Z",
                "agent": "Orchestrator",
                "type": "worker_started",
                "task_id": "DEMO-002",
            },
        ]

        with tempfile.TemporaryDirectory(prefix="ai-status-current-work-taipei-") as temp_dir:
            output_path = Path(temp_dir) / "current-work.md"
            with mock.patch.object(ai_status, "CURRENT_WORK_FILE", output_path):
                ai_status.write_current_work(state, logs)

            content = output_path.read_text(encoding="utf-8")

        self.assertIn("Absolute times below use 台灣時間 (UTC+8).", content)
        self.assertIn("Last updated: 2026-04-10 08:00:00", content)
        self.assertIn("Track the queue and resume work before 2026-04-10 09:30:00.", content)
        self.assertIn("Resume at 2026-04-10 09:45:00.", content)
        self.assertIn("| `DEMO-002` | Foundation | Timezone rendering |", content)
        self.assertIn("| review | - | 2026-04-10 10:00:00 | Waiting until 2026-04-10 10:15:00. |", content)
        self.assertIn("| `DEMO-002` | Codex | Claude | Please review before 2026-04-10 10:20:00. | pending | 2026-04-10 10:05:00 |", content)
        self.assertIn("Reviewer checked the handoff at 2026-04-10 10:30:00.", content)
        self.assertIn("- 2026-04-10 10:10:00 Codex: `DEMO-002` Paused until 2026-04-10 10:40:00.", content)
        self.assertIn("- 2026-04-10 10:11:00 Orchestrator: `DEMO-002` worker_started", content)

    def test_write_current_work_tolerates_structured_log_entries_without_message(self) -> None:
        state = {
            "updated_at": "2026-05-17T16:24:00Z",
            "objective": "Keep generated status views robust.",
            "sprint": "2026-05-17-status-sync",
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                ],
            },
            "agents": [],
            "tasks": [],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }
        logs = [
            {
                "ts": "2026-05-17T16:24:21Z",
                "agent": "Codex2",
                "type": "worker_commit",
                "task_id": "OODA-E2E-002",
                "commit": "abcdef1234567890",
                "scope": ["tests/e2e/test_demo.py"],
            }
        ]

        with tempfile.TemporaryDirectory(prefix="ai-status-current-work-structured-log-") as temp_dir:
            output_path = Path(temp_dir) / "current-work.md"
            with (
                mock.patch.object(ai_status, "CURRENT_WORK_FILE", output_path),
                mock.patch.object(
                    ai_status,
                    "load_archive_index",
                    return_value={
                        "updated_at": None,
                        "counts": {"total": 0, "completed": 0, "superseded": 0},
                        "recent_terminal_ids": [],
                    },
                ),
                mock.patch.object(ai_status, "recent_terminal_summaries", return_value=[]),
            ):
                ai_status.write_current_work(state, logs)

            content = output_path.read_text(encoding="utf-8")

        self.assertIn(
            "- 2026-05-18 00:24:21 Codex2: `OODA-E2E-002` "
            "worker_commit: commit abcdef123456; scope `tests/e2e/test_demo.py`",
            content,
        )

    def test_build_onboarding_prompt_mentions_active_planning(self) -> None:
        state = {
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                ],
                "L0.5 Derived Narrative": [
                    "current-work.md",
                ],
                "L2 Planning & Execution": [
                    "docs/02-architecture/consensus/phase1/README.md",
                    "docs/02-architecture/consensus/phase1/planning-session.json",
                ],
            }
        }

        with mock.patch.object(ai_status, "load_planning_state", return_value={"status": "active"}):
            prompt = ai_status.build_onboarding_prompt(state)

        self.assertIn("Discussion planning is active", prompt)
        self.assertIn("docs/02-architecture/consensus/phase1/README.md", prompt)
        self.assertIn("docs/02-architecture/consensus/phase1/planning-session.json", prompt)

    def test_write_current_work_includes_planning_snapshot(self) -> None:
        state = {
            "updated_at": "2026-04-11T00:00:00Z",
            "objective": "Stand up a planning-aware control plane.",
            "sprint": "2026-04-11-planning-mode",
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                ],
                "L0.5 Derived Narrative": [
                    "current-work.md",
                ],
                "L2 Planning & Execution": [
                    "docs/02-architecture/consensus/phase1/README.md",
                    "docs/02-architecture/consensus/phase1/planning-session.json",
                ],
            },
            "agents": [
                {"name": "Codex", "capability_lane": ["integration"], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
            ],
            "tasks": [],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }

        planning_state = {
            "session_id": "phase1-2026-04-11",
            "status": "active",
            "baton_owner": "Codex",
            "current_round": 1,
            "consensus_status": "draft",
            "human_gate_status": "pending",
            "switch_gate": {
                "ready_for_human": False,
                "ready_to_materialize": False,
            },
        }

        with tempfile.TemporaryDirectory(prefix="ai-status-planning-current-work-") as temp_dir:
            output_path = Path(temp_dir) / "current-work.md"
            with mock.patch.object(ai_status, "CURRENT_WORK_FILE", output_path):
                with mock.patch.object(ai_status, "load_planning_state", return_value=planning_state):
                    ai_status.write_current_work(state, [])

            content = output_path.read_text(encoding="utf-8")

        self.assertIn("## Discussion Planning", content)
        self.assertIn("phase1-2026-04-11", content)
        self.assertIn("`active`", content)

    def test_write_current_work_keeps_active_planning_session_out_of_canonical_files(self) -> None:
        state = {
            "updated_at": "2026-04-11T00:00:00Z",
            "objective": "Keep planning records separate from blueprint truth.",
            "sprint": "2026-04-11-planning-boundary",
            "canonical_document_layers": ai_status.default_canonical_document_layers(),
            "agents": [],
            "tasks": [],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }
        planning_state = {
            "session_id": "phase6-2026-04-16-oss-ecosystem-closure",
            "status": "accepted",
            "artifacts": {
                "planning_readme": {
                    "path": "docs/02-architecture/consensus/sessions/phase6-2026-04-16-oss-ecosystem-closure/README.md"
                },
                "planning_session": {
                    "path": "docs/02-architecture/consensus/sessions/phase6-2026-04-16-oss-ecosystem-closure/planning-session.json"
                },
            },
            "session_file": "docs/02-architecture/consensus/sessions/phase6-2026-04-16-oss-ecosystem-closure/planning-session.json",
        }

        with tempfile.TemporaryDirectory(prefix="ai-status-planning-boundary-") as temp_dir:
            output_path = Path(temp_dir) / "current-work.md"
            with mock.patch.object(ai_status, "CURRENT_WORK_FILE", output_path):
                with mock.patch.object(ai_status, "load_planning_state", return_value=planning_state):
                    ai_status.write_current_work(state, [])

            content = output_path.read_text(encoding="utf-8")

        self.assertIn(
            "- Planning mode: `docs/02-architecture/consensus/sessions/phase6-2026-04-16-oss-ecosystem-closure/README.md`",
            content,
        )
        self.assertNotIn(
            "`docs/02-architecture/consensus/sessions/phase6-2026-04-16-oss-ecosystem-closure/README.md`,",
            content.split("- Canonical files: ", 1)[1].split("\n", 1)[0],
        )

    def test_write_current_work_includes_lovable_coordination_snapshot(self) -> None:
        state = {
            "updated_at": "2026-04-11T00:00:00Z",
            "objective": "Track cross-repo Lovable delivery.",
            "sprint": "2026-04-11-lovable-loop",
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                ],
                "L0.5 Derived Narrative": [
                    "current-work.md",
                ],
            },
            "agents": [
                {"name": "Codex", "capability_lane": ["integration"], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
            ],
            "tasks": [],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }

        orchestrator_state = {
            "coordination": {
                "last_scan_at": "2026-04-11T02:30:00Z",
                "features": {
                    "F-042": {
                        "feature_id": "F-042",
                        "screen": "promotion-review",
                        "current_payload_type": "frontend-feedback",
                        "source_repo": "ajoe734/front-ai-trading-system",
                        "source_repo_id": "front_ai_trading_system",
                        "target_repo_id": "pantheon",
                        "lovable_task_path": ".coordination/responses/F-042-lovable-ui-task.yaml",
                        "lovable_prompt_path": ".coordination/responses/F-042-lovable-prompt.md",
                        "mirrored_to_target_repo": {"target_repo_id": "front_ai_trading_system"},
                        "requests_by_type": {
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": "../front-ai-trading-system/.coordination/requests/F-042-frontend-feedback.yaml",
                                "payload": {"type": "frontend-feedback", "summary": "Feedback bundle ready"},
                                "updated_at": "2026-04-11T02:30:00Z",
                            }
                        },
                        "responses_by_type": {
                            "lovable-ui-task": {
                                "type": "lovable-ui-task",
                                "path": ".coordination/responses/F-042-lovable-ui-task.yaml",
                                "payload": {"type": "lovable-ui-task", "status": "ready"},
                                "updated_at": "2026-04-11T02:00:00Z",
                            }
                        },
                    }
                },
            }
        }

        with tempfile.TemporaryDirectory(prefix="ai-status-lovable-current-work-") as temp_dir:
            output_path = Path(temp_dir) / "current-work.md"
            with mock.patch.object(ai_status, "CURRENT_WORK_FILE", output_path):
                with (
                    mock.patch.object(ai_status, "load_json_file", return_value=orchestrator_state),
                    mock.patch.object(ai_status, "coordination_local_response_path", return_value=None),
                    mock.patch.object(ai_status, "coordination_review_snapshot", return_value=None),
                ):
                    ai_status.write_current_work(state, [])

            content = output_path.read_text(encoding="utf-8")

        self.assertIn("## Lovable Coordination", content)
        self.assertIn("Lovable-ready packets: `1`", content)
        self.assertIn("Frontend feedback returned: `1`", content)
        self.assertIn("| `F-042` | promotion-review | `frontend_feedback_received` | yes | yes | no | yes |", content)

    def test_build_dashboard_bundle_summarizes_truth_layers(self) -> None:
        state = {
            "updated_at": "2026-04-11T13:00:00Z",
            "agents": [],
            "tasks": [
                {
                    "id": "APP-002-W1-FRONT-HANDOFF",
                    "title": "Publish front-end handoff packet",
                    "summary_zh": "整理交接封包。",
                    "phase": "Planning Materialized",
                    "owner": "Copilot",
                    "reviewer": "Codex",
                    "status": "todo",
                    "depends_on": [],
                    "next": "Assignment created",
                    "last_update": "2026-04-11T13:00:00Z",
                },
                {
                    "id": "APP-002-W2-READ-INCIDENT",
                    "title": "Incident response read surfaces",
                    "summary_zh": "補 incident read view。",
                    "phase": "Planning Materialized",
                    "owner": "Qwen",
                    "reviewer": "Codex",
                    "status": "review",
                    "depends_on": [],
                    "next": "Reviewer validating read model",
                    "last_update": "2026-04-11T13:00:00Z",
                },
            ],
        }
        planning_state = {
            "status": "accepted",
            "session_id": "phase2-2026-04-13-blueprint-gap",
            "planning_dir": "docs/02-architecture/consensus/phase2",
            "session_file": "docs/02-architecture/consensus/phase2/planning-session.json",
            "runtime_mode": "supervisor_managed_execution",
            "consensus_status": "accepted",
            "human_gate_status": "approved",
            "counts": {"readouts_resolved": 5, "open_items": 0},
            "artifacts": {
                "consensus_packet": {"path": "docs/02-architecture/consensus/phase2/consensus-packet.md"},
                "execution_materialization": {"path": "docs/02-architecture/consensus/phase2/execution-materialization.md"},
            },
            "materialization_contract": {
                "source_plane": "planning",
                "session_id": "phase2-2026-04-13-blueprint-gap",
                "phase": "phase2",
                "planning_dir": "docs/02-architecture/consensus/phase2",
                "session_file": "docs/02-architecture/consensus/phase2/planning-session.json",
                "consensus_packet": "docs/02-architecture/consensus/phase2/consensus-packet.md",
                "execution_materialization": "docs/02-architecture/consensus/phase2/execution-materialization.md",
            },
            "proposed_execution_tasks": [
                {
                    "id": "APP-002-W1-FRONT-HANDOFF",
                    "source_plane": "planning",
                    "source_ref": {"session_id": "phase2-2026-04-13-blueprint-gap"},
                },
                {
                    "id": "APP-002-W2-READ-INCIDENT",
                    "source_plane": "planning",
                    "source_ref": {"session_id": "phase2-2026-04-13-blueprint-gap"},
                },
                {"id": "APP-002-W5-SSE-LIVE"},
            ],
        }
        orchestrator_state = {
            "supervisor": {"pid": 294672, "last_heartbeat_at": "2026-04-11T13:08:22Z"},
            "queue": {"events": {}},
            "workers": {
                "copilot-run-1": {
                    "task_id": "APP-002-W1-FRONT-HANDOFF",
                    "queue_event_id": "evt-1",
                    "agent_id": "copilot",
                    "provider": "copilot",
                    "status": "running",
                    "last_event_at": "2026-04-11T13:08:21Z",
                    "request_snapshot": {"reason": "owned_ready_dispatch"},
                }
            },
        }
        approval_state = {"pending": [], "history": []}

        resolver = mock.Mock()
        resolver.active_task_map.return_value = {
            "APP-002-W1-FRONT-HANDOFF": state["tasks"][0],
            "APP-002-W2-READ-INCIDENT": state["tasks"][1],
        }
        resolver.dependency_status.side_effect = lambda task_id: "missing"
        resolver.dependency_satisfied.side_effect = lambda task_id: False
        resolver.get.side_effect = lambda task_id: {
            "APP-002-W1-FRONT-HANDOFF": state["tasks"][0],
            "APP-002-W2-READ-INCIDENT": state["tasks"][1],
        }.get(task_id)
        resolver.source.side_effect = lambda task_id: "active" if task_id in resolver.active_task_map.return_value else None

        with (
            mock.patch.object(ai_status, "task_resolver", return_value=resolver),
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        self.assertEqual(bundle["focus_mode"], "execution")
        self.assertEqual(bundle["runtime_summary"]["running_workers"], 1)
        self.assertEqual(bundle["runtime_summary"]["dispatch_targets"]["Codex"], 5)
        self.assertEqual(bundle["runtime_summary"]["dispatch_targets"]["Gemini"], 5)
        self.assertEqual(bundle["execution_summary"]["ready_now"], 0)
        self.assertEqual(bundle["execution_summary"]["dependency_ready"], 1)
        self.assertEqual(bundle["execution_summary"]["in_review"], 1)
        self.assertEqual(bundle["planning_summary"]["materialized_count"], 2)
        self.assertEqual(bundle["bridge_summary"]["source_plane"], "planning")
        self.assertEqual(bundle["bridge_summary"]["session_id"], "phase2-2026-04-13-blueprint-gap")
        self.assertEqual(bundle["bridge_summary"]["materialized_count"], 2)
        self.assertEqual(bundle["bridge_summary"]["pending_materialization_count"], 1)
        self.assertEqual(bundle["bridge_summary"]["consensus_packet"], "docs/02-architecture/consensus/phase2/consensus-packet.md")
        self.assertEqual(len(bundle["truth_mismatches"]), 1)
        self.assertEqual({item["type"] for item in bundle["truth_mismatches"]}, {"running_worker_on_todo"})
        mismatch_hints = {item["type"]: item["resolution_hint"] for item in bundle["truth_mismatches"]}
        self.assertIn("先把 task 狀態推成 in_progress", mismatch_hints["running_worker_on_todo"])
        self.assertEqual(bundle["worker_task_links"][0]["task_id"], "APP-002-W1-FRONT-HANDOFF")
        self.assertEqual(bundle["worker_task_links"][0]["task_title"], "Publish front-end handoff packet")
        self.assertEqual(bundle["worker_task_links"][0]["task_summary"], "整理交接封包。")
        self.assertEqual(bundle["worker_task_links"][0]["queue_status"], None)
        self.assertEqual(bundle["worker_task_links"][0]["mismatch_count"], 1)
        self.assertIn("running_worker_on_todo", bundle["worker_task_links"][0]["mismatch_flags"])
        self.assertTrue(bundle["worker_task_links"][0]["resolution_hints"])

    def test_related_live_sidecar_worker_does_not_flag_parent_as_without_worker(self) -> None:
        state = {
            "updated_at": "2026-04-15T15:32:45Z",
            "agents": [
                {"name": "Codex", "status": "busy", "current_task_ids": ["BP5-SVC-001"], "branch": "", "next": "", "last_update": "2026-04-15T15:32:45Z"},
                {"name": "Claude", "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": "2026-04-15T15:32:45Z"},
                {"name": "Gemini", "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": "2026-04-15T15:32:45Z"},
            ],
            "tasks": [
                {
                    "id": "BP5-SVC-001",
                    "title": "Lock the deployable service baseline and single-VM topology",
                    "summary_zh": "主線 baseline 定義。",
                    "owner": "Codex",
                    "reviewer": "Gemini",
                    "status": "in_progress",
                    "depends_on": [],
                    "artifacts": [],
                    "acceptance": [],
                    "next": "Supervisor auto-started BP5-SVC-001 after successful dispatch.",
                    "last_update": "2026-04-15T15:29:37Z",
                },
                {
                    "id": "BP5-SVC-001-SIDECAR-ACCEPTANCE",
                    "title": "Prepare BP5-SVC-001 acceptance packet and dependency map",
                    "summary_zh": "Sidecar review 中。",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "status": "review",
                    "depends_on": [],
                    "artifacts": ["support/sidecars/BP5-SVC-001/BP5-SVC-001-SIDECAR-ACCEPTANCE.md"],
                    "acceptance": [],
                    "next": "Acceptance packet handed off to Codex for review.",
                    "last_update": "2026-04-15T15:32:28Z",
                    "task_class": "sidecar",
                    "auto_generated": True,
                    "helper_parent": "BP5-SVC-001",
                    "helper_kind": "acceptance_packet",
                },
            ],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }
        planning_state = {"mode": "execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 123, "last_heartbeat_at": "2026-04-15T15:32:45Z"},
            "queue": {"events": {"evt-1": {"status": "started", "run_id": "codex-20260415T153233Z-97359030", "processed_at": "2026-04-15T15:32:33Z"}}},
            "workers": {
                "codex-20260415T153233Z-97359030": {
                    "run_id": "codex-20260415T153233Z-97359030",
                    "task_id": "BP5-SVC-001-SIDECAR-ACCEPTANCE",
                    "queue_event_id": "evt-1",
                    "agent_id": "codex",
                    "provider": "codex",
                    "status": "running",
                    "last_event_at": "2026-04-15T15:32:47Z",
                    "request_snapshot": {"reason": "review_ready_dispatch"},
                }
            },
        }
        approval_state = {"pending": [], "history": []}

        resolver = mock.Mock()
        resolver.active_task_map.return_value = {task["id"]: task for task in state["tasks"]}
        resolver.dependency_status.side_effect = lambda task_id: "done"
        resolver.dependency_satisfied.side_effect = lambda task_id: True
        resolver.get.side_effect = lambda task_id: {task["id"]: task for task in state["tasks"]}.get(task_id)
        resolver.source.side_effect = lambda task_id: "active" if task_id in resolver.active_task_map.return_value else None

        with (
            mock.patch.object(ai_status, "task_resolver", return_value=resolver),
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        mismatch_ids = {item["id"] for item in bundle["truth_mismatches"]}
        self.assertNotIn("active-task-without-worker:BP5-SVC-001", mismatch_ids)

    def test_paused_reviewer_does_not_flag_review_task_without_worker(self) -> None:
        state = {
            "updated_at": "2026-04-15T16:35:29Z",
            "agents": [
                {"name": "Claude", "status": "working", "current_task_ids": ["BP5-SVC-002"], "branch": "", "next": "", "last_update": "2026-04-15T16:35:29Z"},
                {"name": "Qwen", "status": "blocked", "current_task_ids": ["BP5-LUV-001"], "branch": "", "next": "", "last_update": "2026-04-15T16:35:29Z"},
            ],
            "tasks": [
                {
                    "id": "BP5-SVC-002",
                    "title": "Registry review",
                    "summary_zh": "等待 reviewer 檢查。",
                    "owner": "Claude",
                    "reviewer": "Qwen",
                    "status": "review",
                    "depends_on": [],
                    "artifacts": [],
                    "acceptance": [],
                    "next": "Ready for Qwen review.",
                    "last_update": "2026-04-15T16:35:29Z",
                }
            ],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }
        planning_state = {"mode": "execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 123, "last_heartbeat_at": "2026-04-15T16:35:46Z"},
            "provider_guardrails": {
                "dispatch_pauses": {
                    "qwen": {
                        "provider": "qwen",
                        "blocked_until": "2099-04-15T16:38:40Z",
                        "summary": "Capacity / rate limit failure",
                    }
                }
            },
            "queue": {"events": {}},
            "workers": {},
        }
        approval_state = {"pending": [], "history": []}

        resolver = mock.Mock()
        resolver.active_task_map.return_value = {task["id"]: task for task in state["tasks"]}
        resolver.dependency_status.side_effect = lambda task_id: "review" if task_id == "BP5-SVC-002" else "missing"
        resolver.dependency_satisfied.side_effect = lambda task_id: True
        resolver.get.side_effect = lambda task_id: {task["id"]: task for task in state["tasks"]}.get(task_id)
        resolver.source.side_effect = lambda task_id: "active" if task_id in resolver.active_task_map.return_value else None

        with (
            mock.patch.object(ai_status, "task_resolver", return_value=resolver),
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        mismatch_ids = {item["id"] for item in bundle["truth_mismatches"]}
        self.assertNotIn("active-task-without-worker:BP5-SVC-002", mismatch_ids)

    def test_review_task_without_live_worker_does_not_flag_truth_mismatch(self) -> None:
        state = {
            "updated_at": "2026-04-16T06:50:44Z",
            "agents": [
                {"name": "Claude", "status": "reviewing", "current_task_ids": ["BP5-LUV-007"], "branch": "", "next": "", "last_update": "2026-04-16T06:50:44Z"},
                {"name": "Codex", "status": "working", "current_task_ids": [], "branch": "", "next": "", "last_update": "2026-04-16T06:50:44Z"},
            ],
            "tasks": [
                {
                    "id": "BP5-LUV-007",
                    "title": "Lovable lineage review",
                    "summary_zh": "等待 reviewer 接手。",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "status": "review",
                    "depends_on": [],
                    "artifacts": [],
                    "acceptance": [],
                    "next": "Review notes already prepared; waiting in review queue.",
                    "last_update": "2026-04-16T06:50:44Z",
                }
            ],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }
        planning_state = {"mode": "execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 123, "last_heartbeat_at": "2026-04-16T06:53:55Z"},
            "queue": {"events": {}},
            "workers": {},
        }
        approval_state = {"pending": [], "history": []}

        resolver = mock.Mock()
        resolver.active_task_map.return_value = {task["id"]: task for task in state["tasks"]}
        resolver.dependency_status.side_effect = lambda task_id: "review" if task_id == "BP5-LUV-007" else "missing"
        resolver.dependency_satisfied.side_effect = lambda task_id: True
        resolver.get.side_effect = lambda task_id: {task["id"]: task for task in state["tasks"]}.get(task_id)
        resolver.source.side_effect = lambda task_id: "active" if task_id in resolver.active_task_map.return_value else None

        with (
            mock.patch.object(ai_status, "task_resolver", return_value=resolver),
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        mismatch_ids = {item["id"] for item in bundle["truth_mismatches"]}
        self.assertNotIn("active-task-without-worker:BP5-LUV-007", mismatch_ids)

    def test_coordination_worker_missing_taskboard_entry_does_not_flag_truth_mismatch(self) -> None:
        state = {
            "updated_at": "2026-04-16T06:53:55Z",
            "agents": [],
            "tasks": [],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }
        planning_state = {"mode": "execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 123, "last_heartbeat_at": "2026-04-16T06:53:55Z"},
            "queue": {"events": {}},
            "workers": {
                "codex-1": {
                    "run_id": "codex-1",
                    "task_id": "PKT-002-incident-action-drawer",
                    "queue_event_id": "coord-1",
                    "agent_id": "codex",
                    "provider": "codex",
                    "status": "running",
                    "last_event_at": "2026-04-16T06:53:55Z",
                    "request_snapshot": {
                        "reason": "coordination:bff-gap",
                        "metadata": {
                            "coordination": {
                                "feature_id": "PKT-002-incident-action-drawer",
                                "payload_type": "bff-gap",
                            }
                        },
                    },
                }
            },
        }
        approval_state = {"pending": [], "history": []}

        resolver = mock.Mock()
        resolver.active_task_map.return_value = {}
        resolver.dependency_status.side_effect = lambda task_id: "missing"
        resolver.dependency_satisfied.side_effect = lambda task_id: False
        resolver.get.side_effect = lambda task_id: None
        resolver.source.side_effect = lambda task_id: None

        with (
            mock.patch.object(ai_status, "task_resolver", return_value=resolver),
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        mismatch_ids = {item["id"] for item in bundle["truth_mismatches"]}
        self.assertNotIn("worker-task-missing:codex-1", mismatch_ids)

    def test_pending_approval_task_does_not_flag_without_live_worker(self) -> None:
        state = {
            "updated_at": "2026-04-15T16:41:31Z",
            "agents": [
                {"name": "Claude", "status": "working", "current_task_ids": ["BP5-SVC-003"], "branch": "", "next": "", "last_update": "2026-04-15T16:41:31Z"},
            ],
            "tasks": [
                {
                    "id": "BP5-SVC-003",
                    "title": "Governance API fixup",
                    "summary_zh": "等待 approval 後續跑。",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "status": "in_progress",
                    "depends_on": [],
                    "artifacts": [],
                    "acceptance": [],
                    "next": "Waiting for safe verification approval.",
                    "last_update": "2026-04-15T16:41:31Z",
                }
            ],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }
        planning_state = {"mode": "execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 123, "last_heartbeat_at": "2026-04-15T16:41:31Z"},
            "queue": {"events": {}},
            "workers": {},
        }
        approval_state = {
            "pending": [
                {
                    "approval_id": "apr-1",
                    "task_id": "BP5-SVC-003",
                    "worker_run_id": "claude-run-1",
                }
            ],
            "history": [],
        }

        resolver = mock.Mock()
        resolver.active_task_map.return_value = {task["id"]: task for task in state["tasks"]}
        resolver.dependency_status.side_effect = lambda task_id: "in_progress" if task_id == "BP5-SVC-003" else "missing"
        resolver.dependency_satisfied.side_effect = lambda task_id: True
        resolver.get.side_effect = lambda task_id: {task["id"]: task for task in state["tasks"]}.get(task_id)
        resolver.source.side_effect = lambda task_id: "active" if task_id in resolver.active_task_map.return_value else None

        with (
            mock.patch.object(ai_status, "task_resolver", return_value=resolver),
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        mismatch_ids = {item["id"] for item in bundle["truth_mismatches"]}
        self.assertNotIn("active-task-without-worker:BP5-SVC-003", mismatch_ids)

    def test_write_dashboard_bundle_persists_json_artifact(self) -> None:
        state = {
            "updated_at": "2026-04-11T13:00:00Z",
            "agents": [],
            "tasks": [
                {
                    "id": "APP-002-W1-FRONT-HANDOFF",
                    "title": "Publish front-end handoff packet",
                    "summary_zh": "整理交接封包。",
                    "phase": "Planning Materialized",
                    "owner": "Copilot",
                    "reviewer": "Codex",
                    "status": "in_progress",
                    "depends_on": [],
                    "next": "Working",
                    "last_update": "2026-04-11T13:00:00Z",
                },
            ],
        }
        config = {"paths": {"state_file": ".orchestrator/state.json", "event_queue": ".orchestrator/event-queue.jsonl"}}
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {"supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-11T13:08:22Z"}, "queue": {"events": {}}, "workers": {}}
        approval_state = {"pending": [], "history": []}

        with tempfile.TemporaryDirectory(prefix="ai-status-dashboard-bundle-") as temp_dir:
            output_path = Path(temp_dir) / "dashboard-bundle.json"
            with mock.patch.object(ai_status, "DASHBOARD_BUNDLE_FILE", output_path):
                with mock.patch.object(ai_status, "load_config", return_value=config):
                    with mock.patch.object(ai_status, "load_planning_state", return_value=planning_state):
                        with mock.patch.object(ai_status, "load_runtime_state", return_value=orchestrator_state) as load_runtime_state:
                            with mock.patch.object(ai_status, "load_json_file", return_value=approval_state) as load_json_file:
                                ai_status.write_dashboard_bundle(state)

            bundle = json.loads(output_path.read_text(encoding="utf-8"))

        load_runtime_state.assert_called_once_with(config)
        load_json_file.assert_called_once_with(ai_status.APPROVAL_QUEUE_FILE, {"pending": [], "history": []})
        self.assertEqual(bundle["runtime_summary"]["supervisor_pid"], 1)
        self.assertEqual(bundle["execution_summary"]["in_progress"], 1)
        self.assertEqual(bundle["focus_mode"], "execution")
        self.assertIn("worker_task_links", bundle)
        self.assertIn("truth_mismatches", bundle)

    def test_build_dashboard_bundle_reads_terminal_counts_from_archive_summary(self) -> None:
        state = {
            "updated_at": "2026-04-14T02:00:00Z",
            "agents": [],
            "tasks": [
                {
                    "id": "REG-101",
                    "title": "Still active",
                    "summary_zh": "等待已封存依賴。",
                    "phase": "Epic X",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "todo",
                    "depends_on": ["REG-100"],
                    "next": "Ready to start",
                    "last_update": "2026-04-14T02:00:00Z",
                },
            ],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-14T02:05:00Z"},
            "queue": {"events": {}},
            "workers": {},
            "recent_terminal_tasks": [
                {
                    "task_id": "REG-100",
                    "terminal_outcome": "completed",
                    "archived_at": "2026-04-14T01:59:00Z",
                }
            ],
        }
        approval_state = {"pending": [], "history": []}

        resolver = mock.Mock()
        resolver.active_task_map.return_value = {"REG-101": state["tasks"][0]}
        resolver.dependency_status.side_effect = lambda task_id: "done" if task_id == "REG-100" else "todo"
        resolver.dependency_satisfied.side_effect = lambda task_id: task_id == "REG-100"
        resolver.get.side_effect = lambda task_id: state["tasks"][0] if task_id == "REG-101" else {"id": "REG-100", "status": "done"}
        resolver.source.side_effect = lambda task_id: "active" if task_id == "REG-101" else "archive"

        with (
            mock.patch.object(ai_status, "task_resolver", return_value=resolver),
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={
                    "updated_at": "2026-04-14T02:00:00Z",
                    "counts": {"total": 3, "completed": 2, "superseded": 1},
                    "recent_terminal_ids": ["REG-100"],
                },
            ),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        self.assertEqual(bundle["execution_summary"]["ready_now"], 1)
        self.assertEqual(bundle["execution_summary"]["dependency_ready"], 1)
        self.assertEqual(bundle["execution_summary"]["done"], 2)
        self.assertEqual(bundle["execution_summary"]["superseded"], 1)
        self.assertEqual(bundle["archive_summary"]["recent_terminal_ids"], ["REG-100"])
        self.assertEqual(bundle["archive_summary"]["recent_terminal_tasks"][0]["task_id"], "REG-100")

    def test_build_dashboard_bundle_distinguishes_dependency_ready_from_dispatchable_ready(self) -> None:
        state = {
            "updated_at": "2026-04-16T08:50:00Z",
            "agents": [],
            "tasks": [
                {
                    "id": "RUN-001",
                    "title": "Running task",
                    "summary_zh": "Claude lane 已占用。",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "status": "in_progress",
                    "depends_on": [],
                    "next": "Still running",
                    "last_update": "2026-04-16T08:50:00Z",
                },
                {
                    "id": "TODO-CLAUDE",
                    "title": "Ready but owner busy",
                    "summary_zh": "依賴都完成，但 Claude 已忙碌。",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "status": "todo",
                    "depends_on": [],
                    "next": "Ready to start",
                    "last_update": "2026-04-16T08:50:00Z",
                },
                {
                    "id": "TODO-CODEX",
                    "title": "Ready but provider paused",
                    "summary_zh": "依賴都完成，但 Codex 正在 pause。",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "todo",
                    "depends_on": [],
                    "next": "Ready to start",
                    "last_update": "2026-04-16T08:50:00Z",
                },
            ],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-16T08:50:05Z", "focus_mode": "execution"},
            "queue": {"events": {}},
            "workers": {
                "claude-run": {
                    "run_id": "claude-run",
                    "provider": "claude",
                    "task_id": "RUN-001",
                    "status": "running",
                    "last_event_at": "2026-04-16T08:50:04Z",
                    "queue_event_id": "evt-1",
                    "pid": 1234,
                }
            },
            "provider_guardrails": {
                "dispatch_pauses": {
                    "codex": {
                        "provider": "codex",
                        "paused_at": "2026-04-16T08:45:00Z",
                        "blocked_until": "2026-04-16T09:00:00Z",
                        "reason": "402 You have no quota",
                    }
                }
            },
        }
        approval_state = {"pending": [], "history": []}
        config = {"ready_dispatcher": {"max_tasks_per_agent_by_agent": {"Claude": 1}}}

        with (
            mock.patch.object(ai_status, "load_config", return_value=config),
            mock.patch.object(ai_status, "load_archive_index", return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []}),
            mock.patch.object(ai_status, "pid_is_alive", return_value=True),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        self.assertEqual(bundle["runtime_summary"]["running_workers"], 1)
        self.assertEqual(bundle["execution_summary"]["ready_now"], 0)
        self.assertEqual(bundle["execution_summary"]["dependency_ready"], 2)

    def test_build_dashboard_bundle_counts_ready_capacity_when_owner_has_free_slots(self) -> None:
        state = {
            "updated_at": "2026-04-16T08:50:00Z",
            "agents": [],
            "tasks": [
                {
                    "id": "RUN-CODEX",
                    "title": "Running Codex task",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "in_progress",
                    "depends_on": [],
                    "next": "Running",
                    "last_update": "2026-04-16T08:50:00Z",
                },
                {
                    "id": "TODO-CODEX",
                    "title": "Ready Codex task",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "todo",
                    "depends_on": [],
                    "next": "Ready to start",
                    "last_update": "2026-04-16T08:50:00Z",
                },
            ],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-16T08:50:05Z", "focus_mode": "execution"},
            "queue": {"events": {}},
            "workers": {
                "codex-run": {
                    "run_id": "codex-run",
                    "logical_agent_id": "codex",
                    "agent_id": "codex1_1",
                    "provider": "codex1-1",
                    "task_id": "RUN-CODEX",
                    "status": "running",
                    "last_event_at": "2026-04-16T08:50:04Z",
                    "queue_event_id": "evt-1",
                    "pid": 1234,
                }
            },
            "provider_guardrails": {"dispatch_pauses": {}},
        }
        approval_state = {"pending": [], "history": []}
        config = {"ready_dispatcher": {"max_tasks_per_agent_by_agent": {"Codex": 2}}}

        with (
            mock.patch.object(ai_status, "load_config", return_value=config),
            mock.patch.object(ai_status, "load_archive_index", return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []}),
            mock.patch.object(ai_status, "pid_is_alive", return_value=True),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        self.assertEqual(bundle["runtime_summary"]["running_workers"], 1)
        self.assertEqual(bundle["execution_summary"]["ready_now"], 1)
        self.assertEqual(bundle["execution_summary"]["dependency_ready"], 1)
        self.assertEqual(bundle["dispatch_policy"]["max_tasks_per_agent"], None)
        self.assertEqual(bundle["dispatch_policy"]["max_tasks_per_agent_by_agent"], {"Codex": 2})

    def test_build_dashboard_bundle_includes_coordination_summary(self) -> None:
        state = {
            "updated_at": "2026-04-14T02:00:00Z",
            "agents": [],
            "tasks": [],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-14T02:05:00Z"},
            "queue": {"events": {}},
            "workers": {},
            "coordination": {
                "last_scan_at": "2026-04-14T02:04:00Z",
                "features": {
                    "F-042": {
                        "feature_id": "F-042",
                        "screen": "promotion-review",
                        "summary": "Feedback bundle ready",
                        "current_payload_type": "frontend-feedback",
                        "source_repo": "ajoe734/front-ai-trading-system",
                        "source_repo_id": "front_ai_trading_system",
                        "target_repo_id": "pantheon",
                        "target_agent": "Codex",
                        "worker_kind": "front-sync-worker",
                        "last_updated_at": "2026-04-14T02:04:00Z",
                        "last_dispatched_at": "2026-04-14T02:03:00Z",
                        "lovable_task_path": ".coordination/responses/F-042-lovable-ui-task.yaml",
                        "lovable_prompt_path": ".coordination/responses/F-042-lovable-prompt.md",
                        "mirrored_to_target_repo": {"target_repo_id": "front_ai_trading_system"},
                        "requests_by_type": {
                            "ui-done": {
                                "type": "ui-done",
                                "path": "../front-ai-trading-system/.coordination/requests/F-042-ui-done.yaml",
                                "payload": {"type": "ui-done", "summary": "UI done"},
                                "updated_at": "2026-04-14T02:02:00Z",
                            },
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": "../front-ai-trading-system/.coordination/requests/F-042-frontend-feedback.yaml",
                                "payload": {"type": "frontend-feedback", "summary": "Feedback ready"},
                                "updated_at": "2026-04-14T02:04:00Z",
                            },
                        },
                        "responses_by_type": {
                            "contract-ready": {
                                "type": "contract-ready",
                                "path": ".coordination/responses/F-042-contract-ready.yaml",
                                "payload": {"type": "contract-ready"},
                                "updated_at": "2026-04-14T02:00:00Z",
                            },
                            "lovable-ui-task": {
                                "type": "lovable-ui-task",
                                "path": ".coordination/responses/F-042-lovable-ui-task.yaml",
                                "payload": {"type": "lovable-ui-task", "status": "ready"},
                                "updated_at": "2026-04-14T02:01:00Z",
                            },
                        },
                    }
                },
            },
        }
        approval_state = {"pending": [], "history": []}

        with (
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
            mock.patch.object(ai_status, "coordination_local_response_path", return_value=None),
            mock.patch.object(ai_status, "coordination_review_snapshot", return_value=None),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        summary = bundle["coordination_summary"]
        self.assertEqual(summary["last_scan_at"], "2026-04-14T02:04:00Z")
        self.assertEqual(summary["counts"]["tracked_features"], 1)
        self.assertEqual(summary["counts"]["lovable_ready"], 1)
        self.assertEqual(summary["counts"]["ui_done_received"], 1)
        self.assertEqual(summary["counts"]["frontend_feedback_received"], 1)
        self.assertEqual(summary["counts"]["waiting_for_lovable"], 0)
        self.assertEqual(summary["features"][0]["stage"], "frontend_feedback_received")
        self.assertTrue(summary["features"][0]["mirrored_to_target_repo"])
        self.assertEqual(summary["features"][0]["paths"]["frontend_feedback"], "../front-ai-trading-system/.coordination/requests/F-042-frontend-feedback.yaml")

    def test_build_dashboard_bundle_does_not_count_stale_bff_gap_after_frontend_feedback(self) -> None:
        state = {
            "updated_at": "2026-04-14T02:00:00Z",
            "agents": [],
            "tasks": [],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-14T02:05:00Z"},
            "queue": {"events": {}},
            "workers": {},
            "coordination": {
                "last_scan_at": "2026-04-14T02:04:00Z",
                "features": {
                    "PKT-003-lineage-view": {
                        "feature_id": "PKT-003-lineage-view",
                        "screen": "lineage-view",
                        "summary": "Feedback bundle ready",
                        "current_payload_type": "frontend-feedback",
                        "source_repo": "ajoe734/front-ai-trading-system",
                        "source_repo_id": "front_ai_trading_system",
                        "target_repo_id": "pantheon",
                        "target_agent": "Codex",
                        "worker_kind": "front-sync-worker",
                        "last_updated_at": "2026-04-14T02:04:00Z",
                        "last_dispatched_at": "2026-04-14T02:03:00Z",
                        "lovable_task_path": ".coordination/responses/PKT-003-lineage-view-lovable-ui-task.yaml",
                        "lovable_prompt_path": ".coordination/responses/PKT-003-lineage-view-lovable-prompt.md",
                        "mirrored_to_target_repo": {"target_repo_id": "front_ai_trading_system"},
                        "requests_by_type": {
                            "bff-gap": {
                                "type": "bff-gap",
                                "path": "../front-ai-trading-system/.coordination/requests/PKT-003-lineage-view-bff-gap.yaml",
                                "payload": {"type": "bff-gap", "status": "blocked", "summary": "Old gap payload"},
                                "updated_at": "2026-04-14T02:01:00Z",
                            },
                            "ui-done": {
                                "type": "ui-done",
                                "path": "../front-ai-trading-system/.coordination/requests/PKT-003-lineage-view-ui-done.yaml",
                                "payload": {"type": "ui-done", "summary": "UI done"},
                                "updated_at": "2026-04-14T02:02:00Z",
                            },
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": "../front-ai-trading-system/.coordination/requests/PKT-003-lineage-view-frontend-feedback.yaml",
                                "payload": {"type": "frontend-feedback", "summary": "Feedback ready"},
                                "updated_at": "2026-04-14T02:04:00Z",
                            },
                        },
                        "responses_by_type": {
                            "contract-ready": {
                                "type": "contract-ready",
                                "path": ".coordination/responses/PKT-003-lineage-view-contract-ready.yaml",
                                "payload": {"type": "contract-ready"},
                                "updated_at": "2026-04-14T02:00:00Z",
                            },
                            "lovable-ui-task": {
                                "type": "lovable-ui-task",
                                "path": ".coordination/responses/PKT-003-lineage-view-lovable-ui-task.yaml",
                                "payload": {"type": "lovable-ui-task", "status": "ready"},
                                "updated_at": "2026-04-14T02:01:30Z",
                            },
                        },
                    }
                },
            },
        }
        approval_state = {"pending": [], "history": []}

        with (
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
            mock.patch.object(ai_status, "coordination_local_response_path", return_value=None),
            mock.patch.object(ai_status, "coordination_review_snapshot", return_value=None),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        summary = bundle["coordination_summary"]
        self.assertEqual(summary["counts"]["open_bff_gaps"], 0)
        self.assertEqual(summary["features"][0]["stage"], "frontend_feedback_received")
        self.assertFalse(summary["features"][0]["bff_gap_open"])
        self.assertTrue(summary["features"][0]["has_bff_gap"])

    def test_build_dashboard_bundle_marks_reviewed_frontend_feedback_when_review_packet_exists(self) -> None:
        state = {
            "updated_at": "2026-04-14T02:00:00Z",
            "agents": [],
            "tasks": [],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-14T02:05:00Z"},
            "queue": {"events": {}},
            "workers": {},
            "coordination": {
                "last_scan_at": "2026-04-14T02:04:00Z",
                "features": {
                    "F-042": {
                        "feature_id": "F-042",
                        "screen": "promotion-review",
                        "summary": "Feedback bundle ready",
                        "current_payload_type": "frontend-feedback",
                        "source_repo": "ajoe734/front-ai-trading-system",
                        "source_repo_id": "front_ai_trading_system",
                        "target_repo_id": "pantheon",
                        "target_agent": "Codex",
                        "worker_kind": "front-sync-worker",
                        "last_updated_at": "2026-04-14T02:04:00Z",
                        "last_dispatched_at": "2026-04-14T02:03:00Z",
                        "lovable_task_path": ".coordination/responses/F-042-lovable-ui-task.yaml",
                        "requests_by_type": {
                            "ui-done": {
                                "type": "ui-done",
                                "path": "../front-ai-trading-system/.coordination/requests/F-042-ui-done.yaml",
                                "payload": {"type": "ui-done", "summary": "UI done"},
                                "updated_at": "2026-04-14T02:02:00Z",
                            },
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": "../front-ai-trading-system/.coordination/requests/F-042-frontend-feedback.yaml",
                                "payload": {"type": "frontend-feedback", "summary": "Feedback ready"},
                                "updated_at": "2026-04-14T02:04:00Z",
                            },
                        },
                    }
                },
            },
        }
        approval_state = {"pending": [], "history": []}

        with (
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
            mock.patch.object(ai_status, "coordination_local_response_path", return_value=None),
            mock.patch.object(
                ai_status,
                "coordination_review_snapshot",
                return_value={"path": ".coordination/reviews/F-042-review.md", "disposition": "approved"},
            ),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        feature = bundle["coordination_summary"]["features"][0]
        self.assertEqual(feature["stage"], "frontend_feedback_reviewed")
        self.assertEqual(feature["review_disposition"], "approved")
        self.assertEqual(feature["paths"]["review"], ".coordination/reviews/F-042-review.md")

    def test_build_dashboard_bundle_prefers_closeout_response_for_loop_complete(self) -> None:
        state = {
            "updated_at": "2026-04-14T02:00:00Z",
            "agents": [],
            "tasks": [],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-14T02:05:00Z"},
            "queue": {"events": {}},
            "workers": {},
            "coordination": {
                "last_scan_at": "2026-04-14T02:04:00Z",
                "features": {
                    "EW-05-mutation-review": {
                        "feature_id": "EW-05-mutation-review",
                        "screen": "mutation-review",
                        "summary": "Feedback bundle ready",
                        "current_payload_type": "frontend-feedback",
                        "source_repo": "ajoe734/front-ai-trading-system",
                        "source_repo_id": "front_ai_trading_system",
                        "target_repo_id": "pantheon",
                        "target_agent": "Codex",
                        "worker_kind": "front-sync-worker",
                        "last_updated_at": "2026-04-14T02:04:00Z",
                        "last_dispatched_at": "2026-04-14T02:03:00Z",
                        "requests_by_type": {
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": "../front-ai-trading-system/.coordination/requests/EW-05-mutation-review-frontend-feedback.yaml",
                                "payload": {"type": "frontend-feedback", "summary": "Feedback ready"},
                                "updated_at": "2026-04-14T02:04:00Z",
                            },
                        },
                        "responses_by_type": {
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": ".coordination/responses/EW-05-mutation-review-frontend-feedback.yaml",
                                "payload": {
                                    "type": "frontend-feedback",
                                    "disposition": "close",
                                    "can_close": True,
                                    "lovable_ui_task_status": "closed",
                                    "next_action": "none",
                                },
                                "updated_at": "2026-04-14T02:04:30Z",
                            },
                            "lovable-ui-task": {
                                "type": "lovable-ui-task",
                                "path": ".coordination/responses/EW-05-mutation-review-lovable-ui-task.yaml",
                                "payload": {"type": "lovable-ui-task", "status": "follow-up-required"},
                                "updated_at": "2026-04-14T02:01:30Z",
                            },
                        },
                    }
                },
            },
        }
        approval_state = {"pending": [], "history": []}

        with (
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
            mock.patch.object(ai_status, "coordination_review_snapshot", return_value=None),
            mock.patch.object(ai_status, "load_local_coordination_payload", return_value=None),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        feature = bundle["coordination_summary"]["features"][0]
        self.assertEqual(feature["stage"], "loop_complete")

    def test_build_dashboard_bundle_counts_pantheon_frontend_feedback_response_as_feedback_and_runtime_proof(self) -> None:
        state = {
            "updated_at": "2026-04-14T02:00:00Z",
            "agents": [],
            "tasks": [],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-14T02:05:00Z"},
            "queue": {"events": {}},
            "workers": {},
            "coordination": {
                "last_scan_at": "2026-04-14T02:04:00Z",
                "features": {
                    "KW-01-institutional-memory": {
                        "feature_id": "KW-01-institutional-memory",
                        "screen": "institutional-memory",
                        "summary": "Pantheon closeout proof ready",
                        "current_payload_type": "lovable-ui-task",
                        "source_repo": "ajoe734/pantheon",
                        "source_repo_id": "pantheon",
                        "target_agent": "Gemini",
                        "worker_kind": "runtime-worker",
                        "last_updated_at": "2026-04-14T02:04:00Z",
                        "last_dispatched_at": "2026-04-14T02:03:00Z",
                        "requests_by_type": {
                            "ui-done": {
                                "type": "ui-done",
                                "path": "../front-ai-trading-system/.coordination/requests/KW-01-institutional-memory-ui-done.yaml",
                                "payload": {"type": "ui-done", "summary": "UI ready"},
                                "updated_at": "2026-04-14T02:03:30Z",
                            },
                        },
                        "responses_by_type": {
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": ".coordination/responses/KW-01-institutional-memory-frontend-feedback.yaml",
                                "payload": {
                                    "type": "frontend-feedback",
                                    "disposition": "close",
                                    "can_close": True,
                                    "runtime_verified_at": "2026-04-14T02:04:30Z",
                                    "verified_runtime_ref": ".coordination/reviews/KW-01-institutional-memory-review.md",
                                },
                                "updated_at": "2026-04-14T02:04:30Z",
                            },
                        },
                    }
                },
            },
        }
        approval_state = {"pending": [], "history": []}

        with (
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
            mock.patch.object(ai_status, "coordination_review_snapshot", return_value=None),
            mock.patch.object(ai_status, "coordination_repo_root", return_value=None),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        feature = bundle["coordination_summary"]["features"][0]
        self.assertEqual(feature["stage"], "loop_complete")
        self.assertTrue(feature["has_frontend_feedback"])
        self.assertEqual(feature["paths"]["frontend_feedback"], ".coordination/responses/KW-01-institutional-memory-frontend-feedback.yaml")
        self.assertTrue(feature["state_flags"]["runtime_verified"])
        self.assertEqual(bundle["coordination_summary"]["counts"]["frontend_feedback_received"], 1)
        self.assertEqual(bundle["coordination_summary"]["counts"]["runtime_verified"], 1)

    def test_build_dashboard_bundle_marks_closed_scope_when_followup_response_has_no_active_next_step(self) -> None:
        state = {
            "updated_at": "2026-04-14T02:00:00Z",
            "agents": [],
            "tasks": [],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-14T02:05:00Z"},
            "queue": {"events": {}},
            "workers": {},
            "coordination": {
                "last_scan_at": "2026-04-14T02:04:00Z",
                "features": {
                    "PKT-003-post-incident-review": {
                        "feature_id": "PKT-003-post-incident-review",
                        "screen": "post-incident-review-console",
                        "summary": "Feedback bundle ready",
                        "current_payload_type": "frontend-feedback",
                        "source_repo": "ajoe734/front-ai-trading-system",
                        "source_repo_id": "front_ai_trading_system",
                        "target_repo_id": "pantheon",
                        "target_agent": "Codex",
                        "worker_kind": "front-sync-worker",
                        "last_updated_at": "2026-04-14T02:04:00Z",
                        "last_dispatched_at": "2026-04-14T02:03:00Z",
                        "requests_by_type": {
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": "../front-ai-trading-system/.coordination/requests/PKT-003-post-incident-review-frontend-feedback.yaml",
                                "payload": {"type": "frontend-feedback", "summary": "Feedback ready"},
                                "updated_at": "2026-04-14T02:04:00Z",
                            },
                        },
                        "responses_by_type": {
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": ".coordination/responses/PKT-003-post-incident-review-frontend-feedback.yaml",
                                "payload": {
                                    "type": "frontend-feedback",
                                    "disposition": "follow-up-required",
                                    "can_close": False,
                                },
                                "updated_at": "2026-04-14T02:04:30Z",
                            },
                            "lovable-ui-task": {
                                "type": "lovable-ui-task",
                                "path": ".coordination/responses/PKT-003-post-incident-review-lovable-ui-task.yaml",
                                "payload": {"type": "lovable-ui-task", "status": "closed"},
                                "updated_at": "2026-04-14T02:01:30Z",
                            },
                        },
                    }
                },
            },
        }
        approval_state = {"pending": [], "history": []}

        with (
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
            mock.patch.object(ai_status, "coordination_review_snapshot", return_value=None),
            mock.patch.object(ai_status, "load_local_coordination_payload", return_value=None),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        feature = bundle["coordination_summary"]["features"][0]
        self.assertEqual(feature["stage"], "closed")

    def test_build_dashboard_bundle_exposes_coordination_state_flags(self) -> None:
        state = {
            "updated_at": "2026-04-14T02:00:00Z",
            "agents": [],
            "tasks": [],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-14T02:05:00Z"},
            "queue": {"events": {}},
            "workers": {},
            "coordination": {
                "last_scan_at": "2026-04-14T02:04:00Z",
                "features": {
                    "F-042": {
                        "feature_id": "F-042",
                        "screen": "promotion-review",
                        "summary": "Contract ready and mirrored",
                        "current_payload_type": "frontend-feedback",
                        "source_repo": "ajoe734/front-ai-trading-system",
                        "source_repo_id": "front_ai_trading_system",
                        "target_repo_id": "pantheon",
                        "target_agent": "Codex",
                        "worker_kind": "front-sync-worker",
                        "last_updated_at": "2026-04-14T02:04:00Z",
                        "last_dispatched_at": "2026-04-14T02:03:00Z",
                        "mirrored_to_target_repo": {"target_repo_id": "front_ai_trading_system"},
                        "requests_by_type": {
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": "../front-ai-trading-system/.coordination/requests/F-042-frontend-feedback.yaml",
                                "payload": {"type": "frontend-feedback", "summary": "Feedback ready"},
                                "updated_at": "2026-04-14T02:04:00Z",
                            },
                            "needs-runtime": {
                                "type": "needs-runtime",
                                "path": ".coordination/requests/F-042-needs-runtime.yaml",
                                "payload": {
                                    "type": "needs-runtime",
                                    "status": "resolved",
                                    "runtime_verified_at": "2026-04-14T02:02:30Z",
                                },
                                "updated_at": "2026-04-14T02:02:30Z",
                            },
                        },
                        "responses_by_type": {
                            "contract-ready": {
                                "type": "contract-ready",
                                "path": ".coordination/responses/F-042-contract-ready.yaml",
                                "payload": {"type": "contract-ready"},
                                "updated_at": "2026-04-14T02:00:00Z",
                            },
                            "lovable-ui-task": {
                                "type": "lovable-ui-task",
                                "path": ".coordination/responses/F-042-lovable-ui-task.yaml",
                                "payload": {"type": "lovable-ui-task", "status": "ready"},
                                "updated_at": "2026-04-14T02:01:00Z",
                            },
                        },
                    }
                },
            },
        }
        approval_state = {"pending": [], "history": []}

        with (
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
            mock.patch.object(ai_status, "coordination_local_response_path", return_value=None),
            mock.patch.object(ai_status, "coordination_review_snapshot", return_value=None),
            mock.patch.object(ai_status, "coordination_repo_root", side_effect=lambda repo_id: Path(f"/tmp/{repo_id}")),
            mock.patch.object(
                ai_status,
                "coordination_repo_payload_exists",
                side_effect=lambda _root, rel_path: str(rel_path).endswith("F-042-contract-ready.yaml"),
            ),
            mock.patch.object(
                ai_status,
                "coordination_audit_matches",
                side_effect=lambda _root, _feature_id, marker: marker in {"dispatch-emitted", "received"},
            ),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        feature = bundle["coordination_summary"]["features"][0]
        flags = feature["state_flags"]
        self.assertEqual(flags["backend_route_live"], True)
        self.assertEqual(flags["pantheon_handoff_published"], True)
        self.assertEqual(flags["mirrored_to_front_default_branch"], True)
        self.assertEqual(flags["dispatch_emitted"], True)
        self.assertEqual(flags["front_receiver_applied"], True)
        self.assertEqual(flags["lovable_consumed"], True)
        self.assertEqual(flags["ui_activated"], True)
        self.assertEqual(flags["runtime_verified"], True)

        counts = bundle["coordination_summary"]["counts"]
        self.assertEqual(counts["backend_route_live"], 1)
        self.assertEqual(counts["pantheon_handoff_published"], 1)
        self.assertEqual(counts["mirrored_to_front_default_branch"], 1)
        self.assertEqual(counts["dispatch_emitted"], 1)
        self.assertEqual(counts["front_receiver_applied"], 1)
        self.assertEqual(counts["lovable_consumed"], 1)
        self.assertEqual(counts["ui_activated"], 1)
        self.assertEqual(counts["runtime_verified"], 1)

    def test_build_dashboard_bundle_treats_dead_suspended_approval_as_approval_wait_not_live_worker(self) -> None:
        state = {
            "updated_at": "2026-04-14T01:42:00Z",
            "agents": [],
            "tasks": [
                {
                    "id": "BG-005",
                    "title": "Define golden replay scenario and acceptance runbook",
                    "summary_zh": "定義 golden replay scenario 與 acceptance runbook。",
                    "phase": "Blueprint Gap P0",
                    "owner": "Claude",
                    "reviewer": "Qwen",
                    "status": "review_approved",
                    "depends_on": ["BG-000"],
                    "next": "Supervisor resumed BG-005 for finalize after successful dispatch.",
                    "last_update": "2026-04-14T00:42:04Z",
                },
            ],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {
                "pid": 490443,
                "last_heartbeat_at": "2026-04-14T01:42:22Z",
                "mode_status": "active",
                "mode_occupancy": {
                    "planning": {"running": 0, "pending": 0, "queued": 0},
                    "execution": {"running": 0, "pending": 1, "queued": 0},
                    "coordination": {"running": 0, "pending": 0, "queued": 0},
                },
            },
            "queue": {
                "events": {
                    "evt-1": {
                        "status": "manual_pending",
                        "run_id": "claude-run-1",
                        "processed_at": "2026-04-14T00:42:04Z",
                    }
                }
            },
            "workers": {
                "claude-run-1": {
                    "task_id": "BG-005",
                    "queue_event_id": "evt-1",
                    "agent_id": "claude",
                    "provider": "claude",
                    "status": "suspended_approval",
                    "pid": 477808,
                    "last_event_at": "2026-04-14T00:42:46Z",
                    "request_snapshot": {"reason": "owned_finalize_dispatch"},
                }
            },
        }
        approval_state = {
            "pending": [
                {
                    "approval_id": "apr-1",
                    "task_id": "BG-005",
                    "worker_run_id": "claude-run-1",
                    "provider": "claude",
                    "created_at": "2026-04-14T00:42:46Z",
                }
            ],
            "history": [],
        }

        with mock.patch.object(ai_status, "pid_is_alive", return_value=False):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        self.assertEqual(bundle["runtime_summary"]["running_workers"], 0)
        self.assertEqual(bundle["runtime_summary"]["pending_workers"], 0)
        self.assertEqual(bundle["worker_task_links"], [])
        self.assertFalse(any(item["type"] == "queue_started_without_worker" for item in bundle["truth_mismatches"]))

    def test_build_dashboard_bundle_skips_planning_approval_without_task_board_row(self) -> None:
        state = {
            "updated_at": "2026-04-14T05:35:00Z",
            "agents": [],
            "tasks": [],
        }
        planning_state = {"status": "active", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-14T05:35:00Z"},
            "queue": {"events": {}},
            "workers": {
                "claude-run-1": {
                    "task_id": "phase3-2026-04-14-pantheon-console-loop",
                    "queue_event_id": "evt-1",
                    "agent_id": "claude",
                    "provider": "claude",
                    "status": "suspended_approval",
                    "last_event_at": "2026-04-14T05:35:00Z",
                    "request_snapshot": {
                        "reason": "discussion_planning_readout",
                        "metadata": {"planning": {"mode": "discussion_planning"}},
                    },
                }
            },
        }
        approval_state = {
            "pending": [
                {
                    "approval_id": "apr-1",
                    "task_id": "phase3-2026-04-14-pantheon-console-loop",
                    "worker_run_id": "claude-run-1",
                    "provider": "claude",
                    "created_at": "2026-04-14T05:35:00Z",
                }
            ],
            "history": [],
        }

        bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        self.assertFalse(any(item["type"] == "approval_missing_task" for item in bundle["truth_mismatches"]))

    def test_build_dashboard_bundle_skips_planning_worker_without_task_board_row(self) -> None:
        state = {
            "updated_at": "2026-04-14T05:37:00Z",
            "agents": [],
            "tasks": [],
        }
        planning_state = {"status": "active", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-14T05:37:00Z"},
            "queue": {"events": {}},
            "workers": {
                "claude-run-1": {
                    "task_id": "phase3-2026-04-14-pantheon-console-loop",
                    "queue_event_id": "evt-1",
                    "agent_id": "claude",
                    "provider": "claude",
                    "status": "running",
                    "pid": None,
                    "last_event_at": "2026-04-14T05:37:00Z",
                    "request_snapshot": {
                        "reason": "discussion_planning_round",
                        "metadata": {"planning": {"mode": "discussion_planning"}},
                    },
                }
            },
        }
        approval_state = {"pending": [], "history": []}

        bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        self.assertFalse(any(item["type"] == "worker_task_missing" for item in bundle["truth_mismatches"]))


class ActivityLogRotationTests(unittest.TestCase):
    def _make_log(self, *, size_per_line: int = 200, line_count: int = 100) -> Path:
        tmp = tempfile.TemporaryDirectory(prefix="ai-status-rotate-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        log_path = root / "ai-activity-log.jsonl"
        payload = ("x" * (size_per_line - 1)) + "\n"
        log_path.write_text(payload * line_count, encoding="utf-8")
        return log_path

    def test_does_not_rotate_when_under_threshold(self) -> None:
        log_path = self._make_log(line_count=10)
        with (
            mock.patch.object(ai_status, "LOG_FILE", log_path),
            mock.patch.object(ai_status, "LOG_ROTATE_MAX_BYTES", 1_000_000),
            mock.patch.object(ai_status, "LOG_ROTATE_KEEP_LINES", 5),
        ):
            archive = ai_status.maybe_rotate_activity_log()
        self.assertIsNone(archive)
        self.assertEqual(len(log_path.read_bytes().splitlines()), 10)
        archive_dir = log_path.parent / "archive" / "logs"
        self.assertFalse(archive_dir.exists())

    def test_rotates_and_keeps_tail_when_over_threshold(self) -> None:
        log_path = self._make_log(size_per_line=200, line_count=100)  # ~20 KB
        with (
            mock.patch.object(ai_status, "LOG_FILE", log_path),
            mock.patch.object(ai_status, "LOG_ROTATE_MAX_BYTES", 5_000),
            mock.patch.object(ai_status, "LOG_ROTATE_KEEP_LINES", 8),
        ):
            archive = ai_status.maybe_rotate_activity_log()
        assert archive is not None
        self.assertTrue(archive.exists())
        self.assertTrue(str(archive).endswith(".gz"))
        # The active log now holds just the tail
        active_lines = log_path.read_bytes().splitlines()
        self.assertEqual(len(active_lines), 8)
        # The gzip archive holds the full original
        import gzip as _gz
        with _gz.open(archive, "rb") as fh:
            archived = fh.read().splitlines()
        self.assertEqual(len(archived), 100)

    def test_rotation_preserves_inode_for_concurrent_appenders(self) -> None:
        log_path = self._make_log(line_count=80)
        before_inode = log_path.stat().st_ino
        with (
            mock.patch.object(ai_status, "LOG_FILE", log_path),
            mock.patch.object(ai_status, "LOG_ROTATE_MAX_BYTES", 1),
            mock.patch.object(ai_status, "LOG_ROTATE_KEEP_LINES", 3),
        ):
            ai_status.maybe_rotate_activity_log()
        after_inode = log_path.stat().st_ino
        self.assertEqual(before_inode, after_inode)

    def test_append_log_triggers_rotation(self) -> None:
        log_path = self._make_log(size_per_line=200, line_count=50)  # ~10 KB
        with (
            mock.patch.object(ai_status, "LOG_FILE", log_path),
            mock.patch.object(ai_status, "LOG_ROTATE_MAX_BYTES", 5_000),
            mock.patch.object(ai_status, "LOG_ROTATE_KEEP_LINES", 4),
        ):
            ai_status.append_log({"ts": "2026-05-18T00:00:00Z", "msg": "new entry"})
        active_lines = log_path.read_text(encoding="utf-8").splitlines()
        # 4 kept tail lines + 1 new = 5
        self.assertEqual(len(active_lines), 5)
        self.assertIn("new entry", active_lines[-1])
        archive_dir = log_path.parent / "archive" / "logs"
        archives = list(archive_dir.glob("*.gz"))
        self.assertEqual(len(archives), 1)


class StatusCheckEmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        ai_status.clear_ai_status_caches()

    def test_get_repository_slug_safe_env(self) -> None:
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "test-owner/test-repo"}):
            self.assertEqual(ai_status.get_repository_slug_safe(), "test-owner/test-repo")

    def test_get_repository_slug_safe_config(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(ai_status, "load_config", return_value={"repository": "foo/bar"}), \
                 mock.patch.object(ai_status, "repository_slug", return_value="foo/bar"):
                self.assertEqual(ai_status.get_repository_slug_safe(), "foo/bar")

    def test_resolve_task_sha_rejects_branch_absent_old_pr_and_local_head(self) -> None:
        mock_result = mock.Mock(returncode=0, stdout="")

        with mock.patch("subprocess.run", return_value=mock_result) as mock_run, \
             mock.patch.object(ai_status, "get_gh_executable", return_value="gh"):
            sha = ai_status.resolve_task_sha("ODP-001")
            self.assertIsNone(sha)
            mock_run.assert_called_once_with(
                [
                    "git",
                    "ls-remote",
                    "--heads",
                    "origin",
                    "refs/heads/task/ODP-001",
                    "refs/heads/task-ODP-001",
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=ai_status.ROOT,
            )

    def test_resolve_task_sha_prefers_pushed_remote_over_local_and_merged_pr(self) -> None:
        task_id = "ODP-REMOTE-001"
        remote_sha = "2" * 40
        local_sha = "3" * 40
        merged_pr_sha = "1" * 40

        def side_effect(cmd, **kwargs):
            result = mock.Mock(returncode=1, stdout="")
            if cmd[:4] == ["git", "ls-remote", "--heads", "origin"]:
                result.returncode = 0
                result.stdout = f"{remote_sha}\trefs/heads/task/{task_id}\n"
            elif cmd == ["git", "branch", "--show-current"]:
                result.returncode = 0
                result.stdout = f"task/{task_id}\n"
            elif cmd == ["git", "rev-parse", "HEAD"]:
                result.returncode = 0
                result.stdout = f"{local_sha}\n"
            elif "pr" in cmd and "view" in cmd:
                result.returncode = 0
                result.stdout = json.dumps({"headRefOid": merged_pr_sha})
            return result

        with mock.patch("subprocess.run", side_effect=side_effect) as mock_run:
            sha = ai_status.resolve_task_sha(task_id)

        self.assertEqual(sha, remote_sha)
        self.assertEqual(mock_run.call_count, 1)

    def test_resolve_task_sha_rejects_remote_failure_without_cached_fallback(self) -> None:
        with mock.patch(
            "subprocess.run",
            return_value=mock.Mock(returncode=1, stdout="stale-cached-ref"),
        ) as mock_run:
            self.assertIsNone(ai_status.resolve_task_sha("ODP-001"))
        mock_run.assert_called_once()

    def test_resolve_task_sha_rejects_ambiguous_or_malformed_remote_refs(self) -> None:
        task_id = "ODP-001"
        cases = (
            "not-a-sha\trefs/heads/task/ODP-001\n",
            (
                f"{'1' * 40}\trefs/heads/task/{task_id}\n"
                f"{'2' * 40}\trefs/heads/task-{task_id}\n"
            ),
        )
        for stdout in cases:
            with self.subTest(stdout=stdout), mock.patch(
                "subprocess.run",
                return_value=mock.Mock(returncode=0, stdout=stdout),
            ):
                ai_status.clear_ai_status_caches()
                self.assertIsNone(ai_status.resolve_task_sha(task_id))

    def test_resolve_task_sha_uses_bounded_warm_cache_for_ordinary_lookups(self) -> None:
        task_id = "ODP-WARM-CACHE-001"
        sha_initial = "a" * 40

        mock_res = mock.Mock(returncode=0, stdout=f"{sha_initial}\trefs/heads/task/{task_id}\n")
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "alfloop-dev/odayplus"}, clear=False), \
             mock.patch("subprocess.run", return_value=mock_res) as mock_run:
            ai_status.clear_ai_status_caches()
            # First ordinary call populates cache
            first_sha = ai_status.resolve_task_sha(task_id)
            self.assertEqual(first_sha, sha_initial)
            self.assertEqual(mock_run.call_count, 1)

            # Second ordinary call reuses warm cache without hitting origin
            second_sha = ai_status.resolve_task_sha(task_id)
            self.assertEqual(second_sha, sha_initial)
            self.assertEqual(mock_run.call_count, 1)

            # Non-governance payload call also reuses warm cache
            payload = ai_status.task_review_status_payload({"id": task_id}, "review_approved")
            self.assertIsNotNone(payload)
            self.assertEqual(payload["sha"], sha_initial)
            self.assertEqual(mock_run.call_count, 1)

            # Force refresh query bypasses warm cache
            refreshed_sha = ai_status.resolve_task_sha(task_id, force_refresh=True)
            self.assertEqual(refreshed_sha, sha_initial)
            self.assertEqual(mock_run.call_count, 2)

    def test_resolve_task_sha_bypasses_warm_cache_on_remote_change_removal_or_failure(self) -> None:
        task_id = "ODP-CACHE-TEST-001"
        sha_initial = "a" * 40
        sha_updated = "b" * 40

        # Step 1: Initial call returns sha_initial and populates cache
        mock_res1 = mock.Mock(returncode=0, stdout=f"{sha_initial}\trefs/heads/task/{task_id}\n")
        with mock.patch("subprocess.run", return_value=mock_res1):
            ai_status.clear_ai_status_caches()
            first_sha = ai_status.resolve_task_sha(task_id)
            self.assertEqual(first_sha, sha_initial)

        # Step 2: Remote branch is updated (force-push to sha_updated). force_refresh=True returns sha_updated, NOT cached sha_initial.
        mock_res2 = mock.Mock(returncode=0, stdout=f"{sha_updated}\trefs/heads/task/{task_id}\n")
        with mock.patch("subprocess.run", return_value=mock_res2):
            second_sha = ai_status.resolve_task_sha(task_id, force_refresh=True)
            self.assertEqual(second_sha, sha_updated)

        # Step 3: Remote branch is removed/deleted. fresh=True fails closed (returns None), NOT cached sha_updated.
        mock_res3 = mock.Mock(returncode=0, stdout="")
        with mock.patch("subprocess.run", return_value=mock_res3):
            third_sha = ai_status.resolve_task_sha(task_id, fresh=True)
            self.assertIsNone(third_sha)

        # Step 4: Remote origin command fails. force_refresh=True fails closed (returns None), NOT cached sha_updated.
        mock_res4 = mock.Mock(returncode=1, stdout="")
        with mock.patch("subprocess.run", return_value=mock_res4):
            fourth_sha = ai_status.resolve_task_sha(task_id, force_refresh=True)
            self.assertIsNone(fourth_sha)

    def test_active_branch_governance_refreshes_but_done_uses_delivery_provenance(self) -> None:
        task_id = "ODP-GOV-TEST-001"
        stale_sha = "1" * 40
        remote_sha = "2" * 40

        # 1. Warm cache with stale_sha
        mock_warm = mock.Mock(returncode=0, stdout=f"{stale_sha}\trefs/heads/task/{task_id}\n")
        with mock.patch("subprocess.run", return_value=mock_warm):
            ai_status.clear_ai_status_caches()
            self.assertEqual(ai_status.resolve_task_sha(task_id), stale_sha)

        # 2. Test command_approve when origin changes to remote_sha
        state_approve = {
            "tasks": [{
                "id": task_id,
                "status": "review",
                "owner": "Codex",
                "reviewer": "Claude",
                "review_submission": {"remote_sha": remote_sha},
            }]
        }
        mock_changed = mock.Mock(returncode=0, stdout=f"{remote_sha}\trefs/heads/task/{task_id}\n")
        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False), \
             mock.patch("subprocess.run", return_value=mock_changed):
            ai_status.command_approve(state_approve, [task_id, "Approved new head"])
            task = ai_status.get_task(state_approve, task_id)
            self.assertEqual(task["approved_head"], remote_sha)
            self.assertNotEqual(task["approved_head"], stale_sha)

        # A branch update after task_finalize invalidates the submitted review
        # packet. The reviewer must never approve a different remote head.
        state_mismatch = {
            "tasks": [{
                "id": task_id,
                "status": "review",
                "owner": "Codex",
                "reviewer": "Claude",
                "review_submission": {"remote_sha": stale_sha},
            }]
        }
        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False), \
             mock.patch("subprocess.run", return_value=mock_changed):
            with self.assertRaises(SystemExit) as cm:
                ai_status.command_approve(state_mismatch, [task_id, "Must not approve moved head"])
        self.assertIn("does not match current remote task head", str(cm.exception))
        self.assertEqual(ai_status.get_task(state_mismatch, task_id)["status"], "review")

        # 3. Once merged, command_done uses the unique checkout and immutable
        # PR head from delivery provenance, not the now-ephemeral remote ref.
        state_done = {
            "tasks": [{"id": task_id, "status": "review_approved", "approved_head": stale_sha, "owner": "Codex", "reviewer": "Claude"}]
        }
        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False), \
             mock.patch.object(
                 ai_status,
                 "resolve_task_sha",
                 side_effect=AssertionError("done must not query the ephemeral remote ref"),
             ), \
             mock.patch.object(
                 ai_status,
                 "collect_done_delivery_metadata",
                 return_value={
                     "verified_head": remote_sha,
                     "pull_request": {"head_sha": stale_sha, "merge_commit": "3" * 40},
                 },
             ):
            with self.assertRaises(SystemExit) as cm:
                ai_status.command_done(state_done, [task_id, "Done attempt"])
            self.assertIn("differs from reviewer-approved head", str(cm.exception))

        # 4. Test command_restore_approved when origin is removed
        state_restore = {
            "tasks": [{"id": task_id, "status": "in_progress", "last_approved_head": stale_sha, "review_notes_zh": ["note"], "owner": "Codex", "reviewer": "Claude"}],
            "handoffs": [],
            "blockers": []
        }
        with mock.patch("subprocess.run", return_value=mock_warm):
            ai_status.clear_ai_status_caches()
            self.assertEqual(ai_status.resolve_task_sha(task_id), stale_sha)

        mock_removed = mock.Mock(returncode=0, stdout="")
        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False), \
             mock.patch("subprocess.run", return_value=mock_removed):
            with self.assertRaises(SystemExit) as cm:
                ai_status.command_restore_approved(state_restore, [task_id, "Restore attempt"])
            self.assertIn("branch HEAD could not be resolved", str(cm.exception))

        # 5. Test command_restore_approved_head when origin changes
        state_rah = {
            "tasks": [{"id": task_id, "status": "review_approved", "owner": "Codex", "reviewer": "Claude"}]
        }
        with mock.patch("subprocess.run", return_value=mock_warm):
            ai_status.clear_ai_status_caches()
            self.assertEqual(ai_status.resolve_task_sha(task_id), stale_sha)

        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False), \
             mock.patch("subprocess.run", return_value=mock_changed):
            with self.assertRaises(SystemExit) as cm:
                ai_status.command_restore_approved_head(state_rah, [task_id, stale_sha, "Restore head attempt"])
            self.assertIn("does not match the task branch head", str(cm.exception))

    def test_resolve_task_sha_accepts_exact_40_and_64_hex_lengths(self) -> None:
        task_id = "ODP-HEX-001"
        sha_40 = "a" * 40
        sha_64 = "f" * 64

        mock_40 = mock.Mock(returncode=0, stdout=f"{sha_40}\trefs/heads/task/{task_id}\n")
        with mock.patch("subprocess.run", return_value=mock_40) as m_run:
            ai_status.clear_ai_status_caches()
            res = ai_status.resolve_task_sha(task_id)
            self.assertEqual(res, sha_40)
            self.assertTrue(m_run.called)

        mock_64 = mock.Mock(returncode=0, stdout=f"{sha_64}\trefs/heads/task/{task_id}\n")
        with mock.patch("subprocess.run", return_value=mock_64) as m_run:
            ai_status.clear_ai_status_caches()
            res = ai_status.resolve_task_sha(task_id)
            self.assertEqual(res, sha_64)
            self.assertTrue(m_run.called)

        for invalid_len in (39, 41, 63, 65):
            bad_sha = "c" * invalid_len
            mock_bad = mock.Mock(returncode=0, stdout=f"{bad_sha}\trefs/heads/task/{task_id}\n")
            with self.subTest(invalid_len=invalid_len), mock.patch("subprocess.run", return_value=mock_bad) as m_run:
                ai_status.clear_ai_status_caches()
                res = ai_status.resolve_task_sha(task_id)
                self.assertIsNone(res)
                self.assertTrue(m_run.called)

        for nonhex_sha in ("g" * 40, "z" * 64, "G" * 40, "X" * 64, "123456789012345678901234567890123456789g"):
            mock_nonhex = mock.Mock(returncode=0, stdout=f"{nonhex_sha}\trefs/heads/task/{task_id}\n")
            with self.subTest(nonhex_sha=nonhex_sha), mock.patch("subprocess.run", return_value=mock_nonhex) as m_run:
                ai_status.clear_ai_status_caches()
                res = ai_status.resolve_task_sha(task_id)
                self.assertIsNone(res)
                self.assertTrue(m_run.called)

    def test_emit_task_review_status_check_approved(self) -> None:
        task = {"id": "ODP-001", "reviewer": "Codex", "approved_head": "sha123"}
        mock_run = mock.Mock()
        mock_run.returncode = 0

        with mock.patch.object(ai_status, "resolve_task_sha", return_value="sha123"), \
             mock.patch.object(ai_status, "get_repository_slug_safe", return_value="owner/repo"), \
             mock.patch.object(ai_status, "get_gh_executable", return_value="gh"), \
             mock.patch("subprocess.run", return_value=mock_run) as mock_subprocess:
            ai_status.emit_task_review_status_check(task, "review_approved")
            mock_subprocess.assert_called_once_with(
                [
                    "gh", "api",
                    "-X", "POST",
                    "repos/owner/repo/statuses/sha123",
                    "-F", "state=success",
                    "-F", "context=task-review-gate",
                    "-F", "description=Approved by assigned reviewer Codex"
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=ai_status.ROOT,
            )

    def test_emit_status_checks_for_changed_tasks(self) -> None:
        state_before = {
            "tasks": [{"id": "ODP-001", "status": "review"}]
        }
        state_after = {
            "tasks": [{"id": "ODP-001", "status": "review_approved"}]
        }

        with mock.patch.object(ai_status, "emit_task_review_status_check") as mock_emit:
            ai_status.emit_status_checks_for_changed_tasks(
                state_before, state_after, "approve", ["ODP-001"]
            )
            mock_emit.assert_called_once_with(
                {"id": "ODP-001", "status": "review_approved"},
                "review_approved"
            )


class ActorReferenceValidationTests(unittest.TestCase):
    """Actor-shaped fields must hold agent names, never prose or task ids.

    Regression cover for the synthetic-agent fabrication path: a worker that
    passed a blocker sentence where an agent name was expected used to mint a
    permanent fleet agent in ai-status.json.
    """

    PROSE = (
        "STOP FOR CODEX2 REVIEW ON EXACT HEAD 6ca4726c (revision 8). Two gates are "
        "open at once and both need Codex2/coordinator action, not more owner work."
    )
    TASK_ID = "ODP-P10-FLEET-CONFLICT-REAUDIT-001"
    # Stands in for the merged Supervisor config: config.json workers plus the
    # config.local.json overlay that declares Codex3 through Codex9.
    CONFIGURED = frozenset(
        {"Claude", "Claude2", "Claude3", "Antigravity3", "Codex", "Codex2", "Codex5", "Codex8"}
    )

    def setUp(self) -> None:
        self.addCleanup(self._restore_roster)
        self._known_before = dict(ai_status.KNOWN_AGENTS)
        self._quarantined_before = set(ai_status.QUARANTINED_AGENTS)
        # Keep every case away from the real merged .orchestrator config.
        patcher = mock.patch.object(
            ai_status, "configured_agent_names", return_value=set(self.CONFIGURED)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _restore_roster(self) -> None:
        ai_status.KNOWN_AGENTS.clear()
        ai_status.KNOWN_AGENTS.update(self._known_before)
        ai_status.QUARANTINED_AGENTS.clear()
        ai_status.QUARANTINED_AGENTS.update(self._quarantined_before)

    def _state(self) -> dict:
        return {
            "agents": [
                {
                    "name": name,
                    "capability_lane": [],
                    "status": "idle",
                    "current_task_ids": [],
                    "branch": "",
                    "next": "",
                    "last_update": None,
                }
                for name in ("Claude", "Codex2")
            ],
            "tasks": [
                {
                    "id": "ODP-ACTOR-REF-001",
                    "title": "Actor reference fixture",
                    "phase": "Fleet Control Plane Integrity",
                    "owner": "Claude",
                    "reviewer": "Codex2",
                    "status": "in_progress",
                    "depends_on": [],
                    "artifacts": [],
                    "acceptance": [],
                    "next": "",
                    "last_update": "2026-07-29T00:00:00Z",
                }
            ],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }

    # -- shape validation ---------------------------------------------------

    def test_valid_named_actors_are_accepted(self) -> None:
        for name in ("Claude", "Claude2", "Antigravity7", "Codex2", "Human/Ops", "CodexCoordinator"):
            self.assertIsNone(ai_status.actor_reference_problem(name), name)

    def test_task_id_is_rejected(self) -> None:
        problem = ai_status.actor_reference_problem(self.TASK_ID)
        self.assertIsNotNone(problem)
        self.assertIn("task id", problem)

    def test_prose_is_rejected(self) -> None:
        problem = ai_status.actor_reference_problem("Remediation task for migration-compat timeout")
        self.assertIsNotNone(problem)
        self.assertIn("prose", problem)

    def test_oversized_string_is_rejected(self) -> None:
        problem = ai_status.actor_reference_problem("x" * 400)
        self.assertIsNotNone(problem)
        self.assertIn("max 40", problem)

    def test_empty_reference_is_rejected(self) -> None:
        self.assertIsNotNone(ai_status.actor_reference_problem(""))
        self.assertIsNotNone(ai_status.actor_reference_problem(None))

    def test_aliases_and_human_ops_semantics_survive_validation(self) -> None:
        for raw, expected in (
            ("agy3", "Antigravity3"),
            ("claude 2", "Claude2"),
            ("human ops", "Human/Ops"),
            ("ops", "Human/Ops"),
        ):
            self.assertEqual(
                expected,
                ai_status.resolve_actor_reference(raw, field="waiting-for"),
                raw,
            )

    def test_unknown_but_well_shaped_actor_is_rejected(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            ai_status.resolve_actor_reference("Nessie9", field="owner")
        self.assertIn("not a registered agent", str(ctx.exception))

    def test_durable_roster_entry_is_not_an_authority_for_new_input(self) -> None:
        """A name that only exists in agents[] must not legitimise itself.

        agents[] is exactly where a fabricated actor lands, so accepting it as a
        declaration would let one bad record validate the next command.
        """
        state = self._state()
        state["agents"].append({"name": "Nessie9", "capability_lane": [], "status": "idle",
                                "current_task_ids": [], "branch": "", "next": "", "last_update": None})
        with self.assertRaises(SystemExit) as ctx:
            ai_status.resolve_actor_reference("Nessie9", field="owner")
        self.assertIn("agents[] is not a declaration", str(ctx.exception))
        # The record itself stays readable — reading state must never abort.
        ai_status.validate_state(state)
        self.assertIn("Nessie9", [agent["name"] for agent in state["agents"]])

    def test_static_known_agent_without_config_is_rejected(self) -> None:
        """Gemini/Copilot survive in KNOWN_AGENTS but the Supervisor cannot dispatch them."""
        for stale in ("Gemini", "Gemini2", "Copilot"):
            self.assertIn(stale, ai_status.KNOWN_AGENTS, stale)
            with self.assertRaises(SystemExit, msg=stale) as ctx:
                ai_status.resolve_actor_reference(stale, field="owner")
            self.assertIn("not a registered agent", str(ctx.exception))

    def test_non_worker_actors_are_accepted_without_config(self) -> None:
        for actor in ("Human/Ops", "Orchestrator", "CodexCoordinator"):
            self.assertEqual(
                actor, ai_status.resolve_actor_reference(actor, field="waiting-for"), actor
            )

    def test_extra_agents_env_registers_an_actor(self) -> None:
        with self.assertRaises(SystemExit):
            ai_status.resolve_actor_reference("Nessie9", field="owner")
        with mock.patch.dict(os.environ, {"AI_STATUS_EXTRA_AGENTS": "Nessie9"}):
            self.assertEqual(
                "Nessie9", ai_status.resolve_actor_reference("Nessie9", field="owner")
            )

    # -- no durable mutation on rejection -----------------------------------

    def test_blocker_command_rejects_prose_before_mutating_state(self) -> None:
        state = self._state()
        before = json.dumps(state, sort_keys=True)
        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}):
            with self.assertRaises(SystemExit):
                ai_status.command_blocker(state, ["ODP-ACTOR-REF-001", "message", self.PROSE])
        self.assertEqual(before, json.dumps(state, sort_keys=True))
        self.assertNotIn(self.PROSE, ai_status.KNOWN_AGENTS)

    def test_blocker_command_rejects_task_id_before_mutating_state(self) -> None:
        state = self._state()
        before = json.dumps(state, sort_keys=True)
        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}):
            with self.assertRaises(SystemExit):
                ai_status.command_blocker(state, ["ODP-ACTOR-REF-001", "message", self.TASK_ID])
        self.assertEqual(before, json.dumps(state, sort_keys=True))
        self.assertNotIn(self.TASK_ID, ai_status.KNOWN_AGENTS)

    def test_assign_rejects_prose_owner(self) -> None:
        state = self._state()
        with self.assertRaises(SystemExit):
            ai_status.command_assign(state, ["ODP-NEW-001", self.PROSE, "Codex2"])
        self.assertEqual(1, len(state["tasks"]))

    # -- corrupt state on disk stays readable, but never grows the roster ---

    def test_existing_prose_reference_does_not_become_a_durable_agent(self) -> None:
        state = self._state()
        state["blockers"].append(
            {
                "task_id": "ODP-ACTOR-REF-001",
                "owner": "Claude",
                "waiting_for": self.PROSE,
                "message": "recorded before validation existed",
                "status": "open",
                "created_at": "2026-07-29T00:00:00Z",
            }
        )
        ai_status.validate_state(state)
        ai_status.recompute_agents(state)
        ai_status.recompute_workload(state)

        roster = [agent["name"] for agent in state["agents"]]
        self.assertNotIn(self.PROSE, roster)
        self.assertNotIn(self.PROSE, state["workload"])
        self.assertIn("Claude", roster)

    def test_invalid_actor_references_are_reported(self) -> None:
        state = self._state()
        state["tasks"][0]["waiting_for"] = self.TASK_ID
        findings = ai_status.invalid_actor_references(state)
        self.assertEqual(1, len(findings))
        location, value, problem = findings[0]
        self.assertIn("waiting_for", location)
        self.assertEqual(self.TASK_ID, value)
        self.assertIn("task id", problem)

    # -- audited repair path ------------------------------------------------

    def test_retarget_blocker_repairs_reference_and_keeps_original_text(self) -> None:
        state = self._state()
        state["tasks"][0]["status"] = "blocked"
        state["tasks"][0]["waiting_for"] = self.PROSE
        state["blockers"].append(
            {
                "task_id": "ODP-ACTOR-REF-001",
                "owner": "Claude",
                "waiting_for": self.PROSE,
                "message": "rollout gate",
                "status": "open",
                "created_at": "2026-07-29T00:00:00Z",
            }
        )
        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}), \
                mock.patch.object(ai_status, "append_log") as logged:
            ai_status.command_retarget_blocker(
                state, ["ODP-ACTOR-REF-001", "Codex2", "reviewer owns the gate"]
            )

        blocker = state["blockers"][0]
        self.assertEqual("Codex2", blocker["waiting_for"])
        self.assertEqual(self.PROSE, blocker["original_waiting_for"])
        self.assertIn(self.PROSE, blocker["message"])
        self.assertEqual("Claude", blocker["retargeted_by"])
        self.assertEqual("Codex2", state["tasks"][0]["waiting_for"])
        logged.assert_called_once()

    def test_retarget_blocker_rejects_a_prose_replacement(self) -> None:
        state = self._state()
        state["blockers"].append(
            {
                "task_id": "ODP-ACTOR-REF-001",
                "owner": "Claude",
                "waiting_for": self.PROSE,
                "message": "rollout gate",
                "status": "open",
                "created_at": "2026-07-29T00:00:00Z",
            }
        )
        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}):
            with self.assertRaises(SystemExit):
                ai_status.command_retarget_blocker(
                    state, ["ODP-ACTOR-REF-001", self.TASK_ID, "bad replacement"]
                )
        self.assertEqual(self.PROSE, state["blockers"][0]["waiting_for"])

    def test_retarget_blocker_leaves_another_owners_valid_blocker_alone(self) -> None:
        state = self._state()
        state["blockers"].append(
            {
                "task_id": "ODP-ACTOR-REF-001",
                "owner": "Codex2",
                "waiting_for": "Antigravity3",
                "message": "valid blocker owned by someone else",
                "status": "open",
                "created_at": "2026-07-29T00:00:00Z",
            }
        )
        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}), \
                mock.patch.object(ai_status, "append_log"):
            ai_status.command_retarget_blocker(
                state, ["ODP-ACTOR-REF-001", "Claude2", "attempted reassignment"]
            )
        self.assertEqual("Antigravity3", state["blockers"][0]["waiting_for"])

    # -- audited cleanup ----------------------------------------------------

    def _roster_state_for_prune(self) -> dict:
        state = self._state()
        for name in (self.TASK_ID, "Codex77", "Nessie9"):
            state["agents"].append(
                {
                    "name": name,
                    "capability_lane": [],
                    "status": "idle",
                    "current_task_ids": [],
                    "branch": "",
                    "next": "",
                    "last_update": None,
                }
            )
        state["handoffs"].append(
            {
                "task_id": "ODP-ACTOR-REF-001",
                "from": "Nessie9",
                "to": "Claude",
                "message": "still referenced",
                "status": "done",
                "created_at": "2026-07-29T00:00:00Z",
            }
        )
        return state

    def test_prune_is_dry_run_by_default(self) -> None:
        state = self._roster_state_for_prune()
        before = [agent["name"] for agent in state["agents"]]
        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}), \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            ai_status.command_prune_agents(state, [])
        self.assertEqual(before, [agent["name"] for agent in state["agents"]])

    def test_prune_removes_only_unreferenced_synthetic_entries(self) -> None:
        state = self._roster_state_for_prune()
        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}), \
                mock.patch.object(ai_status, "append_log") as logged, \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            ai_status.command_prune_agents(state, ["--apply", "cleanup"])

        roster = [agent["name"] for agent in state["agents"]]
        # Removed: the task-id entry and the undeclared, unreferenced Codex77.
        self.assertNotIn(self.TASK_ID, roster)
        self.assertNotIn("Codex77", roster)
        # Kept: configured agents and the undeclared-but-referenced Nessie9.
        self.assertIn("Claude", roster)
        self.assertIn("Codex2", roster)
        self.assertIn("Nessie9", roster)
        self.assertEqual(2, logged.call_count)

    def test_prune_never_removes_a_configured_agent_that_is_idle(self) -> None:
        state = self._state()
        state["agents"].append(
            {
                "name": "Codex5",
                "capability_lane": [],
                "status": "idle",
                "current_task_ids": [],
                "branch": "",
                "next": "",
                "last_update": None,
            }
        )
        report = {entry["name"]: entry for entry in ai_status.synthetic_roster_entries(state)}
        self.assertTrue(report["Codex5"]["keep"])
        self.assertIn("config.local.json", report["Codex5"]["reason"])

    def test_prune_does_not_advertise_static_lanes_as_removable(self) -> None:
        """Gemini/Copilot are not registered actors, but pruning them is a no-op.

        `recompute_agents()` recreates a roster row for every static
        `KNOWN_AGENTS` lane, so reporting them as removable would churn the
        roster on every sync and misreport the result.
        """
        state = self._state()
        state["agents"].append(
            {
                "name": "Gemini2",
                "capability_lane": [],
                "status": "idle",
                "current_task_ids": [],
                "branch": "",
                "next": "",
                "last_update": None,
            }
        )
        report = {entry["name"]: entry for entry in ai_status.synthetic_roster_entries(state)}
        self.assertTrue(report["Gemini2"]["keep"])
        self.assertIn("no-op", report["Gemini2"]["reason"])
        # Being kept in the roster is not the same as being a usable actor.
        with self.assertRaises(SystemExit):
            ai_status.resolve_actor_reference("Gemini2", field="owner")

    def test_prune_keeps_an_undeclared_agent_that_is_carrying_work(self) -> None:
        """Cleanup is for dead synthetic entries, never for a busy actor."""
        state = self._state()
        state["agents"].append(
            {
                "name": "Nessie9",
                "capability_lane": [],
                "status": "working",
                "current_task_ids": ["ODP-ACTOR-REF-001"],
                "branch": "",
                "next": "",
                "last_update": None,
            }
        )
        report = {entry["name"]: entry for entry in ai_status.synthetic_roster_entries(state)}
        self.assertTrue(report["Nessie9"]["keep"])
        self.assertIn("live workload", report["Nessie9"]["reason"])

    def test_prune_never_removes_a_config_declared_agent(self) -> None:
        state = self._state()
        state["agents"].append(
            {
                "name": "Claude3",
                "capability_lane": [],
                "status": "idle",
                "current_task_ids": [],
                "branch": "",
                "next": "",
                "last_update": None,
            }
        )
        with mock.patch.object(ai_status, "configured_agent_names", return_value={"Claude3"}):
            report = {entry["name"]: entry for entry in ai_status.synthetic_roster_entries(state)}
        self.assertTrue(report["Claude3"]["keep"])
        self.assertIn("config.json", report["Claude3"]["reason"])


class MergedConfigActorAuthorityTests(unittest.TestCase):
    """Actor authority must be the config the Supervisor actually dispatches from.

    `common.load_config()` deep-merges `.orchestrator/config.json` with the
    gitignored `.orchestrator/config.local.json`. On this fleet the overlay is
    the *only* place Codex3-Codex9 are declared, so reading the tracked half
    alone would classify six live workers as synthetic.
    """

    BASE_AGENTS = {
        "claude": {"display_name": "Claude", "provider": "claude"},
        "codex2": {"display_name": "Codex2", "provider": "codex"},
    }
    OVERLAY_AGENTS = {
        f"codex{index}": {"display_name": f"Codex{index}", "provider": "codex"}
        for index in range(3, 10)
    }

    def setUp(self) -> None:
        ai_status._MERGED_CONFIG_CACHE.clear()
        self.addCleanup(ai_status._MERGED_CONFIG_CACHE.clear)
        self._known_before = dict(ai_status.KNOWN_AGENTS)
        self.addCleanup(self._restore_roster)

    def _restore_roster(self) -> None:
        ai_status.KNOWN_AGENTS.clear()
        ai_status.KNOWN_AGENTS.update(self._known_before)

    def _write_config(self, directory: Path, payload: dict, name: str = "config.json") -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_local_overlay_workers_are_declared_and_match_common_deep_merge(self) -> None:
        import common

        with tempfile.TemporaryDirectory(prefix="ai-status-merged-config-") as temp_dir:
            orchestrator_dir = Path(temp_dir) / ".orchestrator"
            config_file = self._write_config(orchestrator_dir, {"agents": self.BASE_AGENTS})
            self._write_config(
                orchestrator_dir, {"agents": self.OVERLAY_AGENTS}, name="config.local.json"
            )
            missing_status_overlay = Path(temp_dir) / "status" / ".orchestrator" / "config.local.json"

            with (
                mock.patch.object(ai_status, "CONFIG_FILE", config_file),
                mock.patch.object(
                    ai_status, "STATUS_ROOT_CONFIG_LOCAL_FILE", missing_status_overlay
                ),
            ):
                merged = ai_status.merged_orchestrator_config()
                names = ai_status.configured_agent_names()
                accepted = [
                    ai_status.resolve_actor_reference(name, field="owner")
                    for name in ("Codex5", "Codex6", "Codex8", "Codex9")
                ]
                # Case-folding must land on the declared spelling, not a new name.
                folded = ai_status.canonical_agent_name("codex5")

        expected = common.deep_merge({"agents": self.BASE_AGENTS}, {"agents": self.OVERLAY_AGENTS})
        self.assertEqual(expected, merged)
        self.assertEqual({"Claude", "Codex2"} | {f"Codex{i}" for i in range(3, 10)}, names)
        self.assertEqual(["Codex5", "Codex6", "Codex8", "Codex9"], accepted)
        self.assertEqual("Codex5", folded)

    def test_without_the_overlay_the_same_workers_are_not_declared(self) -> None:
        """Proves the overlay — not a static table — is what admits Codex3-Codex9."""
        with tempfile.TemporaryDirectory(prefix="ai-status-base-only-") as temp_dir:
            orchestrator_dir = Path(temp_dir) / ".orchestrator"
            config_file = self._write_config(orchestrator_dir, {"agents": self.BASE_AGENTS})
            missing_status_overlay = Path(temp_dir) / "status" / ".orchestrator" / "config.local.json"

            with (
                mock.patch.object(ai_status, "CONFIG_FILE", config_file),
                mock.patch.object(
                    ai_status, "STATUS_ROOT_CONFIG_LOCAL_FILE", missing_status_overlay
                ),
            ):
                self.assertEqual({"Claude", "Codex2"}, ai_status.configured_agent_names())
                with self.assertRaises(SystemExit):
                    ai_status.resolve_actor_reference("Codex5", field="owner")

    def test_status_root_overlay_covers_worker_worktrees(self) -> None:
        """A worktree only checks out the tracked half of the config.

        Workers run `ai_status.py` from a worktree while writing to the live
        status root, so the live overlay has to be merged from there too or the
        same command would reject Codex5 in a worktree and accept it at home.
        """
        with tempfile.TemporaryDirectory(prefix="ai-status-worktree-config-") as temp_dir:
            worktree_dir = Path(temp_dir) / "worktree" / ".orchestrator"
            config_file = self._write_config(worktree_dir, {"agents": self.BASE_AGENTS})
            live_dir = Path(temp_dir) / "live" / ".orchestrator"
            status_overlay = self._write_config(
                live_dir, {"agents": self.OVERLAY_AGENTS}, name="config.local.json"
            )

            with (
                mock.patch.object(ai_status, "CONFIG_FILE", config_file),
                mock.patch.object(ai_status, "STATUS_ROOT_CONFIG_LOCAL_FILE", status_overlay),
            ):
                self.assertIn("Codex7", ai_status.configured_agent_names())
                self.assertEqual(
                    "Codex7", ai_status.resolve_actor_reference("Codex7", field="owner")
                )

    def test_live_path_delegates_to_common_load_config(self) -> None:
        """When pointed at the real config path, use Supervisor's loader verbatim."""
        import common

        self.assertEqual(common.DEFAULT_CONFIG_PATH, ai_status.CONFIG_FILE)
        with mock.patch.object(
            common, "load_config", return_value={"agents": {"nessie": {"display_name": "Nessie9"}}}
        ) as load_config:
            ai_status._MERGED_CONFIG_CACHE.clear()
            names = ai_status.configured_agent_names()
        load_config.assert_called_once_with()
        self.assertIn("Nessie9", names)

    def test_codex3_is_its_own_worker_not_an_alias_of_codex(self) -> None:
        """The retired `codex3 -> Codex` alias would silently reassign a real worker."""
        self.assertNotIn("codex3", ai_status.AGENT_ALIASES)
        with mock.patch.object(
            ai_status, "configured_agent_names", return_value={"Codex", "Codex3"}
        ):
            self.assertEqual("Codex3", ai_status.canonical_agent_name("codex3"))
            self.assertEqual(
                "Codex3", ai_status.resolve_actor_reference("Codex3", field="owner")
            )


class ActorCommandMutationGuardTests(unittest.TestCase):
    """Every mutating actor command must fail *before* it touches durable state."""

    PROSE = (
        "STOP FOR CODEX2 REVIEW ON EXACT HEAD 6ca4726c (revision 8). Two gates are "
        "open at once and both need Codex2/coordinator action, not more owner work."
    )
    TASK_ID = "ODP-ACTOR-REF-001"

    def setUp(self) -> None:
        self._known_before = dict(ai_status.KNOWN_AGENTS)
        self._quarantined_before = set(ai_status.QUARANTINED_AGENTS)
        self.addCleanup(self._restore_roster)
        patcher = mock.patch.object(
            ai_status, "configured_agent_names", return_value={"Claude", "Codex2"}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        # A real durable write would be a test failure, not a fixture.
        log_patcher = mock.patch.object(ai_status, "append_log")
        self.append_log = log_patcher.start()
        self.addCleanup(log_patcher.stop)

    def _restore_roster(self) -> None:
        ai_status.KNOWN_AGENTS.clear()
        ai_status.KNOWN_AGENTS.update(self._known_before)
        ai_status.QUARANTINED_AGENTS.clear()
        ai_status.QUARANTINED_AGENTS.update(self._quarantined_before)

    def _state(self) -> dict:
        return {
            "agents": [
                {
                    "name": name,
                    "capability_lane": [],
                    "status": "idle",
                    "current_task_ids": [],
                    "branch": "",
                    "next": "",
                    "last_update": None,
                }
                for name in ("Claude", "Codex2")
            ],
            "tasks": [
                {
                    "id": self.TASK_ID,
                    "title": "Actor reference fixture",
                    "phase": "Fleet Control Plane Integrity",
                    "owner": "Claude",
                    "reviewer": "Codex2",
                    "status": "in_progress",
                    "depends_on": [],
                    "artifacts": [],
                    "acceptance": [],
                    "next": "",
                    "last_update": "2026-07-29T00:00:00Z",
                }
            ],
            "handoffs": [],
            "blockers": [
                {
                    "task_id": self.TASK_ID,
                    "owner": "Claude",
                    "waiting_for": self.PROSE,
                    "message": "recorded before validation existed",
                    "status": "open",
                    "created_at": "2026-07-29T00:00:00Z",
                }
            ],
            "workload": {},
            "workload_summary": {},
        }

    def _assert_rejected_without_mutation(self, command, args, env) -> None:
        state = self._state()
        before = json.dumps(state, sort_keys=True)
        roster_before = set(ai_status.KNOWN_AGENTS)
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch("sys.stdout", new_callable=io.StringIO):
                with self.assertRaises(SystemExit):
                    command(state, args)
        self.assertEqual(before, json.dumps(state, sort_keys=True))
        self.assertEqual(roster_before, set(ai_status.KNOWN_AGENTS))
        self.append_log.assert_not_called()

    def test_bad_actor_argument_is_rejected_by_every_mutating_command(self) -> None:
        cases = {
            "assign owner": (
                ai_status.command_assign,
                ["ODP-NEW-001", self.PROSE, "Codex2"],
                {"AI_NAME": "Claude"},
            ),
            "assign reviewer": (
                ai_status.command_assign,
                ["ODP-NEW-001", "Claude", "ODP-P10-FLEET-CONFLICT-REAUDIT-001"],
                {"AI_NAME": "Claude"},
            ),
            "handoff target": (
                ai_status.command_handoff,
                [self.TASK_ID, self.PROSE, "please review"],
                {"AI_NAME": "Claude"},
            ),
            "blocker waiting-for": (
                ai_status.command_blocker,
                [self.TASK_ID, "blocked on the gate", self.PROSE],
                {"AI_NAME": "Claude"},
            ),
            "retarget_blocker target": (
                ai_status.command_retarget_blocker,
                [self.TASK_ID, self.PROSE, "repair"],
                {"AI_NAME": "Claude"},
            ),
        }
        for label, (command, args, env) in cases.items():
            with self.subTest(command=label):
                self._assert_rejected_without_mutation(command, args, env)

    def test_unregistered_actor_argument_is_rejected_by_every_mutating_command(self) -> None:
        cases = {
            "assign owner": (
                ai_status.command_assign,
                ["ODP-NEW-001", "Nessie9", "Codex2"],
                {"AI_NAME": "Claude"},
            ),
            "handoff target": (
                ai_status.command_handoff,
                [self.TASK_ID, "Nessie9", "please review"],
                {"AI_NAME": "Claude"},
            ),
            "blocker waiting-for": (
                ai_status.command_blocker,
                [self.TASK_ID, "blocked on the gate", "Nessie9"],
                {"AI_NAME": "Claude"},
            ),
            "retarget_blocker target": (
                ai_status.command_retarget_blocker,
                [self.TASK_ID, "Nessie9", "repair"],
                {"AI_NAME": "Claude"},
            ),
        }
        for label, (command, args, env) in cases.items():
            with self.subTest(command=label):
                self._assert_rejected_without_mutation(command, args, env)

    # Every mutating command that records an actor, with arguments that are
    # valid enough to get past its usage check — so the only thing that can
    # reject the call is the AI_NAME gate itself.
    AI_NAME_CASES = {
        "assign": ["ODP-NEW-001", "Claude", "Codex2"],
        "start": [TASK_ID, "starting"],
        "progress": [TASK_ID, "still going"],
        "note": [TASK_ID, "a note"],
        "reopen": [TASK_ID, "reopening"],
        "re_review": [TASK_ID, "re-reviewing"],
        "re-review": [TASK_ID, "re-reviewing"],
        "submit_review": [TASK_ID, "123", "submit for review"],
        "handoff": [TASK_ID, "Codex2", "please review"],
        "blocker": [TASK_ID, "blocked", "Codex2"],
        "retarget_blocker": [TASK_ID, "Codex2", "repair"],
        "prune_agents": ["--apply", "cleanup"],
        "restore_approved": [TASK_ID, "restoring"],
        "restore_approved_head": [TASK_ID, "1111111122222222333333334444444455555555", "attesting"],
        "done": [TASK_ID, "finished"],
        "supersede": [TASK_ID, "superseded"],
        "approve": [TASK_ID, "approved"],
        "archive_migrate": [],
        "wave open": ["open", "W-2026-07-29"],
        "wave close": ["close"],
    }

    def test_ai_name_case_table_covers_every_actor_bearing_command(self) -> None:
        """The table below is the contract; this keeps it from silently rotting.

        A new mutating command must either appear here or be declared actorless
        on purpose. Without this check the table stays green while the surface
        it claims to cover grows past it — which is exactly how the eleven
        unvalidated call sites survived the first pass.
        """
        covered = {label.split()[0] for label in self.AI_NAME_CASES}
        expected = set(ai_status.MUTATING_COMMANDS) - ai_status.ACTORLESS_MUTATING_COMMANDS
        self.assertEqual(expected, covered)
        self.assertEqual({"sync"}, set(ai_status.ACTORLESS_MUTATING_COMMANDS))

    def test_bad_ai_name_is_rejected_by_every_mutating_command(self) -> None:
        """Malformed prose and a well-shaped but unregistered name both fail closed.

        `Nessie9` matters as much as the prose case: it passes every shape check
        and would have been invented as a roster entry by the old tolerant path.
        """
        for label, args in self.AI_NAME_CASES.items():
            command = ai_status.MUTATING_COMMANDS[label.split()[0]]
            for bad_name in (self.PROSE, "Nessie9"):
                with self.subTest(command=label, ai_name=bad_name[:24]):
                    self._assert_rejected_without_mutation(command, args, {"AI_NAME": bad_name})

    def test_no_unvalidated_actor_read_remains(self) -> None:
        """The unvalidated `current_actor()` helper must stay deleted.

        Codex2 found the eleven gaps by scanning for this call; encoding the
        scan means the next one is caught by CI instead of by a reviewer.
        """
        tree = ast.parse(Path(ai_status.__file__).read_text(encoding="utf-8"))
        offenders = [
            f"line {node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == "current_actor"
        ]
        self.assertEqual([], offenders)
        self.assertFalse(hasattr(ai_status, "current_actor"))


class HistoricalClosemergeProvenanceTests(unittest.TestCase):
    """Closeout of work that merged before its evidence was cleaned up.

    Every fixture here is taken from live state on 2026-08-09, when the
    finalize lane held tasks whose work had demonstrably landed on ``dev``
    yet could not be finalized. The point of these tests is that the two
    reasons were reading defects, not missing delivery -- and that removing
    them does not create a way to finalize something unmerged.
    """

    TASK_ID = "ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001"
    BRANCH = f"task/{TASK_ID}"
    APPROVED_HEAD = "cc560e00ee5f268dc595150e1221c7f15b86ffa1"
    DEV_MERGE_COMMIT = "8227d0d923bc2b456970e6af2a2ccd809e6bd6cb"
    MAIN_MERGE_COMMIT = "574dde52b56992b5088aedc74332e2e90fb40b44"
    REPOSITORY = "alfloop-dev/odayplus"

    def pr_575_into_dev(self) -> dict[str, object]:
        """The task PR. Its rollup still carries the ``product`` run that failed."""

        return {
            "number": 575,
            "state": "MERGED",
            "headRefOid": self.APPROVED_HEAD,
            "headRefName": self.BRANCH,
            "baseRefName": "dev",
            "mergedAt": "2026-08-07T01:26:54Z",
            "mergeCommit": {"oid": self.DEV_MERGE_COMMIT},
            "url": "https://github.com/alfloop-dev/odayplus/pull/575",
            "statusCheckRollup": [
                {
                    "__typename": "CheckRun",
                    "name": "product",
                    "workflowName": "CI",
                    "status": "COMPLETED",
                    "conclusion": "FAILURE",
                    "startedAt": "2026-08-04T06:21:34Z",
                    "completedAt": "2026-08-04T06:31:02Z",
                },
                {
                    "__typename": "CheckRun",
                    "name": "product",
                    "workflowName": "CI",
                    "status": "COMPLETED",
                    "conclusion": "SUCCESS",
                    "startedAt": "2026-08-06T22:10:04Z",
                    "completedAt": "2026-08-06T22:19:41Z",
                },
                {
                    "__typename": "CheckRun",
                    "name": "orchestrator",
                    "workflowName": "CI",
                    "status": "COMPLETED",
                    "conclusion": "SUCCESS",
                    "startedAt": "2026-08-06T22:10:04Z",
                    "completedAt": "2026-08-06T22:12:27Z",
                },
                {
                    "__typename": "StatusContext",
                    "context": "task-review-gate",
                    "state": "SUCCESS",
                    "startedAt": "2026-08-06T22:24:34Z",
                },
            ],
        }

    def pr_617_into_main(self) -> dict[str, object]:
        """The ReviewBus PR: same branch, same head, merged later, wrong base."""

        return {
            "number": 617,
            "state": "MERGED",
            "headRefOid": self.APPROVED_HEAD,
            "headRefName": self.BRANCH,
            "baseRefName": "main",
            "mergedAt": "2026-08-07T12:25:14Z",
            "mergeCommit": {"oid": self.MAIN_MERGE_COMMIT},
            "url": "https://github.com/alfloop-dev/odayplus/pull/617",
            "statusCheckRollup": [],
        }

    # -- stale rollup ----------------------------------------------------

    def test_rerun_supersedes_the_earlier_red_run_of_the_same_check(self) -> None:
        latest, superseded = ai_status.latest_status_check_runs(
            self.pr_575_into_dev()["statusCheckRollup"]
        )

        self.assertEqual(
            [(c["name"] if "name" in c else c["context"], c.get("conclusion") or c.get("state")) for c in latest],
            [("product", "SUCCESS"), ("orchestrator", "SUCCESS"), ("task-review-gate", "SUCCESS")],
        )
        self.assertEqual([c["conclusion"] for c in superseded], ["FAILURE"])

    def test_stale_red_run_no_longer_blocks_a_merged_pr_but_stays_on_record(self) -> None:
        checks = ai_status.normalized_green_pr_checks(self.pr_575_into_dev())

        by_name = [(check["name"], check["conclusion"], check.get("superseded", False)) for check in checks]
        self.assertIn(("product", "SUCCESS", False), by_name)
        self.assertIn(("product", "FAILURE", True), by_name)

    def test_latest_run_still_decides_when_it_is_the_red_one(self) -> None:
        """Collapsing to the newest run is not a way to bury a current failure."""

        pr_status = self.pr_575_into_dev()
        rollup = pr_status["statusCheckRollup"]
        rollup[1]["conclusion"] = "FAILURE"
        rollup[0]["conclusion"] = "SUCCESS"

        with self.assertRaisesRegex(SystemExit, r"CI is not green.*product"):
            ai_status.normalized_green_pr_checks(pr_status)

    def test_untimestamped_runs_fall_back_to_rollup_order(self) -> None:
        rollup = [
            {"__typename": "CheckRun", "name": "product", "status": "COMPLETED", "conclusion": "SUCCESS"},
            {"__typename": "CheckRun", "name": "product", "status": "COMPLETED", "conclusion": "FAILURE"},
        ]
        latest, superseded = ai_status.latest_status_check_runs(rollup)

        self.assertEqual([c["conclusion"] for c in latest], ["FAILURE"])
        self.assertEqual([c["conclusion"] for c in superseded], ["SUCCESS"])

    def test_a_timestamped_run_outranks_one_with_no_timestamps(self) -> None:
        rollup = [
            {
                "__typename": "CheckRun",
                "name": "product",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
                "completedAt": "2026-08-06T22:19:41Z",
            },
            {"__typename": "CheckRun", "name": "product", "status": "COMPLETED", "conclusion": "FAILURE"},
        ]
        latest, _ = ai_status.latest_status_check_runs(rollup)

        self.assertEqual([c["conclusion"] for c in latest], ["SUCCESS"])

    def test_re_run_of_a_different_workflow_is_not_treated_as_the_same_check(self) -> None:
        rollup = [
            {
                "__typename": "CheckRun",
                "name": "product",
                "workflowName": "CI",
                "status": "COMPLETED",
                "conclusion": "FAILURE",
                "completedAt": "2026-08-06T22:19:41Z",
            },
            {
                "__typename": "CheckRun",
                "name": "product",
                "workflowName": "Nightly",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
                "completedAt": "2026-08-07T22:19:41Z",
            },
        ]
        with self.assertRaisesRegex(SystemExit, "CI is not green"):
            ai_status.normalized_green_pr_checks({"statusCheckRollup": rollup})

    # -- zero-timestamp sentinel -----------------------------------------

    def rerun_in_progress_over_an_older_success(self) -> list[dict[str, object]]:
        """The `gh pr view --json statusCheckRollup` shape for a running re-run.

        Verbatim field set, including the two details `gh` emits that a
        hand-written fixture omits: `conclusion` is the empty string rather than
        absent, and `completedAt` is Go's zero time rather than absent.
        """

        return [
            {
                "__typename": "CheckRun",
                "name": "product",
                "workflowName": "CI",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
                "startedAt": "2026-08-06T22:10:04Z",
                "completedAt": "2026-08-06T22:19:41Z",
                "detailsUrl": "https://github.com/alfloop-dev/odayplus/actions/runs/1/job/1",
            },
            {
                "__typename": "CheckRun",
                "name": "product",
                "workflowName": "CI",
                "status": "IN_PROGRESS",
                "conclusion": "",
                "startedAt": "2026-08-09T13:02:11Z",
                "completedAt": "0001-01-01T00:00:00Z",
                "detailsUrl": "https://github.com/alfloop-dev/odayplus/actions/runs/2/job/2",
            },
        ]

    def test_a_running_rerun_supersedes_the_success_it_was_started_to_replace(self) -> None:
        """The zero `completedAt` must not sort the running re-run into the past."""

        latest, superseded = ai_status.latest_status_check_runs(
            self.rerun_in_progress_over_an_older_success()
        )

        self.assertEqual([check["status"] for check in latest], ["IN_PROGRESS"])
        self.assertEqual([check["conclusion"] for check in superseded], ["SUCCESS"])

    def test_an_older_success_cannot_green_a_pr_whose_rerun_is_still_running(self) -> None:
        """Fail closed: unfinished is not green, however old the passing run is."""

        with self.assertRaisesRegex(SystemExit, r"CI is not green.*pending checks: product"):
            ai_status.normalized_green_pr_checks(
                {"statusCheckRollup": self.rerun_in_progress_over_an_older_success()}
            )

    def test_zero_completed_at_falls_back_to_started_at_not_to_no_timestamp(self) -> None:
        """A sentinel means "unfinished", so the entry still ranks by its start."""

        self.assertEqual(
            ai_status.status_check_timestamp(
                {"startedAt": "2026-08-09T13:02:11Z", "completedAt": "0001-01-01T00:00:00Z"}
            ),
            "2026-08-09T13:02:11Z",
        )
        self.assertEqual(
            ai_status.status_check_timestamp(
                {"startedAt": "0001-01-01T00:00:00Z", "completedAt": "0001-01-01T00:00:00Z"}
            ),
            "",
        )

    def test_a_queued_rerun_with_no_real_timestamp_still_wins_on_rollup_order(self) -> None:
        """`gh` zeroes both stamps on a queued run; order is then all that is left.

        GitHub appends re-runs, so the later entry is the newer one -- and it is
        unfinished, which must read as pending rather than as the earlier pass.
        """

        rollup = [
            {
                "__typename": "CheckRun",
                "name": "product",
                "workflowName": "CI",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
                "startedAt": "0001-01-01T00:00:00Z",
                "completedAt": "0001-01-01T00:00:00Z",
            },
            {
                "__typename": "CheckRun",
                "name": "product",
                "workflowName": "CI",
                "status": "QUEUED",
                "conclusion": "",
                "startedAt": "0001-01-01T00:00:00Z",
                "completedAt": "0001-01-01T00:00:00Z",
            },
        ]

        latest, _ = ai_status.latest_status_check_runs(rollup)

        self.assertEqual([check["status"] for check in latest], ["QUEUED"])

    # -- provenance selection --------------------------------------------

    def test_dev_merge_is_selected_over_the_later_main_merge(self) -> None:
        selected = ai_status.select_merged_pull_request(
            [self.pr_617_into_main(), self.pr_575_into_dev()],
            branch=self.BRANCH,
            base_branch="dev",
            head_sha=self.APPROVED_HEAD,
        )

        self.assertEqual(selected["number"], 575)

    def test_no_candidate_matches_when_the_approved_head_never_merged(self) -> None:
        """ODP-ORCH-REBASE-HEAD-LIVENESS-001: merged, but at an earlier head."""

        merged_at_other_head = self.pr_575_into_dev()
        merged_at_other_head["headRefOid"] = "cdc5e5b68590a6b864455cadc9e1d12660876cbf"

        self.assertIsNone(
            ai_status.select_merged_pull_request(
                [merged_at_other_head],
                branch=self.BRANCH,
                base_branch="dev",
                head_sha=self.APPROVED_HEAD,
            )
        )

    def test_selection_rejects_open_wrong_base_and_wrong_branch_candidates(self) -> None:
        cases = {
            "open": {"state": "OPEN"},
            "wrong-base": {"baseRefName": "main"},
            "wrong-branch": {"headRefName": "task/OTHER-001"},
            "no-merge-time": {"mergedAt": ""},
            "no-merge-commit": {"mergeCommit": None},
        }
        for label, mutation in cases.items():
            with self.subTest(label=label):
                candidate = self.pr_575_into_dev()
                candidate.update(mutation)
                self.assertIsNone(
                    ai_status.select_merged_pull_request(
                        [candidate],
                        branch=self.BRANCH,
                        base_branch="dev",
                        head_sha=self.APPROVED_HEAD,
                    )
                )

    def test_two_merge_commits_for_one_head_are_ambiguous_and_fail_closed(self) -> None:
        duplicate = self.pr_575_into_dev()
        duplicate["number"] = 999
        duplicate["mergeCommit"] = {"oid": "f" * 40}

        with self.assertRaisesRegex(SystemExit, "ambiguous merge provenance"):
            ai_status.select_merged_pull_request(
                [self.pr_575_into_dev(), duplicate],
                branch=self.BRANCH,
                base_branch="dev",
                head_sha=self.APPROVED_HEAD,
            )

    def test_lookup_asks_for_every_pr_on_the_branch_before_choosing(self) -> None:
        calls: list[list[str]] = []

        def fake_list(args: list[str], **kwargs: object) -> list[dict[str, object]]:
            calls.append(args)
            return [self.pr_617_into_main(), self.pr_575_into_dev()]

        with (
            mock.patch.object(ai_status, "run_gh_json_list_command", side_effect=fake_list),
            mock.patch.object(ai_status, "run_gh_json_command", return_value=self.pr_617_into_main()),
        ):
            pr_status = ai_status.pull_request_status_for_branch(
                Path("/repo"),
                self.BRANCH,
                self.REPOSITORY,
                base_branch="dev",
                head_sha=self.APPROVED_HEAD,
            )

        self.assertEqual(pr_status["number"], 575)
        self.assertIn("--state", calls[0])
        self.assertIn("all", calls[0])

    def test_lookup_without_a_target_keeps_the_historical_recency_behaviour(self) -> None:
        with (
            mock.patch.object(ai_status, "run_gh_json_list_command", return_value=[]),
            mock.patch.object(ai_status, "run_gh_json_command", return_value=self.pr_617_into_main()),
        ):
            pr_status = ai_status.pull_request_status_for_branch(
                Path("/repo"), self.BRANCH, self.REPOSITORY
            )

        self.assertEqual(pr_status["number"], 617)

    # -- absent delivery checkout ----------------------------------------

    def worktree_listing(self, *, include_task: bool) -> str:
        listing = (
            "worktree /home/lupin/oday-plus-supervisor-live\n"
            "HEAD e496be62c47c45d758681b8a4d3abfae16f1c96d\n"
            "branch refs/heads/dev\n\n"
        )
        if include_task:
            listing += (
                "worktree /tmp/task-owned-checkout\n"
                f"HEAD {self.APPROVED_HEAD}\n"
                f"branch refs/heads/{self.BRANCH}\n\n"
            )
        return listing

    def resolve_with_worktrees(self, listing: str) -> dict[str, object]:
        def fake_git(args: list[str], **kwargs: object) -> str:
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return "dev"
            if args == ["worktree", "list", "--porcelain"]:
                return listing
            raise AssertionError(f"unexpected git command: {args}")

        with mock.patch.object(ai_status, "run_git_command", side_effect=fake_git):
            return ai_status.resolve_task_delivery_checkout(
                Path("/home/lupin/oday-plus-supervisor-live"), self.TASK_ID
            )

    def test_absent_checkout_is_reported_not_raised(self) -> None:
        resolved = self.resolve_with_worktrees(self.worktree_listing(include_task=False))

        self.assertFalse(resolved["present"])
        self.assertEqual(resolved["branch"], self.BRANCH)
        self.assertEqual(resolved["checkout"], Path("/home/lupin/oday-plus-supervisor-live"))

    def test_two_task_checkouts_remain_ambiguous_and_fail_closed(self) -> None:
        listing = self.worktree_listing(include_task=True) + (
            "worktree /tmp/second-task-checkout\n"
            f"HEAD {self.APPROVED_HEAD}\n"
            f"branch refs/heads/{self.BRANCH}\n\n"
        )
        with self.assertRaisesRegex(SystemExit, "expected exactly one task-owned delivery"):
            self.resolve_with_worktrees(listing)

    def test_legacy_helper_still_raises_on_an_absent_checkout(self) -> None:
        """`task_delivery_checkout` keeps its contract for callers that need one."""

        def fake_git(args: list[str], **kwargs: object) -> str:
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return "dev"
            if args == ["worktree", "list", "--porcelain"]:
                return self.worktree_listing(include_task=False)
            raise AssertionError(f"unexpected git command: {args}")

        with mock.patch.object(ai_status, "run_git_command", side_effect=fake_git):
            with self.assertRaisesRegex(SystemExit, "found 0"):
                ai_status.task_delivery_checkout(Path("/home/lupin/oday-plus-supervisor-live"), self.TASK_ID)

    def task(self) -> dict[str, object]:
        return {
            "id": self.TASK_ID,
            "owner": "Antigravity2",
            "reviewer": "Codex2",
            "status": "review_approved",
            "approved_head": self.APPROVED_HEAD,
            "artifacts": [],
        }

    def collect_without_checkout(
        self,
        *,
        approved_head_present: bool = True,
        pr_status: dict[str, object] | None = None,
    ) -> dict[str, object]:
        def fake_git(args: list[str], **kwargs: object) -> str:
            responses = {
                ("show", "-s", "--format=%s", self.APPROVED_HEAD): f"{self.TASK_ID}: land base advance",
                ("show", "-s", "--format=%b", self.APPROVED_HEAD): (
                    f"LLM-Agent: Antigravity2\nTask-ID: {self.TASK_ID}\nReviewer: Codex2\n"
                ),
                ("show", "-s", "--format=%an", self.APPROVED_HEAD): "Antigravity2",
                ("show", "-s", "--format=%ae", self.APPROVED_HEAD): "antigravity2@example.com",
                ("remote",): "origin",
                ("fetch", "origin", "dev"): "",
                ("rev-parse", "--verify", "origin/dev"): "dev-tip",
            }
            key = tuple(args)
            if key not in responses:
                raise AssertionError(f"unexpected git command: {args}")
            return responses[key]

        def fake_succeeds(args: list[str], **kwargs: object) -> bool:
            if args == ["cat-file", "-e", f"{self.APPROVED_HEAD}^{{commit}}"]:
                return approved_head_present
            if args == ["merge-base", "--is-ancestor", self.APPROVED_HEAD, "origin/dev"]:
                return True
            if args == ["merge-base", "--is-ancestor", self.DEV_MERGE_COMMIT, "origin/dev"]:
                return True
            if args == ["merge-base", "--is-ancestor", self.MAIN_MERGE_COMMIT, "origin/dev"]:
                return False
            raise AssertionError(f"unexpected git check: {args}")

        with (
            mock.patch.object(
                ai_status,
                "resolve_task_delivery_checkout",
                return_value={
                    "checkout": Path("/home/lupin/oday-plus-supervisor-live"),
                    "branch": self.BRANCH,
                    "present": False,
                },
            ),
            mock.patch.object(ai_status, "run_git_command", side_effect=fake_git),
            mock.patch.object(ai_status, "git_command_succeeds", side_effect=fake_succeeds),
            mock.patch.object(
                ai_status,
                "pull_request_status_for_branch",
                return_value=self.pr_575_into_dev() if pr_status is None else pr_status,
            ),
            mock.patch.object(ai_status, "repository_slug", return_value=self.REPOSITORY),
            mock.patch.object(ai_status, "git_remote_repository_slug", return_value=self.REPOSITORY),
        ):
            return ai_status.collect_done_delivery_metadata(
                self.task(), "Antigravity2", approved_head=self.APPROVED_HEAD
            )

    def test_a_cleaned_up_checkout_finalizes_from_merged_pr_provenance_alone(self) -> None:
        delivery = self.collect_without_checkout()

        self.assertFalse(delivery["task_checkout_present"])
        self.assertEqual(delivery["provenance_mode"], "merged_pr_without_task_checkout")
        self.assertTrue(delivery["merge_verified_via_pr"])
        self.assertEqual(delivery["pull_request"]["number"], 575)
        self.assertEqual(delivery["verified_head"], self.APPROVED_HEAD)

    def test_absent_checkout_records_what_it_could_not_observe(self) -> None:
        """Skipped gates are reported as unevaluated, never as passed."""

        delivery = self.collect_without_checkout()

        self.assertIsNone(delivery["git_clean"])
        self.assertFalse(delivery["git_clean_evaluated"])
        self.assertEqual(delivery["push_status"], "no_task_checkout")
        self.assertIsNone(delivery["upstream"])

    def test_absent_checkout_fails_closed_when_the_approved_head_is_gone(self) -> None:
        with self.assertRaisesRegex(SystemExit, "is not present in"):
            self.collect_without_checkout(approved_head_present=False)

    def test_absent_checkout_fails_closed_when_the_pr_does_not_prove_delivery(self) -> None:
        with self.assertRaisesRegex(SystemExit, "immutable approved-head PR provenance"):
            self.collect_without_checkout(pr_status=self.pr_617_into_main())

    def test_absent_checkout_is_not_a_route_around_the_merged_pr_gate(self) -> None:
        """The one gate this path relies on cannot be switched off from the environment.

        With no checkout, the merged-PR gate is the *only* remaining evidence:
        the working-tree and push-status gates have nothing to read. A config
        that turned it off would leave the path verifying nothing at all.
        """

        disabled = {
            "TASK_REQUIRE_MERGED_PR": "false",
            "TASK_REQUIRE_GIT_CLEAN": "false",
            "TASK_REQUIRE_COMMIT_HASH": "false",
        }
        with mock.patch.dict(os.environ, disabled, clear=False):
            with self.assertRaisesRegex(SystemExit, "immutable approved-head PR provenance"):
                self.collect_without_checkout(pr_status=self.pr_617_into_main())


class StaleTaskCheckoutFinalizeTests(unittest.TestCase):
    """Closeout when a superseded checkout of the same task branch survives.

    A reassigned, restarted or helper-claimed lane can leave an older checkout of
    `task/<ID>` behind in the configured repository. Finalize reads the task
    checkout's HEAD as the task's delivery head, and only ever had a
    carry-forward path for a HEAD *ahead* of the approved commit. A HEAD behind
    it read as "the wrong head", so a task whose exact approved head had already
    merged could not be closed out at all.

    A behind checkout is history, not a second delivery: it holds a prefix of the
    reviewed line. These tests pin that it is read that way, and that doing so
    does not open a route past the wrong-head or unmerged gates.
    """

    TASK_ID = "ODP-ORCH-FINALIZE-STALE-WORKTREE-001"
    BRANCH = f"task/{TASK_ID}"
    APPROVED_HEAD = "3f0b1c9d5a8e47b26c1d0f9a4e7b2c8d16a5f309"
    # The anchor commit the superseded lane stopped on: an ancestor of the head
    # the reviewer went on to approve.
    STALE_HEAD = "9c2e4a17b0d38f5619a7c4e2b8d0f36a5c1e9407"
    # Not an ancestor of anything: a different line of work on the same name.
    DIVERGED_HEAD = "7d41e8b2609fa35c1d8e074b2a9c6f3018de52ab"
    MERGE_COMMIT = "5a7d2f81c93e64b0a8f1d7c2e5b394086af1cd23"
    REPOSITORY = "alfloop-dev/odayplus"
    STALE_CHECKOUT = Path("/tmp/pantheon-worker-worktrees/stale-lane")
    LIVE_ROOT = Path("/home/lupin/oday-plus-supervisor-live")

    # Verbs that would rewrite the branch or the working tree. `merge-base` and
    # `fetch` are reads; everything below changes something.
    MUTATING_GIT_VERBS = {
        "reset", "rebase", "checkout", "switch", "branch", "push", "merge",
        "commit", "cherry-pick", "revert", "restore", "clean", "stash",
        "update-ref", "worktree", "am", "apply",
    }

    def task(self) -> dict[str, object]:
        return {
            "id": self.TASK_ID,
            "owner": "Claude",
            "reviewer": "Antigravity3",
            "status": "review_approved",
            "approved_head": self.APPROVED_HEAD,
            "artifacts": [],
        }

    def merged_pr(self) -> dict[str, object]:
        return {
            "number": 812,
            "state": "MERGED",
            "headRefOid": self.APPROVED_HEAD,
            "headRefName": self.BRANCH,
            "baseRefName": "dev",
            "mergedAt": "2026-08-11T09:14:02Z",
            "mergeCommit": {"oid": self.MERGE_COMMIT},
            "url": "https://github.com/alfloop-dev/odayplus/pull/812",
            "statusCheckRollup": [
                {"__typename": "CheckRun", "name": "orchestrator", "status": "COMPLETED", "conclusion": "SUCCESS"},
                {"__typename": "StatusContext", "context": "task-review-gate", "state": "SUCCESS"},
            ],
        }

    def collect_with_stale_checkout(
        self,
        *,
        checkout_head: str | None = None,
        approved_head_present: bool = True,
        pr_status: dict[str, object] | None = None,
        porcelain: str = "",
        commands: list[list[str]] | None = None,
    ) -> dict[str, object]:
        head = checkout_head or self.STALE_HEAD
        seen = commands if commands is not None else []

        def fake_git(args: list[str], **kwargs: object) -> str:
            seen.append(args)
            responses = {
                ("rev-parse", "HEAD"): head,
                ("show", "-s", "--format=%s", self.APPROVED_HEAD): f"{self.TASK_ID}: seal stale-worktree finalize",
                ("show", "-s", "--format=%b", self.APPROVED_HEAD): (
                    f"LLM-Agent: Claude\nTask-ID: {self.TASK_ID}\nReviewer: Antigravity3\n"
                ),
                ("show", "-s", "--format=%an", self.APPROVED_HEAD): "Claude",
                ("show", "-s", "--format=%ae", self.APPROVED_HEAD): "claude@example.com",
                ("status", "--porcelain", "--untracked-files=all"): porcelain,
                ("remote",): "origin",
                # The task branch's remote ref is deleted when its PR merges.
                ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"): "",
                ("fetch", "origin", "dev"): "",
                ("rev-parse", "--verify", "origin/dev"): "dev-tip",
            }
            key = tuple(args)
            if key not in responses:
                raise AssertionError(f"unexpected git command: {args}")
            return responses[key]

        def fake_succeeds(args: list[str], **kwargs: object) -> bool:
            seen.append(args)
            if args == ["cat-file", "-e", f"{self.APPROVED_HEAD}^{{commit}}"]:
                return approved_head_present
            if args == ["merge-base", "--is-ancestor", head, self.APPROVED_HEAD]:
                return head == self.STALE_HEAD
            if args == ["merge-base", "--is-ancestor", self.APPROVED_HEAD, "origin/dev"]:
                return False
            if args == ["merge-base", "--is-ancestor", self.MERGE_COMMIT, "origin/dev"]:
                return True
            raise AssertionError(f"unexpected git check: {args}")

        with (
            mock.patch.object(
                ai_status,
                "resolve_task_delivery_checkout",
                return_value={"checkout": self.STALE_CHECKOUT, "branch": self.BRANCH, "present": True},
            ),
            # The advance path is exercised by its own tests; here it is only the
            # first of the two carry-forward paths and it never applies to a HEAD
            # that is behind, so hold it at False and test the second one.
            mock.patch.object(ai_status, "is_approved_head_satisfied", return_value=False),
            mock.patch.object(ai_status, "run_git_command", side_effect=fake_git),
            mock.patch.object(ai_status, "git_command_succeeds", side_effect=fake_succeeds),
            mock.patch.object(
                ai_status,
                "pull_request_status_for_branch",
                return_value=self.merged_pr() if pr_status is None else pr_status,
            ),
            mock.patch.object(ai_status, "repository_slug", return_value=self.REPOSITORY),
            mock.patch.object(ai_status, "git_remote_repository_slug", return_value=self.REPOSITORY),
        ):
            return ai_status.collect_done_delivery_metadata(
                self.task(), "Claude", approved_head=self.APPROVED_HEAD
            )

    # -- the behind checkout stops blocking ------------------------------

    def test_a_behind_checkout_finalizes_from_the_exact_merged_approved_head(self) -> None:
        delivery = self.collect_with_stale_checkout()

        self.assertTrue(delivery["stale_task_checkout"])
        self.assertEqual(delivery["provenance_mode"], "merged_pr_with_stale_task_checkout")
        self.assertEqual(delivery["stale_checkout_head"], self.STALE_HEAD)
        self.assertEqual(delivery["verified_head"], self.APPROVED_HEAD)
        self.assertEqual(delivery["approved_head"], self.APPROVED_HEAD)
        self.assertTrue(delivery["merge_verified_via_pr"])
        self.assertEqual(delivery["pull_request"]["head_sha"], self.APPROVED_HEAD)

    def test_done_accepts_the_delivery_a_behind_checkout_produced(self) -> None:
        """The collector's answer has to survive `command_done`'s own freeze check."""

        state = {
            "agents": [],
            "tasks": [dict(self.task(), owner="Claude")],
            "handoffs": [],
            "blockers": [],
        }
        registered_actor = {"AI_NAME": "Claude", "AI_STATUS_EXTRA_AGENTS": "Claude,Antigravity3"}
        delivery = self.collect_with_stale_checkout()

        with (
            mock.patch.dict(os.environ, registered_actor, clear=False),
            mock.patch.object(ai_status, "collect_done_delivery_metadata", return_value=delivery),
            mock.patch.object(ai_status, "archive_task_snapshot", return_value={"task_id": self.TASK_ID}),
            mock.patch.object(ai_status, "append_log"),
        ):
            ai_status.command_done(state, [self.TASK_ID, "closed out from merged PR 812"])

        self.assertEqual(state["tasks"], [])

    def test_the_superseded_tree_is_read_but_never_rewritten(self) -> None:
        """No reset, rebase or branch move: finalize stays read-only on the branch."""

        commands: list[list[str]] = []
        self.collect_with_stale_checkout(commands=commands)

        mutating = [args for args in commands if args and args[0] in self.MUTATING_GIT_VERBS]
        self.assertEqual(mutating, [])
        self.assertIn(["rev-parse", "HEAD"], commands)

    def test_uncommitted_work_in_the_superseded_tree_is_recorded_not_gated(self) -> None:
        """Those edits sit on a commit the PR already merged past; they cannot ship."""

        delivery = self.collect_with_stale_checkout(porcelain=" M scripts/ai_status.py\n?? notes.txt")

        self.assertIsNone(delivery["git_clean"])
        self.assertFalse(delivery["git_clean_evaluated"])
        self.assertEqual(
            delivery["git_clean_skip_reason"],
            "task-owned delivery checkout is behind the reviewer-approved head",
        )
        self.assertEqual(delivery["stale_checkout_dirty_entry_count"], 2)

    # -- and nothing else got easier -------------------------------------

    def test_a_diverged_checkout_is_still_the_wrong_head(self) -> None:
        with self.assertRaisesRegex(SystemExit, "differs from reviewer-approved head"):
            self.collect_with_stale_checkout(checkout_head=self.DIVERGED_HEAD)

    def test_a_behind_checkout_still_fails_closed_on_an_unmerged_pr(self) -> None:
        open_pr = self.merged_pr()
        open_pr.update({"state": "OPEN", "mergedAt": "", "mergeCommit": None})

        with self.assertRaisesRegex(SystemExit, "immutable approved-head PR provenance"):
            self.collect_with_stale_checkout(pr_status=open_pr)

    def test_a_behind_checkout_still_fails_closed_on_a_pr_merged_at_another_head(self) -> None:
        moved = self.merged_pr()
        moved["headRefOid"] = self.DIVERGED_HEAD

        with self.assertRaisesRegex(SystemExit, "immutable approved-head PR provenance"):
            self.collect_with_stale_checkout(pr_status=moved)

    def test_behind_is_not_claimed_when_the_approved_commit_is_not_here(self) -> None:
        """Ancestry that cannot be checked is not ancestry that passed."""

        with self.assertRaisesRegex(SystemExit, "differs from reviewer-approved head"):
            self.collect_with_stale_checkout(approved_head_present=False)

    def test_a_behind_checkout_is_not_a_route_around_the_merged_pr_gate(self) -> None:
        disabled = {
            "TASK_REQUIRE_MERGED_PR": "false",
            "TASK_REQUIRE_GIT_CLEAN": "false",
            "TASK_REQUIRE_COMMIT_HASH": "false",
        }
        open_pr = self.merged_pr()
        open_pr.update({"state": "OPEN", "mergedAt": "", "mergeCommit": None})

        with mock.patch.dict(os.environ, disabled, clear=False):
            with self.assertRaisesRegex(SystemExit, "immutable approved-head PR provenance"):
                self.collect_with_stale_checkout(pr_status=open_pr)

    # -- selection among coexisting checkouts ----------------------------

    def worktree_listing(self, *heads: tuple[str, str]) -> str:
        listing = (
            f"worktree {self.LIVE_ROOT}\n"
            "HEAD e496be62c47c45d758681b8a4d3abfae16f1c96d\n"
            "branch refs/heads/dev\n\n"
        )
        for path, head in heads:
            listing += f"worktree {path}\nHEAD {head}\nbranch refs/heads/{self.BRANCH}\n\n"
        return listing

    def resolve(self, listing: str, *, approved_head: str | None = None) -> dict[str, object]:
        def fake_git(args: list[str], **kwargs: object) -> str:
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return "dev"
            if args == ["worktree", "list", "--porcelain"]:
                return listing
            raise AssertionError(f"unexpected git command: {args}")

        def fake_succeeds(args: list[str], **kwargs: object) -> bool:
            if args == ["merge-base", "--is-ancestor", self.STALE_HEAD, self.APPROVED_HEAD]:
                return True
            if args == ["merge-base", "--is-ancestor", self.DIVERGED_HEAD, self.APPROVED_HEAD]:
                return False
            raise AssertionError(f"unexpected git check: {args}")

        with (
            mock.patch.object(ai_status, "run_git_command", side_effect=fake_git),
            mock.patch.object(ai_status, "git_command_succeeds", side_effect=fake_succeeds),
        ):
            return ai_status.resolve_task_delivery_checkout(
                self.LIVE_ROOT, self.TASK_ID, approved_head=approved_head
            )

    def test_the_checkout_holding_the_approved_head_wins_over_a_behind_one(self) -> None:
        listing = self.worktree_listing(
            (str(self.STALE_CHECKOUT), self.STALE_HEAD),
            ("/tmp/pantheon-worker-worktrees/current-lane", self.APPROVED_HEAD),
        )

        resolved = self.resolve(listing, approved_head=self.APPROVED_HEAD)

        self.assertEqual(resolved["checkout"], Path("/tmp/pantheon-worker-worktrees/current-lane"))
        self.assertEqual(resolved["superseded_checkouts"], [str(self.STALE_CHECKOUT)])

    def test_a_diverged_claimant_keeps_the_selection_ambiguous(self) -> None:
        listing = self.worktree_listing(
            ("/tmp/pantheon-worker-worktrees/other-lane", self.DIVERGED_HEAD),
            ("/tmp/pantheon-worker-worktrees/current-lane", self.APPROVED_HEAD),
        )

        with self.assertRaisesRegex(SystemExit, "expected exactly one task-owned delivery"):
            self.resolve(listing, approved_head=self.APPROVED_HEAD)

    def test_two_claimants_at_the_approved_head_stay_ambiguous(self) -> None:
        """Naming the head disambiguates history, not two live claims to it."""

        listing = self.worktree_listing(
            ("/tmp/pantheon-worker-worktrees/lane-a", self.APPROVED_HEAD),
            ("/tmp/pantheon-worker-worktrees/lane-b", self.APPROVED_HEAD),
        )

        with self.assertRaisesRegex(SystemExit, "expected exactly one task-owned delivery"):
            self.resolve(listing, approved_head=self.APPROVED_HEAD)

    def test_without_an_approved_head_coexisting_checkouts_still_fail_closed(self) -> None:
        listing = self.worktree_listing(
            (str(self.STALE_CHECKOUT), self.STALE_HEAD),
            ("/tmp/pantheon-worker-worktrees/current-lane", self.APPROVED_HEAD),
        )

        with self.assertRaisesRegex(SystemExit, "expected exactly one task-owned delivery"):
            self.resolve(listing)

    def test_a_lone_checkout_records_no_superseded_siblings(self) -> None:
        listing = self.worktree_listing((str(self.STALE_CHECKOUT), self.STALE_HEAD))

        resolved = self.resolve(listing, approved_head=self.APPROVED_HEAD)

        self.assertEqual(resolved["checkout"], self.STALE_CHECKOUT)
        self.assertTrue(resolved["present"])
        self.assertNotIn("superseded_checkouts", resolved)

    # -- the predicate itself --------------------------------------------

    def test_stale_predicate_reports_false_when_git_cannot_run(self) -> None:
        """A missing checkout directory must fail closed, not raise."""

        self.assertFalse(
            ai_status.is_stale_task_checkout(
                Path("/nonexistent-checkout-path"), self.STALE_HEAD, self.APPROVED_HEAD
            )
        )

    def test_stale_predicate_rejects_an_equal_or_empty_head(self) -> None:
        for label, args in {
            "equal": (self.APPROVED_HEAD, self.APPROVED_HEAD),
            "empty-checkout": ("", self.APPROVED_HEAD),
            "empty-approved": (self.STALE_HEAD, ""),
        }.items():
            with self.subTest(label=label):
                with mock.patch.object(
                    ai_status,
                    "git_command_succeeds",
                    side_effect=AssertionError("must decide without shelling out"),
                ):
                    self.assertFalse(
                        ai_status.is_stale_task_checkout(self.STALE_CHECKOUT, *args)
                    )


class EvidenceOnlyAdvanceTests(unittest.TestCase):
    """Approval must survive a commit that only records the review.

    Closeout requires writing evidence; writing evidence is a commit; the commit
    moves the head; task-review-gate is bound to a commit SHA, so approval stops
    applying and the task bounces back to review, where closeout moves the head
    again. 62 evidence commits between 2026-07-20 and 2026-08-04 came out of that
    loop, one task resealing seven times.
    """

    def _repo(self) -> Path:
        repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)


        def run(*a: str) -> None:
            subprocess.run(list(a), cwd=repo, check=True, capture_output=True)

        run("git", "init", "-q")
        run("git", "config", "user.email", "t@example.com")
        run("git", "config", "user.name", "t")
        (repo / "src.py").write_text("x = 1\n", encoding="utf-8")
        run("git", "add", "-A")
        run("git", "commit", "-q", "-m", "base")
        return repo

    def _head(self, repo: Path) -> str:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()

    def _commit(self, repo: Path, rel: str, body: str, message: str) -> str:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", message], cwd=repo, check=True, capture_output=True
        )
        return self._head(repo)

    def test_evidence_only_commit_carries_approval_forward(self) -> None:
        repo = self._repo()
        approved = self._head(repo)
        current = self._commit(
            repo, "docs/evidence/REVIEW.md", "reviewed\n", "record review evidence"
        )

        self.assertTrue(ai_status.is_evidence_only_advance(approved, current, repo))

    def test_source_change_invalidates_even_when_evidence_is_included(self) -> None:
        repo = self._repo()
        approved = self._head(repo)
        (repo / "docs" / "evidence").mkdir(parents=True, exist_ok=True)
        (repo / "docs/evidence/REVIEW.md").write_text("reviewed\n", encoding="utf-8")
        (repo / "src.py").write_text("x = 2\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "evidence and source"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        self.assertFalse(
            ai_status.is_evidence_only_advance(approved, self._head(repo), repo)
        )

    def test_rewritten_history_is_never_carried_forward(self) -> None:
        repo = self._repo()
        approved = self._head(repo)
        self._commit(repo, "docs/evidence/A.md", "a\n", "evidence a")
        subprocess.run(
            ["git", "reset", "--hard", approved], cwd=repo, check=True, capture_output=True
        )
        diverged = self._commit(repo, "docs/evidence/B.md", "b\n", "evidence b")
        subprocess.run(
            ["git", "commit", "-q", "--amend", "-m", "amended"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        rewritten = self._head(repo)

        self.assertNotEqual(diverged, rewritten)
        # A head that is not a descendant of the approved head must never be
        # carried forward, however evidence-shaped its diff looks.
        self.assertFalse(ai_status.is_evidence_only_advance(approved, "0" * 40, repo))

    def test_unreadable_repository_root_fails_closed(self) -> None:
        """A check that could not run must never carry an approval forward.

        subprocess raises on a missing cwd instead of returning a non-zero code,
        so the returncode guard alone let the exception escape and broke an
        unrelated provenance test.
        """
        self.assertFalse(
            ai_status.is_evidence_only_advance(
                "a" * 40, "b" * 40, Path("/nonexistent-repo-root")
            )
        )

    def test_identical_heads_do_not_use_this_path(self) -> None:
        repo = self._repo()
        approved = self._head(repo)

        self.assertFalse(ai_status.is_evidence_only_advance(approved, approved, repo))

    def test_approved_head_satisfied_accepts_evidence_only_advance(self) -> None:
        repo = self._repo()
        approved = self._head(repo)
        current = self._commit(
            repo, "docs/evidence/REVIEW.md", "reviewed\n", "record review evidence"
        )

        self.assertTrue(
            ai_status.is_approved_head_satisfied(
                {"id": "ODP-T-001"}, current, approved, repository_root=repo
            )
        )

    def test_approved_head_satisfied_still_rejects_source_change(self) -> None:
        repo = self._repo()
        approved = self._head(repo)
        current = self._commit(repo, "src.py", "x = 2\n", "change source")

        self.assertFalse(
            ai_status.is_approved_head_satisfied(
                {"id": "FREEZE-TEST-001"}, current, approved, repository_root=repo
            )
        )


class ReviewGateHeadDriftTests(unittest.TestCase):
    """The gate must exist on the head that GitHub is actually gating.

    A GitHub status belongs to one commit. Emission fired only on a status
    transition and nothing else in the orchestrator posted this check, so a branch
    that advanced afterwards left the new head with no gate. Since it is a
    *required* check, absent reads as unmergeable and nothing would put it back.
    Seen live on 2026-08-04: #616 and #622 had four green checks and no gate;
    #628, whose head had not moved, had all five.
    """

    def test_drift_detected_when_head_moved_past_recorded_gate(self) -> None:
        with mock.patch.object(ai_status, "resolve_task_sha", return_value="b" * 40):
            self.assertTrue(
                ai_status.review_gate_head_drifted(
                    {"id": "ODP-T-1", "review_gate_sha": "a" * 40}
                )
            )

    def test_no_drift_when_head_matches_recorded_gate(self) -> None:
        with mock.patch.object(ai_status, "resolve_task_sha", return_value="a" * 40):
            self.assertFalse(
                ai_status.review_gate_head_drifted(
                    {"id": "ODP-T-1", "review_gate_sha": "a" * 40}
                )
            )

    def test_no_drift_claimed_when_gate_was_never_recorded(self) -> None:
        """Without a recorded SHA every sync would re-post for unrelated tasks."""
        with mock.patch.object(ai_status, "resolve_task_sha", return_value="b" * 40):
            self.assertFalse(ai_status.review_gate_head_drifted({"id": "ODP-T-1"}))

    def test_no_drift_claimed_when_head_cannot_be_resolved(self) -> None:
        with mock.patch.object(ai_status, "resolve_task_sha", return_value=None):
            self.assertFalse(
                ai_status.review_gate_head_drifted(
                    {"id": "ODP-T-1", "review_gate_sha": "a" * 40}
                )
            )

    def test_missing_task_id_is_not_drift(self) -> None:
        self.assertFalse(ai_status.review_gate_head_drifted({"review_gate_sha": "a" * 40}))


if __name__ == "__main__":
    unittest.main()
