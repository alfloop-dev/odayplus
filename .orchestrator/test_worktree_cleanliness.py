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
import worktree_cleanliness as wc
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


GUARD = ["guarded/zone"]


def init_repo(repo: Path) -> str:
    git(repo, "init", "--quiet", "-b", "dev")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "--quiet", "-m", "base")
    return git(repo, "rev-parse", "HEAD")


def synthetic_commit(repo: Path, parent: str, root_entry: str) -> str:
    """Add one synthetic tree entry without materializing anything."""
    listing = git(repo, "ls-tree", parent)
    tree = subprocess.run(
        ["git", "mktree"], cwd=repo, input=f"{listing}\n{root_entry}\n", text=True, capture_output=True, check=True
    ).stdout.strip()
    return git(repo, "commit-tree", tree, "-p", parent, "-m", "synthetic")


class ProtectedPathPolicyTests(unittest.TestCase):
    def test_literal_list_is_normalized_and_prose_or_patterns_are_rejected(self) -> None:
        self.assertEqual(wc.normalize_protected_paths(["guarded/zone/", "a/b", "a/b"]), ("a/b", "guarded/zone"))
        self.assertEqual(wc.normalize_protected_paths(None), ())
        for bad in ("guarded/zone", ["/abs"], ["a/../b"], ["a/*"], ["./a"], [""], [3], ["a/.git"], {"a": 1}):
            with self.subTest(bad=bad), self.assertRaises(wc.ProtectedPathPolicyError):
                wc.normalize_protected_paths(bad)

    def test_reduction_is_detected_but_widening_is_not(self) -> None:
        self.assertEqual(wc.protected_paths_reduction(["guarded/zone"], []), ("guarded/zone",))
        self.assertEqual(wc.protected_paths_reduction(["guarded/zone"], ["guarded/zone/inner"]), ("guarded/zone",))
        self.assertEqual(wc.protected_paths_reduction(["guarded/zone"], ["guarded"]), ())

    def test_direct_descendant_ancestor_and_root_selections_conflict(self) -> None:
        for path in ("guarded/zone", "guarded/zone/x.py", "guarded", ".", "", "./guarded/zone", "other/../guarded"):
            with self.subTest(path=path):
                self.assertEqual(wc.protected_path_conflict(path, GUARD), "guarded/zone")
        for path in ("guarded/zone2", "guarded/other", "tests/unit"):
            with self.subTest(path=path):
                self.assertIsNone(wc.protected_path_conflict(path, GUARD))


class ProtectedMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.base = init_repo(self.repo)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_absent_and_plain_tree_entries_are_safe(self) -> None:
        blob = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"], cwd=self.repo, input="synthetic", text=True,
            capture_output=True, check=True,
        ).stdout.strip()
        zone = subprocess.run(
            ["git", "mktree"], cwd=self.repo, input=f"100644 blob {blob}\tfixture.txt\n", text=True,
            capture_output=True, check=True,
        ).stdout.strip()
        guarded = subprocess.run(
            ["git", "mktree"], cwd=self.repo, input=f"040000 tree {zone}\tzone\n", text=True,
            capture_output=True, check=True,
        ).stdout.strip()
        commit = synthetic_commit(self.repo, self.base, f"040000 tree {guarded}\tguarded")
        self.assertTrue(wc.inspect_protected_git_metadata(self.repo, self.base, GUARD).ok)
        verdict = wc.inspect_protected_git_metadata(self.repo, commit, GUARD)
        self.assertTrue(verdict.ok, verdict)
        self.assertFalse((self.repo / "guarded").exists())

    def test_symlink_ancestor_gitlink_and_bad_commit_fail_closed(self) -> None:
        blob = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"], cwd=self.repo, input="elsewhere", text=True,
            capture_output=True, check=True,
        ).stdout.strip()
        symlink = synthetic_commit(self.repo, self.base, f"120000 blob {blob}\tguarded")
        inner = subprocess.run(
            ["git", "mktree"], cwd=self.repo, input=f"160000 commit {self.base}\tzone\n", text=True,
            capture_output=True, check=True,
        ).stdout.strip()
        gitlink = synthetic_commit(self.repo, self.base, f"040000 tree {inner}\tguarded")
        self.assertEqual(wc.inspect_protected_git_metadata(self.repo, symlink, GUARD).reason, "ancestor_escape")
        self.assertEqual(wc.inspect_protected_git_metadata(self.repo, gitlink, GUARD).reason, "protected_entry_escape")
        self.assertEqual(wc.inspect_protected_git_metadata(self.repo, "f" * 40, GUARD).reason, "unverifiable_commit")

    def test_index_and_worktree_symlink_aliases_fail_closed(self) -> None:
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (self.repo / "guarded").symlink_to(outside, target_is_directory=True)
        verdict = wc.inspect_protected_git_metadata(self.repo, "HEAD", GUARD, worktree_path=self.repo)
        self.assertEqual(verdict.reason, "worktree_escape")
        git(self.repo, "add", "guarded")
        verdict = wc.inspect_protected_git_metadata(self.repo, "HEAD", GUARD, check_index=True)
        self.assertEqual(verdict.reason, "index_escape")

    def test_content_filters_and_supervisor_side_hooks_fail_closed(self) -> None:
        git(self.repo, "config", "filter.synthetic.smudge", "cat")
        self.assertEqual(wc.inspect_protected_git_metadata(self.repo, "HEAD", GUARD).reason, "unsafe_git_execution")
        git(self.repo, "config", "--unset", "filter.synthetic.smudge")
        hooks = Path(self.tmp.name) / "hooks"
        hooks.mkdir()
        (hooks / "commit-msg").write_text("#!/bin/sh\n", encoding="utf-8")
        (hooks / "commit-msg").chmod(0o755)
        git(self.repo, "config", "core.hooksPath", str(hooks))
        self.assertTrue(wc.inspect_protected_git_metadata(self.repo, "HEAD", GUARD).ok)
        (hooks / "post-merge").write_text("#!/bin/sh\n", encoding="utf-8")
        (hooks / "post-merge").chmod(0o755)
        verdict = wc.inspect_protected_git_metadata(self.repo, "HEAD", GUARD)
        self.assertEqual(verdict.reason, "unsafe_git_execution")
        self.assertIn("post-merge", verdict.detail)

    def test_unreadable_metadata_fails_closed(self) -> None:
        with mock.patch.object(wc, "_git_metadata", return_value=None):
            self.assertFalse(wc.inspect_protected_git_metadata(self.repo, "HEAD", GUARD).ok)

    def test_context_paths_refuse_overlap_and_symlink_aliases_before_reading(self) -> None:
        root = Path(self.tmp.name) / "status"
        (root / "guarded" / "zone").mkdir(parents=True)
        (root / "docs").mkdir()
        (root / "docs" / "alias").symlink_to(root / "guarded" / "zone", target_is_directory=True)
        self.assertIn("overlaps", wc.context_path_protection_error(root, "guarded", GUARD))
        self.assertIn("overlaps", wc.context_path_protection_error(root, "guarded/zone/brief.md", GUARD))
        self.assertIn("symlink alias", wc.context_path_protection_error(root, "docs/alias/brief.md", GUARD))
        self.assertIsNone(wc.context_path_protection_error(root, "docs/readme.md", GUARD))
        self.assertFalse(wc.is_safe_context_destination(root, "guarded/zone/seed.md", GUARD))

    def test_dirty_protected_entry_is_fingerprinted_without_opening_its_body(self) -> None:
        (self.repo / "guarded" / "zone").mkdir(parents=True)
        (self.repo / "guarded" / "zone" / "synthetic.txt").write_text("synthetic\n", encoding="utf-8")
        (self.repo / "owner.txt").write_text("owner\n", encoding="utf-8")
        opened: list[str] = []
        real_open = Path.open

        def recording_open(path, *args, **kwargs):
            opened.append(str(path))
            return real_open(path, *args, **kwargs)

        with mock.patch.object(Path, "open", recording_open):
            inspection = inspect_worktree(self.repo, protected_paths=GUARD)

        self.assertEqual(inspection.kind, "owner_dirty")
        self.assertTrue(any(name.endswith("owner.txt") for name in opened))
        self.assertFalse(any("guarded" in name for name in opened))


class ProtectedExecutionEnforcementTests(unittest.TestCase):
    """Real Bubblewrap probes against synthetic fixtures outside tests/sources."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "fleet-root"
        self.repo.mkdir()
        init_repo(self.repo)
        self.task = self.root / "task-worktree"
        git(self.repo, "worktree", "add", "--quiet", "-b", "task/GUARD-001", str(self.task))
        for checkout in (self.repo, self.task):
            (checkout / "guarded" / "zone").mkdir(parents=True)
            (checkout / "guarded" / "zone" / "canary.txt").write_text("synthetic canary\n", encoding="utf-8")
        (self.root / "alias").symlink_to(self.task / "guarded", target_is_directory=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, script: str) -> subprocess.CompletedProcess[str]:
        argv = wc.protected_execution_argv(["sh", "-c", script], cwd=self.task, protected_paths=GUARD)
        return subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)

    def test_denied_through_relative_absolute_alias_fleet_root_and_proc_paths(self) -> None:
        sleeper = subprocess.Popen(["sleep", "30"])
        self.addCleanup(sleeper.kill)
        targets = [
            "guarded/zone/canary.txt",
            str(self.task / "guarded" / "zone" / "canary.txt"),
            str(self.root / "alias" / "zone" / "canary.txt"),
            str(self.repo / "guarded" / "zone" / "canary.txt"),
            f"/proc/{sleeper.pid}/root{self.task}/guarded/zone/canary.txt",
        ]
        for target in targets:
            with self.subTest(target=target):
                result = self._run(f"cat '{target}'")
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("synthetic canary", result.stdout)
        result = self._run(f"sh -c 'cat {targets[0]}'")
        self.assertNotEqual(result.returncode, 0)
        for target in ("guarded/zone/new.txt", str(self.repo / "guarded" / "zone" / "new.txt")):
            with self.subTest(write=target):
                self.assertNotEqual(self._run(f"echo x > '{target}'").returncode, 0)
        self.assertFalse((self.task / "guarded" / "zone" / "new.txt").exists())
        self.assertFalse((self.repo / "guarded" / "zone" / "new.txt").exists())

    def test_normal_read_write_commit_and_subprocess_still_work(self) -> None:
        identity = f"{os.getuid()}:{os.getgid()}"
        result = self._run(
            "set -e; cat tracked.txt; echo safe > safe.txt; python3 -c 'print(open(\"safe.txt\").read().strip())'; "
            "git add safe.txt; git -c user.email=t@example.invalid -c user.name=T commit -q -m safe; "
            "echo \"$(id -u):$(id -g)\"; grep -E '^(NoNewPrivs|CapEff)' /proc/self/status"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("base", result.stdout)
        self.assertIn("safe", result.stdout)
        self.assertIn(identity, result.stdout)
        self.assertRegex(result.stdout, r"NoNewPrivs:\s+1")
        self.assertRegex(result.stdout, r"CapEff:\s+0+\n")
        self.assertEqual(git(self.task, "log", "-1", "--format=%s"), "safe")

    def test_missing_alias_paths_and_later_host_additions_stay_denied(self) -> None:
        """Nothing is created in another checkout, yet nothing added later shows through."""
        import shutil

        shutil.rmtree(self.repo / "guarded" / "zone")
        late_root = self.root / "late-worktree"
        argv = wc.protected_execution_argv(
            [
                "sh",
                "-c",
                "while [ ! -e trigger ]; do sleep 0.05; done; "
                f"cat '{self.repo}/guarded/zone/late.txt'; echo rc1=$?; "
                f"cat '{late_root}/guarded/zone/canary.txt'; echo rc2=$?; "
                f"mkdir '{self.repo}/guarded/zone'; echo rc3=$?; "
                "cat tracked.txt",
            ],
            cwd=self.task,
            protected_paths=GUARD,
        )
        self.assertFalse((self.repo / "guarded" / "zone").exists())
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        try:
            (self.repo / "guarded" / "zone").mkdir()
            (self.repo / "guarded" / "zone" / "late.txt").write_text("synthetic late\n", encoding="utf-8")
            git(self.repo, "worktree", "add", "--quiet", "-b", "task/GUARD-LATE", str(late_root))
            (late_root / "guarded" / "zone").mkdir(parents=True)
            (late_root / "guarded" / "zone" / "canary.txt").write_text("synthetic canary\n", encoding="utf-8")
        finally:
            (self.task / "trigger").touch()
        output, _ = process.communicate(timeout=60)
        self.assertNotIn("synthetic", output)
        for marker in ("rc1=0", "rc2=0", "rc3=0"):
            self.assertNotIn(marker, output)
        self.assertIn("base", output)

    def test_process_filesystem_aliases_are_denied(self) -> None:
        holder = subprocess.Popen(["sleep", "30"], cwd=self.task / "guarded" / "zone")
        self.addCleanup(holder.kill)
        for alias in (f"/proc/{holder.pid}/cwd/canary.txt", f"/proc/{holder.pid}/root{self.task}/guarded/zone/canary.txt"):
            with self.subTest(alias=alias):
                result = self._run(f"cat '{alias}'")
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("synthetic canary", result.stdout)
        result = self._run("cd guarded/zone && cat /proc/self/cwd/canary.txt")
        self.assertNotEqual(result.returncode, 0)

    def test_a_shared_alias_root_without_any_ancestor_refuses_but_a_foreign_one_is_hidden(self) -> None:
        import shutil

        shutil.rmtree(self.repo / "guarded")
        with self.assertRaises(wc.ProtectedExecutionUnavailable):
            wc.protected_execution_argv(["true"], cwd=self.task, protected_paths=GUARD, alias_roots=[self.repo])
        plan = wc.plan_protected_execution(self.task, GUARD)
        self.assertIn(("hide", self.repo.resolve()), plan.operations)
        self.assertFalse((self.repo / "guarded").exists())

    def test_missing_or_fake_sandbox_refuses_before_launch(self) -> None:
        fake = self.root / "fake-bwrap"
        fake.write_text('#!/bin/sh\nwhile [ "$1" != "--" ]; do shift; done\nshift\nexec "$@"\n', encoding="utf-8")
        fake.chmod(0o755)
        for binary in (str(self.root / "missing-bwrap"), str(fake)):
            with self.subTest(binary=binary), self.assertRaises(wc.ProtectedExecutionUnavailable):
                wc.protected_execution_argv(["true"], cwd=self.task, protected_paths=GUARD, bwrap=binary)

    def test_symlinked_protected_ancestor_in_any_checkout_refuses(self) -> None:
        other = self.root / "other-worktree"
        git(self.repo, "worktree", "add", "--quiet", "-b", "task/GUARD-002", str(other))
        (other / "guarded").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(wc.ProtectedExecutionUnavailable):
            wc.protected_execution_argv(["true"], cwd=self.task, protected_paths=GUARD)


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

    def test_review_refresh_fast_forwards_to_the_immutable_submitted_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            git(repo, "init", "--quiet")
            git(repo, "config", "user.email", "test@example.invalid")
            git(repo, "config", "user.name", "Test")
            (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
            git(repo, "add", "tracked.txt")
            git(repo, "commit", "--quiet", "-m", "base")
            git(repo, "switch", "--quiet", "--create", "task/SEAL-REVIEW-001")
            (repo / "reviewed.txt").write_text("review head\n", encoding="utf-8")
            git(repo, "add", "reviewed.txt")
            git(repo, "commit", "--quiet", "-m", "review head")
            expected_head = git(repo, "rev-parse", "HEAD")
            git(repo, "switch", "--quiet", "--create", "dev")
            (repo / "dev-only.txt").write_text("newer dev head\n", encoding="utf-8")
            git(repo, "add", "dev-only.txt")
            git(repo, "commit", "--quiet", "-m", "dev advance")
            dev_head = git(repo, "rev-parse", "HEAD")
            git(repo, "switch", "--quiet", "task/SEAL-REVIEW-001")
            git(repo, "reset", "--hard", "HEAD~1")

            ok, status = supervisor._refresh_reused_worker_worktree(
                repo,
                repo,
                dev_head,
                "task/SEAL-REVIEW-001",
                required_head=expected_head,
            )

            self.assertTrue(ok, status)
            self.assertEqual(status, f"review_head_pinned_at_{expected_head[:12]}")
            self.assertEqual(git(repo, "rev-parse", "HEAD"), expected_head)


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
