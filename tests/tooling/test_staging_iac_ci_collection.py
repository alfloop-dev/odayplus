#!/usr/bin/env python3
"""Tooling tests for Ephemeral Staging Terraform CI Collection Gate.

Guarantees:
1. `.github/workflows/ci.yml` sets up pinned Terraform (1.9.8) and collects `infra` in the `orchestrator` job.
2. `pyproject.toml` includes `infra` in `[tool.pytest.ini_options].testpaths`.
3. Change scope classification recognizes `infra/terraform/` as development tooling, ensuring the `orchestrator` job runs the suite without being skipped.
4. All 18 ephemeral staging contract and plan tests are collected and fail closed (no green skips) in CI when Terraform or init is unavailable.
5. The selection CI actually runs collects those 18 tests for real, so a marker
   expression or a conftest that silently empties the run cannot pass as wiring.
6. The Terraform plan probes run offline: no GCP credentials in scope, no remote
   backend, and no apply/destroy reachable from the test harness.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
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
EPHEMERAL_TEST_RELPATH = EPHEMERAL_TEST_PATH.relative_to(REPO_ROOT)
EPHEMERAL_NODE_PREFIX = f"{EPHEMERAL_TEST_RELPATH.as_posix()}::"
EXPECTED_EPHEMERAL_TEST_COUNT = 18
EXACT_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def pinned_terraform_version(jobs: dict) -> str:
    """Return the terraform version the orchestrator job pins."""

    steps = jobs.get("orchestrator", {}).get("steps", [])
    pins = [
        str(step.get("with", {}).get("terraform_version", ""))
        for step in steps
        if isinstance(step, dict) and str(step.get("uses", "")).startswith("hashicorp/setup-terraform")
    ]
    if len(pins) != 1:
        raise AssertionError(f"expected exactly one setup-terraform step, found {len(pins)}")
    return pins[0]


def terraform_plan_job(jobs: dict) -> str:
    """Return the workflow job id that installs Terraform for the plan probes.

    Derived from the workflow rather than hard-coded, so that moving the plan
    probes to a different job moves this requirement with them instead of
    leaving it asserted against a job that no longer runs them.
    """

    owners = [
        job_id
        for job_id, job in jobs.items()
        if isinstance(job, dict)
        and any(
            isinstance(step, dict) and str(step.get("uses", "")).startswith("hashicorp/setup-terraform")
            for step in job.get("steps", [])
        )
    ]
    if len(owners) != 1:
        raise AssertionError(f"expected exactly one job to install terraform, found {owners}")
    return owners[0]


def job_pytest_command(jobs: dict, job_id: str = "orchestrator") -> str:
    """Return the single `uv run pytest` line the named job executes."""

    runs = [
        str(step.get("run", ""))
        for step in jobs.get(job_id, {}).get("steps", [])
        if isinstance(step, dict) and "uv run pytest" in str(step.get("run", ""))
    ]
    if len(runs) != 1:
        raise AssertionError(f"expected exactly one pytest step in the {job_id} job, found {len(runs)}")
    return runs[0]


def covers_ephemeral_staging_suite(target: str) -> bool:
    """True when a pytest target collects the ephemeral staging test file.

    Path containment rather than a substring: `infra`, `infra/terraform` and the
    file itself are all correct spellings, and a target that merely has "infra"
    somewhere in it is not.
    """

    candidate = Path(target)
    return EPHEMERAL_TEST_RELPATH == candidate or EPHEMERAL_TEST_RELPATH.is_relative_to(candidate)


def parse_pytest_selection(run_line: str) -> tuple[str, list[str]]:
    """Split a `uv run pytest ...` line into its `-m` expression and its targets."""

    tokens = shlex.split(run_line)
    tokens = tokens[tokens.index("pytest") + 1 :]
    marker = ""
    targets: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "-m":
            marker = tokens[index + 1]
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        targets.append(token)
        index += 1
    return marker, targets


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

    def test_orchestrator_pytest_targets_cover_the_ephemeral_staging_suite(self) -> None:
        _, targets = parse_pytest_selection(job_pytest_command(self.jobs))
        covering = [target for target in targets if covers_ephemeral_staging_suite(target)]
        self.assertTrue(
            covering,
            f"no orchestrator pytest target collects {EPHEMERAL_TEST_RELPATH}. Targets: {targets}",
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


class StagingIaCTerraformPinTests(unittest.TestCase):
    """The pinned Terraform version has to be the one the plan probes actually use.

    `shutil.which("terraform")` is satisfied by whatever the runner image happens
    to ship, and GitHub's ubuntu images have shipped a Terraform of their own. So
    deleting the setup-terraform step would not turn the plan tests red -- they
    would keep passing against an unpinned, undeclared version, and the pin in
    `ci.yml` would become documentation of something that is not happening.
    """

    def setUp(self) -> None:
        self.jobs = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8")).get("jobs", {})
        self.pin = pinned_terraform_version(self.jobs)
        self.plan_job = terraform_plan_job(self.jobs)

    def test_terraform_version_is_pinned_to_an_exact_release(self) -> None:
        self.assertRegex(
            self.pin,
            EXACT_SEMVER,
            f"terraform_version must be an exact release, not a range or 'latest'. Found: {self.pin!r}",
        )

    def test_ci_plans_with_the_pinned_terraform(self) -> None:
        # This file lives under tests/tooling, which *two* CI jobs collect: the
        # job that installs Terraform for the plan probes, and `product`, which
        # deliberately does not install it and does not run the probes. So the
        # requirement is keyed to the job actually running them, read out of
        # GITHUB_JOB. Keying it to `CI` instead only proves that some job
        # somewhere set CI=true, and turns `product` red for a tool it has no
        # reason to carry.
        in_plan_job = os.environ.get("GITHUB_JOB") == self.plan_job
        binary = shutil.which("terraform")
        if binary is None:
            self.assertFalse(
                in_plan_job,
                f"job {self.plan_job!r} runs the plan probes and must install the pinned "
                "terraform; without it those probes cannot run",
            )
            return

        result = subprocess.run(
            [binary, "version", "-json"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, f"terraform version failed: {result.stderr}")
        observed = json.loads(result.stdout)["terraform_version"]

        if in_plan_job:
            self.assertEqual(
                observed,
                self.pin,
                f"CI planned with terraform {observed} but ci.yml pins {self.pin}",
            )
        else:
            # A contributor is free to hold a different local Terraform; the
            # binding claim is about the job that installs the pin.
            self.assertTrue(observed, "terraform reported no version")

    def test_the_job_that_installs_terraform_is_the_one_that_runs_the_probes(self) -> None:
        # Guards the discriminator above. If the plan probes were moved to a job
        # that does not install Terraform, `test_ci_plans_with_the_pinned_terraform`
        # would quietly stop enforcing anything: GITHUB_JOB would never match, so
        # a missing binary would return early instead of failing.
        _, targets = parse_pytest_selection(job_pytest_command(self.jobs, self.plan_job))
        self.assertTrue(
            any(covers_ephemeral_staging_suite(target) for target in targets),
            f"job {self.plan_job!r} installs terraform but its pytest selection {targets} "
            "does not reach the ephemeral staging plan probes",
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


class StagingIaCCollectionExecutionTests(unittest.TestCase):
    """Collect the CI selection for real.

    Every other assertion in this file reads wiring: which targets the workflow
    names, which paths `testpaths` lists. Wiring can be entirely correct and the
    run still be empty -- a marker expression that deselects the suite, a
    `collect_ignore` in a conftest, or a rename of the test file all leave the
    workflow line looking exactly the same. So this replays the marker and the
    covering target straight out of `ci.yml` and asserts the node IDs come back.
    """

    def setUp(self) -> None:
        jobs = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8")).get("jobs", {})
        self.marker, targets = parse_pytest_selection(job_pytest_command(jobs))
        covering = [
            target
            for target in targets
            if EPHEMERAL_TEST_RELPATH == Path(target) or EPHEMERAL_TEST_RELPATH.is_relative_to(Path(target))
        ]
        self.assertTrue(covering, f"ci.yml collects no target covering {EPHEMERAL_TEST_RELPATH}")
        self.target = covering[0]

    def _collect(self, marker: str, target: str) -> list[str]:
        command = [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            # `addopts` in pyproject.toml is already `-q`; leaving it in place
            # would make this `-qq`, which prints per-file counts instead of the
            # node IDs this assertion is about.
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
        ]
        if marker:
            command += ["-m", marker]
        command.append(target)
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        # 5 is pytest's "collected nothing", which is a legitimate observation
        # here rather than a broken invocation; anything else means the probe
        # itself failed and must not be read as an empty selection.
        self.assertIn(
            result.returncode,
            (0, 5),
            f"collection failed for {command}:\n{result.stdout}\n{result.stderr}",
        )
        return [line for line in result.stdout.splitlines() if line.startswith(EPHEMERAL_NODE_PREFIX)]

    def test_ci_selection_collects_every_ephemeral_staging_test(self) -> None:
        node_ids = self._collect(self.marker, self.target)
        self.assertEqual(
            len(node_ids),
            EXPECTED_EPHEMERAL_TEST_COUNT,
            f"ci.yml selection (-m {self.marker!r} {self.target}) collected {len(node_ids)} "
            f"ephemeral staging tests, expected {EXPECTED_EPHEMERAL_TEST_COUNT}:\n"
            + "\n".join(node_ids),
        )
        self.assertEqual(len(set(node_ids)), len(node_ids), "duplicate node IDs collected")

    def test_the_collection_probe_can_actually_observe_an_empty_selection(self) -> None:
        # A guard that cannot fail is not a guard. Deselect the suite through the
        # same code path and confirm the probe reports zero rather than passing
        # on a stale expectation.
        node_ids = self._collect("requires_live_env and not requires_live_env", self.target)
        self.assertEqual(node_ids, [])


class EphemeralStagingOfflineHarnessTests(unittest.TestCase):
    """The plan probes must not be able to reach a real project."""

    def test_credential_environment_is_stripped_for_terraform(self) -> None:
        from infra.terraform.tests.test_ephemeral_staging import offline_terraform_env

        planted = {
            "GOOGLE_APPLICATION_CREDENTIALS": "/tmp/adc.json",
            "GOOGLE_CREDENTIALS": "{}",
            "GCLOUD_PROJECT": "real-prod",
            "CLOUDSDK_AUTH_ACCESS_TOKEN": "ya29.secret",
            "GCP_PROJECT": "real-prod",
            "TF_VAR_credentials": "{}",
            "PATH": os.environ.get("PATH", ""),
        }
        with patch.dict(os.environ, planted, clear=True):
            env = offline_terraform_env(Path("/tmp/scratch-home"))

        for leaked in (
            "GOOGLE_CREDENTIALS",
            "GCLOUD_PROJECT",
            "CLOUDSDK_AUTH_ACCESS_TOKEN",
            "GCP_PROJECT",
            "TF_VAR_credentials",
        ):
            self.assertNotIn(leaked, env, f"{leaked} reached the terraform subprocess")
        # HOME is redirected so a developer's Application Default Credentials
        # file cannot stand in for a credential-free CI runner.
        self.assertEqual(env["HOME"], "/tmp/scratch-home")
        self.assertEqual(env["TF_INPUT"], "0")
        self.assertIn("PATH", env, "terraform must still be locatable")
        # The planted ADC path must not survive, but simply dropping it is not
        # enough: on a Google Cloud VM an unset GOOGLE_APPLICATION_CREDENTIALS
        # falls through to the metadata server and the provider authenticates
        # as the VM. The harness replaces it with a path that does not exist,
        # which ADC consults first and fails on, so the fallback is never
        # reached and the probes behave the same on a runner and on a VM.
        adc = env["GOOGLE_APPLICATION_CREDENTIALS"]
        self.assertNotEqual(adc, "/tmp/adc.json", "the ambient ADC path reached terraform")
        self.assertFalse(
            Path(adc).exists(),
            f"GOOGLE_APPLICATION_CREDENTIALS must point at a file that does not exist, got {adc}",
        )

    def test_the_offline_override_stays_out_of_the_production_module(self) -> None:
        from infra.terraform.tests.test_ephemeral_staging import (
            MODULE_DIR,
            OFFLINE_PROVIDER_OVERRIDE_FILENAME,
            write_offline_provider_override,
        )

        # Terraform only merges a file into the base configuration when its name
        # ends in `_override.tf`; under any other name the harness would be a
        # second `provider "google"` block, which is a duplicate-configuration
        # error rather than an override.
        self.assertTrue(
            OFFLINE_PROVIDER_OVERRIDE_FILENAME.endswith("_override.tf"),
            f"{OFFLINE_PROVIDER_OVERRIDE_FILENAME} is not a name Terraform treats as an override",
        )

        # The shim exists so the probes can plan without credentials. It must
        # never reach the module that actually gets deployed: a checked-in
        # `access_token` would pin the deployed provider to a dead credential,
        # and a real one would be a leaked secret.
        self.assertFalse(
            (MODULE_DIR / OFFLINE_PROVIDER_OVERRIDE_FILENAME).exists(),
            f"{OFFLINE_PROVIDER_OVERRIDE_FILENAME} was committed into the deployed module",
        )
        for tf_file in sorted(MODULE_DIR.glob("*.tf")):
            self.assertNotIn(
                "access_token",
                tf_file.read_text(encoding="utf-8"),
                f"{tf_file.name} hands the provider a credential; that belongs to the test harness only",
            )

        # Writing it lands beside a module copy, not in the module itself.
        with tempfile.TemporaryDirectory() as scratch:
            written = write_offline_provider_override(Path(scratch))
            self.assertEqual(written.parent, Path(scratch))
            self.assertIn("access_token", written.read_text(encoding="utf-8"))

    def test_harness_refuses_state_changing_terraform_subcommands(self) -> None:
        from infra.terraform.tests.test_ephemeral_staging import run_terraform

        with tempfile.TemporaryDirectory() as scratch:
            for subcommand in ("apply", "destroy", "import"):
                with self.assertRaises(AssertionError, msg=f"terraform {subcommand} was not refused"):
                    run_terraform(subcommand, chdir=Path(scratch), home=Path(scratch) / "home")

    def test_terraform_init_never_configures_a_remote_backend(self) -> None:
        from infra.terraform.tests import test_ephemeral_staging

        recorded: list[list[str]] = []

        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""

        def _record(command, **kwargs):
            recorded.append(command)
            return _Result()

        with tempfile.TemporaryDirectory() as scratch, patch.object(
            test_ephemeral_staging.subprocess, "run", _record
        ):
            test_ephemeral_staging.run_terraform(
                "init", chdir=Path(scratch), home=Path(scratch) / "home"
            )

        self.assertEqual(len(recorded), 1)
        self.assertIn("-backend=false", recorded[0])


if __name__ == "__main__":
    unittest.main()
