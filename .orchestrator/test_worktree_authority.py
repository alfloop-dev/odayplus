#!/usr/bin/env python3
"""Focused regression tests for registry-owned worker worktree authority."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))
MIGRATION_DIR = THIS_DIR.parent / "scripts" / "orchestrator"
if str(MIGRATION_DIR) not in sys.path:
    sys.path.insert(0, str(MIGRATION_DIR))

import migrate_worker_worktree_config as migration
import supervisor


class RegistryBaseCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.origin = root / "origin.git"
        self.seed = root / "seed"
        self.checkout = root / "checkout"
        self._git(root, "init", "--bare", str(self.origin))
        self._git(root, "init", "-b", "dev", str(self.seed))
        self._git(self.seed, "config", "user.name", "Test")
        self._git(self.seed, "config", "user.email", "test@example.invalid")
        (self.seed / "README.md").write_text("one\n", encoding="utf-8")
        self._git(self.seed, "add", "README.md")
        self._git(self.seed, "commit", "-m", "base one")
        self._git(self.seed, "remote", "add", "origin", str(self.origin))
        self._git(self.seed, "push", "-u", "origin", "dev")
        self.base_one = self._git(self.seed, "rev-parse", "HEAD").stdout.strip()
        self._git(root, "clone", "--branch", "dev", str(self.origin), str(self.checkout))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)

    def _config(self) -> dict:
        return {
            "paths": {
                "status_file": str(self.checkout / "ai-status.json"),
                "activity_log": str(self.checkout / "activity.jsonl"),
            },
            "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
            "coordination": {
                "repositories": {
                    "pantheon": {
                        "repo": None,
                        "local_path": str(self.checkout),
                        "default_branch": "dev",
                    }
                }
            },
            "worker_worktrees": {"root": str(Path(self.tmp.name) / "worktrees")},
        }

    def test_cache_fetches_once_and_new_task_starts_from_cached_sha(self) -> None:
        config = self._config()
        cache: dict = {}
        original_network = supervisor._run_git_network_command
        with mock.patch.object(supervisor, "_run_git_network_command", wraps=original_network) as fetch:
            base, error = supervisor.resolve_worker_base(
                self.checkout,
                repository_id="pantheon",
                default_branch="dev",
                base_cache=cache,
                network_timeout_seconds=5,
            )
            self.assertIsNone(error)
            self.assertEqual(base.sha, self.base_one)

            (self.seed / "README.md").write_text("two\n", encoding="utf-8")
            self._git(self.seed, "add", "README.md")
            self._git(self.seed, "commit", "-m", "base two")
            self._git(self.seed, "push", "origin", "dev")
            base_two = self._git(self.seed, "rev-parse", "HEAD").stdout.strip()

            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="AUTHORITY-001",
                reason="owned_in_progress_dispatch",
                metadata={"task": {"id": "AUTHORITY-001"}},
            )
            with (
                mock.patch.object(supervisor, "materialize_worker_context_files", return_value=[]),
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    {},
                    request,
                    queue_event_id="event-1",
                    target_agent="Codex",
                    base_cache=cache,
                )

        self.assertTrue(ok, message)
        self.assertEqual(fetch.call_count, 1)
        self.assertNotEqual(base_two, self.base_one)
        self.assertEqual(request.metadata["base_sha"], self.base_one)
        self.assertEqual(request.metadata["base_relation"], "created_from_exact_base")
        self.assertEqual(
            self._git(Path(request.metadata["workspace_path"]), "rev-parse", "HEAD").stdout.strip(),
            self.base_one,
        )

    def test_reused_branch_fast_forwards_only_to_explicit_sha(self) -> None:
        worktree = Path(self.tmp.name) / "existing"
        self._git(self.checkout, "worktree", "add", "-b", "task/AUTHORITY-002", str(worktree), self.base_one)
        (self.seed / "README.md").write_text("two\n", encoding="utf-8")
        self._git(self.seed, "add", "README.md")
        self._git(self.seed, "commit", "-m", "base two")
        self._git(self.seed, "push", "origin", "dev")
        base_two = self._git(self.seed, "rev-parse", "HEAD").stdout.strip()
        self._git(self.checkout, "fetch", "origin", "dev")

        ok, status = supervisor._refresh_reused_worker_worktree(
            self.checkout,
            worktree,
            base_two,
            "task/AUTHORITY-002",
        )

        self.assertTrue(ok, status)
        self.assertEqual(status, f"ff_to_{base_two[:12]}")
        self.assertEqual(self._git(worktree, "rev-parse", "HEAD").stdout.strip(), base_two)

    def test_dirty_reused_worktree_is_left_in_place_and_blocks_dispatch(self) -> None:
        config = self._config()
        task_id = "AUTHORITY-DIRTY-001"
        branch = f"task/{task_id}"
        worktree = Path(self.tmp.name) / "dirty-worktree"
        self._git(self.checkout, "worktree", "add", "-b", branch, str(worktree), self.base_one)
        dirty = worktree / "owner-wip.txt"
        dirty.write_text("do not replace me\n", encoding="utf-8")
        request = supervisor.DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="wake",
            task_id=task_id,
            reason="owned_in_progress_dispatch",
            metadata={"task": {"id": task_id}},
        )

        with mock.patch.object(supervisor, "write_activity_log"):
            ok, message = supervisor.prepare_worker_workspace(
                config,
                {},
                request,
                queue_event_id="event-dirty",
                target_agent="Codex",
                base_cache={},
            )

        self.assertFalse(ok)
        self.assertIn("Preserve and commit", message or "")
        self.assertEqual(dirty.read_text(encoding="utf-8"), "do not replace me\n")
        self.assertFalse(request.metadata.get("workspace_path"))
        self.assertEqual(len(list(Path(self.tmp.name).glob("dirty-worktree.lease_*"))), 0)


class WorktreeAuthorityShapeTests(unittest.TestCase):
    def test_clone_fallback_and_reason_allowlist_are_absent(self) -> None:
        self.assertFalse(hasattr(supervisor, "_create_worker_worktree_fallback"))
        settings = supervisor.worker_worktree_settings({"worker_worktrees": {"root": "/tmp/leased"}})
        self.assertEqual(set(settings), {"root", "root_configured", "git_network_timeout_seconds"})

    def test_archive_script_requires_a_supervisor_lease(self) -> None:
        import auto_commit_archive

        ok, message = auto_commit_archive.run_backfill_pr(
            {"briefs": [".orchestrator/task-briefs/a.md"], "archives": [], "index_modified": False},
            workspace_path=None,
            task_id=None,
        )
        self.assertFalse(ok)
        self.assertIn("supervisor-leased", message)

    def test_config_migration_removes_only_retired_switches(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "config.json"
            path.write_text(
                '{"worker_worktrees":{"root":"/tmp/leased","enabled":true,'
                '"base_ref":"origin/dev","reuse_existing":false,'
                '"execution_reasons":["x"],"recover_clean_diverged_worktrees":true}}',
                encoding="utf-8",
            )
            config = migration.load_object(path)
            settings = config["worker_worktrees"]
            for key in migration.RETIRED_KEYS:
                settings.pop(key, None)
            migration.write_atomically(path, config)
            self.assertEqual(migration.load_object(path)["worker_worktrees"], {"root": "/tmp/leased"})


if __name__ == "__main__":
    unittest.main()
