from __future__ import annotations

import unittest
from pathlib import Path

BOOT_DIR = Path(__file__).resolve().parents[1] / "bootstrap"


class TerraformBootstrapContractTests(unittest.TestCase):
    def test_bootstrap_files_exist(self) -> None:
        required = ["main.tf", "variables.tf", "outputs.tf", "README.md"]
        for f in required:
            self.assertTrue((BOOT_DIR / f).is_file(), f"Missing required file: {f}")

    def test_bootstrap_governed_bucket_and_kms(self) -> None:
        main_tf = (BOOT_DIR / "main.tf").read_text(encoding="utf-8")
        self.assertIn('resource "google_storage_bucket" "terraform_state"', main_tf)
        self.assertIn('resource "google_kms_key_ring" "state_backend"', main_tf)
        self.assertIn('resource "google_kms_crypto_key" "state_backend"', main_tf)
        self.assertIn('public_access_prevention    = "enforced"', main_tf)
        self.assertIn("uniform_bucket_level_access = true", main_tf)
        self.assertIn("versioning {", main_tf)
        self.assertIn("retention_policy {", main_tf)
        self.assertIn("default_kms_key_name = google_kms_crypto_key.state_backend.id", main_tf)
        self.assertIn("prevent_destroy = true", main_tf)

    def test_bootstrap_outputs_and_release_prefix_pattern(self) -> None:
        outputs_tf = (BOOT_DIR / "outputs.tf").read_text(encoding="utf-8")
        self.assertIn('output "state_bucket_name"', outputs_tf)
        self.assertIn('output "state_bucket_url"', outputs_tf)
        self.assertIn('output "state_kms_key_id"', outputs_tf)
        self.assertIn('output "staging_ephemeral_release_prefix_pattern"', outputs_tf)
        self.assertIn("oday-plus/staging/releases/{release_id}", outputs_tf)


if __name__ == "__main__":
    unittest.main()
