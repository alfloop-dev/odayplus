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
        # Host git config (e.g. a CI runner's global `git lfs install`) must
        # not leak into the synthetic fixtures; the rule itself still reads it.
        self.global_config = Path(self.tmp.name) / "gitconfig"
        self.global_config.touch()
        env = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(self.global_config), "GIT_CONFIG_NOSYSTEM": "1"})
        env.start()
        self.addCleanup(env.stop)
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
        self.assertTrue(wc.inspect_protected_git_metadata(self.repo, "HEAD", GUARD).ok)
        # An inherited host-level driver is just as able to read protected bodies.
        self.global_config.write_text('[filter "lfs"]\n\tclean = git-lfs clean -- %f\n', encoding="utf-8")
        verdict = wc.inspect_protected_git_metadata(self.repo, "HEAD", GUARD)
        self.assertEqual(verdict.reason, "unsafe_git_execution")
        self.assertIn("filter.lfs.clean", verdict.detail)
        self.global_config.write_text("", encoding="utf-8")
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


SYNTHETIC_DOMAIN = ("guarded/zone",)
SYNTHETIC_NAME = "synthetic_asset.py"
NESTED_COPY = "archive/untracked/guarded/zone/body.py.txt"
NAMED_COPY = "archive/untracked/synthetic_asset.py.txt"


class SyntheticBodyDomain:
    """Hold the shared domain to a synthetic namespace with an isolated Git config."""

    def enter_synthetic_domain(self, case: unittest.TestCase, root: Path) -> None:
        (root / "gitconfig").touch()
        for patcher in (
            mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(root / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"}),
            mock.patch.object(wc, "PROTECTED_BODY_PREFIXES", SYNTHETIC_DOMAIN),
            mock.patch.object(wc, "PROTECTED_BODY_NAMES", (SYNTHETIC_NAME,)),
        ):
            patcher.start()
            case.addCleanup(patcher.stop)


def domain_commit(repo: Path) -> str:
    """Root and nested copies of the synthetic domain beside ordinary files."""
    git(repo, "init", "--quiet", "-b", "dev")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    for relative, text in (
        ("keep/tracked.txt", "keep\n"),
        ("guarded/zone/root.txt", "synthetic root body\n"),
        (NESTED_COPY, "synthetic nested body\n"),
        (NAMED_COPY, "synthetic named body\n"),
        ("archive/untracked/guarded/zone2/sibling.txt", "not in the domain\n"),
    ):
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text(text, encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "domain")
    return git(repo, "rev-parse", "HEAD")


class ProductionBodyDomainTests(unittest.TestCase):
    """The one production domain every runtime, worker and CI checkout uses."""

    def test_production_domain_names_the_canonical_prefix_and_asset(self) -> None:
        self.assertEqual(wc.PROTECTED_BODY_PREFIXES, ("tests/sources",))
        self.assertEqual(wc.PROTECTED_BODY_NAMES, ("test_listing_production_asset.py",))
        self.assertEqual(
            wc.protected_body_sparse_patterns(),
            ("/*", "!**/tests/sources/**", "!**/test_listing_production_asset.py*"),
        )
        self.assertTrue(
            wc.in_protected_body_domain(
                "docs/audits/external-source-ingestion-20260913/implementation-handoff/untracked/tests/sources/x.py.txt"
            )
        )


class ProtectedBodyDomainTests(SyntheticBodyDomain, unittest.TestCase):
    """Synthetic namespace only; every domain verdict is reached without its body."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.enter_synthetic_domain(self, self.root)
        self.seed = self.root / "seed"
        self.seed.mkdir()
        self.commit = domain_commit(self.seed)
        self.worktree = self.root / "worktree"

    def prepared(self) -> Path:
        """Register, exclude, then materialize, exactly as the runtime and workers do."""
        git(self.seed, "worktree", "add", "--quiet", "--no-checkout", "-b", "task/x", str(self.worktree), self.commit)
        self.assertIsNone(wc.install_protected_body_sparse(self.worktree))
        git(self.worktree, "read-tree", "--reset", "-u", self.commit)
        return self.worktree

    def test_domain_is_decided_from_the_name_at_any_depth(self) -> None:
        for path in (
            "guarded/zone",
            "guarded/zone/x.py",
            NESTED_COPY,
            "a/b/guarded/zone/",
            NAMED_COPY,
            SYNTHETIC_NAME,
            f"x/{SYNTHETIC_NAME}.bak/inner.txt",
        ):
            with self.subTest(path=path):
                self.assertTrue(wc.in_protected_body_domain(path))
        for path in ("guarded/zone2/x", "guarded/other", "x/guarded", "zone/guarded/x", "keep/tracked.txt", ""):
            with self.subTest(path=path):
                self.assertFalse(wc.in_protected_body_domain(path))
        self.assertEqual(
            wc.protected_body_sparse_patterns(),
            ("/*", "!**/guarded/zone/**", f"!**/{SYNTHETIC_NAME}*"),
        )

    def test_exclusion_before_materialization_keeps_root_and_nested_copies_out(self) -> None:
        worktree = self.prepared()
        self.assertTrue((worktree / "keep" / "tracked.txt").is_file())
        self.assertTrue((worktree / "archive/untracked/guarded/zone2/sibling.txt").is_file())
        for path in ("guarded/zone/root.txt", NESTED_COPY, NAMED_COPY):
            with self.subTest(path=path):
                self.assertFalse(os.path.lexists(worktree / path))
        flags = dict(line.split(" ", 1)[::-1] for line in git(worktree, "ls-files", "-t").splitlines())
        self.assertEqual({flags["guarded/zone/root.txt"], flags[NESTED_COPY], flags[NAMED_COPY]}, {"S"})
        self.assertIsNone(wc.protected_body_checkout_refusal(worktree, require_sparse=True))
        self.assertEqual(inspect_worktree(worktree).kind, "clean")
        self.assertEqual(
            [entry[2] for entry in wc.protected_body_tree_entries(worktree, self.commit)],
            sorted(["guarded/zone/root.txt", NESTED_COPY, NAMED_COPY]),
        )

    def test_exclusion_never_follows_a_materialization(self) -> None:
        worktree = self.prepared()
        self.assertIn("already_materialized", wc.install_protected_body_sparse(worktree))
        self.assertTrue((worktree / "keep" / "tracked.txt").is_file())

    def test_unexcluded_checkout_is_refused_before_status_can_compare_a_body(self) -> None:
        commands: list[list[str]] = []
        real_run = subprocess.run

        def recording_run(args, *a, **kw):
            commands.append(list(args))
            return real_run(args, *a, **kw)

        with mock.patch.object(wc.subprocess, "run", side_effect=recording_run):
            inspection = inspect_worktree(self.seed)
        self.assertEqual(inspection.kind, "protected_metadata_refused")
        self.assertIn("sparse checkout is not enabled", inspection.detail)
        self.assertFalse(inspection.handoff_clean)
        self.assertFalse([command for command in commands if "status" in command])

    def test_every_weakened_exclusion_refuses(self) -> None:
        def patterns_replaced(worktree: Path) -> None:
            Path(git(worktree, "rev-parse", "--git-path", "info/sparse-checkout")).write_text("/*\n", encoding="utf-8")

        def unflagged(worktree: Path) -> None:
            git(worktree, "update-index", "--no-skip-worktree", NESTED_COPY)

        def recreated(worktree: Path) -> None:
            (worktree / NESTED_COPY).parent.mkdir(parents=True)
            (worktree / NESTED_COPY).write_text("synthetic again\n", encoding="utf-8")

        def aliased(worktree: Path) -> None:
            outside = self.root / "outside"
            outside.mkdir(exist_ok=True)
            (worktree / "archive/untracked/guarded/zone").symlink_to(outside, target_is_directory=True)

        cases = {
            "sparse off": (lambda w: git(w, "config", "--worktree", "core.sparseCheckout", "false"), "not enabled"),
            "cone on": (lambda w: git(w, "config", "--worktree", "core.sparseCheckoutCone", "true"), "cone mode"),
            "patterns replaced": (patterns_replaced, "patterns differ"),
            "skip-worktree cleared": (unflagged, "not skip-worktree"),
            # Git itself clears skip-worktree from a present file when it loads
            # the index, so a recreated body is seen either way.
            "body recreated": (recreated, f"{NESTED_COPY} is not skip-worktree"),
            "ancestor alias": (aliased, "symlink above"),
        }
        for label, (damage, reason) in cases.items():
            with self.subTest(label=label):
                if self.worktree.exists():
                    git(self.seed, "worktree", "remove", "--force", str(self.worktree))
                    git(self.seed, "branch", "-D", "task/x")
                worktree = self.prepared()
                damage(worktree)
                refusal = wc.protected_body_checkout_refusal(worktree)
                self.assertIsNotNone(refusal)
                self.assertIn(reason, refusal)
                self.assertEqual(inspect_worktree(worktree).kind, "protected_metadata_refused")

    def test_unreadable_metadata_is_never_read_as_nothing_there(self) -> None:
        worktree = self.prepared()
        with mock.patch.object(wc, "_git_metadata", return_value=None):
            self.assertIn("unreadable_metadata", wc.protected_body_checkout_refusal(worktree))
        failed = subprocess.CompletedProcess([], 128, b"", b"")
        with mock.patch.object(wc, "_git_metadata", return_value=failed):
            self.assertIn("unreadable_metadata", wc.protected_body_checkout_refusal(worktree))
            self.assertIsNone(wc.protected_body_tree_entries(worktree, self.commit))
        self.assertIsNone(wc.protected_body_tree_entries(worktree, ""))

    def _inspect_recording(self, worktree: Path, **kwargs: object) -> tuple[object, list[list[str]], list[str]]:
        """inspect_worktree, recording every Git command and every Python open."""
        commands: list[list[str]] = []
        opened: list[str] = []
        real_run = subprocess.run
        real_open = Path.open

        def recording_run(args, *a, **kw):
            commands.append(list(args))
            return real_run(args, *a, **kw)

        def recording_open(path, *args, **kw):
            opened.append(str(path))
            return real_open(path, *args, **kw)

        with mock.patch.object(wc.subprocess, "run", side_effect=recording_run), \
             mock.patch.object(Path, "open", recording_open):
            inspection = inspect_worktree(worktree, **kwargs)
        return inspection, commands, opened

    def test_untracked_domain_dirt_refuses_before_status_could_open_it(self) -> None:
        """Untracked domain content blocks, and is never listed through an exclude file."""
        cases = {
            "nested body": ("other/guarded/zone/new.txt", "synthetic one\n"),
            # Status would open this .gitignore to decide what to hide.
            "domain .gitignore": ("other/guarded/zone/.gitignore", "hidden.ban\n"),
            "root domain .gitignore": ("guarded/zone/.gitignore", "hidden.ban\n"),
            "named asset": (f"other/{SYNTHETIC_NAME}.bak", "synthetic named\n"),
        }
        for label, (dirt, text) in cases.items():
            with self.subTest(label=label):
                if self.worktree.exists():
                    git(self.seed, "worktree", "remove", "--force", str(self.worktree))
                    git(self.seed, "branch", "-D", "task/x")
                worktree = self.prepared()
                (worktree / dirt).parent.mkdir(parents=True, exist_ok=True)
                (worktree / dirt).write_text(text, encoding="utf-8")
                (worktree / dirt).with_name("hidden.ban").write_text("synthetic ignored\n", encoding="utf-8")
                inspection, commands, opened = self._inspect_recording(
                    worktree, materialized_paths=[dirt, "other", "guarded"]
                )
                self.assertEqual(inspection.kind, "protected_metadata_refused")
                self.assertIn("untracked domain content", inspection.detail)
                self.assertFalse(inspection.handoff_clean)
                self.assertEqual(inspection.fingerprint, "")
                self.assertFalse([command for command in commands if "status" in command])
                listings = [command for command in commands if "--others" in command]
                self.assertEqual(len(listings), 1)
                self.assertFalse([flag for flag in listings[0] if flag.startswith("--exclude")])
                self.assertFalse(any("guarded/zone" in name or SYNTHETIC_NAME in name for name in opened))
                self.assertTrue((worktree / dirt).is_file(), "refusal never deletes the dirt")
                self.assertFalse(wc.is_reusable_dirt_entry("??", dirt, None, frozenset({dirt})))

    def test_empty_domain_directory_is_no_body(self) -> None:
        """An empty directory (a sandbox mask) holds nothing status could read."""
        worktree = self.prepared()
        (worktree / "guarded" / "zone").mkdir(parents=True)
        self.assertIsNone(wc.protected_body_checkout_refusal(worktree, require_sparse=True))
        self.assertEqual(inspect_worktree(worktree).kind, "clean")

    def test_head_index_union_refuses_same_path_drift_without_opening_it(self) -> None:
        other = git(self.seed, "rev-parse", f"{self.commit}:keep/tracked.txt")

        def head_only(worktree: Path) -> None:
            # Removed from the index while the body is back on disk: only HEAD names it.
            git(worktree, "update-index", "--force-remove", NESTED_COPY)
            (worktree / NESTED_COPY).parent.mkdir(parents=True, exist_ok=True)
            (worktree / NESTED_COPY).write_text("synthetic leftover\n", encoding="utf-8")

        def head_only_absent(worktree: Path) -> None:
            git(worktree, "update-index", "--force-remove", NESTED_COPY)

        def index_only(worktree: Path) -> None:
            git(worktree, "update-index", "--add", "--cacheinfo", f"100644,{other},guarded/zone/staged.txt")

        def object_drift(worktree: Path) -> None:
            git(worktree, "update-index", "--cacheinfo", f"100644,{other},{NESTED_COPY}")

        def mode_drift(worktree: Path) -> None:
            blob = git(worktree, "rev-parse", f":{NESTED_COPY}")
            git(worktree, "update-index", "--cacheinfo", f"100755,{blob},{NESTED_COPY}")

        cases = {
            "HEAD only, body on disk": (head_only, "in HEAD but not in the index"),
            "HEAD only, absent": (head_only_absent, "in HEAD but not in the index"),
            "index only": (index_only, "in the index but not in HEAD"),
            "object drift": (object_drift, "differs between HEAD and the index"),
            "mode drift": (mode_drift, "differs between HEAD and the index"),
        }
        for label, (damage, reason) in cases.items():
            with self.subTest(label=label):
                if self.worktree.exists():
                    git(self.seed, "worktree", "remove", "--force", str(self.worktree))
                    git(self.seed, "branch", "-D", "task/x")
                worktree = self.prepared()
                damage(worktree)
                inspection, commands, opened = self._inspect_recording(worktree)
                self.assertEqual(inspection.kind, "protected_metadata_refused")
                self.assertIn("protected_body_drift", inspection.detail)
                self.assertIn(reason, inspection.detail)
                self.assertEqual(inspection.fingerprint, "")
                self.assertFalse([command for command in commands if "status" in command])
                self.assertFalse(any("guarded/zone" in name for name in opened))

    def test_unmerged_domain_entry_refuses(self) -> None:
        worktree = self.prepared()
        blob = git(worktree, "rev-parse", f":{NESTED_COPY}")
        entries = "".join(
            f"100644 {blob} {stage}\t{NESTED_COPY}\n" for stage in (1, 2, 3)
        )
        git(worktree, "update-index", "--force-remove", NESTED_COPY)
        subprocess.run(
            ["git", "update-index", "--index-info"], cwd=worktree, input=entries.encode(), check=True
        )
        refusal = wc.protected_body_checkout_refusal(worktree)
        self.assertIn("is unmerged in the index", refusal)
        self.assertEqual(inspect_worktree(worktree).kind, "protected_metadata_refused")

    def test_context_paths_in_the_domain_are_refused_for_every_task(self) -> None:
        status = self.root / "status"
        (status / "docs").mkdir(parents=True)
        (status / "docs" / "alias").symlink_to(self.seed / "archive" / "untracked", target_is_directory=True)
        self.assertIn("protected body domain", wc.context_path_protection_error(status, NESTED_COPY, ()))
        self.assertIn("protected body domain", wc.context_path_protection_error(status, NAMED_COPY, ()))
        self.assertIsNone(wc.context_path_protection_error(status, "docs/readme.md", ()))


WORKFLOWS = THIS_DIR.parent / ".github" / "workflows"


def _load_workflow(name: str) -> dict:
    import yaml

    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _reference_scripts() -> tuple[str, str]:
    steps = _load_workflow("tooling-scope-review-gate.yml")["jobs"]["classify"]["steps"]
    return steps[0]["run"], steps[2]["run"]


class CheckoutBoundaryProofTests(unittest.TestCase):
    """The reference proof on a checkout made the way actions/checkout makes it.

    A synthetic namespace stands in for the production prefix; the named-asset
    pattern stays the production one and no file of that name exists here.
    """

    PREFIX = "guarded/zone"
    NESTED = "archive/untracked/guarded/zone/body.py.txt"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "gitconfig").touch()
        env = mock.patch.dict(
            os.environ, {"GIT_CONFIG_GLOBAL": str(self.root / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"}
        )
        env.start()
        self.addCleanup(env.stop)
        self.seed = self.root / "seed"
        self.origin = self.root / "origin.git"
        self.checkout = self.root / "checkout"
        git(self.root, "init", "--quiet", "-b", "dev", str(self.seed))
        git(self.seed, "config", "user.email", "test@example.invalid")
        git(self.seed, "config", "user.name", "Test")
        (self.seed / ".orchestrator").mkdir()
        (self.seed / ".orchestrator" / "worktree_cleanliness.py").write_bytes(
            (THIS_DIR / "worktree_cleanliness.py").read_bytes()
        )
        (self.seed / "keep.txt").write_text("keep\n", encoding="utf-8")
        (self.seed / self.NESTED).parent.mkdir(parents=True)
        (self.seed / self.NESTED).write_text("synthetic archived copy one\n", encoding="utf-8")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "--quiet", "-m", "base")
        self.base = git(self.seed, "rev-parse", "HEAD")
        self.base_body = git(self.seed, "rev-parse", f"HEAD:{self.NESTED}")
        (self.seed / self.NESTED).write_text("synthetic archived copy two\n", encoding="utf-8")
        git(self.seed, "commit", "--quiet", "-am", "drift")
        self.drifted = git(self.seed, "rev-parse", "HEAD")
        self.drifted_body = git(self.seed, "rev-parse", f"HEAD:{self.NESTED}")
        git(self.seed, "branch", "drifted")
        git(self.seed, "reset", "--quiet", "--hard", self.base)
        git(self.root, "init", "--quiet", "--bare", str(self.origin))
        git(self.origin, "config", "uploadpack.allowFilter", "true")
        git(self.origin, "config", "uploadpack.allowAnySHA1InWant", "true")
        git(self.seed, "push", "--quiet", str(self.origin), "dev", "drifted")

    def action_checkout(self, patterns: list[str], ref: str = "dev") -> None:
        """git-source-provider.ts: filtered fetch, sparse setup, then checkout."""
        git(self.root, "init", "--quiet", str(self.checkout))
        git(self.checkout, "remote", "add", "origin", f"file://{self.origin}")
        git(self.checkout, "-c", "protocol.version=2", "fetch", "-q", "--no-tags", "--filter=blob:none",
            "origin", "+refs/heads/*:refs/remotes/origin/*")
        git(self.checkout, "config", "core.sparseCheckout", "true")
        sparse = self.checkout / ".git" / "info" / "sparse-checkout"
        sparse.parent.mkdir(exist_ok=True)
        sparse.write_text("\n".join(patterns) + "\n", encoding="utf-8")
        git(self.checkout, "checkout", "-q", "--force", "-B", "current", f"refs/remotes/origin/{ref}")

    def run_proof(self, compare: str | None = None) -> subprocess.CompletedProcess[str]:
        _preflight, proof = _reference_scripts()
        env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
        env.pop("PROTECTED_COMPARE_SHAS", None)
        if compare is not None:
            env["PROTECTED_COMPARE_SHAS"] = compare
        return subprocess.run(
            ["bash", "-e", "-c", proof.replace('denied = "tests/sources"', f'denied = "{self.PREFIX}"')],
            cwd=self.checkout, env=env, capture_output=True, text=True, check=False,
        )

    def local_objects(self) -> str:
        return git(self.checkout, "cat-file", "--batch-check", "--batch-all-objects")

    def test_nested_copy_stays_tracked_unmaterialized_and_unfetched(self) -> None:
        self.action_checkout(list(wc.protected_body_sparse_patterns((self.PREFIX,))))
        self.assertTrue((self.checkout / "keep.txt").is_file())
        self.assertFalse(os.path.lexists(self.checkout / self.NESTED))
        for compare in (None, f"{self.base} {self.base}"):
            proof = self.run_proof(compare)
            self.assertEqual(proof.returncode, 0, proof.stdout + proof.stderr)
        self.assertNotIn(self.base_body, self.local_objects())

    def test_old_root_only_deny_materializes_the_copy_and_is_refused(self) -> None:
        self.action_checkout(["/*", f"!/{self.PREFIX}"])
        self.assertTrue((self.checkout / self.NESTED).is_file(), "the hazard the domain exists for")
        proof = self.run_proof()
        self.assertNotEqual(proof.returncode, 0)
        self.assertIn("deny patterns changed", proof.stderr)
        self.assertIn("protected body domain", proof.stderr)

    def test_copy_differing_between_compared_commits_is_refused_without_fetching_either(self) -> None:
        self.action_checkout(list(wc.protected_body_sparse_patterns((self.PREFIX,))), ref="drifted")
        proof = self.run_proof(f"{self.base} {self.drifted}")
        self.assertNotEqual(proof.returncode, 0)
        self.assertIn("protected body domain differs between compared commits", proof.stderr)
        objects = self.local_objects()
        self.assertNotIn(self.base_body, objects)
        self.assertNotIn(self.drifted_body, objects)

    def test_unreadable_compared_commit_is_refused(self) -> None:
        self.action_checkout(list(wc.protected_body_sparse_patterns((self.PREFIX,))))
        proof = self.run_proof(f"{self.base} {'a' * 40}")
        self.assertNotEqual(proof.returncode, 0)
        self.assertIn("protected body domain unreadable at a compared commit", proof.stderr)


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
