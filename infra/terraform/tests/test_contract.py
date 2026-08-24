from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "terraform_contract_validator",
    ROOT / "validate_contract.py",
)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


class TerraformProductionContractTests(unittest.TestCase):
    def test_repository_contract_passes(self) -> None:
        self.assertEqual(validator.validate(ROOT), [])

    def test_unbalanced_hcl_is_rejected(self) -> None:
        self.assertFalse(validator._balanced_hcl('resource "x" "y" {'))
        self.assertTrue(
            validator._balanced_hcl(
                'resource "x" "y" { value = "${ignored}" } # ignored {'
            )
        )

    def test_missing_resource_token_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copy_root = Path(directory)
            for relative in validator.REQUIRED_FILES:
                source = ROOT / relative
                destination = copy_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            database = copy_root / "database.tf"
            database.write_text(
                database.read_text(encoding="utf-8").replace("POSTGRES_16", "POSTGRES_15"),
                encoding="utf-8",
            )
            errors = validator.validate(copy_root)
            self.assertTrue(any("POSTGRES_16" in error for error in errors))

    def test_plaintext_database_url_output_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copy_root = Path(directory)
            for relative in validator.REQUIRED_FILES:
                source = ROOT / relative
                destination = copy_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            outputs = copy_root / "outputs.tf"
            outputs.write_text(
                outputs.read_text(encoding="utf-8")
                + '\noutput "bad" { value = random_password.database.result }\n',
                encoding="utf-8",
            )
            errors = validator.validate(copy_root)
            self.assertTrue(any("random_password.database.result" in error for error in errors))

    def test_external_provider_variables_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copy_root = Path(directory)
            for relative in validator.REQUIRED_FILES:
                source = ROOT / relative
                destination = copy_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            variables = copy_root / "variables.tf"
            variables.write_text(
                variables.read_text(encoding="utf-8")
                + '\nvariable "external_provider_endpoints" { type = map(string) }\n',
                encoding="utf-8",
            )
            errors = validator.validate(copy_root)
            self.assertTrue(any("external_provider" in error for error in errors))

    def test_runtime_egress_ip_or_nat_output_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copy_root = Path(directory)
            for relative in validator.REQUIRED_FILES:
                source = ROOT / relative
                destination = copy_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            outputs = copy_root / "outputs.tf"
            outputs.write_text(
                outputs.read_text(encoding="utf-8")
                + '\noutput "runtime_egress_ip" { value = "35.1.2.3" }\n',
                encoding="utf-8",
            )
            errors = validator.validate(copy_root)
            self.assertTrue(any("runtime_egress_ip" in error for error in errors))

    def test_router_nat_resource_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copy_root = Path(directory)
            for relative in validator.REQUIRED_FILES:
                source = ROOT / relative
                destination = copy_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            network = copy_root / "network.tf"
            network.write_text(
                network.read_text(encoding="utf-8")
                + '\nresource "google_compute_router_nat" "nat" { name = "nat" }\n',
                encoding="utf-8",
            )
            errors = validator.validate(copy_root)
            self.assertTrue(any("google_compute_router_nat" in error for error in errors))

    def test_tampered_deny_all_egress_firewall_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copy_root = Path(directory)
            for relative in validator.REQUIRED_FILES:
                source = ROOT / relative
                destination = copy_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            network = copy_root / "network.tf"
            network.write_text(
                network.read_text(encoding="utf-8").replace("priority  = 65534", "priority  = 100"),
                encoding="utf-8",
            )
            errors = validator.validate(copy_root)
            self.assertTrue(any("deny_all_egress: priority must be 65534" in error for error in errors))

    def test_tampered_allow_private_egress_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copy_root = Path(directory)
            for relative in validator.REQUIRED_FILES:
                source = ROOT / relative
                destination = copy_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            network = copy_root / "network.tf"
            network.write_text(
                network.read_text(encoding="utf-8").replace('"10.0.0.0/8",', '"10.0.0.0/8",\n    "0.0.0.0/0",'),
                encoding="utf-8",
            )
            errors = validator.validate(copy_root)
            self.assertTrue(any("allow_private_egress: destination_ranges contains non-RFC1918 destinations" in error for error in errors))

    def test_tampered_allow_restricted_google_apis_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copy_root = Path(directory)
            for relative in validator.REQUIRED_FILES:
                source = ROOT / relative
                destination = copy_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            network = copy_root / "network.tf"
            network.write_text(
                network.read_text(encoding="utf-8").replace('ports    = ["443"]', 'ports    = ["80", "443"]'),
                encoding="utf-8",
            )
            errors = validator.validate(copy_root)
            self.assertTrue(any("allow_restricted_google_apis: ports must be ['443']" in error for error in errors))

    def test_unexpected_egress_firewall_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copy_root = Path(directory)
            for relative in validator.REQUIRED_FILES:
                source = ROOT / relative
                destination = copy_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            network = copy_root / "network.tf"
            network.write_text(
                network.read_text(encoding="utf-8")
                + '\nresource "google_compute_firewall" "allow_all" {\n  name = "allow-all"\n  direction = "EGRESS"\n  priority = 10\n  allow { protocol = "all" }\n}\n',
                encoding="utf-8",
            )
            errors = validator.validate(copy_root)
            self.assertTrue(any("unexpected egress firewall rule 'allow_all'" in error for error in errors))

    def test_tampered_network_cidr_public_ip_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copy_root = Path(directory)
            for relative in validator.REQUIRED_FILES:
                source = ROOT / relative
                destination = copy_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            variables = copy_root / "variables.tf"
            # Simulate removing RFC1918 validation constraint from network_cidr
            variables.write_text(
                variables.read_text(encoding="utf-8").replace(
                    '&& can(regex("^(10\\\\.|172\\\\.(1[6-9]|2[0-9]|3[0-1])\\\\.|192\\\\.168\\\\.)", var.network_cidr))',
                    "",
                ).replace("RFC1918", "any"),
                encoding="utf-8",
            )
            errors = validator.validate(copy_root)
            self.assertTrue(any("network_cidr must restrict subnet to RFC1918" in error for error in errors))

    def test_tampered_live_data_enabled_description_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copy_root = Path(directory)
            for relative in validator.REQUIRED_FILES:
                source = ROOT / relative
                destination = copy_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            variables = copy_root / "variables.tf"
            variables.write_text(
                variables.read_text(encoding="utf-8").replace(
                    "Enable platform snapshot and production model gates",
                    "Enable live provider and production model gates",
                ),
                encoding="utf-8",
            )
            errors = validator.validate(copy_root)
            self.assertTrue(any("live_data_enabled description must not reference legacy live provider mode" in error for error in errors))

    def test_network_cidr_rfc1918_validation_rejects_public_cidrs(self) -> None:
        import ipaddress
        import re

        pattern = re.compile(r"^(10\.|172\.(1[6-9]|2[0-9]|3[0-1])\.|192\.168\.)")

        # Valid RFC1918 CIDRs
        valid_cidrs = ["10.0.0.0/8", "10.42.0.0/24", "172.16.0.0/12", "172.20.1.0/24", "172.31.255.0/24", "192.168.1.0/24"]
        for cidr in valid_cidrs:
            net = ipaddress.ip_network(cidr)
            self.assertTrue(net.is_private, f"{cidr} should be private")
            self.assertTrue(bool(pattern.match(cidr)), f"{cidr} should match RFC1918 pattern")

        # Public / non-RFC1918 CIDRs that must be rejected
        public_cidrs = ["8.8.8.0/24", "1.1.1.0/24", "172.15.0.0/16", "172.32.0.0/16", "192.169.0.0/16", "203.0.113.0/24"]
        for cidr in public_cidrs:
            self.assertFalse(bool(pattern.match(cidr)), f"{cidr} must NOT match RFC1918 pattern")


if __name__ == "__main__":
    unittest.main()
