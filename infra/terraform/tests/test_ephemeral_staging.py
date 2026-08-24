from __future__ import annotations

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
        self.assertIn('paused           = true', main_tf)

    def test_module_variables_validation_rules(self) -> None:
        vars_tf = (MODULE_DIR / "variables.tf").read_text(encoding="utf-8")

        self.assertIn('variable "release_id"', vars_tf)
        self.assertIn('variable "candidate_sha"', vars_tf)
        self.assertIn('variable "manifest_digest"', vars_tf)
        self.assertIn('variable "api_image"', vars_tf)
        self.assertIn('variable "web_image"', vars_tf)
        self.assertIn('variable "ttl_hours"', vars_tf)
        self.assertIn('var.ttl_hours >= 1 && var.ttl_hours <= 168', vars_tf)

    def test_module_outputs_do_not_leak_secrets(self) -> None:
        outputs_tf = (MODULE_DIR / "outputs.tf").read_text(encoding="utf-8")

        for forbidden in ("password", "result", "secret_data"):
            self.assertNotIn(forbidden, outputs_tf)

        self.assertIn('output "staging_api_uri"', outputs_tf)
        self.assertIn('output "staging_web_uri"', outputs_tf)
        self.assertIn('output "staging_database_name"', outputs_tf)
        self.assertIn('output "staging_data_bucket"', outputs_tf)
        self.assertIn('output "resource_labels"', outputs_tf)


if __name__ == "__main__":
    unittest.main()
