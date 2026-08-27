#!/usr/bin/env python3
"""Ephemeral Staging Lifecycle Manager.

Manages the creation, validation, cleanup, and orphan scanning of short-lived,
release-scoped staging environments in accordance with the ODay Plus Ephemeral
Staging Architecture Plan (ODP-DEPLOY-EPHEMERAL-STAGING-PROD-ROLLOUT-PLAN).

Key Guarantees:
1. Every ephemeral staging instance is strictly scoped by a unique ``release_id``.
2. Isolated database/schema, bucket, dedicated service accounts, IAM, Cloud Run
   services, and Pub/Sub messaging are created with release-scoped names & labels.
3. Cloud Scheduler triggers start in a PAUSED state.
4. Label-capable resources carry immutable tracking labels (owner, created_at,
   expires_at, ephemeral=true, managed_by=terraform, release_id, candidate_sha,
   manifest_digest_prefix); unsupported child resources are tracked by the
   release-scoped ownership manifest.
5. Cleanup operates ONLY via exact label matching; broad wildcards are forbidden.
6. Failed staging runs are retained for debugging up to 24 hours by default.
   TTL extensions require explicit owner and documented reason (max 168h / 7d).
7. An orphan scanner detects expired or unmanaged ephemeral staging resources
   and triggers remediation or automated safe cleanup.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# --- Validation Regex Patterns ---

RELEASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
SHA256_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
CANDIDATE_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
IMAGE_DIGEST_PATTERN = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
TENANT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,62}[a-z0-9]$")
PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
SERVICE_ACCOUNT_EMAIL_PATTERN = re.compile(
    r"^[a-z][a-z0-9-]{5,29}@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com$"
)

# Derived tenant ids are bounded to a valid GCP label length so the same value is
# accepted by the Terraform `tenant_id` validation and usable as a label verbatim.
TENANT_ID_PREFIX = "tenant-"
MAX_TENANT_ID_LENGTH = 63

DEFAULT_TTL_HOURS = 24
MAX_TTL_HOURS = 168  # 7 days max allowed extension
DEFAULT_EPHEMERAL_STATE_DIR = Path("/tmp/oday-plus-ephemeral-staging")
DEFAULT_EPHEMERAL_MODULE_DIR = Path("infra/terraform/modules/ephemeral_staging")
LIFECYCLE_STATE_VERSION = 1

# These are the only outputs the Runtime Release staging branch may use as
# runtime authority.  Foundation inputs (project, region, network, Cloud SQL
# instance and KMS/deployer identity) remain workflow/environment inputs; all
# release-scoped endpoints, jobs, tenant and identities must come from this
# Terraform handoff.
REQUIRED_STAGING_OUTPUTS: tuple[str, ...] = (
    "release_id",
    "staging_project_id",
    "created_at",
    "expires_at",
    "staging_api_uri",
    "staging_web_uri",
    "staging_api_service_name",
    "staging_web_service_name",
    "staging_database_name",
    "staging_data_bucket",
    "staging_tenant_id",
    "staging_runtime_service_account",
    "staging_web_service_account",
    "staging_worker_service_account",
    "staging_migration_job_name",
    "staging_worker_job_name",
    "staging_scheduler_job_name",
    "staging_scheduler_trigger_name",
    "staging_cloud_sql_instance",
    "staging_api_image",
    "staging_web_image",
    "staging_worker_image",
    "staging_scheduler_image",
    "resource_labels",
    "ownership_manifest",
)

# Identity errors that start with this prefix mean existing release state was
# found but could not be read or parsed. That state must be preserved: it is
# indistinguishable from a live release, so neither an apply nor a
# failure-path cleanup may run against it.
UNVERIFIABLE_STATE_PREFIX = "Existing release state is unverifiable"

# The tfvars sidecar is the only authoritative record of what an existing
# release actually is. Validating it field-by-field is not enough: every
# comparison is skipped when the stored value is absent, so a truncated bundle
# (in the limit, ``{}``) reads as "no conflict" and lets a rerun overwrite the
# sidecars and apply against state whose real identity nobody can prove. A
# rerun is therefore only allowed when *every* field below is present.
IMMUTABLE_RELEASE_IDENTITY_FIELDS: tuple[str, ...] = (
    "release_id",
    "project_id",
    "region",
    "tenant_id",
    "candidate_sha",
    "manifest_digest",
    "api_image",
    "web_image",
    "worker_image",
    "scheduler_image",
    "created_at",
    "owner_task_id",
)


class ReleaseIdentityConflict(RuntimeError):
    """Create was refused against existing release state, which stays untouched."""


class ReleaseStateUnverifiable(ReleaseIdentityConflict):
    """Existing release state exists but its immutable identity cannot be read."""


# --- Data Structures ---


@dataclasses.dataclass(frozen=True)
class StagingConfig:
    release_id: str
    candidate_sha: str
    manifest_digest: str
    project_id: str
    region: str = "asia-east1"
    tenant_id: str = ""
    cloud_sql_instance_name: str = "oday-staging-db"
    cloud_sql_connection_name: str = "project:asia-east1:oday-staging-db"
    network_name: str = "oday-staging-vpc"
    subnetwork_name: str = "oday-staging-subnet"
    # These are long-lived foundation inputs and must be supplied by the
    # protected staging environment. Placeholder defaults previously allowed a
    # workflow typo to reach Terraform and fail with an unrelated provider
    # error, or worse, target the wrong foundation.
    kms_key_id: str = ""
    deployer_service_account_email: str = ""
    api_image: str = "asia-east1-docker.pkg.dev/proj/repo/api@sha256:" + "0" * 64
    web_image: str = "asia-east1-docker.pkg.dev/proj/repo/web@sha256:" + "0" * 64
    worker_image: str = "asia-east1-docker.pkg.dev/proj/repo/worker@sha256:" + "0" * 64
    scheduler_image: str = "asia-east1-docker.pkg.dev/proj/repo/scheduler@sha256:" + "0" * 64
    ttl_hours: int = DEFAULT_TTL_HOURS
    created_at: str = ""
    owner_task_id: str = ""
    additional_labels: dict[str, str] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class StagingResource:
    resource_type: str
    resource_name: str
    resource_id: str
    release_id: str
    labels: dict[str, str]
    created_at: str
    expires_at: str
    status: str = "active"


@dataclasses.dataclass
class StagingLifecycleReceipt:
    action: str
    release_id: str
    candidate_sha: str
    manifest_digest_prefix: str
    success: bool
    timestamp: str
    resources: list[dict[str, Any]]
    errors: list[str] = dataclasses.field(default_factory=list)
    remediation_required: bool = False
    remediation_notes: str = ""
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class OrphanScanResult:
    scanned_at: str
    project_id: str
    total_scanned: int
    active_count: int
    expired_count: int
    orphan_count: int
    cleaned_count: int
    failed_cleanups: int
    expired_releases: list[str]
    orphan_resources: list[dict[str, Any]]
    cleaned_resources: list[dict[str, Any]]
    alerts: list[str]
    remediation_tasks: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# --- Core Helper Functions ---


def format_timestamp(dt: datetime) -> str:
    """Format datetime in UTC ISO 8601 string with Z suffix."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_timestamp(ts_str: str) -> datetime:
    """Parse ISO timestamp or sanitized label timestamp into timezone-aware datetime."""
    cleaned = ts_str.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        pass

    # Try label format: YYYY-MM-DD-HH-MM-SS or YYYY-MM-DD-HH-MM-SS-ffffff
    parts = cleaned.split("-")
    if len(parts) >= 6:
        try:
            year, month, day, hour, minute, second = (int(p) for p in parts[:6])
            return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
        except Exception:
            pass

    raise ValueError(f"Unable to parse timestamp: {ts_str!r}")


def sanitize_release_suffix(release_id: str) -> str:
    """Normalize release ID into a safe lowercase hyphenated suffix."""
    return re.sub(r"[^a-z0-9-]", "-", release_id.lower()).strip("-")


def compute_release_hash(release_id: str) -> str:
    """Compute deterministic 8-character hex hash of release_id to prevent naming collisions."""
    return hashlib.sha256(release_id.encode("utf-8")).hexdigest()[:8]


def bounded_label_value(value: str, *, max_length: int = 63) -> str:
    """Keep a generated GCP label value valid while preserving uniqueness."""
    normalized = re.sub(r"[^a-z0-9_-]", "-", value.lower()).strip("-")
    if len(normalized) <= max_length:
        return normalized
    suffix = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"{normalized[: max_length - len(suffix) - 1]}-{suffix}"


def release_label_value(release_id: str) -> str:
    """Return the canonical bounded release label used by Terraform and cleanup.

    Always appends the deterministic hash of the exact raw release_id to guarantee unique
    label identity and prevent collisions between release IDs that differ only by case
    or punctuation (e.g. rel_1.0 vs rel.1.0 vs rel-1-0 vs REL_1.0 vs rel_1_0).
    """
    normalized = sanitize_release_suffix(release_id)
    rel_hash = compute_release_hash(release_id)
    prefix = normalized[:54].strip("-")
    if prefix:
        return f"{prefix}-{rel_hash}"
    return f"rel-{rel_hash}"


def _authoritative_inventory_release_id(
    resource: Mapping[str, Any], labels: Mapping[str, str]
) -> str:
    """Resolve the raw release id that is allowed to drive destructive cleanup.

    ``release_id`` is intentionally stored as a bounded, hashed label and is
    not reversible.  An inventory row therefore has to carry the raw id from
    the Terraform ownership manifest (``raw_release_id``); a provider-facing
    ``release_id`` field is accepted only when it proves the same mapping.  A
    label-only row is reportable by the orphan scanner but can never authorize
    deletion.
    """
    label_value = str(labels.get("release_id", "")).strip()
    if not label_value:
        return ""

    candidates = (
        resource.get("raw_release_id"),
        resource.get("release_id"),
    )
    for candidate in candidates:
        raw_id = str(candidate or "").strip()
        if RELEASE_ID_PATTERN.fullmatch(raw_id) and release_label_value(raw_id) == label_value:
            return raw_id
    return ""


def derive_release_tenant_id(release_id: str) -> str:
    """Return the deterministic release-scoped tenant id used when none is supplied.

    ``infra/terraform/modules/ephemeral_staging/main.tf`` derives the identical
    value in ``local.tenant_derived``.  Both sides bound the result to
    ``MAX_TENANT_ID_LENGTH`` so a derived tenant always satisfies the module's
    ``tenant_id`` validation and stays usable as a GCP label value without a
    second round of hashing.  The release hash is computed from the exact raw
    release id, so truncating the readable slug never collapses two releases
    onto one tenant.
    """
    if not release_id.strip():
        return ""
    clean = sanitize_release_suffix(release_id) or "rel"
    rel_hash = compute_release_hash(release_id)
    budget = MAX_TENANT_ID_LENGTH - len(TENANT_ID_PREFIX) - 1 - len(rel_hash)
    if len(clean) > budget:
        clean = clean[:budget].strip("-") or "rel"
    return f"{TENANT_ID_PREFIX}{clean}-{rel_hash}"


def resolve_tenant_id(release_id: str, tenant_id: str = "") -> str:
    """Return the effective tenant id: the explicit one, else the release-derived one.

    Every layer that needs a tenant (tfvars, planned resources, labels, and the
    immutable-identity check) resolves it here so the Python planner and the
    Terraform module can never disagree about which tenant owns a release.
    """
    explicit = tenant_id.strip()
    if explicit:
        return explicit
    return derive_release_tenant_id(release_id)


def tenant_label_value(tenant_id: str, release_id: str = "") -> str:
    """Return canonical bounded tenant label."""
    clean = resolve_tenant_id(release_id, tenant_id) if release_id else tenant_id.strip()
    return bounded_label_value(clean) if clean else ""


def get_ephemeral_resource_names(
    release_id: str,
    project_id: str = "",
    tenant_id: str = "",
) -> dict[str, str]:
    """Compute collision-free, length-compliant GCP resource names for ephemeral staging."""
    clean = sanitize_release_suffix(release_id)
    rel_hash = compute_release_hash(release_id)

    # Keep this in lockstep with Terraform's trim(substr(..., 0, 13), "-")
    # so release ids ending at the boundary do not create a double hyphen in
    # Python-only inventory names.
    sa_slug = clean[:13].strip("-")
    sa_prefix = f"stg-{sa_slug}-{rel_hash}"

    db_slug_clean = clean.replace("-", "_")
    db_slug = db_slug_clean[:40]
    db_user_slug = db_slug_clean[:36]

    bucket_slug = clean[:12]
    bucket_name = f"stg-{bucket_slug}-{rel_hash}-data-{project_id}" if project_id else f"stg-{bucket_slug}-{rel_hash}-data"

    res_slug = clean[:24]
    name_prefix = f"stg-{res_slug}-{rel_hash}"

    effective_tenant = resolve_tenant_id(release_id, tenant_id)

    return {
        "name_prefix": name_prefix,
        "release_hash": rel_hash,
        "tenant_id": effective_tenant,
        "sa_runtime": f"{sa_prefix}-rt",
        "sa_web": f"{sa_prefix}-web",
        "sa_worker": f"{sa_prefix}-wkr",
        "database_name": f"stg_{db_slug}_{rel_hash}",
        "database_user": f"stg_{db_user_slug}_{rel_hash}_app",
        "bucket_name": bucket_name,
        "cloud_run_api": f"{name_prefix}-api",
        "cloud_run_web": f"{name_prefix}-web",
        "cloud_run_migration_job": f"{name_prefix}-migration",
        "cloud_run_worker_job": f"{name_prefix}-worker",
        "cloud_run_scheduler_job": f"{name_prefix}-scheduler",
        "jobs_topic": f"{name_prefix}-jobs",
        "jobs_dlq_topic": f"{name_prefix}-jobs-dlq",
        "jobs_sub": f"{name_prefix}-jobs",
        "jobs_dlq_sub": f"{name_prefix}-jobs-dlq",
        "secret_db_url": f"{name_prefix}-database-url",
        "secret_cursor_key": f"{name_prefix}-cursor-signing-key",
        "secret_web_session": f"{name_prefix}-web-session",
        "scheduler_job": f"{name_prefix}-worker-trigger",
    }


def generate_staging_labels(
    release_id: str,
    candidate_sha: str,
    manifest_digest: str,
    owner_task_id: str = "",
    tenant_id: str = "",
    ttl_hours: int = DEFAULT_TTL_HOURS,
    created_at: datetime | None = None,
    additional_labels: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Generate the canonical tracking label set for ephemeral staging resources."""
    now = created_at or datetime.now(UTC)
    expires = now + timedelta(hours=ttl_hours)
    release_suffix = release_label_value(release_id)

    digest_clean = manifest_digest.replace("sha256:", "")
    manifest_prefix = digest_clean[:16] if digest_clean else "0" * 16

    effective_tenant = resolve_tenant_id(release_id, tenant_id)
    tenant_label = bounded_label_value(effective_tenant) if effective_tenant else "unassigned"

    labels: dict[str, str] = {
        "app": "oday-plus",
        "environment": "staging",
        "managed_by": "terraform",
        "ephemeral": "true",
        "release_id": release_suffix,
        "tenant": tenant_label,
        "owner_task": bounded_label_value(owner_task_id) if owner_task_id else "unassigned",
        "candidate_sha": candidate_sha[:40].lower(),
        "manifest_digest_prefix": manifest_prefix,
        "created_at": now.strftime("%Y-%m-%d-%H-%M-%S"),
        "expires_at": expires.strftime("%Y-%m-%d-%H-%M-%S"),
    }

    if additional_labels:
        for k, v in additional_labels.items():
            if k not in labels:
                labels[k] = str(v)

    return labels


def validate_staging_config(config: StagingConfig, now: datetime | None = None) -> list[str]:
    """Validate all staging configuration parameters against schema and security rules."""
    errors: list[str] = []

    if not RELEASE_ID_PATTERN.fullmatch(config.release_id):
        errors.append(
            f"Invalid release_id: {config.release_id!r}. Must match {RELEASE_ID_PATTERN.pattern}"
        )

    if not config.owner_task_id or not TASK_ID_PATTERN.fullmatch(config.owner_task_id):
        errors.append(
            f"Invalid owner_task_id: {config.owner_task_id!r}. Must match {TASK_ID_PATTERN.pattern}"
        )

    if config.tenant_id.strip() and not TENANT_ID_PATTERN.fullmatch(config.tenant_id.strip()):
        errors.append(
            f"Invalid tenant_id: {config.tenant_id!r}. Must match {TENANT_ID_PATTERN.pattern}"
        )

    if not CANDIDATE_SHA_PATTERN.fullmatch(config.candidate_sha):
        errors.append(
            f"Invalid candidate_sha: {config.candidate_sha!r}. Must be a 40-character lowercase hex SHA."
        )

    if not SHA256_DIGEST_PATTERN.fullmatch(config.manifest_digest):
        errors.append(
            f"Invalid manifest_digest: {config.manifest_digest!r}. Must match sha256:<64 hex>."
        )

    if not IMAGE_DIGEST_PATTERN.fullmatch(config.api_image):
        errors.append(
            f"Invalid api_image: {config.api_image!r}. Must include an immutable @sha256:<64 hex> digest."
        )

    if not IMAGE_DIGEST_PATTERN.fullmatch(config.web_image):
        errors.append(
            f"Invalid web_image: {config.web_image!r}. Must include an immutable @sha256:<64 hex> digest."
        )

    if not IMAGE_DIGEST_PATTERN.fullmatch(config.worker_image):
        errors.append(
            f"Invalid worker_image: {config.worker_image!r}. Must include an immutable @sha256:<64 hex> digest."
        )

    if not IMAGE_DIGEST_PATTERN.fullmatch(config.scheduler_image):
        errors.append(
            f"Invalid scheduler_image: {config.scheduler_image!r}. Must include an immutable @sha256:<64 hex> digest."
        )

    if not (1 <= config.ttl_hours <= MAX_TTL_HOURS):
        errors.append(
            f"Invalid ttl_hours: {config.ttl_hours}. Must be between 1 and {MAX_TTL_HOURS} hours."
        )

    if not PROJECT_ID_PATTERN.fullmatch(config.project_id):
        errors.append(
            f"Invalid project_id: {config.project_id!r}. Must match valid GCP project ID format."
        )

    if not config.cloud_sql_instance_name.strip():
        errors.append("cloud_sql_instance_name must be non-empty.")

    if not config.kms_key_id.strip():
        errors.append("kms_key_id is a required protected staging foundation input.")
    elif config.kms_key_id.strip().lower() in {
        "projects/p/locations/asia-east1/keyrings/r/cryptokeys/k",
        "placeholder",
        "changeme",
    }:
        errors.append("kms_key_id must not use a placeholder foundation value.")

    if not config.deployer_service_account_email.strip():
        errors.append(
            "deployer_service_account_email is a required protected staging foundation input."
        )
    elif not SERVICE_ACCOUNT_EMAIL_PATTERN.fullmatch(config.deployer_service_account_email.strip()):
        errors.append(
            "deployer_service_account_email must be a concrete GCP service account email."
        )

    if config.created_at:
        try:
            created_dt = parse_timestamp(config.created_at)
            now_dt = now or datetime.now(UTC)
            # Future timestamps cannot be used to bypass TTL policy
            if created_dt > now_dt + timedelta(minutes=5):
                errors.append(
                    f"Invalid created_at: {config.created_at!r}. Creation timestamp cannot be in the future (current time: {format_timestamp(now_dt)})."
                )
        except Exception:
            errors.append(f"Invalid created_at: {config.created_at!r}. Must be valid ISO/RFC3339 timestamp.")

    return errors


def generate_tfvars(
    config: StagingConfig,
    created_at: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Generate Terraform variable mapping for ephemeral staging module."""
    ref_now = now or datetime.now(UTC)
    errors = validate_staging_config(config, now=ref_now)
    if errors:
        raise ValueError(f"Cannot generate tfvars for invalid config: {'; '.join(errors)}")

    if created_at is not None:
        target_created_dt = created_at
    elif config.created_at:
        target_created_dt = parse_timestamp(config.created_at)
    else:
        target_created_dt = ref_now

    return {
        "project_id": config.project_id,
        "region": config.region,
        "release_id": config.release_id,
        # Never emit an empty tenant_id: the module's `tenant_id` validation only
        # accepts null or a valid identifier, so an empty string would make every
        # default (no --tenant-id) create fail closed before provisioning.
        "tenant_id": resolve_tenant_id(config.release_id, config.tenant_id),
        "candidate_sha": config.candidate_sha,
        "manifest_digest": config.manifest_digest,
        "api_image": config.api_image,
        "web_image": config.web_image,
        "worker_image": config.worker_image,
        "scheduler_image": config.scheduler_image,
        "ttl_hours": config.ttl_hours,
        "created_at": format_timestamp(target_created_dt),
        "owner_task_id": config.owner_task_id,
        "cloud_sql_instance_name": config.cloud_sql_instance_name,
        "cloud_sql_connection_name": config.cloud_sql_connection_name,
        "network_name": config.network_name,
        "subnetwork_name": config.subnetwork_name,
        "kms_key_id": config.kms_key_id,
        "deployer_service_account_email": config.deployer_service_account_email,
        "additional_labels": config.additional_labels,
    }


def plan_staging_resources(
    config: StagingConfig,
    created_at: datetime | None = None,
    now: datetime | None = None,
) -> list[StagingResource]:
    """Compute the deterministic list of release-scoped ephemeral resources."""
    ref_now = now or datetime.now(UTC)
    if created_at is not None:
        target_created_dt = created_at
    elif config.created_at:
        target_created_dt = parse_timestamp(config.created_at)
    else:
        target_created_dt = ref_now
    expires = target_created_dt + timedelta(hours=config.ttl_hours)
    labels = generate_staging_labels(
        release_id=config.release_id,
        candidate_sha=config.candidate_sha,
        manifest_digest=config.manifest_digest,
        owner_task_id=config.owner_task_id,
        tenant_id=config.tenant_id,
        ttl_hours=config.ttl_hours,
        created_at=target_created_dt,
        additional_labels=config.additional_labels,
    )

    names = get_ephemeral_resource_names(config.release_id, config.project_id, tenant_id=config.tenant_id)
    created_iso = format_timestamp(target_created_dt)
    expires_iso = format_timestamp(expires)

    return [
        StagingResource(
            resource_type="google_sql_database",
            resource_name=names["database_name"],
            resource_id=f"projects/{config.project_id}/instances/{config.cloud_sql_instance_name}/databases/{names['database_name']}",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_sql_user",
            resource_name=names["database_user"],
            resource_id=f"projects/{config.project_id}/instances/{config.cloud_sql_instance_name}/users/{names['database_user']}",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_secret_manager_secret",
            resource_name=names["secret_db_url"],
            resource_id=f"projects/{config.project_id}/secrets/{names['secret_db_url']}",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_secret_manager_secret",
            resource_name=names["secret_cursor_key"],
            resource_id=f"projects/{config.project_id}/secrets/{names['secret_cursor_key']}",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_secret_manager_secret",
            resource_name=names["secret_web_session"],
            resource_id=f"projects/{config.project_id}/secrets/{names['secret_web_session']}",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_storage_bucket",
            resource_name=names["bucket_name"],
            resource_id=f"gs://{names['bucket_name']}",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_service_account",
            resource_name=names["sa_runtime"],
            resource_id=f"projects/{config.project_id}/serviceAccounts/{names['sa_runtime']}@{config.project_id}.iam.gserviceaccount.com",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_service_account",
            resource_name=names["sa_web"],
            resource_id=f"projects/{config.project_id}/serviceAccounts/{names['sa_web']}@{config.project_id}.iam.gserviceaccount.com",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_service_account",
            resource_name=names["sa_worker"],
            resource_id=f"projects/{config.project_id}/serviceAccounts/{names['sa_worker']}@{config.project_id}.iam.gserviceaccount.com",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_pubsub_topic",
            resource_name=names["jobs_topic"],
            resource_id=f"projects/{config.project_id}/topics/{names['jobs_topic']}",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_pubsub_topic",
            resource_name=names["jobs_dlq_topic"],
            resource_id=f"projects/{config.project_id}/topics/{names['jobs_dlq_topic']}",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_pubsub_subscription",
            resource_name=names["jobs_sub"],
            resource_id=f"projects/{config.project_id}/subscriptions/{names['jobs_sub']}",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_pubsub_subscription",
            resource_name=names["jobs_dlq_sub"],
            resource_id=f"projects/{config.project_id}/subscriptions/{names['jobs_dlq_sub']}",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_cloud_run_v2_service",
            resource_name=names["cloud_run_api"],
            resource_id=f"projects/{config.project_id}/locations/{config.region}/services/{names['cloud_run_api']}",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_cloud_run_v2_service",
            resource_name=names["cloud_run_web"],
            resource_id=f"projects/{config.project_id}/locations/{config.region}/services/{names['cloud_run_web']}",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_cloud_run_v2_job",
            resource_name=names["cloud_run_migration_job"],
            resource_id=f"projects/{config.project_id}/locations/{config.region}/jobs/{names['cloud_run_migration_job']}",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_cloud_run_v2_job",
            resource_name=names["cloud_run_worker_job"],
            resource_id=f"projects/{config.project_id}/locations/{config.region}/jobs/{names['cloud_run_worker_job']}",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_cloud_run_v2_job",
            resource_name=names["cloud_run_scheduler_job"],
            resource_id=f"projects/{config.project_id}/locations/{config.region}/jobs/{names['cloud_run_scheduler_job']}",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_cloud_scheduler_job",
            resource_name=names["scheduler_job"],
            resource_id=f"projects/{config.project_id}/locations/{config.region}/jobs/{names['scheduler_job']}",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
    ]


def _terraform_state_paths(
    release_id: str,
    state_dir: Path,
) -> tuple[Path, Path, Path]:
    """Return stable per-release state, variables, and inventory paths.

    Resolves accurately whether release_id is provided as a raw identifier
    or as an already-encoded release label.
    """
    state_path_root = state_dir.expanduser().resolve()
    clean_id = release_id.strip()

    # 1. Direct file stem match (e.g. if clean_id is already a stem/label)
    direct_state = state_path_root / f"{clean_id}.tfstate"
    direct_vars = state_path_root / f"{clean_id}.tfvars.json"
    if direct_state.is_file() or direct_vars.is_file():
        return (
            direct_state,
            direct_vars,
            state_path_root / f"{clean_id}.inventory.json",
        )

    # 2. Canonical stem computed from raw release_id
    suffix = sanitize_release_suffix(clean_id)
    release_hash = compute_release_hash(clean_id)
    stem = f"{suffix[:48]}-{release_hash}"
    stem_state = state_path_root / f"{stem}.tfstate"
    stem_vars = state_path_root / f"{stem}.tfvars.json"
    if stem_state.is_file() or stem_vars.is_file():
        return (
            stem_state,
            stem_vars,
            state_path_root / f"{stem}.inventory.json",
        )

    # 3. Check existing tfvars files for matching raw release_id or release_label
    target_label = release_label_value(clean_id)
    if state_path_root.is_dir():
        for tfvars_file in sorted(state_path_root.glob("*.tfvars.json")):
            try:
                data = json.loads(tfvars_file.read_text(encoding="utf-8"))
                rel = str(data.get("release_id", "")).strip()
                if rel and (
                    rel == clean_id
                    or rel == target_label
                    or release_label_value(rel) == clean_id
                    or release_label_value(rel) == target_label
                ):
                    base_stem = tfvars_file.name[:-len(".tfvars.json")]
                    return (
                        state_path_root / f"{base_stem}.tfstate",
                        tfvars_file,
                        state_path_root / f"{base_stem}.inventory.json",
                    )
            except Exception:
                continue

    # Default to computed stem
    return (
        state_path_root / f"{stem}.tfstate",
        state_path_root / f"{stem}.tfvars.json",
        state_path_root / f"{stem}.inventory.json",
    )


def _lifecycle_state_path(release_id: str, state_dir: Path) -> Path:
    """Return the release-scoped lifecycle marker beside Terraform state."""

    state_path, _, _ = _terraform_state_paths(release_id, state_dir.expanduser().resolve())
    return state_path.with_suffix(".lifecycle.json")


def _write_lifecycle_state(
    release_id: str,
    state_dir: Path,
    *,
    status: str,
    identity: Mapping[str, Any] | None = None,
    outputs: Mapping[str, Any] | None = None,
) -> Path:
    """Persist a secret-free, release-scoped state-machine checkpoint."""

    path = _lifecycle_state_path(release_id, state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema_version": LIFECYCLE_STATE_VERSION,
        "release_id": release_id,
        "status": status,
        "updated_at": format_timestamp(datetime.now(UTC)),
        "secret_values_redacted": True,
    }
    if identity:
        payload["identity"] = {
            key: str(identity[key])
            for key in (
                "release_id",
                "project_id",
                "region",
                "tenant_id",
                "candidate_sha",
                "manifest_digest",
                "api_image",
                "web_image",
                "worker_image",
                "scheduler_image",
                "created_at",
                "owner_task_id",
            )
            if key in identity
        }
    if outputs:
        # Terraform outputs are validated before this writer is called. Keep
        # the complete output object because endpoint/job/identity authority is
        # needed by the next lifecycle stage; outputs contain no secret values.
        payload["outputs"] = dict(outputs)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _read_json_object(path: Path) -> dict[str, Any] | None:
    """Read a JSON object without turning malformed state into an empty state."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _terraform_output_values(
    *,
    module_dir: Path,
    terraform_bin: str,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """Read Terraform's current release outputs, refusing missing/secret values."""

    command = [
        terraform_bin,
        f"-chdir={module_dir}",
        "output",
        "-json",
    ]
    if state_path is not None:
        command.append(f"-state={state_path}")
    process = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    if process.returncode != 0:
        stderr_lines = [line for line in process.stderr.splitlines() if line.strip()]
        detail = " ".join(stderr_lines[-8:]) if stderr_lines else "no diagnostic output"
        raise RuntimeError(
            f"terraform output failed (exit {process.returncode}): {detail}"
        )
    try:
        raw = json.loads(process.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("terraform output did not return a JSON object") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("terraform output did not return an output mapping")

    values: dict[str, Any] = {}
    for name, entry in raw.items():
        if not isinstance(entry, Mapping) or "value" not in entry:
            raise RuntimeError(f"terraform output {name!r} is missing its value")
        if entry.get("sensitive") is True:
            raise RuntimeError(f"terraform output {name!r} is sensitive and cannot be handed off")
        values[str(name)] = entry["value"]
    return values


def _terraform_backend_arguments(
    *,
    backend_bucket: str,
    backend_prefix: str,
) -> list[str]:
    """Return validated GCS backend init arguments for one release key."""

    bucket = backend_bucket.strip()
    prefix = backend_prefix.strip().strip("/")
    if not bucket or not prefix:
        raise ValueError(
            "Terraform GCS backend requires both a protected bucket and a release-scoped prefix"
        )
    if any(value in bucket or value in prefix for value in ("*", "?", "[", "]", "..")):
        raise ValueError("Terraform GCS backend bucket/prefix must not contain wildcards or traversal")
    if bucket.startswith("gs://") or "/" in bucket:
        raise ValueError("Terraform GCS backend bucket must be a bucket name, not a URI or path")
    return [f"-backend-config=bucket={bucket}", f"-backend-config=prefix={prefix}"]


def _validate_backend_prefix_for_release(backend_prefix: str, release_id: str) -> None:
    """Require the durable state key to terminate in this exact release id."""

    prefix = backend_prefix.strip().strip("/")
    final_segment = prefix.rsplit("/", 1)[-1] if prefix else ""
    if final_segment not in {release_id.strip(), release_label_value(release_id)}:
        raise ValueError(
            "Terraform GCS backend prefix must terminate in the exact release_id or its canonical label"
        )


def _terraform_state_pull(
    *,
    module_dir: Path,
    terraform_bin: str,
    allow_missing: bool = False,
) -> dict[str, Any] | None:
    """Read the durable backend state without printing its sensitive payload."""

    process = subprocess.run(
        [terraform_bin, f"-chdir={module_dir}", "state", "pull"],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    if process.returncode != 0:
        stderr_lines = [line for line in process.stderr.splitlines() if line.strip()]
        detail = " ".join(stderr_lines[-8:]) if stderr_lines else "no diagnostic output"
        if allow_missing:
            normalized = detail.lower()
            empty_state_markers = (
                "no state file was found",
                "state file was not found",
                "state file does not exist",
                "no state exists",
                "state does not exist",
                "state not found",
                "state was not found",
                "remote state is empty",
                "state is empty",
            )
            if any(marker in normalized for marker in empty_state_markers):
                return None
        raise RuntimeError(f"terraform state pull failed (exit {process.returncode}): {detail}")
    try:
        state = json.loads(process.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Terraform durable state pull did not return a JSON object") from exc
    if not isinstance(state, Mapping):
        raise RuntimeError("Terraform durable state pull did not return a state mapping")
    if not state.get("resources"):
        if allow_missing:
            return None
        raise RuntimeError("Terraform durable state pull returned no managed resources")
    return dict(state)


def validate_staging_outputs(
    outputs: Mapping[str, Any] | None,
    *,
    release_id: str,
    candidate_sha: str,
    manifest_digest: str,
    project_id: str,
    worker_image: str = "",
    scheduler_image: str = "",
) -> list[str]:
    """Validate the immutable Terraform output handoff used by live staging."""

    errors: list[str] = []
    if not isinstance(outputs, Mapping):
        return [
            "Live staging requires the release-scoped Terraform output handoff; "
            "missing outputs cannot be replaced by static environment variables."
        ]
    missing = [name for name in REQUIRED_STAGING_OUTPUTS if name not in outputs]
    if missing:
        errors.append(
            "Terraform output handoff is incomplete; missing release-scoped output(s): "
            + ", ".join(missing)
        )

    if str(outputs.get("release_id", "")).strip() != release_id:
        errors.append("Terraform release_id output does not match the immutable release handoff.")
    for name in ("created_at", "expires_at"):
        value = str(outputs.get(name, "")).strip()
        if value:
            try:
                parse_timestamp(value)
            except ValueError:
                errors.append(f"Terraform {name} output is not a valid RFC3339 timestamp.")
    created_value = str(outputs.get("created_at", "")).strip()
    expires_value = str(outputs.get("expires_at", "")).strip()
    if created_value and expires_value:
        try:
            if parse_timestamp(expires_value) < parse_timestamp(created_value):
                errors.append("Terraform expires_at output precedes created_at.")
        except ValueError:
            pass
    if str(outputs.get("staging_tenant_id", "")).strip() == "":
        errors.append("Terraform staging_tenant_id output is empty.")
    for name in (
        "staging_api_uri",
        "staging_web_uri",
        "staging_api_service_name",
        "staging_web_service_name",
        "staging_database_name",
        "staging_data_bucket",
        "staging_runtime_service_account",
        "staging_web_service_account",
        "staging_worker_service_account",
        "staging_migration_job_name",
        "staging_worker_job_name",
        "staging_scheduler_job_name",
        "staging_scheduler_trigger_name",
        "staging_cloud_sql_instance",
    ):
        if not str(outputs.get(name, "")).strip():
            errors.append(f"Terraform {name} output is empty.")

    for name in ("staging_api_uri", "staging_web_uri"):
        if str(outputs.get(name, "")).strip() and not str(outputs[name]).startswith("https://"):
            errors.append(f"Terraform {name} must be an HTTPS release-scoped endpoint.")

    for name in (
        "staging_runtime_service_account",
        "staging_web_service_account",
        "staging_worker_service_account",
    ):
        value = str(outputs.get(name, "")).strip()
        if value and not SERVICE_ACCOUNT_EMAIL_PATTERN.fullmatch(value):
            errors.append(f"Terraform {name} is not a concrete service account email.")

    labels = outputs.get("resource_labels")
    if not isinstance(labels, Mapping):
        errors.append("Terraform resource_labels output is not a readable mapping.")
    else:
        expected_prefix = manifest_digest.removeprefix("sha256:")[:16]
        if labels.get("release_id") != release_label_value(release_id):
            errors.append("Terraform resource_labels release_id is not bound to the raw release_id.")
        if labels.get("candidate_sha") != candidate_sha:
            errors.append("Terraform resource_labels candidate_sha is not bound to the release SHA.")
        if labels.get("manifest_digest_prefix") != expected_prefix:
            errors.append("Terraform resource_labels manifest digest is not bound to the manifest.")
        if labels.get("environment") != "staging" or labels.get("ephemeral") != "true":
            errors.append("Terraform resource_labels do not prove ephemeral staging ownership.")

    if str(outputs.get("staging_api_image", "")).strip() == "":
        errors.append("Terraform staging_api_image output is empty.")
    expected_images = {
        "staging_api_image": "api_image",
        "staging_web_image": "web_image",
        "staging_worker_image": "worker_image",
        "staging_scheduler_image": "scheduler_image",
    }
    supplied_images = {
        "api_image": "",
        "web_image": "",
        "worker_image": worker_image,
        "scheduler_image": scheduler_image,
    }
    for output_name, input_name in expected_images.items():
        output_image = str(outputs.get(output_name, "")).strip()
        supplied = supplied_images[input_name].strip()
        if supplied and output_image != supplied:
            errors.append(
                f"Terraform {output_name} does not match the immutable {input_name} handoff."
            )
        if output_image and not IMAGE_DIGEST_PATTERN.fullmatch(output_image):
            errors.append(f"Terraform {output_name} is not an immutable image digest reference.")

    ownership = outputs.get("ownership_manifest")
    if not isinstance(ownership, Mapping):
        errors.append("Terraform ownership_manifest output is missing or unreadable.")
    else:
        resources = ownership.get("resources")
        if not isinstance(resources, Mapping) or not resources:
            errors.append("Terraform ownership_manifest has no release-scoped resources.")

    output_project = str(outputs.get("staging_project_id", project_id)).strip()
    if not output_project:
        errors.append("Terraform staging_project_id output is empty.")
    if output_project and output_project != project_id:
        errors.append("Terraform staging project output does not match the release foundation project.")
    return errors


def _run_terraform(
    *,
    module_dir: Path,
    terraform_bin: str,
    arguments: Sequence[str],
) -> None:
    """Run one non-interactive Terraform command without exposing stdout secrets."""
    command = [terraform_bin, f"-chdir={module_dir}", *arguments]
    process = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    if process.returncode == 0:
        return

    # Terraform can echo variable values in diagnostics. Keep only a bounded
    # stderr tail and never include stdout in an operations receipt.
    stderr_lines = [line for line in process.stderr.splitlines() if line.strip()]
    detail = " ".join(stderr_lines[-8:]) if stderr_lines else "no diagnostic output"
    raise RuntimeError(f"terraform {' '.join(arguments[:2])} failed (exit {process.returncode}): {detail}")


def validate_immutable_release_identity(
    config: StagingConfig,
    state_dir: Path,
) -> list[str]:
    """Validate that config matches immutable release identity if release was already provisioned.

    In accordance with Rollout Plan §5.2:
    Once a manifest enters staging it must not be rewritten. Any change to candidate commit SHA,
    manifest digest, container images, or target project requires a new release_id.
    """
    state_path_root = state_dir.expanduser().resolve()
    if not state_path_root.is_dir():
        return []

    state_path, tfvars_path, inventory_path = _terraform_state_paths(config.release_id, state_path_root)
    errors: list[str] = []

    # Fail closed: if terraform state exists but neither identity sidecar is
    # present, the release identity cannot be verified.  Returning an empty
    # error list here would let make_terraform_creation_executor write new
    # tfvars/inventory and apply against the orphan state, potentially mutating
    # resources whose candidate/manifest/project/tenant/created_at are unknown.
    if state_path.is_file() and not tfvars_path.is_file() and not inventory_path.is_file():
        return [
            f"{UNVERIFIABLE_STATE_PREFIX}: terraform state file {state_path.name} exists for "
            f"release {config.release_id!r} but neither tfvars nor inventory sidecars are present. "
            "The identity of the existing release cannot be verified; a new release_id is required "
            "or the orphan state must be inspected and removed by an operator."
        ]

    # Fail closed: tfstate + inventory but no tfvars means the authoritative
    # identity sidecar is missing.  The inventory alone only carries label
    # prefixes (e.g. manifest_digest_prefix) and cannot prove the full identity
    # (project, full manifest, images, tenant, created_at).  A creation executor
    # could write new tfvars and apply against unverifiable state.
    if state_path.is_file() and not tfvars_path.is_file() and inventory_path.is_file():
        return [
            f"{UNVERIFIABLE_STATE_PREFIX}: terraform state file {state_path.name} exists for "
            f"release {config.release_id!r} with inventory but without the authoritative tfvars "
            "sidecar. Inventory labels alone cannot prove project, full manifest digest, images, "
            "tenant, or created_at; a new release_id is required or the orphan state must be "
            "inspected and restored by an operator."
        ]

    # Fail closed: tfstate + tfvars but no inventory means the release-scoped
    # ownership manifest is gone. cleanup_ephemeral_staging and scan_orphans both
    # read that manifest to enumerate what the release owns, so applying here
    # would extend a release that can no longer be safely deleted.
    if state_path.is_file() and tfvars_path.is_file() and not inventory_path.is_file():
        return [
            f"{UNVERIFIABLE_STATE_PREFIX}: terraform state file {state_path.name} exists for "
            f"release {config.release_id!r} with tfvars but without the release-scoped inventory "
            "manifest. The provisioned resource set cannot be enumerated, so neither an apply nor "
            "a cleanup may run; an operator must restore or remove the state."
        ]

    if tfvars_path.is_file():
        try:
            prev_vars = json.loads(tfvars_path.read_text(encoding="utf-8"))
            if isinstance(prev_vars, dict):
                # Completeness first. Without the full bundle the per-field
                # comparisons below prove nothing, because each one is a no-op
                # when the stored side is missing.
                missing_fields = [
                    field
                    for field in IMMUTABLE_RELEASE_IDENTITY_FIELDS
                    if not str(prev_vars.get(field, "")).strip()
                ]
                if missing_fields:
                    errors.append(
                        f"{UNVERIFIABLE_STATE_PREFIX}: tfvars file {tfvars_path.name} for release "
                        f"{config.release_id!r} is missing the immutable identity field(s) "
                        f"{', '.join(missing_fields)}. A partial bundle cannot prove what the "
                        "existing release is; the state is preserved and this rerun is rejected."
                    )

                # The sidecar resolved for this release must actually belong to
                # it. _terraform_state_paths can land on a stem by file-name
                # match, so the stored release_id is the only proof of ownership.
                prev_release = str(prev_vars.get("release_id", "")).strip()
                if prev_release and release_label_value(prev_release) != release_label_value(config.release_id):
                    errors.append(
                        f"Existing release state for {config.release_id!r} was provisioned for a "
                        f"different release_id {prev_release!r}; rerun against another release's "
                        "state is rejected."
                    )

                prev_region = str(prev_vars.get("region", "")).strip()
                if prev_region and config.region.strip() != prev_region:
                    errors.append(
                        f"Existing release state for {config.release_id!r} has immutable region {prev_region!r}; "
                        f"rerun with region {config.region.strip()!r} is rejected."
                    )

                prev_owner_task = str(prev_vars.get("owner_task_id", "")).strip()
                if prev_owner_task and config.owner_task_id.strip() != prev_owner_task:
                    errors.append(
                        f"Existing release state for {config.release_id!r} has immutable owner_task_id "
                        f"{prev_owner_task!r}; rerun with owner_task_id {config.owner_task_id.strip()!r} is rejected."
                    )

                prev_candidate = str(prev_vars.get("candidate_sha", "")).strip().lower()
                if prev_candidate and config.candidate_sha.strip().lower() != prev_candidate:
                    errors.append(
                        f"Existing release state for {config.release_id!r} has immutable candidate_sha {prev_candidate!r}; "
                        f"rerun with candidate_sha {config.candidate_sha.strip().lower()!r} is rejected. "
                        "Rollout plan §5.2 requires a new release_id for code/candidate changes."
                    )

                prev_manifest = str(prev_vars.get("manifest_digest", "")).strip()
                if prev_manifest and config.manifest_digest.strip() != prev_manifest:
                    errors.append(
                        f"Existing release state for {config.release_id!r} has immutable manifest_digest {prev_manifest!r}; "
                        f"rerun with manifest_digest {config.manifest_digest.strip()!r} is rejected. "
                        "Rollout plan §5.2 requires a new release_id for manifest changes."
                    )

                prev_api = str(prev_vars.get("api_image", "")).strip()
                if prev_api and config.api_image.strip() != prev_api:
                    errors.append(
                        f"Existing release state for {config.release_id!r} has immutable api_image {prev_api!r}; "
                        f"rerun with api_image {config.api_image.strip()!r} is rejected. "
                        "Rollout plan §5.2 requires a new release_id for image changes."
                    )

                prev_web = str(prev_vars.get("web_image", "")).strip()
                if prev_web and config.web_image.strip() != prev_web:
                    errors.append(
                        f"Existing release state for {config.release_id!r} has immutable web_image {prev_web!r}; "
                        f"rerun with web_image {config.web_image.strip()!r} is rejected. "
                        "Rollout plan §5.2 requires a new release_id for image changes."
                    )

                prev_worker = str(prev_vars.get("worker_image", "")).strip()
                if prev_worker and config.worker_image.strip() != prev_worker:
                    errors.append(
                        f"Existing release state for {config.release_id!r} has immutable worker_image {prev_worker!r}; "
                        f"rerun with worker_image {config.worker_image.strip()!r} is rejected. "
                        "Rollout plan §5.2 requires a new release_id for image changes."
                    )

                prev_scheduler = str(prev_vars.get("scheduler_image", "")).strip()
                if prev_scheduler and config.scheduler_image.strip() != prev_scheduler:
                    errors.append(
                        f"Existing release state for {config.release_id!r} has immutable scheduler_image {prev_scheduler!r}; "
                        f"rerun with scheduler_image {config.scheduler_image.strip()!r} is rejected. "
                        "Rollout plan §5.2 requires a new release_id for image changes."
                    )

                prev_project = str(prev_vars.get("project_id", "")).strip()
                if prev_project and config.project_id.strip() != prev_project:
                    errors.append(
                        f"Existing release state for {config.release_id!r} has project_id {prev_project!r}; "
                        f"rerun with project_id {config.project_id.strip()!r} is rejected."
                    )

                # Compare the resolved tenant, not the raw input: a rerun that
                # supplies an explicit tenant after a release was created with the
                # derived one is still a tenant change and must be rejected.
                prev_tenant = str(prev_vars.get("tenant_id", "")).strip()
                current_tenant = resolve_tenant_id(config.release_id, config.tenant_id)
                if prev_tenant and current_tenant and current_tenant != prev_tenant:
                    errors.append(
                        f"Existing release state for {config.release_id!r} has immutable tenant_id {prev_tenant!r}; "
                        f"rerun with tenant_id {current_tenant!r} is rejected."
                    )

                prev_created = str(prev_vars.get("created_at", "")).strip()
                if prev_created and config.created_at:
                    if parse_timestamp(config.created_at) != parse_timestamp(prev_created):
                        errors.append(
                            f"Existing release state for {config.release_id!r} has authoritative created_at {prev_created!r}; "
                            f"rerun with created_at {config.created_at!r} is rejected."
                        )
            else:
                errors.append(
                    f"{UNVERIFIABLE_STATE_PREFIX}: tfvars file {tfvars_path.name} for release "
                    f"{config.release_id!r} is not a JSON object, so the immutable identity of the "
                    "existing release cannot be verified."
                )
        except Exception as exc:
            # Never fall through to an empty error list here. A swallowed parse or
            # read error reads as "no existing release", which lets an apply run
            # against live state and lets the failure path destroy it.
            errors.append(
                f"{UNVERIFIABLE_STATE_PREFIX}: tfvars file {tfvars_path.name} for release "
                f"{config.release_id!r} could not be read or parsed ({type(exc).__name__}: {exc}); "
                "the existing release is preserved and this rerun is rejected."
            )

    if inventory_path.is_file():
        try:
            prev_inv = json.loads(inventory_path.read_text(encoding="utf-8"))
            if isinstance(prev_inv, list) and prev_inv:
                # Every inventory member must be a dict with readable labels;
                # a single unreadable row makes the whole release unverifiable.
                for idx, inv_row in enumerate(prev_inv):
                    if not isinstance(inv_row, dict):
                        errors.append(
                            f"{UNVERIFIABLE_STATE_PREFIX}: inventory file {inventory_path.name} for release "
                            f"{config.release_id!r} has a non-dict entry at index {idx}."
                        )
                        continue
                    inv_labels = inv_row.get("labels", {})
                    if not isinstance(inv_labels, dict):
                        errors.append(
                            f"{UNVERIFIABLE_STATE_PREFIX}: inventory file {inventory_path.name} for release "
                            f"{config.release_id!r} has no readable ownership labels at index {idx}."
                        )
                        continue

                    # The same full-ownership proof cleanup requires. A row that
                    # cannot be proven to belong to this release makes the whole
                    # manifest unusable as an identity record. The per-field
                    # comparisons below still run, so a mismatch is reported with
                    # its specific cause as well.
                    if not is_staging_ephemeral_resource(inv_labels, config.release_id):
                        errors.append(
                            f"{UNVERIFIABLE_STATE_PREFIX}: inventory file {inventory_path.name} for release "
                            f"{config.release_id!r} has an entry at index {idx} whose labels do not prove "
                            "full release-scoped ownership (release_id, app, environment, managed_by, "
                            "ephemeral, candidate_sha, manifest_digest_prefix, owner_task, created_at, "
                            "expires_at)."
                        )

                    # candidate_sha: must match across every member
                    inv_candidate = str(inv_labels.get("candidate_sha", "")).strip().lower()
                    if inv_candidate and config.candidate_sha.strip().lower() != inv_candidate:
                        msg = (
                            f"Existing release inventory for {config.release_id!r} has immutable candidate_sha {inv_candidate!r} "
                            f"at index {idx}; rerun with candidate_sha {config.candidate_sha.strip().lower()!r} is rejected. "
                            "Rollout plan §5.2 requires a new release_id for code/candidate changes."
                        )
                        if msg not in errors:
                            errors.append(msg)

                    # manifest_digest_prefix: must match across every member
                    inv_manifest_prefix = str(inv_labels.get("manifest_digest_prefix", "")).strip().lower()
                    clean_manifest_prefix = config.manifest_digest.replace("sha256:", "")[:16].lower()
                    if inv_manifest_prefix and clean_manifest_prefix != inv_manifest_prefix:
                        msg = (
                            f"Existing release inventory for {config.release_id!r} has immutable manifest_digest_prefix {inv_manifest_prefix!r} "
                            f"at index {idx}; rerun with manifest_digest {config.manifest_digest.strip()!r} is rejected. "
                            "Rollout plan §5.2 requires a new release_id for manifest changes."
                        )
                        if msg not in errors:
                            errors.append(msg)

                    # tenant: the inventory records the bounded tenant label; the
                    # current config must resolve to the same label value.
                    inv_tenant_label = str(inv_labels.get("tenant", "")).strip()
                    current_tenant = resolve_tenant_id(config.release_id, config.tenant_id)
                    current_tenant_label = bounded_label_value(current_tenant) if current_tenant else "unassigned"
                    if inv_tenant_label and current_tenant_label and inv_tenant_label != current_tenant_label:
                        msg = (
                            f"Existing release inventory for {config.release_id!r} has immutable tenant label {inv_tenant_label!r} "
                            f"at index {idx}; rerun with tenant label {current_tenant_label!r} is rejected."
                        )
                        if msg not in errors:
                            errors.append(msg)

                    # created_at: the authoritative creation timestamp must not shift
                    inv_created = str(inv_labels.get("created_at", "")).strip()
                    if inv_created and config.created_at:
                        try:
                            if parse_timestamp(config.created_at) != parse_timestamp(inv_created):
                                msg = (
                                    f"Existing release inventory for {config.release_id!r} has authoritative created_at {inv_created!r} "
                                    f"at index {idx}; rerun with created_at {config.created_at!r} is rejected."
                                )
                                if msg not in errors:
                                    errors.append(msg)
                        except ValueError:
                            pass  # Unparseable label timestamps are already caught by label checks
            else:
                errors.append(
                    f"{UNVERIFIABLE_STATE_PREFIX}: inventory file {inventory_path.name} for release "
                    f"{config.release_id!r} is not a non-empty JSON array of resource records."
                )
        except Exception as exc:
            errors.append(
                f"{UNVERIFIABLE_STATE_PREFIX}: inventory file {inventory_path.name} for release "
                f"{config.release_id!r} could not be read or parsed ({type(exc).__name__}: {exc}); "
                "the existing release is preserved and this rerun is rejected."
            )

    return errors


def make_terraform_creation_executor(
    *,
    module_dir: Path = DEFAULT_EPHEMERAL_MODULE_DIR,
    state_dir: Path = DEFAULT_EPHEMERAL_STATE_DIR,
    terraform_bin: str = "terraform",
    initialize: bool = True,
    outputs_path: Path | None = None,
    backend_bucket: str = "",
    backend_prefix: str = "",
) -> Callable[[StagingConfig, Sequence[StagingResource]], Mapping[str, Any]]:
    """Build the live create executor used by the CLI.

    Terraform state and tfvars are isolated by release id. The planned
    ``created_at`` is persisted into tfvars before apply, so a subsequent
    apply uses the same timestamp rather than refreshing the TTL.
    """
    module_path = module_dir.expanduser().resolve()
    state_path_root = state_dir.expanduser().resolve()
    backend_args = _terraform_backend_arguments(
        backend_bucket=backend_bucket,
        backend_prefix=backend_prefix,
    ) if backend_bucket or backend_prefix else []
    remote_backend = bool(backend_args)

    def execute(config: StagingConfig, resources: Sequence[StagingResource]) -> Mapping[str, Any]:
        if not module_path.is_dir():
            raise RuntimeError(f"Terraform module directory does not exist: {module_path}")
        if not resources:
            raise RuntimeError("Terraform create received an empty resource plan")
        if remote_backend:
            _validate_backend_prefix_for_release(backend_prefix, config.release_id)

        # A GCS backend is the recovery authority. Initialize and inspect it
        # before writing local sidecars: a fresh runner must never overwrite an
        # existing release whose local recovery bundle was lost, and stale local
        # sidecars must never masquerade as an empty remote state.
        remote_state: dict[str, Any] | None = None
        initialized = False
        if remote_backend and initialize:
            try:
                _run_terraform(
                    module_dir=module_path,
                    terraform_bin=terraform_bin,
                    arguments=["init", "-input=false", "-upgrade=false", *backend_args],
                )
            except RuntimeError as exc:
                raise ReleaseStateUnverifiable(
                    f"Existing release state for {config.release_id!r} is unavailable: "
                    f"{UNVERIFIABLE_STATE_PREFIX}: durable Terraform backend initialization "
                    f"failed ({exc}). No apply or failure-path cleanup is permitted."
                ) from exc
            initialized = True

        state_path_root.mkdir(parents=True, exist_ok=True)
        state_path, tfvars_path, inventory_path = _terraform_state_paths(config.release_id, state_path_root)
        lifecycle_path = _lifecycle_state_path(config.release_id, state_path_root)
        if remote_backend:
            try:
                remote_state = _terraform_state_pull(
                    module_dir=module_path,
                    terraform_bin=terraform_bin,
                    allow_missing=True,
                )
            except RuntimeError as exc:
                raise ReleaseStateUnverifiable(
                    f"Existing release state for {config.release_id!r} is unavailable: "
                    f"{UNVERIFIABLE_STATE_PREFIX}: durable Terraform state could not be read "
                    f"({exc}). Restore backend access before retrying."
                ) from exc
            local_sidecars = (tfvars_path, inventory_path, lifecycle_path)
            if remote_state is not None and not all(path.is_file() for path in local_sidecars):
                raise ReleaseStateUnverifiable(
                    f"Existing release state conflict for {config.release_id!r}: "
                    f"{UNVERIFIABLE_STATE_PREFIX}: durable Terraform state exists but the "
                    "tfvars, inventory, and lifecycle recovery sidecars are incomplete. "
                    "Restore the protected recovery bundle before retrying."
                )
            if remote_state is not None:
                lifecycle_state = _read_json_object(lifecycle_path)
                if (
                    lifecycle_state is None
                    or lifecycle_state.get("release_id") != config.release_id
                    or not isinstance(lifecycle_state.get("outputs"), Mapping)
                ):
                    raise ReleaseStateUnverifiable(
                        f"Existing release state conflict for {config.release_id!r}: "
                        f"{UNVERIFIABLE_STATE_PREFIX}: lifecycle recovery marker is malformed "
                        "or has no immutable Terraform output handoff. Restore the protected "
                        "recovery bundle before retrying."
                    )
            if remote_state is None and any(path.is_file() for path in (state_path, *local_sidecars)):
                raise ReleaseStateUnverifiable(
                    f"Existing release state conflict for {config.release_id!r}: "
                    f"{UNVERIFIABLE_STATE_PREFIX}: local recovery evidence exists but the "
                    "release-scoped durable Terraform state is absent. The evidence is preserved "
                    "and must be inspected before a new apply."
                )

        # Validate immutable release identity against existing state BEFORE any write or apply
        identity_errors = validate_immutable_release_identity(config, state_path_root)
        if identity_errors:
            message = f"Existing release state conflict for {config.release_id!r}: {'; '.join(identity_errors)}"
            # Both branches refuse before touching anything, so the caller must not
            # run failure-path cleanup. Unverifiable state is reported separately
            # because it needs a human to inspect the state files.
            if any(err.startswith(UNVERIFIABLE_STATE_PREFIX) for err in identity_errors):
                raise ReleaseStateUnverifiable(message)
            raise ReleaseIdentityConflict(message)

        # Preserve existing authoritative created_at if already provisioned
        created_at = config.created_at or resources[0].created_at
        if tfvars_path.is_file():
            try:
                prev_vars = json.loads(tfvars_path.read_text(encoding="utf-8"))
                previous_created_at = str(prev_vars.get("created_at", "")).strip()
            except Exception as exc:
                # Unreachable while the identity guard above holds, but a raw
                # RuntimeError here would be classified as a live apply failure and
                # trigger a destroy of the existing release.
                raise ReleaseStateUnverifiable(
                    f"Existing release state conflict for {config.release_id!r}: "
                    f"{UNVERIFIABLE_STATE_PREFIX}: tfvars file {tfvars_path.name} could not be read "
                    f"({type(exc).__name__}: {exc})."
                ) from exc
            if previous_created_at:
                created_at = previous_created_at
        elif inventory_path.is_file():
            try:
                inv = json.loads(inventory_path.read_text(encoding="utf-8"))
                if isinstance(inv, list) and inv and isinstance(inv[0], dict):
                    previous_created_at = str(inv[0].get("created_at", "")).strip()
                    if previous_created_at:
                        created_at = previous_created_at
            except Exception:
                pass

        apply_config = dataclasses.replace(config, created_at=created_at)
        authoritative_created_dt = parse_timestamp(created_at)
        authoritative_labels = generate_staging_labels(
            release_id=apply_config.release_id,
            candidate_sha=apply_config.candidate_sha,
            manifest_digest=apply_config.manifest_digest,
            owner_task_id=apply_config.owner_task_id,
            tenant_id=apply_config.tenant_id,
            ttl_hours=apply_config.ttl_hours,
            created_at=authoritative_created_dt,
            additional_labels=apply_config.additional_labels,
        )
        tfvars_path.write_text(
            json.dumps(generate_tfvars(apply_config), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        inventory_path.write_text(
            json.dumps(
                [
                    {
                        "type": resource.resource_type,
                        "name": resource.resource_name,
                        "id": resource.resource_id,
                        "release_id": resource.release_id,
                        "raw_release_id": apply_config.release_id,
                        "labels": dict(authoritative_labels),
                        "created_at": created_at,
                        "expires_at": format_timestamp(
                            authoritative_created_dt + timedelta(hours=apply_config.ttl_hours)
                        ),
                    }
                    for resource in resources
                ],
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        _write_lifecycle_state(
            apply_config.release_id,
            state_path_root,
            status="creating",
            identity=generate_tfvars(apply_config),
        )

        if initialize and not initialized:
            _run_terraform(
                module_dir=module_path,
                terraform_bin=terraform_bin,
                arguments=["init", "-input=false", "-upgrade=false", *backend_args],
            )
        apply_arguments = [
            "apply",
            "-input=false",
            "-auto-approve",
            f"-var-file={tfvars_path}",
        ]
        if not remote_backend:
            apply_arguments.insert(3, f"-state={state_path}")
        _run_terraform(
            module_dir=module_path,
            terraform_bin=terraform_bin,
            arguments=apply_arguments,
        )
        outputs: dict[str, Any] = {}
        if outputs_path is not None:
            outputs = _terraform_output_values(
                module_dir=module_path,
                terraform_bin=terraform_bin,
                state_path=None if remote_backend else state_path,
            )
            output_errors = validate_staging_outputs(
                outputs,
                release_id=apply_config.release_id,
                candidate_sha=apply_config.candidate_sha,
                manifest_digest=apply_config.manifest_digest,
                project_id=apply_config.project_id,
                worker_image=apply_config.worker_image,
                scheduler_image=apply_config.scheduler_image,
            )
            if output_errors:
                raise RuntimeError("Invalid Terraform staging output handoff: " + "; ".join(output_errors))
            output_path = outputs_path.expanduser().resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(outputs, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        _write_lifecycle_state(
            apply_config.release_id,
            state_path_root,
            status="created",
            identity=generate_tfvars(apply_config),
            outputs=outputs or None,
        )
        return {"success": True, "outputs": outputs}

    return execute


def make_terraform_deletion_executor(
    release_id: str,
    *,
    module_dir: Path = DEFAULT_EPHEMERAL_MODULE_DIR,
    state_dir: Path = DEFAULT_EPHEMERAL_STATE_DIR,
    terraform_bin: str = "terraform",
    initialize: bool = True,
    backend_bucket: str = "",
    backend_prefix: str = "",
) -> Callable[[Mapping[str, Any]], bool]:
    """Build a release-scoped live destroy executor.

    The first exact-label match performs one destroy against that release's
    state. Later matches are acknowledged because the same Terraform destroy
    removed the complete release graph. No project-wide destroy is possible.
    """
    module_path = module_dir.expanduser().resolve()
    state_path_root = state_dir.expanduser().resolve()
    state_path, tfvars_path, inventory_path = _terraform_state_paths(release_id, state_path_root)
    backend_args = _terraform_backend_arguments(
        backend_bucket=backend_bucket,
        backend_prefix=backend_prefix,
    ) if backend_bucket or backend_prefix else []
    remote_backend = bool(backend_args)
    attempted = False
    destroy_success = False

    def execute(_resource: Mapping[str, Any]) -> bool:
        nonlocal attempted, destroy_success
        if attempted:
            return destroy_success
        attempted = True

        if not module_path.is_dir():
            raise RuntimeError(f"Terraform module directory does not exist: {module_path}")
        if remote_backend:
            _validate_backend_prefix_for_release(backend_prefix, release_id)
        if not state_path.is_file() or not tfvars_path.is_file():
            if remote_backend and tfvars_path.is_file():
                pass
            else:
                raise RuntimeError(
                    f"No release-scoped Terraform state for cleanup of {release_id!r}: {state_path}"
                )
        if initialize:
            _run_terraform(
                module_dir=module_path,
                terraform_bin=terraform_bin,
                arguments=["init", "-input=false", "-upgrade=false", *backend_args],
            )
        if remote_backend:
            # Do not let `destroy` turn an unavailable/empty backend into a
            # successful no-op. The release state must already be present.
            _terraform_state_pull(module_dir=module_path, terraform_bin=terraform_bin)
        destroy_arguments = [
            "destroy",
            "-input=false",
            "-auto-approve",
            f"-var-file={tfvars_path}",
        ]
        if not remote_backend:
            destroy_arguments.insert(3, f"-state={state_path}")
        _run_terraform(
            module_dir=module_path,
            terraform_bin=terraform_bin,
            arguments=destroy_arguments,
        )
        destroy_success = True
        # The live resources are gone; remove the local release receipt inputs
        # so a later cleanup cannot mistake stale inventory for live resources.
        for path in (
            state_path,
            tfvars_path,
            inventory_path,
            _lifecycle_state_path(release_id, state_path_root),
        ):
            path.unlink(missing_ok=True)
        return True

    return execute


def create_ephemeral_staging(
    config: StagingConfig,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
    creation_executor: Callable[
        [StagingConfig, Sequence[StagingResource]],
        bool | Sequence[dict[str, Any]] | Mapping[str, Any],
    ] | None = None,
    cleanup_executor: Callable[[Mapping[str, Any]], bool] | None = None,
) -> StagingLifecycleReceipt:
    """Create or plan an ephemeral staging environment instance.

    If creation execution fails on a live deployment, failure-path cleanup is
    immediately triggered to avoid leaving partial dangling resources, per
    ODP-DEPLOY-EPHEMERAL-STAGING-PROD-ROLLOUT-PLAN §15.
    """
    now_dt = now or datetime.now(UTC)
    errors = validate_staging_config(config, now=now_dt)
    if errors:
        return StagingLifecycleReceipt(
            action="create",
            release_id=config.release_id,
            candidate_sha=config.candidate_sha,
            manifest_digest_prefix=config.manifest_digest.replace("sha256:", "")[:16],
            success=False,
            timestamp=format_timestamp(now_dt),
            resources=[],
            errors=errors,
            remediation_required=False,
            metadata={"dry_run": dry_run},
        )

    creation_dt = parse_timestamp(config.created_at) if config.created_at else now_dt
    planned = plan_staging_resources(config, created_at=creation_dt, now=now_dt)
    planned_dicts = [
        {
            "type": r.resource_type,
            "name": r.resource_name,
            "id": r.resource_id,
            "release_id": r.release_id,
            "raw_release_id": r.release_id,
            "created_at": r.created_at,
            "expires_at": r.expires_at,
            "labels": dict(r.labels),
        }
        for r in planned
    ]

    if dry_run:
        resource_dicts = [
            {
                "type": r.resource_type,
                "name": r.resource_name,
                "id": r.resource_id,
                "status": "planned",
                "created_at": r.created_at,
                "expires_at": r.expires_at,
                "labels": dict(r.labels),
            }
            for r in planned
        ]
        return StagingLifecycleReceipt(
            action="create",
            release_id=config.release_id,
            candidate_sha=config.candidate_sha,
            manifest_digest_prefix=config.manifest_digest.replace("sha256:", "")[:16],
            success=True,
            timestamp=format_timestamp(now_dt),
            resources=resource_dicts,
            errors=[],
            remediation_required=False,
            metadata={
                "dry_run": True,
                "ttl_hours": config.ttl_hours,
                "project_id": config.project_id,
                "region": config.region,
                "scheduler_paused": True,
            },
        )

    # Non-dry-run without an executor cannot claim live provisioning
    if creation_executor is None:
        return StagingLifecycleReceipt(
            action="create",
            release_id=config.release_id,
            candidate_sha=config.candidate_sha,
            manifest_digest_prefix=config.manifest_digest.replace("sha256:", "")[:16],
            success=False,
            timestamp=format_timestamp(now_dt),
            resources=[
                {
                    "type": r.resource_type,
                    "name": r.resource_name,
                    "id": r.resource_id,
                    "status": "not_provisioned",
                    "created_at": r.created_at,
                    "expires_at": r.expires_at,
                    "labels": dict(r.labels),
                }
                for r in planned
            ],
            errors=["Non-dry-run creation requires a creation_executor or live provisioning backend."],
            remediation_required=True,
            remediation_notes="No creation executor was supplied to perform live resource provisioning.",
            metadata={"dry_run": False},
        )

    exec_success = True
    execution_metadata: dict[str, Any] = {}
    is_conflict = False
    state_unverifiable = False
    try:
        exec_res = creation_executor(config, planned)
        if isinstance(exec_res, Mapping):
            exec_success = bool(exec_res.get("success", True))
            if isinstance(exec_res.get("outputs"), Mapping):
                execution_metadata["lifecycle_outputs"] = dict(exec_res["outputs"])
        elif isinstance(exec_res, Sequence) and not isinstance(exec_res, (str, bytes, bytearray)):
            exec_success = bool(exec_res)
        else:
            exec_success = bool(exec_res)
    except Exception as exc:
        exec_success = False
        err_msg = str(exc)
        errors.append(f"Creation executor failed: {err_msg}")
        # A conflict means the executor refused before mutating anything, so the
        # resources named in the plan may still be a live release. Cleaning them
        # up would destroy exactly what the guard protected.
        if isinstance(exc, ReleaseIdentityConflict):
            is_conflict = True
            state_unverifiable = isinstance(exc, ReleaseStateUnverifiable)
        elif "Existing release state conflict" in err_msg or "Immutable release identity" in err_msg:
            is_conflict = True
            state_unverifiable = UNVERIFIABLE_STATE_PREFIX in err_msg

    cleanup_receipt: dict[str, Any] | None = None
    if not exec_success and not is_conflict:
        # Failure path: trigger exact cleanup per rollout plan §15
        if cleanup_executor is not None:
            try:
                c_receipt = cleanup_ephemeral_staging(
                    release_id=config.release_id,
                    project_id=config.project_id,
                    resource_inventory=planned_dicts,
                    dry_run=False,
                    now=now_dt,
                    deletion_executor=cleanup_executor,
                    allow_empty=True,
                )
                cleanup_receipt = c_receipt.to_dict()
                if not c_receipt.success:
                    errors.extend([f"Failure-path cleanup error: {e}" for e in c_receipt.errors])
            except Exception as clean_exc:
                errors.append(f"Failure-path cleanup failed: {clean_exc}")
        else:
            errors.append("No cleanup executor available for failure-path cleanup.")

    resource_dicts = [
        {
            "type": r.resource_type,
            "name": r.resource_name,
            "id": r.resource_id,
            "status": "provisioned" if exec_success else "failed",
            "created_at": r.created_at,
            "expires_at": r.expires_at,
            "labels": dict(r.labels),
        }
        for r in planned
    ]

    # An identity conflict is a clean refusal and needs no remediation, but
    # unreadable state does: an operator has to inspect the preserved files.
    remediation_required = not exec_success and (not is_conflict or state_unverifiable)
    remediation_notes = ""
    if not exec_success:
        if state_unverifiable:
            remediation_notes = (
                "Creation rejected because existing release state could not be read or parsed; "
                "the existing release was preserved untouched and requires manual inspection "
                "before any rerun or cleanup."
            )
        elif is_conflict:
            remediation_notes = (
                "Creation rejected due to immutable release identity conflict; "
                "existing release state was preserved and not modified."
            )
        elif cleanup_receipt and cleanup_receipt.get("success"):
            remediation_notes = "Creation failed; failure-path cleanup succeeded in deleting partial staging resources."
        else:
            remediation_notes = "Creation failed and partial staging resources may require manual remediation."

    metadata = {
        "dry_run": False,
        "ttl_hours": config.ttl_hours,
        "project_id": config.project_id,
        "region": config.region,
        "scheduler_paused": True,
    }
    metadata.update(execution_metadata)
    if cleanup_receipt is not None:
        metadata["failure_cleanup_receipt"] = cleanup_receipt
    if is_conflict:
        metadata["existing_release_state_preserved"] = True
    if state_unverifiable:
        metadata["release_state_unverifiable"] = True

    return StagingLifecycleReceipt(
        action="create",
        release_id=config.release_id,
        candidate_sha=config.candidate_sha,
        manifest_digest_prefix=config.manifest_digest.replace("sha256:", "")[:16],
        success=exec_success,
        timestamp=format_timestamp(now_dt),
        resources=resource_dicts,
        errors=errors,
        remediation_required=remediation_required,
        remediation_notes=remediation_notes,
        metadata=metadata,
    )


def is_staging_ephemeral_resource(
    labels: Mapping[str, str],
    target_release_id: str,
    *,
    require_full_ownership: bool = True,
) -> bool:
    """Strictly verify if resource labels match the target ephemeral staging release.

    Requires full ownership labels (managed_by=terraform, app=oday-plus, environment=staging,
    ephemeral=true, release_id, candidate_sha, manifest_digest_prefix, owner_task).
    """
    target_clean = str(target_release_id).strip()
    if not target_clean or not RELEASE_ID_PATTERN.fullmatch(target_clean):
        return False

    actual_label = str(labels.get("release_id", "")).strip()
    if not actual_label:
        return False

    target_label = release_label_value(target_clean)
    # The label contains a hash of the raw id and is the only canonical
    # release identity accepted here.  Accepting the raw value as a label (or
    # treating a bounded label as a raw id) makes punctuation/case variants
    # indistinguishable and can delete the wrong release.
    if actual_label != target_label:
        return False

    if (
        labels.get("app") != "oday-plus"
        or labels.get("environment") != "staging"
        or labels.get("managed_by") != "terraform"
        or labels.get("ephemeral") != "true"
    ):
        return False

    if require_full_ownership:
        sha = str(labels.get("candidate_sha", "")).strip().lower()
        if not CANDIDATE_SHA_PATTERN.fullmatch(sha):
            return False
        digest_prefix = str(labels.get("manifest_digest_prefix", "")).strip().lower()
        if len(digest_prefix) != 16 or not re.fullmatch(r"^[0-9a-f]{16}$", digest_prefix):
            return False
        owner_task = str(labels.get("owner_task", "")).strip()
        if not owner_task:
            return False

        # A release without both immutable time labels is not safe to delete:
        # it cannot be proven to be within the staging TTL contract.
        created_str = str(labels.get("created_at", "")).strip()
        expires_str = str(labels.get("expires_at", "")).strip()
        if not created_str or not expires_str:
            return False
        try:
            created_at = parse_timestamp(created_str)
            expires_at = parse_timestamp(expires_str)
        except ValueError:
            return False
        if expires_at < created_at:
            return False

    return True


def cleanup_ephemeral_staging(
    release_id: str,
    *,
    project_id: str,
    resource_inventory: Sequence[Mapping[str, Any]] | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
    deletion_executor: Callable[[Mapping[str, Any]], bool] | None = None,
    allow_empty: bool = False,
) -> StagingLifecycleReceipt:
    """Destroy ephemeral staging resources strictly by exact label matching.

    Guarantees:
    - Never uses wildcards or project-wide deletions.
    - Explicitly verifies `ephemeral=true`, `managed_by=terraform`, `environment=staging`,
      `app=oday-plus`, and matching `release_id` and ownership labels.
    - Protected resources (prod/dev/shared infra) are never touched.
    - Empty inventory or zero matching resources is always rejected as non-success;
      ``allow_empty`` is retained only for compatibility and cannot bypass this gate.
    - Non-dry-run cleanup requires a deletion_executor.
    - If any deletion fails, a remediation task is marked.
    """
    now_dt = now or datetime.now(UTC)
    target_suffix = sanitize_release_suffix(release_id)

    # Reject empty, wildcard, or broad environment strings
    is_unsafe = (
        not target_suffix
        or target_suffix in ("all", "prod", "production", "dev", "staging")
        or any(char in release_id for char in ("*", "?", "[", "]", " ", "%", "/", "\\"))
        or not RELEASE_ID_PATTERN.fullmatch(release_id)
    )

    if is_unsafe:
        return StagingLifecycleReceipt(
            action="cleanup",
            release_id=release_id,
            candidate_sha="",
            manifest_digest_prefix="",
            success=False,
            timestamp=format_timestamp(now_dt),
            resources=[],
            errors=[f"Unsafe or invalid release_id for cleanup: {release_id!r}"],
            remediation_required=True,
            remediation_notes="Cleanup was aborted because release_id is unsafe or broad.",
        )

    inventory = resource_inventory if resource_inventory is not None else []
    matching_resources: list[Mapping[str, Any]] = []
    # Track all same-release inventory entries, including ones that fail
    # ownership validation. The CLI deletion executor is release-scoped: a
    # single terraform destroy removes the entire release graph. If any
    # same-release row has invalid or incomplete labels, the deletion is
    # refused because those rows cannot be proven to belong to the same
    # release and may be a different release's resources.
    same_release_all: list[Mapping[str, Any]] = []
    same_release_invalid: list[str] = []

    target_label = release_label_value(release_id)

    for res in inventory:
        labels = res.get("labels", {})
        if not isinstance(labels, Mapping):
            # A resource with non-mapping labels in the inventory is
            # unverifiable. If it carries no release signal at all, it is
            # irrelevant to this release's cleanup. But we cannot determine
            # that without readable labels, so conservatively flag it if it
            # shares the same inventory source.
            same_release_invalid.append(
                f"Resource {res.get('id', res.get('name', 'unknown'))!r} has non-mapping labels; "
                "its release identity cannot be verified"
            )
            continue

        # Check if this resource belongs to the same release (by label value)
        actual_label = str(labels.get("release_id", "")).strip()
        if actual_label == target_label:
            same_release_all.append(res)

        # Strictly check full label match for deletion eligibility
        if is_staging_ephemeral_resource(labels, release_id):
            matching_resources.append(res)

    # Fail closed: if the inventory contains same-release rows with invalid
    # or incomplete ownership labels, a release-scoped deletion would destroy
    # resources that could not be verified. This protects against the scenario
    # where the authoritative inventory is partial or has been corrupted.
    if same_release_invalid:
        return StagingLifecycleReceipt(
            action="cleanup",
            release_id=release_id,
            candidate_sha="",
            manifest_digest_prefix="",
            success=False,
            timestamp=format_timestamp(now_dt),
            resources=[],
            errors=[
                f"Inventory contains resources with unreadable labels; release-scoped cleanup "
                f"for {release_id!r} is refused to prevent destroying unverified siblings: "
                + "; ".join(same_release_invalid)
            ],
            remediation_required=True,
            remediation_notes=(
                "One or more inventory resources have non-mapping labels. The release-scoped "
                "deletion executor would destroy the entire release graph including unverifiable "
                "resources. Manual inspection of the inventory is required."
            ),
            metadata={
                "dry_run": dry_run,
                "matched_count": len(matching_resources),
                "invalid_count": len(same_release_invalid),
            },
        )

    # Fail closed: if the inventory has same-release rows that did NOT pass
    # full ownership validation (i.e. they share the release label but failed
    # is_staging_ephemeral_resource), the release-scoped destroy would take
    # them out alongside the verified ones.
    unverified_siblings = [
        r for r in same_release_all if r not in matching_resources
    ]
    if unverified_siblings:
        unverified_ids = [
            str(r.get("id") or r.get("name", "unknown")) for r in unverified_siblings
        ]
        return StagingLifecycleReceipt(
            action="cleanup",
            release_id=release_id,
            candidate_sha="",
            manifest_digest_prefix="",
            success=False,
            timestamp=format_timestamp(now_dt),
            resources=[],
            errors=[
                f"Release {release_id!r} has {len(unverified_siblings)} same-release resource(s) "
                f"with incomplete or invalid ownership labels: {', '.join(unverified_ids)}. "
                "Release-scoped cleanup is refused because the deletion executor would destroy "
                "the entire release graph including these unverified siblings."
            ],
            remediation_required=True,
            remediation_notes=(
                "Some resources share the release label but fail full ownership validation. "
                "The release-scoped deletion executor cannot safely target only verified resources. "
                "Repair the inventory labels or remove the unverified resources before retrying."
            ),
            metadata={
                "dry_run": dry_run,
                "matched_count": len(matching_resources),
                "unverified_sibling_count": len(unverified_siblings),
                "unverified_sibling_ids": unverified_ids,
            },
        )

    if not matching_resources:
        inventory_state = "empty inventory" if not inventory else "no exact ownership-label match"
        override_note = (
            " The allow_empty option cannot authorize a cleanup without verified resources."
            if allow_empty
            else ""
        )
        return StagingLifecycleReceipt(
            action="cleanup",
            release_id=release_id,
            candidate_sha="",
            manifest_digest_prefix="",
            success=False,
            timestamp=format_timestamp(now_dt),
            resources=[],
            errors=[
                f"No matching ephemeral staging resources found for release {release_id!r} in {inventory_state}."
                f"{override_note}"
            ],
            remediation_required=True,
            remediation_notes=(
                "Cleanup cannot report success without a verified inventory match; "
                "missing inventory requires an inventory/remediation check."
            ),
            metadata={
                "dry_run": dry_run,
                "matched_count": 0,
                "inventory_count": len(inventory),
                "allow_empty_requested": allow_empty,
            },
        )

    if not dry_run and deletion_executor is None and matching_resources:
        return StagingLifecycleReceipt(
            action="cleanup",
            release_id=release_id,
            candidate_sha="",
            manifest_digest_prefix="",
            success=False,
            timestamp=format_timestamp(now_dt),
            resources=[
                {
                    "id": str(res.get("id") or res.get("name", "unknown")),
                    "type": str(res.get("type", "unknown")),
                    "status": "not_deleted",
                    "labels": dict(res.get("labels", {})),
                }
                for res in matching_resources
            ],
            errors=["Non-dry-run cleanup requires a deletion_executor to perform real resource deletion."],
            remediation_required=True,
            remediation_notes="No deletion executor provided for live cleanup.",
            metadata={"dry_run": False, "matched_count": len(matching_resources)},
        )

    deleted: list[dict[str, Any]] = []
    errors: list[str] = []

    for res in matching_resources:
        res_id = str(res.get("id") or res.get("name", "unknown"))
        res_type = str(res.get("type", "unknown"))

        if dry_run:
            deleted.append({
                "id": res_id,
                "type": res_type,
                "status": "planned_deletion",
                "labels": dict(res.get("labels", {})),
            })
            continue

        success = True
        if deletion_executor is not None:
            try:
                success = deletion_executor(res)
            except Exception as exc:
                success = False
                errors.append(f"Failed to delete {res_type} {res_id}: {exc}")

        if success:
            deleted.append({
                "id": res_id,
                "type": res_type,
                "status": "deleted",
                "labels": dict(res.get("labels", {})),
            })
        else:
            if not any(res_id in err for err in errors):
                errors.append(f"Failed to delete {res_type} {res_id}")

    remediation_required = len(errors) > 0
    remediation_notes = (
        f"Remediation required for {len(errors)} resources that failed cleanup."
        if remediation_required
        else ""
    )

    return StagingLifecycleReceipt(
        action="cleanup",
        release_id=release_id,
        candidate_sha="",
        manifest_digest_prefix="",
        success=len(errors) == 0,
        timestamp=format_timestamp(now_dt),
        resources=deleted,
        errors=errors,
        remediation_required=remediation_required,
        remediation_notes=remediation_notes,
        metadata={"dry_run": dry_run, "matched_count": len(matching_resources)},
    )


REHEARSAL_STAGE_NAMES: tuple[str, ...] = (
    "db_expand_migration",
    "data_platform_snapshot",
    "api_web_authenticated_smoke",
    "worker_idempotency",
    "scheduler_oneshot",
    "backup_restore_drill",
    "rollback_rehearsal",
    "external_providers_disabled_readback",
)


def _live_gcloud(
    args: Sequence[str],
    *,
    gcloud_bin: str = "gcloud",
    timeout: float = 900.0,
    capture_output: bool = False,
) -> str:
    """Run one live gcloud operation without putting its output in receipts."""

    process = subprocess.run(
        [gcloud_bin, *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=os.environ.copy(),
    )
    if process.returncode != 0:
        raise RuntimeError(
            f"gcloud {' '.join(str(arg) for arg in args[:4])} failed "
            f"with exit status {process.returncode}"
        )
    return process.stdout.strip() if capture_output else ""


def _live_cloud_run_field(
    payload: Mapping[str, Any],
    paths: Sequence[Sequence[str | int]],
    *,
    field_name: str,
) -> str:
    """Read one Cloud Run field across the supported gcloud JSON dialects."""

    for path in paths:
        value: Any = payload
        for component in path:
            if isinstance(component, int):
                if not isinstance(value, list) or component >= len(value):
                    value = None
                    break
                value = value[component]
            else:
                if not isinstance(value, Mapping) or component not in value:
                    value = None
                    break
                value = value[component]
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise RuntimeError(f"Cloud Run {field_name} readback is missing from the live resource")


def _live_cloud_run_description(
    resource_kind: str,
    resource_name: str,
    *,
    project_id: str,
    region: str,
    gcloud_bin: str,
) -> Mapping[str, Any]:
    """Fetch a Cloud Run service/job description without exposing its env block."""

    collection = "services" if resource_kind == "service" else "jobs"
    raw = _live_gcloud(
        [
            "run",
            collection,
            "describe",
            resource_name,
            f"--region={region}",
            f"--project={project_id}",
            "--format=json",
        ],
        gcloud_bin=gcloud_bin,
        capture_output=True,
    )
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Cloud Run {resource_kind} description is not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise RuntimeError(f"Cloud Run {resource_kind} description is not a JSON object")
    return payload


def _live_identity_token(
    *,
    audience: str,
    operator_identity: str,
    gcloud_bin: str = "gcloud",
) -> str:
    """Mint a short-lived token only for the verified release-scoped identity."""

    token = _live_gcloud(
        [
            "auth",
            "print-identity-token",
            f"--impersonate-service-account={operator_identity}",
            f"--audiences={audience}",
            "--include-email",
        ],
        gcloud_bin=gcloud_bin,
        timeout=60,
        capture_output=True,
    )
    if not token:
        raise RuntimeError("release-scoped identity token minting returned empty output")
    return token


def _live_json_get(url: str, token: str, *, timeout: float = 30.0) -> Mapping[str, Any]:
    """Read a release-scoped endpoint with a bearer token, without logging body data."""

    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - Terraform output URL
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError(f"release-scoped endpoint readback failed for {url}: {type(exc).__name__}") from exc
    if not isinstance(payload, Mapping):
        raise RuntimeError(f"release-scoped endpoint returned a non-object payload: {url}")
    return payload


def _live_health_is_valid(payload: Mapping[str, Any], *, providers_disabled: bool = False) -> bool:
    """Require explicit health/readback values instead of treating HTTP 200 as proof."""

    status = str(payload.get("status") or "").strip().lower()
    valid = status in {"ok", "healthy", "ready", "pass", "passed"}
    if not valid:
        return False
    if not providers_disabled:
        return True

    serialized = json.dumps(payload, sort_keys=True).lower()
    provider_markers = ("external_providers", "external-providers", "providers")
    if not any(marker in serialized for marker in provider_markers):
        return False
    return not any(marker in serialized for marker in ("\"mode\": \"live\"", "\"enabled\": true"))


def _load_live_state_for_release(
    release_id: str,
    state_dir: Path,
    *,
    require_statuses: frozenset[str] = frozenset({"created", "verification_failed", "held"}),
    remote_state_verified: bool = False,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str]]:
    """Load and prove the release state before verification or retention."""

    state_path, tfvars_path, inventory_path = _terraform_state_paths(release_id, state_dir)
    marker_path = _lifecycle_state_path(release_id, state_dir)
    errors: list[str] = []
    required_files = (
        ("Terraform state", state_path),
        ("Terraform tfvars", tfvars_path),
        ("release ownership inventory", inventory_path),
        ("lifecycle state", marker_path),
    )
    for label, path in required_files:
        if remote_state_verified and label == "Terraform state":
            continue
        if not path.is_file():
            errors.append(f"{label} for release {release_id!r} is missing: {path.name}")

    marker = _read_json_object(marker_path) if marker_path.is_file() else None
    if marker is None and marker_path.is_file():
        errors.append(f"lifecycle state for release {release_id!r} is unreadable")
    elif marker is not None and marker.get("status") not in require_statuses:
        errors.append(
            f"release {release_id!r} has lifecycle status {marker.get('status')!r}; "
            f"expected one of {sorted(require_statuses)}"
        )

    tfvars = _read_json_object(tfvars_path) if tfvars_path.is_file() else None
    if tfvars is None and tfvars_path.is_file():
        errors.append(f"Terraform tfvars for release {release_id!r} is unreadable")
    elif tfvars is not None:
        if str(tfvars.get("release_id", "")).strip() != release_id:
            errors.append("Terraform tfvars release_id does not match the requested release")

    inventory: list[dict[str, Any]] = []
    if inventory_path.is_file():
        try:
            raw_inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            raw_inventory = None
        if not isinstance(raw_inventory, list) or not raw_inventory:
            errors.append("release ownership inventory is not a non-empty JSON array")
        else:
            inventory = [row for row in raw_inventory if isinstance(row, dict)]
            if len(inventory) != len(raw_inventory):
                errors.append("release ownership inventory contains an unreadable resource row")
            for row in inventory:
                labels = row.get("labels")
                if (
                    row.get("raw_release_id") != release_id
                    or not isinstance(labels, Mapping)
                    or not is_staging_ephemeral_resource(labels, release_id)
                ):
                    errors.append(
                        "release ownership inventory contains a resource that cannot prove "
                        "full release-scoped staging ownership"
                    )
                    break
    return marker, inventory, errors


def make_live_rehearsal_executor(
    outputs: Mapping[str, Any],
    *,
    project_id: str,
    region: str,
    operator_identity: str,
    cloud_sql_instance: str = "",
    gcloud_bin: str = "gcloud",
) -> Callable[[str, Mapping[str, Any]], Mapping[str, Any]]:
    """Build the only non-dry-run rehearsal executor.

    It consumes Terraform outputs exclusively. Job execution, endpoint
    readback, SQL export/import, and traffic rollback probes are all performed
    here so a verify receipt cannot be produced by the old no-op stage loop.
    """

    labels = outputs.get("resource_labels")
    if not isinstance(labels, Mapping):
        raise ValueError("Cannot create live rehearsal executor: Terraform resource_labels is unreadable")
    output_errors = validate_staging_outputs(
        outputs,
        release_id=str(outputs.get("release_id", "")),
        candidate_sha=str(labels.get("candidate_sha", "")),
        manifest_digest="sha256:" + str(labels.get("manifest_digest_prefix", "")) + "0" * 48,
        project_id=project_id,
        worker_image=str(outputs.get("staging_worker_image", "")),
        scheduler_image=str(outputs.get("staging_scheduler_image", "")),
    )
    if output_errors:
        raise ValueError("Cannot create live rehearsal executor: " + "; ".join(output_errors))

    api_uri = str(outputs["staging_api_uri"]).rstrip("/")
    web_uri = str(outputs["staging_web_uri"]).rstrip("/")
    runtime_sa = str(outputs["staging_runtime_service_account"]).strip()
    if operator_identity.strip() != runtime_sa:
        raise ValueError(
            "staging rehearsal operator must exactly equal Terraform staging_runtime_service_account"
        )
    token_cache: dict[str, str] = {}
    identities_checked = False

    def ensure_release_identities_exist() -> None:
        nonlocal identities_checked
        if identities_checked:
            return
        for output_name in (
            "staging_runtime_service_account",
            "staging_web_service_account",
            "staging_worker_service_account",
        ):
            expected_identity = str(outputs[output_name]).strip()
            actual_identity = _live_gcloud(
                [
                    "iam",
                    "service-accounts",
                    "describe",
                    expected_identity,
                    f"--project={project_id}",
                    "--format=value(email)",
                ],
                gcloud_bin=gcloud_bin,
                capture_output=True,
            )
            if actual_identity != expected_identity:
                raise RuntimeError(
                    f"{output_name} existence readback does not match its immutable Terraform output"
                )
        identities_checked = True

    def token_for(audience: str) -> str:
        if audience not in token_cache:
            token_cache[audience] = _live_identity_token(
                audience=audience,
                operator_identity=operator_identity,
                gcloud_bin=gcloud_bin,
            )
        return token_cache[audience]

    def execute_service(output_name: str, image_output_name: str, kind: str) -> Mapping[str, Any]:
        service_name = str(outputs[output_name]).strip()
        expected_image = str(outputs[image_output_name]).strip()
        payload = _live_cloud_run_description(
            "service",
            service_name,
            project_id=project_id,
            region=region,
            gcloud_bin=gcloud_bin,
        )
        actual_image = _live_cloud_run_field(
            payload,
            (
                ("spec", "template", "containers", 0, "image"),
                ("spec", "template", "spec", "containers", 0, "image"),
                ("template", "containers", 0, "image"),
                ("template", "template", "containers", 0, "image"),
            ),
            field_name=f"{kind} service image",
        )
        if actual_image != expected_image:
            raise RuntimeError(
                f"{kind} service image readback does not match its immutable Terraform output"
            )
        actual_egress = _live_cloud_run_field(
            payload,
            (
                ("spec", "template", "vpcAccess", "egress"),
                ("spec", "template", "spec", "vpcAccess", "egress"),
                ("template", "vpcAccess", "egress"),
                ("template", "template", "vpcAccess", "egress"),
            ),
            field_name=f"{kind} service VPC egress",
        )
        if actual_egress != "PRIVATE_RANGES_ONLY":
            raise RuntimeError(
                f"{kind} service live VPC egress is {actual_egress!r}; expected PRIVATE_RANGES_ONLY"
            )
        return {
            "service": service_name,
            "image_digest": expected_image,
            "egress": actual_egress,
            "status": "verified",
        }

    def execute_job(output_name: str, image_output_name: str, kind: str) -> Mapping[str, Any]:
        job_name = str(outputs[output_name]).strip()
        expected_image = str(outputs[image_output_name]).strip()
        payload = _live_cloud_run_description(
            "job",
            job_name,
            project_id=project_id,
            region=region,
            gcloud_bin=gcloud_bin,
        )
        actual_image = _live_cloud_run_field(
            payload,
            (
                ("template", "template", "containers", 0, "image"),
                ("spec", "template", "spec", "template", "spec", "containers", 0, "image"),
            ),
            field_name=f"{kind} job image",
        )
        if actual_image != expected_image:
            raise RuntimeError(
                f"{kind} job image readback does not match its immutable Terraform output"
            )
        actual_egress = _live_cloud_run_field(
            payload,
            (
                ("template", "template", "vpcAccess", "egress"),
                ("spec", "template", "spec", "template", "spec", "vpcAccess", "egress"),
            ),
            field_name=f"{kind} job VPC egress",
        )
        if actual_egress != "PRIVATE_RANGES_ONLY":
            raise RuntimeError(
                f"{kind} job live VPC egress is {actual_egress!r}; expected PRIVATE_RANGES_ONLY"
            )
        _live_gcloud(
            [
                "run",
                "jobs",
                "execute",
                job_name,
                f"--region={region}",
                f"--project={project_id}",
                "--wait",
                "--quiet",
            ],
            gcloud_bin=gcloud_bin,
        )
        return {
            "job": job_name,
            "image_digest": expected_image,
            "egress": actual_egress,
            "execution": "succeeded",
        }

    def endpoint(path: str, *, web: bool = False, providers_disabled: bool = False) -> Mapping[str, Any]:
        audience = web_uri if web else api_uri
        payload = _live_json_get(f"{audience}{path}", token_for(audience))
        if not _live_health_is_valid(payload, providers_disabled=providers_disabled):
            raise RuntimeError(f"release-scoped readback failed health contract for {path}")
        if path == "/platform/version" and str(payload.get("release_sha") or "").strip() != str(outputs["resource_labels"]["candidate_sha"]):
            raise RuntimeError("release-scoped version readback does not match candidate_sha")
        return {"endpoint": f"{audience}{path}", "status": "verified"}

    def execute(stage_name: str, _context: Mapping[str, Any]) -> Mapping[str, Any]:
        ensure_release_identities_exist()
        if stage_name == "db_expand_migration":
            return execute_job("staging_migration_job_name", "staging_worker_image", "migration")
        if stage_name == "data_platform_snapshot":
            return endpoint("/platform/health")
        if stage_name == "api_web_authenticated_smoke":
            execute_service("staging_api_service_name", "staging_api_image", "API")
            execute_service("staging_web_service_name", "staging_web_image", "Web")
            endpoint("/platform/health")
            endpoint("/platform/version")
            return endpoint("/operator", web=True)
        if stage_name == "worker_idempotency":
            return execute_job("staging_worker_job_name", "staging_worker_image", "worker")
        if stage_name == "scheduler_oneshot":
            trigger_state = _live_gcloud(
                [
                    "scheduler",
                    "jobs",
                    "describe",
                    str(outputs["staging_scheduler_trigger_name"]).strip(),
                    f"--location={region}",
                    f"--project={project_id}",
                    "--format=value(state)",
                ],
                gcloud_bin=gcloud_bin,
                capture_output=True,
            ).upper()
            if trigger_state != "PAUSED":
                raise RuntimeError(
                    "release-scoped scheduler trigger is not paused during rehearsal"
                )
            return execute_job("staging_scheduler_job_name", "staging_scheduler_image", "scheduler")
        if stage_name == "backup_restore_drill":
            if not cloud_sql_instance:
                raise RuntimeError("backup/restore rehearsal requires the protected Cloud SQL foundation input")
            bucket = str(outputs["staging_data_bucket"]).strip()
            database = str(outputs["staging_database_name"]).strip()
            backup_uri = f"gs://{bucket}/rehearsal/{outputs['release_id']}/database.sql"
            _live_gcloud(
                [
                    "sql",
                    "export",
                    "sql",
                    cloud_sql_instance,
                    backup_uri,
                    f"--database={database}",
                    f"--project={project_id}",
                    "--quiet",
                ],
                gcloud_bin=gcloud_bin,
            )
            _live_gcloud(
                [
                    "sql",
                    "import",
                    "sql",
                    cloud_sql_instance,
                    backup_uri,
                    f"--database={database}",
                    f"--project={project_id}",
                    "--quiet",
                ],
                gcloud_bin=gcloud_bin,
            )
            _live_gcloud(["storage", "rm", backup_uri, "--quiet"], gcloud_bin=gcloud_bin)
            return {"backup_uri": backup_uri, "restore": "succeeded", "secret_values_redacted": True}
        if stage_name == "rollback_rehearsal":
            # Read and restore the exact allocation through Cloud Run's traffic
            # primitive. This is deliberately a real no-op round trip, so a
            # missing service, malformed allocation, or failed readback is a
            # failure instead of a synthetic "rehearsed" result.
            for service_key in ("staging_api_service_name", "staging_web_service_name"):
                service_name = str(outputs[service_key]).strip()
                before_raw = _live_gcloud(
                    [
                        "run",
                        "services",
                        "describe",
                        service_name,
                        f"--region={region}",
                        f"--project={project_id}",
                        "--format=json",
                    ],
                    gcloud_bin=gcloud_bin,
                    capture_output=True,
                )
                try:
                    before_payload = json.loads(before_raw)
                    traffic = before_payload["status"]["traffic"]
                except (KeyError, TypeError, ValueError) as exc:
                    raise RuntimeError(
                        f"{service_name} traffic allocation readback is malformed"
                    ) from exc
                if not isinstance(traffic, list) or not traffic:
                    raise RuntimeError(f"{service_name} has no traffic allocation to restore")
                revisions: dict[str, int] = {}
                for entry in traffic:
                    if not isinstance(entry, Mapping):
                        raise RuntimeError(
                            f"{service_name} traffic allocation contains an unreadable entry"
                        )
                    revision = str(entry.get("revisionName") or entry.get("revision") or "").strip()
                    if not revision:
                        raise RuntimeError(
                            f"{service_name} traffic allocation is not pinned to a revision"
                        )
                    try:
                        percent = int(entry.get("percent"))
                    except (TypeError, ValueError) as exc:
                        raise RuntimeError(f"{service_name} traffic percentage is invalid") from exc
                    if not 0 <= percent <= 100:
                        raise RuntimeError(f"{service_name} traffic percentage is out of range")
                    revisions[revision] = percent
                if sum(revisions.values()) != 100:
                    raise RuntimeError(f"{service_name} traffic allocation does not total 100 percent")
                to_revisions = ",".join(
                    f"{revision}={percent}" for revision, percent in revisions.items()
                )
                _live_gcloud(
                    [
                        "run",
                        "services",
                        "update-traffic",
                        service_name,
                        f"--to-revisions={to_revisions}",
                        f"--region={region}",
                        f"--project={project_id}",
                        "--quiet",
                    ],
                    gcloud_bin=gcloud_bin,
                )
                after_raw = _live_gcloud(
                    [
                        "run",
                        "services",
                        "describe",
                        service_name,
                        f"--region={region}",
                        f"--project={project_id}",
                        "--format=json",
                    ],
                    gcloud_bin=gcloud_bin,
                    capture_output=True,
                )
                try:
                    after_traffic = json.loads(after_raw)["status"]["traffic"]
                    after_revisions = {
                        str(entry.get("revisionName") or entry.get("revision")): int(entry.get("percent"))
                        for entry in after_traffic
                        if isinstance(entry, Mapping)
                    }
                except (KeyError, TypeError, ValueError) as exc:
                    raise RuntimeError(f"{service_name} traffic restore readback is malformed") from exc
                if after_revisions != revisions:
                    raise RuntimeError(
                        f"{service_name} traffic restore readback does not match the saved allocation"
                    )
            return {"traffic_pointer": "restored_and_read_back", "restore": "succeeded"}
        if stage_name == "external_providers_disabled_readback":
            return endpoint("/platform/health", providers_disabled=True)
        raise RuntimeError(f"Unknown staging rehearsal stage: {stage_name}")

    return execute


def verify_ephemeral_staging(
    release_id: str,
    candidate_sha: str,
    manifest_digest: str,
    project_id: str,
    *,
    region: str = "asia-east1",
    worker_image: str = "",
    scheduler_image: str = "",
    state_dir: Path | str = DEFAULT_EPHEMERAL_STATE_DIR,
    dry_run: bool = False,
    stage_executor: Callable[
        [str, Mapping[str, Any]], bool | Mapping[str, Any]
    ] | None = None,
    now: datetime | None = None,
    receipt_path: Path | str | None = None,
    operator_identity: str = "",
    lifecycle_outputs: Mapping[str, Any] | None = None,
    remote_state_verified: bool = False,
) -> StagingLifecycleReceipt:
    """Execute the 8-stage rehearsal verification on ephemeral staging resources.

    Guarantees:
    1. Rehearses DB expand migration, data platform snapshot materialization,
       authenticated API/Web smoke, worker idempotency, scheduler one-shot,
       backup/restore drill, rollback pointer reversal, and external-sources disabled readback.
    2. Enforces release-scoped least-privilege identity; explicitly rejects
       impersonation of dev smoke operator.
    3. Produces secret-free receipts with secret_values_redacted=True.
    4. Third-party sources remain disabled and egress default-deny.
    """
    now_dt = now or datetime.now(UTC)
    errors: list[str] = []

    if not RELEASE_ID_PATTERN.fullmatch(release_id):
        errors.append(f"Invalid release_id format: {release_id!r}")
    if not CANDIDATE_SHA_PATTERN.fullmatch(candidate_sha):
        errors.append(f"Invalid candidate_sha format: {candidate_sha!r}")
    if not SHA256_DIGEST_PATTERN.fullmatch(manifest_digest):
        errors.append(f"Invalid manifest_digest format: {manifest_digest!r}")
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        errors.append(f"Invalid project_id format: {project_id!r}")

    names = get_ephemeral_resource_names(release_id, project_id)
    sa_runtime = f"{names['sa_runtime']}@{project_id}.iam.gserviceaccount.com"
    sa_web = f"{names['sa_web']}@{project_id}.iam.gserviceaccount.com"

    state_errors: list[str] = []
    if not dry_run:
        errors.extend(
            validate_staging_outputs(
                lifecycle_outputs,
                release_id=release_id,
                candidate_sha=candidate_sha,
                manifest_digest=manifest_digest,
                project_id=project_id,
                worker_image=worker_image,
                scheduler_image=scheduler_image,
            )
        )
        if lifecycle_outputs:
            sa_runtime = str(lifecycle_outputs.get("staging_runtime_service_account", "")).strip()
            sa_web = str(lifecycle_outputs.get("staging_web_service_account", "")).strip()
        state_marker, _, state_errors = _load_live_state_for_release(
            release_id,
            Path(state_dir),
            remote_state_verified=remote_state_verified,
        )
        errors.extend(state_errors)
        if state_marker is not None and lifecycle_outputs is not None:
            marker_outputs = state_marker.get("outputs")
            if not isinstance(marker_outputs, Mapping):
                errors.append(
                    "Live lifecycle state has no authoritative Terraform output handoff."
                )
            elif dict(marker_outputs) != dict(lifecycle_outputs):
                errors.append(
                    "Terraform output handoff does not match the output handoff persisted at create time."
                )
        if not operator_identity.strip():
            errors.append(
                "Live staging verification requires the release-scoped operator identity from Terraform outputs."
            )
        elif lifecycle_outputs and operator_identity.strip() != sa_runtime:
            errors.append(
                "Staging smoke operator must exactly match staging_runtime_service_account from Terraform outputs."
            )
        if stage_executor is None:
            errors.append(
                "Non-dry-run staging verification requires the authoritative live stage executor."
            )

    # Identity boundary: staging smoke proof must use release-scoped least-privilege identity,
    # and must not impersonate dev smoke operator.
    if operator_identity:
        if any(dev_token in operator_identity.lower() for dev_token in ("dev-smoke", "dev_smoke", "operator-dev")):
            errors.append(
                f"Staging verification rejected dev smoke operator identity impersonation: {operator_identity!r}. "
                f"Must use release-scoped identity {sa_runtime!r}."
            )

    stages_results: list[dict[str, Any]] = []
    if not errors:
        stage_context = {
            "release_id": release_id,
            "candidate_sha": candidate_sha,
            "manifest_digest": manifest_digest,
            "project_id": project_id,
            "region": region,
            "worker_image": worker_image,
            "scheduler_image": scheduler_image,
            "database_name": names["database_name"],
            "bucket_name": names["bucket_name"],
            "sa_runtime": sa_runtime,
            "sa_web": sa_web,
            "jobs_topic": names["jobs_topic"],
            "scheduler_job": names["scheduler_job"],
            "dry_run": dry_run,
            "lifecycle_outputs": dict(lifecycle_outputs) if lifecycle_outputs else {},
        }

        for stage_name in REHEARSAL_STAGE_NAMES:
            stage_success = True
            stage_detail = f"Stage {stage_name} passed verification for {release_id}"
            stage_proof: dict[str, Any] = {}
            if stage_executor is not None:
                try:
                    stage_result = stage_executor(stage_name, stage_context)
                    if isinstance(stage_result, Mapping):
                        if "success" not in stage_result:
                            stage_success = False
                            stage_detail = (
                                f"Stage {stage_name} returned no explicit authoritative success result."
                            )
                            errors.append(f"Stage {stage_name} returned an incomplete result.")
                        else:
                            stage_success = bool(stage_result["success"])
                        stage_detail = str(stage_result.get("detail") or stage_detail)
                        # Only the live executor's secret-free proof fields may
                        # cross the receipt boundary. Never serialize an
                        # arbitrary callback mapping into a publishable receipt.
                        for key in (
                            "service",
                            "job",
                            "image_digest",
                            "egress",
                            "execution",
                            "endpoint",
                            "status",
                            "backup_uri",
                            "restore",
                            "traffic_pointer",
                        ):
                            if key in stage_result:
                                stage_proof[key] = stage_result[key]
                    else:
                        stage_success = bool(stage_result)
                    if not stage_success:
                        stage_detail = f"Stage {stage_name} failed verification."
                        errors.append(f"Stage {stage_name} failed.")
                except Exception as exc:
                    stage_success = False
                    stage_detail = f"Stage {stage_name} raised exception: {type(exc).__name__}"
                    errors.append(f"Stage {stage_name} error: {type(exc).__name__}: {exc}")

            target_key = (
                "staging_database_name"
                if "db" in stage_name
                else "staging_data_bucket"
                if "snapshot" in stage_name
                else "staging_runtime_service_account"
            )
            output_target = (
                str(lifecycle_outputs.get(target_key, "")).strip()
                if isinstance(lifecycle_outputs, Mapping)
                else ""
            )
            stage_receipt = {
                "stage": stage_name,
                "success": stage_success,
                "status": "passed" if stage_success else "failed",
                "details": stage_detail,
                "target_resource": output_target
                or names.get(
                    "database_name"
                    if "db" in stage_name
                    else "bucket_name"
                    if "snapshot" in stage_name
                    else "sa_runtime"
                ),
            }
            if stage_proof:
                stage_receipt["proof"] = stage_proof
            stages_results.append(stage_receipt)

    success = len(errors) == 0
    receipt = StagingLifecycleReceipt(
        action="verify",
        release_id=release_id,
        candidate_sha=candidate_sha,
        manifest_digest_prefix=manifest_digest.replace("sha256:", "")[:16],
        success=success,
        timestamp=format_timestamp(now_dt),
        resources=stages_results,
        errors=errors,
        remediation_required=not success,
        remediation_notes=(
            "Staging rehearsal verification completed successfully."
            if success
            else f"Rehearsal verification failed with {len(errors)} errors."
        ),
        metadata={
            "dry_run": dry_run,
            "secret_values_redacted": True,
            "external_sources_expected_enabled": [],
            "public_egress": "default_deny",
            "identity_scope": "release_scoped_least_privilege",
            "staging_runtime_sa": sa_runtime,
            "staging_web_sa": sa_web,
            "stages_count": len(stages_results),
            "remote_state_verified": remote_state_verified,
        },
    )

    if receipt_path:
        out_path = Path(receipt_path).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(receipt.to_dict(), indent=2), encoding="utf-8")

    if not dry_run and lifecycle_outputs and not state_errors:
        _write_lifecycle_state(
            release_id,
            Path(state_dir),
            status="verified" if receipt.success else "verification_failed",
            identity={
                "release_id": release_id,
                "project_id": project_id,
                "region": region,
                "candidate_sha": candidate_sha,
                "manifest_digest": manifest_digest,
                "worker_image": worker_image,
                "scheduler_image": scheduler_image,
                "owner_task_id": str(
                    lifecycle_outputs.get("resource_labels", {}).get("owner_task", "")
                    if isinstance(lifecycle_outputs.get("resource_labels"), Mapping)
                    else ""
                ),
            },
            outputs=lifecycle_outputs,
        )

    return receipt


def hold_ephemeral_staging(
    release_id: str,
    project_id: str,
    owner_task_id: str,
    reason: str,
    *,
    ttl_hours: int = DEFAULT_TTL_HOURS,
    state_dir: Path | str = DEFAULT_EPHEMERAL_STATE_DIR,
    created_at: datetime | None = None,
    now: datetime | None = None,
    receipt_path: Path | str | None = None,
    require_live_state: bool = True,
    remote_state_verified: bool = False,
) -> StagingLifecycleReceipt:
    """Record hold / retention of ephemeral staging resources for debugging upon failure.

    Policy:
    - Retains failed staging environments up to TTL (default 24h) for forensic inspection.
    - Requires owner_task_id and documented reason.
    - Outputs secret-free hold receipt.
    """
    now_dt = now or datetime.now(UTC)
    errors: list[str] = []

    if not RELEASE_ID_PATTERN.fullmatch(release_id):
        errors.append(f"Invalid release_id format: {release_id!r}")
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        errors.append(f"Invalid project_id format: {project_id!r}")
    if not owner_task_id or not TASK_ID_PATTERN.fullmatch(owner_task_id):
        errors.append(f"Invalid owner_task_id format: {owner_task_id!r}")
    if not reason or not reason.strip():
        errors.append("Hold requires a non-empty documented reason.")
    if not (1 <= ttl_hours <= MAX_TTL_HOURS):
        errors.append(f"Invalid ttl_hours: {ttl_hours}. Must be between 1 and {MAX_TTL_HOURS}.")

    state_marker: dict[str, Any] | None = None
    inventory: list[dict[str, Any]] = []
    if require_live_state and not errors:
        state_marker, inventory, state_errors = _load_live_state_for_release(
            release_id,
            Path(state_dir),
            require_statuses=frozenset({"created", "verification_failed", "held"}),
            remote_state_verified=remote_state_verified,
        )
        errors.extend(state_errors)

    authoritative_created_at = ""
    _, tfvars_path, _ = _terraform_state_paths(release_id, Path(state_dir))
    if tfvars_path.is_file():
        tfvars = _read_json_object(tfvars_path)
        if tfvars is None:
            errors.append("Cannot read authoritative created_at from the release tfvars sidecar.")
        else:
            authoritative_created_at = str(tfvars.get("created_at", "")).strip()

    if created_at is not None and authoritative_created_at:
        try:
            if parse_timestamp(created_at.isoformat()) != parse_timestamp(authoritative_created_at):
                errors.append("hold created_at does not match the immutable release creation timestamp")
        except ValueError:
            errors.append("hold created_at or authoritative release created_at is invalid")
    elif created_at is None and authoritative_created_at:
        try:
            created_at = parse_timestamp(authoritative_created_at)
        except ValueError:
            errors.append("authoritative release created_at is invalid")

    if require_live_state and not inventory:
        errors.append(
            "hold requires a non-empty verified release ownership inventory; "
            "a create failure or already-cleaned release cannot be claimed as retained"
        )

    created_dt = created_at or now_dt
    expires_dt = created_dt + timedelta(hours=ttl_hours)

    receipt = StagingLifecycleReceipt(
        action="hold",
        release_id=release_id,
        candidate_sha="",
        manifest_digest_prefix="",
        success=len(errors) == 0,
        timestamp=format_timestamp(now_dt),
        resources=(
            [
                {
                    "id": str(row.get("id") or row.get("name", "unknown")),
                    "type": str(row.get("type", "unknown")),
                    "release_id": release_id,
                    "status": "retained_for_debugging",
                    "created_at": str(row.get("created_at") or format_timestamp(created_dt)),
                    "expires_at": str(row.get("expires_at") or format_timestamp(expires_dt)),
                    "ttl_hours": ttl_hours,
                    "labels": dict(row.get("labels", {})),
                }
                for row in inventory
            ]
            if inventory
            else [
                {
                    "release_id": release_id,
                    "owner_task_id": owner_task_id,
                    "status": "not_retained",
                    "created_at": format_timestamp(created_dt),
                    "expires_at": format_timestamp(expires_dt),
                }
            ]
        ),
        errors=errors,
        remediation_required=bool(errors),
        remediation_notes=(
            f"Ephemeral staging retained for debugging until {format_timestamp(expires_dt)}."
            if not errors
            else "Staging retention was refused; inspect the create state before retrying."
        ),
        metadata={
            "secret_values_redacted": True,
            "ttl_policy": "debug_retention",
            "ttl_hours": ttl_hours,
            "owner_task_id": owner_task_id,
            "live_state_required": require_live_state,
            "remote_state_verified": remote_state_verified,
            "state_status": state_marker.get("status") if state_marker else None,
        },
    )

    if receipt.success and require_live_state:
        _write_lifecycle_state(
            release_id,
            Path(state_dir),
            status="held",
            identity={
                "release_id": release_id,
                "owner_task_id": owner_task_id,
                "created_at": format_timestamp(created_dt),
            },
            outputs=(state_marker or {}).get("outputs") if state_marker else None,
        )

    if receipt_path:
        out_path = Path(receipt_path).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(receipt.to_dict(), indent=2), encoding="utf-8")

    return receipt



def _release_group_key(labels: Mapping[str, str], resource_id: str) -> str:
    """Group scanned resources the way a release-scoped destroy would hit them.

    The bounded ``release_id`` label is the only identity every row shares: a
    label-only row and a row carrying ``raw_release_id`` still belong to the same
    Terraform state. A row without the label cannot be grouped at all, so it gets
    a private key and can never be cleaned as part of somebody else's release.
    """
    label_value = str(labels.get("release_id", "")).strip()
    if label_value:
        return label_value
    return f"__unlabeled__:{resource_id}"


def scan_orphans(
    *,
    project_id: str,
    resource_inventory: Sequence[Mapping[str, Any]],
    max_ttl_hours: int = DEFAULT_TTL_HOURS,
    now: datetime | None = None,
    auto_cleanup: bool = False,
    deletion_executor: Callable[[Mapping[str, Any]], bool] | None = None,
) -> OrphanScanResult:
    """Scan inventory for expired ephemeral staging resources and unmanaged orphans."""
    if not (1 <= max_ttl_hours <= MAX_TTL_HOURS):
        raise ValueError(
            f"Invalid max_ttl_hours: {max_ttl_hours}. Must be between 1 and {MAX_TTL_HOURS} hours."
        )

    now_dt = now or datetime.now(UTC)
    scanned_total = len(resource_inventory)

    active_releases: set[str] = set()
    expired_releases: set[str] = set()
    orphan_resources: list[dict[str, Any]] = []
    # Every scanned resource grouped by its bounded release label, including the
    # healthy active ones that never become orphans. Live deletion is
    # release-scoped, so the cleanup decision needs the whole release, not just
    # the rows that happened to be flagged.
    release_members: dict[str, list[dict[str, Any]]] = {}
    cleaned_resources: list[dict[str, Any]] = []
    failed_cleanups = 0
    alerts: list[str] = []
    remediation_tasks: list[dict[str, Any]] = []

    for res in resource_inventory:
        labels = res.get("labels", {})
        if not isinstance(labels, Mapping):
            # Non-mapping labels make the resource unclassifiable. Report it
            # as an orphan with malformed inventory rather than silently
            # skipping it, which would hide damaged inventory from the scanner.
            res_id = str(res.get("id") or res.get("name", "unknown"))
            res_type = str(res.get("type", "unknown"))
            orphan_resources.append({
                "id": res_id,
                "type": res_type,
                "reason": (
                    "Resource has non-mapping labels; its release identity and TTL "
                    "cannot be verified. Automatic deletion is refused."
                ),
                "labels": {},
            })
            alerts.append(
                f"Malformed inventory: resource {res_type} {res_id} has non-mapping labels"
            )
            continue

        # Inventory providers do not expose a common schema. A resource with
        # any staging/ephemeral ownership signal is a candidate; missing
        # identity labels must be reported as an orphan instead of silently
        # disappearing from the scan.
        is_candidate = (
            labels.get("environment") == "staging"
            or labels.get("ephemeral") == "true"
            or bool(str(labels.get("release_id", "")).strip())
            or bool(str(labels.get("owner_task", "")).strip())
        )
        if not is_candidate:
            continue

        res_id = str(res.get("id") or res.get("name", "unknown"))
        res_type = str(res.get("type", "unknown"))

        group_key = _release_group_key(labels, res_id)
        member: dict[str, Any] = {
            "id": res_id,
            "type": res_type,
            "raw_release_id": "",
            # Default to the unsafe verdict: any row that exits this loop early
            # is unverified and must block deletion of its release.
            "expired": False,
            "deletable": False,
        }
        release_members.setdefault(group_key, []).append(member)

        if (
            labels.get("app") != "oday-plus"
            or labels.get("environment") != "staging"
            or labels.get("ephemeral") != "true"
        ):
            orphan_resources.append({
                "id": res_id,
                "type": res_type,
                "reason": "Invalid or incomplete staging identity labels (app/environment/ephemeral)",
                "labels": dict(labels),
            })
            alerts.append(f"Staging candidate has incomplete identity labels: {res_type} {res_id}")
            continue

        # Check managed_by label
        if labels.get("managed_by") != "terraform":
            orphan_resources.append({
                "id": res_id,
                "type": res_type,
                "reason": "Missing or invalid managed_by label on ephemeral resource",
                "labels": dict(labels),
            })
            alerts.append(f"Unmanaged orphan resource found: {res_type} {res_id}")
            continue

        release_id = str(labels.get("release_id", "")).strip()
        raw_release_id = _authoritative_inventory_release_id(res, labels)
        member["raw_release_id"] = raw_release_id
        identity_error = ""
        if not raw_release_id:
            identity_error = (
                "Missing or mismatched authoritative raw_release_id for the bounded release label; "
                "automatic deletion is refused"
            )
        created_str = labels.get("created_at", "")
        expires_str = labels.get("expires_at", "")

        # Check full ownership labels
        candidate_sha = str(labels.get("candidate_sha", "")).strip()
        manifest_prefix = str(labels.get("manifest_digest_prefix", "")).strip()
        owner_task = str(labels.get("owner_task", "")).strip()

        if not release_id or not candidate_sha or not manifest_prefix or not owner_task:
            orphan_resources.append({
                "id": res_id,
                "type": res_type,
                "release_id": release_id,
                "raw_release_id": raw_release_id,
                "reason": "Incomplete ownership labels (release_id, candidate_sha, manifest_digest_prefix, owner_task)",
                "labels": dict(labels),
            })
            alerts.append(f"Orphan resource with incomplete ownership labels: {res_type} {res_id}")
            continue

        if not created_str or not expires_str:
            orphan_resources.append({
                "id": res_id,
                "type": res_type,
                "release_id": release_id,
                "raw_release_id": raw_release_id,
                "reason": "Missing created_at or expires_at ownership label",
                "labels": dict(labels),
            })
            alerts.append(f"Orphan resource missing TTL labels: {res_type} {res_id}")
            continue

        # Determine expiration
        is_expired = False
        try:
            created_dt = parse_timestamp(created_str)
            expires_dt = parse_timestamp(expires_str)
        except ValueError:
            orphan_resources.append({
                "id": res_id,
                "type": res_type,
                "release_id": release_id,
                "raw_release_id": raw_release_id,
                "reason": "Invalid created_at or expires_at ownership label",
                "labels": dict(labels),
            })
            alerts.append(f"Orphan resource has invalid TTL labels: {res_type} {res_id}")
            continue

        if expires_dt < created_dt:
            orphan_resources.append({
                "id": res_id,
                "type": res_type,
                "release_id": release_id,
                "raw_release_id": raw_release_id,
                "reason": "expires_at precedes created_at",
                "labels": dict(labels),
            })
            alerts.append(f"Orphan resource has inverted TTL labels: {res_type} {res_id}")
            continue

        # Determine expiration & policy compliance
        is_over_policy = (expires_dt - created_dt > timedelta(hours=max_ttl_hours))
        is_expired = (now_dt >= expires_dt)
        member["expired"] = is_expired
        member["deletable"] = bool(raw_release_id) and is_staging_ephemeral_resource(labels, raw_release_id)
        identity_prefix = f"{identity_error}; " if identity_error else ""

        if is_expired:
            expired_releases.add(release_id)
            if is_over_policy:
                orphan_resources.append({
                    "id": res_id,
                    "type": res_type,
                    "release_id": release_id,
                    "raw_release_id": raw_release_id,
                    "reason": f"{identity_prefix}Resource expired and exceeded TTL policy ({max_ttl_hours}h)",
                    "labels": dict(labels),
                })
                alerts.append(f"Expired staging resource found (exceeded TTL policy {max_ttl_hours}h): {res_type} {res_id} (release: {release_id})")
            else:
                orphan_resources.append({
                    "id": res_id,
                    "type": res_type,
                    "release_id": release_id,
                    "raw_release_id": raw_release_id,
                    "reason": f"{identity_prefix}Resource expired (exceeded TTL {max_ttl_hours}h)",
                    "labels": dict(labels),
                })
                alerts.append(f"Expired staging resource found: {res_type} {res_id} (release: {release_id})")
        else:
            active_releases.add(release_id)
            if is_over_policy:
                orphan_resources.append({
                    "id": res_id,
                    "type": res_type,
                    "release_id": release_id,
                    "raw_release_id": raw_release_id,
                    "reason": f"{identity_prefix}TTL exceeds policy maximum of {max_ttl_hours}h",
                    "labels": dict(labels),
                })
                alerts.append(f"Staging resource exceeds maximum TTL: {res_type} {res_id}")
            elif identity_error:
                orphan_resources.append({
                    "id": res_id,
                    "type": res_type,
                    "release_id": release_id,
                    "raw_release_id": raw_release_id,
                    "reason": identity_error,
                    "labels": dict(labels),
                })
                alerts.append(f"Staging resource has no authoritative raw release identity: {res_type} {res_id}")

    # Perform auto cleanup on expired resources if requested
    if auto_cleanup and orphan_resources:
        # A live deletion executor is release-scoped: destroying one expired
        # resource tears down the whole release graph (and its Terraform state).
        # Deleting per item would therefore take active siblings with it, so a
        # release is only cleanable when every scanned member of it is verified
        # expired and carries a complete, resolvable ownership identity.
        release_gate: dict[str, str] = {}
        for gate_key, members in release_members.items():
            raw_ids = {m["raw_release_id"] for m in members if m["raw_release_id"]}
            if any(not m["expired"] for m in members):
                release_gate[gate_key] = (
                    "Release still has active or unverified resources; release-scoped automatic "
                    "deletion is refused because it would destroy resources that are not expired."
                )
            elif any(not m["deletable"] for m in members):
                release_gate[gate_key] = (
                    "Release has resources without a complete authoritative ownership identity; "
                    "release-scoped automatic deletion is refused."
                )
            elif len(raw_ids) != 1:
                release_gate[gate_key] = (
                    "Release label resolves to more than one raw release identity; "
                    "release-scoped automatic deletion is refused."
                )

        for item in orphan_resources:
            res_dict = {
                "id": item["id"],
                "type": item["type"],
                "labels": item.get("labels", {}),
                "release_id": item.get("raw_release_id") or "",
                "raw_release_id": item.get("raw_release_id") or "",
            }
            rel_id = str(item.get("raw_release_id") or "")

            # Only a complete, exact ownership set may be handed to cleanup.
            # Incomplete or unmanaged candidates are remediation-only; treating
            # their zero-match result as success would hide an orphan forever.
            if not rel_id or not is_staging_ephemeral_resource(res_dict["labels"], rel_id):
                failed_cleanups += 1
                remediation_tasks.append({
                    "task_type": "ephemeral_staging_orphan_remediation",
                    "resource_id": item["id"],
                    "resource_type": item["type"],
                    "release_id": rel_id,
                    "errors": ["Resource lacks complete authoritative ownership labels; automatic deletion refused."],
                    "timestamp": format_timestamp(now_dt),
                })
                continue

            # Active resources (expires_at > now) must NEVER be auto-deleted.
            # They are remediation-only until expired.
            item_labels = res_dict["labels"]
            expires_str = str(item_labels.get("expires_at", "")).strip()
            try:
                item_expires_dt = parse_timestamp(expires_str)
                if now_dt < item_expires_dt:
                    failed_cleanups += 1
                    remediation_tasks.append({
                        "task_type": "ephemeral_staging_orphan_remediation",
                        "resource_id": item["id"],
                        "resource_type": item["type"],
                        "release_id": rel_id,
                        "errors": ["Resource is active (expires_at > now); automatic deletion refused for over-policy staging resource."],
                        "timestamp": format_timestamp(now_dt),
                    })
                    continue
            except Exception:
                failed_cleanups += 1
                remediation_tasks.append({
                    "task_type": "ephemeral_staging_orphan_remediation",
                    "resource_id": item["id"],
                    "resource_type": item["type"],
                    "release_id": rel_id,
                    "errors": ["Invalid expires_at timestamp; automatic deletion refused."],
                    "timestamp": format_timestamp(now_dt),
                })
                continue

            # Even a fully cleanable resource is refused while any sibling in the
            # same release is still active or unverified.
            gate_reason = release_gate.get(_release_group_key(item_labels, str(item["id"])))
            if gate_reason:
                failed_cleanups += 1
                remediation_tasks.append({
                    "task_type": "ephemeral_staging_orphan_remediation",
                    "resource_id": item["id"],
                    "resource_type": item["type"],
                    "release_id": rel_id,
                    "errors": [gate_reason],
                    "timestamp": format_timestamp(now_dt),
                })
                continue

            cleanup_res = cleanup_ephemeral_staging(
                release_id=rel_id if rel_id else "unknown",
                project_id=project_id,
                resource_inventory=[res_dict],
                dry_run=False,
                now=now_dt,
                deletion_executor=deletion_executor,
                allow_empty=False,
            )
            if cleanup_res.success:
                cleaned_resources.extend(cleanup_res.resources)
            else:
                failed_cleanups += 1
                remediation_tasks.append({
                    "task_type": "ephemeral_staging_cleanup_remediation",
                    "resource_id": item["id"],
                    "resource_type": item["type"],
                    "release_id": rel_id,
                    "errors": cleanup_res.errors,
                    "timestamp": format_timestamp(now_dt),
                })

    return OrphanScanResult(
        scanned_at=format_timestamp(now_dt),
        project_id=project_id,
        total_scanned=scanned_total,
        active_count=len(active_releases),
        expired_count=len(expired_releases),
        orphan_count=len(orphan_resources),
        cleaned_count=len(cleaned_resources),
        failed_cleanups=failed_cleanups,
        expired_releases=sorted(expired_releases),
        orphan_resources=orphan_resources,
        cleaned_resources=cleaned_resources,
        alerts=alerts,
        remediation_tasks=remediation_tasks,
    )


def extend_staging_ttl(
    release_id: str,
    extend_hours: int,
    reason: str,
    owner: str,
    current_expires_at: datetime,
    max_total_ttl_hours: int = MAX_TTL_HOURS,
    now: datetime | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Extend TTL for debugging a failed staging deployment with mandatory owner and reason.

    Policy:
    - Ephemeral staging retention on failure must NOT exceed 24 hours without explicit owner and reason.
    - Maximum allowable extension cannot exceed 168 hours (7 days) from initial creation.
    - Authoritative created_at timestamp is REQUIRED to prevent exceeding max TTL across multiple extensions.
    """
    now_dt = now or datetime.now(UTC)

    if not reason or not reason.strip():
        raise ValueError("TTL extension requires a non-empty documented 'reason'.")

    if not owner or not owner.strip():
        raise ValueError("TTL extension requires a non-empty 'owner' identifier.")

    if extend_hours <= 0:
        raise ValueError("extend_hours must be positive.")

    # This argument is retained for callers that set a scanner-specific
    # policy, but it cannot weaken the product-wide retention contract.  A
    # caller-provided 999h cap used to make an otherwise valid release exceed
    # the hard 168h maximum.
    if not isinstance(max_total_ttl_hours, int) or isinstance(max_total_ttl_hours, bool):
        raise ValueError("max_total_ttl_hours must be an integer between 1 and 168 hours.")
    if not 1 <= max_total_ttl_hours <= MAX_TTL_HOURS:
        raise ValueError(
            f"max_total_ttl_hours must be between 1 and {MAX_TTL_HOURS} hours."
        )

    if created_at is None:
        raise ValueError("TTL extension requires an authoritative 'created_at' timestamp.")

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    else:
        created_at = created_at.astimezone(UTC)

    if current_expires_at.tzinfo is None:
        current_expires_at = current_expires_at.replace(tzinfo=UTC)
    else:
        current_expires_at = current_expires_at.astimezone(UTC)

    if created_at > current_expires_at:
        raise ValueError("created_at cannot be after current_expires_at.")

    if created_at > now_dt + timedelta(minutes=5):
        raise ValueError("created_at cannot be in the future.")

    new_expires_at = current_expires_at + timedelta(hours=extend_hours)
    total_ttl_hours = (new_expires_at - created_at).total_seconds() / 3600.0

    if total_ttl_hours <= 0:
        raise ValueError("Total TTL after extension must be positive.")

    if total_ttl_hours > max_total_ttl_hours:
        raise ValueError(
            f"Total TTL after extension ({total_ttl_hours:.1f}h) exceeds maximum allowable TTL of {max_total_ttl_hours} hours."
        )

    return {
        "release_id": release_id,
        "extended_by_hours": extend_hours,
        "owner": owner.strip(),
        "reason": reason.strip(),
        "previous_expires_at": format_timestamp(current_expires_at),
        "new_expires_at": format_timestamp(new_expires_at),
        "extended_at": format_timestamp(now_dt),
        "total_ttl_hours": total_ttl_hours,
    }


VARIABLE_BLOCK_START_PATTERN = re.compile(r'^variable\s+"([A-Za-z0-9_-]+)"\s*\{', re.MULTILINE)
HCL_STRING_BODY = r'((?:[^"\\]|\\.)*)'
CAN_REGEX_PATTERN = re.compile(r'can\(\s*regex\(\s*"' + HCL_STRING_BODY + r'"\s*,\s*var\.([A-Za-z0-9_-]+)\s*\)\s*\)')


def _hcl_block_body(text: str, open_brace_index: int) -> str:
    """Return the body of the HCL block whose opening brace is at the given index.

    Quoted strings and ``#`` / ``//`` line comments are skipped so a brace or an
    unbalanced quote inside them cannot confuse the depth count.
    """
    depth = 0
    in_string = False
    escaped = False
    index = open_brace_index
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
        elif char == "#" or (char == "/" and text[index + 1 : index + 2] == "/"):
            newline = text.find("\n", index)
            if newline == -1:
                break
            index = newline
            continue
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace_index + 1 : index]
        index += 1
    return ""


def parse_module_variables(variables_text: str) -> dict[str, dict[str, Any]]:
    """Parse ``variable`` blocks into a declaration map for cross-layer checks.

    Only the parts the tfvars contract depends on are extracted: whether the
    variable has a default, and what its ``validation`` blocks accept.
    """
    declarations: dict[str, dict[str, Any]] = {}
    for match in VARIABLE_BLOCK_START_PATTERN.finditer(variables_text):
        name = match.group(1)
        body = _hcl_block_body(variables_text, match.end() - 1)
        conditions = re.findall(r"^\s*condition\s*=\s*(.+)$", body, re.MULTILINE)
        patterns: list[str] = []
        for condition in conditions:
            for raw_pattern, target in CAN_REGEX_PATTERN.findall(condition):
                if target != name:
                    continue
                try:
                    patterns.append(json.loads(f'"{raw_pattern}"'))
                except json.JSONDecodeError:
                    patterns.append(raw_pattern)
        joined = " ".join(conditions)
        declarations[name] = {
            "has_default": bool(re.search(r"^\s*default\s*=", body, re.MULTILINE)),
            "patterns": patterns,
            "allows_null": f"var.{name} == null" in joined,
            "allows_empty": f'var.{name} == ""' in joined,
        }
    return declarations


def validate_tfvars_against_module(
    tfvars: Mapping[str, Any], module_dir: Path
) -> list[str]:
    """Check that generated tfvars are actually accepted by the module's variables.

    Terraform rejects a whole plan/apply when any ``-var-file`` value fails a
    ``validation`` block, so a producer/module mismatch fails closed at deploy
    time rather than in any Python test.  This check runs the module's own
    declared rules against the exact mapping :func:`generate_tfvars` emits.
    """
    variables_path = module_dir / "variables.tf"
    if not variables_path.is_file():
        return [f"Missing required module file: variables.tf (in {module_dir})"]

    declarations = parse_module_variables(variables_path.read_text(encoding="utf-8"))
    errors: list[str] = []

    for name in sorted(set(tfvars) - set(declarations)):
        errors.append(f"tfvars key {name!r} is not a declared module variable.")

    for name, declaration in sorted(declarations.items()):
        if name not in tfvars:
            if not declaration["has_default"]:
                errors.append(
                    f"tfvars is missing required module variable {name!r} (no default declared)."
                )
            continue

        value = tfvars[name]
        if value is None:
            if not declaration["allows_null"]:
                errors.append(f"tfvars sets {name!r} to null, which the module rejects.")
            continue
        if not isinstance(value, str):
            continue
        if value == "":
            if declaration["patterns"] and not declaration["allows_empty"]:
                errors.append(
                    f"tfvars sets {name!r} to an empty string, which the module rejects; "
                    "omit the key or emit a valid value."
                )
            continue
        for pattern in declaration["patterns"]:
            if not re.search(pattern, value):
                errors.append(
                    f"tfvars value for {name!r} ({value!r}) fails the module validation "
                    f"pattern {pattern!r}."
                )

    return errors


def _contract_probe_config() -> StagingConfig:
    """Canonical no-explicit-tenant config used to exercise the tfvars contract.

    This mirrors the default CLI create path (``--tenant-id`` omitted), which is
    the combination that must never produce tfvars the module refuses.
    """
    return StagingConfig(
        release_id="odp-contract-probe-001",
        candidate_sha="0" * 40,
        manifest_digest="sha256:" + "0" * 64,
        project_id="oday-staging-probe",
        owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
        kms_key_id="projects/oday-staging-probe/locations/asia-east1/keyRings/staging/cryptoKeys/release",
        deployer_service_account_email="deployer@oday-staging-probe.iam.gserviceaccount.com",
        created_at="2026-01-01T00:00:00Z",
    )


def validate_module_contract(module_dir: Path) -> list[str]:
    """Validate that the Terraform ephemeral_staging module meets all architectural rules."""
    errors: list[str] = []
    required_files = ("main.tf", "variables.tf", "outputs.tf")

    for fn in required_files:
        p = module_dir / fn
        if not p.is_file():
            errors.append(f"Missing required module file: {fn}")

    if errors:
        return errors

    main_text = (module_dir / "main.tf").read_text(encoding="utf-8")
    vars_text = (module_dir / "variables.tf").read_text(encoding="utf-8")
    out_text = (module_dir / "outputs.tf").read_text(encoding="utf-8")

    # Required resources in main.tf
    expected_resources = (
        'resource "google_sql_database" "staging"',
        'resource "google_sql_user" "staging"',
        'resource "google_secret_manager_secret" "staging_database_url"',
        'resource "google_storage_bucket" "staging_data"',
        'resource "google_service_account" "staging_runtime"',
        'resource "google_service_account" "staging_web"',
        'resource "google_service_account" "staging_worker"',
        'resource "google_cloud_run_v2_service" "staging_api"',
        'resource "google_cloud_run_v2_service" "staging_web"',
        'resource "google_cloud_run_v2_job" "staging_migration"',
        'resource "google_cloud_run_v2_job" "staging_worker"',
        'resource "google_cloud_run_v2_job" "staging_scheduler"',
        'resource "google_cloud_run_v2_service_iam_member" "staging_worker_invokes_api"',
        'resource "google_pubsub_topic" "staging_jobs"',
        'resource "google_pubsub_subscription" "staging_jobs"',
        'resource "google_cloud_scheduler_job" "staging_worker_trigger"',
    )
    for res in expected_resources:
        if res not in main_text:
            errors.append(f"main.tf is missing expected resource: {res}")

    # Paused scheduler check
    if not re.search(r"paused\s*=\s*true", main_text):
        errors.append("google_cloud_scheduler_job.staging_worker_trigger must start paused (`paused = true`).")
    if 'ODP_EXTERNAL_PROVIDER_MODE"\n        value = "fixture"' in main_text:
        errors.append("staging API must keep external provider mode disabled, not fixture-backed.")
    if 'egress = "ALL_TRAFFIC"' in main_text:
        errors.append("staging Cloud Run resources must not allow unrestricted public egress.")

    # Required labels check
    required_labels = (
        "release_id",
        "tenant",
        "candidate_sha",
        "manifest_digest_prefix",
        "created_at",
        "expires_at",
        "owner_task",
        "ephemeral",
        "environment",
        "app",
    )
    for label in required_labels:
        if label not in main_text:
            errors.append(f"resource_labels is missing required tracking label: {label}")

    # created_at and tenant_id variables check
    if 'variable "created_at"' not in vars_text:
        errors.append("variables.tf is missing required variable `created_at` for idempotent applies.")
    if 'variable "tenant_id"' not in vars_text:
        errors.append("variables.tf is missing required variable `tenant_id` for release-scoped tenant isolation.")

    # outputs check
    if 'output "staging_tenant_id"' not in out_text:
        errors.append("outputs.tf is missing required output `staging_tenant_id`.")
    for output_name in REQUIRED_STAGING_OUTPUTS:
        if f'output "{output_name}"' not in out_text:
            errors.append(f"outputs.tf is missing required release-scoped output `{output_name}`.")

    # No forbidden secret exposure in outputs
    forbidden_in_outputs = (
        "random_password.staging_db.result",
        "random_password.staging_cursor_signing_key.result",
        "random_password.staging_web_session.result",
        "secret_data",
        "password",
    )
    for forbidden in forbidden_in_outputs:
        if forbidden in out_text:
            errors.append(f"outputs.tf must not expose sensitive secret token: {forbidden!r}")

    # Cross-layer contract: the tfvars this repo generates for the default
    # create path must satisfy the module's own variable validations.
    probe_config = _contract_probe_config()
    try:
        probe_tfvars = generate_tfvars(probe_config)
    except ValueError as exc:  # pragma: no cover - probe config is static
        errors.append(f"tfvars contract probe could not be generated: {exc}")
    else:
        errors.extend(validate_tfvars_against_module(probe_tfvars, module_dir))

        # The module tolerates a null/empty tenant by deriving one, but the
        # generated tfvars are also the durable record used by the immutable
        # release identity check, so they must pin the tenant explicitly.
        expected_tenant = derive_release_tenant_id(probe_config.release_id)
        probe_tenant = str(probe_tfvars.get("tenant_id", "")).strip()
        if probe_tenant != expected_tenant:
            errors.append(
                "generated tfvars must record the deterministic release-derived tenant_id "
                f"{expected_tenant!r} when no tenant is supplied, got {probe_tenant!r}."
            )

    return errors


# --- CLI Interface ---


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ODay Plus Ephemeral Staging Lifecycle Manager"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_terraform_options(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument(
            "--terraform-module-dir",
            default=str(DEFAULT_EPHEMERAL_MODULE_DIR),
            help="Ephemeral staging Terraform module directory",
        )
        subparser.add_argument(
            "--state-dir",
            default=str(DEFAULT_EPHEMERAL_STATE_DIR),
            help="Directory for per-release Terraform state and tfvars",
        )
        subparser.add_argument(
            "--terraform-bin",
            default="terraform",
            help="Terraform executable",
        )
        subparser.add_argument(
            "--skip-terraform-init",
            action="store_true",
            help="Skip Terraform init when the module is already initialized",
        )
        subparser.add_argument(
            "--terraform-backend-bucket",
            default="",
            help="Protected GCS bucket for the durable Terraform backend",
        )
        subparser.add_argument(
            "--terraform-backend-prefix",
            default="",
            help="Release-scoped prefix for the durable Terraform backend",
        )

    # create
    create_p = subparsers.add_parser("create", help="Plan or create ephemeral staging")
    create_p.add_argument("--release-id", required=True, help="Release identifier")
    create_p.add_argument("--tenant-id", default="", help="Tenant identifier for staging tenant isolation")
    create_p.add_argument("--candidate-sha", required=True, help="Exact 40-character commit SHA")
    create_p.add_argument("--manifest-digest", required=True, help="SHA256 manifest digest")
    create_p.add_argument("--project-id", required=True, help="GCP Project ID")
    create_p.add_argument("--region", default="asia-east1", help="GCP Region")
    create_p.add_argument("--cloud-sql-instance", default="oday-staging-db", help="Cloud SQL instance")
    create_p.add_argument("--api-image", required=True, help="API image reference with @sha256")
    create_p.add_argument("--web-image", required=True, help="Web image reference with @sha256")
    create_p.add_argument("--worker-image", required=True, help="Worker image reference with @sha256")
    create_p.add_argument("--scheduler-image", required=True, help="Scheduler image reference with @sha256")
    create_p.add_argument("--ttl-hours", type=int, default=DEFAULT_TTL_HOURS, help="TTL in hours")
    create_p.add_argument("--created-at", default="", help="Creation timestamp ISO")
    create_p.add_argument("--owner-task-id", required=True, help="Owner Task ID")
    create_p.add_argument("--dry-run", action="store_true", help="Perform dry-run planning only")
    create_p.add_argument("--tfvars-out", help="Path to write tfvars JSON")
    create_p.add_argument("--cloud-sql-connection-name", default="", help="Cloud SQL connection name")
    create_p.add_argument("--network-name", default="oday-staging-vpc", help="Staging VPC network")
    create_p.add_argument("--subnetwork-name", default="oday-staging-subnet", help="Staging VPC subnetwork")
    create_p.add_argument("--kms-key-id", default="", help="CMEK key id")
    create_p.add_argument("--deployer-service-account-email", default="", help="Terraform deployer identity")
    create_p.add_argument("--receipt", help="Path to write create receipt JSON")
    create_p.add_argument(
        "--outputs-out",
        help="Path to write the validated, secret-free Terraform output handoff",
    )
    add_terraform_options(create_p)

    # verify
    verify_p = subparsers.add_parser("verify", help="Run 8-stage rehearsal verification on ephemeral staging")
    verify_p.add_argument("--release-id", required=True, help="Release identifier")
    verify_p.add_argument("--candidate-sha", required=True, help="Exact 40-character commit SHA")
    verify_p.add_argument("--manifest-digest", required=True, help="SHA256 manifest digest")
    verify_p.add_argument("--project-id", required=True, help="GCP Project ID")
    verify_p.add_argument("--region", default="asia-east1", help="GCP Region")
    verify_p.add_argument("--cloud-sql-instance", default="", help="Long-lived staging Cloud SQL foundation instance")
    verify_p.add_argument("--worker-image", default="", help="Worker image reference with @sha256")
    verify_p.add_argument("--scheduler-image", default="", help="Scheduler image reference with @sha256")
    verify_p.add_argument("--dry-run", action="store_true", help="Perform dry-run verification")
    verify_p.add_argument("--operator-identity", default="", help="Optional operator identity to assert least privilege")
    verify_p.add_argument(
        "--outputs-file",
        help="Validated, secret-free Terraform output handoff from create",
    )
    verify_p.add_argument("--receipt", help="Path to write verification receipt JSON")
    add_terraform_options(verify_p)

    # hold
    hold_p = subparsers.add_parser("hold", help="Hold ephemeral staging resources for debugging upon failure")
    hold_p.add_argument("--release-id", required=True, help="Release identifier")
    hold_p.add_argument("--project-id", required=True, help="GCP Project ID")
    hold_p.add_argument("--owner-task-id", required=True, help="Owner Task ID")
    hold_p.add_argument("--reason", required=True, help="Documented reason for retention")
    hold_p.add_argument("--ttl-hours", type=int, default=DEFAULT_TTL_HOURS, help="TTL in hours")
    hold_p.add_argument("--created-at", default="", help="Creation timestamp ISO")
    hold_p.add_argument("--receipt", help="Path to write hold receipt JSON")
    add_terraform_options(hold_p)

    # cleanup
    clean_p = subparsers.add_parser("cleanup", help="Clean up ephemeral staging resources")
    clean_p.add_argument("--release-id", required=True, help="Target release identifier")
    clean_p.add_argument("--project-id", required=True, help="GCP Project ID")
    clean_p.add_argument("--dry-run", action="store_true", help="Perform dry-run without deletion")
    clean_p.add_argument("--inventory-file", help="JSON file with resource inventory for label filtering")
    clean_p.add_argument("--allow-empty", action="store_true", help="Allow empty inventory without error")
    clean_p.add_argument("--receipt", help="Path to write cleanup receipt JSON")
    add_terraform_options(clean_p)

    # scan-orphans
    scan_p = subparsers.add_parser("scan-orphans", help="Scan for expired ephemeral staging resources")
    scan_p.add_argument("--project-id", required=True, help="GCP Project ID")
    scan_p.add_argument("--max-ttl-hours", type=int, default=DEFAULT_TTL_HOURS, help="Max TTL threshold")
    scan_p.add_argument("--inventory-file", required=True, help="JSON file containing resource inventory")
    scan_p.add_argument("--auto-cleanup", action="store_true", help="Automatically delete expired resources")
    add_terraform_options(scan_p)

    # extend-ttl
    ext_p = subparsers.add_parser("extend-ttl", help="Extend TTL for debugging failed staging run")
    ext_p.add_argument("--release-id", required=True, help="Release identifier")
    ext_p.add_argument("--extend-hours", type=int, required=True, help="Hours to extend")
    ext_p.add_argument("--owner", required=True, help="Owner identity requesting extension")
    ext_p.add_argument("--reason", required=True, help="Documented reason for extension")
    ext_p.add_argument("--current-expires-at", required=True, help="Current expires_at ISO timestamp")
    ext_p.add_argument("--created-at", required=True, help="Initial created_at ISO timestamp")

    # validate-contract
    val_p = subparsers.add_parser("validate-contract", help="Validate ephemeral staging Terraform module")
    val_p.add_argument("--module-dir", default="infra/terraform/modules/ephemeral_staging", help="Module dir")

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    if args.command == "create":
        state_dir_path = Path(args.state_dir).expanduser().resolve()
        if not args.dry_run and not args.outputs_out:
            print(
                "ERROR: live staging create requires --outputs-out for the Terraform authority handoff",
                file=sys.stderr,
            )
            return 1
        if not args.dry_run and not (args.terraform_backend_bucket and args.terraform_backend_prefix):
            print(
                "ERROR: live staging create requires the protected GCS Terraform backend bucket and release prefix",
                file=sys.stderr,
            )
            return 1
        existing_created_at = ""
        if not args.created_at:
            _, existing_tfvars_path, existing_inventory_path = _terraform_state_paths(args.release_id, state_dir_path)
            if existing_tfvars_path.is_file():
                try:
                    data = json.loads(existing_tfvars_path.read_text(encoding="utf-8"))
                    existing_created_at = str(data.get("created_at", "")).strip()
                except Exception:
                    pass
            if not existing_created_at and existing_inventory_path.is_file():
                try:
                    inv = json.loads(existing_inventory_path.read_text(encoding="utf-8"))
                    if isinstance(inv, list) and inv and isinstance(inv[0], dict):
                        existing_created_at = str(inv[0].get("created_at", "")).strip()
                except Exception:
                    pass

        created_at_to_use = args.created_at or existing_created_at or format_timestamp(datetime.now(UTC))

        config = StagingConfig(
            release_id=args.release_id,
            tenant_id=args.tenant_id,
            candidate_sha=args.candidate_sha,
            manifest_digest=args.manifest_digest,
            project_id=args.project_id,
            region=args.region,
            cloud_sql_instance_name=args.cloud_sql_instance,
            cloud_sql_connection_name=(
                args.cloud_sql_connection_name
                or f"{args.project_id}:{args.region}:{args.cloud_sql_instance}"
            ),
            network_name=args.network_name,
            subnetwork_name=args.subnetwork_name,
            kms_key_id=args.kms_key_id or StagingConfig.__dataclass_fields__["kms_key_id"].default,
            deployer_service_account_email=(
                args.deployer_service_account_email
                or StagingConfig.__dataclass_fields__["deployer_service_account_email"].default
            ),
            api_image=args.api_image,
            web_image=args.web_image,
            worker_image=args.worker_image,
            scheduler_image=args.scheduler_image,
            ttl_hours=args.ttl_hours,
            created_at=created_at_to_use,
            owner_task_id=args.owner_task_id,
        )

        if args.dry_run:
            identity_errors = validate_immutable_release_identity(config, state_dir_path)
            if identity_errors:
                receipt = StagingLifecycleReceipt(
                    action="create",
                    release_id=config.release_id,
                    candidate_sha=config.candidate_sha,
                    manifest_digest_prefix=config.manifest_digest.replace("sha256:", "")[:16],
                    success=False,
                    timestamp=format_timestamp(datetime.now(UTC)),
                    resources=[],
                    errors=[f"Existing release state conflict for {config.release_id!r}: {'; '.join(identity_errors)}"],
                    remediation_required=False,
                    remediation_notes="Dry-run creation rejected due to immutable release identity conflict with existing release state.",
                    metadata={"dry_run": True},
                )
                if getattr(args, "receipt", None):
                    out_path = Path(args.receipt).expanduser().resolve()
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_text(json.dumps(receipt.to_dict(), indent=2), encoding="utf-8")
                print(json.dumps(receipt.to_dict(), indent=2))
                return 1

        creation_executor = None
        cleanup_executor = None
        if not args.dry_run:
            creation_executor = make_terraform_creation_executor(
                module_dir=Path(args.terraform_module_dir),
                state_dir=Path(args.state_dir),
                terraform_bin=args.terraform_bin,
                initialize=not args.skip_terraform_init,
                outputs_path=Path(args.outputs_out) if args.outputs_out else None,
                backend_bucket=args.terraform_backend_bucket,
                backend_prefix=args.terraform_backend_prefix,
            )
            cleanup_executor = make_terraform_deletion_executor(
                args.release_id,
                module_dir=Path(args.terraform_module_dir),
                state_dir=Path(args.state_dir),
                terraform_bin=args.terraform_bin,
                initialize=not args.skip_terraform_init,
                backend_bucket=args.terraform_backend_bucket,
                backend_prefix=args.terraform_backend_prefix,
            )
        receipt = create_ephemeral_staging(
            config,
            dry_run=args.dry_run,
            creation_executor=creation_executor,
            cleanup_executor=cleanup_executor,
        )
        if args.tfvars_out and receipt.success:
            tfvars = generate_tfvars(config)
            Path(args.tfvars_out).write_text(json.dumps(tfvars, indent=2), encoding="utf-8")

        if getattr(args, "receipt", None):
            out_path = Path(args.receipt).expanduser().resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(receipt.to_dict(), indent=2), encoding="utf-8")

        print(json.dumps(receipt.to_dict(), indent=2))
        return 0 if receipt.success else 1

    elif args.command == "verify":
        lifecycle_outputs: Mapping[str, Any] | None = None
        if args.outputs_file:
            output_path = Path(args.outputs_file).expanduser().resolve()
            lifecycle_outputs = _read_json_object(output_path)
            if lifecycle_outputs is None:
                print(
                    f"ERROR: Terraform staging output handoff is missing or unreadable: {output_path}",
                    file=sys.stderr,
                )
                return 1
        stage_executor = None
        remote_state_verified = False
        if not args.dry_run and (args.terraform_backend_bucket or args.terraform_backend_prefix):
            try:
                backend_args = _terraform_backend_arguments(
                    backend_bucket=args.terraform_backend_bucket,
                    backend_prefix=args.terraform_backend_prefix,
                )
                _run_terraform(
                    module_dir=Path(args.terraform_module_dir).expanduser().resolve(),
                    terraform_bin=args.terraform_bin,
                    arguments=["init", "-input=false", "-upgrade=false", *backend_args],
                )
                _terraform_state_pull(
                    module_dir=Path(args.terraform_module_dir).expanduser().resolve(),
                    terraform_bin=args.terraform_bin,
                )
                if lifecycle_outputs is None:
                    raise RuntimeError(
                        "release-scoped Terraform output handoff is required when reading durable state"
                    )
                remote_outputs = _terraform_output_values(
                    module_dir=Path(args.terraform_module_dir).expanduser().resolve(),
                    terraform_bin=args.terraform_bin,
                )
                if remote_outputs != dict(lifecycle_outputs):
                    raise RuntimeError(
                        "durable Terraform outputs do not match the release output handoff"
                    )
                remote_state_verified = True
            except (RuntimeError, ValueError) as exc:
                print(f"ERROR: cannot read durable staging Terraform state: {exc}", file=sys.stderr)
                return 1
        if not args.dry_run and lifecycle_outputs is not None:
            try:
                stage_executor = make_live_rehearsal_executor(
                    lifecycle_outputs,
                    project_id=args.project_id,
                    region=args.region,
                    operator_identity=args.operator_identity,
                    cloud_sql_instance=args.cloud_sql_instance,
                )
            except (RuntimeError, ValueError) as exc:
                print(f"ERROR: cannot initialize live staging rehearsal: {exc}", file=sys.stderr)
                return 1
        receipt = verify_ephemeral_staging(
            release_id=args.release_id,
            candidate_sha=args.candidate_sha,
            manifest_digest=args.manifest_digest,
            project_id=args.project_id,
            region=args.region,
            worker_image=args.worker_image,
            scheduler_image=args.scheduler_image,
            state_dir=Path(args.state_dir),
            dry_run=args.dry_run,
            stage_executor=stage_executor,
            operator_identity=args.operator_identity,
            lifecycle_outputs=lifecycle_outputs,
            remote_state_verified=remote_state_verified,
            receipt_path=getattr(args, "receipt", None),
        )
        print(json.dumps(receipt.to_dict(), indent=2))
        return 0 if receipt.success else 1

    elif args.command == "hold":
        created_dt = None
        if args.created_at:
            created_dt = parse_timestamp(args.created_at)
        remote_state_verified = False
        if args.terraform_backend_bucket or args.terraform_backend_prefix:
            try:
                backend_args = _terraform_backend_arguments(
                    backend_bucket=args.terraform_backend_bucket,
                    backend_prefix=args.terraform_backend_prefix,
                )
                module_path = Path(args.terraform_module_dir).expanduser().resolve()
                _run_terraform(
                    module_dir=module_path,
                    terraform_bin=args.terraform_bin,
                    arguments=["init", "-input=false", "-upgrade=false", *backend_args],
                )
                _terraform_state_pull(module_dir=module_path, terraform_bin=args.terraform_bin)
                remote_state_verified = True
            except (RuntimeError, ValueError) as exc:
                print(f"ERROR: cannot read durable staging Terraform state: {exc}", file=sys.stderr)
                return 1
        receipt = hold_ephemeral_staging(
            release_id=args.release_id,
            project_id=args.project_id,
            owner_task_id=args.owner_task_id,
            reason=args.reason,
            ttl_hours=args.ttl_hours,
            state_dir=Path(args.state_dir),
            created_at=created_dt,
            receipt_path=getattr(args, "receipt", None),
            require_live_state=True,
            remote_state_verified=remote_state_verified,
        )
        print(json.dumps(receipt.to_dict(), indent=2))
        return 0 if receipt.success else 1

    elif args.command == "cleanup":
        inventory = []
        if args.inventory_file:
            inventory = json.loads(Path(args.inventory_file).read_text(encoding="utf-8"))
        else:
            _, _, inventory_path = _terraform_state_paths(
                args.release_id,
                Path(args.state_dir).expanduser().resolve(),
            )
            if inventory_path.is_file():
                inventory = json.loads(inventory_path.read_text(encoding="utf-8"))

        deletion_executor = None
        if not args.dry_run:
            deletion_executor = make_terraform_deletion_executor(
                args.release_id,
                module_dir=Path(args.terraform_module_dir),
                state_dir=Path(args.state_dir),
                terraform_bin=args.terraform_bin,
                initialize=not args.skip_terraform_init,
                backend_bucket=args.terraform_backend_bucket,
                backend_prefix=args.terraform_backend_prefix,
            )

        receipt = cleanup_ephemeral_staging(
            release_id=args.release_id,
            project_id=args.project_id,
            resource_inventory=inventory,
            dry_run=args.dry_run,
            deletion_executor=deletion_executor,
            allow_empty=args.allow_empty,
        )
        if getattr(args, "receipt", None):
            out_path = Path(args.receipt).expanduser().resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(receipt.to_dict(), indent=2), encoding="utf-8")

        print(json.dumps(receipt.to_dict(), indent=2))
        return 0 if receipt.success else 1

    elif args.command == "scan-orphans":
        if not (1 <= args.max_ttl_hours <= MAX_TTL_HOURS):
            print(
                f"ERROR: Invalid max_ttl_hours: {args.max_ttl_hours}. Must be between 1 and {MAX_TTL_HOURS} hours.",
                file=sys.stderr,
            )
            return 1
        inventory = json.loads(Path(args.inventory_file).read_text(encoding="utf-8"))
        deletion_executors: dict[str, Callable[[Mapping[str, Any]], bool]] = {}

        def orphan_deletion_executor(resource: Mapping[str, Any]) -> bool:
            target_id = str(resource.get("raw_release_id") or "").strip()
            if not target_id:
                raise RuntimeError(
                    "orphan resource has no authoritative raw_release_id for release-scoped cleanup"
                )
            executor = deletion_executors.get(target_id)
            if executor is None:
                backend_prefix = ""
                if args.terraform_backend_bucket or args.terraform_backend_prefix:
                    configured_prefix = args.terraform_backend_prefix.rstrip("/")
                    if configured_prefix.rsplit("/", 1)[-1] not in {
                        target_id,
                        release_label_value(target_id),
                    }:
                        configured_prefix = f"{configured_prefix}/{target_id}"
                    backend_prefix = configured_prefix
                executor = make_terraform_deletion_executor(
                    target_id,
                    module_dir=Path(args.terraform_module_dir),
                    state_dir=Path(args.state_dir),
                    terraform_bin=args.terraform_bin,
                    initialize=not args.skip_terraform_init,
                    backend_bucket=args.terraform_backend_bucket,
                    backend_prefix=backend_prefix,
                )
                deletion_executors[target_id] = executor
            return executor(resource)

        result = scan_orphans(
            project_id=args.project_id,
            resource_inventory=inventory,
            max_ttl_hours=args.max_ttl_hours,
            auto_cleanup=args.auto_cleanup,
            deletion_executor=orphan_deletion_executor if args.auto_cleanup else None,
        )
        print(json.dumps(result.to_dict(), indent=2))
        return 0 if result.failed_cleanups == 0 else 1

    elif args.command == "extend-ttl":
        curr_exp = parse_timestamp(args.current_expires_at)
        created_dt = parse_timestamp(args.created_at)
        res = extend_staging_ttl(
            release_id=args.release_id,
            extend_hours=args.extend_hours,
            reason=args.reason,
            owner=args.owner,
            current_expires_at=curr_exp,
            created_at=created_dt,
        )
        print(json.dumps(res, indent=2))
        return 0

    elif args.command == "validate-contract":
        errors = validate_module_contract(Path(args.module_dir))
        if errors:
            for err in errors:
                print(f"ERROR: {err}", file=sys.stderr)
            return 1
        print("Ephemeral staging module contract is valid.")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
