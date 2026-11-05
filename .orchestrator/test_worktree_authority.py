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

import capacity_controller
import migrate_worker_worktree_config as migration
import supervisor
import worker_workspace
import worktree_cleanliness


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

    def _tracked_protected_commit(self) -> str:
        """A commit tracking a regular synthetic body under the deny prefix."""
        blob = self._git(self.repo, "hash-object", "-w", "--stdin", stdin="synthetic body\n").stdout.strip()
        zone = self._git(self.repo, "mktree", stdin=f"100644 blob {blob}\tbody.txt\n").stdout.strip()
        guarded = self._git(self.repo, "mktree", stdin=f"040000 tree {zone}\tzone\n").stdout.strip()
        return self._synthetic_commit(f"040000 tree {guarded}\tguarded")

    def test_regular_protected_tree_is_metadata_safe_yet_refused_before_registration(self) -> None:
        tracked = self._tracked_protected_commit()
        self.assertEqual(
            worktree_cleanliness.inspect_protected_git_metadata(self.repo, tracked, PROTECTED).reason, "metadata_safe"
        )
        worktree = self.root / "wt-tracked"

        ok, error = supervisor._create_worker_worktree(
            self.repo, worktree, "task/PROT-TRACKED-001", tracked, protected_paths=PROTECTED
        )

        self.assertFalse(ok)
        self.assertIn("protected_entry_tracked", error or "")
        self.assertFalse(worktree.exists())
        self.assertNotIn(str(worktree), self._git(self.repo, "worktree", "list").stdout)

    def test_unprotected_task_still_materializes_a_tracked_tree(self) -> None:
        worktree = self.root / "wt-unprotected"
        ok, error = supervisor._create_worker_worktree(
            self.repo, worktree, "task/PLAIN-TRACKED-001", self._tracked_protected_commit()
        )
        self.assertTrue(ok, error)
        self.assertTrue((worktree / "guarded/zone/body.txt").is_file())

    def test_reuse_refuses_ff_and_review_targets_with_a_regular_protected_tree(self) -> None:
        worktree = self.root / "wt-tracked-ff"
        self._git(self.repo, "worktree", "add", "-b", "task/PROT-TRACKED-002", str(worktree), self.base)
        tracked = self._tracked_protected_commit()

        for required_head in (None, tracked):
            with self.subTest(required_head=required_head):
                ok, status = supervisor._refresh_reused_worker_worktree(
                    self.repo, worktree, tracked, "task/PROT-TRACKED-002",
                    required_head=required_head, protected_paths=PROTECTED,
                )
                self.assertFalse(ok)
                self.assertIn("protected_entry_tracked", status)
                self.assertEqual(self._rev(worktree, "HEAD"), self.base)
                self.assertFalse((worktree / "guarded").exists())

    def test_checkout_already_holding_a_protected_tree_refuses_before_status_and_keeps_wip(self) -> None:
        tracked = self._tracked_protected_commit()
        worktree = self.root / "wt-tracked-head"
        self._git(self.repo, "worktree", "add", "--no-checkout", "-b", "task/PROT-TRACKED-003", str(worktree), tracked)
        (worktree / "owner-wip.txt").write_text("keep me\n", encoding="utf-8")
        commands: list[list[str]] = []
        real_run = subprocess.run

        def recording_run(args, *a, **kw):
            commands.append(list(args))
            return real_run(args, *a, **kw)

        with mock.patch("subprocess.run", side_effect=recording_run):
            ok, status = supervisor._refresh_reused_worker_worktree(
                self.repo, worktree, self.base, "task/PROT-TRACKED-003", protected_paths=PROTECTED
            )
            identity = worker_workspace.compute_worktree_state_identity(worktree, protected_paths=PROTECTED)

        self.assertFalse(ok)
        self.assertIn("protected_entry_tracked", status)
        self.assertTrue(identity.startswith("protected_metadata_refused:"), identity)
        self.assertFalse([cmd for cmd in commands if cmd[:2] == ["git", "status"]])
        self.assertEqual((worktree / "owner-wip.txt").read_text(encoding="utf-8"), "keep me\n")

    def test_handoff_seal_refuses_a_protected_tree_the_owner_committed(self) -> None:
        tracked = self._tracked_protected_commit()
        worktree = self.root / "wt-tracked-seal"
        self._git(self.repo, "worktree", "add", "--no-checkout", "-b", "task/PROT-TRACKED-004", str(worktree), tracked)
        worker = {
            "workspace_mode": "isolated_worktree",
            "workspace_path": str(worktree),
            "workspace_branch": "task/PROT-TRACKED-004",
            "task_id": "PROT-TRACKED-004",
            "request_snapshot": {"metadata": {"protected_paths": PROTECTED}},
        }
        seal = supervisor.seal_worker_handoff({}, {}, worker, {"id": "PROT-TRACKED-004"})
        self.assertFalse(seal.accepted)
        self.assertEqual(seal.reason, "protected_metadata_refused")

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


    def test_prepare_records_the_exact_checkout_head_in_request_and_lease(self) -> None:
        task = {"id": "PROT-LAUNCH-001"}
        ok, message, request, state = self._prepare(task, {"id": "PROT-LAUNCH-001"})
        self.assertTrue(ok, message)
        self.assertEqual(request.metadata["workspace_launch_head"], self.base)
        self.assertEqual(state["worker_worktrees"]["leases"]["PROT-LAUNCH-001"]["workspace_launch_head"], self.base)
        self.assertEqual(
            supervisor.request_snapshot(request)["metadata"]["workspace_launch_head"], self.base,
            "the worker record carries it through the existing snapshot",
        )

        # A reused checkout holding an old local anchor is recorded as it is, ahead of base.
        worktree = Path(request.metadata["workspace_path"])
        self._git(worktree, "config", "user.name", "Test")
        self._git(worktree, "config", "user.email", "test@example.invalid")
        (worktree / "anchor.txt").write_text("old anchor\n", encoding="utf-8")
        self._git(worktree, "add", "anchor.txt")
        self._git(worktree, "commit", "-m", "old anchor")
        anchor = self._rev(worktree, "HEAD")

        ok, message, request, state = self._prepare(task, {"id": "PROT-LAUNCH-001"})
        self.assertTrue(ok, message)
        self.assertEqual(request.metadata["workspace_launch_head"], anchor)
        self.assertNotEqual(anchor, request.metadata["base_sha"])

    def _sidecar(self, **overrides) -> dict:
        record = {
            "id": "SC-001",
            "task_class": "sidecar",
            "auto_created_by": "capacity-controller",
            "helper_parent": "PARENT-001",
            "owner": "Claude",
            "protected_paths": PROTECTED,
        }
        record.update(overrides)
        return record

    def _prepare_sidecar(
        self, sidecar: dict, parent: dict | None, repositories: dict | None = None
    ) -> tuple[bool, str | None]:
        status_file = self.root / "sidecar-status.json"
        board = [sidecar, *([parent] if parent else [])]
        status_file.write_text(json.dumps({"tasks": board}), encoding="utf-8")
        config = self._sidecar_config(repositories)
        request = supervisor.DeliveryRequest(
            agent_id="claude", provider="claude", delivery_mode="claude", message="wake",
            task_id=sidecar["id"], reason="owned_ready_dispatch", metadata={"task": {"id": sidecar["id"]}},
        )
        base = worker_workspace.WorkerBaseResolution(
            repository_id="pantheon", default_branch="dev", sha=self.base, remote_ref="refs/remotes/origin/dev"
        )
        with (
            mock.patch.object(supervisor, "materialize_worker_context_files", return_value=[]),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "resolve_worker_base", return_value=(base, None)) as base_resolver,
        ):
            result = supervisor.prepare_worker_workspace(
                config, {}, request, queue_event_id="event", target_agent="Claude", base_cache={}
            )
        self.prepared_request, self.base_resolver = request, base_resolver
        return result

    def _sidecar_config(self, repositories: dict | None = None) -> dict:
        return {
            "paths": {"status_file": str(self.root / "sidecar-status.json"), "activity_log": str(self.root / "activity.jsonl")},
            "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
            "coordination": {
                "repositories": {
                    "pantheon": {"repo": None, "local_path": str(self.repo), "default_branch": "dev"},
                    **(repositories or {}),
                }
            },
            "worker_worktrees": {"root": str(self.root / "worktrees")},
            "agents": {"claude": {"provider": "claude"}, **{f"slot-{index}": {"slot_id": f"slot-{index}"} for index in range(8)}},
            "capacity_controller": {
                "coordination_reserved_slots": 1,
                "sidecars": {"max_new_per_wave": 1, "max_active": 4, "max_capacity_ratio": 0.5, "ttl_seconds": 7200},
            },
        }

    def test_capacity_sidecar_prepares_only_under_its_live_parent_authority(self) -> None:
        parent = {"id": "PARENT-001", "owner": "Claude", "protected_paths": PROTECTED}
        for name, sidecar, board_parent, expected in (
            ("parent unknown", self._sidecar(), None, "no canonical or archived record"),
            ("child dropped protection", self._sidecar(protected_paths=[]), parent, "does not carry parent protected_paths"),
            ("child owner moved", self._sidecar(owner="Antigravity3"), parent, "not its parent's selected owner"),
            ("parent policy invalid", self._sidecar(), {**parent, "protected_paths": "guarded/zone"}, "protection is invalid"),
            ("parent frozen later", self._sidecar(), {**parent, "frozen_evidence_base_sha": "a" * 40}, "frozen evidence base"),
            ("parent manual gate later", self._sidecar(), {**parent, "status": "blocked", "next": "Waiting for manual approval"}, "human or manual gate"),
            ("parent owner unregistered", self._sidecar(owner="Ghost"), {**parent, "owner": "Ghost"}, "no registered automatic owner"),
            ("parent gate status later", self._sidecar(), {**parent, "gate_status": "pending_human_signoff"}, "human gate"),
            ("parent gate class later", self._sidecar(), {**parent, "task_class": "human_gate"}, "human gate"),
            ("parent human roles later", self._sidecar(), {**parent, "human_required_roles": ["Human/Ops"]}, "human gate"),
            ("parent waits for human later", self._sidecar(), {**parent, "waiting_for": "Human/Ops"}, "human gate"),
            ("parent approval required later", self._sidecar(), {**parent, "requires_human_approval": True}, "human gate"),
            ("parent non-dispatchable later", self._sidecar(), {**parent, "non_dispatchable": True}, "non-dispatchable"),
        ):
            with self.subTest(name):
                ok, message = self._prepare_sidecar(sidecar, board_parent)
                self.assertFalse(ok)
                self.assertIn(expected, message or "")
                self.assertFalse((self.root / "worktrees" / "pantheon" / "sc-001").exists())

        ok, message = self._prepare_sidecar(self._sidecar(), parent)
        self.assertTrue(ok, message)
        ordinary_blocked = {**parent, "status": "blocked", "blocked_reason": "upstream API defect", "waiting_for": "Codex"}
        ok, message = self._prepare_sidecar(self._sidecar(), ordinary_blocked)
        self.assertTrue(ok, message)

    def _side_checkout(self) -> dict:
        side = self.root / "side"
        self._git(self.root, "clone", "--quiet", str(self.repo), str(side))
        self._git(side, "remote", "set-url", "origin", "https://github.com/org/side-repo.git")
        return {
            "side_repo": {
                "repo": "org/side-repo",
                "local_path": str(side),
                "default_branch": "trunk",
                "artifact_prefixes": ["side-repo/"],
            },
            "second_repo": {"repo": None, "local_path": str(self.root), "artifact_prefixes": ["second-repo/"]},
            "absent_repo": {"repo": None, "local_path": str(self.root / "absent")},
            # Another id of the one checkout `pantheon` names.
            "odayplus": {"repo": None, "local_path": str(self.repo), "default_branch": "dev"},
        }

    def test_capacity_sidecar_prepares_only_in_its_live_parent_source(self) -> None:
        repositories = self._side_checkout()
        parent = {"id": "PARENT-001", "owner": "Claude", "protected_paths": PROTECTED}
        for name, sidecar, board_parent, expected in (
            ("legacy child, parent inferred elsewhere", self._sidecar(), {**parent, "artifacts": ["side-repo/diag.md"]}, "current source"),
            ("child moved", self._sidecar(repository="side_repo"), parent, "current source"),
            ("parent moved", self._sidecar(repository="pantheon"), {**parent, "repository": "side_repo"}, "current source"),
            ("child names the unknown", self._sidecar(repository="org/unregistered"), parent, "current source"),
            ("child checkout gone", self._sidecar(repository="absent_repo"), parent, "current source"),
            ("parent names the unknown", self._sidecar(), {**parent, "repository": "org/unregistered"}, "source is unresolved"),
            ("parent checkout gone", self._sidecar(), {**parent, "repository": "absent_repo"}, "source is unresolved"),
            (
                "parent ambiguous",
                self._sidecar(),
                {**parent, "artifacts": ["side-repo/a.md", "second-repo/b.md"]},
                "source is unresolved",
            ),
        ):
            with self.subTest(name):
                ok, message = self._prepare_sidecar(sidecar, board_parent, repositories)
                self.assertFalse(ok)
                self.assertIn(expected, message or "")
                self.assertFalse((self.root / "worktrees").exists(), "refused before anything is materialized")

        for name, sidecar, board_parent, namespace in (
            ("one source by another id", self._sidecar(id="SC-ALIAS"), {**parent, "repository": "odayplus"}, "pantheon"),
            ("inherited elsewhere", self._sidecar(id="SC-SIDE", repository="side_repo"), {**parent, "artifacts": ["side-repo/diag.md"]}, "side-repo"),
        ):
            with self.subTest(name):
                ok, message = self._prepare_sidecar(sidecar, board_parent, repositories)
                self.assertTrue(ok, message)
                self.assertTrue((self.root / "worktrees" / namespace / sidecar["id"].lower()).is_dir())

    def test_artifact_inferred_parent_prepares_its_real_child_in_the_parents_checkout(self) -> None:
        repositories = self._side_checkout()
        parent = {
            "id": "PARENT-001",
            "status": "blocked",
            "owner": "Claude",
            "reviewer": "Codex",
            "artifacts": ["side-repo/diag.md"],
            "protected_paths": PROTECTED,
        }
        wave = {
            "capacity_controller": {
                "chair_decision": {"valid_until": "2999-01-01T00:00:00Z", "sidecar_wave": {"approved": True}}
            }
        }
        [child] = capacity_controller.sidecar_candidates(
            self._sidecar_config(repositories), wave, [parent], runnable_tasks=set()
        )
        self.assertEqual(child["repository"], "org/side-repo")

        ok, message = self._prepare_sidecar(child, parent, repositories)

        self.assertTrue(ok, message)
        metadata = self.prepared_request.metadata
        self.assertEqual(metadata["repository_id"], "side_repo")
        self.assertEqual(metadata["protected_paths"], PROTECTED)
        self.assertTrue(Path(metadata["workspace_path"]).is_relative_to(self.root / "worktrees" / "side-repo"))
        # The source's own configured branch, not the parent's or the fleet's.
        self.assertEqual(self.base_resolver.call_args.kwargs["default_branch"], "trunk")
        self.assertEqual(self.base_resolver.call_args.args[0], self.root / "side")

    def test_archived_parent_remains_the_sidecar_authority(self) -> None:
        archived = {"id": "PARENT-001", "owner": "Claude", "protected_paths": PROTECTED}
        with mock.patch.object(supervisor, "load_archived_task", return_value=archived):
            ok, message = self._prepare_sidecar(self._sidecar(), None)
            self.assertTrue(ok, message)
            ok, message = self._prepare_sidecar(self._sidecar(id="SC-002", protected_paths=[]), None)
            self.assertFalse(ok)
            self.assertIn("does not carry parent protected_paths", message or "")

    def test_ordinary_sidecar_without_controller_provenance_is_not_gated(self) -> None:
        ok, message = self._prepare_sidecar(
            {"id": "SC-OWN-001", "task_class": "sidecar", "owner": "Claude", "helper_parent": "GONE"}, None
        )
        self.assertTrue(ok, message)


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
