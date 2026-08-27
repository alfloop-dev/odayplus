from __future__ import annotations

import unittest
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[1] / "modules" / "runtime_foundation"


class RuntimeFoundationModuleContractTests(unittest.TestCase):
    def test_module_files_exist(self) -> None:
        required = [
            "main.tf",
            "variables.tf",
            "outputs.tf",
            "network.tf",
            "kms.tf",
            "database.tf",
        ]
        for f in required:
            self.assertTrue((MODULE_DIR / f).is_file(), f"Missing required file: {f}")

    def test_network_resources_declared(self) -> None:
        net_tf = (MODULE_DIR / "network.tf").read_text(encoding="utf-8")
        self.assertIn('resource "google_compute_network" "runtime"', net_tf)
        self.assertIn('resource "google_compute_subnetwork" "runtime"', net_tf)
        self.assertIn('resource "google_compute_global_address" "private_services"', net_tf)
        self.assertIn('resource "google_service_networking_connection" "private_services"', net_tf)
        self.assertIn('resource "google_compute_firewall" "deny_all_egress"', net_tf)
        self.assertIn('resource "google_compute_firewall" "allow_private_egress"', net_tf)
        self.assertIn('resource "google_compute_firewall" "allow_restricted_google_apis"', net_tf)
        self.assertIn("private_ip_google_access = true", net_tf)

    def test_kms_resources_declared(self) -> None:
        kms_tf = (MODULE_DIR / "kms.tf").read_text(encoding="utf-8")
        self.assertIn('resource "google_kms_key_ring" "runtime"', kms_tf)
        self.assertIn('resource "google_kms_crypto_key" "runtime"', kms_tf)
        self.assertIn('rotation_period = "7776000s"', kms_tf)
        self.assertIn("prevent_destroy = true", kms_tf)
        self.assertIn('resource "google_kms_crypto_key_iam_member" "cloud_sql"', kms_tf)
        self.assertIn('resource "google_kms_crypto_key_iam_member" "gcs"', kms_tf)
        self.assertIn('resource "google_kms_crypto_key_iam_member" "pubsub"', kms_tf)

    def test_database_instance_declared(self) -> None:
        db_tf = (MODULE_DIR / "database.tf").read_text(encoding="utf-8")
        self.assertIn('resource "google_sql_database_instance" "primary"', db_tf)
        self.assertIn('database_version    = "POSTGRES_16"', db_tf)
        self.assertIn("ipv4_enabled                                  = false", db_tf)
        self.assertIn("point_in_time_recovery_enabled = true", db_tf)
        self.assertIn("encryption_key_name = google_kms_crypto_key.runtime.id", db_tf)

    def test_outputs_expose_necessary_handles(self) -> None:
        outputs_tf = (MODULE_DIR / "outputs.tf").read_text(encoding="utf-8")
        self.assertIn('output "network_name"', outputs_tf)
        self.assertIn('output "subnetwork_name"', outputs_tf)
        self.assertIn('output "kms_crypto_key_id"', outputs_tf)
        self.assertIn('output "cloud_sql_instance_name"', outputs_tf)
        self.assertIn('output "cloud_sql_instance_connection_name"', outputs_tf)
        # Ensure no secrets in outputs
        for forbidden in ("password", "result", "secret_data"):
            self.assertNotIn(forbidden, outputs_tf)


if __name__ == "__main__":
    unittest.main()
