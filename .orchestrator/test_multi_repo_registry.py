#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import multi_repo_registry


class MultiRepoRegistryTests(unittest.TestCase):
    def test_default_registry_includes_execute_plans_checkout(self) -> None:
        repo = multi_repo_registry.resolve_repository({}, "execute_plans")

        self.assertEqual(repo["display_name"], "execute-plans")
        self.assertEqual(repo["repo"], "ajoe734/execute-plans")
        self.assertEqual(repo["default_branch"], "main")
        self.assertEqual(repo["resolved_local_path"], multi_repo_registry.resolve_path("../execute-plans"))

    def test_execute_plans_artifact_prefix_routes_to_sibling_repo(self) -> None:
        artifact = "execute-plans/e2e/dummy.spec.ts"

        self.assertEqual(multi_repo_registry.artifact_repository_id({}, artifact), "execute_plans")
        self.assertEqual(
            multi_repo_registry.repository_relative_artifact_path({}, artifact),
            Path("e2e/dummy.spec.ts"),
        )
        self.assertEqual(
            multi_repo_registry.artifact_local_path({}, artifact),
            multi_repo_registry.resolve_path("../execute-plans") / "e2e" / "dummy.spec.ts",
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
        self.assertEqual(multi_repo_registry.task_primary_repository_id({}, task), "execute_plans")

    def test_task_primary_repository_rejects_multiple_non_pantheon_repos(self) -> None:
        task = {
            "id": "CROSS-REPO",
            "artifacts": [
                "execute-plans/e2e/dummy.spec.ts",
                "front-ai-trading-system/src/routes/dummy.tsx",
            ],
        }

        self.assertIsNone(multi_repo_registry.task_primary_repository_id({}, task))

    def test_oday_data_platform_uses_fallback_checkout_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            missing = tmp_root / "missing"
            fallback = tmp_root / "oday-data-platform-supervisor"
            fallback.mkdir()

            def fake_resolve_path(value):
                if value == "../oday-data-platform":
                    return missing
                if value == "../oday-data-platform-supervisor":
                    return fallback
                return None

            with mock.patch.object(multi_repo_registry, "resolve_path", fake_resolve_path):
                repo = multi_repo_registry.resolve_repository({}, "oday_data_platform")

            self.assertEqual(repo["resolved_local_path"], fallback)


if __name__ == "__main__":
    unittest.main()
