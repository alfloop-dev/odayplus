#!/usr/bin/env python3
from __future__ import annotations

import ast
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import multi_repo_registry


def _config_anchored_at(anchor: Path) -> dict[str, object]:
    return {"paths": {"status_file": str(anchor / "ai-status.json")}}


def _init_repo(root: Path, origin_url: str | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(root)], capture_output=True, check=True)
    if origin_url:
        subprocess.run(["git", "remote", "add", "origin", origin_url], cwd=root, check=True)
    return root


class MultiRepoRegistryTests(unittest.TestCase):
    def test_default_registry_includes_execute_plans_checkout(self) -> None:
        repo = multi_repo_registry.resolve_repository({}, "execute_plans")

        self.assertEqual(repo["display_name"], "execute-plans")
        self.assertEqual(repo["repo"], "ajoe734/execute-plans")
        self.assertEqual(repo["default_branch"], "main")
        self.assertEqual(
            repo["resolved_local_path"],
            (multi_repo_registry.repository_path_anchor({}) / ".." / "execute-plans").resolve(),
        )

    def test_execute_plans_artifact_prefix_routes_to_sibling_repo(self) -> None:
        artifact = "execute-plans/e2e/dummy.spec.ts"
        expected_root = (
            multi_repo_registry.repository_path_anchor({}) / ".." / "execute-plans"
        ).resolve()

        self.assertEqual(multi_repo_registry.artifact_repository_id({}, artifact), "execute_plans")
        self.assertEqual(
            multi_repo_registry.repository_relative_artifact_path({}, artifact),
            Path("e2e/dummy.spec.ts"),
        )
        self.assertEqual(
            multi_repo_registry.artifact_local_path({}, artifact),
            expected_root / "e2e" / "dummy.spec.ts",
        )

    def test_task_primary_repository_prefers_single_non_pantheon_artifact_repo(self) -> None:
        task = {
            "id": "FE-INT-GATE-DUMMY",
            "artifacts": [
                "execute-plans/e2e/dummy.spec.ts",
                "support/evidence/FE-INT-GATE-DUMMY.json",
            ],
        }

        self.assertEqual(multi_repo_registry.task_artifact_repository_ids({}, task), ["execute_plans", "pantheon"])
        self.assertEqual(multi_repo_registry.task_repository_id({}, task), "execute_plans")

    def test_task_primary_repository_rejects_multiple_non_pantheon_repos(self) -> None:
        task = {
            "id": "CROSS-REPO",
            "artifacts": [
                "execute-plans/e2e/dummy.spec.ts",
                "front-ai-trading-system/src/routes/dummy.tsx",
            ],
        }

        self.assertIsNone(multi_repo_registry.task_repository_id({}, task))

    def test_relative_local_paths_anchor_on_the_fleet_root_not_the_code_root(self) -> None:
        # A supervisor running from a per-rollout code checkout must still find
        # its sibling repositories next to the fleet root; anchoring on the code
        # directory re-points every relative path whenever a rollout lands.
        with tempfile.TemporaryDirectory() as tmpdir:
            fleet_root = Path(tmpdir) / "fleet"
            sibling = Path(tmpdir) / "oday-data-platform-supervisor"
            fleet_root.mkdir()
            sibling.mkdir()
            config = _config_anchored_at(fleet_root)
            config["coordination"] = {
                "repositories": {"oday_data_platform": {"local_path": "../oday-data-platform-supervisor"}}
            }

            repo = multi_repo_registry.resolve_repository(config, "oday_data_platform")

        self.assertEqual(repo["resolved_local_path"], sibling)

    def test_env_override_supplies_a_deployment_specific_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fleet_root = Path(tmpdir) / "fleet"
            override = Path(tmpdir) / "elsewhere"
            fleet_root.mkdir()
            override.mkdir()
            config = _config_anchored_at(fleet_root)

            with mock.patch.dict(os.environ, {"ODAY_DATA_PLATFORM_LOCAL_PATHS": str(override)}):
                repo = multi_repo_registry.resolve_repository(config, "oday_data_platform")

        self.assertEqual(repo["resolved_local_path"], override)

    def test_task_binding_prefers_the_declared_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fleet_root = Path(tmpdir) / "fleet"
            checkout = _init_repo(
                Path(tmpdir) / "data-platform",
                "https://github.com/alfloop-dev/oday-data-platform.git",
            )
            fleet_root.mkdir()
            config = _config_anchored_at(fleet_root)
            config["coordination"] = {
                "repositories": {"oday_data_platform": {"local_path": str(checkout)}}
            }

            binding = multi_repo_registry.resolve_task_repository(
                config,
                {"id": "DPF-KRN-MEAS-001", "repository": "alfloop-dev/oday-data-platform"},
            )

        self.assertTrue(binding.resolved)
        self.assertEqual(binding.repo_id, "oday_data_platform")
        self.assertEqual(binding.slug, "alfloop-dev/oday-data-platform")
        self.assertEqual(binding.root, checkout.resolve())
        self.assertIsNone(binding.error)

    def test_task_binding_fails_closed_when_the_checkout_serves_another_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fleet_root = Path(tmpdir) / "fleet"
            wrong = _init_repo(
                Path(tmpdir) / "wrong", "https://github.com/alfloop-dev/odayplus.git"
            )
            fleet_root.mkdir()
            config = _config_anchored_at(fleet_root)
            config["coordination"] = {
                "repositories": {"oday_data_platform": {"local_path": str(wrong)}}
            }

            binding = multi_repo_registry.resolve_task_repository(
                config, {"repository": "alfloop-dev/oday-data-platform"}
            )

        self.assertFalse(binding.resolved)
        self.assertIn("repository_checkout_mismatch", binding.error or "")

    def test_task_binding_reports_an_unregistered_repository(self) -> None:
        binding = multi_repo_registry.resolve_task_repository({}, {"repository": "acme/widgets"})

        self.assertFalse(binding.resolved)
        self.assertIn("unknown_repository", binding.error or "")

    def test_task_binding_falls_back_to_artifact_prefix_inference(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fleet_root = Path(tmpdir) / "fleet"
            checkout = _init_repo(Path(tmpdir) / "execute-plans", "https://github.com/ajoe734/execute-plans.git")
            fleet_root.mkdir()
            config = _config_anchored_at(fleet_root)
            config["coordination"] = {
                "repositories": {"execute_plans": {"local_path": str(checkout)}}
            }

            binding = multi_repo_registry.resolve_task_repository(
                config, {"id": "FE-INT", "artifacts": ["execute-plans/e2e/dummy.spec.ts"]}
            )

        self.assertTrue(binding.resolved)
        self.assertEqual(binding.repo_id, "execute_plans")

    def test_local_repositories_yield_each_checkout_once(self) -> None:
        # `pantheon` and the fleet's real slug both carry local_path ".", so
        # they resolve to one checkout. Walking it twice makes the coordination
        # watcher raise two dispatch events for a single request file.
        with tempfile.TemporaryDirectory() as tmpdir:
            fleet_root = Path(tmpdir) / "fleet"
            fleet_root.mkdir()
            config = _config_anchored_at(fleet_root)

            local = multi_repo_registry.iter_local_repositories(config)
            roots = [repo["resolved_local_path"] for repo in local]
            fleet_entries = [repo["id"] for repo in local if repo["resolved_local_path"] == fleet_root]

        self.assertEqual(len(roots), len(set(roots)))
        self.assertEqual(fleet_entries, ["pantheon"])

    def test_checkout_origin_slug_normalizes_ssh_and_git_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            checkout = _init_repo(Path(tmpdir) / "repo", "git@github.com:alfloop-dev/odayplus.git")

            self.assertEqual(
                multi_repo_registry.checkout_origin_slug(checkout), "alfloop-dev/odayplus"
            )


class TaskRepositoryDeclarationTests(unittest.TestCase):
    """A task that names its repository must not be re-derived from artifacts.

    DPF-GOV-001 declared `alfloop-dev/oday-data-platform` and carried no
    artifacts key, so the artifacts-only derivation answered `pantheon` and the
    finalize gate searched the ODay Plus checkout for a commit that only exists
    in the data-platform repository -- a check no retry could pass.
    """

    def test_a_declared_repository_wins_over_artifact_inference(self) -> None:
        task = {
            "id": "DPF-GOV-001",
            "repository": "alfloop-dev/oday-data-platform",
            "artifacts": ["execute-plans/e2e/dummy.spec.ts"],
        }

        self.assertEqual(
            multi_repo_registry.task_repository_id({}, task), "oday_data_platform"
        )

    def test_a_declared_repository_applies_with_no_artifacts_at_all(self) -> None:
        for task in (
            {"id": "DPF-GOV-001", "repository": "alfloop-dev/oday-data-platform"},
            {"id": "DPF-GOV-001", "repository": "alfloop-dev/oday-data-platform", "artifacts": []},
        ):
            with self.subTest(task=task):
                self.assertEqual(
                    multi_repo_registry.task_repository_id({}, task), "oday_data_platform"
                )

    def test_an_unregistered_declaration_fails_closed(self) -> None:
        """Falling back to artifact inference here would search a repository the
        task never named, which is the failure this ordering exists to prevent."""
        task = {"id": "T-1", "repository": "someone/not-registered"}

        self.assertIsNone(multi_repo_registry.task_repository_id({}, task))

    def test_undeclared_tasks_keep_the_artifact_derivation(self) -> None:
        self.assertEqual(multi_repo_registry.task_repository_id({}, {"id": "T-2"}), "pantheon")
        self.assertEqual(
            multi_repo_registry.task_repository_id(
                {}, {"id": "T-3", "artifacts": ["execute-plans/e2e/dummy.spec.ts"]}
            ),
            "execute_plans",
        )
        self.assertIsNone(
            multi_repo_registry.task_repository_id(
                {},
                {
                    "id": "T-4",
                    "artifacts": [
                        "execute-plans/e2e/dummy.spec.ts",
                        "front-ai-trading-system/src/routes/dummy.tsx",
                    ],
                },
            )
        )

    def test_it_now_agrees_with_the_authoritative_resolver(self) -> None:
        task = {"id": "DPF-GOV-001", "repository": "alfloop-dev/oday-data-platform"}

        binding = multi_repo_registry.resolve_task_repository({}, task)

        self.assertEqual(multi_repo_registry.task_repository_id({}, task), binding.repo_id)


class RepositoryResolutionHasOneAuthorityTests(unittest.TestCase):
    """Deriving a task's repository must not be reachable except through the registry.

    Five call sites were fixed one at a time on 2026-08-20 for the same defect:
    each answered "which repository" for itself and answered wrong for any task
    outside ODay Plus. Fixing instances does not stop the sixth being written,
    so the wrong answer is no longer reachable -- artifact inference is private
    and this scan keeps it that way.

    Modelled on `test_no_unvalidated_actor_read_remains`, which encodes the same
    lesson for actor reads.
    """

    ALLOWED = {"multi_repo_registry.py"}

    def _python_sources(self):
        root = Path(__file__).resolve().parents[1]
        for base in (root / ".orchestrator", root / "scripts", root / "delivery_toolchain"):
            if not base.exists():
                continue
            for path in base.rglob("*.py"):
                if path.name.startswith("test_") or path.name in self.ALLOWED:
                    continue
                yield path

    def test_repository_inference_is_not_reachable_outside_the_registry(self) -> None:
        offenders = []
        for path in self._python_sources():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id == "_infer_repository_from_artifacts":
                    offenders.append(f"{path.name}:{node.lineno}")
                elif isinstance(node, ast.Attribute) and node.attr == "_infer_repository_from_artifacts":
                    offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual([], offenders, "use task_repository_id or resolve_task_repository instead")

    def test_the_retired_public_name_stays_retired(self) -> None:
        """`task_primary_repository_id` was the reachable wrong answer."""
        self.assertFalse(hasattr(multi_repo_registry, "task_primary_repository_id"))

    def test_the_authority_answers_for_a_declared_repository(self) -> None:
        task = {"id": "T-1", "repository": "alfloop-dev/oday-data-platform"}
        self.assertEqual(multi_repo_registry.task_repository_id({}, task), "oday_data_platform")

    def test_the_authority_refuses_what_it_cannot_answer(self) -> None:
        """Both kinds of "cannot answer" must look the same to a caller."""
        unknown = {"id": "T-2", "repository": "someone/not-registered"}
        ambiguous = {"id": "T-3", "artifacts": [
            "execute-plans/e2e/a.spec.ts",
            "front-ai-trading-system/src/b.tsx",
        ]}
        self.assertIsNone(multi_repo_registry.task_repository_id({}, unknown))
        self.assertIsNone(multi_repo_registry.task_repository_id({}, ambiguous))
        for task in (unknown, ambiguous):
            binding = multi_repo_registry.resolve_task_repository({}, task)
            self.assertEqual(binding.source, "unresolved")
            self.assertIsNotNone(binding.error)


class RepositoryIdAuthorityCanonicalizeTests(unittest.TestCase):
    """Regression tests for ODP-ORCH-REPOSITORY-ID-AUTHORITY-001.

    A task's ``repository`` field may carry a registry ID, an alias, the
    GitHub slug, a ``.git`` URL, or any other name ``matching_repo_id``
    accepts. Before this fix, the raw declared value was passed as
    ``expected_slug`` to origin verification, which compared it against
    the checkout's actual origin slug. When the declared value was not
    already the slug (e.g. a registry ID like ``oday_data_platform``),
    origin verification always failed with a false
    ``repository_checkout_mismatch``.
    """

    def _make_env(self, origin_url: str):
        """Create a temp fleet root + repo checkout with the given origin."""
        import tempfile
        tmpdir = tempfile.mkdtemp()
        fleet_root = Path(tmpdir) / "fleet"
        checkout = _init_repo(
            Path(tmpdir) / "data-platform", origin_url,
        )
        fleet_root.mkdir()
        config = _config_anchored_at(fleet_root)
        config["coordination"] = {
            "repositories": {"oday_data_platform": {"local_path": str(checkout)}}
        }
        return config, checkout, tmpdir

    def test_repo_id_canonicalizes_to_slug_before_origin_check(self) -> None:
        """Registry ID like ``oday_data_platform`` must not cause a mismatch."""
        config, checkout, tmpdir = self._make_env(
            "https://github.com/alfloop-dev/oday-data-platform.git"
        )
        try:
            binding = multi_repo_registry.resolve_task_repository(
                config, {"id": "T-ID", "repository": "oday_data_platform"},
            )
            self.assertTrue(binding.resolved, f"unexpected error: {binding.error}")
            self.assertEqual(binding.repo_id, "oday_data_platform")
            self.assertEqual(binding.slug, "alfloop-dev/oday-data-platform")
            self.assertEqual(binding.root, checkout.resolve())
            self.assertIsNone(binding.error)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_full_slug_still_works(self) -> None:
        """The happy path: ``alfloop-dev/oday-data-platform`` slug."""
        config, checkout, tmpdir = self._make_env(
            "https://github.com/alfloop-dev/oday-data-platform.git"
        )
        try:
            binding = multi_repo_registry.resolve_task_repository(
                config, {"id": "T-SLUG", "repository": "alfloop-dev/oday-data-platform"},
            )
            self.assertTrue(binding.resolved, f"unexpected error: {binding.error}")
            self.assertEqual(binding.repo_id, "oday_data_platform")
            self.assertEqual(binding.slug, "alfloop-dev/oday-data-platform")
            self.assertIsNone(binding.error)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_git_url_slug_canonicalizes_correctly(self) -> None:
        """A ``.git`` URL like ``alfloop-dev/oday-data-platform.git`` must
        resolve after normalization strips the suffix."""
        config, checkout, tmpdir = self._make_env(
            "https://github.com/alfloop-dev/oday-data-platform.git"
        )
        try:
            binding = multi_repo_registry.resolve_task_repository(
                config,
                {"id": "T-GIT", "repository": "alfloop-dev/oday-data-platform.git"},
            )
            self.assertTrue(binding.resolved, f"unexpected error: {binding.error}")
            self.assertEqual(binding.repo_id, "oday_data_platform")
            self.assertEqual(binding.slug, "alfloop-dev/oday-data-platform")
            self.assertEqual(binding.root, checkout.resolve())
            self.assertIsNone(binding.error)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_display_name_canonicalizes_to_slug(self) -> None:
        """A display name like ``oday-data-platform`` must resolve correctly."""
        config, checkout, tmpdir = self._make_env(
            "https://github.com/alfloop-dev/oday-data-platform.git"
        )
        try:
            binding = multi_repo_registry.resolve_task_repository(
                config, {"id": "T-DISP", "repository": "oday-data-platform"},
            )
            self.assertTrue(binding.resolved, f"unexpected error: {binding.error}")
            self.assertEqual(binding.repo_id, "oday_data_platform")
            self.assertEqual(binding.slug, "alfloop-dev/oday-data-platform")
            self.assertIsNone(binding.error)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_wrong_origin_still_fails_closed(self) -> None:
        """A checkout pointing at a different origin must still be rejected."""
        config, checkout, tmpdir = self._make_env(
            "https://github.com/alfloop-dev/odayplus.git"
        )
        try:
            binding = multi_repo_registry.resolve_task_repository(
                config, {"id": "T-WRONG", "repository": "oday_data_platform"},
            )
            self.assertFalse(binding.resolved)
            self.assertIn("repository_checkout_mismatch", binding.error or "")
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_mismatch_error_shows_real_compared_values(self) -> None:
        """The error must show the normalized slug that was actually compared,
        not the raw registry ID or some other un-normalized value."""
        config, checkout, tmpdir = self._make_env(
            "https://github.com/alfloop-dev/odayplus.git"
        )
        try:
            binding = multi_repo_registry.resolve_task_repository(
                config, {"id": "T-MSG", "repository": "oday_data_platform"},
            )
            self.assertFalse(binding.resolved)
            error = binding.error or ""
            # The error must contain the actual origin slug and the expected slug
            self.assertIn("alfloop-dev/odayplus", error)
            self.assertIn("alfloop-dev/oday-data-platform", error)
            # The error must NOT contain the raw registry ID as the expected value
            self.assertNotIn("expected oday_data_platform", error)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_matching_repo_id_normalizes_git_suffix(self) -> None:
        """matching_repo_id must strip .git before comparison so that
        ``alfloop-dev/oday-data-platform.git`` resolves to the registered
        slug ``alfloop-dev/oday-data-platform``."""
        # .git suffix on a known slug
        self.assertEqual(
            multi_repo_registry.matching_repo_id({}, "alfloop-dev/oday-data-platform.git"),
            "oday_data_platform",
        )
        # Without .git (baseline)
        self.assertEqual(
            multi_repo_registry.matching_repo_id({}, "alfloop-dev/oday-data-platform"),
            "oday_data_platform",
        )
        # Alias / display name — no .git, still works
        self.assertEqual(
            multi_repo_registry.matching_repo_id({}, "oday-data-platform"),
            "oday_data_platform",
        )
        # Unknown repo with .git still returns None
        self.assertIsNone(
            multi_repo_registry.matching_repo_id({}, "acme/widgets.git"),
        )


if __name__ == "__main__":
    unittest.main()
