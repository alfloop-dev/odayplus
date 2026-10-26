#!/usr/bin/env python3
"""Tests for Assisted Listing Intake Design Contract Gate workflow path filtering."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from delivery_toolchain.governance.check_code_boundaries import glob_regex

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "assisted-intake-design-validation.yml"
MIGRATION_DIR = REPO_ROOT / "infra" / "db" / "migrations" / "assisted_listing_intake"

EXPECTED_MIGRATION_FILES = {
    "infra/db/migrations/assisted_listing_intake/000_canonical_compatibility.sql",
    "infra/db/migrations/assisted_listing_intake/001_baseline.sql",
    "infra/db/migrations/assisted_listing_intake/002_consistency.sql",
    "infra/db/migrations/assisted_listing_intake/003_promotion_state.sql",
    "infra/db/migrations/assisted_listing_intake/004_tenant_rls_lineage.sql",
    "infra/db/migrations/assisted_listing_intake/README.md",
    "infra/db/migrations/assisted_listing_intake/downgrade.sql",
}


class AssistedIntakeDesignValidationPathsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(WORKFLOW_PATH.is_file(), f"Workflow file missing: {WORKFLOW_PATH}")
        self.content = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        on_block = self.content.get("on") or self.content.get(True) or {}
        self.paths = on_block.get("pull_request", {}).get("paths", [])

    def test_workflow_has_migrations_path_trigger(self) -> None:
        pattern = "infra/db/migrations/assisted_listing_intake/**"
        self.assertIn(
            pattern,
            self.paths,
            f"Expected {pattern!r} in workflow pull_request.paths",
        )

    def test_migration_pattern_matches_all_assisted_intake_ddl(self) -> None:
        pattern = "infra/db/migrations/assisted_listing_intake/**"
        regex = glob_regex(pattern)
        actual_files = {
            p.relative_to(REPO_ROOT).as_posix()
            for p in MIGRATION_DIR.rglob("*")
            if p.is_file()
        }
        self.assertEqual(
            actual_files,
            EXPECTED_MIGRATION_FILES,
            "Actual migration files do not match expected set",
        )
        for path in actual_files:
            self.assertTrue(
                regex.fullmatch(path),
                f"Pattern {pattern} failed to match {path}",
            )

    def test_migration_pattern_does_not_match_unrelated_migrations(self) -> None:
        pattern = "infra/db/migrations/assisted_listing_intake/**"
        regex = glob_regex(pattern)
        unrelated = [
            "infra/db/migrations/000001_baseline_canonical_schema.sql",
            "infra/db/migrations/000002_data_domain_canonical_entities.sql",
            "infra/db/migrations/000016_alert_precision_tracking.sql",
        ]
        for path in unrelated:
            self.assertFalse(
                regex.fullmatch(path),
                f"Pattern {pattern} unexpectedly matched unrelated path {path}",
            )

    def test_every_declared_path_pattern_matches_at_least_one_file(self) -> None:
        for pattern in self.paths:
            regex = glob_regex(pattern)
            matched = any(
                regex.fullmatch(p.relative_to(REPO_ROOT).as_posix())
                for p in REPO_ROOT.rglob("*")
                if p.is_file() and not p.relative_to(REPO_ROOT).as_posix().startswith(".git/")
            )
            self.assertTrue(
                matched,
                f"Workflow path pattern {pattern!r} matches no files in the repository",
            )


if __name__ == "__main__":
    unittest.main()
