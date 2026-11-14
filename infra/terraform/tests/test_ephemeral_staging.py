from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from product_ops.deployment.staging_lifecycle import validate_module_contract

MODULE_DIR = Path(__file__).resolve().parents[1] / "modules" / "ephemeral_staging"

# The plan probes below have to prove the module plans *offline*: no GCP login,
# no remote backend, no apply. Leaving that to "the runner happens to have no
# credentials" makes it a property of the machine rather than of the test, and
# the two machines disagree in both directions:
#
#   * on a Google Cloud VM, Application Default Credentials resolve through the
#     metadata server even with an empty environment and an empty HOME, so the
#     provider silently authenticates as that VM and the probe proves nothing;
#   * on a GitHub runner there is no metadata server, so the same provider
#     stops with "Attempted to load application default credentials since
#     neither `credentials` nor `access_token` was set in the provider block".
#
# So the harness fixes both ends rather than depending on either:
#
#   * an override file hands the provider a synthetic `access_token`, which is
#     the branch the provider takes *before* it ever looks for ADC;
#   * GOOGLE_APPLICATION_CREDENTIALS points at a file that does not exist.
#     That is the first source ADC consults and it fails outright instead of
#     falling through to the metadata server, so no ambient identity is
#     reachable even when the tests run on Google Cloud;
#   * every environment variable the google provider reads credentials from is
#     dropped, and HOME points at a scratch directory;
#   * only init/plan/validate are reachable, so no edit here can grow into an
#     apply or a destroy against a real project;
#   * init always carries -backend=false, so no remote state is configured.
CREDENTIAL_ENV_PREFIXES = ("GOOGLE_", "GCLOUD_", "CLOUDSDK_", "GCP_")
CREDENTIAL_ENV_NAMES = ("TF_VAR_credentials", "TF_TOKEN_app_terraform_io")
OFFLINE_TERRAFORM_SUBCOMMANDS = frozenset({"init", "plan", "validate"})

# The file name matters: Terraform treats `*_override.tf` as an override file
# and merges it into the base configuration argument by argument, so the
# module's own `project` and `region` survive and only `access_token` is added.
# A plain second `provider "google"` block would be a duplicate-configuration
# error instead. The token is never sent anywhere -- the module declares no
# data sources, so a create-only plan needs no API call to resolve.
OFFLINE_PROVIDER_OVERRIDE_FILENAME = "zz_offline_provider_override.tf"
OFFLINE_PROVIDER_OVERRIDE = """\
# Written by the test harness. Not part of the module under test: it exists so
# the provider configures without credentials, and it must never be applied.
provider "google" {
  access_token = "offline"
}
"""
ABSENT_ADC_FILENAME = "absent-application-default-credentials.json"


def offline_terraform_env(home: Path) -> dict[str, str]:
    """Return os.environ with every GCP credential source removed."""

    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(CREDENTIAL_ENV_PREFIXES) and key not in CREDENTIAL_ENV_NAMES
    }
    env["HOME"] = str(home)
    env["TF_IN_AUTOMATION"] = "1"
    env["TF_INPUT"] = "0"
    # Deliberately a path that is never created. See the module comment: this
    # is what stops ADC from reaching the metadata server on a Google Cloud VM.
    env["GOOGLE_APPLICATION_CREDENTIALS"] = str(home / ABSENT_ADC_FILENAME)
    return env


def write_offline_provider_override(module_dir: Path) -> Path:
    """Give the google provider a synthetic token so it never looks for ADC."""

    path = module_dir / OFFLINE_PROVIDER_OVERRIDE_FILENAME
    path.write_text(OFFLINE_PROVIDER_OVERRIDE, encoding="utf-8")
    return path


def run_terraform(
    subcommand: str,
    *args: str,
    chdir: Path,
    home: Path,
) -> subprocess.CompletedProcess[str]:
    """Run one read-only terraform subcommand with no credentials in scope."""

    if subcommand not in OFFLINE_TERRAFORM_SUBCOMMANDS:
        raise AssertionError(
            f"refusing to run 'terraform {subcommand}': these tests may only run "
            f"{sorted(OFFLINE_TERRAFORM_SUBCOMMANDS)} so they can never reach real infrastructure"
        )
    command = ["terraform", f"-chdir={chdir}", subcommand, *args]
    if subcommand == "init":
        command.append("-backend=false")
    home.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=offline_terraform_env(home),
        check=False,
    )


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

    def test_all_release_scoped_cloud_run_resources_use_controlled_vpc_egress(self) -> None:
        main_tf = (MODULE_DIR / "main.tf").read_text(encoding="utf-8")

        self.assertEqual(main_tf.count('egress = "ALL_TRAFFIC"'), 5)
        self.assertNotIn('egress = "PRIVATE_RANGES_ONLY"', main_tf)

    def test_module_variables_validation_rules(self) -> None:
        vars_tf = (MODULE_DIR / "variables.tf").read_text(encoding="utf-8")

        self.assertIn('variable "release_id"', vars_tf)
        self.assertIn('variable "candidate_sha"', vars_tf)
        self.assertIn('variable "manifest_digest"', vars_tf)
        self.assertIn('variable "api_image"', vars_tf)
        self.assertIn('variable "web_image"', vars_tf)
        self.assertIn('variable "worker_image"', vars_tf)
        self.assertIn('variable "scheduler_image"', vars_tf)
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

    def test_verify_operator_identity_grants_are_release_scoped(self) -> None:
        main_tf = (MODULE_DIR / "main.tf").read_text(encoding="utf-8")

        def block(kind: str, name: str) -> str:
            match = re.search(
                rf'resource "{kind}" "{name}" \{{\n(.*?)\n\}}', main_tf, re.DOTALL
            )
            self.assertIsNotNone(match, f"{kind}.{name} is missing")
            return match.group(1)

        token = block("google_service_account_iam_member", "deployer_id_token_runtime")
        self.assertIn("service_account_id = google_service_account.staging_runtime.name", token)
        self.assertIn('role               = "roles/iam.serviceAccountOpenIdTokenCreator"', token)
        self.assertIn('member             = "serviceAccount:${var.deployer_service_account_email}"', token)

        for name, service in (
            ("staging_runtime_invokes_api", "staging_api"),
            ("staging_runtime_invokes_web", "staging_web"),
        ):
            invoker = block("google_cloud_run_v2_service_iam_member", name)
            self.assertIn(f"name     = google_cloud_run_v2_service.{service}.name", invoker)
            self.assertIn('role     = "roles/run.invoker"', invoker)
            self.assertIn(
                'member   = "serviceAccount:${google_service_account.staging_runtime.email}"',
                invoker,
            )

        # Neither grant is widened to the project, and no service is public.
        self.assertNotRegex(
            main_tf,
            r'google_project_iam_(member|binding|policy)"[^}]*(serviceAccountOpenIdTokenCreator|run\.invoker)',
        )
        self.assertNotIn("allUsers", main_tf)
        self.assertNotIn("allAuthenticatedUsers", main_tf)

    def test_password_login_runtime_wiring_is_release_owned(self) -> None:
        """ODP-STAGING-PASSWORD-RUNTIME-WIRING-261114.

        Web password -> durable identity/session/throttle -> local JWT -> API
        verifier, wired only from release-owned resources. Precise declarations
        added for review: one release-owned secret
        (``<name_prefix>-identity-signing-key``) readable by exactly the runtime
        (API verifier) and Web (signer) accounts; the Web account additionally
        reads this release's database URL secret and holds
        ``roles/cloudsql.client`` on the staging project, exactly like the
        runtime account already does.
        """

        main_tf = (MODULE_DIR / "main.tf").read_text(encoding="utf-8")
        vars_tf = (MODULE_DIR / "variables.tf").read_text(encoding="utf-8")

        def block(kind: str, name: str) -> str:
            match = re.search(rf'resource "{kind}" "{name}" \{{\n(.*?)\n\}}', main_tf, re.DOTALL)
            self.assertIsNotNone(match, f"{kind}.{name} is missing")
            return match.group(1)

        def service(name: str) -> str:
            start = main_tf.index(f'resource "google_cloud_run_v2_service" "{name}"')
            end = main_tf.index("\nresource ", start + 1)
            return main_tf[start:end]

        secret = block("google_secret_manager_secret", "staging_identity_token_signing_key")
        self.assertIn('secret_id = "${local.name_prefix}-identity-signing-key"', secret)
        self.assertIn("labels = local.resource_labels", secret)
        version = block("google_secret_manager_secret_version", "staging_identity_token_signing_key")
        self.assertIn("secret_data = random_password.staging_identity_token_signing_key.result", version)

        # Exactly two readers of the signing key: API verifier and Web signer.
        readers = re.findall(
            r'secret_id = google_secret_manager_secret\.staging_identity_token_signing_key\.secret_id\n'
            r'\s*role\s*= "roles/secretmanager\.secretAccessor"\n'
            r'\s*member\s*= "serviceAccount:\$\{google_service_account\.(\w+)\.email\}"',
            main_tf,
        )
        self.assertEqual(sorted(readers), ["staging_runtime", "staging_web"])
        web_db = block("google_secret_manager_secret_iam_member", "staging_web_db_url")
        self.assertIn("secret_id = google_secret_manager_secret.staging_database_url.secret_id", web_db)
        self.assertIn("serviceAccount:${google_service_account.staging_web.email}", web_db)
        web_sql = block("google_project_iam_member", "staging_web_cloudsql")
        self.assertIn('role    = "roles/cloudsql.client"', web_sql)
        self.assertIn("serviceAccount:${google_service_account.staging_web.email}", web_sql)

        api = service("staging_api")
        web = service("staging_web")
        key_ref = (
            "secret  = google_secret_manager_secret.staging_identity_token_signing_key.secret_id\n"
            "            version = google_secret_manager_secret_version.staging_identity_token_signing_key.version"
        )
        db_ref = (
            "secret  = google_secret_manager_secret.staging_database_url.secret_id\n"
            "            version = google_secret_manager_secret_version.staging_database_url.version"
        )
        for text in (api, web):
            self.assertRegex(text, r'name\s*=\s*"ODP_AUTH_MODE"\s*\n\s*value\s*=\s*"local"')
            self.assertRegex(text, r'name\s*=\s*"ODP_AUTH_LOCAL_ISSUER"\s*\n\s*value\s*=\s*local\.local_identity_issuer')
            self.assertRegex(text, r'name\s*=\s*"ODP_AUTH_LOCAL_AUDIENCES"\s*\n\s*value\s*=\s*local\.local_identity_audience')
            # Pinned to the release-created version, never a floating "latest".
            self.assertIn(key_ref, text)
            self.assertIn(db_ref, text)
        self.assertRegex(web, r'name\s*=\s*"ODP_WEB_BASE_URL"\s*\n\s*value\s*=\s*var\.web_base_url')
        self.assertRegex(web, r'name\s*=\s*"ODP_WEB_ALLOW_LEGACY_TRUSTED_HEADERS"\s*\n\s*value\s*=\s*"false"')
        self.assertIn("instances = [var.cloud_sql_connection_name]", web)
        self.assertIn('mount_path = "/cloudsql"', web)
        self.assertIn("condition     = var.web_base_url != null", web)

        # Only the two services mount the key; jobs and the scheduler never do.
        self.assertEqual(main_tf.count('name = "ODP_IDENTITY_TOKEN_SIGNING_KEY"'), 2)
        self.assertNotIn("latest", re.sub(r"#.*", "", main_tf))
        # Local and smoke audiences are distinct release-bound values.
        self.assertIn('local_identity_audience = "urn:odp:staging-identity:${local.release_label}"', main_tf)
        self.assertIn('smoke_token_audience = "urn:odp:staging-smoke:${local.release_label}"', main_tf)
        # No OIDC, bootstrap, dev cohort or caller-supplied key input.
        for forbidden in (
            "ODP_WEB_OIDC",
            "ODP_AUTH_OIDC",
            "ODP_IDENTITY_BOOTSTRAP",
            "ODP_DEV_ADMIN",
            "ODP_AUTH_HS256_KEYS",
            "ODP_AUTH_LOCAL_HS256_KEYS",
            "identity_token_signing_key_ref",
        ):
            self.assertNotIn(forbidden, main_tf)
            self.assertNotIn(forbidden, vars_tf)
        self.assertNotRegex(vars_tf, r'variable\s+"[^"]*(signing|identity|session|secret)[^"]*"')

    def test_staging_api_is_reachable_by_the_runner_but_iam_only(self) -> None:
        main_tf = (MODULE_DIR / "main.tf").read_text(encoding="utf-8")
        match = re.search(
            r'resource "google_cloud_run_v2_service" "staging_api" \{\n(.*?)\n  template \{',
            main_tf,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "google_cloud_run_v2_service.staging_api is missing")
        header = match.group(1)

        # The GitHub-hosted verify runner reads the API directly (§12.5 item 3).
        self.assertRegex(header, r'(?m)^  ingress\s*=\s*"INGRESS_TRAFFIC_ALL"$')
        self.assertNotIn("INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER", header)
        # Reachability never means public: no service grants anonymous invoke.
        self.assertNotIn("allUsers", main_tf)
        self.assertNotIn("allAuthenticatedUsers", main_tf)

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
        self.assertIn('output "staging_project_id"', outputs_tf)
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
        from datetime import UTC, datetime

        if not shutil.which("terraform"):
            if os.environ.get("CI"):
                self.fail("terraform binary not available in CI environment")
            self.skipTest("terraform binary not available in environment")

        valid_now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        future_ts = "2099-01-01T00:00:00Z"

        tfvars_content = {
            "project_id": "test-staging-proj",
            # The governed staging console origin generate_tfvars writes.
            "web_base_url": "https://console-staging.oday-plus.com.tw",
            "region": "asia-east1",
            "release_id": "odp-20260824-001",
            "candidate_sha": "0" * 40,
            "manifest_digest": "sha256:" + "0" * 64,
            "api_image": "asia-east1-docker.pkg.dev/test/repo/api@sha256:" + "0" * 64,
            "web_image": "asia-east1-docker.pkg.dev/test/repo/web@sha256:" + "0" * 64,
            "worker_image": "asia-east1-docker.pkg.dev/test/repo/worker@sha256:" + "0" * 64,
            "scheduler_image": "asia-east1-docker.pkg.dev/test/repo/scheduler@sha256:" + "0" * 64,
            "ttl_hours": 24,
            "owner_task_id": "ODP_TASK_001",
            "release_profile": "core-ops",
            "tenant_id": "custom_tenant",
            "cloud_sql_instance_name": "test-db",
            "cloud_sql_connection_name": "test:asia-east1:test-db",
            "network_name": "test-vpc",
            "subnetwork_name": "test-subnet",
            "kms_key_id": "projects/test-staging-proj/locations/asia-east1/keyRings/staging/cryptoKeys/release",
            "deployer_service_account_email": "deployer@test-staging-proj.iam.gserviceaccount.com",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            # Copy module files
            for f in ("main.tf", "variables.tf", "outputs.tf"):
                if f == "main.tf":
                    # The live module uses a release-scoped GCS backend. This
                    # unit test deliberately plans offline, so remove only
                    # that backend stanza from the temporary copy.
                    main_text = (MODULE_DIR / f).read_text(encoding="utf-8")
                    backend_start = main_text.index("  # The workflow supplies a protected bucket")
                    backend_end = main_text.index("  required_providers", backend_start)
                    (tmppath / f).write_text(
                        main_text[:backend_start] + main_text[backend_end:],
                        encoding="utf-8",
                    )
                else:
                    shutil.copy(MODULE_DIR / f, tmppath / f)

            # Init terraform
            scratch_home = tmppath / "offline-home"
            write_offline_provider_override(tmppath)
            init_res = run_terraform("init", chdir=tmppath, home=scratch_home)
            self.assertEqual(init_res.returncode, 0, f"terraform init failed: {init_res.stderr}")

            # 1. Test future timestamp produces plan failure
            future_vars = dict(tfvars_content)
            future_vars["created_at"] = future_ts
            (tmppath / "future.tfvars.json").write_text(json.dumps(future_vars), encoding="utf-8")

            future_plan = run_terraform(
                "plan",
                "-var-file=future.tfvars.json",
                chdir=tmppath,
                home=scratch_home,
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

            valid_plan = run_terraform(
                "plan",
                "-var-file=valid.tfvars.json",
                chdir=tmppath,
                home=scratch_home,
            )
            self.assertEqual(
                valid_plan.returncode,
                0,
                f"Terraform plan failed for valid inputs: {valid_plan.stderr}\n{valid_plan.stdout}",
            )

            # 3. Without web_base_url the Web password sign-in origin is
            # unknown, so creating the Web service fails closed at plan.
            missing_vars = dict(valid_vars)
            del missing_vars["web_base_url"]
            (tmppath / "missing_web_base_url.tfvars.json").write_text(
                json.dumps(missing_vars), encoding="utf-8"
            )
            missing_plan = run_terraform(
                "plan",
                "-no-color",
                "-var-file=missing_web_base_url.tfvars.json",
                chdir=tmppath,
                home=scratch_home,
            )
            self.assertNotEqual(missing_plan.returncode, 0, "plan must refuse a Web without web_base_url")
            self.assertIn("web_base_url is required", missing_plan.stderr + missing_plan.stdout)

            # 4. Anything but a bare https origin is refused by the variable.
            for index, bad_origin in enumerate(
                (
                    "http://console-staging.oday-plus.com.tw",
                    "https://console-staging.oday-plus.com.tw/",
                    "https://console-staging.oday-plus.com.tw/login",
                    "https://console-staging.oday-plus.com.tw:8443",
                    "https://Console-Staging.oday-plus.com.tw",
                    "",
                )
            ):
                bad_vars = dict(valid_vars, web_base_url=bad_origin)
                bad_file = f"bad_web_base_url_{index}.tfvars.json"
                (tmppath / bad_file).write_text(json.dumps(bad_vars), encoding="utf-8")
                bad_plan = run_terraform(
                    "plan", "-no-color", f"-var-file={bad_file}", chdir=tmppath, home=scratch_home
                )
                self.assertNotEqual(bad_plan.returncode, 0, f"plan must refuse web_base_url={bad_origin!r}")
                self.assertIn("web_base_url must be a bare https origin", bad_plan.stderr + bad_plan.stdout)



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
        if not shutil.which("terraform"):
            if os.environ.get("CI"):
                raise AssertionError("terraform binary not available in CI environment")
            raise unittest.SkipTest("terraform binary not available in environment")

        cls._tmpdir = tempfile.TemporaryDirectory()
        cls.workdir = Path(cls._tmpdir.name) / "module"
        cls.workdir.mkdir()
        cls.scratch_home = Path(cls._tmpdir.name) / "offline-home"
        for filename in ("main.tf", "variables.tf", "outputs.tf"):
            if filename == "main.tf":
                main_text = (MODULE_DIR / filename).read_text(encoding="utf-8")
                backend_start = main_text.index("  # The workflow supplies a protected bucket")
                backend_end = main_text.index("  required_providers", backend_start)
                (cls.workdir / filename).write_text(
                    main_text[:backend_start] + main_text[backend_end:],
                    encoding="utf-8",
                )
            else:
                shutil.copy(MODULE_DIR / filename, cls.workdir / filename)

        write_offline_provider_override(cls.workdir)
        init_res = run_terraform("init", chdir=cls.workdir, home=cls.scratch_home)
        if init_res.returncode != 0:
            cls._tmpdir.cleanup()
            raise AssertionError(f"terraform init failed with code {init_res.returncode}: {init_res.stderr}\n{init_res.stdout}")

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
            worker_image="asia-east1-docker.pkg.dev/test/repo/worker@sha256:" + "0" * 64,
            scheduler_image="asia-east1-docker.pkg.dev/test/repo/scheduler@sha256:" + "0" * 64,
            cloud_sql_instance_name="test-db",
            cloud_sql_connection_name="test:asia-east1:test-db",
            network_name="test-vpc",
            subnetwork_name="test-subnet",
            kms_key_id="projects/test-staging-proj/locations/asia-east1/keyRings/staging/cryptoKeys/release",
            deployer_service_account_email="deployer@test-staging-proj.iam.gserviceaccount.com",
            created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    def _plan(self, name: str, tfvars: dict) -> str:
        var_file = f"{name}.tfvars.json"
        (self.workdir / var_file).write_text(json.dumps(tfvars), encoding="utf-8")
        result = run_terraform(
            "plan",
            "-no-color",
            f"-var-file={var_file}",
            chdir=self.workdir,
            home=self.scratch_home,
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

    def test_generated_tfvars_bind_password_login_runtime_inputs(self) -> None:
        """caller config -> generated tfvars -> planned API/Web runtime env.

        Synthetic fixture values; this proves the planned binding only, not a
        live sign-in, an account, or any UAT case.
        """

        from product_ops.deployment.staging_lifecycle import (
            STAGING_WEB_PUBLIC_ORIGIN,
            generate_tfvars,
            get_ephemeral_resource_names,
            release_label_value,
            staging_smoke_token_audience,
        )

        release_id = "odp-20260824-001"
        config = self._config(release_id)
        tfvars = generate_tfvars(config)
        self.assertEqual(tfvars["web_base_url"], STAGING_WEB_PUBLIC_ORIGIN)
        output = self._plan("password_login", tfvars)
        names = get_ephemeral_resource_names(release_id, config.project_id)

        def planned(address: str) -> str:
            start = output.index(f"# {address} will be created")
            end = output.find("\n  # ", start + 1)
            return output[start:] if end < 0 else output[start:end]

        def env_value(text: str, name: str) -> str:
            match = re.search(rf'name\s+=\s+"{name}"\n\s+\+ value\s+=\s+"([^"]*)"', text)
            self.assertIsNotNone(match, f"{name} is not planned")
            return match.group(1)

        def env_secret(text: str, name: str) -> str:
            match = re.search(
                rf'name\s+=\s+"{name}"\n\n\s+\+ value_source \{{\n\s+\+ secret_key_ref \{{\n'
                rf'\s+\+ secret\s+=\s+"([^"]+)"',
                text,
            )
            self.assertIsNotNone(match, f"{name} is not planned from Secret Manager")
            return match.group(1)

        # Expected values written out (main.tf locals are the only owner).
        audience = "urn:odp:staging-identity:" + release_label_value(release_id)
        self.assertNotEqual(audience, staging_smoke_token_audience(release_id))
        api = planned("google_cloud_run_v2_service.staging_api")
        web = planned("google_cloud_run_v2_service.staging_web")
        for text in (api, web):
            self.assertEqual(env_value(text, "ODP_AUTH_MODE"), "local")
            self.assertEqual(env_value(text, "ODP_AUTH_LOCAL_ISSUER"), "urn:odp:identity:local")
            self.assertEqual(env_value(text, "ODP_AUTH_LOCAL_AUDIENCES"), audience)
            self.assertEqual(env_secret(text, "ODP_IDENTITY_TOKEN_SIGNING_KEY"), names["secret_identity_key"])
            self.assertEqual(env_secret(text, "ODAY_DATABASE_URL"), names["secret_db_url"])
        # The governed console origin, not the release's Cloud Run URL.
        self.assertEqual(env_value(web, "ODP_WEB_BASE_URL"), "https://console-staging.oday-plus.com.tw")
        # The smoke audience stays the API's only service audience.
        self.assertEqual(env_value(api, "ODP_AUTH_SERVICE_AUDIENCES"), staging_smoke_token_audience(release_id))
        self.assertIn(
            f'secret_id             = "{names["secret_identity_key"]}"',
            planned("google_secret_manager_secret.staging_identity_token_signing_key"),
        )

    def test_explicit_tenant_still_wins_over_the_derived_one(self) -> None:
        from product_ops.deployment.staging_lifecycle import generate_tfvars

        config = self._config("odp-20260824-001")
        tfvars = generate_tfvars(config)
        tfvars["tenant_id"] = "custom-tenant-42"

        planned_tenant = self._planned_tenant(self._plan("explicit_tenant", tfvars))

        self.assertEqual(planned_tenant, "custom-tenant-42")


if __name__ == "__main__":
    unittest.main()
