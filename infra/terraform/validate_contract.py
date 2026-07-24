#!/usr/bin/env python3
"""Dependency-free structural checks for the ODay Plus Terraform contract.

This deliberately supplements, rather than replaces, ``terraform validate``.
It is runnable in restricted fleet workers where the Terraform binary and
provider plugins are unavailable.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

REQUIRED_FILES = {
    "checks.tf",
    "cloud_run.tf",
    "database.tf",
    "iam.tf",
    "kms.tf",
    "main.tf",
    "messaging.tf",
    "network.tf",
    "outputs.tf",
    "storage.tf",
    "variables.tf",
    "audit/main.tf",
    "audit/outputs.tf",
    "audit/variables.tf",
}

REQUIRED_TOKENS = {
    "main.tf": {
        'backend "gcs"',
        'version = "~> 5.35"',
        'source  = "hashicorp/google-beta"',
        'version = "~> 3.6"',
        "ODP_PERSISTENCE",
        'ODP_REQUIRE_LIVE_DATA',
        'ODP_EXTERNAL_PROVIDER_MODE',
        'ODP_AUTH_JWKS_URI',
        'MLFLOW_TRACKING_URI',
        'ODP_API_SERVICE_AUDIENCE',
        'ODP_DATA_BINDING_MODE',
        'ODP_WEB_BASE_URL',
        'ODP_WEB_OIDC_CLIENT_ID',
    },
    "checks.tf": {
        'resource "terraform_data" "production_contract"',
        "precondition",
        'check "production_image_and_capacity"',
        'check "production_identity_contract"',
        'check "production_external_provider_contract"',
        'check "production_model_runtime_contract"',
        'check "production_forbidden_values"',
        "@sha256:[0-9a-f]{64}$",
        "allUsers",
        "allAuthenticatedUsers",
        "var.web_image",
        "var.web_oidc_client_secret_ref",
        "var.web_invoker_members",
        "var.web_min_instances",
        "required_provider_secret_env_names",
        "required_model_config_env_names",
        "forbidden_production_value_pattern",
    },
    "database.tf": {
        'database_version    = "POSTGRES_16"',
        'availability_type           = local.is_prod ? "REGIONAL" : "ZONAL"',
        "point_in_time_recovery_enabled = true",
        "backup_retention_settings",
        "ipv4_enabled",
        "private_network",
        "ssl_mode",
        "encryption_key_name",
        "deletion_protection = local.is_prod",
        'resource "google_sql_user" "app"',
        'resource "google_secret_manager_secret_version" "database_url"',
    },
    "network.tf": {
        'resource "google_compute_network" "runtime"',
        'resource "google_service_networking_connection" "private_services"',
        'resource "google_compute_router_nat" "runtime"',
        'nat_ip_allocate_option',
        "private_ip_google_access = true",
    },
    "kms.tf": {
        'resource "google_kms_crypto_key" "runtime"',
        'rotation_period = "7776000s"',
        "prevent_destroy = true",
        "cloud_sql",
        "pubsub",
        "gcs",
    },
    "storage.tf": {
        'resource "google_storage_bucket" "artifacts"',
        'resource "google_storage_bucket" "source_snapshots"',
        'public_access_prevention    = "enforced"',
        "default_kms_key_name",
        "lock_retention_policy                 = local.is_prod",
    },
    "messaging.tf": {
        'resource "google_pubsub_topic" "jobs"',
        'resource "google_pubsub_topic" "dead_letter"',
        'resource "google_pubsub_subscription" "jobs"',
        "dead_letter_policy",
        "retry_policy",
        "allowed_persistence_regions",
    },
    "iam.tf": {
        'roles/cloudsql.client',
        'roles/secretmanager.secretAccessor',
        'roles/storage.objectUser',
    },
    "cloud_run.tf": {
        'image = var.api_image',
        'image = var.web_image',
        'resource "google_cloud_run_v2_service" "web"',
        'resource "google_cloud_run_v2_service_iam_member" "web_invokes_api"',
        'ODP_WEB_SESSION_SECRET',
        'service_account',
        'cloud_sql_instance',
        'mount_path = "/cloudsql"',
        'path = "/readiness"',
        'path = "/healthz"',
        'secret_key_ref',
        'egress = "ALL_TRAFFIC"',
        'INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER',
    },
}

FORBIDDEN_PLAINTEXT_ASSIGNMENTS = (
    re.compile(r"ODP_(?:LISTING|POI|GEOCODE|WEATHER|DEMOGRAPHICS)_PROVIDER_API_KEY\s*=\s*\"[^$]"),
    re.compile(r"ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN\s*=\s*\"[^$]"),
    re.compile(r"ODAY_DATABASE_URL\s*=\s*\"postgres"),
    re.compile(r"ODP_INTAKE_CURSOR_SIGNING_KEY\s*=\s*\"[^$]"),
)


def _balanced_hcl(text: str) -> bool:
    """Check braces while ignoring quoted strings and line comments."""

    depth = 0
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
        elif char == "#":
            newline = text.find("\n", index)
            if newline == -1:
                break
            index = newline
        elif char == "/" and index + 1 < len(text) and text[index + 1] == "/":
            newline = text.find("\n", index)
            if newline == -1:
                break
            index = newline
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return False
        index += 1
    return depth == 0 and not in_string


def validate(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    missing = sorted(path for path in REQUIRED_FILES if not (root / path).is_file())
    errors.extend(f"missing required file: {path}" for path in missing)

    texts: dict[str, str] = {}
    for path in sorted(REQUIRED_FILES - set(missing)):
        text = (root / path).read_text(encoding="utf-8")
        texts[path] = text
        if not _balanced_hcl(text):
            errors.append(f"unbalanced HCL braces or string: {path}")

    for path, tokens in REQUIRED_TOKENS.items():
        text = texts.get(path, "")
        for token in sorted(tokens):
            if token not in text:
                errors.append(f"{path}: missing production contract token {token!r}")

    combined = "\n".join(texts.values())
    for pattern in FORBIDDEN_PLAINTEXT_ASSIGNMENTS:
        if pattern.search(combined):
            errors.append(f"plaintext secret-like assignment matched {pattern.pattern!r}")

    outputs = texts.get("outputs.tf", "")
    for forbidden in (
        "random_password.database.result",
        "secret_data",
        "password =",
        "password=",
    ):
        if forbidden in outputs:
            errors.append(f"outputs.tf must not expose {forbidden!r}")

    if not re.search(
        r'ODP_PERSISTENCE\s*=\s*"postgresql"',
        texts.get("main.tf", ""),
    ):
        errors.append("runtime persistence is not fixed to PostgreSQL")
    if not re.search(
        r'ODP_EXTERNAL_PROVIDER_MODE\s*=\s*'
        r'\(local\.is_prod \|\| var\.live_data_enabled\) \? "live" : "fixture"',
        texts.get("main.tf", ""),
    ):
        errors.append("provider mode does not force live behavior in production")
    if "is_locked        = var.lock_retention_policy" not in texts.get("audit/main.tf", ""):
        errors.append("audit retention lock is not environment-controlled")

    return errors


def main() -> int:
    errors = validate()
    if errors:
        print("Terraform production contract validation: FAILED")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Terraform production contract validation: PASS")
    print(f"Checked {len(REQUIRED_FILES)} Terraform files without exposing secret values.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
