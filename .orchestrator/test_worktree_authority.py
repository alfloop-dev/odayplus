#!/usr/bin/env python3
"""Focused regression tests for registry-owned worker worktree authority."""
from __future__ import annotations

import json
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


PROTECTED = ["guarded/zone"]


class ProtectedWorktreeLifecycleTests(unittest.TestCase):
    """Real Git fixtures; protected entries are synthetic tree metadata only."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # Keep host git config (e.g. a CI runner's global LFS filter) out of the fixtures.
        (self.root / "gitconfig").touch()
        env = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(self.root / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"})
        env.start()
        self.addCleanup(env.stop)
        self.repo = self.root / "repo"
        self._git(self.root, "init", "-b", "dev", str(self.repo))
        self._git(self.repo, "config", "user.name", "Test")
        self._git(self.repo, "config", "user.email", "test@example.invalid")
        (self.repo / "README.md").write_text("one\n", encoding="utf-8")
        self._git(self.repo, "add", "README.md")
        self._git(self.repo, "commit", "-m", "base one")
        self.base = self._rev(self.repo, "HEAD")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _git(cwd: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=cwd, text=True, input=stdin, capture_output=True, check=True)

    def _rev(self, cwd: Path, ref: str) -> str:
        return self._git(cwd, "rev-parse", ref).stdout.strip()

    def _commit_on_dev(self, name: str, text: str) -> str:
        (self.repo / name).write_text(text, encoding="utf-8")
        self._git(self.repo, "add", name)
        self._git(self.repo, "commit", "-m", f"add {name}")
        return self._rev(self.repo, "HEAD")

    def _synthetic_commit(self, root_entry: str) -> str:
        """A commit whose tree gains one synthetic entry; nothing is checked out."""
        listing = self._git(self.repo, "ls-tree", self.base).stdout
        tree = self._git(self.repo, "mktree", stdin=listing + root_entry + "\n").stdout.strip()
        return self._git(self.repo, "commit-tree", tree, "-p", self.base, "-m", "synthetic").stdout.strip()

    def _symlink_ancestor_commit(self) -> str:
        blob = self._git(self.repo, "hash-object", "-w", "--stdin", stdin="elsewhere").stdout.strip()
        return self._synthetic_commit(f"120000 blob {blob}\tguarded")

    def _gitlink_protected_commit(self) -> str:
        inner = self._git(self.repo, "mktree", stdin=f"160000 commit {self.base}\tzone\n").stdout.strip()
        return self._synthetic_commit(f"040000 tree {inner}\tguarded")

    def test_new_allocation_is_metadata_first_at_the_exact_base(self) -> None:
        worktree = self.root / "wt-new"
        commands: list[list[str]] = []
        real_run = subprocess.run

        def recording_run(args, *a, **kw):
            commands.append(list(args))
            return real_run(args, *a, **kw)

        with mock.patch("subprocess.run", side_effect=recording_run):
            ok, error = supervisor._create_worker_worktree(
                self.repo, worktree, "task/PROT-NEW-001", self.base, protected_paths=PROTECTED
            )

        self.assertTrue(ok, error)
        add = next(cmd for cmd in commands if cmd[:3] == ["git", "worktree", "add"])
        self.assertIn("--no-checkout", add)
        self.assertLess(
            commands.index(add),
            next(i for i, cmd in enumerate(commands) if cmd[:2] == ["git", "read-tree"]),
        )
        self.assertEqual(self._rev(worktree, "HEAD"), self.base)
        self.assertEqual(self._git(worktree, "symbolic-ref", "HEAD").stdout.strip(), "refs/heads/task/PROT-NEW-001")
        self.assertEqual(self._git(worktree, "status", "--porcelain").stdout, "")
        self.assertFalse((worktree / "guarded").exists())

    def test_symlink_ancestor_refuses_before_any_registration_or_materialization(self) -> None:
        escape = self._symlink_ancestor_commit()
        worktree = self.root / "wt-escape"

        ok, error = supervisor._create_worker_worktree(
            self.repo, worktree, "task/PROT-ESC-001", escape, protected_paths=PROTECTED
        )

        self.assertFalse(ok)
        self.assertIn("ancestor_escape", error or "")
        self.assertFalse(worktree.exists())
        self.assertNotIn(str(worktree), self._git(self.repo, "worktree", "list").stdout)

    def test_gitlink_protected_entry_refuses(self) -> None:
        ok, error = supervisor._create_worker_worktree(
            self.repo, self.root / "wt-gitlink", "task/PROT-GL-001", self._gitlink_protected_commit(),
            protected_paths=PROTECTED,
        )
        self.assertFalse(ok)
        self.assertIn("protected_entry_escape", error or "")

    def test_unsafe_checkout_hook_refuses_protected_creation_only(self) -> None:
        hooks = self.root / "hooks"
        hooks.mkdir()
        hook = hooks / "post-checkout"
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        hook.chmod(0o755)
        self._git(self.repo, "config", "core.hooksPath", str(hooks))

        ok, error = supervisor._create_worker_worktree(
            self.repo, self.root / "wt-hook", "task/PROT-HOOK-001", self.base, protected_paths=PROTECTED
        )
        self.assertFalse(ok)
        self.assertIn("post-checkout", error or "")
        ok, error = supervisor._create_worker_worktree(
            self.repo, self.root / "wt-plain", "task/PLAIN-HOOK-001", self.base
        )
        self.assertTrue(ok, error)

    def test_reused_clean_branch_fast_forwards_to_validated_base(self) -> None:
        worktree = self.root / "wt-ff"
        self._git(self.repo, "worktree", "add", "-b", "task/PROT-FF-001", str(worktree), self.base)
        advanced = self._commit_on_dev("two.txt", "two\n")

        ok, status = supervisor._refresh_reused_worker_worktree(
            self.repo, worktree, advanced, "task/PROT-FF-001", protected_paths=PROTECTED
        )

        self.assertTrue(ok, status)
        self.assertEqual(status, f"ff_to_{advanced[:12]}")
        self.assertEqual(self._rev(worktree, "HEAD"), advanced)

    def test_reuse_refuses_a_forbidden_ff_target_without_touching_the_checkout(self) -> None:
        worktree = self.root / "wt-ff-escape"
        self._git(self.repo, "worktree", "add", "-b", "task/PROT-FF-002", str(worktree), self.base)
        escape = self._symlink_ancestor_commit()

        ok, status = supervisor._refresh_reused_worker_worktree(
            self.repo, worktree, escape, "task/PROT-FF-002", protected_paths=PROTECTED
        )

        self.assertFalse(ok)
        self.assertIn("ancestor_escape", status)
        self.assertEqual(self._rev(worktree, "HEAD"), self.base)
        self.assertFalse((worktree / "guarded").exists())

    def test_review_pin_moves_forward_and_backward_to_exact_head(self) -> None:
        worktree = self.root / "wt-review"
        self._git(self.repo, "worktree", "add", "-b", "task/PROT-REV-001", str(worktree), self.base)
        (worktree / "review.txt").write_text("submitted\n", encoding="utf-8")
        self._git(worktree, "add", "review.txt")
        self._git(worktree, "commit", "-m", "submitted head")
        submitted = self._rev(worktree, "HEAD")
        self._git(worktree, "reset", "--hard", self.base)

        ok, status = supervisor._refresh_reused_worker_worktree(
            self.repo, worktree, self.base, "task/PROT-REV-001", required_head=submitted, protected_paths=PROTECTED
        )
        self.assertTrue(ok, status)
        self.assertEqual(self._rev(worktree, "HEAD"), submitted)

        (worktree / "later.txt").write_text("later\n", encoding="utf-8")
        self._git(worktree, "add", "later.txt")
        self._git(worktree, "commit", "-m", "later work")
        ok, status = supervisor._refresh_reused_worker_worktree(
            self.repo, worktree, self.base, "task/PROT-REV-001", required_head=submitted, protected_paths=PROTECTED
        )
        self.assertTrue(ok, status)
        self.assertEqual(self._rev(worktree, "HEAD"), submitted)
        self.assertNotEqual(self._rev(self.repo, "refs/heads/task/PROT-REV-001"), submitted)

    def test_backward_pin_keeps_unreferenced_detached_work(self) -> None:
        worktree = self.root / "wt-unref"
        self._git(self.repo, "worktree", "add", "--detach", str(worktree), self.base)
        (worktree / "orphan.txt").write_text("unreferenced\n", encoding="utf-8")
        self._git(worktree, "add", "orphan.txt")
        self._git(worktree, "commit", "-m", "unreferenced work")
        orphan = self._rev(worktree, "HEAD")

        ok, status = supervisor._refresh_reused_worker_worktree(
            self.repo, worktree, self.base, "task/PROT-UNREF-001", required_head=self.base, protected_paths=PROTECTED
        )

        self.assertFalse(ok)
        self.assertIn("unpreserved detached work", status)
        self.assertEqual(self._rev(worktree, "HEAD"), orphan)

    def test_detached_owner_reattaches_on_the_validated_sha(self) -> None:
        self._git(self.repo, "branch", "task/PROT-ATT-001", self.base)
        worktree = self.root / "wt-attach"
        self._git(self.repo, "worktree", "add", "--detach", str(worktree), self.base)
        ahead = self._commit_on_dev("ahead.txt", "ahead\n")
        self._git(self.repo, "branch", "-f", "task/PROT-ATT-001", ahead)

        ok, status = supervisor._refresh_reused_worker_worktree(
            self.repo, worktree, ahead, "task/PROT-ATT-001", protected_paths=PROTECTED
        )

        self.assertTrue(ok, status)
        self.assertEqual(self._git(worktree, "symbolic-ref", "--short", "HEAD").stdout.strip(), "task/PROT-ATT-001")
        self.assertEqual(self._rev(worktree, "HEAD"), ahead)

    def test_symbolic_ref_drift_after_precheck_refuses_before_mutation(self) -> None:
        worktree = self.root / "wt-drift"
        self._git(self.repo, "worktree", "add", "-b", "task/PROT-DRIFT-001", str(worktree), self.base)
        advanced = self._commit_on_dev("drift.txt", "drift\n")
        real_output = supervisor._git_output
        seen = {"count": 0}

        def drifting_output(cwd, *args):
            if args == ("symbolic-ref", "--quiet", "HEAD"):
                seen["count"] += 1
                if seen["count"] > 1:
                    return 0, "refs/heads/someone-else"
            return real_output(cwd, *args)

        with mock.patch.object(supervisor, "_git_output", side_effect=drifting_output):
            ok, status = supervisor._refresh_reused_worker_worktree(
                self.repo, worktree, advanced, "task/PROT-DRIFT-001", protected_paths=PROTECTED
            )

        self.assertFalse(ok)
        self.assertEqual(status, "head_drift_after_precheck")
        self.assertEqual(self._rev(worktree, "HEAD"), self.base)

    def test_dirty_protected_task_worktree_is_retained(self) -> None:
        worktree = self.root / "wt-dirty"
        self._git(self.repo, "worktree", "add", "-b", "task/PROT-DIRTY-001", str(worktree), self.base)
        (worktree / "owner-wip.txt").write_text("keep me\n", encoding="utf-8")
        advanced = self._commit_on_dev("three.txt", "three\n")

        ok, status = supervisor._refresh_reused_worker_worktree(
            self.repo, worktree, advanced, "task/PROT-DIRTY-001", protected_paths=PROTECTED
        )

        self.assertFalse(ok)
        self.assertIn("owner-wip.txt", status)
        self.assertEqual((worktree / "owner-wip.txt").read_text(encoding="utf-8"), "keep me\n")
        self.assertEqual(self._rev(worktree, "HEAD"), self.base)

    def _prepare(self, task_record: dict, snapshot: dict) -> tuple[bool, str | None, supervisor.DeliveryRequest, dict]:
        status_file = self.root / "ai-status.json"
        status_file.write_text(json.dumps({"tasks": [task_record]}), encoding="utf-8")
        config = {
            "paths": {"status_file": str(status_file), "activity_log": str(self.root / "activity.jsonl")},
            "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
            "coordination": {
                "repositories": {"pantheon": {"repo": None, "local_path": str(self.repo), "default_branch": "dev"}}
            },
            "worker_worktrees": {"root": str(self.root / "worktrees")},
        }
        request = supervisor.DeliveryRequest(
            agent_id="claude",
            provider="claude",
            delivery_mode="claude",
            message="wake",
            task_id=task_record["id"],
            reason="owned_in_progress_dispatch",
            metadata={"task": snapshot},
        )
        state: dict = {}
        import worker_workspace

        base = worker_workspace.WorkerBaseResolution(
            repository_id="pantheon", default_branch="dev", sha=self.base, remote_ref="refs/remotes/origin/dev"
        )
        with (
            mock.patch.object(supervisor, "materialize_worker_context_files", return_value=[]),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "resolve_worker_base", return_value=(base, None)),
        ):
            ok, message = supervisor.prepare_worker_workspace(
                config, state, request, queue_event_id="event", target_agent="Claude", base_cache={}
            )
        return ok, message, request, state

    def test_prepare_carries_only_canonical_protection_into_request_and_lease(self) -> None:
        task = {"id": "PROT-PREP-001", "protected_paths": PROTECTED}
        ok, message, request, state = self._prepare(task, {"id": "PROT-PREP-001"})
        self.assertTrue(ok, message)
        self.assertEqual(request.metadata["protected_paths"], PROTECTED)
        self.assertEqual(state["worker_worktrees"]["leases"]["PROT-PREP-001"]["protected_paths"], PROTECTED)

    def test_snapshot_protection_without_canonical_authority_refuses(self) -> None:
        ok, message, request, _state = self._prepare(
            {"id": "PROT-PREP-002"}, {"id": "PROT-PREP-002", "protected_paths": PROTECTED}
        )
        self.assertFalse(ok)
        self.assertIn("canonical task record does not", message or "")
        self.assertNotIn("workspace_path", request.metadata)


class ProtectedContextSeedingTests(unittest.TestCase):
    def test_protected_or_aliased_context_is_refused_before_hash_or_copy(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            status_root = root / "status"
            (status_root / "guarded" / "zone").mkdir(parents=True)
            (status_root / "guarded" / "zone" / "canary.md").write_text("synthetic\n", encoding="utf-8")
            (status_root / "docs").mkdir()
            (status_root / "docs" / "alias").symlink_to(status_root / "guarded" / "zone", target_is_directory=True)
            (status_root / "docs" / "plain.md").write_text("plain\n", encoding="utf-8")
            (status_root / "ai-status.json").write_text(json.dumps({"tasks": [{"id": "SEED-1"}]}), encoding="utf-8")
            workspace = root / "workspace"
            workspace.mkdir()
            subprocess.run(["git", "init", "--quiet"], cwd=workspace, check=True)
            config = {"paths": {"status_file": str(status_root / "ai-status.json")}}
            for context in ("guarded/zone/canary.md", "guarded", "docs/alias/canary.md", "docs"):
                request = supervisor.DeliveryRequest(
                    agent_id="claude",
                    provider="claude",
                    delivery_mode="claude",
                    message="wake",
                    task_id="SEED-1",
                    context_files=[context],
                    metadata={"protected_paths": ["guarded/zone"]},
                )
                with (
                    self.subTest(context=context),
                    mock.patch.object(supervisor, "_file_or_dir_hash") as hashed,
                    mock.patch.object(supervisor, "_atomic_copy_context_file") as copied,
                    self.assertRaisesRegex(ValueError, "protected path"),
                ):
                    supervisor.materialize_worker_context_files(config, request, workspace)
                hashed.assert_not_called()
                copied.assert_not_called()
            self.assertFalse((workspace / "guarded").exists())
            self.assertFalse((workspace / "docs").exists())


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
