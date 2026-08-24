from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from product_ops.deployment.staging_lifecycle import validate_module_contract

MODULE_DIR = Path(__file__).resolve().parents[1] / "modules" / "ephemeral_staging"


class EphemeralStagingModuleContractTests(unittest.TestCase):
    def test_ephemeral_staging_module_structure_and_tokens(self) -> None:
        errors = validate_module_contract(MODULE_DIR)
        self.assertEqual(errors, [])

    def test_module_contains_isolated_resources(self) -> None:
        main_tf = (MODULE_DIR / "main.tf").read_text(encoding="utf-8")

        # Database and user isolation
        self.assertIn('resource "google_sql_database" "staging"', main_tf)
        self.assertIn('resource "google_sql_user" "staging"', main_tf)
        self.assertIn('resource "random_password" "staging_db"', main_tf)

        # Bucket isolation
        self.assertIn('resource "google_storage_bucket" "staging_data"', main_tf)

        # Service Account isolation
        self.assertIn('resource "google_service_account" "staging_runtime"', main_tf)
        self.assertIn('resource "google_service_account" "staging_web"', main_tf)
        self.assertIn('resource "google_service_account" "staging_worker"', main_tf)

        # Cloud Run services
        self.assertIn('resource "google_cloud_run_v2_service" "staging_api"', main_tf)
        self.assertIn('resource "google_cloud_run_v2_service" "staging_web"', main_tf)

        # PubSub messaging
        self.assertIn('resource "google_pubsub_topic" "staging_jobs"', main_tf)
        self.assertIn('resource "google_pubsub_subscription" "staging_jobs"', main_tf)

        # Paused Scheduler trigger
        self.assertIn('resource "google_cloud_scheduler_job" "staging_worker_trigger"', main_tf)
        self.assertTrue(re.search(r"paused\s*=\s*true", main_tf))

    def test_module_variables_validation_rules(self) -> None:
        vars_tf = (MODULE_DIR / "variables.tf").read_text(encoding="utf-8")

        self.assertIn('variable "release_id"', vars_tf)
        self.assertIn('variable "candidate_sha"', vars_tf)
        self.assertIn('variable "manifest_digest"', vars_tf)
        self.assertIn('variable "api_image"', vars_tf)
        self.assertIn('variable "web_image"', vars_tf)
        self.assertIn('variable "ttl_hours"', vars_tf)
        self.assertIn('variable "created_at"', vars_tf)
        self.assertIn('var.ttl_hours >= 1 && var.ttl_hours <= 168', vars_tf)

    def test_module_no_dynamic_timestamp_leak(self) -> None:
        main_tf = (MODULE_DIR / "main.tf").read_text(encoding="utf-8")
        # Direct dynamic timestamp() is forbidden during apply to prevent perpetual diffs,
        # but plan-time plantimestamp() is required in lifecycle preconditions for future guard.
        self.assertFalse(re.search(r"(?<!plan)timestamp\(\)", main_tf))
        self.assertIn("plantimestamp()", main_tf)
        self.assertNotIn('"2026-08-24T00:00:00Z"', main_tf)
        self.assertIn("timeadd(local.created_at", main_tf)
        self.assertIn("var.created_at", main_tf)

    def test_release_and_owner_identity_matches_python_normalization_order(self) -> None:
        main_tf = (MODULE_DIR / "main.tf").read_text(encoding="utf-8")
        # Terraform must lowercase before replacing punctuation, matching
        # staging_lifecycle.sanitize_release_suffix / bounded_label_value for IDs such as REL_1.0 or ODP_TASK_001.
        self.assertIn(
            'replace(lower(var.release_id), "/[^a-z0-9-]/", "-")',
            main_tf,
        )
        self.assertIn(
            'replace(lower(var.owner_task_id), "/[^a-z0-9_-]/", "-")',
            main_tf,
        )
        self.assertIn(
            'replace(lower(local.tenant_id), "/[^a-z0-9_-]/", "-")',
            main_tf,
        )

    def test_provider_configuration_and_resource_projects(self) -> None:
        main_tf = (MODULE_DIR / "main.tf").read_text(encoding="utf-8")
        self.assertIn('provider "google"', main_tf)
        self.assertIn("project = var.project_id", main_tf)
        self.assertIn("region  = var.region", main_tf)

    def test_scheduler_worker_invoker_iam_binding(self) -> None:
        main_tf = (MODULE_DIR / "main.tf").read_text(encoding="utf-8")
        self.assertIn('resource "google_cloud_run_v2_service_iam_member" "staging_worker_invokes_api"', main_tf)
        self.assertIn("serviceAccount:${google_service_account.staging_worker.email}", main_tf)
        self.assertIn("google_cloud_run_v2_service_iam_member.staging_worker_invokes_api", main_tf)

    def test_tenant_isolation_contract(self) -> None:
        vars_tf = (MODULE_DIR / "variables.tf").read_text(encoding="utf-8")
        main_tf = (MODULE_DIR / "main.tf").read_text(encoding="utf-8")
        outputs_tf = (MODULE_DIR / "outputs.tf").read_text(encoding="utf-8")

        self.assertIn('variable "tenant_id"', vars_tf)
        self.assertIn('tenant                 = local.tenant_label', main_tf)
        self.assertIn('tenant_id            = local.tenant_id', main_tf)
        self.assertIn('name  = "ODP_TENANT_ID"', main_tf)
        self.assertIn('name  = "ODP_SCHEDULED_INGESTION_TENANT_ID"', main_tf)
        self.assertIn('"X-Tenant-Id" = local.tenant_id', main_tf)
        self.assertIn('output "staging_tenant_id"', outputs_tf)

    def test_mandatory_labels_win_over_additional_labels(self) -> None:
        main_tf = (MODULE_DIR / "main.tf").read_text(encoding="utf-8")
        self.assertLess(main_tf.index("var.additional_labels"), main_tf.index("app                    = \"oday-plus\""))
        self.assertIn('resource "terraform_data" "staging_ownership"', main_tf)

    def test_creation_and_owner_inputs_are_required(self) -> None:
        vars_tf = (MODULE_DIR / "variables.tf").read_text(encoding="utf-8")
        self.assertIn("Required fixed RFC3339 timestamp", vars_tf)
        self.assertIn("owner_task_id must be a non-empty task identifier", vars_tf)
        self.assertNotIn('default     = ""', vars_tf)

    def test_module_outputs_do_not_leak_secrets(self) -> None:
        outputs_tf = (MODULE_DIR / "outputs.tf").read_text(encoding="utf-8")

        for forbidden in ("password", "result", "secret_data"):
            self.assertNotIn(forbidden, outputs_tf)

        self.assertIn('output "staging_api_uri"', outputs_tf)
        self.assertIn('output "staging_web_uri"', outputs_tf)
        self.assertIn('output "staging_database_name"', outputs_tf)
        self.assertIn('output "staging_data_bucket"', outputs_tf)
        self.assertIn('output "staging_tenant_id"', outputs_tf)
        self.assertIn('output "resource_labels"', outputs_tf)

    def test_cross_implementation_tenant_and_owner_normalization(self) -> None:
        from product_ops.deployment.staging_lifecycle import (
            bounded_label_value,
            generate_staging_labels,
            tenant_label_value,
        )

        tenant_id = "custom_tenant"
        owner_task_id = "ODP_TASK_001"
        release_id = "odp-20260824-001"
        candidate_sha = "0" * 40
        manifest_digest = "sha256:" + "0" * 64

        labels = generate_staging_labels(
            release_id=release_id,
            candidate_sha=candidate_sha,
            manifest_digest=manifest_digest,
            owner_task_id=owner_task_id,
            tenant_id=tenant_id,
        )

        self.assertEqual(labels["tenant"], "custom_tenant")
        self.assertEqual(labels["owner_task"], "odp_task_001")
        self.assertEqual(bounded_label_value(tenant_id), "custom_tenant")
        self.assertEqual(bounded_label_value(owner_task_id), "odp_task_001")
        self.assertEqual(tenant_label_value(tenant_id), "custom_tenant")

    def test_terraform_standalone_plan_guards_future_timestamp_and_accepts_valid(self) -> None:
        import shutil
        import subprocess
        import tempfile
        from datetime import UTC, datetime

        if not shutil.which("terraform"):
            self.skipTest("terraform binary not available in environment")

        valid_now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        future_ts = "2099-01-01T00:00:00Z"

        tfvars_content = {
            "project_id": "test-staging-proj",
            "region": "asia-east1",
            "release_id": "odp-20260824-001",
            "candidate_sha": "0" * 40,
            "manifest_digest": "sha256:" + "0" * 64,
            "api_image": "asia-east1-docker.pkg.dev/test/repo/api@sha256:" + "0" * 64,
            "web_image": "asia-east1-docker.pkg.dev/test/repo/web@sha256:" + "0" * 64,
            "ttl_hours": 24,
            "owner_task_id": "ODP_TASK_001",
            "tenant_id": "custom_tenant",
            "cloud_sql_instance_name": "test-db",
            "cloud_sql_connection_name": "test:asia-east1:test-db",
            "network_name": "test-vpc",
            "subnetwork_name": "test-subnet",
            "kms_key_id": "projects/p/locations/asia-east1/keyRings/r/cryptoKeys/k",
            "deployer_service_account_email": "deployer@test.iam.gserviceaccount.com",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            # Copy module files
            for f in ("main.tf", "variables.tf", "outputs.tf"):
                shutil.copy(MODULE_DIR / f, tmppath / f)

            # Init terraform
            init_res = subprocess.run(
                ["terraform", f"-chdir={tmppath}", "init", "-backend=false"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(init_res.returncode, 0, f"terraform init failed: {init_res.stderr}")

            # 1. Test future timestamp produces plan failure
            future_vars = dict(tfvars_content)
            future_vars["created_at"] = future_ts
            (tmppath / "future.tfvars.json").write_text(json.dumps(future_vars), encoding="utf-8")

            future_plan = subprocess.run(
                ["terraform", f"-chdir={tmppath}", "plan", "-var-file=future.tfvars.json"],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(
                future_plan.returncode,
                0,
                "Terraform plan MUST fail closed on future created_at timestamp.",
            )
            self.assertIn("created_at cannot be in the future", future_plan.stderr + future_plan.stdout)

            # 2. Test valid current timestamp produces plan success
            valid_vars = dict(tfvars_content)
            valid_vars["created_at"] = valid_now
            (tmppath / "valid.tfvars.json").write_text(json.dumps(valid_vars), encoding="utf-8")

            valid_plan = subprocess.run(
                ["terraform", f"-chdir={tmppath}", "plan", "-var-file=valid.tfvars.json"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                valid_plan.returncode,
                0,
                f"Terraform plan failed for valid inputs: {valid_plan.stderr}\n{valid_plan.stdout}",
            )



class EphemeralStagingDefaultTenantPlanTests(unittest.TestCase):
    """Plan the module with the tfvars the default create path actually writes.

    The pre-existing plan coverage always passed an explicit ``tenant_id``, so it
    never exercised the CLI default (``--tenant-id`` omitted). That default used
    to emit ``tenant_id: ""``, which the module's variable validation rejected,
    making every live create fail closed before provisioning.
    """

    TENANT_OUTPUT_PATTERN = re.compile(r'staging_tenant_id\s*=\s*"([^"]+)"')

    @classmethod
    def setUpClass(cls) -> None:
        import shutil
        import subprocess
        import tempfile

        if not shutil.which("terraform"):
            raise unittest.SkipTest("terraform binary not available in environment")

        cls._tmpdir = tempfile.TemporaryDirectory()
        cls.workdir = Path(cls._tmpdir.name)
        for filename in ("main.tf", "variables.tf", "outputs.tf"):
            shutil.copy(MODULE_DIR / filename, cls.workdir / filename)

        init_res = subprocess.run(
            ["terraform", f"-chdir={cls.workdir}", "init", "-backend=false"],
            capture_output=True,
            text=True,
        )
        if init_res.returncode != 0:
            cls._tmpdir.cleanup()
            raise unittest.SkipTest(f"terraform init unavailable: {init_res.stderr}")

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "_tmpdir"):
            cls._tmpdir.cleanup()

    def _config(self, release_id: str):
        from datetime import UTC, datetime

        from product_ops.deployment.staging_lifecycle import StagingConfig

        return StagingConfig(
            release_id=release_id,
            candidate_sha="0" * 40,
            manifest_digest="sha256:" + "0" * 64,
            project_id="test-staging-proj",
            owner_task_id="ODP_TASK_001",
            api_image="asia-east1-docker.pkg.dev/test/repo/api@sha256:" + "0" * 64,
            web_image="asia-east1-docker.pkg.dev/test/repo/web@sha256:" + "0" * 64,
            cloud_sql_instance_name="test-db",
            cloud_sql_connection_name="test:asia-east1:test-db",
            network_name="test-vpc",
            subnetwork_name="test-subnet",
            kms_key_id="projects/p/locations/asia-east1/keyRings/r/cryptoKeys/k",
            deployer_service_account_email="deployer@test.iam.gserviceaccount.com",
            created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    def _plan(self, name: str, tfvars: dict) -> str:
        import subprocess

        var_file = f"{name}.tfvars.json"
        (self.workdir / var_file).write_text(json.dumps(tfvars), encoding="utf-8")
        result = subprocess.run(
            ["terraform", f"-chdir={self.workdir}", "plan", "-no-color", f"-var-file={var_file}"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"terraform plan failed for {name}: {result.stderr}\n{result.stdout}",
        )
        return result.stdout + result.stderr

    def _planned_tenant(self, output: str) -> str:
        match = self.TENANT_OUTPUT_PATTERN.search(output)
        self.assertIsNotNone(match, f"plan did not expose staging_tenant_id:\n{output}")
        return match.group(1)

    def test_default_generated_tfvars_plan_succeeds_with_derived_tenant(self) -> None:
        from product_ops.deployment.staging_lifecycle import (
            derive_release_tenant_id,
            generate_tfvars,
        )

        release_id = "odp-20260824-001"
        config = self._config(release_id)
        self.assertEqual(config.tenant_id, "", "probe must exercise the no-tenant default")

        tfvars = generate_tfvars(config)
        planned_tenant = self._planned_tenant(self._plan("default_tenant", tfvars))

        self.assertEqual(planned_tenant, derive_release_tenant_id(release_id))

    def test_plan_tolerates_an_explicitly_empty_tenant_id(self) -> None:
        from product_ops.deployment.staging_lifecycle import (
            derive_release_tenant_id,
            generate_tfvars,
        )

        release_id = "odp-20260824-001"
        tfvars = generate_tfvars(self._config(release_id))
        tfvars["tenant_id"] = ""

        planned_tenant = self._planned_tenant(self._plan("empty_tenant", tfvars))

        self.assertEqual(planned_tenant, derive_release_tenant_id(release_id))

    def test_terraform_and_python_derive_the_same_bounded_tenant(self) -> None:
        from product_ops.deployment.staging_lifecycle import (
            MAX_TENANT_ID_LENGTH,
            derive_release_tenant_id,
            generate_tfvars,
        )

        release_id = "odp-" + "x" * 120
        tfvars = generate_tfvars(self._config(release_id))
        tfvars["tenant_id"] = ""

        planned_tenant = self._planned_tenant(self._plan("long_release_tenant", tfvars))

        self.assertEqual(planned_tenant, derive_release_tenant_id(release_id))
        self.assertLessEqual(len(planned_tenant), MAX_TENANT_ID_LENGTH)

    def test_explicit_tenant_still_wins_over_the_derived_one(self) -> None:
        from product_ops.deployment.staging_lifecycle import generate_tfvars

        config = self._config("odp-20260824-001")
        tfvars = generate_tfvars(config)
        tfvars["tenant_id"] = "custom-tenant-42"

        planned_tenant = self._planned_tenant(self._plan("explicit_tenant", tfvars))

        self.assertEqual(planned_tenant, "custom-tenant-42")


if __name__ == "__main__":
    unittest.main()

