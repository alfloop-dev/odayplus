from __future__ import annotations

import ipaddress
import json
import re
import unittest
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[1] / "modules" / "runtime_foundation"
EVIDENCE_DIR = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "evidence"
    / "runtime"
    / "ODP-STAGING-FOUNDATION-IAC-REMEDIATION-001"
)


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

    def test_private_google_dns_is_bound_to_the_runtime_network(self) -> None:
        net_tf = (MODULE_DIR / "network.tf").read_text(encoding="utf-8")

        def block(kind: str, name: str) -> str:
            match = re.search(rf'resource "{kind}" "{name}" \{{\n(.*?)\n\}}', net_tf, re.DOTALL)
            self.assertIsNotNone(match, f"{kind}.{name} is missing")
            return match.group(1)

        zones = dict(
            re.findall(
                r'(\w+)\s*=\s*"([a-z.]+\.)"',
                re.search(r"private_google_dns_zones = \{(.*?)\}", net_tf, re.DOTALL).group(1),
            )
        )
        self.assertEqual(zones, {"googleapis": "googleapis.com.", "run": "run.app."})

        zone = block("google_dns_managed_zone", "private_google")
        self.assertIn("for_each    = local.private_google_dns_zones", zone)
        self.assertIn('visibility  = "private"', zone)
        # Bound to this module's runtime VPC only, never the default network.
        self.assertEqual(
            re.findall(r"network_url\s*=\s*(\S+)", zone),
            ["google_compute_network.runtime.id"],
        )
        self.assertNotIn("default", zone)
        # Per-environment name, distinct from the default-network oday-dev-connector-* zones.
        self.assertIn('name        = "${local.name_prefix}-runtime-${each.key}"', zone)
        self.assertNotIn("dev-connector", zone)

        vip = block("google_dns_record_set", "private_google_vip")
        self.assertIn('name    = each.key == "googleapis" ? "private.googleapis.com." : each.value', vip)
        self.assertIn('type    = "A"', vip)
        self.assertIn("rrdatas = local.private_google_api_ips", vip)
        wildcard = block("google_dns_record_set", "private_google_wildcard")
        self.assertIn('name         = "*.${each.value}"', wildcard)
        self.assertIn('type         = "CNAME"', wildcard)
        self.assertIn("rrdatas      = [google_dns_record_set.private_google_vip[each.key].name]", wildcard)

        # Every VIP address is admitted by the existing Google APIs firewall
        # allow, and only one VIP (private) is used, as Google requires.
        ips = re.findall(
            r'"([\d.]+)"',
            re.search(r"private_google_api_ips = \[(.*?)\]", net_tf, re.DOTALL).group(1),
        )
        self.assertEqual(ips, ["199.36.153.8", "199.36.153.9", "199.36.153.10", "199.36.153.11"])
        firewall = block("google_compute_firewall", "allow_restricted_google_apis")
        self.assertIn('direction = "EGRESS"', firewall)
        self.assertIn('ports    = ["443"]', firewall)
        allowed = [
            ipaddress.ip_network(cidr)
            for cidr in re.findall(r'"(\d+\.\d+\.\d+\.\d+/\d+)"', firewall)
        ]
        for ip in ips:
            self.assertTrue(any(ipaddress.ip_address(ip) in net for net in allowed), ip)
        self.assertTrue(
            all(ipaddress.ip_address(ip) in ipaddress.ip_network("199.36.153.8/30") for ip in ips)
        )

    def test_root_enables_cloud_dns_for_the_foundation_zones(self) -> None:
        root_main = (MODULE_DIR.parents[1] / "main.tf").read_text(encoding="utf-8")
        apis = re.search(r"required_apis = toset\(\[(.*?)\]\)", root_main, re.DOTALL).group(1)
        self.assertIn('"dns.googleapis.com"', apis)

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

    def test_state_bucket_rejects_plan_uploads_and_preserves_quarantine(self) -> None:
        runbook = (EVIDENCE_DIR / "STAGING_FOUNDATION_RUNBOOK.md").read_text(encoding="utf-8")
        apply_receipt = json.loads(
            (EVIDENCE_DIR / "live-apply-plan-receipt.json").read_text(encoding="utf-8")
        )
        readback = json.loads(
            (EVIDENCE_DIR / "live-foundation-readback-receipt.json").read_text(encoding="utf-8")
        )
        incident = apply_receipt["security_quarantine_incident"]
        state_backend = readback["foundation_resources"]["state_backend"]
        quarantine = readback["security_quarantine"]
        iam = state_backend["least_privilege_iam"]

        self.assertNotIn("saved_plan_artifact", apply_receipt)
        self.assertNotIn("saved_plan_artifact", runbook)
        self.assertNotIn("LIVE_APPLIED_AND_VERIFIED", runbook)
        self.assertEqual(state_backend["plan_upload_policy"], "PROHIBITED")
        self.assertNotEqual(state_backend["status"], "LIVE_APPLIED_AND_VERIFIED")
        self.assertIn("binary plan、一般 release artifact", runbook)
        self.assertIn("一律禁止", runbook)
        self.assertEqual(incident["status"], "CONTAINED_CLEANUP_DEFERRED")
        self.assertEqual(incident["cmek_key_id"], state_backend["cmek_key_id"])
        self.assertEqual(incident["retention_expiration"], "2026-09-26T09:24:24Z")
        self.assertEqual(incident["retention_expires_at"], "2026-09-26T09:24:24Z")
        self.assertTrue(incident["no_early_deletion"])
        self.assertEqual(incident["expiry_cleanup_owner"], "Human/Ops")
        self.assertEqual(incident["cleanup_executor"], "Antigravity2")
        self.assertEqual(
            incident["follow_up_task_id"],
            "ODP-STAGING-STATE-PLAN-QUARANTINE-CLEANUP-001",
        )
        self.assertEqual(incident["cleanup_not_before"], "2026-09-26T09:24:24Z")
        self.assertTrue(incident["containment_verified"])
        self.assertFalse(incident["foundation_delivery_gate"])
        self.assertEqual(incident["deletion_before_retention_expiration"], "PROHIBITED")
        self.assertEqual(state_backend["retention_period_days"], 30)
        self.assertTrue(state_backend["prevent_destroy"])
        self.assertTrue(apply_receipt["validation_results"]["completion_claims_withheld"])
        self.assertEqual(
            apply_receipt["validation_results"]["completion_claims_withheld_by"],
            ["DIRECT_VPC_ALL_TRAFFIC_LIVE_READBACK"],
        )
        self.assertFalse(
            apply_receipt["validation_results"]["security_quarantine_foundation_gate"]
        )

        self.assertEqual(readback["receipt_status"], "PENDING_DIRECT_VPC_LIVE_READBACK")
        self.assertEqual(apply_receipt["receipt_status"], "PENDING_DIRECT_VPC_LIVE_READBACK")
        self.assertFalse(state_backend["completion_claims_withheld"])
        self.assertEqual(quarantine["status"], "CONTAINED_CLEANUP_DEFERRED")
        self.assertEqual(quarantine["follow_up_task_id"], incident["follow_up_task_id"])
        self.assertFalse(quarantine["foundation_delivery_gate"])
        self.assertFalse(quarantine["completion_claims_withheld"])
        self.assertEqual(apply_receipt["iam_blocker_resolution"]["status"], "RESOLVED")
        self.assertEqual(iam["status"], "LIVE_VERIFIED")
        self.assertTrue(iam["verified"])
        self.assertEqual(iam["admin_readback"]["role"], "roles/storage.admin")
        self.assertEqual(
            iam["admin_readback"]["scope"],
            "bucket:oday-tfstate-staging-odayplus-runtime-20260825",
        )
        self.assertIn("storage.buckets.get", iam["admin_readback"]["verified_permissions"])
        self.assertIn(
            "storage.buckets.getIamPolicy", iam["admin_readback"]["verified_permissions"]
        )
        self.assertEqual(iam["deployer_readback"]["role"], "roles/storage.objectUser")
        self.assertEqual(iam["deployer_readback"]["github_actions_run_id"], "33320822376")
        self.assertEqual(len(iam["deployer_readback"]["remote_state_objects_verified"]), 2)
        self.assertFalse(iam["project_wide_storage_admin_required"])
        self.assertEqual(iam["completion_claim"], "VERIFIED")
        self.assertTrue(readback["security_compliance"]["state_bucket_iam_least_privilege_verified"])


if __name__ == "__main__":
    unittest.main()
