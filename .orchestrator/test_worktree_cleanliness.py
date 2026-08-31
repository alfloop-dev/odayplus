#!/usr/bin/env python3
from __future__ import annotations
# ruff: noqa: I001

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import supervisor
import status_transition
from runtime_state import migrate_state
from worktree_cleanliness import inspect_porcelain, inspect_worktree


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


class WorktreeCleanlinessPolicyTests(unittest.TestCase):
    def test_all_unknown_untracked_files_are_owner_dirt(self) -> None:
        inspection = inspect_porcelain(
            b"?? fix_probe.py\0"
            b"?? nested/test_probe.py\0"
            b"?? change.orig\0"
            b"?? change.patch\0"
            b"?? change.rej\0"
            b"?? .python-version\0"
        )

        self.assertEqual(inspection.kind, "owner_dirty")
        self.assertEqual(
            {path for _code, path in inspection.blocking_entries},
            {
                "fix_probe.py",
                "nested/test_probe.py",
                "change.orig",
                "change.patch",
                "change.rej",
                ".python-version",
            },
        )

    def test_an_unrecorded_orchestrator_path_is_still_owner_dirt(self) -> None:
        inspection = inspect_porcelain(
            b"?? .orchestrator/task-briefs/owner-notes.md\0"
        )

        self.assertEqual(inspection.kind, "owner_dirty")

    def test_only_supervisor_context_and_materialized_files_are_handoff_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            git(repo, "init", "--quiet")
            git(repo, "config", "user.email", "test@example.invalid")
            git(repo, "config", "user.name", "Test")
            (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
            git(repo, "add", "tracked.txt")
            git(repo, "commit", "--quiet", "-m", "base")
            (repo / ".orchestrator" / "skills").mkdir(parents=True)
            (repo / ".orchestrator" / "skills" / "guide.md").write_text("guide\n", encoding="utf-8")
            (repo / "external-context.md").write_text("context\n", encoding="utf-8")

            inspection = inspect_worktree(
                repo,
                materialized_paths=[".orchestrator/skills/guide.md", "external-context.md"],
            )

        self.assertEqual(inspection.kind, "orchestrator_seed_only")
        self.assertTrue(inspection.handoff_clean)

    def test_symlink_and_hardlink_context_paths_are_not_allowlisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "worktree"
            root.mkdir()
            outside = Path(tmpdir) / "outside-context.md"
            outside.write_text("outside\\n", encoding="utf-8")
            (root / "symlink-context.md").symlink_to(outside)
            (root / "tracked.md").write_text("tracked\\n", encoding="utf-8")
            os.link(root / "tracked.md", root / "hardlink-context.md")

            symlink = inspect_porcelain(
                b"?? symlink-context.md\0",
                worktree_path=root,
                materialized_paths=["symlink-context.md"],
            )
            hardlink = inspect_porcelain(
                b"?? hardlink-context.md\0",
                worktree_path=root,
                materialized_paths=["hardlink-context.md"],
            )

        self.assertEqual(symlink.kind, "owner_dirty")
        self.assertEqual(hardlink.kind, "owner_dirty")


class OwnerContinuationTests(unittest.TestCase):
    def test_only_same_owner_can_resume_exact_unsealed_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            git(repo, "init", "--quiet")
            git(repo, "config", "user.email", "test@example.invalid")
            git(repo, "config", "user.name", "Test")
            (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
            git(repo, "add", "tracked.txt")
            git(repo, "commit", "--quiet", "-m", "base")
            git(repo, "switch", "--quiet", "--create", "task/SEAL-001")
            (repo / "unfinished.py").write_text("print('finish me')\n", encoding="utf-8")
            inspection = inspect_worktree(repo)
            head_sha = git(repo, "rev-parse", "HEAD")
            state = {
                "worker_worktrees": {
                    "handoff_blocks": {
                        "SEAL-001": {
                            "owner": "Codex",
                            "workspace_path": str(repo.resolve()),
                            "workspace_branch": "task/SEAL-001",
                            "head_sha": head_sha,
                            "dirt_fingerprint": inspection.fingerprint,
                            "detail": inspection.detail,
                        }
                    }
                }
            }
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="close out the existing worktree",
                task_id="SEAL-001",
                reason="owned_in_progress_dispatch",
            )
            task = {"id": "SEAL-001", "owner": "Codex"}

            allowed, _detail = supervisor.sealed_owner_continuation_allowed(
                {"schema": {"assignee_field": "owner"}},
                state,
                request,
                task,
                target_agent="codex",
                worktree_path=repo,
                branch="task/SEAL-001",
            )
            self.assertTrue(allowed)

            owner_ready_request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="resume the existing worktree",
                task_id="SEAL-001",
                reason="owned_ready_dispatch",
            )
            allowed, _detail = supervisor.sealed_owner_continuation_allowed(
                {"schema": {"assignee_field": "owner"}},
                state,
                owner_ready_request,
                task,
                target_agent="codex",
                worktree_path=repo,
                branch="task/SEAL-001",
            )
            self.assertTrue(allowed)

            reviewer_request = supervisor.DeliveryRequest(
                agent_id="claude",
                provider="claude",
                delivery_mode="claude",
                message="review",
                task_id="SEAL-001",
                reason="review_ready_dispatch",
            )
            allowed, reason = supervisor.sealed_owner_continuation_allowed(
                {"schema": {"assignee_field": "owner"}},
                state,
                reviewer_request,
                task,
                target_agent="claude",
                worktree_path=repo,
                branch="task/SEAL-001",
            )
            self.assertFalse(allowed)
            self.assertEqual(reason, "not_owner_execution")

            (repo / "unfinished.py").write_text("print('changed')\n", encoding="utf-8")
            allowed, reason = supervisor.sealed_owner_continuation_allowed(
                {"schema": {"assignee_field": "owner"}},
                state,
                request,
                task,
                target_agent="codex",
                worktree_path=repo,
                branch="task/SEAL-001",
            )
            self.assertFalse(allowed)
            self.assertEqual(reason, "dirt_changed")


class HandoffTransitionTests(unittest.TestCase):
    def test_rejected_owner_exit_reopens_review_without_discarding_its_submission(self) -> None:
        task = {
            "id": "SEAL-REOPEN-001",
            "status": "review",
            "owner": "Codex",
            "reviewer": "Claude",
            "waiting_for": "Claude",
            "approved_head": "a" * 40,
            "review_submission": {"remote_sha": "b" * 40, "pr_number": 42},
        }
        status = {
            "tasks": [task],
            "handoffs": [{"task_id": task["id"], "status": "pending"}],
        }
        with (
            mock.patch.object(status_transition, "commit_canonical_task_transition", return_value=True),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = status_transition.reject_unsealed_worker_handoff(
                {"paths": {"status_file": "/tmp/status.json"}},
                status,
                task,
                worker_run_id="owner-run-1",
                reason="owner_dirty",
                detail="1 dirty change (1 untracked): probe.py",
            )

        self.assertTrue(changed)
        self.assertEqual(task["status"], "in_progress")
        self.assertNotIn("waiting_for", task)
        self.assertNotIn("approved_head", task)
        self.assertEqual(task["review_submission"]["pr_number"], 42)
        self.assertEqual(task["handoff_seal"]["status"], "rejected")
        self.assertEqual(status["handoffs"][0]["status"], "done")
        write_activity_log.assert_called_once()

    def test_runtime_state_preserves_only_valid_handoff_block_records(self) -> None:
        state = migrate_state(
            {
                "worker_worktrees": {
                    "leases": {},
                    "handoff_blocks": {"good": {"owner": "Codex"}, "bad": "not-a-record"},
                }
            }
        )

        self.assertEqual(state["worker_worktrees"]["handoff_blocks"], {"good": {"owner": "Codex"}})


if __name__ == "__main__":
    unittest.main()
