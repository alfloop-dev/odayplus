#!/usr/bin/env python3
"""Tooling tests for Ephemeral Staging Terraform CI Collection Gate.

Guarantees:
1. `.github/workflows/ci.yml` sets up pinned Terraform (1.9.8) and collects `infra` in the `orchestrator` job.
2. `pyproject.toml` includes `infra` in `[tool.pytest.ini_options].testpaths`.
3. Change scope classification recognizes `infra/terraform/` as development tooling, ensuring the `orchestrator` job runs the suite without being skipped.
4. All 18 ephemeral staging contract and plan tests are collected and fail closed (no green skips) in CI when Terraform or init is unavailable.
"""

from __future__ import annotations

import os
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from delivery_toolchain.governance.classify_change_review_scope import (
    classify_paths,
    load_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
EPHEMERAL_TEST_PATH = REPO_ROOT / "infra" / "terraform" / "tests" / "test_ephemeral_staging.py"


class StagingIaCCIWorkflowCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(WORKFLOW_PATH.is_file(), f"Workflow file missing: {WORKFLOW_PATH}")
        self.workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        self.jobs = self.workflow.get("jobs", {})

    def test_orchestrator_job_exists_and_runs_without_scope_skip(self) -> None:
        self.assertIn("orchestrator", self.jobs, "orchestrator job missing from ci.yml")
        orchestrator = self.jobs["orchestrator"]
        # The orchestrator job must run unconditionally on all PRs/pushes so development_tooling
        # (including infra/terraform/) changes are never skipped.
        self.assertNotIn(
            "if",
            orchestrator,
            "orchestrator job must not have an 'if' skip condition that bypasses development tooling",
        )

    def test_orchestrator_job_has_pinned_terraform_setup_step(self) -> None:
        orchestrator = self.jobs.get("orchestrator", {})
        steps = orchestrator.get("steps", [])

        tf_steps = [
            step
            for step in steps
            if isinstance(step, dict) and str(step.get("uses", "")).startswith("hashicorp/setup-terraform")
        ]
        self.assertEqual(
            len(tf_steps),
            1,
            "orchestrator job must contain exactly one hashicorp/setup-terraform step",
        )
        tf_step = tf_steps[0]
        step_with = tf_step.get("with", {})
        self.assertEqual(
            str(step_with.get("terraform_version", "")),
            "1.9.8",
            "Terraform version must be pinned to 1.9.8 in ci.yml",
        )
        self.assertFalse(
            step_with.get("terraform_wrapper", True),
            "terraform_wrapper must be false to avoid stdout/stderr interception in python subprocess tests",
        )

    def test_orchestrator_job_collects_infra_in_pytest_step(self) -> None:
        orchestrator = self.jobs.get("orchestrator", {})
        steps = orchestrator.get("steps", [])

        pytest_steps = [
            step
            for step in steps
            if isinstance(step, dict) and "uv run pytest" in str(step.get("run", ""))
        ]
        self.assertTrue(pytest_steps, "orchestrator job missing pytest execution step")
        runs = [step["run"] for step in pytest_steps]
        infra_collected = any("infra" in run.split() for run in runs)
        self.assertTrue(
            infra_collected,
            f"orchestrator pytest step must explicitly include 'infra' target. Found runs: {runs}",
        )

    def test_orchestrator_job_lints_infra_code(self) -> None:
        orchestrator = self.jobs.get("orchestrator", {})
        steps = orchestrator.get("steps", [])

        ruff_steps = [
            step
            for step in steps
            if isinstance(step, dict) and "uv run ruff check" in str(step.get("run", ""))
        ]
        self.assertTrue(ruff_steps, "orchestrator job missing ruff check step")
        runs = [step["run"] for step in ruff_steps]
        infra_linted = any("infra" in run.split() for run in runs)
        self.assertTrue(
            infra_linted,
            f"orchestrator ruff step must explicitly include 'infra' target. Found runs: {runs}",
        )


class StagingIaCPyprojectConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(PYPROJECT_PATH.is_file(), f"pyproject.toml missing: {PYPROJECT_PATH}")
        self.pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))

    def test_pyproject_testpaths_includes_infra(self) -> None:
        pytest_opts = self.pyproject.get("tool", {}).get("pytest", {}).get("ini_options", {})
        testpaths = pytest_opts.get("testpaths", [])
        self.assertIn(
            "infra",
            testpaths,
            f"pyproject.toml [tool.pytest.ini_options].testpaths must include 'infra'. Found: {testpaths}",
        )


class StagingIaCChangeScopeClassificationTests(unittest.TestCase):
    def test_infra_terraform_changes_are_classified_as_development_tooling(self) -> None:
        manifest = load_manifest()
        infra_paths = [
            "infra/terraform/modules/ephemeral_staging/main.tf",
            "infra/terraform/modules/ephemeral_staging/variables.tf",
            "infra/terraform/modules/ephemeral_staging/outputs.tf",
            "infra/terraform/tests/test_ephemeral_staging.py",
        ]
        result = classify_paths(infra_paths, manifest)
        self.assertEqual(
            result.get("scope"),
            "development_tooling",
            f"infra/terraform paths must classify as development_tooling. Result: {result}",
        )


class EphemeralStagingTestCollectionIntegrityTests(unittest.TestCase):
    EXPECTED_TEST_COUNT = 18

    def test_ephemeral_staging_test_file_exists(self) -> None:
        self.assertTrue(
            EPHEMERAL_TEST_PATH.is_file(),
            f"Test file missing: {EPHEMERAL_TEST_PATH}",
        )

    def test_all_18_contract_and_plan_tests_defined(self) -> None:
        from infra.terraform.tests import test_ephemeral_staging

        contract_tests = [
            attr
            for attr in dir(test_ephemeral_staging.EphemeralStagingModuleContractTests)
            if attr.startswith("test_")
        ]
        plan_tests = [
            attr
            for attr in dir(test_ephemeral_staging.EphemeralStagingDefaultTenantPlanTests)
            if attr.startswith("test_")
        ]

        # EphemeralStagingModuleContractTests has 13 contract tests + 1 standalone plan test = 14
        # EphemeralStagingDefaultTenantPlanTests has 4 default/empty/long/explicit tenant plan tests = 4
        # Total = 18 tests
        self.assertEqual(
            len(contract_tests) + len(plan_tests),
            self.EXPECTED_TEST_COUNT,
            f"Expected {self.EXPECTED_TEST_COUNT} total tests, got {len(contract_tests)} contract and {len(plan_tests)} plan tests",
        )
        self.assertIn("test_ephemeral_staging_module_structure_and_tokens", contract_tests)
        self.assertIn("test_module_contains_isolated_resources", contract_tests)
        self.assertIn("test_all_release_scoped_cloud_run_resources_use_controlled_vpc_egress", contract_tests)
        self.assertIn("test_module_variables_validation_rules", contract_tests)
        self.assertIn("test_module_no_dynamic_timestamp_leak", contract_tests)
        self.assertIn("test_release_and_owner_identity_matches_python_normalization_order", contract_tests)
        self.assertIn("test_provider_configuration_and_resource_projects", contract_tests)
        self.assertIn("test_scheduler_worker_invoker_iam_binding", contract_tests)
        self.assertIn("test_tenant_isolation_contract", contract_tests)
        self.assertIn("test_mandatory_labels_win_over_additional_labels", contract_tests)
        self.assertIn("test_creation_and_owner_inputs_are_required", contract_tests)
        self.assertIn("test_module_outputs_do_not_leak_secrets", contract_tests)
        self.assertIn("test_cross_implementation_tenant_and_owner_normalization", contract_tests)
        self.assertIn("test_terraform_standalone_plan_guards_future_timestamp_and_accepts_valid", contract_tests)

        self.assertIn("test_default_generated_tfvars_plan_succeeds_with_derived_tenant", plan_tests)
        self.assertIn("test_plan_tolerates_an_explicitly_empty_tenant_id", plan_tests)
        self.assertIn("test_terraform_and_python_derive_the_same_bounded_tenant", plan_tests)
        self.assertIn("test_explicit_tenant_still_wins_over_the_derived_one", plan_tests)

    def test_ephemeral_staging_fails_closed_in_ci_when_terraform_missing(self) -> None:
        from infra.terraform.tests.test_ephemeral_staging import (
            EphemeralStagingDefaultTenantPlanTests,
            EphemeralStagingModuleContractTests,
        )

        with patch("shutil.which", return_value=None), patch.dict(os.environ, {"CI": "true"}):
            instance = EphemeralStagingModuleContractTests(
                methodName="test_terraform_standalone_plan_guards_future_timestamp_and_accepts_valid"
            )
            with self.assertRaises(AssertionError):
                instance.test_terraform_standalone_plan_guards_future_timestamp_and_accepts_valid()

            with self.assertRaises(AssertionError):
                EphemeralStagingDefaultTenantPlanTests.setUpClass()


if __name__ == "__main__":
    unittest.main()
