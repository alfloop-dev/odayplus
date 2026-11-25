from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

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
            database = copy_root / "modules" / "runtime_foundation" / "database.tf"
            database.write_text(
                database.read_text(encoding="utf-8").replace("POSTGRES_16", "POSTGRES_15"),
                encoding="utf-8",
            )
            errors = validator.validate(copy_root)
            self.assertTrue(any("POSTGRES_16" in error for error in errors))

    def test_release_profile_without_fail_closed_contract_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copy_root = Path(directory)
            for relative in validator.REQUIRED_FILES:
                source = ROOT / relative
                destination = copy_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            variables = copy_root / "variables.tf"
            original = variables.read_text(encoding="utf-8")
            variables.write_text(
                original.replace(
                    'variable "release_profile" {\n  type        = string\n',
                    'variable "release_profile" {\n  type        = string\n  default     = "full"\n',
                ),
                encoding="utf-8",
            )
            errors = validator.validate(copy_root)
            self.assertIn("variables.tf: release_profile must have no default (a missing profile fails closed)", errors)
            variables.write_text(original.replace("^(full|core-ops)$", "^(full|core-ops|dev-admin)$"), encoding="utf-8")
            errors = validator.validate(copy_root)
            self.assertIn("variables.tf: release_profile must accept only full or core-ops", errors)

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
            network = copy_root / "modules" / "runtime_foundation" / "network.tf"
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
            network = copy_root / "modules" / "runtime_foundation" / "network.tf"
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
            network = copy_root / "modules" / "runtime_foundation" / "network.tf"
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
            network = copy_root / "modules" / "runtime_foundation" / "network.tf"
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
            network = copy_root / "modules" / "runtime_foundation" / "network.tf"
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

    def test_moved_blocks_cover_all_extracted_foundation_resources(self) -> None:
        network_tf = (ROOT / "network.tf").read_text(encoding="utf-8")
        expected_moved_targets = [
            "google_compute_network.runtime",
            "google_compute_subnetwork.runtime",
            "google_compute_subnetwork_iam_member.runtime_network_user",
            "google_compute_subnetwork_iam_member.web_network_user",
            "google_compute_global_address.private_services",
            "google_service_networking_connection.private_services",
            "google_compute_firewall.deny_all_egress",
            "google_compute_firewall.allow_private_egress",
            "google_compute_firewall.allow_restricted_google_apis",
            "google_kms_key_ring.runtime",
            "google_kms_crypto_key.runtime",
            "google_project_service_identity.cloud_sql",
            "google_project_service_identity.pubsub",
            "google_kms_crypto_key_iam_member.cloud_sql",
            "google_kms_crypto_key_iam_member.gcs",
            "google_kms_crypto_key_iam_member.pubsub",
            "google_sql_database_instance.primary",
        ]
        for target in expected_moved_targets:
            self.assertIn(f"from = {target}", network_tf, f"Missing moved block for {target}")
        self.assertIn(
            'to   = module.runtime_foundation.google_compute_subnetwork_iam_member.network_users["runtime"]',
            network_tf,
        )
        self.assertIn(
            'to   = module.runtime_foundation.google_compute_subnetwork_iam_member.network_users["web"]',
            network_tf,
        )
        for target in expected_moved_targets[4:]:
            self.assertIn(f"to   = module.runtime_foundation.{target}", network_tf)

    def test_foundation_subnet_iam_uses_managed_staging_identities(self) -> None:
        network_tf = (ROOT / "network.tf").read_text(encoding="utf-8")

        self.assertIn(
            'runtime = "serviceAccount:${google_service_account.runtime.email}"',
            network_tf,
        )
        self.assertIn(
            'web     = "serviceAccount:${google_service_account.web.email}"',
            network_tf,
        )
        self.assertIn("google_service_account.runtime,", network_tf)
        self.assertIn("google_service_account.web,", network_tf)
        self.assertIn(
            'enable_deletion_protection = local.is_prod || var.environment == "staging"',
            network_tf,
        )

    def _errors_after_replacing(self, relative: str, old: str, new: str) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            copy_root = Path(directory)
            for path in validator.REQUIRED_FILES:
                destination = copy_root / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text((ROOT / path).read_text(encoding="utf-8"), encoding="utf-8")
            target = copy_root / relative
            text = target.read_text(encoding="utf-8")
            self.assertIn(old, text)
            target.write_text(text.replace(old, new, 1), encoding="utf-8")
            return validator.validate(copy_root)

    def test_monitoring_api_must_be_enabled(self) -> None:
        errors = self._errors_after_replacing("main.tf", '    "monitoring.googleapis.com",\n', "")
        self.assertTrue(any("monitoring.googleapis.com" in error for error in errors))

    def test_hard_coded_notification_email_is_rejected(self) -> None:
        errors = self._errors_after_replacing(
            "monitoring.tf",
            "email_address = each.value",
            'email_address = "alerts@ops.invalid"',
        )
        self.assertTrue(any("hard-code notification email" in error for error in errors))

    def test_notification_email_default_must_be_empty(self) -> None:
        errors = self._errors_after_replacing(
            "variables.tf",
            'Production requires this or monitoring_notification_channel_ids."\n  default     = []',
            'Production requires this or monitoring_notification_channel_ids."\n  default     = ["alerts@ops.invalid"]',
        )
        self.assertTrue(any("monitoring_notification_emails must default" in error for error in errors))

    def test_alert_policy_without_notification_channels_is_rejected(self) -> None:
        errors = self._errors_after_replacing(
            "monitoring.tf",
            "  notification_channels = local.monitoring_notification_channels\n",
            "",
        )
        self.assertTrue(any("must route to local.monitoring_notification_channels" in error for error in errors))

    def test_production_monitoring_precondition_is_required(self) -> None:
        errors = self._errors_after_replacing(
            "checks.tf",
            "length(var.monitoring_notification_emails) + length(var.monitoring_notification_channel_ids) > 0\n        &&",
            "true\n        &&",
        )
        self.assertTrue(any("fail closed without an alert notification target" in error for error in errors))

    def test_ratified_api_latency_threshold_is_asserted(self) -> None:
        errors = self._errors_after_replacing(
            "monitoring.tf",
            "monitoring_api_latency_p95_threshold_ms = 3000",
            "monitoring_api_latency_p95_threshold_ms = 5000",
        )
        self.assertTrue(any("monitoring_api_latency_p95_threshold_ms = 3000" in error for error in errors))


class CustomDomainLoadBalancerContractTests(unittest.TestCase):
    """The persistent per-environment custom-domain load balancer (2026-11-20)."""

    @staticmethod
    def _resource(text: str, kind: str, name: str) -> str:
        match = re.search(rf'resource "{kind}" "{name}" \{{\n(.*?)\n\}}', text, re.DOTALL)
        assert match is not None, f"{kind}.{name} is missing"
        return match.group(1)

    def setUp(self) -> None:
        self.lb = (ROOT / "load_balancer.tf").read_text(encoding="utf-8")

    def test_staging_name_is_the_one_the_release_lifecycle_attaches_to(self) -> None:
        from product_ops.deployment.staging_lifecycle import STAGING_WEB_LOAD_BALANCER_NAME

        main = (ROOT / "main.tf").read_text(encoding="utf-8")
        self.assertIn('name_prefix  = "oday-${var.environment}"', main)
        self.assertIn('web_lb_name    = "${local.name_prefix}-web-lb"', self.lb)
        self.assertEqual(STAGING_WEB_LOAD_BALANCER_NAME, "oday-staging-web-lb")
        for kind in (
            "google_compute_global_address",
            "google_compute_url_map",
            "google_compute_target_https_proxy",
            "google_compute_global_forwarding_rule",
        ):
            self.assertIn("name ", self._resource(self.lb, kind, "web_lb"))
            self.assertRegex(self._resource(self.lb, kind, "web_lb"), r"(?m)^  name\s+= local\.web_lb_name$")
        self.assertRegex(
            self._resource(self.lb, "google_compute_backend_service", "web_lb_release"),
            r"(?m)^  name\s+= local\.web_lb_name$",
        )

    def test_staging_backend_list_belongs_to_the_release_lifecycle(self) -> None:
        release = self._resource(self.lb, "google_compute_backend_service", "web_lb_release")
        self.assertIn("count = local.web_lb_release_attached ? 1 : 0", release)
        self.assertIn("ignore_changes = [backend]", release)
        self.assertNotIn("backend {", release)
        self.assertIn('web_lb_release_attached  = local.web_lb_enabled && var.environment == "staging"', self.lb)
        # Staging never routes to the root stack's own Web/API services.
        self.assertIn('web_lb_terraform_backend = local.web_lb_enabled && var.environment != "staging"', self.lb)
        terraform_neg = self._resource(self.lb, "google_compute_region_network_endpoint_group", "web_lb")
        self.assertIn("count = local.web_lb_terraform_backend ? 1 : 0", terraform_neg)
        self.assertIn("service = local.web_service_name", terraform_neg)

    def test_frontend_is_https_only_on_a_reserved_ip_with_a_managed_certificate(self) -> None:
        address = self._resource(self.lb, "google_compute_global_address", "web_lb")
        self.assertIn('address_type = "EXTERNAL"', address)
        self.assertIn("prevent_destroy = true", address)
        certificate = self._resource(self.lb, "google_compute_managed_ssl_certificate", "web_lb")
        self.assertIn("domains = local.web_lb_domains", certificate)
        self.assertIn("create_before_destroy = true", certificate)
        rule = self._resource(self.lb, "google_compute_global_forwarding_rule", "web_lb")
        self.assertIn('port_range            = "443"', rule)
        self.assertIn("ip_address            = google_compute_global_address.web_lb[0].id", rule)
        self.assertIn("target                = google_compute_target_https_proxy.web_lb[0].id", rule)
        proxy = self._resource(self.lb, "google_compute_target_https_proxy", "web_lb")
        self.assertIn("ssl_certificates = [google_compute_managed_ssl_certificate.web_lb[0].id]", proxy)
        self.assertIn("ssl_policy       = google_compute_ssl_policy.web_lb[0].id", proxy)
        policy = self._resource(self.lb, "google_compute_ssl_policy", "web_lb")
        self.assertIn('min_tls_version = "TLS_1_2"', policy)
        self.assertIn("name            = local.web_lb_name", policy)

    def test_port_80_only_redirects_to_https_on_the_same_address(self) -> None:
        redirect = self._resource(self.lb, "google_compute_url_map", "web_lb_http_redirect")
        self.assertIn("https_redirect         = true", redirect)
        self.assertIn('redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"', redirect)
        self.assertIn("strip_query            = false", redirect)
        self.assertNotIn("default_service", redirect)
        self.assertNotIn("backend", redirect)
        proxy = self._resource(self.lb, "google_compute_target_http_proxy", "web_lb_http_redirect")
        self.assertIn("url_map = google_compute_url_map.web_lb_http_redirect[0].id", proxy)
        rule = self._resource(self.lb, "google_compute_global_forwarding_rule", "web_lb_http_redirect")
        self.assertIn('port_range            = "80"', rule)
        self.assertIn("ip_address            = google_compute_global_address.web_lb[0].id", rule)
        self.assertIn("target                = google_compute_target_http_proxy.web_lb_http_redirect[0].id", rule)
        self.assertEqual(self.lb.count('resource "google_compute_target_http_proxy"'), 1)

    def test_api_is_routed_only_by_its_own_host_and_never_in_staging(self) -> None:
        url_map = self._resource(self.lb, "google_compute_url_map", "web_lb")
        self.assertIn("default_service = local.web_lb_backend_service_id", url_map)
        self.assertIn("hosts        = [host_rule.value]", url_map)
        self.assertIn('condition     = var.environment != "staging" || var.api_public_host == ""', url_map)
        self.assertIn("!endswith(var.web_base_url, \".run.app\")", url_map)
        api_neg = self._resource(self.lb, "google_compute_region_network_endpoint_group", "api_lb")
        self.assertIn("service = local.api_service_name", api_neg)
        # The API keeps its internal-and-load-balancer ingress; IAM stays on.
        cloud_run = (ROOT / "cloud_run.tf").read_text(encoding="utf-8")
        self.assertIn('ingress  = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"', cloud_run)

    def test_production_requires_the_load_balancer_and_one_api_host(self) -> None:
        checks = (ROOT / "checks.tf").read_text(encoding="utf-8")
        self.assertIn(
            "        var.web_load_balancer_enabled\n"
            '        && var.api_public_host != ""\n'
            "        && var.monitoring_api_uptime_host == var.api_public_host\n",
            checks,
        )
        main = (ROOT / "main.tf").read_text(encoding="utf-8")
        self.assertIn("      var.api_public_host,\n", main)
        # One parse of web_base_url feeds both the uptime check and the certificate.
        monitoring = (ROOT / "monitoring.tf").read_text(encoding="utf-8")
        self.assertIn("local.web_public_host != \"\"", monitoring)
        self.assertNotIn("regex(", monitoring.split("monitoring_web_uptime_host", 1)[1].split("monitoring_cloud_run_services", 1)[0])


class StagingDeployerIamWriteBackTests(unittest.TestCase):
    """The hand-granted staging github-deployer grants, written back exactly.

    LIVE is the project-level policy of odayplus-runtime-20260825 for
    github-deployer, read back 2026-11-20 (gcloud projects get-iam-policy and
    gcloud iam roles describe). The write-back may adopt it, add only the
    load balancer attach role, and widen nothing.
    """

    LIVE_PREDEFINED = {
        "roles/cloudscheduler.admin",
        "roles/cloudsql.client",
        "roles/compute.networkViewer",
        "roles/iam.serviceAccountAdmin",
        "roles/iam.serviceAccountUser",
        "roles/logging.viewer",
        "roles/pubsub.admin",
        "roles/run.admin",
        "roles/secretmanager.admin",
        "roles/serviceusage.serviceUsageConsumer",
        "roles/storage.admin",
    }
    LIVE_SQL_USERS_PERMISSIONS = {
        f"cloudsql.{name}"
        for name in (
            "databases.create", "databases.delete", "databases.get", "databases.list", "databases.update",
            "instances.export", "instances.get", "instances.import",
            "users.create", "users.delete", "users.get", "users.list", "users.update",
        )
    }
    LIVE_CONDITION = (
        "api.getAttribute('iam.googleapis.com/modifiedGrantsByRole', []).hasOnly(['roles/cloudsql.client'])"
    )
    # Declared for every environment by the pre-existing github_deployer_*
    # resources above the staging section (not part of the write-back).
    ROOT_DECLARED = {
        "roles/run.admin",
        "roles/cloudscheduler.admin",
        "roles/cloudsql.client",
        "roles/logging.viewer",
    }

    def setUp(self) -> None:
        iam = (ROOT / "iam.tf").read_text(encoding="utf-8")
        marker = "# --- Staging github-deployer grants for the ephemeral staging lifecycle ---"
        self.assertIn(marker, iam)
        self.root_part, self.section = iam.split(marker, 1)

    def _list(self, name: str) -> set[str]:
        match = re.search(rf"{name} = \[\n(.*?)\n\s*\]", self.section, re.DOTALL)
        assert match is not None, name
        return set(re.findall(r'"([^"]+)"', match.group(1)))

    def test_predefined_write_back_plus_root_declared_is_exactly_live(self) -> None:
        match = re.search(r"staging_deployer_predefined_roles = .*?toset\(\[\n(.*?)\n\s*\]\)", self.section, re.DOTALL)
        assert match is not None
        written = set(re.findall(r'"([^"]+)"', match.group(1)))
        self.assertEqual(written | self.ROOT_DECLARED, self.LIVE_PREDEFINED)
        self.assertFalse(written & self.ROOT_DECLARED, "a root-declared grant would be managed twice")
        for role in self.ROOT_DECLARED:
            self.assertIn(f'role    = "{role}"', self.root_part)

    def test_custom_roles_match_live_and_only_the_attach_role_is_new(self) -> None:
        roles = re.findall(r"\n    (\w+) = \{\n(.*?)\n      live = (true|false)\n    \}", self.section, re.DOTALL)
        by_name = {name: (body, live) for name, body, live in roles}
        self.assertEqual(set(by_name), {"stagingLifecycleSqlUsers", "stagingWebLbAttach"})
        sql_body, sql_live = by_name["stagingLifecycleSqlUsers"]
        self.assertEqual(sql_live, "true")
        self.assertEqual(set(re.findall(r'"(cloudsql\.[^"]+)"', sql_body)), self.LIVE_SQL_USERS_PERMISSIONS)
        self.assertIn('title       = "Staging lifecycle SQL databases and users"', sql_body)
        attach_body, attach_live = by_name["stagingWebLbAttach"]
        self.assertEqual(attach_live, "false")
        permissions = set(re.findall(r'"(compute\.[^"]+)"', attach_body))
        mutations = {p for p in permissions if not p.endswith((".get", ".list"))}
        # Only the release NEG and the backend list change; the frontend
        # (address, certificate, URL maps, proxies, forwarding rules, SSL
        # policy) stays read-only for the deployer.
        self.assertEqual(
            mutations,
            {
                "compute.backendServices.update",
                "compute.regionNetworkEndpointGroups.create",
                "compute.regionNetworkEndpointGroups.delete",
                "compute.regionNetworkEndpointGroups.use",
            },
        )

    def test_conditional_project_iam_admin_matches_live(self) -> None:
        self.assertIn('"staging-lifecycle-cloudsql-client-only" = {', self.section)
        self.assertIn(f'expression  = "{self.LIVE_CONDITION}"', self.section)
        self.assertIn('role    = "roles/resourcemanager.projectIamAdmin"', self.section)

    def test_targeted_apply_adopts_live_bindings_and_never_creates_the_deployer(self) -> None:
        code = "\n".join(line for line in self.section.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn("google_service_account.github_deployer", code)
        self.assertIn(
            'staging_deployer_member  = "serviceAccount:github-deployer@${var.project_id}.iam.gserviceaccount.com"',
            self.section,
        )
        self.assertIn('staging_deployer_enabled = var.environment == "staging"', self.section)
        imports = re.findall(r"\nimport \{\n(.*?)\n\}", self.section, re.DOTALL)
        targets = {re.search(r"to\s+= (\S+)\[each\.key\]", body).group(1) for body in imports}
        self.assertEqual(
            targets,
            {
                "google_project_iam_custom_role.staging_deployer",
                "google_project_iam_member.staging_deployer_custom",
                "google_project_iam_member.staging_deployer_predefined",
                "google_project_iam_member.staging_deployer_project_iam_admin",
            },
        )
        # The new attach role and its binding are the only creates.
        for body in imports:
            if "staging_deployer_custom" in body or "google_project_iam_custom_role" in body:
                self.assertIn("if role.live", body)
        # for_each on import blocks needs Terraform 1.7.
        main = (ROOT / "main.tf").read_text(encoding="utf-8")
        self.assertIn('required_version = ">= 1.7.0"', main)


def _offline_root_copy(case: unittest.TestCase, *, strip_imports: bool) -> None:
    """Copy the root stack into a scratch directory and `terraform init` it offline.

    The backend is removed and the providers get a synthetic token, as in
    test_ephemeral_staging.run_terraform. ``strip_imports`` drops the import
    blocks: an import reads the live object during plan, which an offline plan
    cannot do, so a stripped plan shows those objects as creates.
    """
    import os
    import shutil

    from infra.terraform.tests.test_ephemeral_staging import run_terraform

    if not shutil.which("terraform"):
        if os.environ.get("CI"):
            raise AssertionError("terraform binary not available in CI environment")
        raise unittest.SkipTest("terraform binary not available in environment")
    case._run = staticmethod(run_terraform)
    case._tmpdir = tempfile.TemporaryDirectory()
    case.workdir = Path(case._tmpdir.name) / "root"
    case.home = Path(case._tmpdir.name) / "offline-home"
    shutil.copytree(ROOT, case.workdir, ignore=shutil.ignore_patterns(".terraform", "tests"))
    main = case.workdir / "main.tf"
    main.write_text(main.read_text(encoding="utf-8").replace('  backend "gcs" {}\n', ""), encoding="utf-8")
    if strip_imports:
        for path in case.workdir.glob("*.tf"):
            path.write_text(
                re.sub(r"\nimport \{\n.*?\n\}\n", "\n", path.read_text(encoding="utf-8"), flags=re.DOTALL),
                encoding="utf-8",
            )
    (case.workdir / "zz_offline_provider_override.tf").write_text(
        'provider "google" {\n  access_token = "offline"\n}\n'
        'provider "google-beta" {\n  access_token = "offline"\n}\n',
        encoding="utf-8",
    )
    init = run_terraform("init", chdir=case.workdir, home=case.home)
    if init.returncode != 0:
        case._tmpdir.cleanup()
        raise AssertionError(f"terraform init failed: {init.stderr}\n{init.stdout}")


class StagingLoadBalancerTargetedPlanTests(unittest.TestCase):
    """Offline, credential-free plans of the root stack's load balancer chain.

    The staging foundation state is a targeted subset of this root, so its
    load balancer is applied with ``-target`` on the forwarding rule. That
    target must not drag the root stack's own Cloud Run services (or anything
    they reference) into the staging foundation.
    """

    TARGETS = (
        "-target=google_compute_global_forwarding_rule.web_lb",
        "-target=google_compute_global_forwarding_rule.web_lb_http_redirect",
    )
    IMAGE = "asia-east1-docker.pkg.dev/{project}/oday/{name}@sha256:" + "1" * 64

    @classmethod
    def setUpClass(cls) -> None:
        _offline_root_copy(cls, strip_imports=False)

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "_tmpdir"):
            cls._tmpdir.cleanup()

    def _plan(self, name: str, **overrides: object) -> tuple[int, str]:
        import json

        project = str(overrides.get("project_id", "odayplus-runtime-20260825"))
        tfvars = {
            "project_id": project,
            "environment": "staging",
            "api_image": self.IMAGE.format(project=project, name="api"),
            "web_image": self.IMAGE.format(project=project, name="web"),
            "release_sha": "1" * 40,
            "release_profile": "full",
            "web_base_url": "https://console-staging.oday-plus.com.tw",
            "web_load_balancer_enabled": True,
            **overrides,
        }
        (self.workdir / f"{name}.tfvars.json").write_text(json.dumps(tfvars), encoding="utf-8")
        result = self._run(
            "plan", "-no-color", "-refresh=false", f"-var-file={name}.tfvars.json", *self.TARGETS,
            chdir=self.workdir, home=self.home,
        )
        return result.returncode, result.stdout + result.stderr

    @staticmethod
    def _created(output: str) -> set[str]:
        return {
            address
            for address in re.findall(r"^  # (\S+) will be created$", output, re.MULTILINE)
            if not address.startswith("google_project_service.required[")
        }

    def test_staging_target_creates_only_the_load_balancer_chain(self) -> None:
        code, output = self._plan("staging")
        self.assertEqual(code, 0, output)
        self.assertEqual(
            self._created(output),
            {
                "google_compute_global_address.web_lb[0]",
                "google_compute_managed_ssl_certificate.web_lb[0]",
                "google_compute_backend_service.web_lb_release[0]",
                "google_compute_url_map.web_lb[0]",
                "google_compute_target_https_proxy.web_lb[0]",
                "google_compute_global_forwarding_rule.web_lb[0]",
                "google_compute_ssl_policy.web_lb[0]",
                "google_compute_url_map.web_lb_http_redirect[0]",
                "google_compute_target_http_proxy.web_lb_http_redirect[0]",
                "google_compute_global_forwarding_rule.web_lb_http_redirect[0]",
                # Already in the staging foundation state; a no-op there.
                "terraform_data.production_contract",
            },
        )
        self.assertIn('name                      = "oday-staging-web-lb-979243f1"', output)
        self.assertRegex(output, r'domains = \[\n\s+\+ "console-staging\.oday-plus\.com\.tw",\n\s+\]')

    def test_dev_and_prod_route_to_terraform_negs_by_service_name(self) -> None:
        code, output = self._plan(
            "dev",
            project_id="oday-lb-probe-123456",
            environment="dev",
            web_base_url="https://console.oday-plus.com.tw",
            api_public_host="api-host.owner-decision.test",
        )
        self.assertEqual(code, 0, output)
        created = self._created(output)
        self.assertNotIn("google_compute_backend_service.web_lb_release[0]", created)
        for address in (
            "google_compute_region_network_endpoint_group.web_lb[0]",
            "google_compute_region_network_endpoint_group.api_lb[0]",
            "google_compute_backend_service.web_lb[0]",
            "google_compute_backend_service.api_lb[0]",
            "google_compute_ssl_policy.web_lb[0]",
            "google_compute_global_forwarding_rule.web_lb_http_redirect[0]",
        ):
            self.assertIn(address, created)
        # Staging-only deployer write-back plans nothing outside staging.
        self.assertNotIn("staging_deployer", output)
        self.assertFalse(any(address.startswith("google_cloud_run_v2_service.") for address in created))
        self.assertIn('service = "oday-dev-web"', output)
        self.assertIn('service = "oday-dev-api"', output)

    def test_staging_refuses_an_api_host_and_a_run_app_origin(self) -> None:
        for name, overrides, message in (
            ("staging_api", {"api_public_host": "api-staging.oday-plus.com.tw"}, "api_public_host must stay empty in staging"),
            ("staging_run_app", {"web_base_url": "https://x-2l6wuyl67q-de.a.run.app"}, "bare https custom-domain origin"),
        ):
            with self.subTest(name=name):
                code, output = self._plan(name, **overrides)
                self.assertNotEqual(code, 0)
                self.assertIn(message, " ".join(output.split()))


class ProductionCoreOpsPlanTests(unittest.TestCase):
    """Offline, credential-free full plans of the root stack for production.

    Owner decisions 2026-11-20: core-ops profile, local password sign-in, the
    Runtime Release deploy path owns the Cloud Run services, and Terraform
    creates a new private CMEK REGIONAL Cloud SQL instance next to the
    hand-made oday-prod-sql. The import blocks that adopt the hand-made
    identities and secrets are stripped (they read live objects) and checked
    statically instead.
    """

    PROJECT = "odayplus-prod-20260826"

    @classmethod
    def setUpClass(cls) -> None:
        _offline_root_copy(cls, strip_imports=True)

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "_tmpdir"):
            cls._tmpdir.cleanup()

    def _tfvars(self, **overrides: object) -> dict[str, object]:
        return {
            "project_id": self.PROJECT,
            "environment": "prod",
            "release_profile": "core-ops",
            "auth_mode": "local",
            "cloud_run_services_managed": False,
            "release_sha": "1" * 40,
            "cloud_sql_instance_name": "oday-prod-foundation-sql",
            "cloud_sql_tier": "db-custom-4-15360",
            "cloud_sql_disk_gb": 100,
            "cloud_sql_retained_backups": 30,
            "artifact_registry_repository": "oday-plus",
            "web_base_url": "https://console.oday-plus.com.tw",
            "web_load_balancer_enabled": True,
            "api_public_host": "api.oday-plus.com.tw",
            "service_auth_audiences": ["https://api.oday-plus.com.tw"],
            "identity_token_signing_key_ref": {"secret_id": "oday-prod-identity-token-signing-key", "version": "1"},
            "model_runtime_config": {"ODP_FORECAST_ENGINE": "statsforecast", "ODP_FORECAST_MODEL": "seasonal_naive"},
            "monitoring_notification_channel_ids": [f"projects/{self.PROJECT}/notificationChannels/1234567890"],
            "monitoring_api_uptime_host": "api.oday-plus.com.tw",
            "monitoring_cloud_sql_max_connections": 400,
            **overrides,
        }

    def _plan(self, name: str, tfvars: dict[str, object]) -> tuple[int, str]:
        import json

        (self.workdir / f"{name}.tfvars.json").write_text(json.dumps(tfvars), encoding="utf-8")
        result = self._run(
            "plan", "-no-color", "-refresh=false", f"-var-file={name}.tfvars.json",
            chdir=self.workdir, home=self.home,
        )
        return result.returncode, result.stdout + result.stderr

    @staticmethod
    def _created(output: str) -> set[str]:
        return set(re.findall(r"^  # (\S+) will be created$", output, re.MULTILINE))

    def test_core_ops_production_plans_the_foundation_without_cloud_run(self) -> None:
        # Pre-first-release foundation bootstrap: oday-prod-api does not exist
        # yet, so the cloud_run_revision uptime check waits until the first
        # Runtime Release deploy creates the service.
        bootstrap_code, bootstrap_output = self._plan("prod_core_ops_bootstrap", self._tfvars())
        self.assertEqual(bootstrap_code, 0, bootstrap_output)
        self.assertNotIn("google_monitoring_uptime_check_config.api_health[0]", self._created(bootstrap_output))
        self.assertIn("google_monitoring_uptime_check_config.web_login", self._created(bootstrap_output))
        self.assertIn('google_monitoring_alert_policy.uptime_failure["web"]', self._created(bootstrap_output))

        # Steady-state after first deploy: cloud_run_api_service_deployed = true
        # enables the OIDC-authenticated cloud_run_revision uptime check and alert.
        code, output = self._plan("prod_core_ops", self._tfvars(cloud_run_api_service_deployed=True))
        self.assertEqual(code, 0, output)
        created = self._created(output)
        self.assertFalse(
            [address for address in created if address.startswith("google_cloud_run_v2_service")],
            "production Cloud Run services and their service-scoped IAM belong to the deploy path",
        )
        for address in (
            "module.runtime_foundation.google_sql_database_instance.primary",
            "google_project_iam_custom_role.production_deployer_sql_backup[0]",
            "google_project_iam_member.production_deployer_sql_backup[0]",
            "google_project_iam_member.github_deployer_logging_viewer",
            "google_service_account_iam_member.github_deployer_token_creator_smoke_operator",
            "google_compute_region_network_endpoint_group.web_lb[0]",
            "google_compute_region_network_endpoint_group.api_lb[0]",
            'google_monitoring_alert_policy.cloud_run_5xx_ratio["api"]',
            "google_monitoring_alert_policy.api_latency_p95",
            "google_monitoring_uptime_check_config.api_health[0]",
            'google_monitoring_alert_policy.uptime_failure["api"]',
        ):
            self.assertIn(address, created)
        self.assertRegex(output, r'type\s+=\s+"cloud_run_revision"')
        self.assertIn('resource.type = \\"cloud_run_revision\\"', output)
        self.assertNotIn("staging_deployer", output)
        self.assertRegex(output, r'"attribute\.ref"\s*=\s*"assertion\.ref"')
        self.assertNotIn('"attribute.aud"', output)
        # The new instance, never the hand-made oday-prod-sql.
        self.assertIn('name                           = "oday-prod-foundation-sql"', output)
        self.assertNotIn('"oday-prod-sql"', output)
        self.assertIn('availability_type           = "REGIONAL"', output)
        self.assertIn("ipv4_enabled                                  = false", output)
        self.assertIn('tier                        = "db-custom-4-15360"', output)
        self.assertIn("retained_backups = 30", output)
        # NEGs and alert filters name the deploy-path services.
        self.assertIn('service = "oday-prod-web"', output)
        self.assertIn('service = "oday-prod-api"', output)
        self.assertIn('resource.labels.service_name = \\"oday-prod-api\\"', output)
        self.assertIn('account_id   = "oday-prod-smoke-operator"', output)
        for permission in (
            "cloudsql.backupRuns.create", "cloudsql.backupRuns.get", "cloudsql.backupRuns.list", "cloudsql.instances.get",
        ):
            self.assertIn(f'"{permission}"', output)
        self.assertRegex(
            output,
            rf'(?m)^\s*\+\s+audit_worm_sink_uri\s+= "gs://oday-prod-audit-worm-{self.PROJECT}/audit-evidence"$',
        )
        for name in ("audit_recovery_reader_service_account", "database_url_secret_ref"):
            self.assertRegex(output, rf"(?m)^\s*\+\s+{name}\s+= \(known after apply\)$")
        self.assertIn('account_id   = "oday-prod-audit-reader"', output)
        # No model is served: nothing model-related is required or projected.
        self.assertNotIn("ODP_AVM_", output)

    def test_production_fails_closed(self) -> None:
        cases = {
            "derived_sql_name": (
                {"cloud_sql_instance_name": ""},
                "the derived oday-prod-sql is the hand-made instance",
            ),
            "hand_made_sql_name": (
                {"cloud_sql_instance_name": "oday-prod-sql"},
                "the derived oday-prod-sql is the hand-made instance",
            ),
            "core_ops_without_forecast": (
                {"model_runtime_config": {"ODP_FORECAST_ENGINE": "statsforecast"}},
                "model_runtime_config is missing configuration its release profile requires",
            ),
            "full_without_models": (
                {"release_profile": "full"},
                "model_runtime_config is missing configuration its release profile requires",
            ),
            "full_without_mlflow": (
                {
                    "release_profile": "full",
                    "model_runtime_config": {
                        "ODP_AVM_LIQUIDITY_APPROVED_AT": "2026-11-20T00:00:00Z",
                        "ODP_AVM_LIQUIDITY_APPROVED_BY": "ml-risk-owner",
                        "ODP_AVM_LIQUIDITY_ARTIFACT_SHA256": "a" * 64,
                        "ODP_AVM_LIQUIDITY_ARTIFACT_URI": "gs://approved-models/avm",
                        "ODP_AVM_LIQUIDITY_DATASET_SNAPSHOT_ID": "snapshot-20261120",
                        "ODP_AVM_LIQUIDITY_MODEL_VERSION": "7",
                        "ODP_AVM_MODEL_NAME": "avm-liquidity",
                        "ODP_FORECAST_ENGINE": "statsforecast",
                        "ODP_FORECAST_MODEL": "seasonal_naive",
                    },
                },
                "requires a remote HTTPS MLflow registry",
            ),
            "managed_cloud_run_without_images": (
                {"cloud_run_services_managed": True},
                "require api_image and web_image",
            ),
            "placeholder_mlflow_on_core_ops": (
                {"mlflow_tracking_uri": "https://mlflow.placeholder.test"},
                "otherwise unauthoritative values",
            ),
        }
        for name, (overrides, message) in cases.items():
            with self.subTest(name=name):
                code, output = self._plan(name, self._tfvars(**overrides))
                self.assertNotEqual(code, 0, output)
                self.assertIn(message, " ".join(output.split()))

    def test_missing_release_profile_fails_closed(self) -> None:
        tfvars = self._tfvars()
        del tfvars["release_profile"]
        code, output = self._plan("no_profile", tfvars)
        self.assertNotEqual(code, 0)
        self.assertIn('No value for required variable', output)

    def test_dev_still_owns_its_cloud_run_services(self) -> None:
        image = "asia-east1-docker.pkg.dev/oday-dev-123456/oday/{}@sha256:" + "1" * 64
        code, output = self._plan(
            "dev_default",
            {
                "project_id": "oday-dev-123456",
                "environment": "dev",
                "release_profile": "full",
                "api_image": image.format("api"),
                "web_image": image.format("web"),
                "release_sha": "1" * 40,
            },
        )
        self.assertEqual(code, 0, output)
        created = self._created(output)
        for address in (
            "google_cloud_run_v2_service.api[0]",
            "google_cloud_run_v2_service.web[0]",
            "google_cloud_run_v2_service_iam_member.web_invokes_api[0]",
        ):
            self.assertIn(address, created)
        self.assertIn('account_id   = "oday-dev-smoke-operator"', output)
        self.assertIn('display_name = "ODay Dev Smoke Operator Service Account"', output)
        self.assertNotIn("production_deployer_sql_backup", output)


class ProductionAdoptionImportTests(unittest.TestCase):
    """The hand-made production objects the root declares are adopted, not created.

    Read back from odayplus-prod-20260826 on 2026-11-20: these exist under the
    names the root stack derives for prod, so a plain create would fail with
    409. oday-prod-sql must never be adopted.
    """

    EXPECTED = {
        "google_service_account.github_deployer": "github-deployer",
        "google_service_account.runtime": "${local.name_prefix}-runtime",
        "google_service_account.smoke_operator": "${local.name_prefix}-smoke-operator",
        "google_iam_workload_identity_pool.github_actions": "github-actions",
        "google_iam_workload_identity_pool_provider.odayplus": "github-actions/providers/odayplus",
        "google_secret_manager_secret.database_url": "${local.name_prefix}-database-url",
        "google_secret_manager_secret.web_session_secret": "${local.name_prefix}-web-session-secret",
    }

    def test_prod_only_imports_cover_exactly_the_hand_made_objects(self) -> None:
        iam = (ROOT / "iam.tf").read_text(encoding="utf-8")
        marker = "# --- Staging github-deployer grants for the ephemeral staging lifecycle ---"
        production = iam.split(marker, 1)[0]
        imports = re.findall(r"\nimport \{\n(.*?)\n\}", production, re.DOTALL)
        adopted = {}
        for body in imports:
            target = re.search(r"to\s+= (\S+)", body).group(1)
            match = re.search(r'for_each = local\.is_prod \? toset\(\["([^"]+)"\]\) : toset\(\[\]\)', body)
            self.assertIsNotNone(match, f"{target} must be adopted in production only")
            adopted[target] = match.group(1)
        self.assertEqual(adopted, self.EXPECTED)
        everything = "\n".join(path.read_text(encoding="utf-8") for path in ROOT.rglob("*.tf"))
        # The hand-made instance is never named, so it can be neither imported nor managed.
        self.assertNotIn('"oday-prod-sql"', everything)
        self.assertNotIn("to       = module.runtime_foundation.google_sql_database_instance", everything)


if __name__ == "__main__":
    unittest.main()
